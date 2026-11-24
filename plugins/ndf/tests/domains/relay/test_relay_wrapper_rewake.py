"""ラッパーが起床の hook の登録を、確かめたバージョンの子にだけ渡す（#4068 の受け入れ条件 3・13）。

`test_relay_wrapper.py` が 1,000 行の規則に近いため、#4068 の分をこのファイルに置く（実装計画の「修正対象」の頭）。
"""

from __future__ import annotations

import json
import time

from ndf.domains.relay import accounts, claude_args, wrapper
from ndf.domains.relay.account_decide import Usage
from relay_children import FakeChild, put_usages, register, run_wrapper

USER_SETTINGS = json.dumps({"env": {"X": "1"}})
LIMITED = b"  \xe2\x8e\xbf  You've hit your individual spend limit \xc2\xb7 resets 10:40pm\r\n"


def _settings_of(argv: list) -> list:
    return [v for o, v in claude_args.options(argv[1:]) if o == "--settings"]


def _switching(tmp_path):
    def prepare(_d, state):
        store = register(tmp_path, "team", "main")
        put_usages(
            store,
            [Usage("team", 50.0, 50.0, None, None, False), Usage("main", 5.0, 5.0, None, None, False)],
            fetched_at=time.time() - 3600,
        )
        accounts.use(state, "team")

    return prepare


def test_user_settings_stay_once_on_the_first_launch_and_on_resume(tmp_path):
    # 4068:3（変えない側）: 利用者の --settings は、初回の起動と --resume の再開でそのまま 1 つだけ
    _, spawn, _ = run_wrapper(
        tmp_path, [FakeChild([LIMITED], None), FakeChild([], 0)], _switching(tmp_path), args=("--settings", USER_SETTINGS)
    )
    (first, env1), (second, env2) = spawn.calls
    assert second[1] == "--resume"
    assert _settings_of(first) == [USER_SETTINGS] and _settings_of(second) == [USER_SETTINGS]
    assert "NDF_RELAY_REWAKE" not in env1 and "NDF_RELAY_REWAKE" not in env2


def test_no_settings_are_added_while_no_version_is_listed(tmp_path):
    # 実装の PR ではバージョンの一覧が空なので、どの子にも起床の hook の登録を渡さない
    _, spawn, _ = run_wrapper(tmp_path, [FakeChild([LIMITED], None), FakeChild([], 0)], _switching(tmp_path))
    for argv, env in spawn.calls:
        assert _settings_of(argv) == [] and "NDF_RELAY_REWAKE" not in env
    assert wrapper.CLAUDE == spawn.calls[0][0][0]


# ---------------------------------------------------------------- バージョンの門（#4068 の受け入れ条件 3・13）

import pytest  # noqa: E402

from ndf.adapters import cli_floor  # noqa: E402
from ndf.contracts import limits  # noqa: E402
from ndf.domains.hooks import registration  # noqa: E402
from ndf.domains.relay import account_choice, signals, wrapper_launch  # noqa: E402
from relay_children import rows_of  # noqa: E402

LISTED = ("2.1.330", "2.1.332")


@pytest.fixture
def gate(monkeypatch):
    """一覧を `LISTED` にし、子の claude の実行ファイルとそのバージョンを差し替える（`--version` は打たない）。"""
    found = {"version": "2.1.330"}
    real = limits.texts
    monkeypatch.setattr(limits, "texts", lambda name, key: LISTED if name == "cache.rewake" else real(name, key))
    monkeypatch.setattr(cli_floor, "resolve", lambda argv0, env=None, cwd=None: "/opt/claude/2.1.330/claude")
    monkeypatch.setattr(wrapper_launch, "recorded_version", lambda exe, env, cwd: found["version"])
    return found


def _first(tmp_path, **changes):
    _, spawn, w = run_wrapper(tmp_path, [FakeChild([], 0)], **changes)
    (argv, env) = spawn.calls[0]
    return argv, env, w


@pytest.mark.parametrize(
    ("version", "added"), [("2.1.330", True), ("2.1.332", True), ("2.1.331", False), ("2.1.329", False), ("2.1.333", False), (None, False)]
)
def test_only_listed_versions_get_the_registration(tmp_path, gate, version, added):
    gate["version"] = version
    argv, env, w = _first(
        tmp_path, environ={"NDF_RELAY_REWAKE": "1", "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"), "NDF_STATE_HOME": str(tmp_path / "old")}
    )
    want = [json.dumps(registration.rewake_settings())] if added else []
    assert _settings_of(argv) == want
    assert (env.get("NDF_RELAY_REWAKE") == "1") is added and ("NDF_RELAY_REWAKE" in env) is added
    row = rows_of(w, "account")[0]
    assert (row["rewake"], row["claude_version"], row["rewake_ready"]) == (added, version, True)


def test_registration_lands_right_after_the_head(tmp_path, gate):
    argv, _, _ = _first(tmp_path)
    assert argv[1] == "--session-id" and argv[3] == "--settings" and argv[5:7] == ["--model", "x"]


def test_launch_record_says_whether_the_registration_was_given(tmp_path, gate, monkeypatch):
    seen = []
    real = signals.set_launch
    monkeypatch.setattr(
        signals,
        "set_launch",
        lambda d, launch, rewake=False, claude_version=None: (
            seen.append((rewake, claude_version)) or real(d, launch, rewake, claude_version)
        ),
    )
    _first(tmp_path)
    assert (True, "2.1.330") in seen


@pytest.mark.parametrize("user", [("--settings", USER_SETTINGS), ("--settings=x.json",)])
def test_user_settings_win_even_for_a_listed_version(tmp_path, gate, user):
    argv, env, w = _first(tmp_path, args=user)
    assert "rewake" not in " ".join(argv) and "NDF_RELAY_REWAKE" not in env
    assert rows_of(w, "account")[0]["rewake_ready"] is False


def test_unreachable_wake_adds_nothing_and_logs_why(tmp_path, gate, monkeypatch):
    monkeypatch.setattr(registration, "rewake_unreachable", lambda: "届かない")
    argv, env, w = _first(tmp_path)
    assert _settings_of(argv) == [] and "NDF_RELAY_REWAKE" not in env
    assert rows_of(w, "rewake_off")[0]["reason"] == "届かない"


def test_merged_settings_keep_one_settings_with_the_key_hooks():
    key = json.dumps({"apiKeyHelper": "h", "hooks": {"ConfigChange": [{"hooks": []}]}})
    got = account_choice.merged_settings(["claude", "--settings", key, "--session-id", "s"], json.dumps(registration.rewake_settings()))
    assert got.count("--settings") == 1
    merged = json.loads(got[2])
    assert (
        merged["apiKeyHelper"] == "h"
        and "ConfigChange" in merged["hooks"]
        and merged["hooks"]["Stop"][0]["hooks"][0]["asyncRewake"] is True
    )


@pytest.mark.parametrize(("key", "version", "added"), [(False, "2.1.330", True), (True, "2.1.332", True), (False, "2.1.333", False)])
def test_metered_child_on_another_executable_gets_it_after_verify(tmp_path, monkeypatch, key, version, added):
    from relay_children import make_settings

    w = wrapper.Wrapper(make_settings(tmp_path), spawn=None)
    w.rewake = wrapper_launch.RewakePlan(True, LISTED, json.dumps(registration.rewake_settings()), "/opt/claude/a")
    monkeypatch.setattr(w, "_gate_after", lambda g, k: version)
    base = json.dumps({"apiKeyHelper": "h"}) if key else None
    built = ["claude", "--settings", base, "--session-id", "s"] if key else ["claude", "--session-id", "s", "--model", "x"]
    gate = wrapper_launch.Gate("metered", 1, {}, ({}, True), "/opt/claude/b", False, None)
    got, on, seen = w._gate_built(gate, built, key)
    assert (on, seen, got.count("--settings")) == (added, version, 1 if key or added else 0)
    assert ("asyncRewake" in " ".join(got)) is added
