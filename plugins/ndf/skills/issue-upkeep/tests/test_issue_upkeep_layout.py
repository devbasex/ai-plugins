"""`issue-upkeep` の構成と配線（#331 / #712 / #713）。"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess

import pytest

SKILL_DIR = pathlib.Path(__file__).resolve().parents[1]
SKILLS = SKILL_DIR.parent
ROOT = SKILLS.parents[2]

SKILL = SKILL_DIR / "SKILL.md"
GROUPING = SKILL_DIR / "references" / "grouping.md"


def plain(text: str) -> str:
    """折り返しの改行に加えて強調の印を除く。太字の付け外しで同じ契約が落ちないようにする。"""
    return text.replace("\n", "").replace("**", "")


def locate(text: str, fragment: str) -> int:
    """強調の印を除いた文の、本文での位置。切り出しの起点に使う。"""
    return text.index(plain(fragment))


def section(text: str, heading: str) -> str:
    """見出しから、同じ深さか浅い次の見出しまでを返す。"""
    level = heading.split(" ", 1)[0]
    start = text.index(heading + "\n")
    rest = text[start + len(heading):]
    ends = [m.start() for m in re.finditer(r"^(#+) ", rest, re.MULTILINE)
            if len(m.group(1)) <= len(level)]
    return heading + (rest[:ends[0]] if ends else rest)


@pytest.mark.parametrize("runtime", ["claude", "codex", "kiro", "agy"])
def test_the_skill_is_distributed(runtime: str) -> None:
    """4 つの manifest すべてに載る。"""
    manifest = ROOT / "plugins" / "ndf" / "manifests" / f"{runtime}-skills.txt"
    names = [line.split("#", 1)[0].strip()
             for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert "issue-upkeep" in names


# ---------- 対象の範囲 ----------


def test_the_target_query_keeps_only_null_milestone_issues() -> None:
    """段 1 の jq を実際に流し、milestone が null の課題だけを残すことを固定する。

    現状固定: 期待値の根拠は仕様ではなく、抽出手順が返す jq 式の振る舞いである。
    文字列の存在（別の現状固定テスト）ではなく、分岐そのものを流して固定する。
    """
    body = SKILL.read_text(encoding="utf-8")
    block = body[locate(body, "**起票者は問わない。**"):]
    start = block.index("```bash")
    block = block[start:block.index("```", start + len("```bash")) + 3]
    expression = re.search(r"--jq '([^']*)'", block).group(1)

    issues = [
        {"number": 10, "title": "foo", "milestone": None},
        {"number": 20, "title": "bar", "milestone": {"title": "m1"}},
        {"number": 30, "title": "baz baz", "milestone": None},
    ]
    done = subprocess.run(["jq", "-r", expression], input=json.dumps(issues),
                          capture_output=True, text=True, check=True)
    lines = done.stdout.splitlines()

    # branch: milestone が null の 2 件だけが残り、milestone を持つ課題は落ちる。
    assert lines == ["#10 foo", "#30 baz baz"]
    # 整形の形: 各行は "#<number> <title>" である（表示文言ではなく形として見る）。
    for line, issue in zip(lines, [issues[0], issues[2]]):
        assert line.startswith(f"#{issue['number']} ")
        assert line == f"#{issue['number']} {issue['title']}"


# ---------- やり直しで 2 度行わない（grouping.md） ----------

REDO_SECTION = "## やり直しで 2 度行わない"


def redo_section() -> str:
    return section(GROUPING.read_text(encoding="utf-8"), REDO_SECTION)


def redo_commands() -> list[str]:
    """やり直しの節が持つ判定材料の取り方（bash の例）を、出てくる順に返す。"""
    return re.findall(r"```bash\n(.*?)```", redo_section(), re.DOTALL)


def test_the_parent_side_lookup_lists_the_linked_children() -> None:
    """現状固定: 親の側の jq を実際に流し、結び付け済みの子の番号だけが並ぶことを固定する。

    この一覧に無い子が、やり直しで結び付ける残りである。番号以外の項目は落ちる。
    """
    from_parent, _ = redo_commands()
    expression = re.search(r"--jq '([^']*)'", from_parent).group(1)

    sub_issues = [
        {"id": 1001, "number": 12, "title": "child a"},
        {"id": 1002, "number": 34, "title": "child b"},
    ]
    done = subprocess.run(["jq", "-r", expression], input=json.dumps(sub_issues),
                          capture_output=True, text=True, check=True)
    assert done.stdout.splitlines() == ["12", "34"]

    # branch: 子が 1 件も結び付いていない親では、一覧が空で返る。
    done = subprocess.run(["jq", "-r", expression], input="[]",
                          capture_output=True, text=True, check=True)
    assert done.stdout.splitlines() == []
