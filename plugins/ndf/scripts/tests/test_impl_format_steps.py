"""実装のプランの整形と範囲の静的解析のステップ・送信の前の整形（#1651 の 1651-3〜1651-7、#1507 の 1507-1〜1507-5）。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SUPERVISE = SCRIPTS / "supervise.py"
TEST_RUN = SCRIPTS / "test-run.py"
PY = sys.executable

sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import formatting  # noqa: E402
import result_posts  # noqa: E402
from supervise_lib import decl, templates  # noqa: E402

# 行末の空白を消す整形。`check` は行末の空白があれば 1 で落ちる検査（pre-push に使う）
STRIP = "sed -i 's/[[:space:]]*$//' *.txt"
CHECK = "! grep -n '[[:space:]]$' *.txt"
LINT_SUITE = {"name": "sh", "runner": "sh", "kind": "lint", "scope_command": "sh -n {paths}", "command": "true", "paths": ["*.sh"]}
TEST_SUITE = {"name": "unit", "runner": "sh", "kind": "test", "scope_command": "true {paths}", "command": "true"}


def git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def make_repo(root: Path, supervise: dict | None = None, project: dict | None = None) -> Path:
    (root / ".ndf").mkdir(parents=True)
    (root / ".ndf" / "worktree.json").write_text(json.dumps({"base_branch": "main"}))
    if supervise is not None:
        (root / ".ndf" / "supervise.json").write_text(json.dumps(supervise))
    if project is not None:
        (root / ".ndf" / "project.json").write_text(json.dumps(project))
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / "a.txt").write_text("a\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "init")
    return root


def new_impl(root: Path, *extra: str) -> dict:
    out = root.parent / "plan.json"
    cmd = [PY, str(SUPERVISE), "new", "impl", "--issue", "5", "--worktree", str(root), "--tests", "t", "--title", "T", "--mode", "light"]
    p = subprocess.run([*cmd, *extra, "--out", str(out)], capture_output=True, text=True, cwd=root)
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(out.read_text())


def by_id(plan: dict) -> dict:
    return {s["id"]: s for s in plan["steps"]}


SV = {"test": {"command": "true {paths}"}}
FULL = {**SV, "format": [{"name": "strip", "command": STRIP}], "sync_checks": [{"name": "check", "command": "true"}]}


# ---------- 雛形（1651-3〜1651-7） ----------


def test_steps_follow_the_declarations(tmp_path):
    """1651-4・1651-5: impl → base-sync → format → sync → lint-limited → test-limited。修正はすべて並びの先頭へ戻る。"""
    plan = new_impl(make_repo(tmp_path / "wt", FULL, {"test": {"suites": [TEST_SUITE, LINT_SUITE]}}))
    ids = [s["id"] for s in plan["steps"]]
    assert ids[: ids.index("test-limited") + 1] == [
        *["impl", "base-sync", "fix-base", "format", "fix-format", "sync", "fix-sync", "lint-limited", "fix-lint", "test-limited"]
    ]
    st = by_id(plan)
    assert [st[i]["next"] for i in ("base-sync", "format", "sync", "lint-limited")] == ["format", "sync", "lint-limited", "test-limited"]
    assert st["base-sync"]["on_exit"]["1"] == "fix-base"
    assert st["format"]["on_fail"] == "fix-format" and st["format"]["on_exit"] == {"2": "stop"}
    assert st["lint-limited"]["on_fail"] == "fix-lint" and st["lint-limited"]["cmd"].endswith("--only-kind lint")
    assert st["test-limited"]["cmd"].endswith("--only-kind test")
    assert st["fix-format"]["next"] == st["fix-sync"]["next"] == st["fix-lint"]["next"] == st["fix"]["next"] == "format"


def test_no_declarations_keep_the_steps(tmp_path):
    """1651-6・I7: 整形と静的解析の宣言が無ければ今と同じ並び（impl → base-sync → test-limited）。"""
    plan = new_impl(make_repo(tmp_path / "wt", SV))
    st = by_id(plan)
    assert not {"format", "fix-format", "sync", "lint-limited", "fix-lint"} & set(st)
    assert st["base-sync"]["next"] == "test-limited" and st["fix"]["next"] == "test-limited"
    assert "--only-kind" not in st["test-limited"]["cmd"]


def test_lint_without_format_points_the_fixes_at_a_real_step(tmp_path):
    """1651-4・1651-5（行き先）: format の宣言が無ければ fix-lint と fix は sync（無ければ lint-limited）へ戻る。"""
    project = {"test": {"suites": [TEST_SUITE, LINT_SUITE]}}
    st = by_id(new_impl(make_repo(tmp_path / "a", {**SV, "sync_checks": [{"name": "c", "command": "true"}]}, project)))
    assert st["fix-lint"]["next"] == st["fix"]["next"] == st["base-sync"]["next"] == "sync"
    st = by_id(new_impl(make_repo(tmp_path / "b", SV, project)))
    assert st["fix-lint"]["next"] == st["fix"]["next"] == st["base-sync"]["next"] == "lint-limited"


def test_round_only_has_no_scoped_lint(tmp_path):
    """1651-5（round-only）: 範囲に当てない戦略では lint-limited を置かず、test-limited は今のまま。"""
    project = {"test": {"strategy": "round-only", "suites": [{"name": "u", "runner": "sh", "command": "true"}, LINT_SUITE]}}
    st = by_id(new_impl(make_repo(tmp_path / "wt", SV, project)))
    assert "lint-limited" not in st and "--only-kind" not in st["test-limited"]["cmd"]


def prompt_ns(**kw) -> argparse.Namespace:
    return argparse.Namespace(**{"issue": [5], "prompt": None, "prompt_file": None, "files": None, **kw})


def test_glossary_line_follows_the_glossary(tmp_path):
    """1651-3: 用語集の設定と用語集が揃うときだけ glossary.py check の行が入る。"""
    assert "glossary.py check" in templates.impl_prompt(prompt_ns(glossary_ready=True))
    assert "glossary.py check" not in templates.impl_prompt(prompt_ns(glossary_ready=False))
    assert decl.glossary_ready([tmp_path]) is False
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "glossary.json").write_text(json.dumps({"version": 1, "format": "json", "source": "g.json", "document": "g.md"}))
    assert decl.glossary_ready([tmp_path]) is False  # 設定はあるが用語集が無い
    p = subprocess.run([PY, str(SCRIPTS / "glossary.py"), "init", "--root", str(tmp_path)], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert decl.glossary_ready([tmp_path]) is True


def test_prompt_does_not_tell_the_worker_to_format(tmp_path):
    """1651-7・I3: 雛形の指示に宣言の整形・静的解析のコマンドが現れない。"""
    plan = new_impl(make_repo(tmp_path / "wt", FULL, {"test": {"suites": [TEST_SUITE, LINT_SUITE]}}))
    prompt = by_id(plan)["impl"]["prompt"]
    assert STRIP not in prompt and LINT_SUITE["scope_command"].split(" {")[0] not in prompt and "check-lint" not in prompt


# ---------- test-run.py scope --only-kind（1651-5） ----------


def run_scope(root: Path, *args: str) -> tuple[int, dict]:
    p = subprocess.run([PY, str(TEST_RUN), "scope", *args, "--root", str(root)], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def test_only_kind_runs_one_kind_on_its_own_range(tmp_path):
    """lint は --changed へ、test は --paths へ当てる。種別を絞った後に打つものが無ければ 0。"""
    marks = tmp_path / "marks"
    test = {**TEST_SUITE, "scope_command": f"echo test {{paths}} >> {marks}"}
    lint = {**LINT_SUITE, "scope_command": f"echo lint {{paths}} >> {marks}"}
    root = make_repo(tmp_path / "wt", SV, {"test": {"suites": [test, lint]}})
    code, _ = run_scope(root, "--paths", "tests/t.py", "--changed", "x.sh", "--only-kind", "lint")
    assert code == 0 and marks.read_text().splitlines() == ["lint x.sh"]
    code, _ = run_scope(root, "--paths", "tests/t.py", "--changed", "x.sh", "--only-kind", "test")
    assert code == 0 and marks.read_text().splitlines() == ["lint x.sh", "test tests/t.py"]
    code, out = run_scope(root, "--paths", "tests/t.py", "--changed", "README.md", "--only-kind", "lint")
    assert code == 0 and out["status"] == "ok"  # 範囲に入るファイルが無い静的解析は通る


def test_only_lint_declared_lets_the_test_step_pass(tmp_path):
    """1651-5（静的解析だけの宣言）: テストの suite が無ければ --only-kind test は 0。付けなければ今のまま止まる。"""
    root = make_repo(tmp_path / "wt", SV, {"test": {"suites": [LINT_SUITE]}})
    assert run_scope(root, "--paths", "t", "--changed", "README.md", "--only-kind", "test")[0] == 0
    code, out = run_scope(root, "--paths", "t", "--changed", "README.md")
    assert code != 0 and out["status"] == "stopped"


def test_round_only_only_kind_runs_the_kind(tmp_path):
    marks = tmp_path / "marks"
    suites = [{"name": "u", "runner": "sh", "command": f"echo u >> {marks}"}, {**LINT_SUITE, "command": f"echo l >> {marks}"}]
    root = make_repo(tmp_path / "wt", SV, {"test": {"strategy": "round-only", "suites": suites}})
    assert run_scope(root, "--paths", "t", "--only-kind", "lint")[0] == 0
    assert marks.read_text().splitlines() == ["l"]


# ---------- supervise.py format（1651-4） ----------


def run_format(root: Path, *args: str) -> tuple[int, dict]:
    p = subprocess.run([PY, str(SUPERVISE), "format", "--root", str(root), *args], capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def test_format_commits_only_what_the_format_changed(tmp_path):
    """1651-4・I6: 整形の違反のコミットへ整形のコミットを足す。整形の前から変わっていたファイルは入れない。"""
    root = make_repo(tmp_path / "wt", FULL)
    (root / "b.txt").write_text("b   \n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Add: b")
    (root / "a.txt").write_text("worker   \n")  # worker が残した未コミットの変更
    code, out = run_format(root, "--commit")
    assert code == 0, out
    commit = next(i for i in out["items"] if i["name"] == "commit")
    assert commit["files"] == ["b.txt"] and git(root, "rev-parse", "HEAD") == commit["sha"]
    assert git(root, "show", "HEAD:b.txt") == "b" and git(root, "status", "--porcelain") == "M a.txt"
    assert git(root, "rev-list", "--count", "HEAD") == "3"
    code, out = run_format(root, "--commit")  # 変わるものが無ければコミットしない
    assert code == 0 and git(root, "rev-list", "--count", "HEAD") == "3"


def test_format_commits_literal_names(tmp_path):
    """日本語名（core.quotePath で引用される）と glob の文字を含む名前も、その名前のファイルだけをコミットする。"""
    root = make_repo(tmp_path / "wt")
    for rel in ("設計.txt", "[i].txt", "i.txt"):
        (root / rel).write_text("x\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Add: names")
    (root / "i.txt").write_text("user   \n")  # 整形の前からある利用者の変更。`[i].txt` の glob に一致する
    cmd = "sed -i 's/x/y   /' 設計.txt '[i].txt' && sed -i 's/[[:space:]]*$//' 設計.txt '[i].txt'"
    out = formatting.apply_format(root, [formatting.FormatCommand("f", cmd)])
    assert out.status == formatting.OK, out
    assert out.files == ["[i].txt", "設計.txt"]
    assert git(root, "-c", "core.quotePath=false", "status", "--porcelain") == "M i.txt"


@pytest.mark.parametrize("exit_code, want", [(1, 1), (3, 2)])
def test_format_failures_stop(tmp_path, exit_code, want):
    root = make_repo(tmp_path / "wt", {**SV, "format": [{"name": "f", "command": f"echo boom; exit {exit_code}"}]})
    code, out = run_format(root, "--commit")
    assert code == want and out["status"] == "stopped" and "boom" in out["summary"]


def test_format_without_a_declaration_stops(tmp_path):
    assert run_format(make_repo(tmp_path / "wt", SV), "--commit")[0] == 2


def test_both_readers_share_one_shape_check(tmp_path):
    """1507-5・I8: 雛形（decl）と送信（push_fix）が同じ関数で同じ宣言を読み、同じ誤りを出す。"""
    bad = {"format": [{"name": "x"}]}
    with pytest.raises(decl.DeclError) as e:
        decl.format_of(bad)
    root = make_repo(tmp_path / "wt", bad)
    assert str(e.value) in formatting.format_before_push(root)


# ---------- 送信の前の整形（1507-1〜1507-4） ----------


@pytest.fixture
def pushing(tmp_path):
    """整形の違反（行末の空白）を持つ修正のコミットが HEAD の作業ツリー。origin の pre-push が違反を拒む。"""

    def make(command: str = STRIP):
        root = make_repo(tmp_path / "wt", {**SV, "format": [{"name": "strip", "command": command}]})
        origin = tmp_path / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        git(root, "remote", "add", "origin", str(origin))
        git(root, "push", "-q", "-u", "origin", "main")
        hooks = tmp_path / "hooks"
        hooks.mkdir()
        (hooks / "pre-push").write_text(f"#!/bin/sh\n{CHECK}\n")
        (hooks / "pre-push").chmod(0o755)
        git(root, "config", "core.hooksPath", str(hooks))
        (root / "a.txt").write_text("fixed  \n")
        git(root, "commit", "-q", "-am", "fix: 修正")
        return root, git(root, "rev-parse", "HEAD")

    return make


def test_push_fix_formats_before_sending(pushing):
    """1507-1・1507-2・I5: 整形のコミットを上に足して送り、fix_commit は送り先の祖先のまま。"""
    root, fix = pushing()
    got = result_posts.push_fix(root, "main", fix)
    assert got.ok and got.contains, got.detail
    assert git(root, "rev-parse", "HEAD^") == fix and git(root, "show", "origin/main:a.txt") == "fixed"
    assert got.sent == git(root, "rev-parse", "HEAD")


def test_push_fix_adds_nothing_when_clean(pushing):
    """1507-3: 整形で変わるファイルが無ければコミットを足さない。"""
    root, fix = pushing("true")
    (root / "a.txt").write_text("fixed\n")
    git(root, "commit", "-q", "-am", "fix: 整形済み")
    fix = git(root, "rev-parse", "HEAD")
    assert result_posts.push_fix(root, "main", fix).ok and git(root, "rev-parse", "HEAD") == fix


def test_push_fix_stops_when_the_format_fails(pushing):
    """1507-4: 整形のコマンドが 0 以外なら送らず、理由に名前と出力の末尾。"""
    root, fix = pushing("echo 直らない; exit 1")
    got = result_posts.push_fix(root, "main", fix)
    assert not got.ok and not got.pushed and "strip" in got.detail and "直らない" in got.detail
    assert git(root, "rev-parse", "origin/main") != fix
