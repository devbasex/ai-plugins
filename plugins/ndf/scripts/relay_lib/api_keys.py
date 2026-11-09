"""`relay.py account` の API キーの副命令: `add-api-key`・`api-key-use` と、`list`・`remove`・`check` の API キーの分岐（#2855）。

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

STATE_LABEL = {ak.OK: "使える", ak.CREDIT_LOW: "残高切れ", ak.AUTH_FAILED: "認証の失敗"}
ARG_ERROR = (
    "引数を読めない（引数は表示しない）。usage: relay.py account add-api-key <名前> --account <アカウント> [--key-file <パス>]"
    "（キーの値は標準入力か --key-file で渡す）"
)
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


def key_add(asker: Asker, name: str | None, account: str | None, key_file: str | None) -> dict:
    """API キーを確かめてから登録する（F1）。確かめの失敗・衝突・書けないときは保存しない（I1〜I3・I9）。"""
    name = asker.value("<名前>", "API キーの名前", name)
    account = asker.value("--account", "結び付ける登録済みアカウント", account, ca.names())
    _precheck(name, account)
    key = _read_key(asker, key_file)
    _verify(key)
    try:
        replaced = ak.add_key(name, key, account)
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


def key_use(value: str | None) -> dict:
    """使い方の宣言を読む・変える（F4）。"""
    if value is None:
        now = ak.use_setting()
        return {"use": now, "text": f"API キーの使い方: {now}"}
    if value not in ak.USES:
        raise Fail("invalid_name", f"使い方は {' / '.join(ak.USES)} のどれか: {value}", 2)
    try:
        ak.set_use(value)
    except (ak.BookError, ak.book_lock_timeout(), OSError) as e:
        raise Fail("write_failed", f"置き場へ書けない（{e}）") from e
    return {"use": value, "text": f"API キーの使い方: {value}"}


def is_key(name: str | None) -> bool:
    return bool(name) and name in ak.key_names()


def key_check(name: str) -> dict:
    """登録済み API キーに 1 回要求を打ち、成功なら状態を `ok` へ戻す（F3）。失敗なら状態を変えない。"""
    k = ak.get(name)
    if k is None:
        raise Fail("not_registered", f"登録されていない: {name}")
    _verify(k.key)
    ak.note_state(k.name, k.generation, ak.OK, checked=True)
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


def table_row(r: dict, width: int) -> tuple[str, ...]:
    """表の 1 行（識別の列に結び付いたアカウント、状態の列に状態の語）。`width` は表の列の数。"""
    return (r["name"], r["account"], *("-",) * (width - 3), STATE_LABEL.get(r["state"], r["state"]))
