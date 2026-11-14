"""スプリントの閉じの判断（RF5b）: リリース記録の番号・導入の確認の要否・閉じの手順の並び。

閉じ（まとめ）の手順は、確定仕様化 → PR →（リリース記録があれば）リリース後テスト → 振り返りの判定 → 棚卸し →
マージ → 課題を閉じる、の順で並ぶ。並びと要否はここで決め、組み立てる側は値を写さずにこの結果を使う。

- リリース記録の番号 0 は「リリース記録なし」で、偽として読み捨てない（#2968）
- 導入の確認（`.ndf/pace.json` の `<進め方>.verify`）を要るのは、進め方が fast・auto で MVV の判定があり、
  本番かマージの経路があるときだけである。進め方 normal では読まない（#2967）
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Optional

from ndf.domains.release import version_decide

# リリース記録なし（閉じの引数の 0）
RECORD_NONE = "0"
# 閉じが本番の記録から番号を引く印（明示の番号が無く、届ける経路があるとき）
RECORD_FOUND = "{queue_record}"
# 導入の確認を置く進め方（MVV の判定で進める進め方）
MVV_PACES = ("fast", "auto")
# 経路の段（届け方）。本番へ上げる・検査がマージする・手で届ける・届けない
PROMOTE, MERGED_BY_CHECK, MANUAL, NONE = "promote", "merged-by-check", "manual", "none"
ROUTE_STAGES = (PROMOTE, MERGED_BY_CHECK, MANUAL, NONE)
# 閉じの手順の id（並びの順）
SPEC, PR, VERIFY, DEEP_DUE, RETROSPECTIVE, BACKLOG, MERGE, CLOSE_ISSUES = (
    "spec", "pr", "release-verification", "deep-due", "retrospective", "backlog", "merge", "close-issues",
)  # fmt: skip


class SprintError(ValueError):
    """閉じの引数の値が語彙に無い。"""


@dataclass(frozen=True)
class CloseFacts:
    """閉じを組む事実。"""

    pace: str
    routes: tuple[str, ...]
    mvv: bool = False
    record_pr: Optional[int] = None
    retrospective_due: bool = False


@dataclass(frozen=True)
class ClosePlan:
    """閉じの判断。"""

    steps: tuple[str, ...]
    record_pr: str
    verify: bool
    pace: str


def record_pr_of(given: Optional[int], fallback: str) -> str:
    """閉じに渡すリリース記録の番号。`given` が None のときだけ `fallback`。0 は `RECORD_NONE`（#2968）。"""
    if given is None:
        return fallback
    if given < 0:
        raise SprintError(f"リリース記録の番号は 0 以上: {given}")
    return str(given)


def has_record(record_pr: str) -> bool:
    """閉じが読むリリース記録があるか。"""
    return record_pr != RECORD_NONE


def record_fallback(routes: Sequence[str]) -> str:
    """明示の番号が無いときの番号。経路がすべて届けない経路なら記録なし、ほかは本番の記録から引く。"""
    return RECORD_NONE if all(r == NONE for r in routes) else RECORD_FOUND


def closing_pace(pace: str) -> str:
    """閉じが読む進め方の節。MVV の進め方（fast・auto）はそのまま、ほかは normal。"""
    return pace if pace in MVV_PACES else "normal"


def verify_needed(pace: str, mvv: bool, promote: bool, merge: bool) -> bool:
    """導入の確認の段を置くか。進め方が fast・auto で、MVV の判定があり、本番かマージの経路があるときだけ（#2967）。"""
    return pace in MVV_PACES and mvv and (promote or merge)


def prod_version(version: Optional[str]) -> Optional[str]:
    """明示の開発版から本番の版（開発版の印を外したもの）。開発版が無ければ None（本番は版の記録から読む）。"""
    return version_decide.base_of(version) if version else None


def close_plan(facts: CloseFacts, paces: Sequence[str]) -> ClosePlan:
    """閉じの手順の並びと、リリース記録の番号・導入の確認の要否。"""
    if facts.pace not in paces:
        raise SprintError(f"進め方 {facts.pace!r} は {'・'.join(paces)} のどれでもない")
    unknown = [r for r in facts.routes if r not in ROUTE_STAGES]
    if unknown or not facts.routes:
        raise SprintError(f"経路の段は {'・'.join(ROUTE_STAGES)} から 1 つ以上: {list(facts.routes)}")
    record = record_pr_of(facts.record_pr, record_fallback(facts.routes))
    verify = verify_needed(facts.pace, facts.mvv, PROMOTE in facts.routes, MERGED_BY_CHECK in facts.routes)
    steps = [SPEC, PR, *([VERIFY] if has_record(record) else []), DEEP_DUE,
             *([RETROSPECTIVE] if facts.retrospective_due else []), BACKLOG, MERGE, CLOSE_ISSUES]  # fmt: skip
    return ClosePlan(tuple(steps), record, verify, closing_pace(facts.pace))
