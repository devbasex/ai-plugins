from __future__ import annotations

import json

import pytest

from ndf.contracts import version


def _bump(data):
    last = data["history"][-1]["version"]
    return {**data, "history": [*data["history"], {"version": last + 1, "sha256": version.fingerprint()}]}


def test_plugin_contracts_match_the_last_version():
    assert version.errors() == []
    assert version.current() == len(version.declaration()["history"])


@pytest.mark.parametrize(
    ("rel", "change"),
    [
        ("causes.json", lambda d: {**d, "causes": [*d["causes"], {**d["causes"][0], "kind": "new_kind"}]}),
        ("facts/answers.json", lambda t: {**t, "rows": [*t["rows"], {"id": "hold", "label": "保留する"}]}),
        ("schemas/result.schema.json", lambda s: {**s, "required": [*s["required"], "run_id"]}),
        ("schemas/route-chosen.schema.json", lambda s: {**s, "required": [*s["required"], "cause"]}),
    ],
)
def test_contract_change_needs_a_version_bump(contracts_copy, rel, change):
    contracts_copy(rel, change)
    errs = version.errors()
    assert len(errs) == 1 and "版を上げて" in errs[0]
    contracts_copy("version.json", _bump)
    assert version.errors() == []
    assert version.current() == 2


def test_wording_and_whitespace_do_not_change_the_fingerprint(contracts_copy):
    before = version.fingerprint()
    contracts_copy("causes.json", lambda d: {**d, "causes": [{**c, "meaning": c["meaning"] + "。"} for c in d["causes"]]})
    path = contracts_copy.root / "schemas/result.schema.json"
    path.write_text(json.dumps(json.loads(path.read_text(encoding="utf-8")), indent=4), encoding="utf-8")
    assert version.fingerprint() == before


@pytest.mark.parametrize(
    ("change", "expect"),
    [
        (lambda d: {**d, "history": [{**d["history"][0], "version": 2}]}, "1 から 1 つずつ"),
        (lambda d: {**d, "history": [*d["history"], {"version": 2, "sha256": d["history"][0]["sha256"]}]}, "同じ指紋"),
    ],
)
def test_history_rules(contracts_copy, change, expect):
    contracts_copy("version.json", change)
    assert any(expect in e for e in version.errors())


@pytest.mark.parametrize(
    "change",
    [
        lambda d: {**d, "history": []},
        lambda d: {k: v for k, v in d.items() if k != "owner"},
        lambda d: {**d, "history": [{"version": 1, "sha256": "x"}]},
    ],
)
def test_wrong_declaration_stops(contracts_copy, change):
    contracts_copy("version.json", change)
    with pytest.raises(version.VersionError):
        version.errors()
