"""起動が工程名から CLI の上限を導く（#598 / #537 の AC34 / AC35）。

agy の `--print-timeout` は、同じ環境変数で解決した監視の上限 + 120 秒になる。
監視の上限だけを延ばしても CLI が先に打ち切らない（設計の決定 12）。

`agy` そのものは起動しない。PATH へ引数を書き出すだけの実行ファイルを置き、
記録された引数を読む。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time

import pytest

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
LAUNCH = HERE.parents[2] / "scripts" / "lib" / "launch-cli.sh"
PR = 7101

STUB = """#!/bin/sh
: > "$NDF_TEST_ARGS_FILE.tmp"
for a in "$@"; do printf '%s\\0' "$a" >> "$NDF_TEST_ARGS_FILE.tmp"; done
mv "$NDF_TEST_ARGS_FILE.tmp" "$NDF_TEST_ARGS_FILE"
"""


def _env(tmp_path: pathlib.Path, **over: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "agy"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)
    env = dict(os.environ)
    env.pop("NDF_CRITIQUE_PRINT_TIMEOUT", None)
    env.update({
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "NDF_TEST_ARGS_FILE": str(tmp_path / "args.txt"),
        **over,
    })
    return env


def _recorded(tmp_path: pathlib.Path) -> list[str]:
    args_file = tmp_path / "args.txt"
    for _ in range(200):
        if args_file.is_file():
            break
        time.sleep(0.05)
    assert args_file.is_file(), "agy が起動されていない"
    return args_file.read_text(encoding="utf-8").split("\0")[:-1]


def _print_timeout(args: list[str]) -> str:
    return args[args.index("--print-timeout") + 1]


def _launch_lib(tmp_path: pathlib.Path, seventh: str | None, **env: str) -> subprocess.CompletedProcess:
    workdir = tmp_path / "worktree"
    workdir.mkdir(exist_ok=True)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("レビューしてください\n", encoding="utf-8")
    stem = tmp_path / "out" / "agy-review-pr1"
    extra = [] if seventh is None else ["", "", seventh]
    return subprocess.run(
        [str(LAUNCH), "agy", str(workdir), str(prompt), str(stem), *extra],
        env=_env(tmp_path, **env), capture_output=True, text=True,
    )


# ---------- AC34: 共通層の第 7 引数 ----------

@pytest.mark.parametrize(("phase", "expected"), [
    ("review", "1320s"), ("critique", "1320s"), ("propose", "1320s"),
    ("judge-test-changes", "1320s"), ("apply", "3720s"), ("fix", "3720s"),
    ("final-fix", "3720s"),
])
def test_a_phase_name_derives_the_cli_timeout(tmp_path, phase: str, expected: str) -> None:
    r = _launch_lib(tmp_path, phase)
    assert r.returncode == 0, r.stderr
    assert _print_timeout(_recorded(tmp_path)) == expected


def test_the_exported_monitor_timeout_moves_the_cli_timeout(tmp_path) -> None:
    """要求の文書の例: `MONITOR_TIMEOUT=1800` で `review` を起動すると 1920 秒。"""
    r = _launch_lib(tmp_path, "review", MONITOR_TIMEOUT="1800")
    assert r.returncode == 0, r.stderr
    assert _print_timeout(_recorded(tmp_path)) == "1920s"


def test_the_per_agent_monitor_timeout_moves_the_cli_timeout(tmp_path) -> None:
    r = _launch_lib(tmp_path, "critique", MONITOR_TIMEOUT="1800", MONITOR_TIMEOUT_AGY="1500")
    assert r.returncode == 0, r.stderr
    assert _print_timeout(_recorded(tmp_path)) == "1620s"


def test_a_number_is_used_as_seconds(tmp_path) -> None:
    r = _launch_lib(tmp_path, "900", MONITOR_TIMEOUT="1800")
    assert r.returncode == 0, r.stderr
    assert _print_timeout(_recorded(tmp_path)) == "900s"


def test_an_empty_value_takes_the_apply_value(tmp_path) -> None:
    r = _launch_lib(tmp_path, "")
    assert r.returncode == 0, r.stderr
    assert _print_timeout(_recorded(tmp_path)) == "3720s"


def test_an_omitted_value_takes_the_apply_value(tmp_path) -> None:
    r = _launch_lib(tmp_path, None)
    assert r.returncode == 0, r.stderr
    assert _print_timeout(_recorded(tmp_path)) == "3720s"


@pytest.mark.parametrize("name", ["propose-tests", "reviews"])
def test_an_unknown_phase_name_exits_1_without_launching(tmp_path, name: str) -> None:
    r = _launch_lib(tmp_path, name)
    assert r.returncode == 1
    assert name in r.stderr, "案内が渡された工程名を出していない"
    time.sleep(0.3)
    assert not (tmp_path / "args.txt").exists()
    assert not (tmp_path / "out" / "agy-review-pr1.pid").exists()


# ---------- AC35: レビューと反証の起動 ----------

def _review_state(tmp_path: pathlib.Path) -> None:
    state = {
        "current_pr": PR, "repo": "o/r", "worktree_path": str(tmp_path),
        "rounds": [{"round": 1, "head_sha": "a" * 40}],
        "review_findings": [{
            "finding_id": "codex-r1-0", "agent": "codex", "round": 1, "path": "a.py",
            "line": 1, "severity": "major", "body": "b", "evidence": "e",
            "falsification": "f", "suggested_check": "true",
        }],
    }
    (tmp_path / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def test_launch_reviewer_passes_the_review_phase(tmp_path) -> None:
    _review_state(tmp_path)
    r = subprocess.run(
        ["bash", str(SCRIPTS / "launch-reviewer.sh"), "agy", str(PR), "1"],
        env=_env(tmp_path, CROSS_REVIEW_TMP_DIR=str(tmp_path), MONITOR_TIMEOUT="1800"),
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert _print_timeout(_recorded(tmp_path)) == "1920s"


def _critique(tmp_path: pathlib.Path, **env: str) -> subprocess.CompletedProcess:
    _review_state(tmp_path)
    return subprocess.run(
        ["bash", str(SCRIPTS / "critique.sh"), "agy", str(PR), "1"],
        env=_env(tmp_path, CROSS_REVIEW_TMP_DIR=str(tmp_path), **env),
        capture_output=True, text=True,
    )


def test_critique_passes_the_critique_phase(tmp_path) -> None:
    r = _critique(tmp_path)
    assert r.returncode == 0, r.stderr
    assert _print_timeout(_recorded(tmp_path)) == "1320s"


def test_a_short_critique_print_timeout_is_raised_to_the_derived_value(tmp_path) -> None:
    r = _critique(tmp_path, NDF_CRITIQUE_PRINT_TIMEOUT="600")
    assert r.returncode == 0, r.stderr
    assert _print_timeout(_recorded(tmp_path)) == "1320s"
    lines = [line for line in r.stderr.splitlines() if "NDF_CRITIQUE_PRINT_TIMEOUT" in line]
    assert len(lines) == 1 and "600" in lines[0] and "1320" in lines[0]


def test_a_long_critique_print_timeout_is_kept(tmp_path) -> None:
    r = _critique(tmp_path, NDF_CRITIQUE_PRINT_TIMEOUT="2000")
    assert r.returncode == 0, r.stderr
    assert _print_timeout(_recorded(tmp_path)) == "2000s"
    assert "NDF_CRITIQUE_PRINT_TIMEOUT" not in r.stderr


def test_a_non_numeric_critique_print_timeout_takes_the_derived_value(tmp_path) -> None:
    """工程名として読まれて起動が失敗しないよう、導出値へ戻す。"""
    r = _critique(tmp_path, NDF_CRITIQUE_PRINT_TIMEOUT="30m")
    assert r.returncode == 0, r.stderr
    assert _print_timeout(_recorded(tmp_path)) == "1320s"
    assert "NDF_CRITIQUE_PRINT_TIMEOUT" in r.stderr


def test_the_critique_print_timeout_follows_the_monitor_timeout(tmp_path) -> None:
    """引き上げの基準も同じ環境変数で解決した値である。"""
    r = _critique(tmp_path, NDF_CRITIQUE_PRINT_TIMEOUT="1800", MONITOR_TIMEOUT="1800")
    assert r.returncode == 0, r.stderr
    assert _print_timeout(_recorded(tmp_path)) == "1920s"
