"""承認ゲートの問い・答えごとの行き先・承認資料（`ndf.progress.gate` の部品）。

問いの id は待ちに入った事象の番号から作り、差し戻しで流し直した後の問いは別の id になる。承認資料は問いごとに 1 つで、
書いた後は書き換えない（答えた者が見た資料の sha256 を変えないため）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

from ndf import cli
from ndf.contracts import result
from ndf.progress import lane, model, run

# 結果 JSON の tool（副命令の表がこのパッケージを指す）
TOOL = cli.tool_of(__package__)

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


# 承認の後に文書だけが加わって出し直さなかった head の置き場（承認資料の隣）
ACCEPTED_SUFFIX = ".accepted.json"


def accepted_path(root: Path, table_id: str, gate_id: str) -> Path:
    return run.work_root(root, table_id) / MATERIAL_DIR / f"{gate_id}{ACCEPTED_SUFFIX}"


# 依頼の中身の kind（計画の進行表の依頼。依頼の ID の頭にも使う）
RELEASE_KIND = "lane-release"


def request_id(q: Question) -> str:
    """リリースの依頼の ID（問いの id から作る。差し戻しで流し直した問いは別の依頼になる）。"""
    return f"{RELEASE_KIND}-{q.id}"


def plan_request(view: lane.PlanView, ident: str) -> Optional[lane.Request]:
    return next((r for r in view.requests.values() if r.body.get("kind") == RELEASE_KIND and r.body.get("id") == ident), None)


@dataclass(frozen=True)
class Answer:
    """`ndf answer` が受ける答え。`by` は答えた者（`service:<主体>` など）、`key` は冪等の鍵、`seen` は見た対象。

    `outputs` は答えと同じトランザクションで工程の出力としてスプリントの文脈へ書く値（まとめたリリースの版と
    リリース記録の PR。#2783）。
    """

    gate_id: str
    value: str
    reason: str
    by: str
    key: str
    notes: Optional[Path] = None
    seen: Target = field(default_factory=Target)
    drop: tuple[int, ...] = ()
    outputs: Mapping[str, Any] = field(default_factory=dict)
