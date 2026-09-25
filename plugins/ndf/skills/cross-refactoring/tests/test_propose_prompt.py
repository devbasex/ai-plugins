"""状態の値が各手順のプロンプトへ渡ること（#933 の AC5 AC12、実装計画 Task 8）。

- 提案: 語彙（スメル・手法・重要度）と**観点**（`vocabulary.viewpoints`）の値を列挙する。
  列挙しないと全件が語彙外で降格する（実測）。想定最大時間も渡す
- 改修計画: 候補の全件（鍵 `path#symbol#smell`）
- テストの追加と実装: 項目ごとの締め切り（AC12）と、実装は項目の語の並び

**文言は照合しない**（AGENTS.md）。渡った値と語彙の値の列挙だけを見る。

CLI そのものは起動しない。PATH へ何もしない実行ファイルを置き、組み立て済みの
プロンプトだけを読む。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

from crossref_helpers import make_state_v2

LAUNCH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "launch-cli.sh"
RUNTIME = "codex"
DEADLINE = "2026-09-24T10:41:00+00:00"
TEST_DEADLINE = "2026-09-24T10:20:00+00:00"


def _items() -> list[dict]:
    base = {"path": "src/a.py", "smell": "long_method", "technique": "extract_method",
            "rationale": "r", "plan": "p", "fix_count": 0}
    return [
        {**base, "id": "I-017", "rank": 1, "symbol": "Foo", "tests": ["tests/test_a.py"],
         "test_targets": ["tests/test_a.py"], "command": ["pytest", "-q", "tests/test_a.py"],
         "estimate": {"test": 2.7, "implement": 1.3, "verify": 0.2},
         "start_deadline": DEADLINE, "test_start_deadline": TEST_DEADLINE, "status": "planned"},
        {**base, "id": "I-042", "rank": 2, "symbol": "Bar", "tests": [],
         "test_targets": ["tests/test_b.py"], "command": ["pytest", "-q", "tests/test_b.py"],
         "estimate": {"test": 0.0, "implement": 1.3, "verify": 0.2},
         "start_deadline": "2026-09-24T10:43:00+00:00", "test_start_deadline": None,
         "status": "planned"},
    ]


@pytest.fixture
def render(refactor, tmp_path):
    vocabulary = sys.modules["refactor_lib.vocabulary"].vocabulary()
    work = tmp_path / "work"
    for name in ("work", RUNTIME):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    state_path = make_state_v2(
        tmp_path, work, runtimes=[RUNTIME, "kiro"], vocabulary=vocabulary, budget_minutes=45,
        candidates=[{"path": "src/a.py", "symbol": "Foo", "smell": "long_method",
                     "technique": "extract_method", "severity": "major", "rationale": "r",
                     "plan": "p", "proposed_by": ["codex", "kiro"]}],
        items=_items())
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / RUNTIME
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)

    def _render(phase: str) -> str:
        subprocess.run(
            [str(LAUNCH), RUNTIME, phase, "130"],
            env={**os.environ,
                 "CROSS_REFACTORING_TMP_DIR": str(state_path.parent),
                 "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"},
            check=True, capture_output=True, text=True,
        )
        return (state_path.parent / f"{RUNTIME}-{phase}-rf130-prompt.md").read_text(
            encoding="utf-8")

    return _render, vocabulary


def test_propose_lists_every_viewpoint_and_the_vocabulary(render):
    """AC5: 観点の値を全件列挙する。語彙の列挙も外さない。"""
    _render, vocabulary = render
    text = _render("propose")
    assert vocabulary["viewpoints"], "観点の語彙が空"
    for key in vocabulary["viewpoints"]:
        assert f"`{key}`" in text
    for key in list(vocabulary["smells"]) + list(vocabulary["techniques"]):
        assert f"`{key}`" in text
    for severity in vocabulary["severities"]:
        assert f"`{severity}`" in text
    assert "45" in text                  # 想定最大時間
    assert "$RF_" not in text


def test_plan_receives_every_candidate_by_its_key(render):
    _render, _ = render
    text = _render("plan")
    assert "src/a.py#Foo#long_method" in text
    assert "$RF_" not in text


def test_add_tests_passes_only_items_with_tests_and_their_test_deadline(render):
    """AC12: テストの追加の締め切り（`test_start_deadline`）を項目ごとに渡す。"""
    _render, _ = render
    text = _render("add-tests")
    assert "I-017" in text and TEST_DEADLINE in text
    assert "I-042" not in text           # 足すテストの無い項目は渡さない
    assert "$RF_" not in text


def test_implement_passes_each_item_with_its_deadline_and_command(render):
    """AC12: 実装の締め切り（`start_deadline`）と、検証に使う語の並びを項目ごとに渡す。"""
    _render, _ = render
    text = _render("implement")
    assert "I-017" in text and DEADLINE in text
    assert "I-042" in text and "2026-09-24T10:43:00+00:00" in text
    assert "pytest -q tests/test_a.py" in text and "pytest -q tests/test_b.py" in text
    assert "$RF_" not in text


def test_fix_passes_only_the_failing_items(render, tmp_path):
    _render, _ = render
    text = _render("fix")
    assert "I-017" not in text and "I-042" not in text   # 落ちた項目が無い
    assert "$RF_" not in text
