"""worker が書いた結果ファイルの outputs を `ndf worker run` の結果へ渡すところ（`core/worker_outcome.py`。#3908）。

必須の名前の欠けと形の違い・確かめた値だけを渡すことは `tests/worker/test_delegate.py` が見る。ここは ok でない結果ファイルの境界を見る。
"""

from __future__ import annotations

import pytest

from ndf.contracts import result
from ndf.core import worker_outcome

OUTPUTS = {"worktree": "/r/.worktrees/b", "branch": "b"}


@pytest.mark.parametrize("status", ["gate", "stopped"])
def test_outputs_of_a_report_that_is_not_ok_are_not_handed_on(tmp_path, status):
    # 判断を求めた・できなかった作業の outputs は、必須の名前がそろっていても後の工程へ渡さない
    report = {"status": status, "summary": "途中", "outputs": OUTPUTS}
    checked, outputs = worker_outcome.require_outputs(report, list(OUTPUTS))
    got = worker_outcome.outcome_of("worktree", tmp_path / "r.json", checked, [], {"attempts": 1}, outputs=outputs)
    assert outputs is None and "outputs" not in got and got["status"] == status and result.errors(got) == []


def test_a_report_without_outputs_and_no_required_names_stays_ok(tmp_path):
    report = {"status": "ok", "summary": "済んだ"}
    checked, outputs = worker_outcome.require_outputs(report, [])
    got = worker_outcome.outcome_of("impl", tmp_path / "r.json", checked, [], {"attempts": 1}, outputs=outputs)
    assert got["status"] == "ok" and "outputs" not in got and result.errors(got) == []
