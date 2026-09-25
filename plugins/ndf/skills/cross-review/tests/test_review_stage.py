"""設計 PR のレビューの 2 段（#1111）: 1 ラウンド目はモデルの段、2 ラウンド目以降は詳細の段。

| 条件 | 段 |
| --- | --- |
| design・1 ラウンド目・ドメインモデルの節がある | model |
| design・2 ラウンド目以降、または節が無い | detail |
| code | 段を持たない |

モデルの段の APPROVE では抜けずに詳細の段へ進み、関門の数とラウンドの上限は変えない。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from classifications import has_domain_model, review_stage  # noqa: E402

PR = 1111


@pytest.mark.parametrize("kind,round_no,has_model,want", [
    ("design", 1, True, "model"),
    ("design", 2, True, "detail"),
    ("design", 3, True, "detail"),
    ("design", 1, False, "detail"),
    ("code", 1, True, None),
    ("code", 2, False, None),
])
def test_review_stage_table(kind, round_no, has_model, want):
    assert review_stage(kind, round_no, has_model) == want


def test_has_domain_model_reads_changed_design_docs(tmp_path):
    (tmp_path / "issues").mkdir()
    (tmp_path / "issues/issue-1-design.md").write_text("# 設計\n\n## ドメインモデル\n\n### 集約\n")
    (tmp_path / "issues/issue-2-design.md").write_text("# 設計\n\n## 機能一覧\n")
    (tmp_path / "issues/issue-1-requirements.md").write_text("## ドメインモデル\n")
    assert has_domain_model(tmp_path, ["issues/issue-1-design.md"])
    assert not has_domain_model(tmp_path, ["issues/issue-2-design.md", "issues/issue-1-requirements.md"])
    assert not has_domain_model(tmp_path, ["issues/missing-design.md"])


def test_design_stage_fields_hold_instructions_per_stage(state_mod, tmp_path):
    (tmp_path / "issues").mkdir()
    (tmp_path / "issues/issue-1-design.md").write_text("## ドメインモデル\n")
    changed = [{"status": "A", "paths": ["issues/issue-1-design.md"]}]
    fields = state_mod._design_stage_fields("design", tmp_path, changed, "詳細の観点", "手で足した観点")
    assert fields["design_has_model"] is True
    by = fields["review_instructions_by_stage"]
    assert by["detail"] == "詳細の観点"
    assert by["model"].startswith(state_mod.MODEL_REVIEW_TEMPLATE) and by["model"].endswith("手で足した観点")
    assert state_mod._design_stage_fields("code", tmp_path, changed, "x", "") == {}


def test_round_stage_follows_state(state_mod):
    st = {"review_kind": "design", "design_has_model": True}
    assert [state_mod._round_stage(st, n) for n in (1, 2, 3)] == ["model", "detail", "detail"]
    assert state_mod._round_stage({"review_kind": "code"}, 1) is None
    assert state_mod._round_stage({}, 1) is None  # 分類を持たない状態ファイル（再開）


def _approved(no, **over):
    entry = {"round": no, "pr": PR, "started_at": "2026-09-25T00:00:00+00:00",
             "codex": {"intent": "APPROVE", "by_severity": {}}, "agy": {"intent": "APPROVE", "by_severity": {}},
             "head_sha": "abc"}
    entry.update(over)
    return entry


def _state(rounds):
    return {"current_pr": PR, "repo": "o/r", "review_kind": "design", "design_has_model": True, "max_rounds": 3,
            "rotate_after": 8, "only": None, "rounds": rounds, "deferred_nits": [], "carried_over": None,
            "final": None}


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(state_mod, "_fetch_check_runs", lambda repo, sha: [])
    return tmp_path


def _judge(state_mod, tmp_dir, state):
    path = tmp_dir / f"cross-review-pr{PR}-state.json"
    path.write_text(json.dumps(state))
    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))
    return e.value.code, json.loads(path.read_text())


def test_model_stage_approve_does_not_leave_the_loop(state_mod, tmp_dir, capsys):
    code, st = _judge(state_mod, tmp_dir, _state([_approved(1, stage="model")]))
    assert code == 2
    assert st["final"] is None and st["rounds"][-1]["verdict"] == "model_confirmed"
    assert "MODEL_CONFIRMED=1" in capsys.readouterr().out


def test_detail_stage_approve_leaves_the_loop(state_mod, tmp_dir):
    code, st = _judge(state_mod, tmp_dir, _state([_approved(1, stage="model", verdict="model_confirmed"),
                                                   _approved(2, stage="detail")]))
    assert code == 0 and st["final"] == "approved"


def _launch(tmp_path, state, round_no):
    worktree = tmp_path / "worktree"
    worktree.mkdir(exist_ok=True)
    tmp_dir = worktree / ".cross_review"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps({**state, "worktree_path": str(worktree)}))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "codex").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "codex").chmod(0o755)
    subprocess.run(["bash", str(SCRIPTS / "launch-reviewer.sh"), "codex", str(PR), str(round_no)],
                   env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                        "CROSS_REVIEW_TMP_DIR": str(tmp_dir)},
                   check=True, capture_output=True, text=True, timeout=10)
    return (tmp_dir / f"codex-review-pr{PR}-prompt.md").read_text(encoding="utf-8")


def test_launcher_passes_the_instructions_of_the_round_stage(tmp_path):
    base = {"current_pr": PR, "repo": "o/r", "review_kind": "design", "review_instructions": "今の観点",
            "review_instructions_by_stage": {"model": "モデルの観点", "detail": "詳細の観点"}}
    first = _launch(tmp_path, {**base, "rounds": [{"round": 1, "head_sha": "1" * 40, "stage": "model"}]}, 1)
    assert "モデルの観点" in first and "詳細の観点" not in first
    second = _launch(tmp_path, {**base, "rounds": [{"round": 1, "head_sha": "1" * 40, "stage": "model"},
                                                   {"round": 2, "head_sha": "2" * 40, "stage": "detail"}]}, 2)
    assert "詳細の観点" in second and "モデルの観点" not in second


def test_launcher_falls_back_without_stage_instructions(tmp_path):
    state = {"current_pr": PR, "repo": "o/r", "review_instructions": "今の観点",
             "rounds": [{"round": 1, "head_sha": "1" * 40}]}
    assert "今の観点" in _launch(tmp_path, state, 1)
