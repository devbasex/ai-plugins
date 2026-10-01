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


LEDGER = PLUGIN.parents[1] / "docs" / "ndf-experiments.md"


def test_trials_moved_to_the_main_body_are_removed():
    """台帳の行き先が「本体へ移した」の試行は、試行の置き場に残らない（台帳だけ直して試行を残さない）。"""
    left = []
    for line in LEDGER.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5 or not cells[0].startswith("`") or not cells[-1].startswith("本体へ移した"):
            continue
        name = cells[0].strip("`")
        if (EXP / name).exists() or (EXP / Path(name).stem).exists():
            left.append(name)
    assert left == []


def fake_gh(tmp_path, view_body):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "view.txt").write_text(view_body)
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/bash\n[ "$2" = view ] && {{ cat {tmp_path / "view.txt"}; echo; }}\nexit 0\n')
    gh.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}


@pytest.mark.parametrize(
    "view_body, code",
    [
        ("## 本文\n\n- 行\n", 0),  # 末尾の改行だけ違う（gh -q が 1 つ足す）
        ("## 本文\r\n\r\n- 行\r\n\r\n", 0),  # CR と末尾の空行
        ("## 本文\n\n- 別の行\n", 1),  # 中身が違う
    ],
)
def test_issue_body_set_compares_after_reread(tmp_path, view_body, code):
    f = tmp_path / "body.md"
    f.write_text("## 本文\n\n- 行\n")
    p = subprocess.run(
        [sys.executable, str(EXP / "issue-body.py"), "set", "1", str(f)], capture_output=True, text=True, env=fake_gh(tmp_path, view_body)
    )
    assert p.returncode == code, p.stdout + p.stderr
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert out["items"][0]["result"] == ("matched" if code == 0 else "mismatch")


def test_resume_reports_manual_start_after_no_mark(tmp_path):
    """前の区間が no-mark で終わり、利用者が手で起動した区間（2026-09-25 03:25 の形）。"""
    root = tmp_path / "state" / "ndf" / "relay"
    prev, cur = root / "20260925T001638Z-1-a", root / "20260925T032526Z-2-b"
    prev.mkdir(parents=True)
    cur.mkdir()
    (prev / "log.jsonl").write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {"event": "start", "section": 2, "from_session": "2f5b", "plugin_version": "10.17.11"},
                {"event": "end", "section": 2, "seconds": 7769.9, "ended_by": "no-mark", "at": "t1"},
            ]
        )
        + "\n"
    )
    (cur / "log.jsonl").write_text(
        json.dumps({"event": "start", "section": 1, "from_session": "", "plugin_version": "10.17.11", "at": "t2"}) + "\n"
    )
    env = {
        **os.environ,
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "cfg"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
    }
    p = subprocess.run([sys.executable, str(EXP / "resume.py"), "--relay-dir", str(cur)], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    item = json.loads(p.stdout.strip().splitlines()[-1])["items"][0]
    assert item["position"] == "not-running"  # ラッパーの pid が無い
    assert item["started_by"].startswith("手")
    assert item["previous_end"]["ended_by"] == "no-mark"
    assert item["previous_dir"] == str(prev)


def test_resume_lists_mark_skipped_of_previous_and_current_section(tmp_path):
    """前の区間と今の区間の mark_skipped を並べ、ほかの区間の分は出さない（#1035）。"""
    root = tmp_path / "state" / "ndf" / "relay"
    cur = root / "20260925T041000Z-1-a"
    cur.mkdir(parents=True)
    task = {"id": "b5rbmp9yj", "type": "shell", "command": "until grep -q x q.log; do sleep 30; done"}
    (cur / "log.jsonl").write_text(
        "\n".join(
            json.dumps(r)
            for r in [
                {"event": "start", "section": 1, "from_session": ""},
                {"event": "mark_skipped", "section": 1, "reason": "blocks", "tasks": [], "held": False},
                {"event": "end", "section": 1, "ended_by": "mark"},
                {"event": "start", "section": 2, "from_session": "s1"},
                {"event": "mark_skipped", "section": 2, "reason": "background", "tasks": [task], "held": True},
                {"event": "end", "section": 2, "ended_by": "mark"},
                {"event": "start", "section": 3, "from_session": "s2"},
                {"event": "mark_skipped", "section": 3, "reason": "blocks", "tasks": [], "held": False},
            ]
        )
        + "\n"
    )
    env = {
        **os.environ,
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "cfg"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
    }
    p = subprocess.run([sys.executable, str(EXP / "resume.py"), "--relay-dir", str(cur)], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    item = json.loads(p.stdout.strip().splitlines()[-1])["items"][0]
    assert [(r["section"], r["reason"]) for r in item["mark_skipped"]] == [(2, "background"), (3, "blocks")]
    assert "b5rbmp9yj" in p.stdout and "背景の作業が残った" in p.stdout


@pytest.mark.parametrize(
    "row, shown, mismatch",
    [
        ({"relay_version_dir": "relay-10.17.53-bbbb2222"}, "動いているラッパー 10.17.53", False),
        ({"relay_version_dir": "relay-10.17.52-aaaa1111"}, "動いているラッパー 10.17.52", True),
        ({}, "動いているラッパー 不明（打ち直すと見える）", False),
    ],
    ids=["same", "differs", "unknown"],
)
def test_resume_lists_running_wrapper_version(tmp_path, row, shown, mismatch):
    """版の行に動いているラッパーの版を並べ、違えば食い違いあり。不明は食い違いに数えない（#1587 の AC15・AC16）。"""
    cur = tmp_path / "state" / "ndf" / "relay" / "20261001T000000Z-1-a"
    cur.mkdir(parents=True)
    (cur / "log.jsonl").write_text(
        json.dumps({"event": "start", "section": 1, "from_session": "", "plugin_version": "10.17.53", **row}) + "\n"
    )
    (tmp_path / "cfg" / "ndf").mkdir(parents=True)
    (tmp_path / "cfg" / "ndf" / "relay.version").write_text("10.17.53\n")
    env = {
        **os.environ,
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "cfg"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
    }
    p = subprocess.run([sys.executable, str(EXP / "resume.py"), "--relay-dir", str(cur)], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    line = next(x for x in p.stdout.splitlines() if x.startswith("版: "))
    assert shown in line and ("（食い違いあり）" in line) is mismatch
    res = json.loads(p.stdout.strip().splitlines()[-1])
    assert res["items"][0]["versions"]["動いているラッパー"] == shown.split(" ", 1)[1]
    assert res["metrics"]["version_mismatch"] is mismatch


def _body_repo(tmp_path):
    """origin（bare）と clone を作り、clone に origin/develop へ載っていないファイルを 1 つ置く。"""

    def run(*a, cwd=None):
        return subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True)

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
    (bindir / "gh").write_text('#!/bin/sh\necho called >> "$FAKE_GH_LOG"\n')
    (bindir / "gh").chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", FAKE_GH_LOG=str(tmp_path / "gh.log"))
    return clone, env


def test_issue_body_refuses_paths_only_on_this_machine(tmp_path):
    # 手元にだけあって GitHub から読めないファイルを本文が指すと、書き込む前に止める
    clone, env = _body_repo(tmp_path)
    body = tmp_path / "body.md"
    body.write_text("要求は `issues/local-only.md` にある。仕様は `docs/pushed.md`。新しく `docs/new.md` を作る\n")
    p = subprocess.run(
        [sys.executable, str(EXP / "issue-body.py"), "set", "1", str(body)], cwd=clone, env=env, capture_output=True, text=True
    )
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert p.returncode == 1 and out["status"] == "stopped"
    assert [i["path"] for i in out["items"]] == ["issues/local-only.md"]
    assert not (tmp_path / "gh.log").exists()  # gh は呼ばない


def _run_body(tmp_path, clone, env, text, *extra):
    body = tmp_path / "body.md"
    body.write_text(text)
    # 読み直しは書いた本文をそのまま返す
    (tmp_path / "bin" / "gh").write_text(f'#!/bin/sh\necho called >> "$FAKE_GH_LOG"\n[ "$2" = view ] && cat {body}\nexit 0\n')
    p = subprocess.run(
        [sys.executable, str(EXP / "issue-body.py"), "set", "1", str(body), *extra], cwd=clone, env=env, capture_output=True, text=True
    )
    return p, json.loads(p.stdout.strip().splitlines()[-1])


def _commit_on_branch(clone, push):
    def run(*a):
        subprocess.run(a, cwd=clone, check=True, capture_output=True, text=True)

    run("git", "switch", "-q", "-c", "design/x")
    run("git", "add", "issues/local-only.md")
    run("git", "commit", "-qm", "design")
    if push:
        run("git", "push", "-q", "-u", "origin", "design/x")


def test_issue_body_accepts_paths_pushed_to_the_current_branch(tmp_path):
    # 設計 PR のブランチへ push 済みなら、起点のブランチへマージする前でも通し、そのブランチの blob の URL を案内する。
    # URL のリポジトリはブランチと同じ origin から決め、課題の置き場の --repo は使わない
    clone, env = _body_repo(tmp_path)
    _commit_on_branch(clone, push=True)
    subprocess.run(["git", "remote", "set-url", "origin", "https://github.com/o/r.git"], cwd=clone, check=True)
    p, out = _run_body(tmp_path, clone, env, "要求は `issues/local-only.md` にある\n", "--repo", "records/elsewhere")
    assert p.returncode == 0, p.stdout + p.stderr
    pushed = [i for i in out["items"] if i["result"] == "pushed_branch"]
    assert pushed == [
        {
            "number": 1,
            "result": "pushed_branch",
            "path": "issues/local-only.md",
            "ref": "origin/design/x",
            "url": "https://github.com/o/r/blob/design/x/issues/local-only.md",
        }
    ]


def test_issue_body_still_refuses_paths_committed_but_not_pushed(tmp_path):
    clone, env = _body_repo(tmp_path)
    _commit_on_branch(clone, push=False)
    p, out = _run_body(tmp_path, clone, env, "要求は `issues/local-only.md` にある\n")
    assert p.returncode == 1 and [i["path"] for i in out["items"]] == ["issues/local-only.md"]
    assert not (tmp_path / "gh.log").exists()


def test_review_terms_count_counts_findings_without_replies(tmp_path):
    """review-terms-count.py: 返信を除いた指摘を、語・定義と食い違いの語の並びで数える（1 件が両方に当たりうる）。"""
    comments = [
        {"id": 1, "body": "この語の定義が表と食い違う"},
        {"id": 2, "body": "終了コードが 2 になる"},
        {"id": 3, "body": "名前が揺れている"},
        {"id": 4, "in_reply_to_id": 1, "body": "定義を直した"},
    ]
    reviews = [
        {"body": "## 🤖 cross-review | round 1 | codex | COMMENT"},
        {"body": "## 🤖 cross-review | round 3 | kiro | APPROVE"},
        {"body": "ok"},
    ]
    (tmp_path / "c.json").write_text(json.dumps(comments, ensure_ascii=False))
    (tmp_path / "r.json").write_text(json.dumps(reviews, ensure_ascii=False))
    p = subprocess.run(
        [
            sys.executable,
            str(EXP / "review-terms-count.py"),
            "--comments-file",
            str(tmp_path / "c.json"),
            "--reviews-file",
            str(tmp_path / "r.json"),
        ],
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stderr
    m = json.loads(p.stdout.strip().splitlines()[-1])["metrics"]
    assert m == {"findings": 3, "terms": 2, "mismatch": 1, "rounds": 3}
