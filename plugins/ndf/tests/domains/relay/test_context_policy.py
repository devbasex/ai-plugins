"""再起動の方針の解決（`ndf relay context resolve`。#3569）。判定の記録・較正・集計のテストは test_context_report にある。"""

from __future__ import annotations

import json

import pytest

from ndf.domains.records import files
from ndf.domains.relay import context_policy


def _setting(root, data) -> None:
    path = files.path(root, context_policy.setting_id())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def test_resolve_defaults_without_setting(tmp_path):
    got = context_policy.resolve(tmp_path, None, {})
    assert (got.policy, got.decide_from, got.limit, got.margin) == ("current", 200_000, 200_000, 50_000)
    assert got.sources["policy"] == "default" and got.config_error is None


def test_resolve_trial_from_setting_and_env_overrides(tmp_path):
    _setting(tmp_path, {"version": 1, "policy": "trial", "trial": {"decide_from": 150_000, "limit": 300_000}})
    got = context_policy.resolve(tmp_path, None, {})
    assert (got.policy, got.decide_from, got.limit) == ("trial", 150_000, 300_000)
    assert got.sources["limit"] == "config"
    lowered = context_policy.resolve(tmp_path, None, {"NDF_CONTEXT_LIMIT": "100000"})
    assert (lowered.decide_from, lowered.limit, lowered.sources["limit"]) == (100_000, 100_000, "env")
    cli_current = context_policy.resolve(tmp_path, "current", {"NDF_CONTEXT_POLICY": "trial"})
    assert (cli_current.policy, cli_current.sources["policy"]) == ("current", "cli")
    ignored = context_policy.resolve(tmp_path, None, {"NDF_CONTEXT_POLICY": "fast"})
    assert ignored.sources["ignored"] == "NDF_CONTEXT_POLICY=fast"


@pytest.mark.parametrize(
    "data",
    ["{not json", {"version": 2}, {"policy": "fast"}, {"trial": []}, {"trial": {"decide_from": 5, "limit": 1}}, {"margin": -1}],
)
def test_broken_setting_falls_back_to_current(tmp_path, data):
    _setting(tmp_path, data)
    got = context_policy.resolve(tmp_path, None, {})
    assert got.policy == "current" and got.limit == 200_000
    assert got.config_error
