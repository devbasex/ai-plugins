"""登録済みの claude アカウント: 置き場・トークン・使用量の保存・選び方・子の環境（#1389）。

ラッパー（`relay_lib/`）と `supervise.py` が同じこの部品を使う。置き場のファイルを書くのはこのモジュールだけである
（`account add` の中で claude 自身が書く `.credentials.json` と `.claude.json` を除く）。宛先への 1 回の要求と
文言の読みは `lib/claude_usage.py` が持つ。

置き場は `${NDF_ACCOUNTS_DIR:-${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/accounts}/`（0700）。アカウントごとの設定ディレクトリ
`<名前>/`（0700。`auth login` の書き先）に `.credentials.json`・`account.json`・`usage.json`（0600）を置き、
排他は `<名前>.lock` で取る。

- 共有の設定ディレクトリの `.credentials.json` は読まず、書かない。子へは選んだアカウントのアクセストークンを
  環境変数 `CLAUDE_CODE_OAUTH_TOKEN` で渡す（引数に載せない）
- 使用量の取得先は 1 アカウントにつき `NDF_ACCOUNT_CHECK_INTERVAL` 秒（既定 300）に 1 回までしか呼ばない。
  数えるのは `usage.json` の `fetched_at`（成否を問わない）で、プロセス・コンテナをまたぐ
- 選び方・判定に LLM を呼ばない。呼ぶのは使用量の取得先とトークンの更新の宛先だけである
- 標準ライブラリと `claude_usage`・`locks`（filelock。使う関数の中で import する）だけを読む
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from claude_usage import Usage, epoch, get_usage, iso_utc, refresh_oauth

NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
METERED = "metered"  # 従量の接続を表す予約の名前。登録できない
TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
NAME_ENV = "NDF_CLAUDE_ACCOUNT"
# 認証の優先順位でトークンより上に来る変数（アカウントの子で外す）と、専用の設定ディレクトリの claude で外す変数
FOREIGN_AUTH_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")
AUTH_ENV = (TOKEN_ENV, NAME_ENV, *FOREIGN_AUTH_ENV)
FALLBACK_ENV = "NDF_SUPERVISE_CLAUDE_FALLBACK"
ACCOUNT_FILE = "account.json"
USAGE_FILE = "usage.json"
CRED_FILE = ".credentials.json"
REFRESH_BEFORE = 3600.0  # 期限のこの秒数前を切ったら更新する
LOCK_WAIT = 30.0
NO_RESET_HOLD = 5 * 3600.0  # リセット時刻の読めない上限の観測を候補から外す秒数
# 枠の大きさの対応表（`rateLimitTier` → 枠ごとの USD 換算）。根拠は issues/relay-capacity-estimate-2026-09-28.md。
# Max 5x の週の枠は非公式の比（1,100 × 3.5 / 6）による仮の値（#1453 の決定 1）。表に無い tier は宣言だけで決まる
CAPACITY: dict[str, dict[str, float]] = {
    "default_claude_max_20x": {"five_hour": 210.0, "seven_day": 1100.0},
    "default_claude_max_5x": {"five_hour": 52.5, "seven_day": 640.0},
}
WINDOWS = ("five_hour", "seven_day")


# ---------------------------------------------------------------- 設定と置き場


def _setting(name: str, default: float) -> float:
    """設定の数（負は 0）。無い・読めないときは `default`。"""
    try:
        return max(0.0, float(os.environ.get(name, "") or default))
    except ValueError:
        return default


def check_interval() -> float:
    """使用量の取得の最短の間隔（秒）。定期の確認の間隔も兼ねる。"""
    return _setting("NDF_ACCOUNT_CHECK_INTERVAL", 300)


def switch_at() -> float:
    """切り替えの閾値（%）。100 以上なら閾値による切り替えをしない。"""
    return _setting("NDF_ACCOUNT_SWITCH_AT", 90)


def store_dir() -> str:
    forced = os.environ.get("NDF_ACCOUNTS_DIR")
    if forced:
        return forced
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "ndf", "accounts")


def account_dir(name: str) -> str:
    return os.path.join(store_dir(), name)


def valid_name(name: str) -> bool:
    return bool(NAME_RE.fullmatch(name or "")) and name != METERED


def names() -> list[str]:
    """登録済みのアカウントの名前（名前の順）。置き場が無ければ空。"""
    try:
        entries = sorted(os.listdir(store_dir()))
    except OSError:
        return []
    return [n for n in entries if valid_name(n) and os.path.isfile(os.path.join(store_dir(), n, ACCOUNT_FILE))]


def registered() -> int:
    return len(names())


def _read(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write(path: str, data: dict) -> None:
    """0600 の一時ファイルに書いてから置き換える。"""
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _tighten(path: str, mode: int) -> None:
    st = os.stat(path)
    if st.st_mode & 0o777 & ~mode:
        os.chmod(path, mode)


def _secure(name: str) -> bool:
    """置き場とアカウントのディレクトリを 0700、中のファイルを 0600 へ直す。直せなければ偽。"""
    d = account_dir(name)
    try:
        _tighten(store_dir(), 0o700)
        _tighten(d, 0o700)
        for f in os.listdir(d):
            p = os.path.join(d, f)
            if os.path.isfile(p) and not os.path.islink(p):
                _tighten(p, 0o600)
    except OSError:
        return False
    return True


def make_store() -> str:
    d = store_dir()
    os.makedirs(d, mode=0o700, exist_ok=True)
    _tighten(d, 0o700)
    return d


@contextmanager
def _locked(name: str):
    """アカウントごとの排他（`<置き場>/<名前>.lock`）。取れなければ locks.LockTimeout。"""
    import locks  # 外部パッケージ（filelock）。読み込みのときには import しない

    make_store()
    with locks.exclusive(os.path.join(store_dir(), name), timeout=LOCK_WAIT):
        yield


def lock_timeout() -> type[BaseException]:
    import locks

    return locks.LockTimeout


# ---------------------------------------------------------------- アカウントと上限の観測


@dataclass
class Account:
    name: str
    email: str
    needs_relogin: bool
    limit: dict | None
    usage: Usage | None
    org_id: str = ""  # 組織（`claude auth status` の orgId）。組織を記録する前の登録は空
    org_name: str = ""
    tier: str = ""  # `.credentials.json` の `claudeAiOauth.rateLimitTier`。読めなければ空
    declared: dict | None = None  # 枠の大きさの宣言（`account.json` の `capacity`。正の数の枠だけ）

    def score(self) -> float | None:
        """使用率（残量を読んでいなければ None）。"""
        return self.usage.score() if self.usage else None

    def known(self) -> bool:
        """残量を読めているか。"""
        return bool(self.usage and self.usage.windows_known())

    def capacity(self) -> dict:
        """枠ごとの枠の大きさ（USD）。宣言 → 対応表 → 不明（None）の順に決まる（I2）。"""
        table = CAPACITY.get(self.tier) or {}
        declared = self.declared or {}
        return {k: declared.get(k, table.get(k)) for k in WINDOWS}

    def remaining(self) -> float | None:
        """残りの量: 枠の大きさと使用率が分かる枠の「枠の大きさ ×（1 − 使用率 / 100）」の最小（I1）。無ければ None。

        モデル別の週の枠の大きさは週の枠と同じとする。"""
        u = self.usage
        if u is None or not u.windows_known():
            return None
        cap = self.capacity()
        pairs = [(cap["five_hour"], u.five_hour), (cap["seven_day"], u.seven_day)]
        pairs += [(cap["seven_day"], w) for w in u.scoped or []]
        vals = [
            max(0.0, c * (1 - w["utilization"] / 100))
            for c, w in pairs
            if c is not None and isinstance(w, dict) and isinstance(w.get("utilization"), (int, float))
        ]
        return min(vals) if vals else None

    def observed_until(self, now: float) -> float | None:
        """上限の観測（`limit`）が今も効いているなら、その終わりの時刻。"""
        lim = self.limit
        if not isinstance(lim, dict):
            return None
        observed = epoch(lim.get("observed_at")) or now
        until = epoch(lim.get("resets_at")) or observed + NO_RESET_HOLD
        if until <= now:
            return None
        u = self.usage
        if u is not None and u.windows_known() and u.fetched_at > observed and u.limited_until(now) is None:
            return None  # 観測の後に読んだ残量が上限にない
        return until

    def limited_until(self, now: float) -> float | None:
        vals = [v for v in (self.usage.limited_until(now) if self.usage else None, self.observed_until(now)) if v]
        return max(vals) if vals else None

    def state(self, now: float) -> str:
        """一覧の状態の列。"""
        if self.needs_relogin:
            return "再登録が要る"
        until = self.limited_until(now)
        if until is not None:
            if self.known() and self.usage.spend_reached() or (self.limit or {}).get("type") == "spend":
                return "支出上限"
            return f"上限（{local_time(until)}）"
        return "使える" if self.known() else "残量不明"


def local_time(t: float | None, form: str = "%m-%d %H:%M") -> str:
    if t is None or t == math.inf:
        return "不明"
    return datetime.fromtimestamp(t).astimezone().strftime(form)


def load_account(name: str) -> Account | None:
    a = _read(_account_path(name))
    if a is None:
        return None
    return Account(
        name=name,
        email=str(a.get("email") or ""),
        needs_relogin=bool(a.get("needs_relogin")),
        limit=a.get("limit") if isinstance(a.get("limit"), dict) else None,
        usage=Usage.from_json(_read(_usage_path(name))),
        org_id=str(a.get("org_id") or ""),
        org_name=str(a.get("org_name") or ""),
        tier=_tier(name),
        declared=_declared(a.get("capacity")),
    )


def _declared(v) -> dict | None:
    """`account.json` の `capacity` のうち正の数の枠だけ。1 つも無ければ None（I7）。"""
    if not isinstance(v, dict):
        return None
    out = {k: float(v[k]) for k in WINDOWS if isinstance(v.get(k), (int, float)) and not isinstance(v.get(k), bool) and v[k] > 0}
    return out or None


def _tier(name: str) -> str:
    """`.credentials.json` の `rateLimitTier` だけを読む（トークンは持たない。I8）。"""
    o = _oauth(name)
    t = o.get("rateLimitTier") if o is not None else None
    return t if isinstance(t, str) else ""


def _update_account(name: str, **fields) -> None:
    """排他の中で呼ぶ。登録の無いアカウントは作らない（I2）。"""
    path = _account_path(name)
    a = _read(path)
    if a is not None:
        a.update(fields)
        _write(path, a)


def note_limit(name: str, kind: str, resets_at: float | None, now: float | None = None) -> None:
    """アカウント `name` が上限（種類 `kind`）に達したと観測したことを残す。"""
    if not name or name == METERED:
        return
    now = time.time() if now is None else now
    try:
        with _locked(name):
            _update_account(name, limit={"type": kind, "resets_at": iso_utc(resets_at), "observed_at": iso_utc(now)})
    except (lock_timeout(), OSError):
        pass


# ---------------------------------------------------------------- トークン


def _creds_path(name: str) -> str:
    return os.path.join(account_dir(name), CRED_FILE)


def _account_path(name: str) -> str:
    return os.path.join(account_dir(name), ACCOUNT_FILE)


def _usage_path(name: str) -> str:
    return os.path.join(account_dir(name), USAGE_FILE)


def _oauth(name: str) -> dict | None:
    """`.credentials.json` の `claudeAiOauth`（辞書でなければ None）。"""
    d = _read(_creds_path(name))
    o = d.get("claudeAiOauth") if d else None
    return o if isinstance(o, dict) else None


def _creds(name: str) -> dict | None:
    o = _oauth(name)
    return o if o is not None and isinstance(o.get("accessToken"), str) and o["accessToken"] else None


def _token_held(name: str, before: float | None, now: float, force: bool = False, min_left: float = 0) -> str | None:
    """排他の中で呼ぶ。使えるアクセストークン（使えなければ None）。

    `before` は期限の何秒前を切ったら更新するか（None は更新しない）。`min_left`（打ち切りの秒）以下しか残らないものは
    更新するか None にする。`force` は期限に関わらず更新する（401 のとき）。断られたら `needs_relogin` を真にする。"""
    a = _read(_account_path(name))
    if a is None or a.get("needs_relogin"):
        return None
    o = _creds(name)
    if not _secure(name) or o is None:
        _update_account(name, needs_relogin=True)
        return None
    exp = (o.get("expiresAt") or 0) / 1000
    if not _needs_refresh(exp, now, before, force, min_left):
        return o["accessToken"] if exp - now > min_left else None
    # 一時的な失敗なら、残りが足りれば今のトークンを使う
    return _refresh_and_store(name, o, now, o["accessToken"] if exp - now > min_left and not force else None)


def _needs_refresh(exp: float, now: float, before: float | None, force: bool, min_left: float) -> bool:
    """期限 `exp` のトークンを更新するか。`before` は `min_left` 以上に引き上げる（None は更新しない）。"""
    before = None if before is None else max(before, min_left)
    return force or (before is not None and exp - now <= before)


def _refresh_and_store(name: str, o: dict, now: float, fallback: str | None) -> str | None:
    """排他の中で呼ぶ。トークンを更新して書き込み、新しいアクセストークンを返す。一時的な失敗なら `fallback`。"""
    rexp = o.get("refreshTokenExpiresAt")
    if isinstance(rexp, (int, float)) and rexp / 1000 <= now:
        _update_account(name, needs_relogin=True)
        return None
    how, new = refresh_oauth(o, now)
    if how == "rejected":
        _update_account(name, needs_relogin=True)
        return None
    if new is None:
        return fallback
    try:
        whole = _read(_creds_path(name)) or {}
        whole["claudeAiOauth"] = new
        _write(_creds_path(name), whole)
    except OSError:
        _update_account(name, needs_relogin=True)
        return None
    return new["accessToken"]


def token(name: str, before: float | None = REFRESH_BEFORE, now: float | None = None, min_left: float = 0) -> str | None:
    """子へ渡すアクセストークン。期限の `before` 秒前を切っていれば更新する（None は更新しない）。`min_left` は `_token_held`。"""
    now = time.time() if now is None else now
    try:
        with _locked(name):
            return _token_held(name, before, now, min_left=min_left)
    except (lock_timeout(), OSError):
        return None


# ---------------------------------------------------------------- 使用量


def _fetch(name: str, before: float | None, now: float) -> Usage:
    """排他の中で呼ぶ。取得先を呼んで残量を読む（推論は呼ばない）。401 なら 1 度だけ更新してやり直す。"""
    tok = _token_held(name, before, now)
    if tok is None:
        return Usage(fetched_at=now, error="token")
    o = _creds(name) or {}
    if "user:profile" not in (o.get("scopes") or ["user:profile"]):
        return Usage(fetched_at=now, error="scope")
    status, u = get_usage(tok, now)
    if status == 401 and before is not None:
        tok = _token_held(name, before, now, force=True)
        if tok is None:
            return u
        status, u = get_usage(tok, now)
    return u


def usage(name: str, before: float | None = REFRESH_BEFORE, now: float | None = None) -> Usage | None:
    """残量。前の取得から `check_interval()` 秒の中なら保存した値を返し、取得先を呼ばない（I6）。"""
    now = time.time() if now is None else now
    path = _usage_path(name)
    try:
        with _locked(name):
            saved = Usage.from_json(_read(path))
            if saved is not None and now - saved.fetched_at < check_interval():
                return saved
            if _read(_account_path(name)) is None:
                return None
            u = _fetch(name, before, now)
            _write(path, u.to_json())
            return u
    except (lock_timeout(), OSError):
        return Usage.from_json(_read(path))


# ---------------------------------------------------------------- 選び方


@dataclass
class Choice:
    """選んだアカウント（無ければ None）と、すべて上限のときに最も早く戻るアカウントと時刻。

    `score` は選んだアカウントの使用率（切り替えの閾値と比べる値）、`remaining` は残りの量（不明は None）。"""

    name: str | None
    score: float | None = None
    earliest: tuple[str, float] | None = None
    remaining: float | None = None


def _try_order(pool: list[Account], readable: bool) -> list[Account]:
    """試す順（I3）: 使用率が閾値未満（読めないものを含む）の側を先に、各側の中で (1) 残りの量の大きい順 →
    (2) 残りの量が不明で使用率を読めるものを使用率の小さい順 → (3) 読める候補が無いときだけ残量不明を名前の順。
    同順は `five_hour` のリセット時刻の早い方、次に名前の順。"""
    thr = switch_at()

    def reset(a: Account) -> float:
        return (a.usage.resets("five_hour") if a.usage else None) or math.inf

    out: list[Account] = []
    below = [a for a in pool if a.score() is None or a.score() < thr]
    above = [a for a in pool if a not in below]
    for side in (below, above):
        rem = {a.name: a.remaining() for a in side}
        by_rem = [a for a in side if rem[a.name] is not None]
        by_score = [a for a in side if rem[a.name] is None and a.score() is not None]
        out += sorted(by_rem, key=lambda a: (-rem[a.name], reset(a), a.name))
        out += sorted(by_score, key=lambda a: (a.score(), reset(a), a.name))
        if not readable:
            out += sorted((a for a in side if a not in by_rem and a not in by_score), key=lambda a: a.name)
    return out


def _candidates(exclude, before_for, now: float) -> tuple[list[Account], tuple[str, float] | None]:
    """上限に達していない候補と、上限にあるもののうち最も早く戻るアカウントと時刻。「再登録が要る」は外す。"""
    earliest: tuple[str, float] | None = None
    pool: list[Account] = []
    for n in names():
        if n in exclude:
            continue
        usage(n, before_for(n), now)
        acc = load_account(n)
        if acc is None or acc.needs_relogin:
            continue
        until = acc.limited_until(now)
        if until is not None:
            if earliest is None or until < earliest[1]:
                earliest = (n, until)
            continue
        pool.append(acc)
    return pool, earliest


def choose(exclude=(), before: float | None = REFRESH_BEFORE, keep=(), now: float | None = None, min_left: float = 0) -> Choice:
    """上限に達していないアカウントのうち、残りの量の最も大きいものを選ぶ（#1453 の I3）。

    使用率が切り替えの閾値未満の候補を先に試し、残りの量の分からない候補は分かる候補の後ろへ使用率の順で並べる。
    残量不明は、上限に達していない候補に読めるものが無いときだけ候補にする（名前の順）。「再登録が要る」とトークンを
    得られないもの（残り `min_left` 秒以下を含む）は外す（#1389 の I13）。`keep` の名前はトークンを更新しない
    （動いている区間のアカウント。#1389 の I5）。"""
    now = time.time() if now is None else now

    def before_for(n: str) -> float | None:
        return None if n in keep else before

    pool, earliest = _candidates(exclude, before_for, now)
    readable = any(a.known() for a in pool)
    for pick in _try_order(pool, readable):
        if token(pick.name, before_for(pick.name), now, min_left) is not None:
            return Choice(pick.name, pick.score(), earliest, pick.remaining())
    return Choice(None, None, earliest)


# ---------------------------------------------------------------- 子の環境と従量の接続


def fallback_env(environ=None) -> dict:
    """従量の接続の宣言 `NDF_SUPERVISE_CLAUDE_FALLBACK`（`KEY=VALUE` を空白区切り）を読む。"""
    out = {}
    for tok in shlex.split((environ if environ is not None else os.environ).get(FALLBACK_ENV, "")):
        k, sep, v = tok.partition("=")
        if sep and k:
            out[k] = v
    return out


def account_env(name: str, base: dict, before: float | None = REFRESH_BEFORE, min_left: float = 0) -> dict | None:
    """`base` にアカウント `name`（か `metered`）の環境を重ねる。トークンを得られなければ None。

    従量の接続は `CLAUDE_CODE_OAUTH_TOKEN` と `FOREIGN_AUTH_ENV` を外してから宣言の変数を重ねる（認証の方式を
    宣言どおり 1 つにする）。アカウントは宣言のキーと、認証の優先順位でトークンより上に来る変数
    （`FOREIGN_AUTH_ENV`）を外してからトークンと名前を足す（混ぜない。I16）。"""
    env = dict(base)
    declared = fallback_env(base)
    if name == METERED:
        for k in (TOKEN_ENV, *FOREIGN_AUTH_ENV):
            env.pop(k, None)
        env.update(declared)
        env[NAME_ENV] = METERED
        return env
    tok = token(name, before, min_left=min_left)
    if tok is None:
        return None
    for k in (*declared, *FOREIGN_AUTH_ENV):
        env.pop(k, None)
    env[TOKEN_ENV] = tok
    env[NAME_ENV] = name
    return env


def account_label(name: str | None) -> str:
    """画面の 1 行に出す識別（`名前（メール）`）。トークンは含めない。"""
    if not name:
        return "既定のログイン"
    if name == METERED:
        return "従量の接続"
    acc = load_account(name)
    return f"{name}（{acc.email}）" if acc and acc.email else name


# ---------------------------------------------------------------- 登録・削除・一覧（`relay.py account` が呼ぶ）


def staging_dir(name: str) -> str:
    """登録の途中の設定ディレクトリ（0700）。`names()` には現れない。"""
    make_store()
    d = os.path.join(store_dir(), f".add-{name}-{os.getpid()}")
    shutil.rmtree(d, ignore_errors=True)
    os.mkdir(d, 0o700)
    return d


def register(name: str, staging: str, email: str, org_id: str = "", org_name: str = "") -> None:
    """ログインの済んだ `staging` を `name` として置く。同じ名前の古いものは置き換える。"""
    with _locked(name):
        final = account_dir(name)
        row = {"name": name, "email": email, "registered_at": iso_utc(time.time()), "needs_relogin": False, "limit": None}
        if org_id:
            row.update(org_id=org_id, org_name=org_name)
        _write(os.path.join(staging, ACCOUNT_FILE), row)
        if os.path.exists(final):
            shutil.rmtree(final)
        os.replace(staging, final)
        _secure(name)


def set_org(name: str, org_id: str, org_name: str) -> None:
    """組織を記録する前の登録へ、読み直した組織を書き戻す。"""
    with _locked(name):
        _update_account(name, org_id=org_id, org_name=org_name)


def set_capacity(name: str, declared: dict) -> Account | None:
    """枠の大きさの宣言を書く。`declared` は枠ごとに正の数（宣言する）か None（外して対応表へ戻す）。

    登録されていなければ None。書くのは `account.json` だけである（I8）。"""
    with _locked(name):
        path = _account_path(name)
        a = _read(path)
        if a is None:
            return None
        cur = _declared(a.get("capacity")) or {}
        for k in WINDOWS:
            if k in declared:
                if declared[k] is None:
                    cur.pop(k, None)
                else:
                    cur[k] = float(declared[k])
        _update_account(name, capacity=cur or None)
    return load_account(name)


def unregister(name: str) -> None:
    with _locked(name):
        shutil.rmtree(account_dir(name), ignore_errors=True)


def discard(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def rows(now: float | None = None) -> list[dict]:
    """一覧の中身（推論は呼ばない）。期限の過ぎたトークンだけを更新する（動いている区間のトークンを替えない）。"""
    now = time.time() if now is None else now
    out = []
    for n in names():
        usage(n, 0, now)
        acc = load_account(n)
        if acc is None:
            continue
        u = acc.usage if acc.known() else None
        out.append(
            {
                "name": n,
                "email": acc.email,
                "org_id": acc.org_id or None,
                "org_name": acc.org_name or None,
                "five_hour": u.five_hour if u else None,
                "seven_day": u.seven_day if u else None,
                "scoped": u.scoped if u else None,
                "spend_limit_reached": u.spend_reached() if u else None,
                "spend": u.spend if u else None,
                "tier": acc.tier or None,
                "capacity": acc.capacity(),
                "capacity_declared": {k: (acc.declared or {}).get(k) for k in WINDOWS},
                "remaining": acc.remaining(),
                "state": acc.state(now),
            }
        )
    return out
