"""手動反映の本番系（#1454）: 起点と本番チャネルが同じで、本番系の行がすべて手動のリポジトリの pace: auto / fast。

使ってよい条件・ステージの並び（開発版 → リリース承認ゲート→ 本番）・リリース承認ゲートのプランと deploy-facts を見る。
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

SEC = {"enabled": True, "modes": ["light", "standard", "legacy-refactor"], "verify": "echo stack-ok"}
STG = {"target": "staging", "kind": "manual", "trigger": "cdk deploy Stg", "branch": "main", "versioned": False, "production": False}
PRD = {"target": "production", "kind": "manual", "trigger": "cdk deploy Prd", "branch": "main", "versioned": False, "production": True}
AUTO_MAIN = {"target": "web", "kind": "auto", "trigger": "push", "branch": "main", "versioned": False}


# new sprint は --design に無い課題の本文を gh で読む（#1767）。見本の本文で答える
pytestmark = pytest.mark.usefixtures("issue_bodies")


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
