"""再起動の方針の適用値と工程境界の判定（#2262）。`lib/context_policy.py` の単体。"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import context_policy as cp  # noqa: E402


def git_repo(tmp_path: Path, setting: dict | str | None = None) -> Path:
    root = tmp_path / "repo"
    (root / ".ndf").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    if setting is not None:
        body = setting if isinstance(setting, str) else json.dumps(setting)
        (root / ".ndf" / "context.json").write_text(body)
    return root


# ---------------------------------------------------------------- 適用値（条件 1・2・4・8・9、I1〜I4・I15）


def test_without_setting_the_policy_is_current_with_200k():
    a = cp.resolve_policy(None, {})
    assert (a.policy, a.decide_from, a.limit, a.config_error) == ("current", 200_000, 200_000, None)


def test_trial_setting_gives_initial_values(tmp_path):
    a = cp.resolve_policy(git_repo(tmp_path, {"version": 1, "policy": "trial"}), {})
    assert (a.policy, a.decide_from, a.limit, a.margin) == ("trial", 200_000, 400_000, 50_000)
    assert a.sources["policy"] == "config"


def test_initial_values_are_the_constants():
    assert (cp.TRIAL_DECIDE_FROM, cp.TRIAL_LIMIT, cp.MARGIN, cp.CURRENT_LIMIT) == (200_000, 400_000, 50_000, 200_000)


def test_priority_cli_over_env_over_setting(tmp_path):
    root = git_repo(tmp_path, {"policy": "trial", "trial": {"decide_from": 100_000, "limit": 300_000}})
    assert cp.resolve_policy(root, {}).limit == 300_000
    env = {"NDF_CONTEXT_LIMIT": "250000"}
    a = cp.resolve_policy(root, env)
    assert (a.limit, a.sources["limit"]) == (250_000, "env")
    a = cp.resolve_policy(root, env, {"limit": 220_000})
    assert (a.limit, a.sources["limit"]) == (220_000, "cli")
    a = cp.resolve_policy(root, {"NDF_CONTEXT_POLICY": "current"})
    assert (a.policy, a.limit, a.sources["policy"]) == ("current", 200_000, "env")
    a = cp.resolve_policy(root, {"NDF_CONTEXT_POLICY": "current"}, {"policy": "trial"})
    assert (a.policy, a.sources["policy"]) == ("trial", "cli")


def test_unknown_env_policy_is_ignored_and_recorded(tmp_path):
    a = cp.resolve_policy(None, {"NDF_CONTEXT_POLICY": "fast"})
    assert a.policy == "current" and a.sources["ignored"] == "NDF_CONTEXT_POLICY=fast"


def test_env_limit_lowers_decide_from_in_trial():
    a = cp.resolve_policy(None, {"NDF_CONTEXT_POLICY": "trial", "NDF_CONTEXT_LIMIT": "150000"})
    assert (a.decide_from, a.limit) == (150_000, 150_000)


def test_env_limit_keeps_current_meaning():
    a = cp.resolve_policy(None, {"NDF_CONTEXT_LIMIT": "300000"})
    assert (a.policy, a.limit) == ("current", 300_000)


@pytest.mark.parametrize(
    "body",
    [
        {"version": 2, "policy": "trial"},
        {"policy": "fast"},
        {"policy": "trial", "trial": {"decide_from": 500_000, "limit": 400_000}},
        {"policy": "trial", "trial": {"limit": -1}},
        {"policy": "trial", "margin": -5},
        "{not json",
    ],
)
def test_broken_setting_falls_back_to_current(tmp_path, body):
    a = cp.resolve_policy(git_repo(tmp_path, body), {})
    assert (a.policy, a.limit) == ("current", 200_000)
    assert a.config_error and "context.json" in a.config_error


def test_window_and_model_do_not_choose_the_policy():
    a = cp.resolve_policy(None, {})
    assert a.policy == "current"
    v = cp.decide_boundary(a, 300_000, window=1_000_000)
    assert v.verdict == "拒否" and v.reason == "運用上限"


# ---------------------------------------------------------------- 帯（条件 5・6・7、I5〜I7）


TRIAL = cp.resolve_policy(None, {"NDF_CONTEXT_POLICY": "trial"})
CURRENT = cp.resolve_policy(None, {})


@pytest.mark.parametrize(
    "context,band,verdict",
    [(200_000, "継続帯", "継続"), (200_001, "判断帯", "継続"), (400_000, "判断帯", "継続"), (400_001, "上限超", "拒否")],
)
def test_trial_band_edges(context, band, verdict):
    v = cp.decide_boundary(TRIAL, context, window=None, estimate=None)
    assert (v.band, v.verdict) == (band, verdict)


@pytest.mark.parametrize("context,verdict", [(200_000, "継続"), (200_001, "拒否")])
def test_current_band_edges(context, verdict):
    v = cp.decide_boundary(CURRENT, context, window=1_000_000)
    assert v.verdict == verdict and v.missing == ()


def test_small_window_denies_by_capacity():
    v = cp.decide_boundary(TRIAL, 250_001, window=300_000)
    assert (v.verdict, v.reason, v.effective_limit) == ("拒否", "容量", 250_000)
    v = cp.decide_boundary(TRIAL, 250_000, window=300_000)
    assert v.verdict == "継続" and v.band == "判断帯"


def test_missing_values_are_not_filled():
    v = cp.decide_boundary(TRIAL, None)
    assert (v.verdict, v.basis, v.missing) == ("継続", "値なし", ("context",))
    v = cp.decide_boundary(TRIAL, 300_000)
    assert v.basis == "見込みなし" and v.missing == ("window", "estimate")


def test_cost_decides_the_judgement_band():
    cheap = cp.decide_boundary(TRIAL, 300_000, window=1_000_000, estimate=cp.Estimate(10.0, 5.0))
    assert (cheap.verdict, cheap.reason) == ("拒否", "費用")
    dear = cp.decide_boundary(TRIAL, 300_000, window=1_000_000, estimate=cp.Estimate(5.0, 10.0))
    assert (dear.verdict, dear.basis) == ("継続", "費用")


# ---------------------------------------------------------------- 費用の見込み（条件 10、I8）


def estimates(samples=3, **over):
    body = {
        "restore_context": 60_000,
        "restore_calls": 10,
        "handoff_calls": 8,
        "handoff_output": 4_000,
        "stage_calls": {"skill:cross-review": 40, "*": 30},
        "stage_expiries": {"skill:cross-review": 1, "*": 1},
        "samples": {"restore": samples, "handoff": samples, "stage:skill:cross-review": samples, "stage:*": samples},
    }
    body.update(over)
    return cp.Estimates.from_dict(body)


def test_cost_follows_the_formula():
    e = cp.estimate_cost(estimates(), 300_000, "claude-opus-5-5", "skill:cross-review")
    r, w, c, q = 0.05, 2.0, 300_000, 60_000
    assert e.continue_cost == pytest.approx(40 * r * c + 1 * w * c)
    assert e.restart_cost == pytest.approx(8 * r * c + 5 * 4_000 + w * q + 10 * r * q + 40 * r * q + 1 * w * q)


def test_cost_needs_three_samples():
    assert cp.estimate_cost(estimates(samples=2), 300_000, None, "skill:cross-review") is None
    assert cp.estimate_cost(None, 300_000, None, "plan") is None


def test_cost_falls_back_to_all_stages():
    assert cp.estimate_cost(estimates(), 300_000, None, "plan") is not None


def test_decide_and_cost_do_not_touch_the_outside(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("外部を呼んだ")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(socket, "socket", boom)
    e = cp.estimate_cost(estimates(), 300_000, "x", "plan")
    assert cp.decide_boundary(TRIAL, 300_000, 1_000_000, e) == cp.decide_boundary(TRIAL, 300_000, 1_000_000, e)
