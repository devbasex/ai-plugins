"""デプロイとリリースを CD で解く定義（#2623）: 経路・CD の案内・公開の処理・CD の結果・リリース記録・ワークフロー。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parents[2]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import deps  # noqa: E402

deps.require("schema", "md", "yamlio")
import delivery  # noqa: E402
import dist_record  # noqa: E402
from project_lib import merge, model  # noqa: E402
from release_lib import cd_result, record  # noqa: E402

STEPS = SCRIPTS / "release-steps.py"
WT = {"version": 1, "base_branch": "develop", "production_branch": "main"}
CD_PROD = {"target": "prd", "kind": "auto", "trigger": "main へのマージ", "channel": "production", "versioned": False}
CD_BASE = {"target": "stg", "kind": "auto", "trigger": "develop へのマージ", "channel": "base", "versioned": False}
HAND = {"target": "aws", "kind": "manual", "trigger": "cdk deploy", "versioned": False}
PLUGIN_CD = {**CD_PROD, "versioned": True, "publishes_release": True, "result": {"workflow": "release-cd.yml"}}


def decl_of(rows, wt=WT, release=None):
    return delivery.build(wt, {"delivery": rows}, release)


def routes_of(rows, wt=WT):
    return [(r.route, r.stage) for r in delivery.routes(decl_of(rows, wt))]


# ---------------- 経路（受け入れ条件 5）----------------


def test_a_versioned_auto_row_takes_the_same_route_as_an_unversioned_one():
    assert routes_of([{**CD_PROD, "versioned": True}]) == [("merge", "promote")]
    assert routes_of([{**CD_BASE, "versioned": True}]) == [("merge", "merged-by-check")]


def test_a_merge_into_a_versioned_cd_channel_stays_behind_the_release_gate():
    """受け入れ条件 6・I2: 経路を変えても、本番チャネルへのマージは承認の無いまま通らない。"""
    got = delivery.judge_target(decl_of([PLUGIN_CD]), "main")
    assert got.value == delivery.PRODUCTION and got.stops


def test_a_versioned_manual_row_stays_manual():
    assert routes_of([{**HAND, "versioned": True}]) == [("manual", "manual")]


# ---------------- CD の案内（受け入れ条件 9〜14）----------------


@pytest.mark.parametrize("project", [{}, {"delivery": {"unknown": "調べていない"}}])
def test_guidance_has_the_four_contents_when_the_declaration_is_missing(project):
    item = delivery.cd_guidance(delivery.build(WT, project)).as_item()
    assert item["kind"] == "cd-guidance" and "delivery" in item["missing"]
    assert "cd-setup.md" in item["steps_ref"] and '"kind": "auto"' in item["example"] and "manual_reason" in item["manual_reason_how"]


@pytest.mark.parametrize(
    ("rows", "listed"),
    [
        ([HAND], ["aws"]),
        ([CD_PROD, HAND], ["aws"]),
        ([HAND, {**HAND, "target": "gcp", "manual_reason": "人が確かめる"}], ["aws"]),
        ([{**HAND, "manual_reason": "   "}], ["aws"]),
    ],
)
def test_guidance_lists_only_the_manual_rows_without_a_reason(rows, listed):
    assert list(delivery.cd_guidance(decl_of(rows)).rows) == listed


@pytest.mark.parametrize("rows", [[], [CD_PROD], [PLUGIN_CD], [{**HAND, "manual_reason": "人が確かめる"}]])
def test_no_guidance_for_cd_for_no_delivery_or_for_a_recorded_reason(rows):
    assert delivery.cd_guidance(decl_of(rows)) is None


@pytest.mark.parametrize(
    "bad",
    [
        {"manual_reason": 1},
        {"publishes_release": "yes"},
        {"result": []},
        {"result": {"workflow": "a.yml", "command": "true"}},
        {"result": {"workflow": ""}},
        {"result": {"timeout_seconds": 0}},
        {"result": {"timeout_seconds": True}},
    ],
)
def test_a_broken_row_gives_no_guidance_and_stops_as_undetermined(bad):
    d = decl_of([{**CD_PROD, **bad}])
    assert d.problems and delivery.cd_guidance(d) is None
    assert delivery.judge_target(d, "main").value == delivery.UNDETERMINED


def test_a_recorded_reason_goes_to_the_manual_stage_with_the_trigger():
    r = delivery.routes(decl_of([{**HAND, "manual_reason": "人が確かめる"}]))[0]
    assert (r.route, r.stage) == ("manual", "manual") and "cdk deploy" in r.note


# ---------------- 公開の担い手（I11）----------------


@pytest.mark.parametrize(
    ("rows", "want"),
    [
        ([PLUGIN_CD], True),
        ([{**PLUGIN_CD, "publishes_release": False}], False),
        ([{k: v for k, v in PLUGIN_CD.items() if k != "publishes_release"}], False),
        ([{**PLUGIN_CD, "versioned": False}], False),
        ([{**PLUGIN_CD, "production": False}], False),
        ([{**PLUGIN_CD, "channel": "base"}], False),
        ([HAND], False),
    ],
)
def test_publishes_by_cd_needs_the_declaration(rows, want):
    assert delivery.publishes_by_cd(decl_of(rows)) is want


# ---------------- 宣言のモデルと解析（I6）----------------


def test_model_accepts_the_new_fields_and_old_rows():
    assert model.Delivery.model_validate({**PLUGIN_CD, "result": {"command": "x {revision}", "timeout_seconds": 60}})
    assert model.Delivery.model_validate({**HAND, "manual_reason": "人が確かめる"})
    assert model.Delivery.model_validate(HAND)


def test_reanalysis_keeps_the_user_fields_of_the_same_target():
    cur = [{**HAND, "manual_reason": "人が確かめる"}, PLUGIN_CD]
    digest = merge.value_digest(cur)
    new = [HAND, {**PLUGIN_CD, "result": {"workflow": "other.yml"}}, CD_BASE]
    value, _, ours = merge.merge_item("delivery", cur, True, new, "判断した", {"delivery": digest})
    assert ours and value[0]["manual_reason"] == "人が確かめる"
    assert value[1]["result"] == {"workflow": "other.yml"} and value[1]["publishes_release"] is True  # 答えの値が先に効く
    assert value[2] == CD_BASE


# ---------------- 結果の読み取り（受け入れ条件 7・8、I13）----------------


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def fake_runs(monkeypatch, answers):
    """gh run list の答えを順に返す。尽きたら最後の答えを繰り返す。"""
    calls = []

    def gh(args, cwd=None):
        calls.append(args)
        out = answers[min(len(calls) - 1, len(answers) - 1)]
        return (
            subprocess.CompletedProcess(args, *out) if isinstance(out, tuple) else subprocess.CompletedProcess(args, 0, json.dumps(out), "")
        )

    monkeypatch.setattr(cd_result.gh_parts, "gh", gh)
    return calls


def read_with(root, rows, **kw):
    clock = FakeClock()
    return cd_result.read_cd(root, decl_of(rows), "a" * 40, clock=clock, sleep=clock.sleep, **kw)


RUN_OK = [{"status": "completed", "conclusion": "success", "url": "https://x/runs/1"}]
RUN_NG = [{"status": "completed", "conclusion": "failure", "url": "https://x/runs/2"}]
RUN_GOING = [{"status": "in_progress", "conclusion": "", "url": "https://x/runs/3"}]


def test_workflow_waits_until_completed_and_success_is_read_from_the_state(tmp_path, monkeypatch):
    calls = fake_runs(monkeypatch, [[], RUN_GOING, RUN_OK])
    got = read_with(tmp_path, [PLUGIN_CD])
    assert got.value == cd_result.SUCCESS and got.revision == "a" * 40 and got.evidence[0]["url"] == "https://x/runs/1"
    assert len(calls) == 3 and "--commit" in calls[0] and "release-cd.yml" in calls[0]


def test_completed_without_success_is_a_failure(tmp_path, monkeypatch):
    fake_runs(monkeypatch, [RUN_NG])
    got = read_with(tmp_path, [PLUGIN_CD])
    assert got.value == cd_result.FAILURE and "failure" in got.reason


@pytest.mark.parametrize("answer", [RUN_GOING, []])
def test_reaching_the_limit_is_undetermined_and_never_success(tmp_path, monkeypatch, answer):
    fake_runs(monkeypatch, [answer])
    row = {**PLUGIN_CD, "result": {"workflow": "release-cd.yml", "timeout_seconds": 60}}
    got = read_with(tmp_path, [row])
    assert got.value == cd_result.UNDETERMINED and "待ちの上限" in got.reason


@pytest.mark.parametrize("answer", [(1, "", "boom"), (0, "not json", "")])
def test_a_failed_query_is_undetermined_without_waiting(tmp_path, monkeypatch, answer):
    calls = fake_runs(monkeypatch, [answer])
    got = read_with(tmp_path, [PLUGIN_CD])
    assert got.value == cd_result.UNDETERMINED and len(calls) == 1 and "待ちの上限" not in got.reason


def test_a_row_without_a_way_to_read_is_undetermined(tmp_path):
    got = read_with(tmp_path, [CD_PROD])
    assert got.value == cd_result.UNDETERMINED and "result" in got.reason


def test_no_row_reaching_production_is_none(tmp_path):
    assert read_with(tmp_path, [CD_BASE, HAND]).value == cd_result.NONE


@pytest.mark.parametrize(
    ("code", "want"), [(0, cd_result.SUCCESS), (1, cd_result.FAILURE), (2, cd_result.UNDETERMINED), (9, cd_result.UNDETERMINED)]
)
def test_command_reads_by_exit_code_with_the_revision(tmp_path, code, want):
    seen = tmp_path / "seen"
    row = {**CD_PROD, "result": {"command": f"echo {{revision}} > {seen}; exit {code}"}}
    assert read_with(tmp_path, [row]).value == want
    assert seen.read_text().strip() == "a" * 40


def test_command_75_waits(tmp_path):
    flag = tmp_path / "n"
    cmd = f"[ -f {flag} ] && exit 0; touch {flag}; exit 75"
    assert read_with(tmp_path, [{**CD_PROD, "result": {"command": cmd}}]).value == cd_result.SUCCESS


def test_rows_fold_failure_first_then_all_success(tmp_path):
    ok, ng = {"command": "exit 0"}, {"command": "exit 1"}
    rows = lambda *rs: [{**CD_PROD, "target": str(i), "result": r} for i, r in enumerate(rs)]  # noqa: E731
    assert read_with(tmp_path, rows(ok, ok)).value == cd_result.SUCCESS
    assert read_with(tmp_path, rows(ok, ng)).value == cd_result.FAILURE
    assert read_with(tmp_path, [*rows(ok), CD_PROD]).value == cd_result.UNDETERMINED


def test_result_file_round_trip_and_none_and_broken(tmp_path):
    f = tmp_path / "work" / "cd.json"
    cd_result.save_result(f, cd_result.CdResult(cd_result.SUCCESS, "b" * 40, "", [{"url": "u"}]))
    assert cd_result.load_result(f).value == cd_result.SUCCESS
    cd_result.save_result(f, cd_result.CdResult(cd_result.NONE))
    assert cd_result.load_result(f) is None
    assert cd_result.load_result(tmp_path / "missing.json") is None and cd_result.load_result(None) is None
    f.write_text("{")
    assert cd_result.load_result(f).value == cd_result.UNDETERMINED


# ---------------- リリース記録（受け入れ条件 7・8、I14）----------------


@pytest.mark.parametrize(("value", "word"), [(cd_result.SUCCESS, "成功"), (cd_result.FAILURE, "失敗"), (cd_result.UNDETERMINED, "未確定")])
def test_record_line_is_parsed_with_the_revision(value, word):
    cd = cd_result.CdResult(value, "5d6e7f8" + "0" * 33, "理由", [{"url": "https://x/runs/1"}])
    body = record.promote_body_of(12, "develop", "main", "1a2b3c4", "5d6e7f8", [1, 2], cd)
    assert f"CD の結果: {word}（リビジョン 5d6e7f8… https://x/runs/1 理由）" in body
    assert dist_record.parse_record(body)["cd_result"] == word


def test_a_failure_cannot_be_closed_and_a_later_success_can():
    fail = cd_result.CdResult(cd_result.FAILURE, "5" * 40, "落ちた")
    body = record.promote_body_of(12, "develop", "main", None, "5555555", [1], fail)
    rec = dist_record.parse_record(body)
    assert rec["stage"].startswith("反映失敗") and not dist_record.released(rec["stage"])
    again = (
        body + "\n" + record.promote_body_of(12, "develop", "main", None, "5555555", [1], cd_result.CdResult(cd_result.SUCCESS, "5" * 40))
    )
    assert dist_record.released(dist_record.parse_record(again)["stage"])


def test_undetermined_does_not_close_without_the_release_test():
    """未確定の記録は本番の段階だが、閉じる条件はリリース後テストの記録を別に求める（Value 1。#2623 の確認）。"""
    und = cd_result.CdResult(cd_result.UNDETERMINED, "5" * 40, "待ちの上限")
    rec = dist_record.parse_record(record.promote_body_of(12, "develop", "main", None, "5555555", [1], und))
    assert rec["stage"].startswith("本番") and rec["cd_result"] == "未確定" and rec["verify_block"] is None


def test_exists_sees_a_different_cd_result_as_a_new_record():
    und = record.promote_body_of(12, "develop", "main", None, "5555555", [1], cd_result.CdResult(cd_result.UNDETERMINED, "5" * 40))
    assert record.exists(und, "main 5555555", [1], "未確定")
    assert not record.exists(und, "main 5555555", [1], "成功")


def test_no_cd_means_no_line():
    body = record.promote_body_of(12, "develop", "main", None, "5555555", [1], None)
    assert "CD の結果" not in body and dist_record.parse_record(body)["cd_result"] is None


def test_by_cd_does_not_state_the_tag_as_done():
    body = record.body_of(Path("."), "ndf", "1.2.3", "ndf--v1.2.3", None, [1], cd_result.CdResult(cd_result.UNDETERMINED, "5" * 40), True)
    assert "CD が担う" in body and "を出した" not in body


# ---------------- 公開の処理（受け入れ条件 16・18・19、I8〜I10）----------------


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


FAKE_GH = """#!{py}
import json, os, pathlib, sys
log = pathlib.Path(os.environ["GH_LOG"])
state = pathlib.Path(os.environ["GH_STATE"])
args = sys.argv[1:]
with log.open("a") as f:
    f.write(json.dumps(args) + "\\n")
if args[:2] == ["release", "view"]:
    sys.exit(0 if state.exists() else 1)
if args[:2] == ["release", "create"]:
    notes = pathlib.Path(args[args.index("--notes-file") + 1]).read_text()
    state.write_text(json.dumps({{"args": args, "notes": notes}}))
    sys.exit(0)
if args[:2] == ["repo", "view"]:
    print("o/r")
    sys.exit(0)
sys.exit(1)
"""


@pytest.fixture
def pub(tmp_path, monkeypatch):
    """origin つきの作業用のリポジトリと、偽の gh。main の版は 1.2.3。"""
    bare = tmp_path / "o.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    root = tmp_path / "r"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "remote", "add", "origin", str(bare))
    (root / ".ndf").mkdir()
    (root / ".ndf" / "worktree.json").write_text(json.dumps(WT))
    (root / ".ndf" / "supervise.json").write_text(
        json.dumps({"release": {"form": "package-plugin", "plugin": "ndf", "runtimes": ["claude"]}})
    )
    (root / "plugins" / "ndf" / ".claude-plugin").mkdir(parents=True)
    set_version(root, "1.2.3")
    (root / "CHANGELOG.md").write_text("# Changelog\n\n## [ndf 1.2.3] - 2026-01-01\n\n- 変更 A\n\n## [ndf 1.2.2] - 2025-12-01\n\n- 前\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "t")
    git(root, "push", "-q", "origin", "HEAD:main")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH.format(py=sys.executable))
    gh.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GH_LOG": str(tmp_path / "gh.log"),
        "GH_STATE": str(tmp_path / "release.json"),
    }
    return root, env, tmp_path


def set_version(root: Path, ver: str) -> None:
    (root / "plugins" / "ndf" / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "ndf", "version": ver}))


def publish(root, env, rev="HEAD"):
    p = subprocess.run(
        [sys.executable, str(STEPS), "publish", "--root", str(root), "--revision", rev], capture_output=True, text=True, env=env
    )
    return p.returncode, (json.loads(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else {}), p


def results(out):
    return {i["kind"]: i["result"] for i in out["items"]}


def test_publish_makes_the_tag_and_the_latest_release_from_the_revision(pub):
    root, env, tmp = pub
    head = git(root, "rev-parse", "HEAD")
    code, out, _ = publish(root, env, head)
    assert code == 0 and results(out) == {"tag": "pushed", "release": "created"}
    assert git(root, "rev-parse", "ndf--v1.2.3^{commit}") == head
    made = json.loads((tmp / "release.json").read_text())
    assert made["args"][made["args"].index("--title") + 1] == "ndf v1.2.3" and "--latest" in made["args"]
    assert made["notes"].strip() == "- 変更 A"


def test_publish_reads_the_version_of_the_revision_not_of_the_working_tree(pub):
    root, env, _ = pub
    head = git(root, "rev-parse", "HEAD")
    set_version(root, "9.9.9")
    git(root, "commit", "-qam", "next")
    code, out, _ = publish(root, env, head)
    assert code == 0 and out["metrics"]["version"] == "1.2.3"


def test_rerun_fills_the_release_and_changes_nothing_when_complete(pub):
    root, env, tmp = pub
    head = git(root, "rev-parse", "HEAD")
    git(root, "tag", "-a", "ndf--v1.2.3", head, "-m", "x")
    git(root, "push", "-q", "origin", "ndf--v1.2.3")
    code, out, _ = publish(root, env)
    assert code == 0 and results(out) == {"tag": "exists", "release": "created"}
    before = (tmp / "release.json").read_text()
    code, out, _ = publish(root, env)
    assert code == 0 and results(out) == {"tag": "exists", "release": "exists"}
    assert (tmp / "release.json").read_text() == before


def test_a_suffixed_version_makes_nothing_and_fails(pub):
    root, env, tmp = pub
    set_version(root, "1.2.4-dev.1")
    git(root, "commit", "-qam", "dev")
    code, _, p = publish(root, env)
    assert code == 1 and not (tmp / "release.json").exists()
    assert git(root, "ls-remote", "--tags", "origin") == ""


def test_a_local_only_tag_on_another_commit_is_not_pushed(pub):
    root, env, tmp = pub
    old = git(root, "rev-parse", "HEAD")
    git(root, "commit", "-q", "--allow-empty", "-m", "next")
    head = git(root, "rev-parse", "HEAD")
    git(root, "tag", "-a", "ndf--v1.2.3", old, "-m", "x")
    code, _, _ = publish(root, env, head)
    assert code == 1 and not (tmp / "release.json").exists()
    assert git(root, "ls-remote", "--tags", "origin") == ""


def test_a_tag_on_another_commit_fails_and_is_not_moved(pub):
    root, env, tmp = pub
    old = git(root, "rev-parse", "HEAD")
    git(root, "tag", "-a", "ndf--v1.2.3", old, "-m", "x")
    git(root, "push", "-q", "origin", "ndf--v1.2.3")
    (root / "a.txt").write_text("x")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "same version")
    code, _, _ = publish(root, env)
    assert code == 1 and not (tmp / "release.json").exists()
    remote = git(root, "ls-remote", "origin", "refs/tags/ndf--v1.2.3^{}")
    assert remote.split()[0] == old


def test_an_unreadable_version_fails(pub):
    root, env, _ = pub
    set_version(root, "")
    git(root, "commit", "-qam", "none")
    assert publish(root, env)[0] == 1


# ---------------- cd-guidance のコマンド（I3・I4）----------------


def guidance(root, env):
    p = subprocess.run([sys.executable, str(STEPS), "cd-guidance", "--root", str(root)], capture_output=True, text=True, env=env)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def test_guidance_command_keeps_the_exit_code_and_does_not_touch_the_declaration(pub):
    root, env, _ = pub
    f = root / ".ndf" / "project.json"
    code, out = guidance(root, env)
    assert code == 0 and out["items"][0]["kind"] == "cd-guidance" and not f.exists()
    text = json.dumps({"delivery": [{**HAND, "manual_reason": "人が確かめる"}]})
    f.write_text(text)
    code, out = guidance(root, env)
    assert code == 0 and out["items"] == [] and f.read_text() == text


def test_guidance_command_stops_on_a_broken_declaration(pub):
    root, env, _ = pub
    (root / ".ndf" / "project.json").write_text(json.dumps({"delivery": "x"}))
    assert guidance(root, env)[0] == 3


# ---------------- ai-plugins の宣言とワークフロー（受け入れ条件 15・17・20、I12）----------------


def workflow() -> dict:
    from ruamel.yaml import YAML

    return YAML(typ="safe").load((ROOT / ".github" / "workflows" / "release-cd.yml").read_text(encoding="utf-8"))


def test_ai_plugins_declares_the_cd_row_and_the_workflow_exists():
    rows = json.loads((ROOT / ".ndf" / "project.json").read_text(encoding="utf-8"))["delivery"]
    row = next(r for r in rows if r.get("channel") == "production")
    assert row["kind"] == "auto" and row["publishes_release"] is True and row["versioned"] is True
    name = row["result"]["workflow"]
    assert name in row["trigger"] and (ROOT / ".github" / "workflows" / name).is_file()
    assert model.Delivery.model_validate(row)


def test_workflow_runs_only_on_a_push_to_main():
    on = workflow().get("on", workflow().get(True))
    assert list(on) == ["push"] and on["push"]["branches"] == ["main"]


def test_workflow_gives_write_only_to_the_publish_job_and_no_new_secret():
    w = workflow()
    assert w["permissions"] == {"contents": "read"}
    assert list(w["jobs"]) == ["publish"] and w["jobs"]["publish"]["permissions"] == {"contents": "write"}
    text = (ROOT / ".github" / "workflows" / "release-cd.yml").read_text(encoding="utf-8")
    assert "secrets." not in text and "github.token" in text
    assert 'release-steps.py publish --root . --revision "$GITHUB_SHA"' in text
