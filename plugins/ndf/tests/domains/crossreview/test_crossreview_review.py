"""指摘をコミットごとの 1 つのレビューで書き、書く前に PR から記録を戻す（#4016 の決定 4・5・6、受け入れ条件 1・2・6）。"""

from __future__ import annotations

import contextlib
import copy
import threading
from pathlib import Path

import pytest

from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview import journal, prior_decide, report_decide, store, threads

from .conftest import FakePort

HEAD1, HEAD2 = "a" * 40, "b" * 40
# 収束の妨げになる重さの指摘が下げられないための 1 文（#4014）
IMPACT = "保存が止まり、利用者の入力が消える"


def _finding(fid: str, round_no: int, path: str, line=None, outcome: str = fin.UNRESOLVED, **extra) -> dict:
    row = {"id": fid, "by": "codex", "round": round_no, "outcome": outcome, "reason": None, "severity": "major", "text": f"{fid} の指摘"}
    row |= {"path": path} | ({"line": line} if line else {}) | extra
    return row


def _data(*findings: dict) -> dict:
    data = {"pr": 7, "rounds": [{"round": 1, "head": HEAD1}, {"round": 2, "head": HEAD2}], "findings": list(findings)}
    journal.with_occurrences(data)
    return data


def test_findings_of_one_commit_are_written_in_one_review() -> None:
    """受け入れ条件 1: 1 つのコミットの N 件はレビュー 1 回・1 件ずつの作成 0 回。2 つのコミットなら 2 回。"""
    port = FakePort()
    data = _data(_finding("r1-1", 1, "a.py", 3), _finding("r1-2", 1, "b.py", 4), _finding("r2-1", 2, "a.py", 9))
    assert threads.sync(data, port) == []
    assert [(c, [p.fid for p in placed]) for c, placed in port.batches] == [(HEAD1, ["r1-1", "r1-2"]), (HEAD2, ["r2-1"])]
    assert port.singles == [] and port.replies == []
    assert {fid: m["comment"] for fid, m in data[threads.THREADS].items()} == {"r1-1": 1001, "r1-2": 1002, "r2-1": 1003}
    # 変更ファイルは 1 回、スレッドは書く前と書いた後の 2 回だけ読む
    assert (port.changed_reads, port.thread_reads) == (1, 2)


def test_findings_without_a_line_are_not_read_again_after_writing() -> None:
    """1 件ずつの作成は応答の id を使い、書いた後にスレッドを読み直さない（書く前の 1 回だけ）。"""
    port = FakePort()
    data = _data(_finding("r1-1", 1, "a.py"), _finding("r1-2", 1, "b.py"))
    assert threads.sync(data, port) == []
    assert (len(port.singles), port.batches, port.thread_reads) == (2, [], 1)
    assert {fid: m["comment"] for fid, m in data[threads.THREADS].items()} == {"r1-1": 1001, "r1-2": 1002}


def test_thread_is_not_resolved_when_the_reply_fails() -> None:
    """引き継いだ指摘を閉じる返信が失敗したら resolve せず、次の実行で返信してから閉じる。"""

    class NoReply(FakePort):
        def reply(self, comment_id, body):
            raise RuntimeError("書けない")

    port = NoReply()
    port.left = [prior_decide.Thread(77, "前の実行のスレッド", False, "a.py", 3, True)]
    data = _data(_finding("r1-1", 1, "a.py", 3, fin.FIXED))
    data[threads.THREADS] = {"r1-1": {"comment": 77}}
    assert threads.sync(data, port) and port.resolved == []
    again = FakePort()
    again.left = port.left
    assert threads.sync(data, again) == []
    assert (len(again.replies), again.resolved) == (1, [77])


def test_nothing_is_read_or_written_without_findings() -> None:
    port = FakePort()
    assert threads.sync(_data(), port) == []
    assert (port.changed_reads, port.thread_reads, port.opened) == (0, 0, [])


def test_finding_outside_the_diff_stays_out_of_the_review() -> None:
    """差分に無いファイルの指摘は書かず、組の残りはレビュー 1 回で書く（記録のコメントの表には載る）。"""
    port = FakePort(files={"a.py"})
    data = _data(_finding("r1-1", 1, "a.py", 3), _finding("r1-2", 1, "gone.py", 4))
    assert threads.sync(data, port) == []
    assert [[p.fid for p in placed] for _, placed in port.batches] == [["r1-1"]]
    assert "r1-2" not in data[threads.THREADS] and port.changed_reads == 1
    assert "| r1-2 |" in report_decide.comment(7, data, None)


def test_review_refused_for_a_line_falls_back_to_one_thread_each() -> None:
    """決定 5: 行の位置で断られた組だけを 1 件ずつ書く。閉じた指摘の本文には閉じた理由を入れる。"""
    port = FakePort()
    port.refuse_position = True
    data = _data(_finding("r1-1", 1, "a.py", 3, fin.FIXED), _finding("r1-2", 1, "b.py", 4))
    assert threads.sync(data, port) == []
    assert [(p, line) for p, line, _, _ in port.singles] == [("a.py", 3), ("b.py", 4)]
    assert threads.CLOSED_HEAD in port.singles[0][3] and data[threads.THREADS]["r1-1"]["replied"]
    assert port.replies == [] and port.resolved == [1001]


def test_review_refused_for_another_reason_is_not_split() -> None:
    """スパムの判定・原因の分からない拒否は 1 件ずつに展開せず、組の指摘ごとに注意へ残す。"""
    port = FakePort(fail=True)
    data = _data(_finding("r1-1", 1, "a.py", 3), _finding("r1-2", 1, "b.py", 4))
    notes = threads.sync(data, port)
    assert [n.split(" のスレッド")[0] for n in notes] == ["指摘 r1-1", "指摘 r1-2"]
    assert port.singles == [] and data[threads.THREADS] == {}


def test_rerun_without_the_saved_record_restores_from_the_pr() -> None:
    """不変条件 I4: 記録を保存せずに打ち直しても、PR の自分のスレッドから記録を戻し、レビュー・返信・resolve を増やさない。"""
    port = FakePort()
    data = _data(_finding("r1-1", 1, "a.py", 3, fin.FIXED), _finding("r1-2", 1, "b.py"))
    unsaved = copy.deepcopy(data)
    threads.sync(data, port)
    assert (len(port.batches), len(port.singles), port.resolved) == (1, 1, [1001])
    assert threads.sync(unsaved, port) == []
    assert (len(port.batches), len(port.singles), port.replies, port.resolved) == (1, 1, [], [1001])
    assert unsaved[threads.THREADS] == data[threads.THREADS]


def test_rerun_after_a_reply_does_not_reply_again() -> None:
    """返信の後・resolve の前に止まった打ち直しは、PR の返信の発生の値を見て返信 0 回・resolve 1 回。"""

    class Stops(FakePort):
        def resolve(self, comment_id):
            raise RuntimeError("止まった")

    port = Stops()
    port.left = [prior_decide.Thread(77, "前の実行のスレッド", False, "a.py", 3, True)]
    data = _data(_finding("r1-1", 1, "a.py", 3, fin.FIXED))
    data[threads.THREADS] = {"r1-1": {"comment": 77}}
    unsaved = copy.deepcopy(data)
    assert threads.sync(data, port) and len(port.replies) == 1
    again = FakePort()
    again.left, again.replies = port.left, list(port.replies)
    assert threads.sync(unsaved, again) == []
    assert (len(again.replies), again.resolved) == (1, [77])


def test_threads_of_another_writer_or_occurrence_are_not_taken() -> None:
    """受け入れ条件 6: ほかの書き手のスレッド・同じ番号で発生の値の違うスレッド・発生の値の無いスレッドは拾わずに書く。"""
    finding = _finding("r1-1", 1, "a.py", 3)
    data = _data(finding)
    mine = threads.opening(data["findings"][0])
    other = threads.opening({**data["findings"][0], "occurrence": "000000000000"})
    legacy = threads.opening({k: v for k, v in data["findings"][0].items() if k != "occurrence"})
    port = FakePort()
    port.left = [
        prior_decide.Thread(1, mine, False, "a.py", 3, False),
        prior_decide.Thread(2, other, True, "a.py", 3, True),
        prior_decide.Thread(3, legacy, True, "a.py", 3, True),
    ]
    threads.sync(data, port)
    assert len(port.batches) == 1 and data[threads.THREADS]["r1-1"]["comment"] == 1001
    assert threads.restored({k: v for k, v in finding.items() if k != "occurrence"}, port.left[0]._replace(trusted=True)) is None


def test_revived_finding_is_written_again_not_bound_to_the_old_thread() -> None:
    """再発した指摘（新しい発生の値）は、同じ番号の resolve 済みの古いスレッドに結び付かず新しく書かれる。"""
    rows = journal.FindingJournal()
    rows.enter(1, [{"text": "A が止まる", "severity": "major", "impact": IMPACT, "path": "a.py", "line": 3, "by": "codex"}])
    old = threads.opening(rows.rows["r1-1"])
    rows.rows["r1-1"]["outcome"] = fin.FIXED
    rows.enter(2, [{"text": "A が止まる", "severity": "major", "impact": IMPACT, "path": "a.py", "line": 5, "by": "codex"}])
    port = FakePort()
    port.left = [prior_decide.Thread(77, old, True, "a.py", 3, True)]
    data = {"rounds": [{"round": 1, "head": HEAD1}, {"round": 2, "head": HEAD2}], "findings": list(rows.rows.values())}
    threads.sync(data, port)
    assert data[threads.THREADS]["r1-1"]["comment"] == 1001 and len(port.batches) == 1


def test_finding_of_reads_the_number_occurrence_and_closing() -> None:
    finding = _finding("r1-1", 1, "a.py", 3, occurrence="0123456789ab")
    assert threads.finding_of(threads.opening(finding)) == threads.Mark("r1-1", "0123456789ab", False)
    assert threads.finding_of(threads.opening(finding, "直した")) == threads.Mark("r1-1", "0123456789ab", True)
    assert threads.finding_of("人の指摘") is None
    # 引き継ぎは発生の値の行があっても指摘の本文を読む
    [carried] = prior_decide.carried([prior_decide.Thread(5, threads.opening(finding), False, "a.py", 3, True)], ())
    assert carried.text == "r1-1 の指摘"


@pytest.mark.parametrize(
    ("status", "reasons", "position", "unresolved"),
    [
        (422, ["pull_request_review_thread.line must be part of the diff"], True, False),
        (422, ["pull_request_review_thread.path could not be resolved"], True, True),
        (422, ["x" * 400, "y", "pull_request_review_thread.line must be part of the diff"], True, False),
        (422, ["was submitted too quickly"], False, False),
        (422, ["Validation Failed"], False, False),
        (403, ["line must be part of the diff"], False, False),
        (422, ["path could not be resolved", "was submitted too quickly"], False, False),
        (422, ["line must be part of the diff", "You have exceeded a secondary rate limit"], False, False),
    ],
)
def test_position_refusal_is_read_from_every_reason(status, reasons, position, unresolved) -> None:
    assert threads.position_refused(status, reasons) is position
    assert threads.path_unresolved(status, reasons) is unresolved


@pytest.mark.parametrize(
    ("status", "reasons", "position", "unresolved"),
    [
        # 理由がなければどちらも偽
        (422, [], False, False),
        # 位置の語は大文字小文字を問わない
        (422, ["Pull_Request_Review_Thread.Line MUST BE part of the diff"], True, False),
        (422, ["PATH COULD NOT BE RESOLVED"], True, True),
        # diff_hunk も位置の語
        (422, ["pull_request_review_comment.diff_hunk is invalid"], True, False),
        # 除外の語も大文字小文字を問わず、同じ理由の中にあっても効く
        (422, ["line must be part of the diff (Rate Limit)"], False, False),
        (422, ["path could not be resolved: SPAM detected"], False, False),
        (422, ["line must be part of the diff", "flagged as spam"], False, False),
        # 422 以外は解決できないパスでも偽
        (403, ["path could not be resolved"], False, False),
        (400, ["path could not be resolved"], False, False),
        # 位置の語と解決できないパスが別の理由に並べば両方とも真
        (422, ["line must be part of the diff", "path could not be resolved"], True, True),
        # 位置の語が無い理由だけでは偽（除外の語が無くても）
        (422, ["Not Found", "Validation Failed"], False, False),
    ],
)
def test_position_refusal_ignores_case_and_any_refusal_reason(status, reasons, position, unresolved) -> None:
    assert threads.position_refused(status, reasons) is position
    assert threads.path_unresolved(status, reasons) is unresolved
    # 理由は tuple でも読む
    assert threads.position_refused(status, tuple(reasons)) is position
    assert threads.path_unresolved(status, tuple(reasons)) is unresolved


class _Refused(Exception):
    """`github.GitHubError` の形の偽物（状態・理由・二次レート制限でない）。"""

    def __init__(self, status: int, *reasons: str):
        super().__init__(reasons[0] if reasons else "")
        self.status, self.reasons = status, reasons
        self.retry_after, self.secondary, self.hinted = None, False, False


class _Hub:
    """REST の偽物。`refuse` の順に断り、送った要求を残す。"""

    repo = "o/r"

    def __init__(self, *refuse: Exception):
        self.refuse = list(refuse)
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[str] = []

    def rest(self, method, path, payload=None, params=None):
        if method == "GET":
            self.gets.append(path)
            return [{"filename": "a.py"}] if path.endswith("/files") else {"head": {"sha": HEAD2}}
        self.posts.append((path, payload))
        if self.refuse:
            raise self.refuse.pop(0)
        return {"id": len(self.posts)}


class _Guard:
    """公開の検査の偽物（呼んだ回数を数え、秘密の形の文字列を伏せる）。"""

    def __init__(self):
        self.calls = 0

    def guarded(self, *texts):
        self.calls += 1
        return [t.replace("ghp_secret", "[REDACTED]") for t in texts], {}


def test_open_review_guards_every_body_once_and_posts_one_review() -> None:
    hub, guard = _Hub(), _Guard()
    port = threads.GitHubPort(hub, guard, 7, _Refused, sleep=lambda s: None)
    placed = [
        threads.Placed("r1-1", "a.py", 3, "ghp_secret を出す"),
        threads.Placed("r1-2", "a.py", 9, f"本文\n{threads.CLOSED_HEAD}\n\nghp_secret"),
    ]
    assert port.open_review(HEAD1, placed) == 1
    [(path, payload)] = hub.posts
    assert path == "/repos/o/r/pulls/7/reviews" and guard.calls == 1
    assert (payload["commit_id"], payload["event"]) == (HEAD1, "COMMENT")
    # レビュー本体の本文（GitHub の必須）は行のコメントの本文と別に送る
    assert payload["body"] == threads.review_body(2) and payload["body"].startswith(report_decide.MARKER)
    assert [(c["path"], c["line"], c["side"]) for c in payload["comments"]] == [("a.py", 3, "RIGHT"), ("a.py", 9, "RIGHT")]
    assert all("ghp_secret" not in c["body"] for c in payload["comments"])


def test_open_review_turns_only_a_position_refusal_into_position_refused() -> None:
    port = threads.GitHubPort(_Hub(_Refused(422, "line must be part of the diff")), _Guard(), 7, _Refused, sleep=lambda s: None)
    with pytest.raises(threads.PositionRefused):
        port.open_review(HEAD1, [threads.Placed("r1-1", "a.py", 3, "本文")])
    port = threads.GitHubPort(_Hub(_Refused(422, "was submitted too quickly")), _Guard(), 7, _Refused, sleep=lambda s: None)
    with pytest.raises(_Refused):
        port.open_review(HEAD1, [threads.Placed("r1-1", "a.py", 3, "本文")])


def test_open_thread_moves_to_the_file_only_on_a_position_refusal() -> None:
    hub = _Hub(_Refused(422, "line must be part of the diff"))
    assert threads.GitHubPort(hub, _Guard(), 7, _Refused).open_thread("a.py", 3, HEAD1, "本文") == 2
    assert hub.posts[1][1]["subject_type"] == "file"
    for reasons in (("was submitted too quickly",), ("Validation Failed",), ("path could not be resolved", "secondary rate limit")):
        hub = _Hub(_Refused(422, *reasons))
        with pytest.raises(_Refused):
            threads.GitHubPort(hub, _Guard(), 7, _Refused).open_thread("a.py", 3, HEAD1, "本文")
        # 付け直さず、PR の head も読まない
        assert len(hub.posts) == 1 and hub.gets == ["/repos/o/r/pulls/7/files"]


def test_open_thread_posts_again_on_the_head_when_the_path_is_unresolved() -> None:
    hub = _Hub(_Refused(422, "pull_request_review_thread.path could not be resolved"))
    assert threads.GitHubPort(hub, _Guard(), 7, _Refused).open_thread("a.py", None, HEAD1, "本文") == 2
    assert [p["commit_id"] for _, p in hub.posts] == [HEAD1, HEAD2]


def test_open_threads_keep_only_our_replies_other_than_the_first_comment() -> None:
    class Hub(_Hub):
        def repository(self, query, variables):
            first = {"databaseId": 1, "body": threads.opening(_finding("r1-1", 1, "a.py")), "author": {"login": "bot"}}
            replies = [
                first,
                {"databaseId": 2, "body": "直した", "author": {"login": "bot"}},
                {"databaseId": 3, "body": "人", "author": {"login": "alice"}},
            ]
            node = {
                "id": "T1",
                "isResolved": False,
                "path": "a.py",
                "line": 3,
                "comments": {"nodes": [first]},
                "replies": {"nodes": replies},
            }
            return {"pullRequest": {"reviewThreads": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [node]}}}

        def graphql(self, query, variables):
            return {"viewer": {"login": "bot"}}

    [thread] = threads.GitHubPort(Hub(), _Guard(), 7, _Refused).open_threads()
    assert thread.trusted and thread.replies == ("直した",)


def _state_dir(tmp_path: Path) -> Path:
    state_dir = tmp_path / "st"
    store.write_object(store.state_pr(state_dir, 7).path, {"pr": 7, **_data(_finding("r1-1", 1, "a.py", 3), _finding("r1-2", 1, "a.py"))})
    return state_dir


def test_concurrent_reports_on_one_pr_write_each_thread_once(tmp_path: Path) -> None:
    """不変条件 I4: 同じ PR への 2 つの公開を同時に走らせても、どの指摘のスレッドも 1 回だけ作る。"""
    from ndf.domains.crossreview import cli

    held, count, arrived, both = threading.Lock(), threading.Lock(), [], threading.Event()

    @contextlib.contextmanager
    def publishing(pr):
        with count:
            arrived.append(pr)
            if len(arrived) == 2:
                both.set()
        with held:
            yield

    class Racing(FakePort):
        def open_threads(self):
            # 先に入った公開は、もう一方がロックの手前に着くまで書く前の読みで止まる（実時間でなく到達の順で競わせる）
            both.wait(timeout=10)
            return super().open_threads()

    state_dir, port = _state_dir(tmp_path), Racing()
    hub = cli.Hub(lambda pr, body, previous: "https://github.com/o/r/pull/7#issuecomment-1", port.at, port.at, publishing=publishing)
    runs = [threading.Thread(target=cli._post_report, args=(state_dir, 7, None, (), lambda: hub)) for _ in range(2)]
    for run in runs:
        run.start()
    for run in runs:
        run.join()
    assert len(port.opened) == 2 and len(port.batches) == 1


def test_report_is_not_written_without_the_publishing_lock(tmp_path: Path) -> None:
    from filelock import Timeout

    from ndf.domains.crossreview import cli

    @contextlib.contextmanager
    def busy(pr):
        raise Timeout("pr.lock")
        yield

    port = FakePort()
    hub = cli.Hub(lambda pr, body, previous: "url", port.at, port.at, publishing=busy)
    with pytest.raises(cli.ReportFailed):
        cli._post_report(_state_dir(tmp_path), 7, None, (), lambda: hub)
    assert port.opened == []


def test_report_writes_occurrences_of_older_findings_before_the_threads(tmp_path: Path) -> None:
    """前のバージョンの指摘（発生の値が無い）には、スレッドを作る前に値を書いて状態ファイルへ保存する。"""
    from ndf.domains.crossreview import cli

    state_dir = tmp_path / "st"
    path = store.state_pr(state_dir, 7).path
    store.write_object(path, {"pr": 7, "rounds": [{"round": 1, "head": HEAD1}], "findings": [_finding("r1-1", 1, "a.py", 3)]})
    seen: list = []

    class Peek(FakePort):
        def open_review(self, commit, comments):
            seen.append(store.read_object(path)["findings"][0].get("occurrence"))
            return super().open_review(commit, comments)

    port = Peek()
    cli._post_report(state_dir, 7, None, (), lambda: cli.Hub(lambda pr, b, p: "url", port.at, port.at))
    assert seen and seen[0] == store.read_object(path)["findings"][0]["occurrence"]
    assert seen[0] in port.batches[0][1][0].body
