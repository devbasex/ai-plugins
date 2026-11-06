"""承認ゲートの名前の改名（#2619）: 新しい版は正の名前で書き、旧い版が書いた記録を同じ意味に読む。"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

import gate_answer  # noqa: E402
import gate_rejections as gr  # noqa: E402
import legacy_names  # noqa: E402
import legacy_values  # noqa: E402
import parallel_external as pe  # noqa: E402
import project_mvv_signals as pms  # noqa: E402
import sprint_loop_gate2 as gate2  # noqa: E402
import sprint_mvv  # noqa: E402

DESIGN, RELEASE = gate_answer.DESIGN_GATE, gate_answer.RELEASE_GATE
OLD_DESIGN, OLD_RELEASE = "関門 1", "関門 2"
BOTH = ((OLD_DESIGN, DESIGN), (OLD_RELEASE, RELEASE))
AT = "2026-10-30T01:00:00+00:00"


def both_spellings(build):
    """旧名で書いた記録と正の名前で書いた記録で `build` を呼び、結果の組を返す。"""
    return build(OLD_DESIGN, OLD_RELEASE), build(DESIGN, RELEASE)


def sprint_state(tmp: Path, args: list[str], gates=()) -> subprocess.CompletedProcess:
    state = tmp / "state.json"
    if not state.exists():
        state.write_text(json.dumps({"name": "m1", "gates": list(gates)}, ensure_ascii=False))
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "sprint-state.py"), "gate", str(state), *args, "--at", AT]
        + ["--mvv-log", str(tmp / "mvv-gate.jsonl"), "--root", str(tmp)],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("old, new", BOTH)
def test_canonical_folds_only_the_old_gate_names(old, new):
    assert gate_answer.canonical(old) == new
    assert gate_answer.canonical(new) == new
    for other in ("MVV", "承認ゲート 2", "ゲート 2", "関門 3", "", None, ["関門 1"]):
        assert gate_answer.canonical(other) == other


@pytest.mark.parametrize("mode", ["forward", "refuse"])
def test_old_gate_name_is_accepted_whatever_the_mode_is(monkeypatch, capsys, mode):
    """Factory のランナーが旧名を渡し続けるため、受け付けをやめる切り替えでも止まらない（決定 5）。"""
    monkeypatch.setattr(legacy_names, "MODE", mode)
    assert gate_answer.accept(OLD_RELEASE) == RELEASE
    assert capsys.readouterr().err.strip() == f"{OLD_RELEASE} は {RELEASE} へ改名した。{RELEASE} で呼ぶ"
    assert gate_answer.accept(RELEASE) == RELEASE
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("old, new", BOTH)
def test_sprint_state_writes_the_canonical_name_and_tells_once(tmp_path, old, new):
    p = sprint_state(tmp_path, [old, "--what", "要約", "--by", "user", "--outcome", "approved"])
    assert p.returncode == 0, p.stdout + p.stderr
    assert [ln for ln in p.stderr.splitlines() if ln] == [f"{old} は {new} へ改名した。{new} で呼ぶ"]
    assert new in json.loads(p.stdout)["summary"]
    assert [g["name"] for g in json.loads((tmp_path / "state.json").read_text())["gates"]] == [new]


def test_other_gate_names_are_written_as_they_are(tmp_path):
    p = sprint_state(tmp_path, ["承認ゲート 3", "--what", "要約"])
    assert p.returncode == 0 and p.stderr == ""
    assert [g["name"] for g in json.loads((tmp_path / "state.json").read_text())["gates"]] == ["承認ゲート 3"]


def test_gate_rejections_record_writes_the_canonical_name(tmp_path):
    p = subprocess.run(
        [sys.executable, str(SCRIPTS / "gate-rejections.py"), "record", "--gate", OLD_DESIGN, "--what", "要約", "--issue", "1"]
        + ["--repo", "o/r", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert OLD_DESIGN in p.stderr
    assert [r["gate"] for r in gr.read_rejections()] == [DESIGN]


@pytest.mark.parametrize("old, new", BOTH)
def test_a_new_answer_replaces_the_same_gate_recorded_under_the_old_name(tmp_path, old, new):
    """I3: 同じ承認ゲート・同じ PR の利用者の承認は 1 件に置き換わる。"""
    recorded = {"name": old, "what": "x", "at": AT, "by": "user", "outcome": "approved", "pr": 7}
    p = sprint_state(tmp_path, [new, "--what", "要約", "--by", "user", "--outcome", "approved", "--pr", "7"], [recorded])
    assert p.returncode == 0, p.stdout + p.stderr
    assert [(g["name"], g["what"]) for g in json.loads((tmp_path / "state.json").read_text())["gates"]] == [(new, "要約")]


def test_withdraw_by_the_old_name_removes_records_of_both_names(tmp_path):
    gates = [{"name": name, "what": "x", "at": AT, "by": "mvv", "verdict": "follow"} for name in (OLD_RELEASE, RELEASE)]
    p = sprint_state(tmp_path, [OLD_RELEASE, "--withdraw"], gates)
    assert p.returncode == 0, p.stdout + p.stderr
    m = json.loads((tmp_path / "state.json").read_text())
    assert m["gates"] == [] and [w["name"] for w in m["withdrawals"]] == [RELEASE]


def test_records_under_the_old_name_read_the_same_as_the_canonical_name(tmp_path):
    """I2: 読む側の判定と件数が、名前を正の名前に書き換えた記録と同じになる。"""

    def read(design: str, release: str) -> dict:
        state = tmp_path / f"state-{design}.json"
        d = {"name": design, "at": AT, "by": "user", "outcome": "approved", "pr": 1}
        r = {"name": release, "at": AT, "by": "mvv", "verdict": "not_follow"}
        m = {"gates": [d, r], "withdrawals": [{"name": design, "at": "2026-10-29T00:00:00+00:00"}]}
        state.write_text(json.dumps(m, ensure_ascii=False))
        answer_dir = tmp_path / f"answer-{design}"
        answer_dir.mkdir()
        (answer_dir / "answer.json").write_text(json.dumps({"gates": [r]}, ensure_ascii=False))
        log = tmp_path / "mvv-gate.jsonl"
        log.touch()
        rows = [{"gate": name, "outcome": "rejected"} for name in (design, design, release, "承認ゲート 3")]
        return {
            "approved": gate_answer.approved(state, design, pr=1) is not None,
            "approved_by_new_name": gate_answer.approved(state, DESIGN, pr=1) is not None,
            "not_approved": gate_answer.approved(state, release),
            "verdict": pms.last_mvv_verdict(m, str(state), release, log),
            "verdict_by_new_name": pms.last_mvv_verdict(m, str(state), RELEASE, log),
            "withdrawn": sprint_mvv.withdrawn(m, DESIGN),
            "not_withdrawn": sprint_mvv.withdrawn(m, RELEASE),
            "answered": gr.state_answered(state),
            "counts": gr.count_by_gate(rows),
            "gate2": gate2.latest_gate2_answer([{"name": release, "by": "user", "at": AT}], [], "2026-10-29T00:00:00+00:00"),
            "mvv_passed": pe.mvv_passed(answer_dir),
        }

    old, new = both_spellings(read)
    assert old == new
    assert (new["approved"], new["not_approved"], new["verdict"], new["withdrawn"]) == (True, None, "not_follow", True)
    assert new["counts"] == {"gate_1": 2, "gate_2": 1, "other": 1, "total": 4}
    assert (new["answered"], new["gate2"], new["mvv_passed"]) == (True, "approved", True)


def test_status_shows_old_records_under_the_canonical_name(tmp_path):
    """旧名の記録も、人へ出す状態の一覧では正の名前で読む。"""
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"name": "s", "plans": [], "gates": [{"name": "関門 1", "at": AT}]}, ensure_ascii=False))
    out = subprocess.run([sys.executable, str(SCRIPTS / "sprint-state.py"), "status", str(state)], capture_output=True, text=True)
    assert DESIGN in out.stdout and "関門 1" not in out.stdout


@pytest.mark.parametrize("name", ["承認ゲート 2", "ゲート 2"])
def test_other_spellings_stay_unread_as_before(tmp_path, name):
    """記録に残る別の書き方は、変更の前も承認ゲートの記録として読まれていない。読み方を変えない。"""
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"gates": [{"name": name, "at": AT, "by": "user", "outcome": "approved"}]}, ensure_ascii=False))
    assert gate_answer.approved(state, RELEASE) is None
    assert not gr.state_answered(state)
    assert gr.count_by_gate([{"gate": name}])["other"] == 1


@pytest.mark.parametrize("old", ["配布（承認ゲート 2）", "リリース（承認ゲート 2）"])
def test_old_phase_of_the_release_gate_reads_as_the_current_one(old):
    assert legacy_values.to_current("phase", old) == "リリース（リリース承認ゲート）"


def python_sources():
    return [p for p in SCRIPTS.rglob("*.py") if "tests" not in p.relative_to(SCRIPTS).parts]


def test_gate_names_live_in_one_place_only():
    """I4: 正の名前の定数は gate_answer.py、旧名は legacy_names.py の外に無い（報告の結果の値 `関門` は別の識別子）。"""
    names = {DESIGN, RELEASE}
    constants, old_names = [], []
    for path in python_sources():
        rel = path.relative_to(SCRIPTS).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if node.value in names and rel != "lib/gate_answer.py":
                constants.append(rel)
            if (OLD_DESIGN in node.value or OLD_RELEASE in node.value) and rel != "lib/legacy_names.py":
                old_names.append(rel)
    assert constants == [] and old_names == []
