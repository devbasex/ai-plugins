"""承認ゲートの質問・回答ごとのルート・承認資料（`ndf.progress.gate` の部品）。

質問の id は待ちに入った事象の番号から作り、変更要求で流し直した後の質問は別の id になる。承認資料は質問ごとに 1 つで、
書いた後は書き換えない（回答した者が見た資料の sha256 を変えないため）。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field, replace
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any, Optional

from filelock import FileLock

from ndf import cli
from ndf.contracts import limits, result
from ndf.core import merge_approval
from ndf.progress import lane, model, plan, run
from ndf.progress.run_view import MATERIAL_DIR

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


# --- 質問 --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """見る対象。`head` は PR の head の SHA、`material` は承認資料の sha256。分からなければ None。

    `repo`・`pr`・`base` は承認の対象（ベースリポジトリ・PR の番号・マージ先）、`production` は承認でマージが本番へ
    反映され得るか（判定の部品の `reaches_production`）。マージの承認の証跡はこの組に結び付ける（#4409 の決定 7）。
    `presentation` は資料を書いた（出し直した）ときに発行した提示の識別子（`write_material`）。分からなければ None。
    `body`・`title` は PR の本文と題の sha256（承認の質問の文は題から作る。#4446）。分からなければ None。
    """

    head: Optional[str] = None
    material: Optional[str] = None
    repo: Optional[str] = None
    pr: Optional[int] = None
    base: Optional[str] = None
    production: Optional[bool] = None
    presentation: Optional[str] = None
    body: Optional[str] = None
    title: Optional[str] = None

    def binding(self) -> Optional[merge_approval.MergeTarget]:
        """承認の対象の 4 つの値がそろえば `merge_approval.MergeTarget`、欠ければ None。"""
        if not self.repo or self.pr is None or not self.base or not self.head:
            return None
        return merge_approval.MergeTarget(self.repo, int(self.pr), self.base, self.head)

    def differs(self, seen: Target) -> list[str]:
        """回答した者が見た対象 `seen` と今の対象の違い（今の側が分かる項目だけを比べる）。

        本文と題は、見た側も値を持つときだけ比べる。MVV 判定で省いた回答は head だけを見て書くためである。"""
        out = []
        for name, now, then in (("head の SHA", self.head, seen.head), ("承認資料の sha256", self.material, seen.material)):
            if now is not None and then != now:
                out.append(f"{name}（見た値 {then or '無し'}・今の値 {now}）")
        for name, now, then in (("PR の本文の sha256", self.body, seen.body), ("PR の題の sha256", self.title, seen.title)):
            if now is not None and then is not None and then != now:
                out.append(f"{name}（見た値 {then}・今の値 {now}）")
        return out

    def as_dict(self) -> dict[str, Any]:
        """見た対象を記録する形（事象の payload の `seen` と、リクエストの隣の `.seen.json`）。承認の対象の値は分かるときだけ置く。"""
        extra = {"repo": self.repo, "pr": self.pr, "base": self.base, "production": self.production, "presentation": self.presentation}
        seen = {"head": self.head, "material_sha256": self.material, "body_sha256": self.body, "title_sha256": self.title}
        return {**seen, **{k: v for k, v in extra.items() if v is not None}}

    @classmethod
    def from_dict(cls, raw: Any) -> Target:
        """`as_dict` の形から読み戻す。オブジェクトでなければ空の対象。旧版の形（無いキー）は None で読む。"""
        if not isinstance(raw, dict):
            return cls()
        pr = raw.get("pr")
        production = raw.get("production")
        return cls(
            raw.get("head"),
            raw.get("material_sha256"),
            raw.get("repo") if isinstance(raw.get("repo"), str) else None,
            pr if isinstance(pr, int) and not isinstance(pr, bool) else None,
            raw.get("base") if isinstance(raw.get("base"), str) else None,
            production if isinstance(production, bool) else None,
            raw.get("presentation") if isinstance(raw.get("presentation"), str) and raw.get("presentation") else None,
            raw.get("body_sha256"),
            raw.get("title_sha256"),
        )

    def presented(self, now: "Target") -> "Target":
        """提示した対象（承認の対象と本番へ反映されるか）に、回答した者が見た `now` の head・承認資料・本文と題を重ねた対象。"""
        return replace(self, head=now.head, material=now.material, body=now.body, title=now.title)


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
    if q.target.body:
        seen += ["--body-sha256", q.target.body]
    if q.target.title:
        seen += ["--title-sha256", q.target.title]
    if q.target.presentation:
        seen += ["--presentation", q.target.presentation]
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
        "body_sha256": q.target.body,
        "title_sha256": q.target.title,
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


def text_sha256(text: str) -> str:
    """PR の本文と題の sha256（UTF-8 のバイト列。`ndf records reading` の本文のハッシュと同じ計算）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
    return [*lines, *_labeled_bullets("理由", reasons), *_labeled_bullets("レッドライン", boundary)]


def _labeled_bullets(label: str, items: list[str]) -> list[str]:
    """見出しの行と、字下げした箇条書きの行。空なら「（無し）」の 1 行。"""
    return [f"- {label}:", *[f"  - {x}" for x in items or ["（無し）"]]]


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
    if q.target.base:
        lines.append(f"- マージ先: {q.target.base}")
    if q.target.production:
        lines.append(f"- {PRODUCTION_LINE}")
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
    先に置いた）その資料を使い、上書きしない。資料のディレクトリは 0700 で作る（#3959）。

    質問の対象（`q.target`）が承認の対象を持てば、提示した承認の対象として隣（`presented_path`）へ 1 度だけ書く
    （#4409 の決定 7）。提示した対象を書き直す必要があれば（`presented_stale`）、同じ head の資料と提示した対象の両方を
    `q.target` で書き直す。資料の sha256 が変わるので、古い資料を見て打った回答は断られる。
    提示した対象を書くたびに、新しい提示の識別子（`presentation`）を発行して一緒に書く。承認の対象が A → B → A と
    戻っても、最後の提示は最初の提示と別の識別子になる（リクエスト・回答・証跡を最初の提示と取り違えない）。

    承認の対象を持つ質問は、資料と提示した対象の書き込みを質問ごとに排他にする。同時に走った 2 つの呼び出しの片方の
    資料と、もう片方の対象（本番へ反映されるかが違い得る）が組にならないためである。
    """
    with _question_lock(root, q):
        return _write_pair(root, v, q, private)


def present_material(root: Path, v: run.View, q: Question, *, private: bool = True) -> tuple[Path, str, Target]:
    """`write_material` と同じに書き（書いてあれば使い直す）、承認資料のパス・sha256・提示した対象を返す。

    3 つを同じ排他の中で読む。解放した後に別々に読むと、同時に走った別の呼び出しの書き直しをはさんで、古いハッシュと
    新しい提示の識別子が同じ回答のルートに入る。
    """
    with _question_lock(root, q):
        path = _write_pair(root, v, q, private)
        return path, material_sha256(path), read_presented(root, q)


@contextlib.contextmanager
def _question_lock(root: Path, q: Question) -> Iterator[None]:
    """質問の承認資料と提示した対象の排他。承認の対象を持たない質問は提示した対象を書かないので取らない。"""
    if q.target.binding() is None:
        yield
        return
    held = presented_path(root, q)
    held.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with FileLock(str(held) + ".lock", timeout=limits.seconds("locks.append")):
        yield


def _write_pair(root: Path, v: run.View, q: Question, private: bool) -> Path:
    """`write_material` の本体（承認資料と提示した対象の組を書く）。"""
    path = material_path(root, q)
    shown = presented_path(root, q)
    if presented_stale(root, q):
        path.unlink(missing_ok=True)
        shown.unlink(missing_ok=True)
    if not path.exists():
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with _private_temp(path.parent, material(v, q, private=private)) as tmp:
            with contextlib.suppress(FileExistsError):  # noqa: NDF402 ファイル: 同時に走った別の呼び出しが先に置いた資料を使う
                os.link(tmp, path)
    if q.target.binding() is not None and not shown.exists():
        issued = replace(q.target, material=None, presentation=uuid.uuid4().hex[:12])
        with _private_temp(shown.parent, json.dumps(issued.as_dict(), ensure_ascii=False) + "\n") as tmp:
            os.replace(tmp, shown)
    return path


@contextlib.contextmanager
def _private_temp(directory: Path, text: str) -> Iterator[str]:
    """`directory` に別名・排他的・0600 で作って `text` を書いた一時ファイル。抜けるときに残っていれば消す。"""
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        yield tmp
    finally:
        Path(tmp).unlink(missing_ok=True)


# 提示した承認の対象の保存先（承認資料の隣）
PRESENTED_SUFFIX = ".target.json"
# 承認資料の、本番へ反映され得ることを示す行（`ndf ci ask` の承認資料と同じ文）
PRODUCTION_LINE = merge_approval.PRODUCTION_MATERIAL_LINE


def _presented_target_moved(held: Target, now: Target) -> bool:
    """提示した承認の対象 `held` と今の対象 `now` の、承認の対象か「本番へ反映されるか」が違うか（両方がそろうときだけ比べる）。"""
    then, current = held.binding(), now.binding()
    if then is None or current is None:
        return False
    return not then.same(current) or held.production != now.production


def presented_stale(root: Path, q: Question) -> bool:
    """質問の資料と提示した対象を `q.target` で書き直す必要があるか（承認の対象を持たない質問は書き直さない）。

    提示した対象が無い（旧版で書いた資料・対象を書く前に落ちた資料は、何を示したかを確かめられない）か、提示した承認の
    対象（マージ先・HEAD）か「本番へ反映されるか」が今と違えば真（待ちの間に PR が動いた）。"""
    if q.target.binding() is None:
        return False
    return not presented_path(root, q).exists() or _presented_target_moved(read_presented(root, q), q.target)


def presented_path(root: Path, q: Question) -> Path:
    """質問で提示した承認の対象（`Target.as_dict` の形）の保存先。"""
    return run.work_root(root, q.machine_id) / MATERIAL_DIR / f"{q.id}{PRESENTED_SUFFIX}"


def read_presented(root: Path, q: Question) -> Target:
    """質問で提示した承認の対象。無い・読めなければ空の対象（旧版で出した質問）。"""
    try:
        return Target.from_dict(json.loads(presented_path(root, q).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        # 提示の記録の無い質問は、承認の対象の無い質問として扱う
        return Target()


# オーガナイザーのリリースの回答の語 → 承認ゲートの回答の語（`release.take_reply` と、中継を確かめる `answering` が共有する）
ORGANIZER_ANSWERS = {plan.RELEASED: run.APPROVED, plan.REJECTED: run.REJECTED}
# リクエストの中身の kind（計画のステートマシンのリクエスト。リクエストの ID の頭にも使う）
RELEASE_KIND = "lane-release"


def presentation_key(t: Target) -> str:
    """提示の識別子（`write_material` が資料を書くたびに発行した `presentation`）。承認の対象か識別子が無ければ空。

    値から作らないので、承認の対象が元へ戻って資料を出し直しても、前の提示と同じ値にならない。リリースのリクエストの
    ID とマージの承認の証跡の記録の識別子に入れ、出し直す前の提示への回答と証跡を、出し直した提示に使わない（#4409）。
    """
    if t.binding() is None or not t.presentation:
        return ""
    return t.presentation


def presented_id(q: Question) -> str:
    """質問 `q` の、提示した承認の対象（`q.target`）ごとの識別子。承認の対象が無ければ質問の id のまま。"""
    key = presentation_key(q.target)
    return f"{q.id}-{key}" if key else q.id


def request_id(q: Question) -> str:
    """リリースのリクエストの ID（質問の id と提示した承認の対象から作る。変更要求で流し直した質問と、待ちの間に
    資料を出し直した質問は別のリクエストになる）。`q.target` は提示した承認の対象（`read_presented`）を持たせて渡す。"""
    return f"{RELEASE_KIND}-{presented_id(q)}"


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
    # 承認を記録した時点のバージョンの保存先の種類（`none`・`versioned`・`unknown`）。回答が値を運ぶとき、出力の事象の
    # payload に `outputs` と並べて書く。後の工程へ渡す値でなく承認の時点の事実なので文脈へは書かない（#4513）
    release_store: Optional[str] = None
