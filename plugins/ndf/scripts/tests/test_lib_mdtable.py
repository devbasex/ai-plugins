"""Markdown の表の組み立ての包み（lib/mdtable.py・#1142 の決定 19）。外部パッケージは全体テストの環境（根の pyproject.toml）が入れる。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import tabulate  # noqa: E402,F401  包みの外部パッケージ。無ければ集めるところで落とす
import mdtable  # noqa: E402


def test_compact_rows_escape_and_number_columns_right_aligned():
    out = mdtable.table_markdown(["名前", "件数", "備考"], [["a|b", 3, None], ["c", 10.5, "x\ny"]])
    assert out == ("| 名前 | 件数 | 備考 |\n| --- | ---: | --- |\n| a\\|b | 3 |  |\n| c | 10.5 | x y |")


def test_explicit_alignment_short_rows_and_empty_tables():
    assert mdtable.table_markdown(["a", "b"], [[1]], align=["center", None]) == "| a | b |\n| :---: | --- |\n| 1 |  |"
    assert mdtable.table_markdown(["a"], [], align=["right"]) == "| a |\n| ---: |"
    assert mdtable.table_markdown(["n"], [["007"], [True]]) == "| n |\n| --- |\n| 007 |\n| True |"
    with pytest.raises(ValueError):
        mdtable.table_markdown(["a"], [], align=["middle"])
