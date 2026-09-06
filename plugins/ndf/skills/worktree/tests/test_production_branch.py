"""本番のチャネルの解決を検証する（#424）。

**開発の起点と本番のチャネルは別物である。** `base_branch` は作業ツリーの分岐元と
主ディレクトリの追従先を決める鍵で、このリポジトリでは `develop` を指す。本番の
チャネルとして定めるのは `main` であるため、**流用すると開発版のチャネルへのマージが
本番の扱いになる**（決定 10）。

宣言が無いリポジトリでは既定ブランチを本番のチャネルとして扱う。
"""
from __future__ import annotations

import json
from pathlib import Path

from worktree_helpers import add_origin, git, push_branch, run_lib, write_declaration


def resolve(main_repo: Path) -> tuple[str, str, int]:
    got = run_lib(f'wt_production_branch "{main_repo}"; echo rc=$?', cwd=main_repo)
    lines = got.stdout.splitlines()
    rc = int(lines.pop().removeprefix("rc="))
    return "\n".join(lines), got.stderr, rc


def test_without_declaration_uses_the_default_branch(main_repo: Path) -> None:
    add_origin(main_repo)
    out, _, rc = resolve(main_repo)
    assert rc == 0
    assert out == "main"


def test_without_the_key_uses_the_default_branch(main_repo: Path) -> None:
    add_origin(main_repo)
    write_declaration(main_repo, json.dumps({"version": 1}))
    out, _, rc = resolve(main_repo)
    assert rc == 0
    assert out == "main"


def test_the_declared_branch_is_used(main_repo: Path) -> None:
    add_origin(main_repo)
    push_branch(main_repo, "release")
    write_declaration(
        main_repo, json.dumps({"version": 1, "production_branch": "release"})
    )
    out, _, rc = resolve(main_repo)
    assert rc == 0
    assert out == "release"


def test_the_base_branch_is_not_borrowed(main_repo: Path) -> None:
    """**`base_branch` は開発の起点である。** 流用すると開発版が本番の扱いになる。"""
    add_origin(main_repo)
    git(main_repo, "branch", "develop")
    write_declaration(main_repo, json.dumps({"version": 1, "base_branch": "develop"}))
    out, _, rc = resolve(main_repo)
    assert rc == 0
    assert out == "main"


def test_a_branch_that_does_not_exist_is_refused(main_repo: Path) -> None:
    """本番のチャネルを取り違えると、配布の承認の対象が変わる。既定へ落とさない。"""
    add_origin(main_repo)
    write_declaration(
        main_repo, json.dumps({"version": 1, "production_branch": "nowhere"})
    )
    _out, err, rc = resolve(main_repo)
    assert rc == 1
    assert "production_branch" in err


def test_this_repository_declares_the_production_branch() -> None:
    """このリポジトリでは `main` が正式版、`develop` が開発版である。"""
    root = Path(__file__).resolve().parents[5]
    declaration = json.loads((root / ".ndf" / "worktree.json").read_text(encoding="utf-8"))
    assert declaration["production_branch"] == "main"
    assert declaration["base_branch"] == "develop"


def test_the_schema_documents_the_key() -> None:
    root = Path(__file__).resolve().parents[1]
    schema = json.loads(
        (root / "schemas" / "worktree.schema.json").read_text(encoding="utf-8")
    )
    described = schema["properties"]["production_branch"]["description"]
    assert "base_branch" in described
