"""リリースの設定（`.ndf/release.json`）の決まった手順の読み込みと実行（`release-steps.py` の `steps` から分けた）。

宣言の形は lib/schema.py で見て、値の決まり（空でない・相対パス・1 以上）は `parse_steps` が見る。コマンドの表示と起動は
release_lib/step_run.py が持つ。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

import jsonio
import limits
import proc
import schema
from release_lib import step_run
from step_result import StepError

DECLARATION = ".ndf/release.json"

SUPPORTED_VERSIONS = (1,)
DEFAULT_TIMEOUT = limits.seconds("release.declared-step")


class DeclarationError(Exception):
    pass


@dataclass
class Step:
    name: str
    stage: str
    command: list[str]
    writes: list[str]
    guide: str | None
    timeout: int


def _relative(value, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise DeclarationError(f"{where}: 空でない文字列で書く")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise DeclarationError(f"{where}: リポジトリの根からの相対パスで書く（絶対パスと .. は使えない）: {value!r}")
    return value


class _StepShape(schema.Shape):
    model_config = {**schema.Shape.model_config, "strict": True}
    name: str
    stage: Literal["production", "verification", "any"]
    command: list[str]
    writes: list[str]
    guide: str | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT


class _DeclarationShape(schema.Shape):
    model_config = {**schema.Shape.model_config, "strict": True}
    version: int
    steps: list[_StepShape]


def parse_steps(raw) -> list[Step]:
    """宣言の形は lib/schema.py で見て、値の決まり（空でない・相対パス・1 以上）はここで見る。"""
    if not isinstance(raw, dict):
        raise DeclarationError("release.json: 最上位はオブジェクトで書く")
    try:
        decl = schema.load_shape(_DeclarationShape, {k: v for k, v in raw.items() if k != "$schema"})
    except schema.ShapeError as e:
        raise DeclarationError(f"release.json: {e}") from None
    if decl.version not in SUPPORTED_VERSIONS:
        raise DeclarationError(f"version: 無いか未対応である: {decl.version!r}（読めるのは {SUPPORTED_VERSIONS}）")
    out = []
    for i, s in enumerate(decl.steps):
        where = f"steps[{i}]"
        if not s.name.strip():
            raise DeclarationError(f"{where}.name: 空でない文字列で書く（必須）")
        if not s.command:
            raise DeclarationError(f"{where}.command: 空でない文字列の配列で書く（必須）")
        writes = [_relative(w, f"{where}.writes[{j}]") for j, w in enumerate(s.writes)]
        guide = None if s.guide is None else _relative(s.guide, f"{where}.guide")
        if s.timeout_seconds < 1:
            raise DeclarationError(f"{where}.timeout_seconds: 1 以上の整数で書く: {s.timeout_seconds!r}")
        out.append(Step(s.name, s.stage, s.command, writes, guide, s.timeout_seconds))
    return out


def load_steps(root: Path) -> list[Step] | None:
    path = root / DECLARATION
    if not path.is_file():
        return None
    try:
        raw = jsonio.read(path)
    except jsonio.JsonReadError as e:
        raise DeclarationError(f"release.json: JSON として読めない: {e.detail or e}") from e
    return parse_steps(raw)


# ---------- 変更の判定 ----------


def status_paths(root: Path) -> set[str]:
    """`git status` が挙げたパス。名前の変更は元と先の両方を返す。"""
    tokens = proc.git(root, "status", "--porcelain=v1", "-z", "-uall").stdout.split("\0")
    paths: set[str] = set()
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        i += 1
        if len(entry) < 4:
            continue
        paths.add(entry[3:])
        if entry[0] in "RC" or entry[1] in "RC":
            paths.add(tokens[i])
            i += 1
    return paths


def digests(root: Path, paths: set[str]) -> dict[str, str | None]:
    """パスごとの内容の要約。無いパスは None。"""
    present = sorted(p for p in paths if (root / p).is_file() or (root / p).is_symlink())
    out: dict[str, str | None] = {p: None for p in paths}
    if present:
        hashes = proc.run(["git", "-C", str(root), "hash-object", "--stdin-paths"], input="\n".join(present) + "\n").stdout.split()
        out.update(zip(present, hashes))
    return out


def allowed(path: str, writes: list[str]) -> bool:
    for w in writes:
        prefix = w.rstrip("/")
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


# ---------- 実行 ----------


def selected(steps: list[Step], stage: str) -> list[Step]:
    return [s for s in steps if s.stage in (stage, "any")]


def expand(step: Step, version: str) -> list[str]:
    return [c.replace("{version}", version) for c in step.command]


def changed_paths(root: Path, before_paths: set[str], before: dict[str, str | None]) -> list[str]:
    """コマンドの後の状態と比べ、内容の変わったパスを返す。"""
    after_paths = status_paths(root)
    union = before_paths | after_paths
    after = digests(root, union)
    for p in after_paths - before_paths:  # コマンドの前は変更が無かった = HEAD の内容
        before[p] = _head_digest(root, p)
    return sorted(p for p in union if before[p] != after[p])


def run_steps(root: Path, stage: str, version: str, dry_run: bool) -> int:
    steps = load_steps(root)
    if steps is None:
        return 0
    for step in selected(steps, stage):
        command = expand(step, version)
        if dry_run:
            step_run.print_step(step, command)
            continue
        try:
            before_paths = status_paths(root)
            before = digests(root, before_paths)
        except (OSError, StepError) as e:
            print(f"コマンド: {step.name} → 実行しない（git status を取れない: {e}）", file=sys.stderr)
            return 1
        rc = step_run.run_command(root, step, command)
        if rc is None:
            return 1
        changed = changed_paths(root, before_paths, before)
        outside = [p for p in changed if not allowed(p, step.writes)]
        print(f"コマンド: {step.name} → {rc}")
        for p in changed:
            print(f"  変えたパス: {p}{'（書いてよい場所の外）' if p in outside else ''}")
        if rc != 0:
            return 1
        if outside:
            print(f"コマンド: {step.name} が書いてよい場所（{', '.join(step.writes) or 'なし'}）の外を変えた", file=sys.stderr)
            return 1
        if step.guide:
            print(f"guide: {step.guide}")
    return 0


def _head_digest(root: Path, path: str) -> str | None:
    """コマンドの前に変更の無かったパスの要約（HEAD の内容。HEAD に無ければ None）。"""
    try:
        return proc.git(root, "rev-parse", "--verify", "-q", f"HEAD:{path}").stdout.strip() or None
    except StepError:
        return None
