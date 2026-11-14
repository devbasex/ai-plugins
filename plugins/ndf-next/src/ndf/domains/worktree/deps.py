"""worktree の依存の用意（宣言の `deps` 節）。

`deps` は共有設定（`.ndf/worktree.json`）からだけ読む（個人設定の `deps` は重ねない）。手順は次の順に行う。

- `copy_from_main`: メインディレクトリの同じパスをハードリンクで複製する（できなければ実体を複製する）
- `copy_as_real`: 実体を複製する（worktree の側で書き換えるもの）
- `run`: worktree の根で `sh -c` で打つ

用意の後に、git の状態が変わっていないこと（依存物は git が無視するパスに置く）と、ハードリンクで複製したファイルが
その場で書き換えられていないこと（メインディレクトリの同じファイルも変わる）を確かめる。

用意した印は `<worktree の git ディレクトリ>/ndf-deps` に、今の HEAD と宣言の中身の要約を書く。`if_unprepared` では
印が今のものと同じなら何もしない。印が別の HEAD・宣言のもの（前に用意した worktree）なら、既にある複製の宛先は
そのまま使い、無い宛先の複製と `run` だけをやり直す。
"""

from __future__ import annotations

import filecmp
import hashlib
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ndf.adapters.git import Git
from ndf.domains.worktree import container, declaration, repo

# 宣言の節と、その中の手順の名前（行う順）
SECTION = declaration.DEPS
STEPS = ("copy_from_main", "copy_as_real", "run")
# 印のファイルの名前（worktree の git ディレクトリの下）
MARK = "ndf-deps"
# 失敗の後に返す出力の末尾の行数
TAIL_LINES = 20


class DepsError(Exception):
    """宣言の `deps` 節の形が違う（手順を 1 つも行わない）。"""


@dataclass
class Prepared:
    """用意の結果。`failed` は失敗した手順（成功なら空）、`log` は手順の出力と失敗の理由。"""

    steps: int = 0
    skipped: bool = False
    failed: str = ""
    log: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def tail(self) -> list[str]:
        return self.log[-TAIL_LINES:]


def safe_relative(rel: str) -> bool:
    """worktree の中を指す相対パスか（絶対パス・`..` を含む・`~` で始まるものは外を指しうる）。"""
    parts = Path(rel).parts
    return bool(rel) and not rel.startswith(("/", "~")) and ".." not in parts and rel not in (".",)


def read_steps(decl: Optional[declaration.Declaration]) -> dict[str, list[str]]:
    """`deps` 節の手順。宣言・節が無ければ空。形が違えば `DepsError`。"""
    if decl is None or SECTION not in decl.raw:
        return {}
    section = decl.raw[SECTION]
    if not isinstance(section, dict):
        raise DepsError(f"{SECTION} が object でない")
    out: dict[str, list[str]] = {}
    for key in STEPS:
        value = section[key] if key in section else []
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise DepsError(f"{SECTION}.{key} が文字列の配列でない")
        out[key] = value
    return out


def mark_key(worktree: Git, main_dir: Path) -> str:
    """印の中身: 今の HEAD と宣言の中身の要約。どちらかが変われば印は効かない。"""
    head = worktree.run("rev-parse", "HEAD").strip()
    shared = declaration.shared_file(main_dir)
    digest = hashlib.sha256(shared.read_bytes()).hexdigest()[:16] if shared.is_file() else "none"
    return f"head={head} decl={digest}"


def mark_file(worktree: Git) -> Path:
    return Path(worktree.run("rev-parse", "--absolute-git-dir").strip()) / MARK


def _same(src: Path, dest: Path) -> bool:
    if src.is_file() and dest.is_file():
        return filecmp.cmp(src, dest, shallow=False)
    return src.is_dir() and dest.is_dir()


def _copy(src: Path, dest: Path, link: bool) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir() and not src.is_symlink():
        try:
            shutil.copytree(src, dest, symlinks=True, copy_function=os.link if link else shutil.copy2)
        except (OSError, shutil.Error):  # ハードリンクを張れない（別のファイルシステム）なら実体を複製し直す
            if not link:
                raise
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(src, dest, symlinks=True)
        return
    if link:
        try:
            os.link(src, dest)
            return
        except OSError as exc:  # ハードリンクを張れないときは実体を複製する
            if dest.exists():
                raise exc
    shutil.copy2(src, dest, follow_symlinks=False)


def copy_step(main_dir: Path, top: Path, rel: str, kind: str, redo: bool) -> Optional[str]:
    """複製の 1 件。失敗の理由（成功なら `None`）。"""
    if not safe_relative(rel):
        return f"{rel} は worktree の外を指す"
    src, dest = main_dir / rel, top / rel
    if not src.exists():
        return f"複製元が無い: {src}"
    if dest.exists() and not dest.is_symlink():
        if redo:
            return None
        if not _same(src, dest):
            return f"worktree の側で書き換えられている: {rel}"
        if kind == "copy_from_main":
            return None
        shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
    try:
        _copy(src, dest, link=kind == "copy_from_main")
    except (OSError, shutil.Error) as exc:  # 複製の失敗は手順の失敗として返す
        return f"複製できない: {rel}: {exc}"
    return None


def _rewritten(top: Path, rels: list[str], since: float) -> list[str]:
    """ハードリンクで複製し、用意の中で書き換えられたファイル（先頭の `TAIL_LINES` 件）。"""
    found: list[str] = []
    for rel in rels:
        base = top / rel
        paths = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file() and not p.is_symlink()]
        found += [str(p) for p in paths if p.stat().st_nlink > 1 and p.stat().st_mtime > since]
    return found[:TAIL_LINES]


def _run_step(shell: container.Shell, top: Path, command: str, out: Prepared) -> bool:
    done = shell.sh(command, top)
    out.log += (done.stdout + done.stderr).splitlines()
    return done.returncode == 0


def _do_steps(main_dir: Path, top: Path, steps: dict[str, list[str]], redo: bool, out: Prepared, shell: container.Shell) -> None:
    for kind in STEPS[:2]:
        for i, rel in enumerate(steps[kind]):
            why = copy_step(main_dir, top, rel, kind, redo)
            if why is not None:
                out.failed, out.log = f"{kind}[{i}] {rel}", [*out.log, why]
                return
    for i, command in enumerate(steps["run"]):
        if not _run_step(shell, top, command, out):
            out.failed = f"run[{i}] {command}"
            return


def _verify(wt: Git, top: Path, steps: dict[str, list[str]], before: str, since: float, out: Prepared) -> None:
    rewritten = _rewritten(top, steps["copy_from_main"], since)
    if rewritten:
        out.failed = "copy_from_main の書き換え"
        out.log += ["ハードリンクで複製したファイルが用意の中で書き換えられた（メインディレクトリの同じファイルも変わった）。"
                    "書き換えるパスは copy_as_real に置く:", *rewritten]  # fmt: skip
        return
    after = wt.run("status", "--porcelain")
    if after != before:
        out.failed = "git の状態"
        out.log += [
            "用意の前後で git の状態が変わった。依存物は git が無視するパスに置く:",
            *sorted(set(after.splitlines()) - set(before.splitlines())),
        ]


def prepare(worktree: Path, if_unprepared: bool = False, shell: Optional[container.Shell] = None) -> Prepared:
    """依存を用意する。宣言・`deps` 節が無ければ何もしない。形が違えば `DepsError`、git の失敗は `GitError`。"""
    wt = Git(worktree)
    main_dir = repo.locate(wt).main_dir
    started = time.monotonic()
    marker, key = mark_file(wt), mark_key(wt, main_dir)
    previous = marker.read_text(encoding="utf-8").strip() if marker.is_file() else None
    if if_unprepared and previous == key:
        return Prepared(skipped=True)
    steps = read_steps(declaration.load(main_dir))
    out = Prepared(steps=sum(len(v) for v in steps.values()))
    if out.steps == 0:
        return out
    redo = if_unprepared and previous is not None
    marker.unlink(missing_ok=True)
    before, since = wt.run("status", "--porcelain"), time.time()
    _do_steps(main_dir, worktree, steps, redo, out, shell if shell is not None else container.Shell())
    if not out.failed:
        _verify(wt, worktree, steps, before, since, out)
    if not out.failed:
        marker.write_text(key + "\n", encoding="utf-8")
    out.seconds = round(time.monotonic() - started, 1)
    return out


def summary(got: Prepared) -> dict[str, Any]:
    """結果の metrics。"""
    return {"steps": got.steps, "skipped": got.skipped, "seconds": got.seconds}
