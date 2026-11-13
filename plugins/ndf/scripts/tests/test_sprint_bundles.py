"""課題のまとまりでスプリントを組む（#3081 の I8a・I8b・I8c・I9）と、壊れた計画のレーンで止まること。

レーンのまとまりの読み出し（`parallel_bundles.bundles_of`）を差し替え、`supervise.py new sprint` をプロセスの中で打つ。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SCRIPTS))
import inproc  # noqa: E402
import supervise_lib  # noqa: E402,F401  lib/ を sys.path へ足す
import parallel_bundles as pb  # noqa: E402
import design_results  # noqa: E402
import pr_mode  # noqa: E402

SUPERVISE = SCRIPTS / "supervise.py"
BUNDLES = [pb.Bundle(name="review-scope", issues=[12, 11, 99]), pb.Bundle(name="issue-13", issues=[13])]

pytestmark = pytest.mark.usefixtures("issue_bodies")


def stages(out: Path) -> dict[str, dict]:
    """ステージ名 → {プランのキー: プラン}。"""
    index = json.loads((out / "sprint.json").read_text(encoding="utf-8"))["ステージ"]
    got = {}
    for e in index:
        plans = {}
        for path in e.get("plans") or []:
            key = Path(path).stem.split("-", 1)[1]
            plans[key] = json.loads(Path(path).read_text(encoding="utf-8"))
        got[e["name"]] = plans
    return got


def ids(plan: dict) -> list[str]:
    return [s["id"] for s in plan["steps"]]


def targets(plan: dict) -> set[str]:
    out = set()
    for s in plan["steps"]:
        out |= {s[k] for k in ("next", "on_fail", "skip_to", "gate_next") if k in s}
        out |= set((s.get("on_exit") or {}).values())
    return out


def rows(text: str) -> design_results.DesignResults:
    res = design_results.DesignResults(design_prs=[40])
    head = "## 設計の結果\n\n| 課題 | 扱い | 取り込み先 | 触るファイル |\n| --- | --- | --- | --- |\n"
    res.add(design_results.parse_results(head + text))
    return res


def bundle_plans() -> dict[int, dict]:
    return {12: {"課題": [12, 11], "steps": []}, 13: {"課題": [13], "steps": []}}


def test_bundled_sprint_branch_pr_needs_review(monkeypatch, tmp_path):
    """I8b: まとまりの記録のあるスプリントブランチ宛ては `metrics.review` が真。宛て先の区分は sprint のまま。"""
    assert pr_mode.needs_review("sprint/m2", bundled=True) and not pr_mode.needs_review("sprint/m2")
    steps = inproc.load(SCRIPTS / "pr-steps.py")
    monkeypatch.setattr(pb, "bundles_of", lambda main, lane=None, status=None: BUNDLES)
    assert steps.bundled_sprint(tmp_path, "sprint/m2") and not steps.bundled_sprint(tmp_path, "develop")
    monkeypatch.setattr(pb, "bundles_of", lambda main, lane=None, status=None: None)
    assert not steps.bundled_sprint(tmp_path, "sprint/m2")
    assert pr_mode.pr_target("sprint/m2") == "sprint"
