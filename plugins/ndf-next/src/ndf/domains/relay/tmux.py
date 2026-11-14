"""リレーハブが名前つきのラッパーを起動する tmux の呼び出し。

ソケットは `ndf-lanes`（`-L`）に分け、利用者の tmux のセッションと混ぜない。セッションの名前はラッパーの名前で、
中で `bin/ndf relay run --name <名前>` を打つ。tmux の実行ファイルは `NDF_RELAY_TMUX`（無ければ PATH の tmux）。
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any, Mapping, NamedTuple, Sequence

SOCKET = "ndf-lanes"


class Done(NamedTuple):
    ok: bool
    detail: str


class Tmux:
    """`runner` は `subprocess.run` と同じ形の関数（テストで差し替える）。"""

    def __init__(self, environ: Mapping[str, str], timeout: float, runner: Any = subprocess.run):
        self.exe = environ.get("NDF_RELAY_TMUX") or "tmux"
        self.timeout = timeout
        self.runner = runner

    def _run(self, args: Sequence[str]) -> Done:
        try:
            done = self.runner([self.exe, "-L", SOCKET, *args], capture_output=True, text=True, timeout=self.timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Done(False, f"tmux を打てない: {exc}")
        detail = (done.stderr or done.stdout).strip()[-300:]
        return Done(done.returncode == 0, detail)

    def has(self, name: str) -> bool:
        return self._run(["has-session", "-t", f"={name}"]).ok

    def start(self, name: str, cwd: Path, command: Sequence[str], env: Mapping[str, str]) -> Done:
        """名前つきのセッションを切り離して作る。`env` は `-e` で渡す。"""
        pairs = [arg for key, value in env.items() for arg in ("-e", f"{key}={value}")]
        line = " ".join(shlex.quote(c) for c in command)
        return self._run(["new-session", "-d", "-s", name, "-c", str(cwd), *pairs, line])

    def kill(self, name: str) -> Done:
        return self._run(["kill-session", "-t", f"={name}"])

    def send(self, name: str, text: str) -> Done:
        """入力欄へ文字を送って Enter を押す（`-l` で文字どおりに送る）。"""
        sent = self._run(["send-keys", "-t", f"={name}", "-l", text])
        return sent if not sent.ok else self._run(["send-keys", "-t", f"={name}", "Enter"])
