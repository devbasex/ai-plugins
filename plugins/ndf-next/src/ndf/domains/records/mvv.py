"""MVV の記録を読み書きする口（N8b・#2603・#2614・#2777・#2970・#2853）。

記録は状態の置き場の mvv-state（表 places）の下の JSONL で、追記だけで持つ。記録の `repo` はメインディレクトリの
パスにする（worktree から書いても同じ鍵）。改訂の兆候は、ベースブランチの宣言を読んで数える。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any, Optional

from ndf import identity
from ndf.adapters import git as git_mod
from ndf.adapters.github import PER_PAGE, GitHub, GitHubError
from ndf.contracts import facts, limits
from ndf.domains.records import jsonl
from ndf.domains.records import mvv_decide as md
from ndf.domains.records import mvv_material_decide as mm
from ndf.progress.gate.checks import Changed, MvvFacts

# LLM の判定の関数: 照合の入力を受け、判定の語と費用・秒の実測値を返す
Judge = Callable[[Mapping[str, str]], tuple[str, Mapping[str, Any]]]


class MvvError(ValueError):
    """宣言が読めない・メインディレクトリを解けない。"""


def _state_dir() -> Path:
    var = facts.value("places", "mvv-state", "env")
    if os.environ.get(var):
        return Path(os.environ[var])
    return identity.load().state_dir / facts.value("places", "mvv-state", "path")


def record_path(file_id: str) -> Path:
    """記録のファイル（表 ndf-files の mvv-vet・mvv-signals）。"""
    return _state_dir() / facts.value("ndf-files", file_id, "path")


@cache
def repo_key(root: Path) -> str:
    """記録の `repo`（メインディレクトリの絶対パス。worktree から呼んでも同じ）。git の外なら `MvvError`。

    git は 1 回だけ起動する（プロセスの中で覚える）。
    """
    try:
        entries = git_mod.Git(root).worktrees()
    except git_mod.GitError as exc:
        raise MvvError(f"メインディレクトリを解けない: {exc}") from exc
    if not entries:
        raise MvvError(f"メインディレクトリを解けない: {root}")
    return str(entries[0].path)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def decl_path() -> str:
    """MVV の宣言のパス（根からの相対）。"""
    place = facts.value("ndf-files", "mvv", "place")
    return f"{facts.value('places', place, 'path')}/{facts.value('ndf-files', 'mvv', 'path')}"


def decl_at(root: Path, ref: str) -> dict[str, Any]:
    """参照（`origin/<開発の起点>` など）にある MVV の宣言を読む。読めなければ `MvvError`。"""
    try:
        text = git_mod.Git(root).run("show", f"{ref}:{decl_path()}")
        raw = json.loads(text)
    except (git_mod.GitError, json.JSONDecodeError) as exc:
        raise MvvError(f"{ref} の MVV の宣言を読めない: {exc}") from exc
    if not isinstance(raw, dict):
        raise MvvError(f"{ref} の MVV の宣言が object でない")
    return raw


def signals(root: Path, base_branch: str) -> md.Signals:
    """ベースブランチの宣言の現行の版で、改訂の兆候を数える（本番のブランチの古い版で数えない）。"""
    decl = decl_at(root, md.signals_ref(base_branch, git_mod.ORIGIN))
    rows, _ = jsonl.read_rows(record_path("mvv-signals"))
    return md.count_signals(rows, decl, repo_key(root))


def record_answer(root: Path, answer: Mapping[str, Any], version: Optional[int]) -> Optional[dict[str, Any]]:
    """承認ゲートの答え（`verdict`・`approved`・`state`・`gate`・`pr`）を覆しの記録へ写す。覆しでなければ書かずに None。"""
    kind = md.override_kind(answer.get("verdict"), bool(answer.get("approved")))
    if kind is None:
        return None
    row = {"at": _now(), "kind": kind, "version": version, "repo": repo_key(root)}
    row |= {k: answer[k] for k in ("state", "gate", "pr") if k in answer}
    jsonl.append_locked(record_path("mvv-signals"), row)
    return row


def vet_sprint(root: Path, inputs: Mapping[str, str], judge: Judge, reuse: bool = True) -> dict[str, Any]:
    """スプリント MVV を照合し、記録に足した行を返す。

    `reuse` なら、同じ照合の入力の最新の判定が「従う」の記録を再利用して LLM を呼ばない。記録が無い・壊れた行を
    含んでも止まらず LLM で判定する。`project-mvv vet` に当たる呼び手は `reuse=False` で毎回 LLM を呼ぶ。
    """
    key = md.vet_key(inputs)
    path = record_path("mvv-vet")
    repo = repo_key(root)
    rows, _ = jsonl.read_rows(path)
    origin = md.reusable(rows, key) if reuse else None
    if origin is not None:
        row = md.reuse_row(origin, _now(), repo)
    else:
        verdict, cost = judge(inputs)
        row = md.judged_row(key, verdict, _now(), repo, cost)
    jsonl.append_locked(path, row)
    return row


def _changed(hub: GitHub, number: int) -> list[Changed]:
    """PR の変えたファイル（`github.list-pages` ページまで）。超えれば `GitHubError`。"""
    pages = int(limits.value("github.list-pages", "count"))
    out: list[Changed] = []
    for page in range(1, pages + 1):
        files = hub.rest("GET", f"/repos/{hub.repo}/pulls/{int(number)}/files", params={"per_page": PER_PAGE, "page": page})
        out += [Changed(str(f["filename"]), f["previous_filename"] if "previous_filename" in f else None,
                        f["status"] == "renamed") for f in files]  # fmt: skip
        if len(files) < PER_PAGE:
            return out
    raise GitHubError(f"PR #{number} の変更ファイルが {pages} ページを超えた（github.list-pages）")


def pr_facts(hub: GitHub, number: int, base: MvvFacts) -> MvvFacts:
    """PR の本文と変えたファイルを読み、機械の検査の事実を埋める（検査 6 の `body`・`claimed`）。"""
    return mm.facts_from_pr(base, hub.pull(number).body, _changed(hub, number))
