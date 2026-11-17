"""承認ゲートへの回答（`ndf answer`）と、回答を読む前の今の対象との検証（`ndf.progress.gate` の部品）。

待っていない行（実行の前）・違う質問の id・今の対象と違う見た対象は断り、行は待ちのまま残る（3364-2）。同じ冪等のキーの
回答し直しは書かずに同じ結果を返す。設計承認ゲートの承認で外した課題は、同じトランザクションの中でレーンの順番表の次の行へ
入れる（#3097 の決定 99）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from ndf.adapters.git import Git, GitError
from ndf.adapters.github import GitHub, GitHubError, client
from ndf.contracts import result
from ndf.progress import command, invoke, lane, model, plan, run, stages, store
from ndf.progress.gate.asking import (
    DESIGN_GATE,
    REJECTING,
    RELEASE_GATE,
    TOOL,
    Answer,
    Question,
    Target,
    accepted_path,
    material_path,
    material_sha256,
    question,
    release_request_of,
    waiting,
)
from ndf.progress.gate.records import record_answer, rejections_path

# 回答した者の形（人・conductor・サービス。サービスは `service:<主体>`）
ACTORS = re.compile(r"^(human|conductor|organizer|service):[A-Za-z0-9._@/-]+$|^(human|conductor|organizer)$")

# --- 回答 ---------------------------------------------------------------------------------------


# 今の対象を読む口（質問を渡し、PR の head と承認資料の sha256 を返す）
Observe = Callable[[Question], Target]


def no_observe(q: Question) -> Target:
    """今の対象を読まない（比べない）。"""
    return Target()


def github_observe(cwd: Path, root: Path, hub: Optional[Callable[[], GitHub]] = None) -> Observe:
    """今の対象を読む: PR の head の SHA（GitHub）と承認資料の sha256。読めなければ回答を受けない（`model.Refused`）。"""
    made: list[GitHub] = []

    def _hub() -> GitHub:
        if not made:
            made.append(hub() if hub is not None else GitHub(Git(cwd).remote_repo(), client()))
        return made[0]

    def observe(q: Question) -> Target:
        head = None
        if q.pr is not None:
            try:
                head = _hub().pull(q.pr).head_sha
            except (GitHubError, GitError) as exc:
                raise model.Refused(f"PR #{q.pr} の head を読めないので、回答と対象を照らせない: {exc}") from None
        path = material_path(root, q)
        return Target(head, material_sha256(path) if path.exists() else None)

    return observe


def _answered_with_key(v: run.View, key: str) -> Optional[model.Event]:
    for ev in v.events:
        p = run.payload_of(ev)
        if ev.draft.kind == run.ANSWERED and p.get("key") == key and ev.draft.rev == v.rev:
            return ev
    return None


def _refusal(v: run.View, a: Answer, observe: Observe) -> Question:
    """回答を受けられるかを確かめ、待っている質問を返す。断るなら `model.Refused`（行は待ちのまま残る）。"""
    q = waiting(v)
    if q is None:
        raise model.Refused(f"ステートマシン {v.machine_id} は回答を待っていない（実行の前か、済んだ回答）")
    if q.id != a.gate_id:
        raise model.Refused(f"質問 {a.gate_id} は今の質問 {q.id}（工程 {q.stage}）と違う")
    if a.value not in q.choices:
        raise model.Refused(f"工程 {q.stage} の回答は {', '.join(q.choices)} のどれか（渡された値 {a.value}）")
    if a.drop and not (a.value == run.APPROVED and q.gate == DESIGN_GATE):
        raise model.Refused("課題を外せるのは設計承認ゲートの承認だけ")
    if a.drop:
        _kept(v, a.drop)
    changed = observe(q).differs(a.seen)
    if changed:
        raise model.Refused(f"回答した後に対象が変わった。待ちへ戻すので、今の対象を見て回答し直す: {' / '.join(changed)}")
    return q


def _kept(v: run.View, drop: Sequence[int]) -> list[int]:
    """外した後に今のスプリントに残る課題（文脈の `issues` の順）。スプリントに無い課題と、すべてを外す回答は断る。"""
    current = [int(n) for n in v.context()["issues"]]
    unknown = sorted(set(drop) - set(current))
    if unknown:
        raise model.Refused(f"外す課題 {', '.join(map(str, unknown))} はこのスプリントの課題（{', '.join(map(str, current))}）に無い")
    kept = [n for n in current if n not in set(drop)]
    if not kept:
        raise model.Refused("課題をすべて外す回答は受けない（スプリントを止めるなら却下 rejected で回答する）")
    return kept


def _dropped(v: run.View, q: Question, drop: Sequence[int]) -> list[model.EventDraft]:
    """外した課題を今のスプリントの文脈の `issues` から除く事象。後の工程は残った課題だけを読む（`View.context`）。

    回答した工程の設定の outputs に `issues` は無いが、課題を外すのは設計承認ゲートの回答だけが持つ決まり（決定 99）で、
    回答と同じトランザクションで conductor が書く。"""
    if not drop:
        return []
    payload = {"outputs": {"issues": _kept(v, drop)}}
    return [model.EventDraft(stages.OUTPUTS_EVENT, run.CONDUCTOR, q.stage, payload=payload, rev=v.rev)]


def _carry(conn: Any, machine_id: str, issues: Sequence[int], reason: str) -> Optional[str]:
    """外した課題を、スプリントを持つレーンの順番表の次の行へ入れる。レーンの外のスプリントなら断る。"""
    if not issues:
        return None
    found = lane.sprint_row(conn, machine_id)
    if found is None:
        raise model.Refused(f"ステートマシン {machine_id} を持つレーンの行が無く、外した課題を入れる先が無い")
    plan_id, name, row = found
    return lane.carry_issues(conn, plan_id, name, row.key, list(issues), reason)


def answer(st: store.Store, machine_id: str, a: Answer, observe: Observe = no_observe) -> dict[str, Any]:
    """回答を事象 `answered` に残す（外した課題の行と、今のスプリントの課題から外す事象も同じトランザクション）。
    同じ冪等のキーは書かずに同じ結果を返す。"""
    if not ACTORS.match(a.by):
        raise model.Refused(f"回答した者 {a.by} の形が違う（human・conductor・organizer か service:<主体>）")
    text = run.notes_text(a.value, a.notes)
    with store.transaction(st) as conn:
        v = run.view(conn, machine_id)
        held = _answered_with_key(v, a.key)
        if held is not None:
            return _same(held, a, machine_id)
        q = _refusal(v, a, observe)
        carried = _carry(conn, machine_id, a.drop, a.reason)
        rejected = _reject_release(conn, q, a)
        extra = {
            "gate_id": q.id,
            "gate": q.gate,
            "by": a.by,
            "key": a.key,
            "seen": a.seen.as_dict(),
            "dropped": list(a.drop),
            "carried_row": carried,
            "release_request": rejected,
        }
        draft = run.answer_draft(v, q.stage, a.value, a.reason, text, extra)
        drafts = [draft, *_outputs(v, q, a), *_dropped(v, q, a.drop)]
        seqs = store.append_events(conn, machine_id, drafts, v.events[-1].seq if v.events else 0)
    summary = f"工程 {q.stage} へ回答 {a.value} を書いた（{a.by}）。続きは ndf progress next で流す"
    metrics = {"machine_id": machine_id, "seqs": seqs, "gate_id": q.id, "carried_row": carried}
    out = result.result(TOOL, result.OK, summary, metrics=metrics, next=result.command("progress", "next", machine_id))
    return command.with_warnings(out, after=record_answer(v, q, a, rejections_path))


# 承認ゲートの回答だけが文脈へ書くキー（工程の定義の outputs には置かない）。まとめたリリースのリリース記録の PR は
# オーガナイザーの回答だけが運び、デプロイの作業は返さない。工程の outputs に置くと、工程の終わりの検証が
# 作業の出力にこのキーを求め、人が回答したデプロイが必ず止まる（#2783）
ANSWER_OUTPUTS = {RELEASE_GATE: ("release_pr",)}


def _outputs(v: run.View, q: Question, a: Answer) -> list[model.EventDraft]:
    """回答が運ぶ値（`Answer.outputs`）を、承認ゲートの工程の出力として文脈へ書く事象。工程の定義の outputs と
    `ANSWER_OUTPUTS` に無いキーと、文脈のスキーマに合わない値は断る。"""
    if not a.outputs:
        return []
    extra = ANSWER_OUTPUTS[q.gate] if q.gate in ANSWER_OUTPUTS else ()
    allowed = (*v.snap.stage(q.stage).outputs, *extra)
    kept, dropped = stages.keep_declared(allowed, a.outputs)
    if dropped:
        raise model.Refused(f"工程 {q.stage} の定義の outputs に無いキーは回答から書けない: {'; '.join(dropped)}")
    errs = invoke.value_errors(kept)
    if errs:
        raise model.Refused(f"工程 {q.stage} への回答が運ぶ値が文脈の形に合わない: {'; '.join(errs)}")
    return [model.EventDraft(stages.OUTPUTS_EVENT, run.ACTOR, q.stage, payload={"outputs": kept}, rev=v.rev)]


def _same(held: model.Event, a: Answer, machine_id: str) -> dict[str, Any]:
    p = run.payload_of(held)
    if p.get("answer") != a.value or p.get("gate_id") != a.gate_id:
        raise model.Refused(f"冪等のキー {a.key} は別の回答（{p.get('answer')}・質問 {p.get('gate_id')}）に使った")
    summary = f"冪等のキー {a.key} の回答は書いてある（事象 {held.seq}）"
    metrics = {"machine_id": machine_id, "seqs": [held.seq], "gate_id": a.gate_id, "replayed": True}
    return result.result(TOOL, result.OK, summary, metrics=metrics, next=result.command("progress", "next", machine_id))


def lane_env(env: run.Env) -> run.Env:
    """スプリントの完了と同じトランザクションで、スプリントを持つレーンの行を終える `Env`（決定 17。レーンの外なら何もしない）。"""
    return replace(env, ended=lambda conn, machine_id: lane.end_row(conn, machine_id, lane.COMPLETED, run.ACTOR))


# 承認の後に加わった範囲（見た head → 今の head）が文書だけか。分からなければ False（出し直す側に倒す）
DocsOnly = Callable[[str, str], bool]


def never_docs_only(seen: str, now: str) -> bool:
    return False


def git_docs_only(cwd: Path) -> DocsOnly:
    """承認の後に加わった範囲が文書だけか（差分の分け方は `command.range_diff_kinds`。#3020）。

    見た head が今の head の祖先でない（履歴の書き換え）か、git が読めなければ文書だけと見なさない。振る舞いを決める
    文書（`.ndf/project.json` の `docs.behavior`）が加わったときも文書だけではない。`docs.behavior` を定義していない
    リポジトリでは、どの文書が振る舞いを決めるか分からないため、文書だけと見なさない（承認を出し直す側に倒す）。
    """

    def docs_only(seen: str, now: str) -> bool:
        if not command.behavior_declared(cwd):
            return False
        git = Git(cwd)
        try:
            return git.is_ancestor(seen, now) and command.range_diff_kinds(git, f"{seen}..{now}", cwd)["docs_only_diff"]
        except GitError:  # noqa: NDF401 外部コマンド: 範囲を読めなければ出し直す側に倒す
            return False

    return docs_only


def _docs_only_after(q: Question, seen: Target, now: Target, docs_only: DocsOnly) -> bool:
    """リリース承認ゲートで、違いが PR の head だけで、加わった範囲が文書だけか（出し直さない。#3020）。"""
    if q.gate != RELEASE_GATE or (now.material is not None and now.material != seen.material):
        return False
    return seen.head is not None and now.head is not None and docs_only(seen.head, now.head)


def _accept(root: Path, q: Question, now: Target) -> None:
    """出し直さずに進めた今の head を残す（マージの側が照らす head。`approved_head`）。"""
    path = accepted_path(root, q.machine_id, q.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"head": now.head}) + "\n", encoding="utf-8")


def recheck(st: store.Store, machine_id: str, observe: Observe, docs_only: DocsOnly = never_docs_only) -> Optional[str]:
    """回答を読む前に、回答した者が見た対象と今の対象を比べる。違えば待ちへ戻し（質問は新しい id）、理由を返す（3364-2）。

    `ndf answer` が書いた回答（質問の id と見た対象を持つ）だけを比べる。どのサブコマンドでも、回答が読まれる前に打つ。待ちへ戻すのは承認ゲートのステップの
    待ちの記録で、回答はその後の回答だけが読まれる（`run.StepProgress`）。回答が課題を外していれば、同じトランザクションで
    文脈の `issues` を回答の前へ戻し、外した課題を入れたレーンの行を取り消す（回答し直しで同じ課題を外せる）。
    """
    v = run.read(st, machine_id)
    m = run.decide(v)
    if m.action != run.Action.CONSUME or m.event is None or m.stage is None or m.step is None or m.progress is None:
        return None
    p = run.payload_of(m.event)
    if "gate_id" not in p:
        return None
    q = question(v, replace(m, action=run.Action.GATE))
    seen, now = Target.from_dict(p.get("seen")), observe(q)
    changed = now.differs(seen)
    if not changed:
        return None
    if _docs_only_after(q, seen, now, docs_only):
        _accept(st.root, q, now)
        return f"承認の後に加わった範囲（{seen.head} → {now.head}）は文書だけなので、リリース承認ゲートを出し直さずに新しい範囲で進める"
    why = "回答した後に対象が変わった: " + " / ".join(changed)
    payload = {"step": m.step.id, "status": "waiting", "report": None, "generation": m.progress.generation, "reason": why}
    draft = model.EventDraft(run.STEP_EVENT, run.ACTOR, m.stage.name, payload=payload, rev=v.rev)
    # 比べた後に回答が読まれていれば（同じ回答の事象が今も読む回答でなければ）書かない
    answered, stage = m.event, m.stage.name
    undo: list[bool] = []

    def build(now: run.View) -> list[model.EventDraft]:
        undo.clear()
        if not _still(now, answered):
            return []
        undo.append(True)
        return [draft, *_undropped(now, stage, answered)]

    def after(conn: Any) -> None:
        if undo:
            _uncarry(conn, machine_id, p.get("carried_row"), why)

    run.append(st, machine_id, build, after)
    return why


def _undropped(v: run.View, stage: str, answered: model.Event) -> list[model.EventDraft]:
    """待ちへ戻した回答が外した課題を、今のスプリントの文脈の `issues` へ戻す事象（回答の前の値。`_dropped` の逆）。"""
    if not run.payload_of(answered).get("dropped"):
        return []
    before = dict(v.doc["context"])
    for ev in v.events:
        if ev.seq < answered.seq and ev.draft.kind == stages.OUTPUTS_EVENT and ev.draft.rev == v.rev:
            before.update(run.payload_of(ev)["outputs"])
    payload = {"outputs": {"issues": before["issues"]}}
    return [model.EventDraft(stages.OUTPUTS_EVENT, run.CONDUCTOR, stage, payload=payload, rev=v.rev)]


def _uncarry(conn: Any, machine_id: str, carried: Optional[str], reason: str) -> None:
    """待ちへ戻した回答が外した課題を入れたレーンの行を取り消す（`_carry` の逆）。"""
    if not carried:
        return
    found = lane.sprint_row(conn, machine_id)
    if found is None:
        return
    plan_id, name, _row = found
    lane.cancel_row(conn, plan_id, name, carried, lane.RowChange(run.CONDUCTOR, reason))


def _still(v: run.View, answered: model.Event) -> bool:
    m = run.decide(v)
    return m.action == run.Action.CONSUME and m.event is not None and m.event.seq == answered.seq


def _reject_release(conn: Any, q: Question, a: Answer) -> Optional[str]:
    """リリース承認ゲートの変更要求（承認しない回答）で、計画のステートマシンに開いているリクエストを変更要求の記録で閉じる。"""
    if q.gate != RELEASE_GATE or a.value not in REJECTING:
        return None
    found = release_request_of(conn, q)
    if found is None:
        return None
    plan_id, _name, held = found
    if held is None or held.closed is not None:
        return None
    plan.record_release(conn, plan_id, [held.key], plan.ReleaseAnswer(plan.REJECTED, a.reason))
    return held.key
