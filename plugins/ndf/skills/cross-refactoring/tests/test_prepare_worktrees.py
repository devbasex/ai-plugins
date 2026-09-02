"""作業ディレクトリの準備と Skill 配置のテスト。

一時的な git リポジトリを作って実際に実行する。gh / 各 CLI は呼ばない。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

import pytest

_HERE = pathlib.Path(__file__).resolve().parent
_SCRIPT = _HERE.parent / "scripts" / "prepare-worktrees.sh"
REQUIRED = ["refactoring", "tdd-cycle", "quality-gates"]
RUNTIMES = ["codex", "gemini", "kiro"]



def _git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )


@pytest.fixture
def repo(tmp_path):
    """origin 付きの一時リポジトリと、head ブランチの作業ディレクトリを用意する。"""
    origin = tmp_path / "origin.git"
    work_repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(work_repo)],
                   check=True, capture_output=True)
    _git("config", "user.email", "t@e.st", cwd=work_repo)
    _git("config", "user.name", "test", cwd=work_repo)
    (work_repo / "src").mkdir()
    (work_repo / "src" / "foo.py").write_text("def f():\n    pass\n")
    _git("add", "-A", cwd=work_repo)
    _git("commit", "-qm", "init", cwd=work_repo)
    _git("branch", "-M", "main", cwd=work_repo)
    _git("push", "-q", "origin", "main", cwd=work_repo)
    _git("checkout", "-qb", "refactor/target", cwd=work_repo)
    (work_repo / "src" / "bar.py").write_text("x = 1\n")
    _git("add", "-A", cwd=work_repo)
    _git("commit", "-qm", "wip", cwd=work_repo)
    _git("push", "-q", "origin", "refactor/target", cwd=work_repo)
    _git("checkout", "-q", "main", cwd=work_repo)

    root = tmp_path / "rf130"
    _git("worktree", "add", "-q", str(root / "work"), "refactor/target", cwd=work_repo)
    tmp_dir = root / "work" / ".cross_refactoring"
    tmp_dir.mkdir(parents=True)
    state = {
        "id": 130, "repo": "acme/demo", "current_pr": 130,
        "base_branch": "main", "head_branch": "refactor/target",
        "worktree_root": str(root),
        "worktrees": {"work": str(root / "work"),
                      **{r: str(root / r) for r in RUNTIMES}},
        "tmp_dir": str(tmp_dir), "target_scope": ["src"],
        "host": "claude", "host_detection": "explicit",
        "runtimes": RUNTIMES, "impl_capable": ["claude", "codex", "kiro"],
        "models": {r: None for r in ["claude", "codex", "gemini", "kiro"]},
        "skills": {"required": REQUIRED},
        "max_outer_rounds": 3, "max_fix_rounds": 3, "max_items_per_round": 5,
        "severity_threshold": "minor",
        "baseline_test": {"command": "true", "status": "green", "checked_at": "x"},
        "outer_round": 0, "phase": "init", "rounds": [], "items": [],
        "deferred_items": [], "final": None,
    }
    state_path = tmp_dir / "cross-refactoring-rf130-state.json"
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return {"repo": work_repo, "root": root, "tmp_dir": tmp_dir, "state": state_path}


def _run(repo, *args, expect_ok=True):
    env = {**os.environ, "CROSS_REFACTORING_TMP_DIR": str(repo["tmp_dir"])}
    r = subprocess.run(
        ["bash", str(_SCRIPT), "130", *args],
        cwd=repo["repo"], env=env, capture_output=True, text=True,
    )
    if expect_ok:
        assert r.returncode == 0, r.stderr
    return r


def test_creates_detached_worktrees_for_participants(repo):
    _run(repo)
    for rt in RUNTIMES:
        assert (repo["root"] / rt).is_dir()
        head = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo["root"] / rt)
        assert head.stdout.strip() == "HEAD", f"{rt} が detach されていない"


def test_work_is_the_only_checked_out_branch(repo):
    """同一ブランチを 2 つの作業ディレクトリへ checkout できないため。"""
    _run(repo)
    head = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo["root"] / "work")
    assert head.stdout.strip() == "refactor/target"


def test_skills_are_provisioned_into_runtime_locations(repo):
    _run(repo)
    layout = {
        "codex": ".agents/skills", "gemini": ".gemini/skills", "kiro": ".kiro/skills",
    }
    for rt, rel in layout.items():
        for name in REQUIRED:
            assert (repo["root"] / rt / rel / name / "SKILL.md").is_file()


def test_work_gets_all_three_impl_runtime_locations(repo):
    """実装担当はラウンドごとに変わるため、work には 3 ランタイム分すべてを作る。"""
    _run(repo)
    for rel in (".claude/skills", ".agents/skills", ".kiro/skills"):
        for name in REQUIRED:
            assert (repo["root"] / "work" / rel / name / "SKILL.md").is_file()


def test_provisioning_does_not_touch_the_pull_request_diff(repo):
    _run(repo)
    status = _git("status", "--short", cwd=repo["root"] / "work")
    assert status.stdout.strip() == "", f"差分に現れている: {status.stdout}"


def test_provisioning_does_not_touch_the_repository_itself(repo):
    """配置は作業ディレクトリの中だけで完結する。対象リポジトリ本体を触らない。"""
    common_exclude = repo["repo"] / ".git" / "info" / "exclude"
    before = common_exclude.read_text() if common_exclude.exists() else ""
    _run(repo)
    after = common_exclude.read_text() if common_exclude.exists() else ""
    assert before == after
    status = _git("status", "--short", cwd=repo["repo"])
    assert status.stdout.strip() == ""


def test_existing_skill_is_not_overwritten(repo):
    """対象リポジトリが元から持っている Skill は使い、上書きしない。

    「元から持っている」とは、対象リポジトリが追跡している状態を指す。
    そのため作業ディレクトリへ先置きするのではなく、ブランチへコミットして作る。
    """
    work = repo["root"] / "work"
    tracked = work / ".agents" / "skills" / "refactoring"
    tracked.mkdir(parents=True)
    (tracked / "SKILL.md").write_text("利用者の設定\n", encoding="utf-8")
    _git("add", "-A", cwd=work)
    _git("-c", "user.email=t@e.st", "-c", "user.name=test",
         "commit", "-qm", "add own skill", cwd=work)
    _git("push", "-q", "origin", "HEAD:refactor/target", cwd=work)

    _run(repo)

    codex_copy = repo["root"] / "codex" / ".agents" / "skills" / "refactoring"
    assert codex_copy.joinpath("SKILL.md").read_text() == "利用者の設定\n"
    assert (work / ".agents" / "skills" / "refactoring" / "SKILL.md").read_text() \
        == "利用者の設定\n"
    skills = json.loads(repo["state"].read_text())["skills"]
    assert skills["codex"]["refactoring"] == "preexisting"
    assert skills["codex"]["tdd-cycle"] == "provisioned"
    assert skills["work.codex"]["refactoring"] == "preexisting"


def test_results_are_recorded_in_the_state_file(repo):
    _run(repo)
    skills = json.loads(repo["state"].read_text())["skills"]
    for rt in RUNTIMES:
        assert skills[rt] == {n: "provisioned" for n in REQUIRED}
    for rt in ("claude", "codex", "kiro"):
        assert skills[f"work.{rt}"] == {n: "provisioned" for n in REQUIRED}


def test_provisioned_status_survives_a_second_run(repo):
    """再開しても「自分が配置した」記録が消えないこと。"""
    _run(repo)
    _run(repo)
    skills = json.loads(repo["state"].read_text())["skills"]
    assert skills["codex"]["refactoring"] == "provisioned"


def test_missing_skill_fails_the_run(repo, monkeypatch, tmp_path):
    """ホスト側にも見つからない Skill があれば失敗する。黙って劣化させない。"""
    state = json.loads(repo["state"].read_text())
    state["skills"]["required"] = ["refactoring", "no-such-skill"]
    repo["state"].write_text(json.dumps(state), encoding="utf-8")
    r = _run(repo, expect_ok=False)
    assert r.returncode != 0
    assert "no-such-skill" in r.stderr


def test_second_run_is_idempotent(repo):
    _run(repo)
    _run(repo)
    status = _git("status", "--short", cwd=repo["root"] / "work")
    assert status.stdout.strip() == ""


def test_stale_directory_is_moved_aside(repo):
    """現リポジトリの作業ディレクトリでないパスは退避して作り直す。"""
    stale = repo["root"] / "codex"
    stale.mkdir(parents=True)
    (stale / "leftover.txt").write_text("別リポジトリの残骸")
    _run(repo)
    assert (stale / ".git").exists(), "作り直されていない"
    moved = list(repo["root"].glob("codex.stale-*"))
    assert moved and (moved[0] / "leftover.txt").is_file()


def test_sync_moves_readonly_worktrees_to_a_sha(repo):
    _run(repo)
    sha = _git("rev-parse", "HEAD~1", cwd=repo["root"] / "work").stdout.strip()
    _run(repo, "sync", sha)
    for rt in RUNTIMES:
        head = _git("rev-parse", "HEAD", cwd=repo["root"] / rt).stdout.strip()
        assert head == sha


def test_non_empty_destination_is_not_deleted(repo):
    """SKILL.md が無いだけで既存の中身を消さない。

    利用者が作りかけている Skill や補助ファイルを失う。作業ディレクトリを作った
    あとに置かれた場合を再現するため、1 度目の実行後に作りかけを置く。
    """
    _run(repo)
    dest = repo["root"] / "codex" / ".agents" / "skills" / "refactoring"
    shutil.rmtree(dest)
    (dest / "references").mkdir(parents=True)
    (dest / "references" / "draft.md").write_text("作りかけ", encoding="utf-8")

    r = _run(repo, expect_ok=False)

    assert r.returncode != 0, "衝突しているのに続行している"
    assert "codex/refactoring" in r.stderr
    assert (dest / "references" / "draft.md").read_text() == "作りかけ"
    assert not (dest / "SKILL.md").exists(), "上書きされている"
    skills = json.loads(repo["state"].read_text())["skills"]
    assert skills["codex"]["refactoring"] == "conflict"


def test_empty_destination_is_provisioned(repo):
    """空ディレクトリは配置してよい。消えて困るものが無い。"""
    _run(repo)
    dest = repo["root"] / "codex" / ".agents" / "skills" / "refactoring"
    shutil.rmtree(dest)
    dest.mkdir()
    _run(repo)
    assert (dest / "SKILL.md").is_file()


# ---------- gemini の読み取り除外 ----------

def test_gemini_gets_a_setting_that_allows_reading_the_provisioned_skills(repo):
    """gemini は除外設定を**読み取りにも**適用するため、無効にする設定を置く。

    置かないと、配置した手順書を `read_file` で一切開けず、
    語彙を読めないまま提案が語彙外になって全件降格する。
    """
    _run(repo)
    settings = repo["root"] / "gemini" / ".gemini" / "settings.json"
    assert settings.is_file(), "gemini の設定が置かれていない"
    conf = json.loads(settings.read_text(encoding="utf-8"))
    # 項目名は gemini の版で変わる。新旧どちらの形式でも書く
    assert conf["context"]["fileFiltering"]["respectGitIgnore"] is False
    assert conf["context"]["fileFiltering"]["respectGeminiIgnore"] is False
    assert conf["fileFiltering"]["respectGitIgnore"] is False
    assert conf["fileFiltering"]["respectGeminiIgnore"] is False


def test_gemini_settings_are_not_in_the_diff(repo):
    _run(repo)
    status = _git("status", "--short", cwd=repo["root"] / "gemini")
    assert status.stdout.strip() == "", f"差分に現れている: {status.stdout}"


def test_only_gemini_gets_the_reading_setting(repo):
    """他のランタイムの設定は触らない。"""
    _run(repo)
    for rt in ("codex", "kiro"):
        assert not (repo["root"] / rt / ".gemini").exists()
