"""new close --pace auto / --carry / --record-pr（#2089 の決定 10・受け入れ条件 16・18）。"""

from __future__ import annotations

from test_supervise_pace import cli, load, sprint_state, steps_of


def close(tmp_path, *extra):
    out = tmp_path / "close"
    p = cli(
        "new", "close", "--name", "l1-1", "--worktree", str(tmp_path), "--issue", "11", "12",
        "--version", "10.18.0-dev.2", "--prod", "10.18.0", "--state", str(sprint_state(tmp_path, "fast")), "--out", str(out), *extra,
    )  # fmt: skip
    assert p.returncode == 0, p.stdout + p.stderr
    return load(out / "sprint.json")


def test_auto_builds_only_the_wrapup_stage_with_the_record_marker(tmp_path):
    manifest = close(tmp_path, "--pace", "auto")
    assert [w["name"] for w in manifest["ステージ"]] == ["まとめ"]
    assert manifest["進め方"] == "auto"
    plan = load(manifest["ステージ"][0]["plans"][0])
    assert plan["進め方"] == "auto"
    assert "--record-pr {queue_pr:release-prod}" in steps_of(plan)["close"]["cmd"]


def test_carry_reaches_the_refinement_prompt_and_record_pr_overrides(tmp_path):
    manifest = close(tmp_path, "--pace", "auto", "--carry", "31", "32", "--record-pr", "77")
    plan = load(manifest["ステージ"][0]["plans"][0])
    steps = steps_of(plan)
    assert "#31 #32" in steps["refine"]["prompt"] and "--add" in steps["refine"]["prompt"]
    assert "--record-pr 77 " in steps["close"]["cmd"]


def test_without_pace_the_close_is_unchanged(tmp_path):
    manifest = close(tmp_path)
    assert [w["name"] for w in manifest["ステージ"]] == ["最終の検査", "開発版", "本番", "まとめ"]
    plan = load(manifest["ステージ"][-1]["plans"][0])
    assert plan["進め方"] == "fast" and "前の周回の持ち越し" not in steps_of(plan)["refine"]["prompt"]
