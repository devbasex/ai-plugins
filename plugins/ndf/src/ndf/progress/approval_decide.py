"""記録した回答が、承認ゲートを通す有効な承認かを決める（純粋な処理。ファイルもプロセスも時刻も読まない。#4520）。

有効な承認は、境界（直近の開き直し・手で終えた回答待ち・質問の出し直し）より後の、承認ゲートの最新の回答が承認で、
回答が見た対象が今の対象と一致するものである。対象を読むのは呼び手（`ndf.progress.gate`）で、ここは値を比べる。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, NamedTuple, Optional

from ndf.core import merge_approval
from ndf.progress import invoke, model, stages
from ndf.progress.folding import step_progress
from ndf.progress.run_view import ANSWERED, APPROVAL_POSTING, STEP_EVENT, View, closes_only, ends_approvals, event_payload

# 設計承認ゲートの区分（承認した PR のマージまで確かめる承認ゲート）
DESIGN_GATE = next(g for g in model.table()["gates"] if g == "design")


class Standing(NamedTuple):
    """承認の回答を今の対象に照らした結果。`differences` が空なら有効、`merged` は PR がマージ済みか、`head` は今の head。"""

    differences: list[str]
    merged: bool
    head: Optional[str] = None


def merged_otherwise(head: Optional[str]) -> str:
    """承認と違う対象になった PR がマージ済みのときに、質問し直す理由へ足す警告。"""
    return f"（承認と違う内容がマージされた。今の head {head}）"


# 照らす口（ステートマシン・承認ゲートの区分・対象の PR・承認の回答の事象）
StandingOf = Callable[[View, Optional[str], Optional[int], model.Event], Standing]


def unreadable(v: View, gate: Optional[str], pr: Optional[int], answered: model.Event) -> Standing:
    """対象を読む口を持たない呼び手の既定。承認を数えずに止める。"""
    raise model.Refused(f"承認の対象を読めないので、ステートマシン {v.machine_id} の承認を照らせない")


def gate_pr(
    context: Mapping[str, Any], stage: stages.StageDef, done: Mapping[str, Any], gate: Optional[str], *, by_hand: bool
) -> Optional[int]:
    """承認ゲート `gate` の対象の PR。設計承認ゲートは `design_pr`、ほかは `pr`。

    工程の出力は工程が終わるまで文脈に無いので、済んだステップの出力を重ねる。手で終えた工程（`by_hand`）は、
    手で書いた出力を持つ文脈を、前に流したステップの出力より先に使う。"""
    steps = stages.step_outputs(stage, done)
    merged = {**steps, **context} if by_hand else {**context, **steps}
    key = stages.DESIGN_PR if gate == DESIGN_GATE else "pr"
    number = merged.get(key)
    return number if isinstance(number, int) else None


def approval_boundary(rows: Sequence[model.Event], gate_step: str) -> int:
    """工程の事象の列で、承認を数え始める事象の番号（直近の開き直し・手で終えた回答待ち・質問の出し直し。無ければ 0）。"""
    cut = 0
    for ev in rows:
        if ends_approvals(ev) and (ev.draft.kind != STEP_EVENT or event_payload(ev).get("step") == gate_step):
            cut = ev.seq
    return cut


def latest_answer(rows: Sequence[model.Event], gate: Optional[str], after: int) -> Optional[model.Event]:
    """境界より後の、承認ゲート `gate` の最新の回答。承認でなくても、前の回答へはさかのぼらない。"""
    found = None
    for ev in rows:
        p = event_payload(ev)
        if ev.seq > after and ev.draft.kind == ANSWERED and p.get("kind") == invoke.LISTEN and p.get("gate") == gate:
            found = ev
    return found


def rejection_posted_after(rows: Sequence[model.Event], gate: Optional[str], seq: int) -> bool:
    """事象 `seq` の後に、承認ゲート `gate` への却下か変更要求の投稿を試みた記録があるか。"""
    posting = (event_payload(ev) for ev in rows if ev.seq > seq and ev.draft.kind == APPROVAL_POSTING)
    return any("rejecting" in held and held["rejecting"] == gate for held in posting)


def counted_approval(rows: Sequence[model.Event], gate: Optional[str], gate_step: str) -> Optional[model.Event]:
    """対象を読まずに決まる承認の候補。境界より後の最新の回答が、数える承認でなければ None。

    その回答の後に却下か変更要求の投稿を試みた記録があれば None。PR に却下が載っていて回答の確定だけが失敗した可能性がある。"""
    found = latest_answer(rows, gate, approval_boundary(rows, gate_step))
    if found is None or rejection_posted_after(rows, gate, found.seq):
        return None
    p = event_payload(found)
    return None if p.get("answer") != invoke.APPROVED or closes_only(p) or model.mvv_answer_void(p, gate) else found


def target_pr(v: View, stage: stages.StageDef, gate: Optional[str]) -> Optional[int]:
    """ステートマシンの今の記録で、工程 `stage` の承認ゲート `gate` の対象の PR。"""
    prog = step_progress(v, stage.name)
    return gate_pr(v.context(), stage, prog.done, gate, by_hand=prog.held_finish is not None)


def saw_another_pr(v: View, stage: str, answer: Mapping[str, Any]) -> bool:
    """回答の payload が見た PR が、工程の今の対象の PR と違うか（どちらかが分からなければ偽）。"""
    seen, gate = answer.get("seen"), answer.get("gate")
    held = seen.get("pr") if isinstance(seen, Mapping) else None
    if held is None or not isinstance(gate, str):
        return False
    number = target_pr(v, v.snap.stage(stage), gate)
    return number is not None and number != held


def _target_of(seen: Any, head: Any = None) -> Optional[merge_approval.MergeTarget]:
    """本番へ反映される承認の対象（事象の `seen` か判定の `target`）。そろっていなければ None（証跡を書かない承認）。"""
    if not isinstance(seen, Mapping) or seen.get("production") is not True:
        return None
    repo, number, base, sha = seen.get("repo"), seen.get("pr"), seen.get("base"), head or seen.get("head")
    if not (isinstance(repo, str) and type(number) is int and isinstance(base, str) and isinstance(sha, str)):
        return None
    return merge_approval.MergeTarget(repo, number, base, sha)


def _record_of(answer: Mapping[str, Any]) -> str:
    """回答の payload の、証跡の記録の識別子。"""
    seen = answer.get("seen")
    shown = seen.get("presentation") if isinstance(seen, Mapping) else None
    return merge_approval.presented_record_id(str(answer.get("gate_id")), shown)


def _trail(ev: model.Event) -> Optional[tuple[merge_approval.MergeTarget, str]]:
    """事象が証跡を書いた承認の対象と記録の識別子。承認の回答と、省くと決めた MVV 判定の記録（判定のコマンドが証跡を書く）を読む。"""
    p = event_payload(ev)
    if ev.draft.kind == ANSWERED:
        seen = p.get("seen")
        target = _target_of(seen) if p.get("answer") == invoke.APPROVED and not closes_only(p) else None
        if target is None:
            return None
        return target, _record_of(p)
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


def revocations(since: Mapping[str, Sequence[model.Event]], machine_id: str, names: Sequence[str]) -> list[merge_approval.Revocation]:
    found: dict[merge_approval.MergeTarget, merge_approval.Revocation] = {}
    for name in names:
        for ev in since[name]:
            trail = _trail(ev)
            if trail is None:
                continue
            target, approval = trail
            approvals = found[target].approvals if target in found else frozenset()
            found[target] = merge_approval.Revocation(target, approvals | {approval}, f"reopen:{machine_id}:{name}:{ev.seq}")
    held = list(found.values())
    for name in names:
        for target, record in _unrecorded(since[name]):
            at = next((i for i, r in enumerate(held) if r.target.same(target)), None)
            if at is None:
                reopen_id = f"reopen:{machine_id}:{name}:{record}"
                held.append(merge_approval.Revocation(target, frozenset({record}), reopen_id, unconfirmed=True))
            else:
                held[at] = held[at]._replace(approvals=held[at].approvals | {record})
    return held


def _unrecorded(rows: Sequence[model.Event]) -> list[tuple[merge_approval.MergeTarget, str]]:
    """工程の事象の列で、証跡の投稿を試みて承認の回答が記録されていない承認の対象と記録の識別子（古い順）。

    同じ提示への却下や変更要求が記録されても、投稿した承認は確定していないので数え続ける。

    回答は証跡を投稿した後の DB の確定に失敗しうるので、投稿の前の記録（`APPROVAL_POSTING`）から指す。直近の承認の境界より
    前の記録は数えない（境界へ移るときに取り消してある）。"""
    cut = max((ev.seq for ev in rows if ends_approvals(ev)), default=0)
    answered = (event_payload(ev) for ev in rows if ev.draft.kind == ANSWERED)
    recorded = {_record_of(p) for p in answered if p.get("answer") == invoke.APPROVED}
    posted = (event_payload(ev) for ev in rows if ev.seq > cut and ev.draft.kind == APPROVAL_POSTING)
    # 却下と変更要求の投稿前の記録（`rejecting`）は、取り消す承認ではない
    approvals = (p for p in posted if "rejecting" not in p)
    return [(merge_approval.MergeTarget(*p["target"]), p["record"]) for p in approvals if p["record"] not in recorded]


def stage_revocations(v: View, stage: str) -> list[merge_approval.Revocation]:
    """工程の直近の承認の境界（`ends_approvals`）より後の承認（回答が記録されていない投稿を含む）の取り消し。手で終えた回答待ちか質問の出し直しで境界が移るときに、
    移る前に渡す。境界より前の承認は、その境界へ移るときに取り消してある。"""
    rows = [ev for ev in v.events if ev.draft.row_key == stage]
    cut = max((ev.seq for ev in rows if ends_approvals(ev)), default=0)
    return revocations({stage: [ev for ev in rows if ev.seq > cut]}, v.machine_id, [stage])


def content_differences(now: Mapping[str, Any], seen: Mapping[str, Any]) -> list[str]:
    """見た対象 `seen` と今の対象 `now`（事象の `seen` の形）の中身の違い（今の側が分かる項目だけを比べる）。

    本文と題は、見た側も値を持つときだけ比べる。MVV 判定で省いた回答は head だけを見て書くためである。"""
    out = []
    for name, key, needs_seen in (
        ("head の SHA", "head", False),
        ("承認資料の sha256", model.MATERIAL_KEY, False),
        ("PR の本文の sha256", "body_sha256", True),
        ("PR の題の sha256", "title_sha256", True),
    ):
        current, then = now.get(key), seen.get(key)
        if current is not None and then != current and (then is not None or not needs_seen):
            out.append(f"{name}（見た値 {then or '無し'}・今の値 {current}）")
    return out


def _binding_names() -> dict[str, str]:
    """承認の対象の項目（事象の `seen` のキー）→ 違いの文に書く名前。"""
    return dict(repo="リポジトリ", pr="PR の番号", base="マージ先", production="本番へ反映されるか")


def binding_differences(now: Mapping[str, Any], seen: Mapping[str, Any]) -> list[str]:
    """見た対象が持つ承認の対象（リポジトリ・PR・マージ先・本番へ反映されるか）と、今の値の違い。今読めなければ違いにする。"""
    return [
        f"{name}（見た値 {seen[key]}・今の値 {now[key] if key in now else '無し'}）"
        for key, name in _binding_names().items()
        if seen.get(key) is not None and seen[key] != now.get(key)
    ]


def differences(answer: Mapping[str, Any], now: Mapping[str, Any], *, gate: Optional[str], carried_head: Optional[str]) -> list[str]:
    """回答の payload が承認として数えられない理由と、見た対象と今の対象の違い。空なら有効。

    `carried_head` は、承認の後に文書だけが加わって出し直さなかった head。今の側だけが承認の対象を持つ回答
    （対象に結び付かずに記録した承認）も違いにする。承認資料の比較は呼び手が足す（保存したファイルを読むため）。"""
    if answer.get("answer") != invoke.APPROVED:
        return ["承認の回答でない"]
    if model.mvv_answer_void(answer, gate):
        return ["数えない MVV 判定の回答"]
    held = answer.get("seen")
    seen: Mapping[str, Any] = held if isinstance(held, Mapping) else {}
    if carried_head is not None and now.get("head") == carried_head:
        seen = {**seen, "head": carried_head}
    unbound = [
        f"{name}（見た値 無し・今の値 {now[key]}）" for key, name in _binding_names().items() if key in now and seen.get(key) is None
    ]
    contents = content_differences({**now, model.MATERIAL_KEY: None}, seen)
    return [*unbound, *binding_differences(now, seen), *contents]
