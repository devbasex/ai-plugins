"""プランを組む前の入力の確かめ（inputs_decide。N2b）。"""

from __future__ import annotations

from ndf.core.plan.inputs_decide import (
    DesignResult,
    absorbed_missing,
    criteria,
    design_gaps,
    files_cell,
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
    """受け入れ条件のセクションを持たない本文を通さない（#2246）。"""
    assert section_lack(BODY) is None
    assert "セクションが無い" in section_lack("## 目的\n\nx\n")
    assert "条件が無い" in section_lack("## 受け入れ条件\n\n説明だけ\n")


def test_kinds_lack_lists_criteria_without_a_kind():
    """区分の無い受け入れ条件を拾う（#2357）。"""
    assert kinds_lack(BODY) == [2]
    assert kinds_lack("## 受け入れ条件\n\n- [ ] A\n") == [1]


def test_absorbed_missing_lists_lost_criteria_per_issue():
    """統合した課題の受け入れ条件が統合先から落ちていれば拾う（#2274）。"""
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


def test_a_cell_delegating_files_to_a_split_document_stops():
    """#2453 の再現: 触るファイルのセルが分割した設計文書へのリンクで委ねていれば、拾ったパスだけで組まずに止まる。"""
    cell = "`a.py`。ループの側の触るファイルは [issue-1-design-lane-loop.md](issue-1-design-lane-loop.md) にある"
    files, delegated = files_cell(cell)
    assert (files, delegated) == (("a.py",), ("issue-1-design-lane-loop.md",))
    gaps = design_gaps([1], {1: DesignResult(1, files, delegated=delegated)})
    assert len(gaps) == 1 and "issue-1-design-lane-loop.md" in gaps[0]
    assert files_cell("`a.py`、`b.py`") == (("a.py", "b.py"), ())


# --- 現状固定（I-003: acceptance_decide.read_checks と check_kinds の重複を寄せる前に、両方の読み方を記録する） ---

CHECKS_TABLE = """## 受け入れ条件の確認

説明

| 手段 | 区分 | 条件 |
| --- | --- | --- |
| `test_a` | マージ前 | 1 |
| — | リリース後 | 2. 補足 |
|  | マージ前 | 3 |
| — | マージ前 | 4 |
| x | 未定 | 5 |
| y | マージ前 | 番号なし |
| 短い |
| `pytest t.py` | マージ前 | 1 |

```md
| z | マージ前 | 6 |
```

### 小見出しはセクションの中
| w | リリース後 | 7 |

## 次のセクション
| v | マージ前 | 8 |
"""


def test_current_read_checks_and_check_kinds_over_the_same_table():
    from ndf.domains.loop.acceptance_decide import Check, read_checks
    from ndf.core.plan.inputs_decide import check_kinds

    # 列の順は見出しで決まる。同じ番号は後の行が勝つ。手段の無いマージ前の行は read_checks だけが読まない。
    # 小見出しの後の行は別の表。見出しが無ければ最初の表の列の順で読み、最初の表に無い番号だけを足す（#3886 の r4-1・r5-1）
    assert read_checks(CHECKS_TABLE) == {
        1: Check("マージ前", "`pytest t.py`"),
        2: Check("リリース後", ""),
        7: Check("リリース後", ""),
    }
    assert check_kinds(CHECKS_TABLE) == {1: "マージ前", 2: "リリース後", 3: "マージ前", 4: "マージ前", 7: "リリース後"}


def test_current_read_checks_and_check_kinds_without_a_usable_table():
    from ndf.domains.loop.acceptance_decide import Check, read_checks
    from ndf.core.plan.inputs_decide import check_kinds

    # 手段の列が無い表: read_checks は空、check_kinds は読む（CRLF も読む）
    no_means = "## 受け入れ条件の確認\r\n\r\n| 条件 | 区分 |\r\n| --- | --- |\r\n| 1 | マージ前 |\r\n"
    assert (read_checks(no_means), check_kinds(no_means)) == ({}, {1: "マージ前"})
    # 見出しの行だけ・セクションが無い・見出しが違う
    for body in (
        "## 受け入れ条件の確認\n| 条件 | 区分 | 手段 |\n",
        "",
        "## 受け入れ条件の確認(旧)\n\n| 条件 | 区分 | 手段 |\n|---|---|---|\n| 1 | リリース後 | x |\n",
    ):
        assert (read_checks(body), check_kinds(body)) == ({}, {})
    # 見出しの行末の空白は許す
    trailing = "## 受け入れ条件の確認 \n\n| 条件 | 区分 | 手段 |\n|---|---|---|\n| 1 | リリース後 | x |\n"
    assert (read_checks(trailing), check_kinds(trailing)) == ({1: Check("リリース後", "")}, {1: "リリース後"})


# --- 現状固定（I-016: section_lines と design_tests_decide の囲みの読み方の重複を寄せる前に、両方の読み方を記録する） ---


def test_current_section_lines_fences_headings_and_boundaries():
    from ndf.core.plan.inputs_decide import section_lines

    body = (
        "前\n## 対象\r\na\n   ```\n## 囲みの中ではない\nb\n   ```\n```\n## 囲みの中\n~~~\nc\n~~~\n```\n"
        "### 小見出し\n# 上の見出し\nd\n## 対象\ne\n"
    )
    # 字下げした囲みは囲みとして扱わない。最初の見出しのセクションだけを読み、`#` / `##` の見出しで終わる
    assert section_lines(body, "## 対象") == ["a", "   ```"]
    assert section_lines("## 対象\n", "## 対象") == [""]
    assert section_lines("x", "## 対象") is None
    assert section_lines("## 対象x\n", "## 対象") is None
    assert section_lines("  ## 対象\n", "## 対象") is None
    # `###` 以下と、空白の無い `##` では終わらない
    assert section_lines("## 対象\n#### 深い\nz\n##不正\ny\n## 次", "## 対象") == ["#### 深い", "z", "##不正", "y"]


def test_current_design_target_lines_fences_and_sections():
    from ndf.domains.loop.design_tests_decide import target_lines

    text = (
        "## テスト設計\n\n`a`\n  ```\n`b`\n  ```\n```\n## 構成要素\n```\n> 引用\n- **採らなかった案**: `x`\n"
        "### 小\n`y`\n## 別\n`z`\n## 決定の記録\r\n`w`\n"
    )
    # 字下げした囲みも囲みとして扱う。行番号は 1 始まり。`###` は採らなかった案を解くがセクションは変えない
    assert target_lines(text) == [
        ("テスト設計", 2, ""),
        ("テスト設計", 3, "`a`"),
        ("テスト設計", 13, "`y`"),
        ("決定の記録", 17, "`w`"),
    ]
