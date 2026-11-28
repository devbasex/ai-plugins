"""承認ゲートへの回答（`ndf answer`）の記録と、回答する対象の読み取り（`ndf.progress.gate` の部品）。

待っていない行（実行の前）・違う質問の id・今の対象と違う見た対象は断り、行は待ちのまま残る（3364-2）。同じ冪等のキーの
回答し直しは書かずに同じ結果を返す。設計承認ゲートの承認で外した課題は、同じトランザクションの中でレーンの順番表の次の行へ
入れる（#3097 の決定 99）。回答を読む前の確かめと、承認ゲートの待ちへの戻しは `rechecking`。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any, Optional, Protocol

from ndf.adapters.git import Git
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
    presented_id,
    read_presented,
    release_request_of,
    waiting,
)
from ndf.progress.gate.records import record_answer, rejections_path
from ndf.progress.gate.targets import TargetReader
from ndf.progress.run_view import APPROVAL_POSTING, closes_only

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


class Post(Protocol):
    """マージの承認の証跡を書く口（行を渡し、書いたら真、重なる証跡が既にあれば偽）。`resend` は DB に記録済みの回答の送り直し。"""

    def __call__(self, row: merge_approval.MergeRow, *, resend: bool = False) -> bool: ...


def no_post(row: merge_approval.MergeRow, *, resend: bool = False) -> bool:
    """証跡を書かない口（PR を読まないテストと、承認の対象を持たない質問）。"""
    return False


def github_post(cwd: Path, hub: Optional[Callable[[], GitHub]] = None) -> Post:
    """`merge_approval.write_merge_row` を token の利用者（アカウント ID）で呼ぶ口。GitHub は 1 つだけ作る。

    `observe` と同じ GitHub を渡せば、設定の読み取りで読んだ利用者を使い、利用者の読み取りを増やさない。
    書けない（利用者のアカウント ID を読めない・値が不正）なら `model.Refused`（回答は記録しない）。"""
    get_hub = shared_hub(cwd, hub)

    def post(row: merge_approval.MergeRow, *, resend: bool = False) -> bool:
        made = get_hub()
        try:
            return merge_approval.write_merge_row(made, row, made.viewer(), latest_only=not resend)
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


def _post_trail(q: Question, a: Answer, seen: Target, by: str, post: Post, *, resend: bool = False) -> None:
    """照らした承認の対象と提示ごとの識別子（`presented_id`）で証跡の行を作り、`post` で書く。

    同じ提示への送り直しだけが重なりとして書かれない。資料を出し直した後の回答は、出し直す前の提示の証跡があっても書く。"""
    target = seen.binding()
    if target is None or a.value not in _TRAIL_ANSWERS:
        return
    record = presented_id(replace(q, target=seen))
    row = merge_approval.MergeRow(_TRAIL_ANSWERS[a.value], by, target, seen.production is True, record)
    if resend:
        post(row, resend=True)
    else:
        post(row)


def _note_posting(conn: Any, v: run.View, q: Question, a: Answer, seen: Target) -> Optional[int]:
    """証跡を投稿する回答は、投稿する前に事象へ記録する。本番へ反映される承認は取り消す対象
    （`approval_decide.stage_revocations`）を、却下と変更要求は先の承認を数えないこと（`approval_decide.counted_approval`）を
    残す。投稿の後の確定に失敗しても残る。追記した事象の番号を返し、ほかの回答は何も書かずに None。呼び手は証跡を投稿する
    前にトランザクションを確定する。"""
    target = seen.binding()
    if target is None or a.value not in _TRAIL_ANSWERS:
        return None
    record = presented_id(replace(q, target=seen))
    if _TRAIL_ANSWERS[a.value] == approval_words.REJECTED:
        payload: dict[str, Any] = {"rejecting": q.gate, "record": record}
    elif seen.production:
        payload = {"target": list(target), "record": record}
    else:
        return None
    draft = model.EventDraft(APPROVAL_POSTING, run.CONDUCTOR, q.stage, payload=payload, rev=v.rev)
    return store.append_events(conn, v.machine_id, [draft], v.events[-1].seq)[-1]


@dataclass(frozen=True)
class _Checked:
    """確かめを通った回答。`closes` は手で終えたデプロイの工程を閉じるだけの確認か。"""

    q: Question
    a: Answer
    seen: Target
    text: Optional[str]
    closes: bool


def _check_answer(conn: Any, v: run.View, a: Answer, observe: Observe, root: Path, text: Optional[str]) -> tuple[_Checked, Optional[str]]:
    """回答を受けられるかを確かめ、確かめを通った回答と証跡の担い手（証跡を書かないなら None）を返す。"""
    q, seen = _refusal(v, a, observe, root)
    # 以後の質問は照らした対象（提示した承認の対象）を持つ。リリースのリクエストと証跡の識別子を提示ごとに分ける
    q = replace(q, target=seen)
    by_hand = run.step_progress(v, q.stage).held_finish is not None
    if by_hand:
        _require_person(conn, q, a)
    # 手で終えたデプロイの回答待ちへの回答は、工程を閉じる確認で、マージの事前の承認ではない
    closes = by_hand and q.gate == RELEASE_GATE
    by = _trail_by(conn, q, a, seen)
    if closes and a.value == run.APPROVED:
        by = None
    return _Checked(q, a, seen, text, closes), by


def _commit_answer(conn: Any, v: run.View, got: _Checked, last_seq: int) -> tuple[list[int], Optional[str]]:
    """回答の事象と、それが運ぶ事象を追記し、番号と外した課題を入れた行を返す。`last_seq` の後に事象が増えていれば
    `store.Conflict`。"""
    q, a = got.q, got.a
    carried = _carry(conn, v.machine_id, a.drop, a.reason)
    rejected = _reject_release(conn, q, a)
    extra = {
        "gate_id": q.id,
        "gate": q.gate,
        "by": a.by,
        "key": a.key,
        "seen": got.seen.as_dict(),
        "dropped": list(a.drop),
        "carried_row": carried,
        "release_request": rejected,
        **({"closes_stage": True} if got.closes else {}),
    }
    draft = run.answer_draft(v, q.stage, a.value, a.reason, got.text, extra)
    drafts = [draft, *_outputs(v, q, a), *_dropped(v, q, a.drop)]
    return store.append_events(conn, v.machine_id, drafts, last_seq), carried


def _begin_answer(
    conn: Any, v: run.View, a: Answer, observe: Observe, root: Path, text: Optional[str]
) -> tuple[_Checked, Optional[str], int, Optional[tuple[list[int], Optional[str]]]]:
    """回答の 1 つ目のトランザクション。確かめを通った回答、証跡の担い手、回答を書くときに期待する事象の番号、書いた結果を返す。

    証跡を投稿しない回答（担い手が None）はここで書く。投稿する回答は書かず（結果は None）、投稿前の記録だけを追記する
    （`_note_posting`）。"""
    got, by = _check_answer(conn, v, a, observe, root, text)
    last = v.events[-1].seq if v.events else 0
    if by is None:
        return got, by, last, _commit_answer(conn, v, got, last)
    noted = _note_posting(conn, v, got.q, a, got.seen)
    return got, by, last if noted is None else noted, None


def _require_person(conn: Any, q: Question, a: Answer) -> None:
    """手で終えた工程の回答待ちの承認は工程を終了にするので、本番へ反映されないマージでも人の回答に限る（`model.Refused`）。

    `service:mvv` はここで断らない（判定の写しの無い回答は、読む側の `model.mvv_answer_void` が数えない）。"""
    if a.value != run.APPROVED or a.by == MVV_BY or a.by.split(":", 1)[0] == actors.HUMAN:
        return
    if a.by.split(":", 1)[0] == "organizer" and _relayed_by_organizer(conn, q, a):
        return
    raise model.Refused(
        f"手で終えた工程 {q.stage} の承認は、回答した者 {a.by} では確定しない（利用者が --by human で回答する。"
        "オーガナイザーはリクエストに記録された利用者の回答だけを中継する）"
    )


def answer(st: store.Store, machine_id: str, a: Answer, observe: Observe = no_observe, post: Post = no_post) -> dict[str, Any]:
    """回答を事象 `answered` に残す（外した課題の行と、今のスプリントの課題から外す事象も同じトランザクション）。
    同じ冪等のキーは書かずに同じ結果を返す。

    回答の確かめから DB の確定までは、開き直しと共有する排他（`store.approval_lock`）の中で行う。証跡の投稿は DB の
    トランザクションの外で行う（SQLite は書くトランザクションの間、保存先全体の書き込みを止める）。

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
                replayed = _same(held, a, machine_id)
                send = _ensure_trail(conn, v, held, a, post)
            else:
                got, by, last, done = _begin_answer(conn, v, a, observe, st.root, text)
        if held is not None:
            send()
            return replayed
        if by is not None:
            # 投稿前の記録を確定してから投稿する（投稿の後に止まっても、DB から取り消す承認を指せる）
            _post_trail(got.q, a, got.seen, by, post)
            with store.transaction(st) as conn:
                done = _commit_answer(conn, v, got, last)
    assert done is not None  # 証跡を投稿しない回答は 1 つ目のトランザクションで、投稿する回答は投稿の後に書いた
    (seqs, carried), q = done, got.q
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


def _no_trail() -> None:
    """書く証跡が無い送り直し。"""


def _ensure_trail(conn: Any, v: run.View, held: model.Event, a: Answer, post: Post) -> Callable[[], None]:
    """記録済みの回答の証跡が無ければ書く関数を返す（証跡の後・記録の前でなく、記録の後に止まった送り直し）。呼び手は
    返した関数をトランザクションの外で打つ。

    担い手は記録した回答した者（事象の `by`）から決める。送り直しの `a.by` は `_same` が記録と同じことを確かめている。
    承認の境界（`approval_decide.approval_boundary`）より前の回答には書かない（取り消した承認を、送り直しで PR へ戻さない）。
    同じ承認ゲートへの却下か変更要求の投稿前の記録より古い承認にも書かない（別のアカウントの証跡として承認が戻らない）。
    工程を閉じるだけの承認（`closes_only`）にも書かない。"""
    p = run.event_payload(held)
    rows = [ev for ev in v.events if ev.draft.row_key == held.draft.row_key]
    if held.seq < approval_decide.approval_boundary(rows, str(p.get("step"))):
        return _no_trail
    if closes_only(p) and p.get("answer") == run.APPROVED:
        return _no_trail
    seen = Target.from_dict(p.get("seen"))
    gate = p.get("gate")
    if p.get("answer") == run.APPROVED and approval_decide.rejection_posted_after(rows, gate, held.seq):
        return _no_trail
    if seen.binding() is None or not isinstance(p.get("gate_id"), str):
        return _no_trail
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
    return _no_trail if by is None else partial(_post_trail, q, a, seen, by, post, resend=True)


def _same(held: model.Event, a: Answer, machine_id: str) -> dict[str, Any]:
    """同じ冪等のキーの送り直しが記録と同じ回答かを確かめ、結果を返す（違えば `model.Refused`）。"""
    p = run.event_payload(held)
    if p.get("answer") != a.value or p.get("gate_id") != a.gate_id:
        raise model.Refused(f"冪等のキー {a.key} は別の回答（{p.get('answer')}・質問 {p.get('gate_id')}）に使った")
    # 送り直しで回答した者を変えない（記録は自動の処理の回答なのに、送り直しの申告で利用者の証跡を書かない）
    if p.get("by") != a.by:
        raise model.Refused(f"冪等のキー {a.key} の回答は {p.get('by')} が書いた。送り直しの回答した者 {a.by} と違う")
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
