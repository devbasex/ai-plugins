"""GitHub と git へ書くのをレビューを回す側だけにした決定が、文書に反映されている（#730）。

| 受け入れ条件 | 文書 | 消える記述 | 入る記述 |
| --- | --- | --- | --- |
| AC25 | `SKILL.md` の設計方針の表 | 担当が `gh api` で直接投稿する | 投稿の担い手とその理由 |
| AC26 | `docs/03-review-output.md` | 担当の直接投稿の決定 | 待ち行列を通す形 |
| AC27 | `docs/02-fix-and-rotation.md` | 担当の送信の行 | 取り込む側の送信 |
| AC28 | `references/context-budget.md` | 中間ペイロードがメインを通らない | 本文がプロセスの中だけを通る |
| AC29 | `docs/04-contracts.md` | — | 投稿の種別ごとの契約 |
"""
from __future__ import annotations

import pathlib

import pytest

HERE = pathlib.Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (HERE / rel).read_text(encoding="utf-8")


def test_the_policy_table_names_who_posts() -> None:
    text = _read("SKILL.md")
    assert "AI 自身が `gh api` で PR に直接投稿" not in text
    assert "| 投稿の担い手 |" in text


def test_the_review_output_goes_through_the_queue() -> None:
    text = _read("docs/03-review-output.md")
    assert "AI 直接投稿" not in text
    assert "待ち行列" in text


def test_the_fix_procedure_does_not_push() -> None:
    text = _read("docs/02-fix-and-rotation.md")
    assert "git push origin {HEAD_BRANCH}" not in text
    assert "HEAD:<ブランチ名>" in text


def test_the_context_budget_keeps_bodies_in_the_process() -> None:
    text = _read("references/context-budget.md")
    assert "中間ペイロードがメインを通らない" not in text
    assert "プロセスの中だけを通り" in text


@pytest.mark.parametrize("kind", ["review-post", "review-reply", "thread-resolve", "pr-comment"])
def test_the_contract_lists_each_kind_of_post(kind: str) -> None:
    text = _read("docs/04-contracts.md")
    section = text.split("## 投稿の種別ごとの契約", 1)[1].split("\n## ", 1)[0]
    assert f"| `{kind}` |" in section
