"""`ndf-next` のブロックの読み方の宣言（contracts/forms/ndf-next-block.json。#2347 の決定 8・I55）。

この形を写すほかの読み手（サービスのランナー）のテストも同じ例の組を読む。どちらかの読み方が変われば片方が落ちる。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

NDF = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(NDF / "scripts"))
from relay_lib import mark as relay_mark  # noqa: E402

FORM = json.loads((NDF / "contracts" / "forms" / "ndf-next-block.json").read_text(encoding="utf-8"))
RESULT_LINE = re.compile(r"^\s*-\s*結果:\s*(.*?)\s*$", re.M)


def decision_needed(text: str) -> bool:
    """規則の後半: 応答の文の最後の「- 結果:」の行が「判断が要る」。relay.py は conductor がこのときブロックを出さない前提で読まない。"""
    found = RESULT_LINE.findall(text)
    return bool(found) and found[-1] == "判断が要る"


@pytest.mark.parametrize("case", FORM["cases"], ids=lambda c: c["text"][:24])
def test_relay_reads_the_declared_cases(case):
    blocks = relay_mark.next_blocks(case["text"])
    assert blocks == case["blocks"]
    launch = relay_mark._mark_action(blocks, []) == "write" and not decision_needed(case["text"])
    assert launch is case["launch"]


def test_cases_cover_the_rule():
    texts = [c["text"] for c in FORM["cases"]]
    assert any("~~~ndf-next" in t for t in texts) and any("````" in t for t in texts)
    assert {len(c["blocks"]) for c in FORM["cases"]} >= {0, 1, 2}
    assert any(decision_needed(t) for t in texts) and any(c["launch"] for c in FORM["cases"])
