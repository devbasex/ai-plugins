"""ラウンドの上限と、1 ラウンド目の範囲（#3780。`test_crossreview_run.py` の行数の上限のため分けた）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.domains.crossreview import finish_decide as fin

from .test_crossreview_run import Fake, Recording, _run, _state


@pytest.mark.parametrize("purpose", ["code", "design"])
def test_passed_max_rounds_is_the_limit(wt: Path, tmp_path: Path, capsys, monkeypatch, purpose: str) -> None:
    """受け入れ条件 3: `--max-rounds` を渡せば、目的によらずその数で止まる。"""
    fake = Fake(tmp_path / "st", {"codex": [["一つ目"], ["二つ目"], ["三つ目"]], "kiro": [[], [], []]})
    _run(capsys, monkeypatch, fake, wt, "--purpose", purpose, "--max-rounds", "2")
    state = _state(fake.state_dir)
    assert len(state["rounds"]) == 2
    assert state["rounds"][-1]["verdict"] != fin.CONVERGED


@pytest.mark.parametrize("purpose", ["code", "design"])
def test_first_round_reads_the_full_diff(wt: Path, tmp_path: Path, capsys, monkeypatch, purpose: str) -> None:
    """受け入れ条件 4: 1 ラウンド目は、相手役の全員が差分の全体を読む。"""
    fake = Recording(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    _run(capsys, monkeypatch, fake, wt, "--purpose", purpose)
    assert _state(fake.state_dir)["rounds"][0]["scope"] == "full"
    first = [prompt for _, name, prompt in fake.prompts if name.startswith("round1-")]
    assert len(first) == 2 and all("範囲: full" in p for p in first)
