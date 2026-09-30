"""`relay.py account`: 切り替えに使う claude アカウントと従量の接続の登録・一覧・確かめ・枠の大きさの宣言・削除（#1389・#1453・#1468）。

置き場を書くのは `lib/claude_accounts.py` である。ここは副命令の入口（引数の読み取り・登録の途中の状態の掃除・
結果を文か JSON で出す）と、`list`・`remove`・`capacity`・`check` の中身を持つ（推論は呼ばない）。OAuth の登録は
`login`、Bedrock の登録と確かめは `bedrock`、値の決定（引数 → 対話 → 足りない引数）は `ask` が持つ。

すべての副命令が `--yes`（確認を飛ばす。確認の無い副命令では何もしない）と `--json`（結果を JSON 1 つで標準出力へ。
人向けの文と問いは標準エラー）を受ける。端末でなければ入力を待たず、足りない引数を名前で示して終了コード 2 で終わる。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import bedrock as bd
from . import login
from .ask import Asker, Fail, MissingArgs
from .common import PKG_ROOT  # noqa: F401  lib/ を sys.path に置く
from .login import owner_of  # noqa: F401  公開の名前（テストが使う）

import claude_accounts as ca  # noqa: E402,I001

USAGE = (
    "usage: relay.py account add [<名前>] [--code <コード>|-] | add-bedrock [--profile <名前>] [--region <地域>] [--model <ID>]"
    " | check metered | list | capacity <名前> <5 時間の枠|-> <週の枠|-> | remove <名前>|metered  （共通: [--yes] [--json]）"
)
AUTH_ENV = ca.AUTH_ENV  # 専用の設定ディレクトリで claude を起動するときに外す変数（正本は claude_accounts）


def cmd_add(name: str, code: str | None = None, yes: bool = False, as_json: bool = False, swept: set[str] | None = None) -> int:
    """`account add`（関数として呼ぶ形。入口は `cmd_account`）。"""
    return _respond("add", as_json, lambda: _add(Asker(yes), name, code, as_json, swept or set()))


def _add(asker: Asker, name: str | None, code: str | None, as_json: bool, swept: set[str]) -> dict:
    name = asker.value("<名前>", "アカウントの名前", name)
    if code is not None:
        res = login.finish_login(name, login.read_code(code), swept)
    elif asker.interactive:
        res = login.add_once(name, quiet_stdout=as_json)
    else:
        res = login.start_login(name)
    return {"name": name, **res}


def _window(w: dict | None) -> str:
    """枠の使用率（表が 1 行に収まるよう、リセットの日時は添えない。日時は JSON の出力が持つ）。"""
    if not w:
        return "-"
    return f"{w['utilization']:.0f}%"


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
    return f"{w['model'] or '-'} {_window(w)}"


def _metered(source: str, state: str, provider: str | None = None, details: dict | None = None, **extra) -> dict:
    """従量の接続の行（`extra` は `state` の前に置く）。"""
    return {
        "name": ca.METERED,
        "kind": "metered",
        "source": source,
        "provider": provider,
        "details": details or {},
        **extra,
        "state": state,
    }


def _metered_row() -> dict | None:
    """一覧の最後に足す従量の接続の行（宣言が無ければ None）。環境変数の宣言は値を出さず変数の名前だけを出す。"""
    if ca.FALLBACK_ENV in os.environ:
        keys = list(ca.fallback_env())
        if not keys:
            return None
        return _metered("env", "環境変数の宣言", keys=keys)
    decl = ca.load_metered()
    if decl is not None:
        return _metered("saved", "保存した宣言", decl.provider, decl.details)
    if ca.metered_problem() is not None:
        return _metered("saved", "壊れている（宣言なしとして扱う）")
    return None


def _metered_label(m: dict) -> str:
    if m["source"] == "env":
        return "環境変数（" + ", ".join(m["keys"]) + "）"
    if m["provider"] == bd.PROVIDER:
        return bd.decl_short(m["details"])
    return m["provider"] or "-"


def _list_rows() -> dict:
    try:
        rows = [{**r, "kind": "oauth"} for r in ca.rows()]
    except OSError as e:
        raise Fail("write_failed", f"置き場を読めない（{e}）") from e
    m = _metered_row()
    rows += [m] if m else []
    if not rows:
        return {"rows": rows, "text": "登録済みのアカウントは無い"}
    return {"rows": rows, "text": "\n".join(_table_lines(rows))}


HEADER = ("名前", "識別", "5 時間", "7 日", "モデル別の週", "支出上限", "枠の大きさ", "残り", "状態")


def _table_row(r: dict, emails: list[str]) -> tuple[str, ...]:
    """一覧の 1 件を表示の 1 行へ写す。`emails` は全件のメールアドレス（小文字）。"""
    if r["kind"] == "metered":
        return (r["name"], _metered_label(r), *("-",) * (len(HEADER) - 3), r["state"])
    spend = {True: "yes", False: "no"}.get(r["spend_limit_reached"], "-")
    # 同じメールアドレスが 2 件以上あるときだけ組織名を添える（1 件なら個人の組織名はメールの繰り返しになる）
    ident = f"{r['email']}（{_org_label(r)}）" if r["org_name"] and emails.count(r["email"].lower()) > 1 else r["email"]
    return (
        r["name"],
        ident,
        _window(r["five_hour"]),
        _window(r["seven_day"]),
        _scoped(r["scoped"]),
        spend,
        _capacity(r),
        _usd(r["remaining"]),
        r["state"],
    )


def _org_label(r: dict) -> str:
    """組織名の表示。個人の組織の既定の名前（`<メール>'s Organization`）は `個人` に縮める。"""
    return "個人" if r["org_name"].lower() == f"{r['email']}'s organization".lower() else r["org_name"]


def _table_lines(rows: list[dict]) -> list[str]:
    """見出しと各件を列の幅（全角は 2）でそろえた行。"""
    emails = [r["email"].lower() for r in rows if r["kind"] == "oauth"]
    table = [HEADER, *(_table_row(r, emails) for r in rows)]
    widths = [max(_width(row[i]) for row in table) for i in range(len(table[0]))]
    return ["  ".join(c + " " * (w - _width(c)) for c, w in zip(row, widths)).rstrip() for row in table]


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


def _capacity_cmd(asker: Asker, name: str | None, five: str | None, seven: str | None) -> dict:
    """枠の大きさを宣言する（`-` はその枠の宣言を外して対応表へ戻す）。"""
    name = asker.value("<名前>", "アカウントの名前", name)
    five = asker.value("<5 時間の枠>", "5 時間の枠（USD か -）", five)
    seven = asker.value("<週の枠>", "週の枠（USD か -）", seven)
    try:
        declared = {"five_hour": _capacity_arg(five), "seven_day": _capacity_arg(seven)}
    except ValueError as e:
        raise Fail("invalid_name", USAGE, 2) from e
    try:
        acc = ca.set_capacity(name, declared)
    except (ca.lock_timeout(), OSError) as e:
        raise Fail("write_failed", f"置き場へ書けない（{e}）") from e
    if acc is None:
        raise Fail("not_registered", f"登録されていない: {name}")
    cap = acc.capacity()
    return {"name": name, "capacity": cap, "text": f"枠の大きさ: {name} 5 時間 {_usd(cap['five_hour'])} / 週 {_usd(cap['seven_day'])}"}


def _remove(asker: Asker, name: str | None) -> dict:
    name = asker.value("<名前>", "外す名前（アカウントか metered）", name)
    if name == ca.METERED:
        if not ca.remove_metered():
            raise Fail("no_declaration", "保存した従量の接続の宣言が無い")
        return {"name": name, "text": f"外した: {name}（保存した従量の接続の宣言）"}
    if ca.load_account(name) is None:
        raise Fail("not_registered", f"登録されていない: {name}")
    if not login.logout(name):
        print("claude auth logout が通らなかった（登録は外す）", file=sys.stderr)
    ca.unregister(name)
    return {"name": name, "text": f"外した: {name}"}


def _verify_or_fail(t: bd.Target) -> None:
    fail = bd.verify(t)
    if fail is not None:
        raise Fail(fail.reason, f"{t.label()} を{fail.text()}。保存しない", aws_error=fail.aws_error)


def _add_bedrock(asker: Asker, profile: str | None, region: str | None, model: str | None) -> dict:
    """Bedrock の従量の接続を登録する（F1）。呼べるかの確認が通ったものだけを保存する（I2）。"""
    if bd.aws_path() is None:
        raise Fail("aws_missing", "aws CLI が見つからない（Bedrock の登録には aws が要る）")
    if not profile:
        cands = bd.profiles()
        if not cands:
            raise Fail("no_profiles", "AWS のプロファイルが無い（aws configure list-profiles が 0 件）。先に aws configure sso などで作る")
        profile = asker.value("--profile", "AWS のプロファイル", None, cands)
    region = region or bd.region(profile) or asker.value("--region", f"地域（{profile} に region が無い）")
    model = model or asker.value("--model", "モデル", None, bd.models(profile, region))
    t = bd.Target(profile, region, model)
    _verify_or_fail(t)
    old = ca.load_metered()
    det = t.details()
    q, previous = f"{bd.decl_label(det)} を従量の接続として保存する？", {}
    if old is not None:
        prev = old.details.get("profile") or old.provider
        q, previous = f"前の宣言（{prev}）を {bd.decl_label(det)} で置き換える？", {"previous_profile": prev}
    if not asker.confirm("--yes", q):
        raise MissingArgs(["--yes"])
    try:
        ca.save_metered(bd.PROVIDER, t.env(), det)
    except (OSError, ValueError) as e:
        raise Fail("write_failed", f"置き場へ書けない（{e}）") from e
    if ca.FALLBACK_ENV in os.environ:
        print(f"環境変数 {ca.FALLBACK_ENV} が定義されているため、保存した宣言は効かない（環境変数が優先する）", file=sys.stderr)
    return {
        "provider": bd.PROVIDER,
        **det,
        "replaced": old is not None,
        "text": f"登録した: 従量の接続（{bd.decl_inline(det)}）",
        **previous,
    }


def _check(name: str | None) -> dict:
    """今効いている従量の接続の宣言で、Bedrock の Claude を呼べるかを確かめる（F2）。"""
    if name != ca.METERED:
        raise Fail("invalid_name", "確かめられるのは metered だけ: account check metered", 2)
    env = ca.fallback_env()
    if not env:
        raise Fail("no_declaration", "従量の接続の宣言が無い")
    t = bd.from_env(env)
    if t is None:
        raise Fail("not_bedrock", "今の宣言は Bedrock（CLAUDE_CODE_USE_BEDROCK・AWS_REGION・ANTHROPIC_MODEL）でないため確かめられない")
    if bd.aws_path() is None:
        raise Fail("aws_missing", "aws CLI が見つからない")
    _verify_or_fail(t)
    det = t.details()
    return {"provider": bd.PROVIDER, **det, "text": f"呼べる: 従量の接続（{bd.decl_inline(det)}）"}


# ---------------------------------------------------------------- 入口


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Fail("invalid_name", f"{message}\n{USAGE}", 2)


def _account_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--yes", action="store_true")
    common.add_argument("--json", action="store_true")
    p = _Parser(prog="relay.py account", add_help=False)
    sub = p.add_subparsers(dest="sub", parser_class=_Parser)
    a = sub.add_parser("add", parents=[common], add_help=False)
    a.add_argument("name", nargs="?")
    a.add_argument("--code")
    b = sub.add_parser("add-bedrock", parents=[common], add_help=False)
    for k in ("--profile", "--region", "--model"):
        b.add_argument(k)
    sub.add_parser("check", parents=[common], add_help=False).add_argument("name", nargs="?")
    sub.add_parser("list", parents=[common], add_help=False)
    sub.add_parser("remove", parents=[common], add_help=False).add_argument("name", nargs="?")
    c = sub.add_parser("capacity", parents=[common], add_help=False)
    for k in ("name", "five", "seven"):
        c.add_argument(k, nargs="?")
    return p


def _emit(command: str, as_json: bool, code: int, payload: dict) -> int:
    if as_json:
        body = payload["rows"] if command == "list" and code == 0 else {"ok": code == 0, "command": command, **payload}
        print(json.dumps(body, ensure_ascii=False, indent=2 if command == "list" else None))
    return code


def _fail(command: str, as_json: bool, code: int, message: str, payload: dict) -> int:
    """失敗を文（標準エラー）か JSON 1 つで出す。JSON の鍵は `payload` の後に `message` を置く。"""
    if not as_json:
        print(message, file=sys.stderr)
    return _emit(command, as_json, code, {**payload, "message": message})


def _respond(command: str, as_json: bool, fn) -> int:
    """副命令を動かし、結果を文（成功は標準出力・失敗は標準エラー）か JSON 1 つ（標準出力）で出す。"""
    try:
        res = fn()
    except MissingArgs as e:
        cands = "".join(f"\n  {k} の候補: {', '.join(v)}" for k, v in e.candidates.items())
        msg = ("入力を待たずに止めた。足りない引数: " + ", ".join(e.missing) + cands) if e.missing else "入力が無い"
        return _fail(command, as_json, 2, msg, {"reason": "missing_args", "missing": e.missing, "candidates": e.candidates})
    except Fail as e:
        return _fail(command, as_json, e.code, e.message, {"reason": e.reason, **e.extra})
    except OSError as e:
        return _fail(command, as_json, 1, f"置き場へ書けない（{e}）", {"reason": "write_failed"})
    text = res.pop("text", None)
    if not as_json and text:
        print(text)
    return _emit(command, as_json, 0, res)


def cmd_account(args: list[str]) -> int:
    try:
        a = _account_parser().parse_args(args)
    except Fail as e:  # 読めない引数は usage と 2（今と同じ。決定 9）
        print(e.message, file=sys.stderr)
        return 2
    if a.sub is None:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        swept = ca.sweep_pending()  # 期限を過ぎた登録の途中の状態を毎回捨てる（E10）
    except OSError:
        swept = set()
    asker = Asker(a.yes)
    handlers = {
        "add": lambda: _add(asker, a.name, a.code, a.json, swept),
        "add-bedrock": lambda: _add_bedrock(asker, a.profile, a.region, a.model),
        "check": lambda: _check(a.name),
        "list": _list_rows,
        "remove": lambda: _remove(asker, a.name),
        "capacity": lambda: _capacity_cmd(asker, a.name, a.five, a.seven),
    }
    return _respond(a.sub, a.json, handlers[a.sub])
