"""statusline の本体（`statusline.sh`）が入力の JSON から組む表示と、費用の書き出しと合算。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

BODY = Path(__file__).resolve().parents[3] / "src" / "ndf" / "domains" / "statusline" / "statusline.sh"
ANSI = re.compile(r"\x1b\[[0-9;]*m")

pytestmark = pytest.mark.skipif(shutil.which("jq") is None or shutil.which("bash") is None, reason="jq と bash が要る")


def _context(tmp_path: Path) -> Path:
    return tmp_path / "context"


def _render(tmp_path: Path, cost: object, session: str = "me") -> str:
    data = {
        "session_id": session,
        "workspace": {"project_dir": "/w"},
        "context_window": {"total_input_tokens": 164000},
        "model": {"display_name": "Opus 5.5"},
        "cost": {"total_cost_usd": cost},
    }
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "CLAUDE_CODE_USE_BEDROCK": "1", "NDF_CONTEXT_DIR": str(_context(tmp_path))}
    got = subprocess.run(["bash", str(BODY)], input=json.dumps(data), capture_output=True, text=True, env=env, check=True)
    return ANSI.sub("", got.stdout)


def _fresh_total(tmp_path: Path) -> None:
    (_context(tmp_path) / "cost-total.txt").unlink(missing_ok=True)


def test_shows_own_cost_and_the_sum_of_sessions_and_print_runs(tmp_path):
    sessions = _context(tmp_path) / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "other.cost.json").write_text('{"base": 1, "last": 2.5}', encoding="utf-8")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rows = [json.dumps({"at": now, "usd": 0.75}), json.dumps({"at": "2020-01-01T00:00:00Z", "usd": 100}), "書きかけ"]
    (_context(tmp_path) / "print-costs.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    assert _render(tmp_path, 3) == "/w [Opus5.5 164k $3.00 $7.25] bedrock"


def test_a_restarted_count_keeps_the_earlier_cost(tmp_path):
    _render(tmp_path, 3)
    _fresh_total(tmp_path)
    assert _render(tmp_path, 0.5) == "/w [Opus5.5 164k $0.50 $3.50] bedrock"


@pytest.mark.parametrize("cost", [None, "x"])
def test_leaves_out_the_costs_when_the_cost_is_not_a_number(tmp_path, cost):
    assert _render(tmp_path, cost) == "/w [Opus5.5 164k] bedrock"
