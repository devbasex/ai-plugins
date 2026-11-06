"""テスト環境の用意・detached HEAD の採番・破棄（#2567）。

偽のコンテナ実行系は呼び出しを `$FAKE_STATE/calls` へ残す。`up -d` で「動いている」印を置き、`compose exec` は印があるときだけ
worktree で（`FAKE_SEES_MAIN` があればメインディレクトリで）コマンドを走らせる。実物の Docker での確認はリリース後テストで行う。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from worktree_helpers import SCRIPTS_DIR, git, run_lib, write_declaration

sys.path.insert(0, str(SCRIPTS_DIR / "lib"))
import container_reach  # noqa: E402
import worktree_base  # noqa: E402

TESTENV = SCRIPTS_DIR / "worktree-testenv.sh"
TEST_RUN = SCRIPTS_DIR / "test-run.py"

FAKE_DOCKER = r"""#!/bin/sh
printf '%s\n' "$*" >> "$FAKE_STATE/calls"
if [ "$1" = volume ]; then
  case "$*" in *" -q "*) ;; *) echo "DRIVER    VOLUME NAME" ;; esac
  [ -z "${FAKE_VOLUME_LS_FAIL:-}" ] || exit 1
  cat "$FAKE_STATE/volumes" 2>/dev/null
  exit 0
fi
[ "$1" = compose ] || exit 1
shift
while [ "$1" = -p ] || [ "$1" = -f ]; do shift 2; done
case "$1" in
  up)
    case "$*" in
      *--wait*) [ -z "${FAKE_WAIT_FAIL:-}" ] || { echo "dependency db failed" >&2; exit 1; } ;;
      *)
        [ -z "${FAKE_UP_FAIL:-}" ] || { echo "image not found" >&2; exit 1; }
        [ -z "${FAKE_UP_SLEEP:-}" ] || sleep "$FAKE_UP_SLEEP"
        : > "$FAKE_STATE/running"
        ;;
    esac
    ;;
  down)
    [ ! -d "${NDF_WORKTREE:-/nonexistent}" ] || echo "down: worktree present" >> "$FAKE_STATE/calls"
    [ -z "${FAKE_DOWN_FAIL:-}" ] || { echo "device busy" >&2; exit 1; }
    rm -f "$FAKE_STATE/running"
    ;;
  exec)
    shift
    [ "$1" = -T ] && shift
    shift
    [ -f "$FAKE_STATE/running" ] || { echo "service is not running" >&2; exit 1; }
    [ -z "${FAKE_SEES_MAIN:-}" ] || cd "$FAKE_MAIN" || exit 1
    exec "$@"
    ;;
esac
"""

RUN = 'echo ran >> "$RAN_LOG"'
SCOPED = 'echo {paths} >> "$RAN_LOG"'
EXPOSE = {"enabled": True, "public_tag": "golden-public", "base_domain": "t.example", "open_command": "true"}


@pytest.fixture()
def fake(tmp_path: Path, main_repo: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """偽の実行系を PATH の先頭へ置き、この環境を返す。in-process の到達の確認も同じ環境で動く。"""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text(FAKE_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    (main_repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    git(main_repo, "add", ".gitignore")
    git(main_repo, "commit", "-q", "-m", "ignore")
    env = {
        "LC_ALL": "C.UTF-8",
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "FAKE_MAIN": str(main_repo),
        "FAKE_STATE": str(state),
        "RAN_LOG": str(tmp_path / "ran.log"),
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return {**os.environ, **env}


def declare(main: Path, testenv: dict | None = None, *, localenv: bool = True, suites: list | None = None) -> None:
    body: dict = {"version": 1}
    if localenv:
        body["localenv"] = {"kind": "compose", "compose_files": ["compose.yml"]}
    if testenv is not None:
        body["testenv"] = testenv
    write_declaration(main, json.dumps(body))
    default = [{"name": "unit", "runner": "sh", "command": RUN, "scope_command": SCOPED, "container": {"service": "app"}}]
    project = {"test": {"strategy": "local-full", "suites": default if suites is None else suites}}
    (main / ".ndf" / "project.json").write_text(json.dumps(project), encoding="utf-8")


def wt_env(args: list[str], env: dict, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(TESTENV), *args], cwd=str(cwd), env=env, capture_output=True, text=True)


def run_tests(worktree: Path, env: dict, *args: str) -> tuple[subprocess.CompletedProcess, dict]:
    p = subprocess.run(
        [sys.executable, str(TEST_RUN), *args, "--root", str(worktree)], cwd=str(worktree), env=env, capture_output=True, text=True
    )
    return p, json.loads(p.stdout.strip().splitlines()[-1])


def calls(env: dict) -> list[str]:
    f = Path(env["FAKE_STATE"]) / "calls"
    return f.read_text(encoding="utf-8").splitlines() if f.exists() else []


def ran(env: dict) -> bool:
    return Path(env["RAN_LOG"]).exists()


def ups(env: dict) -> list[str]:
    return [c for c in calls(env) if c.startswith("compose") and " up " in c]


def wake_rows(payload: dict) -> list[dict]:
    return [row for item in payload["items"] for row in item.get("testenv", [])]


def registry(main: Path) -> list[dict]:
    return json.loads((main / ".git" / "ndf" / "worktree-registry.json").read_text(encoding="utf-8"))["assignments"]


def detached(main: Path, name: str) -> Path:
    path = main / ".worktrees" / ".ndf" / "o--r" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    git(main, "worktree", "add", "-q", "--detach", str(path), "HEAD")
    return path


# --- テストの前の用意 ----------------------------------------------------------------


@pytest.mark.parametrize("args", [("scope", "--paths", "a.py"), ("whole",)], ids=["scope", "whole"])
def test_a_stopped_testenv_is_woken_and_the_test_runs(main_repo: Path, worktree: Path, fake: dict, args: tuple) -> None:
    declare(main_repo, {})

    p, out = run_tests(worktree, fake, *args)

    assert p.returncode == 0 and out["status"] == "ok", p.stdout + p.stderr
    assert ran(fake)
    rows = wake_rows(out)
    assert [r["result"] for r in rows] == ["woken"] and rows[0]["environment"].startswith("main-wt-feature-x-")
    # 全体の up の後に、宣言のサービスだけを待つ
    assert ups(fake)[0].endswith(" up -d") and ups(fake)[1].endswith(" up -d --wait app")
    assert len(ups(fake)) == 2


def test_the_wake_does_not_count_against_the_test_limit(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {})

    p, out = run_tests(worktree, {**fake, "FAKE_UP_SLEEP": "3"}, "whole", "--limit", "2")

    assert p.returncode == 0 and out["status"] == "ok", p.stdout + p.stderr


def test_a_reachable_worktree_starts_nothing(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {})
    assert wt_env(["up", str(worktree)], fake, main_repo).returncode == 0
    Path(fake["FAKE_STATE"], "calls").unlink()

    p, out = run_tests(worktree, fake, "whole")

    assert p.returncode == 0, p.stdout + p.stderr
    assert ups(fake) == [] and not any(c.startswith("volume") for c in calls(fake))
    assert wake_rows(out) == []


def test_ci_strategy_without_local_commands_starts_nothing(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {})
    project = json.loads((main_repo / ".ndf" / "project.json").read_text(encoding="utf-8"))
    project["test"]["strategy"] = "local-scoped-ci-whole"
    project["test"]["ci"] = {"check": "unit"}
    (main_repo / ".ndf" / "project.json").write_text(json.dumps(project), encoding="utf-8")

    run_tests(worktree, fake, "whole")

    assert ups(fake) == []


def test_a_detached_worktree_is_woken_like_a_branch_worktree(main_repo: Path, fake: dict) -> None:
    declare(main_repo, {})
    work = detached(main_repo, "rf1-work")

    env = container_reach.env_for(work)

    assert env["NDF_WORKTREE"] == str(work.resolve())
    assert [e["result"] for e in container_reach.wake_events()][-1] == "woken"


@pytest.mark.parametrize(
    ("extra", "want"),
    [
        ({"FAKE_UP_FAIL": "1"}, "image not found"),
        ({"FAKE_WAIT_FAIL": "1"}, "dependency db failed"),
        ({"FAKE_SEES_MAIN": "1"}, "この worktree ではない"),
    ],
    ids=["up", "wait", "still-unreachable"],
)
def test_a_failed_wake_stops_without_running(main_repo: Path, worktree: Path, fake: dict, extra: dict, want: str) -> None:
    declare(main_repo, {})

    p, out = run_tests(worktree, {**fake, **extra}, "whole")

    assert p.returncode == 2, p.stdout + p.stderr
    assert out["status"] == "stopped" and want in out["summary"] and "届かない" in out["summary"]
    assert "app" in out["summary"]
    assert not ran(fake)
    assert [r["result"] for r in wake_rows(out)] == ["failed"]
    assert want in wake_rows(out)[0]["reason"]


def test_no_free_slot_stops_with_the_service_and_the_reason(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {})
    rows = [
        {"worktree": f"/w/{i}", "slot": i, "environment": f"e{i}", "released_at": None, "assigned_at": "2026-11-06T00:00:00Z"}
        for i in range(64)
    ]
    registry_file = main_repo / ".git" / "ndf" / "worktree-registry.json"
    registry_file.parent.mkdir(parents=True)
    registry_file.write_text(json.dumps({"version": 1, "assignments": rows}), encoding="utf-8")

    p, out = run_tests(worktree, fake, "whole")

    assert p.returncode == 2 and not ran(fake)
    assert "サービス app" in out["summary"] and "空きスロットがありません" in out["summary"]
    assert ups(fake) == []


def test_a_wake_over_the_limit_stops_and_is_not_retried(
    main_repo: Path, worktree: Path, fake: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    declare(main_repo, {})
    monkeypatch.setenv("FAKE_UP_SLEEP", "30")
    monkeypatch.setattr(container_reach, "UP_TIMEOUT", 1)

    with pytest.raises(container_reach.Unreachable) as first:
        container_reach.env_for(worktree)
    with pytest.raises(container_reach.Unreachable) as second:
        container_reach.env_for(worktree)

    assert "1 秒で終わらなかった" in first.value.reason and second.value is first.value
    assert len(ups(fake)) == 1


def test_without_a_testenv_declaration_it_asks_for_the_declaration(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, None)

    p, out = run_tests(worktree, fake, "whole")

    assert p.returncode == 2
    assert "testenv の宣言が無い" in out["summary"] and "worktree-testenv.sh up" not in out["summary"]
    assert ups(fake) == []


def test_without_compose_files_it_names_the_missing_declaration(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {}, localenv=False)

    p, out = run_tests(worktree, fake, "whole")

    assert p.returncode == 2 and "localenv.compose_files" in out["summary"]
    assert ups(fake) == []


def test_without_a_runtime_nothing_is_woken(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {})

    p, out = run_tests(worktree, {**fake, "WT_DOCKER_COMMAND": "/nonexistent/docker"}, "whole")

    assert p.returncode == 2 and "見つからない" in out["summary"]
    assert calls(fake) == [] and registry_missing(main_repo)


def registry_missing(main: Path) -> bool:
    return not (main / ".git" / "ndf" / "worktree-registry.json").exists()


def test_without_a_container_declaration_the_runtime_is_not_called(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {}, suites=[{"name": "unit", "runner": "sh", "command": RUN}])

    assert container_reach.env_for(worktree) == {}
    assert calls(fake) == []


def test_reused_volumes_are_reported_and_do_not_stop(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {})
    Path(fake["FAKE_STATE"], "volumes").write_text("old_db\n", encoding="utf-8")

    p, out = run_tests(worktree, fake, "whole")

    assert p.returncode == 0 and ran(fake)
    assert wake_rows(out)[0]["reused_volumes"] == ["old_db"]


def test_an_unreadable_volume_list_does_not_stop(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {})

    p, out = run_tests(worktree, {**fake, "FAKE_VOLUME_LS_FAIL": "1"}, "whole")

    assert p.returncode == 0 and ran(fake)
    assert "reused_volumes" not in wake_rows(out)[0]


# --- 全体テストの報告 ----------------------------------------------------------------


def four_suites(failing: int | None) -> list[dict]:
    out = []
    for i in range(4):
        command = f'echo ran-{i} >> "$RAN_LOG"'
        if i == failing:
            command += f"; echo out-{i}; echo ghp_{'a' * 36}; exit 3"
        out.append({"name": f"s{i}", "runner": "sh", "command": command})
    return out


def test_the_failed_command_is_reported_with_its_log(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, None, suites=four_suites(2))

    _, out = run_tests(worktree, fake, "whole")

    assert "全体テスト 4 本のうち 1 本が落ちた（" in out["summary"]
    failed = [row for item in out["items"] for row in item.get("failed_commands", [])]
    assert len(failed) == 1 and failed[0]["code"] == 3
    assert "ran-2" in failed[0]["command"] and failed[0]["log"] == ".ndf/tmp/test-run/whole-2.log"
    assert "out-2" in failed[0]["tail"] and "ghp_aaa" not in failed[0]["tail"]


def test_a_line_cut_by_the_tail_is_not_reported(main_repo: Path, worktree: Path, fake: dict) -> None:
    """読み始めが認証ヘッダーの値の途中なら、切れた行を結果へ写さない（切れた行は伏せ字の規則に当たらない）。"""
    suites = four_suites(2)
    value = "v" * 3000 + "SECRETEND"
    suites[2]["command"] = suites[2]["command"].replace("; exit 3", f"; echo 'Authorization: Bearer {value}'; echo last-line; exit 3")
    declare(main_repo, None, suites=suites)

    _, out = run_tests(worktree, fake, "whole")

    failed = [row for item in out["items"] for row in item.get("failed_commands", [])]
    assert "last-line" in failed[0]["tail"] and "SECRETEND" not in failed[0]["tail"]


def test_the_summary_is_unchanged_when_everything_passes(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, None, suites=four_suites(None))

    p, out = run_tests(worktree, fake, "whole")

    assert p.returncode == 0
    assert out["summary"].startswith("全体テスト 4 本（") and out["summary"].endswith("秒 / 戦略 local-full）")


# --- detached HEAD -------------------------------------------------------------------


def test_a_detached_worktree_keeps_its_environment_when_head_moves(main_repo: Path, fake: dict) -> None:
    declare(main_repo, {"port_band": [20000, 29999], "port_roles": {"http": 0}})
    work = detached(main_repo, "rf1")

    first = wt_env(["env", str(work)], fake, main_repo)
    assert first.returncode == 0, first.stderr
    assigned = json.loads(first.stdout)
    assert assigned["branch"] == "" and assigned["environment"].startswith("main-wt-detached-rf1-")
    assert len(assigned["environment"]) <= 40

    (work / "b.txt").write_text("b\n", encoding="utf-8")
    git(work, "add", "b.txt")
    git(work, "commit", "-q", "-m", "move")
    again = json.loads(wt_env(["env", str(work)], fake, main_repo).stdout)
    assert (again["environment"], again["slot"]) == (assigned["environment"], assigned["slot"])

    assert wt_env(["up", str(work)], fake, main_repo).returncode == 0
    assert "COMPOSE_PROJECT_NAME=" + assigned["environment"] in wt_env(["compose-env", str(work)], fake, main_repo).stdout
    assert wt_env(["down", str(work)], fake, main_repo).returncode == 0


def test_two_detached_worktrees_of_a_commit_get_their_own_environment(main_repo: Path, fake: dict) -> None:
    declare(main_repo, {})
    a, b = detached(main_repo, "rf1"), detached(main_repo, "rf2")

    first = json.loads(wt_env(["env", str(a)], fake, main_repo).stdout)
    second = json.loads(wt_env(["env", str(b)], fake, main_repo).stdout)

    assert first["environment"] != second["environment"] and first["slot"] != second["slot"]


def test_the_same_directory_name_under_two_paths_gets_two_names(main_repo: Path, fake: dict) -> None:
    declare(main_repo, {})
    a, b = detached(main_repo, "rf1/work"), detached(main_repo, "rf2/work")

    first = json.loads(wt_env(["env", str(a)], fake, main_repo).stdout)
    second = json.loads(wt_env(["env", str(b)], fake, main_repo).stdout)

    assert first["environment"] != second["environment"]


def test_outside_a_repository_env_still_refuses(main_repo: Path, tmp_path: Path, fake: dict) -> None:
    declare(main_repo, {})
    outside = tmp_path / "outside"
    outside.mkdir()

    r = wt_env(["env", str(outside)], fake, main_repo)

    assert r.returncode == 1 and "ブランチを取れません" in r.stderr


# --- ボリュームの扱い（testenv.discard_volumes） ------------------------------------------


@pytest.mark.parametrize(
    ("value", "discards"),
    [(None, True), (True, True), (False, False), ("true", False), ("false", False), (0, False)],
)
def test_down_follows_the_declaration(main_repo: Path, worktree: Path, fake: dict, value: object, discards: bool) -> None:
    declare(main_repo, {"discard_volumes": value})
    assert wt_env(["env", str(worktree)], fake, main_repo).returncode == 0

    assert wt_env(["down", str(worktree)], fake, main_repo).returncode == 0

    down = [c for c in calls(fake) if " down" in c and c.startswith("compose")]
    assert len(down) == 1 and down[0].endswith(" down --volumes" if discards else " down")


def test_down_discards_volumes_when_the_key_is_absent(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {})
    wt_env(["env", str(worktree)], fake, main_repo)

    wt_env(["down", str(worktree)], fake, main_repo)

    assert any(c.endswith(" down --volumes") for c in calls(fake))


def test_the_volumes_flag_works_against_a_false_declaration(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {"discard_volumes": False})
    wt_env(["env", str(worktree)], fake, main_repo)

    wt_env(["down", str(worktree), "--volumes"], fake, main_repo)

    assert any(c.endswith(" down --volumes") for c in calls(fake))


def test_a_declaration_only_on_the_working_branch_does_not_keep_volumes(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {})
    write_declaration(worktree, json.dumps({"version": 1, "testenv": {"discard_volumes": False}}))
    wt_env(["env", str(worktree)], fake, main_repo)

    wt_env(["down", str(worktree)], fake, main_repo)

    assert any(c.endswith(" down --volumes") for c in calls(fake))


# --- 残ったボリュームと公開の判定 ---------------------------------------------------------


def tag_of(main: Path, worktree: Path) -> str | None:
    rows = [r for r in registry(main) if r["worktree"] == str(worktree.resolve()) and r["released_at"] is None]
    return rows[-1].get("golden_tag")


def leftover(fake: dict) -> None:
    Path(fake["FAKE_STATE"], "volumes").write_text("old_db\n", encoding="utf-8")


@pytest.mark.parametrize("assigned_first", [True, False], ids=["env-then-up", "up-assigns"])
def test_up_over_leftover_volumes_writes_no_tag(main_repo: Path, worktree: Path, fake: dict, assigned_first: bool) -> None:
    declare(main_repo, {"expose": EXPOSE})
    leftover(fake)
    if assigned_first:
        wt_env(["env", str(worktree)], fake, main_repo)

    r = wt_env(["up", str(worktree), "--tag", "golden-public"], fake, main_repo)

    assert r.returncode == 0, r.stderr
    assert ups(fake) and tag_of(main_repo, worktree) is None
    assert f"down {worktree.resolve()} --volumes" in r.stderr
    assert wt_env(["expose", str(worktree)], fake, main_repo).returncode == 1


def test_an_unreadable_volume_list_writes_no_tag(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {"expose": EXPOSE})

    r = wt_env(["up", str(worktree), "--tag", "golden-public"], {**fake, "FAKE_VOLUME_LS_FAIL": "1"}, main_repo)

    assert r.returncode == 0 and tag_of(main_repo, worktree) is None


def test_no_leftover_writes_the_tag_and_expose_passes(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {"expose": EXPOSE})

    wt_env(["up", str(worktree), "--tag", "golden-public"], fake, main_repo)

    assert tag_of(main_repo, worktree) == "golden-public"
    assert wt_env(["expose", str(worktree)], fake, main_repo).returncode == 0


def test_a_row_with_a_tag_is_overwritten_without_looking(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {})
    wt_env(["up", str(worktree), "--tag", "first"], fake, main_repo)
    leftover(fake)
    Path(fake["FAKE_STATE"], "calls").unlink()

    wt_env(["up", str(worktree), "--tag", "second"], fake, main_repo)

    assert tag_of(main_repo, worktree) == "second"
    assert not any(c.startswith("volume") for c in calls(fake))


def test_an_empty_tag_does_not_look_at_volumes_or_suggest_a_delete(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {})
    leftover(fake)

    r = wt_env(["up", str(worktree)], fake, main_repo)

    assert not any(c.startswith("volume") for c in calls(fake)) and "--volumes" not in r.stderr


def test_the_delete_is_not_suggested_when_expose_is_off(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {})
    leftover(fake)

    r = wt_env(["up", str(worktree), "--tag", "golden-public"], fake, main_repo)

    assert r.returncode == 0 and tag_of(main_repo, worktree) is None and "--volumes" not in r.stderr


def test_a_failed_down_drops_the_tag_for_a_recreated_worktree(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {"expose": EXPOSE})
    wt_env(["up", str(worktree), "--tag", "golden-public"], fake, main_repo)

    r = wt_env(["down", str(worktree)], {**fake, "FAKE_DOWN_FAIL": "1"}, main_repo)

    assert r.returncode == 1 and tag_of(main_repo, worktree) is None
    git(main_repo, "worktree", "remove", "--force", str(worktree))
    git(main_repo, "worktree", "add", "-q", str(worktree), "feature/x")
    leftover(fake)
    wt_env(["up", str(worktree), "--tag", "golden-public"], fake, main_repo)
    assert wt_env(["expose", str(worktree)], fake, main_repo).returncode == 1


# --- 破棄（discard） ---------------------------------------------------------------


def test_discard_without_an_assignment_does_not_call_the_runtime(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {})

    assert container_reach.discard_testenv(worktree) == (True, None)
    assert calls(fake) == []


def test_discard_downs_and_releases(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {})
    wt_env(["up", str(worktree)], fake, main_repo)

    assert container_reach.discard_testenv(worktree) == (True, None)

    assert "down: worktree present" in calls(fake)
    assert registry(main_repo)[-1]["released_at"]


def test_a_failed_discard_lets_the_worktree_go_with_the_name(
    main_repo: Path, worktree: Path, fake: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    declare(main_repo, {})
    assigned = json.loads(wt_env(["env", str(worktree)], fake, main_repo).stdout)
    monkeypatch.setenv("FAKE_DOWN_FAIL", "1")

    ok, why = container_reach.discard_testenv(worktree)

    assert ok and f"テスト環境を破棄できない: {assigned['environment']}" in why
    assert registry(main_repo)[-1]["released_at"] is None


def test_an_open_expose_is_closed_before_the_discard(main_repo: Path, worktree: Path, fake: dict) -> None:
    closed = Path(fake["FAKE_STATE"]) / "closed"
    declare(main_repo, {"expose": {**EXPOSE, "close_command": f"[ -f {fake['FAKE_STATE']}/running ] && touch {closed}"}})
    wt_env(["up", str(worktree), "--tag", "golden-public"], fake, main_repo)
    assert wt_env(["expose", str(worktree)], fake, main_repo).returncode == 0

    assert container_reach.discard_testenv(worktree) == (True, None)

    assert closed.exists()  # コンテナが動いているうちに閉じた
    assert registry(main_repo)[-1]["expose"]["closed_at"]


def test_a_failed_close_keeps_everything(main_repo: Path, worktree: Path, fake: dict) -> None:
    declare(main_repo, {"expose": {**EXPOSE, "close_command": "false"}})
    wt_env(["up", str(worktree), "--tag", "golden-public"], fake, main_repo)
    assert wt_env(["expose", str(worktree)], fake, main_repo).returncode == 0

    ok, why = container_reach.discard_testenv(worktree)

    row = registry(main_repo)[-1]
    assert not ok and row["environment"] in why and "https://wt0.t.example" in why
    assert f"worktree-testenv.sh unexpose {worktree.resolve()}" in why
    assert row["released_at"] is None and not any(" down" in c for c in calls(fake))


def test_no_expose_does_not_run_the_close_command(main_repo: Path, worktree: Path, fake: dict) -> None:
    marker = Path(fake["FAKE_STATE"]) / "closed"
    declare(main_repo, {"expose": {**EXPOSE, "close_command": f"touch {marker}"}})
    wt_env(["env", str(worktree)], fake, main_repo)

    container_reach.discard_testenv(worktree)

    assert not marker.exists()


# --- 作業用 worktree の置き場 ----------------------------------------------------------


@pytest.fixture()
def no_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(worktree_base.ENV, raising=False)


def test_a_container_declaration_places_worktrees_under_main(
    main_repo: Path, worktree: Path, fake: dict, no_base: None, tmp_path: Path
) -> None:
    declare(main_repo, {})
    want = main_repo.resolve() / ".worktrees" / ".ndf"

    sub = worktree / "sub"
    sub.mkdir()
    assert worktree_base.worktree_parent(main_repo) == want
    assert worktree_base.worktree_parent(worktree) == want
    assert worktree_base.worktree_parent(sub) == want


def test_the_explicit_base_wins_over_the_declaration(main_repo: Path, fake: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    declare(main_repo, {})
    monkeypatch.setenv(worktree_base.ENV, str(tmp_path / "base"))

    assert worktree_base.worktree_parent(main_repo) == tmp_path / "base"


def test_without_a_declaration_the_temp_place_stays(main_repo: Path, tmp_path: Path, no_base: None) -> None:
    import tempfile

    want = Path(tempfile.gettempdir()) / "ndf-worktrees"
    assert worktree_base.worktree_parent(main_repo) == want
    outside = tmp_path / "outside"
    outside.mkdir()
    assert worktree_base.worktree_parent(outside) == want


def test_the_dev_listing_skips_the_place(main_repo: Path, worktree: Path) -> None:
    detached(main_repo, "pr1")

    got = run_lib(f'wt_dev_worktrees "{main_repo}"', cwd=main_repo).stdout

    assert str(worktree) in got and ".ndf" not in got


def test_the_place_is_registered_in_git_exclude(main_repo: Path, tmp_path: Path, no_base: None) -> None:
    declare(main_repo, None)
    before = (main_repo / ".gitignore").read_text(encoding="utf-8") if (main_repo / ".gitignore").exists() else None
    target = main_repo / ".worktrees" / ".ndf" / "o--r" / "pr1"

    worktree_base.ensure_ignored(main_repo, target)
    worktree_base.ensure_ignored(main_repo, target)
    target.mkdir(parents=True)
    (target / "f").write_text("x", encoding="utf-8")

    exclude = (main_repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert exclude.splitlines().count(".worktrees/") == 1
    assert ".worktrees" not in git(main_repo, "status", "--porcelain").stdout
    after = (main_repo / ".gitignore").read_text(encoding="utf-8") if (main_repo / ".gitignore").exists() else None
    assert after == before


def test_a_place_outside_is_not_registered(main_repo: Path, tmp_path: Path, no_base: None) -> None:
    declare(main_repo, None)

    worktree_base.ensure_ignored(main_repo, tmp_path / "elsewhere")

    assert ".worktrees/" not in (main_repo / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines()


def test_a_failed_registration_raises(main_repo: Path, tmp_path: Path, no_base: None, monkeypatch: pytest.MonkeyPatch) -> None:
    declare(main_repo, None)
    monkeypatch.setattr(worktree_base, "add_exclude", lambda *_: False)

    with pytest.raises(OSError):
        worktree_base.ensure_ignored(main_repo, main_repo / ".worktrees" / ".ndf" / "pr1")
