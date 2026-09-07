"""テスト整備ラウンドの提案プロンプトが組み立てられること（C1）。

**新しい語彙は作らない**（決定 9）。`case` と `level` の許容値を状態ファイルから
そのまま列挙し、手順書は既存の 3 本の参照を名指しで読ませる。

CLI そのものは起動しない。PATH へ何もしない実行ファイルを置き、組み立て済みの
プロンプトだけを読む。
"""
from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from crossref_helpers import make_state

LAUNCH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "launch-cli.sh"
RUNTIME = "codex"


@pytest.fixture
def prompt(refactor, tmp_path):
    state_path = make_state(tmp_path, test_vocabulary=refactor.test_vocabulary())
    for name in ("work", RUNTIME):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)

    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / RUNTIME
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)

    subprocess.run(
        [str(LAUNCH), RUNTIME, "propose-tests", "130", "1"],
        env={
            **os.environ,
            "CROSS_REFACTORING_TMP_DIR": str(state_path.parent),
            "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        },
        check=True, capture_output=True, text=True,
    )
    # 結果ファイルの名前は提案ラウンドと同じにする。ラウンド番号は通しなので
    # 衝突せず、監視の雛形もそのまま使える。
    return (state_path.parent / f"{RUNTIME}-propose-rf130-r1-prompt.md").read_text(
        encoding="utf-8"
    )


def test_the_vocabulary_values_are_listed(prompt):
    """許容値をそのまま列挙する。**手順書を読ませるだけでは足りない**（実測）。"""
    for value in ("`normal`", "`branch`", "`boundary`", "`error`"):
        assert value in prompt
    for value in ("`unit`", "`integration`", "`contract`", "`e2e`"):
        assert value in prompt


def test_the_three_references_are_named(prompt):
    """語彙の出所と、採らない提案の基準を名指しで読ませる（決定 9）。"""
    assert "characterization-tests.md" in prompt
    assert "testing-levels.md" in prompt
    assert "test-quality.md" in prompt


def test_the_dedupe_key_is_stated(prompt):
    """3 者が同じ経路を挙げる。**鍵が伝わらないと重複排除が効かない。**"""
    assert "target" in prompt and "case" in prompt


def test_the_scope_and_the_adoption_cap_are_passed(prompt):
    assert "src" in prompt
    assert "構造は変えない" in prompt
