"""並列の計画（#2436）: `plan.json` の pydantic のモデル・検査（I1〜I4・I19・I21）・`plan.md` の描画・引継ぎ文書の作成（I5）。

置き場はメインディレクトリの `.ndf/parallel/<計画名>/`（コミットしない）。検査の誤りは pydantic の検証の誤りとして
集め、誤りの種類（`PydanticCustomError` の type）の `I1`〜`I3`・`I19` を `items[].rule` に写す。上限の値は検証の
文脈（`context`）で渡す。触る場所の重なり（I4）は誤りにせず、検証の後に別に数える。

使う側は `deps.require("schema")` を先に呼ぶ。
"""

from __future__ import annotations

import fnmatch
import graphlib
import re
import shlex
from pathlib import Path
from typing import Annotated, Iterable, Literal, Mapping, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator, model_validator
from pydantic_core import PydanticCustomError

import parallel_bundles as pb
import parallel_hold
from parallel_files import PARALLEL_DIR, resolved_path  # noqa: F401  置き場の正本（問いの hook も読む）

VERSION = 1
PLAN_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")  # 計画名は 24 字まで（一時レーンの名前を 32 字に収める。I1）
LANE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")  # リレーハブの名前の形（relay_lib/common.py の NAME_RE と同じ）
HELPER_RE = re.compile(r"-rel\d+$")  # 一時レーンの名前の形（`<計画名>-rel<連番>`）
DEFAULT_MAX_PLANS = 6  # 同時に流すプランの合計の既定（正本は relay_limit.DEFAULT_PLANS）
DEFAULT_MAX_NAMED = 3  # relay_lib/hub.py の max_named の既定


def max_plans() -> tuple[int, Optional[str]]:
    """プランの本数の上限と、読めなかった出所の 1 文。マシンの数え方と同じ関数で読む（`relay_limit`。#2999 の I1）。"""
    import relay_limit

    n, _source, note = relay_limit.plans_cap()
    return n, note


def named_limit() -> tuple[int, Optional[str]]:
    """レーン数の上限と、読めなかった出所の 1 文。リレーハブと同じ関数で読む（`relay_limit`。#2733 の I12）。"""
    import relay_limit

    n, _source, note = relay_limit.named_cap()
    return n, note


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
    bundles: Optional[list[pb.Bundle]] = None  # 課題のまとまり（#3081）。省くと「まとまりの記録なし」。I1〜I3 は検証で見る
    check_bundles = model_validator(mode="after")(pb.checked_input)
    bundle_list = pb.input_bundle_list  # まとまりの一覧（I4 の 1 件のまとまりを補う）。欄が無ければ None


LaneInput = Annotated[Union[LoopInput, SprintInput], Field(discriminator="kind")]


class Lane(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    input: LaneInput
    plans: int = Field(ge=1)
    touches: list[str]
    after: list[str] = Field(default_factory=list)
    handoff: Optional[str] = None
    repo: Optional[str] = None  # レーンのリポジトリのメインディレクトリ（絶対パス）。省くと計画の置き場のリポジトリ（#2733 の I1）

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not LANE_RE.match(v):
            raise PydanticCustomError("I1", "レーン名の形が違う（{pattern}）: {name}", {"pattern": LANE_RE.pattern, "name": v})
        if HELPER_RE.search(v):
            raise PydanticCustomError("I1", "レーン名が一時レーンの名前の形（-rel と数字で終わる）に当たる: {name}", {"name": v})
        return v

    @field_validator("handoff")
    @classmethod
    def _handoff(cls, v: Optional[str]) -> Optional[str]:
        import handoff  # 引継ぎ文書の名の規則は handoff.py が持つ

        if v is not None and (not handoff.NAME_RE.match(v) or v.endswith(handoff.HISTORY_SUFFIX)):
            raise PydanticCustomError(
                "I5", "引継ぎ文書の名の形が違う（{pattern}）: {name}", {"pattern": handoff.NAME_RE.pattern, "name": v}
            )
        return v

    def repo_dir(self, home: Path) -> Path:
        """レーンのリポジトリ（`repo` を省けば計画の置き場 `home`）。"""
        return Path(self.repo) if self.repo else Path(home)

    def own_release(self, home: Path) -> bool:
        """別のリポジトリのレーンか（自分のリポジトリの設定で本番を出し、まとめたリリースに入らない。#2733 の I7）。"""
        return resolved_path(self.repo_dir(home)) != resolved_path(Path(home))

    def handoff_name(self) -> str:
        """引継ぎ文書の名（ループなら `loop-<ループ名>`、スプリントなら `issue-<最初の番号>`）。"""
        if self.handoff:
            return self.handoff
        if isinstance(self.input, LoopInput):
            return f"loop-{self.input.loop}"
        return f"issue-{self.input.issues[0]}"

    def start_command(self, plan: str) -> str:
        """レーンの開始のコマンド（設計の「レーンの最初の入力」の表）。1 スプリントのレーンの終わりは終わりの記録（#3058 の A1）。"""
        if isinstance(self.input, LoopInput):
            args = " ".join(self.input.args)
            mid = f" {args}" if args else ""
            return (
                f"/goal /ndf:development-workflow --loop {self.input.loop}{mid}"
                f" -- 終わり: [ndf-loop] stopped {self.input.loop} の行を出したとき"
            )
        issues, args = " ".join(f"#{n}" for n in self.input.issues), " ".join(self.input.args)
        end = f" -- 終わり: [ndf-parallel] lane-end {plan} {self.name} の行を出したとき"
        return f"/goal /ndf:development-workflow {issues}{(' ' + args) if args else ''}{end}"

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
        home = ctx.get("home")
        self._check_unique(home)
        self._check_limits(ctx)
        self._check_deps()
        overlap = _issue_overlap(self.lanes, home)
        if overlap:
            raise PydanticCustomError("I19", "レーンの間で課題かマイルストーンが重なる: {pairs}", {"pairs": overlap})
        return self

    def _check_unique(self, home: Optional[str]) -> None:
        """レーン名と引継ぎ文書の重なり（I1）。引継ぎ文書はレーンのリポジトリに置くため、同じリポジトリの間だけで見る（#2733 の I3）。"""
        dup = _dups([ln.name for ln in self.lanes])
        if dup:
            raise PydanticCustomError("I1", "レーン名が重なる: {names}", {"names": ", ".join(dup)})
        dup = _dups([f"{lane_repo_key(ln, home)}:{ln.handoff_name()}" for ln in self.lanes])
        dup = [d.rsplit(":", 1)[1] for d in dup]
        if dup:  # 同じ引継ぎ文書へ書くと、後のレーンの文書が先のレーンの文書を上書きする
            raise PydanticCustomError("I1", "レーンの引継ぎ文書が重なる: {names}", {"names": ", ".join(dup)})

    def _check_limits(self, ctx: dict) -> None:
        """レーン数とプラン本数の合計の上限（I2）。"""
        cap = int(ctx.get("max_named") or DEFAULT_MAX_NAMED)
        if len(self.lanes) > cap:
            raise PydanticCustomError("I2", "レーンの数 {n} が同時に動かせる数の上限 {cap} を超える", {"n": len(self.lanes), "cap": cap})
        total, limit = sum(ln.plans for ln in self.lanes), int(ctx.get("max_plans") or DEFAULT_MAX_PLANS)
        if total > limit:
            raise PydanticCustomError("I2", "プランの本数の合計 {n} が上限 {cap} を超える", {"n": total, "cap": limit})

    def _check_deps(self) -> None:
        """依存先の存在と循環（I3）。"""
        known = {ln.name for ln in self.lanes}
        for ln in self.lanes:
            missing = [a for a in ln.after if a not in known]
            if missing:
                raise PydanticCustomError("I3", "{lane} の依存先が計画に無い: {names}", {"lane": ln.name, "names": ", ".join(missing)})
        try:
            graphlib.TopologicalSorter({ln.name: set(ln.after) for ln in self.lanes}).prepare()
        except graphlib.CycleError as exc:
            cyc = " → ".join(exc.args[1]) if len(exc.args) > 1 else ""
            raise PydanticCustomError("I3", "依存が循環する: {cycle}", {"cycle": cyc}) from None

    def lane(self, name: str) -> Optional[Lane]:
        return next((ln for ln in self.lanes if ln.name == name), None)


def _dups(values: list[str]) -> list[str]:
    """2 回以上現れる値を整列して返す。"""
    return sorted({v for v in values if values.count(v) > 1})


def lane_repo_key(lane: Lane, home: Optional[str]) -> str:
    """重なりを比べる単位（レーンのリポジトリ）。置き場が分からないときは `repo` を省いたレーンを 1 つのリポジトリとみなす。"""
    if not lane.repo:
        return resolved_path(Path(home)) if home else ""
    return resolved_path(Path(lane.repo))


def _issue_overlap(lanes: list[Lane], home: Optional[str] = None) -> str:
    """課題とマイルストーンの重なり（I19）。`--milestones` の無いループのレーンはほかのレーンと重なるものとする。
    課題の番号はリポジトリごとに振られるため、同じリポジトリのレーンの間だけで見る（#2733 の I3）。"""
    pairs = []
    for i, a in enumerate(lanes):
        for b in lanes[i + 1 :]:
            if lane_repo_key(a, home) == lane_repo_key(b, home) and _overlaps(a, b):
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


def touch_overlaps(plan: ParallelPlan, home: Optional[str] = None) -> list[dict]:
    """触る場所が重なるレーンの組（I4。警告）。片方が他方の接頭辞か、glob が他方に当たれば重なる。同じリポジトリの間だけで見る。"""
    out = []
    for i, a in enumerate(plan.lanes):
        for b in plan.lanes[i + 1 :]:
            if lane_repo_key(a, home) != lane_repo_key(b, home):
                continue
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


def repo_errors(lane: Lane) -> Optional[str]:
    """レーンの `repo` の形の誤り（I20）。絶対パスで、Git のメインディレクトリそのものを指さなければ理由を返す。"""
    if not lane.repo:
        return None
    p = Path(lane.repo)
    if not p.is_absolute():
        return f"{lane.name} の repo が絶対パスでない: {lane.repo}"
    import decl

    main = decl.main_dir(str(p)) if p.is_dir() else None
    if main is None or resolved_path(Path(main)) != resolved_path(p):
        return f"{lane.name} の repo が Git のメインディレクトリでない（worktree・存在しないパスは置けない）: {lane.repo}"
    return None


def check_data(
    data: object,
    home: Optional[Path] = None,
    estimates: Optional[Mapping[str, Mapping[int, list[str]]]] = None,
    shared: Iterable[Mapping] | Mapping[str, list] = (),  # レーン → 共有の一覧（`touch_estimate.lane_estimates`）か 1 つの一覧
    estimate_error: Optional[str] = None,
) -> tuple[Optional[ParallelPlan], list[dict], dict]:
    """下書きか `plan.json` の中身を検査する。(計画か None, items, metrics) を返す。items の `level` は error / warning。
    `home`（計画の置き場）を渡すと、`repo` を省いたレーンと同じ置き場を指すレーンを同じリポジトリとして重なりを見る。
    `estimates`（レーン → 課題 → 触る場所の見込み）を渡すと、同じ時間帯に動くレーンの課題の見込みの重なりを I21 の警告にする。
    見込みを集められなかったときは `estimate_error` に理由を渡し、I21 の警告を 1 つ出す（#2997 の決定 4）。"""
    plans_cap, plans_note = max_plans()
    named_cap, named_note = named_limit()
    caps = {"max_plans": plans_cap, "max_named": named_cap}
    notes = [n for n in (plans_note, named_note) if n]
    ctx = {**caps, "home": str(home) if home else None}
    try:
        plan = ParallelPlan.model_validate(data, context=ctx)
    except ValidationError as exc:
        items = _validation_items(exc)
        return None, items, _metrics_of(items, caps, notes)
    items = _repo_items(plan)
    if items:
        return None, items, _metrics_of(items, caps, notes)  # I20 の誤りがある計画は書かない・起動しない
    items = _overlap_items(plan, home)
    if estimate_error:
        items.append({"level": "warning", "rule": "I21", "at": "lanes", "message": f"課題の見込みを集められない: {estimate_error}"})
    if estimates:
        import touch_estimate as te

        key = str(home) if home else None
        same = lambda a, b: lane_repo_key(a, key) == lane_repo_key(b, key)  # noqa: E731
        items += [{"level": "warning", "rule": "I21", "at": "lanes", **it} for it in te.lane_overlaps(plan.lanes, estimates, shared, same)]
        items += te.bundle_overlaps(plan.lanes, estimates, shared)  # 同じスプリントの異なるまとまりの重なり（#3081 の I5a）
    return plan, items, _metrics_of(items, caps, notes)


def _validation_items(exc: ValidationError) -> list[dict]:
    """Pydantic の検証の誤りを公開の items（level=error）へ変える。不変条件の番号でない型は `form` とする。"""
    items = []
    for e in exc.errors():
        rule = e["type"] if re.fullmatch(r"I\d+|bundle", e["type"]) else "form"
        loc = ".".join(str(p) for p in e["loc"])
        items.append({"level": "error", "rule": rule, "at": loc or None, "message": e["msg"]})
    return items


def _repo_items(plan: ParallelPlan) -> list[dict]:
    """レーンの `repo` の誤り（I20）を items（level=error）として返す。"""
    return [
        {"level": "error", "rule": "I20", "at": f"lanes.{i}.repo", "message": err}
        for i, ln in enumerate(plan.lanes)
        if (err := repo_errors(ln))
    ]


def _overlap_items(plan: ParallelPlan, home: Optional[Path]) -> list[dict]:
    """触る場所の重なり（I4）を items（level=warning）として返す。"""
    return [
        {
            "level": "warning",
            "rule": "I4",
            "at": "lanes",
            "message": f"触る場所が重なる: {'・'.join(ov['lanes'])}（{', '.join(ov['touches'])}）",
            **ov,
        }
        for ov in touch_overlaps(plan, str(home) if home else None)
    ]


def _metrics_of(items: list[dict], caps: dict, notes: list[str]) -> dict:
    """検査の集計。誤りと警告の数・上限・上限の注記を返す。"""
    return {
        "errors": sum(1 for it in items if it["level"] == "error"),
        "warnings": sum(1 for it in items if it["level"] == "warning"),
        "max_plans": caps["max_plans"],
        "max_named": caps["max_named"],
        "notes": notes,
    }


# ---- 置き場と描画 ----


def plan_dir(main: Path, name: str) -> Path:
    return Path(main) / PARALLEL_DIR / name


def load_plan(main: Path, name: str) -> object:
    """`plan.json` の中身（JSON の値）。無ければ FileNotFoundError、読めなければ ValueError。"""
    import json

    return json.loads((plan_dir(main, name) / "plan.json").read_text(encoding="utf-8"))


# 上限を外して読む（上限は計画を作るときに見る。読むときは書かれた計画をそのまま受ける）
_NO_LIMIT = {"max_plans": 10**6, "max_named": 10**6}


def load_valid_plan(main: Path, name: str) -> ParallelPlan:
    """`plan.json` を上限を外して検証した計画。例外は `load_plan` と検証のものをそのまま投げる。"""
    return ParallelPlan.model_validate(load_plan(main, name), context=_NO_LIMIT)


def waiting_text(w: Optional[dict]) -> Optional[str]:
    """起動の待ち（`parallel_machine.admit_lanes` が返す）の 1 句（`plan.md` の進行の表の「起動」の欄）。待っていなければ None。"""
    if not isinstance(w, dict):
        return None
    if w.get("reason") == "lock":
        return "起動待ち（起動の排他を取れない）"
    if w.get("reason") == "observe":
        return "起動待ち（レーンの状態の一覧を読めない）"
    return f"起動待ち（マシン {w.get('machine_plans')} + このレーン {w.get('plans')} > 上限 {w.get('max_plans')}）"


def render_md(plan: ParallelPlan, progress: Optional[dict] = None) -> str:
    """`plan.md`: レーンの表・決まり・（進行があれば）レーンの状態とリリースの内訳。"""
    lines = [f"# 並列の計画 {plan.name}", "", "`plan.json` から描画した。正本は `plan.json` である。", "", "## レーン", ""]
    lines += [
        "| レーン | リポジトリ | 最初の入力 | プランの本数 | 触る場所 | 依存先 | 引継ぎ文書 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for ln in plan.lanes:
        first = (
            f"ループ `{ln.input.loop}`" if isinstance(ln.input, LoopInput) else "スプリント " + " ".join(f"#{n}" for n in ln.input.issues)
        )
        if ln.input.args:
            first += " `" + " ".join(ln.input.args) + "`"
        lines.append(
            f"| {ln.name} | {f'`{ln.repo}`' if ln.repo else '計画の置き場'} | {first} | {ln.plans} | {', '.join(f'`{t}`' for t in ln.touches) or '-'} | {', '.join(ln.after) or '-'} | `.ndf/handoff/{ln.handoff_name()}.md` |"
        )
    lines += [*pb.render_rows(plan.lanes), "", "## レーンに共通の決まり", ""]
    lines += [
        f"- 同時に流すプランの本数はレーンごとに上の表のとおり（合計 {sum(ln.plans for ln in plan.lanes)} 本）",
        "- 触る場所は上の表のとおり。取り込みの衝突は base-sync で解く",
        f"- リリースの雛形で出すリポジトリでは、オーガナイザーがリリースを 1 本にまとめる。レーンは本番を自分で出さず、リリースの依頼を送る（`parallel.py request {plan.name}`）",
        "- リリースの Pull Request で出すリポジトリでは、レーンは本番チャネルへ入れるリリースプランをそのまま流す。組み上がったプランを手で置き換えない",
        "- 承認ゲートと区分つきの問いはオーガナイザーだけが出す。レーンは問いで止まらず、別の作業へ進む",
    ]
    if any(ln.repo for ln in plan.lanes):
        lines.append(
            "- 別のリポジトリのレーンは、リリースの依頼を送らず自分のリポジトリの設定で本番を出す。リリース承認ゲートの問いはオーガナイザーへ送る"
        )
    lines += [f"- {r}" for r in plan.rules]
    if progress:
        lines += ["", "## 進行", "", "| レーン | 状態 | 起動 |", "| --- | --- | --- |"]
        for n, st in sorted((progress.get("lanes") or {}).items()):
            launch = st.get("started") or waiting_text(st.get("waiting")) or st.get("refused") or "-"
            lines.append(f"| {n} | {st.get('state') or '-'} | {launch} |")
        rels = progress.get("releases") or []
        if rels:
            lines += ["", "## まとめたリリース", "", "| 連番 | 状態 | 版 | コミット |", "| --- | --- | --- | --- |"]
            for r in rels:
                lines.append(f"| {r.get('n')} | {r.get('state')} | {r.get('version') or '-'} | {(r.get('sha') or '-')[:12]} |")
        lines += parallel_hold.render_hold(progress.get("hold"))
    return "\n".join(lines) + "\n"


def render_handoff(plan: ParallelPlan, lane: Lane, plan_md: Path) -> str:
    """レーンの引継ぎ文書。決まりは写さず、並列の計画を参照する（受け入れ条件 2）。"""
    cmd = lane.start_command(plan.name)
    return "\n".join(
        [
            f"# 引継ぎ: 並列の計画 {plan.name} のレーン {lane.name}",
            "",
            "## 進め方（利用者の指示）",
            "",
            f"- 並列の計画 `{plan.name}` のレーン `{lane.name}` として進める。共通の決まりは [plan.md]({plan_md}) にあり、ここへは写さない",
            "- リリースの雛形で出すリポジトリでは、リリースを自分で出さずオーガナイザーへリリースの依頼を送る。リリースの Pull Request で出すリポジトリでは本番チャネルへ入れるリリースプランをそのまま流す。組み上がったプランを手で置き換えない",
            "- 承認ゲートと区分つきの問いはオーガナイザーへ送り、待たずに別の作業へ進む",
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
    """`plan.json`・`plan.md`・引継ぎ文書を書く。在る引継ぎ文書・在る計画があれば 1 つも書かずに FileExistsError（I5）。

    引継ぎ文書の置き場と書き込みは handoff.py（`paths_of`・`write_doc`）に任せる。"""
    import json

    import handoff

    d = plan_dir(main, plan.name)
    # 引継ぎ文書はレーンのリポジトリに置く（別のリポジトリのレーンは自分のリポジトリの引継ぎ文書を読む。#2733）
    targets = {ln.name: handoff.paths_of(ln.repo_dir(main), ln.handoff_name())[0] for ln in plan.lanes}
    existing = [str(p) for p in [d / "plan.json", *targets.values()] if p.exists()]
    if existing:
        raise FileExistsError(", ".join(existing))
    d.mkdir(parents=True, exist_ok=True)
    import parallel_homes  # 別のリポジトリのレーンと計画の外の判定が読む計画の所在の一覧（#2733 の決定 1）

    parallel_homes.record_home(main)
    data = dict(raw)
    data["lanes"] = [{**dict(r), "handoff": ln.handoff_name()} for r, ln in zip(raw["lanes"], plan.lanes)]
    (d / "plan.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (d / "plan.md").write_text(render_md(plan), encoding="utf-8")
    for ln in plan.lanes:
        handoff.write_doc(targets[ln.name], render_handoff(plan, ln, d / "plan.md"))
    return [d / "plan.json", d / "plan.md", *targets.values()]


def manual_commands(plan: ParallelPlan, home: Path) -> list[str]:
    """ラッパーの外・tmux が無いときの、レーンごとの手で起動する 1 行（受け入れ条件 9）。レーンのリポジトリ（`home` は計画の置き場）へ
    移ってから起動する（引継ぎ文書はレーンのリポジトリにある。耐久ステップ「起動」の cwd と同じ。#2733）。"""
    return [f"cd {shlex.quote(str(ln.repo_dir(home)))} && NDF_RELAY_NAME={ln.name} claude '{first_input(ln)}'" for ln in plan.lanes]


def first_input(lane: Lane) -> str:
    """耐久ステップ「起動」がレーンへ渡す最初の入力。"""
    return f"/goal /ndf:development-workflow .ndf/handoff/{lane.handoff_name()}.md の続きから"


def ready_lanes(plan: ParallelPlan, started: set[str], stopped: set[str]) -> list[Lane]:
    """起動の対象: 起動に成功していないレーンのうち、依存先がすべて止まったもの（I7・I8）。"""
    return [ln for ln in plan.lanes if ln.name not in started and all(a in stopped for a in ln.after)]
