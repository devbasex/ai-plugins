"""並列の計画（#2436）: `plan.json` の pydantic のモデル・検査（I1〜I4・I19）・`plan.md` の描画・引継ぎ文書の作成（I5）。

置き場はメインディレクトリの `.ndf/parallel/<計画名>/`（コミットしない）。検査の誤りは pydantic の検証の誤りとして
集め、誤りの種類（`PydanticCustomError` の type）の `I1`〜`I3`・`I19` を `items[].rule` に写す。上限の値は検証の
文脈（`context`）で渡す。触る場所の重なり（I4）は誤りにせず、検証の後に別に数える。

使う側は `deps.require("schema")` を先に呼ぶ。
"""

from __future__ import annotations

import fnmatch
import graphlib
import os
import re
from pathlib import Path
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator, model_validator
from pydantic_core import PydanticCustomError

VERSION = 1
PLAN_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")  # 計画名は 24 字まで（一時レーンの名前を 32 字に収める。I1）
LANE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")  # 統括役の名前の形（relay_lib/common.py の NAME_RE と同じ）
HELPER_RE = re.compile(r"-rel\d+$")  # 一時レーンの名前の形（`<計画名>-rel<連番>`）
DEFAULT_MAX_PLANS = 6  # 同時に流すプランの合計の既定（2026-09-25 の実測。8 コア・30 GB）
DEFAULT_MAX_NAMED = 3  # relay_lib/hub.py の max_named の既定
PARALLEL_DIR = Path(".ndf") / "parallel"


def _positive_env(name: str, default: int) -> tuple[int, Optional[str]]:
    """環境変数の正の整数。正の整数でなければ既定と、そのことを書いた 1 文を返す（I2）。"""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default, None
    try:
        v = int(raw)
    except ValueError:
        v = 0
    if v < 1:
        return default, f"{name}={raw!r} は正の整数でないため既定の {default} を使った"
    return v, None


def max_plans() -> tuple[int, Optional[str]]:
    return _positive_env("NDF_PARALLEL_MAX_PLANS", DEFAULT_MAX_PLANS)


def max_named() -> tuple[int, Optional[str]]:
    return _positive_env("NDF_RELAY_MAX_NAMED", DEFAULT_MAX_NAMED)


class LoopInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["loop"]
    loop: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)


class SprintInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["sprint"]
    issues: list[int] = Field(min_length=1)
    args: list[str] = Field(default_factory=list)


LaneInput = Annotated[Union[LoopInput, SprintInput], Field(discriminator="kind")]


class Lane(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    input: LaneInput
    plans: int = Field(ge=1)
    touches: list[str]
    after: list[str] = Field(default_factory=list)
    handoff: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not LANE_RE.match(v):
            raise PydanticCustomError("I1", "レーン名の形が違う（{pattern}）: {name}", {"pattern": LANE_RE.pattern, "name": v})
        if HELPER_RE.search(v):
            raise PydanticCustomError("I1", "レーン名が一時レーンの名前の形（-rel と数字で終わる）に当たる: {name}", {"name": v})
        return v

    def handoff_name(self) -> str:
        """引継ぎ文書の名（ループなら `loop-<ループ名>`、スプリントなら `issue-<最初の番号>`）。"""
        if self.handoff:
            return self.handoff
        if isinstance(self.input, LoopInput):
            return f"loop-{self.input.loop}"
        return f"issue-{self.input.issues[0]}"

    def start_command(self) -> str:
        """レーンの開始のコマンド（設計の「レーンの最初の入力」の表）。"""
        if isinstance(self.input, LoopInput):
            args = " ".join(self.input.args)
            mid = f" {args}" if args else ""
            return (
                f"/goal /ndf:development-workflow --loop {self.input.loop}{mid}"
                f" -- 終わり: [ndf-loop] stopped {self.input.loop} の行を出したとき"
            )
        issues = " ".join(f"#{n}" for n in self.input.issues)
        args = " ".join(self.input.args)
        return f"/goal /ndf:development-workflow {issues}{(' ' + args) if args else ''}"

    def milestones(self) -> Optional[set[str]]:
        """ループの `--milestones` の値（無ければ None。I19 の「ほかと重なる」）。"""
        args = self.input.args
        for i, a in enumerate(args):
            if a.startswith("--milestones="):
                return {m for m in a.split("=", 1)[1].split(",") if m}
            if a == "--milestones" and i + 1 < len(args):
                return {m for m in args[i + 1].split(",") if m}
        return None


class ParallelPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1]
    name: str
    rules: list[str]
    lanes: list[Lane] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not PLAN_RE.match(v):
            raise PydanticCustomError("I1", "計画名の形が違う（{pattern}）: {name}", {"pattern": PLAN_RE.pattern, "name": v})
        return v

    @model_validator(mode="after")
    def _whole(self, info: ValidationInfo) -> "ParallelPlan":
        ctx = info.context or {}
        names = [ln.name for ln in self.lanes]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise PydanticCustomError("I1", "レーン名が重なる: {names}", {"names": ", ".join(dup)})
        cap = int(ctx.get("max_named") or DEFAULT_MAX_NAMED)
        if len(self.lanes) > cap:
            raise PydanticCustomError("I2", "レーンの数 {n} が同時に動かせる数の上限 {cap} を超える", {"n": len(self.lanes), "cap": cap})
        total, limit = sum(ln.plans for ln in self.lanes), int(ctx.get("max_plans") or DEFAULT_MAX_PLANS)
        if total > limit:
            raise PydanticCustomError("I2", "プランの本数の合計 {n} が上限 {cap} を超える", {"n": total, "cap": limit})
        known = set(names)
        for ln in self.lanes:
            missing = [a for a in ln.after if a not in known]
            if missing:
                raise PydanticCustomError("I3", "{lane} の依存先が計画に無い: {names}", {"lane": ln.name, "names": ", ".join(missing)})
        try:
            graphlib.TopologicalSorter({ln.name: set(ln.after) for ln in self.lanes}).prepare()
        except graphlib.CycleError as exc:
            cyc = " → ".join(exc.args[1]) if len(exc.args) > 1 else ""
            raise PydanticCustomError("I3", "依存が循環する: {cycle}", {"cycle": cyc}) from None
        overlap = _issue_overlap(self.lanes)
        if overlap:
            raise PydanticCustomError("I19", "レーンの間で課題かマイルストーンが重なる: {pairs}", {"pairs": overlap})
        return self

    def lane(self, name: str) -> Optional[Lane]:
        return next((ln for ln in self.lanes if ln.name == name), None)


def _issue_overlap(lanes: list[Lane]) -> str:
    """課題とマイルストーンの重なり（I19）。`--milestones` の無いループのレーンはほかのレーンと重なるものとする。"""
    pairs = []
    for i, a in enumerate(lanes):
        for b in lanes[i + 1 :]:
            if _overlaps(a, b):
                pairs.append(f"{a.name}・{b.name}")
    return ", ".join(pairs)


def _overlaps(a: Lane, b: Lane) -> bool:
    la, lb = isinstance(a.input, LoopInput), isinstance(b.input, LoopInput)
    if not la and not lb:
        return bool(set(a.input.issues) & set(b.input.issues))
    ma, mb = (a.milestones() if la else None), (b.milestones() if lb else None)
    if (la and ma is None) or (lb and mb is None):
        return True
    if la and lb:
        return bool(ma & mb)
    return False  # ループのマイルストーンとスプリントの課題の照合は課題の取得が要るため検査では見ない


def touch_overlaps(plan: ParallelPlan) -> list[dict]:
    """触る場所が重なるレーンの組（I4。警告）。片方が他方の接頭辞か、glob が他方に当たれば重なる。"""
    out = []
    for i, a in enumerate(plan.lanes):
        for b in plan.lanes[i + 1 :]:
            hits = sorted({f"{x}・{y}" for x in a.touches for y in b.touches if _touch_hit(x, y)})
            if hits:
                out.append({"lanes": [a.name, b.name], "touches": hits})
    return out


def _touch_hit(x: str, y: str) -> bool:
    px, py = x.rstrip("/"), y.rstrip("/")
    if px == py or px.startswith(py + "/") or py.startswith(px + "/"):
        return True
    return fnmatch.fnmatch(px, py) or fnmatch.fnmatch(py, px)


# ---- 検査 ----


def check_data(data: object) -> tuple[Optional[ParallelPlan], list[dict], dict]:
    """下書きか `plan.json` の中身を検査する。(計画か None, items, metrics) を返す。items の `level` は error / warning。"""
    plans_cap, plans_note = max_plans()
    named_cap, named_note = max_named()
    items: list[dict] = []
    notes = [n for n in (plans_note, named_note) if n]
    try:
        plan = ParallelPlan.model_validate(data, context={"max_plans": plans_cap, "max_named": named_cap})
    except ValidationError as exc:
        plan = None
        for e in exc.errors():
            rule = e["type"] if re.fullmatch(r"I\d+", e["type"]) else "form"
            loc = ".".join(str(p) for p in e["loc"])
            items.append({"level": "error", "rule": rule, "at": loc or None, "message": e["msg"]})
    if plan is not None:
        for ov in touch_overlaps(plan):
            items.append(
                {
                    "level": "warning",
                    "rule": "I4",
                    "at": "lanes",
                    "message": f"触る場所が重なる: {'・'.join(ov['lanes'])}（{', '.join(ov['touches'])}）",
                    **ov,
                }
            )
    metrics = {
        "errors": sum(1 for it in items if it["level"] == "error"),
        "warnings": sum(1 for it in items if it["level"] == "warning"),
        "max_plans": plans_cap,
        "max_named": named_cap,
        "notes": notes,
    }
    return plan, items, metrics


# ---- 置き場と描画 ----


def plan_dir(main: Path, name: str) -> Path:
    return Path(main) / PARALLEL_DIR / name


def load(main: Path, name: str) -> object:
    """`plan.json` の中身（JSON の値）。無ければ FileNotFoundError、読めなければ ValueError。"""
    import json

    return json.loads((plan_dir(main, name) / "plan.json").read_text(encoding="utf-8"))


def render_md(plan: ParallelPlan, progress: Optional[dict] = None) -> str:
    """`plan.md`: レーンの表・決まり・（進行があれば）レーンの状態と配布の内訳。"""
    lines = [f"# 並列の計画 {plan.name}", "", "`plan.json` から描画した。正本は `plan.json` である。", "", "## レーン", ""]
    lines += ["| レーン | 最初の入力 | プランの本数 | 触る場所 | 依存先 | 引継ぎ文書 |", "| --- | --- | --- | --- | --- | --- |"]
    for ln in plan.lanes:
        first = (
            f"ループ `{ln.input.loop}`" if isinstance(ln.input, LoopInput) else "スプリント " + " ".join(f"#{n}" for n in ln.input.issues)
        )
        if ln.input.args:
            first += " `" + " ".join(ln.input.args) + "`"
        lines.append(
            f"| {ln.name} | {first} | {ln.plans} | {', '.join(f'`{t}`' for t in ln.touches) or '-'} | {', '.join(ln.after) or '-'} | `.ndf/handoff/{ln.handoff_name()}.md` |"
        )
    lines += ["", "## レーンに共通の決まり", ""]
    lines += [
        f"- 同時に流すプランの本数はレーンごとに上の表のとおり（合計 {sum(ln.plans for ln in plan.lanes)} 本）",
        "- 触る場所は上の表のとおり。取り込みの衝突は base-sync で解く",
        f"- 本番の配布はオーガナイザーが 1 本にまとめる。レーンは本番を自分で出さず、配布の依頼を送る（`parallel.py request {plan.name}`）",
        "- 承認ゲートと区分つきの問いはオーガナイザーだけが出す。レーンは問いで止まらず、別の作業へ進む",
    ]
    lines += [f"- {r}" for r in plan.rules]
    if progress:
        lines += ["", "## 進行", "", "| レーン | 状態 | 起動 |", "| --- | --- | --- |"]
        for n, st in sorted((progress.get("lanes") or {}).items()):
            lines.append(f"| {n} | {st.get('state') or '-'} | {st.get('started') or st.get('refused') or '-'} |")
        rels = progress.get("releases") or []
        if rels:
            lines += ["", "## まとめた配布", "", "| 連番 | 状態 | 版 | コミット |", "| --- | --- | --- | --- |"]
            for r in rels:
                lines.append(f"| {r.get('seq')} | {r.get('state')} | {r.get('version') or '-'} | {(r.get('sha') or '-')[:12]} |")
    return "\n".join(lines) + "\n"


def render_handoff(plan: ParallelPlan, lane: Lane, plan_md: Path) -> str:
    """レーンの引継ぎ文書。決まりは写さず、並列の計画を参照する（受け入れ条件 2）。"""
    cmd = lane.start_command()
    return "\n".join(
        [
            f"# 引継ぎ: 並列の計画 {plan.name} のレーン {lane.name}",
            "",
            "## 進め方（利用者の指示）",
            "",
            f"- 並列の計画 `{plan.name}` のレーン `{lane.name}` として進める。共通の決まりは [plan.md]({plan_md}) にあり、ここへは写さない",
            "- 本番の配布は自分で出さず、オーガナイザーへ配布の依頼を送る。承認ゲートと区分つきの問いはオーガナイザーへ送り、待たずに別の作業へ進む",
            "",
            "## 次にやること",
            "",
            f"1. `{cmd}` で始める",
            "",
            "## 次に実行するコマンド",
            "",
            "```text",
            cmd,
            "```",
            "",
        ]
    )


def write_all(main: Path, plan: ParallelPlan, raw: dict) -> list[Path]:
    """`plan.json`・`plan.md`・引継ぎ文書を書く。在る引継ぎ文書・在る計画があれば 1 つも書かずに FileExistsError（I5）。"""
    import json

    d = plan_dir(main, plan.name)
    handoffs = Path(main) / ".ndf" / "handoff"
    targets = {ln.name: handoffs / f"{ln.handoff_name()}.md" for ln in plan.lanes}
    existing = [str(p) for p in [d / "plan.json", *targets.values()] if p.exists()]
    if existing:
        raise FileExistsError(", ".join(existing))
    d.mkdir(parents=True, exist_ok=True)
    handoffs.mkdir(parents=True, exist_ok=True)
    data = dict(raw)
    data["lanes"] = [{**dict(r), "handoff": ln.handoff_name()} for r, ln in zip(raw["lanes"], plan.lanes)]
    written = [d / "plan.json", d / "plan.md"]
    (d / "plan.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (d / "plan.md").write_text(render_md(plan), encoding="utf-8")
    for ln in plan.lanes:
        targets[ln.name].write_text(render_handoff(plan, ln, d / "plan.md"), encoding="utf-8")
        written.append(targets[ln.name])
    return written


def manual_commands(plan: ParallelPlan) -> list[str]:
    """ラッパーの外・tmux が無いときの、レーンごとの手で起動する 1 行（受け入れ条件 9）。"""
    return [f"NDF_RELAY_NAME={ln.name} claude '{first_input(ln)}'" for ln in plan.lanes]


def first_input(lane: Lane) -> str:
    """耐久ステップ「起動」がレーンへ渡す最初の入力。"""
    return f"/goal /ndf:development-workflow .ndf/handoff/{lane.handoff_name()}.md の続きから"


def ready_lanes(plan: ParallelPlan, started: set[str], stopped: set[str]) -> list[Lane]:
    """起動の対象: 起動に成功していないレーンのうち、依存先がすべて止まったもの（I7・I8）。"""
    return [ln for ln in plan.lanes if ln.name not in started and all(a in stopped for a in ln.after)]
