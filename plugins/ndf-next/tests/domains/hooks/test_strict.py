"""`strict` の guard（本番チャネルへのマージ）を、宛先のリポジトリの宣言と入口の経路で確かめる（M3-H6・#2446・#2838）。"""

from __future__ import annotations

import io
import json
import sys

import pytest

from ndf.domains.hooks import boot, strict

from ._hooks import AUTO, MANUAL, bash, call_entry, deny_reason, make_repo


def _check(cwd, command):
    return strict.check(bash(cwd, command), str(cwd))


def test_direct_merge_in_auto_delivery_repo_is_denied(tmp_path):
    repo = make_repo(tmp_path / "r", delivery=AUTO)
    got = _check(repo, "gh pr merge 12 --merge")
    assert got is not None and got.kind == "deny" and "自動で本番へ届く" in got.reason


def test_merge_in_manual_delivery_repo_passes(tmp_path):
    repo = make_repo(tmp_path / "r", delivery=MANUAL)
    assert _check(repo, "gh pr merge 12 --merge") is None


@pytest.mark.parametrize("flag", ["-R other/repo", "--repo other/repo", "--repo=other/repo", "-Rother/repo"])
def test_repo_flag_naming_another_repository_is_judged_by_that_repository(tmp_path, flag):
    # 作業ディレクトリ（手動の宣言）の宣言で別のリポジトリの宛先を判定しない（#2446）
    repo = make_repo(tmp_path / "r", slug="owner/repo", delivery=MANUAL)
    got = _check(repo, f"gh {flag} pr merge 3")
    assert got is not None and "other/repo" in got.reason and "clone" in got.reason


def test_cd_into_the_target_clone_uses_its_declaration(tmp_path):
    # セッションのリポジトリ（自動）ではなく、cd した宛先（手動）の宣言で判定する（#2838）
    session = make_repo(tmp_path / "session", slug="owner/session", delivery=AUTO)
    target = make_repo(tmp_path / "target", slug="volare/target", delivery=MANUAL, worktree=True)
    assert _check(session, f"cd {target} && gh pr merge 65 -R volare/target --merge") is None
    assert _check(session, "gh pr merge 65 --merge") is not None


def test_matching_repo_flag_reads_the_declaration(tmp_path):
    repo = make_repo(tmp_path / "r", slug="owner/repo", delivery=AUTO)
    assert _check(repo, "gh -R OWNER/repo pr merge 3") is not None
    repo2 = make_repo(tmp_path / "m", slug="owner/m", delivery=MANUAL)
    assert _check(repo2, "GH_DEBUG=1 command gh -R owner/m pr merge 3") is None


@pytest.mark.parametrize(
    "case",
    [
        ("decl", "gh pr merge 1"),  # 宣言が無い
        ("quote", "gh pr merge 1 --body 'x"),  # 閉じない引用
        ("norepo", "gh pr merge 1"),  # リポジトリの外
    ],
)
def test_failures_of_the_strict_guard_deny(tmp_path, case):
    kind, command = case
    cwd = tmp_path if kind == "norepo" else make_repo(tmp_path / "r", delivery=None if kind == "decl" else AUTO)
    got = _check(cwd, command)
    assert got is not None and got.kind == "deny"


@pytest.mark.parametrize(
    "command",
    ["gh pr view 3", "echo gh pr merge", "git merge main", "gh pr list --search merge", "cat <<'EOF'\nmerge gh\nEOF"],
)
def test_commands_that_do_not_merge_pass(tmp_path, command):
    repo = make_repo(tmp_path / "r", delivery=AUTO)
    assert _check(repo, command) is None


def test_multiline_quoted_text_does_not_split_the_command(tmp_path):
    repo = make_repo(tmp_path / "r", delivery=AUTO)
    assert _check(repo, 'git commit -m "line 1\ngh pr merge 3\n"') is None
    assert _check(repo, 'echo "a\nb" && gh pr merge 3') is not None


def test_other_tools_and_events_are_not_judged(tmp_path):
    repo = make_repo(tmp_path / "r", delivery=AUTO)
    payload = bash(repo, "gh pr merge 1")
    assert strict.check({**payload, "tool_name": "Read"}, str(repo)) is None
    assert strict.check({**payload, "hook_event_name": "PostToolUse"}, str(repo)) is None
    assert strict.check("not a dict", str(repo)) is None


def test_entry_denies_before_dispatch_even_when_the_dependency_import_fails(tmp_path, monkeypatch):
    # 依存の import（dispatch と予算の読み手）を落とした状態でも、本番チャネルへのマージを止める（IH4）
    import ndf.domains.hooks as package

    repo = make_repo(tmp_path / "r", delivery=AUTO)
    for name in ("dispatch", "budget"):
        monkeypatch.setitem(sys.modules, f"ndf.domains.hooks.{name}", None)
        monkeypatch.delattr(package, name, raising=False)
    assert deny_reason(call_entry("claude", "PreToolUse", bash(repo, "gh pr merge 1"))) is not None
    assert call_entry("claude", "PreToolUse", bash(repo, "ls")) is None


def test_boot_path_denies_without_the_environment(tmp_path):
    # 環境が無いときの入口（標準ライブラリだけ）も、同じ strict の guard で止める
    repo = make_repo(tmp_path / "r", delivery=AUTO)
    out = io.StringIO()
    assert boot.run_boot(["boot.py", "claude", "PreToolUse"], io.StringIO(json.dumps(bash(repo, "gh pr merge 1"))), out) == 0
    assert deny_reason(json.loads(out.getvalue())) is not None
    quiet = io.StringIO()
    assert boot.run_boot(["boot.py", "claude", "PreToolUse"], io.StringIO(json.dumps(bash(repo, "ls"))), quiet) == 0
    assert quiet.getvalue() == ""


def test_hook_off_switch_turns_off_the_strict_guard(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r", delivery=AUTO)
    monkeypatch.setenv("NDF_HOOK_OFF", "merge-production")
    assert call_entry("claude", "PreToolUse", bash(repo, "gh pr merge 1")) is None
