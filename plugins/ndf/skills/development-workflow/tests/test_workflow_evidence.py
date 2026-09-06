"""Pull Request を作る時点で、実行証跡の欠落を案内する（#424 の C）。

v10.5.0 は判定したモードに対する工程を通さずに配布まで進んだ。**進行の記録を一度も
書かなかったため、gate が一度も走らなかった。**

**拒否はしない。** 記録が無いことは、その工程を通っていないことと同じではない。記録の
側が遅れているだけの状態で Pull Request の作成が止まると、正当な操作が止まる。
"""
from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path

import pytest

from workflow_helpers import (
    base_env,
    init_repo,
    path_with,
    pre_tool_use,
    run_guard,
    run_lib,
    run_stage_check,
)

# `standard` で Pull Request の作成までに求める工程（「レビュー」を除く）。
STANDARD_BEFORE_PR = (
    "要求と受け入れ条件", "作業場所の用意", "設計", "設計レビュー", "計画", "実装",
    "構造改善", "完了判定",
)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return init_repo(tmp_path / "main")


@pytest.fixture()
def state(tmp_path: Path) -> Path:
    return tmp_path / "state"


def seed(repo: Path, state: Path, issue: int, mode: str | None, stages: tuple[str, ...]) -> None:
    env = base_env(state)
    if mode:
        run_stage_check("record", str(issue), "mode", mode, cwd=repo, env=env)
    for stage in stages:
        run_stage_check("record", str(issue), "stage", stage, cwd=repo, env=env)


def guard(repo: Path, state: Path, command: str, extra: dict | None = None,
          env: dict | None = None) -> subprocess.CompletedProcess:
    return run_guard(pre_tool_use(command, repo), cwd=repo, env=env or base_env(state, extra))


def context(result: subprocess.CompletedProcess) -> str:
    """案内の本文。出力が無ければ空文字を返す。"""
    assert result.returncode == 0, result.stderr
    if not result.stdout.strip():
        return ""
    return json.loads(result.stdout)["hookSpecificOutput"].get("additionalContext", "")


def create(body: str, base: str = "develop") -> str:
    return (
        f'gh pr create --base {base} --head feature/x --title "t" --body "{body}"'
    )


# --- C1: 走査の絞り込み -----------------------------------------------------


def test_the_candidate_filter_lets_pr_create_through() -> None:
    """ここで早期に落ちると `workflow-guard.sh` の本体へ届かない。"""
    result = run_lib('wf_is_candidate "gh pr create --base develop" && echo hit')
    assert result.stdout.strip() == "hit", result.stderr


def test_the_candidate_filter_still_rejects_unrelated_commands() -> None:
    result = run_lib('wf_is_candidate "ls -la" || echo miss')
    assert result.stdout.strip() == "miss", result.stderr


# --- C2: 本文からリポジトリと番号の組を取る ---------------------------------


def test_it_reads_the_closing_words_from_the_body(repo: Path) -> None:
    command = create("Closes #417")
    result = run_lib(f'wf_parse_pr_create {shlex.quote(command)}', cwd=repo)
    assert result.stdout.strip() == "devbasex/ai-plugins\t417", result.stderr


def test_it_reads_the_closing_words_from_a_body_equals_option(repo: Path) -> None:
    command = 'gh pr create --base develop --body="Closes #419"'
    result = run_lib(f"wf_parse_pr_create {shlex.quote(command)}", cwd=repo)
    assert result.stdout.strip() == "devbasex/ai-plugins\t419", result.stderr


def test_it_reads_the_closing_words_from_a_body_file(repo: Path, tmp_path: Path) -> None:
    body = tmp_path / "body.md"
    body.write_text("まとめ\n\nCloses #418\nCloses #420\n", encoding="utf-8")
    command = f'gh pr create --base develop --body-file {body}'
    result = run_lib(f'wf_parse_pr_create {shlex.quote(command)}', cwd=repo)
    assert result.stdout.split() == [
        "devbasex/ai-plugins", "418", "devbasex/ai-plugins", "420"
    ], result.stderr


def test_it_reads_the_closing_words_from_a_body_file_equals_option(
    repo: Path, tmp_path: Path
) -> None:
    body = tmp_path / "body.md"
    body.write_text("まとめ\n\nCloses #421\n", encoding="utf-8")
    command = f"gh pr create --base develop --body-file={body}"
    result = run_lib(f"wf_parse_pr_create {shlex.quote(command)}", cwd=repo)
    assert result.stdout.strip() == "devbasex/ai-plugins\t421", result.stderr


def test_a_body_without_closing_words_yields_nothing(repo: Path) -> None:
    command = create("ただの説明")
    result = run_lib(f'wf_parse_pr_create {shlex.quote(command)}', cwd=repo)
    assert result.stdout.strip() == ""
    assert result.returncode != 0


# --- C3 / C12: モードと記録が無いとき ---------------------------------------


def test_it_says_the_mode_is_missing(repo: Path, state: Path) -> None:
    seed(repo, state, 417, None, ("実装",))
    out = context(guard(repo, state, create("Closes #417")))
    assert "モードの記録" in out
    assert "417" in out


def test_an_empty_note_is_reported_too(repo: Path, state: Path) -> None:
    """v10.5.0 では記録を一度も書かなかったため、gate が一度も走らなかった。"""
    out = context(guard(repo, state, create("Closes #417")))
    assert "417" in out
    assert "記録" in out


# --- C4 / C8 / C11: 必須の工程の欠落 ----------------------------------------


def test_it_lists_the_required_stages_that_have_no_record(repo: Path, state: Path) -> None:
    """#417 の再現。`standard` を記録し、工程を飛ばして Pull Request を作る。"""
    seed(repo, state, 417, "standard", ("要求と受け入れ条件", "作業場所の用意", "実装"))
    out = context(guard(repo, state, create("Closes #417")))
    assert "設計" in out
    assert "計画" in out


def test_the_review_stage_is_not_required_yet(repo: Path, state: Path) -> None:
    """`cross-review` は Pull Request が無いと回せない。毎回出ると読まれなくなる。"""
    seed(repo, state, 417, "standard", STANDARD_BEFORE_PR)
    result = guard(repo, state, create("Closes #417"))
    assert result.stdout.strip() == "", result.stdout


def test_light_is_not_exempt(repo: Path, state: Path) -> None:
    """必須の工程が少ないモードほど、通していないことが見えにくい。"""
    seed(repo, state, 422, "light", ("実装",))
    out = context(guard(repo, state, create("Closes #422")))
    assert "要求と受け入れ条件" in out


# --- C5: 拒否しない ---------------------------------------------------------


def test_it_never_denies(repo: Path, state: Path) -> None:
    seed(repo, state, 417, "standard", ("実装",))
    result = guard(repo, state, create("Closes #417"))
    assert "permissionDecision" not in result.stdout


# --- C6: 判定できないときは通す ---------------------------------------------


def test_without_jq_it_says_nothing(repo: Path, state: Path, tmp_path: Path) -> None:
    seed(repo, state, 417, "standard", ("実装",))
    env = base_env(state)
    env["PATH"] = path_with(tmp_path / "nojq", without=("jq",))
    result = guard(repo, state, create("Closes #417"), env=env)
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_a_repository_without_a_remote_says_nothing(tmp_path: Path, state: Path) -> None:
    bare = init_repo(tmp_path / "bare", remote=None)
    result = guard(bare, state, create("Closes #417"))
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_a_body_file_that_does_not_exist_says_nothing(repo: Path, state: Path) -> None:
    command = "gh pr create --base develop --body-file /nonexistent/body.md"
    result = guard(repo, state, command)
    assert result.returncode == 0
    assert result.stdout.strip() == ""


# --- C7: 制限時間 -----------------------------------------------------------


def test_the_judgement_finishes_within_the_hook_timeout(repo: Path, state: Path) -> None:
    seed(repo, state, 417, "standard", ("実装",))
    body = ("x" * 200 + "\n") * 100 + "Closes #417"
    started = time.monotonic()
    guard(repo, state, f'gh pr create --base develop --body "{body}"')
    assert time.monotonic() - started < 10


# --- C9: モードが食い違うとき -----------------------------------------------


def test_conflicting_modes_take_the_highest_and_say_so(repo: Path, state: Path) -> None:
    seed(repo, state, 417, "light", ("実装", "完了判定"))
    seed(repo, state, 418, "standard", ("実装", "完了判定"))
    out = context(guard(repo, state, create("Closes #417 Closes #418")))
    assert "食い違" in out
    assert "standard" in out
    assert "設計" in out


def test_standard_outranks_legacy_refactor(repo: Path, state: Path) -> None:
    """高さは `WF_MODE_HEIGHT` が持つ。`WF_MODES` の並びからは導かない（決定 2-b）。"""
    result = run_lib('wf_higher_mode legacy-refactor standard')
    assert result.stdout.strip() == "standard", result.stderr


# --- C10: 別のリポジトリ ----------------------------------------------------


def test_another_repository_uses_its_own_note(repo: Path, state: Path) -> None:
    """番号だけへ潰さない。同じ番号の別リポジトリの控えに当たらない。"""
    seed(repo, state, 5, "light", ("要求と受け入れ条件", "作業場所の用意", "実装",
                                   "完了判定"))
    out = context(guard(repo, state, create("Closes other/repo#5")))
    assert "other/repo" in out
    assert "モードの記録" in out


# --- C13: 断定しない --------------------------------------------------------


def test_the_wording_does_not_assert_that_a_stage_was_skipped(repo: Path, state: Path) -> None:
    seed(repo, state, 417, "standard", ("実装",))
    out = context(guard(repo, state, create("Closes #417")))
    assert "記録の側が遅れている" in out
    for word in ("飛ばしました", "通っていません", "違反"):
        assert word not in out


# --- レビューで出た形（#427 の 3 件） ---------------------------------------


HEREDOC = """gh pr create --base develop --title "t" --body "$(cat <<'EOF'
## まとめ

本文の途中に閉じる語がある。

Closes #424
EOF
)\""""


def test_a_heredoc_body_is_read_to_the_end(repo: Path) -> None:
    """`pr` が必須と定める形はヒアドキュメントである。

    語の分割は引用符の中の改行を 1 つの語として保つが、**行区切りで受け取ると
    1 行目だけを本文と読む**。末尾の閉じる語が落ちる。
    """
    result = run_lib(f"wf_parse_pr_create {shlex.quote(HEREDOC)}", cwd=repo)
    assert result.stdout.strip() == "devbasex/ai-plugins\t424", result.stderr


def test_a_heredoc_body_reaches_the_guard(repo: Path, state: Path) -> None:
    seed(repo, state, 424, "standard", ("実装",))
    out = context(guard(repo, state, HEREDOC))
    assert "424" in out
    assert "設計" in out


def test_the_repository_option_before_pr_is_skipped(repo: Path) -> None:
    """`gh -R <所有者>/<リポジトリ> pr create` でも見落とさない。"""
    command = 'gh -R devbasex/ai-plugins pr create --base develop --body "Closes #424"'
    result = run_lib(f"wf_parse_pr_create {shlex.quote(command)}", cwd=repo)
    assert result.stdout.strip() == "devbasex/ai-plugins\t424", result.stderr


def test_a_multi_line_body_keeps_every_closing_word(repo: Path) -> None:
    command = 'gh pr create --body "Closes #418\nCloses #420"'
    result = run_lib(f"wf_parse_pr_create {shlex.quote(command)}", cwd=repo)
    assert result.stdout.split() == [
        "devbasex/ai-plugins", "418", "devbasex/ai-plugins", "420"
    ], result.stderr
