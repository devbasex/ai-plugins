"""pr-steps.py（#858）: plan / commit / push / create / update / report。

一時の git リポジトリと bare の origin を作り、gh は PATH の先頭に置いた偽物で置き換える。
偽物は FAKE_GH_PRS（ブランチ → OPEN の PR の配列）と FAKE_GH_VIEW（番号 → gh pr view の JSON）を読み、
FAKE_GH_GRAPHQL_LIMIT が立っていれば pr list / pr create / pr view を GraphQL の上限の文言で落とす。
呼ばれた引数は FAKE_GH_LOG へ 1 行ずつ残す。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
PY = sys.executable

FAKE_GH = """#!{py}
import json, os, sys
a = sys.argv[1:]
open(os.environ["FAKE_GH_LOG"], "a").write(json.dumps(a, ensure_ascii=False) + "\\n")
limit = os.environ.get("FAKE_GH_GRAPHQL_LIMIT") == "1"
prs = json.loads(os.environ.get("FAKE_GH_PRS", "{{}}"))
views = json.loads(os.environ.get("FAKE_GH_VIEW", "{{}}"))
def graphql_fail():
    sys.stderr.write("error checking for existing pull request: GraphQL: API rate limit already exceeded\\n")
    sys.exit(1)
if a[:2] == ["repo", "view"]:
    print(json.dumps({{"owner": {{"login": "o"}}, "name": "r"}})); sys.exit(0)
if a[:2] == ["pr", "list"]:
    if limit: graphql_fail()
    head = a[a.index("--head") + 1]
    print(json.dumps(prs.get(head, []))); sys.exit(0)
if a[:2] == ["pr", "create"]:
    if limit: graphql_fail()
    print("https://github.com/o/r/pull/42"); sys.exit(0)
if a[:2] == ["pr", "edit"]:
    sys.exit(0)
if a[:2] == ["pr", "view"]:
    if limit: graphql_fail()
    if a[2] in views:
        print(json.dumps(views[a[2]])); sys.exit(0)
    sys.stderr.write("no pull requests found\\n"); sys.exit(1)
if a[0] == "api":
    path = a[1]
    if "pulls?" in path:
        print("[]"); sys.exit(0)
    if path.endswith("/pulls") and "--input" in a:
        body = json.load(open(a[a.index("--input") + 1]))
        print(json.dumps({{"number": 43, "html_url": "https://github.com/o/r/pull/43", "title": body["title"]}}))
        sys.exit(0)
    if "/pulls/" in path and path.rsplit("/", 1)[-1] in views:
        v = views[path.rsplit("/", 1)[-1]]
        print(json.dumps({{"number": v["number"], "title": v["title"], "html_url": v["url"], "draft": v["isDraft"],
                          "base": {{"ref": v["baseRefName"]}}, "head": {{"ref": v["headRefName"]}}, "body": v["body"]}}))
        sys.exit(0)
sys.stderr.write("fake gh: " + " ".join(a) + "\\n")
sys.exit(1)
"""


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


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
    e["FAKE_GH_PRS"] = "{}"
    e["FAKE_GH_VIEW"] = "{}"
    e.pop("FAKE_GH_GRAPHQL_LIMIT", None)
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        e.pop(k, None)
    return e


@pytest.fixture
def repo(tmp_path):
    """develop を起点に feature/x で 1 コミット進めた作業ツリー。origin は bare。"""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "develop", str(origin)], check=True)
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "develop")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "config", "commit.gpgsign", "false")
    write(root, ".ndf/worktree.json", json.dumps({"base_branch": "develop", "production_branch": "main"}))
    write(root, "keep.txt", "head\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    git(root, "remote", "add", "origin", str(origin))
    git(root, "push", "-q", "-u", "origin", "develop")
    git(root, "checkout", "-q", "-b", "feature/x")
    write(root, "a.txt", "a\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Add: a を足す（#858）")
    return root


def call(args, env, cwd):
    p = subprocess.run([PY, str(SCRIPTS / "pr-steps.py"), *args], capture_output=True, text=True, env=env, cwd=cwd)
    lines = p.stdout.strip().splitlines()
    out = json.loads(lines[-1]) if lines else None
    return p.returncode, out, p.stderr


def gh_calls(env):
    return [json.loads(l) for l in Path(env["FAKE_GH_LOG"]).read_text().splitlines()]


def check_shape(out):
    assert set(out) >= {"tool", "status", "summary", "items", "metrics"}
    assert out["tool"] == "pr" and out["status"] in ("ok", "gate", "stopped")


# --- plan ------------------------------------------------------------------------

def test_plan_collects_branch_base_and_changes(repo, env):
    write(repo, "b.txt", "b\n")
    code, out, err = call(["plan", "--draft"], env, repo)
    assert code == 0, err
    check_shape(out)
    m = out["metrics"]
    assert m["branch"] == "feature/x" and m["base"] == "develop" and m["draft"] is True
    assert m["existing_pr"] is None and m["uncommitted"] == 1 and m["commits"] == 1 and m["files"] == 1
    assert [i["result"] for i in out["items"] if i["kind"] == "existing_pr"] == ["create"]


def test_plan_on_base_branch_redirects_to_worktree(repo, env):
    git(repo, "checkout", "-q", "develop")
    code, out, _ = call(["plan"], env, repo)
    assert code == 3 and out["status"] == "stopped" and out["metrics"]["redirect"] == "worktree"
    assert "worktree" in out["next"]


def test_plan_non_main_base_redirects_to_cherry_pick(repo, env):
    code, out, _ = call(["plan", "--base", "qa/staging"], env, repo)
    assert code == 3 and out["metrics"]["redirect"] == "cherry-pick-pr" and "qa/staging" in out["next"]


def test_plan_closing_word_in_message_stops(repo, env):
    code, out, _ = call(["plan", "--message", "Fix: 直す Fixes #12"], env, repo)
    assert code == 1 and out["status"] == "stopped" and out["metrics"]["closing_words_in_message"] == ["Fixes #12"]


def test_plan_reports_existing_pr_and_closing_words_in_commits(repo, env):
    write(repo, "c.txt", "c\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Add: c\n\nCloses #5")
    env["FAKE_GH_PRS"] = json.dumps({"feature/x": [{"number": 7, "url": "https://github.com/o/r/pull/7",
                                                    "isDraft": True, "baseRefName": "develop"}]})
    code, out, _ = call(["plan"], env, repo)
    assert code == 0 and out["metrics"]["existing_pr"]["number"] == 7
    assert len(out["metrics"]["commits_with_closing_words"]) == 1
    assert [i["result"] for i in out["items"] if i["kind"] == "existing_pr"] == ["update"]


def test_plan_rest_fallback_when_graphql_limited(repo, env):
    env["FAKE_GH_GRAPHQL_LIMIT"] = "1"
    code, out, err = call(["plan"], env, repo)
    assert code == 0, err
    assert out["metrics"]["existing_pr"] is None
    assert any(c[0] == "api" and "pulls?head=o:feature/x" in c[1] for c in gh_calls(env))


# --- commit / push ---------------------------------------------------------------

def test_commit_adds_all_and_refuses_closing_words(repo, env):
    write(repo, "d.txt", "d\n")
    code, out, _ = call(["commit", "--message", "Add: d Resolves #3"], env, repo)
    assert code == 1 and out["metrics"]["closing_words"] == ["Resolves #3"]
    code, out, _ = call(["commit", "--message", "Add: d を足す（#858）"], env, repo)
    assert code == 0 and out["metrics"]["committed"] is True
    assert git(repo, "log", "-1", "--format=%s").strip() == "Add: d を足す（#858）"
    code, out, _ = call(["commit", "--message", "何も無い"], env, repo)
    assert code == 0 and out["metrics"]["committed"] is False


def test_push_sets_upstream(repo, env):
    code, out, err = call(["push"], env, repo)
    assert code == 0, err
    assert out["metrics"]["retried_with_credential_fallback"] is False
    assert git(repo, "rev-parse", "--abbrev-ref", "feature/x@{upstream}").strip() == "origin/feature/x"


def test_push_failure_retries_then_stops(repo, env):
    git(repo, "remote", "set-url", "origin", str(repo.parent / "missing.git"))
    code, out, _ = call(["push"], env, repo)
    assert code == 1 and out["status"] == "stopped" and out["metrics"]["retried_with_credential_fallback"] is True


# --- create / update -------------------------------------------------------------

def body_file(tmp_path, text="## Summary\n\n- 要点\n\nCloses #858\n\n## Test plan\n\n- [x] pytest\n- [ ] lint\n"):
    p = tmp_path / "body.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_create_makes_draft_pr_and_appends_review_mark(repo, env, tmp_path):
    b = body_file(tmp_path)
    code, out, err = call(["create", "--title", "題", "--body-file", str(b), "--draft"], env, repo)
    assert code == 0, err
    m = out["metrics"]
    assert m["action"] == "created" and m["number"] == 42 and m["closing_issues_in_body"] == ["#858"]
    create = next(c for c in gh_calls(env) if c[:2] == ["pr", "create"])
    assert "--draft" in create and create[create.index("--base") + 1] == "develop"
    sent = Path(create[create.index("--body-file") + 1]).read_text()
    assert sent.rstrip().endswith("<!-- I want to review in Japanese. -->")


def test_create_updates_when_pr_exists(repo, env, tmp_path):
    env["FAKE_GH_PRS"] = json.dumps({"feature/x": [{"number": 7, "url": "https://github.com/o/r/pull/7",
                                                    "isDraft": False, "baseRefName": "develop"}]})
    code, out, _ = call(["create", "--title", "題", "--body-file", str(body_file(tmp_path))], env, repo)
    assert code == 0 and out["metrics"]["action"] == "updated" and out["metrics"]["number"] == 7
    assert any(c[:3] == ["pr", "edit", "7"] for c in gh_calls(env))


def test_create_falls_back_to_rest_on_graphql_limit(repo, env, tmp_path):
    env["FAKE_GH_GRAPHQL_LIMIT"] = "1"
    code, out, err = call(["create", "--title", "題", "--body-file", str(body_file(tmp_path))], env, repo)
    assert code == 0, err
    assert out["metrics"]["action"] == "created_rest" and out["metrics"]["number"] == 43


def test_create_without_title_is_unreadable(repo, env, tmp_path):
    code, out, _ = call(["create", "--body-file", str(body_file(tmp_path))], env, repo)
    assert code == 2 and out["status"] == "stopped"


def test_update_without_pr_is_precondition(repo, env, tmp_path):
    code, out, _ = call(["update", "--body-file", str(body_file(tmp_path))], env, repo)
    assert code == 3 and out["status"] == "stopped"


# --- report ----------------------------------------------------------------------

def test_report_builds_six_lines(repo, env, tmp_path):
    env["FAKE_GH_VIEW"] = json.dumps({"7": {"number": 7, "title": "題", "url": "https://github.com/o/r/pull/7",
                                            "isDraft": True, "baseRefName": "develop", "headRefName": "feature/x",
                                            "body": body_file(tmp_path).read_text()}})
    code, out, err = call(["report", "7"], env, repo)
    assert code == 0, err
    m = out["metrics"]
    assert m["commits"] == 1 and m["files"] == 1 and m["test_plan_total"] == 2 and m["test_plan_done"] == 1
    text = out["next"]
    assert text.startswith("PR #7 題") and text.rstrip().endswith("URL: https://github.com/o/r/pull/7")
    assert "develop ← feature/x（ドラフト: あり）" in text and "Test plan 2 件（実行済み 1 件）" in text


def test_report_uses_rest_when_graphql_limited(repo, env, tmp_path):
    env["FAKE_GH_GRAPHQL_LIMIT"] = "1"
    env["FAKE_GH_VIEW"] = json.dumps({"7": {"number": 7, "title": "題", "url": "https://github.com/o/r/pull/7",
                                            "isDraft": False, "baseRefName": "develop", "headRefName": "feature/x",
                                            "body": ""}})
    code, out, err = call(["report", "7"], env, repo)
    assert code == 0, err
    assert "ドラフト: なし" in out["next"] and "（Summary が無い）" in out["next"]


def test_report_without_pr_is_precondition(repo, env):
    code, out, _ = call(["report"], env, repo)
    assert code == 3 and out["status"] == "stopped"


@pytest.mark.parametrize("args", [["plan", "--bogus"], ["update"], []])
def test_bad_calls_exit_2(repo, env, args):
    code, _, _ = call(args, env, repo)
    assert code == 2


# --- ミッションの宛て先とモードの 1 行（#1005） --------------------------------------

def test_plan_mission_base_is_accepted_without_review(repo, env):
    git(repo, "push", "-q", "origin", "develop:mission/m1")
    git(repo, "fetch", "-q", "origin")
    code, out, err = call(["plan", "--base", "mission/m1"], env, repo)
    assert code == 0, err
    assert out["metrics"]["target"] == "mission" and out["metrics"]["review"] is False


def test_plan_develop_base_needs_review(repo, env):
    code, out, err = call(["plan"], env, repo)
    assert code == 0, err
    assert out["metrics"]["target"] == "develop" and out["metrics"]["review"] is True


def test_create_writes_mode_line_before_review_mark(repo, env, tmp_path):
    b = body_file(tmp_path)
    code, out, err = call(["create", "--title", "題", "--body-file", str(b), "--base", "mission/m1",
                           "--mode", "standard", "--stages", "設計,実装"], env, repo)
    assert code == 0, err
    create = next(c for c in gh_calls(env) if c[:2] == ["pr", "create"])
    assert create[create.index("--base") + 1] == "mission/m1"
    lines = [l for l in Path(create[create.index("--body-file") + 1]).read_text().splitlines() if l.strip()]
    assert lines[-2:] == ["モード: standard / 通した工程: 設計 → 実装", "<!-- I want to review in Japanese. -->"]


def test_update_replaces_existing_mode_line(repo, env, tmp_path):
    env["FAKE_GH_PRS"] = json.dumps({"feature/x": [{"number": 7, "url": "https://github.com/o/r/pull/7",
                                                    "isDraft": True, "baseRefName": "develop"}]})
    b = body_file(tmp_path, "## Summary\n\n- 要点\n\nモード: light / 通した工程: 実装\n")
    code, _, err = call(["update", "--body-file", str(b), "--mode", "standard",
                         "--stages", "構造改善,実装レビュー"], env, repo)
    assert code == 0, err
    edit = next(c for c in gh_calls(env) if c[:2] == ["pr", "edit"])
    sent = Path(edit[edit.index("--body-file") + 1]).read_text()
    assert [l for l in sent.splitlines() if l.startswith("モード: ")] == [
        "モード: standard / 通した工程: 構造改善 → 実装レビュー"]


# --- 利用者向けの変化の節（#1054）---

def test_template_has_user_changes_section(repo, env, tmp_path):
    out_file = tmp_path / "tpl.md"
    code, out, err = call(["template", "--out", str(out_file)], env, repo)
    assert code == 0, err
    check_shape(out)
    heads = [l for l in out_file.read_text(encoding="utf-8").splitlines() if l.startswith("## ")]
    assert heads == ["## Summary", "## 利用者向けの変化", "## Test plan"]
    code, out, _ = call(["template", "--out", str(out_file)], env, repo)
    assert code == 3


def test_create_reports_missing_user_changes_section(repo, env, tmp_path):
    code, out, err = call(["create", "--title", "題", "--body-file", str(body_file(tmp_path))], env, repo)
    assert code == 0, err
    assert out["metrics"]["user_changes"] is False and "利用者向けの変化" in out["next"]
    b = body_file(tmp_path, "## Summary\n\n- 要点\n\n## 利用者向けの変化\n\n- できること\n\n## Test plan\n\n- [x] t\n")
    code, out, err = call(["create", "--title", "題", "--body-file", str(b)], env, repo)
    assert code == 0, err
    assert out["metrics"]["user_changes"] is True and "next" not in out


def test_user_changes_heading_inside_fence_does_not_count(repo, env, tmp_path):
    """囲みの中の `## 利用者向けの変化` は節にしない（lib/md.py。行の字面で見ていた頃は節があるとみなした）。"""
    b = body_file(tmp_path, "## Summary\n\n```md\n## 利用者向けの変化\n```\n\n## Test plan\n\n- [x] t\n")
    code, out, err = call(["create", "--title", "題", "--body-file", str(b)], env, repo)
    assert code == 0, err
    assert out["metrics"]["user_changes"] is False
