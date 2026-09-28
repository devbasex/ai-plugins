"""手動反映の本番系（#1454）: 起点と本番チャネルが同じで、本番系の行がすべて手動のリポジトリの pace: auto / fast。

使ってよい条件・ステージの並び（開発版 → 承認ゲート 2 → 本番）・承認ゲート 2 のプランと deploy-facts を見る。
実機の claude と gh は呼ばない（計画の形と、run のステップだけの計画を流して見る）。
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

SEC = {"enabled": True, "modes": ["light", "standard", "legacy-refactor"], "verify": "echo stack-ok"}
STG = {"target": "staging", "kind": "manual", "trigger": "cdk deploy Stg", "branch": "main", "versioned": False, "production": False}
PRD = {"target": "production", "kind": "manual", "trigger": "cdk deploy Prd", "branch": "main", "versioned": False, "production": True}
AUTO_MAIN = {"target": "web", "kind": "auto", "trigger": "push", "branch": "main", "versioned": False}


def make_repo(tmp_path: Path, rows, verify: str = "echo stack-ok") -> Path:
    """起点も本番チャネルも main の git リポジトリ（carmo-cdk の形）。rows が ... なら delivery を書かない。"""
    repo = tmp_path / "repo"
    (repo / ".ndf").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "base_branch": "main", "production_branch": "main"}))
    (repo / ".ndf" / "supervise.json").write_text(json.dumps({"test": {"command": "true"}}))
    (repo / ".ndf" / "project.json").write_text(json.dumps({} if rows is ... else {"delivery": rows}))
    sec = {**SEC, "verify": verify}
    (repo / ".ndf" / "pace.json").write_text(json.dumps({"version": 1, "auto": sec, "fast": sec}))
    return repo


def new_sprint(tmp_path, repo, pace="auto"):
    out = tmp_path / "m"
    args = ["new", "sprint", "--name", "m2", "--worktree", str(repo), "--issue", "11", "--design", "11"]
    args += ["--pace", pace, "--state", str(sprint_state(tmp_path)), "--out", str(out)]
    p = subprocess.run([PY, str(SUPERVISE), *args], capture_output=True, text=True, cwd=repo)
    return p, out


def waves_of(tmp_path, rows, pace="auto"):
    p, out = new_sprint(tmp_path, make_repo(tmp_path, rows), pace)
    assert p.returncode == 0, p.stdout + p.stderr
    return load(out / "sprint.json")["ステージ"]


@pytest.mark.parametrize("pace, rows, tail", [("auto", [STG, PRD], ["開発版"]), ("fast", [PRD], [])])
def test_manual_production_uses_the_pace(tmp_path, pace, rows, tail):
    """AC1・AC4・I5: 断らず、承認ゲート 2 の MVV 判定は検査の後・本番の前に 1 回だけある
    （fast の単独の queue は利用者の承認ゲートになる。test_fast_alone_asks_the_user_instead_of_the_mvv_judge）。"""
    waves = waves_of(tmp_path, rows, pace)
    names = [w["name"] for w in waves]
    assert names[-3:] == [*(["検査"] if pace == "auto" else ["実装レビュー"]) * (not tail), *tail, "承認ゲート 2", "本番"], names
    gates = [(i, p) for i, w in enumerate(waves) for p in w.get("plans", []) if "--gate release" in Path(p).read_text()]
    first = names.index("検査")
    assert len(gates) == 1 and first < gates[0][0] < names.index("本番"), gates
    assert Path(gates[0][1]).name.endswith("gate-2.json")
    assert all("manual" in waves[names.index(n)] for n in [*tail, "本番"])


def test_the_gate_waits_for_the_dev_release_when_there_is_one(tmp_path):
    """決定 6: 開発版があれば承認ゲート 2 は単独の queue で、auto は検査の PR を渡す。"""
    waves = {w["name"]: w for w in waves_of(tmp_path, [STG, PRD])}
    gate = waves["承認ゲート 2"]
    assert "command" in gate and "then_of" not in gate
    s = steps_of(load(gate["plans"][0]))
    assert "{queue_pr:check}" in s["mvv"]["cmd"] and s["facts"]["next"] == "mvv" and "on_fail" not in s["facts"]
    assert "deploy-facts" in s["facts"]["cmd"] and "echo stack-ok" in s["facts"]["cmd"]


def test_the_gate_follows_the_check_without_a_dev_release(tmp_path):
    waves = {w["name"]: w for w in waves_of(tmp_path, [PRD])}
    assert "開発版" not in waves and waves["承認ゲート 2"]["then_of"] == "設計"
    assert "{queue_pr:check}" in steps_of(load(waves["承認ゲート 2"]["plans"][0]))["mvv"]["cmd"]
    fast = {w["name"]: w for w in waves_of(tmp_path / "f", [PRD], "fast")}
    assert "{queue_prs}" in steps_of(load(fast["承認ゲート 2"]["plans"][0]))["mvv"]["cmd"]


def test_fast_alone_asks_the_user_instead_of_the_mvv_judge(tmp_path):
    """決定 6: fast の単独の queue は PR を集められないため、MVV 判定を打たずに 10 を返す。"""
    waves = {w["name"]: w for w in waves_of(tmp_path, [STG, PRD], "fast")}
    mvv = steps_of(load(waves["承認ゲート 2"]["plans"][0]))["mvv"]
    assert "mvv-gate.py" not in mvv["cmd"] and mvv["gate_next"] == "end"
    assert subprocess.run(mvv["cmd"], shell=True, capture_output=True).returncode == 10


def test_rows_without_production_go_after_the_gate(tmp_path):
    """I7: production を書いていない手動の行は本番へ入り、開発版へ入らない。本番は承認資料のコミットを届ける。"""
    extra = {"target": "batch", "kind": "manual", "trigger": "deploy batch", "versioned": False}
    waves = {w["name"]: w for w in waves_of(tmp_path, [STG, PRD, extra])}
    assert "deploy batch" in waves["本番"]["note"] and "deploy batch" not in waves["開発版"]["note"]
    assert "cdk deploy Stg" in waves["開発版"]["note"] and "cdk deploy Prd" in waves["本番"]["note"]
    assert "checkout" in waves["本番"]["note"] and "approval-deploy.md" in waves["本番"]["note"]


@pytest.mark.parametrize("rows", [[STG, PRD, AUTO_MAIN], ..., [STG, {**PRD, "production": None}]])
def test_auto_is_refused_when_the_production_is_not_manual(tmp_path, rows):
    """AC2・AC3: 自動反映の本番チャネル・delivery が無い・本番系の行が無いときは今と同じく断る。"""
    p, out = new_sprint(tmp_path, make_repo(tmp_path, rows))
    res = json.loads(p.stdout)
    assert p.returncode == 1 and "開発版のチャネル" in res["summary"] and not out.exists(), res


def test_normal_keeps_one_release_stage(tmp_path):
    """決定 5: normal は今と同じ「リリース」1 つ。"""
    repo = make_repo(tmp_path, [STG, PRD])
    out = tmp_path / "n"
    args = ["new", "sprint", "--name", "m2", "--worktree", str(repo), "--issue", "11", "--out", str(out)]
    p = subprocess.run([PY, str(SUPERVISE), *args], capture_output=True, text=True, cwd=repo)
    assert p.returncode == 0, p.stdout + p.stderr
    assert [w["name"] for w in load(out / "sprint.json")["ステージ"]][-1] == "リリース"


@pytest.mark.parametrize("verify, ran", [("true", True), ("false", False)])
def test_a_failed_verify_stops_before_the_mvv_judge(tmp_path, verify, ran):
    """AC6・I6: facts が導入の確認を走らせ、非 0 なら mvv へ進まずにプランが止まる。"""
    repo = make_repo(tmp_path, [STG, PRD], verify)
    commit_all(repo)
    p, out = new_sprint(tmp_path, repo)
    assert p.returncode == 0, p.stdout + p.stderr
    gate = next(w for w in load(out / "sprint.json")["ステージ"] if w["name"] == "承認ゲート 2")
    plan = load(gate["plans"][0])
    mark = tmp_path / "mvv-ran"
    for s in plan["steps"]:
        if s["id"] == "mvv":
            s["cmd"] = f"touch {mark}"
        if s["id"] == "note":
            s["cmd"] = "true"
    path = tmp_path / "gate-2.json"
    path.write_text(json.dumps(plan, ensure_ascii=False))
    res = queue.cmd_queue([str(path)], 1, poll=0.05)
    assert mark.exists() is ran, res
    assert (res["items"][0]["result"] == "完了") is ran, res


def deploy_facts(repo, verify, out):
    p = subprocess.run(
        [PY, str(STEPS), "deploy-facts", "--root", str(repo), "--verify", verify, "--out", str(out)], capture_output=True, text=True
    )
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def commit_all(repo):
    g = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*g, "add", "-A"], check=True)
    subprocess.run([*g, "commit", "-qm", "c"], check=True)


def test_deploy_facts_writes_the_material(tmp_path):
    """AC6: 確認の終了コードと本番系へ届ける行を承認資料に載せ、非 0 なら 1 で終える。出力は資料へ載せず 0600 のログへ分ける。"""
    extra = {"target": "batch", "kind": "manual", "trigger": "deploy batch", "versioned": False}
    repo = make_repo(tmp_path, [STG, PRD, extra])
    commit_all(repo)
    out = tmp_path / "a.md"
    code, res = deploy_facts(repo, "echo out-$((40+2))", out)
    text, log = out.read_text(), Path(res["metrics"]["log"])
    assert code == 0 and res["metrics"]["verify_exit"] == 0 and "out-42" not in text
    assert "out-42" in log.read_text() and log.stat().st_mode & 0o777 == 0o600
    assert "cdk deploy Prd" in text and "deploy batch" in text and "cdk deploy Stg" not in text
    code, res = deploy_facts(repo, "echo drift; exit 3", out)
    assert code == 1 and res["status"] == "stopped" and res["metrics"]["verify_exit"] == 3
    assert "終了コード: 3" in out.read_text()
    (repo / ".ndf" / "project.json").write_text("{")
    assert deploy_facts(repo, "true", out)[0] == 2


def test_deploy_facts_stops_when_head_is_not_the_delivered_commit(tmp_path):
    """確認するコミットと届けるコミットを同じにする: HEAD が先頭と違う・追跡中の変更があれば確認を走らせずに 3。"""
    repo = make_repo(tmp_path, [STG, PRD])
    commit_all(repo)
    mark, out = tmp_path / "ran", tmp_path / "a.md"
    (repo / ".ndf" / "supervise.json").write_text("{}")
    assert deploy_facts(repo, f"touch {mark}", out)[0] == 3
    commit_all(repo)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--detach", "HEAD~1"], check=True)
    (repo / "diverged").write_text("x")
    commit_all(repo)
    code, res = deploy_facts(repo, f"touch {mark}", out)
    assert code == 3 and not mark.exists() and not out.exists(), res


def test_deploy_facts_catches_up_when_origin_moved_ahead(tmp_path):
    """検査の PR が origin でマージされた後、遅れた主ディレクトリを先頭まで fast-forward してから確認する。"""
    repo = make_repo(tmp_path, [STG, PRD])
    commit_all(repo)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(origin)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(origin)], check=True)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    (other / "merged").write_text("x")
    commit_all(other)
    subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"], check=True)
    ahead = subprocess.run(["git", "-C", str(other), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    mark, out = tmp_path / "ran", tmp_path / "a.md"
    code, res = deploy_facts(repo, f"touch {mark}", out)
    assert code == 0 and mark.exists() and res["metrics"]["sha"] == ahead, res
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert head == ahead


def test_the_model_accepts_production_and_keeps_old_declarations():
    """AC7: production は任意の真偽値。書いていない宣言（ai-plugins の .ndf/project.json）も今と同じく通る。"""
    sys.path.insert(0, str(SCRIPTS / "lib"))
    import schema
    from project_lib import model

    model.validate_item("delivery", [STG, PRD])
    model.validate_decl(json.loads((SCRIPTS.parents[2] / ".ndf" / "project.json").read_text()))
    with pytest.raises(schema.ShapeError):
        model.validate_item("delivery", [{**PRD, "production": "yes"}])


@pytest.mark.parametrize("form", [True, False])
def test_separate_branches_keep_their_stages(tmp_path, form):
    """AC8・AC9・I8: 起点と本番が違えば、production を書いても書かなくても今の並び（雛形か「リリース」）のまま。"""
    import test_supervise_pace_auto as pa

    repo = pa.make_repo(tmp_path, {"auto": pa.AUTO}, form=form)
    if not form:
        rows = [{**STG, "branch": "develop"}, PRD]
        (repo / ".ndf" / "project.json").write_text(json.dumps({"delivery": rows}))
    p, out = pa.new_sprint(tmp_path, repo, pa.sprint_state(tmp_path))
    assert p.returncode == 0, p.stdout + p.stderr
    manifest = load(out / "sprint.json")
    names = [w["name"] for w in manifest["ステージ"]]
    assert names == (pa.STAGES if form else [*pa.STAGES[:5], "リリース"]), names
    assert all("production" not in r for r in manifest["リリースの経路"]) is form
