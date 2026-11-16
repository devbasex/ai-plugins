"""ブランチ戦略の宣言（#3672）の 10 系の単体テスト。5 種の宣言は fixtures/branch_strategy/ の 1 か所にある。"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS / "lib"), str(SCRIPTS)]
import branch_strategy as bs  # noqa: E402
import branch_strategy_cli as bs_cli  # noqa: E402
import branch_strategy_legacy as legacy  # noqa: E402
import release_case  # noqa: E402
from project_lib import branch_strategy_measure as bsm  # noqa: E402
from project_lib import branch_strategy_migrate as bmig  # noqa: E402
from project_lib import branch_strategy_write as bw  # noqa: E402
from release_lib import approval_trail, land  # noqa: E402
from release_lib.strategy_route import route_of  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "branch_strategy"
KINDS = ("web-develop", "web-trunk", "mobile", "docs", "plugin")
DECL = SCRIPTS.parent.parent.parent  # リポジトリの根


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
    git(root, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", "-A")
    git(root, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "init")
    return root


# --- スキーマと組み合わせの規則 --------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_five_kinds_pass_schema(kind):
    assert bs.validate_section(section(kind)) == []


def _broken(kind: str, edit) -> list[str]:
    s = section(kind)
    edit(s)
    return [f["rule"] for f in bs.validate_section(s)]


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
    assert rule in _broken(kind, edit)


def test_trunk_without_version_store_is_the_r4_example():
    """要求の例「trunk なのに版数の置き場が無い」は、release_pr の trunk で落ち、production_pr の trunk（web-trunk・docs）は通る。"""
    s = section("plugin")
    s["release"].update(version_store={"kind": "none"}, tag=None, github_release=False, cd=None)
    assert [f["rule"] for f in bs.validate_section(s)] == ["R4"]
    assert bs.validate_section(section("web-trunk")) == [] and bs.validate_section(section("docs")) == []


def test_merge_base_branch_without_version_store_passes():
    s = section("web-develop")
    s["release"].update(version_store={"kind": "none"}, tag=None, github_release=False)
    assert bs.validate_section(s) == []


def test_schema_has_no_old_names():
    text = bs.SCHEMA.read_text(encoding="utf-8")
    body = json.loads(text)["$defs"]["BranchStrategy"]
    assert body["properties"]["release_strategy"]["enum"] == ["merge_base_branch", "release_branch", "trunk"]
    for old in ('"promotion"', '"whole"', '"selected"'):
        assert old not in json.dumps(body)


def test_presets_carry_no_repo_specific_names():
    presets = bw.load_presets()
    dumped = json.dumps(presets)
    for name in ('"plugin": "ndf"', "ai-plugins", "release-cd.yml", "release-dev.yml"):
        assert name not in dumped


# --- 読む部品 ----------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_load_reads_each_kind(tmp_path, kind):
    root = make_repo(tmp_path, {"project.json": {"version": 1, "branch_strategy": section(kind)}})
    assert bs.load_strategy(root).section == section(kind)


def test_load_stops_without_section(tmp_path):
    root = make_repo(tmp_path, {"project.json": {"version": 1}})
    with pytest.raises(bs.StrategyError) as e:
        bs.load_strategy(root)
    assert "branch-strategy init" in e.value.reason and e.value.exit_code == 3


@pytest.mark.parametrize(
    "files,where",
    [
        ({"worktree.json": {"version": 1, "branch_flow": {"promotion": "whole"}}}, "/branch_flow/promotion"),
        ({"worktree.json": {"version": 1, "base_branch": "develop"}}, "/base_branch"),
        ({"project.json": {"version": 1, "production_merge": {"admin": True}}}, "/production_merge/admin"),
        ({"supervise.json": {"version": 1, "release": {"dev_workflow": "d.yml"}}}, "/release/dev_workflow"),
        ({"release.json": {"version": 1, "version_files": ["a"]}}, "/version_files"),
        ({"worktree.json": {"version": 1, "branch_strategy": {}}}, "/branch_strategy"),
    ],
)
def test_load_stops_on_legacy_key(tmp_path, files, where):
    ndf = {"project.json": {"version": 1, "branch_strategy": section("plugin")}}
    for k, v in files.items():
        ndf[k] = {**ndf.get(k, {}), **v}
    root = make_repo(tmp_path, ndf)
    with pytest.raises(bs.StrategyError) as e:
        bs.load_strategy(root)
    assert where in e.value.reason and "branch-strategy migrate" in e.value.reason


def test_load_stops_on_schema_and_broken_json(tmp_path):
    bad = section("docs")
    bad["merge"]["method"] = "direct"
    root = make_repo(tmp_path, {"project.json": {"version": 1, "branch_strategy": bad}})
    with pytest.raises(bs.StrategyError) as e:
        bs.load_strategy(root)
    assert "R2" in e.value.reason and e.value.exit_code == 1
    (root / ".ndf" / "project.json").write_text("{", encoding="utf-8")
    with pytest.raises(bs.StrategyError) as e:
        bs.load_strategy(root)
    assert e.value.exit_code == 2


def test_load_at_ref_reads_target_branch(tmp_path):
    root = make_repo(tmp_path, {"project.json": {"version": 1, "branch_strategy": section("docs")}})
    git(root, "checkout", "-qb", "other")
    (root / ".ndf" / "project.json").write_text(json.dumps({"version": 1}), encoding="utf-8")
    assert bs.load_at_ref(root, "main").section == section("docs")
    with pytest.raises(bs.StrategyError):
        bs.load_at_ref(root, "nope")


def test_cli_get_and_start_ref(tmp_path, capsys):
    root = make_repo(tmp_path, {"project.json": {"version": 1, "branch_strategy": section("mobile")}})
    assert bs_cli.main(["get", "/branches/base", "--root", str(root)]) == 0
    assert capsys.readouterr().out == "develop\n"
    assert bs_cli.main(["start-ref", "--root", str(root)]) == 0
    assert capsys.readouterr().out == "main\n"
    assert bs_cli.main(["get", "/nope", "--root", str(root)]) == 2
    assert bs_cli.main(["get", "/verify", "--root", str(root)]) == 0
    assert capsys.readouterr().out == "null\n"


def test_snapshot_digest_and_diff():
    s = bs.Strategy(section("plugin"))
    snap = bs.snapshot_section(s, "start")
    reordered = bs.Strategy(json.loads(json.dumps(section("plugin"), sort_keys=True)))
    assert bs.snapshot_section(reordered, "start")["digest"] == snap["digest"]
    assert bs.snapshot_diff(snap, s) == []
    changed = section("plugin")
    changed["merge"]["method"] = "strict"
    assert bs.snapshot_diff(snap, bs.Strategy(changed)) == ["/merge/method"]


# --- 代表の 5 種の振る舞い（受け入れ条件 5・19） --------------------------------------------------

EXPECT = {
    # 種類: (スプリントの PR の宛先, 版上げの PR の起点（None は無し）, 宛先, 承認の回数, 主体, 方式)
    "web-develop": ("develop", "develop", "main", 1, "ndf", "direct"),
    "web-trunk": ("main", None, "main", 1, "ndf", "merge-queue"),
    "mobile": ("develop", "main", "main", 1, "ndf", "direct"),
    "docs": ("main", None, "main", 1, "ndf", "strict"),
    "plugin": ("main", "main", "main", 1, "ndf", "merge-queue"),
}


@pytest.mark.parametrize("kind", KINDS)
def test_five_kinds_routes(kind):
    s = bs.Strategy(section(kind))
    sprint_to, source, target, approvals, actor, method = EXPECT[kind]
    assert s.pr_target("sprint") == sprint_to
    assert s.pr_target("release") == "main"
    r = route_of(s)
    assert (r.source if r.release_pr else None) == source and r.target == target
    assert r.sync == (kind in ("web-develop", "mobile"))
    assert (s.section["merge"]["actor"], s.section["merge"]["method"]) == (actor, method)
    # 人へ問う承認は正式版 1 本につき 1 回（リリース承認ゲートだけ。ruleset の承認を求めない）
    assert s.section["approval"]["via"] == "gate" and approvals == 1


def test_sprint_start_and_doc_target():
    assert bs.Strategy(section("web-develop")).sprint_start_ref() == "develop"
    assert bs.Strategy(section("mobile")).sprint_start_ref() == "main"
    assert bs.Strategy(section("mobile")).pr_target("doc", "sprint/x") == "sprint/x"
    with pytest.raises(ValueError):
        bs.Strategy(section("mobile")).pr_target("doc")
    with pytest.raises(ValueError):
        bs.Strategy(section("docs")).pr_target("other")


def test_route_without_version_store_is_the_pr_itself():
    s = section("web-develop")
    s["release"].update(version_store={"kind": "none"}, tag=None, github_release=False)
    r = route_of(bs.Strategy(s))
    assert r.release_pr and not r.bump and (r.source, r.target) == ("develop", "main")


# --- 旧い宣言の検査 ----------------------------------------------------------------------------


def test_load_moves_only_section_rows():
    moves = legacy.load_moves()
    olds = {(m.file, m.old) for m in moves}
    assert ("worktree", "/branch_flow/promotion") in olds and ("worktree", "/branch_flow/release_strategy") in olds
    assert all(m.new is None or m.new.startswith("/branch_strategy") for m in moves)


def test_load_moves_rejects_broken_table(tmp_path):
    p = tmp_path / "t.json"
    p.write_text("{}", encoding="utf-8")
    with pytest.raises(legacy.LegacyTableError):
        legacy.load_moves(p)


def test_find_legacy_on_ai_plugins_like_files():
    files = {
        "worktree": {"base_branch": "develop", "production_branch": "main", "branch_flow": {"promotion": "whole", "start": "base"}},
        "project": {"production_merge": {"admin": True}, "delivery": []},
        "supervise": {"release": {"form": "package-plugin", "dev_workflow": "d.yml"}},
    }
    found = {(f.file, f.pointer) for f in legacy.find_legacy(files, legacy.load_moves())}
    assert ("worktree.json", "/branch_flow/promotion") in found and ("project.json", "/delivery") in found
    assert ("worktree.json", "/branch_flow") not in found  # 子の位置だけを出す
    assert legacy.legacy_reason(legacy.find_legacy(files, legacy.load_moves()), "11").endswith(
        "`ndf project branch-strategy migrate` で移してください"
    )


# --- 作る・書く・消す（受け入れ条件 8・12） -----------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_init_values_new_repo_has_no_cd(kind):
    sec, q = bw.init_values(kind, {"production": "main", "merge_queue": True}, bw.load_presets())
    assert q == [] and sec["release"]["cd"] is None and sec["release"]["tag"] is None and sec["release"]["github_release"] is False
    assert bs.validate_section(sec) == []


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
    assert "branch_strategy" not in json.loads((root / ".ndf" / "project.json").read_text())
    bw.commit_plan(root, plan, plan.digest)
    assert bs.load_strategy(root).section == section("docs")
    again = bw.plan_write(root, section("docs"))
    assert again.diff == []


def test_remove_section(tmp_path):
    root = make_repo(tmp_path, {"project.json": {"version": 1, "branch_strategy": section("docs")}})
    plan = bw.plan_remove(root)
    bw.commit_plan(root, plan, plan.digest)
    assert "branch_strategy" not in json.loads((root / ".ndf" / "project.json").read_text())
    assert bw.plan_remove(root).diff == []


def test_lock_is_shared(tmp_path):
    import locks

    root = make_repo(tmp_path, {"project.json": {"version": 1}})
    plan = bw.plan_write(root, section("docs"))
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    assert locks.lock_path(common / bw.LOCK_TARGET).name == "ndf-branch-strategy.lock"
    held = locks.try_exclusive(common / bw.LOCK_TARGET)
    try:
        import limits

        orig = limits.seconds
        limits.seconds = lambda name: 0.1
        with pytest.raises(bw.WriteError) as e:
            bw.commit_plan(root, plan, plan.digest)
        assert "別の実行" in e.value.reason
    finally:
        limits.seconds = orig
        held.release()


# --- 移行（受け入れ条件 20） --------------------------------------------------------------------

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
            # ai-plugins の宣言と同じ形（スプリントの PR が cross-review を掛ける。掛けないとレビューの単位の規則に落ちる）
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
    plan, q = bmig.plan_migration(root, legacy.load_moves())
    assert q == [] and not plan.failed
    bw.commit_plan(root, plan, plan.digest)
    s = bs.load_strategy(root).section
    assert s["release_strategy"] == "merge_base_branch" and s["branches"] == {"production": "main", "base": "develop"}
    assert s["merge"]["admin"] is True and s["merge"]["method"] == "direct"
    assert s["release"]["cd"] == {"workflow": "release-cd.yml"} and s["release"]["tag"] == "{plugin}--v{version}"
    assert {"name": "dev", "ref": "develop", "workflow": "release-dev.yml"} in s["channels"]
    wt = json.loads((root / ".ndf" / "worktree.json").read_text())
    assert wt == {"version": 1, "guard": {"allow_paths": [".ndf/"]}}
    assert json.loads((root / ".ndf" / "supervise.json").read_text()) == {"version": 1, "test": {"command": "x"}}
    plan2, _ = bmig.plan_migration(root, legacy.load_moves())
    assert plan2.diff == []


def test_migrate_resumes_after_partial_write(tmp_path):
    old = copy.deepcopy(AI_PLUGINS_OLD)
    root = make_repo(tmp_path, old)
    plan, _ = bmig.plan_migration(root, legacy.load_moves())
    (root / ".ndf" / "project.json").write_text(json.dumps(plan.files["project.json"]), encoding="utf-8")
    with pytest.raises(bs.StrategyError):
        bs.load_strategy(root)  # セクションと旧いキーが両方ある（I1）
    again, _ = bmig.plan_migration(root, legacy.load_moves())
    bw.commit_plan(root, again, again.digest)
    first = plan.files["project.json"]["branch_strategy"]
    assert bs.load_strategy(root).section == first  # 先に消えた project.json の値（迂回・delivery）も落とさない


def test_migrate_unknown_value_asks_and_rule_failure_stops(tmp_path):
    root = make_repo(tmp_path, {"worktree.json": {"version": 1, "branch_flow": {"promotion": "odd"}}})
    plan, q = bmig.plan_migration(root, legacy.load_moves())
    assert q and q[0]["pointer"] == "/release_strategy"
    root2 = make_repo(tmp_path / "b", {"worktree.json": {"version": 1, "base_branch": "main", "production_branch": "main"}})
    plan2, q2 = bmig.plan_migration(root2, legacy.load_moves())
    assert q2 == [] and any(f["rule"] == "R1" for f in plan2.failed)


def test_migrate_remove_row_only(tmp_path):
    root = make_repo(
        tmp_path, {"project.json": {"version": 1, "branch_strategy": section("docs")}, "worktree.json": {"version": 1, "branch_flow": {}}}
    )
    plan, _ = bmig.plan_migration(root, legacy.load_moves())
    bw.commit_plan(root, plan, plan.digest)
    assert bs.load_strategy(root).section == section("docs")


# --- 測る（受け入れ条件 9・10・11） ------------------------------------------------------------


def fake_api(prs, tags=(), releases=(), rules=None, rulesets=None, default="main"):
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
        if path.endswith("/rulesets"):
            return rulesets or [], ""
        if "/rulesets/" in path:
            return {"current_user_can_bypass": "never"}, ""
        return None, "unknown"

    return api


def history_repo(tmp_path, merges: int, extra: dict | None = None) -> Path:
    root = make_repo(tmp_path, {"project.json": {"version": 1}})
    for name, text in (extra or {}).items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    c = ["-c", "user.email=t@example.com", "-c", "user.name=t"]
    git(root, *c, "add", "-A")
    git(root, *c, "commit", "-q", "--allow-empty", "-m", "files")
    for i in range(merges):
        git(root, "checkout", "-qb", f"sprint/{i}")
        git(root, *c, "commit", "-q", "--allow-empty", "-m", f"work {i}")
        git(root, "checkout", "-q", "main")
        git(root, *c, "merge", "-q", "--no-ff", f"sprint/{i}", "-m", f"Merge pull request #{i + 1}")
    return root


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
    res = bsm.measure_strategy(root, api)
    assert res["status"] == "ok", res["questions"]
    v = res["values"]
    assert res["kind"] == "web-develop" and v["release_strategy"] == "merge_base_branch" and v["branches"]["base"] == "develop"
    assert v["merge"]["method"] == "direct" and v["release"]["tag"] == "v{version}" and v["release"]["cd"] == {"workflow": "release.yml"}
    refs = {(e["pointer"], e["source"]) for e in res["evidence"]}
    assert ("/release_strategy", "pr") in refs and ("/release/tag", "tag") in refs and ("/release/form", "preset") in refs
    assert bs.validate_section(v) == []


def test_measure_trunk_without_tags(tmp_path):
    root = history_repo(tmp_path, 4)
    api = fake_api([(i, "main", f"sprint/{i}") for i in range(1, 5)])
    res = bsm.measure_strategy(root, api)
    assert res["status"] == "ok", res["questions"]
    v = res["values"]
    assert res["kind"] == "docs" and v["release_strategy"] == "trunk" and v["merge"]["method"] == "strict"
    assert v["release"]["tag"] is None and v["release"]["github_release"] is False
    assert any(e["pointer"] == "/release/tag" and e["ref"] == "0 件" for e in res["evidence"])


def test_measure_doc_conflict_and_insufficient(tmp_path):
    root = history_repo(tmp_path, 4, {"CONTRIBUTING.md": "`--base develop` で出す\n"})
    res = bsm.measure_strategy(root, fake_api([(i, "main", f"sprint/{i}") for i in range(1, 5)]))
    assert res["status"] == "ask" and any(q["why"] == "conflict" and q["pointer"] == "/branches/base" for q in res["questions"])
    root2 = history_repo(tmp_path / "b", 0)
    res2 = bsm.measure_strategy(root2, fake_api([]))
    assert res2["status"] == "ask" and {q["why"] for q in res2["questions"]} >= {"insufficient"}


def test_measure_tags_without_cd_asks_r10(tmp_path):
    root = history_repo(tmp_path, 4)
    res = bsm.measure_strategy(
        root,
        fake_api(
            [(i, "main", f"sprint/{i}") for i in range(1, 5)], tags=["v1.0.0", "v1.0.1", "v1.0.2"], releases=["v1.0.0", "v1.0.1", "v1.0.2"]
        ),
    )
    assert res["status"] == "ask" and res["questions"][0]["pointer"] == "/release/cd"


# --- collect_samples の現状固定（I-023） ------------------------------------------------------


def test_collect_samples_reads_api_history_and_local_facts(tmp_path):
    root = history_repo(
        tmp_path,
        2,
        {
            "package.json": '{"version": "1.0.0"}',
            ".github/workflows/release.yml": "run: gh release create v1\n",
            "plugins/p/.claude-plugin/plugin.json": '{"name": "p"}',
            "plugins/p/.codex-plugin/plugin.json": '{"name": "p"}',
        },
    )
    seen = []
    base = fake_api([(1, "develop", "a"), (2, "main", "develop"), (3, "develop", "c")], tags=["v1", "v2"], releases=["v1"])

    def api(path):
        seen.append(path)
        if "/pulls?" in path:
            got, why = base(path)
            return got + [{"number": 99, "merged_at": None, "base": {"ref": "x"}, "head": {"ref": "y"}}, "junk"], why
        return base(path)

    s = bsm.collect_samples(root, api, float("inf"), 2)
    assert s.production == "main"
    assert s.prs == [{"number": 1, "base": "develop", "head": "a"}, {"number": 2, "base": "main", "head": "develop"}]
    assert s.tags == ["v1", "v2"] and s.releases == ["v1"]
    assert s.rules == release_case.RuleFacts(False, 0, False, False, None)
    assert [c["subject"] for c in s.commits] == ["Merge pull request #2", "Merge pull request #1"]
    assert all(c["parents"] == 2 and c["landed"] is None for c in s.commits)
    assert list(s.workflows) == ["release.yml"] and s.version_files == {"package.json": "/version"}
    assert s.plugins == {"p": ["claude", "codex"]}
    assert s.unreadable == [] and s.warnings == []
    assert seen == [
        "repos/{owner}/{repo}",
        "repos/{owner}/{repo}/pulls?state=closed&per_page=2",
        "repos/{owner}/{repo}/tags?per_page=2",
        "repos/{owner}/{repo}/releases?per_page=2",
        "repos/{owner}/{repo}/rules/branches/main",
    ]


def test_collect_samples_past_deadline_skips_api_lists_only(tmp_path):
    root = history_repo(tmp_path, 1)
    seen = []
    base = fake_api([(1, "main", "a")], tags=["v1"])

    def api(path):
        seen.append(path)
        return base(path)

    s = bsm.collect_samples(root, api, float("-inf"), 5)
    assert s.prs == [] and s.tags == [] and s.releases == []
    assert s.warnings == ["時間切れで打ち切った: prs", "時間切れで打ち切った: tags", "時間切れで打ち切った: releases"]
    assert s.production == "main" and s.rules is not None
    assert [c["subject"] for c in s.commits] == ["Merge pull request #1", "files", "init"]
    assert seen == ["repos/{owner}/{repo}", "repos/{owner}/{repo}/rules/branches/main"]


@pytest.mark.parametrize("now, cut", [(9.0, []), (10.0, []), (11.0, ["prs", "tags", "releases"])])
def test_collect_samples_cuts_only_after_the_deadline(tmp_path, monkeypatch, now, cut):
    """締切の比較は「超えた」（>）。締切と等しい時刻では打ち切らない。打ち切っても ruleset とローカルの事実は集める。"""
    from types import SimpleNamespace

    root = history_repo(tmp_path, 1)
    seen = []
    base = fake_api([(1, "main", "a")], tags=["v1"])

    def api(path):
        seen.append(path)
        return base(path)

    monkeypatch.setattr(bsm, "time", SimpleNamespace(monotonic=lambda: now))
    s = bsm.collect_samples(root, api, 10.0, 5)
    assert s.warnings == [f"時間切れで打ち切った: {k}" for k in cut]
    assert len([p for p in seen if "per_page" in p]) == 3 - len(cut)
    assert seen[0] == "repos/{owner}/{repo}" and seen[-1] == "repos/{owner}/{repo}/rules/branches/main"


def test_collect_samples_unreadable_default_branch_and_lists(tmp_path):
    root = history_repo(tmp_path, 0)
    s = bsm.collect_samples(root, lambda path: (None, "unknown"), float("inf"), 5)
    assert s.production is None
    assert s.unreadable == ["default_branch", "prs", "tags", "releases"]
    assert s.commits == [] and s.rules is None


def test_collect_samples_rulesets_and_unreadable_ruleset(tmp_path):
    root = history_repo(tmp_path, 0)
    rules = [
        {"type": "merge_queue", "ruleset_id": 7},
        {"type": "pull_request", "ruleset_id": 3, "parameters": {"required_approving_review_count": 1}},
    ]
    seen = []
    base = fake_api([], rules=rules)

    def api(path):
        seen.append(path)
        return base(path)

    s = bsm.collect_samples(root, api, float("inf"), 5)
    assert seen[-2:] == ["repos/{owner}/{repo}/rulesets/3", "repos/{owner}/{repo}/rulesets/7"]
    assert s.rules == release_case.rule_facts_of(rules, ["never", "never"])
    assert s.rules.present and s.rules.queue

    def broken(path):
        if "/rules/branches/" in path:
            return {"message": "x"}, "error"
        return base(path)

    s2 = bsm.collect_samples(root, broken, float("inf"), 5)
    assert s2.rules is None and s2.unreadable == ["ruleset"]


def test_collect_samples_missing_history_and_landed_trailer(tmp_path):
    root = history_repo(tmp_path, 0)
    s = bsm.collect_samples(root, fake_api([], default="nothere"), float("inf"), 5)
    assert s.production == "nothere" and "history" in s.unreadable and s.commits == []

    tip = git(root, "rev-parse", "HEAD").strip()
    c = ["-c", "user.email=t@example.com", "-c", "user.name=t"]
    git(root, *c, "commit", "-q", "--allow-empty", "-m", f"Land\n\nNDF-Landed-PR: 42\nNDF-Tip: {tip}")
    s2 = bsm.collect_samples(root, fake_api([(42, "main", "x")]), float("inf"), 5)
    assert s2.commits[0]["landed"] == "42" and s2.prs == [{"number": 42, "base": "main", "head": "x"}]
    s3 = bsm.collect_samples(root, fake_api([]), float("inf"), 5)
    assert s3.prs == [{"number": 42, "base": "main", "head": None, "landed": s3.commits[0]["sha"]}]


def test_count_key_thresholds():
    rule = {"min_samples": 3, "min_ratio": 0.9}
    assert bsm.count_key("/x", [("a", "1"), ("a", "2")], rule).why == "insufficient"
    assert bsm.count_key("/x", [("a", str(i)) for i in range(3)], rule).value == "a"
    nine = [("a", str(i)) for i in range(9)] + [("b", "9")]
    assert bsm.count_key("/x", nine, rule).value == "a"
    eight = [("a", str(i)) for i in range(8)] + [("b", "8"), ("b", "9")]
    m = bsm.count_key("/x", eight, rule)
    assert m.why == "conflict" and m.candidates == ["a", "b"]


@pytest.mark.parametrize(
    "facts,rs,want",
    [
        (release_case.RuleFacts(True, 0, True, False, None), "trunk", "merge-queue"),
        (release_case.RuleFacts(True, 0, True, False, None), "merge_base_branch", "merge-queue"),
        (release_case.RuleFacts(True, 0, False, True, None), "trunk", "strict"),
        (release_case.RuleFacts(True, 0, False, True, None), "merge_base_branch", "strict"),
        (release_case.RuleFacts(True, 0, False, False, None), "trunk", "conflict"),
        (release_case.RuleFacts(True, 0, False, False, None), "merge_base_branch", "direct"),
        (release_case.RuleFacts(False, 0, False, False, None), "trunk", "strict"),
        (release_case.RuleFacts(False, 0, False, False, None), "merge_base_branch", "direct"),
        (None, "trunk", "insufficient"),
    ],
)
def test_decide_merge_method(facts, rs, want):
    m = bsm.decide_merge_method(facts, rs)
    assert (m.why or m.value) == want


def test_classify_repo(tmp_path):
    root = make_repo(tmp_path, {"project.json": {"version": 1}})
    assert bsm.classify_repo(root, fake_api([]))[0] == "new"
    assert bsm.classify_repo(root, fake_api([], tags=["v1"]))[0] == "existing"
    assert bsm.classify_repo(root, lambda p: (None, "boom"))[0] == "existing"
    root2 = history_repo(tmp_path / "b", 0)
    assert bsm.classify_repo(root2, fake_api([]))[0] == "existing"


def test_pick_kind_ambiguous_and_queue_matches_strict():
    presets = bw.load_presets()
    m = {
        "/release_strategy": bsm.Measured("trunk"),
        "/sprint/start": bsm.Measured("production"),
        "/release/version_store": bsm.Measured({"kind": "plugin"}),
        "/merge/commit": bsm.Measured("merge"),
        "/merge/method": bsm.Measured("strict"),
    }
    assert bsm.pick_kind(m, presets) == ("plugin", [])
    m["/release/version_store"] = bsm.Measured({"kind": "json", "path": "p", "pointer": "/v"})
    kind, cands = bsm.pick_kind(m, presets)
    assert kind is None and cands


# --- 承認とマージ（受け入れ条件 17・18・I10） ---------------------------------------------------


def facts(**kw):
    base = {"present": True, "approvals": 0, "queue": False, "up_to_date": False, "bypass": None}
    return release_case.RuleFacts(**{**base, **kw})


def test_ruleset_mismatch():
    plugin = bs.Strategy(section("plugin"))
    assert land.ruleset_mismatch(plugin, facts(queue=True)) == []
    assert land.ruleset_mismatch(plugin, facts(queue=True, approvals=1))[0]["key"] == "approval.via"
    assert land.ruleset_mismatch(plugin, facts(queue=False))[0]["key"] == "merge.method"
    assert land.ruleset_mismatch(plugin, facts(present=False))[0]["key"] == "merge.method"
    docs = bs.Strategy(section("docs"))
    assert land.ruleset_mismatch(docs, facts(present=False)) == []
    admin = section("web-develop")
    admin["merge"]["admin"] = True
    assert land.ruleset_mismatch(bs.Strategy(admin), facts(approvals=1, bypass="pull_requests_only")) == []
    assert land.ruleset_mismatch(bs.Strategy(admin), facts(approvals=1, bypass="exempt"))[0]["key"] == "merge.admin"


def test_check_production_pr():
    plugin, docs, dev = (bs.Strategy(section(k)) for k in ("plugin", "docs", "web-develop"))
    prod = {"tip": "t" * 40, "version": "1.0.0"}
    assert land.check_production_pr(plugin, {"bump": True, "cut_version": "1.0.0", "number": 1}, prod, []) is None
    assert land.check_production_pr(plugin, {"bump": True, "cut_version": "0.9.0", "number": 1}, prod, [])
    assert land.check_production_pr(plugin, {"bump": True, "cut_version": "1.0.0", "number": 1}, prod, [{"number": 2, "bump": True}])
    assert land.check_production_pr(plugin, {"bump": False}, prod, []) is None
    assert land.check_production_pr(docs, {"contains_tip": False}, prod, [])
    assert land.check_production_pr(docs, {"contains_tip": True}, prod, []) is None
    assert land.check_production_pr(dev, {}, prod, []) is None


class FakePort:
    def __init__(self, pull, rules, prod=None, queue=(), record=None):
        self._pull, self._rules, self._prod, self._queue, self._record = (
            pull,
            rules,
            prod or {"tip": "t" * 40, "version": "1.0.0"},
            list(queue),
            record,
        )
        self.calls: list = []
        self.ref_ok = True

    def pull(self, n):
        return self._pull

    def production(self, b):
        return self._prod

    def queue(self, b):
        return self._queue

    def rule_facts(self, b):
        return self._rules

    def merge(self, n, sha, commit, admin):
        self.calls.append(("merge", sha, commit, admin))
        return True, ""

    def enqueue(self, n, head, jump):
        self.calls.append(("enqueue", head, jump))
        return True, ""

    def create_commit(self, message, tree_of, parents):
        self.calls.append(("commit", message, parents))
        return "c" * 40

    def update_ref(self, b, sha):
        self.calls.append(("ref", sha))
        return self.ref_ok

    def close(self, n, comment):
        self.calls.append(("close", n))
        return True

    def comment(self, n, body):
        self.calls.append(("comment", body))
        return True

    def landed_record(self, b, n, since):
        return self._record

    def has_human_record(self, n):
        return any(c[0] == "comment" for c in self.calls)


H1, H2 = "1" * 40, "2" * 40


def test_land_stops_when_head_moved_3661():
    port = FakePort({"head": H2, "base": "main", "bump": True, "cut_version": "1.0.0"}, facts(queue=True))
    out = land.land_approved(port, bs.Strategy(section("plugin")), 5, land.Approval(H1))
    assert out["result"] == "stopped" and "承認を取り直して" in out["reason"] and port.calls == []


def test_land_queue_jumps_release_pr():
    port = FakePort({"head": H1, "base": "main", "bump": True, "cut_version": "1.0.0"}, facts(queue=True))
    out = land.land_approved(port, bs.Strategy(section("plugin")), 5, land.Approval(H1))
    assert out["result"] == "queued" and port.calls == [("enqueue", H1, True)]


def test_land_direct_and_strict():
    port = FakePort({"head": H1, "base": "main"}, facts())
    assert land.land_approved(port, bs.Strategy(section("web-develop")), 5, land.Approval(H1))["result"] == "merged"
    assert port.calls == [("merge", H1, "merge", False)]
    port2 = FakePort({"head": H1, "base": "main", "contains_tip": False}, facts(present=False))
    out = land.land_approved(port2, bs.Strategy(section("docs")), 6, land.Approval(H1))
    assert out["result"] == "stopped" and port2.calls == []
    port3 = FakePort({"head": H1, "base": "main", "contains_tip": True}, facts(present=False))
    assert land.land_approved(port3, bs.Strategy(section("docs")), 6, land.Approval(H1))["result"] == "merged"
    kinds = [c[0] for c in port3.calls]
    assert kinds == ["commit", "ref"] and "NDF-Approved-Head: " + H1 in port3.calls[0][1]


def test_land_ruleset_mismatch_and_human():
    port = FakePort({"head": H1, "base": "main", "bump": True, "cut_version": "1.0.0"}, facts(queue=True, approvals=1))
    assert land.land_approved(port, bs.Strategy(section("plugin")), 5, land.Approval(H1))["result"] == "stopped"
    merged = FakePort({"head": H1, "merged": True}, facts())
    s = bs.Strategy(section("plugin"))
    assert land.land_approved(merged, s, 5, land.Approval(H1))["result"] == "human"
    land.land_approved(merged, s, 5, land.Approval(H1))
    assert [c[0] for c in merged.calls] == ["comment"]  # 記録は 1 回だけ


def test_fast_forward_refused_when_production_moved():
    port = FakePort({}, facts(present=False))
    port.ref_ok = False
    out = land.fast_forward_land(port, "main", 7, "t" * 40, H1, "squash")
    assert out["result"] == "stopped" and ("close", 7) not in port.calls


def test_land_resumes_from_record_only_when_heads_agree():
    rec = {"head": H1, "tree_ok": True, "first_parent_is_tip": True, "commit": "c" * 40}
    port = FakePort({"head": H1, "base": "main", "state": "open", "contains_tip": False}, facts(present=False), record=rec)
    assert land.land_approved(port, bs.Strategy(section("docs")), 8, land.Approval(H1))["result"] == "merged"
    port2 = FakePort({"head": H2, "base": "main", "state": "open", "contains_tip": False}, facts(present=False), record=rec)
    assert land.land_approved(port2, bs.Strategy(section("docs")), 8, land.Approval(H2))["result"] == "stopped"


def test_trail_writer_one_per_case_3671():
    s = section("plugin")
    assert approval_trail.trail_writer(bs.Strategy(s), False) == "answer"
    assert approval_trail.trail_writer(bs.Strategy(s), True) == "land"
    s["merge"]["actor"] = "human"
    assert approval_trail.trail_writer(bs.Strategy(s), False) == "land"
    s["approval"]["via"] = "review"
    assert approval_trail.trail_writer(bs.Strategy(s), False) == "github"


# --- CLI ---------------------------------------------------------------------------------------


def run_decl(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "project-decl.py"), "branch-strategy", *args, "--root", str(root)], capture_output=True, text=True
    )


def test_cli_migrate_then_digest(tmp_path):
    root = make_repo(tmp_path, copy.deepcopy(AI_PLUGINS_OLD))
    shown = run_decl(root, "migrate")
    assert shown.returncode == 0, shown.stderr
    out = json.loads(shown.stdout.strip().splitlines()[-1])
    assert out["status"] == "ok" and out["diff"]
    assert "branch_strategy" not in json.loads((root / ".ndf" / "project.json").read_text())
    done = run_decl(root, "migrate", "--digest", out["digest"])
    assert done.returncode == 0, done.stderr
    assert bs.load_strategy(root).section["release_strategy"] == "merge_base_branch"


def test_cli_bad_verb_is_2(tmp_path):
    assert run_decl(tmp_path, "nope").returncode == 2


def test_sprint_pr_waits_while_release_pr_is_approved_decision_18():
    """利用者の判断 3（固定する）: 版上げの PR の承認からキューへの投入まで、スプリントの PR をキューへ入れない。"""
    plugin = bs.Strategy(section("plugin"))
    prod = {"tip": "t" * 40, "version": "1.0.0"}
    held = [{"number": 9, "approved_release": True}]
    assert "#9" in land.check_production_pr(plugin, {"bump": False, "number": 3}, prod, held)
    assert land.check_production_pr(plugin, {"bump": True, "cut_version": "1.0.0", "number": 9}, prod, held) is None
    port = FakePort({"head": H1, "base": "main", "bump": False}, facts(queue=True), queue=held)
    out = land.land_approved(port, plugin, 3, land.Approval(H1))
    assert out["result"] == "stopped" and port.calls == []
