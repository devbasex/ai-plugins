"""監視が工程から上限を引く（#598 / #537 の AC30 / AC32 / AC33）。

`monitor.py --phase <工程>` の監視の上限は `--timeout` → `MONITOR_TIMEOUT_<担当>` →
`MONITOR_TIMEOUT` → 上限の表の順で決まる。解決した値は監視の開始時に標準エラーへ
`hard timeout <秒>s` の形で出る。無進捗の許容が監視の上限以上になった担当は警告される。

pid ファイルには終わったプロセスの pid を書き、監視を直ちに終わらせる（上限まで待たない）。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

_LIB = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "lib"
_MONITOR_LIB = _LIB / "monitor.py"


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _run(tmp_dir: pathlib.Path, *extra: str, agents: str = "agy",
         env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    for agent in agents.split(","):
        (tmp_dir / f"{agent}-review-pr7.pid").write_text(str(_dead_pid()))
        (tmp_dir / f"{agent}-review-pr7-result.json").write_text('{"event": "APPROVE"}')
    base = {k: v for k, v in os.environ.items() if not k.startswith("MONITOR_")}
    return subprocess.run(
        [sys.executable, str(_MONITOR_LIB), "7", "--agents", agents,
         "--tmp-dir", str(tmp_dir), "--poll", "1", *extra],
        capture_output=True, text=True, env={**base, **(env or {})}, timeout=60,
    )


def _hard_timeout(proc: subprocess.CompletedProcess, agent: str) -> int:
    m = re.search(rf"^\[{agent}\] .*hard timeout (\d+)s", proc.stderr, re.MULTILINE)
    assert m, proc.stderr
    return int(m.group(1))


def _warnings(proc: subprocess.CompletedProcess) -> list[str]:
    return [line for line in proc.stderr.splitlines() if "無進捗の許容" in line and "⚠" in line]


# ---------- AC32 ----------

@pytest.mark.parametrize(("phase", "expected"), [
    ("review", 1200), ("critique", 1200), ("propose", 1200),
    ("judge-test-changes", 1200), ("apply", 3600), ("fix", 3600), ("final-fix", 3600),
])
def test_the_phase_default_is_the_table_value(tmp_path, phase: str, expected: int) -> None:
    proc = _run(tmp_path, "--phase", phase)
    assert proc.returncode == 0, proc.stderr
    assert _hard_timeout(proc, "agy") == expected


def test_omitting_the_phase_uses_the_review_value(tmp_path) -> None:
    proc = _run(tmp_path)
    assert _hard_timeout(proc, "agy") == 1200


def test_the_shared_environment_overrides_the_phase(tmp_path) -> None:
    proc = _run(tmp_path, "--phase", "apply", env={"MONITOR_TIMEOUT": "1800"})
    assert _hard_timeout(proc, "agy") == 1800


def test_the_per_agent_environment_overrides_the_shared_one(tmp_path) -> None:
    proc = _run(tmp_path, "--phase", "review", agents="agy,codex",
                env={"MONITOR_TIMEOUT": "1500", "MONITOR_TIMEOUT_AGY": "1700"})
    assert _hard_timeout(proc, "agy") == 1700
    assert _hard_timeout(proc, "codex") == 1500


def test_the_argument_overrides_the_environment(tmp_path) -> None:
    proc = _run(tmp_path, "--phase", "review", "--timeout", "999",
                env={"MONITOR_TIMEOUT": "1500", "MONITOR_TIMEOUT_AGY": "1700"})
    assert _hard_timeout(proc, "agy") == 999


def test_an_unknown_phase_is_a_usage_error(tmp_path) -> None:
    proc = _run(tmp_path, "--phase", "propose-tests")
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert "propose-tests" in proc.stderr


# ---------- AC33 ----------

def test_the_table_defaults_do_not_warn(tmp_path) -> None:
    proc = _run(tmp_path, "--phase", "review", agents="codex,agy,kiro,claude")
    assert _warnings(proc) == []


def test_the_apply_stall_of_1800_does_not_warn(tmp_path) -> None:
    """cross-refactoring の適用の許容（`IMPL_STALL_TIMEOUT` の既定）は 3600 より小さい。"""
    proc = _run(tmp_path, "--phase", "apply", "--stall-timeout", "1800")
    assert _warnings(proc) == []


def test_a_stall_equal_to_the_monitor_timeout_warns(tmp_path) -> None:
    proc = _run(tmp_path, "--phase", "apply", "--stall-timeout", "3600")
    lines = _warnings(proc)
    assert len(lines) == 1
    assert "agy" in lines[0] and lines[0].count("3600") == 2


def test_an_environment_override_that_breaks_the_order_warns_per_agent(tmp_path) -> None:
    proc = _run(tmp_path, "--phase", "review", agents="codex,claude",
                env={"MONITOR_TIMEOUT": "600"})
    lines = _warnings(proc)
    assert len(lines) == 1, proc.stderr
    assert "claude" in lines[0] and "900" in lines[0] and "600" in lines[0]


def test_the_warning_does_not_change_the_exit_code_or_stdout(tmp_path) -> None:
    (tmp_path / "quiet").mkdir()
    (tmp_path / "loud").mkdir()
    quiet = _run(tmp_path / "quiet", "--phase", "apply")
    loud = _run(tmp_path / "loud", "--phase", "apply", "--stall-timeout", "4000")
    assert _warnings(loud) and not _warnings(quiet)
    assert quiet.returncode == loud.returncode == 0

    def rows(proc: subprocess.CompletedProcess) -> list[dict]:
        drop = ("elapsed", "idle_seconds", "pid", "detail")
        return [{k: v for k, v in json.loads(line).items() if k not in drop}
                for line in proc.stdout.splitlines()]

    assert rows(quiet) == rows(loud)


# ---------- 記録の工程 ----------

def test_the_journal_records_the_phase(tmp_path) -> None:
    _run(tmp_path, "--phase", "critique")
    row = json.loads((tmp_path / "monitor-outcomes.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["phase"] == "critique"
    outcome = json.loads((tmp_path / "agy-review-pr7-monitor.json").read_text(encoding="utf-8"))
    assert outcome["phase"] == "critique"


def test_the_journal_phase_is_null_without_the_argument(tmp_path) -> None:
    _run(tmp_path)
    row = json.loads((tmp_path / "monitor-outcomes.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["phase"] is None


# ---------- AC30: 既定値を持つのは表だけ ----------

def test_the_monitor_defaults_point_at_the_table(monitor_mod) -> None:
    import importlib.util
    spec = importlib.util.spec_from_file_location("ndf_lib_limits_t", _LIB / "limits.py")
    limits = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(limits)
    assert monitor_mod.DEFAULT_TIMEOUT == limits.PHASE_TIMEOUT["review"]
    assert monitor_mod.DEFAULT_STALL_AGENT_BUILTIN == limits.AGENT_STALL


def test_the_monitor_source_has_no_numeric_limit_of_its_own() -> None:
    """表の値を `monitor.py` に書き写すと、表だけを直したときに食い違う。"""
    src = _MONITOR_LIB.read_text(encoding="utf-8")
    assert not re.search(r"^DEFAULT_TIMEOUT\s*=\s*\d", src, re.MULTILINE)
    assert not re.search(r"^\s*\"(?:codex|agy|kiro|claude)\":\s*\d+", src, re.MULTILINE)
