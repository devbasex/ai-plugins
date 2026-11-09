"""差分の棚卸しの所要を深さの記録へ写すことと、比べ方のコマンド（#2315 の受け入れ条件 17b・17d、不変条件 I1・I2・I6）。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS.parents[2] / "scripts" / "lib"))
from step_result import validate_result  # noqa: E402

PY = sys.executable
BEFORE, AFTER = "2000-01-01T00:00:00+00:00", "2999-01-0{}T00:00:00+00:00"


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args], check=True, capture_output=True)


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "commit", "-q", "--allow-empty", "-m", "base")
    _git(r, "tag", "v1")
    return r


def _upkeep(root, *args):
    p = subprocess.run([PY, str(SCRIPTS / "upkeep.py"), *args, "--root", str(root)], capture_output=True, text=True)
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert validate_result(out, p.returncode) == [], (out, p.returncode, p.stderr)
    return p.returncode, out


def _depth_rows(root):
    return [json.loads(x) for x in (root / ".ndf" / "backlog" / "depths.jsonl").read_text().splitlines()]


def _step(step="refine", seconds=1.0, cost=1.0, models=("claude-opus-5-5",)):
    return {"kind": "step", "step": step, "exit": 0, "seconds": seconds, "cost": cost, "models": {m: {"cost": cost} for m in models}}


# ---------------- 記録のステップ（17b） ----------------


def test_record_copies_the_last_refine_row_of_the_plan_progress(root, tmp_path):
    prog = tmp_path / "progress.jsonl"
    rows = [_step(seconds=300.0, cost=2.0), _step("refine-record", seconds=1.0, cost=0.0), _step(seconds=150.5, cost=1.25)]
    prog.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    code, out = _upkeep(root, "depth", "record", "--depth", "diff", "--progress", str(prog), "--loop", "l1", "--lap", "2")
    row = _depth_rows(root)[-1]
    assert code == 0 and out["items"] == [row]
    assert (row["seconds"], row["cost"], row["values"]["models"]) == (150.5, 1.25, ["claude-opus-5-5"])
    assert (row["loop"], row["lap"]) == ("l1", 2)
    _upkeep(root, "depth", "record", "--depth", "diff", "--progress", str(prog), "--seconds", "9", "--cost", "0.5")
    assert (_depth_rows(root)[-1]["seconds"], _depth_rows(root)[-1]["cost"]) == (9.0, 0.5)  # 渡した値を優先する


@pytest.mark.parametrize("content", [None, "{壊れた\n", json.dumps(_step("retro")) + "\n"])
def test_record_writes_an_empty_row_when_the_progress_has_no_refine_row(root, tmp_path, content):
    prog = tmp_path / "progress.jsonl"
    if content is not None:
        prog.write_text(content)
    code, _ = _upkeep(root, "depth", "record", "--depth", "diff", "--progress", str(prog))
    rows = _depth_rows(root)
    assert code == 0 and len(rows) == 1
    assert rows[0]["seconds"] is None and rows[0]["cost"] is None and "models" not in rows[0]["values"]


# ---------------- 比べ方のコマンド（17d） ----------------


def _ran(at, seconds=100.0, cost=1.0, models=("claude-opus-5-5",), **kw):
    return {"at": at, "depth": "diff", "event": "ran", "seconds": seconds, "cost": cost, "values": {"models": list(models)}, **kw}


def _write(root, rows):
    d = root / ".ndf" / "backlog"
    d.mkdir(parents=True, exist_ok=True)
    (d / "depths.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _compare(root, *extra):
    return _upkeep(root, "depth", "compare", "--since-ref", "v1", "--seconds-under", "206", "--cost-under", "1.41",
                   "--model", "claude-opus-5-5", *extra)  # fmt: skip


def test_compare_uses_the_oldest_rows_after_the_ref_once_per_lap(root):
    rows = [
        _ran(BEFORE, seconds=1, cost=0.1, loop="a", lap=1),  # 起点より前
        _ran(AFTER.format(1), seconds=None, cost=None, loop="a", lap=2),  # 秒の空
        _ran(AFTER.format(2), models=("other",), loop="a", lap=3),  # 言語モデルが違う
        _ran(AFTER.format(3), seconds=300, cost=2.0, loop="a", lap=4),  # 同じループの 1 回の打ち直しで、次の行が採られる
        _ran(AFTER.format(4), seconds=100, cost=1.0, loop="a", lap=4),
        _ran(AFTER.format(5), seconds=200, cost=1.2, loop="b", lap=1),  # 最後の行が空でも、前のそろう行を 1 回と数える
        _ran(AFTER.format(6), seconds=None, cost=None, loop="b", lap=1),
        _ran(AFTER.format(7), seconds=150, cost=1.3, sprint="s1"),
        _ran(AFTER.format(8), seconds=1, cost=0.01, sprint="s2"),  # 4 件目は使わない
        {**_ran(AFTER.format(9)), "depth": "review"},
    ]
    _write(root, rows)
    code, out = _compare(root)
    m = out["metrics"]
    used = [i for i in out["items"] if i["result"] == "used"]
    assert [(i["seconds"], i["cost"]) for i in used] == [(100, 1.0), (200, 1.2), (150, 1.3)]
    assert (m["count"], m["needed"], m["judged"], m["median_seconds"], m["median_cost"]) == (3, 3, True, 150, 1.2)
    assert m["delta"] == {"seconds": -56.0, "cost": -0.21} and code == 0
    reasons = sorted(i["reason"] for i in out["items"] if i["result"] == "excluded")
    assert reasons == ["duplicate", "model", "no_usage", "no_usage"]


def test_compare_holds_until_enough_rows_and_needs_both_under(root):
    _write(root, [_ran(AFTER.format(1), loop="a", lap=1), _ran(AFTER.format(2), loop="a", lap=2)])
    code, out = _compare(root)
    assert code == 3 and out["metrics"]["count"] == 2 and out["metrics"]["judged"] is False
    _write(root, [_ran(AFTER.format(k), seconds=100, cost=c, loop="a", lap=k) for k, c in ((1, 2.0), (2, 2.0), (3, 1.0))])
    code, out = _compare(root)
    assert code == 1 and out["metrics"]["judged"] is True  # 秒は下回るがドルは下回らない
    code, _ = _upkeep(root, "depth", "compare", "--since-ref", "nope", "--seconds-under", "1", "--cost-under", "1")
    assert code == 2
