"""監視の結果ファイルと監視の記録（#662 の AC1〜AC7）。

監視は PATH の偽物ではなく **実プロセス** を相手にする。終わったプロセス（pid ファイルの
指す先がもう無い）と、名前に担当を含む眠るプロセスの 2 つを使う。

- AC1: `<stem>-monitor.json` のキーと時刻の形
- AC2: `monitor-outcomes.jsonl` への追記
- AC3: 状態から理由への対応
- AC4: `launched_at` は pid ファイルの更新時刻。pid ファイルが無ければ `null`
- AC5: 標準出力の 13 個のキーと型、終了コードは変えない
- AC6: 一時ディレクトリへ書けなくても AC5 のとおり
- AC7: `launch-cli.sh` は起動の前に結果ファイルを消し、記録は消さない
"""
from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
from unittest import mock

import pytest

_HERE = pathlib.Path(__file__).resolve().parent
_LIB = _HERE.parents[2] / "scripts" / "lib"
_MONITOR_LIB = _LIB / "monitor.py"
_MONITOR_SHIM = _HERE.parent / "scripts" / "monitor.py"
_LAUNCH_CLI = _LIB / "launch-cli.sh"

OUTCOME_KEYS = {
    "agent", "stem", "status", "exit_code", "reason", "detail",
    "launched_at", "started_at", "ended_at", "elapsed", "idle_seconds",
    "progress_tail", "result_exists", "pid",
}
STDOUT_KEYS = {
    "agent": str, "status": str, "exit_code": int, "pid": (int, type(None)),
    "elapsed": float, "detail": str, "err_log_size": int,
    "stdout_log_size": int, "progress_log_size": int, "progress_tail": str,
    "idle_seconds": float, "result_exists": bool, "sentinel_seen": bool,
}


def _load_outcome_mod():
    spec = importlib.util.spec_from_file_location("monitor_outcome_t", _LIB / "monitor_outcome.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _run_monitor(tmp_dir: pathlib.Path, *extra: str, script: pathlib.Path = _MONITOR_LIB,
                 pr: int = 7, agents: str = "codex") -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MONITOR_")}
    return subprocess.run(
        [sys.executable, str(script), str(pr), "--agents", agents,
         "--tmp-dir", str(tmp_dir), "--poll", "1", *extra],
        capture_output=True, text=True, env=env, timeout=60,
    )


def _finished(tmp_dir: pathlib.Path, stem: str, *, result: bool) -> int:
    pid = _dead_pid()
    (tmp_dir / f"{stem}.pid").write_text(str(pid))
    if result:
        (tmp_dir / f"{stem}-result.json").write_text('{"event": "APPROVE"}')
    return pid


def _is_iso_with_tz(value: str) -> bool:
    parsed = _dt.datetime.fromisoformat(value)
    return parsed.tzinfo is not None


# ---------- AC1 / AC3 / AC4 ----------

def test_outcome_file_has_the_14_keys_and_tz_aware_times(tmp_path):
    stem = "codex-review-pr7"
    pid = _finished(tmp_path, stem, result=True)
    os.utime(tmp_path / f"{stem}.pid", (1_700_000_000, 1_700_000_000))

    proc = _run_monitor(tmp_path)

    assert proc.returncode == 0, proc.stderr
    outcome = json.loads((tmp_path / f"{stem}-monitor.json").read_text(encoding="utf-8"))
    assert set(outcome) == OUTCOME_KEYS
    assert outcome["agent"] == "codex"
    assert outcome["stem"] == stem
    assert outcome["status"] == "OK"
    assert outcome["exit_code"] == 0
    assert outcome["reason"] == "ok"
    assert outcome["pid"] == pid
    assert outcome["result_exists"] is True
    for key in ("launched_at", "started_at", "ended_at"):
        assert _is_iso_with_tz(outcome[key]), key
    # AC4: pid ファイルの更新時刻
    assert _dt.datetime.fromisoformat(outcome["launched_at"]).timestamp() == 1_700_000_000
    assert outcome["started_at"] <= outcome["ended_at"]


def test_outcome_without_result_is_missing(tmp_path):
    stem = "codex-review-pr7"
    _finished(tmp_path, stem, result=False)

    proc = _run_monitor(tmp_path)

    assert proc.returncode == 3
    outcome = json.loads((tmp_path / f"{stem}-monitor.json").read_text(encoding="utf-8"))
    assert (outcome["status"], outcome["reason"]) == ("NO_RESULT", "missing")


def test_outcome_of_a_timed_out_process_is_timeout(tmp_path):
    stem = "codex-review-pr7"
    # 名前に担当を含む眠るプロセス（`cmdline` の照合を通す）。
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "codex"])
    try:
        (tmp_path / f"{stem}.pid").write_text(str(sleeper.pid))
        proc = _run_monitor(tmp_path, "--timeout", "2", "--stall-timeout", "100")
    finally:
        sleeper.kill()
        sleeper.wait()
    assert proc.returncode == 2
    outcome = json.loads((tmp_path / f"{stem}-monitor.json").read_text(encoding="utf-8"))
    assert (outcome["status"], outcome["reason"]) == ("TIMEOUT", "timeout")


@pytest.mark.parametrize(("status", "reason"), [
    ("OK", "ok"), ("TIMEOUT", "timeout"), ("STALLED", "stalled"),
    ("EARLY_ERROR", "early_error"), ("NO_RESULT", "missing"),
    ("PIDFILE_BAD", "pidfile_bad"),
])
def test_reason_follows_status(status, reason):
    assert _load_outcome_mod().reason_for(status) == reason


def test_reason_rejects_unknown_status():
    with pytest.raises(ValueError, match="UNKNOWN"):
        _load_outcome_mod().reason_for("UNKNOWN")


def test_pidfile_bad_outcome_has_null_launched_at(monitor_mod, tmp_path, monkeypatch, capsys):
    """pid ファイルを 30 秒待つ猶予を省くため、監視の本体だけを差し替えて CLI を通す。"""
    def fake_monitor_agent(**kwargs):
        return monitor_mod.AgentStatus(
            agent=kwargs["agent"], status="PIDFILE_BAD", exit_code=6,
            detail="pidfile not found")

    monkeypatch.setattr(sys, "argv", [
        "monitor.py", "7", "--agents", "kiro", "--tmp-dir", str(tmp_path)])
    with mock.patch.object(monitor_mod, "monitor_agent", side_effect=fake_monitor_agent):
        with pytest.raises(SystemExit) as exc:
            monitor_mod.main()
    assert exc.value.code == 6
    outcome = json.loads((tmp_path / "kiro-review-pr7-monitor.json").read_text(encoding="utf-8"))
    assert outcome["reason"] == "pidfile_bad"
    assert outcome["launched_at"] is None
    assert outcome["pid"] is None


# ---------- AC2 ----------

def test_journal_keeps_every_run_of_the_same_stem(tmp_path):
    stem = "codex-review-pr7"
    _finished(tmp_path, stem, result=False)
    _run_monitor(tmp_path)
    _finished(tmp_path, stem, result=True)
    _run_monitor(tmp_path)

    lines = (tmp_path / "monitor-outcomes.jsonl").read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines]
    assert [r["reason"] for r in rows] == ["missing", "ok"]
    assert all(set(r) == OUTCOME_KEYS for r in rows)
    # 結果ファイルは最後の監視の結果である
    last = json.loads((tmp_path / f"{stem}-monitor.json").read_text(encoding="utf-8"))
    assert last["reason"] == "ok"


def test_each_agent_gets_its_own_outcome_and_journal_line(tmp_path):
    _finished(tmp_path, "codex-propose-rf3-r1", result=True)
    _finished(tmp_path, "kiro-propose-rf3-r1", result=False)

    proc = _run_monitor(tmp_path, "--stem-template", "{agent}-propose-rf{id}-r1",
                        pr=3, agents="codex,kiro")

    assert proc.returncode == 3
    rows = [json.loads(line) for line in
            (tmp_path / "monitor-outcomes.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sorted((r["agent"], r["reason"]) for r in rows) == [
        ("codex", "ok"), ("kiro", "missing")]
    assert (tmp_path / "kiro-propose-rf3-r1-monitor.json").is_file()


def test_shim_entry_point_writes_the_same_files(tmp_path):
    """cross-review はシム（`exec` で読み込む）から呼ぶ。読み込みの経路が違っても書く。"""
    stem = "agy-review-pr7"
    _finished(tmp_path, stem, result=True)

    proc = _run_monitor(tmp_path, script=_MONITOR_SHIM, agents="agy")

    assert proc.returncode == 0, proc.stderr
    assert json.loads((tmp_path / f"{stem}-monitor.json").read_text(encoding="utf-8"))["reason"] == "ok"


# ---------- AC5 / AC6 ----------

def _stdout_rows(proc: subprocess.CompletedProcess) -> list[dict]:
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def test_stdout_keeps_the_13_keys_and_types(tmp_path):
    _finished(tmp_path, "codex-review-pr7", result=True)

    proc = _run_monitor(tmp_path)

    rows = _stdout_rows(proc)
    assert len(rows) == 1
    assert set(rows[0]) == set(STDOUT_KEYS)
    for key, typ in STDOUT_KEYS.items():
        assert isinstance(rows[0][key], typ), key


def _comparable(rows: list[dict]) -> list[dict]:
    """実行ごとに揺れる値（経過秒）だけを外す。"""
    return [{k: v for k, v in r.items() if k not in ("elapsed", "idle_seconds")} for r in rows]


def test_unwritable_outcome_paths_keep_exit_code_and_stdout(tmp_path):
    """書き出し先が書けなくても、終了コードと標準出力は書けるときと同じ。

    root でも書けない形にするため、結果ファイルと記録の名前にディレクトリを置く。
    """
    good = tmp_path / "good"
    bad = tmp_path / "bad"
    good.mkdir()
    bad.mkdir()
    stem = "codex-review-pr7"
    pid = _dead_pid()
    for d in (good, bad):
        (d / f"{stem}.pid").write_text(str(pid))
        (d / f"{stem}-result.json").write_text('{"event": "APPROVE"}')
    (bad / f"{stem}-monitor.json").mkdir()
    (bad / "monitor-outcomes.jsonl").mkdir()

    ok = _run_monitor(good)
    ng = _run_monitor(bad)

    assert ng.returncode == ok.returncode == 0
    assert _comparable(_stdout_rows(ng)) == _comparable(_stdout_rows(ok))
    assert "監視の結果を書けません" in ng.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root は読み取り専用のディレクトリにも書ける")
def test_read_only_tmp_dir_keeps_exit_code_and_stdout(tmp_path):
    stem = "codex-review-pr7"
    _finished(tmp_path, stem, result=True)
    tmp_path.chmod(0o555)
    try:
        proc = _run_monitor(tmp_path)
    finally:
        tmp_path.chmod(0o755)
    assert proc.returncode == 0
    rows = _stdout_rows(proc)
    assert rows[0]["status"] == "OK"
    assert not (tmp_path / f"{stem}-monitor.json").exists()


# ---------- AC7 ----------

def test_launch_cli_removes_stale_outcome_but_keeps_journal(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "codex"
    fake.write_text("#!/usr/bin/env bash\ncat >/dev/null\nexit 0\n")
    fake.chmod(0o755)
    work = tmp_path / "work"
    work.mkdir()
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    prompt = tmp_path / "prompt.md"
    prompt.write_text("レビューしてください\n")
    stem = tmp_dir / "codex-review-pr7"
    (tmp_dir / "codex-review-pr7-monitor.json").write_text('{"reason": "timeout"}')
    (tmp_dir / "monitor-outcomes.jsonl").write_text('{"reason": "timeout"}\n')

    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    proc = subprocess.run(
        ["bash", str(_LAUNCH_CLI), "codex", str(work), str(prompt), str(stem)],
        capture_output=True, text=True, env=env, timeout=30,
    )

    assert proc.returncode == 0, proc.stderr
    assert not (tmp_dir / "codex-review-pr7-monitor.json").exists()
    assert (tmp_dir / "monitor-outcomes.jsonl").read_text() == '{"reason": "timeout"}\n'
