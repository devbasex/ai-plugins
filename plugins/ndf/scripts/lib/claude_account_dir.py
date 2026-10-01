"""アカウントの設定ディレクトリの共有物: 共有の項目への symlink と `.claude.json` の共有する設定の部分の同期（#1576）。

呼ぶのは `lib/claude_accounts.py` だけで、アカウントの排他の中で呼ぶ。置き場の決め方と NDF の排他は知らず、渡された
パスだけを扱う。アカウント固有の項目（symlink にしない項目）の一覧の正本はここに置く。

- symlink の参照先は共有の設定ディレクトリの直下の項目（`<共有>/<項目>`）だけにする（I4）
- 共有の設定ディレクトリの項目とアカウントの設定ディレクトリの実体を消さず、移さず、上書きしない（I5）
- `.claude.json` の同期は `projects` と `mcpServers` だけを共有側と突き合わせ、Claude Code 本体と同じ排他
  （`<実パス>.lock` のディレクトリ）の中で書く（I10・I11・I18）
- 値（`.claude.json` の中身・`userID`・参照先のパス）を返り値の理由と名前のほかに出さない（I14）
- 標準ライブラリだけを読む
"""

from __future__ import annotations

import json
import os
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field

# アカウント固有の項目（Claude Code 2.1.286 と NDF が書く）。前方一致は書き込みの一時ファイルと排他を含む
LOCAL_PREFIXES = (".credentials.json", ".claude.json", ".oauth_refresh.lock")
LOCAL_NAMES = (
    ".config.json",
    "backups",
    "policy-limits.json",
    "policy-limits.json.stamp.json",
    "remote-settings.json",
    "mcp-needs-auth-cache.json",
    ".ndf-statusline-auth.json",
    "account.json",
    "usage.json",
    ".ndf-shared-base.json",
)
CONFIG_FILE = ".claude.json"
LEGACY_CONFIG_FILE = ".config.json"  # `.claude.json` の古い名前（本体はあれば先に読む）
BASE_FILE = ".ndf-shared-base.json"
SHARED_KEYS = ("projects", "mcpServers")
ONBOARDING_KEYS = ("hasCompletedOnboarding", "lastOnboardingVersion")
LOCK_WAIT = 2.0  # `.claude.json` の排他の待ちの上限（秒）
LOCK_STALE = 10.0  # 本体と同じく、これより古い排他は残骸とみなす
_MISSING = object()


def is_local(item: str) -> bool:
    return item in LOCAL_NAMES or item.startswith(LOCAL_PREFIXES)


@dataclass
class Links:
    """`link_shared` の結果。`skipped` は同じ名前の実体があって飛ばした項目、`local_only` は共有側に無く
    アカウント側にだけある実体の名前。`projects_shared` はアカウント側の `projects` が共有側と同じ実体か。"""

    added: int = 0
    skipped: list[str] = field(default_factory=list)
    local_only: list[str] = field(default_factory=list)
    projects_shared: bool = False


def _ensure_shared(shared: str) -> None:
    """共有側に `projects` と `settings.json` が無ければ作る（無いまま起動すると claude がアカウント側に実体を作る）。"""
    os.makedirs(shared, exist_ok=True)
    p = os.path.join(shared, "projects")
    if not os.path.lexists(p):
        os.mkdir(p, 0o700)
    s = os.path.join(shared, "settings.json")
    if not os.path.lexists(s):
        fd = os.open(s, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("{}\n")


def link_shared(shared: str, account: str) -> Links:
    """共有の設定ディレクトリの直下の項目のうち、アカウント固有でないものをアカウント側の symlink にする。

    何も無ければ作り、参照先の違う symlink は付け替え、実体は飛ばす。OSError はそのまま上げる。"""
    shared = os.path.abspath(shared)
    _ensure_shared(shared)
    out = Links()
    for item in sorted(os.listdir(shared)):
        if is_local(item):
            continue
        target, p = os.path.join(shared, item), os.path.join(account, item)
        if not os.path.lexists(p):
            os.symlink(target, p)
            out.added += 1
        elif os.path.islink(p):
            if os.readlink(p) != target:
                tmp = f"{p}.{os.getpid()}.link"
                if os.path.lexists(tmp):
                    os.unlink(tmp)
                os.symlink(target, tmp)
                os.replace(tmp, p)
                out.added += 1
        else:
            out.skipped.append(item)
    for item in sorted(os.listdir(account)):
        p = os.path.join(account, item)
        if not is_local(item) and not os.path.islink(p) and not os.path.lexists(os.path.join(shared, item)):
            out.local_only.append(item)
    proj = os.path.join(account, "projects")
    out.projects_shared = os.path.isdir(proj) and os.path.realpath(proj) == os.path.realpath(os.path.join(shared, "projects"))
    return out


def unlink_shared(account: str) -> None:
    """アカウント側の直下の symlink を外す（参照先には触れない）。"""
    try:
        items = os.listdir(account)
    except FileNotFoundError:
        return
    for item in items:
        p = os.path.join(account, item)
        if os.path.islink(p) and not is_local(item):
            os.unlink(p)


# ---------------------------------------------------------------- .claude.json


def config_file(account: str) -> str:
    """アカウント側の `.claude.json` のパス（本体と同じく `.config.json` があればそれ）。"""
    legacy = os.path.join(account, LEGACY_CONFIG_FILE)
    return legacy if os.path.exists(legacy) else os.path.join(account, CONFIG_FILE)


def _read_config(path: str):
    """(ファイルがあるか, 中身の dict か読めなければ None)。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return False, {}
    except (OSError, ValueError):
        return True, None
    return True, data if isinstance(data, dict) else None


def _drop_base(account: str) -> None:
    try:
        os.remove(os.path.join(account, BASE_FILE))
    except FileNotFoundError:
        pass


def readable(account: str) -> bool:
    """アカウント側の `.claude.json` が JSON として読めるか（無いのは読める）。読めなければ同期の控えを消す（I18・I19）。"""
    if _read_config(config_file(account))[1] is None:
        _drop_base(account)
        return False
    return True


def identity(account: str) -> tuple[str, str] | None:
    """アカウント側の `oauthAccount` の (メールアドレス, 組織の識別)。無い・読めなければ None。"""
    o = (_read_config(config_file(account))[1] or {}).get("oauthAccount")
    if not isinstance(o, dict) or not isinstance(o.get("emailAddress"), str) or not o["emailAddress"]:
        return None
    org = o.get("organizationUuid")
    return o["emailAddress"], org if isinstance(org, str) else ""


@contextmanager
def _claude_lock(path: str):
    """本体と同じ `.claude.json` の排他（`<実パス>.lock` のディレクトリ）。取れなければ TimeoutError。"""
    lock = os.path.realpath(path) + ".lock"
    deadline = time.monotonic() + LOCK_WAIT
    while True:
        try:
            os.mkdir(lock)
            break
        except FileExistsError:
            try:
                if time.time() - os.stat(lock).st_mtime > LOCK_STALE:
                    os.rmdir(lock)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(lock) from None
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            os.rmdir(lock)
        except OSError:
            pass


def _leaves(key: str, value) -> dict:
    """`projects` はプロジェクトのパスと項目の 2 層、`mcpServers` は名前の 1 層の葉へ平らにする。"""
    out: dict = {}
    if not isinstance(value, dict):
        return out
    for k, v in value.items():
        if key == "projects" and isinstance(v, dict) and v:
            for k2, v2 in v.items():
                out[(k, k2)] = v2
        else:
            out[(k,)] = v
    return out


def _unflatten(leaves: dict) -> dict:
    out: dict = {}
    for path, v in sorted(leaves.items(), key=lambda kv: len(kv[0])):  # 1 層の葉を先に（空の項目を 2 層の葉で上書き）
        if len(path) == 1:
            out[path[0]] = v
        else:
            sub = out.get(path[0])
            if not isinstance(sub, dict):
                sub = out[path[0]] = {}
            sub[path[1]] = v
    return out


def _merge_leaves(s: dict, a: dict, b: dict) -> dict:
    """葉ごとに、片側だけで変わった値を採る。両側で変わったら共有側（I11）。"""
    out = {}
    for k in set(s) | set(a) | set(b):
        sv, av, bv = s.get(k, _MISSING), a.get(k, _MISSING), b.get(k, _MISSING)
        v = sv if sv != bv else av
        if v is not _MISSING:
            out[k] = v
    return out


def _write_json(path: str, data: dict, mode: int) -> None:
    """実パスの隣の一時ファイルから実パスへ置き換える（symlink と権限を保つ）。"""
    real = os.path.realpath(path)
    tmp = f"{real}.ndf-{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, mode)
    os.replace(tmp, real)


def sync_config(shared_file: str, account: str) -> str | None:
    """共有する設定の部分（`projects`・`mcpServers`）を、同期の控えと突き合わせて両側へ写す。

    同期できたら None、できなければ `shared_unreadable`・`account_unreadable`・`lock_busy`・`write_failed`。"""
    afile = config_file(account)
    try:
        with ExitStack() as st:
            st.enter_context(_claude_lock(afile))
            st.enter_context(_claude_lock(shared_file))
            return _sync_held(shared_file, afile, account)
    except TimeoutError:
        return "lock_busy"
    except OSError:
        return "write_failed"


def _sync_held(shared_file: str, afile: str, account: str) -> str | None:
    s_exists, s = _read_config(shared_file)
    if s is None:
        return "shared_unreadable"
    a_exists, a = _read_config(afile)
    if a is None:
        _drop_base(account)
        return "account_unreadable"
    _, b = _read_config(os.path.join(account, BASE_FILE))
    b = b if isinstance(b, dict) and b.get("version") == 1 else None
    uid = a.get("userID") if isinstance(a.get("userID"), str) else None
    lost = not a_exists or b is None or uid is None or b.get("userID") != uid
    new_s, new_a, base = dict(s), dict(a), {"version": 1}
    for key in SHARED_KEYS:
        sl, al = _leaves(key, s.get(key)), _leaves(key, a.get(key))
        bl = _leaves(key, (b or {}).get(key))
        if lost or (bl and not al):
            bl = al  # 最初の同期と同じに扱う（共有側の値にそろう。I18）
        merged = _merge_leaves(sl, al, bl)
        value = _unflatten(merged)
        if key in s or merged:
            new_s[key] = value
        if key in a or merged:
            new_a[key] = value
        base[key] = value
    for k in ONBOARDING_KEYS:
        if k not in new_a and k in s:
            new_a[k] = s[k]
    if new_s != s:
        _write_json(shared_file, new_s, _mode(shared_file, 0o600))
    if new_a != a:
        _write_json(afile, new_a, 0o600)
    if uid is not None:
        base["userID"] = uid
    _write_json(os.path.join(account, BASE_FILE), base, 0o600)
    return None


def _mode(path: str, default: int) -> int:
    try:
        return os.stat(path).st_mode & 0o777
    except OSError:
        return default
