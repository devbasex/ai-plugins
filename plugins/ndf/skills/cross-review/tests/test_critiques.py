"""反証の取り込み（#156 の 3 本目 Task 3）。

**提案者以外が各指摘へ 1 つの値を返す。** 自己支持を数に入れると、1 者が出した指摘が
常に 1 票を持つ。統合された指摘では `origin_runtimes` に載る担当すべてが提案者である。
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest

PR = 6003


def _finding(fid, agent, **over):
    f = {
        "finding_id": fid, "agent": agent, "path": "a.py", "line": 1,
        "body": "x", "severity": "major", "pr": PR, "round": 1,
        "origin_runtimes": [agent],
    }
    f.update(over)
    return f


def _state(findings, **over):
    st = {
        "current_pr": PR, "repo": "o/r", "max_rounds": 12, "rotate_after": 8,
        "only": None, "host": "claude",
        "rounds": [{"round": 1, "pr": PR, "started_at": "2026-09-10T00:00:00+00:00"}],
        "review_findings": list(findings), "final": None,
    }
    st.update(over)
    return st


def _write(tmp_dir, st):
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(st))


def _read(tmp_dir):
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())


def _critique_file(tmp_dir, agent, items):
    (tmp_dir / f"{agent}-critique-pr{PR}-round1.json").write_text(
        json.dumps({"critiques": items}))


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


def collect(state_mod):
    state_mod.cmd_collect_critiques(argparse.Namespace(pr=PR))


# ---------- 取り込み ----------

def test_a_critique_is_attached_to_the_finding(tmp_dir, state_mod):
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    _critique_file(tmp_dir, "kiro", [
        {"finding_id": "codex-r1-0", "verdict": "refute", "reason": "None を弾く"}])

    collect(state_mod)

    got = _read(tmp_dir)["review_findings"][0]["critiques"]
    assert got == [{"agent": "kiro", "verdict": "refute", "reason": "None を弾く"}]


def test_the_agent_comes_from_the_file_name(tmp_dir, state_mod):
    """**本文の申告を採らない。** 別の担当を名乗った値をそのまま数えることになる。"""
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    _critique_file(tmp_dir, "kiro", [
        {"finding_id": "codex-r1-0", "verdict": "support", "reason": "確認した",
         "agent": "agy"}])

    collect(state_mod)

    assert _read(tmp_dir)["review_findings"][0]["critiques"][0]["agent"] == "kiro"


def test_critiques_from_several_agents_accumulate(tmp_dir, state_mod):
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    _critique_file(tmp_dir, "kiro", [
        {"finding_id": "codex-r1-0", "verdict": "support", "reason": "a"}])
    _critique_file(tmp_dir, "agy", [
        {"finding_id": "codex-r1-0", "verdict": "refute", "reason": "b"}])

    collect(state_mod)

    got = _read(tmp_dir)["review_findings"][0]["critiques"]
    assert sorted(c["agent"] for c in got) == ["agy", "kiro"]


# ---------- 自分の指摘へは返さない ----------

def test_a_critique_on_own_finding_is_dropped(tmp_dir, state_mod):
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    _critique_file(tmp_dir, "codex", [
        {"finding_id": "codex-r1-0", "verdict": "support", "reason": "自分で確認"}])

    collect(state_mod)

    assert _read(tmp_dir)["review_findings"][0].get("critiques", []) == []


def test_a_critique_from_a_merged_proposer_is_dropped(tmp_dir, state_mod):
    """統合された指摘では、`origin_runtimes` の担当すべてが提案者である。"""
    _write(tmp_dir, _state([
        _finding("codex-r1-0", "codex", origin_runtimes=["codex", "kiro"])]))
    _critique_file(tmp_dir, "kiro", [
        {"finding_id": "codex-r1-0", "verdict": "support", "reason": "自分も出した"}])

    collect(state_mod)

    assert _read(tmp_dir)["review_findings"][0].get("critiques", []) == []


# ---------- 結び先が無いとき ----------

def test_an_unknown_finding_id_is_kept_separately(tmp_dir, state_mod):
    """**黙って捨てない。** 反証 0 件と、結び先を誤ったラウンドが同じに見える。"""
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    _critique_file(tmp_dir, "kiro", [
        {"finding_id": "nowhere-r1-9", "verdict": "refute", "reason": "?"}])

    collect(state_mod)

    st = _read(tmp_dir)
    assert st["review_findings"][0].get("critiques", []) == []
    assert st["unmatched_critiques"][0]["finding_id"] == "nowhere-r1-9"
    assert st["unmatched_critiques"][0]["agent"] == "kiro"


def test_a_duplicate_keeps_its_target(tmp_dir, state_mod):
    """`duplicate` は相手の `finding_id` を持つ。2 段目の統合が読む。"""
    _write(tmp_dir, _state([
        _finding("codex-r1-0", "codex"), _finding("kiro-r1-0", "kiro")]))
    _critique_file(tmp_dir, "agy", [
        {"finding_id": "codex-r1-0", "verdict": "duplicate",
         "duplicate_of": "kiro-r1-0", "reason": "同じ箇所"}])

    collect(state_mod)

    got = _read(tmp_dir)["review_findings"][0]["critiques"][0]
    assert got["duplicate_of"] == "kiro-r1-0"


# ---------- 形が違うとき ----------

def test_an_unknown_verdict_is_dropped(tmp_dir, state_mod):
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    _critique_file(tmp_dir, "kiro", [
        {"finding_id": "codex-r1-0", "verdict": "maybe", "reason": "?"}])

    collect(state_mod)

    st = _read(tmp_dir)
    assert st["review_findings"][0].get("critiques", []) == []
    assert st["unmatched_critiques"][0]["verdict"] == "maybe"


def test_a_broken_file_does_not_stop_the_import(tmp_dir, state_mod):
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))
    (tmp_dir / f"kiro-critique-pr{PR}-round1.json").write_text("not json")
    _critique_file(tmp_dir, "agy", [
        {"finding_id": "codex-r1-0", "verdict": "support", "reason": "a"}])

    collect(state_mod)

    got = _read(tmp_dir)["review_findings"][0]["critiques"]
    assert [c["agent"] for c in got] == ["agy"]


def test_no_files_leaves_the_findings_untouched(tmp_dir, state_mod):
    _write(tmp_dir, _state([_finding("codex-r1-0", "codex")]))

    collect(state_mod)

    assert _read(tmp_dir)["review_findings"][0].get("critiques", []) == []


# ---------- 起動（critique.sh） ----------

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"


def _state_file(tmp_dir, findings, worktree):
    st = _state(findings)
    st["worktree_path"] = str(worktree)
    _write(tmp_dir, st)


def run_critique(tmp_dir, agent, worktree, launcher=None):
    import subprocess
    env = dict(os.environ, CROSS_REVIEW_TMP_DIR=str(tmp_dir))
    if launcher:
        env["PATH"] = f"{launcher}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(SCRIPTS / "critique.sh"), agent, str(PR), "1"],
        capture_output=True, text=True, env=env,
    )


import os


@pytest.fixture()
def fake_launcher(tmp_path):
    """`launch-cli.sh` の呼び出しを記録するだけの置き換え。"""
    lib = tmp_path / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "launch-cli.sh").write_text(
        '#!/usr/bin/env bash\necho "launched $1" > "$6/launched.txt"\n')
    (lib / "launch-cli.sh").chmod(0o755)
    return lib


def test_the_prompt_lists_only_other_agents_findings(tmp_dir, tmp_path):
    """**自分が出した指摘は渡さない。**"""
    work = tmp_path / "work"; work.mkdir()
    _state_file(tmp_dir, [
        _finding("codex-r1-0", "codex"),
        _finding("kiro-r1-0", "kiro"),
    ], work)

    run_critique(tmp_dir, "kiro", work)

    prompt = (tmp_dir / f"kiro-critique-pr{PR}-prompt.md").read_text()
    assert "codex-r1-0" in prompt
    assert "kiro-r1-0" not in prompt


def test_a_merged_proposer_is_excluded(tmp_dir, tmp_path):
    """統合された指摘では `origin_runtimes` の担当すべてが提案者である。"""
    work = tmp_path / "work"; work.mkdir()
    _state_file(tmp_dir, [
        _finding("codex-r1-0", "codex", origin_runtimes=["codex", "kiro"]),
    ], work)

    result = run_critique(tmp_dir, "kiro", work)

    assert "対象がありません" in result.stdout
    assert not (tmp_dir / f"kiro-critique-pr{PR}-prompt.md").exists()


@pytest.mark.parametrize("findings", [
    [],
    [_finding("codex-r2-0", "codex", round=2)],
], ids=["empty-findings", "other-round-only"])
def test_no_targets_exits_successfully_without_launching(tmp_dir, tmp_path, findings):
    """現状固定: 対象 0 件では案内だけを出し、生成・起動を行わない。"""
    work = tmp_path / "work"
    work.mkdir()
    _state_file(tmp_dir, findings, work)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "kiro-cli"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    before = set(tmp_dir.rglob("*"))

    result = run_critique(tmp_dir, "kiro", work, launcher=bin_dir)

    assert result.returncode == 0, result.stderr
    assert "反証の対象がありません" in result.stdout
    assert result.stderr == ""
    assert not (tmp_dir / f"kiro-critique-pr{PR}-prompt.md").exists()
    assert set(tmp_dir.rglob("*")) == before


def test_a_merged_side_is_not_offered(tmp_dir, tmp_path):
    work = tmp_path / "work"; work.mkdir()
    _state_file(tmp_dir, [
        _finding("codex-r1-0", "codex"),
        _finding("codex-r1-1", "codex", merged_into="codex-r1-0"),
    ], work)

    run_critique(tmp_dir, "kiro", work)

    prompt = (tmp_dir / f"kiro-critique-pr{PR}-prompt.md").read_text()
    assert "codex-r1-0" in prompt
    assert "codex-r1-1" not in prompt


def test_the_prompt_names_the_five_verdicts(tmp_dir, tmp_path):
    work = tmp_path / "work"; work.mkdir()
    _state_file(tmp_dir, [_finding("codex-r1-0", "codex")], work)

    run_critique(tmp_dir, "kiro", work)

    prompt = (tmp_dir / f"kiro-critique-pr{PR}-prompt.md").read_text()
    for verdict in ("support", "refute", "insufficient_evidence",
                    "duplicate", "out_of_scope"):
        assert verdict in prompt


def test_the_prompt_forbids_editing(tmp_dir, tmp_path):
    work = tmp_path / "work"; work.mkdir()
    _state_file(tmp_dir, [_finding("codex-r1-0", "codex")], work)

    run_critique(tmp_dir, "kiro", work)

    prompt = (tmp_dir / f"kiro-critique-pr{PR}-prompt.md").read_text()
    assert "編集しない" in prompt


def test_an_unknown_runtime_is_refused(tmp_dir, tmp_path):
    work = tmp_path / "work"; work.mkdir()
    _state_file(tmp_dir, [_finding("codex-r1-0", "codex")], work)

    result = run_critique(tmp_dir, "nowhere", work)

    assert result.returncode != 0


@pytest.mark.parametrize("state_setup", ["missing", "empty"], ids=["missing", "empty"])
def test_missing_or_empty_state_file_exits_with_error(tmp_dir, tmp_path, state_setup):
    """現状固定: state.json が存在しない、または空ファイルの場合は終了コード 1 で終了する。"""
    work = tmp_path / "work"
    work.mkdir()
    if state_setup == "empty":
        (tmp_dir / f"cross-review-pr{PR}-state.json").write_text("")

    result = run_critique(tmp_dir, "kiro", work)

    assert result.returncode == 1
    assert "state.json not found" in result.stderr

