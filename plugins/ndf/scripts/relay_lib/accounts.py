"""`relay.py account add|list|remove`: 切り替えに使う claude アカウントの登録・一覧・削除（#1389）。

置き場を書くのは `lib/claude_accounts.py` である。ここは端末の入出力と、専用の設定ディレクトリでの
`claude auth login` / `auth status` / `auth logout` の起動だけを持つ（推論は呼ばない）。登録は利用者が端末から
打ったときだけ行う（I2）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from . import claude as cl
from .common import PKG_ROOT  # noqa: F401  lib/ を sys.path に置く

import claude_accounts as ca  # noqa: E402,I001
import claude_usage as cu  # noqa: E402

USAGE = "usage: relay.py account add <名前> | list [--json] | remove <名前>"
# 専用の設定ディレクトリで claude を起動するときに外す変数（外の認証と混ぜない）
AUTH_ENV = (ca.TOKEN_ENV, ca.NAME_ENV, "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")


def _env(config_dir: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in AUTH_ENV and k not in cl.DROP_ENV and k != "NDF_RELAY_DIR"}
    env["CLAUDE_CONFIG_DIR"] = config_dir
    return env


def _auth(claude: str, config_dir: str, *args: str) -> subprocess.CompletedProcess | None:
    """専用の設定ディレクトリで `claude auth <副命令>` を起動する。起動できなければ None。"""
    try:
        return subprocess.run(
            [claude, "auth", *args], env=_env(config_dir), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _email(claude: str, config_dir: str) -> str | None:
    """専用の設定ディレクトリの `claude auth status`（JSON）からメールアドレスを読む。"""
    p = _auth(claude, config_dir, "status", "--json")
    if p is None:
        return None
    try:
        d = json.loads(p.stdout)
    except ValueError:
        return None
    if not isinstance(d, dict) or not d.get("loggedIn") or not isinstance(d.get("email"), str):
        return None
    return d["email"]


def cmd_add(name: str) -> int:
    if not ca.valid_name(name):
        print(f"名前は英小文字・数字・- と _ の 32 字まで（{ca.METERED} は使えない）: {name}", file=sys.stderr)
        return 2
    if not sys.stdin.isatty():
        print("登録は端末から打つ（claude auth login が認可コードの貼り付けを待つ）", file=sys.stderr)
        return 2
    old = ca.load_account(name)
    if old is not None and not old.needs_relogin:
        print(f"登録済み: {name}（{old.email}）。置き直すなら先に account remove {name}", file=sys.stderr)
        return 1
    claude = cl.resolve_claude()
    if claude is None:
        print("本物の claude が見つからない", file=sys.stderr)
        return 1
    staging = ca.staging_dir(name)
    try:
        try:
            subprocess.run([claude, "auth", "login"], env=_env(staging))
        except (OSError, subprocess.SubprocessError) as e:
            print(f"claude auth login を起動できない（{e}）", file=sys.stderr)
            return 1
        email = _email(claude, staging)
        if email is None or not os.path.isfile(os.path.join(staging, ca.CRED_FILE)):
            print("ログインが通らなかった。登録しない", file=sys.stderr)
            return 1
        owner = ca.owner_of(email, other_than=name)
        if owner:
            print(f"登録済み: {owner}（{email}）", file=sys.stderr)
            return 1
        ca.register(name, staging, email)
    finally:
        ca.discard(staging)
    print(f"登録した: {name}（{email}）")
    return 0


def _window(w: dict | None, form: str) -> str:
    if not w:
        return "-"
    return f"{w['utilization']:.0f}%（{ca.local_time(cu.epoch(w.get('resets_at')), form)}）"


def cmd_list(as_json: bool) -> int:
    try:
        rows = ca.rows()
    except OSError as e:
        print(f"置き場を読めない（{e}）", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("登録済みのアカウントは無い")
        return 0
    table = [("名前", "識別", "5 時間", "7 日", "支出上限", "状態")]
    for r in rows:
        spend = {True: "達している", False: "達していない"}.get(r["spend_limit_reached"], "-")
        table.append((r["name"], r["email"], _window(r["five_hour"], "%H:%M"), _window(r["seven_day"], "%m-%d"), spend, r["state"]))
    widths = [max(_width(row[i]) for row in table) for i in range(len(table[0]))]
    for row in table:
        print("  ".join(c + " " * (w - _width(c)) for c, w in zip(row, widths)).rstrip())
    return 0


def _width(s: str) -> int:
    """端末の表示幅（全角は 2）。"""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in s)


def cmd_remove(name: str) -> int:
    if ca.load_account(name) is None:
        print(f"登録されていない: {name}", file=sys.stderr)
        return 1
    claude = cl.resolve_claude()
    ok = False
    if claude is not None:
        p = _auth(claude, ca.account_dir(name), "logout")
        ok = p is not None and p.returncode == 0
    if not ok:
        print("claude auth logout が通らなかった（登録は外す）", file=sys.stderr)
    ca.unregister(name)
    print(f"外した: {name}")
    return 0


def cmd_account(args: list[str]) -> int:
    sub = args[0] if args else ""
    if sub == "add" and len(args) == 2:
        return cmd_add(args[1])
    if sub == "list" and args[1:] in ([], ["--json"]):
        return cmd_list(args[1:] == ["--json"])
    if sub == "remove" and len(args) == 2:
        return cmd_remove(args[1])
    print(USAGE, file=sys.stderr)
    return 2
