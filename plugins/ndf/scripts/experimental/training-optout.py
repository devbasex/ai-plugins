#!/usr/bin/env python3
"""training-optout.py（試行）: LLM へ入力を渡す前に、そのアカウントが入力を学習に使わない設定かを確かめる。

    python3 training-optout.py check [--runtime claude] [--runtime codex ...]

claude は Claude Code と同じ OAuth の認証で `GET /api/oauth/account/settings` を読み、`grove_enabled`
（設定画面の「Help improve our AI models」）が false なら学習に使わないと判定する。トークンは
`CLAUDE_CODE_OAUTH_TOKEN`、無ければ `$CLAUDE_CONFIG_DIR`（既定 `~/.claude`）の `.credentials.json` から読む。
期限切れのトークンは更新しない（401 で止まる。claude を 1 度起動すれば更新される）。

codex / kiro / agy は確かめる手段が無いため `unsupported` を返す。

すべてが学習に使わない設定なら ok（0）。学習に使う設定があれば stopped（1）。確かめられないもの
（認証が無い・応答の形が違う・HTTP の失敗・unsupported）があれば stopped（3）で、学習に使わないとは扱わない。
認証情報とアカウントの ID は出力に出さない。結果は lib/step_result.py の形の 1 行の JSON。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from step_result import EXIT_OK, EXIT_PRECONDITION, EXIT_VIOLATION, emit, main_with, result  # noqa: E402

TOOL = "training-optout"
URL = "https://api.anthropic.com/api/oauth/account/settings"
SOURCE = "oauth/account/settings.grove_enabled"
RUNTIMES = ("claude", "codex", "kiro", "agy")


def claude_token() -> str | None:
    tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if tok:
        return tok
    conf = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    try:
        data = json.loads((conf / ".credentials.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tok = (data.get("claudeAiOauth") or {}).get("accessToken") if isinstance(data, dict) else None
    return tok if isinstance(tok, str) and tok else None


def unknown(runtime: str, reason: str) -> dict:
    return {"runtime": runtime, "training": None, "source": None, "updated_at": None, "reason": reason}


def check_claude() -> dict:
    tok = claude_token()
    if not tok:
        return unknown("claude", "OAuth のトークンが無い（CLAUDE_CODE_OAUTH_TOKEN・.credentials.json）")
    req = urllib.request.Request(URL, headers={"Authorization": f"Bearer {tok}", "anthropic-beta": "oauth-2025-04-20"})
    try:
        body = json.load(urllib.request.urlopen(req, timeout=20))
    except urllib.error.HTTPError as e:
        return unknown("claude", f"HTTP {e.code}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return unknown("claude", f"読めない（{type(e).__name__}）")
    grove = body.get("grove_enabled") if isinstance(body, dict) else None
    if not isinstance(grove, bool):
        return unknown("claude", "応答に grove_enabled の真偽値が無い")
    return {"runtime": "claude", "training": grove, "source": SOURCE, "updated_at": body.get("grove_updated_at"), "reason": None}


def cmd_check(a):
    runtimes = list(dict.fromkeys(a.runtime or ["claude"]))
    items = [check_claude() if r == "claude" else unknown(r, "unsupported") for r in runtimes]
    used = [i["runtime"] for i in items if i["training"] is True]
    unread = [i["runtime"] for i in items if i["training"] is None]
    if unread:
        code, summary = EXIT_PRECONDITION, f"確かめられない: {', '.join(unread)}"
    elif used:
        code, summary = EXIT_VIOLATION, f"学習に使う設定: {', '.join(used)}"
    else:
        code, summary = EXIT_OK, f"学習に使わない設定: {', '.join(runtimes)}"
    emit(result(TOOL, "ok" if code == EXIT_OK else "stopped", summary, items), code)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(required=True)
    c = sub.add_parser("check")
    c.add_argument("--runtime", action="append", choices=RUNTIMES)
    c.set_defaults(func=cmd_check)
    return main_with(ap, lambda a: TOOL, argv)


if __name__ == "__main__":
    main()
