"""hook-trial の timing（ht_checks.cmd_timing）の現状固定テスト。

hook の起動（once）と一時リポジトリ（make_repo）を偽物へ差し替え、集計と判定の形だけを固定する。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS / "experimental" / "hook-trial"))
import ht_checks  # noqa: E402

GUARD, DIRECT, SH = "worktree-guard.sh", "python 直", "sh + python"
KEYS = [f"{mode} / {p}" for mode in ("steady", "fresh") for p in ("bash_write", "bash_read", "edit")]


def variant(argv) -> str:
    if argv[0] == "bash":
        return GUARD
    if argv[0] == "sh":
        return SH
    return "base" if argv[1] == "-c" else DIRECT


def run_timing(tmp_path, monkeypatch, capsys, ms: dict, notice: dict | None = None, runs: int = 2):
    calls = []

    def fake_once(argv, data, env):
        name = variant(argv)
        calls.append((name, data))
        session = json.loads(data)["session_id"] if data else ""
        out = b"notice\n" if (notice or {}).get(name) and session.startswith("t2-fresh") else b""
        return ms.get(name, 1.0), subprocess.CompletedProcess(argv, 0, out, b"")

    monkeypatch.setattr(ht_checks, "once", fake_once)
    monkeypatch.setattr(ht_checks, "make_repo", lambda work: work / "timing" / "repo")
    with pytest.raises(SystemExit) as e:
        ht_checks.cmd_timing(SimpleNamespace(work=str(tmp_path), runs=runs))
    out = json.loads(capsys.readouterr().out)
    rows = json.loads((tmp_path / "timing.json").read_text(encoding="utf-8"))
    return e.value.code, out, rows, calls


def test_timing_slower_variant_stops(tmp_path, monkeypatch, capsys):
    code, out, rows, calls = run_timing(tmp_path, monkeypatch, capsys, {GUARD: 10.0, DIRECT: 5.0, SH: 20.04})
    assert code == 1
    assert out["tool"] == "hook-trial" and out["status"] == "stopped"
    worse = [f"{k} / {SH}" for k in KEYS]
    assert out["summary"] == "hook 1 回の所要の中央値（2 回ずつ）。悪くなった: " + " / ".join(worse)
    assert [i["name"] for i in out["items"]] == [f"{k} / {n}" for k in KEYS for n in (DIRECT, SH)]
    assert out["items"][0] == {"kind": "timing", "name": f"steady / bash_write / {DIRECT}", "result": "faster", "now_ms": 10.0, "new_ms": 5.0}
    assert out["items"][1] == {"kind": "timing", "name": f"steady / bash_write / {SH}", "result": "slower", "now_ms": 10.0, "new_ms": 20.0}
    assert out["metrics"] == rows
    assert list(rows) == [f"{k} / {n}" for k in KEYS for n in (GUARD, DIRECT, SH)] + ["python -c pass", "python + import tree_sitter_bash"]
    assert rows[f"fresh / edit / {SH}"] == {"median_ms": 20.0, "p90_ms": 20.0, "runs": 2, "exit": 0, "notice": False}
    assert rows["python -c pass"] == {"median_ms": 1.0, "runs": 2}
    # 1 つの組につき 温め 3 回 × 3 形 + 計測 runs 回 × 3 形（交互）、最後に素の python を runs 回ずつ
    assert len(calls) == 6 * (9 + 2 * 3) + 2 * 2
    assert [n for n, _ in calls[:15]] == [GUARD] * 3 + [DIRECT] * 3 + [SH] * 3 + [GUARD, DIRECT, SH] * 2
    steady = [json.loads(d)["session_id"] for _, d in calls[9:15]]
    fresh = [json.loads(d)["session_id"] for _, d in calls[3 * 15 + 9 : 4 * 15]]
    assert steady == ["t2-timing"] * 6
    assert fresh == ["t2-fresh-0"] * 3 + ["t2-fresh-1"] * 3


def test_timing_equal_is_ok(tmp_path, monkeypatch, capsys):
    code, out, rows, _ = run_timing(tmp_path, monkeypatch, capsys, {GUARD: 10.0, DIRECT: 10.0, SH: 10.0}, runs=1)
    assert code == 0
    assert out["status"] == "ok"
    assert out["summary"] == "hook 1 回の所要の中央値（1 回ずつ）"
    assert {i["result"] for i in out["items"]} == {"faster"}
    assert (tmp_path / "timing" / "tmp").is_dir()


def test_timing_notice_mismatch_is_compared_only_in_fresh(tmp_path, monkeypatch, capsys):
    code, out, rows, _ = run_timing(tmp_path, monkeypatch, capsys, {GUARD: 10.0, DIRECT: 5.0, SH: 5.0}, notice={GUARD: True, SH: True})
    assert code == 1
    fresh = [k for k in KEYS if k.startswith("fresh")]
    assert out["summary"] == "hook 1 回の所要の中央値（2 回ずつ）。悪くなった: " + " / ".join(f"{k} / {DIRECT} の案内の有無が今と違う" for k in fresh)
    assert rows[f"fresh / edit / {GUARD}"]["notice"] is True
    assert rows[f"fresh / edit / {DIRECT}"]["notice"] is False
    assert rows[f"steady / edit / {GUARD}"]["notice"] is False
