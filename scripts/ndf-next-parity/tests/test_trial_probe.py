from __future__ import annotations

import json

import pytest

from parity.trial import probe


def _features():
    return {
        "locations": {"M8": ["src/m8/"], "S1a": ["skills/"], "S1b": ["hooks/"]},
        "rows": [
            {"id": "entrypoint:scripts/merged-steps.py", "kind": "entrypoint", "now": "scripts/merged-steps.py", "to": "M8"},
            {"id": "entrypoint:scripts/old-name.py", "kind": "entrypoint", "now": "scripts/old-name.py", "not_built": "旧名"},
            {"id": "skill:merged", "kind": "skill", "now": "skills/merged", "to": "S1a"},
            {"id": "skill:tidy", "kind": "skill", "now": "skills/tidy", "to": "S1a"},
            {"id": "skill:gone", "kind": "skill", "now": "skills/gone", "to": "S1a"},
        ],
    }


@pytest.fixture
def plugin(tmp_path):
    (tmp_path / "src/m8").mkdir(parents=True)
    (tmp_path / "hooks").mkdir()
    (tmp_path / "skills/merged").mkdir(parents=True)
    (tmp_path / "skills/merged/SKILL.md").write_text('python3 "$SCRIPTS/merged-steps.py" plan\n', encoding="utf-8")
    (tmp_path / "skills/tidy").mkdir()
    (tmp_path / "skills/tidy/SKILL.md").write_text("bin/ndf worktree tidy\n", encoding="utf-8")
    return tmp_path


def test_a_skill_that_still_calls_an_old_script_fails_and_names_the_child(plugin):
    got = probe.judge(_features(), root=plugin, repo=plugin)
    assert got["skill:merged"]["result"] == "failed"
    assert "merged-steps.py（M8）" in got["skill:merged"]["reason"]
    assert got["entrypoint:scripts/merged-steps.py"]["result"] == "failed"


def test_a_skill_without_old_calls_is_not_run_until_the_trial_reaches_it(plugin):
    assert probe.judge(_features(), root=plugin, repo=plugin)["skill:tidy"]["result"] == "not_run"


def test_rows_passed_in_the_trial_and_not_built_rows_without_callers_pass(plugin):
    got = probe.judge(_features(), passed=["skill:tidy"], root=plugin, repo=plugin)
    assert got["skill:tidy"]["result"] == "passed"
    assert got["entrypoint:scripts/old-name.py"]["result"] == "passed"


def test_a_static_failure_wins_over_a_passed_record(plugin):
    got = probe.judge(_features(), passed=["skill:merged", "skill:gone"], root=plugin, repo=plugin)
    assert got["skill:merged"]["result"] == "failed"
    assert got["skill:gone"]["result"] == "failed"
    assert got["skill:merged"]["record"] == ""


def test_a_missing_skill_or_destination_fails(plugin):
    (plugin / "src/m8").rmdir()
    got = probe.judge(_features(), root=plugin, repo=plugin)
    assert got["skill:gone"]["result"] == "failed"
    assert "src/m8/" in got["entrypoint:scripts/merged-steps.py"]["reason"]


def test_a_removed_workflow_is_not_a_failure(plugin):
    features = _features()
    features["locations"]["M2"] = ["src/m8/"]
    features["rows"].append({"id": "workflow:gone.yml", "kind": "workflow", "now": ".github/workflows/gone.yml", "to": "M2"})
    assert probe.judge(features, root=plugin, repo=plugin)["workflow:gone.yml"]["result"] == "not_run"


def test_a_name_inside_a_longer_name_is_not_a_call():
    assert probe.callers("state.py", {"a.md": "sprint-state.py run"}) == []
    assert probe.callers("state.py", {"a.md": "$CR/state.py start"}) == ["a.md"]


def test_write_changes_only_the_trial_of_each_row(tmp_path):
    path = tmp_path / "features.json"
    rows = [{"id": "a:1", "trial": None}, {"id": "a:2", "trial": None}]
    lines = ["{", '  "rows": ['] + [f"    {json.dumps(r, ensure_ascii=False)}," for r in rows]
    lines[-1] = lines[-1].removesuffix(",")
    path.write_text("\n".join([*lines, "  ]", "}"]) + "\n", encoding="utf-8")
    probe.write_trials(path, {"a:1": {"result": "passed"}, "a:2": {"result": "not_run", "reason": "r"}})
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert [r["trial"]["result"] for r in doc["rows"]] == ["passed", "not_run"]


def _twin_features():
    return {
        "locations": {"M4": [], "M5": [], "S1a": ["skills/"], "S1b": ["hooks/"]},
        "rows": [
            {"id": "entrypoint:skills/rf/scripts/drive.py", "kind": "entrypoint", "now": "skills/rf/scripts/drive.py", "to": "M4"},
            {"id": "entrypoint:skills/cr/scripts/drive.py", "kind": "entrypoint", "now": "skills/cr/scripts/drive.py", "to": "M5"},
            {"id": "skill:rf", "kind": "skill", "now": "skills/rf", "to": "S1a"},
            {"id": "skill:cr", "kind": "skill", "now": "skills/cr", "to": "S1a"},
        ],
    }


def test_scripts_with_the_same_name_are_told_apart_by_the_skill_that_calls_them(tmp_path):
    (tmp_path / "hooks").mkdir()
    (tmp_path / "skills/rf").mkdir(parents=True)
    (tmp_path / "skills/rf/SKILL.md").write_text('python3 "$RF/drive.py" 1\n', encoding="utf-8")
    (tmp_path / "skills/cr").mkdir()
    (tmp_path / "skills/cr/SKILL.md").write_text("bin/ndf review drive\n", encoding="utf-8")
    got = probe.judge(_twin_features(), root=tmp_path, repo=tmp_path)
    assert "skills/rf/scripts/drive.py（M4）" in got["skill:rf"]["reason"]
    assert got["entrypoint:skills/rf/scripts/drive.py"]["result"] == "failed"
    assert got["entrypoint:skills/cr/scripts/drive.py"]["result"] == "not_run"


def test_a_written_path_points_at_that_script_from_any_skill(tmp_path):
    (tmp_path / "hooks").mkdir()
    (tmp_path / "skills/rf").mkdir(parents=True)
    (tmp_path / "skills/rf/SKILL.md").write_text("python3 skills/cr/scripts/drive.py 1\n", encoding="utf-8")
    got = probe.judge(_twin_features(), root=tmp_path, repo=tmp_path)
    assert got["entrypoint:skills/cr/scripts/drive.py"]["result"] == "failed"
    assert got["entrypoint:skills/rf/scripts/drive.py"]["result"] == "not_run"


def test_rows_passed_in_a_later_record_pass_and_name_that_record(tmp_path, plugin):
    (tmp_path / "trial").mkdir()
    (tmp_path / "trial/2026-11-15.json").write_text(json.dumps({"passed": []}), encoding="utf-8")
    (tmp_path / "trial/2026-11-20.json").write_text(json.dumps({"passed": ["skill:tidy"]}), encoding="utf-8")
    (tmp_path / "trial/2026-11-25.json").write_text(json.dumps({"passed": []}), encoding="utf-8")
    passed, latest = probe.records(tmp_path)
    got = probe.judge(_features(), passed, latest, root=plugin, repo=plugin)
    assert got["skill:tidy"] == {"result": "passed", "record": "trial/2026-11-20.json"}
    assert got["skill:merged"]["record"] == "trial/2026-11-25.json"


def test_main_refuses_a_missing_plugin_root(tmp_path):
    """読むプラグインの根は引数で受け、無い根では書かずに止まる（#3617 の受け入れ条件 19）。"""
    assert probe.main(["--plugin-root", str(tmp_path / "none")]) == 2


def test_texts_read_under_the_given_root(plugin):
    got = probe.texts(_features()["locations"], plugin)
    assert "skills/merged/SKILL.md" in got and all(not p.endswith(".py") for p in got)


def test_default_plugin_root_is_ndf_next_of_this_repository():
    assert (probe.PLUGIN_ROOT / "manifests" / "skills.json").is_file()
