"""共通の公開入口から codex / claude を起動する現状固定テスト。"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time

import pytest


LAUNCH = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "lib" / "launch-cli.sh"
STUB = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
pathlib.Path(os.environ["NDF_TEST_RECORD"]).write_text(json.dumps({
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "stdin": sys.stdin.read(),
}), encoding="utf-8")
'''
PROMPT = "1 行目の依頼\n2 行目の条件\n"


def _launch(tmp_path: pathlib.Path, runtime: str) -> tuple[dict[str, object], pathlib.Path]:
    workdir = tmp_path / "worktree"
    workdir.mkdir()
    prompt = tmp_path / "prompt.md"
    prompt.write_text(PROMPT, encoding="utf-8")
    stem = tmp_path / "artifacts" / f"{runtime}-review-pr683"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / runtime
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)
    record = tmp_path / f"{runtime}.json"

    subprocess.run(
        [str(LAUNCH), runtime, str(workdir), str(prompt), str(stem), "test-model"],
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "NDF_TEST_RECORD": str(record),
        },
        check=True,
        capture_output=True,
        text=True,
    )
    for _ in range(200):
        if record.is_file() and stem.with_suffix(".pid").is_file():
            break
        time.sleep(0.01)
    assert record.is_file(), f"{runtime} が起動されていない"
    assert stem.with_suffix(".pid").is_file()
    return json.loads(record.read_text(encoding="utf-8")), stem


@pytest.mark.parametrize("runtime", ["codex", "claude"])
def test_public_entry_passes_context_and_keeps_artifacts_under_stem(tmp_path, runtime):
    recorded, stem = _launch(tmp_path, runtime)

    assert recorded["cwd"] == str(tmp_path / "worktree")
    assert recorded["stdin"] == PROMPT
    argv = recorded["argv"]
    assert argv[argv.index("--model") + 1] == "test-model"
    if runtime == "codex":
        assert argv[argv.index("-C") + 1] == str(tmp_path / "worktree")
    else:
        assert argv[0] == "-p"
    assert stem.parent == tmp_path / "artifacts"
    assert stem.with_suffix(".pid").parent == stem.parent
    assert pathlib.Path(f"{stem}-stdout.log").parent == stem.parent
    assert pathlib.Path(f"{stem}-err.log").parent == stem.parent
