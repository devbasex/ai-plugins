"""`issue-upkeep` のテストが共有する補助と対象パス。

テストを責務ごとのモジュールへ分けても、折り返しを畳む `flat`・見出しで切る `section`・
表を読む `table` と、読み込む SKILL のパスは全モジュールで同じものを使う。1 箇所へ寄せて、
各モジュールはここから取り込む。
"""
from __future__ import annotations

import pathlib
import re

# tests/ -> issue-upkeep/
SKILL_DIR = pathlib.Path(__file__).resolve().parents[1]
SKILLS = SKILL_DIR.parent
ROOT = SKILLS.parents[2]

SKILL = SKILL_DIR / "SKILL.md"
NO_WORK = SKILL_DIR / "references" / "no-work.md"
MILESTONES = SKILL_DIR / "references" / "milestones.md"
GROUPING = SKILL_DIR / "references" / "grouping.md"
VOCABULARY = SKILLS / "refactoring" / "references" / "vocabulary.md"
OUT_OF_SCOPE = SKILLS / "out-of-scope" / "SKILL.md"
RETROSPECTIVE = SKILLS / "retrospective" / "SKILL.md"
PROBLEM_SOLVING = SKILLS / "problem-solving" / "SKILL.md"

# 手順の表が持つ 8 つの判定。**この並びが基準である。**
VERDICTS = [
    "そのまま", "追記が要る", "書き直しが要る", "閉じてよい",
    "やらない", "重複", "ルートコーズ", "要判断",
]

# 起点の種類ごとの投稿先・実行コマンド・辿る経路。**この対応が基準である。**
# コマンドは起点で `gh issue comment` / `gh pr comment` が分かれ、辿る経路は
# 「起点の issue を持たない変更」だけ追加の 1 行が要らない。
RETROSPECTIVE_TARGETS = [
    ("1 件の issue", "その issue へのコメント",
     "gh issue comment", "その issue の本文末尾へ 1 行"),
    ("複数の issue（まとまり）", "そのまとまりを配布した Pull Request へのコメント",
     "gh pr comment", "対象のすべての issue の本文末尾へ 1 行"),
    ("起点の issue を持たない変更", "その変更の Pull Request へのコメント",
     "gh pr comment", "追加の 1 行は要らない"),
]

STAGE_3_GATE = "**`updated_at` が変わっていれば本文の要約値を見る。**"


def flat(text: str) -> str:
    """折り返しの改行を除く。日本語の文は改行の位置で語が割れるため、照合の前に繋ぐ。"""
    return text.replace("\n", "")


def section(text: str, heading: str) -> str:
    """見出しから、同じ深さか浅い次の見出しまでを返す。"""
    level = heading.split(" ", 1)[0]
    start = text.index(heading + "\n")
    rest = text[start + len(heading):]
    ends = [m.start() for m in re.finditer(r"^(#+) ", rest, re.MULTILINE)
            if len(m.group(1)) <= len(level)]
    return heading + (rest[:ends[0]] if ends else rest)


def table(text: str, header: str) -> list[list[str]]:
    """見出し行で始まる表の、データ行のセルを返す。太字の印は外す。"""
    block = text[text.index(header):]
    block = block[:block.index("\n\n")] if "\n\n" in block else block
    rows = [line for line in block.split("\n")[2:] if line.startswith("|")]
    return [[cell.strip().strip("*").strip() for cell in row.strip("|").split("|")]
            for row in rows]
