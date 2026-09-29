"""supervise.py の `pace: auto`（#1370）: 使ってよい条件・ステージの並び・承認ゲート 1・2 の MVV 判定のステップ・
handoff（記録の後の失敗を承認ゲートへ落とす）・resume と、sprint-state.py gate --withdraw。

実機の claude と gh は呼ばない（計画の形と、run のステップだけの計画を流して見る）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SUPERVISE = SCRIPTS / "supervise.py"
SPRINT_STATE = SCRIPTS / "sprint-state.py"
REPO = SCRIPTS.parents[2]
PY = sys.executable

sys.path.insert(0, str(SCRIPTS))
from supervise_lib import queue  # noqa: E402

AUTO = {"enabled": True, "modes": ["light", "standard", "legacy-refactor"], "verify": "true"}
STAGES = ["設計", "関門 1", "スプリントブランチ", "実装", "検査", "開発版", "本番"]


def load(path) -> dict:
    return json.loads(Path(path).read_text())


def steps_of(plan: dict) -> dict:
    return {s["id"]: s for s in plan["steps"]}


def sprint_state(tmp_path: Path, approve: bool = True) -> Path:
    mvv = tmp_path / "mvv-src.md"
    mvv.write_text("## Mission\n速く\n## Vision\n回る\n## Value\n実測\n")
    state = tmp_path / "state" / "sprint-state.json"
    subprocess.run(
        [PY, str(SPRINT_STATE), "init", str(state), "--name", "m", "--pace", "auto", "--mvv", str(mvv), "--root", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    if approve:
        subprocess.run([PY, str(SPRINT_STATE), "gate", str(state), "MVV", "--what", "MVV を承認"], check=True, capture_output=True)
    return state


def make_repo(tmp_path: Path, pace: dict | None, prod: str = "main", form: bool = True) -> Path:
    """宣言だけを持つリポジトリ。pace は pace.json の中身（None なら置かない）。"""
    repo = tmp_path / "repo"
    (repo / ".ndf").mkdir(parents=True)
    (repo / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "base_branch": "develop", "production_branch": prod}))
    sup = load(REPO / ".ndf" / "supervise.json")
    if not form:
        sup.pop("release", None)
    (repo / ".ndf" / "supervise.json").write_text(json.dumps(sup))
    if pace is not None:
        (repo / ".ndf" / "pace.json").write_text(json.dumps({"version": 1, **pace}))
    return repo


def new_sprint(tmp_path, repo, state, *extra, pace="auto", design=True):
    out = tmp_path / "m"
    args = ["new", "sprint", "--name", "m27", "--worktree", str(repo), "--issue", "11", "12"]
    args += ["--design", "11"] if design else []
    args += ["--version", "10.18.0-dev.1", "--pace", pace, "--state", str(state), "--out", str(out), *extra]
    p = subprocess.run([PY, str(SUPERVISE), *args], capture_output=True, text=True, cwd=repo)
    return p, out


# ---------- 使ってよい条件（AC1・I1・I9） ----------


def test_auto_sprint_writes_the_normal_order_with_mvv_gates(tmp_path):
    repo = make_repo(tmp_path, {"auto": AUTO})
    p, out = new_sprint(tmp_path, repo, sprint_state(tmp_path))
    assert p.returncode == 0, p.stdout + p.stderr
    manifest = load(out / "sprint.json")
    assert manifest["進め方"] == "auto" and manifest["ブランチ"] == "sprint/m27" and manifest["状態"].endswith("sprint-state.json")
    assert [w["name"] for w in manifest["ステージ"]] == STAGES
    waves = {w["name"]: w for w in manifest["ステージ"]}
    # 設計の queue が後ろのステージを 1 ステージずつ --then で流す
    cmd = waves["設計"]["command"]
    later = [waves[n]["plans"][0] for n in STAGES[2:]]
    assert [cmd.index("--then " + p) for p in later] == sorted(cmd.index("--then " + p) for p in later)
    assert all(waves[n]["then_of"] == "設計" and "command" not in waves[n] for n in STAGES[2:])
    # resume はそのステージから最後までを流す
    for i, n in enumerate(STAGES[2:]):
        resume = waves[n]["resume"]
        assert resume.split(" --then ")[0].endswith(f"{waves[n]['plans'][-1]} --max 3")
        assert resume.count(" --then ") == len(STAGES[2:]) - 1 - i
    # 実装はスプリントブランチへ入り、検査はスプリントの develop 宛 PR を 1 回出す
    impl = load(waves["実装"]["plans"][0])
    assert impl["起点"] == "origin/sprint/m27" and impl["進め方"] == "auto"
    assert next(s for s in impl["steps"] if s["type"] == "pr")["base"] == "sprint/m27"
    check = load(waves["検査"]["plans"][0])
    assert steps_of(check)["pr"]["base"] == "develop" and "実行の条件" not in check
    # 検査のトリガー（check-trigger.py eval / prepare）はどのプランにも無い。検査の記録（record）は書く（#1317）
    for path in [p for w in manifest["ステージ"] for p in w.get("plans", [])]:
        text = Path(path).read_text()
        assert "check-trigger.py eval" not in text and "check-trigger.py prepare" not in text
    # ゲート 1: 用語チェックの後に mvv、0 なら approve → merge、落ちたら handoff
    ds = steps_of(load(waves["設計"]["plans"][0]))
    assert ds["push-glossary"]["next"] == "mvv" and ds["mvv"]["next"] == "approve" and ds["mvv"]["gate_next"] == "end"
    assert ds["approve"]["on_fail"] == "handoff" and ds["merge"]["on_fail"] == "handoff"
    assert '"関門 1"' in ds["handoff"]["cmd"] or "'関門 1'" in ds["handoff"]["cmd"]
    # ゲート 2: 開発版は承認資料を gate_as_ok で写し、本番の先頭が mvv → note → bump
    assert steps_of(load(waves["開発版"]["plans"][0]))["facts"]["gate_as_ok"] is True
    prod = load(waves["本番"]["plans"][0])
    first, second = prod["steps"][0], prod["steps"][1]
    assert first["id"] == "mvv" and "--gate release" in first["cmd"] and "--note" in first["cmd"] and first["next"] == "note"
    assert second["id"] == "note" and second["next"] == "bump" and second["on_fail"] == "handoff"
    assert "{queue_pr:check}" in first["cmd"] and "{queue_prs}" not in json.dumps(prod)


@pytest.mark.parametrize(
    "pace, word",
    [
        (None, "pace.json"),
        ({"fast": AUTO}, "auto.enabled"),
        ({"auto": {**AUTO, "enabled": False}}, "auto.enabled"),
        ({"auto": {**AUTO, "verify": ""}}, "auto.verify"),
        ({"auto": {**AUTO, "modes": ["light"]}}, "モード standard"),
    ],
)
def test_auto_is_refused_when_the_declaration_does_not_allow_it(tmp_path, pace, word):
    repo = make_repo(tmp_path, pace)
    p, out = new_sprint(tmp_path, repo, sprint_state(tmp_path))
    res = json.loads(p.stdout)
    assert p.returncode == 1 and res["status"] == "stopped" and word in res["summary"], res
    assert "--pace" not in res["next"] and "new sprint" in res["next"] and not out.exists()


def test_the_normal_fallback_keeps_every_sprint_option(tmp_path):
    repo = make_repo(tmp_path, None)
    extra = ["--test-cmd", "pytest {paths}", "--tests", "a", "b", "--scope", "s", "--base", "dev2", "--production-branch", "prd"]
    p, _ = new_sprint(tmp_path, repo, sprint_state(tmp_path), *extra)
    nxt = json.loads(p.stdout)["next"]
    assert "--pace" not in nxt and "--state" not in nxt
    for part in ["--test-cmd 'pytest {paths}'", "--tests a b", "--scope s", "--base dev2", "--production-branch prd", "--design 11"]:
        assert part in nxt, nxt


def test_auto_is_refused_for_operation_mode_without_a_dev_channel_and_without_mvv(tmp_path):
    repo = make_repo(tmp_path, {"auto": {**AUTO, "modes": ["operation", "standard"]}})
    p, _ = new_sprint(tmp_path, repo, sprint_state(tmp_path), "--mode", "operation")
    assert p.returncode == 1 and "モード operation" in json.loads(p.stdout)["summary"]
    repo2 = make_repo(tmp_path / "b", {"auto": AUTO}, prod="develop")
    p, _ = new_sprint(tmp_path / "b", repo2, sprint_state(tmp_path / "b"))
    assert p.returncode == 1 and "開発版のチャネル" in json.loads(p.stdout)["summary"]
    repo3 = make_repo(tmp_path / "c", {"auto": AUTO})
    p, out = new_sprint(tmp_path / "c", repo3, sprint_state(tmp_path / "c", approve=False))
    assert p.returncode == 1 and json.loads(p.stdout)["status"] == "stopped" and not out.exists()


def test_the_fast_and_auto_sections_do_not_stand_in_for_each_other(tmp_path):
    repo = make_repo(tmp_path, {"auto": AUTO})
    p, _ = new_sprint(tmp_path, repo, sprint_state(tmp_path), pace="fast")
    assert p.returncode == 1 and "fast.enabled" in json.loads(p.stdout)["summary"]


# ---------- 設計が無いとき・リリースの雛形が無いとき（I2） ----------


def test_without_design_the_sprint_branch_carries_the_command(tmp_path):
    repo = make_repo(tmp_path, {"auto": AUTO})
    p, out = new_sprint(tmp_path, repo, sprint_state(tmp_path), design=False)
    assert p.returncode == 0, p.stdout + p.stderr
    waves = load(out / "sprint.json")["ステージ"]
    assert [w["name"] for w in waves] == STAGES[2:]
    assert "command" in waves[0] and all(w["then_of"] == "スプリントブランチ" and "resume" in w for w in waves[1:])


def test_without_a_release_template_the_last_stage_is_manual(tmp_path):
    repo = make_repo(tmp_path, {"auto": AUTO}, form=False)
    p, out = new_sprint(tmp_path, repo, sprint_state(tmp_path))
    assert p.returncode == 0, p.stdout + p.stderr
    waves = load(out / "sprint.json")["ステージ"]
    assert [w["name"] for w in waves] == [*STAGES[:5], "リリース"] and "manual" in waves[-1]
    assert not any("--gate release" in Path(p).read_text() for w in waves for p in w.get("plans", []))


# ---------- 実行: handoff・note・withdraw（AC5・AC6・I8） ----------


def gate_mvv(state: Path, gate: str, log: Path) -> None:
    subprocess.run(
        [PY, str(SPRINT_STATE), "gate", str(state), gate, "--what", "MVV 判定", "--by", "mvv", "--verdict", "follow"]
        + ["--reasons", "[]", "--log", str(log)],
        check=True,
        capture_output=True,
    )


def auto_plans(tmp_path) -> tuple[Path, dict]:
    repo = make_repo(tmp_path, {"auto": AUTO})
    state = sprint_state(tmp_path)
    p, out = new_sprint(tmp_path, repo, state)
    assert p.returncode == 0, p.stdout + p.stderr
    return state, {w["name"]: w for w in load(out / "sprint.json")["ステージ"]}


def test_a_failed_merge_after_a_follow_verdict_ends_in_a_gate_and_withdraws_the_record(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))
    state, waves = auto_plans(tmp_path)
    ds = steps_of(load(waves["設計"]["plans"][0]))
    log = tmp_path / "mvv-gate.jsonl"
    log.write_text(json.dumps({"at": "2026-09-27T00:00:00Z", "gate": "design", "sprint": str(state), "verdict": "follow"}) + "\n")
    # mvv のステップが記録を書いた後に merge が落ちた流れを、run のステップだけで流す
    plan = tmp_path / "design.json"
    steps = [
        {
            "id": "mvv",
            "type": "run",
            "cmd": f"{PY} {SPRINT_STATE} gate {state} '関門 1' --what x --by mvv --verdict follow --reasons '[]' --log {log}",
            "next": "merge",
            "gate_next": "end",
        },
        {"id": "merge", "type": "run", "cmd": "exit 1", "on_fail": "handoff", "next": "end"},
        {**ds["handoff"]},
        {"id": "after", "type": "run", "cmd": f"touch {tmp_path}/after", "next": "end"},
    ]
    plan.write_text(json.dumps({"フェーズ": "試験", "課題": [], "作業場所": str(tmp_path), "steps": steps}, ensure_ascii=False))
    res = queue.cmd_queue([str(plan)], 3, poll=0.05)
    assert res["status"] == "gate" and res["items"][0]["result"] == "関門", res
    assert not (tmp_path / "after").exists()
    m = load(state)
    assert not [g for g in m["gates"] if g["name"] == "関門 1"] and m["withdrawals"][0]["name"] == "関門 1"
    # 外した後の差し戻しは覆しとして書かれない
    p = subprocess.run(
        [PY, str(SPRINT_STATE), "gate", str(state), "関門 1", "--what", "差し戻し", "--outcome", "rejected", "--mvv-log", str(log)],
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0 and "覆し" not in json.loads(p.stdout)["summary"]
    assert not (tmp_path / "mvv-state" / "project-mvv-signals.jsonl").exists()


def test_a_rejection_after_an_automatic_pass_is_an_override(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))
    state = sprint_state(tmp_path)
    gate_mvv(state, "関門 2", tmp_path / "mvv-gate.jsonl")
    p = subprocess.run(
        [PY, str(SPRINT_STATE), "gate", str(state), "関門 2", "--what", "差し戻し", "--outcome", "rejected"]
        + ["--mvv-log", str(tmp_path / "mvv-gate.jsonl")],
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0 and "override_reject" in json.loads(p.stdout)["summary"], p.stdout
    row = json.loads((tmp_path / "mvv-state" / "project-mvv-signals.jsonl").read_text().splitlines()[-1])
    assert row["kind"] == "override_reject" and row["gate"] == "関門 2"


def test_withdraw_keeps_the_user_record_and_does_nothing_without_a_record(tmp_path):
    state = sprint_state(tmp_path)
    p = subprocess.run([PY, str(SPRINT_STATE), "gate", str(state), "関門 1", "--withdraw"], capture_output=True, text=True)
    assert p.returncode == 0 and json.loads(p.stdout)["metrics"]["withdrawn"] == 0
    assert [w["name"] for w in load(state)["withdrawals"]] == ["関門 1"]
    subprocess.run([PY, str(SPRINT_STATE), "gate", str(state), "関門 1", "--what", "承認"], check=True, capture_output=True)
    p = subprocess.run([PY, str(SPRINT_STATE), "gate", str(state), "関門 1", "--withdraw"], capture_output=True, text=True)
    assert p.returncode == 0 and [g["name"] for g in load(state)["gates"]].count("関門 1") == 1
    p = subprocess.run([PY, str(SPRINT_STATE), "gate", str(state), "関門 1", "--withdraw", "--by", "mvv"], capture_output=True, text=True)
    assert p.returncode != 0 and json.loads(p.stdout)["status"] == "stopped"


def test_a_parallel_design_plan_cannot_revive_a_withdrawn_gate(tmp_path):
    """並列の設計プラン: 片方の handoff が先に取り消したら、もう片方が後から書く by: mvv は断られる（#1383 の指摘）。"""
    state = sprint_state(tmp_path)
    p = subprocess.run([PY, str(SPRINT_STATE), "gate", str(state), "関門 1", "--withdraw"], capture_output=True, text=True)
    assert p.returncode == 0
    p = subprocess.run(
        [PY, str(SPRINT_STATE), "gate", str(state), "関門 1", "--what", "MVV 判定", "--by", "mvv", "--verdict", "follow"]
        + ["--reasons", "[]", "--log", str(tmp_path / "mvv-gate.jsonl")],
        capture_output=True,
        text=True,
    )
    assert p.returncode != 0 and json.loads(p.stdout)["status"] == "stopped"
    assert not [g for g in load(state).get("gates", []) if g["name"] == "関門 1"]
    subprocess.run([PY, str(SPRINT_STATE), "gate", str(state), "関門 1", "--what", "承認"], check=True, capture_output=True)
    assert [g.get("by") for g in load(state)["gates"] if g["name"] == "関門 1"] == [None]


def fake_gh(tmp_path, fail: bool = False) -> tuple[dict, Path]:
    bin_dir, log = tmp_path / "fake-bin", tmp_path / "gh.log"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(f'#!/bin/sh\necho "$*" >> {log}\n' + ("exit 1\n" if fail else ""))
    (bin_dir / "gh").chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}, log


def test_the_release_note_comments_on_every_pr_and_a_failure_hands_off(tmp_path):
    state, waves = auto_plans(tmp_path)
    ps = steps_of(load(waves["本番"]["plans"][0]))
    state_dir = tmp_path / "prod-state"
    (state_dir / "work").mkdir(parents=True)
    (state_dir / "work" / "mvv-note.md").write_text("判定\n")
    run = ps["note"]["cmd"].replace("{queue_pr:check}", "31 32").replace("{state_dir}", str(state_dir))
    env, log = fake_gh(tmp_path)
    assert subprocess.run(run, shell=True, env=env).returncode == 0
    assert [line.split()[:3] for line in log.read_text().splitlines()] == [["pr", "comment", "31"], ["pr", "comment", "32"]]
    env, _ = fake_gh(tmp_path, fail=True)
    assert subprocess.run(run, shell=True, env=env).returncode != 0
    gate_mvv(state, "関門 2", tmp_path / "mvv-gate.jsonl")
    p = subprocess.run(ps["handoff"]["cmd"], shell=True, capture_output=True, text=True)
    assert p.returncode == 10 and "関門 2" in p.stdout
    assert not [g for g in load(state)["gates"] if g["name"] == "関門 2"]


def test_an_auto_plan_records_the_pace_before_the_first_stage(tmp_path):
    from supervise_lib import engine

    log, rec = tmp_path / "rec.log", tmp_path / "rec.sh"
    rec.write_text(f'echo "$@" >> {log}\n')
    plan = {"フェーズ": "試験", "課題": [5], "作業場所": str(tmp_path), "記録": str(rec), "進め方": "auto"}
    plan["steps"] = [{"id": "a", "type": "run", "cmd": "true", "stage": "実装", "next": "end"}]
    engine.Engine(plan, tmp_path / "state").run()
    assert log.read_text().splitlines() == ["5 pace auto", "5 stage 実装"]
