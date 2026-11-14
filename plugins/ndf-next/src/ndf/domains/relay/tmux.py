"""リレーハブが名前つきのラッパーを起動する tmux の呼び出し。

ソケットは `ndf-lanes`（`-L`）に分け、利用者の tmux のセッションと混ぜない。セッションの名前はラッパーの名前で、
中で `bin/ndf relay run --name <名前>` を打つ。tmux の実行ファイルは `NDF_RELAY_TMUX`（無ければ PATH の tmux）。

環境の値はプロセスの引数に載せない（`ps` で読める）。`-e` で渡すのは環境変数の表で秘密でない名前だけで、
ほかはプロセスの環境として tmux へ渡す（初めて立てる tmux のサーバーはそれを引き継ぐ）。
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any, Mapping, NamedTuple, Optional, Sequence

from ndf.contracts import facts

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

    def _run(self, args: Sequence[str], env: Optional[Mapping[str, str]] = None) -> Done:
        extra = {"env": dict(env)} if env is not None else {}
        try:
            done = self.runner([self.exe, "-L", SOCKET, *args], capture_output=True, text=True, timeout=self.timeout, check=False, **extra)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Done(False, f"tmux を打てない: {exc}")
        detail = (done.stderr or done.stdout).strip()[-300:]
        return Done(done.returncode == 0, detail)

    def has(self, name: str) -> bool:
        return self._run(["has-session", "-t", f"={name}"]).ok

    def start(self, name: str, cwd: Path, command: Sequence[str], env: Mapping[str, str]) -> Done:
        """名前つきのセッションを切り離して作る。秘密でない表の名前だけを `-e` で、残りはプロセスの環境で渡す。"""
        return self._run(new_session_args(name, cwd, command, env), env)

    def kill(self, name: str) -> Done:
        return self._run(["kill-session", "-t", f"={name}"])

    def send(self, name: str, text: str) -> Done:
        """入力欄へ文字を送って Enter を押す（`-l` で文字どおりに送る）。"""
        sent = self._run(["send-keys", "-t", f"={name}", "-l", text])
        return sent if not sent.ok else self._run(["send-keys", "-t", f"={name}", "Enter"])


def new_session_args(name: str, cwd: Path, command: Sequence[str], env: Mapping[str, str]) -> list[str]:
    """`new-session` の引数。値を載せるのは環境変数の表で `secret` が偽の名前だけ。"""
    shown = set(facts.ids("env", secret=False))
    pairs = [arg for key, value in env.items() if key in shown for arg in ("-e", f"{key}={value}")]
    line = " ".join(shlex.quote(c) for c in command)
    return ["new-session", "-d", "-s", name, "-c", str(cwd), *pairs, line]
