"""試行のスクリプト（scripts/experimental/）の境界と振る舞い。"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
EXP = SCRIPTS / "experimental"
PLUGIN = SCRIPTS.parent


CODE_SUFFIXES = {".py", ".sh", ".json"}


def default_behaviour_files():
    """既定で動くもの: コード・hook・設定と、LLM が手順として読む Skill とエージェントの本文。
    README・CHANGELOG のような利用者向けの説明は、試行の置き場を紹介してよいので見ない。"""
    for p in PLUGIN.rglob("*"):
        if not p.is_file() or EXP in p.parents or "__pycache__" in p.parts or p == Path(__file__):
            continue
        rel = p.relative_to(PLUGIN).parts
        if p.suffix in CODE_SUFFIXES or (p.suffix == ".md" and rel[0] in ("skills", "agents")):
            yield p


def test_stable_side_does_not_reference_experimental():
    """既定の振る舞いに試行が漏れないよう、既定で動くものは experimental/ を参照しない。"""
    hits = []
    for p in default_behaviour_files():
        try:
            text = p.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if "experimental/" in text:
            hits.append(str(p.relative_to(PLUGIN)))
    assert hits == []


def test_default_behaviour_files_cover_skills_and_skip_readme():
    files = {str(p.relative_to(PLUGIN)) for p in default_behaviour_files()}
    assert "skills/development-workflow/SKILL.md" in files
    assert "scripts/supervise.py" in files
    assert "README.md" not in files


def fake_gh(tmp_path, view_body):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "view.txt").write_text(view_body)
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/bash\n[ "$2" = view ] && {{ cat {tmp_path / "view.txt"}; echo; }}\nexit 0\n')
    gh.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}


@pytest.mark.parametrize("view_body, code", [
    ("## 本文\n\n- 行\n", 0),              # 末尾の改行だけ違う（gh -q が 1 つ足す）
    ("## 本文\r\n\r\n- 行\r\n\r\n", 0),     # CR と末尾の空行
    ("## 本文\n\n- 別の行\n", 1),          # 中身が違う
])
def test_issue_body_set_compares_after_reread(tmp_path, view_body, code):
    f = tmp_path / "body.md"
    f.write_text("## 本文\n\n- 行\n")
    p = subprocess.run([sys.executable, str(EXP / "issue-body.py"), "set", "1", str(f)],
                       capture_output=True, text=True, env=fake_gh(tmp_path, view_body))
    assert p.returncode == code, p.stdout + p.stderr
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out["items"][0]["result"] == ("matched" if code == 0 else "mismatch")


def test_resume_reports_manual_start_after_no_mark(tmp_path):
    """前の区間が no-mark で終わり、利用者が手で起動した区間（2026-09-25 03:25 の形）。"""
    root = tmp_path / "state" / "ndf" / "relay"
    prev, cur = root / "20260925T001638Z-1-a", root / "20260925T032526Z-2-b"
    prev.mkdir(parents=True)
    cur.mkdir()
    (prev / "log.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"event": "start", "section": 2, "from_session": "2f5b", "plugin_version": "10.17.11"},
        {"event": "end", "section": 2, "seconds": 7769.9, "ended_by": "no-mark", "at": "t1"}]) + "\n")
    (cur / "log.jsonl").write_text(json.dumps(
        {"event": "start", "section": 1, "from_session": "", "plugin_version": "10.17.11", "at": "t2"}) + "\n")
    env = {**os.environ, "XDG_STATE_HOME": str(tmp_path / "state"), "CLAUDE_CONFIG_DIR": str(tmp_path / "cfg"),
           "XDG_DATA_HOME": str(tmp_path / "data")}
    p = subprocess.run([sys.executable, str(EXP / "resume.py"), "--relay-dir", str(cur)],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    item = json.loads(p.stdout.strip().splitlines()[-1])["items"][0]
    assert item["position"] == "not-running"   # ラッパーの pid が無い
    assert item["started_by"].startswith("手")
    assert item["previous_end"]["ended_by"] == "no-mark"
    assert item["previous_dir"] == str(prev)


def test_resume_lists_mark_skipped_of_previous_and_current_section(tmp_path):
    """前の区間と今の区間の mark_skipped を並べ、ほかの区間の分は出さない（#1035）。"""
    root = tmp_path / "state" / "ndf" / "relay"
    cur = root / "20260925T041000Z-1-a"
    cur.mkdir(parents=True)
    task = {"id": "b5rbmp9yj", "type": "shell", "command": "until grep -q x q.log; do sleep 30; done"}
    (cur / "log.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"event": "start", "section": 1, "from_session": ""},
        {"event": "mark_skipped", "section": 1, "reason": "blocks", "tasks": [], "held": False},
        {"event": "end", "section": 1, "ended_by": "mark"},
        {"event": "start", "section": 2, "from_session": "s1"},
        {"event": "mark_skipped", "section": 2, "reason": "background", "tasks": [task], "held": True},
        {"event": "end", "section": 2, "ended_by": "mark"},
        {"event": "start", "section": 3, "from_session": "s2"},
        {"event": "mark_skipped", "section": 3, "reason": "blocks", "tasks": [], "held": False}]) + "\n")
    env = {**os.environ, "XDG_STATE_HOME": str(tmp_path / "state"), "CLAUDE_CONFIG_DIR": str(tmp_path / "cfg"),
           "XDG_DATA_HOME": str(tmp_path / "data")}
    p = subprocess.run([sys.executable, str(EXP / "resume.py"), "--relay-dir", str(cur)],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    item = json.loads(p.stdout.strip().splitlines()[-1])["items"][0]
    assert [(r["section"], r["reason"]) for r in item["mark_skipped"]] == [(2, "background"), (3, "blocks")]
    assert "b5rbmp9yj" in p.stdout and "背景の作業が残った" in p.stdout


def _body_repo(tmp_path):
    """origin（bare）と clone を作り、clone に origin/develop へ載っていないファイルを 1 つ置く。"""
    run = lambda *a, cwd=None: subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True)
    bare, clone = tmp_path / "origin.git", tmp_path / "clone"
    run("git", "init", "-q", "--bare", "-b", "develop", str(bare))
    run("git", "clone", "-q", str(bare), str(clone))
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        run("git", "config", k, v, cwd=clone)
    (clone / "docs").mkdir()
    (clone / "docs" / "pushed.md").write_text("x\n")
    (clone / ".ndf").mkdir()
    (clone / ".ndf" / "worktree.json").write_text('{"base_branch": "develop"}\n')
    run("git", "add", "-A", cwd=clone)
    run("git", "commit", "-qm", "init", cwd=clone)
    run("git", "push", "-q", "origin", "HEAD:develop", cwd=clone)
    (clone / "issues").mkdir()
    (clone / "issues" / "local-only.md").write_text("y\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text("#!/bin/sh\necho called >> \"$FAKE_GH_LOG\"\n")
    (bindir / "gh").chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", FAKE_GH_LOG=str(tmp_path / "gh.log"))
    return clone, env


def test_issue_body_refuses_paths_only_on_this_machine(tmp_path):
    # 手元にだけあって GitHub から読めないファイルを本文が指すと、書き込む前に止める
    clone, env = _body_repo(tmp_path)
    body = tmp_path / "body.md"
    body.write_text("要求は `issues/local-only.md` にある。仕様は `docs/pushed.md`。新しく `docs/new.md` を作る\n")
    p = subprocess.run([sys.executable, str(EXP / "issue-body.py"), "set", "1", str(body)],
                       cwd=clone, env=env, capture_output=True, text=True)
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert p.returncode == 1 and out["status"] == "stopped"
    assert [i["path"] for i in out["items"]] == ["issues/local-only.md"]
    assert not (tmp_path / "gh.log").exists()  # gh は呼ばない
