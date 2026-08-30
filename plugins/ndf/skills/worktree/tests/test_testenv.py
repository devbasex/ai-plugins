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

from worktree_helpers import SCRIPTS_DIR, git, write_declaration

TESTENV = SCRIPTS_DIR / "worktree-testenv.sh"


def run(args: list[str], cwd: Path) -> dict:
    env = os.environ.copy()
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
    """証跡が追跡対象に入ると差分が埋まる。作業ツリー限りの除外へ登録する。"""
    declare(main_repo, testenv={
        "port_band": [20000, 29999],
        "test_kinds": {"browser": {"run": "true", "out_env": "OUT"}},
    })
    run(["test", str(worktree), "--kind", "browser"], cwd=main_repo)

    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(worktree), capture_output=True, text=True,
    )
    assert ".ndf-evidence" not in status.stdout, status.stdout


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


def test_normalize_does_not_expand_globs(tmp_path: Path) -> None:
    """`*` や `?` を含むパスが、実在するファイルの名前へ化けない。"""
    from worktree_helpers import run_lib

    (tmp_path / "aaa").write_text("x", encoding="utf-8")
    (tmp_path / "bbb").write_text("x", encoding="utf-8")
    got = run_lib(f'wt_normalize_path "*" "{tmp_path}"')
    assert got.stdout.strip() == f"{tmp_path}/*", got.stdout
