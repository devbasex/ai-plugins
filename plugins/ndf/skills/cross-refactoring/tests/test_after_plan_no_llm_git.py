"""改修計画の後の手順で、判断のために LLM を呼ばない（#933 決定 25 / 実装計画 I17）。

改修計画の後で LLM が動くのは、作業の CLI（テストの追加・実装・直し）だけである。
取り込み・検証・取り消し・絞り込み・揺れの判定・最終ゲートは、状態ファイルの値・git・
テストの終了コードだけで進む。ここでは Jev と CLI の呼び出し口を差し替え、呼ばれたら
落とす形で、実装の取り込みから最終ゲートまでを通す。
"""
from __future__ import annotations

import argparse
import subprocess
import sys

import pytest

from crossref_helpers import (
    CALC,
    build_git_flow,
    commit_with_trailers,
    git,
    item_trailers,
    read_state,
    write_state,
)

FAR = "2099-01-01T00:00:00+00:00"
LLM_CLIS = {"claude", "codex", "kiro", "kiro-cli", "agy"}


@pytest.fixture
def flow(tmp_path, monkeypatch, refactor, patch_lib, env_tmp_dir):
    return build_git_flow(tmp_path, monkeypatch, patch_lib, env_tmp_dir,
                          judge={"kind": "jev", "reason": None, "failures": 0})


@pytest.fixture
def forbid_llm(monkeypatch):
    """Jev の問いと、LLM の CLI の起動を禁じる。呼ばれたら落とす。"""
    import jev

    def refuse(*args, **kwargs):
        raise AssertionError(f"改修計画の後に Jev が呼ばれた: {args[:1]}")

    for name in ("ask_boolean", "ask_score", "decide"):
        monkeypatch.setattr(jev, name, refuse)
    for mod in list(sys.modules.values()):
        if getattr(mod, "__name__", "").startswith("refactor_lib") and hasattr(mod, "jev"):
            for name in ("ask_boolean", "ask_score", "decide"):
                monkeypatch.setattr(mod.jev, name, refuse)

    real_popen, real_run = subprocess.Popen, subprocess.run

    def _check(cmd):
        words = cmd if isinstance(cmd, (list, tuple)) else str(cmd).split()
        head = str(words[0]).rsplit("/", 1)[-1] if words else ""
        if head in LLM_CLIS:
            raise AssertionError(f"改修計画の後に LLM の CLI が起動された: {cmd}")

    def popen(cmd, *args, **kwargs):
        _check(cmd)
        return real_popen(cmd, *args, **kwargs)

    def run(cmd, *args, **kwargs):
        _check(cmd)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(subprocess, "run", run)


def _call(module, name, **kwargs):
    return getattr(module, name)(argparse.Namespace(id=130, **kwargs))


def _item(item_id, rank, public_io):
    return {
        "id": item_id, "rank": rank, "path": "src/calc.py", "symbol": "add",
        "smell": "long_method", "technique": "extract_method", "severity": "major",
        "proposed_by": ["codex"], "tier": "high", "risk": False, "public_io": public_io,
        "tests": [], "test_targets": ["tests/test_calc.py"],
        "command": ["pytest", "-q", "tests/test_calc.py"], "command_source": "targets",
        "estimate": {"test": 0.0, "implement": 1.3, "verify": 0.2},
        "start_deadline": FAR, "test_start_deadline": None,
        "status": "planned", "commits": {"test": None, "implement": None, "fix": []},
        "seconds": {}, "fix_count": 0, "danger": [], "estimated_diff_lines": 20,
    }


def test_the_stages_after_the_plan_ask_no_llm(flow, cmd_setup, cmd_implement, cmd_converge,
                                              cmd_gate, forbid_llm):
    work = flow["work"]
    state = read_state(flow["path"])
    state["items"] = [_item("I-001", 1, public_io=True)]      # D5 は改修計画で決めてある
    state["plan"] = {"base_sha": git("rev-parse", "HEAD", cwd=work).stdout.strip(),
                     "reserve": {"danger_whole_test": 0.1, "final_whole_test": 0.1, "fix": 5.5},
                     "end_at": FAR, "table_source": "defaults"}
    state["phase"] = "implement"
    write_state(flow["path"], state)

    _call(cmd_setup, "cmd_start_phase", phase="implement")
    # 期待値の外の差分（経路だけの変更）は機械で決まらない。LLM へ問わずにレビューへ引き継ぐ。
    test_file = work / "tests" / "test_calc.py"
    test_file.write_text(test_file.read_text().replace(
        "from src.calc import add", "from src import calc").replace("add(1, 2)", "calc.add(1, 2)"),
        encoding="utf-8")
    (work / "src" / "calc.py").write_text(CALC + "\n", encoding="utf-8")
    commit_with_trailers(work, "Refactor add", item_trailers("I-001"))
    _call(cmd_implement, "cmd_merge_implement")
    _call(cmd_converge, "cmd_verify")
    _call(cmd_gate, "cmd_final_gate")

    state = read_state(flow["path"])
    item = state["items"][0]
    assert item["status"] == "verified"
    assert "D5" in item["danger"] and state["whole_test"]["ran"] is True
    assert item["review_test_judgements"] == ["tests/test_calc.py"]
    assert state["final_gate"]["status"] == "passed"
    assert state["judge"]["failures"] == 0
