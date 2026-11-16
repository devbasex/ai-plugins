"""ベースブランチの解決を検証する（issue #202・#3672）。

既定ブランチとベースブランチは別物である。ベースブランチはブランチ戦略の宣言の `branches.base` から読み、
宣言が無いときも書かれた名前が実在しないときも既定ブランチへ落とさない。
落とすと、開発版のつもりの変更が正式版から分岐したまま進む。
"""

from __future__ import annotations

import re
from pathlib import Path


from worktree_helpers import SCRIPTS_DIR, add_origin, git, push_branch, resolve, run_lib, write_strategy


def test_local_only_branch_is_used(main_repo: Path) -> None:
    """origin へ送る前でも、ローカルに同名のブランチがあれば起点として使える。"""
    add_origin(main_repo)
    git(main_repo, "branch", "develop")
    write_strategy(main_repo, base="develop", production="main")
    out, _, rc = resolve(main_repo, "wt_base_branch")
    assert rc == 0
    assert out == "develop"


def test_unfetched_remote_branch_is_used(main_repo: Path) -> None:
    """取得していないだけで origin にあるブランチも起点として使える。

    `git push` は送った先の参照も更新するため、それを消して「origin にはあるが手元では
    まだ取得していない」状態を作る。起点を `develop` へ移した直後の作業ディレクトリが
    この形になり、取得済みの参照だけを見ると `git fetch` を挟むまで解決が失敗し続ける。
    """
    add_origin(main_repo)
    push_branch(main_repo, "develop")
    git(main_repo, "update-ref", "-d", "refs/remotes/origin/develop")
    write_strategy(main_repo, base="develop", production="main")
    out, err, rc = resolve(main_repo, "wt_base_branch")
    assert rc == 0, err
    assert out == "develop"


def test_remote_branch_with_matching_tail_is_not_used(main_repo: Path) -> None:
    """origin への問い合わせは、完全な参照名で照合する。

    `git ls-remote` のパターンは参照名の末尾に一致するため、`develop` とだけ渡すと
    `refs/heads/feature/develop` にも一致する（実測）。別のブランチを起点として
    受け入れないことを見る。
    """
    add_origin(main_repo)
    push_branch(main_repo, "feature/develop")
    git(main_repo, "update-ref", "-d", "refs/remotes/origin/feature/develop")
    write_strategy(main_repo, base="develop", production="main")
    out, err, rc = resolve(main_repo, "wt_base_branch")
    assert rc == 1
    assert out == ""
    assert "develop" in err


def test_missing_branch_does_not_fall_back(main_repo: Path) -> None:
    add_origin(main_repo)
    write_strategy(main_repo, base="develop", production="main")
    out, err, rc = resolve(main_repo, "wt_base_branch")
    assert rc == 1
    assert out == ""
    assert "develop" in err
    assert "branches.base" in err


def test_without_a_declaration_it_stops(main_repo: Path) -> None:
    """宣言が無いリポジトリでは既定ブランチへ倒さず、作成のコマンドを示して止まる（I3）。"""
    add_origin(main_repo)
    out, err, rc = resolve(main_repo, "wt_base_branch")
    assert rc != 0
    assert out == ""
    assert "branch-strategy" in err


# --- 手順が示すコマンド ------------------------------------------------------

SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"
CREATE_HEADING = "### 2-3. 作成して移る"


def create_snippet() -> str:
    """作業ツリーを作る手順の bash を取り出す。

    手順を写し取ると、複製だけが正しくてデプロイされた手順が外れている状態を作れてしまう。
    """
    text = SKILL.read_text(encoding="utf-8")
    head = text.index(CREATE_HEADING)
    block = re.search(r"^```bash\n(.*?)^```$", text[head:], re.S | re.M)
    assert block is not None, f"{SKILL} の「{CREATE_HEADING}」に bash のブロックが無い"
    return block.group(1)


def run_documented_steps(main_repo: Path) -> Path:
    """手順のとおりに作業ツリーを作り、その位置を返す。"""
    snippet = create_snippet().replace("feature/<name>", "feature/x")
    # $SCRIPTS は手順 0 で決める値。手順は宣言の依存の用意も打つ
    got = run_lib(f'main_dir="{main_repo}"\nSCRIPTS="{SCRIPTS_DIR}"\n{snippet}', cwd=main_repo)
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
    write_strategy(main_repo, base="develop", production="main")

    created = run_documented_steps(main_repo)

    assert head_of(created) == expected


def test_documented_steps_stop_without_a_declaration(main_repo: Path) -> None:
    """宣言が無いリポジトリでは、既定ブランチから分岐せずに止まる。"""
    add_origin(main_repo)
    snippet = create_snippet().replace("feature/<name>", "feature/x")
    got = run_lib(f'main_dir="{main_repo}"\nSCRIPTS="{SCRIPTS_DIR}"\n{snippet}', cwd=main_repo)
    assert got.returncode != 0
    assert not (main_repo / ".worktrees" / "feature" / "x").exists()
