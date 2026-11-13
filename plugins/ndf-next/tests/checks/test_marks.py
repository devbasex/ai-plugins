from __future__ import annotations

from ndf.checks import marks

CODES = frozenset({"NDF201", "NDF502", "NDF701", "BLE001"})


def test_read_marks_keeps_malformed_marks():
    text = "a = 1  # noqa: NDF502 利用者の入力: 未設定は空\nb = 2  # noqa: NDF502\nc = 3  # noqa: E501\n"
    found = marks.read_marks(text, CODES)
    assert found[1] == [marks.Mark(1, ("NDF502",), "利用者の入力", "未設定は空")]
    assert found[2] == [marks.Mark(2, ("NDF502",), "", "")]
    assert 3 not in found


def mark(label: str, reason: str = "理由", codes=("NDF502",)) -> marks.Mark:
    return marks.Mark(1, codes, label, reason)


def test_mark_problem(rules):
    assert marks.mark_problem(mark("ファイル"), "NDF502", None, rules) is None
    assert marks.mark_problem(mark("上限", codes=("NDF201",)), "NDF201", None, rules) is None
    assert "形" in marks.mark_problem(mark("ファイル", ""), "NDF502", None, rules)
    assert "外せない" in marks.mark_problem(mark("ファイル"), "NDF701", None, rules)
    assert "上限" in marks.mark_problem(mark("上限"), "NDF502", None, rules)
    assert "境界の種類" in marks.mark_problem(mark("どこか"), "NDF502", None, rules)
    assert "内部" in marks.mark_problem(mark("ファイル"), "BLE001", ("KeyError",), rules)


def test_apply_splits_counted_allowed_and_unused(rules):
    found = {
        1: [mark("ファイル")],
        2: [marks.Mark(2, ("NDF701",), "ファイル", "理由")],
        5: [marks.Mark(5, ("NDF502",), "ファイル", "理由")],
    }
    applied = marks.apply([(1, "NDF502"), (2, "NDF701"), (3, "NDF502")], found, {}, rules)
    assert applied.counted == [(2, "NDF701", "この規則は印で外せない。直す"), (3, "NDF502", None)]
    assert [(m.line, code) for m, code in applied.unused] == [(5, "NDF502")]
