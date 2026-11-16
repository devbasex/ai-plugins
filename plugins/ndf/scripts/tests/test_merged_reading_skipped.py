"""merged_lib/reading.py: 同じ head の同じチェックで、後の skipped が前の結論を隠さない（#3445）。

入力は PR #3439 の形を写した statusCheckRollup。push（synchronize）の実行で ndf-next-tests が FAILURE、
本文の編集（edited）の実行で同じ名前のチェックが SKIPPED になった。
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))

from merged_lib.reading import fold_rollup  # noqa: E402

URL = "https://github.com/devbasex/ai-plugins/actions/runs/{run}/job/{job}"


def cr(conclusion, run, started, completed, name="ndf-next-tests"):
    return {
        "__typename": "CheckRun",
        "workflowName": "ndf-next",
        "name": name,
        "status": "COMPLETED",
        "conclusion": conclusion,
        "startedAt": started,
        "completedAt": completed,
        "detailsUrl": URL.format(run=run, job=run + "1"),
    }


PUSH_FAILURE = cr("FAILURE", "43579249317", "2026-11-14T10:00:00Z", "2026-11-14T10:02:00Z")
EDIT_SKIPPED = cr("SKIPPED", "43579411964", "2026-11-14T10:03:00Z", "2026-11-14T10:03:00Z")


def states(rollup):
    latest, superseded = fold_rollup(rollup)
    return [(i.label, i.state) for i in latest], [i.label for i in superseded]


def test_pr3439_a_later_skip_does_not_hide_the_failure():
    assert states([PUSH_FAILURE, EDIT_SKIPPED]) == ([("ndf-next / ndf-next-tests", "failed")], [])
    assert states([EDIT_SKIPPED, PUSH_FAILURE])[0] == [("ndf-next / ndf-next-tests", "failed")]


def test_a_check_that_was_only_skipped_still_passes():
    assert states([EDIT_SKIPPED]) == ([("ndf-next / ndf-next-tests", "passed")], [])


def test_a_newer_success_still_supersedes_an_older_failure():
    newer = cr("SUCCESS", "43579500000", "2026-11-14T10:05:00Z", "2026-11-14T10:07:00Z")
    assert states([PUSH_FAILURE, EDIT_SKIPPED, newer]) == ([("ndf-next / ndf-next-tests", "passed")], ["ndf-next / ndf-next-tests"])
