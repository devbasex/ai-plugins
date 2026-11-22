"""ラッパーが子の画面と会話の記録から読む事実（#3243・#2595・#3337）。

- 画面の末尾: 利用上限の行（`ndf.adapters.agents.usage_limit`）と、認証の失敗で `/goal` が外れた行
- 会話の記録（`<CLAUDE_CONFIG_DIR か ~/.claude>/projects/<作業ディレクトリのキー>/<セッション>.jsonl`）の末尾:
  最後の応答の文脈の量（入力・キャッシュの読み・キャッシュの作成の token の和）と、止めてよい時点の事実
  （道具の実行の途中か・応答が終わったか。#3864 の決定 11）。道具の実行中は会話の行の最後が `stop_reason: tool_use` の
  `assistant` の `tool_use` のままで、終わると `user` の `tool_result` が同じ `tool_use_id` で続く。ファイルの最後の行は
  会話の行でないことがある（`last-prompt` など。#4018 の確かめ 3）
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

from ndf.adapters import agents
from ndf.domains.relay import signals

# 認証の失敗で子が入力待ちに戻ったマーカー（Claude Code の画面の文言。#3243 の画面の写し）
AUTH_FAILED = re.compile(r"authentication failed|Please run /login|API Error: 40[13]\b", re.IGNORECASE)
# 画面の制御の列（色・カーソル）。文言を探す前に外す
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
# 行頭の字下げと罫線（画面のスロット・応答のマーカー ⎿ など）。利用上限の行は行頭で照らすので外す
LEAD = re.compile(r"^[^\w{]+", re.MULTILINE)
ENDED = ("end_turn", "stop_sequence")  # 応答が終わった `stop_reason`
USAGE_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def plain(raw: bytes) -> str:
    return ANSI.sub("", raw.decode("utf-8", errors="replace"))


def limit_line(screen: str) -> Optional[str]:
    return agents.usage_limit(LEAD.sub("", screen))


def auth_failed(screen: str) -> bool:
    return AUTH_FAILED.search(screen) is not None


def transcript_path(environ: Mapping[str, str], cwd: Path, session_id: str) -> Path:
    key = re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    return signals.claude_config_root(environ) / "projects" / key / f"{session_id}.jsonl"


def context_tokens(path: Path, size: int) -> Optional[int]:
    """会話の記録の末尾 `size` バイトから、最後の応答の文脈の量。読めなければ None。"""
    if not path.is_file():
        return None
    for line in reversed(agents.tail(path, size).splitlines()):
        total = _usage_total(line)
        if total is not None:
            return total
    return None


def _usage_total(line: str) -> Optional[int]:
    try:
        row = json.loads(line)
    except ValueError:  # noqa: NDF401 ファイル: 末尾で切れた行は数えない
        return None
    message = row.get("message") if isinstance(row, dict) else None
    usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict):
        return None
    return sum(int(usage[k]) for k in USAGE_KEYS if isinstance(usage.get(k), int))


class _Unsure(ValueError):
    """止めてよい時点を確定できない（記録を読めない・完全な行が壊れている・会話の記録の形が違う）。"""


def _lines_backward(path: Path, size: int) -> Iterator[str]:
    """末尾 `size` バイトの完全な行を新しい順に。行の境は JSONL の改行で決め、範囲の端で切れた行だけを飛ばす。

    範囲の始まりがファイルの先頭でなければ最初の行は切れている。改行で終わらない最後の行は書きかけである。
    読めなければ `_Unsure`（無いファイルは呼ぶ側が先に調べる）。
    """
    try:
        with open(path, "rb") as fh:
            begin = max(0, os.fstat(fh.fileno()).st_size - size)
            fh.seek(begin)
            data = fh.read()
    except OSError as exc:
        raise _Unsure(str(exc)) from exc
    lo = 0
    if begin > 0:
        cut = data.find(b"\n")
        lo = len(data) if cut < 0 else cut + 1
    hi = data.rfind(b"\n")
    while hi >= lo:
        prev = data.rfind(b"\n", lo, hi)
        yield data[prev + 1 : hi].decode("utf-8", errors="replace")
        hi = prev


def _talk(path: Path, size: int) -> Optional[list[dict[str, Any]]]:
    """末尾から読み、最後の会話の記録（`user`・`assistant`）と、最後の `assistant` から後ろの会話の記録を古い順に。

    最後の `assistant` を読んだところで読むのを止める。ファイルが無ければ None。会話でない行は飛ばし、
    読めない・完全な行が JSON でない・形の違う会話の記録（`message` が object でない）は `_Unsure`。
    """
    if not path.is_file():
        return None
    rows: list[dict[str, Any]] = []
    for line in _lines_backward(path, size):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise _Unsure("壊れた行") from exc
        if not isinstance(row, dict) or row.get("type") not in ("user", "assistant"):  # 会話の記録だけ
            continue
        if not isinstance(row.get("message"), dict):
            raise _Unsure(str(row.get("type")))
        rows.append(row)
        if row["type"] == "assistant":
            break
    rows.reverse()
    return rows


def _blocks(row: Mapping[str, Any], kind: str) -> list[Mapping[str, Any]]:
    content = row["message"].get("content")
    return [b for b in content if isinstance(b, dict) and b.get("type") == kind] if isinstance(content, list) else []


def _pending(rows: list[dict[str, Any]]) -> Optional[bool]:
    last = next((i for i in range(len(rows) - 1, -1, -1) if rows[i]["type"] == "assistant"), None)
    if last is None:
        return None
    ids = {b.get("id") for b in _blocks(rows[last], "tool_use")}
    done = {b.get("tool_use_id") for r in rows[last + 1 :] if r["type"] == "user" for b in _blocks(r, "tool_result")}
    return bool(ids - done)


def tool_pending(path: Path, size: int) -> Optional[bool]:
    """最後の `assistant` の記録の `tool_use` に対応する `tool_result` が後ろに無いか。読めない・壊れた・形が違えば None。"""
    try:
        rows = _talk(path, size)
    except _Unsure:  # noqa: NDF401 ファイル: 確定できない記録は止めてよい時点にしない（None）
        return None
    return None if rows is None else _pending(rows)


def turn_done(path: Path, size: int) -> Optional[tuple[bool, Optional[float]]]:
    """最後の会話の記録が終わった応答なら `(True, その時刻)`、途中なら `(False, None)`。読めない・壊れた・形が違えば None。"""
    try:
        rows = _talk(path, size)
    except _Unsure:  # noqa: NDF401 ファイル: 確定できない記録は止めてよい時点にしない（None）
        return None
    if not rows:
        return None
    last = rows[-1]
    if last["type"] != "assistant" or last["message"].get("stop_reason") not in ENDED or _pending(rows):
        return (False, None)
    return (True, _time(last.get("timestamp")))


def _time(value: Any) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:  # noqa: NDF401 ファイル: 時刻の読めない記録は時刻を持たない
        return None
