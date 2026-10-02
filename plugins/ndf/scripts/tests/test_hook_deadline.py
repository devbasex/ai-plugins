"""hook の締め切り（#1340 の AC11〜AC13、I11〜I13）。

`git` の応答が遅いリポジトリを、PATH の先頭に置いた眠り続ける `git` で模す（前提 10）。Claude Code と Codex の起動の形
（`hook.py`・`hook.py --runtime codex`）と、agy と Kiro の起動の形（`worktree-guard.sh`）が、締め切りの後に通して
終わることを確かめる。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
PLUGIN = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import hook  # noqa: E402
from hook_lib import deadline  # noqa: E402

pytestmark = pytest.mark.skipif(not hasattr(__import__("signal"), "setitimer"), reason="setitimer が無い環境では締め切りを掛けない")

# 起動（python の起動と import）に許す時間。締め切りとの和が最も短い hook の上限（5 秒）を超えない
STARTUP_ALLOWANCE = deadline.MARGIN_SECONDS


@pytest.fixture()
def slow_git(tmp_path):
    """呼ばれると眠り続ける `git` を PATH の先頭に置いた環境と、作業ディレクトリを返す。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git = bin_dir / "git"
    git.write_text("#!/bin/sh\nexec sleep 30\n")
    git.chmod(0o755)
    work = tmp_path / "repo"
    work.mkdir()
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["XDG_STATE_HOME"] = str(tmp_path / "state")
    env["XDG_CACHE_HOME"] = str(tmp_path / "cache")
    env["NDF_HOOK_PYTHON"] = sys.executable
    return env, work


def _payload(work: Path, tool: str = "Bash") -> str:
    tool_input = {"command": "echo hi > out.txt"} if tool == "Bash" else {"file_path": str(work / "a.py")}
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "cwd": str(work), "session_id": "t-deadline"})


@pytest.mark.parametrize(
    "argv",
    [
        [sys.executable, str(SCRIPTS / "hook.py")],
        [sys.executable, str(SCRIPTS / "hook.py"), "--runtime", "codex"],
        ["bash", str(SCRIPTS / "worktree-guard.sh")],
    ],
    ids=["claude", "codex", "worktree-guard.sh"],
)
@pytest.mark.parametrize("tool", ["Bash", "Edit"])
def test_a_slow_git_ends_within_the_hook_limit(slow_git, argv, tool):
    """AC11・AC12: git が終わらなくても締め切りの後に通し、終了コード 0・拒否なし・飛ばした判定の名前を 1 行出す。"""
    env, work = slow_git
    started = time.monotonic()
    p = subprocess.run(argv, input=_payload(work, tool), capture_output=True, text=True, env=env, cwd=work, timeout=30)
    elapsed = time.monotonic() - started
    assert p.returncode == 0
    assert elapsed < deadline.DEADLINE_SECONDS + STARTUP_ALLOWANCE
    assert '"deny"' not in p.stdout
    lines = [ln for ln in p.stderr.splitlines() if ln.startswith("[ndf hook]")]
    assert len(lines) == 1 and "worktree-guard" in lines[0]


def test_a_finished_result_survives_a_later_timeout(monkeypatch, capsys):
    """I11: 締め切りまでに終わった判定の結果は出し、残りの判定の名前を標準エラーへ出す。"""
    from hook_lib import token_guard, worktree

    monkeypatch.setattr(deadline, "DEADLINE_SECONDS", 0.2)
    note = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "worktree の案内"}}
    monkeypatch.setattr(worktree, "notice", lambda ev: note)
    monkeypatch.setattr(token_guard, "decision", lambda ev: time.sleep(5))
    raw = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": os.getcwd()}
    started = time.monotonic()
    out = hook.dispatch("", "claude", raw)
    assert time.monotonic() - started < 2
    assert out == note
    assert "token-guard" in capsys.readouterr().err


def test_a_quick_hook_is_not_interrupted(monkeypatch, capsys):
    """I12: 締め切りの中で終われば結果は変わらず、何も出さない。割り込みも残さない。"""
    import signal

    from hook_lib import worktree

    monkeypatch.setattr(worktree, "notice", lambda ev: None)
    raw = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": {"file_path": "x"}, "cwd": os.getcwd()}
    assert hook.dispatch("", "codex", raw) is None
    assert capsys.readouterr().err == ""
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)


# ---------------- I13: 締め切り + 余裕 ≤ hook の上限 ----------------


def _hook_timeouts(path: Path) -> list[float]:
    """hook の定義のうち、hook.py か worktree-guard.sh を PreToolUse で起動するものの上限（秒）。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    found = []

    def walk(node, under_pre: bool) -> None:
        if isinstance(node, dict):
            cmd = node.get("command")
            if under_pre and isinstance(cmd, str) and ("hook.py" in cmd or "worktree-guard.sh" in cmd) and "timeout" in node:
                found.append(float(node["timeout"]))
            for k, v in node.items():
                walk(v, under_pre or k == "PreToolUse")
        elif isinstance(node, list):
            for v in node:
                walk(v, under_pre)

    walk(data, False)
    return found


@pytest.mark.parametrize("rel", ["hooks/claude.json", "hooks/codex.json", "dev.agy/hooks.json"])
def test_the_hook_limit_leaves_the_margin(rel):
    limits = _hook_timeouts(PLUGIN / rel)
    assert limits
    assert all(deadline.DEADLINE_SECONDS + deadline.MARGIN_SECONDS <= t for t in limits)


def test_the_kiro_prompt_hook_limit_leaves_the_margin():
    text = (PLUGIN / "dev.kiro" / "install.sh").read_text(encoding="utf-8")
    m = re.search(r'hooks\["userPromptSubmit"\]\s*=\s*\[.*?guard_script.*?"timeout_ms":\s*(\d+)', text, re.S)
    assert m
    assert deadline.DEADLINE_SECONDS + deadline.MARGIN_SECONDS <= int(m.group(1)) / 1000
