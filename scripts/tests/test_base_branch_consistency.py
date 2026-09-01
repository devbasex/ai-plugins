"""Skill の手順が、共通ライブラリと同じ起点ブランチを解決することを検証する。

起点を扱う Skill は作業ツリーの仕組みを前提にしないため、手順には共通ライブラリを
読み込まずに動く数行を書いている。写しである以上、両者が食い違う経路が残る。同じ入力に
対して同じ名前を返すことを、ここで突き合わせる（issue #202）。

あわせて、開発の起点を既定ブランチの字面で書いていないことを見る。
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from branch_repo_helpers import (
    drop_remote_tracking,
    init_master_only_repo,
    init_origin_repo,
    missing_command,
    push_branch,
    push_lookalike_branch,
)

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "plugins" / "ndf" / "scripts" / "lib" / "worktree-common.sh"
SKILLS = ROOT / "plugins" / "ndf" / "skills"

# 起点を解決する手順を持つ Skill と、その手順を見分ける目印。
INLINE_SKILLS = ("cherry-pick-pr", "deploy", "merged", "pr-review")
MARKER = "dev_base=$(jq"

# 開発の起点を扱う Skill。起点は既定ブランチとは限らないため、字面で書かない。
LITERAL_SKILLS = (
    "ndf-policies",
    "cherry-pick-pr",
    "deploy",
    "merged",
    "pr",
    "pr-review",
    "problem-solving",
    "worktree",
)

# コマンドの引数に現れる既定ブランチの字面。`dev_base=${dev_base:-main}` のような
# 退避先は対象にしない（origin の HEAD すら取れないときの最後の手段である）。
COMMAND_LITERAL = re.compile(r"^\s*git\s+\S+[^\n]*\bmain\b")

pytestmark = pytest.mark.skipif(missing_command() is not None, reason="bash / jq / git が要る")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """origin を持つリポジトリ。既定ブランチは main、origin に develop がある。"""
    main = init_origin_repo(tmp_path)
    push_branch(main, "develop")
    return main


@pytest.fixture()
def repo_without_develop(tmp_path: Path) -> Path:
    """origin にもローカルにも `develop` が無いリポジトリ。"""
    return init_origin_repo(tmp_path)


@pytest.fixture()
def repo_with_unfetched_develop(tmp_path: Path) -> Path:
    """origin に `develop` があり、その参照をまだ取得していないリポジトリ。"""
    main = init_origin_repo(tmp_path)
    push_branch(main, "develop")
    drop_remote_tracking(main, "develop")
    return main


@pytest.fixture()
def repo_with_lookalike_develop(tmp_path: Path) -> Path:
    """origin に `refs/heads/x/refs/heads/develop` だけがあるリポジトリ。"""
    main = init_origin_repo(tmp_path)
    push_lookalike_branch(main, "develop")
    return main


@pytest.fixture()
def repo_without_origin_head(tmp_path: Path) -> Path:
    """origin の HEAD が無く、ローカルに `master` だけがあるリポジトリ。

    宣言した起点を解決する経路も見るため、origin に `develop` を置いておく。
    """
    repo = init_master_only_repo(tmp_path)
    push_branch(repo, "develop")
    return repo


def snippet_of(name: str) -> str:
    """起点を解決する bash のコードブロックを取り出す。"""
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    blocks = [
        block
        for block in re.findall(r"^```bash\n(.*?)^```$", text, re.S | re.M)
        if MARKER in block
    ]
    assert len(blocks) == 1, f"{name}: 起点を解決する bash が 1 つではない（{len(blocks)} 個）"
    return blocks[0]


def run_inline(name: str, repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'set -uo pipefail\n{snippet_of(name)}\nprintf "%s\\n" "$dev_base"\n'],
        cwd=str(repo), capture_output=True, text=True,
    )


def run_library(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'set -uo pipefail\n. "{LIB}"\nwt_base_branch "{repo}"\n'],
        cwd=str(repo), capture_output=True, text=True,
    )


def resolve_inline(name: str, repo: Path) -> str:
    got = run_inline(name, repo)
    assert got.returncode == 0, got.stderr
    return got.stdout.strip()


def resolve_library(repo: Path) -> str:
    got = run_library(repo)
    assert got.returncode == 0, got.stderr
    return got.stdout.strip()


def write_declaration(repo: Path, body: dict) -> None:
    ndf = repo / ".ndf"
    ndf.mkdir(exist_ok=True)
    (ndf / "worktree.json").write_text(json.dumps(body), encoding="utf-8")


DECLARATIONS = {
    "宣言なし": None,
    "起点の指定なし": {"version": 1},
    "起点あり": {"version": 1, "base_branch": "develop"},
    "版が対応外": {"version": 99, "base_branch": "develop"},
    "文字列でない": {"version": 1, "base_branch": 7},
}


@pytest.mark.parametrize("name", INLINE_SKILLS)
@pytest.mark.parametrize("case", list(DECLARATIONS))
def test_inline_resolution_matches_library(name: str, case: str, repo: Path) -> None:
    body = DECLARATIONS[case]
    if body is not None:
        write_declaration(repo, body)
    assert resolve_inline(name, repo) == resolve_library(repo)


@pytest.mark.parametrize("name", INLINE_SKILLS)
@pytest.mark.parametrize("case", list(DECLARATIONS))
def test_inline_resolution_matches_library_without_origin_head(
    name: str, case: str, repo_without_origin_head: Path
) -> None:
    """origin の HEAD が取れないときの落とし先も突き合わせる。

    既定ブランチの解決は `origin の HEAD → main → master` の順で、最後の 1 段は
    origin の HEAD を持たないリポジトリでしか通らない。main を持つリポジトリだけを
    見ていると、手順が `master` へ落ちない食い違いを拾えない。
    """
    body = DECLARATIONS[case]
    if body is not None:
        write_declaration(repo_without_origin_head, body)
    got = resolve_inline(name, repo_without_origin_head)
    assert got == resolve_library(repo_without_origin_head)
    # 宣言が起点を指していない経路は、慣例の名前のうち実在する `master` へ落ちる。
    assert got == ("develop" if case == "起点あり" else "master")


@pytest.mark.parametrize("name", INLINE_SKILLS)
def test_inline_matches_library_when_declared_branch_is_missing(
    name: str, repo_without_develop: Path
) -> None:
    """宣言した名前がどこにも無いとき、手順も共通ライブラリも落とさずに失敗する。

    ここを突き合わせないと、手順だけが実在しない名前をそのまま返す状態が残る。返した名前は
    この後の `git fetch origin "$dev_base"` で落ちるため、失敗する位置が遠くなるだけで、
    起点を解決できていないことは変わらない。
    """
    write_declaration(repo_without_develop, {"version": 1, "base_branch": "develop"})
    inline = run_inline(name, repo_without_develop)
    library = run_library(repo_without_develop)
    assert inline.returncode != 0, inline.stdout
    assert library.returncode != 0, library.stdout
    assert inline.stdout.strip() == ""
    assert library.stdout.strip() == ""
    assert "develop" in inline.stderr
    assert "develop" in library.stderr


@pytest.mark.parametrize("name", INLINE_SKILLS)
def test_inline_matches_library_when_declared_branch_is_unfetched(
    name: str, repo_with_unfetched_develop: Path
) -> None:
    """取得していないだけで origin にあるブランチも、どちらも起点として使う。

    起点を `develop` へ移した直後の作業ディレクトリがこの形になる。取得済みの参照だけを
    見ると「無い」と読み、`git fetch` を挟むまで解決が失敗し続ける。
    """
    write_declaration(repo_with_unfetched_develop, {"version": 1, "base_branch": "develop"})
    assert resolve_inline(name, repo_with_unfetched_develop) == "develop"
    assert resolve_library(repo_with_unfetched_develop) == "develop"


@pytest.mark.parametrize("name", INLINE_SKILLS)
def test_inline_matches_library_when_only_a_lookalike_branch_exists(
    name: str, repo_with_lookalike_develop: Path
) -> None:
    """末尾が一致するだけの別のブランチを、どちらも起点として採らない。

    `git ls-remote` のパターンは参照名の末尾に一致するため、完全な参照名で問い合わせても
    `refs/heads/x/refs/heads/develop` が返る。問い合わせの成功だけを見ると、起点が未作成
    なのに解決できたことになり、手順だけが実在しない名前を返す。
    """
    write_declaration(repo_with_lookalike_develop, {"version": 1, "base_branch": "develop"})
    inline = run_inline(name, repo_with_lookalike_develop)
    library = run_library(repo_with_lookalike_develop)
    assert inline.returncode != 0, inline.stdout
    assert library.returncode != 0, library.stdout
    assert inline.stdout.strip() == ""
    assert library.stdout.strip() == ""
    assert "develop" in inline.stderr
    assert "develop" in library.stderr


@pytest.mark.parametrize("name", LITERAL_SKILLS)
def test_remote_default_branch_is_not_hardcoded(name: str) -> None:
    """取り込む先を `origin/main` の字面で書かない。"""
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    hits = [line for line in text.splitlines() if "origin/main" in line]
    assert hits == [], f"{name}: {hits}"


@pytest.mark.parametrize("name", LITERAL_SKILLS)
def test_commands_do_not_hardcode_default_branch(name: str) -> None:
    """git のコマンドの引数に `main` を書かない。"""
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    hits = [line for line in text.splitlines() if COMMAND_LITERAL.match(line)]
    assert hits == [], f"{name}: {hits}"
