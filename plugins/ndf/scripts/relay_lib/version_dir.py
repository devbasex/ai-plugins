"""ラッパーの複製: ランチャーとバージョンディレクトリ（#928・#1142 の C6。決定 5・I9）。

複製の置き場（`~/.claude/ndf/`。10.17.4〜10.17.6 の旧い複製は `~/.local/share/ndf/`）は次の形になる:

    relay.py                     ランチャー（プラグインの scripts/relay.py と同じバイト列）
    relay.version                複製の版
    relay.current                使うバージョンディレクトリの名前 1 行。原子的に書き換える
    relay-<版>-<digest 8 字>/    relay_lib/・lib/（LIB_FILES）・contracts/limits.json・pyproject.toml・uv.lock・MANIFEST・.venv/・inuse-<持ち主>-<pid>

digest は MANIFEST（ファイルごとの sha256 と相対パス）の sha256 である。バージョンディレクトリは書き終えてから
`relay.current` を替え（I9）、動いている run が使うもの（使用中の印 `inuse-<持ち主>-<pid>`）は消さない。

置き場は別のコンテナと共有の volume にあり、pid は作ったコンテナの中でしか読めない（#2583）。持ち主はホスト名
（コンテナの識別）で、同じ持ち主の印は pid の生死で、別の持ち主の印は寿命の窓（`FOREIGN_WINDOW`）で決める
（`lib/run_owner.py` の `alive` と同じ方針）。動いているラッパーは印の時刻を進める（`refresh_inuse`）。
旧形式の `inuse-<pid>` は持ち主が分からず、pid の生死で読む。

`.venv/` はラッパーを動かす環境で、書きかけのディレクトリの中でプラグインと同じ `pyproject.toml` と `uv.lock` から
`uv sync --frozen` で作る（決定 20。`runtime.sync`）。作れなければバージョンディレクトリを置かない。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import socket
import time

from . import runtime
from .common import (
    COPY_LOCK,
    PKG_ROOT,
    _lock,
    _read_bytes,
    _unlock,
    _write_file,
    config_dir,
    copy_path,
    data_dir,
    launcher_path,
    load_json,
    read_text,
)

import procs  # noqa: E402,I001  common が lib/ を sys.path に置く
import versions  # noqa: E402

CURRENT_FILE = "relay.current"
MANIFEST = runtime.MANIFEST
# ラッパーが import するライブラリ（包みと、venv が使う deps）
LIB_FILES = (
    "lib/ask_policy.py",
    "lib/claude_account_dir.py",
    "lib/claude_accounts.py",
    "lib/claude_api_keys.py",
    "lib/claude_settings.py",
    "lib/claude_usage.py",
    "lib/clock.py",
    "lib/deps.py",
    "lib/exit_codes.py",
    "lib/jsonio.py",
    "lib/limits.py",
    "lib/locks.py",
    "lib/md.py",
    "lib/procs.py",
    "lib/relay_limit.py",
    "lib/state_home.py",
    "lib/versions.py",
    "lib/wait_notice.py",
)
# 環境の宣言と lock。プラグインではプラグインの根（scripts/ の 1 つ上）、バージョンディレクトリでは中にある
PROJECT_FILES = ("pyproject.toml", "uv.lock")
DECL_FILES = ("contracts/limits.json",)  # 上限の宣言（lib/limits.py が読む。#2343）
DIR_RE = re.compile(r"^relay-(.+)-[0-9a-f]{8}$")
TMP_RE = re.compile(r"^relay-.+-[0-9a-f]{8}\.tmp-(\d+)$")
INUSE_RE = re.compile(r"^inuse-(?:(?P<owner>.+)-)?(?P<pid>\d+)$")
KEEP = 2
# 別のコンテナの印の寿命の窓。`lib/run_owner.py` の `WINDOW` と同じ値（run_owner は複製に入らないため import しない）
FOREIGN_WINDOW = 24 * 3600
REFRESH = 3600  # 動いているラッパーが使用中の印の時刻を進める間隔の秒（窓より十分短く）
_refreshed_at = 0.0


def version_of(name: str | None) -> str | None:
    """バージョンディレクトリの名前（`relay-<版>-<digest>`）の版。名前の形でなければ None。"""
    m = DIR_RE.match(name) if isinstance(name, str) else None
    return m.group(1) if m else None


def copy_version_path() -> str:
    return os.path.join(config_dir(), "relay.version")


def old_copy_path() -> str:
    """10.17.4〜10.17.6 が置いた複製。10.17.4〜10.17.6 の囲みの alias が指す。"""
    return os.path.join(data_dir(), "relay.py")


def plugin_version(root: str | None = None) -> str | None:
    """`<プラグインのルート>/.claude-plugin/plugin.json` の版。ルートは relay_lib の 2 つ上。"""
    root = root or os.path.dirname(os.path.realpath(PKG_ROOT))
    data = load_json(os.path.join(root, ".claude-plugin", "plugin.json"))
    v = data.get("version") if isinstance(data, dict) else None
    return v.strip() if isinstance(v, str) and v.strip() else None


def version_key(v: str | None):
    """`X.Y.Z` < 同じ `X.Y.Z` では `-dev.N` < `-rc.N` < 接尾辞なし。読めなければ None（`lib/versions.py`）。"""
    return versions.version_order(v)


def _self_body() -> bytes:
    """ランチャーのバイト列。複製の relay.py はこれと同じにする。"""
    with open(os.path.realpath(launcher_path()), "rb") as f:
        return f.read()


def place_copy_to(dst: str, body: bytes, mode: int = 0o755) -> bool:
    if _read_bytes(dst) == body:
        return False
    _write_file(dst, body, mode)
    return True


def _alive(pid: int) -> bool:
    return procs.pid_alive(pid)


def inuse_owner() -> str:
    """使用中の印の持ち主（このホストまたはコンテナの識別）。"""
    return re.sub(r"[^0-9A-Za-z._-]", "_", socket.gethostname()) or "unknown"


def inuse_name(pid: int) -> str:
    return f"inuse-{inuse_owner()}-{pid}"


def claim_inuse(root: str = PKG_ROOT, pid: int | None = None) -> str | None:
    """`root` がバージョンディレクトリなら使用中の印を置いてそのパスを返す。プラグインのキャッシュなら None。"""
    if not os.path.isfile(os.path.join(root, MANIFEST)):
        return None
    path = os.path.join(root, inuse_name(os.getpid() if pid is None else pid))
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
    except OSError:
        return None
    return path


def release_inuse(path: str | None) -> None:
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


def release_claims(root: str) -> None:
    """`root` に置いたこのプロセスの印を消す。入れ替えの前の版が置いた旧形式の印も消す（pid は exec で変わらない）。"""
    for name in (inuse_name(os.getpid()), f"inuse-{os.getpid()}"):
        release_inuse(os.path.join(root, name))


def refresh_inuse(root: str = PKG_ROOT) -> None:
    """使用中の印の時刻を `REFRESH` 秒ごとに進める。別のコンテナの片付けが寿命の窓で読む。消えていれば置き直す。"""
    global _refreshed_at
    now = time.time()
    if now - _refreshed_at < REFRESH:
        return
    _refreshed_at = now
    path = claim_inuse(root)
    if path:
        try:
            os.utime(path)
        except OSError:
            pass


def _mark_live(mark: str, owner: str | None, pid: int) -> bool:
    """旧形式（持ち主なし）と自分のコンテナの印は pid の生死、別のコンテナの印は最終更新が寿命の窓の中か。"""
    if owner in (None, inuse_owner()):
        return _alive(pid)
    try:
        return time.time() - os.stat(mark).st_mtime < FOREIGN_WINDOW
    except OSError:
        return False


def launch_python(python: str | None = None, base: str | None = None) -> tuple[str | None, str | None]:
    """リレーハブがレーンを起こす python と、切り替えた先のバージョンディレクトリの名前（#2647 の決定 10・I14）。

    リレーハブの python（`sys.executable`）の置き場が消えていれば、`relay.current` が指すバージョンディレクトリの
    仮想環境へ切り替える。どちらも無ければ (None, None) を返す。
    """
    import sys

    python = python or sys.executable
    if os.path.isfile(python):
        return python, None
    base = base or os.path.dirname(launcher_path())
    cur = VersionDir(base).current()
    alt = runtime.python_of(runtime.version_env(os.path.join(base, cur))) if cur else None
    return (alt, cur) if alt and os.path.isfile(alt) else (None, None)


class VersionDir:
    """`base` の下のバージョンディレクトリと、使うものを指す `relay.current`。中身の元は `source`。"""

    def __init__(self, base: str, source: str = PKG_ROOT):
        self.base = base
        self.source = source

    def files(self) -> list[str]:
        """バージョンディレクトリに入れるファイルの相対パス。"""
        pkg = os.path.join(self.source, "relay_lib")
        mods = sorted(f"relay_lib/{n}" for n in os.listdir(pkg) if n.endswith(".py"))
        return mods + list(LIB_FILES) + list(DECL_FILES) + list(PROJECT_FILES)

    def src(self, rel: str) -> str:
        """`rel` の元のファイル。環境の宣言と lock は、元がプラグインならプラグインの根から取る。"""
        if rel in PROJECT_FILES + DECL_FILES and not runtime.is_version_dir(self.source):
            return os.path.join(os.path.dirname(self.source), rel)
        return os.path.join(self.source, rel)

    def manifest(self) -> str:
        rows = []
        for rel in self.files():
            with open(self.src(rel), "rb") as f:
                rows.append(f"{hashlib.sha256(f.read()).hexdigest()}  {rel}\n")
        return "".join(rows)

    def name_for(self, version: str, manifest: str | None = None) -> str:
        manifest = self.manifest() if manifest is None else manifest
        safe = re.sub(r"[^0-9A-Za-z.+_-]", "_", version or "unknown")
        return f"relay-{safe}-{hashlib.sha256(manifest.encode()).hexdigest()[:8]}"

    def current(self) -> str | None:
        """`relay.current` が指すバージョンディレクトリの名前。無い・壊れているときは None。"""
        name = (read_text(os.path.join(self.base, CURRENT_FILE)) or "").strip()
        if DIR_RE.match(name) and os.path.isfile(os.path.join(self.base, name, MANIFEST)):
            return name
        return None

    def is_current(self, version: str) -> bool:
        return self.current() == self.name_for(version)

    def ensure(self, version: str) -> str:
        """`version` のバージョンディレクトリを置いて `relay.current` で指し、古いものを片づける。名前を返す。"""
        manifest = self.manifest()
        name = self.name_for(version, manifest)
        target = os.path.join(self.base, name)
        if read_text(os.path.join(target, MANIFEST)) != manifest or not os.path.isfile(runtime.python_of(runtime.version_env(target))):
            self._write(target, manifest)
        if self.current() != name:
            _write_file(os.path.join(self.base, CURRENT_FILE), (name + "\n").encode(), 0o644)
        self.prune()
        return name

    def _write(self, target: str, manifest: str) -> None:
        """`<target>.tmp-<pid>` へ書き終え、環境を作ってから rename する。途中で落ちたら書きかけを消して上げる。"""
        os.makedirs(self.base, mode=0o700, exist_ok=True)
        tmp = f"{target}.tmp-{os.getpid()}"
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            for rel in self.files():
                os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
                shutil.copyfile(self.src(rel), os.path.join(tmp, rel))
            with open(os.path.join(tmp, MANIFEST), "w") as f:
                f.write(manifest)
            runtime.sync(tmp, runtime.version_env(tmp))
            if os.path.isdir(target):  # 中身の壊れた同じ名前のもの
                shutil.rmtree(target)
            os.rename(tmp, target)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise

    def prune(self) -> list[str]:
        """指されず、生きている run も使っていないバージョンディレクトリを、新しい 2 つを残して消す。
        落ちた書きかけ（死んだ pid の `.tmp-<pid>`）と、使用中でなくなった印も消す。消したパスを返す。"""
        try:
            names = os.listdir(self.base)
        except OSError:
            return []
        cur = self.current()
        gone, idle = [], []
        for n in names:
            path = os.path.join(self.base, n)
            m = TMP_RE.match(n)
            if m and not _alive(int(m.group(1))):
                shutil.rmtree(path, ignore_errors=True)
                gone.append(path)
            elif DIR_RE.match(n) and os.path.isdir(path) and n != cur:
                idle.append((os.stat(path).st_mtime, path))  # inuse の掃除で mtime が変わる前に読む
        idle = [p for _, p in sorted(idle, reverse=True) if not self._in_use(p)]
        for path in idle[KEEP:]:
            shutil.rmtree(path, ignore_errors=True)
            gone.append(path)
        return gone

    @staticmethod
    def _in_use(path: str) -> bool:
        """印が 1 つでも使用中ならディレクトリは使用中。使用中でなくなった印は消す。"""
        used = False
        for n in os.listdir(path):
            m = INUSE_RE.match(n)
            if not m:
                continue
            mark = os.path.join(path, n)
            if _mark_live(mark, m.group("owner"), int(m.group("pid"))):
                used = True
            else:
                release_inuse(mark)
        return used

    def remove_all(self) -> list[str]:
        """uninstall: `relay.current` とバージョンディレクトリをすべて消す。消したパスを返す。"""
        try:
            names = sorted(os.listdir(self.base))
        except OSError:
            return []
        gone = []
        cur = os.path.join(self.base, CURRENT_FILE)
        if os.path.lexists(cur):
            os.unlink(cur)
            gone.append(cur)
        for n in names:
            path = os.path.join(self.base, n)
            if (DIR_RE.match(n) or TMP_RE.match(n)) and os.path.isdir(path):
                shutil.rmtree(path)
                gone.append(path)
        return gone


# ---------------------------------------------------------------- startup の置き直し


def _startup_copy(body: bytes) -> None:
    """判定 0a。`copy.lock` の中で複製の有無と版を読み直し、後退させずに置き直す。

    バージョンディレクトリを先に置いてからランチャーを替える。旧い単体の relay.py の複製を替えた直後でも、
    ランチャーが読むバージョンディレクトリが既に在る。"""
    dst = copy_path()
    if not os.path.exists(dst):
        return
    cfd = _lock(os.path.join(config_dir(), COPY_LOCK), 1)
    if cfd is None:
        return
    try:
        cur = _read_bytes(dst)
        if cur is None:
            return
        ver = plugin_version()
        mine = version_key(ver)
        if mine is None:
            return
        vd = VersionDir(config_dir())
        theirs = version_key(read_text(copy_version_path()))
        same = cur == body and vd.is_current(ver)
        # 中身が同じでも、版の記録が古ければ書き直す（中身の変わらない版を入れたとき）
        if theirs is not None and (theirs >= mine if same else theirs > mine):
            vd.prune()
            return
        vd.ensure(ver)
        if cur != body:
            _write_file(dst, body, 0o755)
        _write_file(copy_version_path(), (ver + "\n").encode(), 0o644)
    finally:
        _unlock(cfd)


def _startup_refresh_old_copy(body: bytes) -> None:
    """10.17.4〜10.17.6 が置いた複製が在れば今の版で置き直す。失敗は従来どおり局所的に無視する。"""
    old = old_copy_path()
    try:
        if os.path.exists(old):
            VersionDir(os.path.dirname(old)).ensure(plugin_version() or "unknown")
            if _read_bytes(old) != body:
                _write_file(old, body, 0o755)
    except OSError:
        pass
