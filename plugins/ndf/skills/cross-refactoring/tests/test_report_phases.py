"""完了報告（#933 の AC26）と、状態が持つ所要の読み方。

報告はフェーズごとの所要・想定最大時間との差・採用と見送りの件数（理由別）・
全体のテストを走らせたかとその理由・判断に Jev を使ったかを持つ。文言ではなく、
状態の値が報告へ渡ることを見る。
"""
from __future__ import annotations

import argparse

from crossref_helpers import make_state_v2


def test_the_report_carries_phases_reasons_whole_test_and_judge(tmp_path, cmd_report,
                                                                env_tmp_dir, capsys):
    path = make_state_v2(
        tmp_path, tmp_path / "work",
        phases={"propose": {"seconds": 270.0}, "plan": {"seconds": 180.0},
                "implement": {"seconds": 600.0}, "verify": {"seconds": 30.0,
                                                             "ended_at": "2026-09-24T10:30:00"}},
        items=[{"id": "I-001", "path": "src/a.py", "symbol": "f", "smell": "long_method",
                "technique": "extract_method", "tier": "high", "status": "verified",
                "estimate": {"test": 0, "implement": 1.3, "verify": 0.2}, "danger": ["D3"],
                "fix_count": 1, "commits": {"test": None, "implement": "abc", "fix": []},
                "seconds": {"implement": 600.0}, "kind": "structure/extract_method"}],
        deferred_items=[{"path": "src/b.py", "symbol": "g", "defer_reason": "budget"},
                        {"path": "src/c.py", "symbol": "h", "defer_reason": "budget"},
                        {"path": "src/d.py", "symbol": "i", "defer_reason": "no_target"}],
        whole_test={"ran": True, "flags": ["D3"], "status": "pass", "seconds": 60.0,
                    "head": "abc", "reverted": False},
        judge={"kind": "runtime", "reason": "private_repo", "failures": 0},
        final_gate={"mode": "test", "status": "passed", "fix_rounds": 0, "checks": [],
                    "whole_test_reused": True},
        plan={"table_source": "history"},
    )
    env_tmp_dir(path)
    cmd_report.cmd_report(argparse.Namespace(id=130, metrics=True))
    out = capsys.readouterr().out
    # フェーズの所要（分）が並ぶ
    assert "| propose | 4.5 |" in out and "| implement | 10.0 |" in out
    # 見送りは理由別の件数で出る（内訳は改修計画にある）
    assert "budget 2" in out and "no_target 1" in out and "test_failed 0" in out
    # 全体のテストを走らせた理由（印）と Jev を使わなかった理由
    assert "D3" in out and "private_repo" in out
    # 想定最大時間（60 分）が報告に出る
    assert "60 分" in out
    # --metrics は種類別の件数と所要
    assert "structure/extract_method" in out


def test_the_report_gives_the_gap_between_the_budget_and_the_elapsed_time(tmp_path, cmd_report,
                                                                          env_tmp_dir, capsys):
    """所要は `init` の開始から最終ゲートの最後の検査まで。差は想定最大時間からの残り。"""
    path = make_state_v2(
        tmp_path, tmp_path / "work",
        started_at="2026-09-24T10:00:00", budget_minutes=60,
        final_gate={"mode": "test", "status": "passed", "fix_rounds": 0,
                    "checks": [{"at": "2026-09-24T10:45:00"}]},
    )
    env_tmp_dir(path)
    cmd_report.cmd_report(argparse.Namespace(id=130, metrics=False))
    out = capsys.readouterr().out
    assert "所要: 45.0 分" in out
    assert "差 +15.0 分" in out
