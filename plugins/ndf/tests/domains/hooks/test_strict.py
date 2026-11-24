"""`strict` の guard（本番チャネルへのマージ）を、マージ先のリポジトリの設定とエントリポイントの経路で確かめる（M3-H6・#2446・#2838）。"""

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
    repo = make_repo(tmp_path / "r", deliveries=AUTO)
    got = _check(repo, "gh pr merge 12 --merge")
    assert got is not None and got.kind == "deny" and "自動で本番へ届く" in got.reason


def test_merge_in_manual_delivery_repo_passes(tmp_path):
    repo = make_repo(tmp_path / "r", deliveries=MANUAL)
    assert _check(repo, "gh pr merge 12 --merge") is None


def test_old_top_level_delivery_is_not_read(tmp_path):
    """#3672 の I4: 旧い保存先（`.ndf/project.json` の最上位の delivery）だけでは届け先を読まず、止める。"""
    repo = make_repo(tmp_path / "r")
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "project.json").write_text(json.dumps({"version": 1, "delivery": MANUAL}), encoding="utf-8")
    got = _check(repo, "gh pr merge 12 --merge")
    assert got is not None and got.kind == "deny" and "branch_strategy.release.deliveries" in got.reason


def test_ndf_settings_without_deliveries_passes_but_a_broken_one_is_denied(tmp_path):
    """ブランチ戦略のプリセット（`deliveries` を書かない）で作った設定は、`release.cd` が無ければ自動で本番へ届く
    届け先が無いとして通し、`release.cd` があれば自動で本番へ届くとして止める。`deliveries` が配列でない設定は読めないとして止める。"""
    repo = make_repo(tmp_path / "r", deliveries=[])
    decl = repo / ".ndf" / "project.json"
    data = json.loads(decl.read_text(encoding="utf-8"))
    del data["branch_strategy"]["release"]["deliveries"]
    data["branch_strategy"]["release"]["cd"] = None
    decl.write_text(json.dumps(data), encoding="utf-8")
    assert _check(repo, "gh pr merge 12 --merge") is None
    data["branch_strategy"]["release"]["cd"] = {"workflow": "release.yml"}
    decl.write_text(json.dumps(data), encoding="utf-8")
    got = _check(repo, "gh pr merge 12 --merge")
    assert got is not None and got.kind == "deny" and "自動で本番へ届く" in got.reason
    data["branch_strategy"]["release"]["deliveries"] = "auto"
    decl.write_text(json.dumps(data), encoding="utf-8")
    got = _check(repo, "gh pr merge 12 --merge")
    assert got is not None and got.kind == "deny" and "branch_strategy.release.deliveries" in got.reason


@pytest.mark.parametrize("flag", ["-R other/repo", "--repo other/repo", "--repo=other/repo", "-Rother/repo"])
def test_repo_flag_naming_another_repository_is_judged_by_that_repository(tmp_path, flag):
    # 作業ディレクトリ（手動の設定）の設定で別のリポジトリのマージ先を判定しない（#2446）
    repo = make_repo(tmp_path / "r", slug="owner/repo", deliveries=MANUAL)
    got = _check(repo, f"gh {flag} pr merge 3")
    assert got is not None and "other/repo" in got.reason and "clone" in got.reason


def test_cd_into_the_target_clone_uses_its_ndf_settings(tmp_path):
    # セッションのリポジトリ（自動）ではなく、cd したマージ先（手動）の設定で判定する（#2838）
    session = make_repo(tmp_path / "session", slug="owner/session", deliveries=AUTO)
    target = make_repo(tmp_path / "target", slug="volare/target", deliveries=MANUAL, worktree=True)
    assert _check(session, f"cd {target} && gh pr merge 65 -R volare/target --merge") is None
    assert _check(session, "gh pr merge 65 --merge") is not None


def test_matching_repo_flag_reads_the_ndf_settings(tmp_path):
    repo = make_repo(tmp_path / "r", slug="owner/repo", deliveries=AUTO)
    assert _check(repo, "gh -R OWNER/repo pr merge 3") is not None
    repo2 = make_repo(tmp_path / "m", slug="owner/m", deliveries=MANUAL)
    assert _check(repo2, "GH_DEBUG=1 command gh -R owner/m pr merge 3") is None


@pytest.mark.parametrize(
    "case",
    [
        ("decl", "gh pr merge 1"),  # 設定が無い
        ("quote", "gh pr merge 1 --body 'x"),  # 閉じない引用
        ("norepo", "gh pr merge 1"),  # リポジトリの外
    ],
)
def test_failures_of_the_strict_guard_deny(tmp_path, case):
    kind, command = case
    cwd = tmp_path if kind == "norepo" else make_repo(tmp_path / "r", deliveries=None if kind == "decl" else AUTO)
    got = _check(cwd, command)
    assert got is not None and got.kind == "deny"


@pytest.mark.parametrize(
    "command",
    ["gh pr view 3", "echo gh pr merge", "git merge main", "gh pr list --search merge", "cat <<'EOF'\nmerge gh\nEOF"],
)
def test_commands_that_do_not_merge_pass(tmp_path, command):
    repo = make_repo(tmp_path / "r", deliveries=AUTO)
    assert _check(repo, command) is None


def test_multiline_quoted_text_does_not_split_the_command(tmp_path):
    repo = make_repo(tmp_path / "r", deliveries=AUTO)
    assert _check(repo, 'git commit -m "line 1\ngh pr merge 3\n"') is None
    assert _check(repo, 'echo "a\nb" && gh pr merge 3') is not None


def test_pull_request_url_naming_another_repository_is_judged_by_that_repository(tmp_path):
    # 手動の設定の作業ディレクトリから、URL で別のリポジトリの本番マージを打っても通さない
    repo = make_repo(tmp_path / "r", slug="owner/repo", deliveries=MANUAL)
    got = _check(repo, "gh pr merge https://github.com/other/production/pull/1 --merge")
    assert got is not None and "other/production" in got.reason
    assert _check(repo, "gh pr merge https://github.com/owner/repo/pull/1 --merge") is None


@pytest.mark.parametrize(
    "command",
    [
        "if true; then gh pr merge 1 --merge; fi",
        "for i in 1; do gh pr merge $i; done",
        "bash -c 'gh pr merge 1'",
        "sh -lc 'cd . && gh pr merge 1'",
        "eval 'gh pr merge 1'",
        "echo 1 | xargs gh pr merge",
        "env -i gh pr merge 1",
        "sudo -u x gh pr merge 1",
        'echo "$(gh pr merge 1)"',
        "echo `gh pr merge 1`",
    ],
)
def test_merges_inside_control_words_wrappers_and_inner_scripts_are_denied(tmp_path, command):
    repo = make_repo(tmp_path / "r", deliveries=AUTO)
    got = _check(repo, command)
    assert got is not None and got.kind == "deny"


def test_heredoc_bodies_are_not_read_as_commands(tmp_path):
    repo = make_repo(tmp_path / "r", deliveries=AUTO)
    message = "git commit -m \"$(cat <<'EOF'\nfix: `gh pr merge` を止める\ndon't\nEOF\n)\""
    assert _check(repo, message) is None
    assert _check(repo, "cat <<'EOF'\nx\nEOF\ngh pr merge 3") is not None


def test_other_tools_and_events_are_not_judged(tmp_path):
    repo = make_repo(tmp_path / "r", deliveries=AUTO)
    payload = bash(repo, "gh pr merge 1")
    assert strict.check({**payload, "tool_name": "Read"}, str(repo)) is None
    assert strict.check({**payload, "hook_event_name": "PostToolUse"}, str(repo)) is None
    assert strict.check("not a dict", str(repo)) is None


def test_entry_denies_before_dispatch_even_when_the_dependency_import_fails(tmp_path, monkeypatch):
    # 依存の import（dispatch と予算の読み手）を落とした状態でも、本番チャネルへのマージを止める（IH4）
    import ndf.domains.hooks as package

    repo = make_repo(tmp_path / "r", deliveries=AUTO)
    for name in ("dispatch", "budget"):
        monkeypatch.setitem(sys.modules, f"ndf.domains.hooks.{name}", None)
        monkeypatch.delattr(package, name, raising=False)
    assert deny_reason(call_entry("claude", "PreToolUse", bash(repo, "gh pr merge 1"))) is not None
    assert call_entry("claude", "PreToolUse", bash(repo, "ls")) is None


def test_boot_path_denies_without_the_environment(tmp_path):
    # 環境が無いときのエントリポイント（標準ライブラリだけ）も、同じ strict の guard で止める
    repo = make_repo(tmp_path / "r", deliveries=AUTO)
    out = io.StringIO()
    assert boot.run_boot(["boot.py", "claude", "PreToolUse"], io.StringIO(json.dumps(bash(repo, "gh pr merge 1"))), out) == 0
    assert deny_reason(json.loads(out.getvalue())) is not None
    quiet = io.StringIO()
    assert boot.run_boot(["boot.py", "claude", "PreToolUse"], io.StringIO(json.dumps(bash(repo, "ls"))), quiet) == 0
    assert quiet.getvalue() == ""


def test_hook_off_switch_turns_off_the_strict_guard(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "r", deliveries=AUTO)
    monkeypatch.setenv("NDF_HOOK_OFF", "merge-production")
    assert call_entry("claude", "PreToolUse", bash(repo, "gh pr merge 1")) is None


@pytest.mark.parametrize(
    "command",
    [
        "bash <<'EOF'\ngh pr merge 1 --merge\nEOF",
        "bash <<< 'gh pr merge 1'",
        "echo 'gh pr merge 1' | bash",
        "sh -s < script.txt && echo merge gh",
    ],
)
def test_shells_reading_the_script_from_stdin_are_denied(tmp_path, command):
    # 中のスクリプトを読めないシェルは止める側へ倒す（手動の設定の作業ディレクトリでも止める）
    repo = make_repo(tmp_path / "r", deliveries=MANUAL)
    got = _check(repo, command)
    assert got is not None and "-c 無し" in got.reason


@pytest.mark.parametrize(
    "command",
    [
        "cat <<'EOF' > /tmp/m.sh\ngh pr merge 1 --merge\nEOF\nbash /tmp/m.sh",
        "source /tmp/m.sh # gh pr merge",
        ". /tmp/m.sh # gh pr merge",
    ],
)
def test_scripts_read_from_a_file_are_denied(tmp_path, command):
    # スクリプトのファイルの中は読めないので、全文が gh と merge を含めば止める側へ倒す
    repo = make_repo(tmp_path / "r", deliveries=MANUAL)
    got = _check(repo, command)
    assert got is not None and "ファイル" in got.reason


def test_shell_without_merge_in_the_command_passes(tmp_path):
    repo = make_repo(tmp_path / "r", deliveries=MANUAL)
    assert _check(repo, "bash scripts/build.sh 2>/dev/null") is None


@pytest.mark.parametrize(
    "command",
    [
        "(cd {manual} && git status); gh pr merge 1 --merge",
        "(cd {manual}); gh pr merge 1 --merge",
        "cd {manual} | true; gh pr merge 1 --merge",
        "cd {manual} & gh pr merge 1 --merge",
        "(export GH_REPO=x/y; cd {manual}); gh pr merge 1",
    ],
)
def test_cd_in_a_subshell_does_not_move_the_outer_statements(tmp_path, command):
    # サブシェル・パイプライン・裏の `&` の cd は外の文の作業ディレクトリを変えない（Bash の Command Grouping）
    session = make_repo(tmp_path / "session", slug="owner/session", deliveries=AUTO)
    manual = make_repo(tmp_path / "manual", slug="owner/manual", deliveries=MANUAL)
    got = _check(session, command.format(manual=manual))
    assert got is not None and "自動で本番へ届く" in got.reason


def test_cd_inside_a_subshell_applies_to_its_own_merge(tmp_path):
    session = make_repo(tmp_path / "session", slug="owner/session", deliveries=AUTO)
    manual = make_repo(tmp_path / "manual", slug="owner/manual", deliveries=MANUAL)
    assert _check(session, f"(cd {manual} && gh pr merge 1 --merge)") is None
    got = _check(session, f"cd {manual}; ) gh pr merge 1")
    assert got is not None and "決められない" in got.reason


@pytest.mark.parametrize("cd", ['cd "$WT"', "cd `pwd`", "cd -", "pushd /tmp", "cd -P x"])
def test_cd_whose_target_is_not_literal_denies_the_merge(tmp_path, cd):
    # 移った先を字句から決められなければ、元の作業ディレクトリの設定で判定しない
    repo = make_repo(tmp_path / "r", deliveries=MANUAL)
    got = _check(repo, f"{cd} && gh pr merge 1 --merge")
    assert got is not None and "決められない" in got.reason


def test_cd_to_an_absolute_path_after_an_unknown_cd_reads_that_path(tmp_path):
    session = make_repo(tmp_path / "session", deliveries=AUTO)
    target = make_repo(tmp_path / "target", deliveries=MANUAL)
    assert _check(session, f'cd "$WT" && cd {target} && gh pr merge 1') is None


@pytest.mark.parametrize(
    "command",
    [
        "GH_REPO=other/production gh pr merge 1 --merge",
        "env GH_REPO=github.com/other/production gh pr merge 1",
        "export GH_REPO=other/production && gh pr merge 1",
        "export GH_REPO=other/production\nbash -c 'gh pr merge 1'",
    ],
)
def test_gh_repo_naming_another_repository_is_judged_by_that_repository(tmp_path, command):
    # gh は GH_REPO を -R と同じ対象のリポジトリとして読む（https://cli.github.com/manual/gh_help_environment）
    repo = make_repo(tmp_path / "r", slug="owner/repo", deliveries=MANUAL)
    got = _check(repo, command)
    assert got is not None and "other/production" in got.reason


def test_gh_repo_matching_the_origin_or_overridden_by_the_flag_passes(tmp_path):
    repo = make_repo(tmp_path / "r", slug="owner/repo", deliveries=MANUAL)
    assert _check(repo, "GH_REPO=owner/repo gh pr merge 1") is None
    assert _check(repo, "GH_REPO=other/production gh -R owner/repo pr merge 1") is None
    assert _check(repo, "export GH_REPO=other/production; unset GH_REPO; gh pr merge 1") is None


def test_gh_repo_in_the_hook_environment_is_the_initial_target(tmp_path, monkeypatch):
    # セッションの前に export した GH_REPO も、gh は -R と同じ対象のリポジトリとして読む
    repo = make_repo(tmp_path / "r", slug="owner/repo", deliveries=MANUAL)
    monkeypatch.setenv("GH_REPO", "other/production")
    got = deny_reason(call_entry("claude", "PreToolUse", bash(repo, "gh pr merge 1 --merge")))
    assert got is not None and "other/production" in got
    assert call_entry("claude", "PreToolUse", bash(repo, "unset GH_REPO; gh pr merge 1 --merge")) is None


def test_cd_key_missing_is_denied_like_an_unreadable_setting(tmp_path):
    """#4409 の決定 2: `deliveries` も `cd` のキーも無い設定はスキーマに合わず、判定の部品が `unknown` を返して止める。"""
    repo = make_repo(tmp_path / "r", deliveries=[])
    decl = repo / ".ndf" / "project.json"
    data = json.loads(decl.read_text(encoding="utf-8"))
    del data["branch_strategy"]["release"]["deliveries"]
    data["branch_strategy"]["release"].pop("cd", None)
    decl.write_text(json.dumps(data), encoding="utf-8")
    got = _check(repo, "gh pr merge 12 --merge")
    assert got is not None and got.kind == "deny"


@pytest.mark.parametrize(
    ("value", "denied"),
    [("automatic", True), ("unknown", True), ("manual", False), ("unset", False)],
)
def test_hook_follows_the_shared_verdict(tmp_path, monkeypatch, value, denied):
    """#4409 の決定 2: hook は判定の部品の値で決める。部品の戻り値を差し替えると hook の結果が変わる。"""
    from ndf.core import production_merge

    repo = make_repo(tmp_path / "r", deliveries=MANUAL)
    monkeypatch.setattr(production_merge, "automatic_rows", lambda section: production_merge.Verdict(value))
    got = _check(repo, "gh pr merge 12 --merge")
    assert (got is not None and got.kind == "deny") is denied


MARK = "<!-- ndf:merge-approval -->"


@pytest.mark.parametrize(
    "command",
    [
        f"gh pr comment 12 --body '{MARK}\n回答: approved'",
        f"gh issue comment 12 -b '{MARK}'",
        f"gh api repos/o/r/issues/12/comments -f body='{MARK}'",
        f"gh pr comment 12 --body-file - <<'X'\n{MARK}\nX",
        f"cd /tmp && gh pr comment 12 --body \"{MARK}\"",
    ],
)
def test_comment_with_the_merge_approval_mark_is_denied(tmp_path, command):
    """#4409 の決定 7: マージの承認の証跡のマーカーを含むコメントを、AI が Bash から書く操作を止める。"""
    repo = make_repo(tmp_path / "r", deliveries=MANUAL)
    got = _check(repo, command)
    assert got is not None and got.kind == "deny" and "ndf:merge-approval" in got.reason


@pytest.mark.parametrize("flag", ["--body-file", "-F", "--input"])
def test_comment_body_file_with_the_mark_is_denied(tmp_path, flag):
    repo = make_repo(tmp_path / "r", deliveries=MANUAL)
    (repo / "body.md").write_text(f"{MARK}\n回答: approved\n", encoding="utf-8")
    verb = "api repos/o/r/issues/12/comments" if flag == "--input" else "pr comment 12"
    got = _check(repo, f"gh {verb} {flag} body.md")
    assert got is not None and got.kind == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "gh pr comment 12 --body 'マージの前に見てください'",
        "gh pr comment 12 --body '<!-- ndf:release-approval -->'",
        "gh pr comment 12 --body-file -",
        "gh pr comment 12 --body-file missing.md",
        f"echo '{MARK}'",
    ],
)
def test_ordinary_comments_pass(tmp_path, command):
    repo = make_repo(tmp_path / "r", deliveries=AUTO)
    (repo / "plain.md").write_text("普通のコメント\n", encoding="utf-8")
    assert _check(repo, command) is None
    assert _check(repo, "gh pr comment 12 --body-file plain.md") is None
