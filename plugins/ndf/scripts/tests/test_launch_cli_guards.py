"""`launch-cli.sh` が不正な入力では CLI を起動しないことを固定する。"""
from __future__ import annotations

import os
import pathlib
import subprocess
import time

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


def test_stale_stem_files_are_removed_before_a_new_launch(tmp_path):
    """起動時に stem に対応する古い残骸（pid・result・progress・stdout・stderr）を
    すべて消してから起動する。残っていると監視側が前の起動の結果を今回のものと読む。"""
    workdir = tmp_path / "work"
    workdir.mkdir()
    prompt = tmp_path / "prompt.md"
    prompt.write_text("prompt\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "codex"
    stub.write_text(
        '#!/bin/sh\necho "new stdout line"\necho "new stderr line" >&2\n',
        encoding="utf-8",
    )
    stub.chmod(0o755)

    stem = tmp_path / "out" / "codex"
    stem.parent.mkdir()
    (tmp_path / "out" / "codex.pid").write_text("99999\n", encoding="utf-8")
    (tmp_path / "out" / "codex-stdout.log").write_text("old-stdout\n", encoding="utf-8")
    (tmp_path / "out" / "codex-err.log").write_text("old-stderr\n", encoding="utf-8")
    (tmp_path / "out" / "codex-result.json").write_text('{"old":true}\n', encoding="utf-8")
    (tmp_path / "out" / "codex-progress.log").write_text("old-progress\n", encoding="utf-8")

    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(
        [str(LAUNCH), "codex", str(workdir), str(prompt), str(stem), "", "", "1"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    pid_file = tmp_path / "out" / "codex.pid"
    stdout_log = tmp_path / "out" / "codex-stdout.log"
    err_log = tmp_path / "out" / "codex-err.log"
    for _ in range(200):
        if stdout_log.read_text() and err_log.read_text():
            break
        time.sleep(0.01)

    # 古い pid は今回のもの（スタブを起動したプロセス）へ置き換わる。
    assert pid_file.read_text().strip() != "99999"
    # 古いログは残らず、今回の出力だけになる。
    assert stdout_log.read_text() == "new stdout line\n"
    assert err_log.read_text() == "new stderr line\n"
    # result と progress は消され、今回は作られない。
    assert not (tmp_path / "out" / "codex-result.json").exists()
    assert not (tmp_path / "out" / "codex-progress.log").exists()
