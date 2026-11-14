"""ラッパーが子の画面と会話の記録から読む事実（#3243・#2595・#3337）。

- 画面の末尾: 利用上限の行（`ndf.adapters.agents.usage_limit`）と、認証の失敗で `/goal` が外れた行
- 会話の記録（`<CLAUDE_CONFIG_DIR か ~/.claude>/projects/<作業ディレクトリの鍵>/<セッション>.jsonl`）の末尾:
  最後の応答の文脈の量（入力・キャッシュの読み・キャッシュの作成の token の和）
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping, Optional

from ndf.adapters import agents

# 認証の失敗で子が入力待ちに戻った印（Claude Code の画面の文言。#3243 の画面の写し）
AUTH_FAILED = re.compile(r"authentication failed|Please run /login|API Error: 40[13]\b", re.IGNORECASE)
# 画面の制御の列（色・カーソル）。文言を探す前に外す
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
# 行頭の字下げと罫線（画面の枠・応答の印 ⎿ など）。利用上限の行は行頭で照らすので外す
LEAD = re.compile(r"^[^\w{]+", re.MULTILINE)
USAGE_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def plain(raw: bytes) -> str:
    return ANSI.sub("", raw.decode("utf-8", errors="replace"))


def limit_line(screen: str) -> Optional[str]:
    return agents.usage_limit(LEAD.sub("", screen))


def auth_failed(screen: str) -> bool:
    return AUTH_FAILED.search(screen) is not None


def transcript_path(environ: Mapping[str, str], cwd: Path, session_id: str) -> Path:
    base = environ.get("CLAUDE_CONFIG_DIR")
    root = Path(base) if base else Path.home() / ".claude"
    key = re.sub(r"[^A-Za-z0-9]", "-", str(cwd))
    return root / "projects" / key / f"{session_id}.jsonl"


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
