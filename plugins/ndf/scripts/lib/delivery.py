"""配布の宣言から、マージの宛先の判定とリリースの経路を決める（#1336）。

宣言は `.ndf/worktree.json`（ベースブランチ・本番チャネル）・`.ndf/project.json` の `delivery`・`.ndf/supervise.json` の
`release` の 3 つ。ファイルと git の参照（既定ブランチ）だけを読み、`gh` を呼ばない。判定は保存せず、呼ぶたびに作り直す。

- `judge_target`: 宛先のブランチへのマージが自動反映の本番チャネルへ入るか（`production` / `not-production` /
  `undetermined`）。決められないときは `undetermined` にし、止める側へ倒す
- `routes`: 変更が本番系へ届く道筋（`template` / `merge` / `manual` / `none`）と、それを届けるステージ
- `dev_channel`: 開発版のチャネルの形（`separate-branch` / `manual-production` / 無し）。`pace: fast` / `auto` の使ってよい
  条件とスプリントのステージの組み立てが読む（#1454）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import project_decl
import repo

PRODUCTION, NOT_PRODUCTION, UNDETERMINED = "production", "not-production", "undetermined"
STOP_VERDICTS = (PRODUCTION, UNDETERMINED)  # 承認の無いマージを止める判定

# 経路（Route.route）と、それを届けるステージ（Route.stage）
TEMPLATE, MERGE, MANUAL, NONE = "template", "merge", "manual", "none"
STAGE_TEMPLATE = "template"  # release.form の雛形（開発版 → 本番）
STAGE_MERGED_BY_CHECK = "merged-by-check"  # ベースブランチへのマージで届く（ステージを置かない）
STAGE_PROMOTE = "promote"  # 昇格の Pull Request（ベースブランチ → 本番チャネル）
STAGE_MANUAL = "manual"  # 手で行うステージ（/ndf:release）
STAGE_NONE = "none"  # 届けない（ステージを置かない）

# 開発版のチャネルの形（DevChannel.value）
SEPARATE_BRANCH = "separate-branch"  # 起点と本番チャネルが違う
MANUAL_PRODUCTION = "manual-production"  # 起点と本番チャネルが同じで、本番系へ届く行がすべて手動（手動反映の本番系）

WT = f".ndf/{repo.WORKTREE_DECL.name}"
PJ = str(project_decl.DECL)
SV = ".ndf/supervise.json"


@dataclass
class DeliveryDecl:
    """判定に使う宣言の値。`rows` は `delivery` が並びなら並び、不明か無ければ `None`（`[]` は届けない）。"""

    base: str | None = None
    production: str | None = None
    rows: list[dict] | None = None
    release: dict | None = None
    problems: list[str] = field(default_factory=list)
    sources: dict = field(default_factory=dict)
    unknown: str | None = None  # delivery が {"unknown": ...} のときの理由


@dataclass
class Verdict:
    target: str
    value: str
    reason: str
    items: list[dict] = field(default_factory=list)  # 判定に使った宣言（kind: decl の行）

    @property
    def stops(self) -> bool:
        return self.value in STOP_VERDICTS


@dataclass
class DevChannel:
    value: str | None
    reason: str = ""  # value が None のときの断る理由

    @property
    def ok(self) -> bool:
        return self.value is not None


@dataclass
class Route:
    route: str
    target: str
    branch: str | None
    stage: str
    note: str | None = None
    production: bool | None = None  # delivery の行の production（無ければ None）

    def as_dict(self) -> dict:
        d = {"route": self.route, "target": self.target, "branch": self.branch, "stage": self.stage, "note": self.note}
        if self.production is not None:
            d["production"] = self.production
        return d


def _rows_of(project: dict, problems: list[str]) -> tuple[list[dict] | None, str | None]:
    if "delivery" not in project or project.get("delivery") is None:
        return None, None
    v = project["delivery"]
    if isinstance(v, dict) and isinstance(v.get("unknown"), str):
        return None, v["unknown"]
    if not isinstance(v, list):
        problems.append(f'{PJ} の delivery: 並びでも {{"unknown": ...}} でもない')
        return None, None
    for i, row in enumerate(v):
        if not isinstance(row, dict) or row.get("kind") not in ("auto", "manual"):
            problems.append(f"{PJ} の delivery[{i}]: kind が auto / manual のオブジェクトでない")
            return None, None
    return v, None


def build(
    wt: dict,
    project: dict,
    release=None,
    *,
    default_branch: str | None = None,
    problems: list[str] | None = None,
    base: str | None = None,
    production: str | None = None,
) -> DeliveryDecl:
    """読んだ宣言の中身から組む。`base` / `production` を渡せばそれを先に使う（引数が宣言より先に効く）。"""
    problems = list(problems or [])
    sources = {}
    wt_base = wt.get("base_branch") if isinstance(wt.get("base_branch"), str) and wt.get("base_branch") else None
    wt_prod = wt.get("production_branch") if isinstance(wt.get("production_branch"), str) and wt.get("production_branch") else None
    b = base or wt_base or default_branch
    sources["base"] = "引数" if base else (f"{WT} の base_branch" if wt_base else "既定ブランチ")
    p = production or wt_prod or default_branch
    sources["production"] = "引数" if production else (f"{WT} の production_branch" if wt_prod else "既定ブランチ（origin/HEAD）")
    rows, unknown = _rows_of(project, problems)
    rel = release if isinstance(release, dict) else None
    return DeliveryDecl(b, p, rows, rel, problems, sources, unknown)


def _read_supervise(f: Path) -> tuple[dict, str | None]:
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {}, f"{SV}: JSON として読めない（{e}）"
    return (data, None) if isinstance(data, dict) else ({}, f"{SV}: 最上位がオブジェクトでない")


def load_delivery(root) -> DeliveryDecl:
    """`root` のリポジトリの宣言を読む。例外を上げず、壊れた宣言は `problems` に書く。"""
    problems = []
    wt, why = repo.read_worktree_decl(root)
    if why:
        problems.append(why)
    project, why = project_decl.read_project_decl_checked(root)
    if why:
        problems.append(why)
    sv = {}
    main = repo.main_dir(root)
    for base in dict.fromkeys(p for p in (Path(root), main) if p):
        f = Path(base) / SV
        if f.is_file():
            sv, why = _read_supervise(f)
            if why:
                problems.append(why)
            break
    return build(wt, project, sv.get("release"), default_branch=repo.default_branch(root), problems=problems)


def _row_text(row: dict) -> str:
    return " ".join(str(x) for x in (row.get("kind"), row.get("branch") or "（ブランチ無し）", row.get("target") or "") if x)


def reaches_by_merge(row: dict, production: str | None) -> bool:
    """行が本番チャネルへのマージで自動で本番系へ届くか。`production: false`（検証の環境）の行は数えない。"""
    return row.get("kind") == "auto" and bool(production) and row.get("branch") == production and row.get("production") is not False


def dev_channel(decl: DeliveryDecl) -> DevChannel:
    """開発版のチャネルの形。上から順に最初に当たった条件で決まる。起点と本番チャネルが違えば delivery を読まない。"""
    none = "開発版のチャネルが無い"
    if not decl.production:
        return DevChannel(None, f"{none}（本番のブランチが分からない）")
    if decl.production != decl.base:
        return DevChannel(SEPARATE_BRANCH)
    if decl.problems or decl.rows is None:
        why = "読めない" if decl.problems else "不明" if decl.unknown else "無い"
        return DevChannel(None, f"{none}（起点と本番のブランチが同じで、{PJ} の delivery が{why}）")
    for i, row in enumerate(decl.rows):
        if reaches_by_merge(row, decl.production):
            return DevChannel(None, f"{none}（delivery[{i}] が本番チャネル {decl.production} へのマージで自動で本番系へ届く）")
    prod = [(i, r) for i, r in enumerate(decl.rows) if r.get("production") is True]
    if not prod:
        return DevChannel(None, f"{none}（起点と本番のブランチが同じで、本番系へ届く行（production: true）が宣言されていない）")
    for i, row in prod:
        if row.get("kind") != "manual":
            return DevChannel(None, f"{none}（delivery[{i}] は本番系へ自動で届く）")
    return DevChannel(MANUAL_PRODUCTION)


def judge_target(decl: DeliveryDecl, target: str) -> Verdict:
    """宛先 `target` へのマージが自動反映の本番チャネルへ入るか。上から順に最初に当たった条件で決まる。"""
    items = [
        {"kind": "decl", "name": decl.sources.get("production", f"{WT} の production_branch"), "result": decl.production or "（無し）"}
    ]

    def v(value, reason):
        return Verdict(target, value, reason, items)

    if decl.problems:
        return v(UNDETERMINED, "宣言を読めない: " + " / ".join(decl.problems))
    if not target:
        return v(UNDETERMINED, "Pull Request の宛先のブランチを読めない")
    if not decl.production:
        return v(UNDETERMINED, "本番チャネルを決められない（production_branch も origin/HEAD も無い）")
    if target != decl.production:
        return v(NOT_PRODUCTION, f"宛先 {target} は本番チャネル {decl.production} でない")
    if decl.rows is None:
        why = f"delivery が不明（{decl.unknown}）" if decl.unknown else "delivery が無い"
        items.append({"kind": "decl", "name": f"{PJ} の delivery", "result": decl.unknown and "unknown" or "（無し）"})
        return v(
            UNDETERMINED,
            f"宛先 {target} は本番チャネルだが、{why}ため反映の仕方を決められない（project-decl.py で delivery を宣言すれば次から判定できる）",
        )
    for i, row in enumerate(decl.rows):
        if reaches_by_merge(row, decl.production):
            items.append({"kind": "decl", "name": f"{PJ} の delivery[{i}]", "result": _row_text(row)})
            return v(PRODUCTION, f"delivery[{i}]（{row.get('target')}）は {target} へのマージで自動で反映する")
    items.append({"kind": "decl", "name": f"{PJ} の delivery", "result": f"{len(decl.rows)} 行"})
    return v(NOT_PRODUCTION, f"{target} へのマージで自動で反映する delivery の行が無い")


def _template_route(decl: DeliveryDecl, form, forms) -> list[Route]:
    if form in forms:
        return [Route(TEMPLATE, str(form), decl.production, STAGE_TEMPLATE)]
    note = f"リリースの形 {form!r} に雛形が無い（雛形のある形: {', '.join(forms)}）"
    return [Route(TEMPLATE, str(form), decl.production, STAGE_MANUAL, note)]


def _row_route(row: dict, decl: DeliveryDecl) -> Route:
    """delivery の 1 行から経路を 1 つ作る。"""
    target, branch = str(row.get("target") or ""), row.get("branch") or None
    prod = row.get("production") if isinstance(row.get("production"), bool) else None
    if row.get("kind") == "auto" and not row.get("versioned"):
        if branch and branch == decl.base:
            return Route(MERGE, target, branch, STAGE_MERGED_BY_CHECK, production=prod)
        if branch and branch == decl.production:
            note = f"{decl.base} → {branch} の昇格の Pull Request で届く"
            return Route(MERGE, target, branch, STAGE_PROMOTE, note, prod)
        why = "ブランチが無い" if not branch else f"{branch} はベースブランチでも本番チャネルでもない"
        return Route(MERGE, target, branch, STAGE_MANUAL, f"{target}: マージで反映するが{why}ため、手で届ける", prod)
    note = f"{target}: {row.get('trigger') or '（手順の記述無し）'}"
    if row.get("versioned"):
        note += "（版数を持つ経路の雛形は release.form で選ぶ）"
    return Route(MANUAL, target, branch, STAGE_MANUAL, note, prod)


def routes(decl: DeliveryDecl, forms=()) -> list[Route]:
    """リリースの経路。`release.form` があればそれ（`template`）だけで決まり、無ければ `delivery` の行ごとに決める。
    `forms` は雛形のある `release.form` の値（無い形は手で行うステージへ落とす）。"""
    form = (decl.release or {}).get("form") if decl.release else None
    if form:
        return _template_route(decl, form, forms)
    if decl.rows is None:
        why = (
            "宣言を読めない（" + " / ".join(decl.problems) + "）"
            if decl.problems
            else f"{PJ} の delivery が不明（{decl.unknown}）"
            if decl.unknown
            else f"{PJ} に delivery も {SV} に release.form も無い"
        )
        return [Route(MANUAL, "（不明）", None, STAGE_MANUAL, f"{why}ため、リリースの経路を決められない")]
    if not decl.rows:
        return [Route(NONE, "（無し）", None, STAGE_NONE, "delivery が [] で、配布しない")]
    return [_row_route(row, decl) for row in decl.rows]


def needs_version(rs: list[Route]) -> bool:
    """版数を要するのは経路が雛形（template）で組むときだけ。"""
    return any(r.route == TEMPLATE and r.stage == STAGE_TEMPLATE for r in rs)
