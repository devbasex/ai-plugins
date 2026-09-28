"""到達の確認（#1337 の受け入れ条件 7・I9・I10）。コンテナで走るテストを worktree に対して走らせ、届かなければ止まる。

偽のコンテナ実行系（`docker`）は `compose exec -T <サービス> <コマンド>` を、`NDF_WORKTREE` が渡されていればその
worktree で、渡されていないか `FAKE_SEES_MAIN` があればメインディレクトリで走らせる。テスト環境の上書き（worktree を
マウントする compose の定義）を、環境変数の受け渡しで真似る。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from worktree_helpers import SCRIPTS_DIR, git, write_declaration

TESTENV = SCRIPTS_DIR / "worktree-testenv.sh"
REACH = SCRIPTS_DIR / "lib" / "container_reach.py"
TEST_RUN = SCRIPTS_DIR / "test-run.py"

FAKE_DOCKER = """#!/bin/sh
[ "$1" = compose ] && [ "$2" = exec ] || { echo "unsupported: $*" >&2; exit 1; }
shift 2
[ "$1" = -T ] && shift
svc=$1; shift
if [ -n "${FAKE_DOWN:-}" ]; then echo "service \\"$svc\\" is not running" >&2; exit 1; fi
if [ -n "${FAKE_SEES_MAIN:-}" ] || [ -z "${NDF_WORKTREE:-}" ]; then root=$FAKE_MAIN; else root=$NDF_WORKTREE; fi
cd "$root" || exit 1
exec "$@"
"""

# worktree にだけ FAIL を置くと落ちるテスト。走ったことを RAN_LOG に残す
RUN = "docker compose exec -T app sh -c 'echo ran >> \"$RAN_LOG\"; test ! -f FAIL'"


@pytest.fixture()
def env(tmp_path: Path, main_repo: Path) -> dict:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    (main_repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    git(main_repo, "add", ".gitignore")
    git(main_repo, "commit", "-q", "-m", "ignore")
    return {
        **os.environ,
        "LC_ALL": "C.UTF-8",
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "FAKE_MAIN": str(main_repo),
        "RAN_LOG": str(tmp_path / "ran.log"),
    }


def declare(main: Path) -> None:
    write_declaration(
        main,
        json.dumps({"version": 1, "testenv": {"test_kinds": {"unit": {"run": RUN, "service": "app"}}}}),
    )


def run_testenv(args: list[str], cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(TESTENV), *args], cwd=str(cwd), env=env, capture_output=True, text=True)


def ran(env: dict) -> bool:
    return Path(env["RAN_LOG"]).exists()


# --- worktree-testenv.sh test ------------------------------------------------------


def test_reachable_container_runs_the_worktree_change(main_repo: Path, worktree: Path, env: dict) -> None:
    declare(main_repo)
    (worktree / "FAIL").write_text("x\n", encoding="utf-8")

    r = run_testenv(["test", str(worktree), "--kind", "unit"], main_repo, env)

    assert r.returncode == 1, r.stderr
    assert ran(env)


def test_reachable_container_passes_without_the_change(main_repo: Path, worktree: Path, env: dict) -> None:
    declare(main_repo)
    r = run_testenv(["test", str(worktree), "--kind", "unit"], main_repo, env)
    assert r.returncode == 0, r.stderr


def test_container_seeing_main_does_not_run(main_repo: Path, worktree: Path, env: dict) -> None:
    declare(main_repo)
    (worktree / "FAIL").write_text("x\n", encoding="utf-8")

    r = run_testenv(["test", str(worktree), "--kind", "unit"], main_repo, {**env, "FAKE_SEES_MAIN": "1"})

    assert r.returncode == 1
    assert "この worktree ではない" in r.stderr
    assert not ran(env)


def test_stopped_container_does_not_run(main_repo: Path, worktree: Path, env: dict) -> None:
    declare(main_repo)
    r = run_testenv(["test", str(worktree), "--kind", "unit"], main_repo, {**env, "FAKE_DOWN": "1"})
    assert r.returncode == 1
    assert "動いていない" in r.stderr and "worktree-testenv.sh up" in r.stderr
    assert not ran(env)


def test_missing_container_runtime_returns_2(main_repo: Path, worktree: Path, env: dict) -> None:
    declare(main_repo)
    r = run_testenv(["test", str(worktree), "--kind", "unit"], main_repo, {**env, "WT_DOCKER_COMMAND": "/nonexistent/docker"})
    assert r.returncode == 2
    assert not ran(env)


# --- compose-env -----------------------------------------------------------------


def test_compose_env_prints_the_assignment(main_repo: Path, worktree: Path, env: dict) -> None:
    write_declaration(
        main_repo,
        json.dumps(
            {
                "version": 1,
                "localenv": {"kind": "compose", "compose_files": ["compose.yml"]},
                "testenv": {"port_band": [20000, 29999], "port_roles": {"http": 0}},
            }
        ),
    )
    assert run_testenv(["compose-env", str(worktree)], main_repo, env).stdout == ""

    assignment = json.loads(run_testenv(["env", str(worktree)], main_repo, env).stdout)
    out = run_testenv(["compose-env", str(worktree)], main_repo, env).stdout.splitlines()

    values = dict(line.split("=", 1) for line in out)
    assert values["NDF_WORKTREE"] == str(worktree.resolve())
    assert values["NDF_PORT_HTTP"] == "20000"
    assert values["COMPOSE_PROJECT_NAME"] == assignment["environment"]
    assert values["COMPOSE_FILE"] == str(worktree.resolve() / "compose.yml")


# --- 探りの印（I10） -------------------------------------------------------------


@pytest.mark.parametrize("extra", [{}, {"FAKE_SEES_MAIN": "1"}, {"FAKE_DOWN": "1"}])
def test_probe_leaves_no_mark(main_repo: Path, worktree: Path, env: dict, extra: dict) -> None:
    declare(main_repo)
    run_testenv(["env", str(worktree)], main_repo, env)

    subprocess.run(
        [sys.executable, str(REACH), "probe", str(worktree), "--service", "app"],
        env={**env, **extra},
        capture_output=True,
        text=True,
    )

    assert not list(worktree.glob(".ndf-evidence/reach-*"))
    assert git(worktree, "status", "--porcelain").stdout == ""


def test_a_left_mark_stays_out_of_git(main_repo: Path, worktree: Path, env: dict) -> None:
    declare(main_repo)
    subprocess.run([sys.executable, str(REACH), "probe", str(worktree), "--service", "app"], env=env, capture_output=True)
    (worktree / ".ndf-evidence").mkdir(exist_ok=True)
    (worktree / ".ndf-evidence" / "reach-left").write_text("x", encoding="utf-8")

    assert git(worktree, "status", "--porcelain").stdout == ""


def test_probe_cli_prints_the_env_when_reached(main_repo: Path, worktree: Path, env: dict) -> None:
    declare(main_repo)
    run_testenv(["env", str(worktree)], main_repo, env)
    r = subprocess.run([sys.executable, str(REACH), "probe", str(worktree), "--service", "app"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert f"NDF_WORKTREE={worktree.resolve()}" in r.stdout.splitlines()


# --- test-run.py（コンテナの suite） ------------------------------------------------


def declare_container_suite(main: Path) -> None:
    """テスト環境と、コンテナで走る suite の宣言。project.json はメインディレクトリから読む。"""
    declare(main)
    (main / ".ndf" / "project.json").write_text(
        json.dumps(
            {
                "test": {
                    "strategy": "local-full",
                    "suites": [
                        {"name": "unit", "runner": "sh", "command": RUN, "container": {"service": "app"}},
                    ],
                }
            }
        ),
        encoding="utf-8",
    )


def run_test_run(worktree: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TEST_RUN), "whole", "--root", str(worktree)],
        cwd=str(worktree),
        env=env,
        capture_output=True,
        text=True,
    )


def test_test_run_runs_container_suite_against_the_worktree(main_repo: Path, worktree: Path, env: dict) -> None:
    declare_container_suite(main_repo)
    run_testenv(["env", str(worktree)], main_repo, env)
    (worktree / "FAIL").write_text("x\n", encoding="utf-8")

    r = run_test_run(worktree, env)

    assert r.returncode != 0 and r.returncode != 2, r.stdout + r.stderr
    assert ran(env)


def test_test_run_stops_when_the_container_sees_main(main_repo: Path, worktree: Path, env: dict) -> None:
    declare_container_suite(main_repo)
    run_testenv(["env", str(worktree)], main_repo, env)

    r = run_test_run(worktree, {**env, "FAKE_SEES_MAIN": "1"})

    assert r.returncode == 2, r.stdout + r.stderr
    assert "この worktree ではない" in json.loads(r.stdout.splitlines()[-1])["summary"]
    assert not ran(env)
