"""登録済みの claude アカウント: 置き場・使用量の保存・選び方・アカウントの設定ディレクトリの用意・子の環境（#1389・#1576）。

ラッパー（`relay_lib/`）と `supervise.py` が同じこの部品を使う。置き場のファイルを NDF の側で書くのはこのモジュールと
その部品（`lib/claude_account_dir.py`）だけである。認証ファイル（`.credentials.json`）と `.claude.json` は claude 自身も
書く。宛先への 1 回の要求と文言の読みは `lib/claude_usage.py` が持つ。

置き場は `${NDF_ACCOUNTS_DIR:-<共有の設定ディレクトリ>/ndf/accounts}/`（0700）。アカウントの設定ディレクトリ `<名前>/`（0700）は、
そのアカウントで起動する claude の `CLAUDE_CONFIG_DIR` になる。`.credentials.json`・`.claude.json`・`account.json`・
`usage.json`・同期の控え（0600）を実体で持ち、ほかは共有の設定ディレクトリの直下への symlink にする（#1576）。
NDF のアカウントごとの排他は `.locks/<名前>.lock` で取る（`<名前>.lock` は claude のトークンの更新の排他と同じパスに
なるため使わない）。保存した従量の接続の宣言 `metered.json`（0600）と、OAuth の 2 回の登録の間の登録の途中の状態
`.pending-<名前>/`（0700）も置き場に置く（#1468）。

- 共有の設定ディレクトリの `.credentials.json` は読まず、書かない。子へはトークンもスコープも渡さず、アカウントの
  設定ディレクトリを `CLAUDE_CONFIG_DIR` で渡す。トークンの更新は claude 自身が行う
- NDF がトークンを更新するのは、動いている claude の無いアカウントで、期限が切れたか取得先が 401 を返し、claude と同じ
  更新の排他（`.oauth_refresh.lock`）を取れたときだけである（#1576 の I8・I9）
- 共有の設定ディレクトリは `NDF_SHARED_CONFIG_DIR`（子へ渡す元の `CLAUDE_CONFIG_DIR`）から求める（入れ子の起動でも同じ）
- 使用量の取得先は 1 アカウントにつき `NDF_ACCOUNT_CHECK_INTERVAL` 秒（既定 300）に 1 回までしか呼ばない。
  数えるのは `usage.json` の `fetched_at`（成否を問わない）で、プロセス・コンテナをまたぐ
- 選び方・判定に LLM を呼ばない。呼ぶのは使用量の取得先とトークンの更新の宛先だけである
- 標準ライブラリと `claude_usage`・`claude_account_dir`・`locks`（filelock）・`procs`（psutil。どちらも使う関数の中で
  import する）だけを読む
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime

import claude_account_dir as account_files
from claude_usage import Usage, epoch, get_usage, iso_utc, refresh_oauth

NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
METERED = "metered"  # 従量の接続を表す予約の名前。登録できない
# トークンとスコープの変数。子へは渡さない（元の環境にあれば外す。#1576 の I1）
TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
SCOPES_ENV = "CLAUDE_CODE_OAUTH_SCOPES"
NAME_ENV = "NDF_CLAUDE_ACCOUNT"
CONFIG_ENV = "CLAUDE_CONFIG_DIR"
# 子へ渡す元の `CLAUDE_CONFIG_DIR`（無かったら空文字）。入れ子の起動で共有の設定ディレクトリを求める（#1576 の決定 2）
SHARED_ENV = "NDF_SHARED_CONFIG_DIR"
# プラグインの導入の記録を共有側のパスにする（#1576 の決定 3）
PLUGIN_CACHE_ENV = "CLAUDE_CODE_PLUGIN_CACHE_DIR"
USAGE_SCOPE = "user:profile"  # 使用量の取得先が要るスコープ
# 認証の優先順位でトークンより上に来る変数（アカウントの子で外す）と、専用の設定ディレクトリの claude で外す変数
FOREIGN_AUTH_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")
STRIPPED_AUTH_ENV = (TOKEN_ENV, SCOPES_ENV) + FOREIGN_AUTH_ENV  # 子へ渡さない認証の変数の組（#1576 の I1・I2）
AUTH_ENV = (TOKEN_ENV, SCOPES_ENV, NAME_ENV, *FOREIGN_AUTH_ENV)
FALLBACK_ENV = "NDF_SUPERVISE_CLAUDE_FALLBACK"
AWS_KEY_ENV = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")
# 保存した宣言に入れてはならない資格情報の変数（#1468 の I1）
SECRET_ENV = (*AWS_KEY_ENV, "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", TOKEN_ENV)
METERED_FILE = "metered.json"
PENDING_TTL = 600.0  # 登録の途中の状態の期限（秒。#1468 の決定 3）
# 登録の途中の状態の置き場（`pending_dir`）に置くもの
PENDING_CONFIG = "config"
PENDING_FIFO = "code.fifo"
PENDING_OUT = "login.out"
PENDING_FILE = "pending.json"
ACCOUNT_FILE = "account.json"
USAGE_FILE = "usage.json"
CRED_FILE = ".credentials.json"
LOCKS_DIR = ".locks"
REFRESH_LOCK = ".oauth_refresh.lock"  # claude と同じ更新の排他（設定ディレクトリの中のディレクトリ）
EXPIRY_MARGIN = 60.0  # 残りがこの秒数以下のアクセストークンは期限切れとみなす
REFRESH_TIMEOUT = 3.0  # 更新の排他の中の宛先の待ちの上限（秒。#1576 の決定 12）
OLD_LOCK_AGE = 60.0  # 置き場の直下の古い形の排他ファイルを消す古さ（秒。これより新しいものは古い版が使っている）
AUTH_FAILED_HOLD = 3600.0  # 認証の失敗の観測を候補から外す秒数（#1576 の決定 13。仮の値）
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


def _now(now: float | None) -> float:
    """時刻の引数の既定（None なら今）。"""
    return time.time() if now is None else now


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


def _home_config() -> str:
    return os.path.join(os.path.expanduser("~"), ".claude")


def _original_config(environ) -> str:
    """元の `CLAUDE_CONFIG_DIR`（無ければ空文字）。アカウントの子の中では `NDF_SHARED_CONFIG_DIR` が持つ。"""
    return environ[SHARED_ENV] if SHARED_ENV in environ else environ.get(CONFIG_ENV) or ""


def shared_dir(environ=None) -> str:
    """共有の設定ディレクトリ。`NDF_SHARED_CONFIG_DIR` があればその値（空なら `~/.claude`）、無ければ
    `CLAUDE_CONFIG_DIR`（無ければ `~/.claude`）。"""
    return _original_config(os.environ if environ is None else environ) or _home_config()


def shared_config_file(environ=None) -> str:
    """共有の `.claude.json`（本体と同じ規則: 共有の設定ディレクトリの `.config.json` → 元の `CLAUDE_CONFIG_DIR` の
    `.claude.json` → `~/.claude.json`）。"""
    environ = os.environ if environ is None else environ
    legacy = os.path.join(shared_dir(environ), account_files.LEGACY_CONFIG_FILE)
    if os.path.exists(legacy):
        return legacy
    orig = _original_config(environ)
    return os.path.join(orig, ".claude.json") if orig else os.path.join(os.path.expanduser("~"), ".claude.json")


def store_dir() -> str:
    forced = os.environ.get("NDF_ACCOUNTS_DIR")
    if forced:
        return forced
    return os.path.join(shared_dir(), "ndf", "accounts")


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
    """アカウントごとの排他（`<置き場>/.locks/<名前>.lock`）。取れなければ locks.LockTimeout。

    `<置き場>/<名前>.lock` は claude がトークンを更新するときに取る旧形式の排他と同じパスなので使わない（#1576 の I7）。"""
    import locks  # 外部パッケージ（filelock）。読み込みのときには import しない

    make_store()
    d = os.path.join(store_dir(), LOCKS_DIR)
    os.makedirs(d, mode=0o700, exist_ok=True)
    with locks.exclusive(os.path.join(d, name), timeout=LOCK_WAIT):
        yield


def _sweep_old_locks(now: float | None = None) -> None:
    """置き場の直下の古い形の排他ファイル（空の `<名前>.lock` で、`OLD_LOCK_AGE` 秒より古いもの）を消す（E2）。"""
    now = _now(now)
    d = store_dir()
    try:
        entries = os.listdir(d)
    except OSError:
        return
    for e in entries:
        if not e.endswith(".lock") or e.startswith("."):
            continue
        p = os.path.join(d, e)
        try:
            st = os.lstat(p)
            if os.path.isfile(p) and not os.path.islink(p) and st.st_size == 0 and now - st.st_mtime > OLD_LOCK_AGE:
                os.remove(p)
        except OSError:
            continue


def lock_timeout() -> type[BaseException]:
    import locks

    return locks.LockTimeout


def _with_lock(name: str, fn, on_fail):
    """アカウント `name` の排他の中で `fn()` を呼ぶ。排他を取れないか OSError なら `on_fail()` を返す。"""
    try:
        with _locked(name):
            return fn()
    except (lock_timeout(), OSError):
        return on_fail()


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
    auth_failed: float | None = None  # 認証の失敗の観測の時刻（`account.json` の `auth_failed.observed_at`。#1576）

    def score(self) -> float | None:  # 使用率（残量を読んでいなければ None）
        return self.usage.score() if self.usage else None

    def known(self) -> bool:  # 残量を読めているか
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

    def auth_held(self, now: float) -> bool:
        """認証の失敗の観測が効いているか（観測から `AUTH_FAILED_HOLD` 秒の間。後の取得の成功で観測は消える）。"""
        return self.auth_failed is not None and now - self.auth_failed < AUTH_FAILED_HOLD

    def limited_until(self, now: float) -> float | None:
        vals = [v for v in (self.usage.limited_until(now) if self.usage else None, self.observed_until(now)) if v]
        return max(vals) if vals else None

    def _is_spend_limit(self) -> bool:
        """上限が支出上限か（読んだ残量が支出上限に達しているか、観測した上限の種類が spend）。"""
        if self.known() and self.usage.spend_reached():
            return True
        return (self.limit or {}).get("type") == "spend"

    def state(self, now: float) -> str:
        """一覧の状態の列。"""
        if self.needs_relogin:
            return "再登録が要る"
        if self.auth_held(now):
            return "認証の失敗"
        until = self.limited_until(now)
        if until is None:
            return "使える" if self.known() else "残量不明"
        if self._is_spend_limit():
            return "支出上限"
        return f"上限（{local_time(until)}）"


def local_time(t: float | None, form: str = "%m-%d %H:%M") -> str:
    if t is None or t == math.inf:
        return "不明"
    return datetime.fromtimestamp(t).astimezone().strftime(form)


def load_account(name: str) -> Account | None:
    a = _read(_path(name, ACCOUNT_FILE))
    if a is None:
        return None
    return Account(
        name=name,
        email=str(a.get("email") or ""),
        needs_relogin=bool(a.get("needs_relogin")),
        limit=a.get("limit") if isinstance(a.get("limit"), dict) else None,
        usage=Usage.from_json(_read(_path(name, USAGE_FILE))),
        org_id=str(a.get("org_id") or ""),
        org_name=str(a.get("org_name") or ""),
        tier=_tier(name),
        declared=_declared(a.get("capacity")),
        auth_failed=epoch((a.get("auth_failed") or {}).get("observed_at")) if isinstance(a.get("auth_failed"), dict) else None,
    )


def _declared(v) -> dict | None:
    """`account.json` の `capacity` のうち正の数の枠だけ。1 つも無ければ None（I7）。"""
    if not isinstance(v, dict):
        return None
    out = {k: float(v[k]) for k in WINDOWS if isinstance(v.get(k), (int, float)) and not isinstance(v.get(k), bool) and v[k] > 0}
    return out or None


def _tier(name: str) -> str:
    """`.credentials.json` の `rateLimitTier` だけを読む（トークンは持たない。I8）。"""
    t = (_oauth(name) or {}).get("rateLimitTier")
    return t if isinstance(t, str) else ""


def _update_account(name: str, **fields) -> None:
    """排他の中で呼ぶ。登録の無いアカウントは作らない（I2）。"""
    path = _path(name, ACCOUNT_FILE)
    a = _read(path)
    if a is not None:
        a.update(fields)
        _write(path, a)


def note_limit(name: str, kind: str, resets_at: float | None, now: float | None = None) -> None:
    """アカウント `name` が上限（種類 `kind`）に達したと観測したことを残す。"""
    if not name or name == METERED:
        return
    now = _now(now)
    _with_lock(
        name,
        lambda: _update_account(name, limit={"type": kind, "resets_at": iso_utc(resets_at), "observed_at": iso_utc(now)}),
        lambda: None,
    )


def note_auth_failed(name: str, now: float | None = None) -> None:
    """子の claude の応答が認証の失敗で終わったことを残す（`needs_relogin` は変えない。#1576 の I12）。"""
    if not name or name == METERED:
        return
    now = _now(now)
    _with_lock(name, lambda: _update_account(name, auth_failed={"observed_at": iso_utc(now)}), lambda: None)


# ---------------------------------------------------------------- 認証ファイルとトークンの更新


def _path(name: str, file: str) -> str:
    return os.path.join(account_dir(name), file)


def _oauth(name: str) -> dict | None:
    o = (_read(_path(name, CRED_FILE)) or {}).get("claudeAiOauth")
    return o if isinstance(o, dict) else None


def _creds(name: str) -> dict | None:
    return o if (o := _oauth(name)) is not None and isinstance(o.get("accessToken"), str) and o["accessToken"] else None


def _fresh(o: dict, now: float) -> bool:
    """アクセストークンが期限内か（残りが `EXPIRY_MARGIN` 秒を超える）。"""
    return (o.get("expiresAt") or 0) / 1000 - now > EXPIRY_MARGIN


def _usable_held(name: str, now: float) -> bool:
    """排他の中で呼ぶ。`usable` の中身。認証ファイルが無い・読めない・権限を直せない・期限の切れたアクセストークンを
    更新できる見込みが無い（リフレッシュトークンが無いか期限切れ）なら `needs_relogin` を真にする。"""
    a = _read(_path(name, ACCOUNT_FILE))
    if a is None or a.get("needs_relogin"):
        return False
    o = _creds(name)
    if not _secure(name) or o is None:
        _update_account(name, needs_relogin=True)
        return False
    rexp = o.get("refreshTokenExpiresAt")
    refreshable = bool(o.get("refreshToken")) and not (isinstance(rexp, (int, float)) and rexp / 1000 <= now)
    if refreshable or _fresh(o, now):
        return True
    _update_account(name, needs_relogin=True)  # 期限の切れたアクセストークンを更新できる見込みが無い（今のまま）
    return False


def usable(name: str, now: float | None = None) -> bool:
    """候補として起動できるか（ネットワークを使わない）。登録の記録があって再登録が要らず、権限を直せ、認証ファイルに
    リフレッシュトークンか期限内のアクセストークンがある。"""
    now = _now(now)
    return bool(_with_lock(name, lambda: _usable_held(name, now), lambda: False))


@contextmanager
def _refresh_lock(name: str):
    """claude と同じ更新の排他（アカウントの設定ディレクトリの中の `.oauth_refresh.lock` のディレクトリ）を待たずに取る。

    取れたら真を、取れなければ偽を流す。残った排他を横取りしない（#1576 の決定 12）。"""
    lock = _path(name, REFRESH_LOCK)
    try:
        os.mkdir(lock, 0o700)
    except OSError:
        yield False
        return
    try:
        yield True
    finally:
        try:
            os.rmdir(lock)
        except OSError:
            pass


def _refresh(name: str, seen: str, now: float) -> str | None:
    """排他の中で呼ぶ。更新の排他を取り、読み直した認証ファイルが変わっていなければトークンを更新する（I9）。

    `seen` は期限切れか 401 と判じたアクセストークン。読み直して別の期限内のトークンなら、宛先を呼ばずにそれを返す。
    断られたら `needs_relogin` を真にして None、一時的な失敗と排他を取れないときも None。書くのは `claudeAiOauth` だけで、
    ほかのキー（`mcpOAuth`）は書く直前に読み直したものを保つ。"""
    with _refresh_lock(name) as held:
        if not held:
            return None
        o = _creds(name)
        if o is None:
            return None
        if o["accessToken"] != seen and _fresh(o, now):
            return o["accessToken"]
        rexp = o.get("refreshTokenExpiresAt")
        how, new = ("rejected", None) if isinstance(rexp, (int, float)) and rexp / 1000 <= now else refresh_oauth(o, now, REFRESH_TIMEOUT)
        if how == "rejected":
            _update_account(name, needs_relogin=True)
            return None
        if new is None:
            return None
        try:
            _write(_path(name, CRED_FILE), {**(_read(_path(name, CRED_FILE)) or {}), "claudeAiOauth": new})
        except OSError:
            return None
        return new["accessToken"]


# ---------------------------------------------------------------- 使用量


def _fetch(name: str, refresh: bool, now: float) -> Usage:
    """排他の中で呼ぶ。取得先を呼んで残量を読む（推論は呼ばない）。`refresh` が偽なら更新の宛先を呼ばない（I8）。

    アクセストークンが期限切れなら更新してから、取得先が 401 なら 1 度だけ更新してやり直す。"""
    o = _creds(name) if _usable_held(name, now) else None
    if o is None:
        return Usage(fetched_at=now, error="token")
    if USAGE_SCOPE not in (o.get("scopes") or [USAGE_SCOPE]):
        return Usage(fetched_at=now, error="scope")
    tok = o["accessToken"] if _fresh(o, now) else (_refresh(name, o["accessToken"], now) if refresh else None)
    if tok is None:
        return Usage(fetched_at=now, error="token")
    status, u = get_usage(tok, now)
    if status == 401 and refresh:
        tok = _refresh(name, tok, now)
        if tok is None:
            return u
        status, u = get_usage(tok, now)
    return u


def usage(name: str, refresh: bool = True, now: float | None = None) -> Usage | None:
    """残量。前の取得から `check_interval()` 秒の中なら保存した値を返し、取得先を呼ばない（I6）。

    `refresh` が偽か、`name` がこのプロセスの環境の `NDF_CLAUDE_ACCOUNT`（動いている claude のアカウント）なら、
    トークンを更新しない（#1576 の I8）。動いている claude の無いアカウントの取得が成功したら、認証の失敗の観測を解く
    （#1576 の I12。子が終わる前の取得では解かない）。"""
    now = _now(now)
    path = _path(name, USAGE_FILE)
    refresh = refresh and name != os.environ.get(NAME_ENV)

    def held() -> Usage | None:
        saved = Usage.from_json(_read(path))
        if saved is not None and now - saved.fetched_at < check_interval():
            return saved
        if _read(_path(name, ACCOUNT_FILE)) is None:
            return None
        u = _fetch(name, refresh, now)
        _write(path, u.to_json())
        if refresh and u.error is None and (_read(_path(name, ACCOUNT_FILE)) or {}).get("auth_failed"):
            _update_account(name, auth_failed=None)
        return u

    return _with_lock(name, held, lambda: Usage.from_json(_read(path)))


# ---------------------------------------------------------------- 選び方


@dataclass
class Choice:
    """選んだアカウント（無ければ None）と、すべて上限のときに最も早く戻るアカウントと時刻。

    `score` は選んだアカウントの使用率（切り替えの閾値と比べる値）、`remaining` は残りの量（不明は None）。"""

    name: str | None
    score: float | None = None
    earliest: tuple[str, float] | None = None
    remaining: float | None = None

    def recoverable(self, thr: float) -> bool:
        """従量の接続からこの候補へ戻せるか（選べて、使用率が閾値 `thr` 未満か不明）。"""
        return bool(self.name) and (self.score is None or self.score < thr)


def _try_order(pool: list[Account], readable: bool) -> list[Account]:
    """試す順（I3）: 使用率が閾値未満（読めないものを含む）の側を先に、各側の中で (1) 残りの量の大きい順 →
    (2) 残りの量が不明で使用率を読めるものを使用率の小さい順 → (3) 読める候補が無いときだけ残量不明を名前の順。
    同順は `five_hour` のリセット時刻の早い方、次に名前の順。"""
    thr = switch_at()
    below = [a for a in pool if a.score() is None or a.score() < thr]
    return _order_side(below, readable) + _order_side([a for a in pool if a not in below], readable)


def _order_side(side: list[Account], readable: bool) -> list[Account]:
    rem = {a.name: a.remaining() for a in side}
    reset = {a.name: (a.usage.resets("five_hour") if a.usage else None) or math.inf for a in side}
    by_rem = [a for a in side if rem[a.name] is not None]
    by_score = [a for a in side if rem[a.name] is None and a.score() is not None]
    out = sorted(by_rem, key=lambda a: (-rem[a.name], reset[a.name], a.name))
    out += sorted(by_score, key=lambda a: (a.score(), reset[a.name], a.name))
    if not readable:
        out += sorted((a for a in side if a not in by_rem and a not in by_score), key=lambda a: a.name)
    return out


def _account_pool(exclude, keep, now: float) -> tuple[list[Account], tuple[str, float] | None]:
    """上限に達していない候補と、上限にあるもののうち最も早く戻るアカウントと時刻。「再登録が要る」と認証の失敗の
    観測があるものは外す。`keep` の名前はトークンを更新しない（動いている claude のアカウント。#1576 の I8）。"""
    pool, earliest = [], None
    for n in [n for n in names() if n not in exclude]:
        usage(n, n not in keep, now)
        acc = load_account(n)
        if acc is None or acc.needs_relogin or acc.auth_held(now):
            continue
        until = acc.limited_until(now)
        if until is None:
            pool.append(acc)
        elif earliest is None or until < earliest[1]:
            earliest = (n, until)
    return pool, earliest


def choose(exclude=(), keep=(), now: float | None = None) -> Choice:
    """上限に達していないアカウントのうち、残りの量の最も大きいものを選ぶ（#1453 の I3）。

    使用率が切り替えの閾値未満の候補を先に試し、残りの量の分からない候補は分かる候補の後ろへ使用率の順で並べる。
    残量不明は、上限に達していない候補に読めるものが無いときだけ候補にする（名前の順）。「再登録が要る」・認証の失敗の
    観測があるもの・起動できないもの（`usable`）は外す。`keep` の名前はトークンを更新しない（#1576 の I8）。"""
    now = _now(now)
    pool, earliest = _account_pool(exclude, keep, now)
    readable = any(a.known() for a in pool)
    for pick in _try_order(pool, readable):
        if usable(pick.name, now):
            return Choice(pick.name, pick.score(), earliest, pick.remaining())
    return Choice(None, None, earliest)


def choose_env(exclude=(), keep=(), base=None, note=None) -> tuple[Choice, dict | None]:
    """候補を選んでその環境を組み立てる。用意できない候補は除いて選び直す。(最後に選んだ結果, 環境か None)。"""
    tried = set(exclude)
    while True:
        c = choose(exclude=tried, keep=keep)
        env = account_env(c.name, dict(os.environ) if base is None else base, note) if c.name else None
        if env is not None or not c.name:
            return c, env
        tried.add(c.name)


# ---------------------------------------------------------------- 子の環境と従量の接続


def fallback_env(environ=None) -> dict:
    """従量の接続の宣言（子へ足す変数の組）。宣言が無ければ空。

    環境変数 `NDF_SUPERVISE_CLAUDE_FALLBACK`（`KEY=VALUE` を空白区切り）が定義されていれば（空でも）それだけを読み、
    無ければ置き場の `metered.json`（保存した宣言）を読む（#1468 の I4）。保存先が壊れていれば空（I5）。"""
    environ = environ if environ is not None else os.environ
    if FALLBACK_ENV not in environ:
        d = load_metered()
        return dict(d.env) if d else {}
    out = {}
    for tok in shlex.split(environ.get(FALLBACK_ENV, "")):
        k, sep, v = tok.partition("=")
        if sep and k:
            out[k] = v
    return out


@dataclass
class Prepared:
    """アカウントの設定ディレクトリの用意の結果（`prepare_account`）。`ok` が偽なら起動しない（理由は `reason`）。

    `added` は足した（付け替えた）symlink の数、`skipped` は同じ名前の実体があって飛ばした項目、`local_only` は共有側に
    無くアカウント側にだけある実体、`sync` は `.claude.json` の同期の結果（できたら None）。"""

    ok: bool
    reason: str | None = None
    added: int = 0
    skipped: list = field(default_factory=list)
    local_only: list = field(default_factory=list)
    sync: str | None = None

    def worth_noting(self) -> bool:
        return not self.ok or bool(self.added or self.skipped or self.local_only or self.sync)

    def row(self, name: str) -> dict:
        """記録の 1 行の中身（項目の名前と理由の語だけ。値とパスを載せない。I14）。"""
        return {
            "account": name,
            "ok": self.ok,
            "reason": self.reason,
            "added": self.added,
            "skipped": list(self.skipped),
            "local_only": list(self.local_only),
            "sync": self.sync,
        }


# `Prepared.reason` の画面の 1 行の理由の文
PREPARE_REASONS = {
    "needs_relogin": "再登録が要る",
    "no_credentials": "認証ファイルが無いか読めない",
    "account_unreadable": "アカウント側の .claude.json が壊れている。退避から戻すか、ファイルを消す",
    "identity_mismatch": "認証ファイルが別のアカウントのものになっている。account add {name} で登録し直す",
    "projects_not_shared": "会話の記録の置き場 projects が共有の設定ディレクトリを指していない",
    "lock_timeout": "アカウントの排他を取れない",
    "io_error": "設定ディレクトリを用意できない",
}


def prepare_reason_text(name: str, reason: str | None) -> str:
    return PREPARE_REASONS.get(reason or "", reason or "").format(name=name)


def _identity_mismatch(a: dict, ident: tuple[str, str] | None) -> bool:
    """アカウント側の `oauthAccount` の識別が `account.json` と食い違うか（無ければ通す。#1576 の I13）。"""
    if ident is None:
        return False
    email, org = ident
    if str(a.get("email") or "").lower() != email.lower():
        return True
    mine = str(a.get("org_id") or "")
    return bool(mine and org and mine != org)


def _prepare_held(name: str, base: dict) -> Prepared:
    _sweep_old_locks()
    a = _read(_path(name, ACCOUNT_FILE))
    if a is None or a.get("needs_relogin"):
        return Prepared(False, "needs_relogin")
    if not _usable_held(name, time.time()):
        return Prepared(False, "no_credentials")
    d = account_dir(name)
    if not account_files.readable(d):
        return Prepared(False, "account_unreadable")
    if _identity_mismatch(a, account_files.identity(d)):
        _update_account(name, needs_relogin=True)
        return Prepared(False, "identity_mismatch")
    links = account_files.link_shared(shared_dir(base), d)
    got = Prepared(links.projects_shared, None, links.added, links.skipped, links.local_only)
    if not links.projects_shared:
        got.reason = "projects_not_shared"
        return got
    got.sync = account_files.sync_config(shared_config_file(base), d)
    if got.sync == "account_unreadable":
        got.ok, got.reason = False, "account_unreadable"
    _secure(name)
    return got


def prepare_account(name: str, base: dict) -> Prepared:
    """アカウントの排他の中で、アカウントの設定ディレクトリを用意する（E2・E3）。例外を出さず、結果で返す。

    古い形の排他ファイルの掃除 → 使えるかの確かめ → `.claude.json` が読めるかの確かめ → 識別の照合 → 共有の項目への
    symlink → `.claude.json` の共有する設定の部分の同期の順。共有の設定ディレクトリは `base` から求める。"""
    try:
        with _locked(name):
            return _prepare_held(name, base)
    except lock_timeout():
        return Prepared(False, "lock_timeout")
    except OSError:
        return Prepared(False, "io_error")


def settle(name: str | None, base: dict | None = None, note=None) -> None:
    """セッションの終わりに、アカウント側の `.claude.json` の共有する設定の部分を共有側へ書き戻す（E12）。

    例外を出さない。同期できなければ `note`（記録の 1 行を受ける関数）へ渡す。"""
    if not name or name == METERED or not valid_name(name) or not os.path.isfile(_path(name, ACCOUNT_FILE)):
        return
    base = dict(os.environ) if base is None else base
    try:
        with _locked(name):
            sync = account_files.sync_config(shared_config_file(base), account_dir(name))
    except lock_timeout():
        sync = "lock_busy"
    except OSError:
        sync = "write_failed"
    if sync and note:
        note(Prepared(True, sync=sync).row(name))


def detach(name: str) -> None:
    """登録を外す前に、アカウントの設定ディレクトリの symlink を外す（参照先は消さない。I5）。"""
    with _locked(name):
        account_files.unlink_shared(account_dir(name))


def account_env(name: str, base: dict, note=None) -> dict | None:
    """`base` にアカウント `name`（か `metered`）の環境を重ねる。使えない・用意できないときは None（理由は `note` へ）。

    登録済みアカウントは、アカウントの設定ディレクトリを用意してから `CLAUDE_CONFIG_DIR` をそこへ向け、トークンと
    スコープの変数を外す（#1576 の I1）。認証の優先順位でトークンより上に来る変数（`FOREIGN_AUTH_ENV`）と、環境変数の
    宣言のときと、`base` が従量の接続の環境（`NDF_CLAUDE_ACCOUNT=metered`）のときだけ宣言のキーを外す（混ぜない。
    #1389 の I16。それ以外の保存した宣言では利用者のシェルの変数を残すため外さない）。従量の接続はトークン・スコープの
    変数と `FOREIGN_AUTH_ENV` を外し、`CLAUDE_CONFIG_DIR` を元の値へ戻してから宣言の変数を重ねる（#1576 の I2）。"""
    declared = fallback_env(base)
    if name == METERED:
        return _metered_env(dict(base), declared, FALLBACK_ENV not in base)
    got = prepare_account(name, base)
    if note is not None and got.worth_noting():
        note(got.row(name))
    if not got.ok:
        return None
    strip = FALLBACK_ENV in base or base.get(NAME_ENV) == METERED
    return _account_env(dict(base), declared if strip else {}, name)


def _metered_env(env: dict, declared: dict, saved: bool) -> dict:
    """従量の接続の環境。`saved`（保存した宣言）なら AWS の鍵も外す（呼べるかの確認と同じ環境にする。#1468 の決定 14）。

    アカウントの子の中（`NDF_SHARED_CONFIG_DIR` がある）なら、`CLAUDE_CONFIG_DIR` を元の値へ戻し（元に無ければ外し）、
    NDF が足したプラグインの置き場の変数を外す。"""
    for k in STRIPPED_AUTH_ENV + (AWS_KEY_ENV if saved else ()):
        env.pop(k, None)
    if SHARED_ENV in env:
        shared = shared_dir(env)
        orig = env.pop(SHARED_ENV)
        if orig:
            env[CONFIG_ENV] = orig
        else:
            env.pop(CONFIG_ENV, None)
        if env.get(PLUGIN_CACHE_ENV) == os.path.join(shared, "plugins"):
            env.pop(PLUGIN_CACHE_ENV)
    env.update(declared)
    env[NAME_ENV] = METERED
    return env


def _account_env(env: dict, declared: dict, name: str) -> dict:
    """アカウントの環境（宣言のキー・FOREIGN_AUTH_ENV・トークンとスコープの変数を外し、設定ディレクトリと名前を足す）。"""
    shared, orig = shared_dir(env), _original_config(env)
    for k in tuple(declared) + STRIPPED_AUTH_ENV:
        env.pop(k, None)
    env[CONFIG_ENV] = os.path.abspath(account_dir(name))
    env[NAME_ENV] = name
    env[SHARED_ENV] = orig
    env.setdefault(PLUGIN_CACHE_ENV, os.path.join(shared, "plugins"))
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


# 登録し直しで staging から置き場へ移すもの（この順。`account.json` を最後にする。#1576 の決定 20）
REPLACED_ON_RELOGIN = (CRED_FILE, account_files.CONFIG_FILE, ACCOUNT_FILE)


def _replace_on_relogin(staging: str, final: str) -> None:
    """登録し直しで、置き場 `final` の認証のファイルだけを `staging` のものへ置き換える。アカウントの排他の中で呼ぶ。"""
    for f in (USAGE_FILE, account_files.BASE_FILE):
        with suppress(FileNotFoundError):
            os.remove(os.path.join(final, f))
    for f in REPLACED_ON_RELOGIN:
        src = os.path.join(staging, f)
        if os.path.exists(src):
            os.replace(src, os.path.join(final, f))
        elif f == account_files.CONFIG_FILE:  # 前の oauthAccount を残さない（I13）
            with suppress(FileNotFoundError):
                os.remove(os.path.join(final, f))
    shutil.rmtree(staging, ignore_errors=True)


def register(name: str, staging: str, email: str, org_id: str = "", org_name: str = "") -> None:
    """ログインの済んだ `staging` を `name` として置く。

    同じ名前の置き場があるときは丸ごと消さない。`usage.json` と同期の控えを消し、staging の `.credentials.json`・
    `.claude.json`・`account.json` だけを置き換えで移して、staging の残りを捨てる。ほかの項目（共有の項目への symlink・
    claude がアカウント側に作った実体・`backups`）には触れない（#1576 の I5・決定 20）。"""
    with _locked(name):
        final = account_dir(name)
        row = {"name": name, "email": email, "registered_at": iso_utc(time.time()), "needs_relogin": False, "limit": None}
        if org_id:
            row.update(org_id=org_id, org_name=org_name)
        _write(os.path.join(staging, ACCOUNT_FILE), row)
        if not os.path.isdir(final) or os.path.islink(final):
            if os.path.lexists(final):
                os.remove(final)
            os.replace(staging, final)
        else:
            _replace_on_relogin(staging, final)
        _secure(name)


def set_org(name: str, org_id: str, org_name: str) -> None:
    """組織を記録する前の登録へ、読み直した組織を書き戻す。"""
    with _locked(name):
        _update_account(name, org_id=org_id, org_name=org_name)


def set_capacity(name: str, declared: dict) -> Account | None:
    """枠の大きさの宣言を書く。`declared` は枠ごとに正の数（宣言する）か None（外して対応表へ戻す）。

    登録されていなければ None。書くのは `account.json` だけである（I8）。"""
    with _locked(name):
        path = _path(name, ACCOUNT_FILE)
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
    """一覧の中身（推論は呼ばない）。期限の過ぎたトークンだけを、更新の排他を取れたときに更新する（I8・I9）。"""
    now = _now(now)
    out = []
    for n in names():
        usage(n, True, now)
        acc = load_account(n)
        if acc is not None:
            out.append(_account_row(n, acc, now))
    return out


def _account_row(name: str, acc: Account, now: float) -> dict:
    """一覧の 1 アカウント分の行。残量を読めていなければ枠と支出の鍵は None。"""
    u = acc.usage if acc.known() else None
    return {
        "name": name,
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


# ---------------------------------------------------------------- 保存した従量の接続の宣言（#1468）


@dataclass
class MeteredDecl:
    """保存した従量の接続の宣言。`env` は子へ足す変数の組、`details` は一覧に出す提供元ごとの識別。"""

    provider: str
    env: dict
    details: dict
    verified_at: str


def metered_path() -> str:
    return os.path.join(store_dir(), METERED_FILE)


def _check_decl(provider, env, details) -> None:
    """宣言の形と I1（資格情報の変数を含まない）を確かめる。違えば ValueError。"""
    if not isinstance(provider, str) or not provider:
        raise ValueError("提供元が無い")
    for m in (env, details):
        if not isinstance(m, dict) or not m or not all(isinstance(k, str) and isinstance(v, str) for k, v in m.items()):
            raise ValueError("変数の組の形が違う")
    bad = [k for k in env if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", k)]
    if bad or any(k in SECRET_ENV for k in env):
        raise ValueError("資格情報の変数か不正な名前を含む")


def _metered_read() -> tuple[MeteredDecl | None, str | None]:
    """(保存した宣言, 壊れているときの理由)。保存先が無ければ (None, None)。"""
    path = metered_path()
    if not os.path.lexists(path):
        return None, None
    d = _read(path)
    if d is None:
        return None, "JSON として読めない"
    if d.get("version") != 1:
        return None, "知らない版"
    try:
        _check_decl(d.get("provider"), d.get("env"), d.get("details"))
    except ValueError as e:
        return None, str(e)
    return MeteredDecl(d["provider"], dict(d["env"]), dict(d["details"]), str(d.get("verified_at") or "")), None


def load_metered() -> MeteredDecl | None:
    return _metered_read()[0]


def metered_problem(environ=None) -> str | None:
    """保存先が壊れていれば、その 1 行（宣言なしとして扱う。I5）。環境変数の宣言が効いているときは None。"""
    if FALLBACK_ENV in (environ if environ is not None else os.environ):
        return None
    why = _metered_read()[1]
    return None if why is None else f"保存した従量の接続の宣言（{metered_path()}）が壊れている（{why}）。宣言なしとして扱う"


def save_metered(provider: str, env: dict, details: dict, now: float | None = None) -> None:
    """呼べるかの確認が通った宣言を保存する（置き換え。I2・I6）。資格情報の変数を含めば ValueError（I1）。"""
    _check_decl(provider, env, details)
    make_store()
    row = {"version": 1, "provider": provider, "env": env, "details": details, "verified_at": iso_utc(time.time() if now is None else now)}
    _write(metered_path(), row)


def remove_metered() -> bool:
    """保存した宣言を消す。無ければ偽。"""
    try:
        os.remove(metered_path())
    except FileNotFoundError:
        return False
    return True


# ---------------------------------------------------------------- 登録の途中の状態（#1468）


@dataclass
class Pending:
    """OAuth の 2 回の登録の間に置き場へ残す状態（`.pending-<名前>/`）。"""

    name: str
    pid: int
    pid_start: float
    expires_at: float

    def alive(self) -> bool:
        """待機中のログインが生きているか（pid の開始の時刻が記録と一致し、ゾンビでない）。"""
        import procs

        return procs.start_time(self.pid) == self.pid_start

    def expired(self, now: float) -> bool:
        return now >= self.expires_at


def pending_dir(name: str) -> str:
    return os.path.join(store_dir(), f".pending-{name}")


def make_pending(name: str) -> str:
    """登録の途中の状態を作り直す（前の待機中のログインは止める）。`config/`（0700）・`code.fifo`・`login.out`（0600）を置く。"""
    discard_pending(name)
    make_store()
    d = pending_dir(name)
    os.mkdir(d, 0o700)
    os.mkdir(os.path.join(d, PENDING_CONFIG), 0o700)
    os.mkfifo(os.path.join(d, PENDING_FIFO), 0o600)
    os.close(os.open(os.path.join(d, PENDING_OUT), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    return d


def save_pending(name: str, pid: int, now: float | None = None) -> Pending:
    """待機中のログインの識別と期限を `pending.json` へ書く。"""
    now = _now(now)
    import procs

    p = Pending(name, pid, procs.start_time(pid) or 0.0, now + PENDING_TTL)
    row = {
        "version": 1,
        "name": name,
        "pid": pid,
        "pid_start": p.pid_start,
        "created_at": iso_utc(now),
        "expires_at": iso_utc(p.expires_at),
    }
    _write(os.path.join(pending_dir(name), PENDING_FILE), row)
    return p


def read_pending(name: str) -> Pending | None:
    d = _read(os.path.join(pending_dir(name), PENDING_FILE))
    if d is None or not isinstance(d.get("pid"), int) or not isinstance(d.get("pid_start"), (int, float)):
        return None
    return Pending(name, d["pid"], float(d["pid_start"]), epoch(d.get("expires_at")) or 0.0)


def discard_pending(name: str) -> None:
    """待機中のログインを止め（SIGTERM → 猶予の後に SIGKILL）、登録の途中の状態を消す。"""
    import procs

    p = read_pending(name)
    if p is not None and p.alive():
        procs.stop_tree(p.pid)
    shutil.rmtree(pending_dir(name), ignore_errors=True)


def sweep_pending(now: float | None = None) -> set[str]:
    """期限を過ぎた登録の途中の状態を捨て、捨てた名前の集合を返す（E10）。"""
    now = _now(now)
    gone = set()
    try:
        entries = os.listdir(store_dir())
    except OSError:
        return gone
    for e in entries:
        name = e.removeprefix(".pending-")
        if name == e:
            continue
        p = read_pending(name)
        try:
            stale = p.expired(now) if p else now - os.stat(pending_dir(name)).st_mtime > PENDING_TTL
        except OSError:
            continue
        if stale:
            discard_pending(name)
            gone.add(name)
    return gone
