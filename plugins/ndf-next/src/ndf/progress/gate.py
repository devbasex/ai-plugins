"""承認ゲート（#3295）: 問いの区分・答えごとの行き先・答えの証拠・MVV 判定の前の機械の検査。

承認ゲートは工程のステップ（`ndf.listen`）で、`ndf progress next` が待ちに入った行を `gate` で返す（#3092 の決定 7）。
このモジュールはその問いと答えの形を持つ。

- 問い（`Question`）: 承認ゲートの区分（設計・リリース。表 `workflow/progress.json` の `gates`）・進行表と工程・
  問いの記録の id・見る対象（PR の URL と head の SHA・承認資料の sha256）。id は待ちに入った事象の番号から作り、
  差し戻しで流し直した後の問いは別の id になる
- 答えごとの行き先（`routes`）: 答えの語ごとに `ndf answer` のそのまま打てるコマンドを持つ。承認ゲートで止まった結果
  JSON は区分・PR の URL・問いの id・行き先を機械で読める形で返す（3390-2）
- 答え（`answer`。`ndf answer`）: 対象の進行表と問いの id・結果・差し戻しの指摘・答えた者・冪等の鍵・見た対象を受け、
  事象 `answered` に残す。待っていない行（実行の前）・違う問いの id・今の対象と違う見た対象は断り、行は待ちのまま残る（3364-2）。
  同じ冪等の鍵の答え直しは書かずに同じ結果を返す
- 外す: 設計承認ゲートの承認で外した課題（`--drop`）は、同じトランザクションの中でレーンの順番表の次の行へ入れる
  （`lane.carry_issues`。#3097 の条件 3 の後半・決定 99）
- スプリントの完了: `lane_env` の `Env` で流すと、完了と同じトランザクションでレーンの行を終える（`lane.end_row`。決定 17）
- 差し戻しの記録（`rejections`）: 承認しない・差し戻しの答えを承認ゲートの区分ごとに数える。正本は事象 `answered` で、
  サービスが読む `gate-rejections.jsonl`（表 ndf-files）は答えを書いた後にそこから写す（`record_answer`。#3136）
- MVV 判定の前の機械の検査（`refusals`）: 承認済み MVV のハッシュ・承認の記録・モード・レッドラインのパス・MVV の宣言を
  変えない、の 5 つ。1 つでも外れれば LLM の判定を読まずに承認ゲートで人へ戻す。判定が「従う」でも、レッドライン
  （判定の `boundary` か機械の検査の当たり）があれば省かない（`may_skip`。#3017）

`ndf answer` の使い方:

    ndf answer <進行表> --gate <問いの id> --result <答え> --reason <理由> --by <答えた者> --key <冪等の鍵>
               [--notes <指摘ファイル>] [--head <見た head の SHA>] [--material-sha256 <見た承認資料の sha256>]
               [--drop <課題の番号>...]
    ndf answer ask [--gate design|release]   # プランの中の承認ゲート（雛形の {{gate}}）。答えを待つ結果 JSON を返す
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

import pathspec

from ndf import cli, identity
from ndf.adapters.git import Git, GitError
from ndf.adapters.github import GitHub, GitHubError, client
from ndf.contracts import facts, result
from ndf.progress import command, invoke, lane, model, run, store

TOOL = cli.tool_of(__name__)
# 答えた者の形（人・conductor・サービス。サービスは `service:<主体>`）
ACTORS = re.compile(r"^(human|conductor|organizer|service):[A-Za-z0-9._@/-]+$|^(human|conductor|organizer)$")
# 承認ゲートを省かないモード（MVV 判定の前の検査 3）
EXCLUDED_MODES = ("operation", "documentation")
# 利用者の MVV の宣言のファイルの名前（拡張子を除く。表 ndf-files の宣言の行から引く。MVV 判定の前の検査 5。共通原則の C7）
MVV_STEM = "mvv"
# MVV 判定の語（判定の応答の verdict）
FOLLOW, NOT_FOLLOW, UNKNOWN = "follow", "not_follow", "unknown"
VERDICTS = (FOLLOW, NOT_FOLLOW, UNKNOWN)
# 問いの id の長さ（sha256 の頭）
ID_LENGTH = 16
# 承認しなかった答え（差し戻しの記録に数える）
REJECTING = (run.REVISE, "rejected")
# 承認資料の置き場（進行表ごとの作業の置き場の下。名前は問いの id）
MATERIAL_DIR = "materials"


# --- 問い --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """見る対象。`head` は PR の head の SHA、`material` は承認資料の sha256。分からなければ None。"""

    head: Optional[str] = None
    material: Optional[str] = None

    def differs(self, seen: Target) -> list[str]:
        """答えた者が見た対象 `seen` と今の対象の違い（今の側が分かる項目だけを比べる）。"""
        out = []
        for name, now, then in (("head の SHA", self.head, seen.head), ("承認資料の sha256", self.material, seen.material)):
            if now is not None and then != now:
                out.append(f"{name}（見た値 {then or '無し'}・今の値 {now}）")
        return out


@dataclass(frozen=True)
class Question:
    """待っている承認ゲートの問い。`gate` は区分（design・release）で、プランの中の承認ゲートは None。"""

    id: str
    table_id: str
    stage: str
    step: str
    gate: Optional[str]
    choices: tuple[str, ...]
    pr: Optional[int] = None
    pr_url: Optional[str] = None
    target: Target = field(default_factory=Target)

    @property
    def label(self) -> str:
        return model.gate_label(self.gate) if self.gate else "プランの中の承認ゲート"


def question_id(table_id: str, rev: int, stage: str, step: str, anchor: int) -> str:
    """問いの記録の id。待ちに入った事象の番号（`anchor`）を含むため、差し戻しで流し直した問いは別の id になる。"""
    text = f"{table_id}\0{rev}\0{stage}\0{step}\0{anchor}"
    return hashlib.sha256(text.encode()).hexdigest()[:ID_LENGTH]


def pr_url(repo: Optional[str], number: Optional[int]) -> Optional[str]:
    return f"https://github.com/{repo}/pull/{number}" if repo and number else None


def question(v: run.View, m: run.Move, *, repo: Optional[str] = None, target: Target = Target()) -> Question:
    """待っている手 `m`（`run.Action.GATE`）の問い。PR は設計承認ゲートなら設計 PR、ほかはスプリントの PR。"""
    if m.action != run.Action.GATE or m.stage is None or m.step is None or m.progress is None:
        raise model.Refused("答えを待つ工程が無い")
    ctx = v.context()
    key = "design_pr" if m.step.gate == "design" else "pr"
    number = ctx[key] if isinstance(ctx.get(key), int) else None
    qid = question_id(v.table_id, v.rev, m.stage.name, m.step.id, m.progress.anchor)
    return Question(qid, v.table_id, m.stage.name, m.step.id, m.step.gate, tuple(run.choices(m)), number, pr_url(repo, number), target)


def waiting(v: run.View, **kw: Any) -> Optional[Question]:
    """進行表が待っている問い。待っていなければ None。"""
    m = run.decide(v)
    return question(v, m, **kw) if m.action == run.Action.GATE else None


# --- 答えごとの行き先 -----------------------------------------------------------------------------


def _asks(q: Question, answer: str) -> tuple[str, ...]:
    """答えを打つ側が埋める鍵。差し戻しは指摘ファイル、設計承認ゲートの承認は外す課題（無ければ省く）も持つ。"""
    asks = ["reason", "by", "key"]
    if answer == run.REVISE:
        asks.insert(0, "notes")
    if answer == run.APPROVED and q.gate == "design":
        asks.append("drop")
    return tuple(asks)


def routes(q: Question) -> dict[str, Any]:
    """答えの語 → `ndf answer` のコマンド。見た対象（head・承認資料）を引数に固定し、答えた後に対象が変われば断られる。"""
    seen: list[str] = []
    if q.target.head:
        seen += ["--head", q.target.head]
    if q.target.material:
        seen += ["--material-sha256", q.target.material]
    return {a: result.command(TOOL, q.table_id, "--gate", q.id, "--result", a, *seen, ask=_asks(q, a)) for a in q.choices}


def describe(q: Question) -> dict[str, Any]:
    """結果 JSON の `metrics.gate` に載せる問い（機械で読む形）。"""
    return {
        "id": q.id,
        "kind": q.gate,
        "label": q.label,
        "table_id": q.table_id,
        "stage": q.stage,
        "step": q.step,
        "pr": q.pr,
        "pr_url": q.pr_url,
        "head": q.target.head,
        "material_sha256": q.target.material,
        "choices": list(q.choices),
    }


def gate_result(q: Question, metrics: Optional[Mapping[str, Any]] = None, presentation: Optional[Path] = None) -> dict[str, Any]:
    """承認ゲートで止まった結果 JSON（status gate・問い・答えごとの行き先）。"""
    where = f"（{q.pr_url}）" if q.pr_url else ""
    summary = f"{q.label}の承認ゲートが答えを待っている: 工程 {q.stage}{where}"
    out = {**(metrics if metrics is not None else {}), "gate": describe(q)}
    return result.result(
        TOOL, result.GATE, summary, metrics=out, routes=routes(q), presentation_path=str(presentation) if presentation else None
    )


def material_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- 承認資料 -------------------------------------------------------------------------------------


def material_path(root: Path, q: Question) -> Path:
    """問いの承認資料の置き場（`run.work_root` の下）。問いごとに 1 つで、書いた後は書き換えない。"""
    return run.work_root(root, q.table_id) / MATERIAL_DIR / f"{q.id}.md"


def unconfirmed(v: run.View, stage: str) -> list[str]:
    """工程の済んだステップの結果が残した欠け（扱われずに届いたレビューの指摘など。#2444）。"""
    out = []
    for step, report in run.step_progress(v, stage).done.items():
        gaps = report["gaps"] if isinstance(report, dict) and isinstance(report.get("gaps"), list) else []
        out += [f"{step}: {g['name']}（{g['reason']}）" for g in gaps if isinstance(g, dict) and {"name", "reason"} <= set(g)]
    return out


def material(v: run.View, q: Question) -> str:
    """承認資料（Markdown）。問い・見る PR と head・出した版（#2554）・未確認の点（#2444）・答えの選択肢を持つ。"""
    ctx = v.context()
    lines = [f"# {q.label}の承認ゲート", "", f"- 工程: {q.stage}（ステップ {q.step}）", f"- 問いの id: {q.id}"]
    if q.pr_url or q.pr:
        lines.append(f"- PR: {q.pr_url or f'#{q.pr}'}")
    if q.target.head:
        lines.append(f"- 見る head: {q.target.head}")
    if q.gate == "release" and isinstance(ctx.get("version"), str):
        lines.append(f"- 出す版: {ctx['version']}")
    lines += ["", "## 未確認の点", "", *([f"- {u}" for u in unconfirmed(v, q.stage)] or ["- （無し）"])]
    lines += ["", "## 答え", "", *[f"- {c}" for c in q.choices]]
    return "\n".join(lines) + "\n"


def write_material(root: Path, v: run.View, q: Question) -> Path:
    """承認資料を書く（書いてあれば書き直さない。答えた者が見た資料の sha256 を変えないため）。"""
    path = material_path(root, q)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(material(v, q), encoding="utf-8")
        tmp.replace(path)
    return path


# --- 答え ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Answer:
    """`ndf answer` が受ける答え。`by` は答えた者（`service:<主体>` など）、`key` は冪等の鍵、`seen` は見た対象。"""

    gate_id: str
    value: str
    reason: str
    by: str
    key: str
    notes: Optional[Path] = None
    seen: Target = field(default_factory=Target)
    drop: tuple[int, ...] = ()


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
        extra = {
            "gate_id": q.id,
            "gate": q.gate,
            "by": a.by,
            "key": a.key,
            "seen": {"head": a.seen.head, "material_sha256": a.seen.material},
            "dropped": list(a.drop),
            "carried_row": carried,
        }
        draft = run.answer_draft(v, q.stage, q.step, a.value, a.reason, text, a.notes, actor=a.by, extra=extra)
        seqs = store.append_events(conn, table_id, [draft], v.events[-1].seq if v.events else 0)
    summary = f"工程 {q.stage} へ答え {a.value} を書いた（{a.by}）。続きは ndf progress next で流す"
    metrics = {"table_id": table_id, "seqs": seqs, "gate_id": q.id, "carried_row": carried}
    out = result.result(TOOL, result.OK, summary, metrics=metrics, next=result.command("progress", "next", table_id))
    return command.with_warnings(out, after=record_answer(v, q, a, rejections_path))


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


def recheck(st: store.Store, table_id: str, observe: Observe) -> Optional[str]:
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
    seen = p["seen"] if isinstance(p.get("seen"), dict) else {}
    changed = observe(q).differs(Target(seen.get("head"), seen.get("material_sha256")))
    if not changed:
        return None
    why = "答えた後に対象が変わった: " + " / ".join(changed)
    payload = {"step": m.step.id, "status": "waiting", "report": None, "generation": m.progress.generation, "reason": why}
    draft = model.EventDraft(run.STEP_EVENT, run.ACTOR, m.stage.name, payload=payload, rev=v.rev)
    # 比べた後に答えが読まれていれば（同じ答えの事象が今も読む答えでなければ）書かない
    run.append(st, table_id, lambda now: [draft] if _still(now, m.event) else [])
    return why


def _still(v: run.View, answered: model.Event) -> bool:
    m = run.decide(v)
    return m.action == run.Action.CONSUME and m.event is not None and m.event.seq == answered.seq


def present(st: store.Store, table_id: str, cwd: Path, out: dict[str, Any], observe: Observe) -> dict[str, Any]:
    """`ndf progress` の結果（next・wait・work）が承認ゲート（`ndf.listen`）の待ちなら、問い・承認資料・答えごとの行き先の結果に差し替える。"""
    v = run.read(st, table_id)
    m = run.decide(v)
    if out["status"] != result.GATE or m.action != run.Action.GATE or m.step is None or not invoke.is_listen(m.step):
        return out
    try:
        repo: Optional[str] = Git(cwd).remote_repo()
    except GitError:
        repo = None
    asked = question(v, m, repo=repo)
    path = write_material(st.root, v, asked)
    q = replace(asked, target=observe(asked))
    metrics = out["metrics"] if isinstance(out.get("metrics"), dict) else {}
    return command.with_warnings(gate_result(q, metrics, path), before=list(out["warnings"]) if "warnings" in out else [])


# --- 差し戻しの記録 ---------------------------------------------------------------------------------


def rejections_path() -> Path:
    """`gate-rejections.jsonl` の置き場（表 places の mvv-state。環境変数があればその位置、無ければ状態の置き場）。"""
    var = facts.value("places", "mvv-state", "env")
    base = Path(os.environ[var]) if os.environ.get(var) else identity.load().state_dir / facts.value("places", "mvv-state", "path")
    return base / facts.value("ndf-files", "gate-rejections", "path")


def rejection_row(v: run.View, q: Question, a: Answer) -> dict[str, Any]:
    """`gate-rejections.jsonl` の 1 行（答えた時刻・区分・答え・理由・範囲・答えた者）。"""
    ctx = v.context()
    issues = sorted(int(n) for n in ctx["issues"]) if isinstance(ctx.get("issues"), list) else []
    prs = sorted({n for n in (ctx.get("design_pr"), ctx.get("pr")) if isinstance(n, int)})
    return {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "gate": q.gate if q.gate else "plan",
        "gate_id": q.id,
        "what": a.reason,
        "sprint": v.table_id,
        "issues": issues,
        "prs": prs,
        "by": a.by,
        "outcome": "rejected" if a.value in REJECTING else a.value,
        "answer": a.value,
    }


def record_answer(v: run.View, q: Question, a: Answer, where: Callable[[], Path]) -> list[str]:
    """答えを `gate-rejections.jsonl` へ写す（承認も差し戻しも）。写せなければ警告を返す（答えの正本は事象に書いてある）。"""
    try:
        path = where()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rejection_row(v, q, a), ensure_ascii=False) + "\n")
    except (OSError, identity.IdentityError) as exc:
        return [f"答えを gate-rejections へ写せなかった（答えは進行表に書いた）: {exc}"]
    return []


def rejections(events: Iterable[model.Event]) -> dict[str, int]:
    """承認しなかった答え（承認しない・差し戻し）を承認ゲートの区分ごとに数える（gate-rejections の正本は事象）。"""
    out: dict[str, int] = {}
    for ev in events:
        p = ev.draft.payload if ev.draft.payload is not None else {}
        if ev.draft.kind == run.ANSWERED and p.get("answer") in REJECTING and p.get("kind") == invoke.LISTEN:
            gate = str(p["gate"]) if p.get("gate") else "plan"
            out[gate] = out[gate] + 1 if gate in out else 1
    return out


# --- MVV 判定の前の機械の検査 --------------------------------------------------------------------------


@dataclass(frozen=True)
class Changed:
    """PR が変えたファイル。`previous` は名前を変えた前のパス、`renamed` は名前の変更か。"""

    path: str
    previous: Optional[str] = None
    renamed: bool = False


@dataclass(frozen=True)
class MvvFacts:
    """機械の検査が読む事実。`status` はプロジェクト MVV の状態（approved・unapproved・mismatch・unreadable・absent）。

    `current` は今の MVV のファイルの sha256、`approved` は利用者の承認の記録の sha256、`referenced` はスプリントの
    状態が持つ参照の sha256。`boundary_paths` は進め方の宣言のレッドラインのパス。
    """

    status: str
    current: Optional[str]
    approved: Optional[str]
    referenced: Optional[str]
    mode: str
    changed: tuple[Changed, ...] = ()
    boundary_paths: tuple[str, ...] = ()


def check_mvv_hash(f: MvvFacts) -> Optional[str]:
    """検査 1: プロジェクト MVV が承認済みで、今のファイルのハッシュが承認の記録と一致する。"""
    if f.status != "approved":
        return f"プロジェクト MVV が承認済みでない（{f.status}）"
    if not f.current or f.current != f.approved:
        return f"今の MVV のハッシュ {f.current or '無し'} が承認の記録 {f.approved or '無し'} と一致しない"
    return None


def check_approval_record(f: MvvFacts) -> Optional[str]:
    """検査 2: スプリントの状態が承認の記録を参照し、その sha256 が承認の記録と一致する。"""
    if not f.referenced:
        return "スプリントの状態に承認済み MVV の参照が無い"
    if f.referenced != f.approved:
        return f"スプリントの状態の参照 {f.referenced} が承認の記録 {f.approved or '無し'} と一致しない"
    return None


def check_mode(f: MvvFacts) -> Optional[str]:
    """検査 3: モードが承認ゲートを省かないモード（operation・documentation）でない。"""
    return f"モード {f.mode} は承認ゲートを省かない" if f.mode in EXCLUDED_MODES else None


def _matches(c: Changed, patterns: Sequence[str]) -> bool:
    """変えたファイル（名前を変えた前のパスも）に、パターン（gitignore の書き方。根からの相対）のどれかが当たるか。"""
    spec = pathspec.PathSpec.from_lines("gitignore", [p for p in patterns if p.strip()])
    return any(spec.match_file(p.removeprefix("./")) for p in (c.path, c.previous) if p)


def mvv_decls() -> tuple[str, ...]:
    """利用者の MVV の宣言のパス（根からの相対。表 ndf-files と places から引く）。"""
    decls = [r for r in facts.rows("ndf-files", kind="declaration") if Path(r["path"]).stem == MVV_STEM]
    return tuple(f"{facts.value('places', r['place'], 'path')}/{r['path']}" for r in decls)


def _hits(changed: Iterable[Changed], patterns: Sequence[str]) -> list[str]:
    out = []
    for c in changed:
        if c.renamed and not c.previous:
            out.append(f"{c.path}（名前を変えた前のパスが分からない）")
        elif _matches(c, patterns):
            out.append(f"{c.previous} → {c.path}" if c.previous else c.path)
    return out


def check_boundary(f: MvvFacts) -> Optional[str]:
    """検査 4: 変えたファイル（名前を変えた前のパスも）がレッドラインのパスに当たらない。"""
    hits = _hits(f.changed, f.boundary_paths) if f.boundary_paths else []
    return "レッドラインのパスに当たる: " + " / ".join(hits) if hits else None


def check_mvv_decl(f: MvvFacts) -> Optional[str]:
    """検査 5: 利用者の MVV の宣言（`.ndf/mvv.*`）を変えない（共通原則の C7。宣言では外せない）。"""
    decls = mvv_decls()
    hits = [c for c in f.changed if _matches(c, decls)]
    return "MVV の宣言を変える（共通原則の C7）: " + " / ".join(c.path for c in hits) if hits else None


# 機械の検査を当てる順
CHECKS: tuple[Callable[[MvvFacts], Optional[str]], ...] = (
    check_mvv_hash,
    check_approval_record,
    check_mode,
    check_boundary,
    check_mvv_decl,
)


def refusals(f: MvvFacts) -> list[str]:
    """外れた機械の検査の理由（空なら MVV 判定へ進める）。"""
    return [why for check in CHECKS if (why := check(f)) is not None]


def well_formed(verdict: Any) -> bool:
    """判定の応答が契約の形か（verdict・reasons・boundary がそろい、reasons と boundary が空でない文字列の配列）。

    契約の外は承認ゲートを省かない。boundary を欠く・空の文字を含む応答を、レッドラインが無いと読まない。
    """

    def strings(x: Any) -> bool:
        return isinstance(x, list) and all(isinstance(s, str) and s.strip() for s in x)

    return (
        isinstance(verdict, dict)
        and verdict.get("verdict") in VERDICTS
        and strings(verdict.get("reasons"))
        and strings(verdict.get("boundary"))
    )


def may_skip(f: MvvFacts, verdict: Any) -> tuple[bool, list[str]]:
    """承認ゲートを省けるか（省けないときの理由）。外れた検査・形の違う判定・従わない判定・レッドラインがあれば省かない。"""
    why = refusals(f)
    if why:
        return False, why
    if not well_formed(verdict):
        return False, ["MVV 判定の応答が契約の形でない"]
    if verdict["verdict"] != FOLLOW:
        return False, [f"MVV 判定が {verdict['verdict']}"]
    if verdict["boundary"]:
        return False, ["レッドラインに当たる: " + " / ".join(verdict["boundary"])]
    return True, []


def note(gate: str, verdict: Any, why: Sequence[str]) -> str:
    """MVV 判定の記録（PR のコメントと承認資料に載せる Markdown）。レッドラインの中身も載せる（#2950）。"""
    v = verdict if isinstance(verdict, dict) else {}
    reasons = [str(r) for r in v["reasons"]] if isinstance(v.get("reasons"), list) else []
    boundary = [str(b) for b in v["boundary"]] if isinstance(v.get("boundary"), list) else []
    lines = [f"## MVV 判定（{model.gate_label(gate)}）", "", f"- 判定: {v['verdict'] if 'verdict' in v else '無し'}"]
    lines += ["", "### 理由", "", *([f"- {r}" for r in reasons] or ["- （無し）"])]
    lines += ["", "### レッドライン", "", *([f"- {b}" for b in boundary] or ["- （無し）"])]
    if why:
        lines += ["", "### 承認ゲートを省かない理由", "", *[f"- {w}" for w in why]]
    return "\n".join(lines) + "\n"


# --- 副命令 ndf answer ----------------------------------------------------------------------------


def ask(gate: Optional[str]) -> dict[str, Any]:
    """プランの中の承認ゲート（雛形の `{{gate}}`）: 答えを待つ結果 JSON。答えはプランの行き先が受ける。"""
    if gate is not None and gate not in model.table()["gates"]:
        raise model.Refused(f"承認ゲートの区分 {gate} は無い（{', '.join(model.table()['gates'])}）")
    label = model.gate_label(gate) if gate else "プランの中の承認ゲート"
    out = {a: result.command("progress", "answer", "--answer", a, ask=("table_id", "stage", "reason")) for a in (run.APPROVED, "rejected")}
    return result.result(TOOL, result.GATE, f"{label}の答えを待つ", metrics={"gate": {"kind": gate, "label": label}}, routes=out)


def _cmd_give(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
    seen = Target(a.head, a.material_sha256)
    given = Answer(a.gate, a.result, a.reason, a.by, a.key, Path(a.notes) if a.notes else None, seen, tuple(a.drop))
    return answer(st, a.table_id, given, github_observe(cwd, st.root))


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL}", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("table_id", help="答える進行表の ID")
    ap.add_argument("--gate", required=True, help="問いの id（承認ゲートの結果の metrics.gate.id）")
    ap.add_argument("--result", required=True, help="答えの語（承認ゲートの結果の routes のキー）")
    ap.add_argument("--reason", required=True)
    ap.add_argument("--by", required=True, help="答えた者（human・conductor・organizer か service:<主体>）")
    ap.add_argument("--key", required=True, help="冪等の鍵（同じ鍵の答え直しは書かない）")
    ap.add_argument("--notes", help="差し戻し（revise）の指摘ファイル")
    ap.add_argument("--head", help="見た PR の head の SHA")
    ap.add_argument("--material-sha256", help="見た承認資料の sha256")
    ap.add_argument("--drop", type=int, nargs="*", default=[], help="設計承認ゲートで外す課題の番号")
    return ap


def ask_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL} ask")
    ap.add_argument("--gate", choices=sorted(model.table()["gates"]))
    return ap


def _progress(a: argparse.Namespace, cwd: Path, st: store.Store, observe: Optional[Observe] = None) -> dict[str, Any]:
    """`ndf progress` の工程を流す副命令。レーンの行を終える `Env` で流し、答えを読む前に対象を見直し、承認ゲートの
    待ちは問いと答えごとの行き先の結果にする。"""
    seen = observe if observe is not None else github_observe(cwd, st.root)
    why = recheck(st, a.table_id, seen)
    out = run.COMMANDS[a.action](run.with_env(a, lane_env(run.Env(facts=command.facts_of))), cwd, st)
    return command.with_warnings(present(st, a.table_id, cwd, out, seen), before=[why] if why else [])


def _verbs(ap: argparse.ArgumentParser) -> set[str]:
    return {name for act in ap._actions if isinstance(act, argparse._SubParsersAction) for name in act.choices}


def progress_verbs() -> set[str]:
    """`ndf progress` の副命令の名前（工程を流す副命令と、進行表を作る・直す副命令）。"""
    return _verbs(run.parser()) | _verbs(command.parser())


def main(argv: list[str]) -> int:
    """副命令 `ndf answer` と `ndf progress`（副命令の表の 2 行がこのモジュールを指す）。

    - `ask` はプランの中の承認ゲート、進行表の ID で始まれば承認ゲートへの答え（`ndf answer`）
    - `ndf progress` の副命令の名前か、引数が無い・`-` で始まるときは `ndf progress`。スプリントの完了と同じ
      トランザクションでレーンの行を終える `Env`（`lane_env`。決定 17）で流す。P1b（`run`）はレーンを読まない
    """
    if argv and argv[0] == "ask":
        return command.run_main(ask_parser(), lambda a, _cwd: ask(a.gate), argv[1:])
    if argv and argv[0] in run.COMMANDS:
        return command.run_main(run.parser(), lambda a, cwd: command.with_store(cwd, _progress, a), argv)
    if not argv or argv[0].startswith("-") or argv[0] in progress_verbs():
        return command.main(argv)
    return command.run_main(parser(), lambda a, cwd: command.with_store(cwd, _cmd_give, a), argv)
