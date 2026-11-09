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
import sys
import time
import urllib.parse
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

import claude_accounts as ca
import claude_settings
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
# キーで起動した子が、キーを外したときに戻る登録済みアカウントの名前（キーの環境は親のアカウントを外すため、名前だけを残す）
BACK_ENV = "NDF_CLAUDE_KEY_ACCOUNT"
BASE_URL_ENV = "ANTHROPIC_BASE_URL"  # claude の送り先。Anthropic の外を指すなら登録済みのキーを使わない
ANTHROPIC_HOST = "api.anthropic.com"
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


def key_state_path() -> str:
    return os.path.join(ca.store_dir(), STATE_FILE)


@contextmanager
def _book_locked():
    import locks  # 外部パッケージ（filelock）。読み込みのときには import しない

    d = os.path.join(ca.make_store(), ca.LOCKS_DIR)
    os.makedirs(d, mode=0o700, exist_ok=True)
    with locks.exclusive(os.path.join(d, LOCK_NAME), timeout=ca.LOCK_WAIT):
        yield


def book_lock_timeout() -> type[BaseException]:
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


def _api_key_of(name: str, row) -> ApiKey | None:
    if not isinstance(row, dict):
        return None
    vals = [row.get(k) for k in ("account", "added_at", "generation", "key")]
    if not all(isinstance(v, str) and v for v in vals):
        return None
    return ApiKey(name, *vals)


def keys() -> list[ApiKey]:
    """登録済み API キー（名前の順）。壊れた行は飛ばす。登録簿が壊れていれば BookError。"""
    raw = _raw_book()["keys"]
    return [k for k in (_api_key_of(n, raw[n]) for n in sorted(raw)) if k is not None]


def key_names() -> list[str]:
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


def use_setting() -> str:
    """使い方の宣言。無い・読めない・値が違うときは `off`（I6）。"""
    try:
        v = _raw_book().get("use") or "off"
    except BookError:
        return "off"
    return v if v in USES else "off"


def set_use(value: str) -> None:
    if value not in USES:
        raise ValueError(value)
    with _book_locked():
        book = _raw_book()
        book["use"] = value
        ca._write(book_path(), book)


def add_key(name: str, key: str, account: str, now: float | None = None) -> bool:
    """確かめの済んだキーを登録する（同じ名前なら置き換える）。置き換えたら真。拒むときは Refused（I1・I2・I9）。"""
    if name == ca.METERED or name in ca.names():
        raise Refused("name_conflict", f"名前が登録済みアカウントか予約の名前と重なる: {name}")
    if not ca.valid_name(name):
        raise Refused("invalid_name", f"名前は英小文字・数字・- と _ の 32 字まで: {name}")
    if account not in ca.names():
        raise Refused("not_registered", f"登録済みアカウントでない: {account}")
    now = time.time() if now is None else now
    with _book_locked():
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
        _write_key_state(name, gen, OK, now, checked=True)
    return replaced


def remove_key(name: str) -> bool:
    """登録を外し、状態の行も消す。登録が無ければ偽。"""
    with _book_locked():
        book = _raw_book()
        if name not in book["keys"]:
            return False
        del book["keys"][name]
        ca._write(book_path(), book)
        rows = _raw_state()
        if rows.pop(name, None) is not None:
            ca._write(key_state_path(), rows)
    return True


# ---------------------------------------------------------------- 状態


def _raw_state() -> dict:
    return ca._read(key_state_path()) or {}


def _write_key_state(name: str, generation: str, state: str, now: float, checked: bool = False) -> None:
    """排他の中で呼ぶ。同じ状態なら `since` を変えない。

    残高切れは観測し直すたびに `since` を今へ進める。保留を最後の観測から数え、保留が過ぎた後に試して尽きたままなら
    また保留の間は選ばないためである（「過ぎたら 1 回だけ試す」。#2855 の決定 7）。"""
    rows = _raw_state()
    old = rows.get(name) if isinstance(rows.get(name), dict) else {}
    same = old.get("generation") == generation and old.get("state") == state and state != CREDIT_LOW
    row = {"generation": generation, "state": state, "since": old.get("since") if same else iso_utc(now)}
    row["checked_at"] = iso_utc(now) if checked else (old.get("checked_at") if old.get("generation") == generation else None)
    rows[name] = row
    ca._write(key_state_path(), rows)


def state_of(k: ApiKey, rows: dict | None = None) -> KeyState:
    """キーの今の状態。行が無い・世代が違う・形が違うときは `ok`（I10）。"""
    rows = _raw_state() if rows is None else rows
    row = rows.get(k.name)
    if not isinstance(row, dict) or row.get("generation") != k.generation or row.get("state") not in STATES:
        return KeyState(OK)
    return KeyState(row["state"], row.get("since"), row.get("checked_at"))


def note_state(name: str, generation: str, state: str, now: float | None = None, checked: bool = False) -> bool:
    """状態を書く。起動の時に選んだ世代が登録簿の今の世代と一致するときだけ書き、書いたら真（I10）。"""
    now = time.time() if now is None else now
    try:
        with _book_locked():
            k = next((x for x in keys() if x.name == name), None)
            if k is None or k.generation != generation:
                return False
            _write_key_state(name, generation, state, now, checked)
    except (BookError, OSError, book_lock_timeout()):
        return False
    return True


def note_success(k: ApiKey, now: float | None = None) -> None:
    """成功した起動の後、記録が `ok` でなければ `ok` へ戻す（保留が過ぎた残高切れのキーが通ったとき）。"""
    try:
        if state_of(k).state != OK:
            note_state(k.name, k.generation, OK, now)
    except OSError:
        pass


# ---------------------------------------------------------------- 選定と子の環境


def pick_key(exclude=(), now: float | None = None) -> ApiKey | None:
    """使えるキーのうち名前の順の先頭（I7・I8）。`exclude` の名前は除く。登録簿が壊れていれば None。"""
    now = time.time() if now is None else now
    try:
        cands = [k for k in keys() if k.name not in exclude]
    except BookError:
        return None
    rows = _raw_state()
    return next((k for k in cands if state_of(k, rows).selectable(now)), None)


def _key_free_env(base: dict) -> dict:
    """`base` から認証の変数・従量の接続の宣言のキー・API キーの名前と世代を外し、親のアカウントの環境を元へ戻した写し。"""
    env = dict(base)
    declared = ca.fallback_env(env)
    for name in (*ca.STRIPPED_AUTH_ENV, *declared, NAME_ENV, GENERATION_ENV, BACK_ENV):
        env.pop(name, None)
    ca.leave_account(env)
    return env


def _is_anthropic(url: str) -> bool:
    try:
        p = urllib.parse.urlsplit(url)
        return p.scheme == "https" and p.hostname == ANTHROPIC_HOST and p.port in (None, 443)
    except ValueError:
        return False


def sends_to_anthropic(base: dict, cwd: str | None = None) -> bool:
    """`base` から組む子の環境で、`cwd` で起動した claude のキーの送り先が Anthropic（`https://api.anthropic.com`）か。

    継承した `ANTHROPIC_BASE_URL` か、子が読む設定のファイルの `env` の `ANTHROPIC_BASE_URL`（起動の環境より優先される）の
    どれかが別のホストを指すなら偽。登録の確かめで Anthropic へだけ送ったキーを、ゲートウェイなど別のサービスへ送らないため、
    そのときはキーを使わず今の認証で起動する。どの設定が勝つかは決めず、どれか 1 つでも外を指せば使わない。"""
    env = _key_free_env(base)
    urls = [u for u in (env.get(BASE_URL_ENV), *claude_settings.env_values(BASE_URL_ENV, env, cwd)) if u]
    return all(_is_anthropic(u) for u in urls)


def env_with_key(k: ApiKey, base: dict, account: str | None = None) -> dict:
    """`base` から認証の変数と従量の接続の宣言のキーを外し、親のアカウントの環境を元へ戻して、選んだ 1 本を足す。

    キーを外したときに戻るアカウント（`account`。無ければ `base` の起動したときのアカウントか、`base` が受け継いだ戻り先）の
    名前を `BACK_ENV` に残す。従量の接続へは戻さない。"""
    back = account if account is not None else (base.get(ca.NAME_ENV) or base.get(BACK_ENV))
    env = _key_free_env(base)
    env[KEY_ENV] = k.key
    env[NAME_ENV] = k.name
    env[GENERATION_ENV] = k.generation
    if back and back != ca.METERED:
        env[BACK_ENV] = back
    return env


def without_key(base: dict) -> dict:
    """`base` から API キーの変数と戻り先の名前を外した環境（入れ子の起動で、受け継いだキーを使えなくなったとき）。"""
    env = dict(base)
    for name in (KEY_ENV, NAME_ENV, GENERATION_ENV, BACK_ENV):
        env.pop(name, None)
    return env


def back_account(environ=None) -> str | None:
    """受け継いだキーを外したときに戻る登録済みアカウントの名前（`BACK_ENV`）。無ければ None。"""
    environ = os.environ if environ is None else environ
    return environ.get(BACK_ENV) or None


def inherited(environ=None) -> tuple[str, str] | None:
    """起動したときの環境が持つ API キーの (名前, 世代)。値と名前と世代がそろわなければ None。"""
    environ = os.environ if environ is None else environ
    name, gen = environ.get(NAME_ENV), environ.get(GENERATION_ENV)
    return (name, gen) if name and gen and environ.get(KEY_ENV) else None


def key_rows() -> list[dict]:
    """`account list` の行（値を除く）。登録簿が壊れていれば BookError。"""
    states = _raw_state()
    out = []
    for k in keys():
        s = state_of(k, states)
        out.append({"name": k.name, "kind": "api_key", "account": k.account, "state": s.state, "since": s.since})
    return out


# ---------------------------------------------------------------- 起動口の持ち方


def near_limit(keep=()) -> bool:
    """登録済みアカウントの選定が候補を返さないか、選んだ候補の使用率が切り替えの閾値以上か（near-limit の条件 1・2。決定 9）。"""
    if ca.registered() == 0:
        return False
    c = ca.choose(keep=keep)
    return c.name is None or (c.score is not None and c.score >= ca.switch_at())


class KeyRoute:
    """`supervise.py` の起動口（`ClaudeRunner`）の 1 プロセスの API キーの持ち方。

    `state` はプランの状態（`cur`・`switched`・`progress_write`）。`key` は直前の起動の API キーの名前（無ければ None）。
    `inherited` は起動したときの環境が持つキー（入れ子の起動）で、使えなくなったら `dropped` にして以後の子から外す。
    `back` はそのとき戻る登録済みアカウント。`account()` は呼び手の今のアカウント（キーの子の戻り先に残す）。"""

    def __init__(self, state, environ=None, account=None):
        self.state = state
        self.key: str | None = None
        self.account = account or (lambda: None)
        environ = os.environ if environ is None else environ
        got = inherited(environ)
        self.inherited = ApiKey(got[0], "", "", got[1], environ[KEY_ENV]) if got else None
        self.back = back_account(environ) if got else None
        self.dropped = False
        self.told = False

    def use(self) -> str:
        """使い方の宣言。登録簿が壊れていれば最初の 1 回だけ標準エラーと進捗ログへ 1 行出し、`off` として動く（I6）。"""
        if not self.told:
            self.told = True
            bad = problem()
            if bad:
                print("supervise: " + bad, file=sys.stderr)
                self.state.progress_write({"kind": "api_keys_invalid", "detail": bad})
        return use_setting()

    def attempt(self, keep, cwd: str | None = None) -> KeyTry:
        """1 回の呼び出しの API キーの試し方。`keep()` はトークンを更新しないアカウントの名前（near-limit の判定へ渡す）。
        `cwd` は子の claude を起動する場所（子が読むプロジェクトの設定の送り先を確かめる）。"""
        return KeyTry(self, self.use(), keep, cwd)

    def note_switch(self, k: ApiKey | None) -> None:
        """使うキーが替わったら、プランの状態（`auth`・`switched`）と進捗ログの `account` の行に残す（E3・E7）。"""
        name = k.name if k is not None else None
        if name == self.key:
            return
        st, (before, self.key) = self.state, (self.key, name)
        if name is not None:
            st.cur["auth"] = f"API キー {name}"
            if st.cur["auth"] not in st.switched:
                st.switched.append(st.cur["auth"])
        st.progress_write({"kind": "account", "step": st.cur.get("id"), "reason": "api_key", "from_key": before, "to_key": name})

    def result(self, k: ApiKey, res: dict, exclude: set) -> bool:
        """キーで起動した結果を読む。残高切れ・認証の失敗・上限ならキーを外して真（同じ呼び出しを起動し直す）。

        残高切れと認証の失敗は状態に記録する（起動の時の世代が今の登録と一致するときだけ。I10）。上限と 429 は
        この呼び出しでだけ外す（決定 6）。受け継いだキーは使えなくなったら以後の子から外す。"""
        if res.get("ok"):
            if k is not self.inherited:
                note_success(k)
            return False
        failure = CREDIT_LOW if res.get("credit") else AUTH_FAILED if res.get("auth") else None
        if failure is None and not res.get("limit"):
            return False
        exclude.add(k.name)
        if self.inherited is not None and k.name == self.inherited.name:
            # 登録簿から選んだ同じ名前のキー（宣言が always の入れ子）も、受け継いだキーと同じものとして以後の子から外す
            self.dropped = True
        if failure is not None:
            reason = failure if note_state(k.name, k.generation, failure) else "stale_generation"
            self.state.progress_write({"kind": "api_key", "step": self.state.cur.get("id"), "name": k.name, "reason": reason})
        return True


class KeyTry:
    """1 回の呼び出しの中の API キーの試し方（外したキーと、上限に当たって替えるアカウントが無かったか）。"""

    def __init__(self, route: KeyRoute, how: str, keep, cwd: str | None = None):
        self.route, self.how, self.keep, self.cwd = route, how, keep, cwd
        self.tried: set[str] = set()
        self.limit_hit = False  # near-limit の条件 3

    def pick(self) -> ApiKey | None:
        """この起動に使うキー。登録済みのキーが選ばれなければ、受け継いだキーを外していない限りそれを使う。"""
        r = self.route
        if not (self.how == "off" or (self.how == "near-limit" and not (self.limit_hit or near_limit(self.keep())))):
            if sends_to_anthropic(dict(os.environ), self.cwd) and (k := pick_key(exclude=self.tried)) is not None:
                return k
        return r.inherited if r.inherited and not r.dropped and r.inherited.name not in self.tried else None

    def run(self, call):
        """キーで起動する（`call(環境)`）。キーで決着したらその結果、使うキーが無くなったら None を返す。"""
        while (k := self.pick()) is not None:
            self.route.note_switch(k)
            res = call(env_with_key(k, dict(os.environ), self.route.account()) if k is not self.route.inherited else None)
            if not self.route.result(k, res, self.tried):
                return res
        self.route.note_switch(None)
        return None

    def env(self, child: dict | None) -> dict | None:
        """キーを使わない起動の環境。受け継いだキーを外したなら、キーの変数を外す。

        受け継いだキーの環境は親のアカウントを外している（`env_with_key`）。起動したときの環境のままでは、ログインの無い
        共有の設定ディレクトリで起動しうるため、親が残した戻り先（`back`）のアカウントの環境へ戻す。登録が 2 つ以上なら
        呼び手（`ClaudeRunner`）が先に戻り先へ替えるため、ここへ来るのは登録が 1 つ以下のときである。"""
        if not self.route.dropped:
            return child
        if child is not None:
            return without_key(child)
        base = without_key(dict(os.environ))
        back = self.route.back
        return (ca.account_env(back, base) if back else None) or base

    def left(self) -> bool:
        """上限に当たって替えるアカウントが無いとき、試せるキーが残っていれば真（以後 near-limit でもキーを使う）。"""
        if self.how == "off" or not sends_to_anthropic(dict(os.environ), self.cwd) or pick_key(exclude=self.tried) is None:
            return False
        self.limit_hit = True
        return True

    def reset(self) -> None:
        """解除を待った後は、上限で外したキーを選び直せる。"""
        self.tried.clear()
        self.limit_hit = False
