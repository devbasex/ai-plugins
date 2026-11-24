"""LLM の判断の呼び出し。回答を選択肢か JSON のスキーマで縛る（#1142 の AD3。SF6）。

- 選択肢のモード（`choices`）: 回答は `{"decision", "reason", "basis"}` の形に合い、`decision` が選択肢の中にある（IS6a）
- 任意の JSON のモード（`schema`）: 回答は渡したスキーマだけに合えばよい。`decision` は求めない（IS6b）

質問を `claude -p --json-schema` で渡し、回答を `jsonschema` で確かめ直す。合わなければ 1 回だけ打ち直し、
なお合わなければ `needs_human` で人へ戻す（docs/specifications/ndf-rebuild-scripted-flow.md の決定 60）。回答の文から近い選択肢を拾わない。
起動は判断の権限（`claude.Permission.JUDGE`。Tool を使わせない）で行う（#3364）。

claude を起動できない・期限を超えた・利用上限・学習の設定のスナップショットが無い・重い起動のスロットが空かないは、打ち直しても直らないので打ち直さず、それぞれの停止理由
（表 `claude-failures` の `judge_cause`）で返す（#3394）。どの理由で止まるかと回答の形はこのモジュールが決め、呼び手の LLM に決めさせない。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional, Sequence

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ndf.adapters import claude, run_log
from ndf.contracts import causes, facts

# 打つ回数（最初の 1 回を含む）。上限の設定ではなく docs/specifications/ndf-rebuild-scripted-flow.md の決定 60 で固定した回数
ATTEMPTS = 2
# needs_human の説明に載せる、回答の先頭の字数
ANSWER_HEAD = 200
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


def ask(question: str, material: str, schema: dict, timeout: float, call: Optional[Caller] = None) -> JudgeAnswer:
    """質問と材料を `claude -p --json-schema` で渡し、回答を確かめる。合わなければ 1 回だけ打ち直す。

    `schema` は `answer_schema` の値。2 回とも合わなければ `cause` が `needs_human` で、2 回の回答の先頭を
    `detail` に持つ。claude が誤りで終えた・出力が JSON でないときは 1 回の外れと数える。起動できない・期限切れ・
    利用上限はその回で止め、打ち直さない。
    """
    run = call if call is not None else claude.call_claude
    argv = claude.claude_args(claude.ClaudeCall(system=SYSTEM, permission=claude.Permission.JUDGE, json_schema=schema))
    errors: list[str] = []
    heads: list[str] = []
    cost = 0.0
    for attempt in range(1, ATTEMPTS + 1):  # noqa: NDF201 上限: docs/specifications/ndf-rebuild-scripted-flow.md の決定 60 の 2 回
        reply = run(argv, _prompt(question, material, errors), timeout)
        cost += reply.cost
        if reply.failure is not None:
            run_log.record("judge", attempts=attempt, valid=False, cost=cost, failure=str(reply.failure))
            kind = facts.value(claude.FAILURES, reply.failure, "judge_cause")
            return JudgeAnswer(False, None, causes.cause(kind, reply.error or str(reply.failure)), attempt, cost)
        errors = [reply.error] if reply.error else check_answer(reply.answer, schema)
        if not errors:
            run_log.record("judge", attempts=attempt, valid=True, cost=cost)
            return JudgeAnswer(True, reply.answer, None, attempt, cost)
        heads.append(f"{attempt} 回目: {_head(reply)}（{'; '.join(errors)}）")
    run_log.record("judge", attempts=ATTEMPTS, valid=False, cost=cost)
    detail = f"判断の回答が {ATTEMPTS} 回とも形に合わなかった。" + " / ".join(heads)
    return JudgeAnswer(False, None, causes.cause("needs_human", detail), ATTEMPTS, cost)
