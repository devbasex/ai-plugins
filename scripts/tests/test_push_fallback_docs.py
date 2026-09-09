"""push の退避の手が手順書に書かれていること（#524）。

**同じ退避のコマンドが 2 か所に書かれると、片方だけが更新される。** 値を持つのは共通層
（`plugins/ndf/scripts/lib/git-credential.sh`）1 か所で、手順書はその値と一致する
コマンドを案内する。ここではその一致を固定する。

**`pr` と `fix` は任意のリポジトリで動く。** 共通層を読み込ませず、`git` のコマンドを
そのまま案内する（読み込みの経路を増やすと、NDF を導入していない環境で手順が成立しない）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "plugins/ndf/scripts/lib/git-credential.sh"

# 退避を案内する手順書。`cross-refactoring` は実装が退避するため、案内の形が違う。
DOCS_WITH_COMMAND = (
    "plugins/ndf/skills/pr/SKILL.md",
    "plugins/ndf/skills/fix/SKILL.md",
)
DOCS_WITH_REFERENCE = ("plugins/ndf/skills/cross-refactoring/SKILL.md",)


def fallback_args() -> list[str]:
    out = subprocess.run(
        ["bash", "-c", f'. "{LIB}"; ndf_git_credential_fallback_args'],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line for line in out.split("\n") if line]


def test_the_shared_library_is_the_only_source_of_the_value():
    assert LIB.is_file()
    assert fallback_args() == [
        "-c", "credential.helper=",
        "-c", "credential.helper=!gh auth git-credential",
    ]


@pytest.mark.parametrize("doc", DOCS_WITH_COMMAND)
def test_the_document_shows_the_same_command(doc):
    """案内するコマンドが共通層の値と一致すること。"""
    text = (REPO_ROOT / doc).read_text(encoding="utf-8")
    # 引用の付け方は文書ごとに違うため、値そのものが順に現れることで見る
    first = text.find("credential.helper=")
    assert first != -1, f"{doc} に退避の案内がない"
    after = text[first:]
    assert "!gh auth git-credential" in after, f"{doc} に gh の helper がない"
    assert after.index("credential.helper=") < after.index("!gh auth git-credential"), (
        f"{doc} は空の値を先に置いていない")


@pytest.mark.parametrize("doc", DOCS_WITH_COMMAND)
def test_the_document_explains_why_the_reset_comes_first(doc):
    text = (REPO_ROOT / doc).read_text(encoding="utf-8")
    assert "空の値を先に置く" in text, f"{doc} に順序の理由がない"


@pytest.mark.parametrize("doc", DOCS_WITH_REFERENCE)
def test_the_implementation_side_points_at_the_shared_library(doc):
    """実装が退避する側は、値を写さず共通層を指す。"""
    text = (REPO_ROOT / doc).read_text(encoding="utf-8")
    assert "scripts/lib/git-credential.sh" in text, f"{doc} が共通層を指していない"
    assert "credential.helper=!gh" not in text, f"{doc} が値を写している"


def test_the_documents_that_carry_the_command_do_not_load_the_library():
    """任意のリポジトリで動く手順書は、共通層の読み込みを求めない。"""
    for doc in DOCS_WITH_COMMAND:
        text = (REPO_ROOT / doc).read_text(encoding="utf-8")
        assert "git-credential.sh" not in text, f"{doc} が共通層の読み込みを求めている"
