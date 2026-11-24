"""配る構成の設定（`contracts/data/toolchain.json`）の読み手（#3687 の決定 3）。"""

from __future__ import annotations

import re

import pytest

from ndf import contracts
from ndf.contracts import toolchain


def test_floors_are_major_minor_pairs():
    assert toolchain.python_floor() == toolchain.version_tuple(toolchain.table()["python"]["floor"])
    assert toolchain.hook_floor() == toolchain.version_tuple(toolchain.table()["python"]["hook_floor"])
    assert toolchain.hook_floor() <= toolchain.python_floor()


def test_uv_version_and_installer_digest():
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", toolchain.uv_version())
    assert re.fullmatch(r"[0-9a-f]{64}", toolchain.uv_installer_sha256())


@pytest.mark.parametrize(("text", "expected"), [("3.11", (3, 11)), ("3.9", (3, 9)), ("10.0", (10, 0))])
def test_version_tuple(text, expected):
    assert toolchain.version_tuple(text) == expected


def _drop_uv(data):
    del data["uv"]
    return data


def _extra_key(data):
    data["python"]["ceiling"] = "3.14"
    return data


def _bad_floor(data):
    data["python"]["floor"] = "3"
    return data


def _bad_digest(data):
    data["uv"]["installer_sha256"] = "XYZ"
    return data


@pytest.mark.parametrize("edit", [_drop_uv, _extra_key, _bad_floor, _bad_digest])
def test_malformed_table_is_refused(contracts_copy, edit):
    contracts_copy(toolchain.TABLE, edit)
    with pytest.raises(toolchain.ToolchainError, match="形が違う"):
        toolchain.table()


def test_missing_table_is_refused(contracts_copy):
    (contracts_copy.root / toolchain.TABLE).unlink()
    contracts.use_dir(contracts_copy.root)
    with pytest.raises(toolchain.ToolchainError):
        toolchain.table()


def test_table_is_read_again_after_the_contracts_dir_changes(contracts_copy):
    assert toolchain.uv_version() != "9.9.9"

    def bump(data):
        data["uv"]["version"] = "9.9.9"
        return data

    contracts_copy(toolchain.TABLE, bump)
    assert toolchain.uv_version() == "9.9.9"
