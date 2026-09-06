"""tool 実行前の hook の判定のテスト（#221 / #266）。

判定は `scripts/lib/workflow-common.sh` に集約されている。**通信は行わない。**
GitHub への問い合わせは `gh` を PATH で差し替えて作り物へ向ける。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from workflow_helpers import (
    base_env, checkout, init_repo, path_with, pre_tool_use, run_guard, run_stage_check,
    state_file, stub_gh,
)

DESIGN_PR = json.dumps({"number": 268, "state": "open", "head": {"ref": "design/parallel-batch-04"}, "labels": []})
DESIGN_PR_APPROVED = json.dumps(
    {"number": 268, "state": "open", "head": {"ref": "design/parallel-batch-04"},
     "labels": [{"name": "design-approved"}]}
)
FEATURE_PR = json.dumps({"number": 218, "state": "open", "head": {"ref": "feature/issue-161"}, "labels": []})
LABEL_DEFINED = json.dumps({"name": "design-approved"})
LABEL_MISSING = "!1:gh: Not Found (HTTP 404)"
BY_BRANCH = json.dumps([{"number": 290, "head": {"ref": "design/parallel-batch-05"}, "labels": []}])


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return init_repo(tmp_path / "main")


@pytest.fixture()
def state(tmp_path: Path) -> Path:
    return tmp_path / "state"


def guard(repo: Path, state: Path, command: str, responses: dict | None = None,
          tmp_path: Path | None = None, extra: dict | None = None) -> subprocess.CompletedProcess:
    env = base_env(state, extra)
    if responses is not None:
        assert tmp_path is not None
        bin_dir = stub_gh(tmp_path / "bin", responses)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return run_guard(pre_tool_use(command, repo), cwd=repo, env=env)


def decision(result: subprocess.CompletedProcess) -> dict:
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), "出力が無い"
    return json.loads(result.stdout)["hookSpecificOutput"]


# --- 判定の対象でないもの ---------------------------------------------------

def test_another_event_does_nothing(repo: Path, state: Path) -> None:
    payload = pre_tool_use("gh pr merge 268 --squash", repo)
    payload["hook_event_name"] = "PostToolUse"

    result = run_guard(payload, cwd=repo, env=base_env(state))

    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_another_tool_does_nothing(repo: Path, state: Path) -> None:
    payload = pre_tool_use("gh pr merge 268 --squash", repo)
    payload["tool_name"] = "Edit"

    result = run_guard(payload, cwd=repo, env=base_env(state))

    assert result.stdout.strip() == ""


@pytest.mark.parametrize("command", ["ls -la", "gh pr view 268 --json body", "git merge origin/develop"])
def test_a_command_outside_the_target_does_nothing(repo: Path, state: Path, command: str) -> None:
    result = guard(repo, state, command)

    assert result.stdout.strip() == ""


# --- #266 設計 Pull Request のマージ ----------------------------------------

def test_a_design_pull_request_without_the_label_is_denied(repo: Path, state: Path, tmp_path: Path) -> None:
    """#266-1"""
    result = guard(repo, state, "gh pr merge 268 --squash", tmp_path=tmp_path,
                   responses={"pulls/268": DESIGN_PR, "labels/design-approved": LABEL_DEFINED})

    assert decision(result)["permissionDecision"] == "deny"
    assert "268" in decision(result)["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command",
    [
        "gh api --method PUT /repos/devbasex/ai-plugins/pulls/268/merge -f merge_method=squash",
        "gh api -X PUT repos/devbasex/ai-plugins/pulls/268/merge",
        "gh pr merge --squash --delete-branch 268",
        "gh pr merge https://github.com/devbasex/ai-plugins/pull/268 --squash",
    ],
)
def test_every_way_of_merging_with_a_number_is_judged(repo: Path, state: Path, tmp_path: Path, command: str) -> None:
    """#266-2: REST の書き方でも同じ判定になる。"""
    result = guard(repo, state, command, tmp_path=tmp_path,
                   responses={"pulls/268": DESIGN_PR, "labels/design-approved": LABEL_DEFINED})

    assert decision(result)["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "gh -R devbasex/ai-plugins pr merge 268 --squash",
        "gh --repo devbasex/ai-plugins pr merge 268 --squash",
        "gh -R=devbasex/ai-plugins pr merge 268 --squash",
        "gh --repo=devbasex/ai-plugins pr merge 268 --squash",
        "gh -Rdevbasex/ai-plugins pr merge 268 --squash",
    ],
)
def test_a_global_option_before_pr_is_judged(repo: Path, state: Path, tmp_path: Path, command: str) -> None:
    """#266-2: `gh` と `pr` の間のグローバルオプションで判定が抜けない。

    `-R` / `--repo` は値を別の語で取る形と、同じ語に含む形（`=` 付き・連結）がある。
    gh 2.98.0 で 5 つとも受け付けられることを確かめた。
    """
    result = guard(repo, state, command, tmp_path=tmp_path,
                   responses={"pulls/268": DESIGN_PR, "labels/design-approved": LABEL_DEFINED})

    assert decision(result)["permissionDecision"] == "deny"
    assert "268" in decision(result)["permissionDecisionReason"]


def test_a_global_option_with_a_value_does_not_hide_pr(repo: Path, state: Path, tmp_path: Path) -> None:
    """値を読み飛ばすのは `-R` / `--repo` に限る。知らないオプションは語だけを飛ばす。"""
    checkout(repo, "design/parallel-batch-05")

    result = guard(repo, state, "gh --help pr merge --squash", tmp_path=tmp_path,
                   responses={"pulls?head=": BY_BRANCH, "labels/design-approved": LABEL_DEFINED})

    assert decision(result)["permissionDecision"] == "deny"


def test_a_merge_without_a_number_is_looked_up_by_branch(repo: Path, state: Path, tmp_path: Path) -> None:
    """#266-2 の後半: ブランチ名から番号とラベルを 1 回の応答で引く。"""
    checkout(repo, "design/parallel-batch-05")

    result = guard(repo, state, "gh pr merge --squash", tmp_path=tmp_path,
                   responses={"pulls?head=": BY_BRANCH, "labels/design-approved": LABEL_DEFINED})

    assert decision(result)["permissionDecision"] == "deny"
    assert "290" in decision(result)["permissionDecisionReason"]


def test_a_design_pull_request_with_the_label_passes(repo: Path, state: Path, tmp_path: Path) -> None:
    """#266-3"""
    result = guard(repo, state, "gh pr merge 268 --squash", tmp_path=tmp_path,
                   responses={"pulls/268": DESIGN_PR_APPROVED})

    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_a_pull_request_outside_the_design_prefix_passes(repo: Path, state: Path, tmp_path: Path) -> None:
    """#266-4"""
    result = guard(repo, state, "gh pr merge 218 --squash", tmp_path=tmp_path,
                   responses={"pulls/218": FEATURE_PR})

    assert result.stdout.strip() == ""


def test_an_undefined_label_passes(repo: Path, state: Path, tmp_path: Path) -> None:
    """#266-5: 定義が**無いことを確かめられた**ときだけ通す。"""
    result = guard(repo, state, "gh pr merge 268 --squash", tmp_path=tmp_path,
                   responses={"pulls/268": DESIGN_PR, "labels/design-approved": LABEL_MISSING})

    assert result.stdout.strip() == ""


# --- #266-6 判定できないときは拒否する --------------------------------------

def test_a_failed_query_is_denied(repo: Path, state: Path, tmp_path: Path) -> None:
    result = guard(repo, state, "gh pr merge 268 --squash", tmp_path=tmp_path,
                   responses={"pulls/268": "!1:gh: API rate limit already exceeded"})

    assert decision(result)["permissionDecision"] == "deny"
    assert "head のブランチ名" in decision(result)["permissionDecisionReason"]


def test_a_failed_label_query_is_denied(repo: Path, state: Path, tmp_path: Path) -> None:
    """404 以外の失敗は「定義が無い」と読まない。"""
    result = guard(repo, state, "gh pr merge 268 --squash", tmp_path=tmp_path,
                   responses={"pulls/268": DESIGN_PR,
                              "labels/design-approved": "!1:gh: API rate limit already exceeded"})

    assert decision(result)["permissionDecision"] == "deny"


def test_a_missing_gh_is_denied(repo: Path, state: Path, tmp_path: Path) -> None:
    """判定に要るコマンドが無い場合も拒否する。`curl` の REST は `gh` が無くても通る。"""
    result = guard(repo, state, "gh pr merge 268 --squash",
                   extra={"PATH": path_with(tmp_path / "bin", without=("gh",))})

    assert decision(result)["permissionDecision"] == "deny"


def test_a_missing_jq_is_denied(repo: Path, state: Path, tmp_path: Path) -> None:
    """入力を読み解けなくても、マージらしい本文は止める。"""
    result = guard(repo, state, "gh pr merge 268 --squash",
                   extra={"PATH": path_with(tmp_path / "bin", without=("jq",))})

    assert decision(result)["permissionDecision"] == "deny"


def test_a_missing_awk_is_denied(repo: Path, state: Path, tmp_path: Path) -> None:
    """語の分割に要る awk が無くても、マージらしい本文は止める。

    awk が無いと `wf_split` が何も出さず、`wf_merge_target` は「マージではない」と
    読める 1 を返す。そのまま通すと拒否の判定へ一度も入らない fail-open になる。
    """
    result = guard(repo, state, "gh pr merge 268 --squash",
                   extra={"PATH": path_with(tmp_path / "bin", without=("awk",))})

    assert decision(result)["permissionDecision"] == "deny"
    assert "awk" in decision(result)["permissionDecisionReason"]


def test_a_missing_awk_with_a_global_option_is_denied(repo: Path, state: Path, tmp_path: Path) -> None:
    """粗い見分けも `gh` と `pr` の間のグローバルオプションを越える。"""
    result = guard(repo, state, "gh -R devbasex/ai-plugins pr merge 268 --squash",
                   extra={"PATH": path_with(tmp_path / "bin", without=("awk",))})

    assert decision(result)["permissionDecision"] == "deny"


def test_a_detached_head_without_a_number_is_denied(repo: Path, state: Path, tmp_path: Path) -> None:
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--detach"], check=True, capture_output=True)

    result = guard(repo, state, "gh pr merge --squash", tmp_path=tmp_path, responses={})

    assert decision(result)["permissionDecision"] == "deny"
    assert "番号" in decision(result)["permissionDecisionReason"]


def test_no_pull_request_for_the_branch_is_denied(repo: Path, state: Path, tmp_path: Path) -> None:
    checkout(repo, "design/parallel-batch-05")

    result = guard(repo, state, "gh pr merge --squash", tmp_path=tmp_path,
                   responses={"pulls?head=": "[]"})

    assert decision(result)["permissionDecision"] == "deny"


def test_the_reason_carries_both_ways_of_passing(repo: Path, state: Path, tmp_path: Path) -> None:
    """#266-8: 通すために何をするかを書く。"""
    reason = decision(guard(repo, state, "gh pr merge 268 --squash", tmp_path=tmp_path,
                            responses={"pulls/268": DESIGN_PR,
                                       "labels/design-approved": LABEL_DEFINED}))["permissionDecisionReason"]

    assert "design-approved" in reason
    assert "gh pr edit 268 --add-label design-approved" in reason
    assert "design/" in reason


def test_the_reason_of_an_undetermined_merge_names_what_was_missing(
    repo: Path, state: Path, tmp_path: Path
) -> None:
    """#266-6: 確かめられなかった値も出す。"""
    reason = decision(guard(repo, state, "gh pr merge 268 --squash", tmp_path=tmp_path,
                            responses={"pulls/268": "!1:boom"}))["permissionDecisionReason"]

    assert "確かめられなかった" in reason
    assert "design-approved" in reason
    assert "design/" in reason


# --- #221 進行の記録の観測 --------------------------------------------------

def test_a_sync_command_is_recorded(repo: Path, state: Path) -> None:
    result = guard(repo, state, 'bash "$SCRIPTS/projects-sync.sh" 161 stage "設計"')

    assert result.returncode == 0
    saved = json.loads(state_file(state, 161).read_text(encoding="utf-8"))
    assert saved["stages"] == ["設計"]


def test_the_mode_is_recorded_from_the_same_command(repo: Path, state: Path) -> None:
    guard(repo, state, 'bash /abs/scripts/projects-sync.sh 161 mode "architecture"')

    saved = json.loads(state_file(state, 161).read_text(encoding="utf-8"))
    assert saved["mode"] == "architecture"


def test_recording_a_stage_before_the_release_says_nothing(repo: Path, state: Path) -> None:
    result = guard(repo, state, 'bash "$SCRIPTS/projects-sync.sh" 161 stage "設計"')

    assert result.stdout.strip() == ""


def test_recording_the_release_reports_the_missing_stages(repo: Path, state: Path) -> None:
    """#221-1: 必須の工程の記録が無いまま配布の記録へ進んだとき、名前が出力に現れる。"""
    env = base_env(state)
    run_stage_check("record", "161", "mode", "architecture", cwd=repo, env=env)
    for stage in ("作業場所の用意", "要求と受け入れ条件", "設計", "設計レビュー", "計画",
                  "実装", "構造改善", "レビュー", "完了判定", "Pull Request", "後片付け"):
        run_stage_check("record", "161", "stage", stage, cwd=repo, env=env)

    result = guard(repo, state, 'bash "$SCRIPTS/projects-sync.sh" 161 stage "配布"')

    assert decision(result)["hookEventName"] == "PreToolUse"
    assert "確定仕様化" in decision(result)["additionalContext"]
    assert "permissionDecision" not in decision(result)


def test_recording_the_release_without_a_gap_says_nothing(repo: Path, state: Path) -> None:
    """#221-3 と同じ考え方。欠落が無ければ何も出さない。"""
    env = base_env(state)
    run_stage_check("record", "161", "mode", "light", cwd=repo, env=env)
    for stage in (
        "要求と受け入れ条件", "作業場所の用意", "実装", "レビュー", "完了判定",
        "Pull Request", "後片付け",
    ):
        run_stage_check("record", "161", "stage", stage, cwd=repo, env=env)

    result = guard(repo, state, 'bash "$SCRIPTS/projects-sync.sh" 161 stage "配布"')

    assert result.stdout.strip() == ""


def test_a_repository_without_a_remote_records_nothing(tmp_path: Path, state: Path) -> None:
    """リポジトリを特定できないときは控えを書かない。工程は止めない。"""
    repo = init_repo(tmp_path / "bare", remote=None)

    result = guard(repo, state, 'bash "$SCRIPTS/projects-sync.sh" 161 stage "配布"')

    assert result.returncode == 0
    assert result.stdout.strip() == ""
