"""cross-review のランタイムの宣言（`.ndf/runtimes.json`。#1598）。

宣言の範囲で参加者を決め（AC2〜AC5）、再開した時点の宣言に従い（AC12・I4）、ラウンドごとに
席・ランタイム・モデル・組の相手を残す（AC13・I6）。宣言はカレントディレクトリのリポジトリから読む。
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest
import review_lib
import review_lib.commands.init
import review_lib.commands.read_result
import review_lib.commands.start_round
import review_lib.findings
import review_lib.github
import review_lib.participants
import review_lib.posts
import review_lib.workspace

PR = 6200
REPO = "acme/demo"


def _declare(root: pathlib.Path, body: dict) -> pathlib.Path:
    (root / ".ndf").mkdir(parents=True, exist_ok=True)
    f = root / ".ndf" / "runtimes.json"
    f.write_text(json.dumps(body), encoding="utf-8")
    return f


@pytest.fixture()
def repo_dir(tmp_path, monkeypatch):
    """宣言を置くリポジトリ。カレントディレクトリにする（宣言はそこから読む）。"""
    d = tmp_path / "repo"
    d.mkdir()
    monkeypatch.chdir(d)
    return d


@pytest.fixture()
def env(state_mod, monkeypatch, tmp_path):
    """初期化と再開を GitHub と git に触れずに通す。認証確認の呼ばれた名前を控える。"""
    (tmp_path / "wt").mkdir(exist_ok=True)
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(review_lib.github, "_repo_from_git", lambda: REPO)
    monkeypatch.setattr(review_lib.github, "_repo_from_gh", lambda: REPO)
    monkeypatch.setattr(
        review_lib.github,
        "_fetch_pr_metadata",
        lambda pr, repo=None: review_lib.github.PrMetadata(REPO, "author", "feat/x", "abc", "develop", False, 4000, None),
    )
    monkeypatch.setattr(review_lib.github, "_viewer_login", lambda: "viewer")
    monkeypatch.setattr(review_lib.github, "_fetch_changed_files", lambda pr, repo: [])
    monkeypatch.setattr(review_lib.posts, "_auto_flush", lambda pr: None)
    monkeypatch.setattr(review_lib.findings, "_record_carried_over", lambda *a, **k: False)
    monkeypatch.setattr(review_lib.workspace, "_is_registered_worktree", lambda path: True)
    monkeypatch.setattr(review_lib.workspace, "_sync_worktree", lambda *a, **k: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="", stderr=""))
    monkeypatch.setattr(review_lib.commands.start_round, "_sync_before_round", lambda st, pr: None)
    calls: list[str] = []
    failing: set[str] = set()

    def probe(runtimes, *, info, env=None):
        calls.extend(runtimes)
        return ({r: {"command": r, "ok": r not in failing, "detail": "未認証" if r in failing else ""} for r in runtimes}, False)

    monkeypatch.setattr(review_lib.participants.auth, "probe_auth", probe)
    state_file = tmp_path / f"cross-review-pr{PR}-state.json"

    def init(*argv: str) -> dict:
        args = state_mod.build_parser().parse_args(["init", str(PR), "--host", "claude", "--worktree", str(tmp_path / "wt"), *argv])
        review_lib.commands.init.cmd_init(args)
        return json.loads(state_file.read_text())

    def start_round() -> dict:
        review_lib.commands.start_round.cmd_start_round(type("A", (), {"pr": PR})())
        return json.loads(state_file.read_text())["rounds"][-1]

    init.calls = calls
    init.failing = failing
    init.start_round = start_round
    init.state_file = state_file
    return init


def test_claude_only_runs_claude_and_claude2_without_probing_others(env, repo_dir):
    """AC2: 宣言が claude だけなら、引数なしで各ラウンドの席が claude と claude-2。認証確認も claude だけ。"""
    f = _declare(repo_dir, {"allowed": ["claude"]})
    st = env()
    assert env.calls == ["claude"]
    assert st["participants"]["pool"] == ["claude"]
    assert st["participants"]["policy"] == {"path": str(f.resolve()), "allowed": ["claude"], "review_seats": None}
    for _ in range(2):
        r = env.start_round()
        assert r["reviewers"] == ["claude", "claude-2"]
        data = json.loads(env.state_file.read_text())
        data["rounds"][-1]["verdict"] = "approved"
        data["rounds"][-1]["fix"] = None
        env.state_file.write_text(json.dumps(data))


@pytest.mark.parametrize("argv", [("--include", "codex"), ("--only", "codex")])
def test_outside_argument_stops_before_participants(env, repo_dir, capsys, argv):
    """AC3・AC4・AC16: 宣言の外の名前は参加者を決める前に終了コード 1。理由にパスと allowed。"""
    f = _declare(repo_dir, {"allowed": ["claude"]})
    with pytest.raises(SystemExit) as e:
        env(*argv)
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "codex は宣言の外" in err and str(f.resolve()) in err and "allowed: claude" in err
    assert env.calls == []
    assert not env.state_file.exists()


def test_broken_declaration_stops_init(env, repo_dir, capsys):
    """AC10: 壊れた宣言では認証確認も起動もせずに止まる。"""
    (repo_dir / ".ndf").mkdir()
    (repo_dir / ".ndf" / "runtimes.json").write_text('{"allowed": []}')
    with pytest.raises(SystemExit) as e:
        env()
    assert e.value.code == 1
    assert "allowed が空" in capsys.readouterr().err
    assert env.calls == []


def test_pinned_pair_is_used_every_round(env, repo_dir):
    """AC6: 固定の組を宣言すると、使えるランタイムが複数でも毎ラウンド claude と claude-2。"""
    _declare(repo_dir, {"allowed": ["claude", "codex", "kiro"], "review_seats": ["claude", "claude-2"]})
    env()
    assert env.start_round()["reviewers"] == ["claude", "claude-2"]


def test_failed_auth_does_not_fill_with_outside_runtime(env, repo_dir):
    """AC7: codex が認証を通らなくても、宣言の外の kiro で埋めない。"""
    _declare(repo_dir, {"allowed": ["claude", "codex"]})
    env.failing.add("codex")
    env()
    assert "kiro" not in env.calls
    assert env.start_round()["reviewers"] == ["claude", "claude-2"]


def test_seats_record_runtime_model_and_partner(env, repo_dir):
    """AC13・I6: 2 席は互いが組の相手、`--only` の 1 席は相手が null。モデルは read-result まで null。"""
    env()
    r = env.start_round()
    a, b = r["reviewers"]
    assert r["seats"] == [
        {"seat": a, "runtime": a.split("-")[0], "model": None, "partner": b},
        {"seat": b, "runtime": b.split("-")[0], "model": None, "partner": a},
    ]
    assert review_lib.participants.seat_records(["codex"]) == [{"seat": "codex", "runtime": "codex", "model": None, "partner": None}]


def test_read_result_fills_the_model_from_claude_stdout(tmp_path, monkeypatch):
    """AC13: claude の stdout の modelUsage からモデルを埋め、読めなければ null のまま。"""
    monkeypatch.setattr(review_lib.commands.read_result.store, "_resolve_tmp_dir", lambda pr: tmp_path)
    (tmp_path / f"claude-2-review-pr{PR}-stdout.log").write_text(
        json.dumps({"type": "result", "result": "x", "modelUsage": {"claude-opus-5-5": {"inputTokens": 10}}})
    )
    entry = {"seats": review_lib.participants.seat_records(["claude", "claude-2"])}
    review_lib.commands.read_result._record_seat_model(entry, "claude-2", PR)
    review_lib.commands.read_result._record_seat_model(entry, "claude", PR)
    assert entry["seats"][1]["model"] == "claude-opus-5-5"
    assert entry["seats"][0]["model"] is None


def test_resume_drops_recorded_outside_runtime_and_reselects_the_open_round(env, repo_dir, capsys):
    """AC12・I4: codex を記録した実行を claude だけの宣言で再開すると、codex を起動しない。

    判定の済んだラウンドの記録は書き換えず、判定の済んでいないラウンドは選び直す。
    """
    st = env("--include", "codex")
    done = {"round": 1, "pr": PR, "started_at": "2026-10-01T00:00:00", "reviewers": ["claude", "codex"], "verdict": "approved"}
    open_ = {"round": 2, "pr": PR, "started_at": "2026-10-01T00:00:00", "reviewers": ["codex", "kiro"]}
    st["rounds"] = [done, open_]
    env.state_file.write_text(json.dumps(st))
    _declare(repo_dir, {"allowed": ["claude"]})
    env.calls.clear()
    st = env()
    assert env.calls == ["claude"]
    assert st["participants"]["available"] == ["claude"] and st["participants"]["included"] == []
    assert st["rounds"][0]["reviewers"] == ["claude", "codex"] and "seats" not in st["rounds"][0]
    assert st["rounds"][1]["reviewers"] == ["claude", "claude-2"]
    assert review_lib.participants._round_reviewers(st, 3) == ["claude", "claude-2"]
    assert any(c["field"] == "policy:included" for c in st["resume_changes"])


def test_resume_without_declaration_keeps_participants(env, repo_dir):
    """AC1: 宣言が無ければ、引数なしの再開は参加者を作り直さない。"""
    env()
    env.calls.clear()
    st = env()
    assert env.calls == []
    assert st["participants"]["policy"] is None
