"""cross-review の記録を PR へ残す（#3939）: 記録のコメント・指摘のスレッド・閉じた指摘の返信と resolve。"""

from __future__ import annotations

import json
from pathlib import Path

from ndf.contracts import result
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview import report_decide, store, threads

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
    assert f"| 1 | `{HEAD1[:9]}` | full | changes_required | `{FIX1[:9]}` | 送った | r1-1 |" in body
    # 表のセルは改行と縦棒で崩れない。見送った理由が載る
    assert "| r1-2 | kiro | — | src/b.py#g | 命名が \\| 揃わない 2 行目 | 見送った | 設計で決めた範囲の外 |" in body
    assert "| r2-1 | codex | — | doc.md#セクション | 根拠が無い | 未解決 | — |" in body


def test_location_uses_the_line_only_when_the_text_names_the_same_file() -> None:
    assert threads.location({"section": "src/a.py#f", "text": "a.py:12 で"}) == ("src/a.py", 12)
    assert threads.location({"section": "src/a.py#f", "text": "other.py:12 で"}) == ("src/a.py", None)
    assert threads.location({"section": "", "text": "a.py:12"}) == (None, None)


def test_sync_opens_threads_replies_and_resolves_closed_findings_once() -> None:
    data, port = _state(), FakePort()
    assert threads.sync(data, port) == []
    # 行に付けるのは本文の先頭がそのファイルを指すときだけ。スレッドはその指摘のラウンドの head に付ける
    assert [(p, line, c) for p, line, c, _ in port.opened] == [("src/a.py", 12, HEAD1), ("src/b.py", None, HEAD1), ("doc.md", None, HEAD2)]
    # 直した指摘は修正のコミットを、見送った指摘は理由を返信して resolve する。未解決は開いたまま
    assert port.replies == [(1001, f"直した（修正のコミット {FIX1[:9]}）"), (1002, "見送った: 設計で決めた範囲の外")]
    assert port.resolved == [1001, 1002]
    # 打ち直しても同じものを書かない
    threads.sync(data, port)
    assert (len(port.opened), len(port.replies), len(port.resolved)) == (3, 2, 2)
    # 後のラウンドで未解決の指摘を直したら、そのときに返信して resolve する
    data["findings"][2]["outcome"] = "fixed"
    threads.sync(data, port)
    assert port.replies[-1] == (1003, "直した") and port.resolved[-1] == 1003


class _Refused(Exception):
    status = 422


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


def test_github_port_falls_back_to_the_file_and_resolves_the_thread() -> None:
    hub = _RestHub()
    port = threads.GitHubPort(hub, _Publisher(), 7, _Refused)
    assert port.open_thread("src/a.py", 12, HEAD1, "本文") == 55
    # PR の差分に無いファイルにはスレッドを作らない（書かずに None）
    assert port.open_thread("issues/design.md", None, HEAD1, "本文") is None
    assert hub.posts == [("/repos/o/r/pulls/7/comments", {"body": "本文", "commit_id": HEAD1, "path": "src/a.py", "subject_type": "file"})]
    port.reply(55, "直した")
    port.resolve(55)
    assert hub.posts[-1] == ("/repos/o/r/pulls/7/comments/55/replies", {"body": "直した"})
    assert hub.mutations == [{"id": "T1"}]


def test_sync_failure_is_a_warning_per_finding() -> None:
    data = _state()
    notes = threads.sync(data, FakePort(fail=True))
    # スレッドを作れなかった指摘は注意に残し、済んだ記録を残さない（打ち直せば作り直す）
    assert len(notes) == 3 and all("スレッドを書けなかった" in n for n in notes)
    assert data[threads.THREADS] == {"r1-1": {}, "r1-2": {}, "r2-1": {}}


def test_run_posts_the_record_with_the_fix_and_returns_its_url(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    fake = Fake(tmp_path / "st", {"codex": [["一つ目"], []], "kiro": [[], []]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert code == 0
    [(slug, pr, body, previous)] = hub.comments
    assert pr == PR and previous is None and body.startswith(report_decide.MARKER)
    fix = store.read_object(store.state_pr(fake.state_dir, PR).path)["rounds"][0]["fix"]
    assert f"`{fix['commit'][:9]}` | 送った | r1-1 |" in body
    assert {"kind": "comment", "number": PR, "url": f"https://github.com/{slug}/pull/{PR}#issuecomment-1"} in got["items"]


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
    assert len(hub.port.opened) == 3 and hub.port.resolved == [1001, 1002]
    # 済んだスレッドは状態ファイルへ残り、打ち直しても二重に書かない。記録のコメントは前のコメントを書き換える
    cli.main(["report", "--state-dir", str(state_dir), "--pr", "7"])
    assert len(hub.port.opened) == 3
    assert hub.comments[-1][3] == "https://github.com/acme/repo/pull/7#issuecomment-1"


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


class _MovedHub(_RestHub):
    """指摘のラウンドの head ではパスを解決できず（422）、PR の今の head では付けられる。"""

    def __init__(self):
        super().__init__()
        self.commits: list[str] = []

    def rest(self, method, path, payload=None, params=None):
        if method == "GET" and path.endswith("/pulls/7"):
            return {"head": {"sha": HEAD2}}
        if method == "GET":
            return [{"filename": "src/a.py"}]
        self.commits.append(payload["commit_id"])
        if payload["commit_id"] == HEAD1:
            raise _Refused('GitHub が断った（422）: {"message":"pull_request_review_thread.path could not be resolved"}')
        self.posts.append((path, payload))
        return {"id": 56}


def test_github_port_reposts_on_the_pr_head_when_the_path_is_not_resolved() -> None:
    """PR 3944 の書き足しで見つけた: 指摘のラウンドの head でパスを解決できなければ、PR の今の head のコミットで付け直す。
    行の位置を断られたとは扱わない（ファイルへ落とさず、同じ行に付ける）。"""
    hub = _MovedHub()
    port = threads.GitHubPort(hub, _Publisher(), 7, _Refused)
    assert port.open_thread("src/a.py", 12, HEAD1, "本文") == 56
    assert hub.commits == [HEAD1, HEAD2]
    assert hub.posts[-1][1]["line"] == 12
    assert threads.path_unresolved(422, "x could not be resolved") and not threads.path_unresolved(422, "line must be part of the diff")


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
    """PR 3944 の r5-2: 持ち主（report_pr）の無い旧形式の記録は、巻き直す前なら元の PR のものとして使い、巻き直した後なら
    コメントとスレッドの id を使わずに退避する。"""
    legacy = {**_state(), "report_comment": "https://github.com/o/r/pull/7#issuecomment-5", "threads": {"r1-1": {"comment": 1}}}
    kept = json.loads(json.dumps(legacy))
    assert report_decide.bind_pr(kept, 7, 7)
    assert kept["report_comment"] == legacy["report_comment"] and kept["report_pr"] == 7
    moved = json.loads(json.dumps(legacy))
    assert report_decide.bind_pr(moved, 8, 7)
    assert "report_comment" not in moved and "threads" not in moved
    assert moved["report_earlier"][report_decide.UNKNOWN_OWNER]["report_comment"] == legacy["report_comment"]
