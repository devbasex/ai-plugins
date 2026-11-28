"""承認ゲートへの回答（`ndf answer`）と、回答を読む前の今の対象との検証（`ndf.progress.gate` の部品）。

待っていない行（実行の前）・違う質問の id・今の対象と違う見た対象は断り、行は待ちのまま残る（3364-2）。同じ冪等のキーの
回答し直しは書かずに同じ結果を返す。設計承認ゲートの承認で外した課題は、同じトランザクションの中でレーンの順番表の次の行へ
入れる（#3097 の決定 99）。
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional, Protocol

from ndf.adapters.git import Git, GitError
from ndf.adapters.github import GitHub, client
from ndf.contracts import result
from ndf.core import actors, approval_words, merge_approval
from ndf.progress import approval_decide, command, invoke, lane, model, plan, run, stages, store
from ndf.progress.gate.asking import (
    ORGANIZER_ANSWERS,
    DESIGN_GATE,
    REJECTING,
    RELEASE_GATE,
    TOOL,
    Answer,
    Question,
    Target,
    material_path,
    material_sha256,
    note_posted,
    presented_id,
    question,
    read_posted,
    read_presented,
    release_request_of,
    waiting,
    write_posted,
)
from ndf.progress.gate.records import record_answer, rejections_path
from ndf.progress.gate.targets import TargetReader
from ndf.progress.run_view import closes_only, read_accepted_head, write_accepted_head

# 回答した者の形（人・conductor・サービス。サービスは `service:<主体>`。プランの承認待ちと共有する）
ACTORS = actors.ACTORS

# --- 回答 ---------------------------------------------------------------------------------------


class Observe(Protocol):
    """今の対象を読む口（質問を渡し、PR の head・本文と題の sha256 と承認資料の sha256 を返す）。`material` が偽なら資料を読まない。
    マージ先の設定（承認の対象と本番へ反映されるか）は、`material` か `config` が真のときに読む。"""

    def __call__(self, q: Question, *, material: bool = True, config: bool = False) -> Target: ...


def no_observe(q: Question, *, material: bool = True, config: bool = False) -> Target:
    """今の対象を読まない（比べない）。"""
    return Target()


def shared_hub(cwd: Path, hub: Optional[Callable[[], GitHub]] = None) -> Callable[[], GitHub]:
    """初めに使うときに GitHub を 1 つだけ作り、以後は同じものを返す口。`hub` があればそれで、無ければ `cwd` のリポジトリで作る。

    今の対象を読む口と証跡を書く口が同じ口を共有すると、設定の読み取り（`branch_file`）が同じ要求で読んだ token の利用者の
    login を証跡の書き込みが使い、login の読み取りを増やさない。
    """
    made: list[GitHub] = []

    def get() -> GitHub:
        if not made:
            made.append(hub() if hub is not None else GitHub(Git(cwd).remote_repo(), client()))
        return made[0]

    return get


def github_observe(cwd: Path, root: Path, hub: Optional[Callable[[], GitHub]] = None, reader: Optional[TargetReader] = None) -> Observe:
    """今の対象を読む: PR の head の SHA・本文と題の sha256（GitHub の同じ 1 回の取得。#4446）、その head の承認資料の
    sha256、承認の対象（ベースリポジトリ・番号・マージ先）と本番へ反映されるか（#4409）。読めなければ回答を受けない（`model.Refused`）。

    資料は今の head を入れた質問の `material_path` で探す（資料は head ごとに別のファイル。#3959）。

    読み取りの回数（#4495 で設計承認ゲートも PR を持ち、増えた分）: 提示（`present`）と回答（`answer`）は PR 1 回とマージ先の
    設定 1 回、回答を読む前の確かめ（`recheck`）は PR 1 回だけで、1 つの呼び出しの中で同じものを読み直さない。どれも承認を
    設計 PR の head とマージ先に結び付け、本番へ反映されるかを決めるのに要る（無ければ対象の無い承認で設計 PR をマージする）。
    退けた代わりの案: (1) 提示で読んだ値を回答で使う — 提示の後に head かマージ先の設定が変わったことを回答で断れなくなる
    （`_check_presented`）。(2) `ndf progress next` の確かめで読んだ PR を同じコマンドの提示で使う — 間に流れるステップが PR へ
    push すると古い head を提示し、回答が必ず断られる。"""
    held = reader

    def observe(q: Question, *, material: bool = True, config: bool = False) -> Target:
        # 渡された読み手が無ければ、毎回読み直す
        got = (held if held is not None else TargetReader(shared_hub(cwd, hub))).read(q, config=material or config).target
        if not material:
            return got
        path = material_path(root, replace(q, target=Target(got.head)))
        return replace(got, material=material_sha256(path) if path.exists() else None)

    return observe


# マージの承認の証跡を書く口（行を渡し、書いたら真、同じ記録の証跡が既にあれば偽）
Post = Callable[[merge_approval.MergeRow], bool]


def no_post(row: merge_approval.MergeRow) -> bool:
    """証跡を書かない口（PR を読まないテストと、承認の対象を持たない質問）。"""
    return False


def github_post(cwd: Path, hub: Optional[Callable[[], GitHub]] = None) -> Post:
    """`merge_approval.write_merge_row` を token の利用者（アカウント ID）で呼ぶ口。GitHub は 1 つだけ作る。

    `observe` と同じ GitHub を渡せば、設定の読み取りで読んだ利用者を使い、利用者の読み取りを増やさない。
    書けない（利用者のアカウント ID を読めない・値が不正）なら `model.Refused`（回答は記録しない）。"""
    get_hub = shared_hub(cwd, hub)

    def post(row: merge_approval.MergeRow) -> bool:
        made = get_hub()
        try:
            return merge_approval.write_merge_row(made, row, made.viewer())
        except merge_approval.TrailError as exc:
            raise model.Refused(f"マージの承認の証跡を書けないので、回答を記録しない: {exc}") from None

    return post


def _answered_with_key(v: run.View, key: str) -> Optional[model.Event]:
    for ev in v.events:
        p = run.event_payload(ev)
        if ev.draft.kind == run.ANSWERED and p.get("key") == key and ev.draft.rev == v.rev:
            return ev
    return None


def _refusal(v: run.View, a: Answer, observe: Observe, root: Optional[Path] = None) -> tuple[Question, Target]:
    """回答を受けられるかを確かめ、待っている質問と、照らした対象を返す。断るなら `model.Refused`（行は待ちのまま残る）。

    提示した承認の対象（`read_presented`）があれば、照らした対象はその対象そのもので、今の承認の対象か回答した者の見た
    HEAD と違えば断る（質問の後に HEAD かマージ先が変わった。#4409）。回答が見た提示の識別子と、本番へ反映されるかが
    提示と違うときも断る（`_check_presented`）。無ければ（旧版の質問）回答した者の見た head を返す。"""
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
    now = observe(q)
    changed = now.differs(a.seen)
    if changed:
        raise model.Refused(f"回答した後に対象が変わった。待ちへ戻すので、今の対象を見て回答し直す: {' / '.join(changed)}")
    shown = read_presented(root, q) if root is not None else Target()
    if shown.binding() is None:
        return q, shown.presented(a.seen if a.seen.head is not None else now)
    _check_presented(shown, now, a.seen)
    material = now.material if now.material is not None else a.seen.material
    return q, replace(shown, material=material, body=a.seen.body, title=a.seen.title)


def _check_presented(shown: Target, now: Target, seen: Target) -> None:
    """提示した承認の対象 `shown` を、今の対象 `now` と回答した者の見た対象 `seen` に照らす。違えば `model.Refused`。

    照らすのは提示した時点の承認の対象そのもの（回答の経路の HEAD で上書きしない。古い資料への回答を新しい HEAD の承認にしない）。
    """
    held, current = shown.binding(), now.binding()
    if held is None:
        return
    if (current is not None and not held.same(current)) or (seen.head is not None and seen.head != held.head):
        raise model.Refused(f"質問の後に承認の対象が変わった（今は {now.base} 宛て、HEAD {now.head}）。今の対象で質問し直す")
    # 回答は見た資料の提示に結び付ける。承認の対象が A → B → A と戻って同じ内容の資料を出し直しても、最初の提示への回答を受けない
    if shown.presentation and seen.presentation != shown.presentation:
        raise model.Refused(
            f"回答が今の提示（{shown.presentation}）への回答でない（見た提示 {seen.presentation or '無し'}）。"
            "資料を出し直したので、今の資料で回答し直す"
        )
    # PR・HEAD・マージ先が同じでも、マージ先の設定が変わって本番へ反映されるかが変われば、古い資料への回答を受けない
    if current is not None and now.production is not None and shown.production != now.production:
        reach = "反映される" if now.production else "反映されない"
        raise model.Refused(
            f"質問の後にマージ先の設定が変わり、このマージは本番へ{reach}ようになった。資料を出し直すので、今の資料で回答し直す"
        )


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


# 証跡を書く回答 → 証跡の回答（変更要求は承認しない回答なので、先の承認を取り消す却下として書く）
_TRAIL_ANSWERS = {
    run.APPROVED: approval_words.APPROVED,
    run.REJECTED: approval_words.REJECTED,
    run.REQUEST_CHANGES: approval_words.REJECTED,
}
# MVV 判定が通した回答（証跡は `mvv_gate.py` が判定の記録と同じコメントに書くので、ここでは書かない）
MVV_BY = model.MVV_SERVICE


def _trail_by(conn: Any, q: Question, a: Answer, seen: Target) -> Optional[str]:
    """回答した者から証跡の担い手を決める。書かないなら None、断るなら `model.Refused`（#4409 の決定 7）。

    承認の対象がそろった（`seen.binding()`）PR を持つ承認ゲートで書く。承認は、マージが本番へ反映され得るときだけ書く
    （反映されないマージの承認は読む側が無い）。却下は反映されないときも書く（同じ対象への先の承認を、設定が自動反映へ
    戻った後も取り消す）。利用者（`human`）と、利用者の回答を中継したと確かめられた `organizer` は `user`。`service:mvv` は
    書かない。ほかの回答した者（`conductor`・ほかの `service:<主体>`）は、本番へ反映され得るなら断り（AI の判断で担い手
    `user` の証跡を作らない）、反映されないなら書かない。
    """
    if seen.binding() is None or q.gate not in (DESIGN_GATE, RELEASE_GATE) or a.by == MVV_BY:
        return None
    if not seen.production and _TRAIL_ANSWERS.get(a.value) != approval_words.REJECTED:
        return None
    role = a.by.split(":", 1)[0]
    if role == actors.HUMAN or (role == "organizer" and _relayed_by_organizer(conn, q, a)):
        return merge_approval.USER_BY
    if seen.production:
        raise model.Refused(
            f"回答した者 {a.by} の承認は、本番へ反映されるマージの証跡にならない（利用者が --by human で回答する。"
            "オーガナイザーはリクエストに記録された利用者の回答だけを中継する）"
        )
    return None


def _relayed_by_organizer(conn: Any, q: Question, a: Answer) -> bool:
    """`organizer` の回答が、質問のリリースのリクエストに記録された利用者の同じ回答の中継か。"""
    found = release_request_of(conn, q) if q.gate == RELEASE_GATE else None
    held = found[2] if found is not None else None
    reply = held.release if held is not None and held.release is not None else None
    if not isinstance(reply, dict):
        return False
    return ORGANIZER_ANSWERS.get(str(reply.get("result"))) == a.value


def _post_trail(root: Optional[Path], q: Question, a: Answer, seen: Target, by: str, post: Post) -> Optional[str]:
    """照らした承認の対象と提示ごとの識別子（`presented_id`）で証跡の行を作り、`post` で書く。

    同じ提示への送り直しだけが重なりとして書かれない。資料を出し直した後の回答は、出し直す前の提示の証跡があっても書く。
    `root` があれば（回答を DB に記録していなければ）、本番へ反映される承認を投稿の前にファイルへ書き（`note_posted`）、
    書いた記録の識別子を返す。呼び手は DB の確定の後にそれを消す。"""
    target = seen.binding()
    if target is None or a.value not in _TRAIL_ANSWERS:
        return None
    record = presented_id(replace(q, target=seen))
    row = merge_approval.MergeRow(_TRAIL_ANSWERS[a.value], by, target, seen.production is True, record)
    noted = root is not None and row.answer == approval_words.APPROVED and row.production
    if noted:
        note_posted(root, q.machine_id, q.stage, target, record)
    post(row)
    return record if noted else None


def answer(st: store.Store, machine_id: str, a: Answer, observe: Observe = no_observe, post: Post = no_post) -> dict[str, Any]:
    """回答を事象 `answered` に残す（外した課題の行と、今のスプリントの課題から外す事象も同じトランザクション）。
    同じ冪等のキーは書かずに同じ結果を返す。

    回答の確かめから DB の確定までは、開き直しと共有する排他（`store.approval_lock`）の中で行う。

    承認の対象を持つ質問への回答は、記録の前にマージの承認の証跡を PR へ書く（`_trail_by`・`_post_trail`。#4409 の
    決定 7）。承認は本番へ反映され得るときだけ、却下はいつも書く。証跡を書けなければ回答を記録せず、同じ回答を
    送り直すと続きから回復する。同じ冪等のキーの回答が既に
    記録されていても、証跡が無ければ書いてから戻る。"""
    if not ACTORS.match(a.by):
        raise model.Refused(actors.invalid_message(a.by))
    text = run.notes_text(a.value, a.notes)
    # 回答の確かめから証跡の投稿と DB の確定まで、開き直しと同じ排他で守る
    with store.approval_lock(st, machine_id):
        with store.transaction(st) as conn:
            v = run.view(conn, machine_id)
            held = _answered_with_key(v, a.key)
            if held is not None:
                return _same(held, a, machine_id, lambda: _ensure_trail(conn, v, held, a, post))
            q, seen = _refusal(v, a, observe, st.root)
            # 以後の質問は照らした対象（提示した承認の対象）を持つ。リリースのリクエストと証跡の識別子を提示ごとに分ける
            q = replace(q, target=seen)
            # 手で終えたデプロイの回答待ちへの回答は、工程を閉じる確認で、マージの事前の承認ではない
            closes = q.gate == RELEASE_GATE and run.step_progress(v, q.stage).held_finish is not None
            by = _trail_by(conn, q, a, seen)
            posted = None
            if by is not None and not (closes and a.value == run.APPROVED):
                posted = _post_trail(st.root, q, a, seen, by, post)
            carried = _carry(conn, machine_id, a.drop, a.reason)
            rejected = _reject_release(conn, q, a)
            extra = {
                "gate_id": q.id,
                "gate": q.gate,
                "by": a.by,
                "key": a.key,
                "seen": seen.as_dict(),
                "dropped": list(a.drop),
                "carried_row": carried,
                "release_request": rejected,
                **({"closes_stage": True} if closes else {}),
            }
            draft = run.answer_draft(v, q.stage, a.value, a.reason, text, extra)
            drafts = [draft, *_outputs(v, q, a), *_dropped(v, q, a.drop)]
            seqs = store.append_events(conn, machine_id, drafts, v.events[-1].seq if v.events else 0)
        if posted is not None:
            forget_posted(st.root, machine_id, {posted})  # 確定した承認は事象から取り消せる
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
    payload: dict[str, Any] = {"outputs": kept}
    if a.release_store is not None:
        payload["release_store"] = a.release_store
    return [model.EventDraft(stages.OUTPUTS_EVENT, run.ACTOR, q.stage, payload=payload, rev=v.rev)]


def _ensure_trail(conn: Any, v: run.View, held: model.Event, a: Answer, post: Post) -> None:
    """記録済みの回答の証跡が無ければ書く（証跡の後・記録の前でなく、記録の後に止まった送り直し）。

    担い手は記録した回答した者（事象の `by`）から決める。送り直しの `a.by` は `_same` が記録と同じことを確かめている。
    承認の境界（`approval_decide.approval_boundary`）より前の回答には書かない（取り消した承認を、送り直しで PR へ戻さない）。
    工程を閉じるだけの承認（`closes_only`）にも書かない。"""
    p = run.event_payload(held)
    rows = [ev for ev in v.events if ev.draft.row_key == held.draft.row_key]
    if held.seq < approval_decide.approval_boundary(rows, str(p.get("step"))):
        return
    if closes_only(p) and p.get("answer") == run.APPROVED:
        return
    seen = Target.from_dict(p.get("seen"))
    gate = p.get("gate")
    if seen.binding() is None or not isinstance(p.get("gate_id"), str):
        return
    q = Question(
        str(p["gate_id"]),
        v.machine_id,
        str(held.draft.row_key),
        str(p.get("step")),
        gate if isinstance(gate, str) else None,
        (),
        target=seen,
    )
    by = _trail_by(conn, q, replace(a, by=str(p.get("by"))), seen)
    if by is not None:
        _post_trail(None, q, a, seen, by, post)


def _same(held: model.Event, a: Answer, machine_id: str, ensure: Callable[[], None] = lambda: None) -> dict[str, Any]:
    p = run.event_payload(held)
    if p.get("answer") != a.value or p.get("gate_id") != a.gate_id:
        raise model.Refused(f"冪等のキー {a.key} は別の回答（{p.get('answer')}・質問 {p.get('gate_id')}）に使った")
    # 送り直しで回答した者を変えない（記録は自動の処理の回答なのに、送り直しの申告で利用者の証跡を書かない）
    if p.get("by") != a.by:
        raise model.Refused(f"冪等のキー {a.key} の回答は {p.get('by')} が書いた。送り直しの回答した者 {a.by} と違う")
    ensure()
    summary = f"冪等のキー {a.key} の回答は書いてある（事象 {held.seq}）"
    metrics = {"machine_id": machine_id, "seqs": [held.seq], "gate_id": a.gate_id, "replayed": True}
    return result.result(TOOL, result.OK, summary, metrics=metrics, next=result.command("progress", "next", machine_id))


def lane_env(env: run.Env) -> run.Env:
    """スプリントの完了と同じトランザクションで、スプリントを持つレーンの行を終える `Env`（決定 17。レーンの外なら何もしない）。

    あわせて、スプリントが計画のレーンの行にあるかを読む口（読み手役のステップを済ませる判定。#4446）を足す。"""
    return replace(env, ended=lambda conn, machine_id: lane.end_row(conn, machine_id, lane.COMPLETED, run.ACTOR), in_lane=in_lane)


def in_lane(st: store.Store, machine_id: str) -> bool:
    """スプリントが計画のレーンの行にあるか（`release_request_of` と同じ `lane.sprint_row` で決める）。"""
    with store.transaction(st) as conn:
        return lane.sprint_row(conn, machine_id) is not None


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


def seen_material(root: Path, q: Question, seen: Target) -> Optional[str]:
    """回答した者が見た承認資料の sha256。見た head の資料（新しい名前）、無ければ更新前の名前 `<質問の id>.md` の順に探し、
    最初に見つかった 1 つだけを読む。どちらも無ければ None（#3959）。"""
    paths = [material_path(root, replace(q, target=Target(seen.head)))] if seen.head else []
    for path in (*paths, material_path(root, replace(q, target=Target()))):
        if path.exists():
            return material_sha256(path)
    return None


def _material_moved(root: Path, q: Question, seen: Target) -> Optional[str]:
    """回答した者が見た資料が、その後に変わったか消えたなら違いの文（突き合わせられなければ変わったと扱う）。ハッシュを記録しない
    回答（`service:mvv` の回答など）は資料で比べない。"""
    if seen.material is None:
        return None
    got = seen_material(root, q, seen)
    return None if got == seen.material else f"承認資料の sha256（見た値 {seen.material}・今の値 {got or '無し'}）"


def _docs_only_after(root: Path, q: Question, seen: Target, now: Target, docs_only: DocsOnly) -> bool:
    """リリース承認ゲートで、加わった範囲が文書だけで、承認した者が見た資料が変わっていないか（出し直さない。#3020）。

    新しい head の資料は比べない（表示済みかどうかで結論を変えないため。#3959）。"""
    if q.gate != RELEASE_GATE or seen.head is None or now.head is None or not docs_only(seen.head, now.head):
        return False
    return _material_moved(root, q, seen) is None


def _accept(root: Path, q: Question, now: Target) -> None:
    """出し直さずに進めた今の head を残す（マージの側が照らす head。`approved_head`）。"""
    write_accepted_head(run.work_root(root, q.machine_id), q.id, now.head)


def standing(reader: TargetReader, root: Path) -> approval_decide.StandingOf:
    """記録した承認の回答を今の対象に照らす口（`run.Env.standing`）。承認資料の比較と、文書だけが加わって
    出し直さなかった head は、回答を読む前の確かめ（`recheck`）と同じ規則で扱う。"""

    def of(v: run.View, gate: Optional[str], pr: Optional[int], answered: model.Event) -> approval_decide.Standing:
        p = run.event_payload(answered)
        q = Question(str(p.get("gate_id")), v.machine_id, str(answered.draft.row_key), str(p.get("step")), gate, (), pr)
        got = reader.read(q)
        seen = Target.from_dict(p.get("seen"))
        carried = read_accepted_head(run.work_root(root, v.machine_id), q.id) if seen.production is False else None
        found = approval_decide.differences(p, got.target.as_dict(), gate=gate, carried_head=carried)
        moved = _material_moved(root, q, seen)
        return approval_decide.Standing([*found, *([moved] if moved else [])], got.merged, got.target.head)

    return of


def waiting_revocations(root: Path, v: run.View, stage: str) -> list[merge_approval.Revocation]:
    """工程の待ちを書き直すときに取り消す承認。記録した承認（`approval_decide.stage_revocations`）に、投稿を試みて回答が
    記録されていない承認を足す（`with_unrecorded`）。"""
    return with_unrecorded(root, v, [stage], approval_decide.stage_revocations(v, stage))


def with_unrecorded(
    root: Path, v: run.View, names: Sequence[str], revocations: Sequence[merge_approval.Revocation]
) -> list[merge_approval.Revocation]:
    """取り消す承認 `revocations` に、工程 `names` で投稿を試みて回答が記録されていない承認を足す。回答は証跡を投稿した後の
    DB の確定に失敗しうるので、記録の無い承認は投稿の前に書いたファイル（`read_posted`）から指す。資料を出し直しても残る。"""
    found = list(revocations)
    posted = [row for row in read_posted(root, v.machine_id) if row[0] in names]
    if not posted:
        return found  # 突き合わせる行が無ければ、回答の記録を走査しない
    recorded = _answered_records(v)
    for stage, target, record in posted:
        if record in recorded:
            continue
        for i, held in enumerate(found):
            if held.target.same(target):
                found[i] = held._replace(approvals=held.approvals | {record})
                break
        else:
            reopen_id = f"reopen:{v.machine_id}:{stage}:{record}"
            found.append(merge_approval.Revocation(target, frozenset({record}), reopen_id, unconfirmed=True))
    return found


def _answered_records(v: run.View) -> set[str]:
    """記録した回答の、証跡の記録の識別子。"""
    answered = (run.event_payload(ev) for ev in v.events if ev.draft.kind == run.ANSWERED)
    return {merge_approval.presented_record_id(str(p.get("gate_id")), Target.from_dict(p.get("seen")).presentation) for p in answered}


def revoked_records(revoked: Sequence[merge_approval.Revocation]) -> set[str]:
    return {record for r in revoked for record in r.approvals}


def forget_posted(root: Path, machine_id: str, done: set[str]) -> None:
    """投稿の前に書いたファイルから、記録の識別子が `done` の承認を消す。消すのは、取り消し終えた承認（次の操作が、
    取り消し済みの承認で PR をキューから外さない）と、回答が DB に確定した承認（行を増やし続けない）である。"""
    rows = read_posted(root, machine_id)
    kept = [row for row in rows if row[2] not in done]
    if len(kept) != len(rows):
        write_posted(root, machine_id, kept)


def no_revoke(revocations: list[merge_approval.Revocation]) -> None:
    """承認の取り消しを書かない口（PR を読まないテスト）。"""


@dataclass(frozen=True)
class Recheck:
    """`recheck` の打ち方。`advancing` が偽なら何も取り消さず書かない。照らした回答の事象の番号は `checked` に足す。
    `locked` は呼び手が作業の排他を持つか。"""

    advancing: bool = True
    checked: Optional[set[int]] = None
    locked: bool = False


def recheck(
    st: store.Store,
    machine_id: str,
    observe: Observe,
    docs_only: DocsOnly = never_docs_only,
    revoke: Callable[[list[merge_approval.Revocation]], None] = no_revoke,
    how: Recheck = Recheck(),
) -> Optional[str]:
    """回答を読む前に、回答した者が見た対象と今の対象を比べる。違えば待ちへ戻し（質問は新しい id）、理由を返す（3364-2）。
    承認の対象を持つ回答は、人の回答も MVV の回答も、マージ先と本番へ反映されるかを比べる（#4520 の懸念 1）。
    待ちへ戻す前に、`revoke` で PR の証跡の承認を取り消す（取り消せなければ何も書かない）。取り消しと書き込みは、回答を読む
    操作と同じ排他（`_driving`）と、回答と共有する排他（`store.approval_lock`）の中で行う。工程を進めない呼び出し（`how.advancing` が偽。`show` など）は、何も取り消さず書かずに、
    違いと次の操作を返す。

    `ndf answer` が書いた回答（質問の id と見た対象を持つ）だけを比べる。どのサブコマンドでも、回答が読まれる前に打つ。工程を進める操作（`next`・`work`）は、作業の排他の中で打つ（`verifier`）。待ちへ戻すのは承認ゲートのステップの
    待ちの記録で、回答はその後の回答だけが読まれる（`run.StepProgress`）。回答が課題を外していれば、同じトランザクションで
    文脈の `issues` を回答の前へ戻し、外した課題を入れたレーンの行を取り消す（回答し直しで同じ課題を外せる）。
    設計 PR に結び付けずに記録した設計の承認（#4495 の直しの前のバージョンの承認）は、対象と比べる前に、消費済みでもマージの前なら同じ取り消しで
    待ちへ戻す（`_unbound_approval`・`_reopen_unbound`。#4495）。
    """
    v = run.read(st, machine_id)
    later = f"。ndf progress next {machine_id} で承認の待ちへ戻す"
    if _unbound_approval(v) is not None:
        return _reopen_unbound(st, machine_id, revoke, how.locked) if how.advancing else UNBOUND_REASON + later
    m = run.decide(v)
    if m.action != run.Action.CONSUME or m.event is None or m.stage is None or m.step is None or m.progress is None:
        return None
    p = run.event_payload(m.event)
    if "gate_id" not in p:
        return None
    if how.checked is not None:
        how.checked.add(m.event.seq)
    q = question(v, replace(m, action=run.Action.GATE))
    # head を先に照らし、資料はその結論に要る 1 つだけを読む（#3959）。本文と題は head と同じ 1 回の取得で比べる（#4446）
    seen = Target.from_dict(p.get("seen"))
    # マージ先の設定は、承認の対象を持つ回答のときだけ読む。資料は下の `_material_moved` が照らす
    now = observe(q, material=False, config=seen.base is not None)
    texts = replace(now, head=None).differs(seen)
    if now.binding() is not None:  # 今の対象が分かるときだけ比べる（ほかの項目と同じ）
        texts += approval_decide.binding_differences(now.as_dict(), seen.as_dict())
    # 本文か題が違えば、文書だけの範囲でも繰り上げない
    moved, carried = _moved(st.root, p, q, now, never_docs_only if texts else docs_only)
    if carried:
        if not how.advancing:
            return None
        _accept(st.root, q, now)
        return f"承認の後に加わった範囲（{seen.head} → {now.head}）は文書だけなので、リリース承認ゲートを出し直さずに新しい範囲で進める"
    changed = [*([moved] if moved else []), *texts]
    if not changed:
        return None
    why = "回答した後に対象が変わった: " + " / ".join(changed)
    if not how.advancing:
        return why + later
    return why if _send_back(st, v, m, why, revoke, how.locked) else None


def verifier(
    st: store.Store,
    observe: Observe,
    docs_only: DocsOnly,
    revoke: Callable[[list[merge_approval.Revocation]], None],
    forget: Callable[[], None],
    take: Callable[[], Optional[str]] = lambda: None,
) -> Callable[[str, Optional[int]], Optional[str]]:
    """`run.Env.verify`: 工程を進める操作の `recheck` を、作業の排他の中で打つ。排他を待つ間や、回答が作業の排他を取らずに
    書かれる間に対象が変わりうるためである。照らす前に読んだ対象を捨て、ここで読んだ結果を後の判定が使う。同じ回答は
    照らし直さない。

    `take`（リリースのリクエストへの回答を承認ゲートの回答として書く）は初回に 1 度だけ、照らす前に打つ。`take` が読んだ対象を
    `recheck` も使い、`take` の知らせと `recheck` の理由をまとめて返す。"""
    checked: set[int] = set()
    pending = [take]

    def verify(machine_id: str, seq: Optional[int]) -> Optional[str]:
        if seq in checked:
            return None
        forget()
        took = pending.pop()() if pending else None
        why = recheck(st, machine_id, observe, docs_only, revoke, Recheck(checked=checked, locked=True))
        found = [text for text in (took, why) if text is not None]
        if not found:
            return None
        return " / ".join(found)

    return verify


def _moved(root: Path, p: Mapping[str, Any], q: Question, now: Target, docs_only: DocsOnly) -> tuple[Optional[str], bool]:
    """回答の payload `p` が見た head と承認資料の、今との違いの文（無ければ None）と、加わった範囲が文書だけで出し直さずに
    繰り上げるか（`recheck` の比較）。"""
    seen = Target.from_dict(p.get("seen"))
    if p.get("by") == model.MVV_SERVICE:
        return _judged_elsewhere(p, q, seen, now), False
    if seen.head is None or now.head is None or seen.head == now.head:
        return _material_moved(root, q, seen), False
    if not seen.production and _docs_only_after(root, q, seen, now, docs_only):
        return None, True
    return f"head の SHA（見た値 {seen.head}・今の値 {now.head}）", False


@contextlib.contextmanager
def _driving(st: store.Store, machine_id: str, locked: bool = False) -> Iterator[None]:
    """回答を読む操作（`next`・`work`）と同じ排他。起動した側が放すまで待ち、取れなければ `model.Refused`。
    呼び手が持っていれば（`locked`）取り直さない。"""
    with contextlib.ExitStack() as stack:
        if not locked and not run._work_lock(stack, st, machine_id, time.sleep):
            raise model.Refused(f"ステートマシン {machine_id} を流す作業が動いている。ndf progress wait {machine_id} で終わりを待つ")
        yield


def _send_back(
    st: store.Store,
    v: run.View,
    m: run.Move,
    why: str,
    revoke: Callable[[list[merge_approval.Revocation]], None],
    locked: bool,
) -> bool:
    """読む前の回答 `m.event` の承認の証跡を取り消してから、承認ゲートの待ちへ戻す（`recheck` の書き込み）。
    回答が先に読まれていれば、何も取り消さず書かずに偽を返す。"""
    assert m.event is not None and m.stage is not None and m.step is not None and m.progress is not None
    p = run.event_payload(m.event)
    draft = _no_report_draft(v, m.stage.name, m.step.id, "waiting", m.progress.generation, why)
    # 比べた後に回答が読まれていれば（同じ回答の事象が今も読む回答でなければ）書かない
    answered, stage, gate = m.event, m.stage.name, m.step.gate
    undo: list[bool] = []

    def build(now: run.View) -> list[model.EventDraft]:
        undo.clear()
        if not _still(now, answered):
            return []
        undo.append(True)
        return [draft, *_reading_reset(now, stage, gate), *_undropped(now, stage, answered)]

    def after(conn: Any) -> None:
        if undo:
            _uncarry(conn, v.machine_id, p.get("carried_row"), why)

    # 回答と共有する排他は作業の排他の後に取る（作業の排他を待つ間の回答を断らない）。
    # 取り消してから境界を進めるまでの間に回答を送り直されると、取り消した承認が PR に戻るので、回答側も止める
    with _driving(st, v.machine_id, locked), store.approval_lock(st, v.machine_id):
        latest = run.read(st, v.machine_id)
        if not _still(latest, answered):
            return False
        revoke(approval_decide.stage_revocations(latest, stage))
        run.append(st, v.machine_id, build, after)
    return True


# 対象に結び付かない設計の承認を待ちへ戻す理由（承認ゲートの待ちの記録に残る）
UNBOUND_REASON = (
    "設計 PR（PR・マージ先・head）に結び付けずに記録した設計の承認（設計 PR を items から読まなかったバージョンの承認）なので、"
    "今の設計 PR で承認し直す"
)


@dataclass(frozen=True)
class _Unbound:
    """対象に結び付かない設計の承認と、その承認ゲート。`consumed` なら承認ゲートのステップは済んでいる（マージの前）。"""

    stage: stages.StageDef
    step: stages.StepDef
    generation: int
    answered: model.Event
    consumed: bool


def _unbound_approval(v: run.View) -> Optional[_Unbound]:
    """今の工程の設計承認ゲートの承認が、items から補った設計 PR に結び付かずに記録されたものなら、その承認（#4495 の直しの前のバージョン）。

    今のバージョンの回答は、設計 PR が特定できれば提示した承認の対象（PR・マージ先・head。`Target.binding`）を `seen` に記録する。
    items を読まなかった前のバージョンは、設計 PR が items にだけあると質問に PR が無く、対象に結び付かない承認を記録した。
    `ndf answer --head` で回答していれば `seen` は head を持つが、PR とマージ先を持たないので、head の有無では分けない（#4606）。そのまま読むと対象に結び付かない承認で設計 PR の
    マージへ進むので、読む前（`run.Action.CONSUME`）と、消費して承認ゲートのステップが済んだ後でも後ろのステップ（マージ）が
    済む前なら拾う。outputs に明示した設計 PR は前のバージョンも質問に載せて head を記録したので、ここでは見ない。"""
    m = run.decide(v)
    if m.stage is None or m.progress is None:
        return None
    s, prog = m.stage, m.progress
    at = s.gate_index(DESIGN_GATE)
    if at is None:
        return None
    step = s.steps[at]
    if m.action == run.Action.CONSUME and m.step is not None and m.step.id == step.id and m.event is not None:
        answered, consumed = m.event, False
    elif step.id in prog.done and not any(st.id in prog.done for st in s.steps[at + 1 :]):
        report = prog.done[step.id]
        seq = report["metrics"].get("seq") if isinstance(report.get("metrics"), dict) else None
        found = [ev for ev in v.row_events(s.name) if ev.draft.kind == run.ANSWERED and ev.seq == seq]
        if not found:
            return None
        answered, consumed = found[0], True
    else:
        return None
    p = run.event_payload(answered)
    if p.get("answer") != run.APPROVED or p.get("by") == model.MVV_SERVICE:
        return None
    filled = stages.item_only_outputs(s, prog.done)
    if Target.from_dict(p.get("seen")).binding() is not None or type(filled.get(stages.DESIGN_PR)) is not int:
        return None
    return _Unbound(s, step, prog.generation, answered, consumed)


def _reopen_unbound(
    st: store.Store, machine_id: str, revoke: Callable[[list[merge_approval.Revocation]], None], locked: bool
) -> Optional[str]:
    """対象に結び付かない設計の承認（`_unbound_approval`）を取り消し、設計承認ゲートの待ちへ戻す。

    対象が変わったときに待ちへ戻す経路（`recheck`）と同じく、1 回のトランザクションで、承認ゲートの待ち・読み手役の
    ステップを未了に戻す事象・回答が外した課題を文脈の `issues` へ戻す事象を書き、外した課題を入れたレーンの行を取り消す。
    消費済みなら承認ゲートのステップを先に未了に戻す。書く時点で同じ承認でなくなっていれば何も書かない。"""
    held: list[_Unbound] = []

    def build(now: run.View) -> list[model.EventDraft]:
        held.clear()
        got = _unbound_approval(now)
        if got is None:
            return []
        held.append(got)
        name, step = got.stage.name, got.step
        reset = [_no_report_draft(now, name, step.id, stages.RESET, got.generation, UNBOUND_REASON)] if got.consumed else []
        wait = _no_report_draft(now, name, step.id, "waiting", got.generation, UNBOUND_REASON)
        return [*reset, wait, *_reading_reset(now, name, step.gate), *_undropped(now, name, got.answered)]

    def after(conn: Any) -> None:
        if held:
            _uncarry(conn, machine_id, run.event_payload(held[0].answered).get("carried_row"), UNBOUND_REASON)

    with _driving(st, machine_id, locked), store.approval_lock(st, machine_id):
        v = run.read(st, machine_id)
        found = _unbound_approval(v)
        if found is not None:
            revoke(approval_decide.stage_revocations(v, found.stage.name))
        run.append(st, machine_id, build, after)
    return UNBOUND_REASON if held else None


def _no_report_draft(v: run.View, stage: str, step: str, status: str, generation: int, reason: str) -> model.EventDraft:
    """報告を持たないステップ記録の事象の下書き（待ちへの書き直しと、未了への戻し）。"""
    payload = {"step": step, "status": status, "report": None, "generation": generation, "reason": reason}
    return run.step_event(v, stage, payload)


def _reading_reset(v: run.View, stage: str, gate: Optional[str]) -> list[model.EventDraft]:
    """同じ工程の、承認ゲート `gate` の前の読み手役のステップを未了に戻す事象（#4446）。待ちへ戻すときと同じトランザクションで書く。

    戻すと工程を進める仕組みが読み手役のステップを打ち直し、今の PR の題・本文・head で記録を作り直してから承認ゲートの待ちに入る。
    読み手役のステップの無い古い定義と、まだ済んでいない読み手役のステップでは何も書かない。"""
    prog = run.step_progress(v, stage)
    reading = v.snap.stage(stage).reading_for(gate)
    if reading is None or reading.id not in prog.done:
        return []
    why = "承認ゲートの待ちへ戻したので、読み手役のステップから今の対象で打ち直す"
    return [_no_report_draft(v, stage, reading.id, stages.RESET, prog.generation, why)]


# 承認の前の確認をやり直して待ちを出し直す理由（承認ゲートの待ちの記録に残る）
REREAD_REASON = "ndf progress reread: 承認の前の確認をやり直し、承認ゲートの待ちを今の対象で出し直す"


def reread(st: store.Store, machine_id: str, cwd: Path, env: run.Env) -> dict[str, Any]:
    """`ndf progress reread`: 承認ゲートの待ちを新しい質問の id で書き直し、読み手役のステップを打ち直す（#4446 の待ちの出し直し）。

    待ちの書き直しと読み手役のステップを未了に戻す事象は、`recheck` と同じく 1 回のトランザクションで、読んだ時点の待ちが
    今も同じときだけ書く（その間に回答が書かれていれば何も書かない）。読み手役のステップの無い古い定義では
    `ndf document-rebuild gate` を直接打つ（`_reread_direct`）。結果の待ちの知らせは、呼び手（`entry`）が今の対象で作る。
    """
    v = run.read(st, machine_id)
    m = run.decide(v)
    if m.action != run.Action.GATE or m.stage is None or m.step is None or m.progress is None or not invoke.is_listen(m.step):
        return result.stopped(TOOL, "precondition", f"ステートマシン {machine_id} の承認ゲートは人の回答を待っていない")
    if m.progress.held_finish is not None:
        detail = (
            f"工程 {m.stage.name} は手で終えて承認を待っていて、承認の前の確認はやり直せない。直すなら "
            f"ndf progress retry {machine_id} {m.stage.name} --reason <理由> --later reopen を打つ"
        )
        return result.stopped(TOOL, "precondition", detail)
    stage, step, anchor = m.stage, m.step, m.progress.anchor

    def build(now: run.View) -> list[model.EventDraft]:
        n = run.decide(now)
        same = n.action == run.Action.GATE and n.step is not None and n.step.id == step.id and n.progress is not None
        if not same or n.progress is None or n.progress.anchor != anchor:
            return []
        wait = _no_report_draft(now, stage.name, step.id, "waiting", n.progress.generation, REREAD_REASON)
        return [wait, *_reading_reset(now, stage.name, step.gate)]

    # 回答を読む操作と回答（`approval_lock`）を止めてから、待ちが同じと確かめて取り消し、書き直す
    with _driving(st, machine_id), store.approval_lock(st, machine_id):
        latest = run.read(st, machine_id)
        if build(latest):
            revoked = waiting_revocations(st.root, latest, stage.name)
            env.revoke(revoked)
            forget_posted(st.root, machine_id, revoked_records(revoked))
        written = run.append(st, machine_id, build)
    if not written:
        summary = "確認をやり直す前に回答が書かれた。書かれた回答は ndf progress next で読む"
        return result.result(
            TOOL, result.OK, summary, metrics={"machine_id": machine_id}, next=result.command("progress", "next", machine_id)
        )
    if stage.reading_for(step.gate) is None:
        return _reread_direct(st, machine_id, cwd, env)
    return _step_once(st, machine_id, cwd, env)


def _reread_direct(st: store.Store, machine_id: str, cwd: Path, env: run.Env) -> dict[str, Any]:
    """読み手役のステップの無い古い定義のスプリントで、`ndf document-rebuild gate` を直接打つ（#4446 の計画の (7)）。

    保存先・結果ファイルと工程の記録から作る引数は、読み手役のステップと同じ `run._reading_run` で足す。
    `outputs.review_again` が返ったら、同じ工程の
    設計のレビューと MVV 判定を未了に戻して流し直す（`run.review_again_drafts`）。"""
    v = run.read(st, machine_id)
    m = run.decide(v)
    assert m.stage is not None and m.step is not None and m.progress is not None
    q = question(v, m)
    work_dir = run.work_root(st.root, machine_id) / m.stage.id / f"reading-{q.id}"
    work_dir.mkdir(parents=True, exist_ok=True)
    pr = ["--pr", str(q.pr)] if q.pr is not None else []
    argv = [invoke.PACKAGE, "document-rebuild", "gate", "--gate", str(m.step.gate), *pr]
    try:
        inputs = invoke.resolve_inputs(m.stage, v.context())
    except invoke.StageInputError as exc:
        return result.stopped(TOOL, exc.kind, str(exc), items=exc.items)
    call = invoke.StageRun(invoke.SCRIPT, invoke.PACKAGE, inputs, m.stage.outputs, "none", "reading", {"argv": argv})
    call = run._reading_run(v, m.stage, m.step, call, work_dir)
    res = env.invoker(call, cwd, work_dir, None)
    if res.status != result.OK:
        return res.report
    stage, gate = m.stage, m.step.gate
    run.append(st, machine_id, lambda now: run.review_again_drafts(now, stage, gate, res.report))
    return _step_once(st, machine_id, cwd, env) if stages.review_again_of(res.report) is None else run.advance(st, machine_id, cwd, env)


def _step_once(st: store.Store, machine_id: str, cwd: Path, env: run.Env) -> dict[str, Any]:
    """排他を取り、次の 1 手が実行中の行のステップ（読み手役）なら同じプロセスで 1 つだけ流す。承認ゲート・失敗・終わりなら
    その結果を返し、続き（設計のレビューへ戻したときなど）があれば作業を起動して `wait` を返す（#4446）。"""
    if run.is_live(st, machine_id):
        summary = f"ステートマシン {machine_id} を流す作業が動いている。終わりは wait で待つ"
        return result.result(
            TOOL, result.OK, summary, metrics={"machine_id": machine_id}, next=result.command("progress", "wait", machine_id)
        )
    with store.run_lock(st, machine_id):
        d = run.Drive(st, machine_id, cwd, env, inline=True)
        v = run.read(st, machine_id)
        m = run.decide(v)
        if m.action == run.Action.RESUME and m.stage is not None and m.step is not None:
            d.write(*run._run_step(d, v, m.stage, m.step))
            d.synced()
        v = run.read(st, machine_id)
        m = run.decide(v)
        if m.action in (run.Action.GATE, run.Action.FAILED, run.Action.DONE):
            return command.with_warnings(run.report(v, m), before=d.warnings)
    return run.advance(st, machine_id, cwd, env)


def _judged_elsewhere(p: Mapping[str, Any], q: Question, seen: Target, now: Target) -> Optional[str]:
    """MVV 判定で承認ゲートを省いた回答（`service:mvv`）が、今の質問の PR と head の両方を判定したものでなければ違いの文。

    同じコミットを指す別の PR へ承認の対象が変わると head だけでは見分けられないため、判定した PR の番号も照らす（#3959）。
    PR か head のどちらかを読めなければ、照らせないので違うと扱う（人へ改めて質問する）。"""
    held = p.get(model.MVV_KEY)
    judged = held.get("pr") if isinstance(held, Mapping) else None
    if q.pr is None or isinstance(judged, bool) or judged != q.pr:
        return f"MVV 判定した PR（判定した PR {judged}・今の質問の PR {q.pr}）"
    if seen.head is None or now.head is None or seen.head != now.head:
        return f"head の SHA（判定した値 {seen.head}・今の値 {now.head}）"
    return None


def _undropped(v: run.View, stage: str, answered: model.Event) -> list[model.EventDraft]:
    """待ちへ戻した回答が外した課題を、今のスプリントの文脈の `issues` へ戻す事象（回答の前の値。`_dropped` の逆）。"""
    if not run.event_payload(answered).get("dropped"):
        return []
    earlier = [ev for ev in v.events if ev.seq < answered.seq]
    before = stages.compose_context(v.doc["context"], stages.recorded_outputs(earlier, v.rev))
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
