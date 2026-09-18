"""担当ごとの指標集計の現状固定テスト。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
METRICS = LIB / "metrics.py"


@pytest.fixture(scope="module")
def metrics():
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    spec = importlib.util.spec_from_file_location("ndf_lib_metrics", METRICS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_aggregate_empty_state_has_no_measurements(metrics):
    """現状固定: ラウンドも項目もない状態は全区分が空になる。"""
    assert metrics.aggregate({}) == {
        "impl": {},
        "reviewer": {},
        "unmeasured": [],
        "assumed": [],
    }


def test_aggregate_review_without_findings_has_no_resolution_rate(metrics):
    """現状固定: 指摘の母数が 0 なら解決率は 0.0 ではなく None になる。"""
    state = {
        "rounds": [{
            "round": 1,
            "impl": "claude",
            "impl_model": {"requested": "sonnet", "observed": "sonnet"},
            "reviewers": ["codex"],
            "reviewer_models": {"codex": {"requested": "default"}},
            "reviews": [{"codex": "APPROVE", "findings": []}],
        }],
        "items": [],
    }

    result = metrics.aggregate(state)

    assert result["reviewer"]["codex / default"]["findings"] == 0
    assert result["reviewer"]["codex / default"]["resolution_rate"] is None


def test_format_report_empty(metrics):
    data = metrics.aggregate({})
    report = metrics.format_report(data)
    assert "## 実装担当\n\n（記録なし）" in report
    assert "## レビュー担当\n\n（記録なし）" in report
    assert "## 比較として読むときの限界" in report


def test_format_report_with_data(metrics):
    state = {
        "rounds": [{
            "round": 1,
            "impl": "claude",
            "impl_model": {"requested": "sonnet", "observed": "sonnet"},
            "reviewers": ["codex"],
            "reviewer_models": {"codex": {"requested": "default"}},
            "reviews": [{"codex": "APPROVE", "findings": []}],
        }],
        "items": [],
    }
    data = metrics.aggregate(state)
    report = metrics.format_report(data)
    assert "| claude / sonnet | 1 | 0 | 0 | 1.00 | 0.00 | — | — | 0 |" in report
    assert "| codex / default | 1 | 0 | — | — | 0 |" in report
