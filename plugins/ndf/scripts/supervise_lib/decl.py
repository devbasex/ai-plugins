"""プロジェクトごとの宣言（リポジトリの根の `.ndf/`）の読み取りと、雛形の引数への当てはめ（#1142 の C1）。"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import branch_flow
import decl as setting_decl
import delivery
import formatting
import project_decl
import repo
import schema
from typing import Annotated

from pydantic import ConfigDict, Field, StrictInt


# プロジェクトごとの宣言（リポジトリの根の .ndf/）。形は plan.py の docstring の「宣言」の節にある
WORKTREE_DECL = "worktree.json"  # base_branch（起点のブランチ）・production_branch（本番のブランチ）
SUPERVISE_DECL = "supervise.json"  # test・sync_checks・format・release・queue・impl_timeout・impl_stall_seconds・refactor_budget_minutes
GLOSSARY_DECL = "glossary.json"  # 用語集の設定（あれば glossary.py gate で用語集が揃っているかを見る）


class DeclError(Exception):
    """宣言が読めない・形が違う。"""


class Decl(schema.Shape):
    """宣言の最上位（オブジェクトであることだけを見る。項目はプロジェクトごとに違うので拒まない）。"""

    model_config = ConfigDict(extra="allow")


class SyncCheck(schema.Shape):
    model_config = ConfigDict(extra="allow")
    name: str
    command: str


class SuperviseDecl(Decl):
    """.ndf/supervise.json のうち、形を見る項目（test・sync_checks）。"""

    test: dict = {}
    sync_checks: list[SyncCheck] = []


def supervise_shape(decl: dict) -> SuperviseDecl:
    """supervise.json の test と sync_checks の形を見る（空・null は無いとみなす）。違えば DeclError。"""
    try:
        return schema.load_shape(SuperviseDecl, {"test": decl.get("test") or {}, "sync_checks": decl.get("sync_checks") or []})
    except schema.ShapeError as e:
        raise DeclError(f'supervise.json: {e}（test はオブジェクト、sync_checks は {{"name", "command"}} の並びで書く）') from e


PositiveInt = Annotated[StrictInt, Field(gt=0)]
# 宣言と引数で同じ名前（a.<名前>）を使う、ステップの時間の枠（#1916）
STEP_BUDGETS = ("impl_timeout", "impl_stall_seconds", "refactor_budget_minutes")


class StepBudgets(schema.Shape):
    """.ndf/supervise.json の実装の上限（impl_timeout・秒）・無進捗の許容時間（impl_stall_seconds・秒）・
    想定最大時間（refactor_budget_minutes・分）。null は書かないのと同じ。"""

    impl_timeout: PositiveInt | None = None
    impl_stall_seconds: PositiveInt | None = None
    refactor_budget_minutes: PositiveInt | None = None


def step_budgets(decl: dict) -> StepBudgets:
    """supervise.json の 3 項目の形を見る（JSON の 1 以上の整数か null）。違えば項目の名前を含む DeclError。"""
    try:
        return schema.load_shape(StepBudgets, {k: decl.get(k) for k in STEP_BUDGETS})
    except schema.ShapeError as e:
        raise DeclError(f"supervise.json: {e}（impl_timeout・impl_stall_seconds・refactor_budget_minutes は 1 以上の整数で書く）") from e


def read_decl(roots, name: str) -> dict:
    """roots の順に .ndf/<name> を探し、最初に見つかった宣言を返す。どこにも無ければ {}。

    契約がキーごとに別の置き場を宣言した値（`worktree.json` の `production_branch`）は、見つけた木から
    `setting_decl.overlay_keys` で読み直す（Pull Request の変更で本番のブランチを動かせないようにする。#2363）。
    """
    for r in roots:
        f = Path(r) / ".ndf" / name
        if not f.is_file():
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            raise DeclError(f"{f}: JSON として読めない: {e}") from e
        try:
            schema.load_shape(Decl, d, str(f))
        except schema.ShapeError as e:
            raise DeclError(f"{e}（最上位はオブジェクトで書く）") from e
        if name in setting_decl.places():
            d, why = setting_decl.overlay_keys(r, name, d, f)
            if why:
                raise DeclError(why)
        return d
    return {}


def decl_roots(worktree: str, repo: str | None = None) -> list[Path]:
    """宣言を探す場所: 作業場所 → 元のリポジトリ（--repo か作業場所の /.worktrees/ より前）→ 今のディレクトリ。"""
    roots = [Path(worktree)]
    if repo:
        roots.append(Path(repo))
    if "/.worktrees/" in str(worktree):
        roots.append(Path(str(worktree).split("/.worktrees/")[0]))
    roots.append(Path.cwd())
    return roots


def format_of(decl: dict) -> list[tuple[str, str]]:
    """.ndf/supervise.json の format（整形）を [(名前, コマンド)] で返す。読むのは `formatting.format_commands` だけ（#1507 の I8）。"""
    try:
        return [(c.name, c.command) for c in formatting.format_commands(decl)]
    except formatting.FormatError as e:
        raise DeclError(str(e)) from e


def glossary_ready(roots) -> bool:
    """用語集の設定と用語集が揃っているか。設定のある最初の根で `glossary.py gate --mode standard` が 0 なら真
    （設計の工程の入口と同じ判定。#1651 の決定 3）。設定がどこにも無ければ偽。"""
    for r in roots:
        if (Path(r) / ".ndf" / GLOSSARY_DECL).is_file():
            gl = Path(__file__).resolve().parent.parent / "glossary.py"
            p = subprocess.run(
                [sys.executable, str(gl), "gate", "--mode", "standard", "--root", str(r)], capture_output=True, text=True, cwd=str(r)
            )
            return p.returncode == 0
    return False


def declared_base_of(roots) -> str | None:
    """.ndf/worktree.json の base_branch。無ければ None。"""
    try:
        v = read_decl(roots, WORKTREE_DECL).get("base_branch")
    except DeclError:
        return None
    return v if isinstance(v, str) and v else None


def sync_checks_of(decl: dict) -> list[tuple[str, str]]:
    """.ndf/supervise.json の sync_checks を [(名前, コマンド)] で返す。"""
    return [(c.name, c.command) for c in supervise_shape(decl).sync_checks]


# 資源の枠の既定（#1142 の決定 30）。宣言の resources.<タグ> が項目ごとに上書きする
QUEUE_RESOURCES = {"graphql": {"limit": 2, "types": ["pr", "drive"], "commands": ["merged-steps.py"]}}


class QueueResource(schema.Shape):
    """資源の枠: 同時に流せる本数（limit）と、タグを持つステップの型（types）・run の cmd に含まれる語（commands）。"""

    limit: int
    types: list[str] = []
    commands: list[str] = []


class SharedList(schema.Shape):
    """共有の一覧: 複数のプランが書き足すファイル（path）と、当たり方（touched_by。無ければ path そのもの）。"""

    path: str
    touched_by: list[str] = []


class QueueDecl(schema.Shape):
    """.ndf/supervise.json の queue。"""

    resources: dict[str, QueueResource] = {}
    shared: list[SharedList] = []


def queue_decl(decl: dict) -> dict:
    """supervise.json の queue を既定と合わせて `{"resources": {タグ: {limit, types, commands}}, "shared": [{path, touched_by}]}` で返す。

    無ければ既定（graphql の枠 2 本・共有の一覧は無し）。形が違えば DeclError。"""
    raw = decl.get("queue") or {}
    hint = '（queue は {"resources": {タグ: {"limit", "types", "commands"}}, "shared": [{"path", "touched_by"}]} で書く）'
    declared = (raw.get("resources") or {}) if isinstance(raw, dict) else None
    if isinstance(declared, dict):  # 既定へ、宣言したタグを項目ごとに重ねる
        merged = {**QUEUE_RESOURCES, **declared}
        raw = {**raw, "resources": {t: {**QUEUE_RESOURCES.get(t, {}), **r} if isinstance(r, dict) else r for t, r in merged.items()}}
    try:
        shape = schema.load_shape(QueueDecl, raw, "supervise.json: queue")
    except schema.ShapeError as e:
        raise DeclError(f"{e}{hint}") from e
    low = [t for t, r in shape.resources.items() if r.limit < 1]
    if low:
        raise DeclError(f"supervise.json: queue.resources.{low[0]}.limit: 1 以上の整数にする{hint}")
    out = schema.dump_shape(shape)
    for s in out["shared"]:
        s["touched_by"] = s["touched_by"] or [s["path"]]
    return out


def queue_decl_of(plans: list[dict]) -> dict:
    """queue が流すプランの宣言（`queue_decl`）。プランの作業場所と元のリポジトリを入れた順に探し、最後に今のディレクトリを探す。"""
    roots: list[Path] = []
    for plan in plans:
        if isinstance(plan, dict) and plan.get("作業場所"):
            roots += decl_roots(str(plan["作業場所"]), plan.get("リポジトリ"))[:-1]
    return queue_decl(read_decl(list(dict.fromkeys([*roots, Path.cwd()])), SUPERVISE_DECL))


# 雛形が宣言から受けるもの。引数が宣言より先に効く
NEEDS = {
    "impl": ("base", "test"),
    "fix": ("base", "test"),
    "check": ("base", "test"),
    "release": ("base", "release"),
}
# 本番のブランチの出所（決め方の順。決め方の正本は `repo.production_of`）。エラーと案内の文面に使う（#2097 の決定 3）
PRODUCTION_SOURCES = f"--production-branch・.ndf/{WORKTREE_DECL} の production_branch・既定ブランチ（origin/HEAD → main / master）"
# 本番のブランチを使う雛形（`--production-branch` を受ける種別と同じ）。ほかの種別では既定ブランチを問い合わせない（git の起動を増やさない）
USES_PRODUCTION = ("release",)


def test_kind(a) -> str:
    """`--test-cmd` の雛形の種別（`--test-kind`。既定はテスト。#1483 決定 3）。"""
    return getattr(a, "test_kind", None) or "test"


def scope_paths(a) -> list[str]:
    """静的解析の雛形の全体テストで `{paths}` に入れる範囲 = 明示した `--tests`。`.`（sprint の既定）は数えない（I4）。"""
    return [str(t) for t in getattr(a, "tests", None) or [] if str(t) not in (".", "")]


def apply_decls(a) -> None:
    """引数に無いものを .ndf/ の宣言から埋める。雛形に要るのにどちらにも無ければ DeclError。

    - 起点のブランチ（a.base）: --base → worktree.json の base_branch
    - 本番のブランチ（a.production_branch）: --production-branch → `repo.production_branch`（worktree.json の
      production_branch → 既定ブランチ。決め方の正本は `repo.production_of`。#2097）。既定ブランチへの補完は
      本番を使う雛形（`USES_PRODUCTION`）だけで行う
    - テスト（a.strategy・a.test_limits・a.test_cmd・a.no_reports）: --test-cmd（雛形）→ project.json の test →
      supervise.json の test.command（project.json に test が無いときだけ。読んだことを a.test_note に残す。#1334 決定 3）。
      --test-all は廃止（知らせて無視する）
    - 同期とチェック（a.sync_checks）: supervise.json の sync_checks（無ければ計画に sync のステップを置かない）
    - 整形（a.format）: supervise.json の format（無ければ計画に format のステップを置かない）
    - 用語集の有無（a.glossary_ready）: `glossary_ready`（真なら実装の指示に用語集と突き合わせる行を入れる）
    - デプロイ（a.release）: supervise.json の release
    - 実装の上限（a.impl_timeout）・無進捗の許容時間（a.impl_stall_seconds）・想定最大時間（a.refactor_budget_minutes）:
      引数 → supervise.json の同じ名前の項目。
      宣言の形は引数があっても見る（#1916 の決定 5）

    - ブランチ運用（a.flow）: worktree.json の branch_flow をブランチ名まで解いたスナップショット（`flow_snapshot`）。
      宣言が壊れていれば既定値へ倒さずに DeclError（#3121 の I1）
    """
    roots = decl_roots(a.worktree, getattr(a, "repo", None))
    wt = read_decl(roots, WORKTREE_DECL)
    sv = read_decl(roots, SUPERVISE_DECL)
    _apply_simple_decls(a, wt, sv)
    if not a.production_branch and roots and a.kind in USES_PRODUCTION:
        # 既定ブランチは、まだ作っていない worktree ではなく存在する木から解く（#2097 の決定 1）
        a.production_branch = repo.production_branch(repo.first_git_tree(roots), wt)[0]
    a.flow = flow_snapshot(a, wt)
    decl, a.test_note = _project_decl(roots, sv)
    _apply_test_strategy(a, decl)
    if getattr(a, "glossary_ready", None) is None:
        a.glossary_ready = glossary_ready(roots)
    _require_declared(a)


FLOW_SOURCE = f".ndf/{WORKTREE_DECL} の {branch_flow.DECL_KEY}"


def flow_snapshot(a, wt: dict) -> dict:
    """ブランチ運用のスナップショット（`branch_flow.ResolvedFlow.as_snapshot`）。引数に既にあればそれを使う。
    宣言が壊れていれば DeclError（#3121 の I3）。"""
    if isinstance(getattr(a, "flow", None), dict) and a.flow:
        return a.flow
    try:
        flow = branch_flow.of_decl(wt)
    except branch_flow.FlowError as e:
        raise DeclError(str(e)) from e
    source = FLOW_SOURCE if flow.declared else f"既定値（{FLOW_SOURCE} が無い）"
    return flow.resolve(a.base, getattr(a, "production_branch", None)).as_snapshot(source)


def sprint_flow(a) -> branch_flow.ResolvedFlow:
    """引数のスナップショットを読む。無ければ（スナップショットを持たない呼び手）宣言の無いリポジトリの値（I3）。"""
    return branch_flow.from_snapshot(getattr(a, "flow", None), getattr(a, "base", None), getattr(a, "production_branch", None))


def sync_source(a, pr) -> str:
    """ベースの取り込みの取り込み元の引数。選んで集める取り込み方は、宛先（ベースブランチ）ではなく
    スプリントの起点から取り込む（#3121 の I4）。それ以外は PR の宛先（`--pr`）。"""
    flow = sprint_flow(a)
    return f"--base {shlex.quote(flow.start_branch)}" if flow.row.selects_units and flow.start_branch else f"--pr {pr}"


def check_flags(a, no_refactor: bool) -> str:
    """`eval`・`prepare` へ渡す範囲の引数（--review / --since / --sprint / --issue）。"""
    flag = " --review" if no_refactor else ""
    if getattr(a, "since_ref", None):
        flag += f" --since {shlex.quote(a.since_ref)}"
    if getattr(a, "final_sprint", None):
        # 最後の検査（#3126）: 範囲をスプリントに属しまだ検査していない PR に絞る
        flag += f" --sprint {shlex.quote(a.final_sprint)}"
        if a.issue:
            flag += " --issue " + " ".join(str(int(i)) for i in a.issue)
    return flag


def check_issues(a) -> list[int]:
    """検査の課題: `--issue` か、無ければ `--sprint` の JSON の issues（読めなければ空）。"""
    if a.issue or not getattr(a, "sprint", None):
        return list(a.issue or [])
    try:
        return [int(i) for i in json.loads(Path(a.sprint).read_text()).get("issues", [])]
    except (OSError, ValueError, TypeError):
        return []


def _apply_simple_decls(a, wt: dict, sv: dict) -> None:
    """引数を優先し、無ければ宣言の値をそのまま写す項目。"""
    sv_test = supervise_shape(sv).test
    budgets = step_budgets(sv)
    for k in STEP_BUDGETS:
        setattr(a, k, getattr(a, k, None) or getattr(budgets, k))
    a.base = getattr(a, "base", None) or wt.get("base_branch") or None
    a.production_branch = getattr(a, "production_branch", None) or wt.get("production_branch") or None
    a.test_cmd = getattr(a, "test_cmd", None) or None
    if getattr(a, "test_all", None):
        print("⚠ --test-all は廃止しました（#1334）。全体テストは宣言の suites[].command で決まります", file=sys.stderr, flush=True)
    a.test_all = None
    a.no_reports = sv_test.get("no_reports") or ""
    a.sync_checks = sync_checks_of(sv)
    a.format = format_of(sv)
    a.release = sv.get("release") or None


def _apply_test_strategy(a, decl: dict) -> None:
    """テスト戦略（a.strategy）と時間の上限（a.test_limits）を宣言と --test-cmd から組む。"""
    import test_strategy as ts

    a.strategy, a.test_limits = None, None
    # 静的解析の雛形の全体テストの範囲。スプリントが各計画（検査の計画を含む）へ引き継ぐ（#1483）
    a.test_paths = getattr(a, "test_paths", None) or scope_paths(a)
    if decl.get("test") is not None or a.test_cmd:
        try:
            strategy = ts.resolve(decl, baseline_test=a.test_cmd, template_kind=test_kind(a), scope_paths=scope_paths(a))
        except ts.StrategyError as e:
            raise DeclError(str(e)) from e
        # 戦略の注記（雛形から組んだ全体テスト・組まなかった静的解析の全体テスト）を計画の作成の時点で知らせる（#1437）
        for note in strategy.notes:
            print(f"⚠ {note}", file=sys.stderr, flush=True)
        a.strategy = strategy.as_state()
        # 所要の記録と、同じリポジトリで先に走った全体テストの実測から決める（#1631）
        a.test_limits = ts.unbudgeted_limits(strategy, decl, a.worktree)


def _require_declared(a) -> None:
    """計画の種別（a.kind）に要る宣言が揃わなければ DeclError。"""
    missing = {
        "base": (not a.base, f"起点のブランチ（--base か .ndf/{WORKTREE_DECL} の base_branch）"),
        "test": (
            a.strategy is None,
            f"テストの宣言（.ndf/{project_decl.DECL.name} の test か --test-cmd、または .ndf/{SUPERVISE_DECL} の test.command）",
        ),
        "release": (not isinstance(a.release, dict) or not a.release.get("form"), f"リリースの形（.ndf/{SUPERVISE_DECL} の release.form）"),
    }
    lack = [missing[k][1] for k in NEEDS[a.kind] if missing[k][0]]
    if lack:
        raise DeclError(f"new {a.kind} に要る宣言が無い: " + "・".join(lack))


def delivery_decl(a) -> "delivery.DeliveryDecl":
    """リリースの判定に使う宣言（lib/delivery.py の DeliveryDecl）。起点と本番のブランチは引数（a.base・a.production_branch）が
    宣言より先に効く。読めない宣言は `problems` に書き、例外を上げない。"""
    roots = decl_roots(a.worktree, getattr(a, "repo", None))
    problems = []
    try:
        wt = read_decl(roots, WORKTREE_DECL)
    except DeclError as e:
        wt, problems = {}, [str(e)]
    try:
        project = read_decl(roots, project_decl.DECL.name)
    except DeclError as e:
        project, problems = {}, [*problems, str(e)]
    return delivery.build(
        wt,
        project,
        a.release,
        default_branch=_default_branch_of(roots),
        problems=problems,
        base=a.base,
        production=a.production_branch,
    )


def _default_branch_of(roots) -> str | None:
    """宣言を探す木の並びのうち、最初に存在する git の木から解いた既定ブランチ（`apply_decls` と同じ木。#2097 の決定 2）。"""
    tree = repo.first_git_tree(roots)
    return repo.default_branch(tree) if tree is not None else None


def _project_decl(roots, sv: dict) -> tuple[dict, str | None]:
    """roots の順に `.ndf/project.json` を探し、`test` を持つ最初の宣言。無ければ supervise.json の test を 1 つの suite として読む。"""
    import test_strategy as ts

    last: tuple[dict, str | None] = ({}, None)
    for r in roots:
        decl, note = ts.decl_of(r, sv)
        if note is None and decl.get("test") is not None:
            return decl, None
        last = (decl, note)
    return last


def decl_fields(a) -> dict:
    """スプリントの雛形が各計画へ引き継ぐ、宣言から埋めた値。"""
    keys = (
        "base",
        "production_branch",
        "test_cmd",
        "test_kind",
        "test_paths",
        "test_all",
        "no_reports",
        "sync_checks",
        "format",
        "glossary_ready",
        "release",
        "strategy",
        "test_limits",
        "test_note",
        "routes",
        "flow",
        *STEP_BUDGETS,
    )
    return {k: getattr(a, k, None) for k in keys}


def with_decls(plan: dict, a) -> dict:
    """計画に起点のブランチと、テストに成果物を作らせない指定を書く（run のステップと pr のステップが読む）。"""
    plan["base_branch"] = a.base
    if getattr(a, "no_reports", ""):
        plan["no_reports"] = a.no_reports
    return plan
