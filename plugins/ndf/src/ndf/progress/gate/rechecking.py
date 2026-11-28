"""回答を読む前の今の対象との確かめと、承認ゲートの待ちへの戻し・出し直し（`ndf.progress.gate` の部品）。

回答の記録（`answering`）の後、工程を進める仕組みが回答を読むまでの間に対象が変われば、回答を読まずに待ちへ戻す
（3364-2）。対象に結び付かない設計の承認の戻し（#4495）と、`ndf progress reread` の出し直し（#4446）も同じ書き方をする。
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional

from ndf.adapters.git import Git, GitError
from ndf.contracts import result
from ndf.core import merge_approval
from ndf.progress import approval_decide, command, invoke, lane, model, run, stages, store
from ndf.progress.gate.answering import Observe
from ndf.progress.gate.asking import (
    DESIGN_GATE,
    RELEASE_GATE,
    TOOL,
    Question,
    Target,
    material_path,
    material_sha256,
    question,
)
from ndf.progress.gate.targets import TargetReader
from ndf.progress.run_view import read_accepted_head, write_accepted_head

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


def no_revoke(revocations: list[merge_approval.Revocation]) -> None:
    """承認の取り消しを書かない口（PR を読まないテスト）。"""


@dataclass(frozen=True)
class Recheck:
    """`recheck` の打ち方。`advancing` が偽なら何も取り消さず書かない。照らした回答の事象の番号は `checked` に足す。
    `locked` は呼び手が作業の排他を持つか。"""

    advancing: bool = True
    checked: Optional[set[int]] = None
    locked: bool = False
    view: Optional[run.View] = None  # 呼び手が作業の排他の中で読んだ記録。無ければ読む


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
    v = how.view if how.view is not None else run.read(st, machine_id)
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
    # 手で終えた工程はここで待ちへ戻り `consume_held` に届かないので、マージ済みの警告をここでも足す
    if m.progress.held_finish is not None and now.merged:
        why += approval_decide.merged_otherwise(now.head)
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
) -> Callable[[run.View, Optional[int]], Optional[str]]:
    """`run.Env.verify`: 工程を進める操作の `recheck` を、作業の排他の中で打つ。排他を待つ間や、回答が作業の排他を取らずに
    書かれる間に対象が変わりうるためである。照らす前に読んだ対象を捨て、ここで読んだ結果を後の判定が使う。同じ回答は
    照らし直さない。

    `take`（リリースのリクエストへの回答を承認ゲートの回答として書く）は初回に 1 度だけ、照らす前に打つ。`take` が読んだ対象を
    `recheck` も使い、`take` の知らせと `recheck` の理由をまとめて返す。

    `recheck` は呼び手が読んだ記録を使う。`take` が何かを知らせたときは、事象が増えうるので読み直す。"""
    checked: set[int] = set()
    pending = [take]

    def verify(v: run.View, seq: Optional[int]) -> Optional[str]:
        if seq in checked:
            return None
        forget()
        took = pending.pop()() if pending else None
        how = Recheck(checked=checked, locked=True, view=v if took is None else None)
        why = recheck(st, v.machine_id, observe, docs_only, revoke, how)
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
            env.revoke(approval_decide.stage_revocations(latest, stage.name))
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
