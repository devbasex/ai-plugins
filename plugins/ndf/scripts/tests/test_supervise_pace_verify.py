"""経路 merge / promote の pace: auto / fast（#1457）: リリース承認ゲートの前に導入の確認（<節>.verify）を 1 度走らせる。

起点 develop・本番 main で release.form を持たないリポジトリ（project-trygroup-prd の形）で、本番チャネルへ入れるリリースプランの先頭の verify と、
経路 merge だけのときの「導入の確認」のステージ、verify-facts の承認資料を見る。実機の claude と gh は呼ばない。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

SUPERVISE = SCRIPTS / "supervise.py"
STEPS = SCRIPTS / "release-steps.py"
PY = sys.executable

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_supervise_pace_auto import load, steps_of  # noqa: E402
from test_supervise_pace_manual import commit_all  # noqa: E402

MODES = ["light", "standard", "legacy-refactor"]
STG = {"target": "stg", "kind": "auto", "trigger": "develop へのマージ", "branch": "develop", "versioned": False}
PRD = {"target": "prd", "kind": "auto", "trigger": "main へのマージ", "branch": "main", "versioned": False}
API = {"target": "api", "kind": "manual", "trigger": "sam deploy", "versioned": False}


# new sprint は --design に無い課題の本文を gh で読む（#1767）。見本の本文で答える
pytestmark = pytest.mark.usefixtures("issue_bodies")


def make_repo(tmp_path: Path, rows, auto: str = "echo check-stg", fast: str = "echo smoke-stg") -> Path:
    """起点 develop・本番 main の git リポジトリ。宣言はコミットして、確認するworktreeを先頭に揃える。"""
    repo = tmp_path / "repo"
    (repo / ".ndf").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "develop", str(repo)], check=True)
    (repo / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "base_branch": "develop", "production_branch": "main"}))
    (repo / ".ndf" / "supervise.json").write_text(json.dumps({"test": {"command": "true"}}))
    (repo / ".ndf" / "project.json").write_text(json.dumps({"delivery": rows}, ensure_ascii=False))
    secs = {k: {"enabled": True, "modes": MODES, **({"verify": v} if v else {})} for k, v in (("auto", auto), ("fast", fast))}
    (repo / ".ndf" / "pace.json").write_text(json.dumps({"version": 1, **secs}))
    commit_all(repo)
    return repo


def plans_of(waves) -> list[dict]:
    return [load(p) for w in waves for p in w.get("plans", [])]


def verify_steps(waves) -> list[dict]:
    return [s for plan in plans_of(waves) for s in plan["steps"] if "verify-facts" in str(s.get("cmd"))]


def wave(waves, name) -> dict:
    return next(w for w in waves if w["name"] == name)


def test_the_promote_plan_uses_the_declared_branch_names(tmp_path):
    """#1529 の AC7・AC8・I5: 確かめたコミットの照合は fast / auto の promote にだけ付き、ブランチ名は引数のものだけが現れる。
    #2104 の AC5・I7: normal の promote はリリースの承認資料を書き、両方の promote-approved が同じ資料で照合する。"""
    from supervise_lib.delivery_templates import PromoteParams, plan_promote

    a = type("A", (), {"base": "trunk", "production_branch": "stable", "issue": "11", "mode": "standard"})()
    normal = steps_of(plan_promote(a, str(tmp_path), 600, PromoteParams(verify="true")))
    fast = steps_of(plan_promote(a, str(tmp_path), 600, PromoteParams(mvv=str(tmp_path / "state.json"), verify="true")))
    base = f"{SCRIPTS / 'merged-steps.py'} promote --head trunk --base stable --timeout 600"
    promote_material = "{state_dir}/work/approval-promote.md"
    assert normal["promote"]["cmd"].endswith(base + f" --out {promote_material}")
    assert normal["promote-approved"]["cmd"].endswith(base + f" --approved-from {promote_material} --gate-approved user")
    assert fast["promote-approved"]["cmd"] == normal["promote-approved"]["cmd"]
    assert fast["prepare"]["cmd"].endswith(f"--prepare --out {promote_material}")  # #2104 の AC5・I7: 書き手と読み手が同じ資料
    want = base + " --expect-head-from {state_dir}/work/approval-verify.md --gate-approved mvv"
    assert fast["promote"]["cmd"].endswith(want)
    for s in (*normal.values(), *fast.values()):
        assert "develop" not in str(s.get("cmd")) and " main" not in str(s.get("cmd"))


def verify_facts(repo, verify, out, base="develop"):
    args = [PY, str(STEPS), "verify-facts", "--root", str(repo), "--base", base, "--verify", verify, "--out", str(out)]
    p = subprocess.run(args, capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def test_verify_facts_writes_the_material_without_the_output(tmp_path):
    """AC5・AC6・I4: 資料にコマンド・終了コード・確認したコミットが載り、出力は載せず 0600 のログへ分ける。非 0 は 1。"""
    repo = make_repo(tmp_path, [STG, PRD])
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    out = tmp_path / "w" / "approval-verify.md"
    code, res = verify_facts(repo, "echo out-$((40+2)); echo err-$((40+3)) >&2", out)
    text, log = out.read_text(), Path(res["metrics"]["log"])
    assert code == 0 and res["metrics"]["verify_exit"] == 0 and res["metrics"]["sha"] == sha and sha in text
    assert "echo out-$((40+2))" in text and "終了コード: 0" in text and "out-42" not in text and "err-43" not in text
    assert "out-42" in log.read_text() and "err-43" in log.read_text() and log.stat().st_mode & 0o777 == 0o600
    code, res = verify_facts(repo, "exit 4", out)
    assert code == 1 and res["status"] == "stopped" and "終了コード: 4" in out.read_text()


def test_verify_facts_stops_when_head_is_not_the_base(tmp_path):
    """AC7・I5: 追跡中の変更がある・HEAD が先頭と違えば、確認を走らせずに 3。"""
    repo = make_repo(tmp_path, [STG, PRD])
    mark, out = tmp_path / "ran", tmp_path / "a.md"
    (repo / ".ndf" / "supervise.json").write_text("{}")
    assert verify_facts(repo, f"touch {mark}", out)[0] == 3
    commit_all(repo)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--detach", "HEAD~1"], check=True)
    (repo / "diverged").write_text("x")
    commit_all(repo)
    code, res = verify_facts(repo, f"touch {mark}", out)
    assert code == 3 and not mark.exists() and not out.exists(), res


PRD_ON_BASE = {**PRD, "trigger": "develop へのマージ", "branch": "develop"}  # ベースブランチへのマージで本番へ届く


def chain(steps) -> list[str]:
    """ready から `next` をたどった id の並び（end まで）。"""
    by = {s["id"]: s for s in steps}
    out, cur = [], "ready"
    while cur in by and cur not in out:
        out.append(cur)
        cur = by[cur].get("next")
    return out


DEPTH_TAIL = [
    "refine",
    "refine-record",
    "overhaul-due",
    "overhaul-claim",
    "overhaul",
    "overhaul-release",
    "review-due",
    "review-claim",
    "review",
    "review-release",
]
MERGE_TAIL = ["merge-gate", "merge", "close"]  # マージの後は課題を閉じる close だけ（#2914 の I3・I8）
