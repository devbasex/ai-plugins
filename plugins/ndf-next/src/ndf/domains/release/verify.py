"""リリース後テストの手元の部品（利用者の手元で打つ）。

- `sync_base`: メインディレクトリのベースブランチを、マージの後の origin へそろえる（手は `sync_decide`）
- `snapshot`: 利用者の設定ディレクトリを読むだけで控える（行の作り方は `user_config_decide`）

利用者の設定ディレクトリは、Claude Code が `CLAUDE_CONFIG_DIR`（無ければ `~/.claude`）、Codex が `CODEX_HOME`
（無ければ `~/.codex`）である。読めないファイルは控えに入れず、読めなかったことを `Snapshot.unreadable` に残す。
"""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from ndf.adapters import git as git_mod
from ndf.domains.release import gather, sync_decide, user_config_decide

# 利用者の設定ディレクトリの環境変数と既定
CLAUDE_DIR_ENV, CODEX_DIR_ENV = "CLAUDE_CONFIG_DIR", "CODEX_HOME"
# 控えに読むファイル（設定ディレクトリから）
CLAUDE_INSTALLED = Path("plugins") / "installed_plugins.json"
CLAUDE_KNOWN = Path("plugins") / "known_marketplaces.json"
CODEX_CONFIG = Path("config.toml")
CODEX_CACHE = Path("plugins") / "cache"


class Snapshot(NamedTuple):
    items: dict[str, str]
    unreadable: tuple[str, ...]


def config_dirs() -> tuple[Path, Path]:
    claude = os.environ.get(CLAUDE_DIR_ENV)
    codex = os.environ.get(CODEX_DIR_ENV)
    return (Path(claude) if claude else Path.home() / ".claude", Path(codex) if codex else Path.home() / ".codex")


def _read(path: Path, parse: Callable[[str], Any], bad: list[str]) -> Any:
    """ファイルを読む。無ければ None（導入していない）。読めなければ `bad` へ足して None。"""
    if not path.is_file():
        return None
    try:
        return parse(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        bad.append(f"{path}: {exc}")
        return None


def _cached(cache: Path) -> dict[str, list[str]]:
    """Codex の導入の置き場（`<取得元>/<プラグイン>/<版>`）の `<プラグイン>@<取得元>` → 版。"""
    if not cache.is_dir():
        return {}
    plugins = sorted(p for p in cache.glob("*/*") if p.is_dir())
    return {f"{p.name}@{p.parent.name}": sorted(v.name for v in p.iterdir() if v.is_dir()) for p in plugins}


def _shaped(fn: Callable[[Any], dict[str, str]], value: Any, where: Path, bad: list[str]) -> dict[str, str]:
    if value is None:
        return {}
    try:
        return fn(value)
    except user_config_decide.ShapeError as exc:
        bad.append(f"{where}: {exc}")
        return {}


def snapshot() -> Snapshot:
    """利用者の設定ディレクトリの控え。"""
    return snapshot_at(*config_dirs())


def snapshot_at(claude: Path, codex: Path) -> Snapshot:
    """渡した設定ディレクトリ（Claude Code・Codex）の控え。"""
    bad: list[str] = []
    items = _shaped(user_config_decide.claude_plugins, _read(claude / CLAUDE_INSTALLED, json.loads, bad), claude / CLAUDE_INSTALLED, bad)
    items |= _shaped(user_config_decide.claude_marketplaces, _read(claude / CLAUDE_KNOWN, json.loads, bad), claude / CLAUDE_KNOWN, bad)
    conf = _read(codex / CODEX_CONFIG, tomllib.loads, bad)
    items |= user_config_decide.codex_entries(conf if isinstance(conf, dict) else {}, _cached(codex / CODEX_CACHE))
    return Snapshot(dict(sorted(items.items())), tuple(bad))


def _branch(repo_git: git_mod.Git) -> str | None:
    done = repo_git.attempt("symbolic-ref", "--quiet", "--short", "HEAD")
    return done.stdout.strip() if done.returncode == 0 else None


def _landed(repo_git: git_mod.Git, paths: list[str], remote_ref: str) -> list[str]:
    """変更のあるパスのうち、作業場所の中身が origin のベースブランチと同じもの（ハッシュで 1 回に比べる）。"""
    if not paths:
        return []
    local = repo_git.run("hash-object", "--", *paths).split()
    remote = repo_git.run("rev-parse", *[f"{remote_ref}:{p}" for p in paths]).split()
    return [p for p, a, b in zip(paths, local, remote) if a == b]


def sync_base(main_dir: Path, base: str) -> sync_decide.Plan:
    """メインディレクトリのベースブランチを origin へそろえる。止めたときは手 `stop` と、重なったパスを返す。"""
    repo_git = git_mod.Git(main_dir)
    gather.fetch(repo_git, base)
    remote_ref = f"{git_mod.ORIGIN}/{base}"
    dirty = repo_git.status_paths()
    incoming = repo_git.changed_files("HEAD", remote_ref)
    overlap = sorted(set(dirty) & set(incoming))
    existing = [p for p in overlap if (main_dir / p).is_file()]
    plan = sync_decide.plan(_branch(repo_git), base, dirty, incoming, _landed(repo_git, existing, remote_ref) if existing else [])
    if plan.action == sync_decide.UPDATE_REF:
        # 取り出さずに参照だけを進める。fast-forward でない・ほかの worktree が取り出していれば git が断る
        repo_git.run("fetch", "--quiet", "--no-tags", "--", git_mod.ORIGIN, f"refs/heads/{base}:refs/heads/{base}")
    elif plan.action == sync_decide.RESTORE_THEN_FF:
        repo_git.run("checkout", "--", *plan.paths)
        repo_git.run("merge", "--ff-only", "--quiet", remote_ref)
    elif plan.action == sync_decide.FAST_FORWARD:
        repo_git.run("merge", "--ff-only", "--quiet", remote_ref)
    return plan
