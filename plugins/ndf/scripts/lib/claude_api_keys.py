"""登録済み API キー: 登録簿と状態の記録の読み書き・選定・子の環境（#2855）。

サブスクリプションにリンクした Console 組織の API キーを名前を付けて預け、`supervise.py` の起動口（`ClaudeRunner`）が
起こす `claude -p` の worker にだけ使う。ラッパーが起こす対話のセッションの経路はこのモジュールを読まない（I5）。

置き場はアカウントの置き場（`claude_accounts.store_dir()`）。

- `api-keys.json`（API キーの登録簿。0600）: `version`・`use`（使い方の宣言 `off`・`always`・`near-limit`）・
  `keys.<名前>`（`key`・`account`・`added_at`・`generation`）。キーの値はこのファイルの外へ出さない（I4）
- `api-key-state.json`（API キーの状態。0600）: `<名前>`（`generation`・`state`・`since`・`checked_at`）。
  世代が登録簿と一致しない行と、行の無いキーは `ok` として扱う（I10）

書くときはアカウントの置き場の `.locks/.api-keys` の排他を取る。登録簿が壊れていれば `BookError` を投げ、読む側
（起動口・一覧）は登録 0 本・宣言 `off` として動く（I6）。
"""

from __future__ import annotations

import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

import claude_accounts as ca
import limits
from claude_usage import epoch, iso_utc

BOOK_FILE = "api-keys.json"
STATE_FILE = "api-key-state.json"
LOCK_NAME = ".api-keys"  # アカウントの名前（先頭は英小文字か数字）と重ならない
VERSION = 1
USES = ("off", "always", "near-limit")
OK, CREDIT_LOW, AUTH_FAILED = "ok", "credit_low", "auth_failed"
STATES = (OK, CREDIT_LOW, AUTH_FAILED)
KEY_ENV = "ANTHROPIC_API_KEY"
NAME_ENV = "NDF_CLAUDE_API_KEY_NAME"  # 子へ渡した API キーの名前（入れ子の supervise.py が読む）
GENERATION_ENV = "NDF_CLAUDE_API_KEY_GENERATION"  # 子へ渡した API キーの世代
CREDIT_HOLD = limits.seconds("claude-api-keys.credit-hold")


class BookError(Exception):
    """登録簿のファイルが読めない・形が違う。"""


class Refused(Exception):
    """登録を拒む。`reason` は `name_conflict`・`not_registered`・`account_taken`・`invalid_name`。"""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass
class ApiKey:
    name: str
    account: str
    added_at: str
    generation: str
    key: str = field(repr=False)  # 値。表示・記録の組み立てへ渡さない


@dataclass
class KeyState:
    state: str
    since: str | None = None
    checked_at: str | None = None

    def selectable(self, now: float) -> bool:
        """選定の候補に入るか（I7）。残高切れは保留の時間が過ぎたら入る。認証の失敗は入らない。"""
        if self.state == AUTH_FAILED:
            return False
        if self.state == CREDIT_LOW:
            since = epoch(self.since)
            return since is None or now - since >= CREDIT_HOLD
        return True


# ---------------------------------------------------------------- 置き場


def book_path() -> str:
    return os.path.join(ca.store_dir(), BOOK_FILE)


def state_path() -> str:
    return os.path.join(ca.store_dir(), STATE_FILE)


@contextmanager
def _locked():
    import locks  # 外部パッケージ（filelock）。読み込みのときには import しない

    d = os.path.join(ca.make_store(), ca.LOCKS_DIR)
    os.makedirs(d, mode=0o700, exist_ok=True)
    with locks.exclusive(os.path.join(d, LOCK_NAME), timeout=ca.LOCK_WAIT):
        yield


def lock_timeout() -> type[BaseException]:
    return ca.lock_timeout()


def _raw_book() -> dict:
    """登録簿の中身（無ければ空の形）。壊れていれば BookError。"""
    path = book_path()
    if not os.path.exists(path):
        return {"version": VERSION, "use": "off", "keys": {}}
    data = ca._read(path)
    if data is None or not isinstance(data.get("keys", {}), dict):
        raise BookError(f"API キーの登録簿が読めない（{path}）")
    data.setdefault("keys", {})
    return data


def _key_of(name: str, row) -> ApiKey | None:
    if not isinstance(row, dict):
        return None
    vals = [row.get(k) for k in ("account", "added_at", "generation", "key")]
    if not all(isinstance(v, str) and v for v in vals):
        return None
    return ApiKey(name, *vals)


def keys() -> list[ApiKey]:
    """登録済み API キー（名前の順）。壊れた行は飛ばす。登録簿が壊れていれば BookError。"""
    raw = _raw_book()["keys"]
    return [k for k in (_key_of(n, raw[n]) for n in sorted(raw)) if k is not None]


def names() -> list[str]:
    """登録済み API キーの名前。登録簿が壊れていれば空（OAuth の登録の衝突の検査が使う）。"""
    try:
        return [k.name for k in keys()]
    except BookError:
        return []


def get(name: str) -> ApiKey | None:
    try:
        return next((k for k in keys() if k.name == name), None)
    except BookError:
        return None


def problem() -> str | None:
    """登録簿が壊れていればその 1 行（起動口が最初の呼び出しの前に出す）。"""
    try:
        _raw_book()
    except BookError as e:
        return f"{e}。API キーを使わずに動く"
    return None


def use() -> str:
    """使い方の宣言。無い・読めない・値が違うときは `off`（I6）。"""
    try:
        v = _raw_book().get("use") or "off"
    except BookError:
        return "off"
    return v if v in USES else "off"


def set_use(value: str) -> None:
    if value not in USES:
        raise ValueError(value)
    with _locked():
        book = _raw_book()
        book["use"] = value
        ca._write(book_path(), book)


def add(name: str, key: str, account: str, now: float | None = None) -> bool:
    """確かめの済んだキーを登録する（同じ名前なら置き換える）。置き換えたら真。拒むときは Refused（I1・I2・I9）。"""
    if name == ca.METERED or name in ca.names():
        raise Refused("name_conflict", f"名前が登録済みアカウントか予約の名前と重なる: {name}")
    if not ca.valid_name(name):
        raise Refused("invalid_name", f"名前は英小文字・数字・- と _ の 32 字まで: {name}")
    if account not in ca.names():
        raise Refused("not_registered", f"登録済みアカウントでない: {account}")
    now = time.time() if now is None else now
    with _locked():
        book = _raw_book()
        taken = [k.name for k in keys() if k.account == account and k.name != name]
        if taken:
            raise Refused("account_taken", f"アカウント {account} には API キー {taken[0]} が結び付いている（1 アカウントに 1 本）")
        replaced = name in book["keys"]
        gen = str(uuid.uuid4())
        book.setdefault("version", VERSION)
        book.setdefault("use", "off")
        book["keys"][name] = {"key": key, "account": account, "added_at": iso_utc(now), "generation": gen}
        ca._write(book_path(), book)
        _write_state(name, gen, OK, now, checked=True)
    return replaced


def remove(name: str) -> bool:
    """登録を外し、状態の行も消す。登録が無ければ偽。"""
    with _locked():
        book = _raw_book()
        if name not in book["keys"]:
            return False
        del book["keys"][name]
        ca._write(book_path(), book)
        rows = _raw_state()
        if rows.pop(name, None) is not None:
            ca._write(state_path(), rows)
    return True


# ---------------------------------------------------------------- 状態


def _raw_state() -> dict:
    return ca._read(state_path()) or {}


def _write_state(name: str, generation: str, state: str, now: float, checked: bool = False) -> None:
    """排他の中で呼ぶ。同じ状態なら `since` を変えない。"""
    rows = _raw_state()
    old = rows.get(name) if isinstance(rows.get(name), dict) else {}
    same = old.get("generation") == generation and old.get("state") == state
    row = {"generation": generation, "state": state, "since": old.get("since") if same else iso_utc(now)}
    row["checked_at"] = iso_utc(now) if checked else (old.get("checked_at") if old.get("generation") == generation else None)
    rows[name] = row
    ca._write(state_path(), rows)


def state_of(k: ApiKey, rows: dict | None = None) -> KeyState:
    """キーの今の状態。行が無い・世代が違う・形が違うときは `ok`（I10）。"""
    rows = _raw_state() if rows is None else rows
    row = rows.get(k.name)
    if not isinstance(row, dict) or row.get("generation") != k.generation or row.get("state") not in STATES:
        return KeyState(OK)
    return KeyState(row["state"], row.get("since"), row.get("checked_at"))


def note(name: str, generation: str, state: str, now: float | None = None, checked: bool = False) -> bool:
    """状態を書く。起動の時に選んだ世代が登録簿の今の世代と一致するときだけ書き、書いたら真（I10）。"""
    now = time.time() if now is None else now
    try:
        with _locked():
            k = next((x for x in keys() if x.name == name), None)
            if k is None or k.generation != generation:
                return False
            _write_state(name, generation, state, now, checked)
    except (BookError, OSError, lock_timeout()):
        return False
    return True


def note_success(k: ApiKey, now: float | None = None) -> None:
    """成功した起動の後、記録が `ok` でなければ `ok` へ戻す（保留が過ぎた残高切れのキーが通ったとき）。"""
    try:
        if state_of(k).state != OK:
            note(k.name, k.generation, OK, now)
    except OSError:
        pass


# ---------------------------------------------------------------- 選定と子の環境


def pick(exclude=(), now: float | None = None) -> ApiKey | None:
    """使えるキーのうち名前の順の先頭（I7・I8）。`exclude` の名前は除く。登録簿が壊れていれば None。"""
    now = time.time() if now is None else now
    try:
        cands = [k for k in keys() if k.name not in exclude]
    except BookError:
        return None
    rows = _raw_state()
    return next((k for k in cands if state_of(k, rows).selectable(now)), None)


def child_env(k: ApiKey, base: dict) -> dict:
    """`base` から認証の変数と従量の接続の宣言のキーを外し、親のアカウントの環境を元へ戻して、選んだ 1 本を足す。"""
    env = dict(base)
    declared = ca.fallback_env(env)
    for name in (*ca.STRIPPED_AUTH_ENV, *declared, NAME_ENV, GENERATION_ENV):
        env.pop(name, None)
    ca.leave_account(env)
    env[KEY_ENV] = k.key
    env[NAME_ENV] = k.name
    env[GENERATION_ENV] = k.generation
    return env


def without_key(base: dict) -> dict:
    """`base` から API キーの変数を外した環境（入れ子の起動で、受け継いだキーを使えなくなったとき）。"""
    env = dict(base)
    for name in (KEY_ENV, NAME_ENV, GENERATION_ENV):
        env.pop(name, None)
    return env


def inherited(environ=None) -> tuple[str, str] | None:
    """起動したときの環境が持つ API キーの (名前, 世代)。値と名前と世代がそろわなければ None。"""
    environ = os.environ if environ is None else environ
    name, gen = environ.get(NAME_ENV), environ.get(GENERATION_ENV)
    return (name, gen) if name and gen and environ.get(KEY_ENV) else None


def rows() -> list[dict]:
    """`account list` の行（値を除く）。登録簿が壊れていれば BookError。"""
    states = _raw_state()
    out = []
    for k in keys():
        s = state_of(k, states)
        out.append({"name": k.name, "kind": "api_key", "account": k.account, "state": s.state, "since": s.since})
    return out
