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
TEXT_SUFFIXES = {".py", ".sh", ".md", ".json", ".txt", ".toml", ".yaml", ".yml"}


def test_stable_side_does_not_reference_experimental():
    """既定の振る舞いに試行が漏れないよう、プラグインの安定側は experimental/ を参照しない。"""
    hits = []
    for p in PLUGIN.rglob("*"):
        if not p.is_file() or p.suffix not in TEXT_SUFFIXES or EXP in p.parents:
            continue
        if p == Path(__file__) or "__pycache__" in p.parts:
            continue
        try:
            text = p.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if "scripts/experimental" in text or ("experimental/" in text and p.suffix in {".py", ".sh"}):
            hits.append(str(p.relative_to(PLUGIN)))
    assert hits == []


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
    assert item["position"] == "not-running"   # 中継の pid が無い
    assert item["started_by"].startswith("手")
    assert item["previous_end"]["ended_by"] == "no-mark"
    assert item["previous_dir"] == str(prev)
