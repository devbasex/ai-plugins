"""テスト環境の回収: worktree の消えた割り当てとコンテナを片付ける（#2660）。

偽のコンテナ実行系は呼び出しを `$FAKE_STATE/calls` へ、破棄の作業ディレクトリと環境変数を `$FAKE_STATE/downs` へ残す。
コンテナの作業ディレクトリは `$FAKE_STATE/ps-<環境名>` の中身をそのまま返す。実物の Docker での確認は手動で行う。
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from worktree_helpers import SCRIPTS_DIR, git, run_lib, write_declaration

sys.path.insert(0, str(SCRIPTS_DIR / "lib"))
import container_reach  # noqa: E402
from supervise_lib import verify_steps  # noqa: E402

TESTENV = SCRIPTS_DIR / "worktree-testenv.sh"
RUN_LIMITED = SCRIPTS_DIR / "lib" / "run-limited.sh"
LOCK_COMMON = SCRIPTS_DIR / "lib" / "lock-common.sh"
ELSEWHERE = "pid:[1]"

FAKE_DOCKER = r"""#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_STATE/calls"
case "$1" in
  ps)
    [ -z "${FAKE_PS_FAIL:-}" ] || { echo "cannot connect" >&2; exit 1; }
    cat "$FAKE_STATE/ps-${4#label=com.docker.compose.project=}" 2>/dev/null
    exit 0
    ;;
  compose) shift ;;
  *) exit 0 ;;
esac
if [ "$1" = ls ]; then
  [ -z "${FAKE_LS_FAIL:-}" ] || { echo "Cannot connect to the Docker daemon" >&2; exit 1; }
  exit 0
fi
[ "$1" = -p ] || exit 0
project=$2
shift 2
while [ "$1" = -f ]; do shift 2; done
[ "$1" = down ] || exit 0
printf '%s\t%s\t%s\n' "$(pwd)" "${COMPOSE_FILE-unset}" "${COMPOSE_PROFILES-unset}" >> "$FAKE_STATE/downs"
: > "$FAKE_STATE/started-$project"
[ ! -f "$FAKE_STATE/hold-$project" ] || sleep 30
[ ! -f "$FAKE_STATE/fail-$project" ] || { echo "device busy" >&2; exit 1; }
[ -z "${FAKE_DOWN_SLEEP:-}" ] || sleep "$FAKE_DOWN_SLEEP"
exit 0
"""

# SIGTERM を無視し、子プロセスも持つ破棄。親と子の番号を残す。
STUBBORN_DOCKER = r"""#!/usr/bin/env bash
case "$1 $2" in
  "compose ls") exit 0 ;;
  ps*) exit 0 ;;
esac
trap '' TERM
sleep 60 &
printf '%s %s\n' "$$" "$!" > "$FAKE_STATE/pids"
wait
sleep 60
"""


@pytest.fixture()
def fake(tmp_path: Path, main_repo: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    # 打ち切りが timeout と gtimeout に頼ると、この偽物で落ちる
    for name in ("timeout", "gtimeout"):
        stub = bindir / name
        stub.write_text("#!/bin/sh\nexit 126\n", encoding="utf-8")
        stub.chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    env = {"LC_ALL": "C.UTF-8", "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_STATE": str(state)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return {**os.environ, **env}


def declare(main: Path, testenv: dict | None = None) -> None:
    body = {"version": 1, "localenv": {"kind": "compose", "compose_files": ["compose.yml"]}, "testenv": testenv or {}}
    write_declaration(main, json.dumps(body))


def registry_file(main: Path) -> Path:
    return main / ".git" / "ndf" / "worktree-registry.json"


def write_rows(main: Path, rows: list[dict]) -> None:
    f = registry_file(main)
    f.parent.mkdir(parents=True, exist_ok=True)
    full = [
        {"slot": i, "assigned_at": "2026-11-06T00:00:00Z", "released_at": None, "expose": None, **row} for i, row in enumerate(rows)
    ]
    f.write_text(json.dumps({"version": 1, "assignments": full}), encoding="utf-8")


def rows_of(main: Path) -> list[dict]:
    return json.loads(registry_file(main).read_text(encoding="utf-8"))["assignments"]


def released(main: Path) -> dict[str, bool]:
    return {r["worktree"]: r["released_at"] is not None for r in rows_of(main)}


def gone(main: Path, name: str = "gone") -> str:
    return str(main / ".worktrees" / name)


def containers(env: dict, environment: str, *paths: str) -> None:
    (Path(env["FAKE_STATE"]) / f"ps-{environment}").write_text("".join(f"{p}\n" for p in paths), encoding="utf-8")


def marker(env: dict, kind: str, environment: str) -> None:
    (Path(env["FAKE_STATE"]) / f"{kind}-{environment}").write_text("", encoding="utf-8")


def calls(env: dict) -> list[str]:
    f = Path(env["FAKE_STATE"]) / "calls"
    return f.read_text(encoding="utf-8").splitlines() if f.exists() else []


def downs(env: dict) -> list[str]:
    return [c for c in calls(env) if " down" in c]


def run(args: list[str], env: dict, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(TESTENV), *args], cwd=str(cwd), env=env, capture_output=True, text=True)


def reclaim(main: Path, env: dict, *args: str) -> tuple[subprocess.CompletedProcess, dict]:
    p = run(["reclaim", str(main), *args], env, main)
    assert p.stdout.strip(), p.stderr
    return p, json.loads(p.stdout)


def by_result(out: dict, result: str) -> list[dict]:
    return [i for i in out["items"] if i["result"] == result]


# --- 回収 ---------------------------------------------------------------------


def test_a_stale_assignment_is_discarded_without_the_definition(main_repo: Path, fake: dict) -> None:
    declare(main_repo)
    wt = gone(main_repo)
    write_rows(main_repo, [{"worktree": wt, "environment": "e1", "golden_tag": "t"}])
    containers(fake, "e1", wt, f"{wt}/docker")

    p, out = reclaim(main_repo, {**fake, "COMPOSE_FILE": "/x/compose.yml", "COMPOSE_PROFILES": "p"})

    assert p.returncode == 0 and out["status"] == "ok" and out["tool"] == "testenv-reclaim"
    assert downs(fake) == ["compose -p e1 down --volumes"]
    assert (Path(fake["FAKE_STATE"]) / "downs").read_text(encoding="utf-8") == "/\tunset\tunset\n"
    assert released(main_repo) == {wt: True}
    assert "golden_tag" not in rows_of(main_repo)[0]
    assert out["items"] == [{"kind": "testenv", "name": "e1", "result": "reclaimed", "worktree": wt, "volumes": "discarded"}]
    assert out["metrics"]["reclaimed"] == 1
    assert out["summary"] == "テスト環境 1 件を回収した（失敗 0 件・残した 0 件）"


@pytest.mark.parametrize("value", [False, "false"], ids=["bool", "text"])
def test_a_false_declaration_keeps_the_volumes(main_repo: Path, fake: dict, value: object) -> None:
    declare(main_repo, {"discard_volumes": value})
    wt = gone(main_repo)
    write_rows(main_repo, [{"worktree": wt, "environment": "e1"}])
    containers(fake, "e1", wt)

    p, out = reclaim(main_repo, fake)

    assert p.returncode == 0 and downs(fake) == ["compose -p e1 down"]
    assert released(main_repo) == {wt: True} and out["items"][0]["volumes"] == "kept" and "reason" not in out["items"][0]


def test_the_volumes_option_overrides_the_declaration(main_repo: Path, fake: dict) -> None:
    declare(main_repo, {"discard_volumes": False})
    wt = gone(main_repo)
    write_rows(main_repo, [{"worktree": wt, "environment": "e1"}])
    containers(fake, "e1", wt)

    reclaim(main_repo, fake, "--volumes")

    assert downs(fake) == ["compose -p e1 down --volumes"]


@pytest.mark.parametrize("args", [(), ("--volumes",)], ids=["declared", "option"])
def test_without_containers_the_volumes_are_kept_and_the_row_is_released(main_repo: Path, fake: dict, args: tuple) -> None:
    declare(main_repo)
    wt = gone(main_repo)
    write_rows(main_repo, [{"worktree": wt, "environment": "e1"}])

    p, out = reclaim(main_repo, fake, *args)

    assert p.returncode == 0 and downs(fake) == ["compose -p e1 down"]
    assert released(main_repo) == {wt: True}
    assert out["items"][0]["result"] == "reclaimed" and out["items"][0]["volumes"] == "kept" and "持ち主" in out["items"][0]["reason"]


def test_a_live_row_and_a_locked_row_are_left_alone(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo)
    locked = main_repo / ".worktrees" / "locked"
    git(main_repo, "worktree", "add", "-q", "-b", "locked", str(locked))
    git(main_repo, "worktree", "lock", str(locked))
    locked.rename(main_repo / ".worktrees" / "moved")
    write_rows(main_repo, [{"worktree": str(worktree), "environment": "e1"}, {"worktree": str(locked), "environment": "e2"}])

    p, out = reclaim(main_repo, fake)

    assert p.returncode == 0 and out["items"] == [] and downs(fake) == []
    assert not any(released(main_repo).values())


def test_a_shared_environment_is_not_discarded(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo)
    wt = gone(main_repo)
    write_rows(main_repo, [{"worktree": wt, "environment": "e1"}, {"worktree": str(worktree), "environment": "e1"}])

    p, out = reclaim(main_repo, fake)

    assert p.returncode == 0 and downs(fake) == []
    assert released(main_repo) == {wt: True, str(worktree): False}
    assert out["items"][0]["result"] == "released" and out["metrics"]["released"] == 1


def test_two_rows_of_one_environment_are_discarded_once(main_repo: Path, fake: dict) -> None:
    declare(main_repo)
    a, b = gone(main_repo, "a"), gone(main_repo, "b")
    write_rows(main_repo, [{"worktree": a, "environment": "e1"}, {"worktree": b, "environment": "e1"}])
    # ラベルは一方のパスしか持たない
    containers(fake, "e1", b)

    p, out = reclaim(main_repo, fake)

    assert p.returncode == 0 and downs(fake) == ["compose -p e1 down --volumes"]
    assert released(main_repo) == {a: True, b: True} and len(out["items"]) == 1


def test_a_container_of_an_already_released_row_counts_as_own(main_repo: Path, fake: dict) -> None:
    declare(main_repo)
    a, b = gone(main_repo, "a"), gone(main_repo, "b")
    write_rows(main_repo, [{"worktree": a, "environment": "e1", "released_at": "2026-11-06T01:00:00Z"}, {"worktree": b, "environment": "e1"}])
    containers(fake, "e1", a)

    _, out = reclaim(main_repo, fake)

    assert out["items"][0]["result"] == "reclaimed" and downs(fake) == ["compose -p e1 down --volumes"]


def test_a_failed_discard_keeps_the_row_and_the_others_go_on(main_repo: Path, fake: dict) -> None:
    declare(main_repo)
    a, b = gone(main_repo, "a"), gone(main_repo, "b")
    write_rows(main_repo, [{"worktree": a, "environment": "e1"}, {"worktree": b, "environment": "e2"}])
    containers(fake, "e1", a)
    containers(fake, "e2", b)
    marker(fake, "fail", "e1")

    p, out = reclaim(main_repo, fake)

    assert p.returncode == 1 and out["status"] == "stopped"
    assert released(main_repo) == {a: False, b: True}
    failed = by_result(out, "failed")
    assert failed[0]["name"] == "e1" and "device busy" in failed[0]["reason"]

    (Path(fake["FAKE_STATE"]) / "fail-e1").unlink()
    p, out = reclaim(main_repo, fake)

    assert p.returncode == 0 and released(main_repo) == {a: True, b: True}


@pytest.mark.parametrize("closed_row", [False, True], ids=["open-row", "released-row"])
def test_an_open_expose_is_left_alone(main_repo: Path, fake: dict, closed_row: bool) -> None:
    declare(main_repo)
    a, b = gone(main_repo, "a"), gone(main_repo, "b")
    expose = {"url": "https://wt0.t.example", "closed_at": None}
    rows = [{"worktree": a, "environment": "e1", "expose": expose}]
    if closed_row:
        rows = [{**rows[0], "released_at": "2026-11-06T01:00:00Z"}, {"worktree": b, "environment": "e1"}]
    write_rows(main_repo, rows)

    p, out = reclaim(main_repo, fake)

    assert p.returncode == 0 and downs(fake) == []
    assert by_result(out, "exposed")[0]["url"] == "https://wt0.t.example" and out["metrics"]["exposed"] == 1
    assert released(main_repo)[b if closed_row else a] is False


def test_only_the_exact_project_is_named(main_repo: Path, fake: dict) -> None:
    declare(main_repo)
    wt = gone(main_repo)
    write_rows(main_repo, [{"worktree": wt, "environment": "e1"}])
    containers(fake, "e1", wt)
    containers(fake, "e1-other", "/somewhere/else")

    reclaim(main_repo, fake)

    named = [c for c in calls(fake) if "e1" in c]
    assert named == [
        'ps -a --filter label=com.docker.compose.project=e1 --format {{.Label "com.docker.compose.project.working_dir"}}',
        "compose -p e1 down --volumes",
    ]


@pytest.mark.parametrize("extra", [{"WT_DOCKER_COMMAND": "no-such-runtime"}, {"FAKE_LS_FAIL": "1"}], ids=["missing", "unreachable"])
def test_no_runtime_releases_nothing(main_repo: Path, fake: dict, extra: dict) -> None:
    declare(main_repo)
    wt = gone(main_repo)
    write_rows(main_repo, [{"worktree": wt, "environment": "e1"}])

    p, out = reclaim(main_repo, {**fake, **extra})

    assert p.returncode == 1 and out["status"] == "stopped"
    assert released(main_repo) == {wt: False}
    assert [i["result"] for i in out["items"]] == ["not_run"] and out["items"][0]["kind"] == "testenv-reclaim"
    assert out["items"][0]["reason"]


def test_a_broken_registry_reclaims_nothing(main_repo: Path, fake: dict) -> None:
    declare(main_repo)
    registry_file(main_repo).parent.mkdir(parents=True)
    registry_file(main_repo).write_text("{broken", encoding="utf-8")

    p, out = reclaim(main_repo, fake)

    assert p.returncode == 1 and out["items"][0]["result"] == "not_run" and calls(fake) == []


def test_without_a_declaration_it_is_silent(main_repo: Path, fake: dict) -> None:
    p = run(["reclaim", str(main_repo)], fake, main_repo)

    assert p.returncode == 0 and p.stdout == "" and calls(fake) == []


def test_no_stale_assignment_reads_only_the_registry(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo)
    write_rows(main_repo, [{"worktree": str(worktree), "environment": "e1"}])

    p, out = reclaim(main_repo, {**fake, "GIT_TRACE": "1"})

    assert p.returncode == 0 and out["items"] == [] and calls(fake) == []
    assert "worktree list" not in p.stderr


def test_two_reclaims_at_once_both_end_cleanly(main_repo: Path, fake: dict) -> None:
    declare(main_repo)
    paths = [gone(main_repo, f"g{i}") for i in range(4)]
    write_rows(main_repo, [{"worktree": p, "environment": f"e{i}"} for i, p in enumerate(paths)])
    for i, p in enumerate(paths):
        containers(fake, f"e{i}", p)

    procs = [
        subprocess.Popen(["bash", str(TESTENV), "reclaim", str(main_repo)], env=fake, stdout=subprocess.PIPE, text=True) for _ in range(2)
    ]
    for p in procs:
        p.communicate(timeout=60)

    assert [p.returncode for p in procs] == [0, 0]
    assert all(released(main_repo).values())
    assert sorted(downs(fake)) == sorted(set(downs(fake)))


# --- 持ち主の確認（決定 11） -----------------------------------------------------


@pytest.mark.parametrize("label", ["/other/clone/.worktrees/gone", ""], ids=["other-path", "empty"])
def test_a_container_of_another_location_is_left_alone(main_repo: Path, fake: dict, label: str) -> None:
    declare(main_repo)
    wt = gone(main_repo)
    write_rows(main_repo, [{"worktree": wt, "environment": "e1"}])
    containers(fake, "e1", wt, label)

    p, out = reclaim(main_repo, fake)

    assert p.returncode == 0 and downs(fake) == [] and released(main_repo) == {wt: False}
    assert by_result(out, "foreign")[0]["reason"] and out["metrics"]["foreign"] == 1


def test_a_sibling_path_is_not_under_the_worktree(main_repo: Path, fake: dict) -> None:
    declare(main_repo)
    wt = gone(main_repo)
    write_rows(main_repo, [{"worktree": wt, "environment": "e1"}])
    containers(fake, "e1", f"{wt}-2")

    _, out = reclaim(main_repo, fake)

    assert out["items"][0]["result"] == "foreign" and downs(fake) == []


def test_an_unreadable_owner_fails_without_a_discard(main_repo: Path, fake: dict) -> None:
    declare(main_repo)
    wt = gone(main_repo)
    write_rows(main_repo, [{"worktree": wt, "environment": "e1"}])

    p, out = reclaim(main_repo, {**fake, "FAKE_PS_FAIL": "1"})

    assert p.returncode == 1 and downs(fake) == [] and "cannot connect" in out["items"][0]["reason"]


def test_a_tag_that_cannot_be_dropped_stops_the_discard(main_repo: Path, fake: dict) -> None:
    declare(main_repo)
    wt = gone(main_repo)
    write_rows(main_repo, [{"worktree": wt, "environment": "e1", "golden_tag": "t"}])
    lock = main_repo / ".git" / "ndf" / "worktree-registry.json.lockdir"
    lock.mkdir()
    (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    (lock / "token").write_text("x\n", encoding="utf-8")

    p, out = reclaim(main_repo, fake)

    assert p.returncode == 1 and downs(fake) == [] and "基準のタグ" in out["items"][0]["reason"]


# --- 見えない位置の割り当て（決定 3） -----------------------------------------------


def test_a_row_outside_without_a_parent_is_unseen(main_repo: Path, tmp_path: Path, fake: dict) -> None:
    declare(main_repo)
    wt = str(tmp_path / "no-parent" / "wt")
    write_rows(main_repo, [{"worktree": wt, "environment": "e1"}])

    p, out = reclaim(main_repo, fake)

    assert p.returncode == 0 and downs(fake) == [] and released(main_repo) == {wt: False}
    assert out["items"][0]["result"] == "unseen" and out["items"][0]["name"] == "e1" and out["metrics"]["unseen"] == 1


def test_a_row_on_another_file_system_is_unseen(main_repo: Path, fake: dict) -> None:
    if os.stat("/dev/shm").st_dev == os.stat(main_repo).st_dev:
        pytest.skip("/dev/shm が別のファイルシステムでない")
    declare(main_repo)
    wt = "/dev/shm/ndf-no-such-worktree"
    write_rows(main_repo, [{"worktree": wt, "environment": "e1"}])

    _, out = reclaim(main_repo, fake)

    assert out["items"][0]["result"] == "unseen" and downs(fake) == []


def test_an_unreadable_file_system_is_unseen(main_repo: Path, tmp_path: Path, fake: dict) -> None:
    declare(main_repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    wt = str(outside / "wt")
    write_rows(main_repo, [{"worktree": wt, "environment": "e1"}])
    stub = Path(fake["PATH"].split(":")[0]) / "stat"
    stub.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    stub.chmod(0o755)

    _, out = reclaim(main_repo, fake)

    assert out["items"][0]["result"] == "unseen" and downs(fake) == []


def test_an_unseen_row_counts_as_sharing_the_environment(main_repo: Path, tmp_path: Path, fake: dict) -> None:
    declare(main_repo)
    wt, unseen = gone(main_repo), str(tmp_path / "no-parent" / "wt")
    write_rows(main_repo, [{"worktree": wt, "environment": "e1"}, {"worktree": unseen, "environment": "e1"}])

    _, out = reclaim(main_repo, fake)

    assert downs(fake) == [] and released(main_repo) == {wt: True, unseen: False}
    assert sorted(i["result"] for i in out["items"]) == ["released", "unseen"]


def test_a_row_outside_on_the_same_file_system_is_reclaimed(main_repo: Path, tmp_path: Path, fake: dict) -> None:
    declare(main_repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    wt = str(outside / "wt")
    write_rows(main_repo, [{"worktree": wt, "environment": "e1"}])
    containers(fake, "e1", wt)

    _, out = reclaim(main_repo, fake)

    assert out["items"][0]["result"] == "reclaimed"


def test_a_deep_row_under_the_main_directory_is_reclaimed(main_repo: Path, fake: dict) -> None:
    declare(main_repo)
    wt = gone(main_repo, "a/b/c")
    write_rows(main_repo, [{"worktree": wt, "environment": "e1"}])

    _, out = reclaim(main_repo, fake)

    assert out["items"][0]["result"] == "reclaimed"


# --- 上限 ---------------------------------------------------------------------


def test_the_limit_defers_the_rest_for_the_next_reclaim(main_repo: Path, fake: dict) -> None:
    declare(main_repo)
    a, b = gone(main_repo, "a"), gone(main_repo, "b")
    write_rows(main_repo, [{"worktree": a, "environment": "e1"}, {"worktree": b, "environment": "e2"}])
    containers(fake, "e1", a)
    containers(fake, "e2", b)

    p, out = reclaim(main_repo, {**fake, "WT_RECLAIM_SECONDS": "4", "FAKE_DOWN_SLEEP": "8"})

    assert p.returncode == 1
    assert by_result(out, "failed")[0]["name"] == "e1" and "上限" in by_result(out, "failed")[0]["reason"]
    assert by_result(out, "deferred")[0]["name"] == "e2" and out["metrics"]["deferred"] == 1
    assert len(downs(fake)) == 1

    p, _ = reclaim(main_repo, fake)

    assert p.returncode == 0 and all(released(main_repo).values())


def test_no_seconds_left_before_the_discard_defers_it(main_repo: Path, fake: dict) -> None:
    declare(main_repo)
    wt = gone(main_repo)
    write_rows(main_repo, [{"worktree": wt, "environment": "e1", "golden_tag": "t"}])
    containers(fake, "e1", wt)
    # 持ち主の確認の後、基準のタグを消す書き込みで時計を進める
    bindir = Path(fake["PATH"].split(":")[0])
    real_date, real_mktemp = shutil.which("date"), shutil.which("mktemp")
    clock, write_hook = bindir / "date", bindir / "mktemp"
    clock.write_text(f'#!/bin/sh\n[ -f "$FAKE_STATE/late" ] && expr "$({real_date} +%s)" + 1000 || exec {real_date} "$@"\n', encoding="utf-8")
    write_hook.write_text(
        f'#!/bin/sh\ncase "$1" in *worktree-registry.json.*) : > "$FAKE_STATE/late" ;; esac\nexec {real_mktemp} "$@"\n', encoding="utf-8"
    )
    clock.chmod(0o755)
    write_hook.chmod(0o755)

    p, out = reclaim(main_repo, fake)

    assert p.returncode == 0 and by_result(out, "deferred")[0]["name"] == "e1"
    assert downs(fake) == [] and released(main_repo) == {wt: False}


def stubborn(fake: dict) -> None:
    docker = Path(fake["PATH"].split(":")[0]) / "docker"
    docker.write_text(STUBBORN_DOCKER, encoding="utf-8")


def pids(fake: dict) -> list[int]:
    f = Path(fake["FAKE_STATE"]) / "pids"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if f.exists() and len(text := f.read_text(encoding="utf-8").split()) == 2:
            return [int(n) for n in text]
        time.sleep(0.05)
    raise AssertionError("破棄が始まらなかった")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0] != "Z"


def wait_gone(numbers: list[int], seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and any(alive(n) for n in numbers):
        time.sleep(0.05)
    assert not any(alive(n) for n in numbers)


def test_a_discard_that_ignores_sigterm_is_stopped_with_its_children(main_repo: Path, fake: dict) -> None:
    declare(main_repo)
    wt = gone(main_repo)
    write_rows(main_repo, [{"worktree": wt, "environment": "e1"}])
    stubborn(fake)

    started = time.monotonic()
    p, out = reclaim(main_repo, {**fake, "WT_RECLAIM_SECONDS": "4"})

    assert p.returncode == 1 and "上限" in out["items"][0]["reason"] and released(main_repo) == {wt: False}
    assert time.monotonic() - started < 20
    wait_gone(pids(fake), 2)


def test_stopping_the_reclaim_stops_the_discard(main_repo: Path, fake: dict) -> None:
    declare(main_repo)
    wt = gone(main_repo)
    write_rows(main_repo, [{"worktree": wt, "environment": "e1"}])
    stubborn(fake)

    with subprocess.Popen(["bash", str(TESTENV), "reclaim", str(main_repo)], env=fake, stdout=subprocess.DEVNULL, start_new_session=True) as p:
        numbers = pids(fake)
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()

    wait_gone(numbers, 1.5)
    assert released(main_repo) == {wt: False}


def limited(fake: dict, *command: str, seconds: int = 30, grace: int = 1) -> int:
    script = f'( set -m; bash "{RUN_LIMITED}" {seconds} {grace} $$ "$@" </dev/null & wait "$!" ) 2>/dev/null'
    return subprocess.run(["bash", "-c", script, "bash", *command], env=fake).returncode


@pytest.mark.parametrize("code", [0, 1, 7])
def test_the_exit_code_of_the_command_is_returned(fake: dict, code: int) -> None:
    assert limited(fake, "sh", "-c", f"exit {code}") == code


def test_the_time_limit_returns_137(fake: dict) -> None:
    assert limited(fake, "sleep", "30", seconds=1) == 137


# --- 回収の予約（決定 4） ---------------------------------------------------------


def hold_discard(main: Path, fake: dict, environment: str) -> subprocess.Popen:
    """破棄の途中で止まる回収を起こし、破棄が始まるまで待つ。"""
    marker(fake, "hold", environment)
    proc = subprocess.Popen(
        ["bash", str(TESTENV), "reclaim", str(main)], env=fake, stdout=subprocess.PIPE, text=True, start_new_session=True
    )
    started = Path(fake["FAKE_STATE"]) / f"started-{environment}"
    deadline = time.monotonic() + 15
    while not started.exists():
        assert time.monotonic() < deadline, "破棄が始まらなかった"
        time.sleep(0.05)
    return proc


def stop(proc: subprocess.Popen) -> None:
    os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()
    proc.stdout.close()


def env_name(main: Path, worktree: Path, fake: dict) -> tuple[str, str]:
    """worktree の環境名と、その環境の残った割り当ての行を用意し、(環境名, 消えた worktree) を返す。"""
    declare(main)
    environment = json.loads(run(["env", str(worktree)], fake, main).stdout)["environment"]
    wt = gone(main)
    write_rows(main, [{"worktree": wt, "environment": environment}])
    containers(fake, environment, wt)
    return environment, wt


def test_env_and_up_wait_for_the_reservation_and_stop(main_repo: Path, worktree: Path, fake: dict) -> None:
    environment, wt = env_name(main_repo, worktree, fake)
    proc = hold_discard(main_repo, fake, environment)
    try:
        p = run(["env", str(worktree)], fake, main_repo)
        assert p.returncode == 1 and "同じ環境名のテスト環境を回収しています" in p.stderr
        assert str(worktree) not in released(main_repo)

        rows = rows_of(main_repo) + [{"worktree": str(worktree), "environment": environment, "slot": 9, "released_at": None}]
        registry_file(main_repo).write_text(json.dumps({"version": 1, "assignments": rows}), encoding="utf-8")
        p = run(["up", str(worktree)], fake, main_repo)
        assert p.returncode == 1 and "同じ環境名のテスト環境を回収しています" in p.stderr
        assert not [c for c in calls(fake) if " up " in c]
    finally:
        stop(proc)


def test_a_row_added_before_the_reclaim_stops_the_discard(main_repo: Path, worktree: Path, fake: dict) -> None:
    environment, wt = env_name(main_repo, worktree, fake)
    rows = rows_of(main_repo) + [{"worktree": str(worktree), "environment": environment, "slot": 9, "released_at": None}]
    registry_file(main_repo).write_text(json.dumps({"version": 1, "assignments": rows}), encoding="utf-8")

    _, out = reclaim(main_repo, fake)

    assert out["items"][0]["result"] == "released" and downs(fake) == []


def test_env_and_up_pass_while_a_test_runs(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo)
    environment = json.loads(run(["env", str(worktree)], fake, main_repo).stdout)["environment"]
    (main_repo / ".git" / "ndf" / f"{environment}.inuse.d").mkdir()
    (main_repo / ".git" / "ndf" / f"{environment}.inuse.d" / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")

    started = time.monotonic()
    assert run(["env", str(worktree)], fake, main_repo).returncode == 0
    assert run(["up", str(worktree)], fake, main_repo).returncode == 0
    assert time.monotonic() - started < 3


def a_reservation(main: Path, environment: str, *, until: int, scope: str = ELSEWHERE, pid: str = "4194000") -> Path:
    lock = main / ".git" / "ndf" / f"{environment}.reclaim.d"
    lock.mkdir(parents=True)
    (lock / "held").write_text("", encoding="utf-8")
    (lock / "token").write_text("t\n", encoding="utf-8")
    (lock / "owner_scope").write_text(f"{scope}\n{until}\n", encoding="utf-8")
    (lock / "pid").write_text(f"{pid}\n", encoding="utf-8")
    return lock


def current_scope() -> str:
    return subprocess.run(["bash", "-c", f'. "{LOCK_COMMON}"; _ndf_lock_scope'], capture_output=True, text=True).stdout.strip()


def test_a_reservation_of_another_container_is_kept_until_its_time(main_repo: Path, worktree: Path, fake: dict) -> None:
    environment, wt = env_name(main_repo, worktree, fake)
    lock = a_reservation(main_repo, environment, until=int(time.time()) + 300)

    p = run(["env", str(worktree)], fake, main_repo)
    _, out = reclaim(main_repo, fake)

    assert p.returncode == 1 and "回収しています" in p.stderr and lock.exists()
    assert out["items"][0]["result"] == "deferred" and downs(fake) == []


def test_a_reservation_of_another_container_is_dropped_after_its_time(main_repo: Path, worktree: Path, fake: dict) -> None:
    environment, wt = env_name(main_repo, worktree, fake)
    a_reservation(main_repo, environment, until=int(time.time()) - 1)

    _, out = reclaim(main_repo, fake)

    assert out["items"][0]["result"] == "reclaimed"


def test_a_reservation_of_a_gone_owner_here_is_dropped_at_once(main_repo: Path, worktree: Path, fake: dict) -> None:
    environment, wt = env_name(main_repo, worktree, fake)
    a_reservation(main_repo, environment, until=int(time.time()) + 300, scope=current_scope())

    _, out = reclaim(main_repo, fake)

    assert out["items"][0]["result"] == "reclaimed"


def test_a_lock_without_the_hold_argument_has_no_owner_scope(main_repo: Path, tmp_path: Path) -> None:
    lock = tmp_path / "plain.lock"

    assert run_lib(f'wt_lock_acquire "{lock}" 1').returncode == 0
    assert not (lock / "owner_scope").exists()


def test_the_hold_argument_writes_the_owner_scope(main_repo: Path, tmp_path: Path) -> None:
    lock = tmp_path / "held.lock"

    assert run_lib(f'wt_lock_acquire "{lock}" 1 330').returncode == 0
    scope, until = (lock / "owner_scope").read_text(encoding="utf-8").splitlines()
    assert scope == current_scope() and 320 < int(until) - time.time() <= 331


def test_the_foreign_state_of_a_lock_is_read_by_is_held(tmp_path: Path) -> None:
    lock = tmp_path / "foreign.lock"
    lock.mkdir()
    (lock / "pid").write_text("4194000\n", encoding="utf-8")
    (lock / "token").write_text("t\n", encoding="utf-8")
    (lock / "owner_scope").write_text(f"{ELSEWHERE}\n{int(time.time()) + 60}\n", encoding="utf-8")

    assert run_lib(f'wt_lock_is_held "{lock}"').returncode == 0


# --- スロットの枯渇 --------------------------------------------------------------


def full_registry(main: Path, stale: int) -> list[str]:
    """64 行で埋める。末尾の `stale` 行が残った割り当てで、残りは見えない位置の行である。"""
    paths = [gone(main, f"g{i}") for i in range(stale)]
    rows = [{"worktree": f"/w/{i}", "environment": f"w{i}"} for i in range(64 - stale)]
    rows += [{"worktree": p, "environment": f"e{i}"} for i, p in enumerate(paths)]
    write_rows(main, rows)
    return paths


def test_a_full_registry_reclaims_one_environment_and_assigns(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo)
    paths = full_registry(main_repo, 2)

    p = run(["env", str(worktree)], fake, main_repo)

    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["worktree"] == str(worktree) and out["slot"] == 62
    assert "残った割り当てを回収しました: 1 件" in p.stderr
    assert downs(fake) == ["compose -p e0 down"]
    assert released(main_repo)[paths[0]] is True and released(main_repo)[paths[1]] is False


def test_a_full_registry_without_a_stale_row_stays_full_and_reclaims_once(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo)
    full_registry(main_repo, 0)

    p = run(["env", str(worktree)], fake, main_repo)

    assert p.returncode == 1 and "空きスロットがありません（上限 64）" in p.stderr
    assert p.stdout == "" and calls(fake).count("compose ls -q") == 1


def test_a_failed_registry_lock_does_not_reclaim(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo)
    write_rows(main_repo, [{"worktree": gone(main_repo), "environment": "e0"}])
    lock = main_repo / ".git" / "ndf" / "worktree-registry.json.lockdir"
    lock.mkdir()
    (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    (lock / "token").write_text("x\n", encoding="utf-8")

    p = run(["env", str(worktree)], fake, main_repo)

    assert p.returncode == 1 and calls(fake) == []


# --- 呼び出し側 -----------------------------------------------------------------


def test_the_step_limit_and_the_reservation_use_the_outer_limit() -> None:
    assert verify_steps.RECLAIM_LIMIT == container_reach.RECLAIM_LIMIT
    sweep = (SCRIPTS_DIR / "lib" / "worktree-testenv-sweep.sh").read_text(encoding="utf-8")
    assert f"\nRECLAIM_HOLD={container_reach.RECLAIM_LIMIT}\n" in sweep


def test_reclaim_testenv_returns_the_rows(main_repo: Path, fake: dict) -> None:
    declare(main_repo)
    wt = gone(main_repo)
    write_rows(main_repo, [{"worktree": wt, "environment": "e1"}])

    items = container_reach.reclaim_testenv(main_repo)

    assert [i["result"] for i in items] == ["reclaimed"]


def test_reclaim_testenv_without_a_declaration_is_empty(main_repo: Path, fake: dict) -> None:
    assert container_reach.reclaim_testenv(main_repo) == []


@pytest.mark.parametrize(
    ("answer", "want"),
    [
        ((None, "", ""), "330 秒で終わらなかった"),
        ((0, "{broken", ""), "読めない"),
        ((0, '{"items": 1}', ""), "形が違う"),
        ((2, "", "https://x-access-token:s3cret-value@ghcr.io"), "終了コード 2"),
    ],
    ids=["limit", "json", "shape", "code"],
)
def test_reclaim_testenv_turns_a_bad_result_into_not_run(monkeypatch: pytest.MonkeyPatch, answer: tuple, want: str) -> None:
    monkeypatch.setattr(container_reach, "_testenv", lambda *a, **k: answer)

    items = container_reach.reclaim_testenv("/m")

    assert items[0]["kind"] == "testenv-reclaim" and items[0]["result"] == "not_run" and items[0]["name"] == "/m"
    assert want in items[0]["reason"] and "s3cret-value" not in items[0]["reason"]


def test_reclaim_testenv_hides_the_credentials_in_a_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {"kind": "testenv", "name": "e", "result": "failed", "reason": "https://x-access-token:s3cret-value@ghcr.io"}
    monkeypatch.setattr(container_reach, "_testenv", lambda *a, **k: (1, json.dumps({"items": [row]}), ""))

    assert "s3cret-value" not in container_reach.reclaim_testenv("/m")[0]["reason"]


def test_up_gets_what_env_left_of_the_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[tuple[str, float | None]] = []

    def answer(*args: str, timeout: float | None = None) -> tuple[int, str, str]:
        seen.append((args[0], timeout))
        if args[0] == "env":
            time.sleep(0.3)
            return 0, '{"environment": "e"}', ""
        return 0, "", ""

    monkeypatch.setattr(container_reach, "_testenv", answer)
    monkeypatch.setattr(container_reach, "compose_env", lambda _: {"COMPOSE_FILE": "x"})
    monkeypatch.setattr(container_reach, "leftover_volumes", lambda _: [])
    monkeypatch.setattr(container_reach, "UP_TIMEOUT", 10)

    container_reach._wake(tmp_path, ["app"], ["app"])

    assert seen[0] == ("env", 10) and seen[1][0] == "up" and seen[1][1] <= 9.7


def test_an_env_over_the_limit_gives_the_reason(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(container_reach, "_testenv", lambda *a, **k: (None, "", ""))

    with pytest.raises(container_reach.Unreachable) as e:
        container_reach._wake(tmp_path, ["app"], ["app"])

    assert "テスト環境の割り当てが 300 秒で終わらなかった" in e.value.reason
