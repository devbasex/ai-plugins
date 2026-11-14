"""hook の依存の環境の用意と片付け（#3345 の決定 89・#3105）。標準ライブラリだけで書く。

    python3 -m ndf.domains.hooks.env prepare   # 切り離した用意（環境が無いときの SessionStart が起動する）

| 置き場 | 中身 | 書き手 |
| --- | --- | --- |
| `<キャッシュ>/<プラグイン名>/venv/<uv.lock の hash>/` | 依存の環境。中の `.last-used` の更新時刻が最後に使った時刻 | `prepare` |
| `<キャッシュ>/<プラグイン名>/venv/<hash>.lock`・`<hash>.failed` | 用意の排他と、用意に失敗した理由 | `prepare` |
| `<キャッシュ>/<プラグイン名>/roots/<プラグインの置き場の sha256 の先頭 16 字>.json` | 根の目印（導入先 1 つが指す環境） | SessionStart のエントリポイント（`mark_root`） |

- 置き場は lock の hash ごとに別で、使い中の置き場は作り直さない。lock が変われば別の置き場に作る（走っている
  プロセスの最中に環境が作り直されて import が落ちることが無い。#3105）
- 作るときは置き場ごとの排他を取り、一時の置き場に `uv sync --frozen` で作ってから名前を変える。排他を取れなければ
  ほかの用意が走っているので何もしない
- 片付け（`prune`）は、根の目印から指されず、`.last-used` が `hook.venv-keep` の秒より古い置き場を消す。排他を
  持たれている置き場は消さない
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Mapping, Optional

from ndf.domains.hooks import tables

LAST_USED = ".last-used"


@dataclass(frozen=True)
class Ident:
    """導入先 1 つ。`cache_home` は XDG_CACHE_HOME（無ければ ~/.cache）。"""

    name: str
    plugin_root: Path
    cache_home: Path

    @classmethod
    def of(cls, plugin_root: Path, environ: Mapping[str, str] = os.environ) -> "Ident":
        manifest = plugin_root / ".claude-plugin" / "plugin.json"
        name = json.loads(manifest.read_text(encoding="utf-8"))["name"]
        cache = environ.get("XDG_CACHE_HOME")
        return cls(name, plugin_root, Path(cache) if cache else Path.home() / ".cache")

    @property
    def base(self) -> Path:
        return self.cache_home / self.name

    @property
    def venv_root(self) -> Path:
        return self.base / "venv"

    @property
    def roots_dir(self) -> Path:
        return self.base / "roots"


def lock_hash(plugin_root: Path) -> str:
    """`uv.lock` の sha256 の先頭 16 字（環境の置き場の名前）。"""
    return hashlib.sha256((plugin_root / "uv.lock").read_bytes()).hexdigest()[:16]


def venv_dir(ident: Ident) -> Path:
    return ident.venv_root / lock_hash(ident.plugin_root)


def venv_python(ident: Ident) -> Path:
    return venv_dir(ident) / "bin" / "python"


def failed_path(ident: Ident) -> Path:
    return ident.venv_root / f"{lock_hash(ident.plugin_root)}.failed"


def _lock_path(ident: Ident, name: str) -> Path:
    return ident.venv_root / f"{name}.lock"


def _try_lock(path: Path) -> Optional[int]:
    """排他を待たずに取る。取れたら fd、持たれていれば None。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def _release(fd: int) -> None:
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


def _uv_sync(ident: Ident, target: Path, uv: str) -> Optional[str]:
    """一時の置き場に環境を作る。失敗の理由か None。"""
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(target)}
    command = [uv, "sync", "--frozen", "--no-dev", "--quiet", "--project", str(ident.plugin_root)]
    try:
        done = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    except OSError as exc:
        return f"uv を起動できない: {exc}"
    if done.returncode != 0:
        return f"uv sync が {done.returncode} で終わった: {done.stderr.strip()[-500:]}"
    return None


def prepare(ident: Ident, uv: str = "uv", now: Optional[float] = None) -> Optional[Path]:
    """lock の hash の置き場に環境を用意する。できた（あった）置き場か、用意しなかった・失敗したときは None。"""
    target = venv_dir(ident)
    if venv_python(ident).exists():
        return target
    fd = _try_lock(_lock_path(ident, target.name))
    if fd is None:
        return None
    try:
        return _prepare_locked(ident, target, uv, now)
    finally:
        _release(fd)


def _prepare_locked(ident: Ident, target: Path, uv: str, now: Optional[float]) -> Optional[Path]:
    if venv_python(ident).exists():
        return target
    tmp = ident.venv_root / f".{target.name}.tmp-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    why = _uv_sync(ident, tmp, uv) if shutil.which(uv) or os.path.isabs(uv) else f"uv が見つからない: {uv}"
    if why is not None:
        shutil.rmtree(tmp, ignore_errors=True)
        failed_path(ident).write_text(why + "\n", encoding="utf-8")
        return None
    os.replace(tmp, target)
    failed_path(ident).unlink(missing_ok=True)
    touch_used(ident)
    prune(ident, time.time() if now is None else now)
    return target


def touch_used(ident: Ident) -> None:
    """`.last-used` の更新時刻を今にする。SessionStart の entry と用意の終わりだけが呼ぶ。"""
    target = venv_dir(ident)
    if target.is_dir():
        (target / LAST_USED).touch()


def _root_mark(ident: Ident) -> Path:
    digest = hashlib.sha256(str(ident.plugin_root).encode("utf-8")).hexdigest()[:16]
    return ident.roots_dir / f"{digest}.json"


def mark_root(ident: Ident) -> bool:
    """根の目印を書く。中身が同じなら書かない。書いたら真。SessionStart のエントリポイントだけが呼ぶ。"""
    path = _root_mark(ident)
    want = {"plugin_root": str(ident.plugin_root), "lock_hash": lock_hash(ident.plugin_root)}
    current = _read_json(path)
    if current is not None and all(current.get(k) == v for k, v in want.items()):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({**want, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}), encoding="utf-8")
    os.replace(tmp, path)
    return True


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 無い・壊れた目印は無いものとして扱う
        return None
    return data if isinstance(data, dict) else None


def _kept_hashes(ident: Ident) -> set:
    """今もある導入先の目印が指す hash。導入先の無くなった目印は消す。"""
    kept = set()
    for mark in sorted(ident.roots_dir.glob("*.json")) if ident.roots_dir.is_dir() else []:
        data = _read_json(mark)
        if data is not None and Path(str(data.get("plugin_root"))).is_dir():
            kept.add(str(data.get("lock_hash")))
        else:
            mark.unlink(missing_ok=True)
    return kept


def _last_used(path: Path) -> float:
    stamp = path / LAST_USED
    return (stamp if stamp.exists() else path).stat().st_mtime


def prune(ident: Ident, now: float) -> List[Path]:
    """使われない環境を消し、消した置き場を返す。用意の処理だけが呼ぶ。"""
    if not ident.venv_root.is_dir():
        return []
    keep_seconds = float(tables.policy_value("hook.venv-keep", "seconds"))
    kept = _kept_hashes(ident)
    candidates = [
        d
        for d in sorted(ident.venv_root.iterdir())
        if d.is_dir() and not d.name.startswith(".") and d.name not in kept and now - _last_used(d) > keep_seconds
    ]
    removed = []
    for path in candidates:
        fd = _try_lock(_lock_path(ident, path.name))
        if fd is None:
            continue
        try:
            shutil.rmtree(path, ignore_errors=True)
            _lock_path(ident, path.name).unlink(missing_ok=True)
            removed.append(path)
        finally:
            _release(fd)
    return removed


def preparing(ident: Ident) -> bool:
    """用意の排他が持たれているか。"""
    fd = _try_lock(_lock_path(ident, lock_hash(ident.plugin_root)))
    if fd is None:
        return True
    _release(fd)
    return False


def kick_prepare(ident: Ident, python: str = sys.executable) -> bool:
    """用意を切り離して起動する（待たない）。用意が走っていれば起動しない。起動したら真。"""
    if preparing(ident):
        return False
    subprocess.Popen(
        [python, "-m", "ndf.domains.hooks.env", "prepare", str(ident.plugin_root)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        cwd=str(ident.plugin_root / "src"),
    )
    return True


def main(argv: List[str]) -> int:
    if len(argv) < 2 or argv[0] != "prepare":
        print("使い方: python3 -m ndf.domains.hooks.env prepare <プラグインの根>", file=sys.stderr)
        return 2
    prepare(Ident.of(Path(argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
