"""supervise.py と supervise_lib/ の形（#1142 の C1）。

エントリポイント（副命令・`new` の種別・`example` の形）が分けた後も変わらないことと、
supervise_lib のモジュールの行数と import の向き（設計の「import の向き」）を固定する。
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SUPERVISE = SCRIPTS / "supervise.py"
PKG = SCRIPTS / "supervise_lib"

SUBCOMMANDS = [
    "run",
    "history",
    "expected",
    "example",
    "new",
    "queue",
    "wait",
    "advance",
    "design-glossary",
    "design-results",
    "design-approved",
    "note",
    "sync-check",
    "format",
    "sync-title",
    "review-settled",
]
NEW_KINDS = ["impl", "fix", "check", "release", "sprint", "close"]
MODULES = [
    "__init__",
    "advance",  # 手順の連続実行（supervise.py advance。#2909）
    "progress_watch",  # 実装のステップの進みの観測（#2505）
    "rerun",  # ステップの打ち直しの同じ原因の上限（#2343）
    "procedures",  # 単発とスプリントの雛形が共有する手順の定義（#1485）
    "pr_materials",
    "design_stage",
    "paths",
    "decl",
    "plan",
    "limits",  # ステップの上限をプランの遷移から求める（#1909）
    "prompts",
    "claude",
    "children",  # 子プロセスの待ち（#2855 で claude から分けた）
    "state",
    "slow",
    "steps",
    "worker_steps",
    "pr",
    "engine",
    "actuals",  # supervise の実行を見積もりの実績の記録へ残す（#1856）
    "admission",
    "running",  # queue のプロセスの中の、走っているプランの登録簿（#1674）
    "flow",
    "queue_waits",  # queue の待ちの秒を理由ごとに数える（#2998）
    "templates",
    "verify_steps",
    "release_flow",  # 取り込み方ごとのリリースのステップの部品（#3121）
    "release_cd",  # CD のある場合の本番のリリースプランの部品（#2773 で release_templates から分けた）
    "release_templates",
    "review_units",  # レビューの単位をスプリントのプランへ差し込む部品（#3163）
    "delivery_templates",
    "sprint_revise",
    "sprint_waves",
    "sprint_bundles",  # 課題のまとまりでスプリントを組む部品（#3081 で sprint_waves から分けた）
    "design_steps",  # 設計のプランの工程の定義（sprint_waves から分けた）
    "sprint_routes",
    "sprint_inputs",  # new sprint が計画を書く前に課題の受け入れ条件を確かめる（#1767）
    "sprint_close_steps",  # new close のまとめのステップ（#2089 で sprint から分けた）
    "sprint_lane_close",  # 1 スプリントのレーンの版の要らないまとめのステージ（#3058）
    "sprint",
    "design_merge",  # normal の設計承認ゲートの後の設計 PR のマージ（#1426）
    "absorb",  # new sprint --absorb の取り込んだ課題（#1426）
    "queue_fill",  # queue の差し込みを前の計画の結果で埋める（queue から分けた）
    "queue",
    "queue_resume",  # キューの続き queue --resume（#1426）
    "commands",
    "new_args",
]
# プランの実行の部品（ハンドラー・状態・監視・claude）とプランを作る側は Engine を import しない
NO_ENGINE = [
    "progress_watch",
    "decl",
    "plan",
    "limits",
    "prompts",
    "claude",
    "children",
    "state",
    "slow",
    "steps",
    "worker_steps",
    "pr",
    "templates",
    "release_flow",
    "release_cd",
    "release_templates",
    "review_units",
    "delivery_templates",
    "sprint_revise",
    "sprint_waves",
    "sprint_bundles",
    "sprint_routes",
    "sprint_inputs",
    "sprint_close_steps",
    "sprint",
    "design_merge",
    "absorb",
    "queue_fill",
    "queue",
    "queue_resume",
    "new_args",
    "paths",
    "admission",
    "running",
    "actuals",
    "queue_waits",
]

# 関数の中の import で、モジュールの循環にしない辺（Engine.run と cmd_queue が耐久ワークフローを始める。
# flow は Engine と、queue のファイルだけに触る関数を import する）
LAZY = {("engine", "flow"), ("queue", "flow")}


# new sprint は --design に無い課題の本文を gh で読む（#1767）。見本の本文で答える
pytestmark = pytest.mark.usefixtures("issue_bodies")


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SUPERVISE), *args], capture_output=True, text=True, timeout=60)


def lib_imports(name: str) -> set[str]:
    """supervise_lib の中の `name` が import する supervise_lib のモジュール。"""
    tree = ast.parse((PKG / f"{name}.py").read_text(encoding="utf-8"))
    got: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("supervise_lib"):
            if node.module == "supervise_lib":
                got |= {a.name for a in node.names}
            else:
                got.add(node.module.split(".", 1)[1])
        elif isinstance(node, ast.ImportFrom) and node.level:
            got |= {a.name for a in node.names} if not node.module else {node.module}
        elif isinstance(node, ast.Import):
            got |= {a.name.split(".", 1)[1] for a in node.names if a.name.startswith("supervise_lib.")}
    return got


def test_subcommands_do_not_change():
    p = run("--help")
    assert p.returncode == 0
    assert "{" + ",".join(SUBCOMMANDS) + "}" in p.stdout


@pytest.mark.parametrize("cmd", SUBCOMMANDS)
def test_each_subcommand_has_help(cmd):
    assert run(cmd, "--help").returncode == 0


def test_new_kinds_do_not_change():
    p = run("new", "--help")
    assert p.returncode == 0 and "{" + ",".join(NEW_KINDS) + "}" in p.stdout
    for kind in NEW_KINDS:
        assert run("new", kind, "--help").returncode == 0, kind


def test_example_is_a_plan():
    p = run("example")
    plan = json.loads(p.stdout)
    assert p.returncode == 0 and plan["steps"] and "作業場所" in plan


def test_example_embeds_no_project_values(tmp_path):
    """見本にテストのコマンドとブランチ名を埋め込まない（どのリポジトリでも写せる形）。プランの読み込みは通る。"""
    p = run("example")
    assert "pytest" not in p.stdout
    plan = json.loads(p.stdout)
    assert all("base" not in s for s in plan["steps"] if s["type"] == "pr")
    f = tmp_path / "plan.json"
    f.write_text(p.stdout, encoding="utf-8")
    assert run("expected", str(f)).returncode == 0


def test_new_without_required_exits_2():
    assert run("new", "impl", "--worktree", ".").returncode == 2


def test_package_has_the_designed_modules():
    assert sorted(f.stem for f in PKG.glob("*.py")) == sorted(MODULES)


@pytest.mark.parametrize("path", [SUPERVISE, *sorted(PKG.glob("*.py"))], ids=lambda p: p.name)
def test_no_file_exceeds_500_lines(path):
    assert len(path.read_text(encoding="utf-8").splitlines()) <= 500


@pytest.mark.parametrize("name", NO_ENGINE)
def test_only_commands_imports_engine(name):
    assert "engine" not in lib_imports(name)


def test_imports_have_no_cycle():
    graph = {m: {n for n in lib_imports(m) & set(MODULES) if (m, n) not in LAZY} for m in MODULES}

    def visit(m: str, path: tuple[str, ...]) -> None:
        assert m not in path, " → ".join((*path, m))
        for n in graph[m]:
            visit(n, (*path, m))

    for m in MODULES:
        visit(m, ())


# --- #2773: CD のある場合のリリースプランの並び -------------------------------------------------------------


def _release_plan(tmp_path, monkeypatch, channel: str, version: str, cd: bool) -> dict:
    """new release をこのリポジトリの宣言で組む。cd なら場合の判定を「CD があり、レビューなしでマージできる」に固定する。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(PKG.parent / "lib"))
    import inproc
    import release_case

    if cd:
        monkeypatch.setattr(release_case, "case_at", lambda root, strict=False: release_case.decide_case(True, 0, ""))
    out = tmp_path / f"{channel}-{cd}.json"
    args = ["new", "release", "--version", version, "--prs", "995", "--channel", channel]
    p = inproc.run(SUPERVISE, *args, "--worktree", f"/r/.worktrees/release/v{version}", "--out", str(out), cwd=SUPERVISE.parents[3])
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def test_cd_prod_plan_stacks_the_bumps_before_the_approval_and_merges_after_it(tmp_path, monkeypatch):
    """受け入れ条件 14・決定 6: 版上げ〜消費の記録は承認ゲートの前に積み、承認の後は証跡とマージだけが進む。"""
    plan = _release_plan(tmp_path, monkeypatch, "prod", "10.17.11", cd=True)
    ids = [s["id"] for s in plan["steps"]]
    steps = {s["id"]: s for s in plan["steps"]}
    assert ids[0] == "bump" and "approve" not in steps
    assert [ids.index(i) for i in ("bump", "bump-others", "changelog", "notes", "snapshot")] == sorted(
        ids.index(i) for i in ("bump", "bump-others", "changelog", "notes", "snapshot")
    )
    assert ids.index("snapshot") < ids.index("release")
    assert "--decided" not in steps["bump-others"]["cmd"] and "--prs 995" in steps["bump-others"]["cmd"]
    release = steps["release"]
    assert release["gate_next"] == "facts" and release["next"] == "cd-result" and "--approval" not in release["cmd"]
    assert (steps["facts"]["next"], steps["explain"]["next"], steps["others"]["next"]) == ("explain", "others", "approval-post")
    assert "--head release/v10.17.11" in steps["facts"]["cmd"]
    assert steps["approval-post"]["next"] == "end" and "approval-post --pr" in steps["approval-post"]["cmd"]
    assert (
        steps["approve-pr"]["next"] == "release"
        and "approve --pr" in steps["approve-pr"]["cmd"]
        and "--by user" in steps["approve-pr"]["cmd"]
    )


@pytest.mark.parametrize("mvv", [False, True])
def test_cd_review_prod_plan_writes_no_trail_and_returns_to_release(tmp_path, monkeypatch, mvv):
    """受け入れ条件 18・I8: レビューが必須の場合は approve-pr（証跡の書き込み）を置かず、承認の後は release へ戻る。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(PKG.parent / "lib"))
    import inproc
    import release_case

    monkeypatch.setattr(release_case, "case_at", lambda root, strict=False: release_case.decide_case(True, 1, ""))
    out = tmp_path / "plan.json"
    extra = ["--mvv", str(tmp_path / "state.json")] if mvv else []
    args = ["new", "release", "--version", "10.17.11", "--prs", "995", "--channel", "prod", *extra]
    p = inproc.run(SUPERVISE, *args, "--worktree", "/r/.worktrees/release/v10.17.11", "--out", str(out), cwd=SUPERVISE.parents[3])
    assert p.returncode == 0, p.stdout + p.stderr
    steps = {s["id"]: s for s in json.loads(out.read_text(encoding="utf-8"))["steps"]}
    assert "approve-pr" not in steps and "approve --pr" not in json.dumps(list(steps.values()), ensure_ascii=False)
    assert steps["approval-post"]["next"] == ("mvv" if mvv else "end")
    assert steps["release"]["gate_next"] == "facts" and "10" not in steps["release"]["on_exit"]
    if mvv:
        assert steps["note"]["next"] == "release"


@pytest.mark.parametrize("mvv", [False, True])
def test_cd_no_review_mvv_plan_does_not_count_the_awaiting_release_as_a_gate(tmp_path, monkeypatch, mvv):
    """MVV 判定のプランでは release の承認待ちの 10 を承認ゲートに数えず facts へ進む（後の mvv が判定し、マージまで済めば完了）。
    利用者が承認するプランは今のまま承認ゲートで止まる。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(PKG.parent / "lib"))
    import inproc
    import release_case

    monkeypatch.setattr(release_case, "case_at", lambda root, strict=False: release_case.decide_case(True, 0, ""))
    out = tmp_path / "plan.json"
    extra = ["--mvv", str(tmp_path / "state.json")] if mvv else []
    args = ["new", "release", "--version", "10.17.11", "--prs", "995", "--channel", "prod", *extra]
    p = inproc.run(SUPERVISE, *args, "--worktree", "/r/.worktrees/release/v10.17.11", "--out", str(out), cwd=SUPERVISE.parents[3])
    assert p.returncode == 0, p.stdout + p.stderr
    release = {s["id"]: s for s in json.loads(out.read_text(encoding="utf-8"))["steps"]}["release"]
    assert release["on_exit"].get("75") == "stop"
    if mvv:
        assert "gate_next" not in release and release["on_exit"]["10"] == "facts"
    else:
        assert release["gate_next"] == "facts" and "10" not in release["on_exit"]


def test_cd_dev_plan_has_no_approval_material_steps(tmp_path, monkeypatch):
    plan = _release_plan(tmp_path, monkeypatch, "dev", "10.17.11-dev.1", cd=True)
    steps = {s["id"]: s for s in plan["steps"]}
    assert not {"facts", "explain", "others", "approval-post"} & set(steps) and steps["verify"]["next"] == "end"


def test_no_cd_prod_plan_keeps_the_approval_first(tmp_path, monkeypatch):
    """受け入れ条件 19・I9: CD の無い場合の本番のプランは今のまま承認の記録が先頭に来る。"""
    plan = _release_plan(tmp_path, monkeypatch, "prod", "10.17.11", cd=False)
    assert plan["steps"][0]["id"] == "approve" and "approval-post" not in {s["id"] for s in plan["steps"]}


@pytest.mark.parametrize("cd", [True, False])
def test_since_last_check_plan_drops_the_whole_test_only_with_cd(tmp_path, monkeypatch, cd):
    """受け入れ条件 16: pace fast の前回の検査からの差分の検査（--since-last）も、CD のある場合は全体テストを流さない。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(PKG.parent / "lib"))
    import inproc
    import release_case

    decided = release_case.decide_case(True, 0, "") if cd else release_case.decide_case(False, None, "")
    monkeypatch.setattr(release_case, "case_at", lambda root, strict=False: decided)
    root = SUPERVISE.parents[3]
    out = tmp_path / "check.json"
    p = inproc.run(SUPERVISE, "new", "check", "--since-last", "--id", "m-1", "--worktree", str(root), "--out", str(out), cwd=root)
    assert p.returncode == 0, p.stdout + p.stderr
    ids = {s["id"] for s in json.loads(out.read_text(encoding="utf-8"))["steps"]}
    assert ("test-all" in ids) is not cd
