"""承認ゲートへの答え（`ndf answer`）と、答えを読む前の今の対象との照合（`ndf.progress.gate` の部品）。

待っていない行（実行の前）・違う問いの id・今の対象と違う見た対象は断り、行は待ちのまま残る（3364-2）。同じ冪等の鍵の
答え直しは書かずに同じ結果を返す。設計承認ゲートの承認で外した課題は、同じトランザクションの中でレーンの順番表の次の行へ
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
from ndf.progress import command, lane, model, plan, run, stages, store
from ndf.progress.gate.asking import (
    REJECTING,
    TOOL,
    Answer,
    Question,
    Target,
    accepted_path,
    material_path,
    material_sha256,
    plan_request,
    question,
    request_id,
    waiting,
)
from ndf.progress.gate.records import record_answer, rejections_path

# 答えた者の形（人・conductor・サービス。サービスは `service:<主体>`）
ACTORS = re.compile(r"^(human|conductor|organizer|service):[A-Za-z0-9._@/-]+$|^(human|conductor|organizer)$")

# --- 答え ---------------------------------------------------------------------------------------


# 今の対象を読む口（問いを渡し、PR の head と承認資料の sha256 を返す）
Observe = Callable[[Question], Target]


def no_observe(q: Question) -> Target:
    """今の対象を読まない（比べない）。"""
    return Target()


def github_observe(cwd: Path, root: Path, hub: Optional[Callable[[], GitHub]] = None) -> Observe:
    """今の対象を読む: PR の head の SHA（GitHub）と承認資料の sha256。読めなければ答えを受けない（`model.Refused`）。"""
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
                raise model.Refused(f"PR #{q.pr} の head を読めないので、答えと対象を照らせない: {exc}") from None
        path = material_path(root, q)
        return Target(head, material_sha256(path) if path.exists() else None)

    return observe


def _answered_with_key(v: run.View, key: str) -> Optional[model.Event]:
    for ev in v.events:
        p = ev.draft.payload if ev.draft.payload is not None else {}
        if ev.draft.kind == run.ANSWERED and p.get("key") == key and ev.draft.rev == v.rev:
            return ev
    return None


def _refusal(v: run.View, a: Answer, observe: Observe) -> Question:
    """答えを受けられるかを確かめ、待っている問いを返す。断るなら `model.Refused`（行は待ちのまま残る）。"""
    q = waiting(v)
    if q is None:
        raise model.Refused(f"進行表 {v.table_id} は答えを待っていない（実行の前か、済んだ答え）")
    if q.id != a.gate_id:
        raise model.Refused(f"問い {a.gate_id} は今の問い {q.id}（工程 {q.stage}）と違う")
    if a.value not in q.choices:
        raise model.Refused(f"工程 {q.stage} の答えは {', '.join(q.choices)} のどれか（渡された値 {a.value}）")
    if a.drop and not (a.value == run.APPROVED and q.gate == "design"):
        raise model.Refused("課題を外せるのは設計承認ゲートの承認だけ")
    changed = observe(q).differs(a.seen)
    if changed:
        raise model.Refused(f"答えた後に対象が変わった。待ちへ戻すので、今の対象を見て答え直す: {' / '.join(changed)}")
    return q


def _carry(conn: Any, table_id: str, issues: Sequence[int], reason: str) -> Optional[str]:
    """外した課題を、スプリントを持つレーンの順番表の次の行へ入れる。レーンの外のスプリントなら断る。"""
    if not issues:
        return None
    found = lane.sprint_row(conn, table_id)
    if found is None:
        raise model.Refused(f"進行表 {table_id} を持つレーンの行が無く、外した課題を入れる先が無い")
    plan_id, name, row = found
    return lane.carry_issues(conn, plan_id, name, row.key, list(issues), reason)


def answer(st: store.Store, table_id: str, a: Answer, observe: Observe = no_observe) -> dict[str, Any]:
    """答えを事象 `answered` に残す（外した課題の行も同じトランザクション）。同じ冪等の鍵は書かずに同じ結果を返す。"""
    if not ACTORS.match(a.by):
        raise model.Refused(f"答えた者 {a.by} の形が違う（human・conductor・organizer か service:<主体>）")
    text = run.notes_text(a.value, a.notes)
    with store.transaction(st) as conn:
        v = run.view(conn, table_id)
        held = _answered_with_key(v, a.key)
        if held is not None:
            return _same(held, a, table_id)
        q = _refusal(v, a, observe)
        carried = _carry(conn, table_id, a.drop, a.reason)
        rejected = _reject_release(conn, q, a)
        extra = {
            "gate_id": q.id,
            "gate": q.gate,
            "by": a.by,
            "key": a.key,
            "seen": {"head": a.seen.head, "material_sha256": a.seen.material},
            "dropped": list(a.drop),
            "carried_row": carried,
            "release_request": rejected,
        }
        draft = run.answer_draft(v, q.stage, a.value, a.reason, text, extra)
        drafts = [draft, *_outputs(v, q, a)]
        seqs = store.append_events(conn, table_id, drafts, v.events[-1].seq if v.events else 0)
    summary = f"工程 {q.stage} へ答え {a.value} を書いた（{a.by}）。続きは ndf progress next で流す"
    metrics = {"table_id": table_id, "seqs": seqs, "gate_id": q.id, "carried_row": carried}
    out = result.result(TOOL, result.OK, summary, metrics=metrics, next=result.command("progress", "next", table_id))
    return command.with_warnings(out, after=record_answer(v, q, a, rejections_path))


def _outputs(v: run.View, q: Question, a: Answer) -> list[model.EventDraft]:
    """答えが運ぶ値（`Answer.outputs`）を、承認ゲートの工程の出力として文脈へ書く事象。宣言に無い鍵は断る。"""
    if not a.outputs:
        return []
    draft, dropped = stages.outputs_draft(v.snap.stage(q.stage), v.rev, a.outputs, run.ACTOR)
    if dropped:
        raise model.Refused(f"工程 {q.stage} の定義の outputs に無い鍵は答えから書けない: {'; '.join(dropped)}")
    return [draft] if draft is not None else []


def _same(held: model.Event, a: Answer, table_id: str) -> dict[str, Any]:
    p = held.draft.payload if held.draft.payload is not None else {}
    if p.get("answer") != a.value or p.get("gate_id") != a.gate_id:
        raise model.Refused(f"冪等の鍵 {a.key} は別の答え（{p.get('answer')}・問い {p.get('gate_id')}）に使った")
    summary = f"冪等の鍵 {a.key} の答えは書いてある（事象 {held.seq}）"
    metrics = {"table_id": table_id, "seqs": [held.seq], "gate_id": a.gate_id, "replayed": True}
    return result.result(TOOL, result.OK, summary, metrics=metrics, next=result.command("progress", "next", table_id))


def lane_env(env: run.Env) -> run.Env:
    """スプリントの完了と同じトランザクションで、スプリントを持つレーンの行を終える `Env`（決定 17。レーンの外なら何もしない）。"""
    return replace(env, ended=lambda conn, table_id: lane.end_row(conn, table_id, lane.COMPLETED, run.ACTOR))


# 承認の後に加わった範囲（見た head → 今の head）が文書だけか。分からなければ False（出し直す側に倒す）
DocsOnly = Callable[[str, str], bool]


def never_docs_only(seen: str, now: str) -> bool:
    return False


def git_docs_only(cwd: Path) -> DocsOnly:
    """承認の後に加わった範囲が文書だけか（差分の分け方は `command.range_diff_kinds`。#3020）。

    見た head が今の head の祖先でない（履歴の書き換え）か、git が読めなければ文書だけと見なさない。振る舞いを決める
    文書（Skill の本文・エージェント定義）が加わったときも文書だけではない。
    """

    def docs_only(seen: str, now: str) -> bool:
        git = Git(cwd)
        try:
            return git.is_ancestor(seen, now) and command.range_diff_kinds(git, f"{seen}..{now}", cwd)["docs_only_diff"]
        except GitError:  # noqa: NDF401 外部コマンド: 範囲を読めなければ出し直す側に倒す
            return False

    return docs_only


def _docs_only_after(q: Question, seen: Target, now: Target, docs_only: DocsOnly) -> bool:
    """リリース承認ゲートで、違いが PR の head だけで、加わった範囲が文書だけか（出し直さない。#3020）。"""
    if q.gate != "release" or (now.material is not None and now.material != seen.material):
        return False
    return seen.head is not None and now.head is not None and docs_only(seen.head, now.head)


def _accept(root: Path, q: Question, now: Target) -> None:
    """出し直さずに進めた今の head を残す（マージの側が照らす head。`approved_head`）。"""
    path = accepted_path(root, q.table_id, q.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"head": now.head}) + "\n", encoding="utf-8")


def recheck(st: store.Store, table_id: str, observe: Observe, docs_only: DocsOnly = never_docs_only) -> Optional[str]:
    """答えを読む前に、答えた者が見た対象と今の対象を比べる。違えば待ちへ戻し（問いは新しい id）、理由を返す（3364-2）。

    `ndf answer` が書いた答え（問いの id と見た対象を持つ）だけを比べる。どの副命令でも、答えが読まれる前に打つ。待ちへ戻すのは承認ゲートのステップの
    待ちの記録で、答えはその後の答えだけが読まれる（`run.StepProgress`）。
    """
    v = run.read(st, table_id)
    m = run.decide(v)
    if m.action != run.Action.CONSUME or m.event is None or m.stage is None or m.step is None or m.progress is None:
        return None
    p = m.event.draft.payload if m.event.draft.payload is not None else {}
    if "gate_id" not in p:
        return None
    q = question(v, replace(m, action=run.Action.GATE))
    held = p["seen"] if isinstance(p.get("seen"), dict) else {}
    seen, now = Target(held.get("head"), held.get("material_sha256")), observe(q)
    changed = now.differs(seen)
    if not changed:
        return None
    if _docs_only_after(q, seen, now, docs_only):
        _accept(st.root, q, now)
        return f"承認の後に加わった範囲（{seen.head} → {now.head}）は文書だけなので、リリース承認ゲートを出し直さずに新しい範囲で進める"
    why = "答えた後に対象が変わった: " + " / ".join(changed)
    payload = {"step": m.step.id, "status": "waiting", "report": None, "generation": m.progress.generation, "reason": why}
    draft = model.EventDraft(run.STEP_EVENT, run.ACTOR, m.stage.name, payload=payload, rev=v.rev)
    # 比べた後に答えが読まれていれば（同じ答えの事象が今も読む答えでなければ）書かない
    answered = m.event
    run.append(st, table_id, lambda now: [draft] if _still(now, answered) else [])
    return why


def _still(v: run.View, answered: model.Event) -> bool:
    m = run.decide(v)
    return m.action == run.Action.CONSUME and m.event is not None and m.event.seq == answered.seq


def _reject_release(conn: Any, q: Question, a: Answer) -> Optional[str]:
    """リリース承認ゲートの差し戻し（承認しない答え）で、計画の進行表に開いている依頼を差し戻しの記録で閉じる。"""
    if q.gate != "release" or a.value not in REJECTING:
        return None
    found = lane.sprint_row(conn, q.table_id)
    if found is None:
        return None
    view, _ = lane.read_plan(conn, found[0])
    held = plan_request(view, request_id(q))
    if held is None or held.closed is not None:
        return None
    plan.record_release(conn, found[0], [held.key], plan.ReleaseAnswer(plan.REJECTED, a.reason))
    return held.key
