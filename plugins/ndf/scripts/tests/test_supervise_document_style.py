"""プランの worker が他者の読む文書を書くとき、document-style の規則が届く（#2372）。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
from supervise_lib.paths import SKILLS  # noqa: E402
from supervise_lib.prompts import FULL_SYSTEM, PR_SYSTEM, WORK_SYSTEM  # noqa: E402

STYLE = SKILLS / "document-style" / "SKILL.md"


def rule_headings() -> list[str]:
    return re.findall(r"^### \d+\. (.+)$", STYLE.read_text(), flags=re.M)


def test_the_tool_using_worker_is_told_where_document_style_is():
    paths = [Path(p) for p in re.findall(r"`(/[^`]+/document-style/SKILL\.md)`", WORK_SYSTEM)]
    assert paths and all(p.is_file() for p in paths)


def test_the_full_worker_is_told_to_load_document_style():
    assert "document-style" in FULL_SYSTEM


def test_the_tool_less_pr_writer_receives_every_rule_of_document_style():
    rules = rule_headings()
    assert len(rules) >= 10  # 見出しを読み取れていること
    assert all(r in PR_SYSTEM for r in rules)
