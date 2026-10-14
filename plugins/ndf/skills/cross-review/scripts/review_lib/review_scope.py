"""レビュー範囲と範囲の照合（#1702）。

レビュー範囲は、そのラウンドで担当が見る差分（base と head の SHA の組と、その間の変更ファイル）である。
`compute_scope` は `start-round` が呼び、`rounds[-1].review_scope` に書く値を返す。`check_scope` は `judge` が呼び、
通した担当の読んだファイル数（`read_files`）を範囲のファイル数と突き合わせた `scope_check` の値を返す。

どちらも例外や終了コードで止めない。範囲を求められないときは `unavailable` に理由を書いて返し、
起動は範囲の節なしで、判定は照合なしで進む（設計の I4）。
"""

from __future__ import annotations

import math
from typing import Any

import classifications  # noqa: E402
import proc  # noqa: E402
from review_lib import participants  # noqa: E402

# 通した担当を数えるのに要る読んだファイル数の比（設計の決定 3）。`ceil(files × 比)` 以上で数える。
READ_FILES_MIN_RATIO = 0.5


def _previous_head(rounds: list[dict[str, Any]], pr: Any) -> str:
    """同じ PR の 1 つ前のラウンドの head。最後の要素（今開くラウンド）は含めずに渡す。"""
    for r in reversed(rounds):
        if r.get("pr") == pr:
            return str(r.get("head_sha") or "")
    return ""


def _has_commit(worktree: str, sha: str) -> bool:
    return proc.git(worktree, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def compute_scope(
    worktree: str,
    head_oid: str | None,
    base_oid: str | None,
    kind: str,
    round_in_pr: int,
    previous_rounds: list[dict[str, Any]],
    pr: Any,
) -> dict[str, Any]:
    """そのラウンドのレビュー範囲を求める。`previous_rounds` は開く前までのラウンドの並び。"""
    if not head_oid:
        return {"unavailable": "no_head"}
    rng = classifications.diff_scope(kind, round_in_pr)
    base = ""
    if rng == "since_previous":
        base = _previous_head(previous_rounds, pr)
        if not base or base == head_oid:
            rng = "full"
    if rng == "full":
        base = str(base_oid or "")
        if not base:
            return {"unavailable": "no_base"}
    if not _has_commit(worktree, base):
        proc.git(worktree, "fetch", "--quiet", "origin", base, check=False)
        if not _has_commit(worktree, base):
            return {"unavailable": "base_missing"}
    out = proc.git_out(worktree, "diff", "--name-only", f"{base}...{head_oid}")
    if out is None:
        return {"unavailable": "diff_failed"}
    files = sum(1 for line in out.splitlines() if line.strip())
    return {"range": rng, "base_sha": base, "head_sha": head_oid, "files": files}


def min_read(files: int) -> int:
    """通すのに要る読んだファイル数。"""
    return math.ceil(files * READ_FILES_MIN_RATIO)


def check_scope(round_entry: dict[str, Any], reviewers: list[str], only: str | None) -> dict[str, Any]:
    """通した担当の読んだファイル数を範囲のファイル数と突き合わせる（副作用なし）。"""
    scope = round_entry.get("review_scope")
    if not isinstance(scope, dict):
        return {"checked": False, "reason": "legacy", "unmatched": []}
    if scope.get("unavailable"):
        return {"checked": False, "reason": str(scope["unavailable"]), "unmatched": []}
    if round_entry.get("stage") == "model":
        return {"checked": False, "reason": "model_stage", "unmatched": []}
    files = int(scope.get("files") or 0)
    need = min_read(files)
    unmatched: list[dict[str, Any]] = []
    for seat in reviewers:
        if participants._skipped_by_only(seat, only):
            continue
        entry = round_entry.get(seat) or {}
        if not participants._is_pass(participants._agent_intent(round_entry, seat, only), entry.get("by_severity")):
            continue
        read = entry.get("read_files")
        if not _is_count(read):
            unmatched.append({"seat": seat, "read_files": None, "reason": str(entry.get("read_files_reason") or "missing")})
        elif read < need:
            unmatched.append({"seat": seat, "read_files": read, "reason": "too_few"})
    return {"checked": True, "files": files, "min_read": need, "unmatched": unmatched}


def _is_count(value: Any) -> bool:
    """0 以上の整数か（真偽値は数えない）。"""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def parse_read_files(result: dict[str, Any]) -> tuple[int | None, str | None]:
    """結果ファイルの `read_files` を検証する。整数なら `(値, None)`、測れなければ `(None, 理由)`。"""
    if "read_files" not in result:
        return None, "missing"
    value = result.get("read_files")
    if _is_count(value):
        return value, None
    return None, "not_integer"


def unmatched_text(check_result: dict[str, Any]) -> str:
    """`SCOPE_UNMATCHED` の値（`kiro=1/16 codex=missing/16`）。"""
    files = check_result.get("files", 0)
    parts = []
    for u in check_result.get("unmatched") or []:
        read = u["read_files"] if u.get("reason") == "too_few" else u.get("reason")
        parts.append(f"{u['seat']}={read}/{files}")
    return " ".join(parts)
