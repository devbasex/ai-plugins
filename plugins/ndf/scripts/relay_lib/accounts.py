"""`relay.py account add|list|capacity|remove`: 切り替えに使う claude アカウントの登録・一覧・枠の大きさの宣言・削除（#1389・#1453）。

置き場を書くのは `lib/claude_accounts.py` である。ここは端末の入出力と、専用の設定ディレクトリでの
`claude auth login` / `auth status` / `auth logout` の起動と、重複の確かめ（I3）だけを持つ（推論は呼ばない）。登録は利用者が端末から
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

USAGE = "usage: relay.py account add <名前> | list [--json] | capacity <名前> <5 時間の枠|-> <週の枠|-> | remove <名前>"
AUTH_ENV = ca.AUTH_ENV  # 専用の設定ディレクトリで claude を起動するときに外す変数（正本は claude_accounts）


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


def _identity(claude: str, config_dir: str) -> tuple[str, str, str] | None:
    """専用の設定ディレクトリの `claude auth status`（JSON）からメールアドレス・組織の ID・組織名を読む。

    組織の 2 つは無ければ空。1 つのメールアドレスで組織ごとに別のアカウントになるため、組織まで読む（I3）。
    """
    p = _auth(claude, config_dir, "status", "--json")
    if p is None:
        return None
    try:
        d = json.loads(p.stdout)
    except ValueError:
        return None
    if not isinstance(d, dict) or not d.get("loggedIn") or not isinstance(d.get("email"), str):
        return None
    org_id, org_name = d.get("orgId"), d.get("orgName")
    return d["email"], org_id if isinstance(org_id, str) else "", org_name if isinstance(org_name, str) else ""


def _backfill_org(claude: str, acc: ca.Account) -> str:
    """組織を記録する前の登録の組織を、その設定ディレクトリの `auth status` から読み直して書き戻す。読めなければ空。"""
    ident = _identity(claude, ca.account_dir(acc.name))
    if ident is None or not ident[1] or ident[0].lower() != acc.email.lower():
        return ""
    ca.set_org(acc.name, ident[1], ident[2])
    return ident[1]


def owner_of(email: str, org_id: str = "", other_than: str = "", claude: str | None = None) -> str | None:
    """同じメールアドレスと組織で登録済みのアカウントの名前（I3）。

    1 つのメールアドレスで複数の組織（個人と Team など）に属せ、組織ごとに利用上限が別になる。既存の登録の
    組織が分からない（組織を記録する前の登録）ときは、`claude` があれば読み直して書き戻す。それでもどちらかの
    組織が分からなければ、違うと言い切れないので同じとみなす。
    """
    for n in ca.names():
        acc = ca.load_account(n)
        if n == other_than or not acc or not acc.email or acc.email.lower() != email.lower():
            continue
        known = acc.org_id or (_backfill_org(claude, acc) if claude and org_id else "")
        if not known or not org_id or known == org_id:
            return n
    return None


def _who(email: str, org_name: str) -> str:
    return f"{email}・{org_name}" if org_name else email


def cmd_add(name: str) -> int:
    if not ca.valid_name(name):
        print(f"名前は英小文字・数字・- と _ の 32 字まで（{ca.METERED} は使えない）: {name}", file=sys.stderr)
        return 2
    if sys.platform == "darwin":
        # macOS の claude は資格情報を Keychain に置き、設定ディレクトリの .credentials.json を書かない
        print("macOS では登録できない（claude が資格情報を Keychain に置き、.credentials.json を書かない）。Linux で使う", file=sys.stderr)
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
        ident = _identity(claude, staging)
        if ident is None or not os.path.isfile(os.path.join(staging, ca.CRED_FILE)):
            print("ログインが通らなかった。登録しない", file=sys.stderr)
            return 1
        email, org_id, org_name = ident
        owner = owner_of(email, org_id, other_than=name, claude=claude)
        if owner:
            acc = ca.load_account(owner)
            print(f"登録済み: {owner}（{_who(acc.email, acc.org_name) if acc else email}）", file=sys.stderr)
            return 1
        ca.register(name, staging, email, org_id, org_name)
    finally:
        ca.discard(staging)
    print(f"登録した: {name}（{_who(email, org_name)}）")
    return 0


def _window(w: dict | None, form: str) -> str:
    if not w:
        return "-"
    return f"{w['utilization']:.0f}%（{ca.local_time(cu.epoch(w.get('resets_at')), form)}）"


def _usd(v: float | None) -> str:
    """USD の値（小数第 1 位まで。整数なら小数を省く）。不明は `-`。"""
    if v is None:
        return "-"
    t = f"{v:,.1f}"
    return t[:-2] if t.endswith(".0") else t


def _capacity(r: dict) -> str:
    """`<5 時間の枠> / <週の枠>`。宣言の値には `*` を付ける。"""
    return " / ".join(_usd(r["capacity"][k]) + ("*" if r["capacity_declared"][k] is not None else "") for k in ca.WINDOWS)


def _scoped(scoped: list | None) -> str:
    """モデル別の週の枠のうち使用率の最も高い 1 つ。"""
    if not scoped:
        return "-"
    w = max(scoped, key=lambda x: x["utilization"])
    return f"{w['model'] or '-'} {_window(w, '%m-%d')}"


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
    table = [("名前", "識別", "5 時間", "7 日", "モデル別の週", "支出上限", "枠の大きさ", "残り", "状態")]
    emails = [r["email"].lower() for r in rows]
    for r in rows:
        spend = {True: "達している", False: "達していない"}.get(r["spend_limit_reached"], "-")
        # 同じメールアドレスが 2 件以上あるときだけ組織名を添える（1 件なら個人の組織名はメールの繰り返しになる）
        ident = f"{r['email']}（{r['org_name']}）" if r["org_name"] and emails.count(r["email"].lower()) > 1 else r["email"]
        table.append(
            (
                r["name"],
                ident,
                _window(r["five_hour"], "%H:%M"),
                _window(r["seven_day"], "%m-%d"),
                _scoped(r["scoped"]),
                spend,
                _capacity(r),
                _usd(r["remaining"]),
                r["state"],
            )
        )
    widths = [max(_width(row[i]) for row in table) for i in range(len(table[0]))]
    for row in table:
        print("  ".join(c + " " * (w - _width(c)) for c, w in zip(row, widths)).rstrip())
    return 0


def _width(s: str) -> int:
    """端末の表示幅（全角は 2）。"""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in s)


def _capacity_arg(v: str) -> float | None:
    """枠の引数（正の数か `-`）。`-` は None、読めない・0 以下は ValueError。"""
    if v == "-":
        return None
    x = float(v)
    if not x > 0 or x == float("inf"):
        raise ValueError(v)
    return x


def cmd_capacity(name: str, five: str, seven: str) -> int:
    """枠の大きさを宣言する（`-` はその枠の宣言を外して対応表へ戻す）。"""
    try:
        declared = {"five_hour": _capacity_arg(five), "seven_day": _capacity_arg(seven)}
    except ValueError:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        acc = ca.set_capacity(name, declared)
    except (ca.lock_timeout(), OSError) as e:
        print(f"置き場へ書けない（{e}）", file=sys.stderr)
        return 1
    if acc is None:
        print(f"登録されていない: {name}", file=sys.stderr)
        return 1
    cap = acc.capacity()
    print(f"枠の大きさ: {name} 5 時間 {_usd(cap['five_hour'])} / 週 {_usd(cap['seven_day'])}")
    return 0


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
    if sub == "capacity" and len(args) == 4:
        return cmd_capacity(*args[1:])
    if sub == "remove" and len(args) == 2:
        return cmd_remove(args[1])
    print(USAGE, file=sys.stderr)
    return 2
