"""工程の開き直し（#4520）: 開き直す工程と事象を組み、作業が止まっていることを確かめ、承認の証跡を取り消す。

開き直しは事象 `reopened` 1 つで表し、過去の事象は消さない。畳み込み（`folding`）と文脈（`stages.recorded_outputs`）と
承認を読む側（`run_view.StageAnswers`・`gate.records.approved_head`）が、直近の開き直しより前を今の判断に使わない。
PR のコメントの証跡だけを読む `ndf ci merge` のために、開き直す工程の承認は PR の側でも取り消す。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import Any, Optional

from ndf.core import merge_approval
from ndf.progress import model, run, stages, store
from ndf.progress.run_view import ANSWERED, CONDUCTOR, FINISHED, REOPENED, STARTED, STEP_EVENT, View, event_payload

# 後ろの工程の扱い（開き直す・残す）
REOPEN, KEEP = "reopen", "keep"

# 承認の取り消しを書く口（対象と記録の識別子を渡し、書いたら真）
Revoke = Callable[[merge_approval.MergeTarget, str], bool]


@dataclass(frozen=True)
class Reopen:
    """開き直し 1 回の中身。`stages` は開き直す工程の名前、`revoke` は取り消す承認の対象と取り消しの行の記録の識別子。"""

    drafts: list[model.EventDraft]
    stages: list[str]
    warnings: list[str]
    revoke: list[tuple[merge_approval.MergeTarget, str]]


def _state(v: View, stage: str) -> Optional[str]:
    row = v.rows.get(stage)
    return row.state if row is not None else None


def _since_reopened(v: View, stage: str) -> list[model.Event]:
    """工程の直近の開き直しより後の事象（改訂を問わない。証跡は改訂が変わっても PR に残る）。"""
    after = v.last_reopened(stage)
    return [ev for ev in v.events if ev.draft.row_key == stage and ev.seq > after]


def _progressed(v: View, stage: str) -> bool:
    """工程に開き直すものがあるか（無効と中止は開き直さない。未着手は、前に流した記録があるときだけ）。"""
    state = _state(v, stage)
    # 終わりの状態のうち開き直すのは終了だけ
    if state is None or (model.is_terminal(state) and state != FINISHED):
        return False
    after = v.last_reopened(stage)
    # 流した・回答した・出力を書いた記録（作成と、無効と有効の切り替えは進みに数えない）
    ran = (STARTED, ANSWERED, stages.OUTPUTS_EVENT)
    return state != "pending" or any(ev.seq > after and ev.draft.kind in ran for ev in v.row_events(stage))


def _target_of(seen: Any, head: Any = None) -> Optional[merge_approval.MergeTarget]:
    """本番へ反映される承認の対象（事象の `seen` か判定の `target`）。そろっていなければ None（証跡を書かない承認）。"""
    if not isinstance(seen, Mapping) or seen.get("production") is not True:
        return None
    repo, number, base, sha = seen.get("repo"), seen.get("pr"), seen.get("base"), head or seen.get("head")
    if not (isinstance(repo, str) and type(number) is int and isinstance(base, str) and isinstance(sha, str)):
        return None
    return merge_approval.MergeTarget(repo, number, base, sha)


def _trail_target(ev: model.Event) -> Optional[merge_approval.MergeTarget]:
    """事象が証跡を書いた承認の対象。承認の回答と、省くと決めた MVV 判定の記録（判定のコマンドが証跡を書く）を読む。"""
    p = event_payload(ev)
    if ev.draft.kind == ANSWERED:
        return _target_of(p.get("seen")) if p.get("answer") == run.APPROVED else None
    if ev.draft.kind != STEP_EVENT:
        return None
    report = p.get("report")
    outputs = report.get("outputs") if isinstance(report, Mapping) else None
    held = outputs.get(model.MVV_KEY) if isinstance(outputs, Mapping) else None
    if not isinstance(held, Mapping) or held.get("skip") is not True:
        return None
    return _target_of(held.get("target"), held.get("head"))


def _revocations(v: View, names: list[str]) -> list[tuple[merge_approval.MergeTarget, str]]:
    found: dict[merge_approval.MergeTarget, str] = {}
    for name in names:
        for ev in _since_reopened(v, name):
            target = _trail_target(ev)
            if target is not None:
                found[target] = f"reopen:{v.machine_id}:{name}:{ev.seq}"
    return list(found.items())


def _outward(step: stages.StepDef) -> bool:
    """外への操作（マージ・リリース）をするステップか。開き直しても、済んだ操作は取り消されない。"""
    return step.kind == "merge" or step.kind == "release"


def _irreversible(v: View, names: list[str]) -> list[str]:
    done = [s for s in v.snap.stages if s.name in names and _state(v, s.name) == FINISHED]
    return [f"工程 {s.name} のマージ・リリースは取り消されない" for s in done if any(map(_outward, s.steps))]


def plan_reopen(v: View, stage: str, *, later: str, reason: str, trigger: Mapping[str, Any], notes: Optional[str] = None) -> Reopen:
    """工程 `stage`（`later` が `reopen` なら進みのある後ろの工程も）を開き直す事象と、取り消すものを組む（書かない）。

    完了・中止したステートマシンは、PR へ書く前に断る（`model.Refused`）。"""
    if model.is_terminal(v.whole):
        raise model.Refused(f"ステートマシン {v.machine_id} は{model.label(v.whole)}で、工程を開き直せない")
    order: list[stages.StageDef] = list(v.snap.stages)
    at = order.index(v.snap.stage(stage))
    behind = [s.name for s in order[at + 1 :] if _progressed(v, s.name)] if later == REOPEN else []
    names = [stage, *behind]
    payload = {"trigger": dict(trigger), "notes": notes, "later": later}
    drafts = [model.EventDraft(REOPENED, CONDUCTOR, name, reason=reason, payload=payload, rev=v.rev) for name in names]
    return Reopen(drafts, names, _irreversible(v, names), _revocations(v, names))


def revoke_approvals(r: Reopen, post: Revoke) -> None:
    """取り消す対象ごとに取り消しを書く。DB の追記より前に呼ぶ（書けなければ `post` が `model.Refused`）。"""
    for target, record_id in r.revoke:
        post(target, record_id)


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
