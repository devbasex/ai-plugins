"""`init` のテスト。

`gh` は呼ばないので `_sh` を差し替える。git は実際に動かし、
**書き込み用の作業ディレクトリが本当に作れるか**を確かめる。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git が必要")

HEAD_BRANCH = "refactor/target"


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True)


@pytest.fixture
def origin_repo(tmp_path):
    """origin にだけ head ブランチがあるリポジトリ。

    `git worktree add <path> <branch>` はローカルにブランチが無いと失敗するため、
    その経路を踏ませる形で用意する。
    """
    origin = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)],
                   check=True, capture_output=True)
    _git("config", "user.email", "t@e.st", cwd=repo)
    _git("config", "user.name", "test", cwd=repo)
    (repo / "src").mkdir()
    (repo / "src" / "foo.py").write_text("def f():\n    pass\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    _git("branch", "-M", "main", cwd=repo)
    _git("push", "-q", "origin", "main", cwd=repo)
    _git("checkout", "-qb", HEAD_BRANCH, cwd=repo)
    (repo / "src" / "bar.py").write_text("x = 1\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "wip", cwd=repo)
    _git("push", "-q", "origin", HEAD_BRANCH, cwd=repo)
    # ローカルの head ブランチを消し、origin にだけある状態にする
    _git("checkout", "-q", "main", cwd=repo)
    _git("branch", "-qD", HEAD_BRANCH, cwd=repo)
    return repo


def _args(tmp_path, **over):
    base = {
        "pr": 130, "scope": ["src"], "host": "claude",
        "max_outer_rounds": 3, "max_fix_rounds": 3, "max_items_per_round": 5,
        "severity_threshold": "minor", "model": None, "baseline_test": None,
        "worktree_root": str(tmp_path / "rf130"),
    }
    base.update(over)
    return type("A", (), base)()


@pytest.fixture
def run_init(refactor, origin_repo, monkeypatch):
    """`gh` 呼び出しだけを差し替えて `init` を走らせる。"""
    def _run(args):
        real_sh = refactor._sh

        def fake_sh(cmd, cwd=None, check=True):
            if cmd[0] == "gh":
                if "nameWithOwner" in cmd:
                    return "acme/demo"
                if "headRefName" in cmd:
                    return HEAD_BRANCH
                if "baseRefName" in cmd:
                    return "main"
                raise AssertionError(f"想定外の gh 呼び出し: {cmd}")
            return real_sh(cmd, cwd=cwd, check=check)

        monkeypatch.setattr(refactor, "_sh", fake_sh)
        monkeypatch.chdir(origin_repo)
        monkeypatch.delenv("CROSS_REFACTORING_TMP_DIR", raising=False)
        refactor.cmd_init(args)
    return _run


def _state_of(tmp_path):
    path = (tmp_path / "rf130" / "work" / ".cross_refactoring"
            / "cross-refactoring-rf130-state.json")
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_init_creates_the_writable_worktree_from_origin(run_init, tmp_path):
    """ローカルに head ブランチが無くても作業ディレクトリを作れること。"""
    run_init(_args(tmp_path))
    work = tmp_path / "rf130" / "work"
    assert (work / "src" / "bar.py").is_file()
    head = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                          cwd=work, capture_output=True, text=True)
    assert head.stdout.strip() == HEAD_BRANCH


def test_init_records_cohorts_separately(run_init, tmp_path):
    """提案・レビューと適用の母集合は別物である。"""
    run_init(_args(tmp_path))
    _, state = _state_of(tmp_path)
    assert state["runtimes"] == ["codex", "gemini", "kiro"]
    assert state["impl_capable"] == ["claude", "codex", "kiro"]
    assert state["host"] == "claude"
    assert state["host_detection"] == "explicit"
    assert state["host"] not in state["runtimes"]


def test_init_records_models(run_init, tmp_path):
    run_init(_args(tmp_path, model=["codex=gpt-5.5", "kiro=claude-opus-5"]))
    _, state = _state_of(tmp_path)
    assert state["models"] == {
        "claude": None, "codex": "gpt-5.5", "gemini": None, "kiro": "claude-opus-5",
    }


def test_init_rejects_unknown_model_runtime(run_init, tmp_path):
    with pytest.raises(SystemExit):
        run_init(_args(tmp_path, model=["gpt=gpt-5.5"]))


def test_init_rejects_gemini_as_host(run_init, tmp_path):
    with pytest.raises(SystemExit):
        run_init(_args(tmp_path, host="gemini"))


def test_init_runs_the_baseline_test(run_init, tmp_path):
    run_init(_args(tmp_path, baseline_test="true"))
    _, state = _state_of(tmp_path)
    assert state["baseline_test"]["status"] == "green"
    assert state["baseline_test"]["command"] == "true"


def test_init_refuses_to_start_when_the_baseline_test_fails(run_init, tmp_path):
    """壊れた状態から始めると、壊したのか元から壊れていたのか区別できない。"""
    with pytest.raises(SystemExit):
        run_init(_args(tmp_path, baseline_test="false"))


def test_init_without_baseline_test_records_unknown(run_init, tmp_path):
    run_init(_args(tmp_path))
    _, state = _state_of(tmp_path)
    assert state["baseline_test"]["status"] == "unknown"


def test_init_is_idempotent(run_init, tmp_path, capsys):
    """再開時は既存の状態をそのまま返し、担当やモデルを作り直さない。"""
    run_init(_args(tmp_path, model=["codex=gpt-5.5"]))
    path, first = _state_of(tmp_path)
    first["rounds"].append({"round": 1, "impl": "codex"})
    path.write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")

    run_init(_args(tmp_path, model=["codex=gpt-4"]))
    _, second = _state_of(tmp_path)
    assert second["models"]["codex"] == "gpt-5.5", "指定値が上書きされている"
    assert len(second["rounds"]) == 1, "状態が作り直されている"


def test_init_emits_shell_assignments(run_init, tmp_path, capsys):
    run_init(_args(tmp_path))
    out = capsys.readouterr().out
    assert "RUNTIMES_CSV=codex,gemini,kiro" in out
    # 空白を含む値は必ず引用する。引用しないと呼び出し側の eval で語が割れる。
    assert "RUNTIMES='codex gemini kiro'" in out
    assert "IMPL_POOL='claude codex kiro'" in out
    assert "TMP_DIR=" in out and "WORK=" in out
