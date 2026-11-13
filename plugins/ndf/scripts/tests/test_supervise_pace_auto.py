"""supervise.py の `pace: auto`（#1370）: 使ってよい条件・ステージの並び・設計承認ゲート・リリース承認ゲートの MVV 判定のステップ・
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

SUPERVISE = SCRIPTS / "supervise.py"
SPRINT_STATE = SCRIPTS / "sprint-state.py"
REPO = SCRIPTS.parents[2]
PY = sys.executable

sys.path.insert(0, str(SCRIPTS))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pace_state import sprint_state  # noqa: E402

AUTO = {"enabled": True, "modes": ["light", "standard", "legacy-refactor"], "verify": "true"}
STAGES = ["設計", "設計承認ゲート", "設計の結果", "スプリントブランチ", "実装", "検査", "開発版", "本番"]


# new sprint は --design に無い課題の本文を gh で読む（#1767）。見本の本文で答える
pytestmark = pytest.mark.usefixtures("issue_bodies")


def load(path) -> dict:
    return json.loads(Path(path).read_text())


def steps_of(plan: dict) -> dict:
    return {s["id"]: s for s in plan["steps"]}


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


# ---------- 使ってよい条件（AC1・I1・I9） ----------


# ---------- 設計が無いとき・リリースの雛形が無いとき（I2） ----------


# ---------- 実行: handoff・note・withdraw（AC5・AC6・I8） ----------


def gate_mvv(state: Path, gate: str, log: Path) -> None:
    subprocess.run(
        [PY, str(SPRINT_STATE), "gate", str(state), gate, "--what", "MVV 判定", "--by", "mvv", "--verdict", "follow"]
        + ["--reasons", "[]", "--log", str(log)],
        check=True,
        capture_output=True,
    )


def test_a_rejection_after_an_automatic_pass_is_an_override(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))
    state = sprint_state(tmp_path, "auto")
    gate_mvv(state, "リリース承認ゲート", tmp_path / "mvv-gate.jsonl")
    p = subprocess.run(
        [PY, str(SPRINT_STATE), "gate", str(state), "リリース承認ゲート", "--what", "差し戻し", "--outcome", "rejected"]
        + ["--mvv-log", str(tmp_path / "mvv-gate.jsonl")],
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0 and "override_reject" in json.loads(p.stdout)["summary"], p.stdout
    row = json.loads((tmp_path / "mvv-state" / "project-mvv-signals.jsonl").read_text().splitlines()[-1])
    assert row["kind"] == "override_reject" and row["gate"] == "リリース承認ゲート"


def test_withdraw_keeps_the_user_record_and_does_nothing_without_a_record(tmp_path):
    state = sprint_state(tmp_path, "auto")
    p = subprocess.run([PY, str(SPRINT_STATE), "gate", str(state), "設計承認ゲート", "--withdraw"], capture_output=True, text=True)
    assert p.returncode == 0 and json.loads(p.stdout)["metrics"]["withdrawn"] == 0
    assert [w["name"] for w in load(state)["withdrawals"]] == ["設計承認ゲート"]
    subprocess.run([PY, str(SPRINT_STATE), "gate", str(state), "設計承認ゲート", "--what", "承認"], check=True, capture_output=True)
    p = subprocess.run([PY, str(SPRINT_STATE), "gate", str(state), "設計承認ゲート", "--withdraw"], capture_output=True, text=True)
    assert p.returncode == 0 and [g["name"] for g in load(state)["gates"]].count("設計承認ゲート") == 1
    p = subprocess.run(
        [PY, str(SPRINT_STATE), "gate", str(state), "設計承認ゲート", "--withdraw", "--by", "mvv"], capture_output=True, text=True
    )
    assert p.returncode != 0 and json.loads(p.stdout)["status"] == "stopped"


def test_a_parallel_design_plan_cannot_revive_a_withdrawn_gate(tmp_path):
    """並列の設計プラン: 片方の handoff が先に取り消したら、もう片方が後から書く by: mvv は断られる（#1383 の指摘）。"""
    state = sprint_state(tmp_path, "auto")
    p = subprocess.run([PY, str(SPRINT_STATE), "gate", str(state), "設計承認ゲート", "--withdraw"], capture_output=True, text=True)
    assert p.returncode == 0
    p = subprocess.run(
        [PY, str(SPRINT_STATE), "gate", str(state), "設計承認ゲート", "--what", "MVV 判定", "--by", "mvv", "--verdict", "follow"]
        + ["--reasons", "[]", "--log", str(tmp_path / "mvv-gate.jsonl")],
        capture_output=True,
        text=True,
    )
    assert p.returncode != 0 and json.loads(p.stdout)["status"] == "stopped"
    assert not [g for g in load(state).get("gates", []) if g["name"] == "設計承認ゲート"]
    subprocess.run([PY, str(SPRINT_STATE), "gate", str(state), "設計承認ゲート", "--what", "承認"], check=True, capture_output=True)
    assert [g.get("by") for g in load(state)["gates"] if g["name"] == "設計承認ゲート"] == [None]


def fake_gh(tmp_path, fail: bool = False) -> tuple[dict, Path]:
    bin_dir, log = tmp_path / "fake-bin", tmp_path / "gh.log"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(f'#!/bin/sh\necho "$*" >> {log}\n' + ("exit 1\n" if fail else ""))
    (bin_dir / "gh").chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}, log


def test_an_auto_plan_records_the_pace_before_the_first_stage(tmp_path):
    from supervise_lib import engine

    log, rec = tmp_path / "rec.log", tmp_path / "rec.sh"
    rec.write_text(f'echo "$@" >> {log}\n')
    plan = {"フェーズ": "試験", "課題": [5], "作業場所": str(tmp_path), "記録": str(rec), "進め方": "auto"}
    plan["steps"] = [{"id": "a", "type": "run", "cmd": "true", "stage": "実装", "next": "end"}]
    engine.Engine(plan, tmp_path / "state").run()
    assert log.read_text().splitlines() == ["5 stage 実装 --pace auto"]


def test_a_plan_records_the_mode_once_before_the_first_stage(tmp_path):
    """課題の進行の見出し行へモードを書く（`モード: —` のまま残らない。#2921）。"""
    from supervise_lib import engine

    log, rec = tmp_path / "rec.log", tmp_path / "rec.sh"
    rec.write_text(f'echo "$@" >> {log}\n')
    plan = {"フェーズ": "試験", "課題": [5], "作業場所": str(tmp_path), "記録": str(rec), "モード": "standard", "進め方": "auto"}
    plan["steps"] = [
        {"id": "a", "type": "run", "cmd": "true", "stage": "実装", "next": "b"},
        {"id": "b", "type": "run", "cmd": "true", "stage": "完了判定", "next": "end"},
    ]
    engine.Engine(plan, tmp_path / "state").run()
    assert log.read_text().splitlines() == [
        "5 stage 実装 --mode standard --pace auto",
        "5 stage 完了判定",
    ]  # 本文の取得と更新は工程ごとに 1 回


def test_a_plan_skips_the_stages_it_does_not_record(tmp_path):
    from supervise_lib import engine

    log, rec = tmp_path / "rec.log", tmp_path / "rec.sh"
    rec.write_text(f'echo "$@" >> {log}\n')
    plan = {"フェーズ": "試験", "課題": [5], "作業場所": str(tmp_path), "記録": str(rec), "記録しない工程": ["完了判定"]}
    plan["steps"] = [
        {"id": "a", "type": "run", "cmd": "true", "stage": "実装", "next": "b"},
        {"id": "b", "type": "run", "cmd": "true", "stage": "完了判定", "next": "c"},
        {"id": "c", "type": "run", "cmd": "true", "stage": "Pull Request", "next": "end"},
    ]
    engine.Engine(plan, tmp_path / "state").run()
    assert log.read_text().splitlines() == ["5 stage 実装", "5 stage Pull Request"]
