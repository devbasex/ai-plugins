"""ローカル環境での動作検証の支援を検証する（受け入れ条件 21〜24、27）。

起動を伴う検証は単体テストへ持ち込まない（詳細設計 06）。ここで確かめるのは
宣言の読み取り・複製・照合の終了コード・モードの提示までで、コンテナの起動と
切り替えは手動確認が担う。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from worktree_helpers import SCRIPTS_DIR, git, write_declaration

LOCALENV = SCRIPTS_DIR / "worktree-localenv.sh"


def run(args: list[str], cwd: Path, env: dict | None = None) -> dict:
    run_env = os.environ.copy()
    run_env["LC_ALL"] = "C"
    if env:
        run_env.update(env)
    proc = subprocess.run(
        ["bash", str(LOCALENV), *args],
        cwd=str(cwd),
        env=run_env,
        capture_output=True,
        text=True,
    )
    return {"rc": proc.returncode, "out": proc.stdout, "err": proc.stderr}


def declare(main_repo: Path, localenv: dict | None = None) -> None:
    body = {"version": 1}
    if localenv is not None:
        body["localenv"] = localenv
    write_declaration(main_repo, json.dumps(body))


# --- 受け入れ条件 21: 宣言が無いリポジトリでは何もしない --------------------


@pytest.mark.parametrize("sub", ["setup", "verify", "healthcheck", "aim", "mode"])
def test_no_declaration_is_silent(main_repo: Path, worktree: Path, sub: str) -> None:
    result = run([sub, str(worktree)], cwd=main_repo)
    assert result["rc"] == 0, result
    assert result["out"].strip() == "", result["out"]


@pytest.mark.parametrize("sub", ["setup", "verify", "mode"])
def test_unsupported_kind_is_silent(main_repo: Path, worktree: Path, sub: str) -> None:
    """`localenv.kind` が `compose` 以外は未対応として扱う。"""
    declare(main_repo, {"kind": "vagrant"})
    result = run([sub, str(worktree)], cwd=main_repo)
    assert result["rc"] == 0, result
    assert result["out"].strip() == "", result["out"]


# --- 受け入れ条件 22・27: 設定と依存物の複製 --------------------------------


def test_setup_copies_declared_paths(main_repo: Path, worktree: Path) -> None:
    (main_repo / ".env").write_text("APP=1\n", encoding="utf-8")
    (main_repo / "vendor").mkdir()
    (main_repo / "vendor" / "lib.txt").write_text("x\n", encoding="utf-8")
    declare(main_repo, {"kind": "compose", "layout": "indirect",
                        "copy_from_main": [".env", "vendor"]})

    result = run(["setup", str(worktree)], cwd=main_repo)

    assert result["rc"] == 0, result
    assert (worktree / ".env").read_text() == "APP=1\n"
    assert (worktree / "vendor" / "lib.txt").read_text() == "x\n"


def test_setup_uses_hardlinks(main_repo: Path, worktree: Path) -> None:
    """複製はハードリンクで行う（実体の増加を避ける）。"""
    (main_repo / ".env").write_text("APP=1\n", encoding="utf-8")
    declare(main_repo, {"kind": "compose", "copy_from_main": [".env"]})

    run(["setup", str(worktree)], cwd=main_repo)

    assert (main_repo / ".env").stat().st_ino == (worktree / ".env").stat().st_ino


def test_setup_replaces_copy_as_real_with_a_real_copy(main_repo: Path, worktree: Path) -> None:
    """書き換えられるパスはハードリンクを外し、実体で置き換える。"""
    (main_repo / "vendor").mkdir()
    (main_repo / "vendor" / "composer").mkdir()
    (main_repo / "vendor" / "composer" / "map.txt").write_text("a\n", encoding="utf-8")
    declare(main_repo, {"kind": "compose",
                        "copy_from_main": ["vendor"],
                        "copy_as_real": ["vendor/composer"]})

    run(["setup", str(worktree)], cwd=main_repo)

    src = main_repo / "vendor" / "composer" / "map.txt"
    dst = worktree / "vendor" / "composer" / "map.txt"
    assert dst.read_text() == "a\n"
    assert src.stat().st_ino != dst.stat().st_ino


def test_setup_aborts_when_contents_differ(main_repo: Path, worktree: Path) -> None:
    """内容が主ディレクトリと食い違う場合は上書きせず中断する（条件 22）。"""
    (main_repo / ".env").write_text("APP=1\n", encoding="utf-8")
    (worktree / ".env").write_text("APP=2\n", encoding="utf-8")
    declare(main_repo, {"kind": "compose", "copy_from_main": [".env"]})

    result = run(["setup", str(worktree)], cwd=main_repo)

    assert result["rc"] == 1, result
    assert ".env" in result["err"], result["err"]
    assert (worktree / ".env").read_text() == "APP=2\n", "上書きしない"


def test_setup_skips_identical_contents(main_repo: Path, worktree: Path) -> None:
    """同じ内容なら中断しない。"""
    (main_repo / ".env").write_text("APP=1\n", encoding="utf-8")
    (worktree / ".env").write_text("APP=1\n", encoding="utf-8")
    declare(main_repo, {"kind": "compose", "copy_from_main": [".env"]})

    result = run(["setup", str(worktree)], cwd=main_repo)

    assert result["rc"] == 0, result


def test_setup_falls_back_to_a_file_copy(main_repo: Path, worktree: Path) -> None:
    """ハードリンクが使えない配置ではファイル複製へ退避する（条件 27）。"""
    (main_repo / ".env").write_text("APP=1\n", encoding="utf-8")
    declare(main_repo, {"kind": "compose", "copy_from_main": [".env"]})

    # ハードリンクを作れない状況を、`ln` を必ず失敗させることで作る。
    fake_bin = main_repo.parent / "fakebin"
    fake_bin.mkdir(exist_ok=True)
    (fake_bin / "ln").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (fake_bin / "ln").chmod(0o755)

    result = run(
        ["setup", str(worktree)],
        cwd=main_repo,
        env={"WT_LINK_COMMAND": str(fake_bin / "ln")},
    )

    assert result["rc"] == 0, result
    assert (worktree / ".env").read_text() == "APP=1\n"
    assert (main_repo / ".env").stat().st_ino != (worktree / ".env").stat().st_ino


def test_setup_ignores_missing_paths(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, {"kind": "compose", "copy_from_main": ["not-there"]})
    result = run(["setup", str(worktree)], cwd=main_repo)
    assert result["rc"] == 0, result


# --- 受け入れ条件 23: 照合の 3 状態 -----------------------------------------


def test_verify_matches(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, {"kind": "compose", "branch_probe": "echo feature/x"})
    result = run(["verify", str(worktree)], cwd=main_repo)
    assert result["rc"] == 0, result


def test_verify_detects_a_mismatch(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, {"kind": "compose", "branch_probe": "echo main"})
    result = run(["verify", str(worktree)], cwd=main_repo)
    assert result["rc"] == 1, result


def test_verify_reports_not_running(main_repo: Path, worktree: Path) -> None:
    """未起動と不一致を同じ値にしない。"""
    declare(main_repo, {"kind": "compose", "branch_probe": "exit 7"})
    result = run(["verify", str(worktree)], cwd=main_repo)
    assert result["rc"] == 2, result


def test_verify_without_a_probe_is_out_of_scope(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, {"kind": "compose"})
    result = run(["verify", str(worktree)], cwd=main_repo)
    assert result["rc"] == 2, result


def test_verify_empty_probe_output_is_not_running(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, {"kind": "compose", "branch_probe": "true"})
    result = run(["verify", str(worktree)], cwd=main_repo)
    assert result["rc"] == 2, result


# --- healthcheck は照合を先に行う -------------------------------------------


def test_healthcheck_runs_only_after_a_match(main_repo: Path, worktree: Path) -> None:
    marker = main_repo / "ran.txt"
    declare(main_repo, {"kind": "compose",
                        "branch_probe": "echo feature/x",
                        "healthcheck": f"touch {marker}"})
    result = run(["healthcheck", str(worktree)], cwd=main_repo)
    assert result["rc"] == 0, result
    assert marker.exists()


def test_healthcheck_stops_on_a_mismatch(main_repo: Path, worktree: Path) -> None:
    marker = main_repo / "ran.txt"
    declare(main_repo, {"kind": "compose",
                        "branch_probe": "echo main",
                        "healthcheck": f"touch {marker}"})
    result = run(["healthcheck", str(worktree)], cwd=main_repo)
    assert result["rc"] == 1, result
    assert not marker.exists(), "照合が通らないうちは実行しない"


def test_healthcheck_returns_the_command_exit_code(main_repo: Path, worktree: Path) -> None:
    """実行したコマンドの終了コードをそのまま返す。"""
    declare(main_repo, {"kind": "compose",
                        "branch_probe": "echo feature/x",
                        "healthcheck": "exit 5"})
    result = run(["healthcheck", str(worktree)], cwd=main_repo)
    assert result["rc"] == 5, result


# --- 受け入れ条件 24: モードの提示 ------------------------------------------


def test_mode_defaults_to_sharing(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, {"kind": "compose", "isolate_when": ["docker/**"]})
    (worktree / "app.txt").write_text("x\n", encoding="utf-8")
    git(worktree, "add", "-A")
    result = run(["mode", str(worktree)], cwd=main_repo)
    assert result["rc"] == 0, result
    assert "相乗り" in result["out"], result["out"]


def test_mode_suggests_isolation_for_matching_paths(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, {"kind": "compose",
                        "isolate_when": ["database/migrations/**", "docker-compose*.yml"]})
    (worktree / "database" / "migrations").mkdir(parents=True)
    (worktree / "database" / "migrations" / "001.sql").write_text("x\n", encoding="utf-8")
    git(worktree, "add", "-A")
    result = run(["mode", str(worktree)], cwd=main_repo)
    assert result["rc"] == 1, result
    assert "分離" in result["out"], result["out"]
    assert "database/migrations/001.sql" in result["out"], result["out"]


def test_mode_matches_a_glob_at_the_top_level(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, {"kind": "compose", "isolate_when": ["docker-compose*.yml"]})
    (worktree / "docker-compose.dev.yml").write_text("x\n", encoding="utf-8")
    git(worktree, "add", "-A")
    result = run(["mode", str(worktree)], cwd=main_repo)
    assert result["rc"] == 1, result


def test_mode_without_conditions_is_sharing(main_repo: Path, worktree: Path) -> None:
    declare(main_repo, {"kind": "compose"})
    (worktree / "docker-compose.dev.yml").write_text("x\n", encoding="utf-8")
    git(worktree, "add", "-A")
    result = run(["mode", str(worktree)], cwd=main_repo)
    assert result["rc"] == 0, result


# --- 宣言の誤りで作業ツリーの外を触らない ------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["/etc/passwd", "../outside", "vendor/../../outside", "..", "~/secrets"],
)
def test_setup_rejects_paths_outside_the_worktree(main_repo: Path, worktree: Path, bad: str) -> None:
    declare(main_repo, {"kind": "compose", "copy_from_main": [bad]})
    result = run(["setup", str(worktree)], cwd=main_repo)
    assert result["rc"] == 1, result
    assert "外を指します" in result["err"], result["err"]


@pytest.mark.parametrize("bad", ["/etc/passwd", "../outside"])
def test_setup_rejects_unsafe_copy_as_real(main_repo: Path, worktree: Path, bad: str) -> None:
    declare(main_repo, {"kind": "compose", "copy_as_real": [bad]})
    result = run(["setup", str(worktree)], cwd=main_repo)
    assert result["rc"] == 1, result
    assert "外を指します" in result["err"], result["err"]


def test_copy_as_real_does_not_discard_local_edits(main_repo: Path, worktree: Path) -> None:
    """作業ツリー側で書き換えられていたら、置き換えずに中断する。"""
    (main_repo / "vendor").mkdir()
    (main_repo / "vendor" / "map.txt").write_text("a\n", encoding="utf-8")
    (worktree / "vendor").mkdir()
    (worktree / "vendor" / "map.txt").write_text("編集済み\n", encoding="utf-8")
    declare(main_repo, {"kind": "compose", "copy_as_real": ["vendor"]})

    result = run(["setup", str(worktree)], cwd=main_repo)

    assert result["rc"] == 1, result
    assert (worktree / "vendor" / "map.txt").read_text() == "編集済み\n", "消さない"


def test_mode_lists_a_path_once(main_repo: Path, worktree: Path) -> None:
    """1 つのパスが複数の条件に当たっても、一覧へは 1 度だけ載せる。"""
    declare(main_repo, {"kind": "compose",
                        "isolate_when": ["docker-compose*.yml", "*.yml"]})
    (worktree / "docker-compose.dev.yml").write_text("x\n", encoding="utf-8")
    git(worktree, "add", "-A")
    result = run(["mode", str(worktree)], cwd=main_repo)
    assert result["rc"] == 1, result
    assert result["out"].count("docker-compose.dev.yml") == 1, result["out"]


def test_setup_refuses_to_follow_a_symlink_destination(main_repo: Path, worktree: Path, tmp_path: Path) -> None:
    """宛先が symlink のときはたどらずに断る。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    (main_repo / ".env").write_text("APP=1\n", encoding="utf-8")
    (worktree / ".env").symlink_to(outside / "stolen.env")
    declare(main_repo, {"kind": "compose", "copy_from_main": [".env"]})

    result = run(["setup", str(worktree)], cwd=main_repo)

    assert result["rc"] == 1, result
    assert "symlink" in result["err"], result["err"]
    assert not (outside / "stolen.env").exists(), "外へ書き込まない"


def test_setup_refuses_a_parent_symlink_leaving_the_worktree(main_repo: Path, worktree: Path, tmp_path: Path) -> None:
    """途中のディレクトリが作業ツリーの外を指していても書き込まない。"""
    outside = tmp_path / "outside"
    outside.mkdir()
    (main_repo / "vendor").mkdir()
    (main_repo / "vendor" / "lib.txt").write_text("x\n", encoding="utf-8")
    (worktree / "vendor").symlink_to(outside)
    declare(main_repo, {"kind": "compose", "copy_from_main": ["vendor/lib.txt"]})

    result = run(["setup", str(worktree)], cwd=main_repo)

    assert result["rc"] == 1, result
    assert not (outside / "lib.txt").exists(), "外へ書き込まない"
