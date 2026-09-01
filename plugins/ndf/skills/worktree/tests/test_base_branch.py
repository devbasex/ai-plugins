"""開発の起点ブランチの解決を検証する（issue #202）。

既定ブランチと開発の起点は別物である。宣言に起点が書かれていればそれを使い、
無ければ既定ブランチへ落とす。書かれた名前が実在しないときは既定へ落とさない。
落とすと、開発版のつもりの変更が正式版から分岐したまま進む。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from worktree_helpers import add_origin, git, push_branch, run_lib, write_declaration


def resolve(main_repo: Path) -> tuple[str, str, int]:
    got = run_lib(f'wt_base_branch "{main_repo}"; echo rc=$?', cwd=main_repo)
    lines = got.stdout.splitlines()
    rc = int(lines.pop().removeprefix("rc="))
    return "\n".join(lines), got.stderr, rc


def test_without_declaration_uses_default_branch(main_repo: Path) -> None:
    add_origin(main_repo)
    out, _, rc = resolve(main_repo)
    assert rc == 0
    assert out == "main"


def test_without_base_branch_uses_default_branch(main_repo: Path) -> None:
    add_origin(main_repo)
    write_declaration(main_repo, json.dumps({"version": 1}))
    out, _, rc = resolve(main_repo)
    assert rc == 0
    assert out == "main"


def test_remote_branch_is_used(main_repo: Path) -> None:
    add_origin(main_repo)
    push_branch(main_repo, "develop")
    write_declaration(main_repo, json.dumps({"version": 1, "base_branch": "develop"}))
    out, _, rc = resolve(main_repo)
    assert rc == 0
    assert out == "develop"


def test_local_only_branch_is_used(main_repo: Path) -> None:
    """origin へ送る前でも、ローカルに同名のブランチがあれば起点として使える。"""
    add_origin(main_repo)
    git(main_repo, "branch", "develop")
    write_declaration(main_repo, json.dumps({"version": 1, "base_branch": "develop"}))
    out, _, rc = resolve(main_repo)
    assert rc == 0
    assert out == "develop"


def test_missing_branch_does_not_fall_back(main_repo: Path) -> None:
    add_origin(main_repo)
    write_declaration(main_repo, json.dumps({"version": 1, "base_branch": "develop"}))
    out, err, rc = resolve(main_repo)
    assert rc == 1
    assert out == ""
    assert "develop" in err
    assert "base_branch" in err


@pytest.mark.parametrize("value", [1, ["develop"], None, "", {"name": "develop"}])
def test_non_string_value_falls_back(main_repo: Path, value: object) -> None:
    add_origin(main_repo)
    push_branch(main_repo, "develop")
    write_declaration(main_repo, json.dumps({"version": 1, "base_branch": value}))
    out, _, rc = resolve(main_repo)
    assert rc == 0
    assert out == "main"


def test_unsupported_version_falls_back(main_repo: Path) -> None:
    add_origin(main_repo)
    push_branch(main_repo, "develop")
    write_declaration(main_repo, json.dumps({"version": 99, "base_branch": "develop"}))
    out, _, rc = resolve(main_repo)
    assert rc == 0
    assert out == "main"


def test_without_origin_head_falls_back_to_local_default(main_repo: Path) -> None:
    """origin を持たないリポジトリでも、既定ブランチの解決はこれまでどおり働く。"""
    out, _, rc = resolve(main_repo)
    assert rc == 0
    assert out == "main"


# --- 手順が示すコマンド ------------------------------------------------------

SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"
CREATE_HEADING = "### 2-3. 作成して移る"


def create_snippet() -> str:
    """作業ツリーを作る手順の bash を取り出す。

    手順を写し取ると、写しだけが正しくて配布された手順が外れている状態を作れてしまう。
    """
    text = SKILL.read_text(encoding="utf-8")
    head = text.index(CREATE_HEADING)
    block = re.search(r"^```bash\n(.*?)^```$", text[head:], re.S | re.M)
    assert block is not None, f"{SKILL} の「{CREATE_HEADING}」に bash のブロックが無い"
    return block.group(1)


def run_documented_steps(main_repo: Path) -> Path:
    """手順のとおりに作業ツリーを作り、その位置を返す。"""
    snippet = create_snippet().replace("feature/<name>", "feature/x")
    got = run_lib(f'main_dir="{main_repo}"\n{snippet}', cwd=main_repo)
    assert got.returncode == 0, got.stderr + got.stdout
    return main_repo / ".worktrees" / "feature" / "x"


def head_of(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def test_documented_steps_branch_from_declared_base(main_repo: Path) -> None:
    add_origin(main_repo)
    git(main_repo, "checkout", "-q", "-b", "develop")
    git(main_repo, "commit", "-q", "--allow-empty", "-m", "develop work")
    expected = head_of(main_repo)
    git(main_repo, "push", "-q", "origin", "develop")
    git(main_repo, "checkout", "-q", "main")
    write_declaration(main_repo, json.dumps({"version": 1, "base_branch": "develop"}))

    created = run_documented_steps(main_repo)

    assert head_of(created) == expected


def test_documented_steps_fall_back_to_default(main_repo: Path) -> None:
    """宣言に起点が無いリポジトリでは、これまでどおり既定ブランチから分岐する。"""
    add_origin(main_repo)
    expected = head_of(main_repo)

    created = run_documented_steps(main_repo)

    assert head_of(created) == expected
