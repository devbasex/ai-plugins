"""worktree を外す前の退避: 追跡していないファイル（無視したものを含む）を退避先へ移し、作り直せる生成物は捨てる。

退避先は `<git の共通ディレクトリ>/ndf/worktree-trash/<ブランチ>-<時刻>/` で、隣の台帳 `<退避先>.json` に元のブランチと
先端のコミット（PR のマージコミットが分かればそれも）を書く（退避先の中に置くと、利用者の同じ名前のファイルを上書きする）。作り直せる生成物の決め方と、
1 件でも移せなければ止める決まりは `docs/specifications/ndf-cleanup-and-bundle-closing.md` の退避の節にある。退避先を消すのは、先端か PR のマージコミットが本番に出たコミットに含まれ、人が名前を見て承認したものだけである
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

from ndf import identity
from ndf.adapters.git import Git, GitError

# 作り直せる生成物（退避せずに捨てる。パスのどこかのディレクトリがこの名前なら、その下ごと当たる）
REGENERABLE = frozenset({".venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
# 名前だけでは生成物と決められないディレクトリと、同じ階層にあれば生成物とみなす作り直しの設定
MANIFEST_REGENERABLE = {"target": ("Cargo.toml", "pom.xml", "dbt_project.yml")}
# 退避先の置き場の名前（git の共通ディレクトリの下の、プラグイン名の置き場に置く）
TRASH_DIR = "worktree-trash"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def trash_root(git: Git) -> Path:
    return git.common_dir() / identity.load().name / TRASH_DIR


def _others(git: Git) -> list[str]:
    """追跡していないパス（無視したものを含む。ディレクトリは 1 件にまとめる）。"""
    return [p.rstrip("/") for p in git.run("ls-files", "--others", "--directory", "-z").split("\0") if p]


class TrashError(Exception):
    """退避できないファイルがあった（残りを移さず、worktree を外さない）。"""


def ledger_of(dest: Path) -> Path:
    """退避先の隣の台帳 `<退避先>.json`。"""
    return dest.with_name(f"{dest.name}.json")


def leftovers(worktree: Path) -> bool:
    """追跡していないファイル（無視したものを含む）が残っているか（`git worktree remove` は無視したファイルを黙って消す）。"""
    return bool(_others(Git(worktree)))


def _regenerable(worktree: Path, rel: str) -> bool:
    """`rel` が作り直せる生成物のディレクトリの下（またはそのディレクトリ）か。"""
    parts = Path(rel).parts
    for i, part in enumerate(parts):
        if part in MANIFEST_REGENERABLE:
            parent = worktree.joinpath(*parts[:i])
            if not any((parent / m).is_file() for m in MANIFEST_REGENERABLE[part]):
                continue
        elif part not in REGENERABLE:
            continue
        if i < len(parts) - 1 or (worktree / rel).is_dir():
            return True
    return False


@dataclass(frozen=True)
class Evacuated:
    """退避の結果。`dest` は移したものが無ければ `None`。"""

    dest: Optional[Path]
    moved: tuple[str, ...]
    discarded: tuple[str, ...]


def evacuate(main: Git, worktree: Path, branch: str, head: str, merge: str = "") -> Evacuated:
    """`worktree` の追跡していないファイルを退避先へ移し、作り直せる生成物を消す。移せなければ `TrashError`。

    `merge` は PR のマージで本流へ入ったコミット（squash・rebase のマージでは先端と別。分からなければ空）。
    """
    rels = _others(Git(worktree))
    discarded = tuple(r for r in rels if _regenerable(worktree, r))
    moved = tuple(r for r in rels if not _regenerable(worktree, r))
    for rel in discarded:
        target = worktree / rel
        shutil.rmtree(target) if target.is_dir() and not target.is_symlink() else target.unlink(missing_ok=True)
    if not moved:
        return Evacuated(None, (), discarded)
    dest = trash_root(main) / f"{_UNSAFE.sub('-', branch)}-{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"
    dest.mkdir(parents=True)
    record = {
        "branch": branch,
        "head": head,
        "from": str(worktree),
        "discarded": list(discarded),
        **({"merge_commit": merge} if merge else {}),
    }
    ledger_of(dest).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    for i, rel in enumerate(moved):
        try:
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(worktree / rel), str(dest / rel))
        except (OSError, shutil.Error) as exc:  # 1 件でも移せなければ残りを移さず、移した分を戻して worktree を外させない
            back = restore(worktree, Evacuated(dest, moved[:i], discarded))
            kept = f"（移した分を戻せない: {back}）" if back is not None else ""
            raise TrashError(f"{rel} を退避先 {dest} へ移せない: {exc}{kept}") from exc
    return Evacuated(dest, moved, discarded)


def restore(worktree: Path, got: Evacuated) -> Optional[str]:
    """`evacuate` で移したファイルを worktree へ戻し、空になった退避先と台帳を消す（worktree を外せなかったとき）。

    戻せなければ理由を返し、退避先と台帳は残す（戻せなかったファイルは退避先にある）。
    """
    if got.dest is None:
        return None
    for rel in got.moved:
        target = worktree / rel
        if target.exists() or target.is_symlink():
            return f"{rel} が worktree に既にあるため退避先 {got.dest} から戻さない"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(got.dest / rel), str(target))
        except (OSError, shutil.Error) as exc:  # 戻せなかったものは退避先に残す
            return f"{rel} を退避先 {got.dest} から戻せない: {exc}"
    shutil.rmtree(got.dest)
    ledger_of(got.dest).unlink(missing_ok=True)
    return None


@dataclass(frozen=True)
class Candidate:
    """退避先 1 つと、消してよいか（先端が本番に出たか）。"""

    name: str
    branch: str
    released: bool
    reason: str


def _read_meta(path: Path) -> Optional[dict]:
    try:
        data = json.loads(ledger_of(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 台帳の無い・壊れた退避先は候補にしない（残す）
        return None
    return data if isinstance(data, dict) else None


def candidates(main: Git, ref: str) -> list[Candidate]:
    """退避先の一覧。先端が `ref` に含まれるものだけを回収の候補（`released`）にする。"""
    root = trash_root(main)
    out: list[Candidate] = []
    for path in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        meta = _read_meta(path)
        branch = str(meta["branch"]) if meta is not None and "branch" in meta else ""
        if meta is None or not isinstance(meta.get("head"), str):
            out.append(Candidate(path.name, "", False, f"台帳（{ledger_of(path).name}）が読めないため残す"))
            continue
        try:
            released = _released(main, meta, ref)
        except GitError as exc:  # 先端のコミットが無い（gc 済み）退避先は判断できないので残す
            out.append(Candidate(path.name, branch, False, f"先端を確かめられないため残す: {exc}"))
            continue
        reason = f"先端かマージコミットが {ref} に含まれる" if released else f"先端もマージコミットも {ref} に含まれないため残す"
        out.append(Candidate(path.name, branch, released, reason))
    return out


def _released(main: Git, meta: dict, ref: str) -> bool:
    """先端か PR のマージコミット（squash のマージでは先端は本番に入らない）が `ref` に含まれるか。

    どれも確かめられなければ `GitError`。
    """
    merge = meta.get("merge_commit")
    commits = [meta["head"], *([merge] if isinstance(merge, str) and merge else [])]
    errors: list[GitError] = []
    for commit in commits:
        try:
            if main.is_ancestor(commit, ref):
                return True
        except GitError as exc:  # 片方のコミットが無くても、もう片方で確かめる
            errors.append(exc)
    if len(errors) == len(commits):
        raise errors[0]
    return False


def sweep(main: Git, ref: str, approved: Iterable[str]) -> list[str]:
    """承認した名前のうち、回収の候補の退避先だけを消し、消した名前を返す。"""
    allowed = set(approved)
    root = trash_root(main)
    done = []
    for c in candidates(main, ref):
        if c.released and c.name in allowed:
            shutil.rmtree(root / c.name)
            ledger_of(root / c.name).unlink(missing_ok=True)
            done.append(c.name)
    return done
