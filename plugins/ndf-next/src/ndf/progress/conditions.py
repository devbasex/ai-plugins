"""工程の定義の条件（`when`）の語のうち、進行表の外（git の差分と宣言のファイル・スプリントの文脈）から求める語。

`ndf.progress.run` の `Env.facts` の既定（`facts_of`）。進行表から求める語（`run.TABLE_FACTS`）はここに無い。
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ndf import identity
from ndf.adapters.git import Git, GitError
from ndf.progress import command


class FactUnknown(Exception):
    """工程の定義の条件（`when`）の語を、今の材料から求められない。"""

    def __init__(self, word: str) -> None:
        super().__init__(f"条件 {word} を求められない")
        self.word = word


WORKTREE_FILE = identity.FIXED_WORDS["declaration_dir"] + "/worktree.json"
DOC_FILES = ("**/*.md",)  # `.ndf/project.json` の `docs.files` を省いたときの文書のファイル
BEHAVIOR_DOCS = ("**/skills/**/*.md", "**/agents/*.md")  # 同じく `docs.behavior`（振る舞いを決める文書）


def _declaration(cwd: Path, name: str) -> dict[str, Any]:
    """リポジトリの根の宣言のファイル。無いか、オブジェクトでなければ空。"""
    path = Path(Git(cwd).run("rev-parse", "--show-toplevel").strip()) / name
    raw = command.read_json(path, "を読めない") if path.exists() else {}
    return raw if isinstance(raw, dict) else {}


def _dig(node: Any, *keys: str) -> Any:
    for key in keys:
        node = node.get(key) if isinstance(node, dict) else None
    return node


def diff_kinds(files: set[str], docs: set[str], behavior: set[str]) -> dict[str, bool]:
    """差分の語の値。振る舞いを決める文書は文書の部分集合。文書だけは、文書が 1 件以上でコードと振る舞いの文書が 0 件。"""
    code, behavior = files - docs, behavior & docs
    return {
        "code_in_diff": bool(code),
        "docs_only_diff": bool(files) and not code and not behavior,
        "behavior_docs_in_diff": bool(behavior),
    }


def _pathspecs(project: Mapping[str, Any], key: str, default: tuple[str, ...]) -> list[str]:
    """宣言のパターン（根からのパス全体に当てる glob。`!` は前が当てたものを外す）を git の pathspec にする。"""
    value = _dig(project, "docs", key)
    patterns = value if isinstance(value, list) and value and all(isinstance(p, str) for p in value) else default
    return [f":(exclude,glob){p[1:]}" if p.startswith("!") else f":(glob){p}" for p in patterns]


def _sprint_range(context: Mapping[str, Any], cwd: Path) -> tuple[Git, str]:
    """スプリントブランチとベースブランチ（`.ndf/worktree.json` の `base_branch`。手元に無ければ `origin/`）の分岐点からの範囲。"""
    base = _dig(_declaration(cwd, WORKTREE_FILE), "base_branch")
    if not isinstance(base, str) or not base or base.startswith("-"):
        raise FactUnknown(f"差分を求められない: {WORKTREE_FILE} の base_branch が無いか、- で始まる")
    git = Git(Path(context.get("worktree") or cwd))
    head = str(context.get("branch") or "HEAD")
    try:
        git.rev_parse(base)
        ref = base
    except GitError:
        ref = f"origin/{base}"
    return git, f"{git.merge_base(ref, head)}...{head}"


def _diff_names(git: Git, rng: str, specs: list[str]) -> set[str]:
    return {p for p in git.run("diff", "--name-only", "-z", rng, "--", *specs).split("\0") if p}


def sprint_diff_kinds(context: Mapping[str, Any], cwd: Path) -> dict[str, bool]:
    """スプリントの差分を、`.ndf/project.json` の `docs.files`・`docs.behavior`（省けば既定）で分ける。"""
    project = _declaration(cwd, command.PROJECT_FILE)
    try:
        git, rng = _sprint_range(context, cwd)
        files = _diff_names(git, rng, [])
        docs = _diff_names(git, rng, _pathspecs(project, "files", DOC_FILES))
        return diff_kinds(files, docs, _diff_names(git, rng, _pathspecs(project, "behavior", BEHAVIOR_DOCS)))
    except GitError as exc:
        raise FactUnknown(f"差分を求められない: {exc}") from exc


def facts_of(words: tuple[str, ...], context: Mapping[str, Any], cwd: Path) -> dict[str, bool]:
    """工程の定義の条件（`when`）の語のうち、進行表の外から求める語の値。求められない語は `FactUnknown`。"""
    kinds = functools.cache(lambda: sprint_diff_kinds(context, cwd))
    known: dict[str, Callable[[], bool]] = {
        **{w: (lambda w=w: kinds()[w]) for w in ("code_in_diff", "docs_only_diff", "behavior_docs_in_diff")},
        "ci_declared": lambda: _dig(_declaration(cwd, command.PROJECT_FILE), "test", "ci", "only") is True,
        "prod_branch": lambda: bool(_dig(_declaration(cwd, WORKTREE_FILE), "production_branch")),
        "release_record": lambda: "version" in context,
        "design_pr": lambda: "design_pr" in context,
    }
    unknown = [w for w in words if w not in known]
    if unknown:
        raise FactUnknown(f"条件 {unknown[0]} を求める手段が無い")
    out = {}
    for w in words:
        try:
            out[w] = known[w]()
        except FactUnknown as exc:
            raise FactUnknown(f"条件 {w} を求められない（{exc}）") from exc
    return out
