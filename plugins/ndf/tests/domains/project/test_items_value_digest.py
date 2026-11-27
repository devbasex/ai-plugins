"""書いた値の指紋 `value_digest` の今の振る舞い（記録済みの指紋と一致し続けること）。"""

from __future__ import annotations

import hashlib

import pytest

from ndf.domains.project.items import value_digest


@pytest.mark.parametrize(
    ("value", "canonical"),
    [
        ({}, "{}"),
        ([], "[]"),
        (None, "null"),
        (True, "true"),
        (1, "1"),
        (1.0, "1.0"),
        ("1", '"1"'),
        ({"b": [2, 1], "a": {"d": None, "c": False}}, '{"a":{"c":false,"d":null},"b":[2,1]}'),
        ({"名前": "値 と 空白"}, '{"名前":"値 と 空白"}'),
        ((1, 2), "[1,2]"),
        (float("nan"), "NaN"),
    ],
)
def test_value_digest_is_the_sha256_of_the_canonical_json_in_utf8(value, canonical):
    assert value_digest(value) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(("value", "error"), [({1, 2}, TypeError), ("\ud800", UnicodeEncodeError)])
def test_value_digest_rejects_a_value_it_cannot_encode(value, error):
    with pytest.raises(error):
        value_digest(value)
