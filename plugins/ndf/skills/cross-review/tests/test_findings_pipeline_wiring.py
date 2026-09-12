"""指摘の整理が副コマンドから届くことを固定する（#156 の 3 本目）。

**統合・実行検証は、書いてあっても呼ばれなければ効かない。** 実際に `_verify_findings` /
`_merge_duplicates` / `_merge_declared_duplicates` はテストからしか呼ばれておらず、
`judge` が読む区分は実行の結果も統合も反映しないまま決まっていた。

順序は「統合 1 段目 → 実行検証 → 反証 → 取り込み（統合 2 段目）」である
（`docs/06-evidence.md` の「走らせる順序」）。
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib

import pytest

STATE_PY = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "state.py"

PR = 6101

# 副コマンドから届かなければならない補助（呼ばれないと区分が決まらない）。
WIRED_HELPERS = ("_merge_duplicates", "_verify_findings", "_merge_declared_duplicates")


# ---------- 配線（構文木で見る） ----------


def _called_names(fn: ast.FunctionDef) -> set[str]:
    return {
        node.func.id for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


@pytest.fixture(scope="module")
def functions() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(STATE_PY.read_text(encoding="utf-8"))
    return {
        node.name: node for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


@pytest.mark.parametrize("helper", WIRED_HELPERS)
def test_the_helper_is_reachable_from_a_subcommand(functions, helper: str) -> None:
    """`cmd_*` のいずれかから、直接または間接に呼ばれている。"""
    reached: set[str] = set()
    frontier = [name for name in functions if name.startswith("cmd_")]
    while frontier:
        name = frontier.pop()
        if name in reached or name not in functions:
            continue
        reached.add(name)
        frontier.extend(_called_names(functions[name]))
    assert helper in reached, f"{helper} がどの副コマンドからも呼ばれていない"


def test_the_subcommand_is_registered(state_mod) -> None:
    args = state_mod.build_parser().parse_args(["verify-findings", str(PR)])
    assert args.func is state_mod.cmd_verify_findings


def test_init_takes_the_verification_arguments(state_mod) -> None:
    args = state_mod.build_parser().parse_args(
        ["init", str(PR), "--verify-command", "pytest", "--verify-exit-code", "3"])
    assert args.verify_command == ["pytest"]
    assert args.verify_exit_code == [3]


# ---------- 振る舞い ----------


def _finding(fid, agent, **over):
    f = {
        "finding_id": fid, "agent": agent, "path": "a.py", "line": 1,
        "body": "同じ本文", "severity": "major", "pr": PR, "round": 1,
        "origin_runtimes": [agent], "suggested_check": "",
    }
    f.update(over)
    return f


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


def _write(tmp_dir, findings, worktree, **over):
    st = {
        "current_pr": PR, "repo": "o/r", "max_rounds": 12, "rotate_after": 8,
        "only": None, "host": "claude", "worktree_path": str(worktree),
        "rounds": [{"round": 1, "pr": PR, "reviewers": ["codex", "kiro"],
                    "started_at": "2026-09-10T00:00:00+00:00"}],
        "review_findings": list(findings), "final": None,
    }
    st.update(over)
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(st))


def _read(tmp_dir):
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())


def test_verify_findings_merges_and_records(tmp_dir, tmp_path, state_mod) -> None:
    """1 段目の統合と実行検証が、1 度の呼び出しで状態ファイルへ残る。"""
    work = tmp_path / "work"
    work.mkdir()
    (work / "t.py").write_text("")
    _write(
        tmp_dir,
        [
            _finding("codex-r1-0", "codex", suggested_check="pytest t.py"),
            _finding("kiro-r1-0", "kiro"),
        ],
        work,
        verify_commands=["pytest"],
    )

    state_mod.cmd_verify_findings(argparse.Namespace(pr=PR))

    findings = {f["finding_id"]: f for f in _read(tmp_dir)["review_findings"]}
    # 同じファイル・同じ行・同じ本文なので 1 件へ束ねられる。
    assert findings["kiro-r1-0"]["merged_into"] == "codex-r1-0"
    assert findings["codex-r1-0"]["origin_runtimes"] == ["codex", "kiro"]
    # 実行検証の記録が代表へ残る（実行そのものの成否は問わない）。
    assert findings["codex-r1-0"]["verification"]["command"] == "pytest t.py"


def test_verify_findings_records_not_run_without_a_command(
    tmp_dir, tmp_path, state_mod
) -> None:
    """**渡されなければ実行しない。** 行わなかったことを記録へ残す。"""
    work = tmp_path / "work"
    work.mkdir()
    _write(tmp_dir, [_finding("codex-r1-0", "codex", suggested_check="rm -rf /")], work)

    state_mod.cmd_verify_findings(argparse.Namespace(pr=PR))

    record = _read(tmp_dir)["review_findings"][0]["verification"]
    assert record["result"] == "not_run"
    assert record["exit_code"] is None


def test_collect_critiques_merges_declared_duplicates(
    tmp_dir, tmp_path, state_mod
) -> None:
    """2 段目の統合が、反証の取り込みと同じ呼び出しで走る。"""
    work = tmp_path / "work"
    work.mkdir()
    _write(tmp_dir, [
        _finding("codex-r1-0", "codex", body="片方の本文"),
        _finding("kiro-r1-0", "kiro", body="もう片方の本文"),
    ], work)
    for agent, fid, other in (
        ("kiro", "codex-r1-0", "kiro-r1-0"),
        ("codex", "kiro-r1-0", "codex-r1-0"),
    ):
        (tmp_dir / f"{agent}-critique-pr{PR}-round1.json").write_text(json.dumps(
            {"critiques": [
                {"finding_id": fid, "verdict": "duplicate",
                 "duplicate_of": other, "reason": "同じ主張である"}]}))

    state_mod.cmd_collect_critiques(argparse.Namespace(pr=PR))

    findings = {f["finding_id"]: f for f in _read(tmp_dir)["review_findings"]}
    assert findings["kiro-r1-0"]["merged_into"] == "codex-r1-0"
    assert findings["codex-r1-0"]["origin_runtimes"] == ["codex", "kiro"]
