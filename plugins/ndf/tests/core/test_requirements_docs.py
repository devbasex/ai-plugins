"""要求仕様書の形・解析（`ndf.core.requirements_decide`）のテスト（#4735 の詳細設計の「要求仕様書の読み出し」）。"""

from __future__ import annotations

import hashlib

import pytest

from ndf.core import requirements_decide as rd

PATH = "issues/issue-7-8-requirements.md"
ORIGIN = "元の Issue: https://github.com/o/r/issues/{}\n"


def _doc_text(*numbers: int, criteria: str = "- [ ] 1. 甲\n- [x] 2. 乙\n") -> str:
    return "# 題\n\n" + "".join(ORIGIN.format(n) for n in numbers) + f"\n## 受け入れ条件\n\n{criteria}\n## 影響\n\n- [ ] 条件でない\n"


def _parse(raw: bytes) -> rd.Doc:
    return rd.parse_doc(PATH, raw, source="tree", commit="a" * 40, repo="o/r")


def test_criterion_digest_is_the_head_of_the_sha256_of_the_sentence() -> None:
    assert rd.criterion_digest("甲が動く") == hashlib.sha256("甲が動く".encode()).hexdigest()[: rd.DIGEST_CHARS]
    assert rd.DIGEST_CHARS == 12


@pytest.mark.parametrize("text", ["  甲が動く  ", "1. 甲が動く", "- [ ] 甲が動く", "- [x] 12. 甲が動く", "[X] 甲が動く"])
def test_criterion_digest_ignores_the_mark_the_number_and_the_spaces(text: str) -> None:
    assert rd.criterion_digest(text) == rd.criterion_digest("甲が動く")


def test_criterion_digest_changes_with_the_sentence() -> None:
    assert rd.criterion_digest("甲が動く") != rd.criterion_digest("甲が動き")
    # バージョン番号のような、空白の続かない先頭の数字は文の一部
    assert rd.criterion_digest("11.0 で動く") != rd.criterion_digest("0 で動く")


def test_criteria_of_numbers_the_checkboxes_from_the_top() -> None:
    got = rd.criteria_of(_doc_text(criteria="- [ ] 1. 甲\n- [x] 乙\n- [ ] 3. 丙\n"))
    assert [(c.number, c.text) for c in got] == [(1, "甲"), (2, "乙"), (3, "丙")]
    assert [c.digest for c in got] == [rd.criterion_digest(t) for t in "甲乙丙"]


@pytest.mark.parametrize(("operation", "outcome"), [("取り消す", "公開される"), ("公開する", "下書きのまま")])
def test_criteria_digests_change_with_the_continuation_lines(operation: str, outcome: str) -> None:
    def read(op: str, result: str) -> tuple[rd.Criterion, ...]:
        return rd.criteria_of(_doc_text(criteria=f"- [ ] 前提: 下書きがある\n      操作: {op}\n\n      結果: {result}\n"))

    before, after = read("公開する", "公開される"), read(operation, outcome)
    assert before[0].text == "前提: 下書きがある\n操作: 公開する\n結果: 公開される"
    assert before[0].digest != after[0].digest
    assert rd.criteria_list_digest(before) != rd.criteria_list_digest(after)


def test_criteria_of_skips_the_headings_in_code_blocks() -> None:
    example = "```markdown\n## 受け入れ条件\n\n- [ ] 例\n```\n\n"
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
