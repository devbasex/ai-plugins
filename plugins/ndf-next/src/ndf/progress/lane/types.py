"""レーンの進行表の型（#3097）: 計画の進行表とレーンの進行表の畳み込みの結果と、行の状態の語。DB を読まない。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

# 計画の進行表の中の、計画そのものの行の鍵（規則・上限・形の番号を持つ）。レーン名の形に当たらない語にする
PLAN_ROW = "@plan"
# 行の状態の語（契約 progress.json の row_states.lane。計画の状態 table_states.plan も同じ語）
PENDING, RUNNING, COMPLETED, CANCELLED = "pending", "running", "completed", "cancelled"
DONE_STATES = (COMPLETED, CANCELLED)
# レーンの状態の語のうち、行の状態に無いもの（after 待ち）
WAITING_AFTER = "waiting_after"


@dataclass(frozen=True)
class LaneSpec:
    """計画の進行表のレーンの行。`after` の要素は `<レーン>`（レーンの全行）か `<レーン>/<行の鍵>`。"""

    name: str
    table_id: str
    kind: str
    plans: int
    touches: tuple[str, ...]
    after: tuple[str, ...] = ()
    repo: Optional[str] = None


@dataclass(frozen=True)
class Request:
    """レーンの側からオーガナイザーへの依頼（`request_opened`）。閉じたら `closed` に理由を持つ。"""

    key: str
    lane: Optional[str]
    body: dict[str, Any]
    reason: Optional[str]
    closed: Optional[str] = None


@dataclass(frozen=True)
class PlanView:
    """計画の進行表の畳み込み。"""

    table_id: str
    name: str
    state: str
    rules: tuple[str, ...]
    max_lanes: int
    max_plans: int
    formats: dict[str, Any]
    ndf_version: Optional[str]
    lanes: dict[str, LaneSpec] = field(default_factory=dict)
    requests: dict[str, Request] = field(default_factory=dict)
    repo: Optional[str] = None

    def open_requests(self) -> list[Request]:
        return [r for r in self.requests.values() if r.closed is None]

    def lane_repo(self, name: str) -> Optional[str]:
        """レーンが動くリポジトリ。`repo` を宣言しないレーンは計画のリポジトリ（`repo`）で動く。

        同じリポジトリかの判定（計画の決まりの検査と起動）はこの値どうしを比べる唯一の規則である。
        """
        declared = self.lanes[name].repo
        return declared if declared is not None else self.repo


@dataclass(frozen=True)
class LaneRow:
    """レーンの進行表の行（スプリント 1 つ）。`answered_seq`・`resumed_seq` は答えと next の印の事象の番号。"""

    key: str
    input: dict[str, Any]
    state: str
    sprint_id: Optional[str] = None
    reason: Optional[str] = None
    answered_seq: int = 0
    answered_at: Optional[datetime] = None
    resumed_seq: int = 0

    @property
    def live(self) -> bool:
        return self.state in (PENDING, RUNNING)

    @property
    def awaiting_next(self) -> bool:
        """答えが載った後に conductor がまだ `ndf progress next` を打っていない実行中の行か。"""
        return self.state == RUNNING and self.answered_seq > self.resumed_seq

    def stalled(self, now: datetime, grace_seconds: float) -> bool:
        """答えの後の next 待ちのまま、答えが載ってから猶予の秒以上たったか（載った時刻が無ければ 0 秒とみなす）。"""
        if not self.awaiting_next:
            return False
        waited = (now - self.answered_at).total_seconds() if self.answered_at else 0.0
        return waited >= grace_seconds

    @property
    def args(self) -> list[Any]:
        """行の入力の args（無ければ空）。"""
        return list(self.input["args"]) if "args" in self.input else []

    def issues(self) -> set[int]:
        return set(self.input["issues"]) if self.input.get("kind") == "sprint" else set()


Lanes = Mapping[str, list[LaneRow]]
