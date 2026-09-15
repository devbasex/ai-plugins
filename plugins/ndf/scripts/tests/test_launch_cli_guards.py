"""`launch-cli.sh` が不正な入力では CLI を起動しないことを固定する。"""
from __future__ import annotations

import os
import pathlib
import subprocess

import pytest


LAUNCH = pathlib.Path(__file__).resolve().parents[1] / "lib" / "launch-cli.sh"


def _env_with_cli_stub(tmp_path: pathlib.Path) -> tuple[dict[str, str], pathlib.Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "cli-started"
    stub = bin_dir / "codex"
    stub.write_text('#!/bin/sh\ntouch "$NDF_TEST_CLI_MARKER"\n', encoding="utf-8")
    stub.chmod(0o755)
    return (
        {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "NDF_TEST_CLI_MARKER": str(marker),
        },
        marker,
    )


def test_missing_workdir_is_rejected_without_launching_cli(tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("prompt\n", encoding="utf-8")
    env, marker = _env_with_cli_stub(tmp_path)

    result = subprocess.run(
        [str(LAUNCH), "codex", str(tmp_path / "missing"), str(prompt), str(tmp_path / "out")],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "作業ディレクトリがありません" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("prompt_kind", ["empty", "missing"])
def test_empty_or_missing_prompt_is_rejected(tmp_path, prompt_kind):
    workdir = tmp_path / "work"
    workdir.mkdir()
    prompt = tmp_path / "prompt.md"
    if prompt_kind == "empty":
        prompt.touch()
    env, marker = _env_with_cli_stub(tmp_path)

    result = subprocess.run(
        [str(LAUNCH), "codex", str(workdir), str(prompt), str(tmp_path / "out")],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "プロンプトが空です" in result.stderr
    assert not marker.exists()


def test_unknown_runtime_is_rejected_without_launching_cli(tmp_path):
    workdir = tmp_path / "work"
    workdir.mkdir()
    prompt = tmp_path / "prompt.md"
    prompt.write_text("prompt\n", encoding="utf-8")
    env, marker = _env_with_cli_stub(tmp_path)

    result = subprocess.run(
        [str(LAUNCH), "bogus", str(workdir), str(prompt), str(tmp_path / "out"), "", "", "1"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "未知のランタイムです" in result.stderr
    assert not marker.exists()
