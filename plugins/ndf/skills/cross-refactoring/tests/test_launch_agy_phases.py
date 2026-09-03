"""4 つのフェーズが `agy` を起動し、作業領域と実行時間の上限を渡すこと（#214）。

`agy` の実行時間の既定は 300 秒で、**どのフェーズの監視の上限よりも短い**。CLI が
先に打ち切ると結果ファイルが残らず、監視からは「起動したのに結果が残らなかった」
場合と区別が付かない。打ち切りの判断を監視の側へ一本化するため、フェーズごとの
上限を起動時に明示する。

`agy` そのものは起動しない。PATH へ引数を書き出すだけの実行ファイルを置く。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import time

import pytest

from crossref_helpers import make_state

LAUNCH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "launch-cli.sh"
RUNTIME = "agy"

# 引数は改行を含みうるため、NUL で区切って書き出す。
STUB = """#!/bin/sh
: > "$NDF_TEST_ARGS_FILE.tmp"
for a in "$@"; do printf '%s\\0' "$a" >> "$NDF_TEST_ARGS_FILE.tmp"; done
mv "$NDF_TEST_ARGS_FILE.tmp" "$NDF_TEST_ARGS_FILE"
"""

# フェーズごとの監視の上限（`SKILL.md` の `--timeout`）。起動時の上限はこれ以上にする。
MONITOR_TIMEOUT = {"propose": 900, "apply": 3600, "review": 900, "fix": 3600}


def _launch(tmp_path: pathlib.Path, phase: str) -> tuple[list[str], pathlib.Path]:
    state_path = make_state(tmp_path, runtimes=["codex", RUNTIME, "kiro"])
    for name in ("work", RUNTIME):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / RUNTIME
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)

    args_file = tmp_path / "args.txt"
    subprocess.run(
        [str(LAUNCH), RUNTIME, phase, "130", "1"],
        env={
            **os.environ,
            "CROSS_REFACTORING_TMP_DIR": str(state_path.parent),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "NDF_TEST_ARGS_FILE": str(args_file),
        },
        check=True, capture_output=True, text=True,
    )
    for _ in range(200):
        if args_file.is_file():
            break
        time.sleep(0.05)
    assert args_file.is_file(), f"{phase} で agy が起動されていない"
    return args_file.read_text(encoding="utf-8").split("\0")[:-1], state_path


@pytest.mark.parametrize("phase", sorted(MONITOR_TIMEOUT))
def test_every_phase_launches_agy(tmp_path, phase: str) -> None:
    args, _ = _launch(tmp_path, phase)
    assert args[-1].startswith("-p="), "プロンプトが `-p=` の値で渡っていない"


@pytest.mark.parametrize("phase", sorted(MONITOR_TIMEOUT))
def test_the_workspace_covers_the_workdir_and_the_result_directory(
    tmp_path, phase: str
) -> None:
    """結果ファイルは全ランタイム共通の一時ディレクトリに置く。作業領域へ足す。"""
    args, state_path = _launch(tmp_path, phase)
    added = [args[i + 1] for i, a in enumerate(args) if a == "--add-dir"]
    workdir = tmp_path / ("work" if phase in ("apply", "fix") else RUNTIME)
    assert added == [str(workdir), str(state_path.parent)]


@pytest.mark.parametrize("phase", sorted(MONITOR_TIMEOUT))
def test_the_print_timeout_covers_the_monitor_timeout(tmp_path, phase: str) -> None:
    args, _ = _launch(tmp_path, phase)
    value = int(args[args.index("--print-timeout") + 1].rstrip("s"))
    assert value >= MONITOR_TIMEOUT[phase], f"{phase} の上限が監視より短い"
