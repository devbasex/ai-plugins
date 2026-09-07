"""構造改善の提案プロンプトが採用上限を伝えること（C2）。

**落ちる提案を、提案の時点で防ぐ。** 上限を伝えないと 3 者が上限を超える件数を
出し、超えた分は見送りとして記録される。以後は「対象外」になるため、提案の労力が
そのまま無駄になる。

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
    state_path = make_state(tmp_path, vocabulary=refactor.vocabulary(),
                            max_items_per_round=4)
    for name in ("work", RUNTIME):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)

    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / RUNTIME
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)

    subprocess.run(
        [str(LAUNCH), RUNTIME, "propose", "130", "1"],
        env={
            **os.environ,
            "CROSS_REFACTORING_TMP_DIR": str(state_path.parent),
            "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        },
        check=True, capture_output=True, text=True,
    )
    return (state_path.parent / f"{RUNTIME}-propose-rf130-r1-prompt.md").read_text(
        encoding="utf-8"
    )


def test_the_adoption_cap_is_stated_with_its_number(prompt):
    """C2 — 3 者の合計で何件までが採用されるかを、数字で伝える。"""
    assert "採用上限" in prompt
    assert "4 件までが採用" in prompt


def test_more_is_not_better_is_stated(prompt):
    """**多く出すより採れる提案を出す。** 上限を伝えるだけでは行動が変わらない。"""
    assert "採れる提案だけを出す" in prompt


def test_the_vocabulary_is_still_listed(prompt):
    """語彙の列挙は外さない。**列挙しないと全件が語彙外で降格する**（実測）。"""
    assert "`long_method`" in prompt and "`extract_method`" in prompt
