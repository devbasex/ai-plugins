"""テスト環境の採番・実行・公開の入口を検証する（受け入れ条件 39 ほか）。

起動を伴う検証は単体テストへ持ち込まない（詳細設計 06）。コンテナの起動と
外部公開の実物は手動確認が担う。ここで確かめるのは採番・タグの計算・
テスト実行の受け渡し・公開の拒否条件までである。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from worktree_helpers import SCRIPTS_DIR, git, init_repo, write_declaration

TESTENV = SCRIPTS_DIR / "worktree-testenv.sh"


def run(args: list[str], cwd: Path, env: dict | None = None) -> dict:
    env = dict(env) if env is not None else os.environ.copy()
    env["LC_ALL"] = "C"
    proc = subprocess.run(
        ["bash", str(TESTENV), *args],
        cwd=str(cwd), env=env, capture_output=True, text=True,
    )
    return {"rc": proc.returncode, "out": proc.stdout, "err": proc.stderr}


def declare(main_repo: Path, testenv: dict | None = None, localenv: dict | None = None) -> None:
    body: dict = {"version": 1}
    if localenv is not None:
        body["localenv"] = localenv
    if testenv is not None:
        body["testenv"] = testenv
    write_declaration(main_repo, json.dumps(body))


def registry(main_repo: Path) -> dict:
    path = main_repo / ".git" / "ndf" / "worktree-registry.json"
    return json.loads(path.read_text(encoding="utf-8"))


# --- 受け入れ条件 39: 宣言が無いリポジトリでは何もしない --------------------


@pytest.mark.parametrize("args", [["env"], ["tag"], ["stop"], ["unexpose"]])
def test_no_declaration_is_silent(main_repo: Path, worktree: Path, args: list[str]) -> None:
    result = run([*args, str(worktree)], cwd=main_repo)
    assert result["rc"] == 0, result
    assert result["out"].strip() == "", result["out"]


def test_no_testenv_section_is_silent(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, localenv={"kind": "compose"})
    result = run(["env", str(worktree)], cwd=main_repo)
    assert result["rc"] == 0, result
    assert result["out"].strip() == "", result["out"]


def test_missing_test_kind_is_silent(main_repo: Path, worktree: Path) -> None:
    """種類の宣言が無いリポジトリでは、テスト実行の仕組みが何もせずに終わる。"""
    declare(main_repo, testenv={"port_band": [20000, 29999]})
    result = run(["test", str(worktree), "--kind", "stateful"], cwd=main_repo)
    assert result["rc"] == 0, result
    assert result["out"].strip() == "", result["out"]


# --- 採番 -------------------------------------------------------------------


def test_env_outputs_name_slot_and_ports(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, testenv={"port_band": [20000, 29999],
                                "port_roles": {"http": 0, "db": 1}})
    result = run(["env", str(worktree)], cwd=main_repo)
    assert result["rc"] == 0, result
    payload = json.loads(result["out"])
    assert payload["slot"] == 0
    assert payload["branch"] == "feature/x"
    assert payload["environment"].startswith("main-wt-feature-x-")
    assert payload["ports"] == {"http": 20000, "db": 20001}


def test_env_without_a_port_band_keeps_an_assignment(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, testenv={})

    result = run(["env", str(worktree)], cwd=main_repo)

    assert result["rc"] == 0, result
    payload = json.loads(result["out"])
    assert payload["environment"].startswith("main-wt-feature-x-")
    assert payload["slot"] == 0
    assert payload["worktree"] == str(worktree.resolve())
    assert payload["branch"] == "feature/x"
    assert payload["ports"] == {}
    active = [row for row in registry(main_repo)["assignments"] if row["released_at"] is None]
    assert len(active) == 1, active


def test_env_is_stable_for_the_same_worktree(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, testenv={"port_band": [20000, 29999], "port_roles": {"http": 0}})
    first = json.loads(run(["env", str(worktree)], cwd=main_repo)["out"])
    second = json.loads(run(["env", str(worktree)], cwd=main_repo)["out"])
    assert first == second


def test_env_records_ports_in_the_registry(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, testenv={"port_band": [20000, 29999], "port_roles": {"http": 0}})
    run(["env", str(worktree)], cwd=main_repo)
    assert registry(main_repo)["assignments"][0]["ports"] == {"http": 20000}


def test_two_worktrees_get_different_ports(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, testenv={"port_band": [20000, 29999], "port_roles": {"http": 0}})
    second = main_repo / ".worktrees" / "fix" / "y"
    git(main_repo, "worktree", "add", "-q", "-b", "fix/y", str(second))

    a = json.loads(run(["env", str(worktree)], cwd=main_repo)["out"])
    b = json.loads(run(["env", str(second)], cwd=main_repo)["out"])

    assert a["slot"] != b["slot"]
    assert a["ports"]["http"] != b["ports"]["http"]
    assert a["environment"] != b["environment"]


# --- 基準のタグ -------------------------------------------------------------


def test_tag_is_derived_from_the_declared_paths(main_repo: Path, worktree: Path) -> None:
    (worktree / "database" / "migrations").mkdir(parents=True)
    (worktree / "database" / "migrations" / "001.sql").write_text("a\n", encoding="utf-8")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "add migration")
    declare(main_repo, testenv={"golden_tag_paths": ["database/migrations"]})

    first = run(["tag", str(worktree)], cwd=main_repo)
    assert first["rc"] == 0, first
    assert len(first["out"].strip()) == 12

    second = run(["tag", str(worktree)], cwd=main_repo)
    assert second["out"] == first["out"], "同じ内容なら同じ値"


def test_tag_changes_with_the_content(main_repo: Path, worktree: Path) -> None:
    (worktree / "database" / "migrations").mkdir(parents=True)
    (worktree / "database" / "migrations" / "001.sql").write_text("a\n", encoding="utf-8")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "add migration")
    declare(main_repo, testenv={"golden_tag_paths": ["database/migrations"]})
    before = run(["tag", str(worktree)], cwd=main_repo)["out"]

    (worktree / "database" / "migrations" / "002.sql").write_text("b\n", encoding="utf-8")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "add another")
    after = run(["tag", str(worktree)], cwd=main_repo)["out"]

    assert before != after


def test_tag_is_out_of_scope_without_declared_paths(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, testenv={"port_band": [20000, 29999]})
    result = run(["tag", str(worktree)], cwd=main_repo)
    assert result["rc"] == 2, result


def test_bake_reports_when_every_golden_volume_already_exists(
    main_repo: Path, worktree: Path,
) -> None:
    """同じタグの基準がすべて存在すると、新しく作らず 2 を返す。"""
    declare(main_repo, testenv={"golden_volumes": {"source-data": "golden-data"}})
    docker = main_repo.parent / "existing-volume-docker"
    docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    docker.chmod(0o755)
    env = os.environ.copy()
    env["WT_DOCKER_COMMAND"] = str(docker)

    result = run(["bake", str(worktree), "--tag", "same-tag"], cwd=main_repo, env=env)

    assert result["rc"] == 2, result
    assert result["out"] == "同じタグの基準が既にあります（1 件）\n", result


# --- テストの実行 -----------------------------------------------------------


def test_test_returns_the_command_exit_code(main_repo: Path, worktree: Path) -> None:
    """テストの成否を包み隠さない。"""
    declare(main_repo, testenv={"port_band": [20000, 29999],
                                "test_kinds": {"pure": {"run": "exit 3"}}})
    result = run(["test", str(worktree), "--kind", "pure"], cwd=main_repo)
    assert result["rc"] == 3, result


def test_test_passes_the_skip_reset_variables(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "test_kinds": {"stateful": {"run": "printf '%s' \"$TEST_SKIP_MIGRATE_FRESH\"",
                                    "skip_reset": {"TEST_SKIP_MIGRATE_FRESH": "true"}}},
    })
    result = run(["test", str(worktree), "--kind", "stateful"], cwd=main_repo)
    assert result["out"] == "true", result


def test_test_passes_the_base_url(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "port_roles": {"http": 0},
        "test_kinds": {"browser": {"run": "printf '%s' \"$PWK_BASE_URL\"",
                                   "base_url_env": "PWK_BASE_URL"}},
    })
    run(["env", str(worktree)], cwd=main_repo)
    result = run(["test", str(worktree), "--kind", "browser"], cwd=main_repo)
    assert result["out"] == "http://localhost:20000", result


def test_evidence_goes_under_the_worktree(main_repo: Path, worktree: Path) -> None:
    """証跡は作業ツリー配下へ固定する。共有の保管先へは送らない。"""
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "test_kinds": {"browser": {"run": "printf '%s' \"$PWK_OUT_DIR\"",
                                   "out_env": "PWK_OUT_DIR"}},
    })
    result = run(["test", str(worktree), "--kind", "browser"], cwd=main_repo)
    assert result["out"].startswith(str(worktree)), result


def test_evidence_path_can_be_given(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "test_kinds": {"browser": {"run": "printf '%s' \"$PWK_OUT_DIR\"",
                                   "out_env": "PWK_OUT_DIR"}},
    })
    out = worktree / "evidence" / "run1"
    result = run(["test", str(worktree), "--kind", "browser", "--out", str(out)], cwd=main_repo)
    assert result["out"] == str(out), result
    assert out.is_dir()


def test_test_runs_in_the_worktree(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, testenv={"port_band": [20000, 29999],
                                "test_kinds": {"pure": {"run": "pwd -P"}}})
    result = run(["test", str(worktree), "--kind", "pure"], cwd=main_repo)
    assert result["out"].strip() == str(worktree.resolve()), result


def test_test_respects_inuse_lock_and_releases_after_failure(main_repo: Path, worktree: Path) -> None:
    """同じ環境のロック取得失敗時は実行せず、失敗終了後もロックを解放する。"""
    import shutil

    record = worktree / "executed.txt"
    declare(
        main_repo,
        testenv={
            "port_band": [20000, 29999],
            "test_kinds": {
                "record": {"run": f"touch '{record}'"},
                "fail": {"run": "exit 4"},
            },
        },
    )
    environment = json.loads(run(["env", str(worktree)], cwd=main_repo)["out"])["environment"]

    lock = main_repo / ".git" / "ndf" / f"{environment}.inuse.d"
    lock.mkdir(parents=True)
    (lock / "held").touch()
    (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    (lock / "token").write_text("held\n", encoding="utf-8")

    result = run(["test", str(worktree), "--kind", "record"], cwd=main_repo)
    assert result["rc"] == 1, result
    assert not record.exists(), "実行中ロックがあるときはテストコマンドを実行しない"

    shutil.rmtree(lock)
    result = run(["test", str(worktree), "--kind", "fail"], cwd=main_repo)
    assert result["rc"] == 4, result
    assert not lock.exists(), "テストコマンド失敗後にも実行中ロックが残らない"



# --- 外部公開の拒否 ---------------------------------------------------------


def test_expose_is_refused_when_disabled(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, testenv={"port_band": [20000, 29999],
                                "expose": {"enabled": False}})
    result = run(["expose", str(worktree)], cwd=main_repo)
    assert result["rc"] == 1, result
    assert "enabled" in result["err"], result["err"]


def test_expose_is_refused_by_default(main_repo: Path, worktree: Path) -> None:
    """`expose` の宣言そのものが無ければ公開しない。"""
    declare(main_repo, testenv={"port_band": [20000, 29999]})
    result = run(["expose", str(worktree)], cwd=main_repo)
    assert result["rc"] == 1, result


@pytest.mark.parametrize(
    "expose_conf",
    [
        {"enabled": True, "base_domain": "example.test"},
        {"enabled": True, "public_tag": "golden-public"},
    ],
)
def test_expose_is_refused_when_public_tag_or_base_domain_is_missing(
    main_repo: Path, worktree: Path, expose_conf: dict
) -> None:
    marker = main_repo / "opened.txt"
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "expose": {**expose_conf, "open_command": f'printf "%s" "$NDF_EXPOSE_URL" > {marker}'},
    })
    run(["env", str(worktree)], cwd=main_repo)

    result = run(["expose", str(worktree)], cwd=main_repo)

    assert result["rc"] == 1, result
    assert "public_tag と base_domain が要ります" in result["err"], result["err"]
    assert not marker.exists(), "公開コマンドは実行されない"
    assert registry(main_repo)["assignments"][0]["expose"] is None


def test_expose_is_refused_when_the_golden_tag_differs(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "expose": {"enabled": True, "public_tag": "golden-public", "base_domain": "example.test"},
    })
    run(["env", str(worktree)], cwd=main_repo)
    result = run(["expose", str(worktree)], cwd=main_repo)
    assert result["rc"] == 1, result
    assert "一致しません" in result["err"], result["err"]


def test_expose_records_the_url_and_closing_time(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "expose": {"enabled": True, "public_tag": "golden-public",
                   "base_domain": "example.test", "ttl": "8h",
                   "open_command": "true", "close_command": "true"},
    })
    run(["env", str(worktree)], cwd=main_repo)
    # 公開を許す基準が載っている状態を作る。
    path = main_repo / ".git" / "ndf" / "worktree-registry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["assignments"][0]["golden_tag"] = "golden-public"
    path.write_text(json.dumps(data), encoding="utf-8")

    result = run(["expose", str(worktree)], cwd=main_repo)
    assert result["rc"] == 0, result
    assert result["out"].strip() == "https://wt0.example.test", result["out"]

    run(["unexpose", str(worktree)], cwd=main_repo)
    row = registry(main_repo)["assignments"][0]
    assert row["expose"]["url"] == "https://wt0.example.test", "URL は残す"
    assert row["expose"]["closed_at"] is not None


def test_expose_allows_only_one_at_a_time(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "expose": {"enabled": True, "public_tag": "golden-public",
                   "base_domain": "example.test", "open_command": "true"},
    })
    second = main_repo / ".worktrees" / "fix" / "y"
    git(main_repo, "worktree", "add", "-q", "-b", "fix/y", str(second))
    run(["env", str(worktree)], cwd=main_repo)
    run(["env", str(second)], cwd=main_repo)

    path = main_repo / ".git" / "ndf" / "worktree-registry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for row in data["assignments"]:
        row["golden_tag"] = "golden-public"
    path.write_text(json.dumps(data), encoding="utf-8")

    assert run(["expose", str(worktree)], cwd=main_repo)["rc"] == 0
    blocked = run(["expose", str(second)], cwd=main_repo)
    assert blocked["rc"] == 1, blocked
    assert "公開中" in blocked["err"], blocked["err"]


def test_down_releases_the_slot(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, testenv={"port_band": [20000, 29999], "port_roles": {"http": 0}})
    run(["env", str(worktree)], cwd=main_repo)
    result = run(["down", str(worktree)], cwd=main_repo)
    assert result["rc"] == 0, result
    rows = registry(main_repo)["assignments"]
    assert rows[0]["released_at"] is not None


def test_base_url_uses_the_declared_port_role(main_repo: Path, worktree: Path) -> None:
    """入口の役割名は宣言で決める。`http` 以外の名前を使うリポジトリがある。"""
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "port_roles": {"web": 3},
        "test_kinds": {"browser": {"run": "printf '%s' \"$BASE\"",
                                   "base_url_env": "BASE", "port_role": "web"}},
    })
    run(["env", str(worktree)], cwd=main_repo)
    result = run(["test", str(worktree), "--kind", "browser"], cwd=main_repo)
    assert result["out"] == "http://localhost:20003", result


def test_quote_in_a_kind_name_does_not_break_the_lookup(main_repo: Path, worktree: Path) -> None:
    """種類名やプロファイル名を jq の式へ埋め込まない。"""
    declare(main_repo, testenv={"port_band": [20000, 29999],
                                "test_kinds": {'weird"name': {"run": "exit 4"}}})
    result = run(["test", str(worktree), "--kind", 'weird"name'], cwd=main_repo)
    assert result["rc"] == 4, result


def test_quote_in_a_branch_name_does_not_break_the_registry(main_repo: Path) -> None:
    """ブランチ名やパスを jq のプログラムへ埋め込まない。"""
    target = main_repo / ".worktrees" / 'quote"branch'
    git(main_repo, "worktree", "add", "-q", "-b", 'quote"branch', str(target))
    declare(main_repo, testenv={"port_band": [20000, 29999], "port_roles": {"http": 0}})

    result = run(["env", str(target)], cwd=main_repo)

    assert result["rc"] == 0, result
    payload = json.loads(result["out"])
    assert payload["branch"] == 'quote"branch'
    assert payload["slot"] == 0


# --- reap -------------------------------------------------------------------


def set_last_used(main_repo: Path, worktree: Path, iso: str) -> None:
    path = main_repo / ".git" / "ndf" / "worktree-registry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for row in data["assignments"]:
        if row["worktree"] == str(worktree):
            row["last_used_at"] = iso
    path.write_text(json.dumps(data), encoding="utf-8")


def test_reap_requires_a_duration(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, testenv={"port_band": [20000, 29999]})
    result = run(["reap"], cwd=main_repo)
    assert result["rc"] == 1, result
    assert "--idle" in result["err"], result["err"]


@pytest.mark.parametrize("bad", ["soon", "45x", "-5m"])
def test_reap_rejects_a_bad_duration(main_repo: Path, bad: str) -> None:
    declare(main_repo, testenv={"port_band": [20000, 29999]})
    result = run(["reap", "--idle", bad], cwd=main_repo)
    assert result["rc"] == 1, result


def test_reap_leaves_recently_used_environments(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, testenv={"port_band": [20000, 29999]})
    run(["env", str(worktree)], cwd=main_repo)
    result = run(["reap", "--idle", "45m"], cwd=main_repo)
    assert result["rc"] == 0, result
    assert "停止します" not in result["out"], result["out"]


def test_reap_targets_idle_environments(main_repo: Path, worktree: Path) -> None:
    """`--idle` を超えて使われていないものだけを対象にする。"""
    declare(main_repo, testenv={"port_band": [20000, 29999]})
    run(["env", str(worktree)], cwd=main_repo)
    set_last_used(main_repo, worktree, "2020-01-01T00:00:00Z")

    result = run(["reap", "--idle", "45m"], cwd=main_repo)

    assert result["rc"] == 0, result
    # コンテナ実行系が無い環境ではここで終わる。あれば起動していないため止めない。


@pytest.mark.parametrize(
    ("value", "expected"),
    [("90", "90"), ("90s", "90"), ("45m", "2700"), ("2h", "7200"), ("1d", "86400")],
)
def test_duration_parsing(value: str, expected: str) -> None:
    from worktree_helpers import run_lib

    got = run_lib(f'wt_duration_seconds "{value}"')
    assert got.stdout.strip() == expected, got.stderr


# --- 引数の扱い -------------------------------------------------------------


def test_target_argument_is_used(main_repo: Path, worktree: Path) -> None:
    """対象を渡した呼び出しは、現在地ではなくその作業ツリーを使う。"""
    declare(main_repo, testenv={"port_band": [20000, 29999], "port_roles": {"http": 0}})
    result = run(["env", str(worktree)], cwd=main_repo)
    payload = json.loads(result["out"])
    assert payload["worktree"] == str(worktree.resolve()), payload
    assert payload["branch"] == "feature/x"


@pytest.mark.parametrize("option", ["--profile", "--kind", "--out", "--tag", "--idle"])
def test_option_without_a_value_fails(main_repo: Path, worktree: Path, option: str) -> None:
    """値を要するオプションが末尾に来ても、同じ引数を読み続けない。"""
    declare(main_repo, testenv={"port_band": [20000, 29999]})
    result = run(["env", str(worktree), option], cwd=main_repo)
    assert result["rc"] == 1, result
    assert "値が要ります" in result["err"], result["err"]


# --- ポートの帯 -------------------------------------------------------------


def test_port_beyond_the_band_is_refused(main_repo: Path, worktree: Path) -> None:
    """帯を出た番号は他の用途と衝突する。黙って使わない。"""
    declare(main_repo, testenv={"port_band": [20000, 20005], "port_roles": {"http": 0, "far": 9}})
    result = run(["env", str(worktree)], cwd=main_repo)
    assert result["rc"] == 1, result
    assert "帯を超えました" in result["err"], result["err"]


def test_port_inside_the_band_is_accepted(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, testenv={"port_band": [20000, 20005], "port_roles": {"http": 0}})
    result = run(["env", str(worktree)], cwd=main_repo)
    assert result["rc"] == 0, result


# --- 証跡の置き場所 ---------------------------------------------------------


def test_evidence_directory_is_excluded_from_tracking(main_repo: Path, worktree: Path) -> None:
    """証跡が追跡対象に入ると差分が埋まる。除外の設定へ登録する。

    git は空のディレクトリを追跡しない。証跡を実際に書いたうえで確かめる。
    """
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "test_kinds": {"browser": {"run": 'printf "x" > "$OUT/evidence.txt"', "out_env": "OUT"}},
    })
    result = run(["test", str(worktree), "--kind", "browser"], cwd=main_repo)
    assert result["rc"] == 0, result

    written = list((worktree / ".ndf-evidence").rglob("evidence.txt"))
    assert written, "証跡が書かれていること（書かれないと除外の検査にならない）"

    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(worktree), capture_output=True, text=True,
    )
    assert ".ndf-evidence" not in status.stdout, status.stdout


def test_evidence_exclusion_is_written_to_the_common_git_dir(main_repo: Path, worktree: Path) -> None:
    """作業ツリー固有の info/exclude は git が読まない。共通の側へ書く。"""
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "test_kinds": {"browser": {"run": 'printf "x" > "$OUT/evidence.txt"', "out_env": "OUT"}},
    })
    run(["test", str(worktree), "--kind", "browser"], cwd=main_repo)

    common = main_repo / ".git" / "info" / "exclude"
    assert ".ndf-evidence/" in common.read_text(encoding="utf-8"), common.read_text()


# --- 排他 -------------------------------------------------------------------


def test_lock_helpers_are_exclusive(tmp_path: Path) -> None:
    """`flock` が無い環境でも、ディレクトリの作成で排他できる。"""
    from worktree_helpers import run_lib

    lock = tmp_path / "a.lock"
    got = run_lib(
        f'wt_lock_acquire "{lock}" 1; echo first=$?; wt_lock_acquire "{lock}" 1; echo second=$?'
    )
    lines = got.stdout.split()
    assert lines[0] == "first=0", got.stdout
    assert lines[1] == "second=1", "同じロックは 2 度取れない"


def test_lock_is_released(tmp_path: Path) -> None:
    from worktree_helpers import run_lib

    lock = tmp_path / "b.lock"
    got = run_lib(
        f'wt_lock_acquire "{lock}" 1 && wt_lock_release "{lock}"; '
        f'wt_lock_acquire "{lock}" 1; echo again=$?'
    )
    assert "again=0" in got.stdout, got.stdout


def test_stale_lock_is_taken_over(tmp_path: Path) -> None:
    """持ち主が消えているロックは奪う。"""
    from worktree_helpers import run_lib

    lock = tmp_path / "c.lock"
    lock.mkdir()
    (lock / "pid").write_text("999999\n", encoding="utf-8")
    got = run_lib(f'wt_lock_acquire "{lock}" 2; echo rc=$?')
    assert "rc=0" in got.stdout, got.stdout


def test_held_lock_is_reported(tmp_path: Path) -> None:
    from worktree_helpers import run_lib

    lock = tmp_path / "d.lock"
    got = run_lib(f'wt_lock_acquire "{lock}" 1; wt_lock_is_held "{lock}"; echo held=$?')
    assert "held=0" in got.stdout, got.stdout


def test_failed_env_does_not_hold_a_slot(main_repo: Path, worktree: Path) -> None:
    """採番に失敗した呼び出しが、台帳に有効な行を残さない。"""
    declare(main_repo, testenv={"port_band": [20000, 20005], "port_roles": {"far": 9}})
    assert run(["env", str(worktree)], cwd=main_repo)["rc"] == 1

    rows = registry(main_repo)["assignments"]
    assert all(row["released_at"] is not None for row in rows), rows


def test_failed_env_keeps_an_existing_assignment(main_repo: Path, worktree: Path) -> None:
    """元からあった割り当てまで解放しない。"""
    declare(main_repo, testenv={"port_band": [20000, 29999], "port_roles": {"http": 0}})
    run(["env", str(worktree)], cwd=main_repo)

    declare(main_repo, testenv={"port_band": [20000, 20005], "port_roles": {"far": 9}})
    assert run(["env", str(worktree)], cwd=main_repo)["rc"] == 1

    rows = [r for r in registry(main_repo)["assignments"] if r["released_at"] is None]
    assert len(rows) == 1, rows


def test_lock_replaces_a_plain_file(tmp_path: Path) -> None:
    """ロックの位置にディレクトリ以外があれば、ロックとして成立しない。"""
    from worktree_helpers import run_lib

    lock = tmp_path / "e.lock"
    lock.write_text("stale\n", encoding="utf-8")
    got = run_lib(f'wt_lock_acquire "{lock}" 1; echo rc=$?')
    assert "rc=0" in got.stdout, got.stdout


def test_lock_without_a_pid_is_not_taken_immediately(tmp_path: Path) -> None:
    """印が無いロックは、作った直後の可能性がある。すぐには奪わない。"""
    from worktree_helpers import run_lib

    lock = tmp_path / "f.lock"
    lock.mkdir()
    got = run_lib(f'wt_lock_acquire "{lock}" 1; echo rc=$?')
    assert "rc=1" in got.stdout, got.stdout


def test_old_lock_without_a_pid_is_taken(tmp_path: Path) -> None:
    """印が無いまま古くなったロックは捨ててよい。"""
    import os
    import time
    from worktree_helpers import run_lib

    lock = tmp_path / "g.lock"
    lock.mkdir()
    old = time.time() - 3600
    os.utime(lock, (old, old))
    got = run_lib(f'wt_lock_acquire "{lock}" 1; echo rc=$?')
    assert "rc=0" in got.stdout, got.stdout


def test_takeover_does_not_break_a_fresh_lock(tmp_path: Path) -> None:
    """判定したものと違うロックになっていたら、取り除かない。"""
    from worktree_helpers import run_lib

    lock = tmp_path / "h.lock"
    lock.mkdir()
    (lock / "pid").write_text("999999\n", encoding="utf-8")
    (lock / "token").write_text("old-token\n", encoding="utf-8")

    # 判定に使う印だけを古い値にして、実体は新しいものへ差し替える。
    got = run_lib(
        f'_wt_lock_discard "{lock}" "seen-but-different" "tok"; echo rc=$?'
    )
    assert "rc=1" in got.stdout, got.stdout
    assert lock.is_dir(), "戻すか、取り直した側が持っている"


# --- issue #312: 保持の判定は陳腐化の判定の否定 ------------------------------


def make_old(path: Path) -> None:
    """更新時刻を 1 時間前にする。陳腐化の分数（5 分）を超える。"""
    import time

    old = time.time() - 3600
    os.utime(path, (old, old))


def test_an_old_lock_without_a_pid_is_not_held(tmp_path: Path) -> None:
    """AC1: 持ち主を書く前に落ちたロックは、古くなれば握られていない。"""
    from worktree_helpers import run_lib

    lock = tmp_path / "i.lock"
    lock.mkdir()
    make_old(lock)
    got = run_lib(
        f'ndf_lock_is_held "{lock}"; echo ndf=$?; wt_lock_is_held "{lock}"; echo wt=$?'
    )
    assert "ndf=1" in got.stdout, got.stdout
    assert "wt=1" in got.stdout, got.stdout


def lock_state(tmp_path: Path, state: str) -> Path:
    """`state` の名前が表す状態のロックを作る。"""
    lock = tmp_path / "state.lock"
    if state == "missing":
        return lock
    if state == "plain-file":
        lock.write_text("x\n", encoding="utf-8")
        return lock
    lock.mkdir()
    (lock / "token").write_text("tok\n", encoding="utf-8")
    if state == "empty-with-held":
        (lock / "held").write_text("", encoding="utf-8")
    elif state == "empty-old":
        make_old(lock)
    elif state == "alive-pid":
        (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    elif state == "alive-pid-old":
        (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        make_old(lock)
    elif state == "dead-pid":
        (lock / "pid").write_text("999999\n", encoding="utf-8")
    return lock


@pytest.mark.parametrize("state", ["empty", "empty-with-held"])
def test_a_fresh_lock_without_a_pid_is_held(tmp_path: Path, state: str) -> None:
    """AC2: 作った直後の空のロックは、取得の途中と区別できないため握られている。"""
    from worktree_helpers import run_lib

    lock = lock_state(tmp_path, state)
    got = run_lib(
        f'ndf_lock_is_held "{lock}"; echo ndf=$?; wt_lock_is_held "{lock}"; echo wt=$?'
    )
    assert "ndf=0" in got.stdout and "wt=0" in got.stdout, got.stdout


def test_a_living_owner_holds_the_lock_even_when_old(tmp_path: Path) -> None:
    """AC3: 持ち主が生きていれば、更新時刻が古くても握られている。"""
    from worktree_helpers import run_lib

    lock = lock_state(tmp_path, "alive-pid-old")
    got = run_lib(
        f'ndf_lock_is_held "{lock}"; echo ndf=$?; wt_lock_is_held "{lock}"; echo wt=$?'
    )
    assert "ndf=0" in got.stdout and "wt=0" in got.stdout, got.stdout


@pytest.mark.parametrize("state", ["dead-pid", "missing", "plain-file", "empty-arg"])
def test_a_lock_without_a_living_owner_is_not_held(tmp_path: Path, state: str) -> None:
    """AC4: 持ち主が消えた・無い・ディレクトリでない・空の引数は握られていない。"""
    from worktree_helpers import run_lib

    arg = "" if state == "empty-arg" else str(lock_state(tmp_path, state))
    got = run_lib(
        f'ndf_lock_is_held "{arg}"; echo ndf=$?; wt_lock_is_held "{arg}"; echo wt=$?'
    )
    assert "ndf=1" in got.stdout and "wt=1" in got.stdout, got.stdout


@pytest.mark.parametrize(
    "state", ["empty", "empty-with-held", "empty-old", "alive-pid", "alive-pid-old", "dead-pid"],
)
def test_held_is_the_negation_of_stale(tmp_path: Path, state: str) -> None:
    """AC5: 判定の規則は 1 つ。保持の判定は陳腐化の判定の否定と一致する。"""
    from worktree_helpers import run_lib

    lock = lock_state(tmp_path, state)
    got = run_lib(
        f'ndf_lock_is_held "{lock}"; echo held=$?; '
        f'_ndf_lock_is_stale "{lock}" "$(cat "{lock}/token")"; echo stale=$?'
    )
    values = dict(line.split("=") for line in got.stdout.split())
    assert values["held"] != values["stale"], got.stdout


def test_held_check_leaves_the_caller_shell_alone(tmp_path: Path) -> None:
    """AC6: 呼び出し側の `$-` に `C` を残さず、標準出力へ書かない。"""
    from worktree_helpers import run_lib

    lock = lock_state(tmp_path, "empty-old")
    got = run_lib(
        f'out=$(ndf_lock_is_held "{lock}"; wt_lock_is_held "{lock}"); '
        'case "$-" in *C*) echo noclobber=yes ;; *) echo noclobber=no ;; esac; '
        'printf "out=[%s]\\n" "$out"'
    )
    assert "noclobber=no" in got.stdout, got.stdout
    assert "out=[]" in got.stdout, got.stdout


@pytest.mark.parametrize("bad", ["/tmp/elsewhere", "../outside", "evidence/../../outside"])
def test_evidence_outside_the_worktree_is_refused(main_repo: Path, worktree: Path, bad: str) -> None:
    """外から渡された置き場所も、作業ツリーの中に収まるかを確かめる。"""
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "test_kinds": {"browser": {"run": "true", "out_env": "OUT"}},
    })
    result = run(["test", str(worktree), "--kind", "browser", "--out", bad], cwd=main_repo)
    assert result["rc"] == 1, result
    assert "作業ツリーの外" in result["err"], result["err"]


def test_evidence_inside_the_worktree_is_accepted(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "test_kinds": {"browser": {"run": "printf '%s' \"$OUT\"", "out_env": "OUT"}},
    })
    out = worktree / "evidence" / "run1"
    result = run(["test", str(worktree), "--kind", "browser", "--out", str(out)], cwd=main_repo)
    assert result["rc"] == 0, result
    assert result["out"] == str(out), result


def golden(main_repo: Path, worktree: Path) -> None:
    """公開を許す基準が載っている状態にする。"""
    path = main_repo / ".git" / "ndf" / "worktree-registry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for row in data["assignments"]:
        row["golden_tag"] = "golden-public"
    path.write_text(json.dumps(data), encoding="utf-8")


def test_expose_without_a_command_records_nothing(main_repo: Path, worktree: Path) -> None:
    """口を開ける手段が無ければ、公開したことにしない。"""
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "expose": {"enabled": True, "public_tag": "golden-public",
                   "base_domain": "example.test"},
    })
    run(["env", str(worktree)], cwd=main_repo)
    golden(main_repo, worktree)

    result = run(["expose", str(worktree)], cwd=main_repo)

    assert result["rc"] == 2, result
    assert "open_command" in result["err"], result["err"]
    assert registry(main_repo)["assignments"][0]["expose"] is None


def test_expose_runs_the_declared_command(main_repo: Path, worktree: Path) -> None:
    marker = main_repo / "opened.txt"
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "expose": {"enabled": True, "public_tag": "golden-public",
                   "base_domain": "example.test",
                   "open_command": f'printf "%s" "$NDF_EXPOSE_URL" > {marker}'},
    })
    run(["env", str(worktree)], cwd=main_repo)
    golden(main_repo, worktree)

    result = run(["expose", str(worktree)], cwd=main_repo)

    assert result["rc"] == 0, result
    assert marker.read_text() == "https://wt0.example.test"


def test_expose_rolls_back_when_the_command_fails(main_repo: Path, worktree: Path) -> None:
    """口を開けられなければ記録を戻す。残すと次の公開が拒まれ続ける。"""
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "expose": {"enabled": True, "public_tag": "golden-public",
                   "base_domain": "example.test", "open_command": "exit 1"},
    })
    run(["env", str(worktree)], cwd=main_repo)
    golden(main_repo, worktree)

    result = run(["expose", str(worktree)], cwd=main_repo)

    assert result["rc"] == 1, result
    row = registry(main_repo)["assignments"][0]
    assert row["expose"]["closed_at"] is not None, "開いたままにしない"


def test_unexpose_runs_the_declared_command(main_repo: Path, worktree: Path) -> None:
    marker = main_repo / "closed.txt"
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "expose": {"enabled": True, "public_tag": "golden-public",
                   "base_domain": "example.test", "open_command": "true",
                   "close_command":
                       f'printf "%s|%s|%s|%s" "$NDF_EXPOSE_URL" "$NDF_EXPOSE_HOST" '
                       f'"$NDF_EXPOSE_ENVIRONMENT" "$NDF_EXPOSE_SLOT" > {marker}'},
    })
    run(["env", str(worktree)], cwd=main_repo)
    golden(main_repo, worktree)
    run(["expose", str(worktree)], cwd=main_repo)

    run(["unexpose", str(worktree)], cwd=main_repo)

    url, host, environment, slot = marker.read_text().split("|")
    assert url == "https://wt0.example.test"
    assert host == "wt0.example.test"
    assert environment.startswith("main-wt-feature-x-"), environment
    assert slot == "0"


def test_env_name_keeps_the_digest_for_long_branches(main_repo: Path) -> None:
    """40 文字で切っても要約値を落とさない。落とすと先頭が同じ名前で衝突する。"""
    from worktree_helpers import run_lib

    long_a = "feature/" + "a" * 80
    long_b = "feature/" + "a" * 79 + "b"
    a = run_lib(f'wt_env_name "{main_repo}" "{long_a}"', cwd=main_repo).stdout.strip()
    b = run_lib(f'wt_env_name "{main_repo}" "{long_b}"', cwd=main_repo).stdout.strip()

    assert len(a) == 40 and len(b) == 40
    assert a != b, (a, b)


def test_unexpose_keeps_the_record_when_closing_fails(main_repo: Path, worktree: Path) -> None:
    """閉じられていないのに台帳だけ閉じると、口が開いたまま次の公開が通る。"""
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "expose": {"enabled": True, "public_tag": "golden-public",
                   "base_domain": "example.test",
                   "open_command": "true", "close_command": "exit 1"},
    })
    run(["env", str(worktree)], cwd=main_repo)
    golden(main_repo, worktree)
    assert run(["expose", str(worktree)], cwd=main_repo)["rc"] == 0

    result = run(["unexpose", str(worktree)], cwd=main_repo)

    assert result["rc"] == 1, result
    assert registry(main_repo)["assignments"][0]["expose"]["closed_at"] is None


def test_next_expose_is_refused_while_a_close_failed(main_repo: Path, worktree: Path) -> None:
    """閉じられていない公開が残っている間は、次の公開を通さない。"""
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "expose": {"enabled": True, "public_tag": "golden-public",
                   "base_domain": "example.test",
                   "open_command": "true", "close_command": "exit 1"},
    })
    second = main_repo / ".worktrees" / "fix" / "y"
    git(main_repo, "worktree", "add", "-q", "-b", "fix/y", str(second))
    run(["env", str(worktree)], cwd=main_repo)
    run(["env", str(second)], cwd=main_repo)
    golden(main_repo, worktree)

    assert run(["expose", str(worktree)], cwd=main_repo)["rc"] == 0
    assert run(["unexpose", str(worktree)], cwd=main_repo)["rc"] == 1

    blocked = run(["expose", str(second)], cwd=main_repo)
    assert blocked["rc"] == 1, blocked


def test_expose_is_idempotent(main_repo: Path, worktree: Path) -> None:
    """既に開いているなら、開ける手段を再実行しない。"""
    counter = main_repo / "opens.txt"
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "expose": {"enabled": True, "public_tag": "golden-public",
                   "base_domain": "example.test",
                   "open_command": f'printf "x" >> {counter}'},
    })
    run(["env", str(worktree)], cwd=main_repo)
    golden(main_repo, worktree)

    first = run(["expose", str(worktree)], cwd=main_repo)
    second = run(["expose", str(worktree)], cwd=main_repo)

    assert first["rc"] == 0 and second["rc"] == 0
    assert first["out"] == second["out"]
    assert counter.read_text() == "x", "2 度目は開ける手段を呼ばない"


def test_unexpose_after_down_still_knows_the_environment(main_repo: Path, worktree: Path) -> None:
    """`down` で割り当てを解放した後でも、閉じる対象を特定できる。"""
    marker = main_repo / "closed2.txt"
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "expose": {"enabled": True, "public_tag": "golden-public",
                   "base_domain": "example.test", "open_command": "true",
                   "close_command":
                       f'printf "%s|%s" "$NDF_EXPOSE_ENVIRONMENT" "$NDF_EXPOSE_SLOT" > {marker}'},
    })
    run(["env", str(worktree)], cwd=main_repo)
    golden(main_repo, worktree)
    run(["expose", str(worktree)], cwd=main_repo)
    run(["down", str(worktree)], cwd=main_repo)

    result = run(["unexpose", str(worktree)], cwd=main_repo)

    assert result["rc"] == 0, result
    environment, slot = marker.read_text().split("|")
    assert environment.startswith("main-wt-feature-x-"), environment
    assert slot == "0"


def test_unexpose_without_an_open_record_changes_nothing(main_repo: Path, worktree: Path) -> None:
    """開いている公開記録が 0 件のとき、何も変更せず正常終了する。"""
    marker = main_repo / "closed.txt"
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "expose": {"enabled": True, "public_tag": "golden-public",
                   "base_domain": "example.test", "open_command": "true",
                   "close_command": f"touch {marker}"},
    })
    run(["env", str(worktree)], cwd=main_repo)
    before = registry(main_repo)["assignments"]

    result = run(["unexpose", str(worktree)], cwd=main_repo)

    assert result["rc"] == 0, result
    assert not marker.exists(), "閉じるコマンドは実行されない"
    after = registry(main_repo)["assignments"]
    assert after == before, (before, after)
    assert after[0]["expose"] is None


def test_normalize_does_not_expand_globs(tmp_path: Path) -> None:
    """`*` や `?` を含むパスが、実在するファイルの名前へ化けない。"""
    from worktree_helpers import run_lib

    (tmp_path / "aaa").write_text("x", encoding="utf-8")
    (tmp_path / "bbb").write_text("x", encoding="utf-8")
    got = run_lib(f'wt_normalize_path "*" "{tmp_path}"')
    assert got.stdout.strip() == f"{tmp_path}/*", got.stdout


def stub_docker(main_repo: Path, dump: Path) -> Path:
    """環境変数と引数を書き出すだけの偽のコンテナ実行系。"""
    stub = main_repo.parent / "fake-docker"
    stub.write_text(
        "#!/bin/sh\n"
        f'env | grep "^NDF_" | sort > "{dump}"\n'
        f'printf "%s\\n" "$*" >> "{dump}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


def test_up_passes_the_numbered_values_to_compose(main_repo: Path, worktree: Path) -> None:
    """採番した値を定義へ渡す。渡さないと作業ツリーごとの分離が効かない。"""
    (worktree / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    declare(
        main_repo,
        testenv={"port_band": [20000, 29999], "port_roles": {"http": 0, "db": 1},
                 "shared_network": "ndf-shared"},
        localenv={"kind": "compose", "compose_files": ["docker-compose.yml"]},
    )
    dump = main_repo.parent / "compose-env.txt"
    stub = stub_docker(main_repo, dump)

    env = os.environ.copy()
    env["WT_DOCKER_COMMAND"] = str(stub)
    proc = subprocess.run(
        ["bash", str(TESTENV), "up", str(worktree)],
        cwd=str(main_repo), env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc

    body = dump.read_text()
    assert "NDF_PORT_HTTP=20000" in body, body
    assert "NDF_PORT_DB=20001" in body, body
    assert "NDF_SLOT=0" in body, body
    assert "NDF_SHARED_NETWORK=ndf-shared" in body, body
    assert "NDF_ENVIRONMENT=main-wt-feature-x-" in body, body
    assert "compose -p main-wt-feature-x-" in body, body


def test_up_passes_the_profile_services_to_compose(main_repo: Path, worktree: Path) -> None:
    """`--profile` を渡すと、その profile の宣言のサービスを宣言順で compose up へ渡す。"""
    (worktree / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    declare(
        main_repo,
        testenv={"port_band": [20000, 29999], "port_roles": {"http": 0},
                 "profiles": {"minimal": ["web", "api"]}},
        localenv={"kind": "compose", "compose_files": ["docker-compose.yml"]},
    )
    dump = main_repo.parent / "compose-profile.txt"
    stub = stub_docker(main_repo, dump)

    env = os.environ.copy()
    env["WT_DOCKER_COMMAND"] = str(stub)
    proc = subprocess.run(
        ["bash", str(TESTENV), "up", str(worktree), "--profile", "minimal"],
        cwd=str(main_repo), env=env, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc

    body = dump.read_text()
    assert body.rstrip().endswith("up -d web api"), body


def test_role_names_become_upper_case_variables(main_repo: Path, worktree: Path) -> None:
    (worktree / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    declare(
        main_repo,
        testenv={"port_band": [20000, 29999], "port_roles": {"object-store": 4}},
        localenv={"kind": "compose", "compose_files": ["docker-compose.yml"]},
    )
    dump = main_repo.parent / "compose-env2.txt"
    stub = stub_docker(main_repo, dump)

    env = os.environ.copy()
    env["WT_DOCKER_COMMAND"] = str(stub)
    subprocess.run(
        ["bash", str(TESTENV), "up", str(worktree)],
        cwd=str(main_repo), env=env, capture_output=True, text=True,
    )

    assert "NDF_PORT_OBJECT_STORE=20004" in dump.read_text(), dump.read_text()


@pytest.mark.parametrize("bad", ["../outside.yml", "/etc/compose.yml", "a/../../outside.yml"])
def test_compose_files_outside_the_worktree_are_refused(main_repo: Path, worktree: Path, bad: str) -> None:
    """宣言に `../` が入ると、作業ツリーの外の定義を読み込む。"""
    declare(
        main_repo,
        testenv={"port_band": [20000, 29999]},
        localenv={"kind": "compose", "compose_files": [bad]},
    )
    dump = main_repo.parent / "never.txt"
    stub = stub_docker(main_repo, dump)

    env = os.environ.copy()
    env["WT_DOCKER_COMMAND"] = str(stub)
    proc = subprocess.run(
        ["bash", str(TESTENV), "up", str(worktree)],
        cwd=str(main_repo), env=env, capture_output=True, text=True,
    )

    assert proc.returncode == 1, proc
    assert "作業ツリーの外" in proc.stderr, proc.stderr
    assert not dump.exists(), "定義を読み込まない"


def test_compose_file_symlink_is_refused(main_repo: Path, worktree: Path, tmp_path: Path) -> None:
    """作業ツリーの中の symlink が外を指していると、実行系はその先を読む。"""
    outside = tmp_path / "outside-compose.yml"
    outside.write_text("services: {}\n", encoding="utf-8")
    (worktree / "docker-compose.yml").symlink_to(outside)
    declare(
        main_repo,
        testenv={"port_band": [20000, 29999]},
        localenv={"kind": "compose", "compose_files": ["docker-compose.yml"]},
    )
    dump = main_repo.parent / "never2.txt"
    stub = stub_docker(main_repo, dump)

    env = os.environ.copy()
    env["WT_DOCKER_COMMAND"] = str(stub)
    proc = subprocess.run(
        ["bash", str(TESTENV), "up", str(worktree)],
        cwd=str(main_repo), env=env, capture_output=True, text=True,
    )

    assert proc.returncode == 1, proc
    assert "symlink" in proc.stderr, proc.stderr
    assert not dump.exists(), "定義を読み込まない"


def test_down_keeps_the_slot_when_it_fails(main_repo: Path, worktree: Path) -> None:
    """破棄に失敗したままスロットを返すと、資源が残ったまま同じ番号が渡る。"""
    (worktree / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    declare(
        main_repo,
        testenv={"port_band": [20000, 29999], "port_roles": {"http": 0}},
        localenv={"kind": "compose", "compose_files": ["docker-compose.yml"]},
    )
    run(["env", str(worktree)], cwd=main_repo)

    failing = main_repo.parent / "failing-docker"
    failing.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    failing.chmod(0o755)
    env = os.environ.copy()
    env["WT_DOCKER_COMMAND"] = str(failing)
    proc = subprocess.run(
        ["bash", str(TESTENV), "down", str(worktree)],
        cwd=str(main_repo), env=env, capture_output=True, text=True,
    )

    assert proc.returncode == 1, proc
    rows = registry(main_repo)["assignments"]
    assert rows[0]["released_at"] is None, "解放しない"


def test_down_releases_the_slot_when_it_succeeds(main_repo: Path, worktree: Path) -> None:
    (worktree / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    declare(
        main_repo,
        testenv={"port_band": [20000, 29999], "port_roles": {"http": 0}},
        localenv={"kind": "compose", "compose_files": ["docker-compose.yml"]},
    )
    run(["env", str(worktree)], cwd=main_repo)

    dump = main_repo.parent / "down-env.txt"
    stub = stub_docker(main_repo, dump)
    env = os.environ.copy()
    env["WT_DOCKER_COMMAND"] = str(stub)
    proc = subprocess.run(
        ["bash", str(TESTENV), "down", str(worktree)],
        cwd=str(main_repo), env=env, capture_output=True, text=True,
    )

    assert proc.returncode == 0, proc
    assert registry(main_repo)["assignments"][0]["released_at"] is not None


@pytest.mark.parametrize(
    ("given", "expected"),
    [("ai-plugins", "ai-plugins"), ("My_Repo", "my_repo"),
     ("Carmo System!", "carmosystem"), ("___x", "x")],
)
def test_compose_project_normalization(given: str, expected: str) -> None:
    """実行系は名前を小文字へ揃え、`a-z0-9_-` 以外を落としてから使う。"""
    from worktree_helpers import run_lib

    got = run_lib(f'wt_compose_project "{given}"')
    assert got.stdout.strip() == expected, got.stderr


def test_lock_timeout_is_measured_in_real_time(tmp_path: Path) -> None:
    """刻みが 0.1 秒か 1 秒かで待ち時間が 10 倍変わらない。"""
    import time
    from worktree_helpers import run_lib

    lock = tmp_path / "t.lock"
    lock.mkdir()
    (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    (lock / "token").write_text("held\n", encoding="utf-8")

    started = time.monotonic()
    got = run_lib(f'wt_lock_acquire "{lock}" 2; echo rc=$?')
    elapsed = time.monotonic() - started

    assert "rc=1" in got.stdout, got.stdout
    assert elapsed < 6, f"上限 2 秒の待ちに {elapsed:.1f} 秒かかった"


def test_compose_file_under_a_symlinked_directory_is_refused(
    main_repo: Path, worktree: Path, tmp_path: Path
) -> None:
    """途中のディレクトリが symlink で外を指していても読み込ませない。"""
    outside = tmp_path / "outside-compose"
    outside.mkdir()
    (outside / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (worktree / "compose").symlink_to(outside)
    declare(
        main_repo,
        testenv={"port_band": [20000, 29999]},
        localenv={"kind": "compose", "compose_files": ["compose/docker-compose.yml"]},
    )
    dump = main_repo.parent / "never3.txt"
    stub = stub_docker(main_repo, dump)

    env = os.environ.copy()
    env["WT_DOCKER_COMMAND"] = str(stub)
    proc = subprocess.run(
        ["bash", str(TESTENV), "up", str(worktree)],
        cwd=str(main_repo), env=env, capture_output=True, text=True,
    )

    assert proc.returncode == 1, proc
    assert "作業ツリーの外" in proc.stderr, proc.stderr
    assert not dump.exists(), "定義を読み込まない"


# --- issue #173: 実機確認で見つかった事象 -----------------------------------


def stub_docker_with_running_container(main_repo: Path, dump: Path) -> Path:
    """`ps` へ稼働中のコンテナを 1 件返し、それ以外は環境変数と引数を書き出す。"""
    stub = main_repo.parent / "fake-docker-running"
    stub.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "ps" ]; then printf "container-id\\n"; exit 0; fi\n'
        f'env | grep "^NDF_" | sort > "{dump}"\n'
        f'printf "%s\\n" "$*" >> "{dump}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return stub


def test_reap_stops_the_idle_environment_with_its_slot(main_repo: Path, worktree: Path) -> None:
    """停止の対象を見つけたとき、割り当てのスロットを渡して止める。

    `compose_env` はスロットを読む。読ませずに `compose` を呼ぶと、未定義の
    変数を参照した時点で終了し、コンテナが動いたまま残る。
    """
    (worktree / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    declare(
        main_repo,
        testenv={"port_band": [20000, 29999], "port_roles": {"http": 0}},
        localenv={"kind": "compose", "compose_files": ["docker-compose.yml"]},
    )
    run(["env", str(worktree)], cwd=main_repo)
    set_last_used(main_repo, worktree, "2020-01-01T00:00:00Z")

    dump = main_repo.parent / "reap-env.txt"
    env = os.environ.copy()
    env["WT_DOCKER_COMMAND"] = str(stub_docker_with_running_container(main_repo, dump))
    proc = subprocess.run(
        ["bash", str(TESTENV), "reap", "--idle", "45m"],
        cwd=str(main_repo), env=env, capture_output=True, text=True,
    )

    assert proc.returncode == 0, proc
    assert "停止します" in proc.stdout, proc.stdout
    body = dump.read_text()
    assert "NDF_SLOT=0" in body, body
    assert "NDF_PORT_HTTP=20000" in body, body
    assert body.rstrip().endswith("stop"), body


@pytest.mark.parametrize(
    ("args", "option"),
    [(["test"], "--kind"), (["bake"], "--tag")],
)
def test_a_missing_option_is_reported_as_text(
    main_repo: Path, worktree: Path, args: list[str], option: str
) -> None:
    """案内の本文が `--` で始まっても、書式指定として解釈されない。"""
    declare(main_repo, testenv={"port_band": [20000, 29999],
                                "test_kinds": {"pure": {"run": "true"}}})
    result = run([*args, str(worktree)], cwd=main_repo)
    assert result["rc"] == 1, result
    assert option in result["err"], result["err"]
    assert "invalid option" not in result["err"], result["err"]


def test_the_declaration_and_registry_come_from_the_target(
    tmp_path: Path, main_repo: Path, worktree: Path
) -> None:
    """別のリポジトリから実行しても、対象側の宣言・台帳・帯で動く。"""
    declare(main_repo, testenv={"port_band": [20000, 29999], "port_roles": {"http": 0}})
    other = init_repo(tmp_path / "other")
    write_declaration(
        other,
        json.dumps({"version": 1,
                    "testenv": {"port_band": [30000, 30199], "port_roles": {"http": 0}}}),
    )

    result = run(["env", str(worktree)], cwd=other)

    assert result["rc"] == 0, result
    payload = json.loads(result["out"])
    assert payload["ports"]["http"] == 20000, payload
    assert payload["environment"].startswith("main-wt-feature-x-"), payload
    assert len(registry(main_repo)["assignments"]) == 1, registry(main_repo)
    assert not (other / ".git" / "ndf" / "worktree-registry.json").exists(), \
        "実行位置側の台帳には記録しない"


def test_a_target_outside_a_repository_keeps_the_existing_report(
    main_repo: Path, tmp_path: Path
) -> None:
    """対象がリポジトリの外にあるときは、サブコマンド自身の案内で終わる。"""
    declare(main_repo, testenv={"port_band": [20000, 29999]})
    outside = tmp_path / "outside"
    outside.mkdir()

    result = run(["env", str(outside)], cwd=main_repo)

    assert result["rc"] == 1, result
    assert "ブランチを取れません" in result["err"], result["err"]


def test_tag_refuses_when_the_declared_paths_have_no_match(
    main_repo: Path, worktree: Path
) -> None:
    """宣言したパスが 1 件も見つからないとき、空の内容に対する値を返さない。"""
    declare(main_repo, testenv={"golden_tag_paths": ["database/migrations"]})

    result = run(["tag", str(worktree)], cwd=main_repo)

    assert result["rc"] == 1, result
    assert result["out"].strip() == "", result["out"]


# --- issue #312: reap と実行中のロック ---------------------------------------


def reap_with_running_container(main_repo: Path, worktree: Path) -> tuple[dict, Path]:
    """使われていない割り当てを 1 つ用意し、稼働中のコンテナを返す偽の実行系で reap する。

    戻り値は環境名を含む `env` の出力と、偽の実行系が書き出したファイルである。
    実行中のロックは呼び出し側が用意してから呼ぶ。
    """
    dump = main_repo.parent / "reap-env.txt"
    env = os.environ.copy()
    env["WT_DOCKER_COMMAND"] = str(stub_docker_with_running_container(main_repo, dump))
    proc = subprocess.run(
        ["bash", str(TESTENV), "reap", "--idle", "45m"],
        cwd=str(main_repo), env=env, capture_output=True, text=True,
    )
    return {"rc": proc.returncode, "out": proc.stdout, "err": proc.stderr}, dump


def idle_assignment(main_repo: Path, worktree: Path) -> Path:
    """使われていない割り当てを作り、その実行中のロックの位置を返す。"""
    (worktree / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    declare(
        main_repo,
        testenv={"port_band": [20000, 29999], "port_roles": {"http": 0}},
        localenv={"kind": "compose", "compose_files": ["docker-compose.yml"]},
    )
    environment = json.loads(run(["env", str(worktree)], cwd=main_repo)["out"])["environment"]
    set_last_used(main_repo, worktree, "2020-01-01T00:00:00Z")
    return main_repo / ".git" / "ndf" / f"{environment}.inuse.d"


def test_reap_stops_an_environment_whose_lock_is_old_and_empty(main_repo: Path, worktree: Path) -> None:
    """AC7: 持ち主を書く前に落ちたロックが古くなれば、停止の対象へ戻る。"""
    lock = idle_assignment(main_repo, worktree)
    lock.mkdir()
    make_old(lock)

    result, dump = reap_with_running_container(main_repo, worktree)

    assert result["rc"] == 0, result
    assert "停止します" in result["out"], result
    assert dump.read_text().rstrip().endswith("stop"), dump.read_text()


@pytest.mark.parametrize("state", ["fresh-empty", "alive-pid"])
def test_reap_leaves_an_environment_whose_lock_is_held(main_repo: Path, worktree: Path, state: str) -> None:
    """AC8: 作った直後の空のロックと、生きている持ち主のロックは止めない。"""
    lock = idle_assignment(main_repo, worktree)
    lock.mkdir()
    if state == "alive-pid":
        (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")

    result, dump = reap_with_running_container(main_repo, worktree)

    assert result["rc"] == 0, result
    assert "停止します" not in result["out"], result
    assert not dump.exists(), "stop を呼ばない"


# --- issue #315: 台帳の更新の失敗 --------------------------------------------


def failing_jq(main_repo: Path, fail_on: str) -> dict:
    """引数のどれかが `fail_on` を含む呼び出しだけ終了コード 5 で終わる偽の jq を置く。

    それ以外の呼び出しは本物の jq へ渡す。`fail_on` には台帳を更新する jq の
    代入式（`.ports = $ports` など）を渡し、狙った 1 か所だけを失敗させる。
    戻り値は PATH の先頭に偽の jq を置いた環境変数である。
    """
    import shlex
    import shutil

    real = shutil.which("jq")
    assert real, "本物の jq が要る"
    bindir = main_repo.parent / "fake-jq"
    bindir.mkdir(exist_ok=True)
    script = bindir / "jq"
    script.write_text(
        "#!/bin/sh\n"
        f"fail_on={shlex.quote(fail_on)}\n"
        'for a in "$@"; do\n'
        '  case "$a" in *"$fail_on"*) exit 5 ;; esac\n'
        "done\n"
        f'exec {shlex.quote(real)} "$@"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bindir}:{env['PATH']}"
    return env


def test_env_fails_when_ports_cannot_be_recorded(main_repo: Path, worktree: Path) -> None:
    """AC9: ポートを台帳へ書けなければ JSON を出さず、新しく取った割り当てを解放する。"""
    declare(main_repo, testenv={"port_band": [20000, 29999], "port_roles": {"http": 0}})

    result = run(["env", str(worktree)], cwd=main_repo, env=failing_jq(main_repo, ".ports = $ports"))

    assert result["rc"] == 1, result
    assert result["out"] == "", result
    assert "ポートを台帳へ記録できませんでした" in result["err"], result
    rows = registry(main_repo)["assignments"]
    assert rows and all(row["released_at"] is not None for row in rows), rows


def test_env_reports_when_the_release_after_a_band_overflow_fails(main_repo: Path, worktree: Path) -> None:
    """AC10: 帯を超えた後の解放も書けなければ、割り当てが残ったことを知らせる。"""
    declare(main_repo, testenv={"port_band": [20000, 20005], "port_roles": {"far": 9}})

    result = run(
        ["env", str(worktree)], cwd=main_repo, env=failing_jq(main_repo, ".released_at = (now"),
    )

    assert result["rc"] == 1, result
    assert "採番が帯を超えました" in result["err"], result
    assert "スロットの解放を台帳へ記録できませんでした" in result["err"], result
    rows = registry(main_repo)["assignments"]
    assert rows[0]["released_at"] is None, "解放は書けていない"


def compose_ready(main_repo: Path, worktree: Path) -> Path:
    """compose の定義と宣言を置き、割り当てを 1 つ作る。偽の実行系の書き出し先を返す。"""
    (worktree / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    declare(
        main_repo,
        testenv={"port_band": [20000, 29999], "port_roles": {"http": 0}},
        localenv={"kind": "compose", "compose_files": ["docker-compose.yml"]},
    )
    run(["env", str(worktree)], cwd=main_repo)
    return main_repo.parent / "compose-env.txt"


def test_up_does_not_start_when_the_golden_tag_cannot_be_recorded(main_repo: Path, worktree: Path) -> None:
    """AC11: 基準のタグを台帳へ書けなければ、起動せずに 1 を返す。"""
    dump = compose_ready(main_repo, worktree)
    env = failing_jq(main_repo, ".golden_tag = $tag")
    env["WT_DOCKER_COMMAND"] = str(stub_docker(main_repo, dump))

    result = run(["up", str(worktree), "--tag", "abc"], cwd=main_repo, env=env)

    assert result["rc"] == 1, result
    assert "基準のタグを台帳へ記録できませんでした" in result["err"], result
    assert not dump.exists(), "compose up を呼ばない"


def test_up_does_not_start_when_the_registry_lock_is_held(main_repo: Path, worktree: Path) -> None:
    """AC12: 台帳の排他を取れないときも起動しない。待ちは 1 回分（上限 5 秒）で終わる。"""
    import time

    dump = compose_ready(main_repo, worktree)
    lock = main_repo / ".git" / "ndf" / "worktree-registry.json.lockdir"
    lock.mkdir()
    (lock / "held").write_text("", encoding="utf-8")
    (lock / "token").write_text("tok\n", encoding="utf-8")
    (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
    env = os.environ.copy()
    env["WT_DOCKER_COMMAND"] = str(stub_docker(main_repo, dump))

    started = time.monotonic()
    result = run(["up", str(worktree), "--tag", "abc"], cwd=main_repo, env=env)
    elapsed = time.monotonic() - started

    assert result["rc"] == 1, result
    assert "基準のタグを台帳へ記録できませんでした" in result["err"], result
    assert not dump.exists(), "compose up を呼ばない"
    assert elapsed < 8, elapsed


def test_up_warns_but_starts_when_the_last_used_time_cannot_be_recorded(main_repo: Path, worktree: Path) -> None:
    """AC16（up）: 最後に使った時刻を書けなくても、警告を出して起動する。"""
    dump = compose_ready(main_repo, worktree)
    env = failing_jq(main_repo, ".last_used_at = (now")
    env["WT_DOCKER_COMMAND"] = str(stub_docker(main_repo, dump))

    result = run(["up", str(worktree)], cwd=main_repo, env=env)

    assert result["rc"] == 0, result
    assert "警告" in result["err"] and "最後に使った時刻" in result["err"], result
    assert dump.read_text().rstrip().endswith("up -d"), dump.read_text()


def test_test_warns_and_keeps_the_command_exit_code(main_repo: Path, worktree: Path) -> None:
    """AC16（test）: 最後に使った時刻を書けなくても、実行したコマンドの終了コードを返す。"""
    declare(main_repo, testenv={"port_band": [20000, 29999], "test_kinds": {"unit": {"run": "exit 3"}}})
    run(["env", str(worktree)], cwd=main_repo)

    result = run(
        ["test", str(worktree), "--kind", "unit"],
        cwd=main_repo, env=failing_jq(main_repo, ".last_used_at = (now"),
    )

    assert result["rc"] == 3, result
    assert "警告" in result["err"] and "最後に使った時刻" in result["err"], result


def test_down_reports_when_the_release_cannot_be_recorded(main_repo: Path, worktree: Path) -> None:
    """AC13: 破棄はしたが解放を書けなければ、1 を返して再実行を案内する。"""
    dump = compose_ready(main_repo, worktree)
    env = failing_jq(main_repo, ".released_at = (now")
    env["WT_DOCKER_COMMAND"] = str(stub_docker(main_repo, dump))

    result = run(["down", str(worktree)], cwd=main_repo, env=env)

    assert result["rc"] == 1, result
    assert "スロットの解放を台帳へ記録できませんでした" in result["err"], result
    assert "down を再実行" in result["err"], result
    assert dump.read_text().rstrip().endswith("down"), "破棄はしている"
    assert registry(main_repo)["assignments"][0]["released_at"] is None


def exposed(main_repo: Path, worktree: Path, **expose: str) -> None:
    """公開を許す基準が載った割り当てを作り、公開する。"""
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "expose": {"enabled": True, "public_tag": "golden-public",
                   "base_domain": "example.test", "open_command": "true", **expose},
    })
    run(["env", str(worktree)], cwd=main_repo)
    golden(main_repo, worktree)
    assert run(["expose", str(worktree)], cwd=main_repo)["rc"] == 0


def test_unexpose_reports_when_the_record_cannot_be_closed(main_repo: Path, worktree: Path) -> None:
    """AC14: 閉じる手段は実行したが台帳を閉じられなければ、1 を返して再実行を案内する。"""
    marker = main_repo / "closed.txt"
    exposed(main_repo, worktree, close_command=f"touch {marker}")

    result = run(
        ["unexpose", str(worktree)], cwd=main_repo,
        env=failing_jq(main_repo, ".expose.closed_at = (now"),
    )

    assert result["rc"] == 1, result
    assert "台帳を閉じられませんでした" in result["err"], result
    assert "unexpose を再実行" in result["err"], result
    assert marker.exists(), "閉じる手段は実行している"
    assert registry(main_repo)["assignments"][0]["expose"]["closed_at"] is None


def test_expose_reports_when_the_rollback_fails(main_repo: Path, worktree: Path) -> None:
    """AC15: 公開の手段が失敗し、記録も戻せなければ、戻したとは言わず unexpose を案内する。"""
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "expose": {"enabled": True, "public_tag": "golden-public",
                   "base_domain": "example.test", "open_command": "exit 1"},
    })
    run(["env", str(worktree)], cwd=main_repo)
    golden(main_repo, worktree)

    result = run(
        ["expose", str(worktree)], cwd=main_repo,
        env=failing_jq(main_repo, ".expose.closed_at = (now"),
    )

    assert result["rc"] == 1, result
    assert "記録を戻しました" not in result["err"], result
    assert "記録も戻せませんでした" in result["err"], result
    assert "unexpose で閉じて" in result["err"], result
    assert registry(main_repo)["assignments"][0]["expose"]["closed_at"] is None
