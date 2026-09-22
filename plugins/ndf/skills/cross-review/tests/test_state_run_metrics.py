"""cross-review の状態の保存と実行の要約（#662 の AC8 / AC11 / AC13 / AC16 / AC18 / AC23 / AC72）。

状態ファイルを一時ディレクトリへ置き、`state.py` の副コマンドを呼んで要約を読む。
要約の置き場所は各テストが `NDF_METRICS_DIR` で一時ディレクトリへ向ける。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

_HERE = pathlib.Path(__file__).resolve().parent
_MEASURE = _HERE.parent / "scripts" / "measure.py"

PR = 665


def _state(tmp_dir: pathlib.Path) -> dict:
    return {
        "started_at": "2026-09-15T10:00:00+09:00",
        "host": "claude",
        "repo": "devbasex/ai-plugins",
        "current_pr": PR,
        "pr_history": [{"pr": PR, "opened_at": "2026-09-15T10:00:00+09:00",
                        "closed_at": None, "rounds": 1}],
        "tmp_dir": str(tmp_dir),
        "max_rounds": 12,
        "review_instructions": "レビュー観点の本文",
        "rounds": [{"round": 1, "pr": PR, "started_at": "2026-09-15T10:00:00+09:00",
                    "reviewers": ["codex", "kiro"]}],
        "review_findings": [],
        "final": None,
    }


@pytest.fixture()
def review_dirs(tmp_path, monkeypatch):
    worktree = tmp_path / "worktree"
    tmp_dir = worktree / ".cross_review"
    tmp_dir.mkdir(parents=True)
    metrics = tmp_path / "metrics"
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_dir))
    monkeypatch.setenv("NDF_METRICS_DIR", str(metrics))
    monkeypatch.delenv("NDF_METRICS", raising=False)
    return worktree, tmp_dir, metrics


def _summaries(metrics: pathlib.Path) -> list[pathlib.Path]:
    return sorted(metrics.rglob("*.json"))


# ---------- AC8 / AC11 / AC13 ----------

def test_every_save_rewrites_one_summary_outside_the_worktree(state_mod, review_dirs):
    worktree, tmp_dir, metrics = review_dirs
    state = _state(tmp_dir)
    state_mod._save(PR, state)
    [first] = _summaries(metrics)
    assert first == metrics / "devbasex--ai-plugins" / f"cross-review-pr{PR}-20260915T010000Z.json"
    assert json.loads(first.read_text())["final"] is None

    state["final"] = "approved"
    state["ended_at"] = "2026-09-15T10:40:00+09:00"
    state_mod._save(PR, state)
    assert _summaries(metrics) == [first]

    # AC11: 作業ツリーを消しても要約は残り、読める
    shutil.rmtree(worktree)
    summary = json.loads(first.read_text())
    assert summary["final"] == "approved"
    assert summary["wall_clock_seconds"] == 2400
    assert summary["kind"] == "cross-review"


def test_summary_measure_equals_measure_py_output(state_mod, review_dirs):
    _, tmp_dir, metrics = review_dirs
    state_mod._save(PR, _state(tmp_dir))
    [path] = _summaries(metrics)

    proc = subprocess.run(
        [sys.executable, str(_MEASURE), str(tmp_dir / f"cross-review-pr{PR}-state.json")],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(path.read_text())["measure"] == json.loads(proc.stdout)


def test_summary_does_not_carry_review_instructions(state_mod, review_dirs):
    _, tmp_dir, metrics = review_dirs
    state_mod._save(PR, _state(tmp_dir))
    [path] = _summaries(metrics)
    assert "レビュー観点の本文" not in path.read_text()


# ---------- AC16 ----------

def _run(state_mod, func, capsys) -> tuple[int, str, str]:
    code = 0
    try:
        func(argparse.Namespace(pr=PR))
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_summary_failure_keeps_exit_code_and_stdout(state_mod, review_dirs, monkeypatch, capsys):
    _, tmp_dir, _ = review_dirs
    state_path = tmp_dir / f"cross-review-pr{PR}-state.json"
    original = json.dumps(_state(tmp_dir), ensure_ascii=False)

    monkeypatch.setenv("NDF_METRICS", "0")
    state_path.write_text(original, encoding="utf-8")
    expected = _run(state_mod, state_mod.cmd_judge, capsys)

    monkeypatch.delenv("NDF_METRICS")

    def boom(*args, **kwargs):
        raise RuntimeError("要約が壊れた")

    monkeypatch.setattr(state_mod.run_metrics, "build_summary", boom)
    state_path.write_text(original, encoding="utf-8")
    actual = _run(state_mod, state_mod.cmd_judge, capsys)

    assert actual[:2] == expected[:2]
    # 保存を通ったこと（要約の書き出しで例外が起きたこと）を確かめる
    assert "要約が壊れた" in actual[2]


# ---------- AC23 ----------

def test_report_ends_with_the_summary_path(state_mod, review_dirs, capsys):
    _, tmp_dir, metrics = review_dirs
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(
        json.dumps(_state(tmp_dir), ensure_ascii=False), encoding="utf-8")

    state_mod.cmd_report(argparse.Namespace(pr=PR))

    last = capsys.readouterr().out.rstrip("\n").splitlines()[-1]
    [path] = _summaries(metrics)
    assert last == f"計測の要約: {path.resolve()}"


def test_report_says_why_it_did_not_write(state_mod, review_dirs, monkeypatch, capsys):
    _, tmp_dir, metrics = review_dirs
    monkeypatch.setenv("NDF_METRICS", "0")
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(
        json.dumps(_state(tmp_dir), ensure_ascii=False), encoding="utf-8")

    state_mod.cmd_report(argparse.Namespace(pr=PR))

    last = capsys.readouterr().out.rstrip("\n").splitlines()[-1]
    assert last == "計測の要約: 書いていません（NDF_METRICS=0）"
    assert _summaries(metrics) == []


# ---------- AC8: init の保存も要約を書く ----------

def _init_args(worktree: pathlib.Path, **over) -> argparse.Namespace:
    args = dict(pr=PR, max_rounds=12, rotate_after=8, only=None, worktree=str(worktree),
                focus=None, extra_instructions_file=None, host="claude")
    args.update(over)
    return argparse.Namespace(**args)


def test_new_init_writes_the_summary(state_mod, review_dirs, monkeypatch):
    """`init` が初期状態を保存した直後に要約がある。start-round を待たない。"""
    worktree, tmp_dir, metrics = review_dirs
    monkeypatch.setattr(state_mod, "_repo_from_git", lambda: "devbasex/ai-plugins")
    monkeypatch.setattr(
        state_mod, "GITHUB", state_mod.GITHUB._replace(
            fetch_pr_metadata=lambda pr, repo=None: state_mod.PrMetadata(
                "devbasex/ai-plugins", "takemi", "feat/x", "abc123", "develop", True, 4000, None)))
    monkeypatch.setattr(state_mod, "_fetch_changed_files", lambda pr, repo: [])
    monkeypatch.setattr(state_mod, "_is_registered_worktree", lambda wt: True)
    monkeypatch.setattr(state_mod, "_sync_worktree", lambda *a, **k: None)
    monkeypatch.setattr(state_mod, "_sh", lambda cmd, check=True: "takemi")
    monkeypatch.setattr(
        state_mod.subprocess, "run",
        lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""))
    monkeypatch.setenv("NDF_SKIP_AUTH_CHECK", "1")

    state_mod.cmd_init(_init_args(worktree))

    assert (tmp_dir / f"cross-review-pr{PR}-state.json").exists()
    [path] = _summaries(metrics)
    assert json.loads(path.read_text())["kind"] == "cross-review"


def test_resume_that_updates_the_state_rewrites_the_summary(state_mod, review_dirs, monkeypatch):
    """再開の入口で状態を書き戻すときも、同じ保存の経路を通る。"""
    worktree, tmp_dir, metrics = review_dirs
    state = _state(tmp_dir)
    state.update(auto_review_instructions="", worktree_path=str(worktree))
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(state_mod, "_repo_from_git", lambda: "devbasex/ai-plugins")
    monkeypatch.setattr(state_mod, "_fetch_unresolved_threads", lambda repo, pr: [])
    monkeypatch.setattr(state_mod, "_sync_worktree", lambda *a, **k: None)

    state_mod.cmd_init(_init_args(worktree, focus="追加の観点"))

    [path] = _summaries(metrics)
    assert json.loads(path.read_text())["kind"] == "cross-review"
