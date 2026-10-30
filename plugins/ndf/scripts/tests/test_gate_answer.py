"""承認ゲートの利用者の承認の記録（承認の証跡。#1426 の決定 1）の書き込みと読み取り。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

import gate_answer  # noqa: E402


def gate(state: Path, tmp: Path, name: str, *args: str, at: str = "2026-10-30T01:00:00+00:00") -> dict:
    p = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "sprint-state.py"),
            "gate",
            str(state),
            name,
            "--what",
            "要約",
            "--at",
            at,
            *args,
            "--mvv-log",
            str(tmp / "mvv-gate.jsonl"),
            "--root",
            str(tmp),
        ],
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(state.read_text())


def test_gate_1_keeps_records_per_pr(tmp_path):
    state = tmp_path / "sprint.json"
    state.write_text(json.dumps({"name": "m1", "gates": []}))
    approve = ("--by", "user", "--outcome", "approved")
    gate(state, tmp_path, "関門 1", *approve, "--pr", "1")
    m = gate(state, tmp_path, "関門 1", *approve, "--pr", "2")
    assert [(g["by"], g["pr"]) for g in m["gates"]] == [("user", 1), ("user", 2)]
    # --pr を渡さない書き込みも --by mvv の書き込みも、pr を持つ記録を消さない
    gate(state, tmp_path, "関門 1", *approve)
    m = gate(state, tmp_path, "関門 1", "--by", "mvv", "--verdict", "follow")
    assert sorted(str(g.get("pr")) for g in m["gates"]) == ["1", "2", "None"]
    assert gate_answer.approved(state, "関門 1", pr=1).pr == 1
    assert gate_answer.approved(state, "関門 1", pr=2).pr == 2
    assert gate_answer.approved(state, "関門 1", pr=3) is None
    # 同じ PR を書き直すと 1 件のまま置き換わる
    m = gate(state, tmp_path, "関門 1", *approve, "--pr", "1", at="2026-10-30T02:00:00+00:00")
    assert [g["at"] for g in m["gates"] if g.get("pr") == 1] == ["2026-10-30T02:00:00+00:00"]


def test_reads_default_and_older_records_as_user_approval(tmp_path):
    state = tmp_path / "sprint.json"
    gates = [
        {"name": "関門 2", "what": "本番", "at": "2026-10-30T01:00:00+00:00"},
        {"name": "関門 3", "what": "x", "at": "2026-10-30T01:00:00+00:00", "outcome": "rejected"},
        {"name": "関門 4", "what": "x", "at": "2026-10-30T01:00:00+00:00", "by": "mvv", "verdict": "follow"},
    ]
    state.write_text(json.dumps({"gates": gates}))
    got = gate_answer.approved(state, "関門 2")
    assert (got.by, got.outcome, got.pr) == ("user", "approved", None)
    assert gate_answer.approved(state, "関門 3") is None
    assert gate_answer.approved(state, "関門 4") is None
    # after より前か同じ時刻の記録は数えない
    assert gate_answer.approved(state, "関門 2", after="2026-10-30T01:00:00+00:00") is None
    assert gate_answer.approved(state, "関門 2", after="2026-10-30T00:59:00Z") is not None
    assert gate_answer.approved(tmp_path / "none.json", "関門 2") is None
    assert gate_answer.approved(None, "関門 2") is None
