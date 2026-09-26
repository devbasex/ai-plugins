"""収束ループの drive の部品（lib/loop_drive.py・#1142 の L0）。2 つの drive.py の同じ本体と同じ振る舞いを持つ。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import loop_drive  # noqa: E402

DRIVES = [SCRIPTS.parent / "skills" / s / "scripts" / "drive.py" for s in ("cross-review", "cross-refactoring")]


def load(path: Path):
    spec = importlib.util.spec_from_file_location(f"drive_{path.parents[1].name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_vars_reads_quoted_values_and_skips_noise():
    text = "A=1 B='x y'\nnot-an-id=3\n'unterminated\nC=\n"
    assert loop_drive.parse_vars(text) == {"A": "1", "B": "x y", "C": ""}


@pytest.mark.parametrize("state, want", [
    ({"final": "approved", "sweep": {"verified": True, "remaining_open": 0, "commit": None}}, "approved"),
    ({"final": "approved", "sweep": {"verified": True, "remaining_open": 1, "commit": None}}, "unverified"),
    ({"final": "approved", "sweep": {"verified": True, "remaining_open": 0, "commit": "abc"}}, "unverified"),
    ({"final": "approved"}, "unverified"),
    ({"final": "max_rounds"}, "max_rounds"),
    ({}, "unknown"),
])
def test_review_status(state, want):
    assert loop_drive.review_status(state) == want


@pytest.mark.parametrize("path", DRIVES, ids=lambda p: p.parents[1].name)
def test_same_behavior_as_both_drives(path):
    drive = load(path)
    text = "A=1 B='x y'\nnot-an-id=3\n"
    assert drive.parse_vars(text) == loop_drive.parse_vars(text)
    for state in ({"final": "approved"}, {"final": "approved", "sweep": {"verified": True}}, {}):
        assert drive.review_status(state) == loop_drive.review_status(state)


def test_call_returns_code_and_stdout_and_passes_stderr(capsys):
    code, out = loop_drive.call([sys.executable, "-c", "import sys; print('o'); sys.stderr.write('e'); sys.exit(5)"])
    assert (code, out) == (5, "o\n")
    assert capsys.readouterr().err == "e"
