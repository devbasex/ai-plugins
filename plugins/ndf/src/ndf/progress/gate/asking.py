"""承認ゲートの質問・回答ごとのルート・承認資料（`ndf.progress.gate` の部品）。

質問の id は待ちに入った事象の番号から作り、変更要求で流し直した後の質問は別の id になる。承認資料は質問ごとに 1 つで、
書いた後は書き換えない（回答した者が見た資料の sha256 を変えないため）。
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
from dataclasses import dataclass, field
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Optional

from ndf import cli
from ndf.contracts import result
from ndf.progress import lane, model, run

# 結果 JSON の tool（サブコマンドの表がこのパッケージを指す）
TOOL = cli.tool_of(__package__)


def _gate(word: str) -> str:
    """承認ゲートの区分の語（コントラクト `workflow/progress.json` の gates の行）。表に無ければ import の時点で `KeyError`。"""
    model.gate_label(word)
    return word


# 承認ゲートの区分（設計承認ゲート・リリース承認ゲート）
DESIGN_GATE = _gate("design")
RELEASE_GATE = _gate("release")
# 質問の id（承認ゲートの回答と、MVV 判定で省いた回答が同じ id を持つため、定義は model）
question_id = model.question_id
# 承認しなかった回答（変更要求の記録に数える）
REJECTING = (run.REQUEST_CHANGES, run.REJECTED)
# 承認資料の保存先（ステートマシンごとの作業の保存先の下。名前は質問の id）
MATERIAL_DIR = "gate-materials"


# --- 質問 --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """見る対象。`head` は PR の head の SHA、`material` は承認資料の sha256。分からなければ None。"""

    head: Optional[str] = None
    material: Optional[str] = None

    def differs(self, seen: Target) -> list[str]:
        """回答した者が見た対象 `seen` と今の対象の違い（今の側が分かる項目だけを比べる）。"""
        out = []
        for name, now, then in (("head の SHA", self.head, seen.head), ("承認資料の sha256", self.material, seen.material)):
            if now is not None and then != now:
                out.append(f"{name}（見た値 {then or '無し'}・今の値 {now}）")
        return out

    def as_dict(self) -> dict[str, Optional[str]]:
        """見た対象を記録する形（事象の payload の `seen` と、リクエストの隣の `.seen.json`）。"""
        return {"head": self.head, "material_sha256": self.material}

    @classmethod
    def from_dict(cls, raw: Any) -> Target:
        """`as_dict` の形から読み戻す。オブジェクトでなければ空の対象。"""
        if not isinstance(raw, dict):
            return cls()
        return cls(raw.get("head"), raw.get("material_sha256"))


@dataclass(frozen=True)
class Question:
    """待っている承認ゲートの質問。`gate` は区分（design・release）で、プランの中の承認ゲートは None。"""

    id: str
    machine_id: str
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


def sprint_issues(v: run.View) -> list[int]:
    """スプリントの文脈の課題の番号（整数の昇順）。文脈に並びが無ければ空。"""
    ctx = v.context()
    return sorted(int(n) for n in ctx["issues"]) if isinstance(ctx.get("issues"), list) else []


def pr_url(repo: Optional[str], number: Optional[int]) -> Optional[str]:
    return f"https://github.com/{repo}/pull/{number}" if repo and number else None


def _waiting_context(v: run.View, prog: run.StepProgress) -> dict[str, Any]:
    """待っている工程から見た文脈。スプリントの文脈に、その工程の終わったステップの出力を順に重ねる。

    工程の出力（`outputs_recorded`）は工程が終わる時にだけ書かれるため、工程の途中の承認ゲート（設計承認ゲートの
    設計 PR など）はステップの記録（`step_recorded` の report.outputs）から読む。"""
    out = v.context()
    for report in prog.done.values():
        if isinstance(report.get("outputs"), dict):
            out.update(report["outputs"])
    return out


def question(v: run.View, m: run.Move, *, repo: Optional[str] = None, target: Target = Target()) -> Question:
    """待っている手 `m`（`run.Action.GATE`）の質問。PR は設計承認ゲートなら設計 PR、ほかはスプリントの PR。"""
    if m.action != run.Action.GATE or m.stage is None or m.step is None or m.progress is None:
        raise model.Refused("回答を待つ工程が無い")
    ctx = _waiting_context(v, m.progress)
    key = "design_pr" if m.step.gate == DESIGN_GATE else "pr"
    number = ctx[key] if isinstance(ctx.get(key), int) else None
    qid = question_id(v.machine_id, v.rev, m.stage.name, m.step.id, m.progress.anchor)
    return Question(qid, v.machine_id, m.stage.name, m.step.id, m.step.gate, tuple(run.choices(m)), number, pr_url(repo, number), target)


def waiting(v: run.View, **kw: Any) -> Optional[Question]:
    """ステートマシンが待っている質問。待っていなければ None。"""
    m = run.decide(v)
    return question(v, m, **kw) if m.action == run.Action.GATE else None


# --- 回答ごとのルート -----------------------------------------------------------------------------


def _asks(q: Question, answer: str) -> tuple[str, ...]:
    """回答を打つ側が埋めるキー。変更要求は指摘ファイル、設計承認ゲートの承認は外す課題（無ければ省く）も持つ。"""
    asks = ["reason", "by", "key"]
    if answer == run.REQUEST_CHANGES:
        asks.insert(0, "notes")
    if answer == run.APPROVED and q.gate == DESIGN_GATE:
        asks.append("drop")
    return tuple(asks)


def routes(q: Question) -> dict[str, Any]:
    """回答の語 → `ndf answer` のコマンド。見た対象（head・承認資料）を引数に固定し、回答した後に対象が変われば断られる。"""
    seen: list[str] = []
    if q.target.head:
        seen += ["--head", q.target.head]
    if q.target.material:
        seen += ["--material-sha256", q.target.material]
    return {a: result.command(TOOL, q.machine_id, "--gate", q.id, "--result", a, *seen, ask=_asks(q, a)) for a in q.choices}


def describe(q: Question) -> dict[str, Any]:
    """結果 JSON の `metrics.gate` に載せる質問（機械で読む形）。"""
    return {
        "id": q.id,
        "kind": q.gate,
        "label": q.label,
        "machine_id": q.machine_id,
        "stage": q.stage,
        "step": q.step,
        "pr": q.pr,
        "pr_url": q.pr_url,
        "head": q.target.head,
        "material_sha256": q.target.material,
        "choices": list(q.choices),
    }


def gate_result(q: Question, metrics: Optional[Mapping[str, Any]] = None, presentation: Optional[Path] = None) -> dict[str, Any]:
    """承認ゲートで止まった結果 JSON（status gate・質問・回答ごとのルート）。"""
    where = f"（{q.pr_url}）" if q.pr_url else ""
    summary = f"{q.label}の承認ゲートが回答を待っている: 工程 {q.stage}{where}"
    out = {**(metrics if metrics is not None else {}), "gate": describe(q)}
    return result.result(
        TOOL, result.GATE, summary, metrics=out, routes=routes(q), presentation_path=str(presentation) if presentation else None
    )


def material_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- 承認資料 -------------------------------------------------------------------------------------


def material_path(root: Path, q: Question) -> Path:
    """質問の承認資料の保存先（`run.work_root` の下）。資料を作る時点の head ごとに別のファイルで、書いた後は書き換えない。

    head が分からなければ質問の id だけの名前（この変更の前の名前でもある。`answering.seen_material` が探す）。"""
    name = f"{q.id}-{q.target.head}.md" if q.target.head else f"{q.id}.md"
    return run.work_root(root, q.machine_id) / MATERIAL_DIR / name


def unconfirmed(v: run.View, stage: str) -> list[str]:
    """工程の済んだステップの結果が残した欠け（扱われずに届いたレビューの指摘など。#2444）。

    終わった（ok の）結果は欠けを `outputs.gaps` に持つ（結果のコントラクトは、上のステップの `gaps` を incomplete にだけ許す）。
    """
    out = []
    for step, report in run.step_progress(v, stage).done.items():
        gaps = [*_gaps_of(report), *_gaps_of(report.get("outputs") if isinstance(report, dict) else None)]
        out += [f"{step}: {g['name']}（{g['reason']}）" for g in gaps if isinstance(g, dict) and {"name", "reason"} <= set(g)]
    return out


def _gaps_of(part: Any) -> list[Any]:
    return part["gaps"] if isinstance(part, dict) and isinstance(part.get("gaps"), list) else []


def bullets(items: Iterable[str]) -> list[str]:
    """Markdown の箇条書きの行。空なら「- （無し）」の 1 行（承認資料と MVV 判定の記録で空の表し方を揃える）。"""
    return [f"- {x}" for x in items] or ["- （無し）"]


# --- 承認資料の「MVV 判定」の欄（#3959） -------------------------------------------------------------

# 判定のやり直しを求める文（承認する人は「変更を求める」で回答し、担当の側が判定を打ち直す）
REDO = "承認せず、「変更を求める」の回答で判定のやり直しを求めてください"
# 照らして一致しないときの保留の欄
UNSEEN_HOLDS = "- レビューの保留: 未確認（今の head の判定が無い）"


def _mismatch(out: Mapping[str, Any], pr: Optional[int], head: Optional[str]) -> Optional[list[str]]:
    """判定の `pr`・`head` を質問の PR と資料を作る時点の head に照らし、一致しなければ欄の行を返す（一致すれば None）。

    別の PR の判定は、判定した PR の番号と head を載せない（承認する人が見られない PR とコミットを知らせないため）。"""
    if "pr" not in out and "head" not in out and isinstance(out.get("stopped_by"), dict):
        text = out["stopped_by"]["text"] if "text" in out["stopped_by"] else ""
        # MVV を導入していないリポジトリは PR を読む前に止める（`mvv_gate.mvv_declared_locally`）。PR の特定の失敗と書かない
        if out["stopped_by"].get("kind") == "mvv_unapproved":
            return [f"- MVV 判定は打っていない。{text}", "- レビューの保留: 未確認（MVV 判定を打っていない）"]
        return [
            f"- 対象の PR を特定できなかったため、MVV 判定は済んでいない。{text}",
            "- レビューの保留: 未確認（保留を読む前に判定が止まった）",
        ]
    if "pr" not in out or "head" not in out or head is None:
        line = f"- 残っている判定が、承認を求める PR #{pr} の今の変更に対するものかを確かめられません。この変更に対する判定はまだありません。{REDO}"
        return [line, UNSEEN_HOLDS]
    if out["pr"] != pr:
        return [f"- 承認を求める PR #{pr}・head {head} に対する判定はまだありません。{REDO}", UNSEEN_HOLDS]
    if out["head"] != head:
        line = (
            f"- 承認を求める PR #{pr}・head {head} の判定はまだ無い。今ある判定は、判定の後に PR が更新される前の head {out['head']} に"
            f"対するもの。この変更に対する判定はまだありません。{REDO}"
        )
        return [line, UNSEEN_HOLDS]
    return None


def _strings(raw: Any) -> list[str]:
    return [str(x) for x in raw] if isinstance(raw, list) else []


def _unread_reason(gate: str, verdict: Any, why: list[str], private: bool) -> str:
    """保留が `null` のときの未確認の理由。空の `why` でも組める。"""
    if gate == RELEASE_GATE:
        return "リリースの保留は #4105 まで読まない（省かない）"
    if verdict is None:
        return f"保留を読む前に判定が止まった: {why[0]}" if why and private else "保留を読む前に判定が止まった"
    return "この判定は保留を読んでいない"


def _holds_lines(out: Mapping[str, Any], gate: str, private: bool) -> list[str]:
    holds, verdict, why = out.get("holds"), out.get("verdict"), _strings(out.get("why"))
    if isinstance(holds, list) and holds:
        return [f"- レビューの保留: {len(holds)} 件", *([f"  - {h}" for h in holds] if private else [])]
    if isinstance(holds, list):
        return ["- レビューの保留: 無し（レビューの記録に保留が無い。記録がまだ作られていない場合を含む）"]
    return [f"- レビューの保留: 未確認（{_unread_reason(gate, verdict, why, private)}）"]


def _verdict_lines(out: Mapping[str, Any], private: bool) -> list[str]:
    verdict = out.get("verdict")
    if not isinstance(verdict, dict):
        stop = out.get("stopped_by")
        text = stop.get("text") if isinstance(stop, dict) else None
        return [f"- 判定できない。理由: {text}" if text else "- 判定できない"]
    reasons, boundary = _strings(verdict.get("reasons")), _strings(verdict.get("boundary"))
    lines = [
        f"- 判定: {verdict['verdict'] if 'verdict' in verdict else '無し'}",
        f"- 根拠の項目: {'・'.join(_strings(verdict.get('basis') if private else model.mvv_basis_shared(verdict.get('basis')))) or '無し'}",
    ]
    if not private:
        return [*lines, f"- 理由 {len(reasons)} 件・レッドライン {len(boundary)} 件"]
    lines += ["- 理由:", *[f"  - {r}" for r in reasons or ["（無し）"]]]
    return [*lines, "- レッドライン:", *[f"  - {b}" for b in boundary or ["（無し）"]]]


def _where_lines(out: Mapping[str, Any], private: bool) -> list[str]:
    """全文の参照先。閲覧範囲を限れない保存先では、全文を読める場所が無ければやり直しを求める（読むべき本文が無ければ求めない）。"""
    url = out.get("note_url") if out.get("noted") is True else None
    if private:
        return (
            [f"- 今回の判定のコメント: {url}"]
            if url
            else ["- 今回は PR に判定のコメントを載せていない。PR に既存の判定のコメントがあっても、今回の結果はここで確かめる"]
        )
    if out.get("verdict") is None and not out.get("holds"):
        return []
    if url:
        return [
            f"- 理由と保留の全文は、今回の判定のコメント {url} で読めます。コメントを開けない場合や、判断に必要な理由が伏せ字で読めない場合は、{REDO}"
        ]
    return [f"- 理由と保留の全文を読める場所がありません。{REDO}"]


def mvv_lines(out: Optional[Mapping[str, Any]], gate: str, pr: Optional[int], head: Optional[str], *, private: bool = True) -> list[str]:
    """承認資料の「## MVV 判定」の欄の行。`out` は待っている工程の判定のステップの出力 `mvv_gate`（無ければ None）。

    `pr`・`head` は質問の PR と資料を作る時点の head で、判定の `pr`・`head` と照らす。`private` が偽（保存先の閲覧範囲を
    限れない）なら判定の自由記述は伏せて保存されているので、本文の代わりに件数と全文の参照先を載せる（#3959）。"""
    if out is None:
        return ["- 判定できない: 待っている工程の判定のステップが済んでいない", "- レビューの保留: 未確認（判定の結果が無い）"]
    off = _mismatch(out, pr, head)
    if off is not None:
        return off
    why = _strings(out.get("why"))
    lines = [f"- 判定した PR: #{pr}・head {head}", *_verdict_lines(out, private)]
    if private:
        lines += ["- 省かない理由:", *[f"  - {w}" for w in why or ["（無し）"]]]
    else:
        lines.append(f"- 省かない理由 {len(why)} 件")
    return [*lines, *_holds_lines(out, gate, private), *_where_lines(out, private)]


def material(v: run.View, q: Question, *, private: bool = True) -> str:
    """承認資料（Markdown）。質問・見る PR と head・出したバージョン（#2554）・未確認の点（#2444）・MVV 判定（#3959）・回答の選択肢を持つ。

    `private` は保存先の閲覧範囲を限れたか（`store.Store.private`）で、偽なら MVV 判定の自由記述の代わりに件数を載せる。"""
    ctx = v.context()
    lines = [f"# {q.label}の承認ゲート", "", f"- 工程: {q.stage}（ステップ {q.step}）", f"- 質問の id: {q.id}"]
    if q.pr_url or q.pr:
        lines.append(f"- PR: {q.pr_url or f'#{q.pr}'}")
    if q.target.head:
        lines.append(f"- 見る head: {q.target.head}")
    if q.gate == RELEASE_GATE and isinstance(ctx.get("product_version"), str):
        lines.append(f"- 出すバージョン: {ctx['product_version']}")
    lines += ["", "## 未確認の点", "", *bullets(unconfirmed(v, q.stage))]
    step = model.MVV_GATE_STEPS.get(q.gate) if q.gate else None
    if step is not None and any(s.id == step for s in v.snap.stage(q.stage).steps):
        lines += ["", "## MVV 判定", "", *mvv_lines(_judged(v, q.stage, step), q.gate, q.pr, q.target.head, private=private)]
    lines += ["", "## 回答", "", *[f"- {c}" for c in q.choices]]
    return "\n".join(lines) + "\n"


def _judged(v: run.View, stage: str, step: str) -> Optional[Mapping[str, Any]]:
    """待っている工程の判定のステップの済んだ記録の出力 `mvv_gate`（無ければ None）。

    文脈（`v.context()`）は工程が終わるまで書かれず前の工程の判定が残るので読まない（#3959）。"""
    report = run.step_progress(v, stage).done.get(step)
    outputs = report.get("outputs") if isinstance(report, dict) else None
    held = outputs.get(model.MVV_KEY) if isinstance(outputs, dict) else None
    return held if isinstance(held, dict) else None


def write_material(root: Path, v: run.View, q: Question, *, private: bool = True) -> Path:
    """承認資料を所有者だけが読める 0600 で書く（書いてあれば書き直さない。回答した者が見た資料の sha256 を変えないため）。

    一時ファイルは呼び出しごとに別名・排他的・0600 で作り、`os.link` で置く。置き先が既にあれば（同時に走った別の呼び出しが
    先に置いた）その資料を使い、上書きしない。資料のディレクトリは 0700 で作る（#3959）。"""
    path = material_path(root, q)
    if path.exists():
        return path
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(material(v, q, private=private))
        with contextlib.suppress(FileExistsError):  # noqa: NDF402 ファイル: 同時に走った別の呼び出しが先に置いた資料を使う
            os.link(tmp, path)
    finally:
        os.unlink(tmp)
    return path


# 承認の後に文書だけが加わって出し直さなかった head の保存先（承認資料の隣）
ACCEPTED_SUFFIX = ".accepted.json"


def accepted_path(root: Path, machine_id: str, gate_id: str) -> Path:
    return run.work_root(root, machine_id) / MATERIAL_DIR / f"{gate_id}{ACCEPTED_SUFFIX}"


# リクエストの中身の kind（計画のステートマシンのリクエスト。リクエストの ID の頭にも使う）
RELEASE_KIND = "lane-release"


def request_id(q: Question) -> str:
    """リリースのリクエストの ID（質問の id から作る。変更要求で流し直した質問は別のリクエストになる）。"""
    return f"{RELEASE_KIND}-{q.id}"


def plan_request(view: lane.PlanView, ident: str) -> Optional[lane.Request]:
    return next((r for r in view.requests.values() if r.body.get("kind") == RELEASE_KIND and r.body.get("id") == ident), None)


def release_request_of(conn: Any, q: Question) -> Optional[tuple[str, str, Optional[lane.Request]]]:
    """スプリントを持つレーンの行の（計画の ID, レーン）と、質問のリリースのリクエスト（無ければ None）。レーンの外なら None。"""
    found = lane.sprint_row(conn, q.machine_id)
    if found is None:
        return None
    plan_id, name, _row = found
    view, _ = lane.read_plan(conn, plan_id)
    return plan_id, name, plan_request(view, request_id(q))


@dataclass(frozen=True)
class Answer:
    """`ndf answer` が受ける回答。`by` は回答した者（`service:<主体>` など）、`key` は冪等のキー、`seen` は見た対象。

    `outputs` は回答と同じトランザクションで工程の出力としてスプリントの文脈へ書く値（まとめたリリースのバージョンと
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
