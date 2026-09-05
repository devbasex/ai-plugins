"""委譲先の名前が手順書と参照で揃っていること（#214）。

`cross-review` は、レビュー担当の名前で一時ファイルの骨格・状態ファイルの鍵・
監視の対象名を揃えている。名前がひとつでも残ると、どの担当を起動するのかが
読み取れなくなる。

過去のレビューの出所を示すコメント（`gemini round N 指摘`）は残す。実行時の
呼び出し先ではなく、その行がそう書かれている理由だからである。
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[5]
SKILLS = ROOT / "plugins/ndf/skills"

# 担当 A が書き換える範囲。説明文書とプラグイン定義は含まない（担当 B が持つ）。
SCOPE = (
    "plugins/ndf/skills/cross-review",
    "plugins/ndf/skills/cross-refactoring",
    "plugins/ndf/skills/external-ai",
    "plugins/ndf/skills/fix/SKILL.md",
    "plugins/ndf/skills/issue-plan-strategy/SKILL.md",
    "plugins/ndf/skills/pr-review/SKILL.md",
    "plugins/ndf/skills/worktree/SKILL.md",
    "plugins/ndf/skills/worktree/references",
    "plugins/ndf/scripts/lib",
    "plugins/ndf/scripts/worktree-guard.sh",
    ".ndf/worktree.json",
)

# 残してよい形。過去のレビューの出所を指す注記だけ。
ALLOWED = re.compile(r"gemini\s+(?:round\s+\d+\s+)?(?:指摘|#\d+)")


# 走査から外すディレクトリ。tests は出所のコメントを持つため、残りは実行時に作られる
# 作業領域である。追跡していないファイルを検査へ入れると、実行した回数で結果が変わる。
_IGNORED_DIRS = {"tests", "__pycache__", ".pytest_cache"}


def _scope_files() -> list[pathlib.Path]:
    found: list[pathlib.Path] = []
    for entry in SCOPE:
        path = ROOT / entry
        if path.is_file():
            found.append(path)
            continue
        found.extend(
            p for p in path.rglob("*")
            if p.is_file()
            and not _IGNORED_DIRS & set(p.relative_to(path).parts)
        )
    return sorted(found)


# ---------- 受け入れ条件 23（起動スクリプトの名前） ----------

def test_the_review_launcher_is_named_after_the_reviewer() -> None:
    assert (SKILLS / "cross-review/scripts/launch-agy.sh").is_file()
    assert not (SKILLS / "cross-review/scripts/launch-gemini.sh").exists()


def test_the_procedure_points_at_the_renamed_launcher() -> None:
    body = (SKILLS / "cross-review/SKILL.md").read_text(encoding="utf-8")
    assert "launch-agy.sh" in body
    assert "launch-gemini.sh" not in body


# ---------- 受け入れ条件 24（委譲先の参照） ----------

def test_the_reference_for_the_delegate_exists() -> None:
    assert (SKILLS / "external-ai/references/cli-agy.md").is_file()
    assert not (SKILLS / "external-ai/references/cli-gemini.md").exists()


def test_the_delegation_skill_points_at_the_reference() -> None:
    body = (SKILLS / "external-ai/SKILL.md").read_text(encoding="utf-8")
    assert "references/cli-agy.md" in body


# ---------- 受け入れ条件 25（委譲先の指定） ----------

def test_the_review_skill_offers_codex_and_agy() -> None:
    body = (SKILLS / "pr-review/SKILL.md").read_text(encoding="utf-8")
    assert "codex|agy" in body


# ---------- 受け入れ条件 26（残る語） ----------

@pytest.mark.parametrize(
    "path", _scope_files(), ids=lambda p: str(p.relative_to(ROOT))
)
def test_only_the_provenance_comments_keep_the_old_name(path: pathlib.Path) -> None:
    left = [
        f"{path.relative_to(ROOT)}:{n}: {line.strip()}"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "gemini" in line.lower() and not ALLOWED.search(line.lower())
    ]
    assert left == []


# ---------- 受け入れ条件 14（共通層の削除） ----------

def _files_left_under(root: pathlib.Path) -> list[str]:
    """`root` に残っている、読み込みうるファイルの相対パスを返す。

    走査から外すのは実行時に作られる作業領域（`_IGNORED_DIRS`）だけである。
    """
    return sorted(
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and not _IGNORED_DIRS & set(p.relative_to(root).parts)
    )


def test_the_gemini_environment_helper_is_gone() -> None:
    """移設した先から共通層を読み込めないこと（#280 / #285）。

    判定するのは**そこから読み込めるファイルが無いこと**であって、ディレクトリの
    有無ではない。移設より前に作られた `__pycache__/*.pyc` は `.gitignore` の
    対象であるため作業ディレクトリに残り、**ディレクトリだけが存在し続ける**
    （#388）。存在で判定すると、v10.2.0 より前にこのリポジトリでテストを動かした
    作業ディレクトリでだけ落ちる。
    """
    assert not (SKILLS.parent / "scripts/lib/_gemini-env.sh").exists()
    assert _files_left_under(SKILLS / "cross-review/scripts/lib") == []


def test_leftover_caches_do_not_count_as_a_remaining_layer(
    tmp_path: pathlib.Path,
) -> None:
    """`__pycache__` だけが残った状態を「共通層が残っている」と読まない（#388）。"""
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "models.cpython-313.pyc").write_bytes(b"")

    assert _files_left_under(tmp_path) == []

    (tmp_path / "models.py").write_text("", encoding="utf-8")
    assert _files_left_under(tmp_path) == ["models.py"]


def test_no_script_sources_the_removed_helper() -> None:
    hits = [
        p for p in SKILLS.glob("**/*.sh")
        if "_gemini-env" in p.read_text(encoding="utf-8")
    ]
    assert hits == []


# ---------- 走査の対象（実行の回数で結果が変わらないこと） ----------

def test_the_scan_skips_directories_made_while_running(tmp_path: pathlib.Path) -> None:
    """実行時に作られる作業領域を検査へ入れない。

    `pytest` は実行のたびに `.pytest_cache/` を作り、その中の `nodeids` には
    テスト関数の名前がそのまま残る。走査へ入れると、`gemini` を含む名前の
    テストを 1 度でも実行した環境で結果が変わる。
    """
    for name in _IGNORED_DIRS:
        target = tmp_path / name / "nested"
        target.mkdir(parents=True)
        (target / "artifact.txt").write_text("gemini\n", encoding="utf-8")
    (tmp_path / "real.txt").write_text("agy\n", encoding="utf-8")

    scanned = [
        p for p in tmp_path.rglob("*")
        if p.is_file()
        and not _IGNORED_DIRS & set(p.relative_to(tmp_path).parts)
    ]

    assert scanned == [tmp_path / "real.txt"]
