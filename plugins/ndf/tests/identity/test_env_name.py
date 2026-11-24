"""環境変数の名前をコントラクトのキーから引く読み手（`identity.env_name`。#4126 の決定 2・3）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from env_contract import use_env_contract

from ndf import identity


def _rows(*rows: object) -> str:
    return json.dumps({"rows": list(rows)})


def test_the_name_is_the_id_of_the_only_row_with_the_key(tmp_path, monkeypatch):
    text = _rows({"id": "PLAIN"}, {"id": "ONE", "key": "one"}, {"id": "TWO", "key": "two"})
    use_env_contract(monkeypatch, tmp_path / "root", text)
    assert (identity.env_name("one"), identity.env_name("two")) == ("ONE", "TWO")


@pytest.mark.parametrize(
    "rows",
    [[{"id": "PLAIN"}], [{"id": "ONE", "key": "one"}, {"id": "OTHER", "key": "one"}]],
    ids=["no-row", "two-rows"],
)
def test_a_key_without_exactly_one_row_is_an_error(tmp_path, monkeypatch, rows):
    use_env_contract(monkeypatch, tmp_path / "root", _rows(*rows))
    with pytest.raises(identity.EnvNameError, match="one"):
        identity.env_name("one")


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(None, id="missing"),
        pytest.param("{", id="not-json"),
        pytest.param("[]", id="table-list"),
        pytest.param("{}", id="no-rows"),
        pytest.param('{"rows": null}', id="rows-null"),
        pytest.param('{"rows": {}}', id="rows-dict"),
        pytest.param(_rows({"id": "ONE", "key": "one"}, "x"), id="row-str"),
        pytest.param(_rows({"id": "ONE", "key": ["one"]}), id="key-list"),
        pytest.param(_rows({"id": "ONE", "key": "one"}, {"id": "X", "key": ""}), id="key-empty"),
        pytest.param(_rows({"id": "", "key": "one"}), id="id-empty"),
        pytest.param(_rows({"key": "one"}), id="id-missing"),
    ],
)
def test_a_broken_contract_is_an_env_name_error(tmp_path, monkeypatch, text):
    use_env_contract(monkeypatch, tmp_path / "root", text)
    with pytest.raises(identity.EnvNameError):
        identity.env_name("one")


def test_the_contract_is_read_once_and_a_failure_is_read_again(tmp_path, monkeypatch):
    use_env_contract(monkeypatch, tmp_path / "root", None)
    with pytest.raises(identity.EnvNameError):
        identity.env_name("one")
    path = identity.ROOT / "contracts" / "domains" / "env.json"
    path.parent.mkdir(parents=True)
    path.write_text(_rows({"id": "ONE", "key": "one"}), encoding="utf-8")
    reads = []
    real = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **kw: (reads.append(self), real(self, *a, **kw))[1])
    assert [identity.env_name("one"), identity.env_name("one")] == ["ONE", "ONE"]
    assert reads == [path]
