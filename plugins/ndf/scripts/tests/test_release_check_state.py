"""開発版のプランが検査のプランの状態ディレクトリを notes --approval へ渡す（#1488 の 1488-7）。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
from supervise_lib import paths, queue, release_templates  # noqa: E402


def plan_file(tmp_path: Path, name: str, cmd: str) -> str:
    f = tmp_path / f"{name}.json"
    f.write_text(json.dumps({"フェーズ": "試験", "steps": [{"id": "t", "type": "run", "cmd": cmd}]}, ensure_ascii=False))
    return str(f)


def done(plan: str) -> dict:
    d = paths.state_dir_of(plan)
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.md").write_text("## フェーズの報告\n\n- 結果: 完了\n- Pull Request: 77\n")
    return {"plan": plan, "result": "完了", "report": str(d / "report.md")}


def cmd_of(plan: str) -> str:
    return json.loads(Path(plan).read_text())["steps"][0]["cmd"]


def test_queue_state_takes_the_named_plan_state_dir(tmp_path):
    check = plan_file(tmp_path, "6-check", "true")
    item = done(check)
    dev = plan_file(tmp_path, "7-release", "notes --prs {queue_pr:check} --check-state '{queue_state:check}'")
    assert queue.fill_queue_pr(dev, [item]) is None
    assert cmd_of(dev) == f"notes --prs 77 --check-state '{paths.state_dir_of(check)}'"
    alone = plan_file(tmp_path, "7-release-b", "x '{queue_state:check}'")  # 単独で流しても同じディレクトリの計画を読む
    assert queue.fill_queue_pr(alone, []) is None and cmd_of(alone) == f"x '{paths.state_dir_of(check)}'"


def test_queue_placeholders_without_the_plan_are_not_filled(tmp_path):
    """名前の一致する計画の結果が無ければ、0・空文字で埋めずに理由を返し、計画を書き換えない（#2079）。"""
    dev = plan_file(tmp_path, "7-release", "notes --prs {queue_pr:check} --check-state '{queue_state:check}'")
    err = queue.fill_queue_pr(dev, [])
    assert err and "{queue_pr:check}" in err and "{queue_state:check}" in err
    assert cmd_of(dev) == "notes --prs {queue_pr:check} --check-state '{queue_state:check}'"


def test_queue_pr_of_a_stopped_plan_is_not_zero(tmp_path):
    check = plan_file(tmp_path, "6-check", "true")
    item = {**done(check), "result": "止まった"}
    dev = plan_file(tmp_path, "7-release", "x {queue_pr:check}")
    assert queue.fill_queue_pr(dev, [item]) and cmd_of(dev) == "x {queue_pr:check}"


def test_queue_stage_with_unfilled_placeholder_is_not_ok(tmp_path):
    dev = plan_file(tmp_path, "7-release", "x {queue_pr:check}")
    skipped, runnable = queue.fill_stage(False, [dev], [])
    assert runnable == [] and skipped[0]["result"] == queue.NOT_RUN and skipped[0]["unfilled"]
    res = queue._queue_result(skipped, 3, tmp_path / "done.json")
    assert res["status"] != "ok" and "{queue_pr:check}" in skipped[0]["reason"]


def test_dev_approval_passes_the_check_state_only_for_the_check_pr():
    a = argparse.Namespace(version="1.0.1-dev.1", prev_tag=None, advise=None, mvv=None, base="develop", release={"plugin": "ndf"})
    steps = {s["id"]: s for s in release_templates._dev_approval_steps(a, "/r", "issues/a.md", "{queue_pr:check}", "claude")}
    assert "--check-state '{queue_state:check}'" in steps["explain"]["cmd"]
    steps = {s["id"]: s for s in release_templates._dev_approval_steps(a, "/r", "issues/a.md", "995 997", "claude")}
    assert "--check-state" not in steps["explain"]["cmd"]
