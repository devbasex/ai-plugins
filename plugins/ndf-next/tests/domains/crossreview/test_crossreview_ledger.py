"""指摘の台帳（drive.FindingLedger）の規則。外部の起動・git を使わずに確かめる。"""

from __future__ import annotations

from ndf.domains.crossreview import drive
from ndf.domains.crossreview import finish_decide as fin


def _row(fid: str, text: str, round_no: int, outcome: str = fin.UNRESOLVED, reason=None, by="codex", section=None) -> dict:
    return {
        "id": fid,
        "key": text.lower(),
        "text": text,
        "section": section,
        "round": round_no,
        "outcome": outcome,
        "reason": reason,
        "by": by,
    }


def test_enter_numbers_new_findings_per_round_and_ignores_duplicates_and_blanks() -> None:
    ledger = drive.FindingLedger()
    raised = [
        {"text": " 一つ目 ", "by": "codex", "section": "a.md#節"},
        {"text": "一つ目", "by": "kiro"},
        {"text": "  ", "by": "kiro"},
        {"text": 3, "by": "kiro"},
        {"text": "二つ目", "by": "kiro", "section": " "},
    ]
    assert ledger.enter(1, raised) == frozenset({"一つ目", "二つ目"})
    assert list(ledger.rows.values()) == [_row("r1-1", "一つ目", 1, section="a.md#節"), _row("r1-2", "二つ目", 1, by="kiro")]


def test_enter_skips_deferred_findings_and_reopens_fixed_ones() -> None:
    ledger = drive.FindingLedger([_row("r1-1", "直した", 1, fin.FIXED), _row("r1-2", "見送った", 1, fin.SKIPPED, "別で直す")])
    assert ledger.enter(2, [{"text": "直した", "by": "kiro"}, {"text": "見送った", "by": "kiro"}]) == frozenset({"直した", "見送った"})
    assert list(ledger.rows.values()) == [_row("r1-1", "直した", 2), _row("r1-2", "見送った", 1, fin.SKIPPED, "別で直す")]
    assert ledger.skipped_texts() == ["見送った"]


def test_rows_without_an_id_are_not_taken_in() -> None:
    assert list(drive.FindingLedger([_row("r1-1", "a", 1), {"text": "id が無い"}, "x"]).rows) == ["r1-1"]


def test_pending_lists_open_findings_and_undecided_handed_items() -> None:
    ledger = drive.FindingLedger([_row("r1-1", "a", 1), _row("rebuild-1", "b", 1, fin.FIXED), _row("rebuild-2", "c", 1)])
    handed = [fin.Handed("rebuild-1", "b"), fin.Handed("rebuild-2", "c"), fin.Handed("rebuild-3", "d")]
    open_, undecided = ledger.pending(handed)
    assert [f["id"] for f in open_] == ["r1-1", "rebuild-2"]
    assert undecided == [fin.Handed("rebuild-2", "c"), fin.Handed("rebuild-3", "d")]


def test_apply_fix_takes_decided_outcomes_and_ignores_unknown_ids() -> None:
    ledger = drive.FindingLedger([_row("r1-1", "a", 1), _row("r1-2", "b", 1)])
    items = [
        {"id": "r1-1", "outcome": fin.FIXED, "reason": "  "},
        {"id": "r1-2", "outcome": fin.UNRESOLVED},
        {"id": "nope", "outcome": fin.FIXED},
        {"id": "rebuild-1", "outcome": fin.SKIPPED, "reason": "後で"},
        "x",
    ]
    reported = ledger.apply_fix(items, [fin.Handed("rebuild-1", "足す")], 2)
    assert reported == [items[0], items[2], items[3]]
    assert list(ledger.rows.values()) == [
        _row("r1-1", "a", 1, fin.FIXED),
        _row("r1-2", "b", 1),
        {
            "id": "rebuild-1",
            "key": None,
            "text": "足す",
            "section": None,
            "round": 2,
            "outcome": fin.SKIPPED,
            "reason": "後で",
            "by": "rebuild",
        },
    ]


def test_reopen_and_findings() -> None:
    ledger = drive.FindingLedger([_row("r1-1", "a", 1, fin.FIXED), _row("r1-2", "b", 1, fin.SKIPPED, "理由"), {"id": "x", "outcome": "?"}])
    ledger.reopen(["r1-1", "nope"])
    assert ledger.rows["r1-1"]["outcome"] == fin.UNRESOLVED
    assert ledger.findings() == (fin.Finding("r1-1", "a", fin.UNRESOLVED, None), fin.Finding("r1-2", "b", fin.SKIPPED, "理由"))
