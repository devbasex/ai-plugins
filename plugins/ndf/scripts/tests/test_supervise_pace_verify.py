"""経路 merge / promote の pace: auto / fast（#1457）: 承認ゲート 2 の前に導入の確認（<節>.verify）を 1 度走らせる。

起点 develop・本番 main で release.form を持たないリポジトリ（project-trygroup-prd の形）で、昇格のプランの先頭の verify と、
経路 merge だけのときの「導入の確認」のステージ、verify-facts の承認資料を見る。実機の claude と gh は呼ばない。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SUPERVISE = SCRIPTS / "supervise.py"
STEPS = SCRIPTS / "release-steps.py"
PY = sys.executable

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from supervise_lib import queue  # noqa: E402
from test_supervise_pace_auto import load, sprint_state, steps_of  # noqa: E402
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


def new_sprint(tmp_path, repo, pace: str | None = "auto"):
    out = tmp_path / "m"
    args = ["new", "sprint", "--name", "m2", "--worktree", str(repo), "--issue", "11", "--out", str(out)]
    if pace:
        args += ["--pace", pace, "--state", str(sprint_state(tmp_path))]
    p = subprocess.run([PY, str(SUPERVISE), *args], capture_output=True, text=True, cwd=repo)
    assert p.returncode == 0, p.stdout + p.stderr
    return load(out / "sprint.json")["ステージ"]


def new_close(tmp_path, repo):
    out = tmp_path / "c"
    args = ["new", "close", "--name", "m2", "--worktree", str(repo), "--issue", "11", "--state", str(sprint_state(tmp_path))]
    p = subprocess.run([PY, str(SUPERVISE), *args, "--out", str(out)], capture_output=True, text=True, cwd=repo)
    return p, out


def plans_of(waves) -> list[dict]:
    return [load(p) for w in waves for p in w.get("plans", [])]


def verify_steps(waves) -> list[dict]:
    return [s for plan in plans_of(waves) for s in plan["steps"] if "verify-facts" in str(s.get("cmd"))]


def wave(waves, name) -> dict:
    return next(w for w in waves if w["name"] == name)


@pytest.mark.parametrize("pace, used, other", [("auto", "check-stg", "smoke-stg"), ("fast", "smoke-stg", "check-stg")])
def test_the_promote_plan_verifies_before_the_mvv_judge(tmp_path, pace, used, other):
    """AC1・AC2・AC5・AC9・I1・I2・I3: 昇格のプランの先頭が選んだ節の verify で、mvv の材料に確認の資料があり、確認は 1 つだけ。"""
    waves = new_sprint(tmp_path, make_repo(tmp_path, [STG, PRD]), pace)
    plan = load(wave(waves, "本番")["plans"][0])
    ids = [s["id"] for s in plan["steps"]]
    assert ids[:5] == ["verify", "prepare", "mvv", "note", "promote"], ids
    s = steps_of(plan)
    assert f"echo {used}" in s["verify"]["cmd"] and other not in s["verify"]["cmd"] and "--base develop" in s["verify"]["cmd"]
    assert s["verify"]["timeout"] == 1500 and "on_fail" not in s["verify"]
    assert "{state_dir}/work/approval-verify.md" in s["mvv"]["cmd"]
    assert len(verify_steps(waves)) == 1 and "導入の確認" not in [w["name"] for w in waves]


def run_promote(tmp_path, verify):
    """昇格のプランの verify 以外のステップを、走った id を記録するだけのコマンドに差し替えて流す。"""
    repo = make_repo(tmp_path, [STG, PRD], auto=verify)
    plan = load(wave(new_sprint(tmp_path, repo), "本番")["plans"][0])
    ran = tmp_path / "ran"
    for s in plan["steps"]:
        if s["id"] != "verify":
            s["cmd"] = f"echo {s['id']} >> {ran}"
    path = tmp_path / "promote.json"
    path.write_text(json.dumps(plan, ensure_ascii=False))
    res = queue.cmd_queue([str(path)], 1, poll=0.05)
    return res, (ran.read_text().split() if ran.exists() else [])


def test_a_failed_verify_stops_before_the_promote_pr(tmp_path):
    """AC3: 確認が 0 以外なら prepare・mvv・note・promote のどれも走らずに止まる。"""
    res, ran = run_promote(tmp_path, "false")
    assert ran == [] and res["items"][0]["result"] != "完了", res


def test_a_passed_verify_keeps_the_order(tmp_path):
    """AC4: 確認が 0 なら今と同じ順で続き、マージの後にリリース記録を書く（#1870）。"""
    res, ran = run_promote(tmp_path, "true")
    assert ran == ["prepare", "mvv", "note", "promote", "record"] and res["items"][0]["result"] == "完了", res


@pytest.mark.parametrize("pace", ["auto", "fast"])
def test_merge_only_places_a_verify_stage(tmp_path, pace):
    """AC8・I1: 経路 merge だけなら検査の後に「導入の確認」を then_of で置き、確認が落ちたら止まる。manual は後に続く。"""
    repo = make_repo(tmp_path, [STG, API], auto="false", fast="false")
    waves = new_sprint(tmp_path, repo, pace)
    names = [w["name"] for w in waves]
    assert names[-2:] == ["導入の確認", "リリース"] and "本番" not in names, names
    w = wave(waves, "導入の確認")
    assert w["then_of"] == wave(waves, "検査")["then_of"]
    assert len(verify_steps(waves)) == 1
    res = queue.cmd_queue([w["plans"][0]], 1, poll=0.05)
    assert res["items"][0]["result"] == "止まった", res


@pytest.mark.parametrize("rows, stage", [([STG, PRD], "本番"), ([STG], "導入の確認")])
def test_close_verifies_only_when_changed(tmp_path, rows, stage):
    """AC10・I7: new close も同じ形で組み、fast の verify を使い、実行の条件（changed）が 3 なら確認も走らない。"""
    repo = make_repo(tmp_path, rows)
    p, out = new_close(tmp_path, repo)
    assert p.returncode == 0, p.stdout + p.stderr
    waves = load(out / "sprint.json")["ステージ"]
    assert [w["name"] for w in waves] == ["最終の検査", stage, "まとめ"]
    plan = load(wave(waves, stage)["plans"][0])
    assert plan["実行の条件"]["skip_code"] == 3 and len(verify_steps(waves)) == 1
    mark = tmp_path / "ran"
    s = steps_of(plan)["verify"]
    assert "smoke-stg" in s["cmd"]
    s["cmd"] = f"touch {mark}"
    plan["実行の条件"]["cmd"] = "exit 3"
    path = tmp_path / "close.json"
    path.write_text(json.dumps(plan, ensure_ascii=False))
    res = queue.cmd_queue([str(path)], 1, poll=0.05)
    assert res["items"][0]["result"] == "完了" and not mark.exists(), res


def test_close_without_verify_writes_no_plan(tmp_path):
    """I3: new close で fast の節に verify が無ければ、プランを書かずに 2 で終える。"""
    p, out = new_close(tmp_path, make_repo(tmp_path, [STG, PRD], fast=""))
    assert p.returncode == 2 and "fast.verify" in p.stderr and not (out / "sprint.json").exists()


def test_normal_keeps_the_promote_plan(tmp_path):
    """AC11・I6: --pace 無しの昇格のプランは promote → promote-approved → record（#837）で、確認のステップを持たない。"""
    waves = new_sprint(tmp_path, make_repo(tmp_path, [STG, PRD]), None)
    ids = [s["id"] for s in load(wave(waves, "本番")["plans"][0])["steps"]]
    assert ids == ["promote", "promote-approved", "record", "judge-record"]
    assert verify_steps(waves) == [] and "導入の確認" not in [w["name"] for w in waves]


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


def test_gate_2_and_the_template_keep_their_stages(tmp_path):
    """AC12・I6: 手動反映の本番系の facts（cmd・上限・cwd）と、release.form の雛形のステージは変わらない。"""
    import test_supervise_pace_auto as pa
    import test_supervise_pace_manual as pm

    waves = pm.waves_of(tmp_path / "g", [pm.PRD])
    facts = steps_of(load(wave(waves, "承認ゲート 2")["plans"][0]))["facts"]
    repo = str((tmp_path / "g" / "repo").resolve())
    assert facts["timeout"] == 1500 and facts["cwd"] == repo and facts["stage"] == "配布" and facts["next"] == "mvv"
    assert "deploy-facts --root" in facts["cmd"] and "--out {state_dir}/work/approval-deploy.md" in facts["cmd"]
    repo = pa.make_repo(tmp_path / "t", {"auto": pa.AUTO}, form=True)
    p, out = pa.new_sprint(tmp_path / "t", repo, pa.sprint_state(tmp_path / "t"))
    assert p.returncode == 0, p.stdout + p.stderr
    waves = load(out / "sprint.json")["ステージ"]
    assert [w["name"] for w in waves] == pa.STAGES and verify_steps(waves) == []


@pytest.mark.parametrize("pace", [None, "fast"])
def test_the_promote_plan_records_after_the_merge(tmp_path, pace):
    """#1870 AC2: 昇格の PR のマージ（promote / promote-approved）の後に record --promote を打ち、落ちたら judge-record へ進む。"""
    waves = new_sprint(tmp_path, make_repo(tmp_path, [STG, PRD]), pace)
    steps = {s["id"]: s for s in load(wave(waves, "本番")["plans"][0])["steps"]}
    assert steps["promote"]["next"] == steps["promote-approved"]["next"] == "record"
    rec = steps["record"]
    assert "release-steps.py record --promote --head develop --base main --prs " in rec["cmd"]
    assert rec["on_fail"] == "judge-record" and rec["pr_from"] == "release_pr_url"
    assert steps["judge-record"]["choices"] == ["record", "stop"]


def close_steps(tmp_path, rows) -> dict:
    p, out = new_close(tmp_path, make_repo(tmp_path, rows))
    assert p.returncode == 0, p.stdout + p.stderr
    close = next(load(f) for f in sorted(out.glob("*.json")) if load(f).get("フェーズ") == "まとめ")
    return close["steps"]


def test_close_of_the_promote_route_reads_the_promote_pr(tmp_path):
    """#1870 AC3・AC6: 昇格の経路のまとめは --record-pr {queue_pr:promote} を打ち、その前にリリース後テストを置く。"""
    steps = close_steps(tmp_path, [STG, PRD])
    ids = [s["id"] for s in steps]
    close = next(s for s in steps if s["id"] == "close")
    assert "--record-pr {queue_pr:promote} " in close["cmd"]
    assert ids.index("release-verify") == ids.index("close") - 1
    rv = steps[ids.index("release-verify")]
    assert rv["type"] == "work" and rv["prompt"].startswith("/ndf:release-verification") and "{queue_pr:promote}" in rv["prompt"]


def test_close_of_the_merge_route_keeps_record_pr_zero(tmp_path):
    """#1870 AC3・AC6: 昇格の無い経路（ベースブランチへのマージで届く）は今と同じ --record-pr 0 で、リリース後テストを置かない。"""
    steps = close_steps(tmp_path, [STG])
    close = next(s for s in steps if s["id"] == "close")
    assert "--record-pr 0 " in close["cmd"] and "release-verify" not in [s["id"] for s in steps]
