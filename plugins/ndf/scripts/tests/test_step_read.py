"""読む側の共通の規則（`lib/step_read.py`、#2227）。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
_spec = importlib.util.spec_from_file_location("ndf_lib_step_read", LIB / "step_read.py")
rd = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rd
_spec.loader.exec_module(rd)


def test_process_if_not_ok_reads_the_last_result_line():
    import subprocess

    ok = {"tool": "t", "status": "ok", "summary": "記録した", "items": [], "metrics": {}}
    p = subprocess.CompletedProcess([], 0, "前の行\n" + json.dumps(ok) + "\n", "")
    assert rd.process_if_not_ok("rec.sh 1 stage 設計", p) == []
    bad = subprocess.CompletedProcess([], 0, "#1 進行 = 設計\n", "")  # 古い形の行は成功と読まない
    got = rd.process_if_not_ok("rec.sh 1 stage 設計", bad)
    assert got[0]["run"] == "rec.sh 1 stage 設計" and "結果 JSON が無い" in got[0]["result"]


def test_read_result_file_reads_ok_and_turns_missing_or_broken_files_into_stopped(tmp_path):
    ok = {"tool": "worker", "status": "ok", "summary": "済んだ", "items": [], "metrics": {}}
    f = tmp_path / "r.json"
    assert rd.read_result_file(f)["gaps"][0]["kind"] == "result-missing"
    f.write_text("結果: 完了\n")
    assert rd.read_result_file(f)["status"] == "stopped"
    f.write_text(json.dumps(ok) + "\n")
    assert rd.is_ok(rd.read_result_file(f))


def test_read_completed_does_not_read_gate_or_mismatched_codes_as_ok():
    import subprocess

    gate = {"tool": "t", "status": "gate", "summary": "待つ", "items": [], "metrics": {}, "next": "聞く"}
    assert not rd.is_ok(rd.read_completed(subprocess.CompletedProcess([], 10, json.dumps(gate), "")))
    ok = {"tool": "t", "status": "ok", "summary": "x", "items": [], "metrics": {}}
    mismatch = rd.read_completed(subprocess.CompletedProcess([], 1, json.dumps(ok), ""))
    assert mismatch["status"] == "stopped" and mismatch["gaps"][0]["kind"] == "result-shape"
    assert rd.read_completed(subprocess.CompletedProcess([], 0, "", "err"))["status"] == "stopped"
