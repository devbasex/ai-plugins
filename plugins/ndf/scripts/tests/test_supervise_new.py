"""supervise.py new の宣言の扱い（#1192）・設計のプランの入口（#1193）・種別ごとの help（#1194）。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import inproc  # noqa: E402
from release_repo import push_origin  # noqa: E402
from gh_fake import install_gh_pr_create  # noqa: E402

SUPERVISE = SCRIPTS / "supervise.py"
PY = sys.executable

sys.path.insert(0, str(SCRIPTS))
from supervise_lib import commands, engine, pr as pr_step  # noqa: E402
from supervise_lib.verify_steps import RECLAIM_LIMIT, with_margin  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))


# new sprint は --design に無い課題の本文を gh で読む（#1767）。見本の本文で答える
pytestmark = pytest.mark.usefixtures("issue_bodies")


def cli(*args, cwd):
    return inproc.run(SUPERVISE, *args, cwd=cwd)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def plain_repo(tmp_path, release=None):
    """main へのマージが配る形のリポジトリ（プラグインでない）を模す。"""
    root = tmp_path / "other"
    (root / ".ndf").mkdir(parents=True)
    (root / ".ndf" / "worktree.json").write_text('{"version": 1, "base_branch": "main"}\n')
    decl = {"version": 1, "test": {"command": "make test {paths}"}}
    if release is not None:
        decl["release"] = release
    (root / ".ndf" / "supervise.json").write_text(json.dumps(decl))
    return root


# --- #1192: リリースの雛形の無い形 -------------------------------------------------


VERSION_LIKE = re.compile(r"\b\d+\.\d+\.\d+(?:-(?:dev|rc)\.\d+)?\b")


def release_plans(out: Path) -> dict[str, dict]:
    return {f.stem: json.loads(f.read_text()) for f in sorted(out.glob("*release*.json"))}


SAMPLES = {
    "carmo-system-console": (
        {"base_branch": "main", "production_branch": "main"},
        [{"target": "本番（ECS）", "kind": "auto", "trigger": "CodePipeline", "branch": "main", "versioned": False}],
        [("merge", "merged-by-check")],
        ["CD の結果"],
    ),
    "project-trygroup-prd": (
        {"base_branch": "develop", "production_branch": "main"},
        [
            {"target": "stg", "kind": "auto", "trigger": "develop へのマージ", "branch": "develop", "versioned": False},
            {"target": "prd", "kind": "auto", "trigger": "main へのマージ", "branch": "main", "versioned": False},
        ],
        [("merge", "merged-by-check"), ("merge", "promote")],
        ["本番"],
    ),
    "carmo-contractors-app": (
        {"base_branch": "main", "production_branch": "main"},
        [
            {"target": "web（Amplify）", "kind": "auto", "trigger": "push", "branch": "main", "versioned": False},
            {"target": "api", "kind": "manual", "trigger": "sam deploy --config-env prod", "versioned": False},
        ],
        [("merge", "merged-by-check"), ("manual", "manual")],
        ["CD の結果", "リリース"],
    ),
    "with-ai-dev": (
        {"base_branch": "main", "production_branch": "main"},
        [{"target": "本番", "kind": "manual", "trigger": "手でデプロイする", "versioned": False}],
        [("manual", "manual")],
        ["リリース"],
    ),
    "no-delivery": ({"base_branch": "main", "production_branch": "main"}, [], [("none", "none")], []),
}

# new close（fast のスプリントの終わり）がまとめと最終の検査の後に置くステージ。経路 merge だけなら「導入の確認」が加わる（#1457）
CLOSE_STAGES = {"carmo-system-console": ["導入の確認", "CD の結果"], "carmo-contractors-app": ["導入の確認", "CD の結果", "リリース"]}


# --- #1193: 設計のプランの入口 --------------------------------------------------------


def glossary_repo(tmp_path):
    root = tmp_path / "wt"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(root, "config", k, v)
    (root / "README.md").write_text("# r\n\n| 語 | 意味 |\n| --- | --- |\n| ステージ | 段 |\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def test_design_glossary_initializes_and_commits_when_missing(tmp_path):
    root = glossary_repo(tmp_path)
    out = tmp_path / "state" / "work" / "glossary-candidates.md"
    p = cli("design-glossary", "--mode", "standard", "--root", ".", "--out", str(out), cwd=root)
    assert p.returncode == 0, p.stdout + p.stderr
    res = json.loads(p.stdout.strip().splitlines()[-1])
    assert res["status"] == "ok" and res["metrics"]["initialized"] == 1
    assert (root / ".ndf" / "glossary.json").is_file()
    assert git(root, "status", "--porcelain") == ""  # 起こした用語集はコミットした
    assert ".ndf/glossary.json" in git(root, "show", "--name-only", "--format=", "HEAD")
    assert out.is_file() and "設計承認ゲート" in out.read_text()
    # 揃った後は何もしない
    head = git(root, "rev-parse", "HEAD")
    out.unlink()
    p = cli("design-glossary", "--mode", "standard", "--root", ".", "--out", str(out), cwd=root)
    assert p.returncode == 0 and json.loads(p.stdout.strip().splitlines()[-1])["metrics"]["initialized"] == 0
    assert git(root, "rev-parse", "HEAD") == head and not out.exists()


def test_design_glossary_stops_when_candidates_fails(tmp_path, monkeypatch):
    """候補の取得の失敗を「候補 0 件」として進めない。"""
    root = glossary_repo(tmp_path)
    out = tmp_path / "glossary-candidates.md"
    real = subprocess.run
    replies = {
        "gate": (1, {"summary": "無い"}),
        "init": (0, {"items": [{"name": ".ndf/glossary.json"}]}),
        "candidates": (2, {"summary": "壊れた"}),
    }

    calls = []

    def fake(cmd, **kw):
        if len(cmd) > 2 and cmd[1].endswith("glossary.py"):
            calls.append(cmd[2])
            code, res = replies[cmd[2]]
            return subprocess.CompletedProcess(cmd, code, json.dumps(res) + "\n", "")
        return real(cmd, **kw)

    monkeypatch.setattr(subprocess, "run", fake)
    res, code = commands.cmd_design_glossary(str(root), "standard", str(out))
    assert code == 2 and res["status"] == "stopped" and "candidates" in res["summary"]
    assert not out.exists()
    # init より前に止まるので、打ち直しも gate の停止から同じ経路を通る
    assert "init" not in calls


def test_design_glossary_removes_created_files_when_commit_fails(tmp_path, monkeypatch):
    """コミットできずに止まったら起こしたファイルを消し、打ち直しの gate が素通りしないようにする。"""
    root = glossary_repo(tmp_path)
    out = tmp_path / "glossary-candidates.md"
    real = subprocess.run

    def fake(cmd, **kw):
        if cmd[:2] == ["git", "commit"]:
            return subprocess.CompletedProcess(cmd, 1, "", "拒否")
        return real(cmd, **kw)

    monkeypatch.setattr(subprocess, "run", fake)
    res, code = commands.cmd_design_glossary(str(root), "standard", str(out))
    assert code == 1 and res["status"] == "stopped"
    assert not (root / ".ndf" / "glossary.json").exists()
    assert git(root, "status", "--porcelain") == ""


def test_pr_step_appends_existing_files(tmp_path, monkeypatch):
    root = glossary_repo(tmp_path)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    git(root, "remote", "add", "origin", str(origin))
    git(root, "push", "-q", "-u", "origin", "main")
    git(root, "checkout", "-q", "-b", "design/x")
    (root / "d.md").write_text("d\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Add: d")
    body = install_gh_pr_create(tmp_path, monkeypatch)
    state = tmp_path / "state"
    (state / "work").mkdir(parents=True)
    (state / "work" / "cand.md").write_text("## 用語集の候補\n\n- 目印の語\n")
    plan = {
        "フェーズ": "設計",
        "課題": [1],
        "作業場所": str(root),
        "steps": [
            {
                "id": "pr",
                "type": "pr",
                "base": "main",
                "body": "template",
                "next": "end",
                "append": ["{state_dir}/work/cand.md", "{state_dir}/work/none.md"],
            }
        ],
    }
    assert "結果: 完了" in engine.Engine(plan, state).run()
    got = body.read_text()
    assert "目印の語" in got and got.index("目印の語") < got.index(pr_step.PR_FOOTER)


# --- #1194: 種別ごとの help ------------------------------------------------------------


def test_new_impl_help_differs_from_sprint(tmp_path):
    impl = cli("new", "impl", "--help", cwd=tmp_path).stdout
    assert "--tests" in impl and "--test-cmd" in impl and "--design" not in impl and "--channel" not in impl


def test_every_new_argument_has_help(tmp_path):
    for kind in ("impl", "fix", "check", "release", "sprint", "close"):
        p = cli("new", kind, "--help", cwd=tmp_path)
        assert p.returncode == 0, kind
        opts = p.stdout.split("options:\n", 1)[1].split("\n\n", 1)[0].splitlines()
        for i, line in enumerate(opts):
            s = line.strip()
            if s.startswith("--") and "  " not in s:
                # 引数の名前だけの行は、次の行に説明が続く
                nxt = opts[i + 1].strip() if i + 1 < len(opts) else ""
                assert nxt and not nxt.startswith("-"), (kind, s)


# --- #1142: 即時修正のプラン（不足 a） --------------------------------------------------


def new_fix(root, out, *extra):
    return cli("new", "fix", "--tests", "tests/test_x.py", "--title", "Fix: x", "--out", str(out), *extra, cwd=root)


def fix_order(plan):
    steps = {s["id"]: s for s in plan["steps"]}
    order, sid = [], plan["steps"][0]["id"]
    while sid != "end":
        order.append(sid)
        sid = steps[sid]["next"]
    return order, steps


def test_new_fix_runs_tests_pr_and_merge_without_the_worker_step(tmp_path):
    root = plain_repo(tmp_path)
    out = tmp_path / "fix.json"
    p = new_fix(root, out, "--worktree", str(root))
    assert p.returncode == 0, p.stderr
    res = json.loads(p.stdout)
    assert res["tool"] == "supervise-new" and res["status"] == "ok" and res["items"][0]["kind"] == "fix"
    plan = json.loads(out.read_text())
    order, steps = fix_order(plan)
    assert order == ["base-sync", "test-limited", "pr", "test-all", "doc-lint", "ready", "merge-gate", "merge"]
    assert not any(s["type"] == "work" and s.get("kind") == "実装" for s in plan["steps"])
    assert "merge-when-green" in steps["merge"]["cmd"] and steps["pr"]["title"] == "Fix: x"
    assert "tests/test_x.py" in steps["test-limited"]["cmd"] and plan["作業場所"] == str(root)
    assert plan["課題"] == [] and plan["base_branch"] == "main"


def test_new_fix_records_the_escape_and_takes_the_issue(tmp_path):
    root = plain_repo(tmp_path)
    out = tmp_path / "fix.json"
    assert new_fix(root, out, "--worktree", str(root), "--issue", "7", "--escape-of", "0").returncode == 0
    plan = json.loads(out.read_text())
    order, steps = fix_order(plan)
    assert order[-1] == "escape" and "escape --pr {pr} --of 0" in steps["escape"]["cmd"]
    assert plan["課題"] == [7]


def test_new_fix_with_branch_only_places_the_worktree_under_the_repository(tmp_path):
    root = plain_repo(tmp_path)
    git(root, "init", "-q")
    out = tmp_path / "fix.json"
    p = new_fix(root, out, "--branch", "fix/x")
    assert p.returncode == 0, p.stderr
    plan = json.loads(out.read_text())
    assert plan["作業場所"] == str(root.resolve() / ".worktrees" / "fix" / "x")
    assert plan["branch"] == "fix/x" and plan["起点"] == "origin/main"


@pytest.mark.parametrize("drop", ["--tests", "--title", "place"])
def test_new_fix_without_a_required_argument_is_two(tmp_path, drop):
    root = plain_repo(tmp_path)
    args = {"--tests": ["tests/test_x.py"], "--title": ["Fix: x"], "place": ["--worktree", str(root)]}
    argv = [x for k, v in args.items() if k != drop for x in ([k, *v] if k != "place" else v)]
    p = cli("new", "fix", *argv, "--out", str(tmp_path / "f.json"), cwd=root)
    assert p.returncode == 2


def test_new_fix_help_shows_only_its_arguments(tmp_path):
    out = cli("new", "fix", "--help", cwd=tmp_path).stdout
    assert "--escape-of" in out and "--branch" in out and "--prompt" not in out and "--design" not in out


# --- #1142: 本番のリリースプランが他のプラグインの版を上げる（不足 c） ----------------------


def plugin_repo(tmp_path):
    """ndf と mcp-serena を持ち、ndf--v1.0.0 の後に mcp-serena だけを変えたリポジトリ。"""
    root = tmp_path / "repo"
    (root / ".ndf").mkdir(parents=True)
    (root / ".ndf" / "worktree.json").write_text('{"version": 1, "base_branch": "develop", "production_branch": "main"}\n')
    (root / ".ndf" / "supervise.json").write_text(
        json.dumps(
            {
                "version": 1,
                "test": {"command": "true {paths}"},
                "release": {"form": "package-plugin", "plugin": "ndf", "runtimes": ["claude"]},
            }
        )
    )
    for rel, v in (("plugins/ndf", "1.0.0"), ("plugins/mcp/mcp-serena", "2.3.4")):
        (root / rel / ".claude-plugin").mkdir(parents=True)
        (root / rel / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": v}, indent=2))
    git(root, "init", "-q", "-b", "develop")
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(root, "config", k, v)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    git(root, "tag", "ndf--v1.0.0")
    (root / "plugins" / "mcp" / "mcp-serena" / "README.md").write_text("changed\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "serena")
    push_origin(root)
    return root


def release_steps_of(root, tmp_path, channel, version, *extra):
    out = tmp_path / f"rel-{channel}.json"
    p = cli(
        "new",
        "release",
        "--version",
        version,
        "--channel",
        channel,
        "--prs",
        "1",
        "--worktree",
        str(root),
        "--repo",
        str(root),
        "--out",
        str(out),
        *extra,
        cwd=root,
    )
    assert p.returncode == 0, p.stderr
    return {s["id"]: s for s in json.loads(out.read_text())["steps"]}


@pytest.mark.parametrize("mvv", [False, True])
def test_prod_release_records_after_the_release_and_never_goes_back_to_it(tmp_path, mvv):
    """#1273 の AC1・AC4・I4・I6: 本番は release → record → verify。record の失敗は judge-record（record か stop）へ回り、
    デプロイへ戻る選択肢を持たない。共有の judge は record を選べない。開発版は record を持たない。"""
    root = plugin_repo(tmp_path)
    extra = ("--mvv", str(tmp_path / "s.json")) if mvv else ()
    steps = release_steps_of(root, tmp_path, "prod", "1.0.1", *extra)
    rec = steps["record"]
    assert steps["release"]["next"] == "cd-result" and steps["cd-result"]["next"] == "record" and rec["next"] == "verify"
    assert "release-steps.py cd-result --base main --head release/v1.0.1 --out {state_dir}/work/cd-result.json" in steps["cd-result"]["cmd"]
    assert "release-steps.py record --version 1.0.1 --prs 1 --cd-result {state_dir}/work/cd-result.json" in rec["cmd"]
    assert rec["pr_from"] == "release_pr_url" and rec["on_exit"] == {"5": "stop"}
    assert rec["on_fail"] == "judge-record" and steps["judge-record"]["choices"] == ["record", "stop"]
    assert "record" not in steps["judge"]["choices"] and "record" not in steps["fix"]["inputs"]
    dev = release_steps_of(root, tmp_path, "dev", "1.0.1-dev.1")
    assert "record" not in dev and "judge-record" not in dev and dev["release"]["next"] == "verify"


@pytest.mark.parametrize("mvv", [False, True])
def test_prod_release_passes_the_approval_material_and_stops_at_the_gate(tmp_path, mvv):
    """#815 の受け入れ条件 8・I7: 本番の release は承認資料を --approval で渡し、承認ゲート（10）なら verify へ進まない。
    MVV 判定のプランは handoff（by: mvv の記録を外す）、それ以外は end で終える。"""
    root = plugin_repo(tmp_path)
    extra = ("--mvv", str(tmp_path / "s.json")) if mvv else ()
    steps = release_steps_of(root, tmp_path, "prod", "1.0.1", *extra)
    rel = steps["release"]
    assert rel["cmd"].endswith(f"--channel prod --approval {root}/issues/approval-ndf-v1.0.1.md")
    assert rel["gate_next"] == ("handoff" if mvv else "end") and rel["next"] == "cd-result"
    assert ("handoff" in steps) == mvv
    dev = release_steps_of(root, tmp_path, "dev", "1.0.1-dev.1")["release"]
    assert "--approval" not in dev["cmd"] and "gate_next" not in dev


@pytest.mark.parametrize(("channel", "version", "prs"), [("dev", "1.0.1-dev.1", 1), ("prod", "1.0.1", 2)])
def test_release_step_stops_on_infra_wait_and_outlasts_the_ci_wait(tmp_path, channel, version, prs):
    """#1645 の AC12・I9: release のステップは 75 で judge を通らず止まり、timeout は 待つ PR の数 × --ci-wait より長い。"""
    root = plugin_repo(tmp_path)
    rel = release_steps_of(root, tmp_path, channel, version)["release"]
    assert rel["on_exit"] == {"75": "stop"} and rel["on_fail"] == "judge"
    ci_wait = int(rel["cmd"].split("--ci-wait ", 1)[1].split()[0])
    assert rel["timeout"] > prs * ci_wait


def approved_others(root, *sets):
    """開発版の others のステップと同じく承認資料へ他のプラグインの表を書き、--set で上げ幅を決める（#1752）。"""
    (root / "issues").mkdir(exist_ok=True)
    (root / "issues" / "approval-ndf-v1.0.1.md").write_text("# t\n\n## 同意を求めること\n\n- [ ] x\n")
    for args in ((), *(("--set", s) for s in sets)):
        p = subprocess.run(
            [
                PY,
                str(SCRIPTS / "release-steps.py"),
                "changed-plugins",
                "--root",
                str(root),
                "--approval",
                "issues/approval-ndf-v1.0.1.md",
                *args,
            ],
            capture_output=True,
            text=True,
        )
        assert p.returncode == 0, p.stdout + p.stderr


@pytest.mark.parametrize("sets, want", [((), "2.3.5"), (("mcp-serena=MAJOR",), "3.0.0")])
def test_prod_release_bumps_other_changed_plugins_after_ndf(tmp_path, sets, want):
    """#1752 の受け入れ条件 5: 本番の bump-others は承認資料の表の上げ幅で他のプラグインを上げる。"""
    root = plugin_repo(tmp_path)
    steps = release_steps_of(root, tmp_path, "prod", "1.0.1")
    assert steps["bump"]["next"] == "bump-others" and steps["bump-others"]["next"] == "changelog"
    assert "bump-others" in steps["judge"]["choices"]
    approved_others(root, *sets)
    p = subprocess.run(steps["bump-others"]["cmd"], shell=True, cwd=root, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    got = json.loads((root / "plugins/mcp/mcp-serena/.claude-plugin/plugin.json").read_text())["version"]
    assert got == want


def test_bump_others_stops_without_the_approved_table(tmp_path):
    """#1752 の受け入れ条件 6: 承認資料の表が無ければ PATCH へ倒さずに落ちる。"""
    root = plugin_repo(tmp_path)
    steps = release_steps_of(root, tmp_path, "prod", "1.0.1")
    p = subprocess.run(steps["bump-others"]["cmd"], shell=True, cwd=root, capture_output=True, text=True)
    assert p.returncode != 0
    got = json.loads((root / "plugins/mcp/mcp-serena/.claude-plugin/plugin.json").read_text())["version"]
    assert got == "2.3.4"


def test_dev_release_lists_other_plugins_into_the_approval(tmp_path):
    """#1752 の受け入れ条件 1: 開発版のプランは explain の後に others で他のプラグインを承認資料へ書く。"""
    root = plugin_repo(tmp_path)
    steps = release_steps_of(root, tmp_path, "dev", "1.0.1-dev.1")
    oth = steps["others"]
    assert steps["explain"]["next"] == "others" and oth["next"] == "end" and oth["on_fail"] == "judge"
    assert "changed-plugins --plugin ndf --prs 1 --approval issues/approval-ndf-v1.0.1.md" in oth["cmd"]
    assert "others" in steps["judge"]["choices"] and "others" in steps["fix"]["inputs"]
    ndf = json.loads((root / "plugins/ndf/.claude-plugin/plugin.json").read_text())["version"]
    assert ndf == "1.0.0"  # ndf は bump のステップが上げる


def test_bump_others_stops_when_changed_plugins_fails(tmp_path):
    root = plugin_repo(tmp_path)
    git(root, "tag", "-d", "ndf--v1.0.0")
    steps = release_steps_of(root, tmp_path, "prod", "1.0.1")
    p = subprocess.run(steps["bump-others"]["cmd"], shell=True, cwd=root, capture_output=True, text=True)
    assert p.returncode != 0


def test_dev_release_does_not_bump_other_plugins(tmp_path):
    root = plugin_repo(tmp_path)
    steps = release_steps_of(root, tmp_path, "dev", "1.0.1-dev.1")
    assert "bump-others" not in steps and steps["bump"]["next"] == "changelog"


def test_merge_steps_gate_then_merge_and_approved_only_by_from(tmp_path):
    """#1336 の決定 1: 判定のステップでリリース承認ゲートに当たればプランを終え、承認の後は --from merge-approved でだけ
    --gate-approved user のマージへ入る（通常の流れの merge は merge-approved へ流れない）。"""
    root = plain_repo(tmp_path)
    out = tmp_path / "plan.json"
    p = new_fix(root, out, "--worktree", str(root))
    assert p.returncode == 0, p.stderr
    order, steps = fix_order(json.loads(out.read_text()))
    assert "merge-approved" not in order
    gate, merge, approved = steps["merge-gate"], steps["merge"], steps["merge-approved"]
    assert "merge-gate --pr {pr}" in gate["cmd"] and gate["gate_next"] == "end" and gate["next"] == "merge"
    assert "--gate-approved" not in merge["cmd"] and approved["cmd"] == merge["cmd"] + " --gate-approved user"
    assert approved["next"] == merge["next"] == "end"
    # CI の基盤待ちは judge を通らずに止まり（#1645）、ベースブランチとの衝突は取り込みへ戻る（#1917）
    assert merge["on_exit"] == approved["on_exit"] == {"75": "stop", "4": "base-return"}
    # マージに続く後片付けのテスト環境の回収の分を足す（#2660 の決定 8）
    ci_wait = int(merge["cmd"].split("--timeout ")[1].split()[0])
    assert merge["timeout"] == approved["timeout"] == with_margin(ci_wait) + RECLAIM_LIMIT


def test_plan_promote_with_mvv_judges_before_merging(tmp_path):
    """#1336 の F5（fast / auto）: verify（#1457）→ prepare → mvv（リリース承認ゲートの判定。承認ゲートならプランを終える）→ note → promote
    （--gate-approved mvv）。note か promote が落ちたら handoff が承認ゲートへ落とす。"""
    import argparse

    from supervise_lib.delivery_templates import PromoteParams, plan_promote

    a = argparse.Namespace(base="develop", production_branch="main", issue=[1], mode="standard", no_reports="")
    plan = plan_promote(a, str(tmp_path), 600, PromoteParams(mvv=str(tmp_path / "s.json"), verify="true"))
    steps = {s["id"]: s for s in plan["steps"]}
    assert [s["id"] for s in plan["steps"]][:5] == ["verify", "prepare", "mvv", "note", "promote"]
    assert "--prepare --out" in steps["prepare"]["cmd"] and steps["mvv"]["gate_next"] == "end"
    material = "--material {state_dir}/work/approval-promote.md {state_dir}/work/approval-verify.md"
    assert "--gate release" in steps["mvv"]["cmd"] and material in steps["mvv"]["cmd"]
    assert steps["promote"]["cmd"].endswith("--gate-approved mvv") and steps["promote"]["on_fail"] == "handoff"
    assert steps["note"]["on_fail"] == "handoff" and steps["handoff"]["gate_next"] == "end"
    assert plan["base_branch"] == "develop"


# --- #1437: 戦略の注記を計画の作成の時点で標準エラーへ出す ---------------------------------

sys.path.insert(0, str(SCRIPTS / "lib"))

PYTEST_SUITE = {"version": 1, "test": {"suites": [{"name": "py", "runner": "pytest", "command": "pytest -q"}]}}


def _repo_1437(tmp_path, project=None):
    """supervise.json に test の無いリポジトリ。`project` を渡せば .ndf/project.json に置く。"""
    root = plain_repo(tmp_path, {"form": "package-plugin", "plugin": "foo", "runtimes": ["claude"]})
    sv = json.loads((root / ".ndf" / "supervise.json").read_text())
    sv.pop("test")
    (root / ".ndf" / "supervise.json").write_text(json.dumps(sv))
    if project is not None:
        (root / ".ndf" / "project.json").write_text(json.dumps(project))
    return root


def _wholes(out):
    return [
        s["cmd"]
        for f in sorted(out.glob("*.json"))
        for s in json.loads(f.read_text()).get("steps") or []
        if "test-run.py whole" in str(s.get("cmd"))
    ]


# 現状固定: new_args.check_new の引数の組の判定（I-012）。止まるときは ap.error の文言と終了コード 2。
from types import SimpleNamespace  # noqa: E402
import argparse  # noqa: E402

from supervise_lib import new_args  # noqa: E402


def new_ns(kind, **kw):
    base = dict(
        kind=kind,
        issue=None,
        tests=None,
        title=None,
        worktree="",
        branch=None,
        since_last=False,
        pr=None,
        id=None,
        review_only=False,
        final=False,
        version=None,
        prs=None,
        prs_from_queue=False,
        channel=None,
        repo=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    "kind,kw,message",
    [
        ("impl", dict(tests="t", title="x"), "new impl には --issue・--tests・--title が要る"),
        ("impl", dict(issue="1", title="x"), "new impl には --issue・--tests・--title が要る"),
        ("fix", dict(tests="t", title="x"), "new fix には --worktree か --branch・--tests・--title が要る"),
        ("fix", dict(worktree="/w", title="x"), "new fix には --worktree か --branch・--tests・--title が要る"),
        ("check", dict(since_last=True, pr="5", id="c"), "new check の --since-last と --pr は同時に渡せない"),
        ("check", dict(since_last=True), "new check --since-last には --id（検査の名前）が要る"),
        ("check", dict(pr="5", review_only=True), "new check の --review-only は --since-last と組にする"),
        ("check", dict(since_last=True, id="c", review_only=True, final=True), "new check の --review-only と --final は同時に渡せない"),
        ("check", dict(), "new check には --pr か --since-last が要る"),
        ("release", dict(prs="1", channel="dev", repo="r"), "new release には --version・--prs（か --prs-from-queue）・--channel が要る"),
        (
            "release",
            dict(version="1.0.0", channel="dev", repo="r"),
            "new release には --version・--prs（か --prs-from-queue）・--channel が要る",
        ),
        (
            "release",
            dict(version="1.0.0", prs="1", channel="dev", worktree="/tmp/x"),
            "new release には --repo が要る（作業場所が /.worktrees/ の下に無い）",
        ),
    ],
)
def test_check_new_rejects_incomplete_combinations(kind, kw, message, capsys):
    with pytest.raises(SystemExit) as e:
        new_args.check_new(argparse.ArgumentParser(prog="p"), new_ns(kind, **kw))
    assert e.value.code == 2
    assert capsys.readouterr().err.strip().endswith(f"error: {message}")


@pytest.mark.parametrize(
    "kind,kw",
    [
        ("impl", dict(issue="1", tests="t", title="x")),
        ("fix", dict(worktree="/w", tests="t", title="x")),
        ("check", dict(pr="5")),
        ("check", dict(pr="5", final=True)),
        ("check", dict(since_last=True, id="c")),
        ("check", dict(since_last=True, id="c", review_only=True)),
        ("release", dict(version="1.0.0", prs="1", channel="dev", repo="r")),
        ("release", dict(version="1.0.0", prs_from_queue=True, channel="dev", worktree="/r/.worktrees/b")),
        ("sprint", dict()),
    ],
)
def test_check_new_accepts_complete_combinations(kind, kw):
    a = new_ns(kind, **kw)
    before = vars(a).copy()
    assert new_args.check_new(argparse.ArgumentParser(prog="p"), a) is None
    assert vars(a) == before


def test_check_new_fix_with_branch_only_fills_worktree(monkeypatch):
    monkeypatch.setattr(new_args, "fix_worktree", lambda branch: f"/wt/{branch}")
    a = new_ns("fix", branch="fix/x", tests="t", title="x")
    new_args.check_new(argparse.ArgumentParser(prog="p"), a)
    assert a.worktree == "/wt/fix/x"


def test_check_new_check_reports_since_last_with_pr_before_missing_id(capsys):
    # 複数の条件に当たるときは先に書かれた判定の文言が出る
    with pytest.raises(SystemExit):
        new_args.check_new(argparse.ArgumentParser(prog="p"), new_ns("check", since_last=True, pr="5", review_only=True, final=True))
    assert "--since-last と --pr は同時に渡せない" in capsys.readouterr().err


# --- #2623: デプロイとリリースを CD で解く -----------------------------------------


TWO_BRANCHES = {"base_branch": "develop", "production_branch": "main"}
ONE_BRANCH = {"base_branch": "main", "production_branch": "main"}
CD_ROW = {"target": "prd", "kind": "auto", "trigger": "main へのマージ", "branch": "main", "versioned": False}
HAND_ROW = {"target": "aws", "kind": "manual", "trigger": "cdk deploy", "versioned": False}


# --- #3144: 設計単体のレビューと詳細設計の段 ----------------------------------------------


DETAIL_ROUTE = ["design", "design-review", "design-review-check", "design-fix", "detail-design", "rebuild"]
DETAIL_IDS = {"design-review", "design-review-check", "design-fix", "detail-design"}


def _revise_plan(with_detail: bool) -> dict:
    steps = [
        {"id": "glossary", "next": "requirements-check"},
        {"id": "requirements-check", "next": "design"},
        {"id": "requirements", "prompt": "要求"},
        {"id": "design", "prompt": "設計"},
        *([{"id": "detail-design", "prompt": "詳細設計"}] if with_detail else []),
        {"id": "push-glossary", "next": "ready"},
    ]
    return {"steps": steps}
