"""ブランチ戦略の宣言を読む部品（`ndf.core.branch_strategy`。#3672・#3681）の単体テスト。

5 種の宣言は `tests/fixtures/branch_strategy/` の 1 か所にあり、10 系のテスト（`plugins/ndf/scripts/tests/strategy_fixture.py`）も
同じファイルを読む。
"""

from __future__ import annotations

import json
from pathlib import Path

import git_sandbox
import pytest

from ndf import contracts
from ndf.core import branch_strategy as bs

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "branch_strategy"
KINDS = ("web-develop", "web-trunk", "mobile", "docs", "plugin")
RESULT_SCHEMA = contracts.schema_path("io/branch-strategy.result")


def section(kind: str) -> dict:
    return json.loads((FIX / f"{kind}.json").read_text(encoding="utf-8"))


@pytest.fixture
def make_repo(tmp_path, monkeypatch):
    git_sandbox.isolate_git(tmp_path, monkeypatch)

    def make(ndf: dict[str, dict]) -> Path:
        root = tmp_path / "repo"
        (root / ".ndf").mkdir(parents=True)
        git_sandbox.sh(root, "init", "-q", "-b", "main")
        for name, doc in ndf.items():
            (root / ".ndf" / name).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        git_sandbox.sh(root, "add", "-A")
        git_sandbox.sh(root, "commit", "-qm", "init", identity=True)
        return root

    return make


# --- スキーマと組み合わせの規則（テスト設計の 2） -----------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_five_kinds_pass_schema(kind):
    assert bs.validate(section(kind)) == []


@pytest.mark.parametrize(
    "rule,kind,edit",
    [
        ("R1", "web-trunk", lambda s: s["branches"].update(base="develop")),
        ("R1", "web-develop", lambda s: s["branches"].update(base="main")),
        ("R2", "docs", lambda s: s["merge"].update(method="direct")),
        ("R3", "plugin", lambda s: s["merge"].update(admin=True)),
        ("R4", "plugin", lambda s: s["release"].update(version_store={"kind": "none"}, tag=None, github_release=False)),
        ("R5", "web-develop", lambda s: s["approval"].update(target="production_pr")),
        ("R6", "web-trunk", lambda s: s["sprint"].update(integrate="check")),
        ("R7", "docs", lambda s: s["channels"].append({"name": "dev", "ref": "main", "workflow": "d.yml"})),
        ("R8", "web-trunk", lambda s: s["release"].update(tag="v{version}", cd={"workflow": "x.yml"})),
        ("R9", "web-develop", lambda s: s["sprint"].update(start="production")),
        ("R10", "docs", lambda s: s["release"].update(version_store={"kind": "plugin"}, github_release=True)),
    ],
)
def test_each_rule_rejects_its_input(rule, kind, edit):
    s = section(kind)
    edit(s)
    assert rule in [f["rule"] for f in bs.validate(s)]


def test_shape_failure_names_the_pointer():
    s = section("docs")
    s["merge"]["method"] = 3
    assert {"rule": "shape", "pointer": "/merge/method"} in bs.validate(s)
    assert {"rule": "shape", "pointer": ""} in bs.validate("not an object")


def test_project_schema_refers_to_the_section_definition():
    schema = contracts.load_json(bs.SCHEMA)
    assert schema["properties"][bs.SECTION] == {"$ref": f"#/$defs/{bs.DEF}"}
    assert schema["$defs"][bs.DEF]["properties"]["release_strategy"]["enum"] == ["merge_base_branch", "release_branch", "trunk"]


# --- 読む部品（I1・I3・I4） --------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_load_reads_each_kind(make_repo, kind):
    root = make_repo({"project.json": {"version": 1, "branch_strategy": section(kind)}})
    assert bs.load(root).section == section(kind)


def test_load_stops_without_section(make_repo):
    root = make_repo({"project.json": {"version": 1}})
    with pytest.raises(bs.StrategyError) as e:
        bs.load(root)
    assert "ndf project branch-strategy init" in e.value.reason and e.value.exit_id == bs.PRECONDITION


@pytest.mark.parametrize(
    "files,where",
    [
        ({"worktree.json": {"version": 1, "branch_flow": {"promotion": "whole"}}}, "`.ndf/worktree.json` の `/branch_flow/promotion`"),
        ({"worktree.json": {"version": 1, "base_branch": "develop"}}, "`.ndf/worktree.json` の `/base_branch`"),
        ({"project.json": {"version": 1, "production_merge": {"admin": True}}}, "`.ndf/project.json` の `/production_merge/admin`"),
        ({"supervise.json": {"version": 1, "release": {"dev_workflow": "d.yml"}}}, "`.ndf/supervise.json` の `/release/dev_workflow`"),
        ({"release.json": {"version": 1, "version_files": ["a"]}}, "`.ndf/release.json` の `/version_files`"),
    ],
)
def test_load_stops_on_legacy_key(make_repo, files, where):
    ndf = {"project.json": {"version": 1, "branch_strategy": section("plugin")}}
    for name, doc in files.items():
        ndf[name] = {**ndf.get(name, {}), **doc}
    root = make_repo(ndf)
    with pytest.raises(bs.StrategyError) as e:
        bs.load(root)
    assert e.value.reason.startswith(where) and e.value.reason.endswith("`ndf project branch-strategy migrate` で移してください")


def test_load_stops_on_schema_and_broken_json(make_repo):
    bad = section("docs")
    bad["merge"]["method"] = "direct"
    root = make_repo({"project.json": {"version": 1, "branch_strategy": bad}})
    with pytest.raises(bs.StrategyError) as e:
        bs.load(root)
    assert "がスキーマに落ちる: " in e.value.reason and "R2 `" in e.value.reason and e.value.exit_id == bs.VIOLATION
    (root / ".ndf" / "project.json").write_text("{", encoding="utf-8")
    with pytest.raises(bs.StrategyError) as e:
        bs.load(root)
    assert e.value.exit_id == bs.UNREADABLE


def test_load_at_ref_reads_target_branch(make_repo):
    root = make_repo({"project.json": {"version": 1, "branch_strategy": section("docs")}})
    git_sandbox.sh(root, "checkout", "-qb", "other")
    (root / ".ndf" / "project.json").write_text(json.dumps({"version": 1}), encoding="utf-8")
    assert bs.load_at_ref(root, "main").section == section("docs")
    with pytest.raises(bs.StrategyError) as e:
        bs.load_at_ref(root, "nope")
    assert "`nope`" in e.value.reason


def test_get_reads_one_value():
    s = bs.Strategy(section("mobile"))
    assert bs.get(s, "/branches/base") == "develop"
    assert bs.get(s, "/verify") is None
    assert bs.get(s, "/channels/0/name") == "stable"
    for missing in ("/nope", "/channels/9", "branches"):
        with pytest.raises(bs.StrategyError):
            bs.get(s, missing)


# --- スナップショット（I13） -------------------------------------------------------------------


def test_snapshot_digest_and_diff():
    s = bs.Strategy(section("plugin"))
    snap = bs.snapshot(s, "start")
    assert snap["source"] == "start" and snap["section"] == section("plugin") and snap["section"] is not s.section
    reordered = bs.Strategy(json.loads(json.dumps(section("plugin"), sort_keys=True)))
    assert bs.snapshot(reordered, "start")["digest"] == snap["digest"]
    assert bs.snapshot_diff(snap, s) == []
    changed = section("plugin")
    changed["merge"]["method"] = "strict"
    del changed["verify"]
    assert bs.snapshot_diff(snap, bs.Strategy(changed)) == ["/merge/method", "/verify"]


# --- 宛先と起点（テスト設計の 22） -------------------------------------------------------------

EXPECT = {
    # 種類: (スプリントの PR の宛先, スプリントの起点)
    "web-develop": ("develop", "develop"),
    "web-trunk": ("main", "main"),
    "mobile": ("develop", "main"),
    "docs": ("main", "main"),
    "plugin": ("main", "main"),
}


@pytest.mark.parametrize("kind", KINDS)
def test_five_kinds_targets(kind):
    s = bs.Strategy(section(kind))
    assert (s.pr_target("sprint"), s.sprint_start_ref()) == EXPECT[kind]
    assert s.pr_target("release") == "main"


def test_doc_target():
    mobile = bs.Strategy(section("mobile"))
    assert mobile.pr_target("doc", "sprint/x") == "sprint/x"
    assert mobile.pr_target("doc", "sprint/x", integrated=True) == "sprint/x"
    docs = bs.Strategy(section("docs"))
    assert docs.pr_target("doc", "sprint/x") == "sprint/x" and docs.pr_target("doc", "sprint/x", integrated=True) == "main"
    assert bs.Strategy(section("web-develop")).pr_target("doc") == "develop"
    with pytest.raises(ValueError):
        mobile.pr_target("doc")
    with pytest.raises(ValueError):
        docs.pr_target("other")


# --- 作成の結果の契約 ---------------------------------------------------------------------------


def test_result_schema_takes_each_status():
    ok = {
        "status": "ok",
        "kind": "docs",
        "values": section("docs"),
        "evidence": [{"source": "preset", "ref": "docs", "value": "/"}],
        "questions": [],
        "diff": [{"op": "add", "path": "/branch_strategy", "value": {}}],
        "digest": "0" * 64,
        "failed": [],
        "warnings": [],
    }
    ask = {"status": "ask", "kind": None, "questions": [{"pointer": "/kind", "why": "no_kind", "candidates": list(KINDS), "evidence": []}]}
    stopped = {"status": "stopped", "failed": [{"rule": "R2", "pointer": "/"}]}
    for result in (ok, ask, stopped):
        assert contracts.schema_errors(result, RESULT_SCHEMA) == []
    assert contracts.schema_errors({"status": "done"}, RESULT_SCHEMA)
    assert contracts.schema_errors({**stopped, "extra": 1}, RESULT_SCHEMA)
