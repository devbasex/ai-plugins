"""Claude Code の会話の記録の末尾を読み、最後の応答の文脈の量と古さを返す（#4089 の「記録の読み手」）。

- `read_tail` は末尾 `size` バイトを読み、行の途中から始まるときだけ先頭の切れた行を捨てる。読めなければ空
- `last_context` は `read_tail` の文字列と手がかり（`ToolUse`・`Reply`・`Tail`）から、圧縮の境より後の最後の応答の
  量・ID・時刻と、手がかりと突き合わせた状態（`current`・`behind`・`none`）を返す。ファイルを開かず、例外を投げない
- 量・ID・時刻は、最後の応答の最も新しい物理の行（採用行）だけから読む。同じ応答の前の行へ替えない
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Union

from ndf import values

USAGE_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
SYNTHETIC_MODEL = "<synthetic>"
COMPACT_SUBTYPE = "compact_boundary"
# 応答の行の `type` と、行の時刻の項目
ASSISTANT = "assistant"
TIMESTAMP = "timestamp"


@dataclass(frozen=True)
class ToolUse:
    """その ID の `tool_use` を持つ応答が今の応答（guard の PreToolUse・PostToolUse）。"""

    tool_use_id: str


@dataclass(frozen=True)
class Reply:
    """最後の応答の文が `text` で、その後に利用者の行が無ければ今の応答（Stop の `last_assistant_message`）。"""

    text: Optional[str]


@dataclass(frozen=True)
class Tail:
    """最後の応答の後に利用者の行が無ければ今の応答（ラッパーの見回り）。"""


Anchor = Union[ToolUse, Reply, Tail]


@dataclass(frozen=True)
class ContextRead:
    """`last_context` の結果。`state` は `current`・`behind`・`none`、`reason` は `current` でない理由。"""

    state: str
    reason: Optional[str] = None
    tokens: Optional[int] = None
    message_id: Optional[str] = None
    timestamp: Optional[str] = None
    entry: Optional[Mapping[str, Any]] = field(default=None, compare=False)


def timestamp_seconds(text: Any) -> Optional[float]:
    """記録の `timestamp` を UNIX 秒に直す。解けない・時差が無い・文字列でないなら None。"""
    if not isinstance(text, str):
        return None
    raw = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        at = datetime.fromisoformat(raw)
    except ValueError:  # noqa: NDF401 ファイル: 会話の記録の解けない時刻は読めない時刻として None を返す
        return None
    return at.timestamp() if at.tzinfo is not None else None


def read_tail(path: str, size: int) -> str:
    """会話の記録の末尾 `size` バイト。読み始めが行の途中なら先頭の切れた行を捨てる。読めなければ空。"""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            start = max(0, f.tell() - size)
            f.seek(max(0, start - 1))
            raw = f.read()
    except OSError:  # noqa: NDF401 ファイル: 会話の記録が無い・読めないときは空として扱う
        return ""
    if start > 0:
        if raw[:1] == b"\n":
            raw = raw[1:]
        else:
            cut = raw.find(b"\n")
            raw = raw[cut + 1 :] if cut >= 0 else b""
    return raw.decode("utf-8", "replace")


# JSON として読めない行の目印（`json_line` の返り値）
BROKEN = object()


def json_line(line: str) -> Any:
    """1 行を JSON として読む。読めなければ目印の `BROKEN`。"""
    try:
        return json.loads(line)
    except ValueError:  # 書きかけ・壊れた行は目印で返し、呼び手が位置で扱いを決める
        return BROKEN


def _message(rec: Mapping[str, Any]) -> Mapping[str, Any]:
    msg = rec.get("message")
    return msg if isinstance(msg, dict) else {}


def is_boundary(rec: Any) -> bool:
    """圧縮の境（`system` の `compact_boundary`）の行か。"""
    return isinstance(rec, dict) and rec.get("type") == "system" and rec.get("subtype") == COMPACT_SUBTYPE


def is_talk(rec: Any) -> bool:
    """会話の行（`type` が `user` か `assistant` で、`isSidechain` が真でない）か。"""
    return isinstance(rec, dict) and rec.get("type") in ("user", ASSISTANT) and rec.get("isSidechain") is not True


def is_reply_line(rec: Any) -> bool:
    """応答に数える行（会話の `assistant` の行で、合成応答でない）か。"""
    return is_talk(rec) and rec.get("type") == ASSISTANT and _message(rec).get("model") != SYNTHETIC_MODEL


def message_id(rec: Mapping[str, Any]) -> Optional[str]:
    """行の `message.id`。空でない文字列でなければ None（その行だけで 1 つの応答）。"""
    mid = _message(rec).get("id")
    return mid if isinstance(mid, str) and mid else None


def _elements(rec: Mapping[str, Any]) -> list:
    content = _message(rec).get("content")
    return [e for e in content if isinstance(e, dict)] if isinstance(content, list) else []


def _reply_text(rec: Mapping[str, Any]) -> str:
    return "".join(e["text"] for e in _elements(rec) if e.get("type") == "text" and isinstance(e.get("text"), str))


def _tool_ids(rec: Mapping[str, Any]) -> set:
    return {e["id"] for e in _elements(rec) if e.get("type") == "tool_use" and isinstance(e.get("id"), str)}


# `claude -p --output-format stream-json` の出力のうち、保存してよい結果の行の項目と、Tool の呼び出しの引数（claude 2.1.332 で形を確かめた）
PERMISSION_DENIALS = "permission_denials"
STREAM_RESULT_KEYS = (
    "type",
    "subtype",
    "is_error",
    "api_error_status",
    "result",
    PERMISSION_DENIALS,
    "num_turns",
    "duration_ms",
    values.CLAUDE_COST_KEY,  # 判定役の起動ごとの費用。共通の費用のジャーナルは起動の記録と対応づけられない
    "stop_reason",
)
STREAM_CALL_ARGS = ("file_path", "path", "offset", "limit")


def _stream_row(line: str) -> dict[str, Any]:
    """出力の 1 行の JSON の辞書。途中で切れた行と、JSON の辞書でない行は空。"""
    try:
        found = json.loads(line)
    except ValueError:  # 途中で切れた行と、JSON でない行
        return {}
    return found if isinstance(found, dict) else {}


def _read_range(row: Mapping[str, Any]) -> Optional[tuple[str, int, int, int]]:
    """`Read` の結果の行の、ファイル・返した最初の行・返した行の数・ファイルの行の数。ほかの行と失敗した `Read` は None。"""
    result = row.get("tool_use_result")
    found = result.get("file") if isinstance(result, dict) and result.get("type") == "text" else None
    if row.get("type") != "user" or not isinstance(found, dict):
        return None
    path = found.get("filePath")
    start, count, total = (found.get(key) for key in ("startLine", "numLines", "totalLines"))
    if not isinstance(path, str) or not all(values.is_int(v) for v in (start, count, total)):
        return None
    return path, start, count, total


def _covers(ranges: list[tuple[int, int, int]]) -> bool:
    """読んだ範囲（最初の行・行の数・ファイルの行の数）を合わせると、1 行目から最後の行まで途切れないか。"""
    need = 1
    for start, count, _ in sorted(ranges):
        if start > need:
            break
        need = max(need, start + count)
    return need > max(total for *_, total in ranges)


def whole_reads(lines: Iterable[str], workdir: Path) -> frozenset[Path]:
    """stream-json の出力の行から、`Read` の結果を合わせて最後の行まで読んだファイル。大きいファイルは先頭だけが成功の結果で
    返る（`truncatedByTokenCap`）ので、成功したかでなく返した行の範囲で確かめる。相対パスは `workdir` から解決する。"""
    ranges: dict[str, list[tuple[int, int, int]]] = {}
    for line in lines:
        found = _read_range(_stream_row(line))
        if found is not None:
            ranges.setdefault(found[0], []).append(found[1:])
    return frozenset(Path(os.path.normpath(Path(workdir, path))) for path, got in ranges.items() if _covers(got))


def _recordable_row(row: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """出力の 1 行のうち保存してよい項目。結果の行と、応答の行の Tool の呼び出し（場所と範囲の引数だけ）で、ほかは None。"""
    if row.get("type") == "result":
        return {key: row[key] for key in STREAM_RESULT_KEYS if key in row}
    if row.get("type") != ASSISTANT or not isinstance(row.get("message"), dict):
        return None
    calls = []
    for part in _elements(row):
        args = part.get("input")
        if part.get("type") == "tool_use" and isinstance(args, dict):
            shown = {key: args[key] for key in STREAM_CALL_ARGS if key in args}
            calls.append({"type": "tool_use", "id": part.get("id"), "name": part.get("name"), "input": shown})
    return {"type": ASSISTANT, "message": {"content": calls}} if calls else None


def recordable(lines: Iterable[str]) -> list[str]:
    """stream-json の出力の行を、保存してよい項目だけの行にする。Tool の結果（読んだファイルの中身）はどの形の行にあっても
    残さないよう、除くものでなく残すものを決める。形を確かめられない行は残さない。"""
    kept = (_recordable_row(_stream_row(line)) for line in lines)
    return [json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in kept if row is not None]


def _squeeze(text: str) -> str:
    return "".join(text.split())


def usage_tokens(rec: Mapping[str, Any]) -> Optional[int]:
    """採用行の `usage` の 3 つの項目の和。形が違えば None（キャッシュの項目は無ければ 0）。"""
    usage = _message(rec).get("usage")
    if not isinstance(usage, dict) or not values.is_int(usage.get("input_tokens")):
        return None
    total = 0
    for key in USAGE_KEYS:
        value = usage[key] if key in usage else 0
        if not values.is_int(value):
            return None
        total += value
    return total


def _in_group(rec: Any, mid: Optional[str]) -> bool:
    return mid is not None and is_reply_line(rec) and message_id(rec) == mid


def _scan_group(lines: list, idx: int, mid: Optional[str]) -> tuple:
    """読み 2: 採用行より古い行から同じ `message.id` の行を集める。止まった行の位置と解釈した行（無ければ None）も返す。"""
    group = []
    for j in range(idx - 1, -1, -1):
        if not lines[j].strip():
            continue
        rec = json_line(lines[j])
        if is_boundary(rec):
            return group, None, None
        if _in_group(rec, mid):
            group.append(rec)
        elif is_reply_line(rec):
            return group, j, rec
    return group, None, None


def _find_tool(lines: list, stop: Optional[int], stop_rec: Any, tool_id: str) -> bool:
    """読み 3: 読み 2 が止まった行から古い順に、その ID の `tool_use` を持つ応答の行を探す。"""
    if stop is None:
        return False
    if tool_id in _tool_ids(stop_rec):
        return True
    for j in range(stop - 1, -1, -1):
        line = lines[j]
        if tool_id not in line and COMPACT_SUBTYPE not in line:
            continue
        rec = json_line(line)
        if is_boundary(rec):
            return False
        if is_reply_line(rec) and tool_id in _tool_ids(rec):
            return True
    return False


def _matched(anchor: Anchor, lines: list, idx: int, adopted: Mapping[str, Any], users_after: int) -> tuple:
    """手がかりと突き合わせ、(通ったか, 外れた理由) を返す。"""
    if isinstance(anchor, Tail):
        return (True, None) if users_after == 0 else (False, "after")
    if isinstance(anchor, Reply) and (anchor.text is None or users_after):
        return False, "unrecorded"
    if isinstance(anchor, ToolUse) and not anchor.tool_use_id:
        return False, "unrecorded"
    group, stop, stop_rec = _scan_group(lines, idx, message_id(adopted))
    records = list(reversed(group)) + [adopted]
    if isinstance(anchor, Reply):
        same = _squeeze("".join(_reply_text(r) for r in records)) == _squeeze(anchor.text)
        return (True, None) if same else (False, "unrecorded")
    if any(anchor.tool_use_id in _tool_ids(r) for r in records):
        return True, None
    return (True, None) if _find_tool(lines, stop, stop_rec, anchor.tool_use_id) else (False, "unrecorded")


def last_context(tail: str, anchor: Anchor) -> ContextRead:
    """圧縮の境より後の最後の応答の文脈の量と、手がかりと突き合わせた古さを返す。"""
    lines = tail.splitlines()
    partial = False
    users_after = 0
    for idx in range(len(lines) - 1, -1, -1):
        if not lines[idx].strip():
            continue
        rec = json_line(lines[idx])
        if rec is BROKEN:
            partial = True
            continue
        if is_boundary(rec):
            return ContextRead("none", "compacted")
        if not is_talk(rec):
            continue
        if rec["type"] == "user":
            users_after += 1
            continue
        if not is_reply_line(rec):
            continue
        if partial:
            return ContextRead("none", "partial")
        return _read_reply(anchor, lines, idx, rec, users_after)
    return ContextRead("none", "empty")


def _read_reply(anchor: Anchor, lines: list, idx: int, adopted: Mapping[str, Any], users_after: int) -> ContextRead:
    ok, reason = _matched(anchor, lines, idx, adopted, users_after)
    tokens = usage_tokens(adopted)
    mid = message_id(adopted)
    stamp = adopted.get(TIMESTAMP)
    stamp = stamp if timestamp_seconds(stamp) is not None else None
    if tokens is not None and mid is not None and stamp is not None:
        return ContextRead("current" if ok else "behind", reason, tokens, mid, stamp, adopted)
    if ok:
        return ContextRead("none", "form", None, mid, stamp, adopted)
    return ContextRead("none", reason)
