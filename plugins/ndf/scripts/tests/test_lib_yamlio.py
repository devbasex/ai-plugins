"""frontmatter と YAML の読み書きの包み（lib/yamlio.py・#1142 の決定 19）。外部パッケージは全体テストの環境（根の pyproject.toml）が入れる。

今の `skill-stats.py:parse_front_matter` から変わる入力を固定する: 値は YAML の型で返る
（`true` は真偽値、`[a, b]` は配列、複数行の `>` は畳んだ文字列）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import ruamel.yaml  # noqa: E402,F401  包みの外部パッケージ。無ければ集めるところで落とす
import yamlio  # noqa: E402

DOC = """---
name: "fix"  # 名前
disable-model-invocation: true
tags: [a, b]
description: >
  一行目
  二行目
---
# 本文
"""


def test_values_come_back_typed():
    fm = yamlio.read_front_matter(DOC)
    assert fm == {"name": "fix", "disable-model-invocation": True, "tags": ["a", "b"],
                  "description": "一行目 二行目\n"}
    assert yamlio.front_matter_text(DOC).startswith('name: "fix"')
    assert yamlio.split_front_matter(DOC).body == "# 本文\n" and yamlio.split_front_matter(DOC).body_line == 8


def test_missing_or_unclosed_front_matter():
    assert yamlio.read_front_matter("# 本文\n") == {}
    assert yamlio.split_front_matter("---\nname: x\n") is None
    assert yamlio.read_front_matter("---\n---\nbody") == {}
    with pytest.raises(yamlio.YamlError, match="キーと値の組"):
        yamlio.read_front_matter("---\n- a\n---\n")


def test_update_keeps_quotes_comments_and_the_body():
    out = yamlio.update_front_matter(DOC, {"tags": None, "version": "1.0"})
    assert out.startswith('---\nname: "fix"  # 名前\ndisable-model-invocation: true\n')
    assert "tags" not in out and "version: '1.0'" in out and out.endswith("---\n# 本文\n")
    assert yamlio.update_front_matter("body\n", {"a": "b"}) == "---\na: b\n---\nbody\n"


def test_unreadable_yaml_names_the_place():
    with pytest.raises(yamlio.YamlError, match=r"^x.yml: 読めない（1 行 6 桁"):
        yamlio.load_yaml("a: [1", "x.yml")
    assert yamlio.load_yaml(yamlio.dump_yaml({"a": [1, 2]})) == {"a": [1, 2]}
