"""担当ごとの指標集計に対する現状固定テスト。"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

SPEC = importlib.util.spec_from_file_location("metrics", LIB / "metrics.py")
assert SPEC is not None and SPEC.loader is not None
metrics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metrics)


def test_aggregate_current_metrics_for_representative_state() -> None:
    """現状固定。代表的な状態辞書から得られる全バケットと比率を記録する。"""
    state = {
        "items": [
            {"item_id": "R1-001", "status": "done", "budget_exceeded": True},
            {"item_id": "R1-002", "status": "abandoned", "test_failed": True},
        ],
        "rounds": [
            {
                "round": 1,
                "impl": "codex",
                "impl_model": {"requested": "gpt-5", "observed": None},
                "reviewers": ["claude", "agy"],
                "reviewer_models": {
                    "claude": {"requested": "sonnet", "observed": "sonnet"},
                    "agy": {"requested": "gpt-5", "observed": None},
                },
                "items": ["R1-001", "R1-002"],
                "durations": {"apply": 100, "fix": 20},
                "fix_rounds": 2,
                "reviewer_seconds": {"claude": 30, "agy": 40},
                "reviews": [
                    {
                        "claude": "REQUEST_CHANGES",
                        "agy": "REQUEST_CHANGES",
                        "findings": [
                            {"reviewer": "claude", "resolved": True},
                            {"reviewer": "claude", "resolved": False},
                            {"reviewer": "agy", "resolved": True},
                        ],
                    },
                    {
                        "claude": "APPROVE",
                        "agy": "REQUEST_CHANGES",
                        "findings": [],
                    },
                ],
            }
        ],
    }

    assert metrics.aggregate(state) == {
        "impl": {
            "codex / gpt-5": {
                "rounds": 1,
                "applied": 1,
                "abandoned": 1,
                "fix_rounds": 2,
                "budget_exceeded": 1,
                "test_failed": 1,
                "first_review_total": 1,
                "first_review_approved": 0,
                "seconds": 120.0,
                "first_review_approval_rate": 0.0,
                "avg_fix_rounds": 2.0,
                "budget_exceeded_rate": 0.5,
                "test_failure_rate": 0.5,
            }
        },
        "reviewer": {
            "agy / gpt-5": {
                "reviews": 2,
                "findings": 1,
                "findings_resolved": 1,
                "verdict_pairs": 2,
                "verdict_agreements": 1,
                "seconds": 40.0,
                "resolution_rate": 1.0,
                "agreement_rate": 0.5,
            },
            "claude / sonnet": {
                "reviews": 2,
                "findings": 2,
                "findings_resolved": 1,
                "verdict_pairs": 2,
                "verdict_agreements": 1,
                "seconds": 30.0,
                "resolution_rate": 0.5,
                "agreement_rate": 0.5,
            },
        },
        "unmeasured": [],
        "assumed": [
            "round 1: codex は指定した gpt-5 で動いた前提で数える"
            "（実測不可）（実装担当）",
            "round 1: agy は指定した gpt-5 で動いた前提で数える"
            "（実測不可）（レビュー担当）",
        ],
    }
