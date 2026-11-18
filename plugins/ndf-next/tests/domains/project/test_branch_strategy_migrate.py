"""`ndf project branch-strategy`（解析のサブコマンド）の単体テスト: 旧い設定を移す（#3672 のテスト設計の 20。#3718）。

入力と期待値は 10 系の `plugins/ndf/scripts/tests/test_branch_strategy.py` の同じ観点のテストと同じにする。
5 種の設定は `tests/fixtures/branch_strategy/` の 1 か所にあり、10 系のテストも同じ fixture を読む。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from strategy_fixture import FIX

from ndf import contracts
from ndf.core import branch_strategy as bs
from ndf.core import branch_strategy_legacy as legacy
from ndf.core import json_pointer
from ndf.domains.project import cli, strategy_migrate
from ndf.domains.project import strategy_write as bw

from ._branch_strategy import git, make_repo, run_cli

PROJECT_SRC = Path(cli.__file__).resolve().parent


# --- 移す（受け入れ条件 20） -------------------------------------------------------------------

# ai-plugins の移行前の設定（plugins/ndf/scripts/tests/test_branch_strategy.py と共有する入力）
AI_PLUGINS_OLD = json.loads((FIX / "ai-plugins-old-settings.json").read_text(encoding="utf-8"))


def test_migrate_moves_old_keys(tmp_path):
    root = make_repo(tmp_path, copy.deepcopy(AI_PLUGINS_OLD))
    plan, q = strategy_migrate.plan_migration(root, legacy.load_moves())
    assert q == [] and not plan.failed
    bw.commit_plan(root, plan, plan.digest)
    s = bs.load(root).section
    assert s["release_strategy"] == "merge_base_branch" and s["branches"] == {"production": "main", "base": "develop"}
    assert s["merge"]["admin"] is True and s["merge"]["method"] == "direct"
    assert s["release"]["cd"] == {"workflow": "release-cd.yml"} and s["release"]["tag"] == "{plugin}--v{version}"
    assert {"name": "dev", "ref": "develop", "workflow": "release-dev.yml"} in s["channels"]
    wt = json.loads((root / ".ndf" / "worktree.json").read_text(encoding="utf-8"))
    assert wt == {"version": 1, "guard": {"allow_paths": [".ndf/"]}}
    assert json.loads((root / ".ndf" / "supervise.json").read_text(encoding="utf-8")) == {"version": 1, "test": {"command": "x"}}
    plan2, _ = strategy_migrate.plan_migration(root, legacy.load_moves())
    assert plan2.diff == []


def test_migrate_resumes_after_partial_write(tmp_path):
    root = make_repo(tmp_path, copy.deepcopy(AI_PLUGINS_OLD))
    plan, _ = strategy_migrate.plan_migration(root, legacy.load_moves())
    (root / ".ndf" / "project.json").write_text(json.dumps(plan.files["project.json"]), encoding="utf-8")
    with pytest.raises(bs.StrategyError):
        bs.load(root)  # セクションと旧いキーが両方ある（I1）
    again, _ = strategy_migrate.plan_migration(root, legacy.load_moves())
    bw.commit_plan(root, again, again.digest)
    assert bs.load(root).section == plan.files["project.json"]["branch_strategy"]


def test_migrate_unknown_value_asks_and_rule_failure_stops(tmp_path):
    root = make_repo(tmp_path, {"worktree.json": {"version": 1, "branch_flow": {"promotion": "odd"}}})
    _, q = strategy_migrate.plan_migration(root, legacy.load_moves())
    assert q and q[0]["pointer"] == "/release_strategy"
    root2 = make_repo(tmp_path / "b", {"worktree.json": {"version": 1, "base_branch": "main", "production_branch": "main"}})
    plan2, q2 = strategy_migrate.plan_migration(root2, legacy.load_moves())
    assert q2 == [] and any(f["rule"] == "R1" for f in plan2.failed)


def test_migrate_fills_omitted_branch_from_default_branch(tmp_path):
    """省いた本番ブランチは、旧い処理と同じく既定ブランチで補う（プリセットの main にしない）。"""
    root = make_repo(tmp_path, {"worktree.json": {"version": 1, "base_branch": "develop"}})
    git(root, "branch", "-m", "master")
    plan, q = strategy_migrate.plan_migration(root, legacy.load_moves())
    assert q == [] and not plan.failed
    assert plan.files["project.json"]["branch_strategy"]["branches"] == {"production": "master", "base": "develop"}
    root2 = make_repo(tmp_path / "b", {"worktree.json": {"version": 1, "production_branch": "main"}})
    plan2, q2 = strategy_migrate.plan_migration(root2, legacy.load_moves())
    assert q2 == [] and not plan2.failed
    sec = plan2.files["project.json"]["branch_strategy"]
    assert sec["release_strategy"] == "trunk" and sec["branches"] == {"production": "main", "base": "main"}


def test_migrate_implied_trunk_with_admin_asks_instead_of_failing(tmp_path):
    old = {
        "worktree.json": {"version": 1, "production_branch": "main"},
        "project.json": {"version": 1, "production_merge": {"admin": True}},
    }
    root = make_repo(tmp_path, old)
    plan, q = strategy_migrate.plan_migration(root, legacy.load_moves())
    assert not plan.failed and plan.files == {} and [x["pointer"] for x in q] == ["/release_strategy"]
    assert {"/release_strategy": "trunk", "/merge/admin": False} in q[0]["candidates"]
    assert {"source": "repo", "ref": ".ndf/project.json:/production_merge/admin", "value": "True"} in q[0]["evidence"]
    plan2, q2 = strategy_migrate.plan_migration(root, legacy.load_moves(), {"/release_strategy": "trunk", "/merge/admin": False})
    assert q2 == [] and not plan2.failed
    sec = plan2.files["project.json"]["branch_strategy"]
    assert sec["release_strategy"] == "trunk" and sec["merge"]["admin"] is False
    plan3, q3 = strategy_migrate.plan_migration(
        root, legacy.load_moves(), {"/release_strategy": "merge_base_branch", "/branches/base": "develop"}
    )
    assert q3 == [] and not plan3.failed
    sec3 = plan3.files["project.json"]["branch_strategy"]
    assert sec3["release_strategy"] == "merge_base_branch" and sec3["branches"] == {"production": "main", "base": "develop"}


def test_cli_migrate_then_digest(tmp_path, capsys):
    root = make_repo(tmp_path, copy.deepcopy(AI_PLUGINS_OLD))
    code, out = run_cli(capsys, root, "migrate")
    assert code == 0 and out["status"] == "ok" and out["diff"]
    assert "branch_strategy" not in json.loads((root / ".ndf" / "project.json").read_text(encoding="utf-8"))
    code, done = run_cli(capsys, root, "migrate", "--digest", out["digest"])
    assert code == 0 and done["status"] == "ok"
    assert bs.load(root).section["release_strategy"] == "merge_base_branch"


def test_project_analysis_reads_no_old_keys_outside_the_migration():
    """`domains/project/` で旧い 4 か所のキーを名指すのは移行（`strategy_migrate.py`）だけ。キーの名前は移す行の表から引き、
    セクションと同じ名前のキー（`release` など）は外す。"""
    section_keys = set(contracts.load_json(bs.SCHEMA)["$defs"]["BranchStrategy"]["properties"])
    names = {json_pointer.tokens(m.old)[0] for m in legacy.load_moves()} - section_keys - {bs.SECTION}
    assert names  # 表が旧いキーを持つ（空なら照らす意味が無い）
    hits = [
        f"{p.name}: {n}"
        for p in sorted(PROJECT_SRC.glob("*.py"))
        if p.name != "strategy_migrate.py"
        for n in sorted(names)
        if f'"{n}"' in p.read_text(encoding="utf-8")
    ]
    assert hits == []
