"""`review` フェーズが受け口から消えていることを確かめる。

#436 で Step 5 の判定はテストへ置き換わり、レビューは Step 7 の `cross-review` が担う。
受け口が残っていると `--phase review` が通り、結果ファイルを待つ側が止まる。
"""
from __future__ import annotations

import os
import pathlib
import subprocess

from crossref_helpers import make_state

LAUNCH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "launch-cli.sh"


def test_the_review_phase_is_rejected(tmp_path: pathlib.Path) -> None:
    """`review` は未知のフェーズとして弾かれる。

    状態ファイルの検査が先に走るため、それを用意したうえで渡す。用意しないと
    「状態ファイルがありません」で落ち、フェーズの判定まで届かない。
    """
    state_path = make_state(tmp_path)
    proc = subprocess.run(
        [str(LAUNCH), "codex", "review", "130", "1"],
        env={**os.environ, "CROSS_REFACTORING_TMP_DIR": str(state_path.parent)},
        capture_output=True, text=True,
    )
    assert proc.returncode != 0
    assert "未知のフェーズです" in proc.stderr


def test_the_launcher_does_not_name_the_removed_prompt() -> None:
    """消したプロンプトの名前を受け口が参照しない。"""
    text = LAUNCH.read_text(encoding="utf-8")
    assert "review.md" not in text
    assert "RF_POST_EVENT_NOTE" not in text


def test_the_review_prompt_is_gone() -> None:
    """レビューの指示そのものが残っていない。"""
    prompts = LAUNCH.resolve().parents[1] / "prompts"
    assert not (prompts / "review.md").exists()
