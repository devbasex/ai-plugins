#!/usr/bin/env python3
"""training-optout.py（試行）: LLM へ入力を渡す前に、そのアカウントが入力を学習に使わない設定かを確かめる。

    python3 training-optout.py check [--runtime claude] [--runtime codex ...] [--config-dir <設定ディレクトリ> ...]

claude は Claude Code と同じ OAuth の認証で `GET /api/oauth/account/settings` を読み、`grove_enabled`
（設定画面の「Help improve our AI models」）が false なら学習に使わないと判定する。トークンは
`CLAUDE_CODE_OAUTH_TOKEN`、無ければ `$CLAUDE_CONFIG_DIR`（既定 `~/.claude`）の `.credentials.json` から読む。
期限（`expiresAt`）の切れたトークンは送らずに確かめられないとし、更新もしない（認証ファイルを書かない。その設定
ディレクトリで claude を 1 度起動すれば更新される）。
`--config-dir` を渡すと、既定のアカウントに加えて、渡した設定ディレクトリごとにその `.credentials.json` のトークンで
確かめる（supervise は利用上限で登録アカウントを切り替えるため、使い得るアカウントをすべて確かめる）。

OAuth で確かめられない接続は確かめられないとする。既定のアカウントの環境に OAuth より優先される認証の変数
（`ANTHROPIC_API_KEY`・`ANTHROPIC_AUTH_TOKEN`・`CLAUDE_CODE_USE_BEDROCK`・`CLAUDE_CODE_USE_VERTEX`）があるときと、
supervise やラッパーが利用上限で切り替える従量の接続の宣言（`NDF_SUPERVISE_CLAUDE_FALLBACK`、無ければ置き場の
`metered.json`）があるときである。宣言を外すには `NDF_SUPERVISE_CLAUDE_FALLBACK=` を空で定義する。

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
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import deps  # noqa: E402
from step_result import EXIT_OK, EXIT_PRECONDITION, EXIT_VIOLATION, emit, main_with, result  # noqa: E402

import claude_accounts as ca  # noqa: E402

deps.require("notify")  # HTTP の呼び出しは notify（httpx の包み）が受け持つ
import notify  # noqa: E402

TOOL = "training-optout"
URL = "https://api.anthropic.com/api/oauth/account/settings"
SOURCE = "oauth/account/settings.grove_enabled"
RUNTIMES = ("claude", "codex", "kiro", "agy")


NO_TOKEN = "OAuth のトークンが無い（CLAUDE_CODE_OAUTH_TOKEN・.credentials.json）"


def claude_token(config_dir: str | None = None) -> tuple[str | None, str | None]:
    """(トークン, 確かめられない理由)。`config_dir` を渡したときは、その設定ディレクトリの `.credentials.json` だけを読む
    （環境のトークンは見ない）。期限の切れたトークンは返さない。"""
    tok = None if config_dir else os.environ.get(ca.TOKEN_ENV)
    if tok:
        return tok, None
    conf = Path(config_dir or os.environ.get(ca.CONFIG_ENV) or Path.home() / ".claude")
    try:
        data = json.loads((conf / ca.CRED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, NO_TOKEN
    o = (data.get("claudeAiOauth") or {}) if isinstance(data, dict) else {}
    tok = o.get("accessToken") if isinstance(o, dict) else None
    if not (isinstance(tok, str) and tok):
        return None, NO_TOKEN
    exp = o.get("expiresAt")
    if isinstance(exp, (int, float)) and exp / 1000 <= time.time():
        return None, f"トークンの期限切れ（CLAUDE_CONFIG_DIR={conf} claude を 1 度起動して更新する）"
    return tok, None


def foreign_auth() -> list[str]:
    """既定のアカウントの環境で OAuth より優先される認証の変数の名前（値は出さない）。"""
    return [k for k in ca.FOREIGN_AUTH_ENV if os.environ.get(k)]


def unchecked(runtime: str, reason: str, config_dir: str | None = None) -> dict:
    return {"runtime": runtime, "config_dir": config_dir, "training": None, "source": None, "updated_at": None, "reason": reason}


def check_claude(config_dir: str | None = None) -> dict:
    foreign = [] if config_dir else foreign_auth()
    if foreign:
        return unchecked("claude", f"OAuth 以外の接続が有効（{', '.join(foreign)}）", config_dir)
    tok, why = claude_token(config_dir)
    if not tok:
        return unchecked("claude", why, config_dir)
    res = notify.http_get(URL, timeout=20, headers={"Authorization": f"Bearer {tok}", "anthropic-beta": "oauth-2025-04-20"})
    if not res.ok:
        return unchecked("claude", res.error if res.status else "読めない（接続の失敗）", config_dir)
    try:
        body = json.loads(res.text)
    except ValueError:
        return unchecked("claude", "読めない（JSON でない応答）", config_dir)
    grove = body.get("grove_enabled") if isinstance(body, dict) else None
    if not isinstance(grove, bool):
        return unchecked("claude", "応答に grove_enabled の真偽値が無い", config_dir)
    return {
        "runtime": "claude",
        "config_dir": config_dir,
        "training": grove,
        "source": SOURCE,
        "updated_at": body.get("grove_updated_at"),
        "reason": None,
    }


def metered() -> list[dict]:
    """従量の接続の宣言があれば、確かめられない 1 項目（変数の名前だけを出す）。"""
    decl = ca.fallback_env()
    if not decl:
        return []
    reason = f"従量の接続の宣言がある（{', '.join(decl)}。外すには {ca.FALLBACK_ENV}= を空で定義する）"
    return [unchecked("claude", reason, ca.METERED)]


def checked_name(item: dict) -> str:
    return f"{item['runtime']}（{item['config_dir']}）" if item["config_dir"] else item["runtime"]


def cmd_check(a):
    runtimes = list(dict.fromkeys(a.runtime or ["claude"]))
    dirs = list(dict.fromkeys(a.config_dir or []))
    items = []
    for r in runtimes:
        items += [check_claude(), *map(check_claude, dirs), *metered()] if r == "claude" else [unchecked(r, "unsupported")]
    used = [checked_name(i) for i in items if i["training"] is True]
    unread = [checked_name(i) for i in items if i["training"] is None]
    if unread:
        code, summary = EXIT_PRECONDITION, f"確かめられない: {', '.join(unread)}"
    elif used:
        code, summary = EXIT_VIOLATION, f"学習に使う設定: {', '.join(used)}"
    else:
        code, summary = EXIT_OK, f"学習に使わない設定: {', '.join(map(checked_name, items))}"
    emit(result(TOOL, "ok" if code == EXIT_OK else "stopped", summary, items), code)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(required=True)
    c = sub.add_parser("check")
    c.add_argument("--runtime", action="append", choices=RUNTIMES)
    c.add_argument("--config-dir", action="extend", nargs="+", help="claude の設定ディレクトリ（既定のアカウントに加えて確かめる）")
    c.set_defaults(func=cmd_check)
    return main_with(ap, lambda a: TOOL, argv)


if __name__ == "__main__":
    main()
