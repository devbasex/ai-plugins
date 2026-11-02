"""tmux の包み（#2346 の決定 3・決定 11）。tmux を呼ぶのはこのモジュールだけである。

名前つきのラッパー 1 本に tmux のセッション 1 つを割り当て、専用のソケット `-L ndf-relay` に置く（利用者の tmux
サーバに触れない）。使う操作は `new-session`・`has-session`・`list-sessions`・`kill-session` と、`attach-session`
への exec の 5 つだけである。実行ファイルは `NDF_RELAY_TMUX`（既定は PATH の `tmux`）。ソケットの置き場は tmux の
規則のまま（`TMUX_TMPDIR`）で、テストは `TMUX_TMPDIR` を使い捨てのディレクトリへ向ける。

tmux 3.6 で確かめたこと（2026-11-02）: `new-session -d -s <名前>` の重複は終了コード 1 と `duplicate session`、
`-e NAME=値` は window の中へ届く、`-t =<名前>` は名前の完全一致、最後のセッションを消すとサーバも終わる。
"""

from __future__ import annotations

import os
import shutil
import subprocess

SOCKET = "ndf-relay"
_TIMEOUT = 5


def binary() -> str | None:
    """tmux の実行ファイル。無ければ None（今の 1 本ずつの起動に戻る。AC23）。"""
    return shutil.which(os.environ.get("NDF_RELAY_TMUX") or "tmux")


def _env() -> dict:
    # 利用者の tmux の中から呼んでも、入れ子の拒否（`sessions should be nested with care`）に当たらないよう外す
    return {k: v for k, v in os.environ.items() if k != "TMUX"}


def _tmux(*args: str):
    """tmux を 1 回呼ぶ。実行ファイルが無い・起動できない・時間切れは None。"""
    exe = binary()
    if exe is None:
        return None
    try:
        return subprocess.run(
            [exe, "-L", SOCKET, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, env=_env(), timeout=_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError):
        return None


def new_session(name: str, cwd: str, argv: list[str], env: dict[str, str]) -> str | None:
    """端末の無いセッション `name` を作り、`argv` を `cwd` で動かす。`env` は window へ足す変数。失敗の理由か None。"""
    opts = [arg for k, v in env.items() for arg in ("-e", f"{k}={v}")]
    p = _tmux("new-session", "-d", "-s", name, "-c", cwd, "-x", "200", "-y", "50", *opts, "--", *argv)
    if p is None:
        return "tmux を起動できない"
    if p.returncode != 0:
        return (p.stderr or p.stdout).strip() or f"tmux new-session が終了コード {p.returncode}"
    return None


def has_session(name: str) -> bool:
    p = _tmux("has-session", "-t", f"={name}")
    return p is not None and p.returncode == 0


def sessions() -> dict[str, dict]:
    """在るセッションの {名前: {attached, activity, pid}}。サーバが無ければ空。"""
    p = _tmux("list-sessions", "-F", "#{session_name}\t#{session_attached}\t#{window_activity}\t#{pane_pid}")
    if p is None or p.returncode != 0:
        return {}
    out = {}
    for line in p.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        name, attached, activity, pid = parts
        out[name] = {
            "attached": attached.isdigit() and int(attached) > 0,
            "activity": float(activity) if activity.isdigit() else None,
            "pid": int(pid) if pid.isdigit() else None,
        }
    return out


def kill_session(name: str) -> bool:
    p = _tmux("kill-session", "-t", f"={name}")
    return p is not None and p.returncode == 0


def exec_attach(name: str):
    """打った端末をセッション `name` へ結び付ける（戻らない）。"""
    exe = binary() or "tmux"
    os.execve(exe, [exe, "-L", SOCKET, "attach-session", "-t", f"={name}"], _env())
