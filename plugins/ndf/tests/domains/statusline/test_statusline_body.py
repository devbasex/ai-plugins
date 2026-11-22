"""statusline の本体（`statusline.sh`）が入力の JSON から組む表示。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

BODY = Path(__file__).resolve().parents[3] / "src" / "ndf" / "domains" / "statusline" / "statusline.sh"
ANSI = re.compile(r"\x1b\[[0-9;]*m")

pytestmark = pytest.mark.skipif(shutil.which("jq") is None or shutil.which("bash") is None, reason="jq と bash が要る")


def _render(tmp_path: Path, data: object) -> str:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "CLAUDE_CODE_USE_BEDROCK": "1", "NDF_STATE_HOME": str(tmp_path / "state")}
    got = subprocess.run(["bash", str(BODY)], input=json.dumps(data), capture_output=True, text=True, env=env, check=True)
    return ANSI.sub("", got.stdout)


@pytest.mark.parametrize(
    ("cost", "shown"),
    [({"total_cost_usd": 12.3456}, "$12.35"), ({"total_cost_usd": 0}, "$0.00")],
)
def test_shows_the_session_cost_before_the_account(tmp_path, cost, shown):
    line = _render(
        tmp_path,
        {
            "workspace": {"project_dir": "/w"},
            "context_window": {"total_input_tokens": 243000},
            "model": {"display_name": "Opus 5.5"},
            "cost": cost,
        },
    )
    assert line == f"/w [Opus5.5 243k] {shown} bedrock"


@pytest.mark.parametrize("data", [{}, {"cost": {"total_cost_usd": None}}, {"cost": {"total_cost_usd": "x"}}])
def test_leaves_out_the_cost_when_it_is_not_a_number(tmp_path, data):
    assert _render(tmp_path, data) == "bedrock"
