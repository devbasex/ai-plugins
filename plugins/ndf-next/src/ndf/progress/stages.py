"""工程の定義の型と、定義の順序の決まり・有効と無効の決め方。

工程の定義の型（`Snapshot`・`StageDef`・`StepDef`）と、定義の順序の決まり（`check_order`）・
有効と無効の決め方（`resolve_enabled`）を置く。ファイルを読む検査は `definition` にある。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Optional

from ndf import contracts
from ndf.progress import model


# --- 工程の定義の型 ----------------------------------------------------------------------


@dataclass(frozen=True)
class StepDef:
    """工程の中のステップ（DBOS のステップ 1 つ）。`output_ref` は出力の規約の参照。"""

    id: str
    stage: str
    call: str
    kind: str
    range: str
    subject: Optional[str] = None
    gate: Optional[str] = None
    args: dict[str, Any] = field(default_factory=dict)
    output_ref: Optional[str] = None


@dataclass(frozen=True)
class StageDef:
    """工程（工程表の行）。`name` が行の鍵、`id` は工程の文書の名前。"""

    id: str
    name: str
    phase: str
    steps: tuple[StepDef, ...]
    when: tuple[str, ...] = ()
    per_issue: bool = False
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    input_ref: Optional[str] = None
    output_ref: Optional[str] = None

    @property
    def gates(self) -> tuple[str, ...]:
        """工程が持つ承認ゲートの種類。1 つでもあれば工程を無効にできない（3364-2）。"""
        return tuple(st.gate for st in self.steps if st.gate)


@dataclass(frozen=True)
class Snapshot:
    """モードの定義のスナップショット。進行表の定義の改訂に JSON で残す。"""

    mode: str
    version: str
    sha256: str
    height: int
    stages: tuple[StageDef, ...]

    def stage(self, name: str) -> StageDef:
        for s in self.stages:
            if s.name == name:
                return s
        raise model.Refused(f"モード {self.mode} の工程に {name} は無い")

    def steps(self) -> list[StepDef]:
        return [st for s in self.stages for st in s.steps]


@dataclass(frozen=True)
class Enablement:
    """工程の有効と無効。`source` は決めた順位（definition・project・sprint）。"""

    enabled: bool
    reason: Optional[str]
    source: str


def _plain(value: Any) -> Any:
    """tuple を JSON の配列にする。"""
    return list(value) if isinstance(value, tuple) else value


def _frozen(value: Any) -> Any:
    """JSON の配列を tuple にする（`_plain` の逆）。"""
    return tuple(value) if isinstance(value, list) else value


def _stage_dict(s: StageDef) -> dict[str, Any]:
    steps = [{k: v for k, v in vars(st).items() if k != "stage"} for st in s.steps]
    return {**{k: _plain(v) for k, v in vars(s).items() if k != "steps"}, "steps": steps}


def _stage_from(raw: Mapping[str, Any]) -> StageDef:
    steps = tuple(StepDef(stage=raw["name"], **st) for st in raw["steps"])
    rest = {k: _frozen(v) for k, v in raw.items() if k != "steps"}
    return StageDef(steps=steps, **rest)


def snapshot_dict(snap: Snapshot) -> dict[str, Any]:
    """スナップショットを JSON の形にする（定義の改訂に書く）。"""
    return {
        "mode": snap.mode,
        "version": snap.version,
        "sha256": snap.sha256,
        "height": snap.height,
        "stages": [_stage_dict(s) for s in snap.stages],
    }


def snapshot_from(raw: Mapping[str, Any]) -> Snapshot:
    """`snapshot_dict` の逆。"""
    return Snapshot(raw["mode"], raw["version"], raw["sha256"], raw["height"], tuple(_stage_from(s) for s in raw["stages"]))


# --- 順序の決まり（I8〜I10） ---------------------------------------------------------------


# 範囲を差分に限るステップの種類（review・test）と、その範囲。verify の範囲はリリースした版
CHECKED_KINDS = ("review", "test")
DIFF_RANGES = ("sprint_diff", "issue_diff")
RELEASED = "released"


def _where(step: StepDef) -> str:
    return f"{step.stage}/{step.id}"


def _range_errors(steps: list[StepDef]) -> list[str]:
    """review・test の範囲は差分だけ、verify の範囲はリリースした版だけ（I9）。"""
    errs = []
    for st in steps:
        if st.kind in CHECKED_KINDS and st.range not in DIFF_RANGES:
            errs.append(f"I9: {_where(st)} の {st.kind} の範囲 {st.range} が差分（sprint_diff・issue_diff）でない")
        if st.kind == "verify" and st.range != RELEASED:
            errs.append(f"I9: {_where(st)} の verify の範囲 {st.range} がリリースした版（released）でない")
    return errs


def _after_release_errors(steps: list[StepDef]) -> list[str]:
    """同じ範囲の review・test がリリースより後ろにある（I8a）。verify がリリースより前・リリースが無い（I8b）。"""
    errs = []
    released: dict[str, StepDef] = {}
    for st in steps:
        if st.kind in CHECKED_KINDS and st.range in released:
            errs.append(
                f"I8a: {_where(st)} の {st.kind} が、同じ範囲 {st.range} をリリースする {_where(released[st.range])} より後ろにある"
            )
        if st.kind == "verify" and not released:
            errs.append(f"I8b: {_where(st)} の verify より前にリリースのステップが無い")
        if st.kind == "release":
            released.setdefault(st.range, st)
    return errs


def _duplicate_errors(steps: list[StepDef]) -> list[str]:
    """同じ（範囲, 対象）の review が 2 つ・同じ範囲の test が 2 つ（I10）。"""
    reviews = Counter((st.range, st.subject) for st in steps if st.kind == "review")
    tests = Counter(st.range for st in steps if st.kind == "test")
    errs = [f"I10: 範囲 {r} の対象 {s} のレビューが {n} つある" for (r, s), n in sorted(reviews.items(), key=str) if n > 1]
    return errs + [f"I10: 範囲 {r} のテストが {n} つある" for r, n in sorted(tests.items()) if n > 1]


def check_order(steps: list[StepDef]) -> list[str]:
    """モードの全ステップを並びの順に平らにした一覧で、順序・範囲・重複の決まりを確かめ、破った理由を返す。"""
    return _range_errors(steps) + _after_release_errors(steps) + _duplicate_errors(steps)


def check_unique_stage_names(stages: Iterable[StageDef]) -> list[str]:
    """同じ工程の名前を 2 つの工程が受け持たない（工程の定義の登録の決まり）。"""
    return [f"工程の名前 {n} を 2 つの工程が受け持つ" for n in contracts.duplicates(s.name for s in stages)]


def gate_refusal(stage: StageDef) -> str:
    """承認ゲートの工程を無効にしようとしたときの断りの理由。"""
    kinds = "・".join(model.gate_label(g) for g in stage.gates)
    return f"{stage.name} は承認ゲート（{kinds}）を持つので無効にできない"


def check_gate_conditions(stages: Iterable[StageDef]) -> list[str]:
    """承認ゲートの工程は定義の条件（when）を持たない。条件が偽になると工程が無効になるため。"""
    return [f"{gate_refusal(s)}: 定義の条件 when を外す" for s in stages if s.gates and s.when]


# --- 有効と無効（決定 8） -------------------------------------------------------------------


def _by_definition(stage: StageDef, facts: Optional[Mapping[str, bool]]) -> Enablement:
    if facts is None:
        return Enablement(True, None, "definition")
    for word in stage.when:
        if not facts[word]:
            return Enablement(False, word, "definition")
    return Enablement(True, None, "definition")


def resolve_enabled(
    snap: Snapshot,
    facts: Optional[Mapping[str, bool]],
    declared: Mapping[str, Mapping[str, Any]],
    disables: Mapping[str, str],
) -> dict[str, Enablement]:
    """工程ごとの有効と理由を 3 つの順位で決める。スプリントの引数 > プロジェクトの宣言 > 定義の条件。

    `facts` が None なら定義の条件はまだ見ない（進行表の作成の時点。条件は工程を流すときに見る）。
    `declared` は `.ndf/project.json` の `workflow.stages`（工程の名前 → `{"enabled": 真偽, "reason": 理由}`）。
    """
    out = {}
    for stage in snap.stages:
        off_by_project = stage.name in declared and not declared[stage.name]["enabled"]
        if stage.gates and (stage.name in disables or off_by_project):
            raise model.Refused(gate_refusal(stage))
        if stage.name in disables:
            out[stage.name] = Enablement(False, disables[stage.name], "sprint")
        elif stage.name in declared:
            decl = declared[stage.name]
            enabled = bool(decl["enabled"])
            out[stage.name] = Enablement(enabled, None if enabled else str(decl["reason"]), "project")
        else:
            out[stage.name] = _by_definition(stage, facts)
    return out


def unknown_stages(snap: Snapshot, names: Iterable[str]) -> list[str]:
    """モードの工程に無い名前。"""
    known = {s.name for s in snap.stages}
    return sorted(set(names) - known)


OUTPUTS_EVENT = "outputs_recorded"  # 工程の出力をスプリントの文脈へ書く事象


def outputs_draft(stage: StageDef, rev: int, outputs: Mapping[str, Any], actor: str) -> tuple[Optional[model.EventDraft], list[str]]:
    """工程の出力を文脈へ書く事象の下書き。鍵は工程の定義の `outputs` に列挙したものだけ。

    `actor` は書き手。工程を流した作業（`workflow`）か、手で終えた conductor（`ndf progress finish`）。
    宣言に無い鍵は書かずに捨て、捨てた鍵を警告で返す。書く鍵が無ければ下書きは None。
    """
    kept = {k: v for k, v in outputs.items() if k in stage.outputs}
    warnings = [f"工程 {stage.name} の定義の outputs に無い鍵を捨てた: {k}" for k in sorted(set(outputs) - set(kept))]
    if not kept:
        return None, warnings
    return model.EventDraft(OUTPUTS_EVENT, actor, stage.name, payload={"outputs": kept}, rev=rev), warnings
