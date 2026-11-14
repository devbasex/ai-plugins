"""プランを組む前の入力の確かめ（inputs_decide。N2b）。"""

from __future__ import annotations

from ndf.core.plan.inputs_decide import (
    DesignResult,
    absorbed_missing,
    criteria,
    design_gaps,
    kinds_lack,
    merged_files,
    path_present,
    section_lack,
)

BODY = """## 目的

x

## 受け入れ条件

- [ ] A が動く
- [x] B が動く

```md
- [ ] コードブロックの中は数えない
```

## 受け入れ条件の確認

| 条件 | 区分 | 手段 |
| --- | --- | --- |
| 1 | マージ前 | test_a |
"""


def test_criteria_reads_checkboxes_outside_code_blocks():
    assert criteria(BODY) == ["A が動く", "B が動く"]


def test_section_lack_names_a_missing_or_empty_section():
    """受け入れ条件の節を持たない本文を通さない（#2246）。"""
    assert section_lack(BODY) is None
    assert "節が無い" in section_lack("## 目的\n\nx\n")
    assert "条件が無い" in section_lack("## 受け入れ条件\n\n説明だけ\n")


def test_kinds_lack_lists_criteria_without_a_kind():
    """区分の無い受け入れ条件を拾う（#2357）。"""
    assert kinds_lack(BODY) == [2]
    assert kinds_lack("## 受け入れ条件\n\n- [ ] A\n") == [1]


def test_absorbed_missing_lists_lost_criteria_per_issue():
    """取り込んだ課題の受け入れ条件が取り込み先から落ちていれば拾う（#2274）。"""
    absorbed = {10: "## 受け入れ条件\n\n- [ ] A が動く\n- [ ] C が動く\n"}
    assert absorbed_missing(BODY, absorbed) == {10: ["C が動く"]}


def test_design_gaps_stop_unread_and_split_designs():
    """設計の結果を読めない課題・PR を分けた課題で止まる（#2596・#2539）。"""
    results = {1: DesignResult(1, ("a.py",)), 2: None, 3: DesignResult(3, ("b.py",), ("x", "y"))}
    gaps = design_gaps([1, 2, 3], results)
    assert len(gaps) == 2
    assert gaps[0].startswith("#2 ")
    assert gaps[1].startswith("#3 ")


def test_merged_files_keep_every_issue_of_the_theme():
    """主題で束ねた課題の触るファイルを先頭の課題の分だけにしない（#2056）。"""
    assert merged_files([DesignResult(1, ("a.py", "b.py")), DesignResult(2, ("b.py", "c.py"))]) == ("a.py", "b.py", "c.py")


def test_path_present_accepts_a_path_relative_to_a_subdirectory():
    """サブディレクトリからの相対のパスで書いたテストを実在として扱う（#2553・#1811）。"""
    files = ["plugins/x/tests/test_a.py", "docs/a.md"]
    assert path_present("tests/test_a.py", files, "plugins/x")
    assert path_present("./tests/test_a.py", files)
    assert path_present("plugins/x/tests/test_a.py", files)
    assert not path_present("tests/test_b.py", files, "plugins/x")
