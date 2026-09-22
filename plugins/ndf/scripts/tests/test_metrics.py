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


def test_aggregate_current_metrics_for_unmeasured_models_and_missing_verdicts() -> None:
    """現状固定。分離理由と判定なしレビューを担当ごとに記録する。"""
    state = {
        "items": [{"item_id": "R2-001", "status": "done"}],
        "rounds": [
            {
                "round": 2,
                "impl": "claude",
                "impl_model": {
                    "requested": "claude-opus",
                    "observed": "claude-sonnet",
                },
                "reviewers": ["claude", "agy", "kiro", "codex"],
                "reviewer_models": {
                    "claude": {"requested": "claude-sonnet", "observed": "claude-sonnet"},
                    "agy": {"requested": "gpt-5", "observed": None},
                    "kiro": {"requested": "auto", "observed": None},
                    "codex": {"requested": None, "observed": None},
                },
                "items": ["R2-001"],
                "reviews": [
                    {"claude": "APPROVE", "findings": []},
                    {"claude": "APPROVE", "agy": "APPROVE", "findings": []},
                ],
            }
        ],
    }

    result = metrics.aggregate(state)

    # モデル不一致は警告されるが、現状では指定モデルの実装集計に残る。
    assert list(result["impl"]) == ["claude / claude-opus"]
    assert result["unmeasured"] == [
        "round 2: ⚠ claude: 指定したモデル claude-opus と実際に動いたモデル "
        "claude-sonnet が食い違っています。比較には使えません",
        "round 2: kiro の auto はラウンドごとに違うモデルが動きうるため、"
        "レビュー担当の集計から分離する",
        "round 2: codex はモデルを指定しておらず、実際に動いたモデルも取得できないため、"
        "レビュー担当の集計から分離する",
    ]
    assert set(result["reviewer"]) == {"agy / gpt-5", "claude / claude-sonnet"}
    assert result["reviewer"]["claude / claude-sonnet"]["reviews"] == 2
    assert result["reviewer"]["agy / gpt-5"]["reviews"] == 1
    assert result["reviewer"]["claude / claude-sonnet"]["agreement_rate"] == 1.0
    assert result["reviewer"]["agy / gpt-5"]["agreement_rate"] == 1.0


def test_format_report_current_structure() -> None:
    """現状固定。format_report の見出し・表の行・注意書きを含む出力構造を記録する。"""
    report_metrics = {
        "impl": {
            "codex / gpt-5": {
                "rounds": 1,
                "applied": 1,
                "abandoned": 0,
                "first_review_approval_rate": 1.0,
                "avg_fix_rounds": 0.0,
                "budget_exceeded_rate": 0.0,
                "test_failure_rate": 0.0,
                "seconds": 120.0,
            }
        },
        "reviewer": {
            "claude / sonnet": {
                "reviews": 1,
                "findings": 2,
                "resolution_rate": 0.5,
                "agreement_rate": 1.0,
                "seconds": 30.0,
            }
        },
        "unmeasured": ["round 2: kiro の auto は分離"],
        "assumed": ["round 1: codex は前提で数える"],
    }

    report = metrics.format_report(report_metrics)

    # 見出しが含まれること
    assert "## 実装担当" in report
    assert "## レビュー担当" in report
    assert "## 集計から分離したラウンド" in report
    assert "## 指定値で代用したラウンド" in report
    assert "## 比較として読むときの限界" in report

    # 各表の要点（行データ）が含まれること
    assert "| codex / gpt-5 |" in report
    assert "| claude / sonnet |" in report

    # unmeasured / assumed / caveats のリスト項目が含まれること
    assert "- round 2: kiro の auto は分離" in report
    assert "- round 1: codex は前提で数える" in report
    for caveat in metrics.COMPARISON_CAVEATS:
        assert f"- {caveat}" in report

    # 全体の行数を現状の値で固定する
    assert len(report.splitlines()) == 29


def test_format_report_empty_tables_emits_no_records() -> None:
    """現状固定。impl と reviewer が空のときに「（記録なし）」が出力されることを記録する。"""
    report_metrics = {
        "impl": {},
        "reviewer": {},
        "unmeasured": [],
        "assumed": [],
    }

    report = metrics.format_report(report_metrics)

    # 実装担当とレビュー担当の見出し直後に「（記録なし）」が出ること
    assert "## 実装担当\n\n（記録なし）" in report
    assert "## レビュー担当\n\n（記録なし）" in report

    # unmeasured / assumed が空のときは見出しが出ないこと
    assert "## 集計から分離したラウンド" not in report
    assert "## 指定値で代用したラウンド" not in report

    # 比較の限界の見出しと注意書きが残ること
    assert "## 比較として読むときの限界" in report
    for caveat in metrics.COMPARISON_CAVEATS:
        assert f"- {caveat}" in report

    # 完全な出力行の一致を固定する
    expected_lines = [
        "## 実装担当",
        "",
        "（記録なし）",
        "",
        "## レビュー担当",
        "",
        "（記録なし）",
        "",
        "## 比較として読むときの限界",
        "",
        *[f"- {caveat}" for caveat in metrics.COMPARISON_CAVEATS],
    ]
    assert report.splitlines() == expected_lines
