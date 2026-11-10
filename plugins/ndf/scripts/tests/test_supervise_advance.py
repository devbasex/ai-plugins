"""手順の連続実行（`supervise.py advance`、#2909 の F5）。

偽の NDF のプラグイン（`scripts/supervise.py` と `skills/` を持つディレクトリ）の `scripts/` に、決めた結果 JSON を返す小さな
スクリプトを置いて並べる。GitHub も LLM も呼ばない。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
PLUGIN = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
from supervise_lib import advance  # noqa: E402
import step_result  # noqa: E402

CHILD = textwrap.dedent(
    """
    import json, sys
    from pathlib import Path
    status, code = sys.argv[1], int(sys.argv[2])
    log = Path(__file__).with_name("ran.log")
    log.write_text(log.read_text() + sys.argv[3] + "\\n" if log.exists() else sys.argv[3] + "\\n")
    out = {"tool": "child", "status": status, "summary": "手順 " + sys.argv[3], "items": [], "metrics": {}}
    if status == "gate":
        out["next"] = "人が決める"
    print(json.dumps(out, ensure_ascii=False))
    sys.exit(code)
    """
)


@pytest.fixture()
def plug(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "skills").mkdir()
    (tmp_path / "scripts" / "supervise.py").write_text("")
    (tmp_path / "scripts" / "child.py").write_text(CHILD)
    (tmp_path / "outside.py").write_text(CHILD)
    return tmp_path


def step(status: str, code: int, tag: str) -> str:
    return f"python3 scripts/child.py {status} {code} {tag}"


def ran(plug: Path) -> list[str]:
    log = plug / "scripts" / "ran.log"
    return log.read_text().split() if log.exists() else []


def test_stops_at_gate_and_points_to_the_next_step(plug):
    steps = [step("ok", 0, "a"), step("ok", 0, "b"), step("gate", 20, "c"), step("ok", 0, "d")]
    out, code = advance.cmd_advance(steps, cwd=str(plug))
    assert step_result.validate_result(out, code) == []
    assert (out["status"], code) == ("gate", 20)
    assert ran(plug) == ["a", "b", "c"]
    assert len(out["process"]) == 3 and out["process"][2]["status"] == "gate"
    assert "人が決める" in out["next"] and out["next"].endswith("--from 4")
    assert out["metrics"] == {"steps": 4, "ran": 3, "stopped_at": 3}


def test_stopped_child_reports_gaps_and_retries_the_same_step(plug):
    steps = [step("ok", 0, "a"), step("stopped", 1, "b"), step("ok", 0, "c")]
    out, code = advance.cmd_advance(steps, cwd=str(plug))
    assert step_result.validate_result(out, code) == []
    assert (out["status"], code) == ("stopped", 1)
    assert ran(plug) == ["a", "b"]
    assert out["gaps"][0]["reason"] == "手順 b" and out["next"].endswith("--from 2")
    assert out["process"][1]["code"] == 1


def test_all_ok_runs_every_step(plug):
    out, code = advance.cmd_advance([step("ok", 0, "a"), step("ok", 0, "b")], cwd=str(plug))
    assert (out["status"], code) == ("ok", 0) and ran(plug) == ["a", "b"]


def test_from_skips_earlier_steps(plug):
    out, code = advance.cmd_advance([step("ok", 0, "a"), step("ok", 0, "b")], start=2, cwd=str(plug))
    assert code == 0 and ran(plug) == ["b"]
    assert out["process"][0]["result"] == advance.SKIPPED


@pytest.mark.parametrize(
    "bad",
    [
        "git status",
        "python3 outside.py ok 0 x",
        f"python3 {PLUGIN}/scripts/lib/gh_parts.py body-lock --issue 1 -- git status",
        f"python3 {PLUGIN}/scripts/lib/post_queue.py retry -- git push --force",
        f"python3 {PLUGIN}/scripts/supervise.py advance --step x",
        "python3 'unterminated",
    ],
)
def test_refuses_outside_commands_before_running_anything(plug, bad):
    out, code = advance.cmd_advance([step("ok", 0, "a"), bad], cwd=str(plug))
    assert step_result.validate_result(out, code) == []
    assert (out["status"], code) == ("stopped", 2)
    assert ran(plug) == []
    assert out["gaps"][0]["name"].startswith("2: ")


def test_every_remainder_subcommand_is_a_relay():
    """`argparse.REMAINDER` を持つ副命令（渡されたコマンドをそのまま走らせる）は、すべて中継の副命令の一覧に載る。"""
    found = set()
    for f in [*PLUGIN.glob("scripts/**/*.py"), *PLUGIN.glob("skills/*/scripts/**/*.py")]:
        if "tests" in f.parts:
            continue
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"nargs=argparse\.REMAINDER", text):
            parsers = re.findall(r'add_parser\(\s*"([^"]+)"', text[: m.start()])
            rel = advance._script_rel(f.resolve())
            found.add((rel, parsers[-1] if parsers else None))
    assert found and found <= advance.RELAYS


def test_cli_emits_the_result():
    p = subprocess.run(
        [sys.executable, str(SCRIPTS / "supervise.py"), "advance", "--step", "git status"], capture_output=True, text=True, timeout=60
    )
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert p.returncode == 2 and out["tool"] == "advance" and out["status"] == "stopped"
