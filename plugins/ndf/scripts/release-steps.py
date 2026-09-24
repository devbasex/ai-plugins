#!/usr/bin/env python3
"""リポジトリが宣言した配布の段を、配布の段階に合わせて走らせる（#893）。

宣言はリポジトリの `.ndf/release.json`。形は `skills/release/schemas/release.schema.json` が定め、
書き方は `skills/release/references/release-steps.md` にある。

    python3 release-steps.py run   --root <dir> --stage production|verification --version <版> [--dry-run]
    python3 release-steps.py check --root <dir>

終了コード:

    0  宣言が無い（run は何も出力しない）、段階に合う段が無い、またはすべての段が 0 で終わり
       書いてよい場所の中だけが変わった
    1  段が 0 以外で終わった・時間切れ・書いてよい場所の外が変わった。最初に落ちた段で止める
    2  check だけが返す。宣言が無い
    3  宣言が読めない。どの項目かを標準エラーに出す

段はシェルを通さずに `--root` を作業ディレクトリにして実行する。段が変えたパスは、段の前後の
`git status --porcelain -uall` に出たパスの内容の要約（`git hash-object`）を比べて決める。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

DECLARATION = ".ndf/release.json"
SUPPORTED_VERSIONS = (1,)
STAGES = ("production", "verification", "any")
DEFAULT_TIMEOUT = 600
STEP_KEYS = {"name", "stage", "command", "writes", "guide", "timeout_seconds"}


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


def parse(raw) -> list[Step]:
    if not isinstance(raw, dict):
        raise DeclarationError("release.json: 最上位はオブジェクトで書く")
    unknown = set(raw) - {"$schema", "version", "steps"}
    if unknown:
        raise DeclarationError(f"release.json: 知らない項目: {', '.join(sorted(unknown))}")
    version = raw.get("version")
    if isinstance(version, bool) or version not in SUPPORTED_VERSIONS:
        raise DeclarationError(f"version: 無いか未対応である: {version!r}（読めるのは {SUPPORTED_VERSIONS}）")
    steps = raw.get("steps")
    if not isinstance(steps, list):
        raise DeclarationError("steps: 段の配列で書く（必須）")
    out = []
    for i, s in enumerate(steps):
        where = f"steps[{i}]"
        if not isinstance(s, dict):
            raise DeclarationError(f"{where}: オブジェクトで書く")
        unknown = set(s) - STEP_KEYS
        if unknown:
            raise DeclarationError(f"{where}: 知らない項目: {', '.join(sorted(unknown))}")
        name = s.get("name")
        if not isinstance(name, str) or not name.strip():
            raise DeclarationError(f"{where}.name: 空でない文字列で書く（必須）")
        if s.get("stage") not in STAGES:
            raise DeclarationError(f"{where}.stage: {' / '.join(STAGES)} のどれかで書く（必須）: {s.get('stage')!r}")
        command = s.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
            raise DeclarationError(f"{where}.command: 空でない文字列の配列で書く（必須）")
        writes = s.get("writes")
        if not isinstance(writes, list):
            raise DeclarationError(f"{where}.writes: パスの前置きの配列で書く（必須。何も書かない段は []）")
        writes = [_relative(w, f"{where}.writes[{j}]") for j, w in enumerate(writes)]
        guide = s.get("guide")
        if guide is not None:
            guide = _relative(guide, f"{where}.guide")
        timeout = s.get("timeout_seconds", DEFAULT_TIMEOUT)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise DeclarationError(f"{where}.timeout_seconds: 1 以上の整数で書く: {timeout!r}")
        out.append(Step(name, s["stage"], command, writes, guide, timeout))
    return out


def load(root: Path) -> list[Step] | None:
    path = root / DECLARATION
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise DeclarationError(f"release.json: JSON として読めない: {e}") from e
    return parse(raw)


# ---------- 変更の判定 ----------

def _git(root: Path, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True,
                          input=stdin).stdout


def status_paths(root: Path) -> set[str]:
    """`git status` が挙げたパス。名前の変更は元と先の両方を返す。"""
    tokens = _git(root, "status", "--porcelain=v1", "-z", "-uall").split("\0")
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
        hashes = _git(root, "hash-object", "--stdin-paths", stdin="\n".join(present) + "\n").split()
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


def run_steps(root: Path, stage: str, version: str, dry_run: bool) -> int:
    steps = load(root)
    if steps is None:
        return 0
    for step in selected(steps, stage):
        command = expand(step, version)
        if dry_run:
            print(f"段: {step.name}（{step.stage}）")
            print(f"  command: {' '.join(command)}")
            print(f"  writes: {', '.join(step.writes) or '（何も書かない）'}")
            if step.guide:
                print(f"  guide: {step.guide}")
            continue
        try:
            before_paths = status_paths(root)
            before = digests(root, before_paths)
        except (OSError, subprocess.CalledProcessError) as e:
            print(f"段: {step.name} → 実行しない（git status を取れない: {e}）", file=sys.stderr)
            return 1
        sys.stdout.flush()
        try:
            rc = subprocess.run(command, cwd=str(root), timeout=step.timeout).returncode
        except subprocess.TimeoutExpired:
            print(f"段: {step.name} → 時間切れ（{step.timeout} 秒）")
            return 1
        except OSError as e:
            print(f"段: {step.name} → 起動できない: {e}")
            return 1
        after_paths = status_paths(root)
        union = before_paths | after_paths
        after = digests(root, union)
        for p in after_paths - before_paths:  # 段の前は変更が無かった = HEAD の内容
            before[p] = _head_digest(root, p)
        changed = sorted(p for p in union if before[p] != after[p])
        outside = [p for p in changed if not allowed(p, step.writes)]
        print(f"段: {step.name} → {rc}")
        for p in changed:
            print(f"  変えたパス: {p}{'（書いてよい場所の外）' if p in outside else ''}")
        if rc != 0:
            return 1
        if outside:
            print(f"段: {step.name} が書いてよい場所（{', '.join(step.writes) or 'なし'}）の外を変えた", file=sys.stderr)
            return 1
        if step.guide:
            print(f"guide: {step.guide}")
    return 0


def _head_digest(root: Path, path: str) -> str | None:
    """段の前に変更の無かったパスの要約（HEAD の内容。HEAD に無ければ None）。"""
    try:
        return _git(root, "rev-parse", "--verify", "-q", f"HEAD:{path}").strip() or None
    except subprocess.CalledProcessError:
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="リポジトリが宣言した配布の段を走らせる")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="段階に合う段を順に実行する")
    r.add_argument("--root", type=Path, default=Path("."))
    r.add_argument("--stage", choices=("production", "verification"), required=True)
    r.add_argument("--version", required=True)
    r.add_argument("--dry-run", action="store_true")
    c = sub.add_parser("check", help="宣言を読むだけ")
    c.add_argument("--root", type=Path, default=Path("."))
    args = ap.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.cmd == "check":
            return 0 if load(root) is not None else 2
        return run_steps(root, args.stage, args.version, args.dry_run)
    except DeclarationError as e:
        print(f"宣言を読めない（{DECLARATION}）: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
