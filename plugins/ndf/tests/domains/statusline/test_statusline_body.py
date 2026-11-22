"""statusline の本体（`statusline.sh`）が入力の JSON から組む表示と、費用の増分と直近 1 時間の合計。"""

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
    (_context(tmp_path) / "cost-hour.txt").unlink(missing_ok=True)


def _journal(tmp_path: Path) -> Path:
    return _context(tmp_path) / "print-costs.jsonl"


def test_shows_own_increase_and_the_last_hour_of_the_journal(tmp_path):
    _context(tmp_path).mkdir(parents=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7200))
    rows = [json.dumps({"at": now, "usd": 0.75, "relay": "p12-lctx"}), json.dumps({"at": old, "usd": 100}), "書きかけ"]
    _journal(tmp_path).write_text("\n".join(rows) + "\n", encoding="utf-8")
    _render(tmp_path, 40)
    _fresh_total(tmp_path)
    assert _render(tmp_path, 43) == "/w [Opus5.5 164k $3.00 $3.75/h] bedrock"


def test_a_resumed_conversation_does_not_count_its_earlier_cost(tmp_path):
    assert _render(tmp_path, 135.76) == "/w [Opus5.5 164k $0.00 $0.00/h] bedrock"
    assert not _journal(tmp_path).exists() or _journal(tmp_path).read_text(encoding="utf-8") == ""


def test_a_restarted_count_keeps_the_earlier_increase(tmp_path):
    _render(tmp_path, 1)
    _render(tmp_path, 4)
    _fresh_total(tmp_path)
    assert _render(tmp_path, 0.5) == "/w [Opus5.5 164k $3.50 $3.50/h] bedrock"


@pytest.mark.parametrize("cost", [None, "x"])
def test_leaves_out_the_costs_when_the_cost_is_not_a_number(tmp_path, cost):
    assert _render(tmp_path, cost) == "/w [Opus5.5 164k] bedrock"


def _render_limits(tmp_path: Path, limits: object) -> str:
    data = {
        "workspace": {"project_dir": "/w"},
        "context_window": {"total_input_tokens": 164000},
        "model": {"display_name": "Opus 5.5"},
        "rate_limits": limits,
    }
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "CLAUDE_CODE_USE_BEDROCK": "1", "NDF_CONTEXT_DIR": str(_context(tmp_path))}
    got = subprocess.run(["bash", str(BODY)], input=json.dumps(data), capture_output=True, text=True, env=env, check=True)
    return ANSI.sub("", got.stdout)


@pytest.mark.parametrize(
    ("limits", "shown"),
    [
        ({"five_hour": {"used_percentage": 23.5}, "seven_day": {"used_percentage": 81.2}}, " 7d:81% 5h:23%"),
        ({"seven_day": {"used_percentage": 41}}, " 7d:41%"),  # 5 時間の上限が無いプラン
        (None, ""),
    ],
)
def test_shows_the_usage_of_the_rate_limit_windows_that_are_present(tmp_path, limits, shown):
    assert _render_limits(tmp_path, limits) == f"/w [Opus5.5 164k{shown}] bedrock"
