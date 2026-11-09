"""hook.py を PreToolUse の入力で子のプロセスとして起動し、拒否の理由を返す（guard のテストの共通の補助）。

`NDF_` で始まる環境変数を外し、`CLAUDE_PLUGIN_DATA` をテストの一時ディレクトリへ向けて起動する。
拒否されたときは理由を、それ以外（出力なし・許可）は None を返す。
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "hook.py"


def deny_reason(data, tmp_path, env=None) -> str | None:
    e = {k: v for k, v in os.environ.items() if not k.startswith("NDF_")}
    e["CLAUDE_PLUGIN_DATA"] = str(tmp_path / "plugin-data")
    e.update(env or {})
    proc = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(data), capture_output=True, text=True, env=e, timeout=20)
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return None
    spec = json.loads(proc.stdout).get("hookSpecificOutput") or {}
    return spec.get("permissionDecisionReason") if spec.get("permissionDecision") == "deny" else None
