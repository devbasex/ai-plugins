"""worktree を外す前の退避: 追跡していないファイル（無視したものを含む）を退避先へ移し、作り直せる生成物は捨てる。

退避先は `<git の共通ディレクトリ>/ndf/worktree-trash/<ブランチ>-<時刻>/` で、`meta.json` に元のブランチと先端の
コミットを書く。退避先を消すのは、先端が本番に出たコミットに含まれ、人が名前を見て承認したものだけである
（`sweep`。消すと戻せない利用者のファイル。C3・C4）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from ndf.adapters.git import Git, GitError

# 作り直せる生成物（退避せずに捨てる。パスのどこかの部分がこの名前なら当たる）
REGENERABLE = frozenset({".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "target", ".tox"})
# 退避先の置き場（git の共通ディレクトリの下）
TRASH_DIR = Path("ndf") / "worktree-trash"
# 退避先ごとの記録
META = "meta.json"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def trash_root(git: Git) -> Path:
    return git.common_dir() / TRASH_DIR


def _others(git: Git) -> list[str]:
    """追跡していないパス（無視したものを含む。ディレクトリは 1 件にまとめる）。"""
    return [p.rstrip("/") for p in git.run("ls-files", "--others", "--directory", "-z").split("\0") if p]


def _regenerable(rel: str) -> bool:
    return any(part in REGENERABLE for part in Path(rel).parts)


@dataclass(frozen=True)
class Evacuated:
    """退避の結果。`dest` は移したものが無ければ `None`。"""

    dest: Optional[Path]
    moved: tuple[str, ...]
    discarded: tuple[str, ...]


def evacuate(main: Git, worktree: Path, branch: str, head: str) -> Evacuated:
    """`worktree` の追跡していないファイルを退避先へ移し、作り直せる生成物を消す。"""
    rels = _others(Git(worktree))
    discarded = tuple(r for r in rels if _regenerable(r))
    moved = tuple(r for r in rels if not _regenerable(r))
    for rel in discarded:
        target = worktree / rel
        shutil.rmtree(target) if target.is_dir() and not target.is_symlink() else target.unlink(missing_ok=True)
    if not moved:
        return Evacuated(None, (), discarded)
    dest = trash_root(main) / f"{_UNSAFE.sub('-', branch)}-{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"
    dest.mkdir(parents=True)
    for rel in moved:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(worktree / rel), str(dest / rel))
    (dest / META).write_text(json.dumps({"branch": branch, "head": head, "from": str(worktree)}, ensure_ascii=False), encoding="utf-8")
    return Evacuated(dest, moved, discarded)


@dataclass(frozen=True)
class Candidate:
    """退避先 1 つと、消してよいか（先端が本番に出たか）。"""

    name: str
    branch: str
    released: bool
    reason: str


def _read_meta(path: Path) -> Optional[dict]:
    try:
        data = json.loads((path / META).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 記録の無い・壊れた退避先は候補にしない（残す）
        return None
    return data if isinstance(data, dict) else None


def candidates(main: Git, ref: str) -> list[Candidate]:
    """退避先の一覧。先端が `ref` に含まれるものだけを回収の候補（`released`）にする。"""
    root = trash_root(main)
    out: list[Candidate] = []
    for path in sorted(root.iterdir()) if root.is_dir() else []:
        meta = _read_meta(path)
        branch = str(meta["branch"]) if meta is not None and "branch" in meta else ""
        if meta is None or not isinstance(meta.get("head"), str):
            out.append(Candidate(path.name, "", False, "記録（meta.json）が読めないため残す"))
            continue
        try:
            released = main.is_ancestor(meta["head"], ref)
        except GitError as exc:  # 先端のコミットが無い（gc 済み）退避先は判断できないので残す
            out.append(Candidate(path.name, branch, False, f"先端を確かめられないため残す: {exc}"))
            continue
        reason = f"先端が {ref} に含まれる" if released else f"先端が {ref} に含まれないため残す"
        out.append(Candidate(path.name, branch, released, reason))
    return out


def sweep(main: Git, ref: str, approved: Iterable[str]) -> list[str]:
    """承認した名前のうち、回収の候補の退避先だけを消し、消した名前を返す。"""
    allowed = set(approved)
    root = trash_root(main)
    done = []
    for c in candidates(main, ref):
        if c.released and c.name in allowed:
            shutil.rmtree(root / c.name)
            done.append(c.name)
    return done
