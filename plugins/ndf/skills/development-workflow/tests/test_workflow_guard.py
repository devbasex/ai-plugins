"""tool 実行前の hook の判定のテスト（#221 / #266）。

判定は `scripts/lib/workflow-common.sh` に集約されている。**通信は行わない。**
GitHub への問い合わせは `gh` を PATH で差し替えて作り物へ向ける。
"""
from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path

import pytest

from workflow_helpers import (
    LIB, base_env, checkout, init_repo, path_with, pre_tool_use, run_guard, run_lib,
    run_stage_check,
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
    assert "判定に要る gh が無い" in decision(result)["permissionDecisionReason"]


def test_a_missing_jq_is_denied(repo: Path, state: Path, tmp_path: Path) -> None:
    """入力を読み解けなくても、マージらしい本文は止める。"""
    result = guard(repo, state, "gh pr merge 268 --squash",
                   extra={"PATH": path_with(tmp_path / "bin", without=("jq",))})

    assert decision(result)["permissionDecision"] == "deny"
    assert "判定に要る jq または awk が無い" in decision(result)["permissionDecisionReason"]


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
    assert "awk" in decision(result)["permissionDecisionReason"]


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

def test_parse_sync_reads_the_issue_key_and_value(repo: Path) -> None:
    result = run_lib(
        'wf_parse_sync \'bash "$SCRIPTS/projects-sync.sh" 161 stage "設計"\'',
        cwd=repo,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "161\tstage\t設計"


def test_parse_sync_rejects_a_command_with_too_few_arguments(repo: Path) -> None:
    result = run_lib(
        'wf_parse_sync \'bash "$SCRIPTS/projects-sync.sh" 161 stage\'',
        cwd=repo,
    )

    assert result.returncode == 1
    assert result.stdout.strip() == ""


def test_parse_sync_rejects_an_unrelated_command(repo: Path) -> None:
    result = run_lib('wf_parse_sync "gh pr create --base develop"', cwd=repo)

    assert result.returncode == 1
    assert result.stdout.strip() == ""


def test_a_sync_command_is_recorded(repo: Path, state: Path) -> None:
    result = guard(repo, state, 'bash "$SCRIPTS/projects-sync.sh" 161 stage "設計"')

    assert result.returncode == 0
    saved = json.loads(state_file(state, 161).read_text(encoding="utf-8"))
    assert saved["stages"] == ["設計"]


def test_the_mode_is_recorded_from_the_same_command(repo: Path, state: Path) -> None:
    guard(repo, state, 'bash /abs/scripts/projects-sync.sh 161 mode "standard"')

    saved = json.loads(state_file(state, 161).read_text(encoding="utf-8"))
    assert saved["mode"] == "standard"


def test_recording_a_stage_before_the_release_says_nothing(repo: Path, state: Path) -> None:
    result = guard(repo, state, 'bash "$SCRIPTS/projects-sync.sh" 161 stage "設計"')

    assert result.stdout.strip() == ""


def test_recording_the_release_reports_the_missing_stages(repo: Path, state: Path) -> None:
    """#221-1: 必須の工程の記録が無いまま配布の記録へ進んだとき、名前が出力に現れる。"""
    env = base_env(state)
    run_stage_check("record", "161", "mode", "standard", cwd=repo, env=env)
    for stage in ("作業場所の用意", "要求と受け入れ条件", "設計", "ドキュメントレビュー", "計画",
                  "実装", "構造改善", "実装レビュー", "完了判定", "Pull Request", "後片付け"):
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
        "要求と受け入れ条件", "作業場所の用意", "設計", "実装", "実装レビュー", "完了判定",
        "Pull Request", "後片付け",
    ):
        run_stage_check("record", "161", "stage", stage, cwd=repo, env=env)

    result = guard(repo, state, 'bash "$SCRIPTS/projects-sync.sh" 161 stage "配布"')

    assert result.stdout.strip() == ""


def test_a_conditional_stage_without_a_record_is_not_a_gap(repo: Path, state: Path) -> None:
    """条件付きの工程は、記録が無くても欠落としては並ばない。

    `light` の「設計」は触る領域が該当したときだけ通る（#375）。当たらない変更では記録が
    残らないため、**必須の工程の欠落と同じ列に並べない**。案内は出るが、文言が違う。
    """
    env = base_env(state)
    run_stage_check("record", "161", "mode", "light", cwd=repo, env=env)
    for stage in (
        "要求と受け入れ条件", "作業場所の用意", "実装", "実装レビュー", "完了判定",
        "Pull Request", "後片付け",
    ):
        run_stage_check("record", "161", "stage", stage, cwd=repo, env=env)

    result = guard(repo, state, 'bash "$SCRIPTS/projects-sync.sh" 161 stage "配布"')

    assert "条件付き: 設計" in result.stdout
    assert "記録なし:" not in result.stdout


def test_a_repository_without_a_remote_records_nothing(tmp_path: Path, state: Path) -> None:
    """リポジトリを特定できないときは控えを書かない。工程は止めない。"""
    repo = init_repo(tmp_path / "bare", remote=None)

    result = guard(repo, state, 'bash "$SCRIPTS/projects-sync.sh" 161 stage "配布"')

    assert result.returncode == 0
    assert result.stdout.strip() == ""


# --- R2-002: 案内の直列化と復号の契約（現状固定） ---------------------------

# `wf_emit_context` は systemMessage と additionalContext の両方へ同じ文字列を
# 載せ、JSON として出す。引用符・バックスラッシュ・改行・タブ・復帰文字を含む値と
# 空文字が、有効な JSON になり復号すると元の値へ戻ることを、最小の出力入口で固定する。
# 生成 JSON の空白・キー順や文言の完全一致は要求しない（復号後の値だけを見る）。
@pytest.mark.parametrize(
    "text",
    [
        "",
        "日本語の案内です",
        'quote " inside',
        r"backslash \ inside",
        "line1\nline2",
        "col1\tcol2",
        "carriage\rreturn",
        '全部盛り 日本語 "q" \\b\n改行\ttab\rcr',
    ],
)
def test_emit_context_round_trips_the_value(text: str) -> None:
    """現状固定: 入力値が JSON を経て systemMessage と additionalContext に保たれる。"""
    result = run_lib(f"wf_emit_context {shlex.quote(text)}")

    assert result.returncode == 0, result.stderr
    decoded = json.loads(result.stdout)
    assert decoded["systemMessage"] == text
    hook = decoded["hookSpecificOutput"]
    assert hook["additionalContext"] == text
    assert hook["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in hook


# `wf_emit_deny` は permissionDecision を deny とし、permissionDecisionReason に
# 理由を載せて JSON として出す。境界入力で有効な JSON になり、復号すると元の値へ
# 戻ることを固定する。
@pytest.mark.parametrize(
    "text",
    [
        "",
        "日本語の案内です",
        'quote " inside',
        r"backslash \ inside",
        "line1\nline2",
        "col1\tcol2",
        "carriage\rreturn",
        '全部盛り 日本語 "q" \\b\n改行\ttab\rcr',
    ],
)
def test_emit_deny_round_trips_the_reason(text: str) -> None:
    """現状固定: 拒否理由が JSON を経て permissionDecisionReason に保たれる。"""
    result = run_lib(f"wf_emit_deny {shlex.quote(text)}")

    assert result.returncode == 0, result.stderr
    decoded = json.loads(result.stdout)
    hook = decoded["hookSpecificOutput"]
    assert hook["permissionDecision"] == "deny"
    assert hook["hookEventName"] == "PreToolUse"
    assert hook["permissionDecisionReason"] == text


# --- #565 コマンドの区切り ---------------------------------------------------
#
# 語の分割は、引用の外の制御演算子と本文の途中の改行で空の語（区切り）を出す。3 つの
# 読み手は、1 つ目の対象のコマンドの終わりで読むのを止める。

PARENT_BODY = (
    "cd /work/ai-plugins; ls issues/ | grep 565; date '+%H:%M'; "
    'bash plugins/ndf/scripts/projects-sync.sh 565 stage "要求と受け入れ条件"; echo "exit=$?"'
)


def split(text: str) -> list[str]:
    """`wf_split` の出力を語の並びで返す。区切りは空文字になる。"""
    result = subprocess.run(
        ["bash", "-c", f'. "$1"; wf_split "$2"', "_", str(LIB), text],
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout.decode("utf-8")
    assert out == "" or out.endswith("\0"), repr(out)
    return out.split("\0")[:-1] if out else []


def stages_of(state: Path, issue: int) -> list[str]:
    path = state_file(state, issue)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["stages"]


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ('a "b c"; d', ["a", "b c", "", "d"]),
        ("x 2>&1 | y", ["x", "2>&1", "", "y"]),
        ("cd x&&gh pr merge 1", ["cd", "x", "", "", "gh", "pr", "merge", "1"]),
        ("cmd &>/dev/null", ["cmd", "&>/dev/null"]),
        ("a b\nc", ["a", "b", "", "c"]),
        ("gh pr \\\nmerge 268", ["gh", "pr", "merge", "268"]),
        ("echo >&2 x", ["echo", ">&2", "x"]),
        ("(a)|b||c", ["", "a", "", "", "b", "", "", "c"]),
        ("sleep 1 & wait", ["sleep", "1", "", "wait"]),
        ('echo "a;b|c&&d(e)"', ["echo", "a;b|c&&d(e)"]),
        ("echo 'x\ny' z", ["echo", "x\ny", "z"]),
    ],
)
def test_split_marks_the_command_boundaries(text: str, words: list[str]) -> None:
    """AC11 と区切りの契約（設計の「`wf_split` の出力」）。"""
    assert split(text) == words


def test_split_does_not_mark_the_last_newline() -> None:
    """here-string が足す最後の改行では区切りを出さない。"""
    assert split("a b\n") == ["a", "b"]


def test_split_finishes_quickly_on_a_long_body() -> None:
    """非機能: 36KB の本文で 0.1 秒以内。演算子を引用の外に置き、区切りの判定を通す。"""
    body = "| 表 | x; y && z 2>&1 |\n" * 1500
    assert len(body.encode("utf-8")) >= 36000
    started = time.monotonic()
    split(body)
    assert time.monotonic() - started < 0.1


def test_a_stage_glued_to_a_semicolon_is_recorded(repo: Path, state: Path) -> None:
    """AC1"""
    guard(repo, state, 'bash plugins/ndf/scripts/projects-sync.sh 161 stage "設計"; echo "exit=$?"')

    assert stages_of(state, 161) == ["設計"]


@pytest.mark.parametrize(
    "command",
    [
        'bash "$SCRIPTS/projects-sync.sh" 161 stage "設計"&& echo ok',
        'bash "$SCRIPTS/projects-sync.sh" 161 stage "設計"|| echo ng',
        'bash "$SCRIPTS/projects-sync.sh" 161 stage "設計"| tail -3',
        '(bash "$SCRIPTS/projects-sync.sh" 161 stage "設計")',
        'bash "$SCRIPTS/projects-sync.sh" 161 stage 設計&&echo',
    ],
)
def test_a_stage_glued_to_an_operator_is_recorded(repo: Path, state: Path, command: str) -> None:
    """AC2"""
    guard(repo, state, command)

    assert stages_of(state, 161) == ["設計"]


def test_the_command_the_parent_ran_is_recorded(repo: Path, state: Path) -> None:
    """AC3: 親の会話で実行した本文そのもの。"""
    guard(repo, state, PARENT_BODY)

    assert stages_of(state, 565) == ["要求と受け入れ条件"]


def test_parse_sync_stops_at_the_boundary(repo: Path) -> None:
    """AC4: 区切りより前に 3 語そろわなければ積まない。"""
    result = run_lib("wf_parse_sync 'projects-sync.sh 161 stage; echo 設計'", cwd=repo)

    assert result.returncode == 1
    assert result.stdout.strip() == ""


@pytest.mark.parametrize(
    "command",
    [
        'bash "$SCRIPTS/projects-sync.sh" 161 stage "設計" 2>&1 | tail -3',
        'bash "$SCRIPTS/projects-sync.sh" 161 stage "設計"',
        'bash /abs/plugins/ndf/scripts/projects-sync.sh 161 stage "設計" 2>&1 | tail -2',
    ],
)
def test_the_forms_recorded_before_stay_the_same(repo: Path, state: Path, command: str) -> None:
    """AC5"""
    guard(repo, state, command)

    assert stages_of(state, 161) == ["設計"]


def test_merge_target_stops_at_the_boundary(repo: Path) -> None:
    """AC6"""
    result = run_lib("wf_merge_target 'gh pr merge 268; echo ok'", cwd=repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "268"


@pytest.mark.parametrize(
    "command",
    ["cd x&&gh pr merge 268", "cd x;gh pr merge 268", "true|gh pr merge 268"],
)
def test_a_merge_after_an_operator_is_denied(repo: Path, state: Path, tmp_path: Path, command: str) -> None:
    """AC7"""
    result = guard(repo, state, command, tmp_path=tmp_path,
                   responses={"pulls/268": DESIGN_PR, "labels/design-approved": LABEL_DEFINED})

    assert decision(result)["permissionDecision"] == "deny"
    assert "268" in decision(result)["permissionDecisionReason"]


def test_a_number_after_the_merge_command_is_not_taken(repo: Path) -> None:
    """AC8"""
    result = run_lib("wf_merge_target 'gh pr merge; echo 268'", cwd=repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_pr_create_body_glued_to_a_semicolon_is_read(repo: Path) -> None:
    """AC9"""
    command = 'gh pr create --body "Closes #161"; echo ok'
    result = run_lib(f"wf_parse_pr_create {shlex.quote(command)}", cwd=repo)

    assert result.stdout.strip() == "devbasex/ai-plugins\t161", result.stderr


def test_pr_create_body_after_the_boundary_is_not_read(repo: Path) -> None:
    """AC10"""
    command = 'gh pr create --title t; echo --body "Closes #161"'
    result = run_lib(f"wf_parse_pr_create {shlex.quote(command)}", cwd=repo)

    assert result.returncode == 1
    assert result.stdout.strip() == ""


@pytest.mark.parametrize("command", ["gh pr merge \\\n  268 --merge", "gh pr \\\nmerge 268"])
def test_a_continued_merge_is_denied(repo: Path, state: Path, tmp_path: Path, command: str) -> None:
    """AC13: 行末の `\\` の継続は区切りではない。hook を通して関門が働く。"""
    result = guard(repo, state, command, tmp_path=tmp_path,
                   responses={"pulls/268": DESIGN_PR, "labels/design-approved": LABEL_DEFINED})

    assert decision(result)["permissionDecision"] == "deny"
    assert "268" in decision(result)["permissionDecisionReason"]


def test_a_continued_stage_is_recorded(repo: Path, state: Path) -> None:
    """AC13"""
    guard(repo, state, 'bash plugins/ndf/scripts/projects-sync.sh \\\n  565 stage "設計"')

    assert stages_of(state, 565) == ["設計"]


# --- R1-003: `wf_is_candidate` の単体（現状固定） ---------------------------
#
# `wf_is_candidate` は、語の分割の前に走る安い絞り込みである。PR #593 で行末の `\` と
# 改行を空白へ畳んでから grep する処理が加わったが、この関数自体の単体テストが無く、
# 行継続で分割された対象コマンドが候補として通過する分岐が単体階層で固定されていない。
# ここで現状の振る舞いを正解として記録する（対象コードは変更しない）。


def is_candidate(text: str) -> int:
    """`wf_is_candidate` の終了コードを返す。0 が候補、1 が非候補。"""
    result = run_lib(f"wf_is_candidate {shlex.quote(text)}")
    return result.returncode


@pytest.mark.parametrize(
    "text",
    [
        "gh pr \\\nmerge 268",
        "gh pr \\\ncreate --base develop",
        "gh pr merge 268 --merge \\\n  --admin",
    ],
    ids=["continued-merge", "continued-create", "continued-tail"],
)
def test_is_candidate_passes_a_line_continued_target(text: str) -> None:
    """現状固定: 行末の `\\` と改行を空白へ畳むため、分割された対象も候補として通る。"""
    assert is_candidate(text) == 0


@pytest.mark.parametrize(
    "text",
    [
        "gh pr merge 268",
        "gh pr create --base develop",
        'bash plugins/ndf/scripts/projects-sync.sh 565 stage "設計"',
        "curl -s https://api.github.com/repos/o/r/pulls/268/merge",
    ],
    ids=["merge", "create", "sync", "rest-merge"],
)
def test_is_candidate_passes_a_single_line_target(text: str) -> None:
    """現状固定: 継続の無い対象コマンドはそのまま候補として通る。"""
    assert is_candidate(text) == 0


@pytest.mark.parametrize(
    "text",
    [
        "",
        "echo hello world",
        "git status --short",
        "gh pr view 268",
    ],
    ids=["empty", "echo", "git-status", "pr-view"],
)
def test_is_candidate_rejects_an_unrelated_command(text: str) -> None:
    """対照: いずれの目印にも当たらない本文は候補にしない。"""
    assert is_candidate(text) == 1


# --- R2-001: `wf_looks_like_merge_text` の単体（現状固定） ------------------

def looks_like_merge_text(text: str) -> int:
    """`wf_looks_like_merge_text` の終了コードを返す。0 が一致、1 が不一致。"""
    result = run_lib(f"wf_looks_like_merge_text {shlex.quote(text)}")
    return result.returncode


@pytest.mark.parametrize(
    "text",
    [
        "gh pr merge 268",
        "gh -R o/r pr merge 268",
        "pulls/12/merge",
    ],
    ids=["gh-pr-merge", "gh-global-option", "rest-merge"],
)
def test_merge_text_matcher_accepts_a_coarse_merge_pattern(text: str) -> None:
    """現状固定: CLI と REST パスの粗いマージ表現を一致として扱う。"""
    assert looks_like_merge_text(text) == 0


@pytest.mark.parametrize(
    "text",
    [
        "gh pr create --base develop",
        "gh pr view 268",
        "",
        "echo hello",
    ],
    ids=["pr-create", "pr-view", "empty", "echo"],
)
def test_merge_text_matcher_rejects_text_without_a_merge_pattern(text: str) -> None:
    """対照: マージ表現を含まない本文は一致として扱わない。"""
    assert looks_like_merge_text(text) == 1


# --- R2-002: `wf_is_mode` の単体（現状固定） --------------------------------
#
# `wf_is_mode` は、指定された文字列が `WF_MODES` に含まれるモードかを判定する。
# 既存のテストでは既知のモードに対する終了コード 0 の復帰分岐のみが確認されていた。
# 未知のモードに対する終了コード 1 と、空引数による早期復帰の終了コード 1 を
# 単体階層で固定する（対象コードは変更しない）。


def is_mode(mode: str) -> int:
    """`wf_is_mode` の終了コードを返す。0 が既知、1 が未知または空。"""
    result = run_lib(f"wf_is_mode {shlex.quote(mode)}")
    return result.returncode


def test_is_mode_accepts_a_known_mode() -> None:
    """現状固定: 既知のモードは終了コード 0 を返す。"""
    assert is_mode("standard") == 0


def test_is_mode_rejects_an_unknown_mode() -> None:
    """現状固定: 未知のモードは終了コード 1 を返す。"""
    assert is_mode("unknown-mode") == 1


def test_is_mode_rejects_an_empty_mode() -> None:
    """現状固定: 空引数は早期復帰により終了コード 1 を返す。"""
    assert is_mode("") == 1



def is_stage(stage: str) -> int:
    """`wf_is_stage` の終了コードを返す。0 が既知の工程、1 が未知または空。"""
    result = run_lib(f"wf_is_stage {shlex.quote(stage)}")
    return result.returncode


def test_is_stage_accepts_a_known_stage() -> None:
    """現状固定: 既知の工程は終了コード 0 を返す。"""
    assert is_stage("配布") == 0


def test_is_stage_rejects_an_unknown_stage() -> None:
    """現状固定: 未知の工程は while ループを抜けて終了コード 1 を返す。"""
    assert is_stage("存在しない工程") == 1


def test_is_stage_rejects_an_empty_stage() -> None:
    """現状固定: 空引数は早期復帰により終了コード 1 を返す。"""
    assert is_stage("") == 1


# --- R2-004: 閉じる課題でモードが食い違うときの案内（現状固定） --------------
#
# `wf_evidence_report` は、閉じる課題の控えのモードが食い違うと最も高いモードを選び、
# **全課題の不足工程をそのモードで数える**。公開の hook 入口へ `gh pr create` を渡し、
# 復号した additionalContext の要点（食い違いの告知・選ばれたモード・課題ごとの不足
# 工程）と、拒否を出さないことを結合の階層で固定する（対象コードは変更しない）。


def test_conflicting_modes_apply_the_highest_to_every_issue(repo: Path, state: Path) -> None:
    """現状固定: `light` の課題にも `standard` の基準で不足工程を並べ、案内だけで通す。"""
    env = base_env(state)
    run_stage_check("record", "417", "mode", "light", cwd=repo, env=env)
    run_stage_check("record", "417", "stage", "実装", cwd=repo, env=env)
    run_stage_check("record", "418", "mode", "standard", cwd=repo, env=env)
    run_stage_check("record", "418", "stage", "設計", cwd=repo, env=env)

    result = guard(repo, state, 'gh pr create --base develop --title "t" --body "Closes #417\nCloses #418"')

    hook = decision(result)
    assert "permissionDecision" not in hook
    lines = hook["additionalContext"].splitlines()
    assert "モードの記録が課題ごとに食い違います（light / standard）。最も高い standard を基準に見ています。" in lines
    note_417 = next(line for line in lines if line.startswith("  #417 (devbasex/ai-plugins): 記録なし: "))
    note_418 = next(line for line in lines if line.startswith("  #418 (devbasex/ai-plugins): 記録なし: "))
    # `light` では条件付きの「設計」も、`standard` を当てるため不足として並ぶ。
    assert "設計" in note_417.split(": ")[-1].split(" / ")
    assert "実装" not in note_417.split(": ")[-1].split(" / ")
    assert "実装" in note_418.split(": ")[-1].split(" / ")
    assert "設計" not in note_418.split(": ")[-1].split(" / ")
