"""問い合わせの記録 `ask.jsonl`（#2206）。hook の判定の行と、AI の代わりの判断の行を追記だけで持つ。

置き場は `${NDF_MVV_STATE_DIR:-~/.local/state/ndf}/ask.jsonl`（承認ゲートの差し戻しの記録と同じ置き場。設計の決定 3）。
1 行 1 件の JSON で、行を書き換えない（I9）。行は列の一覧から作り、hook の入力のほかの欄を写さない（I11）。

| 列 | 判定の行（`kind: verdict`） | 代わりの判断の行（`kind: decision`） |
| --- | --- | --- |
| `v`・`kind`・`id`・`at`・`session`・`cwd` | 持つ | 持つ |
| `runtime`・`source`・`verdict`・`mode`・`decl_problem`・`questions` | 持つ | 持たない |
| `reason` | 判定の理由の種類 | AI の理由の文 |
| `ref`・`question`・`answer` | 持たない | 持つ |

標準ライブラリだけで書く。
"""

from __future__ import annotations

import datetime
import json
import os
import uuid
from pathlib import Path

FILE_NAME = "ask.jsonl"
VERSION = 1


def ledger_path() -> Path:
    env = os.environ.get("NDF_MVV_STATE_DIR")
    base = Path(env).expanduser() if env else Path("~/.local/state/ndf").expanduser()
    return base / FILE_NAME


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _questions(tool_input) -> list[str]:
    qs = tool_input.get("questions") if isinstance(tool_input, dict) else None
    if not isinstance(qs, list):
        return []
    return [q["question"] for q in qs if isinstance(q, dict) and isinstance(q.get("question"), str)]


def verdict_row(
    *, session: str, cwd: str, runtime: str, source: str, verdict: str, reason: str, mode: str, decl_problem: str, tool_input
) -> dict:
    return {
        "v": VERSION,
        "kind": "verdict",
        "id": new_id(),
        "at": now(),
        "session": session or "",
        "cwd": str(cwd),
        "runtime": runtime,
        "source": source or "",
        "verdict": verdict,
        "reason": reason,
        "mode": mode,
        "decl_problem": decl_problem or "",
        "questions": _questions(tool_input),
    }


def decision_row(*, ref: str, question: str, answer: str, reason: str, session: str, cwd: str) -> dict:
    return {
        "v": VERSION,
        "kind": "decision",
        "id": new_id(),
        "at": now(),
        "session": session or "",
        "cwd": str(cwd),
        "ref": ref or "",
        "question": question,
        "answer": answer,
        "reason": reason,
    }


def append(row: dict, path: Path | None = None) -> Path:
    """1 行を 1 回の `write` で追記する（ロックは取らない）。書けなければ OSError。"""
    p = path or ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False) + "\n"
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    return p


def read_rows(path: Path | None = None) -> list[dict]:
    """記録の行。無ければ空。読めない行は飛ばす。"""
    p = path or ledger_path()
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def find(row_id: str, rows: list[dict] | None = None) -> dict | None:
    """判定の行を `id` で引く。"""
    for row in rows if rows is not None else read_rows():
        if row.get("kind") == "verdict" and row.get("id") == row_id:
            return row
    return None


def summary(rows: list[dict]) -> dict:
    """判定ごと・区分ごと・代わりの判断の `ref` の有無ごとの件数と、代わりの判断が無い止めた行の件数。"""
    verdicts = {"deny": 0, "allow": 0, "advise": 0}
    sources: dict[str, int] = {}
    decisions = {"ref": 0, "no_ref": 0}
    denied: set[str] = set()
    answered: set[str] = set()
    for row in rows:
        if row.get("kind") == "verdict":
            v = row.get("verdict")
            if v in verdicts:
                verdicts[v] += 1
            src = row.get("source") or ""
            sources[src] = sources.get(src, 0) + 1
            if v == "deny":
                denied.add(str(row.get("id")))
        elif row.get("kind") == "decision":
            if row.get("ref"):
                decisions["ref"] += 1
                answered.add(str(row["ref"]))
            else:
                decisions["no_ref"] += 1
    return {"verdict": verdicts, "source": dict(sorted(sources.items())), "decision": decisions, "unanswered": len(denied - answered)}
