from __future__ import annotations

from ndf.checks import suppressions

CODES = frozenset({"NDF201", "NDF502", "NDF701", "BLE001"})


def test_read_suppressions_keeps_malformed_suppressions():
    text = "a = 1  # noqa: NDF502 利用者の入力: 未設定は空\nb = 2  # noqa: NDF502\nc = 3  # noqa: E501\n"
    found = suppressions.read_suppressions(text, CODES)
    assert found[1] == [suppressions.Suppression(1, ("NDF502",), "利用者の入力", "未設定は空")]
    assert found[2] == [suppressions.Suppression(2, ("NDF502",), "", "")]
    assert 3 not in found


def suppression(label: str, reason: str = "理由", codes=("NDF502",)) -> suppressions.Suppression:
    return suppressions.Suppression(1, codes, label, reason)


def test_suppression_problem(rules):
    assert suppressions.suppression_problem(suppression("ファイル"), "NDF502", None, rules) is None
    assert suppressions.suppression_problem(suppression("上限", codes=("NDF201",)), "NDF201", None, rules) is None
    assert "形" in suppressions.suppression_problem(suppression("ファイル", ""), "NDF502", None, rules)
    assert "外せない" in suppressions.suppression_problem(suppression("ファイル"), "NDF701", None, rules)
    assert "上限" in suppressions.suppression_problem(suppression("上限"), "NDF502", None, rules)
    assert "境界の種類" in suppressions.suppression_problem(suppression("どこか"), "NDF502", None, rules)
    assert "内部" in suppressions.suppression_problem(suppression("ファイル"), "BLE001", ("KeyError",), rules)


def test_apply_splits_counted_allowed_and_unused(rules):
    found = {
        1: [suppression("ファイル")],
        2: [suppressions.Suppression(2, ("NDF701",), "ファイル", "理由")],
        5: [suppressions.Suppression(5, ("NDF502",), "ファイル", "理由")],
    }
    applied = suppressions.apply([(1, "NDF502"), (2, "NDF701"), (3, "NDF502")], found, {}, rules)
    assert applied.counted == [(2, "NDF701", "この規則は印で外せない。直す"), (3, "NDF502", None)]
    assert [(m.line, code) for m, code in applied.unused] == [(5, "NDF502")]
