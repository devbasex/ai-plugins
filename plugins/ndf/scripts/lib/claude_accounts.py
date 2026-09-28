"""登録済みの claude アカウント: 置き場・トークンの更新・使用量・選び方・子の環境（#1389）。

ラッパー（`relay_lib/`）と `supervise.py` が同じこの部品を使う。置き場のファイルを書くのはこのモジュールだけである
（`account add` の中で claude 自身が書く `.credentials.json` と `.claude.json` を除く）。

```text
${NDF_ACCOUNTS_DIR:-${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/accounts}/   0700
├── work1/              0700。claude の設定ディレクトリ（auth login の書き先）
│   ├── .credentials.json   0600。claude が書き、更新の後はここが書き戻す
│   ├── .claude.json        claude が書く
│   ├── account.json        0600。name・email・registered_at・needs_relogin・limit
│   └── usage.json          0600。最後の取得の結果（上書き）
└── work1.lock          アカウントごとの排他（lib/locks.py）
```

- 共有の設定ディレクトリの `.credentials.json` は読まず、書かない。子へは選んだアカウントのアクセストークンを
  環境変数 `CLAUDE_CODE_OAUTH_TOKEN` で渡す（引数に載せない）
- 使用量の取得先は 1 アカウントにつき `NDF_ACCOUNT_CHECK_INTERVAL` 秒（既定 300）に 1 回までしか呼ばない。
  数えるのは `usage.json` の `fetched_at`（成否を問わない）で、プロセス・コンテナをまたぐ
- 選び方・判定に LLM を呼ばない。呼ぶのは使用量の取得先とトークンの更新の宛先だけである
- 標準ライブラリと `clock`・`locks`（filelock。使う関数の中で import する）だけを読む
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import clock

NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
METERED = "metered"  # 従量の接続を表す予約の名前。登録できない
TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
NAME_ENV = "NDF_CLAUDE_ACCOUNT"
FALLBACK_ENV = "NDF_SUPERVISE_CLAUDE_FALLBACK"
ACCOUNT_FILE = "account.json"
USAGE_FILE = "usage.json"
CRED_FILE = ".credentials.json"
# 取得先と更新の宛先（Claude Code 2.1.283 の本体と同じ）。`NDF_ACCOUNT_USAGE_URL`・`NDF_ACCOUNT_TOKEN_URL` は試験用
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
REFRESH_BEFORE = 3600.0  # 期限のこの秒数前を切ったら更新する
HTTP_TIMEOUT = 10.0
LOCK_WAIT = 30.0
NO_RESET_HOLD = 5 * 3600.0  # リセット時刻の読めない上限の観測を候補から外す秒数
KINDS = ("five_hour", "seven_day", "spend", "unknown")
# claude の古い形の上限の文言（`Claude AI usage limit reached|<解除の UNIX 時刻>`）と、`resets 3pm (UTC)` の形
LIMIT_EPOCH = re.compile(r"usage limit reached\|(\d{9,11})", re.I)
LIMIT_RESETS = re.compile(r"resets?(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)?(?:\s*\(([^)]+)\))?", re.I)


# ---------------------------------------------------------------- 設定と置き場


def _num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def check_interval() -> float:
    """使用量の取得の最短の間隔（秒）。定期の確認の間隔も兼ねる。"""
    return _num("NDF_ACCOUNT_CHECK_INTERVAL", 300)


def switch_at() -> float:
    """切り替えの閾値（%）。100 以上なら閾値による切り替えをしない。"""
    return _num("NDF_ACCOUNT_SWITCH_AT", 90)


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


def _lock_timeout() -> type[BaseException]:
    import locks

    return locks.LockTimeout


def _iso(t: float | None) -> str | None:
    return None if t is None else datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def _epoch(s) -> float | None:
    d = clock.parse(s, naive="utc") if isinstance(s, str) else None
    return d.timestamp() if d is not None else None


# ---------------------------------------------------------------- 残量と上限の観測


@dataclass
class Usage:
    """最後に取得した残量。`five_hour`・`seven_day` は `{utilization, resets_at}`（空は読めなかった）。"""

    five_hour: dict | None = None
    seven_day: dict | None = None
    spend_limit_reached: bool | None = None
    fetched_at: float = 0.0
    error: str | None = None

    @classmethod
    def from_json(cls, d: dict | None) -> Usage | None:
        if not isinstance(d, dict):
            return None
        return cls(
            five_hour=d.get("five_hour") if isinstance(d.get("five_hour"), dict) else None,
            seven_day=d.get("seven_day") if isinstance(d.get("seven_day"), dict) else None,
            spend_limit_reached=d.get("spend_limit_reached") if isinstance(d.get("spend_limit_reached"), bool) else None,
            fetched_at=_epoch(d.get("fetched_at")) or 0.0,
            error=d.get("error") if isinstance(d.get("error"), str) else None,
        )

    def to_json(self) -> dict:
        return {
            "fetched_at": _iso(self.fetched_at),
            "five_hour": self.five_hour,
            "seven_day": self.seven_day,
            "spend_limit_reached": self.spend_limit_reached,
            "error": self.error,
        }

    def known(self) -> bool:
        return self.error is None and (self.five_hour is not None or self.seven_day is not None)

    def score(self) -> float | None:
        """`five_hour` と `seven_day` の使用率の大きい方。読めなければ None。"""
        if not self.known():
            return None
        vals = [w["utilization"] for w in (self.five_hour, self.seven_day) if w and isinstance(w.get("utilization"), (int, float))]
        return float(max(vals)) if vals else None

    def resets(self, key: str) -> float | None:
        w = getattr(self, key)
        return _epoch(w.get("resets_at")) if isinstance(w, dict) else None

    def limited_until(self, now: float) -> float | None:
        """上限にあるならそのリセット時刻（支出上限で時刻が無ければ無限）。無ければ None。"""
        if not self.known():
            return None
        if self.spend_limit_reached:
            return math.inf
        until = [
            self.resets(k) or math.inf
            for k in ("five_hour", "seven_day")
            if isinstance(getattr(self, k), dict) and (getattr(self, k).get("utilization") or 0) >= 100
        ]
        until = [t for t in until if t > now]
        return max(until) if until else None


@dataclass
class Account:
    name: str
    email: str
    needs_relogin: bool
    limit: dict | None
    usage: Usage | None

    def observed_until(self, now: float) -> float | None:
        """上限の観測（`limit`）が今も効いているなら、その終わりの時刻。"""
        lim = self.limit
        if not isinstance(lim, dict):
            return None
        observed = _epoch(lim.get("observed_at")) or now
        until = _epoch(lim.get("resets_at")) or observed + NO_RESET_HOLD
        if until <= now:
            return None
        u = self.usage
        if u is not None and u.known() and u.fetched_at > observed and u.limited_until(now) is None:
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
            if self.usage and self.usage.known() and self.usage.spend_limit_reached or (self.limit or {}).get("type") == "spend":
                return "支出上限"
            return f"上限（{local_time(until)}）"
        return "使える" if self.usage and self.usage.known() else "残量不明"


def local_time(t: float | None, form: str = "%m-%d %H:%M") -> str:
    if t is None or t == math.inf:
        return "不明"
    return datetime.fromtimestamp(t).astimezone().strftime(form)


def load(name: str) -> Account | None:
    d = account_dir(name)
    a = _read(os.path.join(d, ACCOUNT_FILE))
    if a is None:
        return None
    return Account(
        name=name,
        email=str(a.get("email") or ""),
        needs_relogin=bool(a.get("needs_relogin")),
        limit=a.get("limit") if isinstance(a.get("limit"), dict) else None,
        usage=Usage.from_json(_read(os.path.join(d, USAGE_FILE))),
    )


def _update_account(name: str, **fields) -> None:
    """排他の中で呼ぶ。登録の無いアカウントは作らない（I2）。"""
    path = os.path.join(account_dir(name), ACCOUNT_FILE)
    a = _read(path)
    if a is None:
        return
    a.update(fields)
    _write(path, a)


def note_limit(name: str, kind: str, resets_at: float | None, now: float | None = None) -> None:
    """アカウント `name` が上限（種類 `kind`）に達したと観測したことを残す。"""
    if not name or name == METERED:
        return
    now = time.time() if now is None else now
    try:
        with _locked(name):
            _update_account(name, limit={"type": kind, "resets_at": _iso(resets_at), "observed_at": _iso(now)})
    except (_lock_timeout(), OSError):
        pass


# ---------------------------------------------------------------- トークン


def _creds_path(name: str) -> str:
    return os.path.join(account_dir(name), CRED_FILE)


def _creds(name: str) -> dict | None:
    d = _read(_creds_path(name))
    o = d.get("claudeAiOauth") if d else None
    return o if isinstance(o, dict) and isinstance(o.get("accessToken"), str) and o["accessToken"] else None


def _http(req: urllib.request.Request, timeout: float) -> tuple[int, dict | None]:
    """(状態, JSON)。通信の失敗は (0, None)、JSON でなければ (状態, None)。"""
    req.add_header("User-Agent", "ndf-claude-accounts")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            status, body = f.status, f.read()
    except urllib.error.HTTPError as e:
        status, body = e.code, b""
    except (OSError, ValueError):
        return 0, None
    try:
        data = json.loads(body)
    except ValueError:
        return status, None
    return status, data if isinstance(data, dict) else None


def _refresh(o: dict, now: float) -> tuple[str, dict | None]:
    """リフレッシュトークンで更新する。("ok", 新しい claudeAiOauth) / ("rejected", None) / ("error", None)。"""
    body = {
        "grant_type": "refresh_token",
        "refresh_token": o.get("refreshToken") or "",
        "client_id": CLIENT_ID,
        "scope": " ".join(o.get("scopes") or []),
    }
    req = urllib.request.Request(
        os.environ.get("NDF_ACCOUNT_TOKEN_URL") or TOKEN_URL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    status, d = _http(req, 30)
    if status == 0 or status >= 500:
        return "error", None
    if status != 200 or not d or not isinstance(d.get("access_token"), str) or not isinstance(d.get("expires_in"), (int, float)):
        return "rejected", None
    new = dict(o, accessToken=d["access_token"], refreshToken=d.get("refresh_token") or o.get("refreshToken"))
    new["expiresAt"] = int((now + d["expires_in"]) * 1000)
    if isinstance(d.get("refresh_token_expires_in"), (int, float)):
        new["refreshTokenExpiresAt"] = int((now + d["refresh_token_expires_in"]) * 1000)
    if isinstance(d.get("scope"), str) and d["scope"]:
        new["scopes"] = d["scope"].split()
    return "ok", new


def _token_held(name: str, before: float | None, now: float, force: bool = False) -> str | None:
    """排他の中で呼ぶ。使えるアクセストークン（使えなければ None）。

    `before` は期限の何秒前を切ったら更新するか（None は更新しない。更新しないとき、期限を過ぎたものは None）。
    `force` は期限に関わらず更新する（取得先が 401 を返したとき）。更新を断られたら `needs_relogin` を真にする。"""
    a = _read(os.path.join(account_dir(name), ACCOUNT_FILE))
    if a is None or a.get("needs_relogin"):
        return None
    o = _creds(name)
    if not _secure(name) or o is None:
        _update_account(name, needs_relogin=True)
        return None
    exp = (o.get("expiresAt") or 0) / 1000
    if not force and (before is None or exp - now > before):
        return o["accessToken"] if exp > now else None
    rexp = o.get("refreshTokenExpiresAt")
    if isinstance(rexp, (int, float)) and rexp / 1000 <= now:
        _update_account(name, needs_relogin=True)
        return None
    how, new = _refresh(o, now)
    if how == "rejected":
        _update_account(name, needs_relogin=True)
        return None
    if new is None:  # 通信の失敗。期限の前なら今のトークンを使う
        return o["accessToken"] if exp > now and not force else None
    try:
        whole = _read(_creds_path(name)) or {}
        whole["claudeAiOauth"] = new
        _write(_creds_path(name), whole)
    except OSError:
        _update_account(name, needs_relogin=True)
        return None
    return new["accessToken"]


def token(name: str, before: float | None = REFRESH_BEFORE, now: float | None = None) -> str | None:
    """子へ渡すアクセストークン。期限の `before` 秒前を切っていれば更新する（None は更新しない）。"""
    now = time.time() if now is None else now
    try:
        with _locked(name):
            return _token_held(name, before, now)
    except (_lock_timeout(), OSError):
        return None


# ---------------------------------------------------------------- 使用量


def _parse_usage(d: dict | None, now: float) -> Usage:
    def window(key: str) -> dict | None:
        w = d.get(key)
        if w is None:
            return None
        if not isinstance(w, dict) or not isinstance(w.get("utilization"), (int, float)):
            raise ValueError(key)
        return {"utilization": float(w["utilization"]), "resets_at": w.get("resets_at") if isinstance(w.get("resets_at"), str) else None}

    try:
        if not isinstance(d, dict):
            raise ValueError("body")
        five, seven = window("five_hour"), window("seven_day")
        if five is None and seven is None:
            raise ValueError("windows")
    except ValueError:
        return Usage(fetched_at=now, error="shape")
    ex = d.get("extra_usage")
    spend = ex.get("spend_limit_reached") if isinstance(ex, dict) and isinstance(ex.get("spend_limit_reached"), bool) else None
    return Usage(five_hour=five, seven_day=seven, spend_limit_reached=spend, fetched_at=now)


def _fetch(name: str, before: float | None, now: float) -> Usage:
    """排他の中で呼ぶ。取得先を呼んで残量を読む（推論は呼ばない）。"""
    tok = _token_held(name, before, now)
    if tok is None:
        return Usage(fetched_at=now, error="token")
    o = _creds(name) or {}
    if "user:profile" not in (o.get("scopes") or ["user:profile"]):
        return Usage(fetched_at=now, error="scope")
    for attempt in range(2):
        req = urllib.request.Request(
            os.environ.get("NDF_ACCOUNT_USAGE_URL") or USAGE_URL,
            headers={"Authorization": f"Bearer {tok}", "anthropic-beta": "oauth-2025-04-20"},
        )
        status, d = _http(req, HTTP_TIMEOUT)
        if status == 401 and attempt == 0 and before is not None:
            tok = _token_held(name, before, now, force=True)
            if tok is None:
                return Usage(fetched_at=now, error="http-401")
            continue
        if status == 0:
            return Usage(fetched_at=now, error="network")
        if status != 200:
            return Usage(fetched_at=now, error=f"http-{status}")
        return _parse_usage(d, now)
    return Usage(fetched_at=now, error="http-401")


def usage(name: str, before: float | None = REFRESH_BEFORE, now: float | None = None) -> Usage | None:
    """残量。前の取得から `check_interval()` 秒の中なら保存した値を返し、取得先を呼ばない（I6）。"""
    now = time.time() if now is None else now
    path = os.path.join(account_dir(name), USAGE_FILE)
    try:
        with _locked(name):
            saved = Usage.from_json(_read(path))
            if saved is not None and now - saved.fetched_at < check_interval():
                return saved
            if _read(os.path.join(account_dir(name), ACCOUNT_FILE)) is None:
                return None
            u = _fetch(name, before, now)
            _write(path, u.to_json())
            return u
    except (_lock_timeout(), OSError):
        return Usage.from_json(_read(path))


# ---------------------------------------------------------------- 選び方


@dataclass
class Choice:
    """選んだアカウント（無ければ None）と、すべて上限のときに最も早く戻るアカウントと時刻。"""

    name: str | None
    score: float | None = None
    earliest: tuple[str, float] | None = None


def choose(exclude=(), before: float | None = REFRESH_BEFORE, keep=(), now: float | None = None) -> Choice:
    """上限に達していないアカウントのうち、使用率の大きい方が最も小さいものを選ぶ（前提 4・I7）。

    並んだら `five_hour` のリセット時刻が早い方、さらに並べば名前の順。残量の読めないアカウントは、読めるアカウントが
    1 つも無いときだけ候補にする（名前の順）。「再登録が要る」は候補にしない（I13）。トークンを得られないものは
    外して選び直す。`keep` の名前はトークンを更新しない（動いている区間のアカウント。I5）。"""
    now = time.time() if now is None else now
    earliest: tuple[str, float] | None = None
    pool: list[Account] = []
    readable = False  # 残量の読めるアカウントが 1 つでもあるか（上限のものを含む）
    for n in names():
        if n in exclude:
            continue
        acc = load(n)
        if acc is None or acc.needs_relogin:
            continue
        acc.usage = usage(n, None if n in keep else before, now) or acc.usage
        acc.needs_relogin = bool((load(n) or acc).needs_relogin)
        if acc.needs_relogin:
            continue
        readable |= bool(acc.usage and acc.usage.known())
        until = acc.limited_until(now)
        if until is not None:
            if earliest is None or until < earliest[1]:
                earliest = (n, until)
            continue
        pool.append(acc)
    while pool:
        known = [a for a in pool if a.usage is not None and a.usage.score() is not None]
        if known:
            pick = min(known, key=lambda a: (a.usage.score(), a.usage.resets("five_hour") or math.inf, a.name))
        elif readable:
            break
        else:
            pick = min(pool, key=lambda a: a.name)
        if token(pick.name, None if pick.name in keep else before, now) is not None:
            return Choice(pick.name, pick.usage.score() if pick.usage else None, earliest)
        pool.remove(pick)
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


def env_for(name: str, base: dict, before: float | None = REFRESH_BEFORE) -> dict | None:
    """`base` にアカウント `name`（か `metered`）の環境を重ねる。トークンを得られなければ None。

    従量の接続は宣言の変数を足して `CLAUDE_CODE_OAUTH_TOKEN` を外す。アカウントは宣言のキーを外してから
    トークンと名前を足す（混ぜない。I16）。"""
    env = dict(base)
    declared = fallback_env(base)
    if name == METERED:
        env.pop(TOKEN_ENV, None)
        env.update(declared)
        env[NAME_ENV] = METERED
        return env
    tok = token(name, before)
    if tok is None:
        return None
    for k in declared:
        env.pop(k, None)
    env[TOKEN_ENV] = tok
    env[NAME_ENV] = name
    return env


def label(name: str | None) -> str:
    """画面の 1 行に出す識別（`名前（メール）`）。トークンは含めない。"""
    if not name:
        return "既定のログイン"
    if name == METERED:
        return "従量の接続"
    acc = load(name)
    return f"{name}（{acc.email}）" if acc and acc.email else name


# ---------------------------------------------------------------- 上限の文言


def kind_of_text(text: str) -> str:
    """上限の文言の種類（`five_hour`・`seven_day`・`spend`・`unknown`）。"""
    t = (text or "").lower()
    if "spend limit" in t or "spending limit" in t:
        return "spend"
    if "session limit" in t or "5-hour" in t or "five_hour" in t:
        return "five_hour"
    if "weekly limit" in t or "seven_day" in t:
        return "seven_day"
    return "unknown"


def limit_reset_at(text: str, now: float | None = None) -> float | None:
    """上限の文言から解除の時刻（UNIX 時刻）を読む。読めなければ None。

    読む形: `usage limit reached|<UNIX 時刻>` と `resets 3pm (Asia/Tokyo)` / `resets at 15:30`。
    時刻だけの形は、今より後の最初のその時刻（時間帯が無ければ手元の時間帯）とする。
    """
    now = time.time() if now is None else now
    m = LIMIT_EPOCH.search(text or "")
    if m:
        return float(m.group(1))
    m = LIMIT_RESETS.search(text or "")
    if not m:
        return None
    hour, minute, ampm, zone = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower(), m.group(4)
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    try:
        tz = ZoneInfo(zone.strip()) if zone else None
    except (KeyError, ValueError):
        tz = None
    cur = datetime.fromtimestamp(now, tz) if tz else datetime.fromtimestamp(now).astimezone()
    at = cur.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if at.timestamp() <= now:
        at += timedelta(days=1)
    return at.timestamp()


# ---------------------------------------------------------------- 登録・削除（`relay.py account` が呼ぶ）


def staging_dir(name: str) -> str:
    """登録の途中の設定ディレクトリ（0700）。`names()` には現れない。"""
    make_store()
    d = os.path.join(store_dir(), f".add-{name}-{os.getpid()}")
    shutil.rmtree(d, ignore_errors=True)
    os.mkdir(d, 0o700)
    return d


def owner_of(email: str, other_than: str = "") -> str | None:
    """同じメールアドレスで登録済みのアカウントの名前（I3）。"""
    for n in names():
        acc = load(n)
        if n != other_than and acc and acc.email and acc.email.lower() == email.lower():
            return n
    return None


def register(name: str, staging: str, email: str) -> None:
    """ログインの済んだ `staging` を `name` として置く。同じ名前の古いものは置き換える。"""
    now = time.time()
    with _locked(name):
        final = account_dir(name)
        _write(os.path.join(staging, ACCOUNT_FILE), {"name": name, "email": email, "registered_at": _iso(now), "needs_relogin": False, "limit": None})
        if os.path.exists(final):
            shutil.rmtree(final)
        os.replace(staging, final)
        _secure(name)


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
        acc = load(n)
        if acc is None:
            continue
        u = acc.usage if acc.usage and acc.usage.known() else None
        out.append(
            {
                "name": n,
                "email": acc.email,
                "five_hour": u.five_hour if u else None,
                "seven_day": u.seven_day if u else None,
                "spend_limit_reached": u.spend_limit_reached if u else None,
                "state": acc.state(now),
            }
        )
    return out
