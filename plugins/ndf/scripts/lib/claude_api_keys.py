"""登録済み API キー: 登録簿の読み書き・子の環境・起動口の持ち方（#2855・#2930）。

サブスクリプションにリンクした Console 組織の API キーを名前を付けて預け、`supervise.py` の起動口（`ClaudeRunner`）が
起こす `claude -p` の worker にだけ使う。ラッパーが起こす対話のセッションの経路はこのモジュールを読まない（I5）。

置き場はアカウントの置き場（`claude_accounts.store_dir()`）。

- `api-keys.json`（API キーの登録簿。0600）: `version`・`use`（使い方の宣言 `off`・`always`・`near-limit`）・
  `weekly_at`（閾値 T。%。無ければ既定の 85）・`keys.<名前>`（`key`・`account`・`added_at`・`generation`・任意の
  `monthly_usd`（月の額。無ければ未宣言））。キーの値はこのファイルの外へ出さない（I4）

どのキーで起動するかは起動先の決定（`launch_rules`）が決める。判定のための状態のファイルを持たない（#2930 の I8）。
残高切れ・認証の失敗・上限を返したキーは、その呼び出しの除外へ足すだけである。旧版が書いた `api-key-state.json` は
読まず、書かず、消さない。

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
import launch_observe
import launch_rules
from claude_usage import iso_utc

BOOK_FILE = "api-keys.json"
LOCK_NAME = ".api-keys"  # アカウントの名前（先頭は英小文字か数字）と重ならない
VERSION = 1
USES = ("off", "always", "near-limit")
CREDIT_LOW, AUTH_FAILED, LIMITED = "credit_low", "auth_failed", "limit"  # この呼び出しでキーを外した理由
KEY_ENV = "ANTHROPIC_API_KEY"
NAME_ENV = "NDF_CLAUDE_API_KEY_NAME"  # 子へ渡した API キーの名前（入れ子の supervise.py が読む）
GENERATION_ENV = "NDF_CLAUDE_API_KEY_GENERATION"  # 子へ渡した API キーの世代
# キーで起動した子が、キーを外したときに戻る登録済みアカウントの名前（キーの環境は親のアカウントを外すため、名前だけを残す）
BACK_ENV = "NDF_CLAUDE_KEY_ACCOUNT"
BASE_URL_ENV = "ANTHROPIC_BASE_URL"  # claude の送り先。Anthropic の外を指すなら登録済みのキーを使わない
ANTHROPIC_HOST = "api.anthropic.com"


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
    monthly_usd: float | None = None  # 月の額の宣言（ドル。無ければ未宣言で near-limit で選ばない）


# ---------------------------------------------------------------- 置き場


def book_path() -> str:
    return os.path.join(ca.store_dir(), BOOK_FILE)


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


def positive(v) -> float | None:
    """正の数なら float、ほかは None（真偽値は数に数えない）。"""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def _api_key_of(name: str, row) -> ApiKey | None:
    if not isinstance(row, dict):
        return None
    vals = [row.get(k) for k in ("account", "added_at", "generation", "key")]
    if not all(isinstance(v, str) and v for v in vals):
        return None
    return ApiKey(name, *vals, monthly_usd=positive(row.get("monthly_usd")))


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


def weekly_at_setting() -> float:
    """閾値 T（%）。無い・読めない・範囲（1 以上 100 以下）の外なら既定の 85（#2930 の決定 10）。"""
    try:
        v = positive(_raw_book().get("weekly_at"))
    except BookError:
        v = None
    return v if v is not None and v <= 100 else launch_rules.WEEKLY_AT_DEFAULT


def set_use(value: str, weekly_at: float | None = None) -> None:
    """使い方の宣言を書く。`weekly_at`（閾値 T）を渡せば一緒に書き、省けば今の値を保つ。"""
    if value not in USES:
        raise ValueError(value)
    with _book_locked():
        book = _raw_book()
        book["use"] = value
        if weekly_at is not None:
            book["weekly_at"] = weekly_at
        ca._write(book_path(), book)


def set_monthly(name: str, monthly_usd: float | None) -> bool:
    """登録済みのキーの月の額を書く（None で宣言を外す）。世代と値は変えない。登録が無ければ偽。"""
    with _book_locked():
        book = _raw_book()
        row = book["keys"].get(name)
        if not isinstance(row, dict):
            return False
        if monthly_usd is None:
            row.pop("monthly_usd", None)
        else:
            row["monthly_usd"] = monthly_usd
        ca._write(book_path(), book)
    return True


def add_key(name: str, key: str, account: str, now: float | None = None, monthly_usd: float | None = None) -> bool:
    """確かめの済んだキーを登録する（同じ名前なら置き換える）。置き換えたら真。拒むときは Refused（I1・I2・I9）。"""
    if name == ca.METERED or name in ca.names():
        raise Refused("name_conflict", f"名前が登録済みアカウントか予約の名前と重なる: {name}")
    if not ca.valid_name(name):
        raise Refused("invalid_name", "名前は英小文字・数字・- と _ の 32 字まで（入力値は表示しない）")
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
        row = {"key": key, "account": account, "added_at": iso_utc(now), "generation": gen}
        if monthly_usd is not None:
            row["monthly_usd"] = monthly_usd
        book["keys"][name] = row
        ca._write(book_path(), book)
    return replaced


def remove_key(name: str) -> bool:
    """登録を外す。登録が無ければ偽。"""
    with _book_locked():
        book = _raw_book()
        if name not in book["keys"]:
            return False
        del book["keys"][name]
        ca._write(book_path(), book)
    return True


# ---------------------------------------------------------------- 子の環境


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


def key_rows(now: float | None = None) -> list[dict]:
    """`account list` の行（値を除く）: 月の額・直近 7 日の使用額・週の利用枠・使用の割合。登録簿が壊れていれば BookError。

    帳簿を読めなければ使用額・割合は None（表示は「不明」）。"""
    ks = keys()
    obs, ledger_ok = launch_observe.key_obs(ks, "near-limit", time.time() if now is None else now)
    out = []
    for k, o in zip(ks, obs):
        budget = launch_rules.weekly_budget(o.monthly_usd) if launch_rules.declared(o) else None
        spent = o.spent_7d if ledger_ok else None
        ratio = round(spent / budget, 4) if budget and spent is not None else None
        row = {"name": k.name, "kind": "api_key", "account": k.account, "monthly_usd": k.monthly_usd}
        out.append({**row, "spent_7d": spent, "weekly_budget": round(budget, 2) if budget else None, "ratio": ratio})
    return out


# ---------------------------------------------------------------- 起動口の持ち方


class KeyRoute:
    """`supervise.py` の起動口（`ClaudeRunner`）の 1 プロセスの API キーの持ち方。

    `state` はプランの状態（`cur`・`switched`・`progress_write`）。`key` は直前の起動の API キーの名前（無ければ None）。
    `inherited` は起動したときの環境が持つキー（入れ子の起動）で、使えなくなったら `dropped` にして以後の子から外す。
    `back` はそのとき戻る登録済みアカウント。`account()` は呼び手の今のアカウント（キーの子の戻り先に残す）。
    `rule` は直前に記録した起動先の決定の規則の名前（決定が変わったときだけ記録する。#2930 の受け入れ条件 23）。"""

    def __init__(self, state, environ=None, account=None):
        self.state = state
        self.key: str | None = None
        self.rule: str | None = None
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
        """1 回の呼び出しの API キーの試し方。`keep()` はトークンを更新しないアカウントの名前（観測の収集へ渡す）。
        `cwd` は子の claude を起動する場所（子が読むプロジェクトの設定の送り先を確かめる）。"""
        return KeyTry(self, self.use(), keep, cwd)

    def note_decision(self, d: launch_rules.Decision | None) -> None:
        """near-limit の規則 3〜5（帳簿を読めない・宣言が無い・利用枠を使い切った）に当たった・外れたときに 1 行残す。"""
        rule = d.rule if d is not None and d.rule.startswith("near_limit_") and d.route != launch_rules.ROUTE_KEY else None
        if rule == self.rule:
            return
        self.rule = rule
        if rule is not None:
            st = self.state
            st.progress_write({"kind": "account", "step": st.cur.get("id"), "reason": "launch_rule", **d.row()})

    def note_switch(self, k: ApiKey | None, d: launch_rules.Decision | None = None) -> None:
        """使うキーが替わったら、プランの状態（`auth`・`switched`）と進捗ログの `account` の行に残す（E3・E7）。

        行には起動先の決定の規則の名前と根拠の値を足す（キーへ移った・アカウントへ戻った。受け入れ条件 23）。"""
        name = k.name if k is not None else None
        if name == self.key:
            return
        st, (before, self.key) = self.state, (self.key, name)
        if name is not None:
            st.cur["auth"] = f"API キー {name}"
            if st.cur["auth"] not in st.switched:
                st.switched.append(st.cur["auth"])
        row = {"kind": "account", "step": st.cur.get("id"), "reason": "api_key", "from_key": before, "to_key": name}
        if d is not None:
            row.update(rule=d.rule, evidence=d.evidence)
        st.progress_write(row)

    def result(self, k: ApiKey, res: dict, exclude: set) -> bool:
        """キーで起動した結果を読む。残高切れ・認証の失敗・上限ならキーをこの呼び出しの除外へ足して真（同じ呼び出しを
        起動し直す）。除外は呼び出しの外へ持ち越さず、ファイルへ書かない（#2930 の決定 2）。受け継いだキーは使えなく
        なったら以後の子から外す。"""
        failure = CREDIT_LOW if res.get("credit") else AUTH_FAILED if res.get("auth") else LIMITED if res.get("limit") else None
        if res.get("ok") or failure is None:
            return False
        exclude.add(k.name)
        if self.inherited is not None and k.name == self.inherited.name:
            # 登録簿から選んだ同じ名前のキー（宣言が always の入れ子）も、受け継いだキーと同じものとして以後の子から外す
            self.dropped = True
        if failure != LIMITED:
            self.state.progress_write({"kind": "api_key", "step": self.state.cur.get("id"), "name": k.name, "reason": failure})
        return True


class KeyTry:
    """1 回の呼び出しの中の API キーの試し方（この呼び出しで外したキー）。どのキーで起動するかは起動先の決定が決める。"""

    def __init__(self, route: KeyRoute, how: str, keep, cwd: str | None = None):
        self.route, self.how, self.keep, self.cwd = route, how, keep, cwd
        self.tried: set[str] = set()
        self.decision: launch_rules.Decision | None = None

    def decide(self) -> launch_rules.Decision | None:
        """起動先の決定（宣言が `off` なら決めない）。今のアカウントと、この呼び出しで外したキーを渡す。"""
        if self.how == "off":
            return None
        cur = self.route.account()
        return launch_observe.decide_now(current=None if cur == ca.METERED else cur, exclude_keys=self.tried, keep=self.keep(), use=self.how)

    def registered(self) -> ApiKey | None:
        """起動先の決定が選んだ登録済みのキー（決定が API キーでなければ None）。決定を `decision` に残す。"""
        self.decision = d = self.decide()
        self.route.note_decision(d)
        return get(d.target) if d is not None and d.route == launch_rules.ROUTE_KEY else None

    def pick(self) -> ApiKey | None:
        """この起動に使うキー。登録済みのキーが選ばれなければ、受け継いだキーを外していない限りそれを使う。

        送り先が Anthropic でなければ、登録済みのキーも受け継いだキーも使わない。受け継いだキーは外し（`dropped`）、
        キーの変数を除いたログインの環境で起動させる（`env`）。"""
        r = self.route
        wanted = self.registered()
        kept = r.inherited if r.inherited and not r.dropped and r.inherited.name not in self.tried else None
        if wanted is None and kept is None:
            return None
        if not sends_to_anthropic(dict(os.environ), self.cwd):
            if kept is not None:
                r.dropped = True
            return None
        return wanted or kept

    def run(self, call):
        """キーで起動する（`call(環境, キーの名前)`）。キーで決着したらその結果、使うキーが無くなったら None を返す。"""
        while (k := self.pick()) is not None:
            self.route.note_switch(k, self.decision)
            res = call(env_with_key(k, dict(os.environ), self.route.account()) if k is not self.route.inherited else None, k.name)
            if not self.route.result(k, res, self.tried):
                return res
        self.route.note_switch(None, self.decision)
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
        """上限に当たって替えるアカウントが無いとき、起動先の決定が登録済みのキーを選ぶなら真（その時点の観測で決める）。"""
        return self.how != "off" and sends_to_anthropic(dict(os.environ), self.cwd) and self.registered() is not None

    def reset(self) -> None:
        """解除を待った後は、この呼び出しで外したキーを選び直せる。"""
        self.tried.clear()
