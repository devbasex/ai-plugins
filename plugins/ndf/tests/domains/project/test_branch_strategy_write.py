"""`ndf project branch-strategy`（解析のサブコマンド）の単体テスト: 作る・書く・消す（#3672 のテスト設計の 8・12・15。#3718）。

入力と期待値は 10 系の `plugins/ndf/scripts/tests/test_branch_strategy.py` の同じ観点のテストと同じにする。
5 種の設定は `tests/fixtures/branch_strategy/` の 1 か所にあり、10 系のテストも同じ fixture を読む。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from filelock import FileLock
from strategy_fixture import FIX, KINDS, section

from ndf import contracts
from ndf.core import branch_strategy as bs
from ndf.core import branch_strategy_legacy as legacy
from ndf.domains.project import strategy_measure, strategy_migrate
from ndf.domains.project import strategy_write as bw

from ._branch_strategy import fake_api, git, history_repo, make_repo, run_cli

REPO_ROOT = Path(__file__).resolve().parents[5]  # このリポジトリ（ai-plugins）の根


# --- 作る（受け入れ条件 8） --------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_init_values_new_repo_has_no_cd(kind):
    sec, q = bw.init_values(kind, {"production": "main", "merge_queue": True}, bw.load_presets())
    assert q == [] and sec["release"]["cd"] is None and sec["release"]["tag"] is None and sec["release"]["github_release"] is False
    assert bs.validate(sec) == []


def test_kinds_follow_the_presets_and_the_schema_enum():
    """種類の一覧は既定の表のキーの並びで、スキーマの enum（`custom` を除く）と同じ。"""
    enum = contracts.load_json(bs.SCHEMA)["$defs"]["BranchStrategy"]["properties"]["kind"]["enum"]
    assert bw.kinds() == tuple(bw.load_presets()["kinds"]) == tuple(k for k in enum if k != "custom")


def test_init_values_fixtures_and_names():
    facts = json.loads((FIX / "facts.json").read_text(encoding="utf-8"))
    for kind in KINDS:
        assert bw.init_values(kind, facts[kind], bw.load_presets())[0] == section(kind)
    sec, _ = bw.init_values("docs", {"production": "trunk"}, bw.load_presets())
    assert sec["branches"] == {"production": "trunk", "base": "trunk"}
    sec, q = bw.init_values("plugin", {"production": "main", "merge_queue": None}, bw.load_presets())
    assert q[0]["why"] == "merge_queue_unknown" and q[0]["candidates"] == ["merge-queue", "strict"]
    sec, q = bw.init_values("plugin", {"production": "main", "merge_queue": False}, bw.load_presets())
    assert q == [] and sec["merge"]["method"] == "strict"


def test_classify_repo(tmp_path):
    root = make_repo(tmp_path, {"project.json": {"version": 1}})
    assert strategy_measure.classify_repo(root, fake_api([]))[0] == "new"
    assert strategy_measure.classify_repo(root, fake_api([], tags=["v1"]))[0] == "existing"
    assert strategy_measure.classify_repo(root, lambda p: (None, "boom"))[0] == "existing"
    root2 = history_repo(tmp_path / "b", 0)
    assert strategy_measure.classify_repo(root2, fake_api([]))[0] == "existing"


def test_cli_init_asks_the_kind_and_writes_only_with_it(tmp_path, capsys, monkeypatch):
    """新規のリポジトリで種類を渡さなければ `no_kind` を問うて書かず、渡せば CD・タグ・Release の無い既定の行を書く。"""
    root = make_repo(tmp_path, {"project.json": {"version": 1}})
    monkeypatch.setattr(strategy_measure, "default_api", lambda root, deadline: fake_api([]))
    before = (root / ".ndf" / "project.json").read_bytes()
    code, out = run_cli(capsys, root, "init")
    assert code == 3 and out["status"] == "ask" and [q["why"] for q in out["questions"]] == ["no_kind"]
    assert (root / ".ndf" / "project.json").read_bytes() == before
    code, out = run_cli(capsys, root, "init", "--kind", "docs")
    assert code == 0 and out["status"] == "ok" and out["kind"] == "docs" and out["diff"]
    written = bs.load(root).section
    assert written["release"]["cd"] is None and written["release"]["tag"] is None and written["release"]["github_release"] is False
    assert written == bw.init_values("docs", {"production": "main"}, bw.load_presets())[0]


def test_cli_init_on_an_existing_repo_stops(tmp_path, capsys, monkeypatch):
    root = history_repo(tmp_path, 0)
    monkeypatch.setattr(strategy_measure, "default_api", lambda root, deadline: fake_api([]))
    code, out = run_cli(capsys, root, "init", "--kind", "docs")
    assert code == 1 and out["status"] == "stopped" and out["failed"][0]["rule"] == "existing"
    assert "branch_strategy" not in json.loads((root / ".ndf" / "project.json").read_text(encoding="utf-8"))


# --- 書く・消す（受け入れ条件 12） -------------------------------------------------------------


def test_apply_answers_marks_custom():
    res = {"status": "ask", "values": section("docs"), "questions": [{"pointer": "/merge/commit"}]}
    same = bw.apply_answers(res, {"/merge/commit": "merge"})
    assert same["values"]["kind"] == "docs" and same["status"] == "ok"
    changed = bw.apply_answers(res, {"/merge/commit": "squash"})
    assert changed["values"]["kind"] == "custom" and changed["evidence"][-1]["source"] == "answer"


def test_plan_write_rejects_without_writing(tmp_path):
    root = make_repo(tmp_path, {"project.json": {"version": 1}})
    before = (root / ".ndf" / "project.json").read_bytes()
    bad = section("docs")
    bad["merge"]["method"] = "direct"
    plan = bw.plan_write(root, bad)
    assert plan.failed and plan.diff == []
    with pytest.raises(bw.WriteError):
        bw.commit_plan(root, plan, None)
    assert (root / ".ndf" / "project.json").read_bytes() == before


def test_write_digest_and_idempotent(tmp_path):
    root = make_repo(tmp_path, {"project.json": {"version": 1}})
    plan = bw.plan_write(root, section("docs"))
    with pytest.raises(bw.WriteError):
        bw.commit_plan(root, plan, "0" * 64)
    assert "branch_strategy" not in json.loads((root / ".ndf" / "project.json").read_text(encoding="utf-8"))
    bw.commit_plan(root, plan, plan.digest)
    assert bs.load(root).section == section("docs")
    assert bw.plan_write(root, section("docs")).diff == []


def test_remove_section(tmp_path):
    root = make_repo(tmp_path, {"project.json": {"version": 1, "branch_strategy": section("docs")}})
    plan = bw.plan_remove(root)
    bw.commit_plan(root, plan, plan.digest)
    assert "branch_strategy" not in json.loads((root / ".ndf" / "project.json").read_text(encoding="utf-8"))
    assert bw.plan_remove(root).diff == []


def test_lock_is_shared_with_the_10_series(tmp_path, monkeypatch):
    """錠は git の共通ディレクトリの `ndf-branch-strategy.lock`（10 系と同じ実ファイル）で、取られていれば書かない。"""
    root = make_repo(tmp_path, {"project.json": {"version": 1}})
    plan = bw.plan_write(root, section("docs"))
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    assert bw.lock_path(root) == bw.lock_at(common) == common / "ndf-branch-strategy.lock"
    monkeypatch.setattr(bw.limits, "seconds", lambda name: 0.1)
    with FileLock(str(common / "ndf-branch-strategy.lock")):
        with pytest.raises(bw.WriteError) as e:
            bw.commit_plan(root, plan, plan.digest)
    assert "別の実行" in e.value.reason


def test_cli_write_rejects_a_rule_breaking_answer_without_writing(tmp_path, capsys):
    root = make_repo(tmp_path, {"project.json": {"version": 1, "branch_strategy": section("docs")}})
    before = (root / ".ndf" / "project.json").read_bytes()
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"/merge/method": "direct"}), encoding="utf-8")
    code, out = run_cli(capsys, root, "write", "--answers", str(answers), "--digest", "0" * 64)
    assert code == 1 and out["status"] == "stopped" and out["failed"]
    assert (root / ".ndf" / "project.json").read_bytes() == before


def test_cli_write_answers_keeps_the_declared_section(tmp_path, capsys):
    """既存のセクションへの write --answers は今の設定を基盤にし、回答したキーだけを変える（測り直して既定へ戻さない）。"""
    declared = section("docs")
    declared["merge"]["actor"] = "human"
    root = make_repo(tmp_path, {"project.json": {"version": 1, "branch_strategy": declared}})
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"/merge/commit": "squash"}), encoding="utf-8")
    code, out = run_cli(capsys, root, "write", "--answers", str(answers))
    assert code == 0 and out["status"] == "ok"
    assert out["values"]["merge"] == {**declared["merge"], "commit": "squash"}


def test_migrate_remove_row_only_clears_the_key_the_check_found(tmp_path):
    root = make_repo(
        tmp_path, {"project.json": {"version": 1, "branch_strategy": section("docs")}, "worktree.json": {"version": 1, "branch_flow": {}}}
    )
    with pytest.raises(bs.StrategyError):
        bs.load(root)  # 旧いキーの検査がそのキーを返す
    plan, _ = strategy_migrate.plan_migration(root, legacy.load_moves())
    bw.commit_plan(root, plan, plan.digest)
    assert bs.load(root).section == section("docs")


# --- ai-plugins の値（受け入れ条件 15） --------------------------------------------------------


@pytest.mark.skipif(not (REPO_ROOT / ".ndf" / "project.json").is_file(), reason="プラグインだけを写した実行にはリポジトリの設定が無い")
def test_ai_plugins_section_is_the_plugin_row_with_its_measured_facts():
    """ai-plugins の `.ndf/project.json` のセクションは、`init --kind plugin` に ai-plugins の測った事実を渡した結果と一致する。
    移行が持ち込んだ利用者の値（届け先 `release.deliveries`・レビューの単位 `reviews`）だけが init の外にある。"""
    declared = json.loads((REPO_ROOT / ".ndf" / "project.json").read_text(encoding="utf-8"))[bs.SECTION]
    facts = {"production": "main", "plugin": "ndf", "runtimes": ["claude", "codex"], "workflow": "release-cd.yml", "merge_queue": True}
    init, questions = bw.init_values("plugin", facts, bw.load_presets())
    assert questions == []
    carried = copy.deepcopy(declared)
    carried.pop("reviews", None)
    carried["release"].pop("deliveries", None)
    assert carried == init


FLOW = {
    "stages": {"確定仕様化": {"enabled": False, "reason": "別の場所で管理する"}},
    "close": {
        "release_verification": {"required": False, "reason": "監視で確かめる"},
        "retrospective_line": {"required": False, "reason": "週次の会で行う"},
    },
}


def test_issue_4580_branch_strategy_write_and_remove_keep_the_flow(tmp_path):
    """受け入れ条件 13・I3: ブランチ戦略の書き出しと削除は、利用者の流れの設定（workflow）を書き換えない。"""
    root = make_repo(tmp_path, {"project.json": {"version": 1, "workflow": FLOW}})
    plan = bw.plan_write(root, section("web-trunk"))
    bw.commit_plan(root, plan, plan.digest)
    assert json.loads((root / ".ndf" / "project.json").read_text(encoding="utf-8"))["workflow"] == FLOW
    plan = bw.plan_remove(root)
    bw.commit_plan(root, plan, plan.digest)
    assert json.loads((root / ".ndf" / "project.json").read_text(encoding="utf-8"))["workflow"] == FLOW
