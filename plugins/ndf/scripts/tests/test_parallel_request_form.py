"""配布の依頼のメッセージの形（`contracts/forms/parallel-request.json`）を、書き手の検査と送るメッセージで縛る（#2436）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import parallel_lane as pl  # noqa: E402

FORM = json.loads((ROOT / "contracts" / "forms" / "parallel-request.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", FORM["cases"], ids=lambda c: str(c["valid"]))
def test_cases(case):
    assert (pl.request_errors(case["message"]) == []) is case["valid"]


def test_request_id_is_stable_and_well_formed(tmp_path):
    rid = pl.request_id(tmp_path, "lroot")
    assert pl.request_id(tmp_path, "lroot") == rid and pl.REQUEST_ID_RE.match(rid)
