"""ラッパーの回答待ちの質問の扱い: 質問を閉じるまで残して 1 回だけ転送し、ロックの中で質問が無いと確かめてから子を止める（#3814）。

偽の子と偽の時計は `tests/support/relay_children.py` にある。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest
from ndf.contracts import limits
from ndf.domains.relay import accounts, signals, switch_decide, wrapper
from ndf.domains.relay.account_decide import Usage
from relay_children import (
    LIMIT_LINE,
    AskingChild,
    FakeChild,
    log_rows,
    rows_of,
    run_wrapper,
)


def test_question_that_cannot_reach_the_plan_keeps_the_reason(tmp_path, monkeypatch):
    # 計画のリクエストへ載せられない質問は、理由を状態と log.jsonl に残す（#3035）。質問のファイルは子が終わると消える（#3814）
    asked = []

    def ask_plan(path):
        asked.append(json.loads(path.read_text(encoding="utf-8"))["questions"])
        return "サブコマンド lanes question の結果を読めない（終了コード 2）: 壊れた"

    states = []
    real = wrapper.Wrapper._write_state

    def spy(self, state, reason):
        states.append((state, reason))
        return real(self, state, reason)

    monkeypatch.setattr(wrapper.Wrapper, "_write_state", spy)
    ended, _, w = run_wrapper(tmp_path, [AskingChild({1: "q"}, exit_at=3)], ask_plan=ask_plan)
    assert asked == [[{"question": "q"}]] and ended.cause == ""
    assert not signals.has_question(w.dir)
    assert any(state == "waiting" and "計画のリクエストへ載せられない" in reason for state, reason in states)
    assert [(r["ok"], "壊れた" in r["reason"]) for r in log_rows(w) if r["kind"] == "plan_question"] == [(False, True)]


def test_i1_forwarded_question_keeps_the_child_alive_past_the_marker_until_it_is_answered(tmp_path):
    # I1・受け入れ条件 1（要求仕様の「事象」の再現）: マーカーを書いた子が質問を出したまま生き続けても switch が出ない。
    # hook が質問を消した（12 回目の見回り）後に初めて切り替える
    first = AskingChild({1: "toolu_1", 12: None})
    ended, spawn, w = run_wrapper(tmp_path, [first, FakeChild([], 0)], markers={0: "/goal 次"})
    assert ended.cause == "" and len(spawn.calls) == 2
    assert first.killed is not None and first.killed[0] >= 12 and first.killed[1] is False
    assert [r["reason"] for r in rows_of(w, "forward")] == [switch_decide.FORWARD.format(id="toolu_1")]
    assert [r["reason"] for r in rows_of(w, "wait")][:1] == [switch_decide.QUESTION_PENDING]
    assert len(rows_of(w, "switch")) == 1


def _asked_ids(asked: list):
    def ask_plan(path):
        asked.append(json.loads(path.read_text(encoding="utf-8"))["tool_use_id"])
        return ""

    return ask_plan


def test_i2_one_question_is_forwarded_once_and_dropped_when_the_child_ends(tmp_path):
    # I2・受け入れ条件 5・6: 1 つの質問で forward と plan_question の行が 1 つずつ。子が終わると質問のファイルが消える
    asked: list = []
    ended, _, w = run_wrapper(tmp_path, [AskingChild({1: "A"}, exit_at=6)], ask_plan=_asked_ids(asked))
    assert ended.cause == "" and asked == ["A"]
    assert (len(rows_of(w, "forward")), len(rows_of(w, "plan_question"))) == (1, 1)
    assert not signals.question_present(w.dir)
    assert not (w.dir / wrapper.FORWARD_FILE).exists()


def test_i2_a_question_that_replaces_the_forwarded_one_is_forwarded_too(tmp_path):
    # I2・受け入れ条件 5: A を転送した次の見回りまでに hook が A を B へ置き換える（間に別の判断の行が無い）。行は A・B で 1 つずつ
    asked: list = []
    ended, _, w = run_wrapper(tmp_path, [AskingChild({1: "A", 2: "B", 5: None}, exit_at=7)], ask_plan=_asked_ids(asked))
    assert ended.cause == "" and asked == ["A", "B"]
    reasons = [r["reason"] for r in rows_of(w, "forward")]
    assert reasons == [switch_decide.FORWARD.format(id="A"), switch_decide.FORWARD.format(id="B")]
    assert len(rows_of(w, "plan_question")) == 2


SESSION = "11111111-1111-1111-1111-111111111111"


@pytest.mark.parametrize(
    "question",
    [
        {"session_id": SESSION, "tool_use_id": "old", "launch": "前の起動", "at": "x"},  # 同じ会話の起動し直しの前の子
        {"session_id": "s-other", "tool_use_id": "old", "at": "x"},  # launch の無い、セッションの違う質問
    ],
    ids=["launch", "session"],
)
def test_i3_a_question_of_another_launch_is_dropped_with_one_row(tmp_path, question):
    # I3・受け入れ条件 6: 今の子のものでない質問は転送せずに消し、log.jsonl に stale_question を 1 行残す
    def prepare(d, state):
        d.mkdir(parents=True, exist_ok=True)
        (d / signals.QUESTION_FILE).write_text(json.dumps(question), encoding="utf-8")

    ended, _, w = run_wrapper(tmp_path, [FakeChild([b"x\r\n", b"y\r\n"], 0)], prepare, first_session=SESSION, first_resume=True)
    assert ended.cause == "" and not signals.question_present(w.dir)
    assert len(rows_of(w, "stale_question")) == 1 and rows_of(w, "forward") == []


@pytest.mark.parametrize("content", [b"{", b'{"q": "\xff"}'], ids=["json", "utf8"])
def test_a_broken_question_file_is_waited_on_not_a_broken_config(tmp_path, content):
    # 決定 5: 壊れた質問のファイルは回答待ちとして扱い、転送せず子を止めない。question_broken を 1 行残す
    child = AskingChild({}, exit_at=6)

    def broken(directory, session_id, launch):
        child.where = (directory, session_id, launch)
        (directory / signals.QUESTION_FILE).write_bytes(content)

    child.started = broken
    ended, _, w = run_wrapper(tmp_path, [child])
    assert ended.cause == "" and child.killed is None
    assert len(rows_of(w, "question_broken")) == 1 and rows_of(w, "forward") == []
    assert [r["reason"] for r in rows_of(w, "wait")][:1] == [switch_decide.QUESTION_PENDING]
    assert not signals.question_present(w.dir)  # 子が終わると消える


# --- ロックの中で質問が無いと確かめてから子を止める（#3814 の Task 6） ---


def _over_context(monkeypatch) -> None:
    """文脈の量を上限（800,000）より多く読ませ、静かになったら引継ぎの手が出る形にする。"""
    monkeypatch.setattr(wrapper.observe, "context_tokens", lambda path, size: 900_000)


def _race_setup(kind: str, first: FakeChild, monkeypatch) -> tuple[dict, object]:
    """`kind` の手が出る形（設定の変更・マーカー・使用率・文脈の量）を返す。"""
    if kind == "handover":
        _over_context(monkeypatch)
        return {}, None
    if kind == "switch":

        def prepare(d, state):
            accounts.write_usages(state, [Usage("team", 95.0, False), Usage("main", 10.0, False)])
            accounts.use(state, "team")

        return {"markers": {0: "/goal 次"}}, prepare
    if kind == "account":
        first.outputs = [LIMIT_LINE]

        def prepare(d, state):
            accounts.write_usages(state, [Usage("team", 100.0, True), Usage("main", 5.0, False)])
            accounts.use(state, "team")

        return {}, prepare
    return {"stall": 3.0}, None


def _question_after_decide(monkeypatch, kind: str, child: "AskingChild", injected: list) -> None:
    """判定が `kind` を返した直後（`_state` の後・手を打つ前）に、子の hook が質問を書く競合を作る。"""
    real = switch_decide.decide

    def decide(state):
        step = real(state)
        if step.kind == kind and not injected:
            directory, session_id, launch = child.where
            injected.append(child.polls)
            payload = {"tool_use_id": "late", "tool_input": {"questions": [{"question": "late"}]}}
            assert signals.write_question(directory / signals.QUESTION_FILE, session_id, payload, 0.1, launch=launch)
        return step

    monkeypatch.setattr(wrapper.switch_decide, "decide", decide)


@pytest.mark.parametrize("kind", ["switch", "account", "restart", "handover"])
def test_i4_a_question_written_after_the_decision_keeps_the_child_alive(tmp_path, monkeypatch, kind):
    # I4・受け入れ条件 1・3・10: 判定の後に質問が書かれたら、ロックの中の確かめが外れて子を止めない。
    # hook が質問を消した（30 回目の見回り）後に初めて止める。切り替えの account の行は止めた後に書く
    first = AskingChild({30: None})
    changes, prepare = _race_setup(kind, first, monkeypatch)
    injected: list = []
    _question_after_decide(monkeypatch, kind, first, injected)
    ended, spawn, w = run_wrapper(tmp_path, [first, FakeChild([], 0)], prepare, **changes)
    assert ended.cause == "" and len(spawn.calls) == 2
    assert injected and injected[0] < 30
    assert first.killed is not None and first.killed[0] >= 30 and first.killed[1] is False
    assert [r["reason"] for r in rows_of(w, "forward")] == [switch_decide.FORWARD.format(id="late")]
    if kind == "switch":
        rows = log_rows(w)
        forward_at = next(i for i, r in enumerate(rows) if r["kind"] == "forward")
        account_at = [i for i, r in enumerate(rows) if r["kind"] == "account"]
        assert len(account_at) == 1 and account_at[0] > forward_at


class LockWitness(FakeChild):
    """`terminate` の中で、別のスレッドの hook が質問を書こうとする（ロックを持ったまま止めているかを見る）。"""

    def __init__(self):
        super().__init__([b"working\r\n"], None)
        self.where: tuple[Path, str, str] | None = None
        self.wrote_while_stopping: bool | None = None

    def started(self, directory: Path, session_id: str, launch: str) -> None:
        self.where = (directory, session_id, launch)

    def hook_writes(self) -> bool:
        directory, session_id, launch = self.where
        payload = {"tool_use_id": "stale", "tool_input": {"questions": []}}
        return signals.write_question(directory / signals.QUESTION_FILE, session_id, payload, 0.3, launch=launch)

    def terminate(self, force=False):
        got: list = []
        hook = threading.Thread(target=lambda: got.append(self.hook_writes()))
        hook.start()
        hook.join()
        self.wrote_while_stopping = got[0]
        return super().terminate(force)


class AfterStop(FakeChild):
    """次の子。最初の見回りで、止めた前の子の hook が遅れて質問を書こうとした結果を残す。"""

    def __init__(self, previous: LockWitness):
        super().__init__([], 0)
        self.previous = previous
        self.late_write: bool | None = None

    def isalive(self):
        if self.late_write is None:
            self.late_write = self.previous.hook_writes()
        return super().isalive()


def test_i4_the_child_is_stopped_holding_the_lock_and_its_late_hook_cannot_write(tmp_path):
    # I4・受け入れ条件 10: 止めている間は hook がロックを取れず書けない。止めた後に前の子の hook が書こうとしても、
    # 今の起動から外れているので書けず、次の子の質問のファイルは空のまま
    first = LockWitness()
    second = AfterStop(first)
    ended, spawn, w = run_wrapper(tmp_path, [first, second], markers={0: "/goal 次"})
    assert ended.cause == "" and len(spawn.calls) == 2
    assert first.wrote_while_stopping is False
    assert second.late_write is False
    assert not signals.question_present(w.dir)


class Unkillable(FakeChild):
    """強制終了を受けても `exit_after` 回の見回りまで終わらない（None なら終わらない）。ptyprocess の偽の戻り値を真似る。"""

    def __init__(self, exit_after: int | None):
        super().__init__([b"working\r\n"], None)
        self.exit_after = exit_after
        self.after_kill: int | None = None

    def isalive(self):
        if self.after_kill is not None:
            self.after_kill += 1
            if self.exit_after is not None and self.after_kill >= self.exit_after:
                self.alive, self.exitstatus = False, -9
            return self.alive
        return super().isalive()

    def terminate(self, force=False):
        self.after_kill = 0
        return False


@pytest.mark.parametrize("changes", [{"markers": {0: "/goal 次"}}, {"stall": 3.0}], ids=["switch", "restart"])
def test_i4_a_child_that_outlives_the_kill_starts_no_next_child(tmp_path, changes):
    # I4・受け入れ条件 7: 強制終了しても relay.child-exit の秒のうちに終わらない子では、次の子を起動せず refused で終わる
    ended, spawn, w = run_wrapper(tmp_path, [Unkillable(None), FakeChild([], 0)], **changes)
    seconds = limits.seconds("relay.child-exit")
    assert ended == wrapper.Ended("refused", wrapper.CHILD_ALIVE.format(pid=4242, seconds=seconds))
    assert len(spawn.calls) == 1
    assert log_rows(w)[-1]["kind"] == "end"


def test_i4_a_child_that_ends_within_the_wait_after_the_kill_is_replaced(tmp_path):
    # I4・受け入れ条件 7 の境界: 強制終了が偽を返しても、待ちのうちに終わった子は止めたとして次の子を起動する
    ended, spawn, _ = run_wrapper(tmp_path, [Unkillable(2), FakeChild([], 0)], markers={0: "/goal 次"})
    assert ended.cause == "" and len(spawn.calls) == 2


# --- 引継ぎを子の端末へ書かず、同じ会話の起動し直しで頼む（#3814 の Task 7） ---


def test_i6_handover_restarts_the_same_conversation_without_writing_to_the_child(tmp_path, monkeypatch):
    # I6・受け入れ条件 2: 引継ぎは子の端末へ書かず、子を止めて --resume <同じ ID> <引継ぎを頼む文> で起動し直す。
    # 起動し直しの回数には数えず、次の子では引継ぎを頼み直さない。起動ごとに起動の ID が違う
    _over_context(monkeypatch)
    first = FakeChild([b"working\r\n"], None)
    ended, spawn, w = run_wrapper(tmp_path, [first, FakeChild([], 0)])
    assert ended.cause == ""
    (one, one_env), (two, two_env) = spawn.calls
    assert two == [wrapper.CLAUDE, "--resume", one[2], "--model", "x", switch_decide.HANDOVER_PROMPT]
    assert first.written == [] and first.alive is False
    assert w.section.number == 1 and w.section.handover_asked is True
    assert w.starts["count"] == 0
    assert one_env["NDF_RELAY_LAUNCH"] != two_env["NDF_RELAY_LAUNCH"]
    assert len(rows_of(w, "handover")) == 1


# --- 質問の状態を読めないときと例外の経路でも子を止めない（#3814 の Task 8） ---


def _tamper_question_file_on_start(child: AskingChild, tamper) -> None:
    """子の起動時に、起動先を `child.where` へ残してから質問のファイルのパスを `tamper` へ渡して細工させる。"""

    def started(directory, session_id, launch):
        child.where = (directory, session_id, launch)
        tamper(directory / signals.QUESTION_FILE)

    child.started = started


def _make_unreadable(path: Path) -> None:
    path.write_text("{}", encoding="utf-8")
    path.chmod(0)


@pytest.mark.skipif(os.geteuid() == 0, reason="root は読む権限の無いファイルも読める")
def test_an_unreadable_question_file_is_waited_on_not_a_broken_config(tmp_path):
    # 決定 5: 読む権限の無い質問のファイルも回答待ちとして扱い、転送せず子を止めない。question_broken を 1 行残す
    child = AskingChild({}, exit_at=6)
    _tamper_question_file_on_start(child, _make_unreadable)
    ended, _, w = run_wrapper(tmp_path, [child])
    assert ended.cause == "" and child.killed is None
    assert len(rows_of(w, "question_broken")) == 1 and rows_of(w, "forward") == []
    assert not signals.question_present(w.dir)  # 子が終わると消える


def _broken_next(d, state):
    d.mkdir(parents=True, exist_ok=True)
    (d / signals.NEXT_FILE).write_text('{"block": "/goal"}', encoding="utf-8")


def test_a_broken_config_waits_for_the_pending_question_before_stopping_the_child(tmp_path, monkeypatch):
    # 決定 5・受け入れ条件 1: 回答待ちの質問がある間に設定（次のセグメントのマーカー）が壊れても子を止めず waiting にする。
    # hook が質問を消した（10 回目の見回り）後に子を止めて config_broken で終わる
    states = []
    real = wrapper.Wrapper._write_state

    def spy(self, state, reason):
        states.append(state)
        return real(self, state, reason)

    monkeypatch.setattr(wrapper.Wrapper, "_write_state", spy)
    child = AskingChild({1: "q", 10: None})
    ended, spawn, _ = run_wrapper(tmp_path, [child], _broken_next)
    assert ended.cause == "config_broken" and len(spawn.calls) == 1
    assert child.killed is not None and child.killed[0] >= 10 and child.killed[1] is False
    assert "waiting" in states and states[-1] == "stopped"


def test_a_broken_config_ends_when_the_child_ends_by_itself(tmp_path):
    # 決定 5: 回答待ちのまま子が自分で終わったら、止めずに config_broken で終わる
    child = AskingChild({1: "q"}, exit_at=5)
    ended, _, _ = run_wrapper(tmp_path, [child], _broken_next)
    assert ended.cause == "config_broken" and child.killed is None
