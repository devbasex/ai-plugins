"""リリースの形の表と、設定のスキーマの `release.form` の列挙（#3615 の決定 1・受け入れ条件 1・13）。"""

from __future__ import annotations

import pytest

from ndf import contracts
from ndf.contracts import facts
from ndf.core import production_delivery
from ndf.domains.release import forms_decide

SCHEMA = "schemas/ndf-settings/project.schema.json"


def _schema_forms() -> set[str]:
    doc = contracts.load_json(SCHEMA)
    return set(doc["$defs"]["BranchStrategy"]["properties"]["release"]["properties"]["form"]["enum"])


def test_table_rows_match_the_schema_enum() -> None:
    """表の行の id の集合と、スキーマの列挙が一致する（片方だけに足すと落ちる）。"""
    table, schema = set(facts.ids(forms_decide.TABLE)), _schema_forms()
    assert table - schema == set(), f"表にだけある形: {sorted(table - schema)}"
    assert schema - table == set(), f"スキーマにだけある形: {sorted(schema - table)}"


@pytest.mark.parametrize("name", ["package-plugin", "service", "mobile", "procedure", "document"])
def test_supported_forms_return_their_row(name: str) -> None:
    form = forms_decide.form_of(name)
    assert form.id == name and form.supported
    assert forms_decide.reach_line(form).startswith("- 届き方: ")


def test_documents_share_one_value() -> None:
    """今の NDF のスライド・文書・表計算・ページは document の 1 行で受ける。"""
    assert "スライド" in forms_decide.form_of("document").label


def test_unsupported_form_stops_with_the_name_table_and_reason() -> None:
    """受け入れ条件 13・I1: 扱わない形は形の名前・表の在りか・理由を出して止まる。"""
    with pytest.raises(forms_decide.FormUnsupported) as got:
        forms_decide.form_of("desktop")
    text = str(got.value)
    assert "desktop" in text and "release-forms.json" in text and "11.x" in text


def test_unknown_form_lists_the_known_forms() -> None:
    with pytest.raises(forms_decide.FormUnsupported, match="取れる形: package-plugin"):
        forms_decide.form_of("kiosk")


# --- #4513: 本番に届く届け先の行を 1 つ選ぶ ----------------------------------------------------------------------------------

PROD = {"target": "本番", "kind": "manual", "trigger": "手で出す", "channel": "production", "versioned": False}


@pytest.mark.parametrize(("channel", "want"), [("production", True), (None, True), ("base", False)])
def test_reaches_takes_the_production_and_the_unset_channel(channel, want):
    assert forms_decide.reaches({"channel": channel}, "production") is want
    assert forms_decide.reaches({}, "production") is True


@pytest.mark.parametrize(
    "rows",
    [
        [PROD],
        [{**PROD, "channel": "base", "target": "検証"}, PROD],
        [{**PROD, "branch": "main"}],
    ],
)
def test_production_delivery_is_the_one_row_that_reaches_production(rows):
    want = [r for r in rows if r["channel"] != "base"][0]
    assert production_delivery.production_delivery(_settings(rows)) == want


@pytest.mark.parametrize("rows", [[], [PROD, {**PROD, "target": "別"}], [{**PROD, "target": ""}], "x"])
def test_production_delivery_is_none_unless_one_row_fits_the_context(rows):
    assert production_delivery.production_delivery(_settings(rows)) is None


def _settings(rows) -> dict:
    return {"release": {"version_store": {"kind": "none"}, "deliveries": rows}}
