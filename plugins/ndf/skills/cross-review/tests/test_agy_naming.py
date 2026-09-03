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
    "plugins/ndf/scripts/lib/worktree-common.sh",
    "plugins/ndf/scripts/worktree-guard.sh",
    ".ndf/worktree.json",
)

# 残してよい形。過去のレビューの出所を指す注記だけ。
ALLOWED = re.compile(r"gemini\s+(?:round\s+\d+\s+)?(?:指摘|#\d+)")


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
            and not {"tests", "__pycache__"} & set(p.relative_to(path).parts)
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

def test_the_gemini_environment_helper_is_gone() -> None:
    assert not (SKILLS / "cross-review/scripts/lib/_gemini-env.sh").exists()


def test_no_script_sources_the_removed_helper() -> None:
    hits = [
        p for p in SKILLS.glob("**/*.sh")
        if "_gemini-env" in p.read_text(encoding="utf-8")
    ]
    assert hits == []
