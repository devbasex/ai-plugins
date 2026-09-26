"""宣言ファイルの読み取りを検証する（受け入れ条件 21）。

宣言が無い・読めない・版が未対応のいずれの場合も、何も出力せず呼び出し側が
作業を止めない値を返す（詳細設計 06 の決定 9）。
"""
from __future__ import annotations

import json
from pathlib import Path

from hook_lib import worktree
from worktree_helpers import run_lib, write_declaration


def allow_paths(main_repo: Path) -> list[str]:
    """guard の許可パス（hook の判定 hook_lib/worktree.py。#1142 の決定 20）。"""
    return worktree.allow_paths(worktree.declaration(str(main_repo)))


def read_declaration(main_repo: Path) -> tuple[str, int]:
    got = run_lib(f'wt_declaration "{main_repo}"; echo rc=$?', cwd=main_repo)
    lines = got.stdout.splitlines()
    rc = int(lines.pop().removeprefix("rc="))
    return "\n".join(lines), rc


def test_declaration_without_arguments_is_silent() -> None:
    got = run_lib("wt_declaration")
    assert got.returncode == 1
    assert got.stdout == ""


def test_declaration_empty_argument_is_silent() -> None:
    got = run_lib('wt_declaration ""')
    assert got.returncode == 1
    assert got.stdout == ""


def test_missing_declaration_is_silent(main_repo: Path) -> None:
    body, rc = read_declaration(main_repo)
    assert rc == 1
    assert body == ""


def test_broken_json_is_silent(main_repo: Path) -> None:
    write_declaration(main_repo, "{ not json")
    body, rc = read_declaration(main_repo)
    assert rc == 1
    assert body == ""


def test_unsupported_version_is_silent(main_repo: Path) -> None:
    write_declaration(main_repo, json.dumps({"version": 99}))
    body, rc = read_declaration(main_repo)
    assert rc == 1
    assert body == ""


def test_missing_version_is_silent(main_repo: Path) -> None:
    write_declaration(main_repo, json.dumps({"localenv": {"kind": "compose"}}))
    body, rc = read_declaration(main_repo)
    assert rc == 1
    assert body == ""


def test_supported_version_is_returned(main_repo: Path) -> None:
    write_declaration(
        main_repo,
        json.dumps({"version": 1, "guard": {"allow_paths": ["issues/"]}}),
    )
    body, rc = read_declaration(main_repo)
    assert rc == 0
    assert json.loads(body)["guard"]["allow_paths"] == ["issues/"]


def test_unknown_fields_are_kept(main_repo: Path) -> None:
    """知らない項目があっても読み取りは成功する（互換性の規則）。"""
    write_declaration(main_repo, json.dumps({"version": 1, "future_field": {"a": 1}}))
    _, rc = read_declaration(main_repo)
    assert rc == 0


def test_allow_paths_falls_back_to_defaults(main_repo: Path) -> None:
    """`guard.allow_paths` が無ければ組み込みの既定を使う。"""
    write_declaration(main_repo, json.dumps({"version": 1}))
    got = allow_paths(main_repo)
    assert "issues/" in got, got
    assert ".gitignore" in got, got


def test_allow_paths_uses_declaration_when_present(main_repo: Path) -> None:
    write_declaration(
        main_repo,
        json.dumps({"version": 1, "guard": {"allow_paths": ["notes/"]}}),
    )
    got = allow_paths(main_repo)
    assert got == ["notes/"], got


def test_empty_allow_paths_allows_nothing(main_repo: Path) -> None:
    """空の配列は「何も許可しない」という指定で、既定へは戻さない。"""
    write_declaration(main_repo, json.dumps({"version": 1, "guard": {"allow_paths": []}}))
    got = allow_paths(main_repo)
    assert got == [], got


def test_allow_paths_of_wrong_type_falls_back(main_repo: Path) -> None:
    """配列でない値は指定として読まず、既定へ戻す。"""
    write_declaration(main_repo, json.dumps({"version": 1, "guard": {"allow_paths": "issues/"}}))
    got = allow_paths(main_repo)
    assert "issues/" in got
    assert ".gitignore" in got, got
