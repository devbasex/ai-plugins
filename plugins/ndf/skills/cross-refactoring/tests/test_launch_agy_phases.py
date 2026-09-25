"""各手順が `agy` を起動し、作業領域と実行時間の上限を渡すこと（#214 / #933 の I2）。

`agy` の実行時間の既定は 300 秒で、**どの手順の監視の上限よりも短い**。CLI が
先に打ち切ると結果ファイルが残らず、監視からは「起動したのに結果が残らなかった」
場合と区別が付かない。打ち切りの判断を監視の側へ一本化するため、手順ごとの
上限を起動時に明示する。

`start-phase` が予算から導いた上限（`phases.<手順>.timeout`）があれば、CLI の上限は
その秒 + 120 になる（I2）。無ければ上限の表（`lib/limits.py`）の値 + 120 である。

`agy` そのものは起動しない。PATH へ引数を書き出すだけの実行ファイルを置く。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import time

import pytest

from crossref_helpers import git, make_state_v2, read_state

LAUNCH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "launch-cli.sh"
RUNTIME = "agy"

# 引数は改行を含みうるため、NUL で区切って書き出す。
STUB = """#!/bin/sh
: > "$NDF_TEST_ARGS_FILE.tmp"
for a in "$@"; do printf '%s\\0' "$a" >> "$NDF_TEST_ARGS_FILE.tmp"; done
mv "$NDF_TEST_ARGS_FILE.tmp" "$NDF_TEST_ARGS_FILE"
"""

# `start-phase` を通らない起動の CLI の上限（#598 / #537 の AC36）。監視の上限（`lib/limits.py`）+ 120 秒。
CLI_TIMEOUT = {
    "propose": 1320, "plan": 1320,
    "add-tests": 3720, "implement": 3720, "fix": 3720, "final-fix": 3720,
}

# 手順ごとの作業ディレクトリ（`--add-dir` の先頭）と、生成されるプロンプトの接頭辞。
# 提案と改修計画は担当ごとの読み取り用の作業ディレクトリ、書き換える手順は work で行う。
WORKDIR_AND_STEM = {
    "propose": (RUNTIME, "agy-propose-rf130"),
    "plan": (RUNTIME, "agy-plan-rf130"),
    "add-tests": ("work", "agy-add-tests-rf130"),
    "implement": ("work", "agy-implement-rf130"),
    "fix": ("work", "agy-fix-rf130"),
    "final-fix": ("work", "agy-final-fix"),
}


def _state(tmp_path: pathlib.Path, **overrides) -> pathlib.Path:
    work = tmp_path / "work"
    for name in ("work", RUNTIME):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    return make_state_v2(tmp_path, work, runtimes=["codex", RUNTIME, "kiro"], **overrides)


def _run(state_path: pathlib.Path, tmp_path: pathlib.Path, *args: str) -> tuple[
        subprocess.CompletedProcess[str], pathlib.Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in (RUNTIME, "codex"):
        stub = bin_dir / name
        stub.write_text(STUB, encoding="utf-8")
        stub.chmod(0o755)
    args_file = tmp_path / "args.txt"
    result = subprocess.run(
        [str(LAUNCH), *args],
        env={
            **os.environ,
            "CROSS_REFACTORING_TMP_DIR": str(state_path.parent),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "NDF_TEST_ARGS_FILE": str(args_file),
        },
        capture_output=True, text=True,
    )
    return result, args_file


def _launch(tmp_path: pathlib.Path, phase: str, **overrides) -> tuple[list[str], pathlib.Path]:
    state_path = _state(tmp_path, **overrides)
    result, args_file = _run(state_path, tmp_path, RUNTIME, phase, "130")
    assert result.returncode == 0, result.stderr
    for _ in range(200):
        if args_file.is_file():
            break
        time.sleep(0.05)
    assert args_file.is_file(), f"{phase} で agy が起動されていない"
    return args_file.read_text(encoding="utf-8").split("\0")[:-1], state_path


@pytest.mark.parametrize("phase", sorted(CLI_TIMEOUT))
def test_every_phase_launches_agy(tmp_path, phase: str) -> None:
    args, _ = _launch(tmp_path, phase)
    assert args[-1].startswith("-p="), "プロンプトが `-p=` の値で渡っていない"


@pytest.mark.parametrize("phase", sorted(WORKDIR_AND_STEM))
def test_the_workspace_covers_the_workdir_and_the_result_directory(tmp_path, phase: str) -> None:
    """結果ファイルは全ランタイム共通の一時ディレクトリに置く。作業領域へ足す。"""
    args, state_path = _launch(tmp_path, phase)
    workdir_name, stem = WORKDIR_AND_STEM[phase]
    added = [args[i + 1] for i, a in enumerate(args) if a == "--add-dir"]
    assert added == [str(tmp_path / workdir_name), str(state_path.parent)]
    assert (state_path.parent / f"{stem}-prompt.md").is_file()


@pytest.mark.parametrize("phase", sorted(CLI_TIMEOUT))
def test_the_print_timeout_is_the_monitor_timeout_plus_120(tmp_path, phase: str) -> None:
    """`start-phase` を通らない起動は上限の表から導く（#598 / #537 の AC36）。"""
    args, _ = _launch(tmp_path, phase)
    assert args[args.index("--print-timeout") + 1] == f"{CLI_TIMEOUT[phase]}s"


@pytest.mark.parametrize("phase", sorted(CLI_TIMEOUT))
def test_the_budget_derived_timeout_wins_over_the_table(tmp_path, phase: str) -> None:
    """I2 I16: `start-phase` が残した `phases.<手順>.cli_timeout` の秒をそのまま渡す。"""
    args, _ = _launch(tmp_path, phase, phases={phase: {"timeout": 5000, "cli_timeout": 5090}})
    assert args[args.index("--print-timeout") + 1] == "5090s"


def test_a_timeout_of_another_phase_is_not_used(tmp_path) -> None:
    args, _ = _launch(tmp_path, "fix", phases={"implement": {"timeout": 5000, "cli_timeout": 5090}})
    assert args[args.index("--print-timeout") + 1] == f"{CLI_TIMEOUT['fix']}s"


def test_start_phase_records_the_timeout_that_the_launcher_reads(
        tmp_path, cmd_setup, env_tmp_dir) -> None:
    """I1 と I2 の受け渡し: `start-phase` が書いた上限を起動側がそのまま使う。"""
    import argparse

    state_path = _state(tmp_path, implementer=RUNTIME,
                        limits={"margin_seconds": 90, "implement_end_at": "2099-01-01T00:00:00+00:00"})
    env_tmp_dir(state_path)
    git("init", "-q", cwd=tmp_path / "work")
    git("-c", "user.email=t@e.st", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init",
        cwd=tmp_path / "work")
    cmd_setup.cmd_start_phase(argparse.Namespace(id=130, phase="implement"))
    record = read_state(state_path)["phases"]["implement"]
    assert record["cli_timeout"] == record["timeout"] + 90

    result, args_file = _run(state_path, tmp_path, RUNTIME, "implement", "130")
    assert result.returncode == 0, result.stderr
    for _ in range(200):
        if args_file.is_file():
            break
        time.sleep(0.05)
    args = args_file.read_text(encoding="utf-8").split("\0")[:-1]
    assert args[args.index("--print-timeout") + 1] == f"{record['cli_timeout']}s"


def test_unknown_phase_stops_before_writing_a_prompt(tmp_path) -> None:
    """ラウンド制の手順（`apply` / `propose-tests`）は無くなった。起動しない。"""
    state_path = _state(tmp_path)
    for phase in ("apply", "propose-tests"):
        result, args_file = _run(state_path, tmp_path, RUNTIME, phase, "130")
        assert result.returncode != 0
        assert not args_file.exists()
        assert not list(state_path.parent.glob(f"*{phase}*-prompt.md"))


def test_unknown_runtime_stops_before_writing_a_prompt(tmp_path) -> None:
    state_path = _state(tmp_path)
    result, _ = _run(state_path, tmp_path, "unknown", "propose", "130")
    assert result.returncode != 0
    assert not (state_path.parent / "unknown-propose-rf130-prompt.md").exists()
