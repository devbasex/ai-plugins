"""投稿の event の指示が、状態ファイルからレビュープロンプトへ届くこと。

GitHub は自分の Pull Request への `APPROVE` と `REQUEST_CHANGES` を拒むため、
`init` が指示を組み立てて状態ファイルへ入れ、`launch-cli.sh` が雛形へ差し込む。
**他者の Pull Request では倒さない**経路は実機で一度も通っていないので、
組み立てから雛形までをここで固定する。

CLI そのものは起動しない。PATH へ何もしない実行ファイルを置き、
組み立て済みのプロンプトだけを読む。
"""
from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from conftest import make_state

LAUNCH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "launch-cli.sh"
RUNTIME = "codex"


def _prompt_for(tmp_path: pathlib.Path, **overrides: object) -> str:
    """`launch-cli.sh` に review のプロンプトを組み立てさせて中身を返す。"""
    state_path = make_state(tmp_path, **overrides)
    for name in ("work", RUNTIME):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)

    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / RUNTIME
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)

    subprocess.run(
        [str(LAUNCH), RUNTIME, "review", "130", "1"],
        env={
            **os.environ,
            "CROSS_REFACTORING_TMP_DIR": str(state_path.parent),
            "PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}",
        },
        check=True, capture_output=True, text=True,
    )
    return (state_path.parent / f"{RUNTIME}-review-r1-prompt.md").read_text(
        encoding="utf-8"
    )


@pytest.fixture
def note(refactor):
    return refactor._review_post_note


def test_the_note_for_someone_elses_pull_request_reaches_the_prompt(
    tmp_path, note
):
    """他者の Pull Request では判定をそのまま event に渡させること。"""
    text = _prompt_for(tmp_path, review_post_note=note(is_own_pr=False))
    assert "`APPROVE` または `REQUEST_CHANGES`" in text
    assert "COMMENT" not in text


def test_the_note_for_an_own_pull_request_reaches_the_prompt(tmp_path, note):
    text = _prompt_for(tmp_path, review_post_note=note(is_own_pr=True))
    assert "`-f event=COMMENT`" in text


def test_the_placeholder_is_always_expanded(tmp_path, note):
    """雛形の `$RF_POST_EVENT_NOTE` が生のまま残らないこと。"""
    text = _prompt_for(tmp_path, review_post_note=note(is_own_pr=False))
    assert "RF_POST_EVENT_NOTE" not in text


def test_a_state_without_the_note_falls_back(tmp_path):
    """指示が入る前の版で作った状態ファイルでも、投稿の指示が消えないこと。"""
    text = _prompt_for(tmp_path)
    assert "投稿の `-f event=` には判定をそのまま渡してください。" in text
