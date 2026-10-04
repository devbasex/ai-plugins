"""project-decl.py（#1333）: 宣言の判定・測定・書き出し。

仮のリポジトリ（Laravel 風・pytest 風・CI 無し）を作り、`gh` は PATH の先頭に置いた偽物で置き換える。
偽物は受けた引数を FAKE_GH_LOG へ 1 行ずつ書き、FAKE_GH_MODE（ok / fail）で応答を変える。
origin は GitHub の URL にし、リモート追跡のブランチは update-ref で直に作る（ネットワークを使わない）。
LLM の答えはテストが答えのファイルに書いて渡す。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
PY = sys.executable
SECRET = "AKIAABCDEFGHIJKLMNOP"
DB_URL = "mysql://app:hunter2secret@db.internal:3306/app"
TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz0123"

FAKE_GH = r"""#!{py}
import io, json, os, sys, zipfile
a = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps(a) + "\n")
if os.environ.get("FAKE_GH_MODE") != "ok":
    sys.stderr.write("To get started with GitHub CLI, please run:  gh auth login\n")
    sys.exit(4)
path = a[-1]
R = "repos/acme/app/"
def out(v):
    print(json.dumps(v)); sys.exit(0)
if path.startswith(R + "actions/runs?"):
    out({{"workflow_runs": [{{"id": 7, "path": ".github/workflows/test.yml",
        "run_started_at": "2026-09-01T00:00:00Z", "updated_at": "2026-09-01T00:06:30Z"}}]}})
if path.startswith(R + "actions/runs/7/jobs"):
    steps = [{{"name": "Run phpunit", "started_at": "2026-09-01T00:01:00Z", "completed_at": "2026-09-01T00:05:00Z"}},
             {{"name": "Check out latest", "started_at": "2026-09-01T00:00:00Z", "completed_at": "2026-09-01T00:01:00Z"}}]
    out({{"jobs": [{{"steps": steps}}] * 3}})
if path.startswith(R + "actions/runs/7/artifacts"):
    out({{"artifacts": [{{"id": 9, "name": "junit-1", "size_in_bytes": 100}}, {{"id": 10, "name": "coverage", "size_in_bytes": 100}}]}})
if path == R + "actions/artifacts/9/zip":
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.xml", '<testsuites><testsuite name="a" time="1000.5"><testsuite time="3"/></testsuite><testsuite time="200"/></testsuites>')
        z.writestr("b.xml", '<testsuite name="b" time="99.5"/>')
    sys.stdout.buffer.write(buf.getvalue()); sys.exit(0)
if path.startswith(R + "rules/branches/main"):
    out([{{"type": "required_status_checks", "parameters": {{"required_status_checks": [{{"context": "phpunit"}}]}}}}])
if path.startswith(R + "pulls?"):
    out([{{"merged_at": "x", "base": {{"ref": "main"}}, "body": "refs https://redmine.example.jp/issues/1 " + os.environ.get("FAKE_PR_BODY", "")}}])
if path.startswith(R + "issues?"):
    out([{{"number": 1}}, {{"number": 2, "pull_request": {{}}}}])
sys.stderr.write("HTTP 404: Not Found\n"); sys.exit(1)
"""


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def commit(root, msg="c"):
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")


@pytest.fixture
def env(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(FAKE_GH.format(py=PY), encoding="utf-8")
    gh.chmod(0o755)
    e = dict(os.environ)
    e["PATH"] = f"{bindir}{os.pathsep}{e['PATH']}"
    e["FAKE_GH_LOG"] = str(tmp_path / "gh.log")
    e["FAKE_GH_MODE"] = "ok"
    e["NDF_METRICS_DIR"] = str(tmp_path / "metrics")
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        e.pop(k, None)
    return e


def init_repo(root: Path, files: dict[str, str]) -> Path:
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    for k, v in (("user.email", "t@example.com"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(root, "config", k, v)
    git(root, "remote", "add", "origin", "https://github.com/acme/app.git")
    for rel, text in files.items():
        write(root, rel, text)
    commit(root, "init")
    git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    return root


LARAVEL = {
    "composer.json": json.dumps(
        {"require": {"php": "^8.3", "laravel/framework": "^10.10"}, "require-dev": {"phpunit/phpunit": "^10.1", "larastan/larastan": "^2"}}
    ),
    "phpunit.xml": "<phpunit/>\n",
    "pint.json": "{}\n",
    "docker-compose.yml": "services:\n    app:\n        image: app\n    mysql:\n        image: mysql:8.0\n",
    "AGENTS.md": f"# ガイド\n\nテストは `docker compose exec app ./vendor/bin/phpunit` で走らせる。\n\n接続は docker compose exec app で {DB_URL}\n",
    ".github/workflows/test.yml": "on: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
    "app/Models/User.php": "<?php\n",
    "tests/Feature/UserTest.php": "<?php\n",
    ".env.example": f"AWS_KEY={SECRET}\n",
}
PYTEST = {
    "pyproject.toml": '[project]\nname = "x"\nrequires-python = ">=3.11"\ndependencies = ["pytest"]\n',
    "tests/test_a.py": "def test_a():\n    pass\n",
    "CLAUDE.md": "pytest -q で走らせる\n",
}


@pytest.fixture
def laravel(tmp_path):
    root = init_repo(tmp_path / "laravel", LARAVEL)
    write(root, ".env", f"AWS_KEY={SECRET}\nDB_URL={DB_URL}\n")  # 追跡しない秘密
    return root


@pytest.fixture
def plain(tmp_path):
    return init_repo(tmp_path / "plain", PYTEST)


def run(env, *args, cwd=None):
    p = subprocess.run([PY, str(SCRIPTS / "project-decl.py"), *args], capture_output=True, text=True, env=env, cwd=cwd)
    lines = p.stdout.strip().splitlines()
    try:
        out = json.loads(lines[-1]) if lines else None
    except ValueError:
        out = None
    return p.returncode, out, p.stdout + p.stderr


def measure(env, root, tmp_path, *extra):
    out = tmp_path / "work" / "measure.json"
    code, res, text = run(env, "measure", "--root", str(root), "--out", str(out), *extra)
    assert code == 0, text
    return out, json.loads(out.read_text(encoding="utf-8"))


def answers(tmp_path, data: dict) -> Path:
    p = tmp_path / "work" / "answers.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


LARAVEL_ANSWERS = {
    "test": {
        "value": {
            "suites": [
                {
                    "name": "phpunit",
                    "runner": "phpunit",
                    "command": "docker compose exec -T app ./vendor/bin/phpunit",
                    "container": {"service": "app", "compose_files": ["docker-compose.yml"]},
                    "needs": ["mysql"],
                    "paths": ["tests"],
                }
            ]
        },
        "reason": "AGENTS.md:3",
    },
    "branches": {"value": {"base": "main", "production": "main"}},
    "delivery": {"unknown": "デプロイの設定がリポジトリに無い"},
    "issues": {"value": {"primary": "redmine", "others": ["github"]}},
    "ndf_policies": {"value": {"doc_lint": False, "reject_md_wording_tests": False}},
}


def analyze(env, root, tmp_path, ans=None, *extra):
    mpath, m = measure(env, root, tmp_path)
    args = ["write", "--root", str(root), "--measure", str(mpath), *extra]
    if ans is not None:
        args += ["--answers", str(answers(tmp_path, ans))]
    code, out, text = run(env, *args)
    return code, out, text, m


def decl(root):
    return json.loads((root / ".ndf" / "project.json").read_text(encoding="utf-8"))


def snapshot(root):
    d = root / ".ndf"
    return {p.name: p.read_bytes() for p in sorted(d.iterdir())} if d.is_dir() else {}


def gh_calls(env):
    f = Path(env["FAKE_GH_LOG"])
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


KEYS = ("languages", "test", "test_duration", "ci", "services", "delivery", "issues", "checks", "ndf_policies", "instructions")


# --- AC1: 宣言の無いリポジトリで解析すると宣言ができる -----------------------------------


def test_analysis_creates_the_declaration_with_all_items(laravel, env, tmp_path):
    code, _, _ = run(env, "check", "--root", str(laravel))
    assert code == 2
    code, out, text, m = analyze(env, laravel, tmp_path, LARAVEL_ANSWERS)
    assert code == 0, text
    d = decl(laravel)
    assert all(k in d for k in KEYS) and d["version"] == 1
    assert str(laravel / ".ndf" / "project.json") in text
    for k in KEYS:
        assert f" {k}: " in text
    assert out["summary"].startswith("作成した")
    assert d["languages"][0] == {"name": "php", "frameworks": ["laravel 10"], "files": 2, "version": "8.3"}
    assert d["services"] == {"container": True, "compose_files": ["docker-compose.yml"], "databases": ["mysql"]}
    assert d["test"]["suites"][0]["container"]["service"] == "app"
    assert {"name": "pint", "config": "pint.json"} in d["checks"]["tools"]
    assert {"name": "larastan", "config": "composer.json"} in d["checks"]["tools"]
    assert d["delivery"] == {"unknown": "デプロイの設定がリポジトリに無い"}


def test_measure_reads_ci_junit_steps_and_required_checks(laravel, env, tmp_path):
    _, m = measure(env, laravel, tmp_path)
    got = {d["source"]: d["seconds"] for d in m["items"]["test_duration"]["value"]["measured"]}
    # 1000.5 + 200 + 99.5（入れ子の 3 は数えない）・テストの step 4 分 × 3 job（latest の step は数えない）
    assert got == {"ci-junit": 1300.0, "ci-steps": 720.0}
    ci = m["items"]["ci"]["value"]
    assert ci["required_checks"] == ["phpunit"]
    assert ci["workflows"] == [{"path": ".github/workflows/test.yml", "jobs": 3, "wall_seconds": 390.0}]
    assert "redmine" in m["items"]["issues"]["candidates"]
    assert any("main 1" in e["text"] for e in m["items"]["branches"]["evidence"])


def test_ndf_record_is_listed_as_a_source(plain, env, tmp_path):
    rec = Path(env["NDF_METRICS_DIR"]) / "acme--app" / "cross-refactoring-allocation.jsonl"
    rec.parent.mkdir(parents=True)
    rec.write_text("".join(json.dumps({"whole_test": {"init": s}}) + "\n" for s in (130, 140.2, 150)), encoding="utf-8")
    env["FAKE_GH_MODE"] = "fail"
    _, m = measure(env, plain, tmp_path)
    assert m["items"]["test_duration"]["value"]["measured"] == [
        {"seconds": 140.2, "source": "ndf-record", "detail": "NDF の実行の記録の直近 3 件の中央値"}
    ]


def test_ndf_record_skips_the_init_test_rows_and_scope_runs(plain, env, tmp_path):
    """#1385 I8: 着手前のテストの行（kind: init_test）と、範囲テストだけを走らせた実行の行（init が null）を数えない。"""
    rec = Path(env["NDF_METRICS_DIR"]) / "acme--app" / "cross-refactoring-allocation.jsonl"
    rec.parent.mkdir(parents=True)
    rows = [{"whole_test": {"init": s}} for s in (130, 140.2, 150)]
    rows += [{"schema": 2, "kind": "init_test", "mode": "scope", "seconds": 900.0}] * 3
    rows += [{"schema": 2, "kind": "run", "whole_test": {"init": None}}]
    rec.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    env["FAKE_GH_MODE"] = "fail"
    _, m = measure(env, plain, tmp_path)
    assert m["items"]["test_duration"]["value"]["measured"] == [
        {"seconds": 140.2, "source": "ndf-record", "detail": "NDF の実行の記録の直近 3 件の中央値"}
    ]


# --- AC4・I8: 新しい宣言では解析しない ------------------------------------------------


def test_fresh_declaration_check_is_0_writes_nothing_and_calls_no_gh(laravel, env, tmp_path):
    analyze(env, laravel, tmp_path, LARAVEL_ANSWERS)
    before = snapshot(laravel)
    Path(env["FAKE_GH_LOG"]).unlink()
    code, out, text = run(env, "check", "--root", str(laravel))
    assert code == 0, text
    assert out["status"] == "ok"
    assert snapshot(laravel) == before
    assert gh_calls(env) == []


# --- AC5・I9: 入力が変わると古い -----------------------------------------------------


@pytest.mark.parametrize("rel", [".github/workflows/test.yml", "composer.json"])
def test_committed_input_change_makes_it_stale_but_uncommitted_does_not(laravel, env, tmp_path, rel):
    analyze(env, laravel, tmp_path, LARAVEL_ANSWERS)
    write(laravel, rel, (laravel / rel).read_text(encoding="utf-8") + "\n")
    assert run(env, "check", "--root", str(laravel))[0] == 0  # 未コミットの編集では古くならない
    commit(laravel)
    code, out, text = run(env, "check", "--root", str(laravel))
    assert code == 2
    assert f"変わった入力: {rel}" in text


def test_non_input_change_keeps_it_fresh(laravel, env, tmp_path):
    analyze(env, laravel, tmp_path, LARAVEL_ANSWERS)
    write(laravel, "app/Models/Post.php", "<?php\n")
    commit(laravel)
    assert run(env, "check", "--root", str(laravel))[0] == 0


@pytest.mark.parametrize("change", ["add", "remove"])
def test_branch_structure_change_makes_it_stale(laravel, env, tmp_path, change):
    if change == "remove":
        git(laravel, "update-ref", "refs/remotes/origin/develop", "HEAD")
    analyze(env, laravel, tmp_path, LARAVEL_ANSWERS)
    if change == "add":
        git(laravel, "update-ref", "refs/remotes/origin/develop", "HEAD")
    else:
        git(laravel, "update-ref", "-d", "refs/remotes/origin/develop")
    code, _, text = run(env, "check", "--root", str(laravel))
    assert code == 2 and "ブランチの構成" in text


def test_older_analyzer_makes_it_stale(laravel, env, tmp_path, monkeypatch, capsys):
    analyze(env, laravel, tmp_path, LARAVEL_ANSWERS)
    spec = importlib.util.spec_from_file_location("project_decl_cli", SCRIPTS / "project-decl.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "ANALYZER", cli.ANALYZER + 1)
    with pytest.raises(SystemExit) as e:
        cli.main(["check", "--root", str(laravel)])
    assert e.value.code == 2 and "解析器の版が古い" in capsys.readouterr().out


def test_hand_written_declaration_without_analysis_is_stale(plain, env):
    write(plain, ".ndf/project.json", json.dumps({"version": 1, "ndf_policies": {"doc_lint": True, "reject_md_wording_tests": True}}))
    code, _, text = run(env, "check", "--root", str(plain))
    assert code == 2 and "analysis" in text


# --- AC6・I12: gh が使えない ---------------------------------------------------------


def test_without_gh_ci_items_are_unknown_and_measure_exits_0(laravel, env, tmp_path):
    env["FAKE_GH_MODE"] = "fail"
    _, m = measure(env, laravel, tmp_path)
    assert m["items"]["ci"]["status"] == "unknown" and "gh auth login" in m["items"]["ci"]["reason"]
    assert m["items"]["test_duration"]["status"] == "unknown"
    code, out, text = run(env, "write", "--root", str(laravel), "--measure", str(tmp_path / "work" / "measure.json"))
    assert code == 0, text
    d = decl(laravel)
    assert "unknown" in d["ci"] and "unknown" in d["test_duration"]
    assert "P3 test_duration: 不明" in text and "P4 ci: 不明" in text


# --- AC7・I1・I2: 手で書いた値を保つ ------------------------------------------------------


def test_hand_values_are_kept_and_mismatch_shows_both(laravel, env, tmp_path):
    hand = {"suites": [{"name": "unit", "runner": "phpunit", "command": "./vendor/bin/phpunit"}]}
    write(laravel, ".ndf/project.json", json.dumps({"version": 1, "test": hand}))
    wt = {"$schema": "x", "version": 1, "guard": {"allow_paths": ["docs/"]}, "production_branch": "release"}
    write(laravel, ".ndf/worktree.json", json.dumps(wt))
    code, out, text, _ = analyze(env, laravel, tmp_path, LARAVEL_ANSWERS)
    assert code == 0, text
    d = decl(laravel)
    assert d["test"] == hand
    mm = [i for i in out["items"] if i["kind"] == "mismatch"]
    assert {i["key"] for i in mm} == {"test", "worktree.json#production_branch"}
    test_mm = next(i for i in mm if i["key"] == "test")
    assert test_mm["declared"] == hand and test_mm["analyzed"]["suites"][0]["name"] == "phpunit"
    assert "宣言 " in text and " / 解析 " in text
    new_wt = json.loads((laravel / ".ndf" / "worktree.json").read_text(encoding="utf-8"))
    assert list(new_wt) == ["$schema", "version", "guard", "production_branch", "base_branch"]
    assert new_wt["production_branch"] == "release" and new_wt["base_branch"] == "main"
    assert new_wt["guard"] == wt["guard"]


def test_hand_secret_value_is_dropped_before_keeping(laravel, env, tmp_path):
    write(laravel, ".ndf/project.json", json.dumps({"version": 1, "issues": {"primary": "redmine", "others": [TOKEN]}}))
    code, out, text, _ = analyze(env, laravel, tmp_path, LARAVEL_ANSWERS)
    assert code == 0, text
    assert decl(laravel)["issues"] == {"unknown": "秘密の形"}
    assert {"kind": "secret", "key": "issues"} in out["items"]
    assert not [i for i in out["items"] if i["kind"] == "mismatch" and i["key"] == "issues"]
    assert TOKEN not in text and TOKEN not in (laravel / ".ndf" / "project.json").read_text(encoding="utf-8")


# --- I3: 不明で前の値を置き換えない ----------------------------------------------------


def test_unknown_does_not_replace_previous_analysis_value(laravel, env, tmp_path):
    analyze(env, laravel, tmp_path, LARAVEL_ANSWERS)
    ci = decl(laravel)["ci"]
    env["FAKE_GH_MODE"] = "fail"
    code, out, text, _ = analyze(env, laravel, tmp_path, LARAVEL_ANSWERS)
    assert code == 0, text
    assert decl(laravel)["ci"] == ci
    row = next(i for i in out["items"] if i["key"] == "ci")
    assert row["kind"] == "unknown" and row["kept_previous"] is True


# --- I4: 値と不明の両方を持つ項目を拒む -----------------------------------------------------


def test_value_and_unknown_together_is_broken(plain, env):
    write(plain, ".ndf/project.json", json.dumps({"version": 1, "test": {"suites": [], "unknown": "x"}}))
    code, _, text = run(env, "check", "--root", str(plain))
    assert code == 3 and "test" in text


def test_answer_with_wrong_shape_becomes_unknown_and_extra_keys_are_ignored(plain, env, tmp_path):
    bad = {"test": {"value": {"suites": [{"name": "x"}]}}, "languages": {"value": []}}
    code, out, text, _ = analyze(env, plain, tmp_path, bad)
    assert code == 0, text
    assert decl(plain)["test"]["unknown"].startswith("答えが形に合わない")
    assert {"kind": "ignored", "key": "languages"} in out["items"]


# --- AC9: 方針の検査を宣言で切る ------------------------------------------------------


@pytest.mark.parametrize(
    "policies, lint",
    [
        (None, False),
        ({"doc_lint": False, "reject_md_wording_tests": False}, False),
        ({"doc_lint": True, "reject_md_wording_tests": True}, True),
    ],
)
def test_doc_lint_follows_ndf_policies(tmp_path, env, policies, lint):
    root = init_repo(tmp_path / "docs", {"docs/a.md": "# 題\n"})
    if policies is not None:
        write(root, ".ndf/project.json", json.dumps({"version": 1, "ndf_policies": policies}))
    write(root, "docs/a.md", "# 題\n\n以前は別の形だった。\n")
    commit(root)
    p = subprocess.run([PY, str(SCRIPTS / "doc-lint.py"), "--base", "HEAD~1"], capture_output=True, text=True, cwd=root, env=env)
    out = json.loads(p.stdout.strip().splitlines()[-1])
    if lint:
        assert p.returncode == 1 and out["metrics"]["hits"] >= 1
    else:
        assert p.returncode == 0 and out["metrics"] == {"skipped": True}


def test_policy_reader_is_off_unless_true(tmp_path):
    sys.path.insert(0, str(SCRIPTS / "lib"))
    import project_decl

    assert project_decl.policy(tmp_path, "doc_lint") is False
    write(tmp_path, ".ndf/project.json", json.dumps({"ndf_policies": {"unknown": "x"}}))
    assert project_decl.policy(tmp_path, "doc_lint") is False
    write(tmp_path, ".ndf/project.json", "{broken")
    assert project_decl.policy(tmp_path, "doc_lint") is False
    write(tmp_path, ".ndf/project.json", json.dumps({"ndf_policies": {"doc_lint": True}}))
    assert project_decl.policy(tmp_path, "doc_lint") is True


# --- AC10・I11: 壊れた宣言を作り直さない --------------------------------------------------


@pytest.mark.parametrize("text", ["{not json", json.dumps({"version": 2}), json.dumps({"version": 1, "ci": {"workflows": 3}})])
def test_broken_declaration_stops_with_3_and_is_not_rewritten(plain, env, tmp_path, text):
    write(plain, ".ndf/project.json", text)
    for args in (["check"], ["check", "--force"]):
        code, out, _ = run(env, *args, "--root", str(plain))
        assert code == 3 and "直してから" in out["summary"]
    mpath, _ = measure(env, plain, tmp_path)
    code, out, _ = run(env, "write", "--root", str(plain), "--measure", str(mpath))
    assert code == 3
    assert (plain / ".ndf" / "project.json").read_text(encoding="utf-8") == text


def test_check_outside_git_is_1(tmp_path, env):
    d = tmp_path / "nogit"
    d.mkdir()
    code, _, _ = run(env, "check", cwd=d)
    assert code == 1


# --- AC11・I10: 時間切れでも止めない ---------------------------------------------------------


def test_budget_exhausted_items_are_timed_out_unknowns(laravel, env, tmp_path):
    _, m = measure(env, laravel, tmp_path, "--budget", "0")
    assert m["items"]["ci"] == {"status": "unknown", "reason": "時間切れ"}
    assert m["items"]["languages"] == {"status": "unknown", "reason": "時間切れ"}
    assert gh_calls(env) == []


def test_measure_refuses_out_inside_the_repository(plain, env):
    code, _, _ = run(env, "measure", "--root", str(plain), "--out", str(plain / ".ndf" / "m.json"))
    assert code == 1 and not (plain / ".ndf").exists()


# --- AC12: 指示書の案内 --------------------------------------------------------------


def test_old_ndf_guide_import_is_reported_and_files_unchanged(tmp_path, env):
    files = {
        ".claude/CLAUDE.md": "@../AGENTS.md\n@../CLAUDE.ndf.md\n",
        "AGENTS.md": "# ガイド\n",
        "CLAUDE.ndf.md": "<!-- NDF_PLUGIN_GUIDE_START_x -->\n# NDF Plugin (v2.1.0)\n",
    }
    root = init_repo(tmp_path / "old", files)
    _, m = measure(env, root, tmp_path)
    v = m["items"]["instructions"]["value"]
    assert {"from": ".claude/CLAUDE.md", "to": "CLAUDE.ndf.md"} in v["imports"]
    assert any("CLAUDE.ndf.md" in n and ".claude/CLAUDE.md が読み込んでいる" in n for n in v["notes"])
    code, _, text = run(env, "write", "--root", str(root), "--measure", str(tmp_path / "work" / "measure.json"))
    assert code == 0 and "案内: 古い NDF の案内 CLAUDE.ndf.md" in text
    assert git(root, "status", "--porcelain", "--", ".claude", "AGENTS.md", "CLAUDE.ndf.md") == ""


def test_missing_instructions_are_reported(tmp_path, env):
    root = init_repo(tmp_path / "none", {"a.py": "x = 1\n"})
    _, m = measure(env, root, tmp_path)
    assert m["items"]["instructions"]["value"]["files"] == []
    assert any("指示書" in n and "無い" in n for n in m["notes"])


# --- AC13・I6・I7: 秘密を読まない・書かない --------------------------------------------------


def test_secrets_never_reach_measure_or_declaration(laravel, env, tmp_path):
    env["FAKE_PR_BODY"] = f"token {TOKEN}"
    code, _, text, m = analyze(env, laravel, tmp_path, LARAVEL_ANSWERS)
    assert code == 0, text
    blobs = [
        (tmp_path / "work" / "measure.json").read_text(encoding="utf-8"),
        (laravel / ".ndf" / "project.json").read_text(encoding="utf-8"),
        text,
    ]
    for blob in blobs:
        for s in (SECRET, "hunter2secret", TOKEN):
            assert s not in blob
    assert ".env.example" in m["skipped_files"]
    assert any(e["text"] == "（伏せた）" for e in m["items"]["test"]["evidence"])


def test_secret_in_an_answer_is_dropped(plain, env, tmp_path):
    ans = {"issues": {"value": {"primary": "external", "others": [DB_URL]}}}
    code, out, text, _ = analyze(env, plain, tmp_path, ans)
    assert code == 0
    assert decl(plain)["issues"] == {"unknown": "秘密の形"} and "hunter2secret" not in text


def _project_lib(name):
    sys.path.insert(0, str(SCRIPTS))
    try:
        return importlib.import_module(f"project_lib.{name}")
    finally:
        sys.path.remove(str(SCRIPTS))


@pytest.mark.parametrize(
    "value",
    [
        "glpat-abcdefghij0123456789",
        "github_pat_11ABCDEFG0123456789abcdefghij",
        "AIzaSyA0123456789abcdefghijklmnopqrstu",
        "sk_live_0123456789abcdefghij",
        "npm_abcdefghijklmnopqrstuvwxyz0123456789",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig",
    ],
)
def test_secret_shapes_cover_common_credentials(value):
    secret = _project_lib("secret")
    assert secret.has_secret({"others": [f"x {value}"]})
    masked = secret.mask({"kind": "mismatch", "declared": ["ok", f"x {value}"], "analyzed": {"v": value}})
    assert value not in json.dumps(masked, ensure_ascii=False)
    assert masked["declared"][0] == "ok"


def test_assignment_shape_is_masked_but_keeps_the_declaration(laravel, env, tmp_path):
    secret = _project_lib("secret")
    assert not secret.has_secret("PASSWORD=testpassword pytest")
    assert "testpassword" not in json.dumps(secret.mask({"declared": "PASSWORD=testpassword pytest"}))
    analyze(env, laravel, tmp_path, LARAVEL_ANSWERS)
    d = decl(laravel)
    suites = [{"name": "hand", "runner": "phpunit", "command": "PASSWORD=testpassword make test"}]
    d["test"] = {"suites": suites}
    write(laravel, ".ndf/project.json", json.dumps(d, ensure_ascii=False, indent=2))
    analyze(env, laravel, tmp_path, LARAVEL_ANSWERS)
    assert decl(laravel)["test"] == {"suites": suites}


def test_git_is_not_started_after_the_deadline(tmp_path, monkeypatch):
    mr = _project_lib("measure_repo")
    tree = mr.Tree.__new__(mr.Tree)
    tree.root, tree.deadline, tree.files = tmp_path, 0.0, {"x"}
    monkeypatch.setattr(mr.subprocess, "run", lambda *a, **k: pytest.fail("締め切り後に git を起動した"))
    with pytest.raises(mr.TimeUp):
        tree.read("x")


def test_item_crossing_the_deadline_is_timed_out_not_measured(laravel, monkeypatch):
    spec = importlib.util.spec_from_file_location("project_decl_cli", SCRIPTS / "project-decl.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    def time_up(*_):
        raise cli.mr.TimeUp

    monkeypatch.setattr(cli.mr, "measure_test", time_up)
    monkeypatch.setattr(cli.mci, "measure_ci", time_up)
    m = cli.measure_project(laravel, 60.0)
    assert m["items"]["test"] == {"status": "unknown", "reason": "時間切れ"}
    assert m["items"]["ci"] == {"status": "unknown", "reason": "時間切れ"}


# --- AC14: schema はモデルの生成物 -------------------------------------------------------


def test_published_schema_matches_the_model(env):
    code, _, text = run(env, "schema")
    published = SCRIPTS.parent / "skills" / "development-workflow" / "schemas" / "project.schema.json"
    assert (
        published.read_text(encoding="utf-8")
        == subprocess.run([PY, str(SCRIPTS / "project-decl.py"), "schema"], capture_output=True, text=True, env=env).stdout
    )


# --- AC15・I5: 書き込まない --------------------------------------------------------------


def test_measure_writes_nothing_to_the_repo_and_only_gets_from_gh(laravel, env, tmp_path):
    before = git(laravel, "status", "--porcelain", "--ignored")
    measure(env, laravel, tmp_path)
    assert git(laravel, "status", "--porcelain", "--ignored") == before
    calls = gh_calls(env)
    assert calls and all(c[:3] == ["api", "--method", "GET"] for c in calls)


# --- AC16: 入力が変わった後の自動の再解析 ---------------------------------------------------


def test_reanalysis_updates_analysis_values_and_keeps_hand_edits(laravel, env, tmp_path):
    analyze(env, laravel, tmp_path, LARAVEL_ANSWERS)
    d = decl(laravel)
    d["test"] = {"suites": [{"name": "hand", "runner": "phpunit", "command": "make test"}]}
    write(laravel, ".ndf/project.json", json.dumps(d, ensure_ascii=False, indent=2))
    write(laravel, ".github/workflows/lint.yml", "on: push\njobs:\n  a:\n    runs-on: x\n  b:\n    runs-on: x\n")
    commit(laravel)
    assert run(env, "check", "--root", str(laravel))[0] == 2
    code, out, text, _ = analyze(env, laravel, tmp_path, LARAVEL_ANSWERS)
    assert code == 0, text
    d = decl(laravel)
    assert {"path": ".github/workflows/lint.yml", "jobs": 2} in d["ci"]["workflows"]
    assert d["test"]["suites"][0]["name"] == "hand"
    assert any(i["kind"] == "mismatch" and i["key"] == "test" for i in out["items"])
    assert run(env, "check", "--root", str(laravel))[0] == 0


def test_same_result_writes_nothing(laravel, env, tmp_path):
    analyze(env, laravel, tmp_path, LARAVEL_ANSWERS)
    before = snapshot(laravel)
    code, out, text, _ = analyze(env, laravel, tmp_path, LARAVEL_ANSWERS)
    assert code == 0 and out["summary"] == "書くものが無い"
    assert snapshot(laravel) == before


# --- AC17: 手動の再解析 ------------------------------------------------------------------


def test_force_reanalysis(laravel, env, tmp_path):
    analyze(env, laravel, tmp_path, LARAVEL_ANSWERS)
    d = decl(laravel)
    d["checks"] = {"tools": [{"name": "hand", "config": "x"}]}
    write(laravel, ".ndf/project.json", json.dumps(d, ensure_ascii=False, indent=2))
    before = snapshot(laravel)
    code, out, text = run(env, "check", "--force", "--root", str(laravel))
    assert code == 2 and "強制（--force）" in text
    assert snapshot(laravel) == before
    ans = {**LARAVEL_ANSWERS, "issues": {"value": {"primary": "github", "others": []}}}
    code, out, text, _ = analyze(env, laravel, tmp_path, ans)
    assert code == 0, text
    d = decl(laravel)
    assert d["issues"] == {"primary": "github", "others": []}
    assert d["checks"]["tools"] == [{"name": "hand", "config": "x"}]


# --- #1483 AC1・AC2: suite の種別 ---------------------------------------------------------


def _suite_decl(kind: str) -> dict:
    return {"version": 1, "test": {"suites": [{"name": "sc", "runner": "shellcheck", "command": "bash lint.sh", "kind": kind}]}}


def test_a_lint_kind_passes_the_check(plain, env):
    """AC1 — 種別 `lint` を書いた宣言は形の検証を通る（手書きなので古いとだけ言われる）。"""
    write(plain, ".ndf/project.json", json.dumps(_suite_decl("lint")))
    code, _, text = run(env, "check", "--root", str(plain))
    assert code == 2 and "suites[0].kind" not in text and "analysis" in text


def test_an_unknown_kind_fails_the_check_and_names_the_suite(plain, env):
    """AC2 — 2 値以外の種別は検証で落ち、どの suite のどのキーかを出す。"""
    write(plain, ".ndf/project.json", json.dumps(_suite_decl("unit")))
    code, _, text = run(env, "check", "--root", str(plain))
    assert code == 3 and "suites[0].kind" in text


# --- 現状固定: measure_repo.dependencies（I-004） --------------------------------------


def _deps_of(files: dict[str, str]):
    mr = _project_lib("measure_repo")
    tree = mr.Tree.__new__(mr.Tree)
    tree.root, tree.deadline, tree.files = None, float("inf"), set(files)
    tree.read = files.get
    return mr.dependencies(tree)


def test_dependencies_collects_every_manifest_kind_as_is():
    files = {
        "composer.json": json.dumps({"require": {"php": "^8.2", "laravel/framework": "^11.0"}, "require-dev": {"phpunit/phpunit": 10}}),
        "web/package.json": json.dumps(
            {"dependencies": {"react": "^18.2.0"}, "devDependencies": {"vitest": "1"}, "engines": {"node": ">=20"}}
        ),
        "deep/a/package.json": json.dumps({"dependencies": {"ignored": "1"}}),
        "pyproject.toml": (
            '[project]\nrequires-python = ">=3.11"\ndependencies = ["Django>=5.0", "requests[socks]~=2.31"]\n'
            "[project.optional-dependencies]\ndev = [\"pytest; python_version>'3'\"]\n"
            '[dependency-groups]\nlint = ["Ruff==0.6"]\nbad = "x"\n'
            '[tool.poetry.dependencies]\nFlask = "^3.0"\n'
            '[tool.poetry.dev-dependencies]\nBlack = "*"\n'
            '[tool.poetry.group.docs.dependencies]\nMkDocs = "1.6"\n'
        ),
        "requirements.txt": "# comment\nDjango==4.0\nnumpy>=1.26  # pinned\n\n",
        "requirements-dev.txt": "Mypy==1.10\n",
    }
    assert _deps_of(files) == {
        "php": {"php": "^8.2", "laravel/framework": "^11.0", "phpunit/phpunit": "10"},
        "javascript": {"react": "^18.2.0", "vitest": "1", "node": ">=20"},
        "python": {
            "django": "Django>=5.0",
            "requests": "requests[socks]~=2.31",
            "pytest": "pytest; python_version>'3'",
            "ruff": "Ruff==0.6",
            "flask": "^3.0",
            "black": "*",
            "mkdocs": "1.6",
            "python": ">=3.11",
            "numpy": "numpy>=1.26  # pinned",
            "mypy": "Mypy==1.10",
        },
    }


def test_dependencies_of_an_empty_or_broken_tree_are_empty_per_language():
    assert _deps_of({}) == {"php": {}, "javascript": {}, "python": {}}
    broken = {"composer.json": "{", "package.json": "[1]", "pyproject.toml": "[[", "requirements.txt": ""}
    assert _deps_of(broken) == {"php": {}, "javascript": {}, "python": {}}
