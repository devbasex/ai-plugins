"""ラッパーの複製: ランチャーとバージョンディレクトリ（#928・#1142 の C6。決定 5・I9）。

複製の置き場（`~/.claude/ndf/`。10.17.4〜10.17.6 の旧い複製は `~/.local/share/ndf/`）は次の形になる:

    relay.py                     ランチャー（プラグインの scripts/relay.py と同じバイト列）
    relay.version                複製の版
    relay.current                使うバージョンディレクトリの名前 1 行。原子的に書き換える
    relay-<版>-<digest 8 字>/    relay_lib/・lib/（LIB_FILES）・pyproject.toml・uv.lock・MANIFEST・.venv/・inuse-<pid>

digest は MANIFEST（ファイルごとの sha256 と相対パス）の sha256 である。バージョンディレクトリは書き終えてから
`relay.current` を替え（I9）、動いている run が使うもの（生きている pid の `inuse-<pid>`）は消さない。

`.venv/` はラッパーを動かす環境で、書きかけのディレクトリの中でプラグインと同じ `pyproject.toml` と `uv.lock` から
`uv sync --frozen` で作る（決定 20。`runtime.sync`）。作れなければバージョンディレクトリを置かない。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil

from . import runtime
from .common import (COPY_LOCK, PKG_ROOT, _lock, _read_bytes, _unlock, _write_file, config_dir, copy_path, data_dir,
                     launcher_path, load_json, read_text)

import procs  # noqa: E402,I001  common が lib/ を sys.path に置く
import versions  # noqa: E402

CURRENT_FILE = "relay.current"
MANIFEST = runtime.MANIFEST
# ラッパーが import するライブラリ（包みと、venv が使う deps）
LIB_FILES = ("lib/clock.py", "lib/deps.py", "lib/jsonio.py", "lib/locks.py", "lib/md.py", "lib/procs.py",
             "lib/versions.py")
# 環境の宣言と lock。プラグインではプラグインの根（scripts/ の 1 つ上）、バージョンディレクトリでは中にある
PROJECT_FILES = ("pyproject.toml", "uv.lock")
DIR_RE = re.compile(r"^relay-.+-[0-9a-f]{8}$")
TMP_RE = re.compile(r"^relay-.+-[0-9a-f]{8}\.tmp-(\d+)$")
KEEP = 2


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


def claim_inuse(root: str = PKG_ROOT, pid: int | None = None) -> str | None:
    """`root` がバージョンディレクトリなら `inuse-<pid>` を置いてそのパスを返す。プラグインのキャッシュなら None。"""
    if not os.path.isfile(os.path.join(root, MANIFEST)):
        return None
    path = os.path.join(root, f"inuse-{os.getpid() if pid is None else pid}")
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


class VersionDir:
    """`base` の下のバージョンディレクトリと、使うものを指す `relay.current`。中身の元は `source`。"""

    def __init__(self, base: str, source: str = PKG_ROOT):
        self.base = base
        self.source = source

    def files(self) -> list[str]:
        """バージョンディレクトリに入れるファイルの相対パス。"""
        pkg = os.path.join(self.source, "relay_lib")
        mods = sorted(f"relay_lib/{n}" for n in os.listdir(pkg) if n.endswith(".py"))
        return mods + list(LIB_FILES) + list(PROJECT_FILES)

    def src(self, rel: str) -> str:
        """`rel` の元のファイル。環境の宣言と lock は、元がプラグインならプラグインの根から取る。"""
        if rel in PROJECT_FILES and not runtime.is_version_dir(self.source):
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
        if (read_text(os.path.join(target, MANIFEST)) != manifest
                or not os.path.isfile(runtime.python_of(runtime.version_env(target)))):
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
        落ちた書きかけ（死んだ pid の `.tmp-<pid>`）と、死んだ pid の `inuse-<pid>` も消す。消したパスを返す。"""
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
        used = False
        for n in os.listdir(path):
            if not n.startswith("inuse-"):
                continue
            try:
                pid = int(n[len("inuse-"):])
            except ValueError:
                continue
            if _alive(pid):
                used = True
            else:
                release_inuse(os.path.join(path, n))
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
