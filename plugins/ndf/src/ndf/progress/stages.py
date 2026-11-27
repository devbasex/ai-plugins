"""工程の定義の型と、定義の順序の決まり・有効と無効の決め方。

工程の定義の型（`Snapshot`・`StageDef`・`StepDef`）と、定義の順序の決まり（`check_order`）・
有効と無効の決め方（`resolve_enabled`）を置く。ファイルを読む検査は `definition` にある。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Optional

from ndf import contracts
from ndf.contracts import internal, result
from ndf.progress import model


# --- 工程の定義の型 ----------------------------------------------------------------------


# 承認ゲートのステップの種類と、その直前に置く読み手役のステップの種類（`gate` を書いてよい種類の並び。#4446）
GATE_KIND, READING_KIND = contracts.schema_enum(model.NDF_SCHEMA, "/$defs/gated_kind")


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
    """工程（工程表の行）。`name` が行のキー、`id` は工程の文書の名前。"""

    id: str
    name: str
    phase: str
    steps: tuple[StepDef, ...]
    when: tuple[str, ...] = ()
    per_issue: bool = False
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    # 文脈にあるときだけ読むキー（無くても工程を流す。#3879 の gathered）
    optional_inputs: tuple[str, ...] = ()
    input_ref: Optional[str] = None
    output_ref: Optional[str] = None
    # 実装計画のレビューの単位ごとに流す工程（#4369）
    per_unit: bool = False

    @property
    def gates(self) -> tuple[str, ...]:
        """工程が持つ承認ゲートの種類。1 つでもあれば工程を無効にできない（3364-2）。読み手役のステップの `gate` は数えない（#4446）。"""
        return tuple(st.gate for st in self.steps if st.kind == GATE_KIND and st.gate)

    def before(self, step_id: str) -> Optional[str]:
        """ステップ `step_id` の直前のステップの id（先頭か、工程に無ければ None）。"""
        ids = [st.id for st in self.steps]
        at = ids.index(step_id) if step_id in ids else 0
        return ids[at - 1] if at > 0 else None

    def mvv_before(self, step_id: str) -> Optional[str]:
        """承認ゲート `step_id` の前の MVV 判定のステップの id。読み手役のステップがあればその直前、無ければ承認ゲートの直前（#4446）。"""
        gate = next((st.gate for st in self.steps if st.id == step_id), None)
        reading = self.reading_for(gate) if gate else None
        return self.before(reading.id if reading is not None else step_id)

    def gate_index(self, gate: Optional[str]) -> Optional[int]:
        """承認ゲート `gate` のステップの、工程の中の位置。無ければ None。"""
        return next((i for i, st in enumerate(self.steps) if st.kind == GATE_KIND and st.gate == gate), None)

    def reading_for(self, gate: Optional[str]) -> Optional[StepDef]:
        """同じ工程で、承認ゲート `gate` の前の読み手役のステップ。無ければ None（読み手役のステップの無い古い定義）。"""
        return next((st for st in self.steps if st.kind == READING_KIND and st.gate == gate), None)


@dataclass(frozen=True)
class Snapshot:
    """モードの定義のスナップショット。ステートマシンの定義の改訂に JSON で残す。"""

    mode: str
    definition_version: str
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
        "version": internal.version(),
        "mode": snap.mode,
        "definition_version": snap.definition_version,
        "sha256": snap.sha256,
        "height": snap.height,
        "stages": [_stage_dict(s) for s in snap.stages],
    }


def snapshot_from(raw: Mapping[str, Any]) -> Snapshot:
    """`snapshot_dict` の逆。`raw` は核（`internal.read_record` の `stage-snapshot`）を通した今の形。"""
    defs = tuple(_stage_from(s) for s in raw["stages"])
    return Snapshot(raw["mode"], raw["definition_version"], raw["sha256"], raw["height"], defs)


# --- 順序の決まり（I8〜I10） ---------------------------------------------------------------


# 範囲を差分に限るステップの種類（review・test）と、その範囲。verify の範囲はリリースしたバージョン
CHECKED_KINDS = contracts.schema_enum(model.NDF_SCHEMA, "/$defs/checked_kind")
# つなぎ目（文脈の gathered から後のコミット。#3879）
UNREVIEWED_DIFF = "unreviewed_diff"
DIFF_RANGES = ("sprint_diff", "issue_diff", UNREVIEWED_DIFF)
RELEASED = "released"
# 範囲 → その範囲を含んで届けるリリースの範囲（つなぎ目はスプリントの差分の一部で、同じリリースで届く。I8a）
RELEASED_WITH = {UNREVIEWED_DIFF: "sprint_diff"}


def _where(step: StepDef) -> str:
    return f"{step.stage}/{step.id}"


def _range_errors(steps: list[StepDef]) -> list[str]:
    """review・test の範囲は差分だけ、verify の範囲はリリースしたバージョンだけ（I9）。"""
    errs = []
    for st in steps:
        if st.kind in CHECKED_KINDS and st.range not in DIFF_RANGES:
            errs.append(f"I9: {_where(st)} の {st.kind} の範囲 {st.range} が差分（{'・'.join(DIFF_RANGES)}）でない")
        if st.kind == "verify" and st.range != RELEASED:
            errs.append(f"I9: {_where(st)} の verify の範囲 {st.range} がリリースしたバージョン（released）でない")
    return errs


def _after_release_errors(steps: list[StepDef]) -> list[str]:
    """同じ範囲の review・test がリリースより後ろにある（I8a）。verify がリリースより前・リリースが無い（I8b）。"""
    errs = []
    released: dict[str, StepDef] = {}
    for st in steps:
        rng = RELEASED_WITH[st.range] if st.range in RELEASED_WITH else st.range
        if st.kind in CHECKED_KINDS and rng in released:
            part = "" if rng == st.range else f"（{st.range} を含む）"
            errs.append(f"I8a: {_where(st)} の {st.kind} が、同じ範囲 {rng}{part} をリリースする {_where(released[rng])} より後ろにある")
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


# --- 読み手役のステップと設計のレビューへの戻し（#4446） -------------------------------------------------


# ステップを未了に戻す記録の status・読み手役が文書を直して push した出力のキー・読み手役のステップの結果ファイルの名前
RESET, REVIEW_AGAIN, READING_OUT = "reset", "review_again", "gate.result.json"


def review_again_of(report: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """読み手役のステップの結果の `outputs.review_again`（直した文書を push したコミットと文書のパス）。無ければ None。"""
    outputs = report["outputs"] if isinstance(report.get("outputs"), dict) else {}
    held = outputs[REVIEW_AGAIN] if REVIEW_AGAIN in outputs else None
    return dict(held) if isinstance(held, dict) and isinstance(held.get("commit"), str) else None


def review_again_steps(stage: StageDef, gate: Optional[str]) -> list[str]:
    """設計のレビューへ戻すときに未了に戻すステップ。承認ゲート `gate` の前の最後のレビューから、ゲートの直前（読み手役か、
    読み手役の無い古い定義では MVV 判定）まで。"""
    ids = [st.id for st in stage.steps]
    found = stage.gate_index(gate)
    at = len(ids) if found is None else found
    reviews = [i for i, st in enumerate(stage.steps[:at]) if st.kind == "review"]
    return ids[reviews[-1] if reviews else max(at - 1, 0) : at]


def reading_flags(stage: StageDef, again: Sequence[Mapping[str, Any]], finished_at: Mapping[str, int]) -> list[str]:
    """読み手役のステップ（`ndf document-rebuild gate`）へ足す引数。`again` は同じ承認ゲートの前に戻した記録（事象の番号 `seq`・
    コミット）、`finished_at` はステップごとの最後の終わりの事象の番号。最後に戻した後に設計のレビューが済んでいれば
    `--reviewed <コミット>` を、戻した記録が 1 件以上あれば `--no-doc-fix`（往復の上限）を足す。"""
    if not again:
        return []
    last = again[-1]
    reviewed = any(st.id in finished_at and finished_at[st.id] > last["seq"] for st in stage.steps if st.kind == "review")
    return [*(["--reviewed", str(last["commit"])] if reviewed else []), "--no-doc-fix"]


def _reading_errors(steps: list[StepDef]) -> list[str]:
    """読み手役のステップの直後は、同じ工程の同じ種類の承認ゲートにする（#4446）。"""
    errs = []
    for at, st in enumerate(steps):
        nxt = steps[at + 1] if at + 1 < len(steps) else None
        if st.kind == READING_KIND and (nxt is None or nxt.stage != st.stage or nxt.kind != GATE_KIND or nxt.gate != st.gate):
            errs.append(f"I11: {_where(st)} の読み手役のステップの直後が、同じ工程の承認ゲート {st.gate} でない")
    return errs


def check_order(steps: list[StepDef]) -> list[str]:
    """モードの全ステップを並びの順に平らにした一覧で、順序・範囲・重複の決まりを確かめ、破った理由を返す。"""
    return _range_errors(steps) + _after_release_errors(steps) + _duplicate_errors(steps) + _reading_errors(steps)


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
    """工程ごとの有効と理由を 3 つの順位で決める。スプリントの引数 > プロジェクトの設定 > 定義の条件。

    `facts` が None なら定義の条件はまだ見ない（ステートマシンの作成の時点。条件は工程を流すときに見る）。
    `declared` は `.ndf/project.json` の `workflow.stages`（工程の名前 → `{"enabled": 真偽, "reason": 理由}`）。
    """
    return {stage.name: _enablement(stage, facts, declared, disables) for stage in snap.stages}


def _refuse_disabled_gate(stage: StageDef, declared: Mapping[str, Mapping[str, Any]], disables: Mapping[str, str]) -> None:
    """承認ゲートの工程を、スプリントの引数かプロジェクトの設定で無効にしようとしていれば断る。"""
    off_by_project = stage.name in declared and not declared[stage.name]["enabled"]
    if stage.gates and (stage.name in disables or off_by_project):
        raise model.Refused(gate_refusal(stage))


def _by_project(decl: Mapping[str, Any]) -> Enablement:
    enabled = bool(decl["enabled"])
    return Enablement(enabled, None if enabled else str(decl["reason"]), "project")


def _enablement(
    stage: StageDef,
    facts: Optional[Mapping[str, bool]],
    declared: Mapping[str, Mapping[str, Any]],
    disables: Mapping[str, str],
) -> Enablement:
    """工程 1 つの有効と理由（スプリントの引数 > プロジェクトの設定 > 定義の条件）。"""
    _refuse_disabled_gate(stage, declared, disables)
    if stage.name in disables:
        return Enablement(False, disables[stage.name], "sprint")
    if stage.name in declared:
        return _by_project(declared[stage.name])
    return _by_definition(stage, facts)


def enabled_by_project(saved: Mapping[str, Mapping[str, Any]], stage: str) -> bool:
    """作成の時点で保存した enablement で、プロジェクトの設定が工程を有効にしていたか（設定 > 定義の条件）。"""
    en = saved.get(stage)
    return en is not None and en["source"] == "project" and en["enabled"] is True


def unknown_stages(snap: Snapshot, names: Iterable[str]) -> list[str]:
    """モードの工程に無い名前。"""
    known = {s.name for s in snap.stages}
    return sorted(set(names) - known)


OUTPUTS_EVENT = "outputs_recorded"  # 工程の出力をスプリントの文脈へ書く事象


def recorded_outputs(events: Iterable[model.Event], rev: int) -> list[Mapping[str, Any]]:
    """改訂 `rev` で工程が書いた出力（古い順）。"""
    drafts = [ev.draft for ev in events if ev.draft.kind == OUTPUTS_EVENT and ev.draft.rev == rev]
    return [d.payload["outputs"] for d in drafts if d.payload is not None]


def compose_context(initial: Mapping[str, Any], outputs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """スプリントの文脈。作成の時点の値に、工程が書いた出力を順に重ねる。どちらも核が今の形へ移し、新しいバージョンなら `InternalError`。

    出力は文脈の一部のキーだけで形のバージョンを持たないので、文脈のバージョンは作成の時点の値のものにする。"""
    out = internal.read_record("context", initial)
    version = out["version"]
    for raw in outputs:
        out.update(internal.migrate("context", raw))
    return {**out, "version": version}


def keep_declared(declared: Iterable[str], outputs: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """出力のうち定義したキーだけを残した辞書と、設定に無いキー（並べ替えたもの）。`outputs` は書き換えない。"""
    names = set(declared)
    kept = {k: v for k, v in outputs.items() if k in names}
    return kept, sorted(set(outputs) - set(kept))


# 結果の items の PR で補う工程の出力のキー。プラン design は設計 PR を items にだけ載せ、outputs に design_pr を
# 書かない（共通のステップ pr は番号を items に返す）。補わないと設計承認ゲートの質問が PR を持たず（#4495）、
# 工程「設計」が「文脈のキー design_pr が無い」で失敗する（#4455）
DESIGN_PR = "design_pr"
ITEM_PR_OUTPUTS = (DESIGN_PR,)


def step_outputs(stage: StageDef, done: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """工程 `stage` の済んだステップ（`done`: ステップの id → 結果）の outputs を、定義の順に重ねる。

    工程が `ITEM_PR_OUTPUTS` のキーを出力に持ち、どのステップの outputs にも無ければ、種類が work（工程の成果物を作る作業。レビュー・記録・マージは除く）の
    ステップの items の PR（番号が 1 つに決まるときだけ）で補う。outputs に明示した値は、どのステップのものでも補った値より
    優先する。記録済みの結果（11.0.43 までに始めたステートマシン）にも効くよう、読む側で補う。"""
    explicit, filled = _split_outputs(stage, done)
    return {**filled, **explicit}


def item_only_outputs(stage: StageDef, done: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """`step_outputs` のうち、どのステップの outputs にも無く items の PR から補った値だけ。"""
    explicit, filled = _split_outputs(stage, done)
    return {k: v for k, v in filled.items() if k not in explicit}


def _split_outputs(stage: StageDef, done: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """済んだステップの outputs に明示した値と、items の PR から補える値の組。"""
    explicit: dict[str, Any] = {}
    filled: dict[str, Any] = {}
    keys = set(stage.outputs) & set(ITEM_PR_OUTPUTS)
    for st in stage.steps:
        if st.id not in done:
            continue
        report = done[st.id]
        if isinstance(report.get("outputs"), dict):
            explicit.update(report["outputs"])
        prs = set(result.pr_numbers(report)) if st.kind == "work" and keys else set()
        if len(prs) == 1:
            filled.update(dict.fromkeys(keys, next(iter(prs))))
    return explicit, filled


def outputs_draft(stage: StageDef, rev: int, outputs: Mapping[str, Any], actor: str) -> tuple[Optional[model.EventDraft], list[str]]:
    """工程の出力を文脈へ書く事象の下書き。キーは工程の定義の `outputs` に列挙したものだけ。

    `actor` は書き手。工程を流した作業（`workflow`）か、手で終えた conductor（`ndf progress finish`）。
    設定に無いキーは書かずに捨て、捨てたキーを警告で返す。書くキーが無ければ下書きは None。
    """
    kept, dropped = keep_declared(stage.outputs, outputs)
    warnings = [f"工程 {stage.name} の定義の outputs に無いキーを捨てた: {k}" for k in dropped]
    if not kept:
        return None, warnings
    return model.EventDraft(OUTPUTS_EVENT, actor, stage.name, payload={"outputs": kept}, rev=rev), warnings
