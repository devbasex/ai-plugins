"""cross-refactoring の drive.py が共通層の pause の表（`scripts/lib/drive_pause.py`）で止まること。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1]
LIB = SKILL.parents[1] / "scripts" / "lib"
sys.path.insert(0, str(LIB))
import drive_pause  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rf = _load("rf_drive_contract", SKILL / "scripts" / "drive.py")


def test_drive_reads_the_shared_table():
    assert rf.dp is drive_pause
    assert rf.Stop is drive_pause.Stop
    assert not hasattr(rf, "PAUSES")


def test_final_gate_pause_exits_with_the_shared_code(tmp_path, monkeypatch, capsys):
    (tmp_path / "cross-refactoring-rf7-state.json").write_text(json.dumps({"items": []}))

    def fake(cmd, env=None, cwd=None):
        if Path(cmd[1]).name == "refactor.py":
            sub = cmd[2]
            if sub == "init":
                return 0, f"ID=7\nTMP_DIR={tmp_path}\nPHASE=final\nWORK={tmp_path}\n"
            if sub == "final-gate":
                return 0, "FINAL_GATE=cross-review\n"
        return 0, ""

    monkeypatch.setattr(rf, "call", fake)
    with pytest.raises(SystemExit) as e:
        rf.main(["5", "--scope", "src", "--baseline-test", "pytest"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert e.value.code == drive_pause.PAUSE_CODES["cross-review"]
    assert out["items"][0]["pause"] == "cross-review" and out["next"] == "cross-review"


def test_abort_keeps_the_original_code_in_metrics(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rf, "call", lambda cmd, env=None, cwd=None: (4, ""))
    with pytest.raises(SystemExit) as e:
        rf.main(["5"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert e.value.code == drive_pause.EXIT_STOPPED and out["metrics"]["exit"] == 4
