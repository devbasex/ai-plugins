"""skill-stats の frontmatter と Skill の表が包みの上で変わる入力（#1142 の D3）。

frontmatter は `lib/yamlio.py`（ruamel.yaml）、表は `lib/mdtable.py`（tabulate）で読み書きする。
自作の読み取りと組み立てから変わる入力を、ここで固定する。
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "skill-stats.py"


@pytest.fixture(scope="module")
def ss():
    spec = importlib.util.spec_from_file_location("ndf_skill_stats_wrappers", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_folded_description_is_read_as_its_text(ss):
    """`>-` の値は折り畳んだ本文になる（自作の読み取りは `>-` と改行を含む字面を返した）。"""
    fm = ss.parse_front_matter("---\nname: x\ndescription: >-\n  一行目\n  二行目（語・語）\n---\n本文\n")
    assert fm["description"] == "一行目 二行目（語・語）"


def test_quoted_values_lose_their_quotes(ss):
    fm = ss.parse_front_matter('---\nname: "x"\ndescription: \'説明\'\n---\n')
    assert fm == {"name": "x", "description": "説明"}


def test_non_string_values_are_read_as_text(ss):
    """YAML の型で読んだ値を文字列へ直す（真偽値は `True`、配列は 1 要素 1 行）。"""
    fm = ss.parse_front_matter("---\nname: true\nwhen_to_use: [a, b]\n---\n")
    assert fm == {"name": "True", "when_to_use": "a\nb"}


def test_unreadable_yaml_is_empty_and_reported(ss, capsys):
    """YAML として読めない frontmatter は空（名前はディレクトリから取る）。理由を標準エラーへ出す。"""
    assert ss.parse_front_matter("---\nname: [x\n---\n", "skills/x/SKILL.md") == {}
    assert "skills/x/SKILL.md" in capsys.readouterr().err


def test_no_front_matter_is_empty(ss):
    assert ss.parse_front_matter("本文だけ\n") == {}


def test_the_skill_table_uses_spaced_rules_and_an_empty_total_cell(ss):
    """区切りの行は `| --- |` の形、合計の行の空のセルは `|  |` になる（自作は `|---|` と `| |`）。"""
    rows = [
        {"skill": "ndf:pr", "triggers_source": "none", "invocations": 12, "auto": 2, "explicit": 10,
         "triggers": 0, "hits": 0, "hit_rate_pct": 0.0},
        {"skill": "ndf:fix", "triggers_source": "explicit", "invocations": 11, "auto": 3, "explicit": 8,
         "triggers": 8, "hits": 3, "hit_rate_pct": 37.5},
    ]
    total = {"invocations": 23, "auto": 5, "explicit": 18, "triggers": 8, "hits": 3, "hit_rate_pct": 37.5}
    assert ss.format_markdown(rows, total, heading="## 見出し").splitlines() == [
        "## 見出し",
        "| skill | triggers源 | 計 | 自動 | 明示 | 関連話題 | ヒット | ヒット率 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        "| ndf:pr | none | 12 | 2 | 10 | - | - | - |",
        "| ndf:fix | explicit | 11 | 3 | 8 | 8 | 3 | 37.5% |",
        "| **合計** |  | **23** | **5** | **18** | **8** | **3** | **37.5%** |",
    ]
