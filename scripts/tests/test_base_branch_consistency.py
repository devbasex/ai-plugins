"""Skill の手順が、共通ライブラリと同じ起点ブランチを解決することを検証する。

起点を扱う Skill は作業ツリーの仕組みを前提にしないため、手順には共通ライブラリを
読み込まずに動く数行（宣言の読みはブランチ戦略の CLI、名前の実在の確認は手順の中）を書いている。
複製である以上、両者が食い違う経路が残る。同じ入力に対して同じ名前を返すか、同じく失敗することを、
ここで突き合わせる（issue #202・#3672）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from branch_repo_helpers import (
    drop_remote_tracking,
    init_master_only_repo,
    init_origin_repo,
    push_branch,
    push_lookalike_branch,
)

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "ndf"
LIB = PLUGIN / "scripts" / "lib" / "worktree-common.sh"
SKILLS = PLUGIN / "skills"
sys.path.insert(0, str(PLUGIN / "scripts" / "tests"))
import strategy_fixture  # noqa: E402

# 起点を解決する手順を持つ Skill と、その手順を見分ける目印。
INLINE_SKILLS = ("cherry-pick-pr", "deploy", "merged", "retrospective")
MARKER = "dev_base=$(python3"


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
    blocks = [block for block in re.findall(r"^```bash\n(.*?)^```$", text, re.S | re.M) if MARKER in block]
    assert len(blocks) == 1, f"{name}: 起点を解決する bash が 1 つではない（{len(blocks)} 個）"
    return blocks[0]


def run_inline(name: str, repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'set -uo pipefail\n{snippet_of(name)}\nprintf "%s\\n" "$dev_base"\n'],
        cwd=str(repo),
        capture_output=True,
        text=True,
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(PLUGIN)},
    )


def run_library(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'set -uo pipefail\n. "{LIB}"\nwt_base_branch "{repo}"\n'],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )


def resolve_inline(name: str, repo: Path) -> str | None:
    """解決した名前。失敗したら None（失敗したときは何も出力しない）。"""
    got = run_inline(name, repo)
    assert got.returncode == 0 or got.stdout.strip() == "", got.stdout
    return got.stdout.strip() if got.returncode == 0 else None


def resolve_library(repo: Path) -> str | None:
    got = run_library(repo)
    assert got.returncode == 0 or got.stdout.strip() == "", got.stdout
    return got.stdout.strip() if got.returncode == 0 else None


def write_declaration(repo: Path, body) -> None:
    """`.ndf/project.json` を書く。文字列はそのまま、dict は JSON にして書く。"""
    ndf = repo / ".ndf"
    ndf.mkdir(exist_ok=True)
    (ndf / "project.json").write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")


def strategy(base: str, production: str = "main") -> dict:
    kind = "web-trunk" if base == production else "web-develop"
    return {"version": 1, "branch_strategy": strategy_fixture.section(kind, base, production)}


DECLARATIONS = {
    "宣言なし": None,
    "セクションなし": {"version": 1},
    "起点あり": strategy("develop"),
    "起点が本番と同じ": strategy("main"),
    "壊れた JSON": "{not json",
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
def test_inline_resolution_matches_library_without_origin_head(name: str, case: str, repo_without_origin_head: Path) -> None:
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
    # 宣言が起点を指していない経路は、既定ブランチ（`master`）へ落とさずに失敗する（#3672 の I3）。
    assert got == ("develop" if case == "起点あり" else None)


@pytest.mark.parametrize("name", INLINE_SKILLS)
def test_inline_matches_library_when_declared_branch_is_missing(name: str, repo_without_develop: Path) -> None:
    """宣言した名前がどこにも無いとき、手順も共通ライブラリも落とさずに失敗する。

    ここを突き合わせないと、手順だけが実在しない名前をそのまま返す状態が残る。返した名前は
    この後の `git fetch origin "$dev_base"` で落ちるため、失敗する位置が遠くなるだけで、
    起点を解決できていないことは変わらない。
    """
    write_declaration(repo_without_develop, strategy("develop"))
    inline = run_inline(name, repo_without_develop)
    library = run_library(repo_without_develop)
    assert inline.returncode != 0, inline.stdout
    assert library.returncode != 0, library.stdout
    assert inline.stdout.strip() == ""
    assert library.stdout.strip() == ""
    assert "develop" in inline.stderr
    assert "develop" in library.stderr


@pytest.mark.parametrize("name", INLINE_SKILLS)
def test_inline_matches_library_when_declared_branch_is_unfetched(name: str, repo_with_unfetched_develop: Path) -> None:
    """取得していないだけで origin にあるブランチも、どちらも起点として使う。

    起点を `develop` へ移した直後の作業ディレクトリがこの形になる。取得済みの参照だけを
    見ると「無い」と読み、`git fetch` を挟むまで解決が失敗し続ける。
    """
    write_declaration(repo_with_unfetched_develop, strategy("develop"))
    assert resolve_inline(name, repo_with_unfetched_develop) == "develop"
    assert resolve_library(repo_with_unfetched_develop) == "develop"


@pytest.mark.parametrize("name", INLINE_SKILLS)
def test_inline_matches_library_when_only_a_lookalike_branch_exists(name: str, repo_with_lookalike_develop: Path) -> None:
    """末尾が一致するだけの別のブランチを、どちらも起点として採らない。

    `git ls-remote` のパターンは参照名の末尾に一致するため、完全な参照名で問い合わせても
    `refs/heads/x/refs/heads/develop` が返る。問い合わせの成功だけを見ると、起点が未作成
    なのに解決できたことになり、手順だけが実在しない名前を返す。
    """
    write_declaration(repo_with_lookalike_develop, strategy("develop"))
    inline = run_inline(name, repo_with_lookalike_develop)
    library = run_library(repo_with_lookalike_develop)
    assert inline.returncode != 0, inline.stdout
    assert library.returncode != 0, library.stdout
    assert inline.stdout.strip() == ""
    assert library.stdout.strip() == ""
    assert "develop" in inline.stderr
    assert "develop" in library.stderr


# --- 個人の宣言では起点が変わらない（#495 の AC17） --------------------------


def write_local_declaration(repo: Path, body: dict) -> None:
    """追跡しない個人の宣言 `.ndf/worktree.local.json` を書く。"""
    ndf = repo / ".ndf"
    ndf.mkdir(exist_ok=True)
    (ndf / "worktree.local.json").write_text(json.dumps(body), encoding="utf-8")


# 個人の宣言にブランチ名を書いても、起点は共有の宣言（`.ndf/project.json`）だけが決める
LOCAL_BASE_BRANCH = {"version": 1, "branches": {"base": "main"}}


@pytest.mark.parametrize("name", INLINE_SKILLS)
@pytest.mark.parametrize("case", list(DECLARATIONS))
def test_local_declaration_does_not_change_the_resolution(name: str, case: str, repo: Path) -> None:
    """起点は共有の宣言だけが決める。個人の宣言を置いても、手順と共通ライブラリの
    解決結果は個人の宣言が無い木と一致する（#495 の決定 7）。"""
    body = DECLARATIONS[case]
    if body is not None:
        write_declaration(repo, body)
    before = (resolve_inline(name, repo), resolve_library(repo))

    write_local_declaration(repo, LOCAL_BASE_BRANCH)

    assert (resolve_inline(name, repo), resolve_library(repo)) == before
