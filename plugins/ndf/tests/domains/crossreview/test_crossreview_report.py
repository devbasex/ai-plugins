"""cross-review の記録を PR へ残す（#3939）: 記録のコメント・指摘のスレッド・閉じた指摘の返信と resolve。"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Optional

import pytest

from ndf.adapters import publish
from ndf.adapters.github import GitHubError
from ndf.contracts import result
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview import journal, report_decide, store, threads
from ndf.domains.records.mvv import Snapshot

from .conftest import FakePort
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


def test_report_writes_the_record_from_a_left_state_file(tmp_path: Path, capsys, hub) -> None:
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

    def graphql(self, query, variables):
        if query == threads.VIEWER:
            self.viewer_reads = getattr(self, "viewer_reads", 0) + 1
            return {"viewer": {"login": "ndf-bot"}}
        return super().graphql(query, variables)


def test_github_port_marks_only_threads_written_by_the_viewer() -> None:
    """PR 3944 の r1-3: cross-review の書式のスレッドは、最初のコメントの書き手が今の実行の認証の login（viewer）と同じときだけ
    信頼する。書き込みの権限を持つ別の人が同じ書式で書いたものも信頼しない。viewer は 1 回だけ読む。"""
    hub = _ThreadsHub()
    port = threads.GitHubPort(hub, _Publisher(), 7, _Refused)
    assert [(t.comment, t.trusted) for t in port.open_threads()] == [(1, True), (2, False), (3, False)]
    port.open_threads()
    assert hub.viewer_reads == 1


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
    once = publish.once(clean)
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

    once = publish.once(clean)
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

    once = publish.once(clean)
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
