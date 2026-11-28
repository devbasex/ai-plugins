"""要求仕様書の形・解析（`ndf.core.requirements_decide`）のテスト（#4735 の詳細設計の「要求仕様書の読み出し」）。"""

from __future__ import annotations

import hashlib

import pytest

from ndf.core.plan import inputs_decide
from ndf.core import requirements_decide as rd

PATH = "issues/issue-7-8-requirements.md"
ORIGIN = "元の Issue: https://github.com/o/r/issues/{}\n"


def _doc_text(*numbers: int, criteria: str = "- [ ] 1. 甲\n- [x] 2. 乙\n") -> str:
    return "# 題\n\n" + "".join(ORIGIN.format(n) for n in numbers) + f"\n## 受け入れ条件\n\n{criteria}\n## 影響\n\n- [ ] 条件でない\n"


def _parse(raw: bytes) -> rd.Doc:
    return rd.parse_doc(PATH, raw, source="tree", commit="a" * 40, repo="o/r")


def test_criterion_digest_is_the_head_of_the_sha256_of_the_sentence() -> None:
    assert rd.criterion_digest("- [ ] 甲が動く") == hashlib.sha256("甲が動く".encode()).hexdigest()[: rd.DIGEST_CHARS]
    assert rd.DIGEST_CHARS == 12


@pytest.mark.parametrize("text", ["- [ ]   甲が動く  ", "- [ ] 1. 甲が動く", "- [X] 甲が動く", "- [x] 12. 甲が動く"])
def test_criterion_digest_ignores_the_mark_the_number_and_the_spaces(text: str) -> None:
    assert rd.criterion_digest(text) == rd.criterion_digest("- [ ] 甲が動く")


def test_criterion_digest_changes_with_the_sentence() -> None:
    assert rd.criterion_digest("- [ ] 甲が動く") != rd.criterion_digest("- [ ] 甲が動き")
    # バージョン番号のような、空白の続かない先頭の数字は文の一部
    assert rd.criterion_digest("- [ ] 11.0 で動く") != rd.criterion_digest("- [ ] 0 で動く")


def test_criteria_of_numbers_the_checkboxes_from_the_top() -> None:
    got = rd.criteria_of(_doc_text(criteria="- [ ] 1. 甲\n- [x] 乙\n- [ ] 3. 丙\n"))
    assert [(c.number, c.text) for c in got] == [(1, "甲"), (2, "乙"), (3, "丙")]
    assert [c.digest for c in got] == [rd.criterion_digest(f"- [ ] {t}") for t in "甲乙丙"]


@pytest.mark.parametrize(("operation", "outcome"), [("取り消す", "公開される"), ("公開する", "下書きのまま")])
def test_criteria_digests_change_with_the_continuation_lines(operation: str, outcome: str) -> None:
    def read(op: str, result: str) -> tuple[rd.Criterion, ...]:
        return rd.criteria_of(_doc_text(criteria=f"- [ ] 前提: 下書きがある\n      操作: {op}\n\n      結果: {result}\n"))

    before, after = read("公開する", "公開される"), read(operation, outcome)
    assert before[0].text == "前提: 下書きがある\n操作: 公開する\n結果: 公開される"
    assert before[0].digest != after[0].digest
    assert rd.criteria_list_digest(before) != rd.criteria_list_digest(after)


def test_criteria_keep_the_brackets_of_the_sentence() -> None:
    lines = ["- [ ] [x] を表示する", "- [ ] [ ] を表示する", "- [ ] - [ ] を表示する"]
    got = rd.criteria_of(_doc_text(criteria="".join(f"{line}\n" for line in lines)))
    assert [c.text for c in got] == ["[x] を表示する", "[ ] を表示する", "- [ ] を表示する"]
    assert [c.digest for c in got] == [rd.criterion_digest(line) for line in lines]


def test_criteria_include_the_indented_code_block() -> None:
    got = rd.criteria_of(_doc_text(criteria="- [ ] 次を出力する\n  ```\n  - [ ] OK\n  ## OK\n  ```\n- [ ] 乙\n"))
    assert [c.text for c in got] == ["次を出力する\n```\n- [ ] OK\n## OK\n```", "乙"]


def test_criterion_digest_rejects_a_sentence_without_the_mark() -> None:
    with pytest.raises(ValueError):
        rd.criterion_digest("甲が動く")


def test_criteria_read_the_code_block_deeper_than_the_list_item() -> None:
    # 項目の本文から 2 つ字下げした囲みの中の `~~~` は中身で、後ろの条件は残る
    got = rd.criteria_of(_doc_text(criteria="- [ ] 次を出力する\n    ```\n  ~~~\n    ```\n- [ ] 乙\n"))
    assert [c.text for c in got] == ["次を出力する\n```\n~~~\n```", "乙"]


def test_criteria_keep_the_indent_and_the_blank_lines_of_the_code() -> None:
    def read(code: str) -> rd.Criterion:
        return rd.criteria_of(_doc_text(criteria=f"- [ ] 次を出力する\n  ```python\n{code}  ```\n"))[0]

    inside = read("  if a:\n      f()\n      g()\n")
    assert inside.text == "次を出力する\n```python\nif a:\n    f()\n    g()\n```"
    assert inside.digest != read("  if a:\n      f()\n  g()\n").digest
    assert inside.digest != read("  if a:\n      f()\n\n      g()\n").digest


@pytest.mark.parametrize("indent", ["  ", "      "])
def test_criteria_do_not_count_the_html_comments(indent: str) -> None:
    def block(example: str) -> str:
        return f"- [ ] 甲 <!-- {example} -->\n{indent}<!--\n- [ ] {example}\n\n{indent}-->\n{indent}結果: 乙\n"

    got = rd.criteria_of(_doc_text(criteria=block("A")))
    assert [c.text for c in got] == ["甲\n結果: 乙"]
    assert got[0].digest == rd.criterion_digest(block("B"))


def test_the_comment_mark_in_the_text_does_not_hide_the_following_criteria() -> None:
    got = rd.criteria_of(_doc_text(criteria="- [ ] 文中の <!-- を残す\n- [ ] 甲\n<!-- 注 -->\n- [ ] 乙\n"))
    assert [c.text for c in got] == ["文中の <!-- を残す", "甲", "乙"]


def test_criteria_keep_the_comment_mark_written_as_code() -> None:
    got = rd.criteria_of(_doc_text(criteria="- [ ] `<!-- 印 -->` を残す\n  ```\n  <!-- 例 -->\n  ```\n"))
    assert [c.text for c in got] == ["`<!-- 印 -->` を残す\n```\n<!-- 例 -->\n```"]


@pytest.mark.parametrize(
    "literal",
    ["`入力例\n    の <!-- {0} --> を扱う`", "`入力例\n      <!-- {0} -->`", "表示は \\<!-- {0} -->", "\\<!-- {0} -->"],
)
def test_criterion_digest_keeps_the_literal_comment_mark(literal: str) -> None:
    got = [rd.criteria_of(_doc_text(criteria=f"- [ ] {literal.format(word)}\n"))[0] for word in ("例", "本番")]
    assert "例" in got[0].text.replace("入力例", "")
    assert got[0].digest != got[1].digest


def test_a_fence_in_a_comment_does_not_hide_the_following_criteria() -> None:
    got = rd.criteria_of(_doc_text(criteria="- [ ] 甲\n\n<details><!--\n\n```\n-->\n</details>\n\n- [ ] 乙\n"))
    assert [c.text.split("\n", 1)[0] for c in got] == ["甲", "乙"]


def test_a_comment_line_keeps_the_code_after_it() -> None:
    def block(inner: str) -> str:
        return f"- [ ] 甲\n  <!-- 説明 -->\n      if ok:\n{inner}run()"

    assert rd.criterion_digest(block("          ")) != rd.criterion_digest(block("      "))


@pytest.mark.parametrize("opening", ["<details><!--", "文\n\n<details>\n<!--", "文\n      <!--"])
def test_the_fences_in_comments_are_parsed_once(opening: str, monkeypatch: pytest.MonkeyPatch) -> None:
    parsed: list[str] = []
    parse = inputs_decide._MARKDOWN.parse
    monkeypatch.setattr(inputs_decide._MARKDOWN, "parse", lambda text: parsed.append(text) or parse(text))
    example = f"{opening}\n\n```\n-->\n\n" * 8
    got = list(inputs_decide.outside_fences(f"{example}## 後".split("\n")))

    assert got[-1][1] == "## 後"
    assert len(parsed) == 1


@pytest.mark.parametrize(
    "opening, closing",
    [("<details><!--", "--> `<!--`"), ("<details><!--", "--> \\<!--"), ("文\n      <!-- 前置き --> <!--", "-->")],
)
def test_the_comment_ends_at_its_close(opening: str, closing: str) -> None:
    body = f"- [ ] 甲\n\n{opening}\n\n```\n{closing}\n\n- [ ] 乙\n"
    assert [c.text.split("\n")[0] for c in rd.criteria_of(_doc_text(criteria=body))] == ["甲", "乙"]


@pytest.mark.parametrize("comment", ["<!-- メモ -->", "<!-- メモ\n\n      -->"])
def test_a_comment_line_inside_a_paragraph_does_not_change_the_digest(comment: str) -> None:
    rest = "\n      a\n        b"
    assert rd.criterion_digest(f"- [ ] 甲\n      {comment}{rest}") == rd.criterion_digest(f"- [ ] 甲{rest}")


@pytest.mark.parametrize("literal", ["`<!--`", "\\<!--"])
def test_the_code_notation_is_not_read_inside_the_html_block(literal: str) -> None:
    body = f"- [ ] 甲\n\n<details>\n<!-- 前置き --> {literal}\n- [ ] 記入例\n-->\n</details>\n\n- [ ] 乙\n"
    assert [c.text.split("\n")[0] for c in rd.criteria_of(_doc_text(criteria=body))] == ["甲", "乙"]


@pytest.mark.parametrize("value", ["\\<!--", "<!--"])
def test_the_comment_mark_in_an_attribute_does_not_hide_the_following_criteria(value: str) -> None:
    body = f'- [ ] 甲\n\n<!-- メモ --> <span title="{value}">補足</span>\n\n- [ ] 乙\n'
    assert [c.text.split("\n")[0] for c in rd.criteria_of(_doc_text(criteria=body))] == ["甲", "乙"]


@pytest.mark.parametrize(
    "block",
    [
        '<details>\n<span title="説明\n<!-- 補足">本文</span>\n</details>',
        '> <details>\n> <span title="説明\n> <!-- 補足">本文</span>',
        '<details><!--\n\n--> <span title="<!--">補足</span>',
        '<details><!--\n\n--> `<!--` <span title="<!--">本文</span>',
        '<details><!--\n\n--> \\<!-- <span title="<!--">本文</span>',
        '<!-- メモ --> <span title="説明\n<!-- 補足">本文</span>',
        '<details><!--\n\n--> <span title="説明\n<!-- 補足">本文</span>',
        '<details><!--\n\n--> <!-- 注記\n--> <span title="説明\n<!-- 補足">本文</span>',
        '<!-- 注 --> <span title="閉じ記号 --> の例\n<!-- 開き記号の例">本文</span>',
        "<details><!--\n\n--> `\n~~~markdown\n<!-- `\n~~~",
    ],
)
def test_the_comment_mark_in_a_multiline_attribute_does_not_hide_the_following_criteria(block: str) -> None:
    body = f"- [ ] 甲\n\n{block}\n\n- [ ] 乙\n"
    assert [c.text.split("\n")[0] for c in rd.criteria_of(_doc_text(criteria=body))] == ["甲", "乙"]


def test_the_text_after_the_close_is_parsed_inline_once(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed: list[str] = []
    parse = inputs_decide._INLINE.inline.parse
    monkeypatch.setattr(inputs_decide._INLINE.inline, "parse", lambda src, *rest: parsed.append(src) or parse(src, *rest))
    list(inputs_decide.outside_fences("<details><!--\n\n--> `<!--` `<!--` `<!--`".split("\n")))

    assert len(parsed) == 1


@pytest.mark.parametrize(
    "after, count",
    [("```\n<!-- 例 -->\n```", 0), ("本文で `<!--` を示す。", 1)],
)
def test_the_lines_after_a_bare_close_are_not_read_ahead(after: str, count: int, monkeypatch: pytest.MonkeyPatch) -> None:
    parsed: list[str] = []
    parse = inputs_decide._INLINE.inline.parse
    monkeypatch.setattr(inputs_decide._INLINE.inline, "parse", lambda src, *rest: parsed.append(src) or parse(src, *rest))
    list(inputs_decide.outside_fences(f"<details><!--\n\n-->\n{after}".split("\n")))

    assert len(parsed) == count


def test_the_marks_of_a_paragraph_are_converted_once(monkeypatch: pytest.MonkeyPatch) -> None:
    converted: list[str] = []
    convert = inputs_decide._token_marks
    monkeypatch.setattr(inputs_decide, "_token_marks", lambda lines, token: converted.append(token.content) or convert(lines, token))
    list(inputs_decide.outside_fences("> `<!--` を説明する".split("\n")))

    assert len(converted) == 1


@pytest.mark.parametrize("body", ["甲\n      <!--\n\nx\n-->", "甲 <!-- a --> と\n      <!--\n\n-->"])
def test_a_paragraph_is_parsed_inline_once(body: str, monkeypatch: pytest.MonkeyPatch) -> None:
    parsed: list[str] = []
    parse = inputs_decide._INLINE.inline.parse
    monkeypatch.setattr(inputs_decide._INLINE.inline, "parse", lambda src, *rest: parsed.append(src) or parse(src, *rest))
    list(inputs_decide.outside_fences(body.split("\n")))

    assert len(parsed) == 1


def test_a_comment_line_before_the_code_does_not_change_the_digest() -> None:
    code = "\n      a\n      b"
    assert rd.criterion_digest(f"- [ ] 甲\n<!-- メモ -->{code}") == rd.criterion_digest(f"- [ ] 甲{code}")


@pytest.mark.parametrize(
    "example",
    [
        "```markdown\n## 受け入れ条件\n\n- [ ] 例\n```\n\n",
        "  ```markdown\n## 受け入れ条件\n\n- [ ] 例\n   ```\n\n",
        # 別の記号の行・短い並び・後ろに語のある行は、囲みを閉じない
        "```text\n~~~\n```\n\n",
        "````markdown\n```\n## 受け入れ条件\n\n- [ ] 例\n````\n\n",
        "~~~\n~~~python\n## 受け入れ条件\n\n- [ ] 例\n~~~~\n\n",
    ],
)
def test_criteria_of_skips_the_headings_in_code_blocks(example: str) -> None:
    assert [c.text for c in rd.criteria_of(example + _doc_text())] == ["甲", "乙"]


def test_criteria_of_returns_nothing_without_checkboxes() -> None:
    assert rd.criteria_of("# 題\n") == ()


def test_criteria_of_rejects_the_same_sentence_twice() -> None:
    with pytest.raises(rd.RequirementsError) as got:
        rd.criteria_of(_doc_text(criteria="- [ ] 1. 甲\n- [ ] 2. 乙\n- [x] 3. 甲\n"))
    assert got.value.cause == "check_failed"
    assert "1" in got.value.detail and "3" in got.value.detail


def test_criteria_list_digest_follows_the_order_of_the_versions() -> None:
    a, b = rd.criteria_of(_doc_text())
    want = hashlib.sha256(f"{a.digest}\n{b.digest}".encode()).hexdigest()[: rd.DIGEST_CHARS]
    assert rd.criteria_list_digest([a, b]) == want
    assert rd.criteria_list_digest([b, a]) != want


def test_parse_doc_reads_the_issues_and_the_criteria() -> None:
    text = _doc_text(8, 7)
    doc = _parse(text.encode())
    assert (doc.path, doc.source, doc.commit, doc.issues, doc.body) == (PATH, "tree", "a" * 40, (7, 8), text)
    assert [c.text for c in doc.criteria] == ["甲", "乙"]
    assert doc.criteria_digest == rd.criteria_list_digest(doc.criteria)


def test_parse_doc_stops_when_an_origin_line_is_missing() -> None:
    with pytest.raises(rd.RequirementsError) as got:
        _parse(_doc_text(7).encode())
    assert got.value.cause == "check_failed"
    assert [g["name"] for g in got.value.gaps] == ["#8"]
    assert all(g["kind"] and g["reason"] for g in got.value.gaps)


def test_parse_doc_stops_on_bytes_that_are_not_utf8() -> None:
    with pytest.raises(rd.RequirementsError) as got:
        _parse(b"\xff\xfe")
    assert got.value.cause == "check_failed" and PATH in got.value.detail


@pytest.mark.parametrize(("mode", "want"), [("standard", True), ("light", True), ("", True), ("legacy-refactor", False)])
def test_needs_requirements_is_false_only_for_legacy_refactor(mode: str, want: bool) -> None:
    assert rd.needs_requirements(mode) is want


def test_lookup_keeps_the_found_documents_and_the_missing_numbers() -> None:
    doc = _parse(_doc_text(7, 8).encode())
    found = rd.Lookup({7: doc, 8: doc}, (9,))
    assert found.docs[7] is found.docs[8] and found.missing == (9,)
