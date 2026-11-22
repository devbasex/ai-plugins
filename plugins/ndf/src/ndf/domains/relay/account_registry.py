"""アカウントの登録の保存先の読み書き（`ndf relay account` の list・capacity・remove と、登録・従量の接続の基盤。#3569）。

保存先は旧いプラグインと同じ（`NDF_ACCOUNTS_DIR` → `<共有の設定ディレクトリ>/ndf/accounts`）で、形も同じに読み書きする。
旧い `relay.py account` と新しい `ndf relay account` が同じ登録を使い、同じ排他（`<保存先>/.locks/<名前>.lock`）で互いに待つ。

- `<名前>/account.json`（0600）: `name`・`email`・`registered_at`・`needs_relogin`・`limit`・任意の `org_id`・`org_name`・`capacity`
- `metered.json`（0600）: 保存した従量の接続の設定（`version`・`provider`・`env`・`details`・`verified_at`）
- `api-keys.json`（0600）: API キーの登録簿（`version`・`use`・`weekly_at`・`keys.<名前>`）。キーの値はこのファイルの外へ出さない

保存先のディレクトリは 0700、中のファイルは 0600 に保つ。キーの値・トークンは一覧と結果へ出さない。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, NamedTuple, Optional

from filelock import FileLock, Timeout

from ndf import identity
from ndf.adapters import training, training_cache
from ndf.contracts import facts, limits
from ndf.domains.relay import signals

NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
# 登録の世代の形（使用率のキャッシュのファイル名と記録のキーになる。`.`・`/` を含まない 1 つのパスの要素）。
# 登録は `secrets.token_hex(16)` を書く（決定の表の「登録の世代」）
GEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
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
REMOVING_DIR = ".removing"  # 外した登録の残りを消し終えるまで置く（`unregister`）
AWS_KEY_ENV = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")
# 提供元の選択の変数（子の環境の表の provider の行。親の環境から外す・実効の設定の確かめ・従量の接続の検証が同じ分類を使う）
PROVIDER_ENV = facts.ids("env", provider=True)
# 提供元の選択の値の読み方（Claude Code の真偽の解釈と同じ。大文字小文字を問わない）
_PROVIDER_TRUE = ("1", "true", "yes", "on")
_PROVIDER_FALSE = ("", "0", "false", "no", "off")
# 従量の接続で比べる変数の組（提供元の選択のほか）。各組は (秘密の変数, 秘密でない変数)
_AUTH_ENV = (
    ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", training_cache.CLAUDE_TOKEN_ENV),
    ("ANTHROPIC_PROFILE", "ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_ORGANIZATION_ID"),
)
_AWS_ENV = (
    ("AWS_BEARER_TOKEN_BEDROCK", *AWS_KEY_ENV, "ANTHROPIC_AWS_API_KEY"),
    (
        "AWS_PROFILE",
        "AWS_CONFIG_FILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "ANTHROPIC_AWS_WORKSPACE_ID",
        "CLAUDE_CODE_SKIP_BEDROCK_AUTH",
        "CLAUDE_CODE_SKIP_MANTLE_AUTH",
        "CLAUDE_CODE_SKIP_ANTHROPIC_AWS_AUTH",
    ),
)
_GOOGLE_ENV = (
    (),
    (
        "GOOGLE_APPLICATION_CREDENTIALS",
        "ANTHROPIC_VERTEX_PROJECT_ID",
        "ANTHROPIC_GOOGLE_CLOUD_PROJECT",
        "ANTHROPIC_GOOGLE_CLOUD_WORKSPACE_ID",
        "CLAUDE_CODE_SKIP_VERTEX_AUTH",
        "CLAUDE_CODE_SKIP_ANTHROPIC_GOOGLE_CLOUD_AUTH",
    ),
)
_FOUNDRY_ENV = (
    ("ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_AUTH_TOKEN"),
    ("ANTHROPIC_FOUNDRY_RESOURCE", "CLAUDE_CODE_SKIP_FOUNDRY_AUTH"),
)
_ENDPOINT_ENV = (
    (),
    (
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_BEDROCK_BASE_URL",
        "ANTHROPIC_BEDROCK_MANTLE_BASE_URL",
        "ANTHROPIC_AWS_BASE_URL",
        "ANTHROPIC_VERTEX_BASE_URL",
        "ANTHROPIC_GOOGLE_CLOUD_BASE_URL",
        "ANTHROPIC_FOUNDRY_BASE_URL",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "CLOUD_ML_REGION",
        "ANTHROPIC_GOOGLE_CLOUD_LOCATION",
    ),
)
# 要求に足すヘッダー（Authorization などの資格情報を含みうる）
_HEADER_ENV = (("ANTHROPIC_CUSTOM_HEADERS",), ())
_CHECKED_GROUPS = (_AUTH_ENV, _AWS_ENV, _GOOGLE_ENV, _FOUNDRY_ENV, _ENDPOINT_ENV, _HEADER_ENV)
# 従量の接続の子へ切り替える前に、期待値と実効値を比べる変数（提供元の選択・認証の値・資格情報とアカウントの選択・
# 接続先・要求に足すヘッダー）。比べた結果と理由の文に出すのは名前と出所だけ
METERED_CHECKED = (*PROVIDER_ENV, *(n for secret, plain in _CHECKED_GROUPS for n in (*secret, *plain)))
# 提供元の秘密の変数。保存した従量の接続の設定が持てず、保存した設定の子へ親の値を渡さない
SECRET_ENV = tuple(n for secret, _plain in _CHECKED_GROUPS for n in secret)
# ラッパーが子の環境で確定する変数（子の環境の表の child が set・drop・account-set の変数と、名前が NDF_ で始まる変数）。
# 保存した従量の接続の設定は持てない（`session_env.METERED_FIXED` と同じ規則）
_WRAPPER_CHILD = ("set", "drop", "account-set")
WINDOWS = ("five_hour", "seven_day")
USES = ("off", "always", "near-limit")
WEEKLY_AT_DEFAULT = 85.0
# 登録し直しで staging から保存先へ移すもの（この順。`account.json` を最後にする）
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
    generation: str = ""  # 登録の世代（`generation` と同じ読み方）


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


def shared_config_dir(environ: Mapping[str, str]) -> Path:
    """共有の設定ディレクトリ。`NDF_SHARED_CONFIG_DIR` があればその値（空なら `~/.claude`）、無ければ `CLAUDE_CONFIG_DIR`（空か無ければ `~/.claude`）。

    登録済みアカウントの子の中では `CLAUDE_CONFIG_DIR` がアカウントの設定ディレクトリを指し、元の値を `NDF_SHARED_CONFIG_DIR` が持つ。
    """
    if "NDF_SHARED_CONFIG_DIR" in environ:
        shared = environ["NDF_SHARED_CONFIG_DIR"]
        return Path(shared) if shared else Path.home() / ".claude"
    return signals.claude_config_root(environ)


def store_dir(environ: Mapping[str, str]) -> Path:
    """旧い保存先。`NDF_ACCOUNTS_DIR` → `<共有の設定ディレクトリ>/ndf/accounts`。

    `ndf` は旧い NDF のプラグイン名（入れ替えの後の名前 `identity.FINAL_NAME` と同じ。#1142 の決定 73）で、今の
    プラグイン名（`plugin.json` の `name`）から求めない。旧い `relay.py account` と登録・API キー・排他を共有するためである。
    """
    forced = environ.get("NDF_ACCOUNTS_DIR")
    if forced:
        return Path(forced)
    return shared_config_dir(environ) / identity.FINAL_NAME / "accounts"


def provider_on(value: Optional[str]) -> Optional[bool]:
    """提供元の選択の変数の値の真偽（Claude Code の解釈と同じ）。`1`・`true`・`yes`・`on` は真、無い・空・`0`・`false`・`no`・`off` は偽。

    大文字小文字と前後の空白を問わない。それ以外の値は解けないので None（提供元を 1 つに選べない設定として呼び手が外す）。
    """
    if value is None:
        return False
    v = value.strip().lower()
    if v in _PROVIDER_TRUE:
        return True
    if v in _PROVIDER_FALSE:
        return False
    return None


def account_dir(store: Path, name: str) -> Path:
    return store / name


def valid_name(name: str) -> bool:
    return bool(name) and bool(NAME_RE.fullmatch(name)) and name != METERED


def check_name(name: str) -> None:
    if not valid_name(name):
        raise AccountError(f"名前は英小文字・数字・- と _ の 32 字まで（{METERED} は使えない）。入力値は表示しない")


def names(store: Path) -> list[str]:
    """登録済みのアカウントの名前（名前の順）。保存先が無ければ空。"""
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
    """保存先とアカウントのディレクトリを 0700、中のファイルを 0600 へ直す。直せなければ `OSError`。"""
    d = account_dir(store, name)
    _tighten(store, 0o700)
    _tighten(d, 0o700)
    for p in d.iterdir():
        if p.is_file() and not p.is_symlink():
            _tighten(p, 0o600)


@contextmanager
def locked(store: Path, name: str, wait: Optional[float] = None) -> Iterator[None]:
    """アカウントごとの排他（`<保存先>/.locks/<名前>.lock`。旧い書き手と同じパス）。取れなければ `AccountLockTimeout`。"""
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


def generation(store: Path, name: str) -> str:
    """アカウントの登録の世代（`account.json` の `generation`）。登録が無ければ空文字、壊れていれば `AccountError`。

    世代を持たない旧い登録には `legacy_generation` を返す（登録簿ごとに違う値）。
    """
    path = account_dir(store, name) / ACCOUNT_FILE
    return _generation_of(_read(path), path)


def legacy_generation(store: Path, name: str) -> str:
    """世代を書く前の旧い登録の世代。登録簿の実際のパスと名前から決まり、状態の保存先を共有する別の登録簿の同じ名前と重ならない。"""
    where = f"{os.path.realpath(store)}\0{name}".encode()
    return "legacy-" + hashlib.sha256(where).hexdigest()[:32]


def _generation_of(a: Optional[dict[str, Any]], path: Path) -> str:
    """`account.json` の中身の世代。登録が無ければ空文字、`GEN_RE` の形でなければ `AccountError`（パスの要素に使うため）。"""
    if a is None:
        return ""
    got = a.get("generation")
    if got is None:  # 世代を書く前の旧い登録。登録簿ごとに区別できる値を割り当てる
        return legacy_generation(path.parent.parent, path.parent.name)
    if not isinstance(got, str) or not GEN_RE.fullmatch(got):
        raise AccountError(f"{path} の generation の形が違う")
    return got


def _declared(v: Any) -> dict[str, float]:
    """`capacity` のうち正の数のスロットだけ。"""
    if not isinstance(v, dict):
        return {}
    return {k: got for k in WINDOWS if (got := positive(v.get(k))) is not None}


def capacity_view(declared: Mapping[str, float]) -> dict[str, Optional[float]]:
    """スロットの設定を結果の形（`WINDOWS` のスロットごとに値か None）にする。"""
    return {k: declared.get(k) for k in WINDOWS}


def load(store: Path, name: str) -> Optional[Account]:
    """アカウントの `account.json`。無ければ None、壊れていれば `AccountError`。"""
    path = account_dir(store, name) / ACCOUNT_FILE
    a = _read(path)
    if a is None:
        return None
    return Account(
        name=name,
        email=str(a.get("email") or ""),  # noqa: NDF501 ファイル: メールアドレスを読めなかった旧い登録は空で並べる
        needs_relogin=bool(a.get("needs_relogin")),
        org_id=str(a.get("org_id") or ""),  # noqa: NDF501 ファイル: 組織を記録する前の旧い登録は組織を持たない
        org_name=str(a.get("org_name") or ""),  # noqa: NDF501 ファイル: 組織を記録する前の旧い登録は組織を持たない
        declared=_declared(a.get("capacity")),
        generation=_generation_of(a, path),
    )


def _replace_on_relogin(staging: Path, final: Path) -> None:
    """登録し直しで、保存先 `final` の認証のファイルだけを `staging` のものへ置き換える。ほかの項目には触れない。"""
    for f in (USAGE_FILE, BASE_FILE):
        (final / f).unlink(missing_ok=True)
    for f in REPLACED_ON_RELOGIN:
        if (staging / f).exists():
            os.replace(staging / f, final / f)
        elif f == CONFIG_FILE:  # 前の oauthAccount を残さない
            (final / f).unlink(missing_ok=True)
    shutil.rmtree(staging, ignore_errors=True)


def register(
    store: Path, name: str, staging: Path, who: Identity, now: float, settle: Optional[Callable[[Optional[str], str], None]] = None
) -> str:
    """ログインの済んだ `staging` を `name` として置き、新しい登録の世代を返す。同じ名前の保存先は丸ごと消さず、認証のファイルだけを置き換える。

    世代は登録のたびに新しく発行する（同じ名前の登録し直しでも、前の世代の記録を今の記録と読まない）。`settle` を渡すと、
    置いた後に同じ排他の中で `settle(前の世代, 新しい世代)` を呼ぶ（前の世代の記録の後始末。前の登録が無ければ前の世代は None）。
    """
    with locked(store, name):
        final = account_dir(store, name)
        try:
            old: Optional[str] = generation(store, name) if (final / ACCOUNT_FILE).is_file() else None
        except AccountError:  # 壊れた前の登録は世代が分からない。後始末を空文字の世代で打つ
            old = ""
        new = secrets.token_hex(16)
        row: dict[str, Any] = {
            "name": name,
            "email": who.email,
            "registered_at": iso_utc(now),
            "needs_relogin": False,
            "limit": None,
            "generation": new,
        }
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
        if settle is not None:
            settle(old, new)
    return new


def set_capacity(store: Path, name: str, five: Optional[float], seven: Optional[float]) -> dict[str, Any]:
    """スロットの大きさの設定を書く（None はそのスロットの設定を外す）。名前の形が違うか登録が無ければ `AccountError`。"""
    check_name(name)  # 保存先の外（絶対パス・..）のパスと排他を作らない
    with locked(store, name):
        path = account_dir(store, name) / ACCOUNT_FILE
        a = _read(path)
        if a is None:
            raise NotRegistered(f"登録されていない: {name}")
        cur = _declared(a.get("capacity"))
        for k, v in zip(WINDOWS, (five, seven), strict=True):
            if v is None:
                cur.pop(k, None)
            else:
                cur[k] = float(v)
        a["capacity"] = cur or None  # noqa: NDF501 ファイル: 設定が無くなれば旧いと同じく null を書く
        write(path, a)
    return {"name": name, "kind": "oauth", "capacity_declared": capacity_view(cur)}


def detach(store: Path, name: str) -> None:
    """アカウントの設定ディレクトリの直下の symlink（共有の項目へのもの）を外す。logout が共有の中身を消さないため。"""
    d = account_dir(store, name)
    if d.is_dir():
        for p in d.iterdir():
            if p.is_symlink():
                p.unlink()


def removing_dir(store: Path, name: str) -> Path:
    """外した登録の残りを消し終えるまで置く場所（`<保存先>/.removing/<名前>/<世代>`）。名前の形と重ならない。"""
    return store / REMOVING_DIR / name


def removing(store: Path, name: str) -> bool:
    """`name` の外した登録の残りが、消し終わらずに残っているか（`unregister` を打ち直すと続きを消す）。"""
    d = removing_dir(store, name)
    return d.is_dir() and any(d.iterdir())


def unregister(store: Path, name: str, settle: Optional[Callable[[str], None]] = None) -> None:
    """登録を外す。`settle` を渡すと、外した後に同じ排他の中で `settle(外した世代)` を呼ぶ（世代が読めなければ呼ばない）。

    登録を外す操作と残りのファイルの削除を分ける。まず登録のディレクトリを `removing_dir` の下へ世代の名前で移し
    （1 回の `os.replace` で一覧から消える）、その世代で `settle` を呼んでから中身を消す。中身が消え残れば `OSError` で止めるが、
    登録は外れたままで、残りは世代の名前で残る。残りの続きを消すのは `remove_account` の打ち直し（今の登録に触れない）。
    """
    with locked(store, name):
        _unregister(store, name, settle)


def _unregister(store: Path, name: str, settle: Optional[Callable[[str], None]]) -> None:
    d = account_dir(store, name)
    if d.exists() or d.is_symlink():
        try:
            gone: Optional[str] = generation(store, name) or None  # noqa: NDF501 ファイル: 世代の無い登録は後始末の世代を持たない
        except AccountError:  # 壊れた登録は世代が分からないので後始末をしない（世代の形でない名前で置く）
            gone = None
        pending = removing_dir(store, name)
        pending.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.replace(d, pending / (gone or f"-{secrets.token_hex(8)}"))
    _finish_removal(store, name, settle)


class Removal(NamedTuple):
    """`remove_account` の結果。"""

    leftovers: bool  # 前の削除の残りだけを消した（今の登録には触れていない）
    kept: bool  # 残りを消した後も、同じ名前の今の登録が残っている（残りの後に登録し直された）
    logged_out: Optional[bool] = None  # 今の登録を外したときのログアウトの成否（残りだけを消したときは None）


def remove_account(
    store: Path,
    name: str,
    release: Callable[[], bool],
    settle: Optional[Callable[[str], None]] = None,
    seen: Optional[bool] = None,
) -> Removal:
    """`account remove <名前>` の本体。すべてを登録（`register`）と同じ排他の中で行う。

    1. 前の削除の残り（`removing`）があれば、残った世代ごとの `settle` と削除だけをやり直して返す。今の登録には触れない
       （案内どおりの打ち直しで、間に登録し直された新しい認証情報・設定を消さない）。今の登録を外すのは、残りが無くなった後の削除
    2. 残りが無ければ今の登録を外す。排他を取ったまま `release()`（共有の項目の symlink を外してログアウトし、成否を返す）を呼び、
       そのまま `unregister` と同じ手順で外す。世代は排他の中で 1 度だけ決まり、ログアウトから登録を外すまでに登録し直されない

    排他を待つ前に残りを見ていれば、残りの後始末の呼び出しとして扱う。待つ間にほかの端末が残りを片付けても、2 へ進まずに返す
    （残りの後始末として打ち直した呼び出しが、残りの後に登録し直された今の登録を外さない）。
    呼び出し元がこの前にほかの排他（使用率の取得の排他）を待つときは、それを待つ前に見た `removing` を `seen` へ渡す。
    省けばここで見る。

    登録も残りも無ければ `NotRegistered`、消え残れば `OSError`（打ち直すと 1 から）。
    """
    check_name(name)
    if seen is None:
        seen = removing(store, name)
    with locked(store, name):
        if seen or removing(store, name):
            _finish_removal(store, name, settle)
            return Removal(leftovers=True, kept=(account_dir(store, name) / ACCOUNT_FILE).is_file())
        if not (account_dir(store, name) / ACCOUNT_FILE).is_file():
            raise NotRegistered(f"登録されていない: {name}")
        out = release()
        _unregister(store, name, settle)
        return Removal(leftovers=False, kept=False, logged_out=out)


def _finish_removal(store: Path, name: str, settle: Optional[Callable[[str], None]]) -> None:
    """`removing_dir` に残った世代ごとに `settle` を呼んで中身を消す。消え残れば `OSError`（打ち直すと続きから）。"""
    pending = removing_dir(store, name)
    if not pending.is_dir():
        return
    left: list[Path] = []
    for p in sorted(pending.iterdir()):
        if settle is not None and GEN_RE.fullmatch(p.name):
            settle(p.name)
        if p.is_symlink() or not p.is_dir():
            p.unlink(missing_ok=True)
        else:
            shutil.rmtree(p, ignore_errors=True)
        if p.exists() or p.is_symlink():
            left.append(p)
    if left:
        raise OSError(f"外した登録の残りを消せなかった（登録は外した。同じ削除を打ち直すと続きを消す）: {', '.join(map(str, left))}")
    pending.rmdir()
    with suppress(OSError):  # noqa: NDF402 ファイル: ほかの名前の残りがあれば親は残す
        pending.parent.rmdir()


# ---------------------------------------------------------------- 従量の接続の設定


def metered_path(store: Path) -> Path:
    return store / METERED_FILE


def _wrapper_fixed(name: str) -> bool:
    """ラッパーが子の環境で確定する変数か（`_WRAPPER_CHILD` の変数か、名前が `NDF_` で始まる変数）。"""
    return name.startswith("NDF_") or any(name in facts.ids("env", child=c) for c in _WRAPPER_CHILD)


def check_decl(provider: Any, env: Any, details: Any) -> None:
    """設定の形・資格情報の変数とラッパーが確定する変数を含まないこと・提供元を 1 つに選べることを確かめる。違えば `ValueError`。

    提供元は、`PROVIDER_ENV` のうち `provider_on` が真のものがちょうど 1 つで、ほかがどれも偽（解けない値が無い）こと。
    誤りの文には変数の名前だけを出し、値を出さない。
    """
    if not isinstance(provider, str) or not provider:
        raise ValueError("提供元が無い")
    for m in (env, details):
        if not isinstance(m, dict) or not m or not all(isinstance(k, str) and isinstance(v, str) for k, v in m.items()):
            raise ValueError("変数の組の形が違う")
    if any(not re.fullmatch(r"[A-Z_][A-Z0-9_]*", k) or k in SECRET_ENV for k in env):
        raise ValueError("資格情報の変数か不正な名前を含む")
    fixed = sorted(k for k in env if _wrapper_fixed(k))
    if fixed:
        raise ValueError(f"ラッパーが確定する変数を含む: {', '.join(fixed)}")
    on = [provider_on(env.get(k)) for k in PROVIDER_ENV]
    if on.count(True) != 1 or None in on:
        raise ValueError(f"提供元を 1 つに選べない（{'・'.join(PROVIDER_ENV)} のうち 1 つだけを有効にする）")


def load_metered(store: Path) -> tuple[Optional[MeteredDecl], Optional[str]]:
    """(保存した設定, 壊れているときの理由)。保存先が無ければ (None, None)。"""
    path = metered_path(store)
    if not path.exists():
        return None, None
    try:
        d = _read(path)
    except AccountError as exc:
        return None, str(exc)
    if d is None or d.get("version") != 1:
        return None, "知らないバージョン"
    try:
        check_decl(d.get("provider"), d.get("env"), d.get("details"))
    except ValueError as exc:
        return None, str(exc)
    return MeteredDecl(d["provider"], dict(d["env"]), dict(d["details"]), str(d.get("verified_at") or "")), None  # noqa: NDF501 ファイル: 確かめの時刻の無い設定も使える


def save_metered(store: Path, provider: str, env: dict[str, str], details: dict[str, str], now: float) -> None:
    check_decl(provider, env, details)
    make_store(store)
    write(metered_path(store), {"version": 1, "provider": provider, "env": env, "details": details, "verified_at": iso_utc(now)})


def declared_env(environ: Mapping[str, str]) -> Optional[str]:
    """環境変数の従量の接続の設定（定義されていれば空でもその値）。無ければ None。"""
    return environ.get("NDF_SUPERVISE_CLAUDE_FALLBACK")


def fallback_env(store: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """従量の接続の設定（子へ足す変数の組）。環境変数 `NDF_SUPERVISE_CLAUDE_FALLBACK` があればそれだけ、無ければ保存した設定。"""
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
    if not isinstance(data.get("keys", {}), dict):  # noqa: NDF502 ファイル: キーの無い登録簿は登録 0 本
        raise AccountError(f"API キーの登録簿の形が違う（{book_path(store)}）")
    data.setdefault("keys", {})
    return data


def key_names(store: Path) -> list[str]:
    """登録済み API キーの名前。登録簿が壊れていれば空。"""
    try:
        return sorted(read_book(store)["keys"])
    except AccountError:
        return []


def parse_number(v: str) -> Optional[float]:
    """引数の文字列を数にする。読めなければ None（範囲の判定と誤りの文言は呼び手が持つ）。"""
    try:
        return float(v)
    except ValueError:  # noqa: NDF401 利用者の入力: 数でない引数は、呼び手が範囲の外として誤りの文言で止める
        return None


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


# 一覧の使用率の引き方（登録済みアカウントの名前 → 今の世代を受け、`Usage` の並びを返す）
UsagesOf = Callable[[Mapping[str, str]], list[Any]]


def _oauth_rows(store: Path, pinned: Optional[str], usages: UsagesOf) -> tuple[list[dict[str, Any]], list[str]]:
    """OAuth で登録したアカウントの行と、壊れた登録の警告。

    `account.json` は 1 回だけ読み、その世代で `usages` を引く（行の本人と使用率の世代が食い違わない）。
    """
    loaded: list[tuple[str, Optional[Account]]] = []
    warnings: list[str] = []
    for n in names(store):
        try:
            acc = load(store, n)
        except AccountError as exc:
            warnings.append(str(exc))
            loaded.append((n, None))
            continue
        if acc is not None:
            loaded.append((n, acc))
    by_name = _usage_by_name(usages({n: acc.generation for n, acc in loaded if acc is not None}))
    rows: list[dict[str, Any]] = []
    for n, acc in loaded:
        if acc is None:
            rows.append({"name": n, "kind": "oauth", "state": "broken"})
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
                "capacity_declared": capacity_view(acc.declared),
                "state": _state(acc, u),
            }
        )
    return rows, warnings


def _api_key_rows(store: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """API キーの登録簿の行（キーの値を含まない）と、登録簿が壊れているときの警告。"""
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
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
                    "state": "使える" if usd else "月の額が未設定",
                }
            )
    return rows, warnings


def list_rows(store: Path, environ: Mapping[str, str], pinned: Optional[str], usages: UsagesOf) -> tuple[list[dict[str, Any]], list[str]]:
    """登録したアカウント・従量の接続・API キーを 1 件ずつ並べる（キーの値とトークンを含まない）。壊れた登録は警告へ。

    `usages` は、読めた登録の名前と世代を受けて使用率の行を返す呼び出し（壊れた登録は渡さない）。
    """
    oauth, oauth_warnings = _oauth_rows(store, pinned, usages)
    metered = _metered_rows(store, environ)
    keys, key_warnings = _api_key_rows(store)
    return oauth + metered + keys, oauth_warnings + key_warnings


def _metered_rows(store: Path, environ: Mapping[str, str]) -> list[dict[str, Any]]:
    if declared_env(environ) is not None:
        keys = sorted(fallback_env(store, environ))
        return [{"name": METERED, "kind": "metered", "source": "env", "keys": keys, "state": "環境変数の設定"}] if keys else []
    decl, why = load_metered(store)
    if decl is not None:
        return [
            {
                "name": METERED,
                "kind": "metered",
                "source": "saved",
                "provider": decl.provider,
                "details": decl.details,
                "state": "保存した設定",
            }
        ]
    if why is not None:
        return [{"name": METERED, "kind": "metered", "source": "saved", "state": "壊れている（設定なしとして扱う）"}]
    return []


def remove_metered(store: Path) -> bool:
    """保存した設定を消す。無ければ偽。"""
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
    """登録の途中の保存先を消す（無ければ何もしない）。"""
    if path.exists():
        shutil.rmtree(path)
