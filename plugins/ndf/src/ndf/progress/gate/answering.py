"""承認ゲートへの回答（`ndf answer`）と、回答を読む前の今の対象との検証（`ndf.progress.gate` の部品）。

待っていない行（実行の前）・違う質問の id・今の対象と違う見た対象は断り、行は待ちのまま残る（3364-2）。同じ冪等のキーの
回答し直しは書かずに同じ結果を返す。設計承認ゲートの承認で外した課題は、同じトランザクションの中でレーンの順番表の次の行へ
入れる（#3097 の決定 99）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional, Protocol

from ndf.adapters.git import Git, GitError
from ndf.adapters.github import GitHub, GitHubError, client
from ndf.contracts import result
from ndf.core import actors, approval_words, merge_approval, production_merge
from ndf.progress import command, invoke, lane, model, plan, run, stages, store
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
    question,
    read_presented,
    release_request_of,
    waiting,
)
from ndf.progress.gate.records import record_answer, rejections_path
from ndf.progress.run_view import write_accepted_head

# 回答した者の形（人・conductor・サービス。サービスは `service:<主体>`。プランの承認待ちと共有する）
ACTORS = actors.ACTORS

# --- 回答 ---------------------------------------------------------------------------------------


class Observe(Protocol):
    """今の対象を読む口（質問を渡し、PR の head と承認資料の sha256 を返す）。`material` が偽なら head だけを読む。"""

    def __call__(self, q: Question, *, material: bool = True) -> Target: ...


def no_observe(q: Question, *, material: bool = True) -> Target:
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


def github_observe(cwd: Path, root: Path, hub: Optional[Callable[[], GitHub]] = None) -> Observe:
    """今の対象を読む: PR の head の SHA（GitHub）と、その head の承認資料の sha256。読めなければ回答を受けない（`model.Refused`）。

    資料は今の head を入れた質問の `material_path` で探す（資料は head ごとに別のファイル。#3959）。"""
    get_hub = shared_hub(cwd, hub)

    def observe(q: Question, *, material: bool = True) -> Target:
        pull = found = None
        if q.pr is not None:
            try:
                hub = get_hub()
                pull = hub.pull(q.pr)
                # head だけを読むときは、マージ先の設定を読まない
                found = hub.branch_file(pull.base_ref, production_merge.PROJECT_FILE) if material else None
            except (GitHubError, GitError) as exc:
                raise model.Refused(f"PR #{q.pr} の head を読めない（マージ先も読めない）ので、回答と対象を照らせない: {exc}") from None
        head = pull.head_sha if pull is not None else None
        if not material:
            return Target(head, None)
        path = material_path(root, replace(q, target=Target(head)))
        sha = material_sha256(path) if path.exists() else None
        if pull is None or found is None:
            return Target(head, sha)
        verdict = production_merge.judge_project_text(found.text, pull.base_ref, source_repo=found.repo, repo=hub.repo)
        production = production_merge.reaches_production(verdict)
        return Target(head, sha, hub.repo, pull.number, pull.base_ref, production)

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
    return q, replace(shown, material=now.material if now.material is not None else a.seen.material)


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

    承認の対象がそろった（`seen.binding()`）PR を持つ承認ゲートでだけ書く。利用者（`human`）と、利用者の回答を中継したと
    確かめられた `organizer` は `user`。`service:mvv` は書かない。ほかの回答した者（`conductor`・ほかの `service:<主体>`）は、
    承認でマージが本番へ反映され得るなら断り（AI の判断で担い手 `user` の証跡を作らない）、反映されないなら書かない。
    """
    if seen.binding() is None or q.gate not in (DESIGN_GATE, RELEASE_GATE) or a.by == MVV_BY:
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


def _post_trail(q: Question, a: Answer, seen: Target, by: str, post: Post) -> None:
    """照らした承認の対象と提示ごとの識別子（`presented_id`）で証跡の行を作り、`post` で書く。

    同じ提示への送り直しだけが重なりとして書かれない。資料を出し直した後の回答は、出し直す前の提示の証跡があっても書く。"""
    target = seen.binding()
    if target is None or a.value not in _TRAIL_ANSWERS:
        return
    record = presented_id(replace(q, target=seen))
    post(merge_approval.MergeRow(_TRAIL_ANSWERS[a.value], by, target, seen.production is True, record))


def answer(st: store.Store, machine_id: str, a: Answer, observe: Observe = no_observe, post: Post = no_post) -> dict[str, Any]:
    """回答を事象 `answered` に残す（外した課題の行と、今のスプリントの課題から外す事象も同じトランザクション）。
    同じ冪等のキーは書かずに同じ結果を返す。

    承認の対象を持つ質問への承認・却下は、記録の前にマージの承認の証跡を PR へ書く（`_trail_by`・`_post_trail`。#4409 の
    決定 7）。証跡を書けなければ回答を記録せず、同じ回答を送り直すと続きから回復する。同じ冪等のキーの回答が既に
    記録されていても、証跡が無ければ書いてから戻る。"""
    if not ACTORS.match(a.by):
        raise model.Refused(actors.invalid_message(a.by))
    text = run.notes_text(a.value, a.notes)
    with store.transaction(st) as conn:
        v = run.view(conn, machine_id)
        held = _answered_with_key(v, a.key)
        if held is not None:
            return _same(held, a, machine_id, lambda: _ensure_trail(conn, v, held, a, post))
        q, seen = _refusal(v, a, observe, st.root)
        # 以後の質問は照らした対象（提示した承認の対象）を持つ。リリースのリクエストと証跡の識別子を提示ごとに分ける
        q = replace(q, target=seen)
        by = _trail_by(conn, q, a, seen)
        if by is not None:
            _post_trail(q, a, seen, by, post)
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


def _ensure_trail(conn: Any, v: run.View, held: model.Event, a: Answer, post: Post) -> None:
    """記録済みの回答の証跡が無ければ書く（証跡の後・記録の前でなく、記録の後に止まった送り直し）。

    担い手は記録した回答した者（事象の `by`）から決める。送り直しの `a.by` は `_same` が記録と同じことを確かめている。"""
    p = run.event_payload(held)
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
        _post_trail(q, a, seen, by, post)


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
    p = run.event_payload(m.event)
    if "gate_id" not in p:
        return None
    q = question(v, replace(m, action=run.Action.GATE))
    # head を先に照らし、資料はその結論に要る 1 つだけを読む（#3959）
    seen, now = Target.from_dict(p.get("seen")), observe(q, material=False)
    if p.get("by") == model.MVV_SERVICE:
        changed = _judged_elsewhere(p, q, seen, now)
        if changed is None:
            return None
    elif seen.head is None or now.head is None or seen.head == now.head:
        changed = _material_moved(st.root, q, seen)
        if changed is None:
            return None
    elif not seen.production and _docs_only_after(st.root, q, seen, now, docs_only):
        _accept(st.root, q, now)
        return f"承認の後に加わった範囲（{seen.head} → {now.head}）は文書だけなので、リリース承認ゲートを出し直さずに新しい範囲で進める"
    else:
        changed = f"head の SHA（見た値 {seen.head}・今の値 {now.head}）"
    why = "回答した後に対象が変わった: " + changed
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
