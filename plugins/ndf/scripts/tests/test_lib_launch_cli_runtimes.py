"""`launch-cli.sh` のランタイム別引数を固定する。"""
from __future__ import annotations

import os
import pathlib
import subprocess
import time


LAUNCH = pathlib.Path(__file__).resolve().parents[1] / "lib" / "launch-cli.sh"
STUB = """#!/bin/sh
: > "$NDF_TEST_ARGS_FILE.tmp"
for arg in "$@"; do printf '%s\\0' "$arg" >> "$NDF_TEST_ARGS_FILE.tmp"; done
mv "$NDF_TEST_ARGS_FILE.tmp" "$NDF_TEST_ARGS_FILE"
"""


def _launch(tmp_path: pathlib.Path, runtime: str, *, allowed_tools: str | None = None) -> list[str]:
    workdir = tmp_path / "work"
    workdir.mkdir()
    prompt = tmp_path / "prompt.md"
    prompt.write_text("現状を確認してください\n", encoding="utf-8")
    stem = tmp_path / "output" / runtime
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = "kiro-cli" if runtime == "kiro" else runtime
    stub = bin_dir / executable
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)
    args_file = tmp_path / "args.bin"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "NDF_TEST_ARGS_FILE": str(args_file),
    }
    if allowed_tools is not None:
        env["NDF_CLAUDE_ALLOWED_TOOLS"] = allowed_tools

    subprocess.run(
        [str(LAUNCH), runtime, str(workdir), str(prompt), str(stem), "test-model"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    for _ in range(200):
        if args_file.is_file():
            break
        time.sleep(0.01)
    assert args_file.is_file(), f"{runtime} が起動されていない"
    return args_file.read_bytes().decode().split("\0")[:-1]


def test_codex_launch_arguments(tmp_path):
    args = _launch(tmp_path, "codex")

    assert args[:2] == ["exec", "--dangerously-bypass-approvals-and-sandbox"]
    assert args[args.index("--config") + 1] == "reasoning.effort=medium"
    assert args[args.index("-C") + 1] == str(tmp_path / "work")
    assert args[args.index("--model") + 1] == "test-model"


def test_claude_launch_arguments_and_allowed_tools_override(tmp_path):
    args = _launch(tmp_path, "claude", allowed_tools="Read,Grep")

    assert args[0] == "-p"
    assert args[args.index("--permission-mode") + 1] == "acceptEdits"
    assert args[args.index("--allowed-tools") + 1] == "Read,Grep"
    assert args[args.index("--output-format") + 1] == "json"
    assert args[args.index("--model") + 1] == "test-model"


def test_kiro_launch_arguments(tmp_path):
    args = _launch(tmp_path, "kiro")

    assert args[:3] == ["chat", "--no-interactive", "--trust-all-tools"]
    assert args[args.index("--model") + 1] == "test-model"
