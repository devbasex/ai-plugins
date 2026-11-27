"""プランを組む前の入力の確かめ（inputs_decide。N2b）。"""

from __future__ import annotations

import pytest

from ndf import contracts
from ndf.core.plan.inputs_decide import (
    DesignResult,
    absorbed_missing,
    criteria,
    design_handlings,
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


def test_a_cell_delegating_files_to_a_split_document_names_the_linked_document():
    """#2453 の再現: 触るファイルのセルが分割した設計文書へのリンクで委ねていれば、リンクの中をパスに数えず委ねた文書として返す。"""
    cell = "`a.py`。ループの側の触るファイルは [issue-1-design-lane-loop.md](issue-1-design-lane-loop.md) にある"
    assert files_cell(cell) == (("a.py",), ("issue-1-design-lane-loop.md",))
    assert files_cell("`a.py`、`b.py`") == (("a.py", "b.py"), ())


# --- 設計の結果の扱いと統合先、単位のファイルの形と上限（#3702 の Task 1） ---

RESULTS = """## 設計の結果

前書き

| 課題 | 扱い | 統合先 | 触るファイル |
| --- | --- | --- | --- |
| #7 | 実装する | — | `a.py` |
| #8 | マージする | #7 | |
| #9 | 閉じる | — | — |
| #10 | 実装する | #4051 の実装の PR | `b.py` |
| #13 | マージする | #7 の受け入れ条件へ | |
| 合計 | x | y | z |

| 触るファイル | 課題 | 扱い |
| --- | --- | --- |
| `e.py` | #7 | 閉じる |

```md
| #11 | 実装する | — | `c.py` |
```

## 未確認のまま残ること

| #12 | 実装する | — | `d.py` |
"""


def test_design_handlings_read_the_handling_and_the_target_of_each_issue_in_the_results_section():
    # 統合先は「マージする」の行の、課題の番号だけのセルから読む。実装先の PR を書いた「実装する」の行（#10）と、番号に文が
    # 続くセル（#13）は None。後続の補足の表（#7 を「閉じる」とする）・セクションの外・コードブロックの中の行は読まない
    assert design_handlings(RESULTS) == {
        7: ("実装する", None),
        8: ("マージする", 7),
        9: ("閉じる", None),
        10: ("実装する", None),
        13: ("マージする", None),
    }


def test_design_handlings_find_the_columns_by_the_header_and_return_empty_without_the_section():
    swapped = "## 設計の結果\n\n| 扱い | 課題 | 触るファイル |\n| --- | --- | --- |\n| 閉じる | #3 | — |\n"
    assert design_handlings(swapped) == {3: ("閉じる", None)}
    assert design_handlings("## 設計\n\n| 課題 | 扱い | 統合先 |\n| --- | --- | --- |\n| #1 | 実装する | — |\n") == {}
    # 区切りの行の先頭の `|` を省いた表も、同じ表として読む
    bare_rule = "## 設計の結果\n\n| 課題 | 扱い | 統合先 |\n--- | --- | ---\n| #2 | マージする | #1 |\n"
    assert design_handlings(bare_rule) == {2: ("マージする", 1)}
    # 見出しの次が区切りでない表は対象の表ではない
    assert design_handlings("## 設計の結果\n\n| 課題 | 扱い |\n| #1 | 実装する |\n") == {}


UNITS = "workflow/review-units.schema.json"
# 設計書の「データ構造」の例
UNIT_FILE = {
    "version": 1,
    "issues": [3672],
    "tasks": [
        {
            "id": "T1",
            "title": "既定の表を置く",
            "files": ["plugins/ndf/contracts/domains/a.json"],
            "lines": 320,
            "criteria": ["3672:2"],
            "after": [],
        },
        {"id": "T2", "title": "読む部品", "files": [".github/workflows/x.yml"], "lines": 0, "criteria": [], "after": ["T1"], "issue": 3672},
    ],
    "units": [
        {"name": "コントラクト", "tasks": ["T1"], "after": []},
        {
            "name": "読む部品",
            "tasks": ["T2"],
            "after": [{"unit": "コントラクト", "reason": "既定の表を読む", "issue": 3672}],
            "actual_lines": 12,
        },
    ],
}


def _task(**changes) -> dict:
    return {**UNIT_FILE, "tasks": [{**UNIT_FILE["tasks"][0], **changes}]}


def test_the_unit_file_of_the_design_example_fits_the_schema():
    assert contracts.schema_errors(UNIT_FILE, UNITS) == []


@pytest.mark.parametrize(
    "path", ["/a.py", "a/", "a//b.py", "./a.py", "a/./b.py", "a/../b.py", "a/..", "..", "a*.py", "a?.py", "a[b].py", ""]
)
def test_the_unit_file_refuses_paths_that_are_not_a_single_concrete_file(path):
    assert contracts.schema_errors(_task(files=[path]), UNITS)


@pytest.mark.parametrize(
    "doc",
    [
        {**UNIT_FILE, "version": 2},
        {**UNIT_FILE, "order": 1},
        {**UNIT_FILE, "issues": []},
        {**UNIT_FILE, "tasks": []},
        {**UNIT_FILE, "units": []},
        {**UNIT_FILE, "units": [{"name": "a", "tasks": [], "after": []}]},
        _task(lines=-1),
        _task(lines=1.5),
        _task(id="Task 1"),
        _task(criteria=["3672:1-3"]),
        _task(after=["1"]),
        _task(order=1),
        {**UNIT_FILE, "units": [{"name": "a", "tasks": ["T1"], "after": [{"unit": "b", "reason": "r", "order": 2}]}]},
        {**UNIT_FILE, "units": [{"name": "a", "tasks": ["T1"], "after": [], "merge": 1}]},
    ],
)
def test_the_unit_file_refuses_other_shapes_and_order_numbers(doc):
    # 実行順とマージの順は依存から導く値で、ファイルに書かない（要求の受け入れ条件 3）
    assert contracts.schema_errors(doc, UNITS)


def test_the_unit_file_requires_the_lines_and_the_title_of_each_task():
    for key in ("title", "lines", "id", "files"):
        task = {k: v for k, v in UNIT_FILE["tasks"][0].items() if k != key}
        assert contracts.schema_errors({**UNIT_FILE, "tasks": [task]}, UNITS), key


def test_a_task_may_omit_its_issue_only_when_the_file_has_a_single_issue():
    # 課題が 1 件なら省いたタスクはその課題に属する。2 件以上では、省くと所属の課題を決められないので拒む
    bare = {k: v for k, v in UNIT_FILE["tasks"][0].items() if k != "issue"}
    assert contracts.schema_errors({**UNIT_FILE, "tasks": [bare]}, UNITS) == []
    two = {**UNIT_FILE, "issues": [3672, 3698]}
    assert contracts.schema_errors({**two, "tasks": [bare]}, UNITS)
    assert contracts.schema_errors({**two, "tasks": [{**bare, "issue": 3698}]}, UNITS) == []


# 設定 workflow.review_units の形の SSOT。設定のスキーマ（.ndf/project.json の検証）の workflow はここを参照する
REVIEW_UNITS = contracts.schema_path("ndf-settings/project") + "#/$defs/ReviewUnits"


def test_the_project_declares_the_limits_of_a_unit_with_either_or_both_values():
    assert contracts.schema_errors({"target_lines": 300, "max_lines": 800}, REVIEW_UNITS) == []
    assert all(contracts.schema_errors(ok, REVIEW_UNITS) == [] for ok in ({"max_lines": 800}, {"target_lines": 300}, {}))
    for wrong in ({"target_lines": 0}, {"max_lines": "800"}, {"max_lines": 1.5}, {"lines": 800}):
        assert contracts.schema_errors(wrong, REVIEW_UNITS), wrong
    project = contracts.schema_path("ndf-settings/project")
    assert contracts.schema_errors({"version": 1, "workflow": {"review_units": {"lines": 800}}}, project)


# --- 現状固定（I-003:acceptance_decide.read_checks と check_kinds の重複を寄せる前に、両方の読み方を記録する） ---

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
