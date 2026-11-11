"""`relay.py account` の API キーの副命令: `add-api-key`・`api-key-monthly`・`api-key-use` と、`list`・`remove`・`check` の
API キーの分岐（#2855・#2930）。

置き場を書くのは `lib/claude_api_keys.py`、確かめの要求は `lib/claude_usage.py` の `check_api_key` である。ここは
キーの値の受け取り（標準入力かファイル。引数では受けない。決定 1）と、結果の文の組み立てを持つ。出力の組み立てには
値を渡さない（I4）。
"""

from __future__ import annotations

import getpass
import sys

from .ask import Asker, Fail, MissingArgs
from .common import PKG_ROOT  # noqa: F401  lib/ を sys.path に置く

import claude_accounts as ca  # noqa: E402,I001
import claude_api_keys as ak  # noqa: E402
from claude_usage import KeyCheck, check_api_key  # noqa: E402

ARG_ERROR = (
    "引数を読めない（引数は表示しない）。usage: relay.py account add-api-key <名前> --account <アカウント> [--key-file <パス>]"
    " [--monthly <ドル>]（キーの値は標準入力か --key-file で渡す）"
)
MONTHLY_ERROR = "月の額は正の数（ドル）で渡す（入力値は表示しない）"
PURCHASED_NOTE = (
    "組織に買ったクレジットか自動チャージがあると、月の API クレジットを使い切った後もそちらから払われる"
    "（NDF は検出しない。Console の spend limit で抑える）"
)


def _check_text(c: KeyCheck) -> str:
    """確かめの失敗の文。応答の `message` は値を含みうるので出さず、状態コードと型だけを出す。"""
    return {
        "auth_failed": "認証が通らない（キーが違うか失効している）",
        "credit_low": "残高切れ（credit balance is too low）",
        "network": "通信に失敗した",
    }.get(c.result, f"確かめに失敗した（HTTP {c.status}・{c.error_type or '-'}）")


def _verify(key: str) -> KeyCheck:
    c = check_api_key(key)
    if c.result != "ok":
        raise Fail(c.result, f"{_check_text(c)}。保存しない", status=c.status, error_type=c.error_type)
    return c


def _read_key(asker: Asker, key_file: str | None) -> str:
    """キーの値を `--key-file`・端末なら表示しない入力・端末でなければ標準入力の 1 行目 から読む。空なら MissingArgs。"""
    if key_file:
        try:
            with open(key_file, encoding="utf-8") as f:
                value = f.readline().strip()
        except OSError as e:
            raise Fail("missing_args", f"--key-file を読めない（{e.strerror}）", 2) from e
    elif asker.interactive:
        value = getpass.getpass("API キーの値（表示しない）: ", stream=sys.stderr).strip()
    else:
        value = sys.stdin.readline().strip()
    if not value:
        raise MissingArgs(["<キーの値>（標準入力か --key-file）"])
    return value


def _amount(v: str | None, allow_zero: bool = False) -> float | None:
    """月の額の引数（正の数。`allow_zero` なら 0 は宣言を外す None）。読めなければ終了コード 2（値は表示しない）。"""
    if v is None:
        return None
    try:
        x = float(v)
    except ValueError:
        x = -1.0
    if allow_zero and x == 0:
        return None
    if not (x > 0 and x != float("inf")):
        raise Fail("invalid_name", MONTHLY_ERROR, 2)
    return x


def key_add(asker: Asker, name: str | None, account: str | None, key_file: str | None, monthly: str | None = None) -> dict:
    """API キーを確かめてから登録する（F1）。確かめの失敗・衝突・書けないときは保存しない（I1〜I3・I9）。

    `monthly` は月の額の宣言（ドル。#2930）。省けば未宣言で、near-limit では選ばれない。"""
    usd = _amount(monthly)
    name = asker.value("<名前>", "API キーの名前", name)
    account = asker.value("--account", "結び付ける登録済みアカウント", account, ca.names())
    _precheck(name, account)
    key = _read_key(asker, key_file)
    _verify(key)
    try:
        replaced = ak.add_key(name, key, account, monthly_usd=usd)
    except ak.Refused as e:
        raise Fail(e.reason, e.message, 2 if e.reason == "invalid_name" else 1) from e
    except (ak.BookError, ak.book_lock_timeout(), OSError) as e:
        raise Fail("write_failed", f"置き場へ書けない（{e}）") from e
    print(PURCHASED_NOTE, file=sys.stderr)
    return {"name": name, "account": account, "replaced": replaced, "text": f"登録した: {name}（アカウント {account}）"}


def _precheck(name: str, account: str) -> None:
    """要求を打つ前に分かる拒みの理由を先に返す（保存の直前にも `ak.add_key` が同じ規則で確かめる）。"""
    if name == ca.METERED or name in ca.names():
        raise Fail("name_conflict", f"名前が登録済みアカウントか予約の名前と重なる: {name}")
    if not ca.valid_name(name):
        # 入力値を写さない（キーを誤って名前の位置へ渡したとき、値が標準エラーと --json へ出るため）
        raise Fail("invalid_name", "名前は英小文字・数字・- と _ の 32 字まで（入力値は表示しない）", 2)
    if account not in ca.names():
        raise Fail("not_registered", f"登録済みアカウントでない: {account}")
    try:
        taken = [k.name for k in ak.keys() if k.account == account and k.name != name]
    except ak.BookError as e:
        raise Fail("write_failed", f"{e}。直すか消してから登録する") from e
    if taken:
        raise Fail("account_taken", f"アカウント {account} には API キー {taken[0]} が結び付いている（1 アカウントに 1 本）")


def key_monthly(name: str | None, value: str | None) -> dict:
    """登録済みのキーへ月の額を宣言し直す（`0` で宣言を外す）。値と世代は変えない（#2930）。"""
    if not name or value is None:
        raise MissingArgs([x for x, v in (("<名前>", name), ("<ドル>", value)) if v is None or not v])
    usd = _amount(value, allow_zero=True)
    try:
        found = ak.set_monthly(name, usd)
    except (ak.BookError, ak.book_lock_timeout(), OSError) as e:
        raise Fail("write_failed", f"置き場へ書けない（{e}）") from e
    if not found:
        raise Fail("not_registered", f"登録されていない: {name}")
    text = f"月の額を外した: {name}" if usd is None else f"月の額: {name} {usd:g} ドル（週の利用枠 {usd * 7 / 30:.2f} ドル）"
    return {"name": name, "monthly_usd": usd, "text": text}


def _weekly_at(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except ValueError:
        x = 0.0
    if not 1 <= x <= 100:
        raise Fail("invalid_name", "--weekly-at は 1 以上 100 以下の百分率", 2)
    return x


def key_use(value: str | None, weekly_at: str | None = None) -> dict:
    """使い方の宣言と閾値 T（`--weekly-at`）を読む・変える（F4）。T を省けば今の値を保つ。"""
    t = _weekly_at(weekly_at)
    if value is None and t is None:
        now, cur = ak.use_setting(), ak.weekly_at_setting()
        return {"use": now, "weekly_at": cur, "text": f"API キーの使い方: {now}（閾値 T {cur:g}%）"}
    value = ak.use_setting() if value is None else value
    if value not in ak.USES:
        raise Fail("invalid_name", f"使い方は {' / '.join(ak.USES)} のどれか: {value}", 2)
    try:
        ak.set_use(value, t)
    except (ak.BookError, ak.book_lock_timeout(), OSError) as e:
        raise Fail("write_failed", f"置き場へ書けない（{e}）") from e
    cur = ak.weekly_at_setting()
    return {"use": value, "weekly_at": cur, "text": f"API キーの使い方: {value}（閾値 T {cur:g}%）"}


def is_key(name: str | None) -> bool:
    return bool(name) and name in ak.key_names()


def key_check(name: str) -> dict:
    """登録済み API キーに 1 回要求を打つ（F3）。状態は記録しない（#2930 の決定 2）。"""
    k = ak.get(name)
    if k is None:
        raise Fail("not_registered", f"登録されていない: {name}")
    _verify(k.key)
    return {"name": name, "kind": "api_key", "text": f"使える: {name}"}


def key_remove(name: str) -> dict:
    try:
        ak.remove_key(name)
    except (ak.BookError, ak.book_lock_timeout(), OSError) as e:
        raise Fail("write_failed", f"置き場へ書けない（{e}）") from e
    return {"name": name, "kind": "api_key", "text": f"外した: {name}（API キー）"}


def list_rows() -> list[dict]:
    """一覧の API キーの行（値を除く）。登録簿が壊れていれば 1 行を標準エラーへ出して行を足さない。"""
    try:
        return ak.key_rows()
    except ak.BookError as e:
        print(f"{e}（API キーを使わずに動く）", file=sys.stderr)
        return []


def _usd(v: float | None) -> str:
    return "不明" if v is None else f"{v:,.2f}"


def key_state(r: dict) -> str:
    """API キーの行の状態の語（near-limit で選ばれうるか）。"""
    if r["monthly_usd"] is None:
        return "月の額が未宣言（near-limit で選ばない）"
    if r["ratio"] is None:
        return "帳簿を読めない"
    return "利用枠を使い切った" if r["ratio"] >= 1 else "使える"


def table_row(r: dict, width: int) -> tuple[str, ...]:
    """表の 1 行: 識別の列に結び付いたアカウント、枠の大きさの列に月の額、残りの列に直近 7 日の使用額 / 週の利用枠（割合）。"""
    monthly = "月 未宣言" if r["monthly_usd"] is None else f"月 {r['monthly_usd']:,.2f}"
    ratio = "" if r["ratio"] is None else f"（{r['ratio'] * 100:.0f}%）"
    used = f"7日 {_usd(r['spent_7d'])} / 週 {_usd(r['weekly_budget']) if r['weekly_budget'] else '-'}{ratio}"
    return (r["name"], r["account"], *("-",) * (width - 7), monthly, used, "-", "-", key_state(r))
