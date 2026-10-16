"""hook-trial の sdk の引数の比較（ht_sdk.check_args）の現状固定テスト。

SDK の呼び出し（options・drain）と今の引数（supervise_lib.claude.claude_cmd）を偽物へ差し替え、
比較と結果の形だけを固定する。偽の drain は check_args が書いた記録用の CLI を実際に起動する。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "experimental" / "hook-trial"))
import ht_sdk  # noqa: E402
from supervise_lib import claude as cl  # noqa: E402

MCP = {"mcpServers": {"serena": {"command": "x"}}}


def fake_claude_cmd(system, tools, cwd, full=False, serena=False, resume=None):
    if full:
        head = ["claude", "-p", "--output-format", "json", "--permission-mode", "acceptEdits", "--allowed-tools", "A,B"]
        return head + ["--append-system-prompt", system, "--resume", resume]
    cmd = ["claude", "-p", "--output-format", "json", "--system-prompt", system, "--strict-mcp-config", "--setting-sources", ""]
    if not tools:
        return cmd + ["--tools", ""]
    cmd += ["--mcp-config", json.dumps({**MCP, "now_only": 1})]
    return cmd + ["--tools", "Read,Edit", "--allowed-tools", "Read,Edit,mcp__serena", "--permission-mode", "acceptEdits", "--add-dir", cwd]


def run_check_args(tmp_path, monkeypatch, sdk_argv: dict):
    """sdk_argv: 種類 → SDK が CLI へ渡す引数（None なら CLI を起動せずに落ちる）。"""
    calls = {"options": [], "drain": [], "claude_cmd": []}

    def fake_options(kind, system, cwd, cli, resume=None, env=None):
        calls["options"].append((kind, system, cwd, cli, resume))
        return {"kind": kind, "cli": cli}

    async def fake_drain(prompt, opts, timeout, transport=None):
        calls["drain"].append((prompt, opts["kind"], timeout))
        argv = sdk_argv[opts["kind"]]
        if argv is None:
            raise RuntimeError("CLI を起動できない")
        assert os.access(opts["cli"], os.X_OK)
        subprocess.run([opts["cli"], *argv], check=False)
        raise RuntimeError("偽物は応答しない")

    def recording_claude_cmd(system, tools, cwd, full=False, serena=False, resume=None):
        calls["claude_cmd"].append((system, tools, cwd, full, serena, resume))
        return fake_claude_cmd(system, tools, cwd, full, serena, resume)

    monkeypatch.setattr(ht_sdk, "options", fake_options)
    monkeypatch.setattr(ht_sdk, "drain", fake_drain)
    monkeypatch.setattr(cl, "claude_cmd", recording_claude_cmd)
    items, bad = [{"kind": "before"}], ["前の失敗"]
    assert ht_sdk.check_args(tmp_path, items, bad) is None
    return items, bad, calls


def test_check_args_same(tmp_path, monkeypatch):
    work = str(tmp_path)
    sdk_argv = {
        "minimal": ["--output-format", "stream-json", "--verbose", "--system-prompt", "SYS", "--strict-mcp-config"]
        + ["--setting-sources=", "--input-format", "stream-json", "--tools", ""],
        "work": ["--system-prompt", "SYS", "--strict-mcp-config", "--setting-sources=", "--mcp-config", json.dumps({**MCP, "sdk_only": 2})]
        + ["--tools", "Edit,Read", "--allowedTools", "mcp__serena,Read,Edit", "--permission-mode", "acceptEdits", "--add-dir", work],
        "full": ["--allowedTools", "B,A", "--append-system-prompt", "SYS", "--resume=sess-1", "--permission-mode", "acceptEdits"]
        + ["--setting-sources=user,project,local"],
    }
    items, bad, calls = run_check_args(tmp_path, monkeypatch, sdk_argv)
    assert bad == ["前の失敗"]
    assert items == [
        {"kind": "before"},
        {
            "kind": "sdk_args",
            "name": "minimal",
            "result": "same",
            "missing": [],
            "differs": [],
            "sdk_only": ["--input-format", "--verbose"],
        },
        {"kind": "sdk_args", "name": "work", "result": "same", "missing": [], "differs": [], "sdk_only": []},
        {"kind": "sdk_args", "name": "full", "result": "same", "missing": [], "differs": [], "sdk_only": ["--setting-sources"]},
    ]
    assert calls["options"] == [
        ("minimal", "SYS", work, f"{work}/rec-minimal.sh", None),
        ("work", "SYS", work, f"{work}/rec-work.sh", None),
        ("full", "SYS", work, f"{work}/rec-full.sh", "sess-1"),
    ]
    assert calls["drain"] == [("hi", "minimal", 20), ("hi", "work", 20), ("hi", "full", 20)]
    assert calls["claude_cmd"] == [
        ("SYS", None, work, False, False, None),
        ("SYS", cl.WORK_TOOLS, work, False, True, None),
        ("SYS", None, work, True, False, "sess-1"),
    ]
    assert (tmp_path / "argv-full.txt").read_text(encoding="utf-8").splitlines() == sdk_argv["full"]


def test_check_args_differs(tmp_path, monkeypatch):
    sdk_argv = {
        "minimal": None,
        "work": ["--system-prompt", "SYS", "--strict-mcp-config", "--setting-sources=", "--mcp-config", json.dumps({"mcpServers": {}})]
        + ["--tools", "Read", "--allowed-tools", "Read,Edit,mcp__serena", "--permission-mode", "plan", "--debug"],
        "full": ["--allowed-tools", "A,B", "--append-system-prompt", "SYS", "--permission-mode", "acceptEdits", "--resume"],
    }
    items, bad, _ = run_check_args(tmp_path, monkeypatch, sdk_argv)
    assert bad == ["前の失敗", "引数（minimal）", "引数（work）", "引数（full）"]
    assert items[1:] == [
        {
            "kind": "sdk_args",
            "name": "minimal",
            "result": "differs",
            "missing": ["--system-prompt", "--strict-mcp-config", "--setting-sources", "--tools"],
            "differs": [],
            "sdk_only": [],
        },
        {
            "kind": "sdk_args",
            "name": "work",
            "result": "differs",
            "missing": ["--add-dir"],
            "differs": ["--mcp-config", "--tools（Read,Edit / Read）", "--permission-mode（'acceptEdits' / 'plan'）"],
            "sdk_only": ["--debug"],
        },
        {
            "kind": "sdk_args",
            "name": "full",
            "result": "differs",
            "missing": [],
            "differs": ["--resume（'sess-1' / True）"],
            "sdk_only": [],
        },
    ]
    assert not (tmp_path / "argv-minimal.txt").exists()
