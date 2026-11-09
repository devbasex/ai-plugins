"""リリースの設定から、マージの宛先の判定とリリースの経路を決める（#1336）。

宣言は `.ndf/worktree.json`（ベースブランチ・本番チャネル）・`.ndf/project.json` の `delivery`・`.ndf/supervise.json` の
`release` の 3 つ。ファイルと git の参照（既定ブランチ）だけを読み、`gh` を呼ばない。判定は保存せず、呼ぶたびに作り直す。

- `judge_target`: 宛先のブランチへのマージが自動反映の本番チャネルへ入るか（`production` / `not-production` /
  `undetermined`）。決められないときは `undetermined` にし、止める側へ倒す
- `routes`: 変更が本番系へ届く道筋（`template` / `merge` / `manual` / `none`）と、それを届けるステージ
- `dev_channel`: 開発版のチャネルの形（`separate-branch` / `manual-production` / 無し）。`pace: fast` / `auto` の使ってよい
  条件とスプリントのステージの組み立てが読む（#1454）
- `release_way`: リリースの依頼を受けたときのリリースの組み方（組む / 組まない / 組めない）。並列の計画が読む（#2616）
- `cd_guidance`: CD が無いリポジトリへ出す案内。状態を持たず、呼ぶたびに宣言から作る（#2623）
- `publishes_by_cd`: リリースタグと GitHub Release を CD が作ると宣言しているか（#2623 の決定 12）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import limits
import decl
import project_decl
import repo

PRODUCTION, NOT_PRODUCTION, UNDETERMINED = "production", "not-production", "undetermined"
STOP_VERDICTS = (PRODUCTION, UNDETERMINED)  # 承認の無いマージを止める判定

# 経路（Route.route）と、それを届けるステージ（Route.stage）
TEMPLATE, MERGE, MANUAL, NONE = "template", "merge", "manual", "none"
STAGE_TEMPLATE = "template"  # release.form の雛形（開発版 → 本番）
STAGE_MERGED_BY_CHECK = "merged-by-check"  # ベースブランチへのマージで届く（ステージを置かない）
STAGE_PROMOTE = "promote"  # リリースの Pull Request（ベースブランチ → 本番チャネル）
STAGE_MANUAL = "manual"  # 手で行うステージ（/ndf:release）
STAGE_NONE = "none"  # 届けない（ステージを置かない）

# リリースの組み方（ReleaseWay.way）
BUILD, SKIP, REFUSE = "build", "skip", "refuse"  # まとめたリリースを組む / 組まない / 組めない

# 開発版のチャネルの形（DevChannel.value）
SEPARATE_BRANCH = "separate-branch"  # 起点と本番チャネルが違う
MANUAL_PRODUCTION = "manual-production"  # 起点と本番チャネルが同じで、本番系へ届く行がすべて手動（手動反映の本番系）

# CD の結果の待ちの上限（秒）。行の宣言が無ければ DEFAULT_RESULT_TIMEOUT。1 回の読み取りの合計は RESULT_TOTAL_LIMIT で切る
DEFAULT_RESULT_TIMEOUT, RESULT_TOTAL_LIMIT = limits.seconds("delivery.cd-result"), limits.seconds("delivery.cd-result-total")

CD_SETUP_REF = "ndf の release の Skill の references/cd-setup.md"
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

    @property
    def rows_state(self) -> str | None:
        """delivery の行が使えない理由（`problems` / `unknown` / `absent`）。問題が無く行が並びなら None。"""
        if self.problems:
            return "problems"
        if self.rows is None:
            return "unknown" if self.unknown else "absent"
        return None


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


@dataclass(frozen=True)
class ReleaseWay:
    """リリースの依頼を受けたときのリリースの組み方。`way` は `BUILD` / `SKIP` / `REFUSE`、`reason` は組まない・組めない理由。"""

    way: str
    reason: str = ""


@dataclass(frozen=True)
class CdGuidance:
    """CD の案内。`missing` は何が無いか、`rows` は理由の無い手動の行の `target`（宣言そのものが無ければ空）。"""

    missing: str
    rows: tuple[str, ...] = ()
    steps_ref: str = CD_SETUP_REF
    example: str = (
        '{"target": "<届け先>", "kind": "auto", "trigger": "<CD の名前>", "channel": "production", "versioned": false, '
        '"result": {"workflow": "<ワークフローのファイル名>"}}'
    )
    manual_reason_how: str = f"あえて CD を置かないなら、{PJ} の delivery の手動の行に manual_reason（理由）と trigger（手順）を書く"

    def as_item(self) -> dict:
        return {
            "kind": "cd-guidance",
            "name": "CD の案内",
            "result": "guided",
            "missing": self.missing,
            "rows": list(self.rows),
            "steps_ref": self.steps_ref,
            "example": self.example,
            "manual_reason_how": self.manual_reason_how,
        }


def _result_problem(v: dict, where: str) -> str | None:
    """`result`（結果の読み方）の形の誤り。合えば None。"""
    if not isinstance(v, dict):
        return f"{where}.result がオブジェクトでない"
    ways = [k for k in ("workflow", "command") if v.get(k) is not None]
    if len(ways) > 1:
        return f"{where}.result の workflow と command は同時に書けない"
    if ways and not (isinstance(v[ways[0]], str) and v[ways[0]].strip()):
        return f"{where}.result.{ways[0]} が空か文字列でない"
    limit = v.get("timeout_seconds")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0):
        return f"{where}.result.timeout_seconds が正の整数でない"
    return None


def _row_problem(row: dict, where: str) -> str | None:
    """#2623 で足した任意の欄の形の誤り。読めない宣言として止める側へ倒す（I4）。"""
    if row.get("manual_reason") is not None and not isinstance(row["manual_reason"], str):
        return f"{where}.manual_reason が文字列でない"
    if row.get("publishes_release") is not None and not isinstance(row["publishes_release"], bool):
        return f"{where}.publishes_release が真偽値でない"
    return _result_problem(row["result"], where) if row.get("result") is not None else None


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
        if row.get("channel") is not None and row["channel"] not in ("production", "base"):
            problems.append(f"{PJ} の delivery[{i}]: channel が production / base でない")
            return None, None
        if why := _row_problem(row, f"delivery[{i}]"):
            problems.append(f"{PJ} の {why}")
            return None, None
    return v, None


def _resolve_channels(rows: list[dict] | None, base: str | None, production: str | None) -> list[dict] | None:
    """`channel`（`production` / `base`）を持つ行を、読んだ時点で 1 回だけブランチ名へ解いて `branch` に入れる。

    `channel` と `branch` の両方があれば `channel` が勝つ。`channel` の無い行は `branch` をそのまま使う（移行の読み）。
    """
    if rows is None:
        return None
    names = {"production": production, "base": base}
    return [dict(r, branch=names[r["channel"]]) if r.get("channel") in names else r for r in rows]


def build(
    wt: dict,
    project: dict,
    release=None,
    *,
    default_branch: str | None = None,
    problems: list[str] | None = None,
    base: str | None = None,
    production: str | None = None,
    channel_base: str | None = None,
) -> DeliveryDecl:
    """読んだ宣言の中身から組む。`base` / `production` を渡せばそれを先に使う（引数が宣言より先に効く）。

    `channel_base` を渡せば、`channel: base` の行はそのブランチへ解く（認可に関わる判定の値。`load_delivery` が
    メインディレクトリの `base_branch` を渡す）。渡さなければ起点（`base`）で解く。
    """
    problems = list(problems or [])
    sources = {}
    wt_base = wt.get("base_branch") if isinstance(wt.get("base_branch"), str) and wt.get("base_branch") else None
    b = base or wt_base or default_branch
    sources["base"] = "引数" if base else (f"{WT} の base_branch" if wt_base else "既定ブランチ")
    p, sources["production"] = (production, "引数") if production else repo.production_of(wt, default_branch)
    rows, unknown = _rows_of(project, problems)
    rows = _resolve_channels(rows, channel_base or b, p)
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
    default = repo.default_branch(root)
    return build(
        wt,
        project,
        sv.get("release"),
        default_branch=default,
        problems=problems,
        channel_base=_main_base(wt, main, root) or default,
    )


def _main_base(wt: dict, main, root) -> str | None:
    """`channel: base` を解く起点のブランチ。メインディレクトリの `worktree.json` の `base_branch` から読む。

    `channel: base` の行は本番チャネルの判定（リリース承認ゲート）に効くため、Pull Request の head の `base_branch` で
    行を本番から外せないようにする（#2363 round 6）。メインディレクトリにファイルかキーが無ければ `None`（既定ブランチ）で、
    呼ばれた木の値で補わない。メインディレクトリが解けない（git でない）か呼ばれた木そのものなら、読んだ `wt` の値を使う。
    """
    if main is None or Path(main).resolve() == Path(root).resolve():
        got = wt.get("base_branch")
    else:
        f = Path(main) / WT
        got = decl.load_file(f, repo.WORKTREE_DECL.name)[0].get("base_branch") if f.is_file() else None
    return got if isinstance(got, str) and got else None


def _row_text(row: dict) -> str:
    return " ".join(str(x) for x in (row.get("kind"), row.get("branch") or "（ブランチ無し）", row.get("target") or "") if x)


def reaches_by_merge(row: dict, production: str | None) -> bool:
    """行が本番チャネルへのマージで自動で本番系へ届くか。`production: false`（検証の環境）の行は数えない。"""
    return row.get("kind") == "auto" and bool(production) and row.get("branch") == production and row.get("production") is not False


def merge_rows(decl: DeliveryDecl) -> list[dict]:
    """本番チャネルへのマージで自動で本番系へ届く行のすべて（CD の結果を読む対象）。"""
    return [r for r in decl.rows or [] if reaches_by_merge(r, decl.production)]


def publishes_by_cd(decl: DeliveryDecl) -> bool:
    """リリースタグと GitHub Release を CD が作ると宣言しているか。別の成果物を届ける自動の行は数えない（I11）。"""
    return any(r.get("versioned") is True and r.get("publishes_release") is True for r in merge_rows(decl))


def cd_guidance(decl: DeliveryDecl) -> CdGuidance | None:
    """CD の案内。上から順に最初に当たった条件で決まる。読めない宣言には出さない（理由は経路の判定が示す。I4）。"""
    if decl.problems:
        return None
    if decl.rows is None:
        return CdGuidance(f"{PJ} の delivery の宣言")
    rows = [r for r in decl.rows if r.get("kind") == "manual" and not str(r.get("manual_reason") or "").strip()]
    if rows:
        return CdGuidance("CD か、CD を置かない理由（manual_reason）", tuple(str(r.get("target") or "") for r in rows))
    return None


def _first_merge_row(decl: DeliveryDecl) -> tuple[int, dict] | None:
    """本番チャネルへのマージで自動で本番系へ届く最初の行（番号と行）。"""
    return next(((i, row) for i, row in enumerate(decl.rows) if reaches_by_merge(row, decl.production)), None)


def _no_channel(why: str) -> DevChannel:
    return DevChannel(None, f"開発版のチャネルが無い（{why}）")


def dev_channel(decl: DeliveryDecl) -> DevChannel:
    """開発版のチャネルの形。上から順に最初に当たった条件で決まる。起点と本番チャネルが違えば delivery を読まない。"""
    if not decl.production:
        return _no_channel("本番のブランチが分からない")
    if decl.production != decl.base:
        return DevChannel(SEPARATE_BRANCH)
    if decl.rows_state:
        why = {"problems": "読めない", "unknown": "不明", "absent": "無い"}[decl.rows_state]
        return _no_channel(f"起点と本番のブランチが同じで、{PJ} の delivery が{why}")
    hit = _first_merge_row(decl)
    if hit:
        return _no_channel(f"delivery[{hit[0]}] が本番チャネル {decl.production} へのマージで自動で本番系へ届く")
    prod = [(i, r) for i, r in enumerate(decl.rows) if r.get("production") is True]
    if not prod:
        return _no_channel("起点と本番のブランチが同じで、本番系へ届く行（production: true）が宣言されていない")
    for i, row in prod:
        if row.get("kind") != "manual":
            return _no_channel(f"delivery[{i}] は本番系へ自動で届く")
    return DevChannel(MANUAL_PRODUCTION)


def judge_target(decl: DeliveryDecl, target: str) -> Verdict:
    """宛先 `target` へのマージが自動反映の本番チャネルへ入るか。上から順に最初に当たった条件で決まる。"""
    items = []

    def append_decl(name, result):
        items.append({"kind": "decl", "name": name, "result": result})

    def v(value, reason):
        return Verdict(target, value, reason, items)

    append_decl(decl.sources.get("production", f"{WT} の production_branch"), decl.production or "（無し）")

    if decl.problems:
        return v(UNDETERMINED, "宣言を読めない: " + " / ".join(decl.problems))
    if not target:
        return v(UNDETERMINED, "Pull Request の宛先のブランチを読めない")
    if not decl.production:
        return v(UNDETERMINED, "本番チャネルを決められない（production_branch も origin/HEAD も無い）")
    if target != decl.production:
        return v(NOT_PRODUCTION, f"宛先 {target} は本番チャネル {decl.production} でない")
    if decl.rows is None:
        why = {"unknown": f"delivery が不明（{decl.unknown}）", "absent": "delivery が無い"}[decl.rows_state]
        append_decl(f"{PJ} の delivery", {"unknown": "unknown", "absent": "（無し）"}[decl.rows_state])
        return v(
            UNDETERMINED,
            f"宛先 {target} は本番チャネルだが、{why}ため反映の仕方を決められない（project-decl.py で delivery を宣言すれば次から判定できる）",
        )
    hit = _first_merge_row(decl)
    if hit:
        i, row = hit
        append_decl(f"{PJ} の delivery[{i}]", _row_text(row))
        return v(PRODUCTION, f"delivery[{i}]（{row.get('target')}）は {target} へのマージで自動で反映する")
    append_decl(f"{PJ} の delivery", f"{len(decl.rows)} 行")
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
    if row.get("kind") == "auto":  # 版の有無（versioned）と誰が届けるかは別の話で、経路は kind とブランチだけで決める（#2623 の決定 1）
        if branch and branch == decl.base:
            return Route(MERGE, target, branch, STAGE_MERGED_BY_CHECK, production=prod)
        if branch and branch == decl.production:
            note = f"{decl.base} → {branch} のリリースの Pull Request で届く"
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
        why = {
            "problems": "宣言を読めない（" + " / ".join(decl.problems) + "）",
            "unknown": f"{PJ} の delivery が不明（{decl.unknown}）",
            "absent": f"{PJ} に delivery も {SV} に release.form も無い",
        }[decl.rows_state]
        return [Route(MANUAL, "（不明）", None, STAGE_MANUAL, f"{why}ため、リリースの経路を決められない")]
    if not decl.rows:
        return [Route(NONE, "（無し）", None, STAGE_NONE, "delivery が [] で、デプロイしない")]
    return [_row_route(row, decl) for row in decl.rows]


def needs_version(rs: list[Route]) -> bool:
    """版数を要するのは経路が雛形（template）で組むときだけ。"""
    return any(r.route == TEMPLATE and r.stage == STAGE_TEMPLATE for r in rs)


def _reach_text(r: Route) -> str:
    """組まない経路の届き方の 1 語句。"""
    if r.stage == STAGE_MERGED_BY_CHECK:
        return f"{r.target} はマージで反映する"
    return f"{r.target}: {r.note}" if r.stage == STAGE_PROMOTE else str(r.note)


def release_way(decl: DeliveryDecl, forms=()) -> ReleaseWay:
    """リリースの組み方。上から順に最初に当たった条件で決まる。決まらないときは「組めない」へ倒す（#2616 の I1）。"""
    rs = routes(decl, forms)
    if needs_version(rs):
        return ReleaseWay(BUILD)
    fix = f"{PJ} の delivery か {SV} の release.form を直す"
    if decl.problems:
        return ReleaseWay(REFUSE, f"宣言を読めない（{' / '.join(decl.problems)}）。{fix}")
    manual = [str(r.note) for r in rs if r.stage == STAGE_MANUAL]
    if manual:
        return ReleaseWay(REFUSE, f"{' / '.join(manual)}。{fix}")
    return ReleaseWay(SKIP, f"{' / '.join(_reach_text(r) for r in rs)}（{PJ} の delivery）")
