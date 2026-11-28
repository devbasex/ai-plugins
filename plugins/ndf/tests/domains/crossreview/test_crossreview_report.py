"""cross-review の記録を PR へ残す（#3939）: 記録のコメント・指摘のスレッド・閉じた指摘の返信と resolve。"""

from __future__ import annotations

import contextlib
import json
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Optional

import pytest

from ndf.adapters import git, publish
from ndf.adapters.github import GitHubError, Viewer
from ndf.contracts import result
from ndf.core.converge import location
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview import journal, leftover, leftover_decide, report_decide, store, threads, verify_decide
from ndf.domains.records.mvv import Snapshot

from ._repos import BRANCH, Repos, run_cli, sh
from .conftest import FakePort
from .test_crossreview_leftover import _rounds, _said
from .test_crossreview_leftover_issues import FakeGitHub
from .test_crossreview_run import PR, Fake, _run

HEAD1, HEAD2, FIX1 = "a" * 40, "b" * 40, "c" * 40


def _state() -> dict:
    """2 ラウンドで、1 件を直し 1 件を見送り、1 件が未解決のまま残った状態ファイル。"""
    return {
        "pr": 7,
        "rounds": [
            {
                "round": 1,
                "head": HEAD1,
                "scope": "full",
                "verdict": "changes_required",
                "fix": {"commit": FIX1, "fixed": ["r1-1"], "pushed": FIX1, "reported": 2},
            },
            {"round": 2, "head": HEAD2, "scope": "since", "verdict": "max_rounds"},
        ],
        "findings": [
            {
                "id": "r1-1",
                "by": "codex",
                "round": 1,
                "outcome": "fixed",
                "reason": None,
                "section": "src/a.py#f",
                "text": "a.py:12 で None を返す",
            },
            {
                "id": "r1-2",
                "by": "kiro",
                "round": 1,
                "outcome": "deferred",
                "reason": "設計で決めた範囲の外",
                "section": "src/b.py#g",
                "text": "命名が | 揃わない\n2 行目",
            },
            {
                "id": "r2-1",
                "by": "codex",
                "round": 2,
                "outcome": "unresolved",
                "reason": None,
                "section": "doc.md#セクション",
                "text": "根拠が無い",
            },
        ],
    }


def test_comment_carries_rounds_fix_commits_and_every_finding() -> None:
    body = report_decide.comment(7, _state(), fin.MAX_ROUNDS)
    assert body.startswith(report_decide.MARKER)
    assert "ラウンドの上限で止まった。ラウンド 2・指摘 3（直した 1・見送った 1・未解決 1）" in body
    # 修正の記録: コミット・送ったか・直した指摘
    assert f"| 1 | — | `{HEAD1[:9]}` | full | changes_required | `{FIX1[:9]}` | 送った | r1-1 |" in body
    # 表のセルは改行と縦棒で崩れない。見送った理由が載る
    assert "| r1-2 | kiro | — | src/b.py#g | 命名が \\| 揃わない 2 行目 | 見送った | 設計で決めた範囲の外 |" in body
    assert "| r2-1 | codex | — | doc.md#セクション | 根拠が無い | 未解決 | — |" in body


def test_location_uses_the_line_only_when_the_text_names_the_same_file() -> None:
    assert threads.location({"section": "src/a.py#f", "text": "a.py:12 で"}) == ("src/a.py", 12)
    assert threads.location({"section": "src/a.py#f", "text": "other.py:12 で"}) == ("src/a.py", None)
    assert threads.location({"section": "", "text": "a.py:12"}) == (None, None)


def test_sync_puts_the_closing_reason_in_the_first_comment_and_resolves_once() -> None:
    """#4016 の受け入れ条件 2: この実行で閉じた指摘は、閉じた理由を最初のコメントへ入れて返信を 0 回にし、resolve する。"""
    data, port = _state(), FakePort()
    assert journal.with_occurrences(data)
    assert threads.sync(data, port) == []
    # 行に付けるのは本文の先頭がそのファイルを指すときだけ。スレッドはその指摘のラウンドの head に付ける。行の分かる指摘は
    # レビューでまとめて書き、行の分からない指摘はファイルに 1 件ずつ付ける
    assert [(p, line, c) for p, line, c, _ in port.opened] == [("src/a.py", 12, HEAD1), ("src/b.py", None, HEAD1), ("doc.md", None, HEAD2)]
    assert [(commit, [c.fid for c in comments]) for commit, comments in port.batches] == [(HEAD1, ["r1-1"])]
    bodies = [body for _, _, _, body in port.opened]
    assert f"直した（修正のコミット {FIX1[:9]}）" in bodies[0] and threads.CLOSED_HEAD in bodies[0]
    assert "見送った: 設計で決めた範囲の外" in bodies[1] and threads.CLOSED_HEAD in bodies[1]
    assert threads.CLOSED_HEAD not in bodies[2]
    assert port.replies == []
    assert port.resolved == [1001, 1002]
    assert {fid: (m["comment"], m["replied"]) for fid, m in data[threads.THREADS].items()} == {
        "r1-1": (1001, True),
        "r1-2": (1002, True),
        "r2-1": (1003, False),
    }
    # 打ち直しても同じものを書かない
    threads.sync(data, port)
    assert (len(port.opened), len(port.replies), len(port.resolved)) == (3, 0, 2)
    # 前の実行のスレッドを持つ指摘を後で直したら、そのスレッドへ発生の値を入れて返信し、resolve する
    data["findings"][2]["outcome"] = "fixed"
    threads.sync(data, port)
    [(cid, body)] = port.replies
    assert cid == 1003 and body.startswith("直した") and data["findings"][2]["occurrence"] in body
    assert port.resolved[-1] == 1003


class _Refused(Exception):
    """`github.GitHubError` の形の偽物（行の位置の 422。二次レート制限でない）。"""

    status, retry_after, secondary, hinted, reasons = 422, None, False, False, ()


class _RestHub:
    """GitHubPort の下の REST と GraphQL の偽物。行に付けるのを断り、スレッドの一覧を返す。"""

    repo = "o/r"

    def __init__(self):
        self.posts: list[tuple[str, dict]] = []
        self.mutations: list[dict] = []

    def rest(self, method, path, payload=None, params=None):
        if method == "GET":
            return [{"filename": "src/a.py"}]
        if "line" in payload:
            raise _Refused("line は差分に無い")
        self.posts.append((path, payload))
        return {"id": 55}

    def repository(self, query, variables):
        node = {"id": "T1", "isResolved": False, "comments": {"nodes": [{"databaseId": 55}]}}
        return {"pullRequest": {"reviewThreads": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [node]}}}

    def graphql(self, query, variables):
        self.mutations.append(variables)
        return {}


class _Publisher:
    def guarded(self, *texts):
        return list(texts), {}


def test_sync_failure_is_a_warning_per_finding() -> None:
    data = _state()
    notes = threads.sync(data, FakePort(fail=True))
    # スレッドを作れなかった指摘は注意に残し、済んだ記録を残さない（打ち直せば作り直す）
    assert len(notes) == 3 and all("スレッドを書けなかった" in n for n in notes)
    assert data[threads.THREADS] == {}


def test_run_posts_the_record_with_the_fix_and_returns_its_url(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    fake = Fake(tmp_path / "st", {"codex": [["一つ目"], []], "kiro": [[], []]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert code == 0
    [(slug, pr, body, previous)] = hub.comments
    assert pr == PR and previous is None and body.startswith(report_decide.MARKER)
    fix = store.read_object(store.state_pr(fake.state_dir, PR).path)["rounds"][0]["fix"]
    assert f"`{fix['commit'][:9]}` | 送った | r1-1 |" in body
    assert {"kind": "comment", "number": PR, "url": f"https://github.com/{slug}/pull/{PR}#issuecomment-1"} in got["items"]


def test_issue_4014_downgraded_finding_is_a_light_row_without_a_thread(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """#4014: 1 文の無い major は記録のコメントの表に minor と「軽い（直さずに残した）」で出て、スレッドを作らない。"""
    fake = Fake(tmp_path / "st", {"codex": [[{"text": "A が止まる", "severity": "major", "path": "doc.md", "line": 2}]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    assert f"| r1-1 | codex | minor | doc.md:2 | A が止まる | {report_decide.LIGHT_LABEL} | — |" in hub.comments[-1][2]
    assert hub.port.opened == []


def test_run_keeps_its_outcome_when_the_record_cannot_be_written(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    def refuse(pr, body, previous):
        raise RuntimeError("403")

    from ndf.domains.crossreview import cli

    monkeypatch.setattr(cli, "HUB", lambda *a: cli.Hub(refuse, lambda pr: FakePort(), lambda pr: FakePort()))
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    assert any("記録を書けなかった" in w and "ndf cross-review report" in w for w in got["warnings"])


@pytest.mark.parametrize(
    ("state_repo", "own", "refused"),
    [
        pytest.param("o/r", "O/R", False, id="same"),
        pytest.param(None, "o/r", True, id="no-target"),
        pytest.param("o/r", "me/other", True, id="other-main"),
        pytest.param("o/r", None, True, id="main-unreadable"),
    ],
)
def test_issue_3780_2_filing_needs_the_settings_of_the_target_repository(
    state_repo: Optional[str], own: Optional[str], refused: bool
) -> None:
    def remote_repo() -> str:
        if own is None:
            raise git.GitError("リモートが無い")
        return own

    note = leftover.unconfirmed_target(Path("main"), state_repo, lambda p: types.SimpleNamespace(remote_repo=remote_repo))
    assert (note is not None) is refused


@pytest.mark.parametrize(
    ("target", "repo_arg"), [pytest.param("acme/repo", "acme/other", id="differs"), pytest.param(None, "o/r", id="unconfirmed")]
)
def test_issue_3780_2_report_writes_nothing_without_a_matching_target(
    tmp_path: Path, capsys, hub, target: Optional[str], repo_arg: str
) -> None:
    store.write_object(tmp_path / store.STATE_NAME.format(pr=7), {**_left_state(), "target_repo": target})
    code, got = run_cli(capsys, "report", "--state-dir", str(tmp_path), "--pr", "7", "--repo", repo_arg)
    assert (code != 0, got["cause"]["kind"]) == (True, "precondition") and "記録を公開していない" in got["cause"]["detail"]
    assert (hub.made, hub.comments, hub.port.opened, hub.port.resolved) == (0, [], [], [])


@pytest.mark.parametrize("changed", [{"target_repo": "acme/other"}, {"current_pr": 9}])
def test_report_writes_nothing_when_the_state_read_in_the_lock_differs_from_the_connection(
    tmp_path: Path, capsys, monkeypatch, hub, changed: dict
) -> None:
    from ndf.domains.crossreview import cli

    path, state = tmp_path / store.STATE_NAME.format(pr=7), {**_state(), "target_repo": "acme/repo"}
    store.write_object(path, state)

    @contextlib.contextmanager
    def lock(pr: int) -> Iterator[None]:
        store.write_object(path, {**state, **changed})
        yield

    monkeypatch.setattr(cli, "HUB", lambda *a: hub.make(*a)._replace(publishing=lock))
    code, got = run_cli(capsys, "report", "--state-dir", str(tmp_path), "--pr", "7")
    assert code != 0 and "接続先" in got["cause"]["detail"]
    assert (hub.comments, hub.port.opened, hub.port.resolved) == ([], [], [])


def test_issue_3780_2_report_writes_nothing_when_the_state_read_in_the_lock_lost_the_target(
    tmp_path: Path, capsys, monkeypatch, hub
) -> None:
    from ndf.domains.crossreview import cli

    path, changed = tmp_path / store.STATE_NAME.format(pr=7), {**_left_state(), "target_repo": None}
    store.write_object(path, _left_state())

    @contextlib.contextmanager
    def lock(pr: int) -> Iterator[None]:
        store.write_object(path, changed)
        yield

    monkeypatch.setattr(cli, "HUB", lambda *a: hub.make(*a)._replace(publishing=lock))
    code, got = run_cli(capsys, "report", "--state-dir", str(tmp_path), "--pr", "7")
    assert code != 0 and "target_repo" in got["cause"]["detail"]
    assert (hub.comments, hub.port.opened, hub.port.resolved) == ([], [], [])
    assert store.read_object(path) == changed


def test_issue_3780_2_run_saves_the_repository_of_the_confirmed_pr(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    read = threads.ReadPr(f"o/r#{PR}", BRANCH, sh(wt, "rev-parse", "HEAD").strip(), "o/r")
    monkeypatch.setattr(hub.port, "read_pr", lambda: read)
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    _run(capsys, monkeypatch, fake, wt)
    assert store.state_repo(store.state_pr(fake.state_dir, PR)) == "o/r"


@pytest.mark.parametrize("unconfirmed", [None, ""])
def test_issue_3780_2_run_fills_an_unconfirmed_repository(
    wt: Path, tmp_path: Path, capsys, monkeypatch, hub, unconfirmed: Optional[str]
) -> None:
    read = threads.ReadPr(f"o/r#{PR}", BRANCH, sh(wt, "rev-parse", "HEAD").strip(), "o/r")
    monkeypatch.setattr(hub.port, "read_pr", lambda: read)
    fake = Fake(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    store.write_object(fake.state_dir / store.STATE_NAME.format(pr=PR), {"pr": PR, "target_repo": unconfirmed})
    _run(capsys, monkeypatch, fake, wt)
    assert store.state_repo(store.state_pr(fake.state_dir, PR)) == "o/r"


def test_report_writes_the_record_from_a_left_state_file(tmp_path: Path, capsys, monkeypatch, hub) -> None:
    # 起票の候補が無ければ、主 worktree を調べない
    monkeypatch.setattr(location, "main_worktree", lambda g: pytest.fail("主 worktree を調べた"))
    state_dir = tmp_path / "st"
    store.write_object(state_dir / store.STATE_NAME.format(pr=7), {**_state(), "target_repo": "acme/repo"})
    from ndf.domains.crossreview import cli

    code = cli.main(["report", "--state-dir", str(state_dir), "--pr", "7"])
    got = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result.errors(got, code) == [] and code == 0
    [(slug, pr, body, previous)] = hub.comments
    assert (slug, pr, previous) == ("acme/repo", 7, None) and "ラウンドの上限で止まった" in body
    assert len(hub.port.opened) == 3 and hub.port.resolved == [1001, 1002] and hub.port.replies == []
    # 済んだスレッドは状態ファイルへ残り、打ち直しても二重に書かない。記録のコメントは前のコメントを書き換える
    cli.main(["report", "--state-dir", str(state_dir), "--pr", "7"])
    assert len(hub.port.opened) == 3
    assert hub.comments[-1][3] == "https://github.com/acme/repo/pull/7#issuecomment-1"


@pytest.mark.parametrize("recovered", [False, True])
def test_report_tells_the_mvv_record_error_of_the_state_read_in_the_lock(tmp_path: Path, monkeypatch, hub, recovered: bool) -> None:
    """MVV の記録の失敗の注意は、公開のロックの中で読み直した状態から載せる。ロックを待つ間に書けていれば載せない。"""
    from ndf.domains.crossreview import cli, drive

    path, state = tmp_path / store.STATE_NAME.format(pr=7), {**_state(), "target_repo": "acme/repo"}
    store.write_object(path, {**state, store.MVV_RECORD_ERROR: "OSError: 書けない"})

    @contextlib.contextmanager
    def lock(pr: int) -> Iterator[None]:
        if recovered:
            store.write_object(path, state)
        yield

    monkeypatch.setattr(cli, "HUB", lambda *a: hub.make(*a)._replace(publishing=lock))
    assert cli.main(["report", "--state-dir", str(tmp_path), "--pr", "7"]) == 0
    assert (drive.RECORDS_UNWRITTEN in hub.comments[-1][2]) is not recovered
    notices = [] if recovered else [drive.RECORDS_UNWRITTEN]
    data = store.read_object(path)
    assert data[report_decide.PUBLISHED_KEY] == report_decide.published_state(7, data, report_decide.final_of(data), notices)


def test_comment_id_reads_the_id_from_the_url() -> None:
    assert report_decide.comment_id("https://github.com/o/r/pull/7#issuecomment-123") == 123
    assert report_decide.comment_id(None) is None and report_decide.comment_id("https://github.com/o/r/pull/7") is None


class _ThreadsHub(_RestHub):
    """スレッドの最初のコメントの書き手が違う 3 つのスレッド。"""

    def repository(self, query, variables):
        def node(cid, body, login):
            first = {"databaseId": cid, "body": body, "author": {"login": login}}
            return {"id": f"T{cid}", "isResolved": False, "path": "src/a.py", "line": 3, "comments": {"nodes": [first]}}

        opening = threads.opening({"id": "r1-1", "by": "codex", "round": 1, "text": "A が止まる"})
        nodes = [node(1, opening, "ndf-bot"), node(2, opening, "maintainer"), node(3, "人の指摘", "alice")]
        return {"pullRequest": {"reviewThreads": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}

    def viewer(self):
        return Viewer("ndf-bot", 1)


def test_github_port_marks_only_threads_written_by_the_viewer() -> None:
    """PR 3944 の r1-3: cross-review の書式のスレッドは、最初のコメントの書き手が今の実行の認証の login（viewer）と同じときだけ
    信頼する。書き込みの権限を持つ別の人が同じ書式で書いたものも信頼しない。"""
    hub = _ThreadsHub()
    port = threads.GitHubPort(hub, _Publisher(), 7, _Refused)
    assert [(t.comment, t.trusted) for t in port.open_threads()] == [(1, True), (2, False), (3, False)]
    port.open_threads()


def test_rewound_pr_opens_threads_only_for_open_findings() -> None:
    """PR 3944 の r5-3: 巻き直しの前に直した・見送った指摘は新しい PR にスレッドを作らず（記録のコメントの表には載る）、
    未解決の指摘だけを新しい PR へスレッドにする。"""
    data, port = _state(), FakePort()
    report_decide.bind_pr(data, 7, 7)
    threads.sync(data, port)
    assert report_decide.bind_pr(data, 8, 7)
    again = FakePort()
    assert threads.sync(data, again) == []
    assert [p for p, _, _, _ in again.opened] == ["doc.md"] and (again.replies, again.resolved) == ([], [])
    assert "| r1-1 |" in report_decide.comment(8, data, fin.MAX_ROUNDS)
    # 新しい PR で後から直した指摘は、そのスレッドへ返信して resolve する
    data["findings"][2]["outcome"] = "fixed"
    threads.sync(data, again)
    assert again.resolved == [1001]


def test_legacy_record_without_an_owner_is_not_reused_after_a_rewind() -> None:
    """PR 3944 の r5-2: オーナー（report_pr）の無い旧形式の記録は、巻き直す前なら元の PR のものとして使い、巻き直した後なら
    コメントとスレッドの id を使わずに退避する。"""
    legacy = {**_state(), "report_comment": "https://github.com/o/r/pull/7#issuecomment-5", "threads": {"r1-1": {"comment": 1}}}
    kept = json.loads(json.dumps(legacy))
    assert report_decide.bind_pr(kept, 7, 7)
    assert kept["report_comment"] == legacy["report_comment"] and kept["report_pr"] == 7
    moved = json.loads(json.dumps(legacy))
    assert report_decide.bind_pr(moved, 8, 7)
    assert "report_comment" not in moved and "threads" not in moved
    assert moved["report_earlier"][report_decide.UNKNOWN_OWNER]["report_comment"] == legacy["report_comment"]


def test_open_items_survive_the_publishing_scrub_and_hide_secrets(monkeypatch) -> None:
    """PR 3944 の r6-4: 未解決の指摘の自由記述を値ごとに伏せてから JSON にし、本文全体の公開の伏せ字が一覧の構造
    （閉じ括弧・`-->`）を壊さない。JSON の escape で表し方の変わる秘密も原文のうちに伏せる。"""

    secret = 'quote"back\\slash-secret'
    monkeypatch.setenv("NDF_TEST_API_TOKEN", secret)

    def clean(texts):
        return [publish.scrub(t, True, accounts=()).text for t in texts]

    findings = [
        {
            "id": "r1-1",
            "text": "Authorization: Bearer abc.def で通る",
            "by": "codex",
            "round": 1,
            "outcome": fin.UNRESOLVED,
            "severity": "major",
        },
        {
            "id": "r1-2",
            "text": f"キー {secret} が出る・https://u:p@example.com/x を開く",
            "by": "kiro",
            "round": 1,
            "outcome": fin.UNRESOLVED,
        },
        {
            "id": "r1-3",
            "text": "x-access-token:abc を送る a@example.org",
            "by": "kiro",
            "round": 1,
            "outcome": fin.UNRESOLVED,
            "path": "a/b.py",
        },
    ]
    body = report_decide.comment(7, {"findings": findings, "threads": {}}, None, (), clean)
    published = publish.scrub(body, True, accounts=()).text
    items = report_decide.open_items(published)
    assert [i["id"] for i in items] == ["r1-1", "r1-2", "r1-3"]
    assert [i["path"] for i in items] == [None, None, "a/b.py"]
    joined = json.dumps(items, ensure_ascii=False)
    for leaked in ("abc.def", "quote", "u:p@", "x-access-token:abc", "a@example.org"):
        assert leaked not in joined and leaked not in published.rsplit("\n", 2)[-2]
    assert items[0]["text"] == "Authorization: [REDACTED]"


class _CountingHub(_RestHub):
    """スレッドの一覧を読んだ回数を数える。"""

    def __init__(self):
        super().__init__()
        self.lists = 0

    def repository(self, query, variables):
        self.lists += 1
        return super().repository(query, variables)

    def rest(self, method, path, payload=None, params=None):
        if method == "POST" and "line" not in (payload or {}) and path.endswith("/comments"):
            self.posts.append((path, payload))
            return {"id": 99}
        return super().rest(method, path, payload, params)


def test_threads_read_at_the_start_are_reused_to_resolve() -> None:
    """PR 3944 の r6-8: 起動の始めに読んだスレッドの一覧を resolve で使い、新しいスレッドを作っても一覧を捨てない。"""
    hub = _CountingHub()
    port = threads.GitHubPort(hub, _Publisher(), 7, _Refused)
    assert [t.comment for t in port.open_threads()] == [55]
    assert port.open_thread("src/a.py", None, HEAD1, "本文") == 99
    port.resolve(55)
    port.resolve(55)
    assert hub.lists == 1 and hub.mutations == [{"id": "T1"}]


def test_reader_and_writer_share_one_port_per_pr(monkeypatch, tmp_path: Path) -> None:
    """PR 3944 の r6-8: 同じ PR の読む口と書く口は同じ口（読んだスレッドの一覧を共有する）。"""
    import types

    from ndf.adapters import write_gate
    from ndf.domains.crossreview import cli, repo

    # 書き込みの順番の記録を本物のホームの下へ置かない
    monkeypatch.setattr(write_gate, "gate_path", lambda: tmp_path / "write-gate.json")
    gh = types.SimpleNamespace(GitHub=lambda slug, client, gate=None: _RestHub(), client=lambda **_: None, GitHubError=_Refused)
    monkeypatch.setattr(cli, "load_github", lambda: gh)
    monkeypatch.setattr(repo, "resolve_target_repo", lambda *a: types.SimpleNamespace(slug="o/r"))
    made = cli._github_hub(None, None, Path("."))
    assert made.reader(7) is made.threads(7) and made.reader(7) is not made.threads(8)


def test_revived_finding_moves_to_its_new_place_and_replies_with_the_later_fix() -> None:
    """PR 3944 の r7-3: 直した指摘が行を変えて再発したら、位置を再発したラウンドのものにし、直し直した返信は再発の後の
    修正のコミットを案内する（再発する前のコミットを使わない）。"""
    from ndf.domains.crossreview import drive

    journal = drive.FindingJournal()
    journal.enter(1, [{"text": "A が止まる", "severity": "major", "path": "a.py", "line": 10, "by": "codex"}])
    journal.rows["r1-1"]["outcome"] = fin.FIXED
    journal.enter(3, [{"text": "A が止まる", "severity": "major", "path": "a.py", "line": 42, "by": "codex"}])
    row = journal.rows["r1-1"]
    assert (row["outcome"], row["round"], row["line"]) == (fin.UNRESOLVED, 3, 42) and journal.revived == ["r1-1"]
    row["outcome"] = fin.FIXED
    rounds = [
        {"round": 1, "head": "h1", "fix": {"commit": "old0000000", "fixed": ["r1-1"]}},
        {"round": 3, "head": "h3", "fix": {"commit": "new1111111", "fixed": ["r1-1"]}},
    ]
    assert threads.closing(row, rounds) == "直した（修正のコミット new111111）"
    assert threads.location(row) == ("a.py", 42)


def test_revived_finding_drops_the_old_section_when_the_new_one_has_none() -> None:
    """PR 3944 の r8-5: 再発した指摘に場所もセクションも無ければ、前のセクションから古い場所を復元しない。"""
    from ndf.domains.crossreview import drive

    journal = drive.FindingJournal()
    journal.enter(1, [{"text": "A が止まる", "severity": "major", "path": "a.py", "line": 10, "section": "a.py#A", "by": "codex"}])
    journal.rows["r1-1"]["outcome"] = fin.FIXED
    journal.enter(3, [{"text": "A が止まる", "severity": "major", "by": "codex"}])
    row = journal.rows["r1-1"]
    assert row.get("section") is None and "path" not in row and "line" not in row
    assert threads.location(row) == (None, None)


# --- 結果 JSON の MVV の判定 -------------------------------------------------------------------


def _mvv_outputs(rows: list[dict[str, Any]], clean: Any) -> dict[str, Any]:
    snap = Snapshot("approved", 11, "a" * 64, None, (), {}, HEAD1, "c" * 64, None)
    return report_decide.mvv_outputs([{"judged_mvv": snap.identity, **r} for r in rows], snap, HEAD1, clean)


def test_issue_4090_7_free_text_and_paths_are_hidden_in_one_call_and_kept_in_the_journal() -> None:
    """理由・本文・根拠の記述の誤りのパスと引いた項目を、1 回でまとめて伏せる。ジャーナルの指摘は書き換えない。"""
    calls: list[list[str]] = []

    def clean(texts: Any) -> list[str]:
        calls.append(list(texts))
        return [t.replace("秘密", "***") for t in texts]

    rows = [
        {"id": "r1-1", "text": "a", "mvv": "Value 5", "outcome": "deferred", "reason": "秘密の理由"},
        {"id": "r1-2", "text": "秘密の操作", "mvv": "P1", "redline": True, "redline_items": ["P1", "C7"], "outcome": "unresolved"},
        {"id": "r1-n1", "text": "b", "by": "ndf", "path": "秘密.md", "line": 3, "cited": "秘密", "unknown": ["秘密"], "outcome": "fixed"},
        {"id": "r1-3", "text": "c", "mvv": "Value 5", "outcome": "deferred", "reason": "前の MVV", "judged_mvv": "approved:10:b:c"},
    ]
    out = _mvv_outputs(rows, clean)
    assert out["disputed"] == [{"id": "r1-1", "item": "Value 5", "reason": "***の理由"}]
    assert out["redlines"] == [{"id": "r1-2", "item": "P1・C7", "text": "***の操作"}]
    assert out["citations"] == [{"id": "r1-n1", "path": "***.md", "line": 3, "cited": "***", "unknown": ["***"], "outcome": "fixed"}]
    assert len(calls) == 1 and rows[2]["path"] == "秘密.md"
    # 伏せる値が無ければ、照会もしない
    out = _mvv_outputs([{"id": "r1-4", "text": "d", "by": "kiro", "mvv": "Value 1", "outcome": "unresolved"}], clean)
    assert out["findings"] == [{"id": "r1-4", "item": "Value 1", "by": "kiro", "outcome": "unresolved"}] and len(calls) == 1


def test_plain_reasons_kept_only_in_the_records_are_cleaned_in_the_same_call() -> None:
    """#3959: 記録だけに書く通常の指摘の見送りの理由も、結果 JSON と同じ 1 回の照会で伏せ、結果 JSON には載せない。"""
    calls: list[list[str]] = []

    def clean(texts: Any) -> list[str]:
        calls.append(list(texts))
        return list(texts)

    snap = Snapshot("approved", 11, "a" * 64, None, (), {}, HEAD1, "c" * 64, None)
    judged = {"id": "r1-1", "text": "a", "mvv": "Value 5", "outcome": "deferred", "reason": "MVV の理由", "judged_mvv": snap.identity}
    plain = {"id": "r1-2", "text": "b", "by": "codex", "outcome": "deferred", "reason": "通常の理由"}
    once = publish.Once(clean)
    out = report_decide.mvv_outputs([judged, plain], snap, HEAD1, once)
    assert [d["id"] for d in out["disputed"]] == ["r1-1"] and out["findings"] == []
    assert calls == [["MVV の理由", "通常の理由"]]
    # 記録の側が同じ値を伏せても、照会し直さない
    assert once(["通常の理由"]) == ["通常の理由"] and len(calls) == 1


def test_same_value_is_cleaned_once() -> None:
    calls: list[list[str]] = []

    def clean(texts: Any) -> list[str]:
        calls.append(list(texts))
        return [t.upper() for t in texts]

    once = publish.Once(clean)
    assert (once(["a", "b", "a"]), once(["b", "c"]), once([])) == (["A", "B", "A"], ["B", "C"], [])
    assert (calls, once.failure) == ([["a", "b"], ["c"]], None)


@pytest.mark.parametrize(
    ("error", "cause"),
    [
        (GitHubError("o/hidden", quota="graphql"), "レート制限"),
        (GitHubError("o/hidden", unreached=True), "通信の障害"),
        (GitHubError("o/hidden", status=403), "GitHub が照会を断った（HTTP 403）"),
        (ValueError("o/hidden"), "照会の失敗（ValueError）"),
    ],
)
def test_issue_4090_7_failure_hides_every_value_and_the_message(error: Exception, cause: str) -> None:
    """照会に失敗したら、伏せ終えた値も含めて固定の文にし、照会し直さない。原因に例外の文（リポジトリの名前を含みうる）を使わない。"""
    raised: Optional[Exception] = None
    calls = 0

    def clean(texts: Any) -> Any:
        nonlocal calls
        calls += 1
        if raised is not None:
            raise raised
        return texts

    once = publish.Once(clean)
    assert once(["a"]) == ["a"]
    raised = error
    assert (once(["a", "b"]), once(["c"])) == ([publish.WITHHELD] * 2, [publish.WITHHELD])
    assert (once.failure, calls) == (cause, 2)


def test_issue_3955_round_table_names_the_seats_and_whose_seat_was_filled() -> None:
    """受け入れ条件 3: ラウンドの表に「相手役」の列があり、埋めた席は欠けた相手役を添える。列の数は見出しと行でそろう。"""
    filled = {"reviewers": ["codex", "claude"], "filled": [{"seat": "claude", "for": "kiro", "reason": "failed: 1"}]}
    assert report_decide._seats_cell(filled) == "codex・claude（kiro の席）"
    before = {"reviewers": ["codex", "claude"], "filled": [{"seat": "claude", "for": None, "reason": "allowed の相手役 1（codex）"}]}
    assert report_decide._seats_cell(before) == "codex・claude"
    assert report_decide._seats_cell({}) == "—"
    rows = report_decide._round_rows([{"round": 1, **filled}, {"round": 2}])
    assert rows[0].startswith("| ラウンド | 相手役 |")
    assert "| codex・claude（kiro の席） |" in rows[2]
    assert len({r.count(" | ") for r in rows}) == 1


def test_issue_4363_a_lowered_finding_shows_the_raised_severity_and_only_the_fixed_reason() -> None:
    """下げた指摘は、元の重要度と定型の理由を表に出す。判定役が書いた文（作り物の非公開の値）は出さず、状態の記録に残す。"""
    private = "sample-corp-internal-endpoint"
    rows = journal.FindingJournal()
    rows.enter(1, [{"text": "保存が止まる", "severity": "major", "impact": "入力が消える", "by": "codex"}])
    answer = {"id": "r1-1", "holds": False, "condition": private, "path": private, "result": private, "reason": private, "tracks": []}
    rows.apply_checks(["r1-1"], verify_decide.read([answer], ["r1-1"]).verdicts)
    assert rows.rows["r1-1"]["check"]["reason"] == private
    body = report_decide.comment(1, {"findings": list(rows.rows.values())}, None)
    assert "| minor（確認の前は major） |" in body
    assert "| 判定役の確認で成り立たないと分かった |" in body
    assert private not in body


# --- 直さずに残した指摘（#3780） ---------------------------------------------------------------


def _left_state() -> dict:
    """直さずに残した指摘が 2 件（起票する種類を持つものと持たないもの）の状態ファイル。"""
    row = {"by": "codex", "round": 2, "outcome": fin.UNRESOLVED, "severity": "major", "path": "a.py", "line": 5, "parked": True}
    return {
        "pr": 7,
        "target_repo": "o/r",
        "rounds": [{"round": 2, "head": HEAD2, "verdict": fin.CONVERGED}],
        "findings": [
            {**row, "id": "r2-1", "text": "古い行で値を落とす", "check": {"tracks": ["delivered"]}, "mvv": "Value 6"},
            {**row, "id": "r2-2", "text": "古い行の書き方"},
        ],
    }


def test_issue_3780_2_the_table_and_the_list_show_how_a_finding_was_left() -> None:
    data = _left_state()
    url = "https://github.com/o/r/issues/100"
    data["findings"][0]["issue"] = url
    body = report_decide.comment(7, data, fin.CONVERGED)
    rows = {line.split("|")[1].strip(): line for line in body.splitlines() if line.startswith("| r2-")}
    # 公開先の Issue は、伏せ字が所有者名を置き換えても開ける `#番号` で出す
    assert "#100" in rows["r2-1"] and url not in rows["r2-1"] and "記録のみ" in rows["r2-2"]
    assert "Value 6" in rows["r2-1"] and "MVV" not in rows["r2-2"]
    assert "未解決 0" in body
    listed = {i["id"]: (i.get("parked"), i.get("issue")) for i in report_decide.open_items(body)}
    assert listed == {"r2-1": (True, url), "r2-2": (True, None)}


def _filing(monkeypatch, hub, repos: Repos, primary: Optional[str] = "github") -> FakeGitHub:
    """起票先を偽の GitHub にし、打った場所をメインディレクトリ（リモートは状態ファイルの対象）にする。"""
    sh(repos.main, "remote", "set-url", "origin", "https://github.com/o/r.git")
    github = FakeGitHub()
    port = leftover.GitHubIssues(github, publish.Publisher(github, frozenset()), GitHubError, github.viewer, sleep=lambda s: None)
    hub.leftovers = lambda pr: port
    if primary is not None:
        (repos.main / ".ndf").mkdir(exist_ok=True)
        (repos.main / ".ndf" / "project.json").write_text(json.dumps({"issues": {"primary": primary}}), encoding="utf-8")
    monkeypatch.chdir(repos.main)
    return github


def _report_of(state_dir: Path, capsys, pr: int = 7) -> dict:
    code, got = run_cli(capsys, "report", "--state-dir", str(state_dir), "--pr", str(pr))
    assert result.errors(got, code) == [] and code == 0, got
    return got


def test_issue_3780_2_report_files_once_after_a_failed_creation(repos: Repos, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    github = _filing(monkeypatch, hub, repos)
    state_dir = tmp_path / "st"
    path = state_dir / store.STATE_NAME.format(pr=7)
    store.write_object(path, _left_state())
    github.fail[("POST", "repos/o/r/issues")] = 422
    got = _report_of(state_dir, capsys)
    assert any("r2-1" in w for w in got["warnings"]) and store.read_object(path)["findings"][0]["issue_error"]
    assert "記録のみ" in hub.comments[-1][2]
    for _ in range(2):
        _report_of(state_dir, capsys)
    assert len(github.issues) == 1 and f"起票: #{github.issues[0]['number']}）" in hub.comments[-1][2]
    assert store.read_object(path)["findings"][0]["issue"] == github.issues[0]["html_url"]


@pytest.mark.parametrize(
    ("primary", "noted"), [pytest.param("markdown", False, id="not-github"), pytest.param(None, True, id="not-declared")]
)
def test_issue_3780_2_nothing_is_filed_unless_the_primary_is_github(
    repos: Repos, tmp_path: Path, capsys, monkeypatch, hub, primary: Optional[str], noted: bool
) -> None:
    github = _filing(monkeypatch, hub, repos, primary)
    state_dir = tmp_path / "st"
    path = state_dir / store.STATE_NAME.format(pr=7)
    store.write_object(path, _left_state())
    got = _report_of(state_dir, capsys)
    assert github.calls == [] and any("issues.primary" in w for w in got.get("warnings", ())) is noted
    # 未設定の見送りは理由を、GitHub にしない設定の見送りは null を状態にだけ残す
    state = store.read_object(path)
    assert (state["issue_skipped"] is not None) is noted and "issue_error" not in state["findings"][0]


def test_issue_3780_a_rerun_checks_the_target_once_and_reuses_the_comment_while_skipped(tmp_path: Path, monkeypatch, hub) -> None:
    from ndf.domains.crossreview import cli

    github = FakeGitHub()
    port = leftover.GitHubIssues(github, publish.Publisher(github, frozenset()), GitHubError, github.viewer, sleep=lambda s: None)
    hub.leftovers, hub.clean = lambda pr: port, list
    checked: list[str] = []
    monkeypatch.setattr(cli.leftover, "unconfirmed_target", lambda *a: checked.append("target"))
    state_dir = tmp_path / "st"
    store.write_object(state_dir / store.STATE_NAME.format(pr=7), _left_state())
    cfg = types.SimpleNamespace(state_dir=state_dir, pr=7, main=tmp_path)
    got = types.SimpleNamespace(review=types.SimpleNamespace(findings=[], final=fin.CONVERGED), warnings=[], reused=True)

    def rerun() -> list[str]:
        return cli._report(cfg, got, lambda: cli.HUB(None, "o/r", tmp_path))[1]

    rerun()
    warnings = rerun()
    assert len(hub.comments) == 1 and any("issues.primary" in w for w in warnings) and checked == []
    # 設定を直せば公開し直して起票する。作成に失敗した後の打ち直しは、公開の中で 1 回だけ確認する
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "project.json").write_text(json.dumps({"issues": {"primary": "github"}}), encoding="utf-8")
    github.fail[("POST", "repos/o/r/issues")] = 422
    rerun()
    # 未設定から github への変更は確認せずに公開し直し、リモートの確認は起票の前の 1 回だけ
    assert checked == ["target"]
    checked.clear()
    rerun()
    assert checked == ["target"] and len(hub.comments) == 3 and len(github.issues) == 1


@pytest.mark.parametrize("before", ["markdown", "remote-differs"])
def test_issue_3780_a_rerun_files_after_the_skip_is_resolved(tmp_path: Path, monkeypatch, hub, before: str) -> None:
    from ndf.domains.crossreview import cli

    github = FakeGitHub()
    port = leftover.GitHubIssues(github, publish.Publisher(github, frozenset()), GitHubError, github.viewer, sleep=lambda s: None)
    hub.leftovers, hub.clean = lambda pr: port, list
    note: list[Optional[str]] = ["リモートが違う" if before == "remote-differs" else None]
    monkeypatch.setattr(cli.leftover, "unconfirmed_target", lambda *a: note[0])
    state_dir = tmp_path / "st"
    store.write_object(state_dir / store.STATE_NAME.format(pr=7), {**_left_state(), "target_repo": "o/r"})
    cfg = types.SimpleNamespace(state_dir=state_dir, pr=7, main=tmp_path)
    got = types.SimpleNamespace(review=types.SimpleNamespace(findings=[], final=fin.CONVERGED), warnings=[], reused=True)
    (tmp_path / ".ndf").mkdir()
    settings_file = tmp_path / ".ndf" / "project.json"
    settings_file.write_text(json.dumps({"issues": {"primary": "markdown" if before == "markdown" else "github"}}), encoding="utf-8")

    def rerun() -> None:
        cli._report(cfg, got, lambda: cli.HUB(None, "o/r", tmp_path))

    rerun()
    rerun()
    assert len(hub.comments) == 1 and github.issues == []
    note[0] = None
    settings_file.write_text(json.dumps({"issues": {"primary": "github"}}), encoding="utf-8")
    rerun()
    assert len(hub.comments) == 2 and len(github.issues) == 1


def test_filing_skipped_after_a_rewind_takes_no_lock_of_the_origin_pr(tmp_path: Path, monkeypatch, hub) -> None:
    from ndf.domains.crossreview import cli

    hub.leftovers, hub.clean = lambda pr: pytest.fail("起票の口を作った"), list
    locked: list[int] = []

    @contextlib.contextmanager
    def lock(pr: int) -> Iterator[None]:
        locked.append(pr)
        yield

    store.write_object(tmp_path / store.STATE_NAME.format(pr=7), {**_left_state(), "current_pr": 9})
    monkeypatch.setattr(cli, "HUB", lambda *a: hub.make(*a)._replace(publishing=lock))
    monkeypatch.setattr(cli, "_main_of_cwd", lambda: tmp_path)
    assert cli.main(["report", "--state-dir", str(tmp_path), "--pr", "9"]) == 0
    assert locked == [9] and len(hub.comments) == 1


def test_filing_after_a_rewind_also_takes_the_lock_of_the_origin_pr(repos: Repos, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    github = _filing(monkeypatch, hub, repos)
    locked: list[int] = []

    @contextlib.contextmanager
    def lock(pr: int) -> Iterator[None]:
        locked.append(pr)
        yield

    from ndf.domains.crossreview import cli

    monkeypatch.setattr(cli, "HUB", lambda *a: hub.make(*a)._replace(publishing=lock))
    state_dir = tmp_path / "st"
    store.write_object(state_dir / store.STATE_NAME.format(pr=7), {**_left_state(), "current_pr": 9})
    _report_of(state_dir, capsys, 9)
    assert locked == [9, 7] and len(github.issues) == 1


def test_issue_3780_2_results_carry_how_each_finding_was_left() -> None:
    from ndf.domains.crossreview import cli

    data, url = _left_state(), "https://github.com/o/r/issues/100"
    data["findings"][0]["issue"] = url
    record = "https://github.com/o/r/pull/7#issuecomment-1"
    items = [{"kind": "finding", "id": f["id"], "outcome": f["outcome"]} for f in data["findings"]]
    items.append({"kind": "comment", "number": 7, "url": record})
    verdict = cli._with_leftovers(fin.Verdict(fin.OK, None, "要約", (), (), ()), items, data)
    assert [(i.get("disposition"), i.get("url")) for i in items[:2]] == [("issue", url), ("comment", record)]
    assert result.errors(result.result("cross-review", result.OK, verdict.summary, items), 0) == []
    assert "残した指摘 2（起票 1）" in verdict.summary
    [decision] = verdict.decisions
    assert all(part in decision["decision"] + decision["basis"] for part in ("r2-1", "Value 6", url))
    # 照らした位置の記録が無い行は、行で確かめたと言わない
    assert "照らした位置の記録が無い" in decision["basis"] and "変えていない行" not in decision["basis"]
    data["findings"][0]["parked_on_line"] = True
    [decision] = leftover_decide.decisions(data["findings"], record)
    assert "変えていない行" in decision["basis"]
    # 理由は行に残った初出時の位置でなく、残すと決めたときに照らした位置から出す
    data["findings"][0]["parked_on_line"] = False
    [decision] = leftover_decide.decisions(data["findings"], record)
    assert "行を特定していない" in decision["basis"] and "変えていない行" not in decision["basis"]


def test_issue_3780_2_run_results_point_a_left_finding_at_the_record(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    fake, got, state = _rounds(tmp_path, capsys, monkeypatch, wt, [_said("古い行の指摘")])
    [left] = [f["id"] for f in state["findings"] if f.get("parked")]
    found = {i["id"]: i for i in got["items"] if i["kind"] == "finding"}
    assert (found[left]["disposition"], found[left]["url"]) == ("comment", state[report_decide.COMMENT_KEY])
    assert "残した指摘 1（起票 0）" in got["summary"]
    # 起票に失敗した指摘があれば、前回と同じ状態でも記録を書き直す
    path = store.state_pr(fake.state_dir, PR).path
    row = next(f for f in state["findings"] if f["id"] == left)
    row |= {"check": {"tracks": ["delivered"]}, "issue_error": "書けなかった"}
    state["target_repo"] = "o/r"
    state[report_decide.PUBLISHED_KEY] = report_decide.published_state(PR, state, fin.CONVERGED)
    store.write_object(path, state)
    written = len(hub.comments)
    code, again = _run(capsys, monkeypatch, Fake(fake.state_dir, {"codex": [], "kiro": []}), wt)
    assert code == 0 and len(hub.comments) == written + 1, again
    # 起票の記録へ書けていない URL があれば、行に失敗が無くても公開し直す
    state = store.read_object(path)
    for row in state["findings"]:
        row.pop("issue_error", None)
        row.pop("check", None)
    state.pop("issue_skipped", None)
    state["issue_unrecorded"] = {"0123456789ab": "https://github.com/o/r/issues/100"}
    state[report_decide.PUBLISHED_KEY] = report_decide.published_state(PR, state, fin.CONVERGED)
    store.write_object(path, state)
    written = len(hub.comments)
    code, again = _run(capsys, monkeypatch, Fake(fake.state_dir, {"codex": [], "kiro": []}), wt)
    assert code == 0 and len(hub.comments) == written + 1, again
