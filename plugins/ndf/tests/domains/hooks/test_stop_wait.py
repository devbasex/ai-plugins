"""guard `stop-wait` の判定の選び方（#4000 の決定 1・5・6・10・11）。

ルールの前置きと軽いモデルの判定を、詳細設計の「処理の手順」の表の順に確かめる。偽の `ask`・偽の `parent_launch`・偽の
時計を差し込み、実プロセスの起動・実の `/proc`・実時間の経過を使わない。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.domains.hooks import budget, context, dispatch, reply_judge, tables
from ndf.domains.hooks.counter import Counter
from ndf.domains.hooks.guards import stop_wait

NOW = 100.0  # 偽の単調時計（締め切りの基準）
# Claude Code の Stop の締め切りの時刻と、締め切りいっぱいで判定へ渡す秒（`hook.budget.stop` と `hook.stop-wait.judge` から読む）
STOP = budget.budget_for("claude", "Stop")
DEADLINE = NOW + STOP.deadline
JUDGE_SECONDS = min(STOP.usable, reply_judge._judge("seconds"))
WALL = 1000.0  # 偽の壁時計（休みのマーカーの時刻）
CLAUDE_EXE = "/home/u/.local/share/claude/versions/2.1.330"
PLAIN = reply_judge.Launch(argv=["claude", "-p"], project_dir="/w/p", exe=CLAUDE_EXE, exe_id=[2049, 777])
GATEWAY = reply_judge.Launch(argv=["claude", "--settings", "gw.json"], project_dir="/w/p", exe=CLAUDE_EXE, exe_id=[2049, 777])
QUOTED = (
    "レーンの様子を報告します。このレーンは「どちらにするかの指示をください」と書いた後に応答を終えていて、こちらでは何も待っていません。"
)
REPORT = "作業を終えた。テストは通った。"
_BULLETS_3 = "\n".join(f"- 報告の項目 {i}" for i in range(3))
_BULLETS_9 = "\n".join(f"- レーン p{i} の状態" for i in range(9))
# #4000 の「何が起きたか」の表の 1〜5 の本文
MISSED = [
    "どちらにするか決めていただければ進めます。",
    f"どちらにするかを決めてください。\n\n{_BULLETS_3}",
    f"どちらにするかの指示をください。\n\n{_BULLETS_9}",
    "計画のレーンを 1 本増やして進めてよいですか。\n\np12 の知らせは引き続き待っています。",
    "この段落を CLAUDE.md へ載せてよいか、引き続きお返事を待っています。",
    "言い回しの列に無いためだと調べました。これは不具合だと見ています。起票しましょうか。",
]


class FakeCounter:
    """数え手の代わり。`left` は上限の手前か、`waited` はモデルの wait のフラグ。"""

    def __init__(self, left: bool = True, waited: bool = False) -> None:
        self.left = left
        self.waited = waited

    def pushback_left(self) -> bool:
        return self.left

    def mark_model_wait(self) -> None:
        self.waited = True

    def model_waited(self) -> bool:
        return self.waited


class Judge:
    """偽の `ask` と偽の `parent_launch`。呼ばれた引数を残す。"""

    def __init__(self, monkeypatch, kind="report", failure=None, launch=PLAIN) -> None:
        self.kind, self.failure, self.launch = kind, failure, launch
        self.asked: list[tuple] = []
        self.parents = 0
        self.clock = {"mono": NOW, "wall": WALL}
        monkeypatch.setattr(reply_judge, "ask", self.ask)
        monkeypatch.setattr(reply_judge, "parent_launch", self.parent_launch)
        monkeypatch.setattr(stop_wait, "monotonic", lambda: self.clock["mono"])
        monkeypatch.setattr(stop_wait, "wall", lambda: self.clock["wall"])

    def ask(self, text, seconds, env, launch):
        self.asked.append((text, seconds, launch))
        return reply_judge.Judged(self.kind, self.failure, 12.5, 1 if self.failure is None else 0)

    def parent_launch(self, env, pid):
        self.parents += 1
        return self.launch


def _ask_transcript(path: Path) -> str:
    lines = [
        {"type": "user", "message": {"content": "進めて"}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "AskUserQuestion", "input": {}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
    ]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return str(path)


def _check(tmp_path, reply, counter=None, runtime="claude", deadline=DEADLINE, decided=False, **payload):
    ctx = context.HookContext(runtime, "Stop", {"last_assistant_message": reply, **payload})
    svc = dispatch.GuardServices(tmp_path, counter or FakeCounter(), deadline, decided=decided)
    return stop_wait.check(ctx, svc)


@pytest.mark.parametrize("text", MISSED, ids=["1", "2", "3", "4-may-i", "4-awaiting", "5"])
def test_stop_wait_pushes_back_the_misses_of_4000_by_the_rule_alone(tmp_path, monkeypatch, text):
    judge = Judge(monkeypatch, kind="report")
    got = _check(tmp_path, text)
    assert got.kind == "pushback" and "AskUserQuestion" in got.reason
    assert got.notes == {"reply": "prose_wait", "judge": "rule", "judge_ms": 0, "judge_calls": 0}
    assert judge.asked == [] and judge.parents == 0


def test_stop_wait_follows_the_model_when_the_rule_reads_a_report(tmp_path, monkeypatch):
    counter = FakeCounter()
    judge = Judge(monkeypatch, kind="wait")
    got = _check(tmp_path, REPORT, counter)
    assert got.kind == "pushback" and counter.model_waited()
    assert got.notes == {"reply": "report", "judge": "model", "judge_ms": 12.5, "judge_calls": 1}
    assert judge.asked == [(REPORT, JUDGE_SECONDS, PLAIN)]
    judge.kind = "report"
    got = _check(tmp_path, REPORT)
    assert got.kind == "pass" and got.notes["judge"] == "model"


def test_stop_wait_passes_the_whole_prose_of_a_long_report_to_the_model(tmp_path, monkeypatch):
    # ルールは最後の 3 行だけを見るので `report`。質問の行も判定へ渡る
    lines = "\n".join(f"レーン p{i} の作業を終えた。" for i in range(9))
    text = f"どちらにするかの指示をください。\n{lines}"
    judge = Judge(monkeypatch)
    got = _check(tmp_path, text)
    assert got.notes["reply"] == "report" and got.notes["judge"] == "model"
    ((sent, _, _),) = judge.asked
    assert "どちらにするかの指示をください。" in sent


def test_stop_wait_does_not_read_a_question_inside_a_code_block(tmp_path, monkeypatch):
    judge = Judge(monkeypatch)
    got = _check(tmp_path, "結果を出した。\n```\nどうしますか？\n```")
    assert got.kind == "pass" and got.notes["reply"] == "report"
    assert judge.asked == [("結果を出した。", JUDGE_SECONDS, PLAIN)]


def test_stop_wait_sends_a_quoted_question_to_the_model(tmp_path, monkeypatch):
    judge = Judge(monkeypatch, kind="report")
    got = _check(tmp_path, QUOTED)
    assert got.kind == "pass" and got.notes["reply"] == "report" and got.notes["judge"] == "model"
    assert len(judge.asked) == 1


def test_stop_wait_passes_a_wait_elsewhere_without_the_model(tmp_path, monkeypatch):
    judge = Judge(monkeypatch)
    got = _check(tmp_path, "別のレーンは承認を待っています。")
    assert got.kind == "pass" and got.notes["reply"] == "elsewhere_wait" and got.notes["judge"] == "skip:elsewhere"
    assert judge.asked == []


def test_stop_wait_passes_a_completion_report_with_a_set_closing_without_the_model(tmp_path, monkeypatch):
    judge = Judge(monkeypatch, kind="wait")
    got = _check(tmp_path, "作業を終えました。\n引き続きよろしくお願いします。")
    assert got.kind == "pass" and got.notes["reply"] == "closing_report" and got.notes["judge"] == "skip:closing"
    assert judge.asked == []


def test_stop_wait_still_judges_what_comes_before_a_set_closing(tmp_path, monkeypatch):
    judge = Judge(monkeypatch, kind="wait")
    got = _check(tmp_path, "作業を終えました。どうしましょう。\n引き続きよろしくお願いします。")
    assert got.kind == "pushback" and got.notes["reply"] == "report" and got.notes["judge"] == "model"
    assert len(judge.asked) == 1


def test_stop_wait_pushes_back_a_question_here_after_a_wait_elsewhere(tmp_path, monkeypatch):
    judge = Judge(monkeypatch)
    got = _check(tmp_path, "別のレーンは承認を待っています。こちらは #4217 を載せてよいですか。")
    assert got.kind == "pushback" and got.notes["judge"] == "rule" and judge.asked == []


def test_stop_wait_does_not_apply_the_rule_after_ask_user_question(tmp_path, monkeypatch):
    judge = Judge(monkeypatch)
    got = _check(tmp_path, "選択肢から選んでください。", transcript_path=_ask_transcript(tmp_path / "t.jsonl"))
    assert got.kind == "pass" and got.notes["judge"] == "skip:asked" and "reply" not in got.notes
    assert judge.asked == []


@pytest.mark.parametrize(
    ("reply", "kwargs", "judged"),
    [
        ("", {}, "skip:empty"),
        ("```\nどうしますか？\n```", {}, "skip:empty"),
        (REPORT, {"background_tasks": [{"id": "b1"}]}, "skip:background"),
        (REPORT, {"counter": FakeCounter(left=False)}, "skip:limit"),
        (REPORT, {"runtime": "codex"}, "skip:runtime"),
        # 表の前の guard が変更要求を既に返した Stop（再開は決まっている）
        (REPORT, {"decided": True}, "skip:decided"),
    ],
    ids=["empty", "code-only", "background", "limit", "codex", "decided"],
)
def test_stop_wait_does_not_call_the_model_under_the_skip_conditions(tmp_path, monkeypatch, reply, kwargs, judged):
    judge = Judge(monkeypatch, kind="wait")
    got = _check(tmp_path, reply, **kwargs)
    assert got.kind == "pass" and got.notes["judge"] == judged
    assert judge.asked == [] and judge.parents == 0


def test_stop_wait_skips_the_model_when_the_parent_is_unknown(tmp_path, monkeypatch):
    judge = Judge(monkeypatch, launch=None)
    got = _check(tmp_path, REPORT)
    assert got.kind == "pass" and got.notes["judge"] == "skip:endpoint"
    assert judge.parents == 1 and judge.asked == []


def test_stop_wait_skips_the_model_when_the_time_is_short(tmp_path, monkeypatch):
    judge = Judge(monkeypatch)
    # 余白 1.5 秒（`hook.budget.stop`）を引いた残りが 0.4 秒
    got = _check(tmp_path, REPORT, deadline=NOW + 1.5 + 0.4)
    assert got.kind == "pass" and got.notes["judge"] == "skip:time"
    assert judge.parents == 0 and judge.asked == []


def test_stop_wait_subtracts_the_margin_once(tmp_path, monkeypatch):
    judge = Judge(monkeypatch)
    # 締め切りいっぱいなら、判定の上限（`hook.stop-wait.judge` の seconds）で打つ
    _check(tmp_path, REPORT, deadline=DEADLINE)
    assert [seconds for _, seconds, _ in judge.asked] == [JUDGE_SECONDS]
    judge.asked.clear()
    _check(tmp_path, REPORT, deadline=NOW + 1.5 + 2)
    assert [seconds for _, seconds, _ in judge.asked] == [2]


@pytest.mark.parametrize("failure", ["timeout", "launch", "exit", "shape", "no_slot", "refused", "usage_limit", "auth", "endpoint"])
def test_stop_wait_passes_when_the_model_does_not_answer(tmp_path, monkeypatch, failure):
    counter = FakeCounter()
    Judge(monkeypatch, kind=None, failure=failure)
    got = _check(tmp_path, REPORT, counter)
    assert got.kind == "pass" and got.notes["judge"] == f"fallback:{failure}" and not counter.model_waited()


HEADING_ASK = "## どちらの案で進めますか？\n- 案 A\n- 案 B"


def test_stop_wait_follows_the_model_for_a_heading_that_requests_the_user(tmp_path, monkeypatch):
    judge = Judge(monkeypatch, kind="report")
    got = _check(tmp_path, HEADING_ASK)
    assert got.kind == "pass" and got.notes["reply"] == "heading_wait" and got.notes["judge"] == "model"
    judge.kind = "wait"
    assert _check(tmp_path, HEADING_ASK).kind == "pushback"


@pytest.mark.parametrize(
    ("kwargs", "judged"),
    [({"runtime": "codex"}, "skip:runtime"), ({"deadline": NOW + 1.5 + 0.4}, "skip:time")],
    ids=["codex", "time"],
)
def test_stop_wait_pushes_back_a_heading_wait_when_the_model_cannot_be_used(tmp_path, monkeypatch, kwargs, judged):
    judge = Judge(monkeypatch, kind="report")
    got = _check(tmp_path, HEADING_ASK, **kwargs)
    assert got.kind == "pushback" and got.notes["reply"] == "heading_wait" and got.notes["judge"] == judged
    assert judge.asked == []


def test_stop_wait_pushes_back_a_heading_wait_when_the_model_does_not_answer(tmp_path, monkeypatch):
    Judge(monkeypatch, kind=None, failure="timeout")
    got = _check(tmp_path, HEADING_ASK)
    assert got.kind == "pushback" and got.notes["judge"] == "fallback:timeout"


def test_stop_wait_keeps_the_delegated_heading_before_a_long_report(tmp_path, monkeypatch):
    # 見出しの後に上限を超える検証結果が続いても、判定を委ねた見出しはモデルへ渡る
    results = "\n".join(f"検証 {i} の結果はすべて通過しました。" for i in range(200))
    judge = Judge(monkeypatch, kind="report")
    _check(tmp_path, f"## このままマージしてよいですか？\n{results}")
    ((sent, _, _),) = judge.asked
    assert sent.splitlines()[0] == "## このままマージしてよいですか？"


def test_stop_wait_rests_after_a_launch_failure(tmp_path, monkeypatch):
    judge = Judge(monkeypatch, kind=None, failure="launch")
    _check(tmp_path, REPORT)
    judge.kind, judge.failure = "report", None
    got = _check(tmp_path, REPORT)
    assert got.notes["judge"] == "skip:rest" and judge.parents == 1 and len(judge.asked) == 1
    # Codex のセッションでは、休み中でも表で先に当たるランタイムを記録する
    assert _check(tmp_path, REPORT, runtime="codex").notes["judge"] == "skip:runtime"
    judge.clock["wall"] = WALL + 600
    assert _check(tmp_path, REPORT).notes["judge"] == "model" and len(judge.asked) == 2


def test_stop_wait_rests_only_the_launch_form_of_an_endpoint_mismatch(tmp_path, monkeypatch):
    judge = Judge(monkeypatch, kind=None, failure="endpoint", launch=GATEWAY)
    assert _check(tmp_path, REPORT).notes["judge"] == "fallback:endpoint"
    judge.kind, judge.failure = "report", None
    got = _check(tmp_path, REPORT)
    assert got.notes["judge"] == "skip:rest" and judge.parents == 2 and len(judge.asked) == 1
    judge.launch = PLAIN
    assert _check(tmp_path, REPORT).notes["judge"] == "model" and len(judge.asked) == 2


def test_stop_wait_does_not_rest_after_a_timeout(tmp_path, monkeypatch):
    judge = Judge(monkeypatch, kind=None, failure="timeout")
    _check(tmp_path, REPORT)
    _check(tmp_path, REPORT)
    assert len(judge.asked) == 2


@pytest.mark.parametrize(
    ("reply", "waited", "kind", "judged"),
    [
        (REPORT, True, "pushback", "skip:limit"),
        (REPORT, False, "pass", "skip:limit"),
        ("```\nx\n```\n\n", True, "pass", "skip:empty"),
        ("どちらにしますか？", False, "pushback", "rule"),
    ],
    ids=["model-waited", "not-waited", "empty", "rule"],
)
def test_stop_wait_after_the_limit(tmp_path, monkeypatch, reply, waited, kind, judged):
    judge = Judge(monkeypatch, kind="report")
    got = _check(tmp_path, reply, FakeCounter(left=False, waited=waited))
    assert got.kind == kind and got.notes["judge"] == judged and judge.asked == []


def test_stop_wait_after_the_limit_with_no_prose_to_send(tmp_path, monkeypatch):
    # ルールは本文の行と読む（`report`）が、質問へ渡す本文が空（`code_lines` の数が行の数と合わないなど）。ルールと
    # 本文の抜き出しが同じ CommonMark の解析を使うので実際の文では起きにくく、抜き出しの返りを空にして縛る
    judge = Judge(monkeypatch)
    monkeypatch.setattr(reply_judge, "judge_text", lambda *_: "")
    text = REPORT
    assert _check(tmp_path, text, FakeCounter(left=False, waited=True)).kind == "pushback"
    got = _check(tmp_path, text, FakeCounter(left=True, waited=True))
    assert got.kind == "pass" and got.notes == {"reply": "report", "judge": "skip:no_prose", "judge_ms": 0, "judge_calls": 0}
    assert judge.asked == []


def test_stop_wait_after_the_limit_is_passed_by_dispatch_as_limited(tmp_path, monkeypatch):
    judge = Judge(monkeypatch, kind="report")
    spec = next(s for s in dispatch.load_specs() if s.name == "stop-wait")
    ctx = context.HookContext("claude", "Stop", {"last_assistant_message": REPORT})
    with Counter.open("s-1", tmp_path) as counter:
        counter.data["pushbacks"] = tables.policy_value("hook.pushback", "attempts")
        counter.mark_model_wait()
        out = dispatch.run(ctx, [spec], budget.budget_for("claude", "Stop"), dispatch.GuardServices(tmp_path, counter, DEADLINE))
    assert out.limited and out.verdict.kind == "pass" and out.notes["judge"] == "skip:limit" and judge.asked == []


# 受け入れ条件 11 の (f)。文言は照らさず、当たった文が変更要求に載るか・載らないかだけを見る
def test_stop_wait_shows_the_sentence_the_rule_matched(tmp_path, monkeypatch):
    Judge(monkeypatch, kind="report")
    text = "作業を終えた。どちらにするか決めていただければ進めます。"
    got = _check(tmp_path, text)
    assert got.notes["judge"] == "rule" and "どちらにするか決めていただければ進めます。" in got.reason


def test_stop_wait_shows_the_heading_when_the_rule_stops_a_heading_wait(tmp_path, monkeypatch):
    Judge(monkeypatch, kind="report")
    got = _check(tmp_path, HEADING_ASK, runtime="codex")
    assert got.kind == "pushback" and "どちらの案で進めますか？" in got.reason


def test_stop_wait_does_not_quote_the_reply_when_the_model_stopped_it(tmp_path, monkeypatch):
    Judge(monkeypatch, kind="wait")
    plain = _check(tmp_path, REPORT)
    heading = _check(tmp_path, HEADING_ASK)
    assert plain.kind == heading.kind == "pushback"
    assert REPORT not in plain.reason and "どちらの案で進めますか" not in heading.reason
    assert plain.reason == heading.reason == stop_wait.reason()


def test_stop_wait_cuts_a_long_matched_sentence():
    long = "あ" * (stop_wait.MATCHED_CHARS * 2)
    shown = stop_wait.reason((long,))
    assert long not in shown and "あ" * (stop_wait.MATCHED_CHARS - 1) + "…" in shown


# レビュー r1-1: 当たった文は会話へ返す文にだけ入れ、上限で通したときの計測と待ちの知らせへは本文を渡さない
def test_stop_wait_keeps_the_matched_sentence_out_of_the_limited_reason(tmp_path, monkeypatch):
    Judge(monkeypatch, kind="report")
    text = "作業を終えた。秘密の値 s3cr3t を入れてよいか決めていただければ進めます。"
    spec = next(s for s in dispatch.load_specs() if s.name == "stop-wait")
    ctx = context.HookContext("claude", "Stop", {"last_assistant_message": text})
    with Counter.open("s-1", tmp_path) as counter:
        counter.data["pushbacks"] = tables.policy_value("hook.pushback", "attempts")
        out = dispatch.run(ctx, [spec], budget.budget_for("claude", "Stop"), dispatch.GuardServices(tmp_path, counter, DEADLINE))
    assert out.limited and "s3cr3t" not in out.notes["limited_reason"]
    assert out.notes["limited_reason"] == stop_wait.LOGGED


# レビュー r1-2: 質問の Tool が無いランタイムへは、その Tool で出し直す案内を返さない
def test_stop_wait_does_not_ask_codex_to_use_a_missing_tool(tmp_path, monkeypatch):
    Judge(monkeypatch, kind="report")
    claude = _check(tmp_path, MISSED[0])
    codex = _check(tmp_path, MISSED[0], runtime="codex")
    assert claude.kind == codex.kind == "pushback"
    assert stop_wait.ASK["claude"] in claude.reason and stop_wait.ASK["claude"] not in codex.reason
    assert stop_wait.ASK_WITHOUT_TOOL in codex.reason
