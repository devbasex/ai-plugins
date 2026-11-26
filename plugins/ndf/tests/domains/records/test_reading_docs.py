"""読ませたものの記録（reading-docs.jsonl）と、承認の質問の決まった形（#4446 の Task 2。受け入れ条件 2・4・6）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.contracts import facts
from ndf.domains.records import reading
from ndf.domains.records import reading_decide as rd
from ndf.domains.release import forms_decide

HEAD = "a" * 40
C1 = "c" * 40


@pytest.fixture(autouse=True)
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "state"))
    return tmp_path / "state"


def _read(check: str, name: str, hash_: str, rnd: int = 1, verdict: str = "passed", **more) -> dict:
    gated = name in (reading.QUESTION, reading.MATERIAL)
    row = {
        "kind": "read",
        "repo": "o/r",
        "check": check,
        "branch": "design/x",
        "gate": "design" if gated else None,
        "pr": 7 if gated else None,
        "name": name,
        "hash": hash_,
        "reader": reading.reader_of(name),
        "round": rnd,
        "verdict": verdict,
        **more,
    }
    if verdict == "remained":
        row.setdefault("quotes", [{"quote": "通じない文", "why": "理由"}])
    return row


def _done(check: str, gate=None, pr=None, **more) -> dict:
    return reading.done_row({"repo": "o/r", "check": check, "branch": "design/x", "gate": gate, "pr": pr}, **more)


# --- 承認の質問の決まった形と、承認資料のハッシュ ------------------------------------------------------------

REMAINED = [_read("k1", reading.MATERIAL, "h", 2, "remained", quotes=[{"quote": "あの件", "why": "指す先が無い"}])]


def test_the_questions_and_the_material_hashes_are_fixed_by_their_inputs() -> None:
    """条件 2・4: 質問はゲート・PR・題・形から決まった形で作り、承認資料の読ませた本文は末尾の 1 組のマーカーの範囲だけを除く。"""
    (q,) = reading.gate_questions("design", 7, "設計の題", None)
    assert q["question"] == "設計 Pull Request #7（設計の題）をマージしてよいですか"
    assert [o["label"] for o in q["options"]] == ["承認する", "変更を求める"] and all(o["description"] for o in q["options"])
    assert "metadata" not in q
    (q,) = reading.gate_questions("release", 9, "11.0.34 のリリース", "mobile")
    assert q["question"] == "リリースの Pull Request #9（11.0.34 のリリース）を承認し、アプリをストアの審査に提出してよいですか"
    key = rd.question_key(reading.gate_questions("release", 9, "題", "service"))
    assert key == rd.question_key(reading.gate_questions("release", 9, "  題\n", "service"))
    assert key != rd.question_key(reading.gate_questions("release", 9, "題", "package-plugin"))
    for form in (None, "desktop", "unknown"):
        with pytest.raises(reading.FormMissing):
            reading.gate_questions("release", 9, "題", form)
    for fid in facts.ids(forms_decide.TABLE):
        row = facts.row(forms_decide.TABLE, fid)
        assert bool(row.get("approve")) == row["supported"], fid
    # 残った文のセクション: 同じ行から同じ文字列。行が無ければ空
    section = reading.remained_section(REMAINED)
    assert section == reading.remained_section(list(REMAINED)) and reading.remained_section([]) == ""
    assert section.count(reading.BEGIN) == section.count(reading.END) == 1 and "あの件" in section
    body = "# 承認資料\n\n本文\n"
    read, whole = reading.material_hashes(body + section, section)
    assert read == reading.sha256(body) and whole == reading.sha256(body + section) != read
    # 末尾の 1 組でない・内側が違う・基準に残った文が無い、のどれでも何も除かない
    for text, expected in [
        (body + section + "足した文\n", section),
        (body + section + "\n" + reading.BEGIN + "\n" + reading.END + "\n", section),
        (body + section.replace(reading.END, "- 足した指摘\n" + reading.END), section),
        (body + section, ""),
    ]:
        read, whole = reading.material_hashes(text, expected)
        assert read == whole == reading.sha256(text)
    assert reading.row_fingerprint({"a": 1, "b": [1, 2]}) == reading.row_fingerprint({"b": [1, 2], "a": 1})
    assert reading.row_fingerprint({"a": 1}) != reading.row_fingerprint({"a": 2})


# --- 記録の読み書きと reads の作り方 ----------------------------------------------------------------------------


def test_the_records_are_read_by_completed_checks(state: Path) -> None:
    """条件 1・6: 完了の行のある確認の行だけを使い、文書はブランチ、質問と承認資料はゲートと PR で引く。"""
    reading.append_doc(_read("k1", "issues/x.md", "b1"))
    for wrong in ({"kind": "read", "repo": "o/r"}, _read("k1", "issues/x.md", "b1", 1, "remained", quotes=[])):
        with pytest.raises(reading.ReadingInvalid):
            reading.append_doc(wrong)
    assert [r["name"] for r in reading.doc_rows()] == ["issues/x.md"] and reading.docs_path().parent == state
    assert reading.materials_path("o/r", "design", 7) == state / "reading-materials" / "o__r" / "design-7.md"
    assert reading.materials_path("o/other", "design", 7) != reading.materials_path("o/r", "design", 7)
    rows = [
        _read("k1", "issues/x.md", "b1"),
        _read("k1", reading.MATERIAL, "m1", 2, "remained"),
        _done("k1", "design", 7),
        _read("k2", "issues/x.md", "b2"),  # 完了の行が無い
        _read("k2", reading.MATERIAL, "m2", 2, "remained"),
        _read("k3", "issues/x.md", "b3", branch="other"),
        _read("k3", reading.MATERIAL, "m3"),
        _done("k3", "release", 7),
    ]
    assert reading.latest_read(rows, ("o/r", "design/x", "issues/x.md"))["hash"] == "b1"
    assert reading.latest_read(rows, ("o/r", "design", 8, reading.MATERIAL)) is None
    assert [r["hash"] for r in reading.remained_of(rows, "o/r", "design", 7)] == ["m1"]
    assert reading.remained_of(rows, "o/r", "release", 7) == [] and reading.last_done(rows, "o/r", "design", 7)["check"] == "k1"
    pending = {"kind": "review_pending", "repo": "o/r", "check": "k1", "branch": "design/x", "commit": C1, "docs": ["issues/x.md"]}
    assert reading.pending_review([pending], "o/r", "design/x") == pending and reading.pending_review([pending], "o/r", "other") is None
    for kind in ("review_resolved", "review_cancelled"):
        assert (
            reading.pending_review([pending, {"kind": kind, "repo": "o/r", "branch": "design/x", "commit": C1}], "o/r", "design/x") is None
        )
    back = {"kind": "sent_back", "repo": "o/r", "check": "k1", "gate": "design", "pr": 7, "targets": ["title"]}
    assert reading.sent_back([back], "o/r", "design", 7) and not reading.sent_back([back], "o/r", "release", 7)
    # reads: 新しい確認の最後の回（回の大小で選ばない）。途中で止まった確認と別の中身は使わない。今回の結果は完了の行が無くても使う
    past = [
        _read("k1", "issues/x.md", "b1", 1, "failed"),
        _read("k1", "issues/x.md", "b1", 2, "remained"),
        _read("k1", "issues/z.md", "old"),
        _done("k1"),
        _read("k2", "issues/x.md", "b1", 1, "passed"),
        _done("k2"),
        _read("k3", "issues/y.md", "y1"),
        # 同じ名前と中身でも、別のリポジトリ・別のブランチ（文書）・別の PR とゲート（承認資料と質問）の行は使わない
        _read("k4", "issues/z.md", "new", repo="o/other"),
        _read("k4", "issues/z.md", "new", branch="other"),
        _read("k4", reading.MATERIAL, "old-m", pr=8),
        _read("k4", reading.QUESTION, "old-q", gate="release"),
        _done("k4"),
    ]
    current = [_read("k9", reading.QUESTION, "q"), _read("k9", reading.MATERIAL, "m", 1, "failed"), _read("k9", reading.MATERIAL, "m", 2)]
    hashes = {"issues/x.md": "b1", "issues/y.md": "y1", "issues/z.md": "new", reading.QUESTION: "q", reading.MATERIAL: "m"}
    base = {"repo": "o/r", "branch": "design/x", "gate": "design", "pr": 7}
    got = reading.select_reads(current, past, hashes, {reading.MATERIAL}, base)
    assert got == [
        {"name": "issues/x.md", "reader": reading.ENGINEER, "round": 1, "result": "passed"},
        {"name": reading.QUESTION, "reader": reading.APPROVER, "round": 1, "result": "passed"},
        {"name": reading.MATERIAL, "reader": reading.APPROVER, "round": 2, "result": "remained"},
    ]
    elsewhere = {reading.QUESTION: "old-q", reading.MATERIAL: "old-m"}
    assert reading.select_reads([], past, elsewhere, set(), base) == []
