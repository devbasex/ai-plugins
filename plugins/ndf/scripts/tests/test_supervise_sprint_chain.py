"""設計 PR のマージの前の承認の証跡（`supervise.py design-approved`。#1426 の I1）。"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


pytestmark = pytest.mark.usefixtures("issue_bodies")  # 課題の本文は偽の gh が返す


def queue_words(command: str) -> list[list[str]]:
    """queue のコマンドのステージごとのプラン（--then の区切り）。"""
    words = shlex.split(command)
    words = words[words.index("queue") + 1 :]
    stages, cur = [], []
    skip = False
    for w in words:
        if skip:
            skip = False
            continue
        if w in ("--max", "--done"):
            skip = True
            continue
        if w == "--then":
            stages.append(cur)
            cur = []
            continue
        cur.append(w)
    return [*stages, cur]


def plans_of(stages: list[dict]) -> dict[str, dict]:
    return {Path(p).stem.split("-", 1)[1]: json.loads(Path(p).read_text()) for s in stages for p in s.get("plans", [])}


def test_design_approved_needs_a_label_or_the_users_answer_for_that_pr(tmp_path):
    """I1: 承認ラベルも設計承認ゲートの記録も無ければ 10。別の PR の記録は数えない。記録があればラベルを付けて 0。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from supervise_lib import design_merge

    calls = []

    def gh(labels):
        def run(args):
            calls.append(args)
            return (0, json.dumps(labels)) if args[:2] == ["pr", "view"] else (0, "")

        return run

    state = tmp_path / "state.json"
    state.write_text(json.dumps({"gates": [{"name": "設計承認ゲート", "what": "x", "at": "2026-10-30T00:00:00Z", "by": "user", "pr": 2}]}))
    assert design_merge.cmd_design_approved(1, str(state), gh([]))[1] == 10
    assert design_merge.cmd_design_approved(0, str(state), gh([]))[1] == 10
    assert design_merge.cmd_design_approved(1, None, gh(["design-approved"]))[1] == 0
    assert design_merge.cmd_design_approved(1, None, gh(["design-approved-pending"]))[1] == 10  # 部分一致を数えない
    assert design_merge.cmd_design_approved(1, None, lambda args: (0, "not json"))[1] == 2
    calls.clear()
    out, code = design_merge.cmd_design_approved(2, str(state), gh([]))
    assert (code, out["items"][0]["result"]) == (0, "added")
    assert ["pr", "edit", "2", "--add-label", "design-approved"] in calls
    assert design_merge.cmd_design_approved(2, str(state), lambda args: (1, "boom"))[1] == 2
