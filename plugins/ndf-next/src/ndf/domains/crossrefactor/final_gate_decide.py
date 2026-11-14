"""最終ゲートの判断。

- 最終ゲートの直し（全体テストの失敗を直すコミット）は、リファクタリング計画の対象範囲（`target_scope`）の中だけを変えてよい。
  範囲の外のファイルを変えたコミットは取り込まず、落ちたテストと変えたファイルを記録へ残して人へ渡す（#3038）。
  範囲の外に足した新しいファイルも範囲外に数える
- CI を待つ前に、Pull Request がベースブランチと衝突していないかを見る。衝突（`CONFLICTING`・`DIRTY`）している PR では
  GitHub が CI を 1 本も始めないため、待たずにベースブランチの取り込みへ戻す（#2680）
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ndf.contracts import causes
from ndf.domains.crossrefactor.diff_decide import in_scope

CONFLICT_MERGEABLE = frozenset({"CONFLICTING"})
CONFLICT_STATES = frozenset({"DIRTY"})
UNKNOWN_MERGEABLE = frozenset({"UNKNOWN", ""})


@dataclass(frozen=True)
class FinalFixVerdict:
    """直しのコミットの判定。`outside` は (コミット, 範囲外のファイル) の並び、`accepted` は範囲の中だけを変えたコミット。"""

    accepted: tuple[str, ...]
    outside: tuple[tuple[str, str], ...]

    @property
    def ok(self) -> bool:
        return not self.outside

    def cause(self) -> dict[str, Any]:
        """範囲外の直しで止まるときの止まった理由。不変条件で取り込みを断り（`refused`）、人へ渡す。"""
        files = ", ".join(sorted({path for _, path in self.outside}))
        return causes.cause("refused", f"最終ゲートの直しがリファクタリング計画の対象範囲の外を変えた: {files}")

    def record(self, failed_tests: Sequence[str]) -> dict[str, Any]:
        """最終ゲートの記録へ書く `outside_failures`（落ちたテストと、直しが変えた範囲外のファイル）。"""
        return {
            "failed_tests": list(failed_tests),
            "outside_files": sorted({path for _, path in self.outside}),
            "commits": sorted({sha for sha, _ in self.outside}),
        }


def verify_final_fix(changed_by_commit: Mapping[str, Iterable[str]], target_scope: Sequence[str]) -> FinalFixVerdict:
    """コミット → 変えたファイルの並び（古い順）から、範囲外を変えたコミットを分ける。対象範囲が空なら `ValueError`。"""
    if not target_scope:
        raise ValueError("リファクタリング計画の対象範囲（target_scope）が無い")
    scope = tuple(target_scope)
    accepted: list[str] = []
    outside: list[tuple[str, str]] = []
    for sha, paths in changed_by_commit.items():
        bad = [(sha, p) for p in sorted(set(paths)) if not in_scope(p, scope)]
        outside.extend(bad)
        if not bad:
            accepted.append(sha)
    return FinalFixVerdict(tuple(accepted), tuple(outside))


@dataclass(frozen=True)
class CiWait:
    """CI を待つ前の判定。`kind` は `wait`（待つ）・`conflict`（待たずに取り込みへ戻す）・`recheck`（GitHub がまだ計算中）。"""

    kind: str
    detail: str

    def cause(self) -> dict[str, Any] | None:
        """衝突なら止まった理由（`base_conflict`。プランはベースブランチの取り込みへ戻す）。ほかは None。"""
        return causes.cause("base_conflict", self.detail) if self.kind == "conflict" else None


def before_ci_wait(mergeable: str | None, merge_state: str | None) -> CiWait:
    """PR の `mergeable` と `mergeStateStatus` から、CI を待ってよいかを決める。"""
    m = (mergeable if mergeable is not None else "").upper()
    s = (merge_state if merge_state is not None else "").upper()
    if m in CONFLICT_MERGEABLE or s in CONFLICT_STATES:
        return CiWait("conflict", f"Pull Request がベースブランチと衝突している（mergeable: {m}・mergeStateStatus: {s}）。CI は始まらない")
    if m in UNKNOWN_MERGEABLE:
        return CiWait("recheck", "GitHub が衝突をまだ計算している")
    return CiWait("wait", "衝突していない")
