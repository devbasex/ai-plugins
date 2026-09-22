"""入口（`refactor.py` の `main`）の解析とディスパッチを現状固定する。

**正しさを主張しない。** 各サブコマンドが「どの引数を受け取り、どの関数へ渡されるか」
という現在の振る舞いをそのまま記録する。登録の書き方を変えても、ここが通れば
利用者から見える CLI は変わっていない。

ディスパッチ先は入口の名前空間を差し替えて確かめる。`set_defaults(func=...)` は
`main` の実行時にその名前を引くため、登録の仕方が変わっても同じ手段で見える。
"""
from __future__ import annotations

import sys

import pytest


def _dispatch(refactor, monkeypatch, argv: list[str]):
    """`main()` を 1 度通し `(呼ばれた関数名, 渡された Namespace)` を返す。"""
    called: list[tuple[str, object]] = []
    for name in [n for n in vars(refactor) if n.startswith("cmd_")]:
        monkeypatch.setattr(
            refactor, name,
            lambda args, _name=name: called.append((_name, args)),
        )
    monkeypatch.setattr(sys, "argv", ["refactor.py", *argv])
    refactor.main()
    assert len(called) == 1, f"呼ばれた関数が 1 つではない: {called}"
    return called[0]


# ---------- id だけを取るサブコマンド群 ----------

ID_ONLY = [
    ("start-round", "cmd_start_round"),
    ("merge-proposals", "cmd_merge_proposals"),
    ("advance", "cmd_advance"),
    ("final-gate", "cmd_final_gate"),
    ("merge-final-fix", "cmd_merge_final_fix"),
    ("status", "cmd_status"),
]


@pytest.mark.parametrize("name,func", ID_ONLY)
def test_id_only_subcommands(refactor, monkeypatch, name, func):
    called, args = _dispatch(refactor, monkeypatch, [name, "796"])
    assert called == func
    assert args.cmd == name
    assert args.id == 796
    assert not hasattr(args, "round")


@pytest.mark.parametrize("name,_func", ID_ONLY)
def test_id_only_subcommands_require_id(refactor, monkeypatch, name, _func):
    with pytest.raises(SystemExit) as e:
        _dispatch(refactor, monkeypatch, [name])
    assert e.value.code == 2


# ---------- id と round を取るサブコマンド群 ----------

WITH_ROUND = [
    ("next-apply-round", "cmd_next_apply_round"),
    ("verify-round", "cmd_verify_round"),
    ("should-abandon", "cmd_should_abandon"),
    ("merge-fix", "cmd_merge_fix"),
    ("merge-test-judgements", "cmd_merge_test_judgements"),
]


@pytest.mark.parametrize("name,func", WITH_ROUND)
def test_round_subcommands(refactor, monkeypatch, name, func):
    called, args = _dispatch(refactor, monkeypatch, [name, "796", "3"])
    assert called == func
    assert (args.id, args.round) == (796, 3)
    assert not hasattr(args, "dry_run")


@pytest.mark.parametrize("name,_func", WITH_ROUND)
def test_round_subcommands_require_round(refactor, monkeypatch, name, _func):
    with pytest.raises(SystemExit) as e:
        _dispatch(refactor, monkeypatch, [name, "796"])
    assert e.value.code == 2


# ---------- 取り消しうるサブコマンド群（--dry-run を持つ） ----------

WITH_DRY_RUN = [
    ("merge-apply", "cmd_merge_apply"),
    ("abandon-items", "cmd_abandon_items"),
]


@pytest.mark.parametrize("name,func", WITH_DRY_RUN)
def test_dry_run_subcommands(refactor, monkeypatch, name, func):
    called, args = _dispatch(refactor, monkeypatch, [name, "796", "3"])
    assert called == func
    assert (args.id, args.round, args.dry_run) == (796, 3, False)

    _, args = _dispatch(refactor, monkeypatch, [name, "796", "3", "--dry-run"])
    assert args.dry_run is True


# ---------- init と report ----------

def test_init_defaults(refactor, monkeypatch):
    called, args = _dispatch(refactor, monkeypatch, [
        "init", "796",
        "--scope", "src", "tests",
        "--baseline-test", "pytest -q",
    ])
    assert called == "cmd_init"
    assert args.pr == 796
    assert args.scope == ["src", "tests"]
    assert args.baseline_test == "pytest -q"
    assert args.host is None
    assert args.max_outer_rounds == 3
    assert args.max_test_rounds == refactor.DEFAULT_MAX_TEST_ROUNDS
    assert args.max_fix_rounds == 3
    assert args.max_items_per_round == 5
    assert args.ci_check is None
    assert args.severity_threshold == refactor.DEFAULT_SEVERITY_THRESHOLD
    assert args.model is None
    assert args.test_timeout == refactor.DEFAULT_TEST_TIMEOUT
    assert args.sync_command is None
    assert args.plan_file is None
    assert args.workflow_step is False
    assert args.worktree_root is None


def test_init_accepts_options(refactor, monkeypatch):
    _, args = _dispatch(refactor, monkeypatch, [
        "init", "796",
        "--scope", "src",
        "--baseline-test", "pytest -q",
        "--host", "claude",
        "--max-outer-rounds", "4",
        "--max-test-rounds", "2",
        "--max-fix-rounds", "1",
        "--max-items-per-round", "2",
        "--ci-check", "tests",
        "--severity-threshold", "major",
        "--model", "codex=gpt-5.5", "--model", "claude=claude-opus-5",
        "--test-timeout", "600",
        "--sync-command", "bash scripts/build.sh",
        "--plan-file", "issues/plan.md",
        "--workflow-step",
        "--worktree-root", "/tmp/wt",
    ])
    assert args.host == "claude"
    assert (args.max_outer_rounds, args.max_test_rounds) == (4, 2)
    assert (args.max_fix_rounds, args.max_items_per_round) == (1, 2)
    assert args.ci_check == "tests"
    assert args.severity_threshold == "major"
    assert args.model == ["codex=gpt-5.5", "claude=claude-opus-5"]
    assert args.test_timeout == 600
    assert args.sync_command == "bash scripts/build.sh"
    assert args.plan_file == "issues/plan.md"
    assert args.workflow_step is True
    assert args.worktree_root == "/tmp/wt"


@pytest.mark.parametrize("argv", [
    ["init", "796", "--baseline-test", "pytest -q"],      # --scope が無い
    ["init", "796", "--scope", "src"],                    # --baseline-test が無い
])
def test_init_requires_scope_and_baseline(refactor, monkeypatch, argv):
    with pytest.raises(SystemExit) as e:
        _dispatch(refactor, monkeypatch, argv)
    assert e.value.code == 2


def test_report(refactor, monkeypatch):
    called, args = _dispatch(refactor, monkeypatch, ["report", "796"])
    assert called == "cmd_report"
    assert (args.id, args.metrics) == (796, False)

    _, args = _dispatch(refactor, monkeypatch, ["report", "796", "--metrics"])
    assert args.metrics is True


def test_subcommand_is_required(refactor, monkeypatch):
    with pytest.raises(SystemExit) as e:
        _dispatch(refactor, monkeypatch, [])
    assert e.value.code == 2
