"""個人の宣言（`.ndf/worktree.local.json`）の重ね合わせを検証する（#495 の AC11〜AC23）。

共有の宣言 `.ndf/worktree.json` は追跡し、個人の宣言は追跡しない。上書きできるのは
`localenv` / `testenv` / `follow_branch` の 3 つだけで、リポジトリの運用を決める項目
（起点・本番のチャネル・案内を出さないパス・外部への公開）は個人の宣言では変わらない
（設計の決定 7・決定 8）。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from worktree_helpers import (
    SCRIPTS_DIR,
    SESSION,
    git,
    run_lib,
    write_declaration,
)

TESTENV = SCRIPTS_DIR / "worktree-testenv.sh"
LOCALENV = SCRIPTS_DIR / "worktree-localenv.sh"


# --- 補助 -------------------------------------------------------------------


def write_local(main_repo: Path, body: str) -> Path:
    """`.ndf/worktree.local.json` を書く。"""
    ndf = main_repo / ".ndf"
    ndf.mkdir(parents=True, exist_ok=True)
    path = ndf / "worktree.local.json"
    path.write_text(body, encoding="utf-8")
    return path


def local_json(main_repo: Path, body: dict) -> Path:
    return write_local(main_repo, json.dumps(body))


def merged(main_repo: Path) -> dict:
    """重ね合わせた宣言を読む。`wt_declaration` は個人の宣言では失敗しない。"""
    got = run_lib(f'wt_declaration "{main_repo}"', cwd=main_repo)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


def run_script(script: Path, args: list[str], cwd: Path) -> dict:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    proc = subprocess.run(
        ["bash", str(script), *args],
        cwd=str(cwd), env=env, capture_output=True, text=True,
    )
    return {"rc": proc.returncode, "out": proc.stdout, "err": proc.stderr}


def run_session(cwd: Path) -> dict:
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    payload = {"session_id": "local1", "cwd": str(cwd), "hook_event_name": "SessionStart"}
    proc = subprocess.run(
        ["bash", str(SESSION)],
        input=json.dumps(payload), cwd=str(cwd), env=env,
        capture_output=True, text=True,
    )
    return {"rc": proc.returncode, "out": proc.stdout, "err": proc.stderr}


SHARED = {
    "version": 1,
    "base_branch": "develop",
    "production_branch": "main",
    "guard": {"allow_paths": ["notes/"]},
    "localenv": {"kind": "compose", "copy_from_main": [".env", "vendor"]},
    "testenv": {
        "port_band": [20000, 29999],
        "port_roles": {"http": 0, "db": 1, "mail": 2},
        "expose": {"enabled": False, "public_tag": "golden-public"},
    },
}


@pytest.fixture()
def shared(main_repo: Path) -> Path:
    # `wt_base_branch` は宣言した名前の実在を確かめるため、両方のブランチを作る。
    # 個人の宣言が 2 つを入れ替えても解決できる状態にしておかないと、AC15 の
    # 突き合わせが「どちらも解決できない」で一致してしまう。
    git(main_repo, "branch", "develop")
    write_declaration(main_repo, json.dumps(SHARED))
    return main_repo


# --- AC11: ポートの帯が個人の値になる ---------------------------------------


def test_local_port_band_is_used_for_assignment(shared: Path, worktree: Path) -> None:
    local_json(shared, {"version": 1, "testenv": {"port_band": [40000, 40999]}})

    result = run_script(TESTENV, ["env", str(worktree)], cwd=shared)

    assert result["rc"] == 0, result
    ports = json.loads(result["out"])["ports"]
    assert ports, result["out"]
    for port in ports.values():
        assert 40000 <= port <= 40999, ports


# --- AC12: オブジェクトは深く併合する ---------------------------------------


def test_objects_are_merged_deeply(shared: Path) -> None:
    local_json(shared, {"version": 1, "testenv": {"port_roles": {"db": 5}}})

    roles = merged(shared)["testenv"]["port_roles"]

    assert roles["db"] == 5
    assert roles["http"] == 0, roles
    assert roles["mail"] == 2, roles
    assert merged(shared)["testenv"]["port_band"] == [20000, 29999]


# --- AC13: 配列は置き換える -------------------------------------------------


def test_arrays_are_replaced_not_appended(shared: Path) -> None:
    local_json(shared, {"version": 1, "localenv": {"copy_from_main": ["node_modules"]}})

    assert merged(shared)["localenv"]["copy_from_main"] == ["node_modules"]
    assert merged(shared)["localenv"]["kind"] == "compose"


def test_local_symlink_target_is_merged_without_modification(shared: Path) -> None:
    target = shared / "personal-worktree.json"
    target.write_text(
        json.dumps({"version": 1, "localenv": {"copy_from_main": ["node_modules"]}}),
        encoding="utf-8",
    )
    before = target.read_bytes()
    (shared / ".ndf" / "worktree.local.json").symlink_to(target)

    body = merged(shared)

    assert body["localenv"] == {
        "kind": "compose",
        "copy_from_main": ["node_modules"],
    }
    assert target.read_bytes() == before


# --- AC14: 個人の宣言だけで追従を有効にできる -------------------------------


def test_follow_branch_can_be_enabled_locally(main_repo: Path, worktree: Path) -> None:
    write_declaration(main_repo, json.dumps({"version": 1}))
    local_json(main_repo, {"version": 1, "follow_branch": True})
    git(worktree, "commit", "-q", "--allow-empty", "-m", "work")
    expected = git(worktree, "rev-parse", "HEAD").stdout.strip()

    assert run_session(main_repo)["rc"] == 0
    assert git(main_repo, "rev-parse", "HEAD").stdout.strip() == expected
    symbolic = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"],
        cwd=str(main_repo), capture_output=True, text=True,
    )
    assert symbolic.returncode != 0, "detached HEAD であること"


def test_follow_enabled_reads_the_merged_declaration(main_repo: Path) -> None:
    write_declaration(main_repo, json.dumps({"version": 1}))
    local_json(main_repo, {"version": 1, "follow_branch": True})

    got = run_lib(
        f'decl=$(wt_declaration "{main_repo}"); wt_follow_enabled "$decl"; echo rc=$?',
        cwd=main_repo,
    )
    assert got.stdout.strip() == "rc=0", got.stdout


# --- AC15: リポジトリの運用は個人の宣言で変わらない -------------------------


OPERATIONAL_LOCAL = {
    "version": 1,
    "base_branch": "main",
    "production_branch": "develop",
    "guard": {"allow_paths": ["/"]},
    "future_field": {"a": 1},
}


def operational_values(main_repo: Path) -> str:
    got = run_lib(
        "\n".join(
            [
                f'wt_base_branch "{main_repo}"',
                f'wt_production_branch "{main_repo}"',
                f'decl=$(wt_declaration "{main_repo}"); wt_allow_paths "$decl"',
            ]
        ),
        cwd=main_repo,
    )
    assert got.returncode == 0, got.stderr
    return got.stdout


def test_operational_keys_are_not_overridable(shared: Path) -> None:
    before = operational_values(shared)
    assert "develop" in before and "notes/" in before, before

    local_json(shared, OPERATIONAL_LOCAL)

    assert operational_values(shared) == before


# --- AC16: 外部への公開は個人の宣言で有効にできない -------------------------


def test_expose_is_never_taken_from_the_local_declaration(shared: Path) -> None:
    local_json(
        shared,
        {"version": 1, "testenv": {"expose": {"enabled": True, "base_domain": "example.com"}}},
    )

    expose = merged(shared)["testenv"]["expose"]

    assert expose == {"enabled": False, "public_tag": "golden-public"}, expose


# --- AC18: 壊れた個人の宣言では共有の宣言だけで動く -------------------------


BROKEN_FORMS = ["broken_json", "empty", "top_level_array", "unsupported_version", "directory"]


def make_broken_local(main_repo: Path, form: str) -> None:
    path = main_repo / ".ndf" / "worktree.local.json"
    if form == "broken_json":
        write_local(main_repo, "{broken")
    elif form == "empty":
        write_local(main_repo, "")
    elif form == "top_level_array":
        write_local(main_repo, "[1]")
    elif form == "unsupported_version":
        local_json(main_repo, {"version": 2, "testenv": {"port_band": [40000, 40999]}})
    elif form == "directory":
        path.mkdir(parents=True)
    else:  # pragma: no cover - 綴りの誤りを黙って通さない
        raise AssertionError(form)


def observations(main_repo: Path, worktree: Path) -> tuple:
    session = run_session(main_repo)
    localenv = run_script(LOCALENV, ["mode", str(worktree)], cwd=main_repo)
    testenv = run_script(TESTENV, ["env", str(worktree)], cwd=main_repo)
    return (
        (session["rc"], session["out"]),
        (localenv["rc"], localenv["out"]),
        (testenv["rc"], json.loads(testenv["out"])["ports"] if testenv["out"].strip() else None),
    )


@pytest.mark.parametrize("form", BROKEN_FORMS)
def test_broken_local_declaration_falls_back_to_the_shared_one(
    shared: Path, worktree: Path, form: str
) -> None:
    baseline = observations(shared, worktree)

    make_broken_local(shared, form)

    assert observations(shared, worktree) == baseline


@pytest.mark.parametrize("form", BROKEN_FORMS)
def test_declaration_never_fails_on_a_broken_local_file(shared: Path, form: str) -> None:
    make_broken_local(shared, form)

    got = run_lib(f'wt_declaration "{shared}" >/dev/null; echo rc=$?', cwd=shared)

    assert got.stdout.strip() == "rc=0", got.stdout


# --- AC20: 型の合わない項目だけを落とす -------------------------------------


def test_only_the_well_typed_keys_are_applied(shared: Path) -> None:
    local_json(
        shared,
        {
            "version": 1,
            "testenv": "x",
            "follow_branch": "yes",
            "localenv": {"copy_from_main": ["z"]},
        },
    )

    body = merged(shared)

    assert body["localenv"]["copy_from_main"] == ["z"]
    assert body["testenv"]["port_band"] == [20000, 29999], body["testenv"]
    assert "follow_branch" not in body, body


def test_null_does_not_delete_a_shared_section(shared: Path) -> None:
    """個人の宣言から共有の節を消せる形にしない（決定 10）。"""
    local_json(shared, {"version": 1, "testenv": None, "localenv": None})

    body = merged(shared)

    assert body["testenv"]["port_band"] == [20000, 29999]
    assert body["localenv"]["kind"] == "compose"


# --- 状態と反映しない項目を返す関数 -----------------------------------------


def local_state(main_repo: Path) -> str:
    got = run_lib(f'wt_declaration_local_state "{main_repo}"', cwd=main_repo)
    assert got.returncode == 0, got.stderr
    return got.stdout.strip()


def local_ignored(main_repo: Path) -> list[str]:
    got = run_lib(f'wt_declaration_local_ignored "{main_repo}"', cwd=main_repo)
    assert got.returncode == 0, got.stderr
    return got.stdout.split()


def test_local_state_is_absent_without_the_file(shared: Path) -> None:
    assert local_state(shared) == "absent"


def test_local_state_is_present_for_a_readable_file(shared: Path) -> None:
    local_json(shared, {"version": 1, "testenv": {"port_band": [40000, 40999]}})
    assert local_state(shared) == "present"


@pytest.mark.parametrize("form", BROKEN_FORMS)
def test_local_state_is_unreadable_for_broken_forms(shared: Path, form: str) -> None:
    make_broken_local(shared, form)
    assert local_state(shared) == "unreadable"


def test_local_state_is_unused_without_a_shared_declaration(main_repo: Path) -> None:
    """共有の宣言が無いときは個人の宣言を使わない（決定 12）。"""
    local_json(main_repo, {"version": 1, "testenv": {"port_band": [40000, 40999]}})
    assert local_state(main_repo) == "unused"


def test_local_state_is_unused_when_the_shared_declaration_is_unreadable(main_repo: Path) -> None:
    write_declaration(main_repo, "{ not json")
    local_json(main_repo, {"version": 1})
    assert local_state(main_repo) == "unused"


def test_hook_is_silent_with_only_a_local_declaration(main_repo: Path, worktree: Path) -> None:
    """AC22: 共有の宣言が無ければ、個人の宣言だけでは仕組みが動かない。"""
    local_json(main_repo, {"version": 1, "follow_branch": True})
    git(worktree, "commit", "-q", "--allow-empty", "-m", "work")
    before = git(main_repo, "rev-parse", "HEAD").stdout.strip()

    result = run_session(main_repo)

    assert result["rc"] == 0, result
    assert result["out"].strip() == "", result["out"]
    assert git(main_repo, "rev-parse", "HEAD").stdout.strip() == before


def test_local_state_without_arguments_returns_error() -> None:
    got = run_lib("wt_declaration_local_state")
    assert got.returncode == 1 and got.stdout == ""


def test_ignored_lists_operational_and_expose_keys(shared: Path) -> None:
    local_json(
        shared,
        {
            "$schema": "https://example.com/worktree.schema.json",
            "version": 1,
            "base_branch": "main",
            "guard": {"allow_paths": ["/"]},
            "testenv": {"port_band": [40000, 40999], "expose": {"enabled": True}},
        },
    )

    assert local_ignored(shared) == ["base_branch", "guard", "testenv.expose"]


def test_ignored_lists_mistyped_keys(shared: Path) -> None:
    local_json(shared, {"version": 1, "testenv": "x", "follow_branch": "yes",
                        "localenv": {"copy_from_main": ["z"]}})

    assert local_ignored(shared) == ["follow_branch", "testenv"]


def test_ignored_is_empty_when_everything_applies(shared: Path) -> None:
    local_json(shared, {"version": 1, "follow_branch": True,
                        "testenv": {"port_band": [40000, 40999]}})

    assert local_ignored(shared) == []


@pytest.mark.parametrize("form", BROKEN_FORMS)
def test_ignored_is_silent_unless_the_state_is_present(shared: Path, form: str) -> None:
    make_broken_local(shared, form)
    assert local_ignored(shared) == []
