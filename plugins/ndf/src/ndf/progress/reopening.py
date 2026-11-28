"""工程の開き直し（#4520）: 開き直す工程と事象を組み、作業が止まっていることを確かめ、承認の証跡を取り消す。

開き直しは事象 `reopened` 1 つで表し、過去の事象は消さない。畳み込み（`folding`）と文脈（`stages.recorded_outputs`）と
承認を読む側（`run_view.StageAnswers`・`gate.records.approved_head`）が、直近の開き直しより前を今の判断に使わない。
PR のコメントの証跡だけを読む `ndf ci merge` のために、開き直す工程の承認は PR の側でも取り消し、マージキューに
入った PR はキューから外す（キューは証跡を読まずにマージする）。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any, Optional

from ndf.core import merge_approval
from ndf.progress import model, run, stages, store
from ndf.progress.run_view import ANSWERED, CONDUCTOR, FINISHED, REOPENED, STARTED, STEP_EVENT, View, event_payload

# 後ろの工程の扱い（開き直す・残す）
REOPEN, KEEP = "reopen", "keep"

# 承認の取り消しを書く口（取り消す承認をまとめて渡す）
Revoke = Callable[[list[merge_approval.Revocation]], None]


@dataclass(frozen=True)
class Reopen:
    """開き直し 1 回の中身。`stages` は開き直す工程の名前、`revoke` は取り消す承認。"""

    drafts: list[model.EventDraft]
    stages: list[str]
    warnings: list[str]
    revoke: list[merge_approval.Revocation]


# 工程 → 直近の開き直しより後の事象（改訂を問わない。証跡は改訂が変わっても PR に残る）
Since = Mapping[str, list[model.Event]]


def _since_reopened(v: View) -> Since:
    """履歴を 1 回だけたどって、工程ごとの直近の開き直しより後の事象を集める。事象の無い工程は空。"""
    cut = stages.reopened_at(v.events)
    out: defaultdict[str, list[model.Event]] = defaultdict(list)
    for ev in v.events:
        stage = ev.draft.row_key
        if stage is not None and ev.seq > (cut[stage] if stage in cut else 0):
            out[stage].append(ev)
    return out


def _state(rows: Mapping[str, model.RowState], stage: str) -> Optional[str]:
    row = rows.get(stage)
    return row.state if row is not None else None


def _progressed(v: View, rows: Mapping[str, model.RowState], since: Since, stage: str) -> bool:
    """工程に開き直すものがあるか（無効と中止は開き直さない。未着手は、前に流した記録があるときだけ）。"""
    state = _state(rows, stage)
    # 終わりの状態のうち開き直すのは終了だけ
    if state is None or (model.is_terminal(state) and state != FINISHED):
        return False
    # 流した・回答した・出力を書いた記録（作成と、無効と有効の切り替えは進みに数えない）
    ran = (STARTED, ANSWERED, stages.OUTPUTS_EVENT)
    return state != "pending" or any(ev.draft.rev == v.rev and ev.draft.kind in ran for ev in since[stage])


def _target_of(seen: Any, head: Any = None) -> Optional[merge_approval.MergeTarget]:
    """本番へ反映される承認の対象（事象の `seen` か判定の `target`）。そろっていなければ None（証跡を書かない承認）。"""
    if not isinstance(seen, Mapping) or seen.get("production") is not True:
        return None
    repo, number, base, sha = seen.get("repo"), seen.get("pr"), seen.get("base"), head or seen.get("head")
    if not (isinstance(repo, str) and type(number) is int and isinstance(base, str) and isinstance(sha, str)):
        return None
    return merge_approval.MergeTarget(repo, number, base, sha)


def _trail(ev: model.Event) -> Optional[tuple[merge_approval.MergeTarget, str]]:
    """事象が証跡を書いた承認の対象と記録の識別子。承認の回答と、省くと決めた MVV 判定の記録（判定のコマンドが証跡を書く）を読む。"""
    p = event_payload(ev)
    if ev.draft.kind == ANSWERED:
        seen = p.get("seen")
        target = _target_of(seen) if p.get("answer") == run.APPROVED else None
        if target is None:
            return None
        return target, merge_approval.presented_record_id(str(p.get("gate_id")), seen.get("presentation"))
    if ev.draft.kind != STEP_EVENT:
        return None
    report = p.get("report")
    outputs = report.get("outputs") if isinstance(report, Mapping) else None
    held = outputs.get(model.MVV_KEY) if isinstance(outputs, Mapping) else None
    if not isinstance(held, Mapping) or held.get("skip") is not True:
        return None
    target = _target_of(held.get("target"), held.get("head"))
    # 記録の識別子は別のステートマシンの判定と同じになるので、証跡を書いたコメントの URL で指す
    url = held.get("note_url")
    return (target, url) if target is not None and isinstance(url, str) and url else None


def _revocations(since: Since, machine_id: str, names: list[str]) -> list[merge_approval.Revocation]:
    found: dict[merge_approval.MergeTarget, merge_approval.Revocation] = {}
    for name in names:
        for ev in since[name]:
            trail = _trail(ev)
            if trail is None:
                continue
            target, approval = trail
            approvals = found[target].approvals if target in found else frozenset()
            found[target] = merge_approval.Revocation(target, approvals | {approval}, f"reopen:{machine_id}:{name}:{ev.seq}")
    return list(found.values())


def _outward(step: stages.StepDef) -> bool:
    """外への操作（マージ・リリース）をするステップか。開き直しても、済んだ操作は取り消されない。"""
    return step.kind == "merge" or step.kind == "release"


def _acted(rows: Mapping[str, model.RowState], since: Since, s: stages.StageDef) -> bool:
    """工程の外への操作が済んでいるか。工程が終了したか、直近の開き直しより後に外への操作のステップの成功を記録した
    （その後のステップや出力の検証で失敗した工程も含む）。"""
    outward = {step.id for step in s.steps if _outward(step)}
    if not outward:
        return False
    if _state(rows, s.name) == FINISHED:
        return True
    recorded = (event_payload(ev) for ev in since[s.name] if ev.draft.kind == STEP_EVENT)
    return any(p.get("step") in outward and p.get("status") == FINISHED for p in recorded)


def plan_reopen(v: View, stage: str, *, later: str, reason: str, trigger: Mapping[str, Any], notes: Optional[str] = None) -> Reopen:
    """工程 `stage`（`later` が `reopen` なら進みのある後ろの工程も）を開き直す事象と、取り消すものを組む（書かない）。

    完了・中止したステートマシンは、PR へ書く前に断る（`model.Refused`）。"""
    if model.is_terminal(v.whole):
        raise model.Refused(f"ステートマシン {v.machine_id} は{model.label(v.whole)}で、工程を開き直せない")
    order: list[stages.StageDef] = list(v.snap.stages)
    at = order.index(v.snap.stage(stage))
    rows, since = v.rows, _since_reopened(v)
    behind = [s.name for s in order[at + 1 :] if _progressed(v, rows, since, s.name)] if later == REOPEN else []
    names = [stage, *behind]
    payload = {"trigger": dict(trigger), "notes": notes, "later": later}
    drafts = [model.EventDraft(REOPENED, CONDUCTOR, name, reason=reason, payload=payload, rev=v.rev) for name in names]
    warnings = [f"工程 {s.name} のマージ・リリースは取り消されない" for s in order if s.name in names and _acted(rows, since, s)]
    return Reopen(drafts, names, warnings, _revocations(since, v.machine_id, names))


def revoke_approvals(r: Reopen, post: Revoke) -> None:
    """承認を取り消し、PR をマージキューから外す。DB の追記より前に呼ぶ（できなければ `post` が `model.Refused`）。"""
    if r.revoke:
        post(r.revoke)


@contextmanager
def work_stopped(st: store.Store, machine_id: str) -> Iterator[None]:
    """作業の排他を取り、起動した作業がまだ排他を取っていない場合も確かめる。作業が流れていれば断る。"""
    running = f"ステートマシン {machine_id} を流す作業が動いている。ndf progress wait {machine_id} で終わりを待って打ち直す"
    with ExitStack() as stack:
        try:
            stack.enter_context(store.run_lock(st, machine_id))
        except model.Refused as exc:
            raise model.Refused(f"{exc.reason}: {running}") from None
        if run.launching(st, machine_id):
            raise model.Refused(running)
        yield
