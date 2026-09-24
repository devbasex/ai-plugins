"""上限の表（#598 / #537 の AC30 / AC31 / AC32）。

監視の上限・無進捗の許容・CLI の上限の既定値は `lib/limits.py` の 1 つの表だけが持つ。
ここでは表の値と、全工程 × 全担当の組の順序、環境変数と引数の解決順、コマンドの
終了コードを確かめる。監視と起動からの呼び出しは各 Skill のテストが確かめる。
"""
from __future__ import annotations

import importlib.util
import itertools
import os
import pathlib
import subprocess
import sys

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
LIMITS = LIB / "limits.py"

PHASES = {
    "review": 1200, "critique": 1200, "propose": 1200, "judge-test-changes": 1200,
    "plan": 1200, "add-tests": 3600, "implement": 3600, "fix": 3600, "final-fix": 3600,
}
STALLS = {"codex": 180, "agy": 480, "kiro": 480, "claude": 900}


@pytest.fixture()
def limits():
    # 表の既定値を読むテストである。実行した人の環境の `MONITOR_*` は、根の
    # `conftest.py` が実行中だけ外す（#678）。
    spec = importlib.util.spec_from_file_location("ndf_lib_limits", LIMITS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LIMITS), *args],
        env={**os.environ, **env}, capture_output=True, text=True,
    )


# ---------- AC30: 表の値 ----------

def test_the_phase_table_holds_the_contract_values(limits) -> None:
    assert limits.PHASE_TIMEOUT == PHASES


def test_five_phases_take_1200_and_four_take_3600(limits) -> None:
    values = sorted(limits.PHASE_TIMEOUT.values())
    assert values.count(1200) == 5 and values.count(3600) == 4


def test_the_agent_table_holds_the_contract_values(limits) -> None:
    assert limits.AGENT_STALL == STALLS


@pytest.mark.parametrize("phase", sorted(PHASES))
def test_the_cli_timeout_is_the_monitor_timeout_plus_120(limits, phase: str) -> None:
    assert limits.cli_timeout(phase, "agy") == PHASES[phase] + 120


# ---------- AC31: 全 28 組の順序 ----------

@pytest.mark.parametrize(("phase", "agent"), sorted(itertools.product(PHASES, STALLS)))
def test_stall_is_below_monitor_is_below_cli(limits, phase: str, agent: str) -> None:
    stall = limits.stall_timeout(agent)
    monitor = limits.monitor_timeout(phase, agent)
    cli = limits.cli_timeout(phase, agent)
    assert stall < monitor < cli, (phase, agent, stall, monitor, cli)


def test_all_36_pairs_are_covered(limits) -> None:
    assert len(list(itertools.product(limits.PHASE_TIMEOUT, limits.AGENT_STALL))) == 36


def test_check_passes_on_the_table(limits) -> None:
    assert limits.check() == []


def test_check_reports_a_broken_pair(limits, monkeypatch) -> None:
    """表を崩すと、崩れた組を返す。上書きではなく表そのものを見ている。"""
    monkeypatch.setitem(limits.AGENT_STALL, "claude", 1200)
    broken = limits.check()
    assert ("review", "claude") in [(b[0], b[1]) for b in broken]


def test_check_ignores_environment_overrides(limits, monkeypatch) -> None:
    """AC31 は既定値の組だけを固定する。上書きの組は AC33 の警告が扱う。"""
    monkeypatch.setenv("MONITOR_TIMEOUT", "60")
    assert limits.check() == []


def test_the_check_command_exits_0() -> None:
    r = _run("check")
    assert r.returncode == 0, r.stderr


# ---------- AC32: 監視の上限の解決順 ----------

def test_explicit_wins_over_everything(limits, monkeypatch) -> None:
    monkeypatch.setenv("MONITOR_TIMEOUT_AGY", "700")
    monkeypatch.setenv("MONITOR_TIMEOUT", "800")
    assert limits.monitor_timeout("review", "agy", 600) == 600


def test_the_per_agent_environment_wins_over_the_shared_one(limits, monkeypatch) -> None:
    monkeypatch.setenv("MONITOR_TIMEOUT_AGY", "700")
    monkeypatch.setenv("MONITOR_TIMEOUT", "800")
    assert limits.monitor_timeout("review", "agy") == 700
    assert limits.monitor_timeout("review", "codex") == 800


def test_the_shared_environment_wins_over_the_phase(limits, monkeypatch) -> None:
    monkeypatch.setenv("MONITOR_TIMEOUT", "1800")
    assert limits.monitor_timeout("implement", "kiro") == 1800


def test_the_phase_default_applies_without_overrides(limits) -> None:
    assert limits.monitor_timeout("fix", "claude") == 3600


def test_an_explicit_zero_is_returned_as_is(limits, monkeypatch) -> None:
    """現状固定。0 は未指定として扱われず、環境変数と表より優先してそのまま返る。"""
    monkeypatch.setenv("MONITOR_TIMEOUT_AGY", "700")
    monkeypatch.setenv("MONITOR_TIMEOUT", "800")
    assert limits.monitor_timeout("review", "agy", 0) == 0


@pytest.mark.parametrize("name", ["MONITOR_TIMEOUT_AGY", "MONITOR_TIMEOUT"])
def test_a_zero_environment_is_returned_as_is(limits, monkeypatch, name: str) -> None:
    """現状固定。環境変数の文字列 "0" も表の値へ落ちず、0 がそのまま返る。"""
    monkeypatch.setenv(name, "0")
    assert limits.monitor_timeout("review", "agy") == 0


def test_a_non_numeric_environment_falls_back(limits, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MONITOR_TIMEOUT", "abc")
    assert limits.monitor_timeout("review", "agy") == 1200
    assert "MONITOR_TIMEOUT" in capsys.readouterr().err


def test_the_cli_timeout_follows_the_resolved_monitor_timeout(limits, monkeypatch) -> None:
    """利用者が監視の上限だけを延ばしても、CLI の上限が先に打ち切らない（決定 12）。"""
    monkeypatch.setenv("MONITOR_TIMEOUT", "1800")
    assert limits.cli_timeout("review", "agy") == 1920


def test_the_stall_resolution_keeps_its_order(limits, monkeypatch) -> None:
    monkeypatch.setenv("MONITOR_STALL", "240")
    monkeypatch.setenv("MONITOR_STALL_AGY", "300")
    assert limits.stall_timeout("agy") == 300
    assert limits.stall_timeout("codex") == 240
    assert limits.stall_timeout("codex", 60) == 60


def test_an_unknown_agent_takes_the_fallback_stall(limits) -> None:
    assert limits.stall_timeout("unknown") == limits.DEFAULT_STALL


# ---------- コマンド ----------

def test_the_cli_timeout_command_prints_seconds() -> None:
    r = _run("cli-timeout", "critique", "agy")
    assert (r.returncode, r.stdout) == (0, "1320\n")


def test_the_cli_timeout_command_reads_the_environment() -> None:
    r = _run("cli-timeout", "review", "agy", MONITOR_TIMEOUT="1800")
    assert (r.returncode, r.stdout) == (0, "1920\n")


def test_the_monitor_timeout_command_prints_seconds() -> None:
    r = _run("monitor-timeout", "implement", "codex", MONITOR_TIMEOUT_CODEX="900")
    assert (r.returncode, r.stdout) == (0, "900\n")


@pytest.mark.parametrize("phase", ["propose-tests", "apply", "reviews", ""])
def test_an_unknown_phase_exits_1(phase: str) -> None:
    """表に無い名前は受けない。別名を持たせると、表の名前と効く上限が 1 対 1 でなくなる。"""
    r = _run("cli-timeout", phase, "agy")
    assert r.returncode == 1
    assert r.stdout == ""


@pytest.mark.parametrize("args", [(), ("bogus",), ("cli-timeout",)])
def test_an_argv_matching_no_form_prints_usage_to_stderr(args: tuple[str, ...]) -> None:
    """どの形にも当たらない argv（引数なし・未知のコマンド・引数不足）は、使い方を
    標準エラーへ出して終了コード 1 を返す。標準出力は空にする。"""
    r = _run(*args)
    assert r.returncode == 1
    assert r.stdout == ""
    assert "monitor-timeout" in r.stderr
