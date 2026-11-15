"""アカウントの登録の置き場の読み書き（`ndf relay account` の list・capacity・remove と、登録・従量の接続の土台。#3569）。

置き場は旧いプラグインと同じ（`NDF_ACCOUNTS_DIR` → `<共有の設定ディレクトリ>/ndf/accounts`）で、形も同じに読み書きする。
旧い `relay.py account` と新しい `ndf relay account` が同じ登録を使い、同じ排他（`<置き場>/.locks/<名前>.lock`）で互いに待つ。

- `<名前>/account.json`（0600）: `name`・`email`・`registered_at`・`needs_relogin`・`limit`・任意の `org_id`・`org_name`・`capacity`
- `metered.json`（0600）: 保存した従量の接続の宣言（`version`・`provider`・`env`・`details`・`verified_at`）
- `api-keys.json`（0600）: API キーの登録簿（`version`・`use`・`weekly_at`・`keys.<名前>`）。キーの値はこのファイルの外へ出さない

置き場のディレクトリは 0700、中のファイルは 0600 に保つ。キーの値・トークンは一覧と結果へ出さない。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, NamedTuple, Optional

from filelock import FileLock, Timeout

from ndf.adapters import training, training_cache
from ndf.contracts import limits
from ndf.domains.relay import signals

NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
METERED = "metered"  # 従量の接続を表す予約の名前。登録できない
ACCOUNT_FILE = "account.json"
USAGE_FILE = "usage.json"
CRED_FILE = training.CLAUDE_CREDENTIALS
CONFIG_FILE = training_cache.CLAUDE_STATE
BASE_FILE = ".ndf-shared-base.json"
METERED_FILE = "metered.json"
BOOK_FILE = "api-keys.json"
BOOK_LOCK = ".api-keys"  # アカウントの名前（先頭は英小文字か数字）と重ならない
LOCKS_DIR = ".locks"
AWS_KEY_ENV = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")
# 従量の接続の宣言に入れない資格情報の変数
SECRET_ENV = (*AWS_KEY_ENV, *training_cache.CLAUDE_FOREIGN_AUTH[:2], training_cache.CLAUDE_TOKEN_ENV)
WINDOWS = ("five_hour", "seven_day")
USES = ("off", "always", "near-limit")
WEEKLY_AT_DEFAULT = 85.0
# 登録し直しで staging から置き場へ移すもの（この順。`account.json` を最後にする）
REPLACED_ON_RELOGIN = (CRED_FILE, CONFIG_FILE, ACCOUNT_FILE)


class AccountError(ValueError):
    """名前の形が違う・登録が無い・登録のファイルが壊れている。"""


class NotRegistered(AccountError):
    """登録の無い名前。"""


class AccountLockTimeout(Exception):
    """アカウントの排他を待ちきれない（旧い書き手か別のセッションが持つ）。"""


class Account(NamedTuple):
    name: str
    email: str
    needs_relogin: bool
    org_id: str
    org_name: str
    declared: dict[str, float]


class Identity(NamedTuple):
    """ログインで確かめた本人（`claude auth status` のメールアドレス・組織）。"""

    email: str
    org_id: str
    org_name: str


class MeteredDecl(NamedTuple):
    provider: str
    env: dict[str, str]
    details: dict[str, str]
    verified_at: str


def iso_utc(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def store_dir(environ: Mapping[str, str], plugin: str) -> Path:
    """旧い置き場。`NDF_ACCOUNTS_DIR` → `<NDF_SHARED_CONFIG_DIR か CLAUDE_CONFIG_DIR か ~/.claude>/<プラグインの名前>/accounts`。"""
    forced = environ.get("NDF_ACCOUNTS_DIR")
    if forced:
        return Path(forced)
    if "NDF_SHARED_CONFIG_DIR" in environ:  # アカウントの子の中では元の CLAUDE_CONFIG_DIR を持つ（空なら ~/.claude）
        shared = environ["NDF_SHARED_CONFIG_DIR"]
        base = Path(shared) if shared else Path.home() / ".claude"
    else:
        base = signals.claude_config_root(environ)
    return base / plugin / "accounts"


def account_dir(store: Path, name: str) -> Path:
    return store / name


def valid_name(name: str) -> bool:
    return bool(name) and bool(NAME_RE.fullmatch(name)) and name != METERED


def check_name(name: str) -> None:
    if not valid_name(name):
        raise AccountError(f"名前は英小文字・数字・- と _ の 32 字まで（{METERED} は使えない）。入力値は表示しない")


def names(store: Path) -> list[str]:
    """登録済みのアカウントの名前（名前の順）。置き場が無ければ空。"""
    if not store.is_dir():
        return []
    return sorted(p.name for p in store.iterdir() if valid_name(p.name) and (p / ACCOUNT_FILE).is_file())


def _read(path: Path) -> Optional[dict[str, Any]]:
    """JSON の object。無ければ None、壊れていれば `AccountError`。"""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AccountError(f"{path} を読めない: {exc}") from exc
    if not isinstance(data, dict):
        raise AccountError(f"{path} が object でない")
    return data


def write(path: Path, data: Mapping[str, Any]) -> None:
    """0600 の一時ファイルへ書いてから置き換える。"""
    signals.replace_text(path, json.dumps(dict(data), ensure_ascii=False), mode=0o600)


def _tighten(path: Path, mode: int) -> None:
    if path.stat().st_mode & 0o777 & ~mode:
        path.chmod(mode)


def make_store(store: Path) -> Path:
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    _tighten(store, 0o700)
    return store


def secure(store: Path, name: str) -> None:
    """置き場とアカウントのディレクトリを 0700、中のファイルを 0600 へ直す。直せなければ `OSError`。"""
    d = account_dir(store, name)
    _tighten(store, 0o700)
    _tighten(d, 0o700)
    for p in d.iterdir():
        if p.is_file() and not p.is_symlink():
            _tighten(p, 0o600)


@contextmanager
def locked(store: Path, name: str, wait: Optional[float] = None) -> Iterator[None]:
    """アカウントごとの排他（`<置き場>/.locks/<名前>.lock`。旧い書き手と同じパス）。取れなければ `AccountLockTimeout`。"""
    d = make_store(store) / LOCKS_DIR
    d.mkdir(mode=0o700, exist_ok=True)
    lock = FileLock(str(d / f"{name}.lock"))
    timeout = limits.seconds("claude-accounts.lock") if wait is None else wait
    try:
        lock.acquire(timeout=timeout)
    except Timeout:
        raise AccountLockTimeout(
            f"{d / name}.lock の排他を {timeout:g} 秒で取れない（旧い relay.py か別のセッションが書いている）"
        ) from None
    try:
        yield
    finally:
        lock.release()


def _declared(v: Any) -> dict[str, float]:
    """`capacity` のうち正の数の枠だけ。"""
    if not isinstance(v, dict):
        return {}
    return {k: float(v[k]) for k in WINDOWS if isinstance(v.get(k), (int, float)) and not isinstance(v.get(k), bool) and v[k] > 0}


def load(store: Path, name: str) -> Optional[Account]:
    """アカウントの `account.json`。無ければ None、壊れていれば `AccountError`。"""
    a = _read(account_dir(store, name) / ACCOUNT_FILE)
    if a is None:
        return None
    return Account(
        name=name,
        email=str(a.get("email") or ""),  # noqa: NDF501 ファイル: メールアドレスを読めなかった旧い登録は空で並べる
        needs_relogin=bool(a.get("needs_relogin")),
        org_id=str(a.get("org_id") or ""),  # noqa: NDF501 ファイル: 組織を記録する前の旧い登録は組織を持たない
        org_name=str(a.get("org_name") or ""),  # noqa: NDF501 ファイル: 組織を記録する前の旧い登録は組織を持たない
        declared=_declared(a.get("capacity")),
    )


def _replace_on_relogin(staging: Path, final: Path) -> None:
    """登録し直しで、置き場 `final` の認証のファイルだけを `staging` のものへ置き換える。ほかの項目には触れない。"""
    for f in (USAGE_FILE, BASE_FILE):
        (final / f).unlink(missing_ok=True)
    for f in REPLACED_ON_RELOGIN:
        if (staging / f).exists():
            os.replace(staging / f, final / f)
        elif f == CONFIG_FILE:  # 前の oauthAccount を残さない
            (final / f).unlink(missing_ok=True)
    shutil.rmtree(staging, ignore_errors=True)


def register(store: Path, name: str, staging: Path, who: Identity, now: float) -> None:
    """ログインの済んだ `staging` を `name` として置く。同じ名前の置き場は丸ごと消さず、認証のファイルだけを置き換える。"""
    with locked(store, name):
        final = account_dir(store, name)
        row: dict[str, Any] = {"name": name, "email": who.email, "registered_at": iso_utc(now), "needs_relogin": False, "limit": None}
        if who.org_id:
            row.update(org_id=who.org_id, org_name=who.org_name)
        write(staging / ACCOUNT_FILE, row)
        if final.is_dir() and not final.is_symlink():
            _replace_on_relogin(staging, final)
        else:
            if final.is_symlink() or final.exists():
                final.unlink()
            os.replace(staging, final)
        secure(store, name)


def set_capacity(store: Path, name: str, five: Optional[float], seven: Optional[float]) -> dict[str, Any]:
    """枠の大きさの宣言を書く（None はその枠の宣言を外す）。登録が無ければ `AccountError`。"""
    with locked(store, name):
        path = account_dir(store, name) / ACCOUNT_FILE
        a = _read(path)
        if a is None:
            raise NotRegistered(f"登録されていない: {name}")
        cur = _declared(a.get("capacity"))
        for k, v in (("five_hour", five), ("seven_day", seven)):
            if v is None:
                cur.pop(k, None)
            else:
                cur[k] = float(v)
        a["capacity"] = cur or None  # noqa: NDF501 ファイル: 宣言が無くなれば旧いと同じく null を書く
        write(path, a)
    return {"name": name, "kind": "oauth", "capacity_declared": {k: cur.get(k) for k in WINDOWS}}


def detach(store: Path, name: str) -> None:
    """アカウントの設定ディレクトリの直下の symlink（共有の項目へのもの）を外す。logout が共有の中身を消さないため。"""
    d = account_dir(store, name)
    if d.is_dir():
        for p in d.iterdir():
            if p.is_symlink():
                p.unlink()


def unregister(store: Path, name: str) -> None:
    with locked(store, name):
        shutil.rmtree(account_dir(store, name), ignore_errors=True)


# ---------------------------------------------------------------- 従量の接続の宣言


def metered_path(store: Path) -> Path:
    return store / METERED_FILE


def check_decl(provider: Any, env: Any, details: Any) -> None:
    """宣言の形と、資格情報の変数を含まないことを確かめる。違えば `ValueError`。"""
    if not isinstance(provider, str) or not provider:
        raise ValueError("提供元が無い")
    for m in (env, details):
        if not isinstance(m, dict) or not m or not all(isinstance(k, str) and isinstance(v, str) for k, v in m.items()):
            raise ValueError("変数の組の形が違う")
    if any(not re.fullmatch(r"[A-Z_][A-Z0-9_]*", k) or k in SECRET_ENV for k in env):
        raise ValueError("資格情報の変数か不正な名前を含む")


def load_metered(store: Path) -> tuple[Optional[MeteredDecl], Optional[str]]:
    """(保存した宣言, 壊れているときの理由)。保存先が無ければ (None, None)。"""
    path = metered_path(store)
    if not path.exists():
        return None, None
    try:
        d = _read(path)
    except AccountError as exc:
        return None, str(exc)
    if d is None or d.get("version") != 1:
        return None, "知らない版"
    try:
        check_decl(d.get("provider"), d.get("env"), d.get("details"))
    except ValueError as exc:
        return None, str(exc)
    return MeteredDecl(d["provider"], dict(d["env"]), dict(d["details"]), str(d.get("verified_at") or "")), None  # noqa: NDF501 ファイル: 確かめの時刻の無い宣言も使える


def save_metered(store: Path, provider: str, env: dict[str, str], details: dict[str, str], now: float) -> None:
    check_decl(provider, env, details)
    make_store(store)
    write(metered_path(store), {"version": 1, "provider": provider, "env": env, "details": details, "verified_at": iso_utc(now)})


def declared_env(environ: Mapping[str, str]) -> Optional[str]:
    """環境変数の従量の接続の宣言（定義されていれば空でもその値）。無ければ None。"""
    return environ.get("NDF_SUPERVISE_CLAUDE_FALLBACK")


def fallback_env(store: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """従量の接続の宣言（子へ足す変数の組）。環境変数 `NDF_SUPERVISE_CLAUDE_FALLBACK` があればそれだけ、無ければ保存した宣言。"""
    raw = declared_env(environ)
    if raw is None:
        decl = load_metered(store)[0]
        return dict(decl.env) if decl else {}
    out = {}
    for tok in shlex.split(raw):
        k, sep, v = tok.partition("=")
        if sep and k:
            out[k] = v
    return out


# ---------------------------------------------------------------- API キーの登録簿


def book_path(store: Path) -> Path:
    return store / BOOK_FILE


def read_book(store: Path) -> dict[str, Any]:
    """登録簿の中身（無ければ空の形）。壊れていれば `AccountError`。"""
    data = _read(book_path(store))
    if data is None:
        return {"version": 1, "use": "off", "keys": {}}
    if not isinstance(data.get("keys", {}), dict):  # noqa: NDF502 ファイル: 鍵の無い登録簿は登録 0 本
        raise AccountError(f"API キーの登録簿の形が違う（{book_path(store)}）")
    data.setdefault("keys", {})
    return data


def key_names(store: Path) -> list[str]:
    """登録済み API キーの名前。登録簿が壊れていれば空。"""
    try:
        return sorted(read_book(store)["keys"])
    except AccountError:
        return []


def positive(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


# ---------------------------------------------------------------- 一覧と削除


def _usage_by_name(usages: list[Any]) -> dict[str, Any]:
    return {u.name: u for u in usages}


def _state(acc: Account, usage: Any) -> str:
    if acc.needs_relogin:
        return "再登録が要る"
    if usage is None:
        return "残量不明"
    return "上限" if usage.limited else "使える"


def list_rows(store: Path, environ: Mapping[str, str], pinned: Optional[str], usages: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """登録したアカウント・従量の接続・API キーを 1 件ずつ並べる（キーの値とトークンを含まない）。壊れた登録は警告へ。"""
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    by_name = _usage_by_name(usages)
    for n in names(store):
        try:
            acc = load(store, n)
        except AccountError as exc:
            warnings.append(str(exc))
            rows.append({"name": n, "kind": "oauth", "state": "broken"})
            continue
        if acc is None:
            continue
        u = by_name.get(n)
        rows.append(
            {
                "name": n,
                "kind": "oauth",
                "email": acc.email,
                "org_name": acc.org_name or None,  # noqa: NDF501 ファイル: 組織の無い登録は null で並べる
                "pinned": n == pinned,
                "percent": u.percent if u else None,
                "capacity_declared": {k: acc.declared.get(k) for k in WINDOWS},
                "state": _state(acc, u),
            }
        )
    rows += _metered_rows(store, environ)
    try:
        book = read_book(store)
    except AccountError as exc:
        warnings.append(f"{exc}（API キーを使わずに動く）")
        book = {"keys": {}}
    for k in sorted(book["keys"]):
        row = book["keys"][k]
        if isinstance(row, dict):
            usd = positive(row.get("monthly_usd"))
            rows.append(
                {
                    "name": k,
                    "kind": "api_key",
                    "account": row.get("account"),
                    "monthly_usd": usd,
                    "state": "使える" if usd else "月の額が未宣言",
                }
            )
    return rows, warnings


def _metered_rows(store: Path, environ: Mapping[str, str]) -> list[dict[str, Any]]:
    if declared_env(environ) is not None:
        keys = sorted(fallback_env(store, environ))
        return [{"name": METERED, "kind": "metered", "source": "env", "keys": keys, "state": "環境変数の宣言"}] if keys else []
    decl, why = load_metered(store)
    if decl is not None:
        return [
            {
                "name": METERED,
                "kind": "metered",
                "source": "saved",
                "provider": decl.provider,
                "details": decl.details,
                "state": "保存した宣言",
            }
        ]
    if why is not None:
        return [{"name": METERED, "kind": "metered", "source": "saved", "state": "壊れている（宣言なしとして扱う）"}]
    return []


def remove_metered(store: Path) -> bool:
    """保存した宣言を消す。無ければ偽。"""
    path = metered_path(store)
    if not path.exists():
        return False
    path.unlink()
    return True


def remove_key(store: Path, name: str) -> bool:
    with locked(store, BOOK_LOCK):
        book = read_book(store)
        if name not in book["keys"]:
            return False
        del book["keys"][name]
        write(book_path(store), book)
    return True


def discard(path: Path) -> None:
    """登録の途中の置き場を消す（無ければ何もしない）。"""
    if path.exists():
        shutil.rmtree(path)
