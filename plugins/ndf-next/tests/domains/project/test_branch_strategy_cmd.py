"""`ndf project branch-strategy`（解析のサブコマンド）の単体テスト（#3672 のテスト設計の 8・9・10・11・12・15・20。#3718）。

入力と期待値は 10 系の `plugins/ndf/scripts/tests/test_branch_strategy.py` の同じ観点のテストと同じにする。
5 種の宣言は `tests/fixtures/branch_strategy/` の 1 か所にあり、10 系のテストも同じ fixture を読む。
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest
from filelock import FileLock

from ndf import contracts
from ndf.core import branch_strategy as bs
from ndf.core import branch_strategy_legacy as legacy
from ndf.core import json_pointer
from ndf.domains.project import cli, strategy_measure, strategy_migrate
from ndf.domains.project import strategy_write as bw

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "branch_strategy"
REPO_ROOT = Path(__file__).resolve().parents[5]  # このリポジトリ（ai-plugins）の根
PROJECT_SRC = Path(cli.__file__).resolve().parent
RESULT_SCHEMA = "schemas/io/branch-strategy.result.schema.json"
KINDS = ("web-develop", "web-trunk", "mobile", "docs", "plugin")
C = ["-c", "user.email=t@example.com", "-c", "user.name=t"]


def section(kind: str) -> dict:
    return json.loads((FIX / f"{kind}.json").read_text(encoding="utf-8"))


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def make_repo(tmp_path: Path, ndf: dict[str, dict]) -> Path:
    root = tmp_path / "repo"
    (root / ".ndf").mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    for name, doc in ndf.items():
        (root / ".ndf" / name).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    git(root, *C, "add", "-A")
    git(root, *C, "commit", "-qm", "init")
    return root


def fake_api(prs, tags=(), releases=(), rules=None, rulesets=None, default="main", protection=None):
    data = {
        "repos/{owner}/{repo}": {"default_branch": default},
        "pulls": [{"number": n, "merged_at": "x", "base": {"ref": b}, "head": {"ref": h}} for n, b, h in prs],
        "tags": [{"name": t} for t in tags],
        "releases": [{"tag_name": t} for t in releases],
    }

    def api(path):
        if path == "repos/{owner}/{repo}":
            return data[path], ""
        for key in ("pulls", "tags", "releases"):
            if f"/{key}?" in path:
                return data[key], ""
        if "/rules/branches/" in path:
            return (rules, "") if rules is not None else (None, "missing")
        if path.endswith("/protection"):
            return (protection, "") if protection is not None else (None, "missing")
        if path.endswith(f"/branches/{default}"):
            return {"protection": {"enabled": protection is not None}}, ""
        if path.endswith("/rulesets"):
            return rulesets or [], ""
        return None, "unknown"

    return api


def history_repo(tmp_path: Path, merges: int, extra: dict | None = None) -> Path:
    root = make_repo(tmp_path, {"project.json": {"version": 1}})
    for name, text in (extra or {}).items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    git(root, *C, "add", "-A")
    git(root, *C, "commit", "-q", "--allow-empty", "-m", "files")
    for i in range(merges):
        git(root, "checkout", "-qb", f"sprint/{i}")
        git(root, *C, "commit", "-q", "--allow-empty", "-m", f"work {i}")
        git(root, "checkout", "-q", "main")
        git(root, *C, "merge", "-q", "--no-ff", f"sprint/{i}", "-m", f"Merge pull request #{i + 1}")
    return root


def run_cli(capsys, root: Path, *args: str) -> tuple[int, dict]:
    code = cli.main(["branch-strategy", *args, "--root", str(root)])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert contracts.schema_errors(out, RESULT_SCHEMA) == []
    return code, out


def assert_result_shape(res: dict) -> None:
    """作成の結果の形（`branch-strategy.result.schema.json`）に通る。"""
    assert contracts.schema_errors(res, RESULT_SCHEMA) == []


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
    assert bw.lock_path(root) == common / "ndf-branch-strategy.lock"
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
    """既存のセクションへの write --answers は今の宣言を土台にし、答えたキーだけを変える（測り直して既定へ戻さない）。"""
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


# --- 移す（受け入れ条件 20） -------------------------------------------------------------------

AI_PLUGINS_OLD = {
    "worktree.json": {
        "version": 1,
        "base_branch": "develop",
        "production_branch": "main",
        "guard": {"allow_paths": [".ndf/"]},
        "branch_flow": {
            "start": "base",
            "integrate": "check",
            "doc_prs": "base",
            "promotion": "whole",
            "reviews": {
                "impl": {"checks": [], "diff": "none"},
                "sprint": {"checks": ["cross-refactoring", "cross-review"], "diff": "unchecked"},
            },
        },
    },
    "project.json": {
        "version": 1,
        "production_merge": {"admin": True},
        "delivery": [
            {
                "target": "p",
                "kind": "auto",
                "trigger": "t",
                "channel": "production",
                "versioned": True,
                "publishes_release": True,
                "result": {"workflow": "release-cd.yml"},
            }
        ],
    },
    "supervise.json": {
        "version": 1,
        "test": {"command": "x"},
        "release": {"form": "package-plugin", "plugin": "ndf", "runtimes": ["claude", "codex"], "dev_workflow": "release-dev.yml"},
    },
}


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


# --- 測る（受け入れ条件 9・10・11） ------------------------------------------------------------


def test_measure_develop_main(tmp_path):
    root = history_repo(
        tmp_path,
        4,
        {
            "package.json": '{"version": "1.0.0"}',
            "CONTRIBUTING.md": "PR は `gh pr create --base develop` で出す\n",
            ".github/workflows/release.yml": "run: gh release create v1\n",
        },
    )
    prs = [(i, "develop", f"sprint/{i}") for i in range(1, 5)] + [(9, "main", "develop")]
    api = fake_api(prs, tags=["v1.0.0", "v1.1.0", "v1.2.0"], releases=["v1.0.0", "v1.1.0", "v1.2.0"])
    res = strategy_measure.measure_strategy(root, api)
    assert res["status"] == "ok", res["questions"]
    assert_result_shape(res)
    v = res["values"]
    assert res["kind"] == "web-develop" and v["release_strategy"] == "merge_base_branch" and v["branches"]["base"] == "develop"
    assert v["merge"]["method"] == "direct" and v["release"]["tag"] == "v{version}" and v["release"]["cd"] == {"workflow": "release.yml"}
    refs = {(e["pointer"], e["source"]) for e in res["evidence"]}
    assert ("/release_strategy", "pr") in refs and ("/release/tag", "tag") in refs and ("/release/form", "preset") in refs
    assert bs.validate(v) == []


def test_measure_does_not_take_a_sprint_branch_as_the_base(tmp_path):
    """課題の PR → sprint/s1 → develop → main の履歴では、一時的なスプリントブランチを長く残るブランチに数えない。"""
    root = history_repo(tmp_path, 4, {"package.json": '{"version": "1.0.0"}'})
    prs = [(i, "sprint/s1", f"issue/{i}") for i in range(1, 4)]
    prs += [(10 + i, "develop", f"sprint/s{i}") for i in range(1, 5)] + [(20, "main", "develop")]
    res = strategy_measure.measure_strategy(root, fake_api(prs))
    assert res["values"]["branches"]["base"] == "develop"
    assert not any(q["pointer"] == "/branches/base" for q in res["questions"])
    base_refs = {e["ref"] for e in res["evidence"] if e["pointer"] == "/branches/base"}
    assert base_refs and base_refs <= {f"#{10 + i}" for i in range(1, 5)}


def test_measure_trunk_without_tags(tmp_path):
    root = history_repo(tmp_path, 4)
    res = strategy_measure.measure_strategy(root, fake_api([(i, "main", f"sprint/{i}") for i in range(1, 5)]))
    assert res["status"] == "ok", res["questions"]
    assert_result_shape(res)
    v = res["values"]
    assert res["kind"] == "docs" and v["release_strategy"] == "trunk" and v["merge"]["method"] == "strict"
    assert v["release"]["tag"] is None and v["release"]["github_release"] is False
    assert any(e["pointer"] == "/release/tag" and e["ref"] == "0 件" for e in res["evidence"])
    assert ("/release/form", "preset") in {(e["pointer"], e["source"]) for e in res["evidence"]}


def test_measure_doc_conflict_and_insufficient(tmp_path):
    root = history_repo(tmp_path, 4, {"CONTRIBUTING.md": "`--base develop` で出す\n"})
    res = strategy_measure.measure_strategy(root, fake_api([(i, "main", f"sprint/{i}") for i in range(1, 5)]))
    assert res["status"] == "ask" and any(q["why"] == "conflict" and q["pointer"] == "/branches/base" for q in res["questions"])
    assert_result_shape(res)
    root2 = history_repo(tmp_path / "b", 0)
    res2 = strategy_measure.measure_strategy(root2, fake_api([]))
    assert res2["status"] == "ask" and {q["why"] for q in res2["questions"]} >= {"insufficient"}
    # PR が 0 件の既存のリポジトリは、種類を渡しても新規とみなさずに問う
    res3 = strategy_measure.measure_strategy(root2, fake_api([]), prefer="docs")
    assert res3["status"] == "ask" and {q["why"] for q in res3["questions"]} >= {"insufficient"}


def test_answering_the_asked_questions_completes_the_section(tmp_path):
    root = history_repo(tmp_path, 4, {"CONTRIBUTING.md": "`--base develop` で出す\n"})
    res = strategy_measure.measure_strategy(root, fake_api([(i, "main", f"sprint/{i}") for i in range(1, 5)]))
    assert res["values"]["release_strategy"] == "trunk" and res["evidence"]
    got = strategy_measure.complete_answers(res, {"/branches/base": "main"})
    assert got["status"] == "ok" and got["kind"] == "docs" and bs.validate(got["values"]) == []


def test_answering_other_questions_still_asks_the_required_review(tmp_path):
    root = history_repo(tmp_path, 4, {"CONTRIBUTING.md": "`--base develop` で出す\n"})
    protection = {"required_pull_request_reviews": {"required_approving_review_count": 1}, "enforce_admins": {"enabled": True}}
    api = fake_api([(i, "main", f"sprint/{i}") for i in range(1, 5)], rules=[], protection=protection)
    res = strategy_measure.measure_strategy(root, api)
    assert res["status"] == "ask" and res["facts"]["required_approvals"] == 1
    got = strategy_measure.complete_answers(res, {"/branches/base": "main"})
    assert got["status"] == "ask" and [q["pointer"] for q in got["questions"]] == ["/approval/via"]
    done = strategy_measure.complete_answers(res, {"/branches/base": "main", "/approval/via": "review"})
    assert done["status"] == "ok" and done["values"]["approval"]["via"] == "review"


def test_measure_tags_without_cd_asks_r10(tmp_path):
    root = history_repo(tmp_path, 4)
    prs = [(i, "main", f"sprint/{i}") for i in range(1, 5)]
    res = strategy_measure.measure_strategy(
        root, fake_api(prs, tags=["v1.0.0", "v1.0.1", "v1.0.2"], releases=["v1.0.0", "v1.0.1", "v1.0.2"])
    )
    assert res["status"] == "ask" and res["questions"][0]["pointer"] == "/release/cd"


def test_count_key_thresholds():
    rule = {"min_samples": 3, "min_ratio": 0.9}
    assert strategy_measure.count_key("/x", [("a", "1"), ("a", "2")], rule).why == "insufficient"
    assert strategy_measure.count_key("/x", [("a", str(i)) for i in range(3)], rule).value == "a"
    nine = [("a", str(i)) for i in range(9)] + [("b", "9")]
    assert strategy_measure.count_key("/x", nine, rule).value == "a"
    eight = [("a", str(i)) for i in range(8)] + [("b", "8"), ("b", "9")]
    m = strategy_measure.count_key("/x", eight, rule)
    assert m.why == "conflict" and m.candidates == ["a", "b"]


RF = strategy_measure.RuleFacts


@pytest.mark.parametrize(
    ("facts", "rs", "want"),
    [
        (RF(True, 0, True, False), "trunk", "merge-queue"),
        (RF(True, 0, True, False), "merge_base_branch", "merge-queue"),
        (RF(True, 0, False, True), "trunk", "strict"),
        (RF(True, 0, False, True), "merge_base_branch", "strict"),
        (RF(True, 0, False, False), "trunk", "conflict"),
        (RF(True, 0, False, False), "merge_base_branch", "direct"),
        (RF(False, 0, False, False), "trunk", "strict"),
        (RF(False, 0, False, False), "merge_base_branch", "direct"),
        (None, "trunk", "insufficient"),
    ],
)
def test_decide_merge_method(facts, rs, want):
    m = strategy_measure.decide_merge_method(facts, rs)
    assert (m.why or m.value) == want


def test_pick_kind_ambiguous_and_queue_matches_strict():
    presets = bw.load_presets()
    m = {
        "/release_strategy": strategy_measure.Measured("trunk"),
        "/sprint/start": strategy_measure.Measured("production"),
        "/release/version_store": strategy_measure.Measured({"kind": "plugin"}),
        "/merge/commit": strategy_measure.Measured("merge"),
        "/merge/method": strategy_measure.Measured("strict"),
    }
    assert strategy_measure.pick_kind(m, presets) == ("plugin", [])
    m["/release/version_store"] = strategy_measure.Measured({"kind": "json", "path": "p", "pointer": "/v"})
    kind, cands = strategy_measure.pick_kind(m, presets)
    assert kind is None and cands


def test_rule_facts_merge_rulesets_and_classic_protection():
    rules = [{"type": "merge_queue"}, {"type": "pull_request", "parameters": {"required_approving_review_count": 1}}]
    assert strategy_measure.rule_facts(fake_api([], rules=rules), "main") == RF(True, 1, True, False)
    protection = {"required_pull_request_reviews": {"required_approving_review_count": 2}, "required_status_checks": {"strict": True}}
    assert strategy_measure.rule_facts(fake_api([], rules=[], protection=protection), "main") == RF(True, 2, False, True)
    assert strategy_measure.rule_facts(fake_api([]), "main") == RF(False, 0, False, False)
    assert strategy_measure.rule_facts(lambda p: (None, "boom"), "main") is None


# --- ai-plugins の値（受け入れ条件 15） --------------------------------------------------------


@pytest.mark.skipif(not (REPO_ROOT / ".ndf" / "project.json").is_file(), reason="プラグインだけを写した実行にはリポジトリの宣言が無い")
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


def _commit(root: Path, subject: str, trailers: dict | None = None) -> str:
    body = ["-m", "\n".join(f"{k}: {v}" for k, v in trailers.items())] if trailers else []
    git(root, *C, "commit", "-q", "--allow-empty", "-m", subject, *body)
    return git(root, "rev-parse", "HEAD").strip()


def landed_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """本番ブランチの最初の親の履歴に、取り込みの記録（trailer）の各形を並べた木。"""
    root = make_repo(tmp_path, {"project.json": {"version": 1}, "worktree.json": {"remote": "up"}})
    pr, tip = strategy_measure.TRAILER_PR, strategy_measure.TRAILER_TIP
    sha = {"init": git(root, "rev-parse", "HEAD").strip()}
    sha["plain"] = _commit(root, "plain")
    sha["five"] = _commit(root, "landed five", {pr: "5", tip: sha["plain"]})
    sha["known"] = _commit(root, "landed known", {pr: "1", tip: sha["five"]})
    sha["wrong_tip"] = _commit(root, "landed wrong tip", {pr: "7", tip: sha["plain"]})
    sha["not_digit"] = _commit(root, "landed not digit", {pr: "abc", tip: sha["wrong_tip"]})
    sha["five_again"] = _commit(root, "landed five again", {pr: "5", tip: sha["not_digit"], strategy_measure.TRAILER_HEAD: "h"})
    sha["zero"] = _commit(root, "landed zero", {pr: "0", tip: sha["five_again"]})
    git(root, "checkout", "-qb", "side")
    _commit(root, "side work")
    git(root, "checkout", "-q", "main")
    git(root, *C, "merge", "-q", "--no-ff", "side", "-m", "Merge side")
    sha["merge"] = git(root, "rev-parse", "HEAD").strip()
    return root, sha


def test_collect_production_commits_reads_the_first_parent_history_and_landed_prs(tmp_path):
    """現状固定: 最初の親の履歴を新しい順に読み、記録の PR は親が NDF-Tip と同じ・数字・0 でない・未知のときだけ事例に足す。"""
    root, sha = landed_repo(tmp_path)
    s = strategy_measure.collect_samples(root, fake_api([(1, "main", "sprint/0")]), float("inf"), 30)
    assert s.production == "main" and "history" not in s.unreadable
    order = ["merge", "zero", "five_again", "not_digit", "wrong_tip", "known", "five", "plain", "init"]
    landed = {"zero": "0", "five_again": "5", "not_digit": "abc", "wrong_tip": "7", "known": "1", "five": "5"}
    assert [c["sha"] for c in s.commits] == [sha[k] for k in order]
    assert [c["parents"] for c in s.commits] == [2, 1, 1, 1, 1, 1, 1, 1, 0]
    assert [c["landed"] for c in s.commits] == [landed.get(k) for k in order]
    assert [c["subject"] for c in s.commits] == [
        "Merge side",
        "landed zero",
        "landed five again",
        "landed not digit",
        "landed wrong tip",
        "landed known",
        "landed five",
        "plain",
        "init",
    ]
    assert all(set(c) == {"sha", "parents", "subject", "landed"} for c in s.commits)
    # 新しい順に読むため、同じ PR は新しい方の記録が残る
    assert s.prs == [
        {"number": 1, "base": "main", "head": "sprint/0"},
        {"number": 5, "base": "main", "head": None, "landed": sha["five_again"]},
    ]


def test_collect_production_commits_prefers_the_remote_branch_and_honours_the_limit(tmp_path):
    """現状固定: リモートの本番ブランチがあればそちらを読み、件数は limit で切る。"""
    root, sha = landed_repo(tmp_path)
    git(root, "update-ref", "refs/remotes/up/main", sha["known"])
    s = strategy_measure.collect_samples(root, fake_api([]), float("inf"), 30)
    assert [c["sha"] for c in s.commits] == [sha[k] for k in ("known", "five", "plain", "init")]
    assert [p["number"] for p in s.prs] == [1, 5]
    assert s.prs[1] == {"number": 5, "base": "main", "head": None, "landed": sha["five"]}
    s = strategy_measure.collect_samples(root, fake_api([]), float("inf"), 2)
    assert [c["sha"] for c in s.commits] == [sha["known"], sha["five"]]


def test_collect_production_commits_marks_history_unreadable_without_the_branch(tmp_path):
    """現状固定: 本番ブランチがリモートにも手元にも無ければ `history` を読めないとし、履歴は空のまま。"""
    root, _ = landed_repo(tmp_path)
    s = strategy_measure.collect_samples(root, fake_api([], default="gone"), float("inf"), 30)
    assert s.production == "gone"
    assert s.commits == []
    assert "history" in s.unreadable
