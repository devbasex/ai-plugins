"""LLM の判断の呼び出し。回答を選択肢か JSON のスキーマで縛る（#1142 の AD3。SF6）。

- 選択肢のモード（`choices`）: 回答は `{"decision", "reason", "basis"}` の形に合い、`decision` が選択肢の中にある（IS6a）
- 任意の JSON のモード（`schema`）: 回答は渡したスキーマだけに合えばよい。`decision` は求めない（IS6b）

質問を `claude -p --json-schema` で渡し、回答を `jsonschema` で確かめ直す。合わなければ 1 回だけ打ち直し、
なお合わなければ `needs_human` で人へ戻す（docs/specifications/ndf-rebuild-scripted-flow.md の決定 60）。回答の文から近い選択肢を拾わない。
起動は判断の権限（`claude.Permission.JUDGE`。Tool を使わせない）で行う（#3364）。

モデルと effort は質問 1 件を単位に差し込まれた処理（`claude_choice.pick_values`）で選び、2 回目は 1 つ上げる（`invalid_answer`）。モデルを理由に
拒まれたら、打ち直しの回数に数えずに基準の組で 1 回だけ起動し直す。起動ごとに使用量ログへ値と終わり方（`outcome`）の行を渡し、
割り付けを受けた質問は 1 回目の回答が形に合ったかを効果指標として書く（#2944）。

claude を起動できない・期限を超えた・利用上限・学習の設定のスナップショットが無い・重い起動のスロットが空かないは、打ち直しても直らないので打ち直さず、それぞれの停止理由
（表 `claude-failures` の `judge_cause`）で返す（#3394）。どの理由で止まるかと回答の形はこのモジュールが決め、呼び手の LLM に決めさせない。
"""

from __future__ import annotations

import copy
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional, Sequence

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ndf.adapters import claude, claude_choice, run_log
from ndf.contracts import causes, facts
from ndf.contracts.internal import InternalError

# 打つ回数（最初の 1 回を含む）。上限の設定ではなく docs/specifications/ndf-rebuild-scripted-flow.md の決定 60 で固定した回数
ATTEMPTS = 2
# needs_human の説明に載せる、回答の先頭の字数
ANSWER_HEAD = 200
# 2 回目の起動の打ち直しの理由（表 launch-experiments の judge の retries）
RETRY = "invalid_answer"
SYSTEM = "あなたは判断の呼び出しに回答する。渡したスキーマに合う JSON だけを返す。前置きや説明の文を足さない。"

# 選択肢のモードの回答の形。`decision` の `enum` は呼ぶときに選択肢で埋める
JUDGE_ANSWER: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "reason", "basis"],
    "properties": {
        "decision": {"enum": []},
        "reason": {"type": "string", "minLength": 1},
        "basis": {"type": "array", "items": {"type": "string"}},
    },
}

# 1 回の呼び出し: (起動の引数, 質問, 上限の秒) -> 回答。テストは偽の呼び出しへ差し替える
Caller = Callable[[Sequence[str], str, float], claude.ClaudeReply]


class AnswerSchemaError(Exception):
    """回答のスキーマを組めない（引数の誤り・スキーマのファイルが読めない・スキーマとして正しくない）。"""


class JudgeAnswer(NamedTuple):
    """判断の呼び出しの結果。`ok` が真なら `answer` はスキーマに合う。

    偽なら `cause` の種類は、回答が 2 回とも合わなければ `needs_human`、打ち直さない失敗なら表 `claude-failures` の `judge_cause`。
    """

    ok: bool
    answer: Any
    cause: Optional[dict]
    attempts: int
    cost: float


def answer_schema(choices: Optional[list[str]] = None, schema: Optional[str] = None) -> dict:
    """回答のスキーマ。`choices` と `schema`（スキーマのファイルのパス）のどちらか 1 つだけを渡す。"""
    if (choices is None) == (schema is None):
        raise AnswerSchemaError("choices と schema のどちらか 1 つだけを渡す")
    if choices is not None:
        if not choices:
            raise AnswerSchemaError("選択肢が空")
        out = copy.deepcopy(JUDGE_ANSWER)
        out["properties"]["decision"]["enum"] = list(choices)
        return out
    try:
        loaded = json.loads(Path(schema).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(loaded)
    except (OSError, json.JSONDecodeError, SchemaError) as exc:
        raise AnswerSchemaError(f"回答のスキーマを読めない: {schema}: {exc}") from exc
    return loaded


def check_answer(answer: object, schema: dict) -> list[str]:
    """回答をスキーマで確かめ、誤りを並べる。空なら合う。"""
    found = Draft202012Validator(schema).iter_errors(answer)
    return sorted(f"{'/'.join(map(str, e.absolute_path)) or '(根)'}: {e.message}" for e in found)


def _prompt(question: str, material: str, errors: list[str]) -> str:
    parts = [f"# 質問\n{question}", f"# 材料\n{material}"]
    if errors:
        parts.append("# 前の回答は形に合わなかった\n" + "\n".join(errors))
    return "\n\n".join(parts)


def _head(reply: claude.ClaudeReply) -> str:
    shown = json.dumps(reply.answer, ensure_ascii=False) if reply.answer is not None else reply.raw
    return shown[:ANSWER_HEAD]


def judge_outcome(reply: claude.ClaudeReply, schema: dict) -> tuple[str, list[str]]:
    """判定の起動 1 回の終わり方（`valid`・`invalid`・`timeout`・`unknown`）と回答の誤り（空なら合う）。

    `timeout` は起動した後の期限切れ。スキーマの検査は、正常に終わった回答だけに 1 回打つ。
    """
    if reply.failure is claude.Failure.TIMEOUT:
        return "timeout", []
    if reply.failure is not None:
        return "unknown", []
    if reply.error:
        return "unknown", [reply.error]
    errors = check_answer(reply.answer, schema)
    return ("invalid" if errors else "valid"), errors


class _Checks:
    """1 件の質問の回答の検査。同じ回答を使用量ログの行と打ち直しの判断の両方に使い、スキーマの検査を 1 回で済ませる。"""

    def __init__(self, schema: dict) -> None:
        self.schema = schema
        self._last: Optional[tuple[claude.ClaudeReply, tuple[str, list[str]]]] = None

    def of(self, reply: claude.ClaudeReply) -> tuple[str, list[str]]:
        if self._last is None or self._last[0] is not reply:
            self._last = (reply, judge_outcome(reply, self.schema))
        return self._last[1]


def _choice(unit: str, retry: Optional[str] = None) -> Any:
    return claude_choice.pick_values("judge", "ask", cwd=Path.cwd(), unit=unit, retry=retry)


@dataclass(frozen=True)
class _Ask:
    """1 件の質問の中身と起動のしかた（`ask` の引数）。"""

    question: str
    material: str
    schema: dict
    timeout: float
    call: Optional[Caller]
    before_retry: Optional[Callable[[], None]]
    checks: _Checks


def _clock() -> float:
    """期限を測る単調時計の秒。テストが差し替える。"""
    return time.monotonic()


def _launch_judge(run: claude_choice.ChoiceRun, q: _Ask, prompt: str) -> claude.ClaudeReply:
    """選んだ値で 1 回起動する。モデルを理由に拒まれ基準へ切り替えられれば、残りの期限で 1 回だけ起動し直す。

    期限は単調時計で持つ。拒否を受けたら残りの期限を測り、使い切っていれば材料を確かめずに拒否の回答を返す。残っていれば、打ち直しと
    同じく `before_retry` で材料が古くなっていないかを確かめ（例外はそのまま送る）、その直後にもう一度期限を測る。どちらかで止まれば
    切り替えの記録を書かず、基準の組も起動しない。通れば `fall_back` が切り替えの記録を書く。記録を読み書きする間
    （錠の待ちを含む）も期限は進むので、書いた後にもう一度残りを測り、残っていれば基準の組で起動し直す（#2944 の r7-4）。
    """
    deadline = _clock() + q.timeout
    reply = _judge_once(run, q, prompt, q.timeout)
    deadline = min(deadline, _clock() + q.timeout - (reply.seconds if reply.seconds is not None else 0.0))

    def ready() -> bool:
        if deadline - _clock() <= 0:
            return False
        if q.before_retry is not None:
            q.before_retry()
        return deadline - _clock() > 0

    if run.fall_back(reply, ready=ready) and (left := deadline - _clock()) > 0:
        reply = _judge_once(run, q, prompt, left)
    return reply


def _judge_once(run: claude_choice.ChoiceRun, q: _Ask, prompt: str, timeout: float) -> claude.ClaudeReply:
    """起動 1 回。子が終わるたびに行を渡す（差し替えた呼び出しは呼び出しの後に 1 行渡す）。"""
    call = claude.ClaudeCall(SYSTEM, claude.Permission.JUDGE, json_schema=q.schema, model=run.model, effort=run.effort)
    argv = claude.claude_args(call)
    log = claude.LaunchLog({**claude.launch_meta("ask"), **run.meta()}, lambda r: {"outcome": q.checks.of(r)[0]})
    with run_log.overlay_env(run.env_changes()):
        if q.call is None:
            reply = claude.call_claude(argv, prompt, timeout, None, claude.LaunchOptions(log=log))
        else:
            reply = q.call(argv, prompt, timeout)
            log.report(reply)
    run.seen(reply)
    return reply


def ask(
    question: str,
    material: str,
    schema: dict,
    timeout: float,
    call: Optional[Caller] = None,
    before_retry: Optional[Callable[[], None]] = None,
) -> JudgeAnswer:
    """質問と材料を `claude -p --json-schema` で渡し、回答を確かめる。合わなければ 1 回だけ打ち直す。

    `schema` は `answer_schema` の値。2 回とも合わなければ `cause` が `needs_human` で、2 回の回答の先頭を
    `detail` に持つ。claude が誤りで終えた・出力が JSON でないときは 1 回の外れと数える。起動できない・期限切れ・
    利用上限・基準でも拒まれたモデルはその回で止め、打ち直さない。`before_retry` は打ち直しの起動の直前に呼ぶ（1 回目の回答を待つ間に材料が
    古くなっていないかを呼び手が確かめる）。モデルを理由に拒まれて基準の組で起動し直す前にも呼ぶ。例外を投げれば打ち直さず、例外を
    そのまま外へ出す。値の設定の誤りは起動せずに `precondition`。
    """
    request = uuid.uuid4().hex
    try:
        run = claude_choice.ChoiceRun(_choice(request), request)
    except InternalError as exc:
        return JudgeAnswer(False, None, causes.cause(exc.kind, str(exc)), 0, 0.0)
    assigned, ends = run.assign_id, []
    try:
        return _attempts(run, _Ask(question, material, schema, timeout, call, before_retry, _Checks(schema)), ends)
    except InternalError as exc:
        return JudgeAnswer(False, None, causes.cause(exc.kind, str(exc)), len(ends), 0.0)
    finally:
        if assigned is not None and not run.switched and ends:
            claude_choice.report_outcome(assigned, quality(ends[0]), {"outcome": ends[0], "attempts": len(ends)})


def quality(first: str) -> Optional[float]:
    """1 回目の終わり方の効果指標。形に合えば 1、合わないか起動の後の期限切れは 0、ほかは取れない（None）。"""
    if first == "valid":
        return 1.0
    return 0.0 if first in ("invalid", "timeout") else None


def _attempts(run: claude_choice.ChoiceRun, q: _Ask, ends: list[str]) -> JudgeAnswer:
    """`ask` の打ち直しまでの起動。各起動の終わり方を `ends` に足す。"""
    errors: list[str] = []
    heads: list[str] = []
    cost = 0.0
    for attempt in range(1, ATTEMPTS + 1):
        if attempt > 1:
            if q.before_retry is not None:
                q.before_retry()
            run.choice, run.request_try = _choice(run.request_id, RETRY), run.request_try + 1
        reply = _launch_judge(run, q, _prompt(q.question, q.material, errors))
        cost += reply.cost
        outcome, errors = q.checks.of(reply)
        ends.append(outcome)
        if reply.failure is not None:
            run_log.record("judge", attempts=attempt, valid=False, cost=cost, failure=str(reply.failure))
            kind = facts.value(claude.FAILURES, reply.failure, "judge_cause")
            return JudgeAnswer(False, None, causes.cause(kind, reply.error or str(reply.failure)), attempt, cost)
        if not errors:
            run_log.record("judge", attempts=attempt, valid=True, cost=cost)
            return JudgeAnswer(True, reply.answer, None, attempt, cost)
        heads.append(f"{attempt} 回目: {_head(reply)}（{'; '.join(errors)}）")
    run_log.record("judge", attempts=ATTEMPTS, valid=False, cost=cost)
    detail = f"判断の回答が {ATTEMPTS} 回とも形に合わなかった。" + " / ".join(heads)
    return JudgeAnswer(False, None, causes.cause("needs_human", detail), ATTEMPTS, cost)
