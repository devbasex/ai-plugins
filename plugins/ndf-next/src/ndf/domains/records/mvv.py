"""MVV の記録を読み書きする口（N8b・#2603・#2614・#2777・#2970・#2853）。

記録は状態の置き場の mvv-state（表 places）の下の JSONL で、追記だけで持つ。記録の `repo` はメインディレクトリの
パスにする（worktree から書いても同じ鍵）。改訂の兆候は、ベースブランチの宣言を読んで数える。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from functools import cache
from pathlib import Path
from typing import Any, Optional

from ndf import identity
from ndf.adapters import git as git_mod
from ndf.adapters.github import PER_PAGE, GitHub, GitHubError
from ndf.contracts import facts, limits
from ndf.domains.records import files, jsonl
from ndf.domains.records import mvv_decide as md
from ndf.domains.records import mvv_material_decide as mm
from ndf.domains.records import usage_decide as ud
from ndf.progress.gate.checks import Changed, MvvFacts

# LLM の判定の関数: 照合の入力を受け、判定の語と費用・秒の実測値を返す
Judge = Callable[[Mapping[str, str]], tuple[str, Mapping[str, Any]]]


class MvvError(ValueError):
    """宣言が読めない・メインディレクトリを解けない。"""


def _state_dir() -> Path:
    var = facts.value("places", "mvv-state", "env")
    # 環境変数があれば identity を読まない。状態の置き場を解けなければ IdentityError のまま
    state = None if os.environ.get(var) else identity.load().state_dir
    return ud.state_place_dir(os.environ, var, state, facts.value("places", "mvv-state", "path"))


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


def decl_path() -> str:
    """MVV の宣言のパス（根からの相対）。"""
    return files.relpath("mvv")


def decl_at(root: Path, ref: str) -> dict[str, Any]:
    """参照（`<リモート名>/<開発の起点>` など）にある MVV の宣言を読む。読めなければ `MvvError`。"""
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
    decl = decl_at(root, md.signals_ref(base_branch, git_mod.Git(root).remote()))
    rows, _ = jsonl.read_rows(record_path("mvv-signals"))
    return md.count_signals(rows, decl, repo_key(root))


def worktree_version(root: Path) -> Optional[int]:
    """worktree の MVV の宣言の現行の版。宣言を読めなければ `MvvError`。"""
    try:
        raw = json.loads((root / decl_path()).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MvvError(f"worktree の MVV の宣言を読めない: {exc}") from exc
    if not isinstance(raw, dict):
        raise MvvError("worktree の MVV の宣言が object でない")
    return md.current_version(raw)


def record_answer(root: Path, answer: Mapping[str, Any], version: Optional[int]) -> list[dict[str, Any]]:
    """承認ゲートの答え（`verdict`・`approved`・`state`・`gate`・`pr`）を改訂の兆候の記録へ写し、書いた行を返す。

    「判定できない」なら人の答えによらず `unknown` の行を書き、覆し（と「判定できない」を人が通したもの）なら
    その種類の行を書く。どちらでもなければ書かない。`version` が無ければ worktree の宣言の現行の版で埋める
    （版の無い行は兆候に数えられないため）。
    """
    kinds = md.signal_kinds(answer.get("verdict"), bool(answer.get("approved")))
    if not kinds:
        return []
    at, repo = jsonl.now(), repo_key(root)
    got = version if version is not None else worktree_version(root)
    pair = {k: answer[k] for k in ("state", "gate", "pr") if k in answer}
    rows = [{"at": at, "kind": kind, "version": got, "repo": repo, **pair} for kind in kinds]
    path = record_path("mvv-signals")
    for row in rows:
        jsonl.append_locked(path, row)
    return rows


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
        row = md.reuse_row(origin, jsonl.now(), repo)
    else:
        verdict, cost = judge(inputs)
        row = md.judged_row(key, verdict, jsonl.now(), repo, cost)
    jsonl.append_locked(path, row)
    return row


def pr_files(hub: GitHub, number: int) -> list[dict[str, Any]]:
    """PR の変えたファイル（GitHub の files の行。`github.list-pages` ページまで）。超えれば `GitHubError`。"""
    pages = int(limits.value("github.list-pages", "count"))
    out: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        got = hub.rest("GET", f"/repos/{hub.repo}/pulls/{int(number)}/files", params={"per_page": PER_PAGE, "page": page})
        out += got
        if len(got) < PER_PAGE:
            return out
    raise GitHubError(f"PR #{number} の変更ファイルが {pages} ページを超えた（github.list-pages）")


def changed_of(rows: list[dict[str, Any]]) -> list[Changed]:
    """GitHub の files の行 → 機械の検査の変えたファイル（名前を変えた前のパスを持つ）。"""
    return [Changed(str(f["filename"]), f["previous_filename"] if "previous_filename" in f else None,
                    f["status"] == "renamed") for f in rows]  # fmt: skip


def pr_facts(hub: GitHub, number: int, base: MvvFacts, rows: Optional[list[dict[str, Any]]] = None) -> MvvFacts:
    """PR の本文と変えたファイルを読み、機械の検査の事実を埋める（検査 6 の `body`・`claimed`）。

    `rows` は読み済みの変えたファイル（`pr_files` の結果）。無ければここで読む。"""
    body = hub.pull(number).body
    return mm.facts_from_pr(base, body, changed_of(rows if rows is not None else pr_files(hub, number)))
