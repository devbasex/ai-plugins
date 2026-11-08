"""読む Skill の guard: supervisor が `ndf:worker` を起動する Agent の呼び出しを見る PreToolUse の判定（#2399）。Claude Code だけ。

| # | 条件 | 結果 |
| --- | --- | --- |
| 1 | `NDF_WORKER_SKILLS_GUARD=0` | 通す |
| 2 | Tool が Agent・Task でない、または `subagent_type` が `ndf:worker` でない | 通す |
| 3 | 呼び手の `agent_type` が `ndf:supervisor`・`ndf:supervisor-waits` のどちらでもない | 通す |
| 4 | `prompt` に「読む Skill」の欄が無い | 止める（理由に正本の「supervisor → worker」の節） |
| 5 | 欄の値が「無し」 | 通す |
| 6 | 呼び手の会話の記録が読めない | 通す |
| 7 | 呼び手の会話の記録に、この hook のプラグインの `scripts/stage-skills.py`（絶対パス。シンボリックリンクは解決して比べる）で打った `lookup` の実行が無い | 止める（理由に対応表の引き方） |
| 8 | 上のどれにも当たらない | 通す |

規約（欄の決め方）の正本は `development-workflow` の `references/agent-layers.md` の「supervisor → worker」にあり、
止めるときにその節をファイルから読み出して理由に添える。この判定は工程と Skill の組を持たない（対応表だけが持つ）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import payload as pl
from .token_guard import _field, _off, deny

SCRIPTS = Path(__file__).resolve().parents[1]
STAGE_SKILLS = SCRIPTS / "stage-skills.py"
DOC = SCRIPTS.parent / "skills" / "development-workflow" / "references" / "agent-layers.md"
HEADING = "### supervisor → worker"
SUPERVISORS = ("ndf:supervisor", "ndf:supervisor-waits")
FIELD = "読む Skill"
# 行の先頭の箇条書きの記号・表の区切り・見出しの記号・強調を除いたところに欄の名前があり、区切りか行末が続く
_FIELD_RE = re.compile(
    r"^[\s>]*(?:(?:[-*+]|\d+[.)]|#+|\|)\s*)*(?:\*\*|__)?" + re.escape(FIELD) + r"(?:\*\*|__)?\s*(?:[:：|](?P<rest>.*))?$"
)
_NONE_RE = re.compile(r"^[\s|*_`「」]*(?:無し|なし)")
# lookup の前の stage-skills.py のパス（引用符で囲んだものと囲まないもの）
_LOOKUP_RE = re.compile(
    r"(?:\"(?P<dq>[^\"]*stage-skills\.py)\"|'(?P<sq>[^']*stage-skills\.py)'|(?P<bare>[^\s\"';|&()<>]*stage-skills\.py))\s+lookup\b"
)


def field_value(prompt: str) -> str | None:
    """「読む Skill」の欄の値。欄が無ければ None。値は同じ行の区切りの後、空なら次の空でない行。"""
    lines = prompt.splitlines()
    for i, line in enumerate(lines):
        m = _FIELD_RE.match(line)
        if not m:
            continue
        rest = (m.group("rest") or "").strip().strip("|").strip()
        if rest:
            return rest
        return next((ln.strip() for ln in lines[i + 1 :] if ln.strip()), "")
    return None


def is_none(value: str) -> bool:
    return bool(_NONE_RE.match(value))


def _commands(row: dict) -> list[str]:
    msg = row.get("message") if isinstance(row, dict) else None
    content = msg.get("content") if isinstance(msg, dict) else None
    if row.get("type") != "assistant" or not isinstance(content, list):
        return []
    return [
        str(c.get("input", {}).get("command", ""))
        for c in content
        if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Bash" and isinstance(c.get("input"), dict)
    ]


def _is_own(path: str) -> bool:
    """絶対パスで、シンボリックリンクを解決すると この hook のプラグインの stage-skills.py になるか。変数や相対パスは数えない。"""
    p = Path(path).expanduser()
    if not p.is_absolute():
        return False
    try:
        return p.resolve() == STAGE_SKILLS.resolve()
    except (OSError, RuntimeError):
        return False


def _own_lookup(command: str) -> bool:
    return any(_is_own(m.group("dq") or m.group("sq") or m.group("bare")) for m in _LOOKUP_RE.finditer(command))


def looked_up(transcript: Path) -> bool:
    """会話の記録の assistant の Bash の呼び出しに、この hook のプラグインの stage-skills.py の lookup があるか（Tool の結果や指示の文は数えない）。"""
    with transcript.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if "stage-skills" not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and any(_own_lookup(c) for c in _commands(row)):
                return True
    return False


def section(doc: Path | None = None) -> str | None:
    """正本の「supervisor → worker」の節（見出しから次の同じか上の階層の見出しの前まで）。読めなければ None。"""
    try:
        lines = (doc or DOC).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    try:
        start = lines.index(HEADING)
    except ValueError:
        return None
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^#{1,3} ", lines[i])), len(lines))
    return "\n".join(lines[start:end]).strip()


def _own_transcript(raw: dict) -> Path | None:
    tp, aid = _field(raw, "transcript_path"), _field(raw, "agent_id")
    if not tp or not aid:
        return None
    own = Path(f"{tp.removesuffix('.jsonl')}/subagents/agent-{aid}.jsonl")
    return own if own.is_file() else None


def _missing_field() -> dict:
    body = section()
    where = f"正本: {DOC} の「{HEADING.lstrip('# ')}」"
    if body is None:
        text = f"正本の節を読めなかった。{where} を読んで組み直す。"
    else:
        text = f"{where}\n\n{body}"
    return deny(
        f"ndf:worker の起動指示の prompt に「{FIELD}」の欄が無い。起動指示は正本の「supervisor → worker」の項目で組み、"
        f"「{FIELD}」の欄は prompt の本文に置く（工程に入る前の作業と、対応表が空の工程は「無し」と書く）。"
        f"$SCRIPTS は {SCRIPTS} である。止めるなら NDF_WORKER_SKILLS_GUARD=0。\n\n{text}"
    )


def _not_looked_up() -> dict:
    return deny(
        f"ndf:worker の起動指示の「{FIELD}」に Skill を挙げたが、この supervisor は読み込まれた NDF の対応表を引いていない"
        f"（別の場所の stage-skills.py や、変数・相対パスで打った lookup は数えない）。"
        f'先に python3 "{STAGE_SKILLS}" lookup <工程> をこの絶対パスのまま打ち、出力の items の name と path をそのまま並べてから起動し直す'
        f"（工程は、いま通している工程の名前）。規約: {DOC} の「{HEADING.lstrip('# ')}」。止めるなら NDF_WORKER_SKILLS_GUARD=0"
    )


def decision(ev: pl.Event) -> dict | None:
    """判定のエントリポイント（hook.py の振り分けからだけ呼ぶ）。どの失敗も通す側へ倒す（呼び手の hook が拾う）。"""
    raw = ev.raw
    if _off("NDF_WORKER_SKILLS_GUARD"):
        return None
    if ev.tool not in ("Agent", "Task") or _field(raw, "tool_input", "subagent_type") != "ndf:worker":
        return None
    if _field(raw, "agent_type") not in SUPERVISORS:
        return None
    value = field_value(_field(raw, "tool_input", "prompt"))
    if value is None:
        return _missing_field()
    if is_none(value):
        return None
    own = _own_transcript(raw)
    if own is None:
        return None
    try:
        found = looked_up(own)
    except OSError:
        return None
    return None if found else _not_looked_up()
