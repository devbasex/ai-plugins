"""hook の所要（AC23）。1 万ファイルのリポジトリで SessionStart 1 秒・PreToolUse 0.2 秒以内。"""
import json
import os
import resource
import subprocess
import sys

from serena_lsp_testlib import CLI, PLUGIN


def _big_repo(root):
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for i in range(100):
        d = root / f"pkg{i}"
        d.mkdir()
        for j in range(100):
            (d / f"m{j}.{'py' if j % 3 else 'ts'}").write_text("")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    (root / ".serena").mkdir()
    (root / ".serena/project.yml").write_text("language_servers:\n- python\nmcp_serena_excluded: []\n")
    return root


def _time(event, payload, env):
    """hook の子プロセス（その子を含む）が使った CPU 時間を返す。

    壁時計は並列の実行で CPU の順番待ちを含み、揺れる。
    """
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    proc = subprocess.run([sys.executable, str(CLI), "hook", event, "--client", "claude-code"],
                          input=json.dumps(payload), capture_output=True, text=True, env=env)
    assert proc.returncode == 0
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    return (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)


def test_hooks_are_fast_on_ten_thousand_files(tmp_path):
    root = _big_repo(tmp_path / "big")
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(PLUGIN), HOME=str(tmp_path / "home"),
               XDG_STATE_HOME=str(tmp_path / "state"))
    starts = [_time("session-start", {"cwd": str(root)}, env) for _ in range(5)]
    reads = [_time("pre-tool-use", {"session_id": "p", "cwd": str(root), "tool_name": "Read",
                                    "tool_input": {"file_path": str(root / "pkg1/m1.py")}}, env)
             for _ in range(20)]
    assert max(starts) < 1.0, starts
    assert max(reads) < 0.2, reads
