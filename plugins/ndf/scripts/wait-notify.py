#!/usr/bin/env python3
"""利用者の回答か承認を待つときだけ Slack へ知らせる（#821）。

    python3 wait-notify.py --runtime claude|codex|kiro   < フック入力の JSON
    python3 wait-notify.py --send <JSON>                 # 切り離した子（本文の組み立てと送信）

hook の判定と重複の抑止は `scripts/hook.py` の中の関数（`hook_lib/wait_notify.py`）が持つ（#1142 の決定 20）。
`--runtime` はその古いエントリポイントで、Kiro の stop の hook が呼ぶ。hook の用意済みの環境（`hook-env.py`）の
python で動いていなければその python で `hook.py` を起動し直し、環境が無ければ何もせずに終わる。
どの失敗も終了コード 0 で、標準出力へは何も書かない（`PermissionRequest` の判断に使われないため）。
`DEBUG_SLACK_NOTIFY=true` のときだけ `~/.claude/logs/wait-notify-<日付>.log` へ理由を書く。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
sys.path.insert(0, str(HERE))
import proc  # noqa: E402  子プロセスの起動（#1142 の L0）
import wait_notice as wn  # noqa: E402
from hook_lib.wait_notify import RUNTIMES, log  # noqa: E402

GH_TIMEOUT = 5
SLACK_TIMEOUT = 10
DELETE_DELAY = 0.5


# ---------------------------------------------------------------------------
# 子: 本文の組み立てと送信
# ---------------------------------------------------------------------------

def _stdout_of(cmd: list[str], cwd: str, timeout: float) -> str | None:
    """`cmd` の標準出力（前後の空白を落とす）。失敗・起動できない・時間切れは `None`。"""
    try:
        r = proc.run(cmd, cwd=cwd, check=False, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def repo_name(cwd: str) -> str:
    top = _stdout_of(["git", "rev-parse", "--show-toplevel"], cwd, GH_TIMEOUT)
    return os.path.basename(top) if top else (os.environ.get("GIT_REPO") or "unknown")


def current_pr(cwd: str) -> str | None:
    out = _stdout_of(["gh", "pr", "view", "--json", "url", "--jq", ".url"], cwd, GH_TIMEOUT)
    return out if out and out.startswith("http") else None


def slack_api(method: str, data: dict) -> dict | None:
    """Slack の Web API を 1 回呼ぶ（`lib/notify.py`）。失敗は None で、理由はログへ書く。"""
    import notify  # 切り離した子（--send）の中でだけ読む（deps.require("notify") の後）
    return notify.slack_call(method, data, timeout=SLACK_TIMEOUT,
                             on_error=lambda m, why: log("slack error:", m, why))


def build_notice(payload: dict) -> wn.Notice:
    runtime, kind, cwd = payload["runtime"], payload["kind"], payload["cwd"]
    wait = wn.Wait(kind, payload.get("excerpt", ""), payload.get("key", ""))
    locator = wn.build_locator(runtime, payload.get("hook_input") or {}, dict(os.environ), socket.gethostname(), cwd)
    slug = wn.github_slug(_stdout_of(["git", "remote", "get-url", "origin"], cwd, GH_TIMEOUT) or "")
    redmine = os.environ.get("REDMINE_URL") or None
    text = payload.get("search", "")
    urls = wn.extract_urls(kind, text, slug, redmine)
    if kind == wn.APPROVAL and not any(label == "PR" for label, _ in urls):
        pr = current_pr(cwd)
        if pr:
            urls = wn.extract_urls(kind, text, slug, redmine, pr_fallback=pr)
    return wn.Notice(wait, locator, repo_name(cwd), tuple(urls))


def send_notice(payload: dict) -> None:
    notice = build_notice(payload)
    clean = notice.text()
    if clean is None:
        log("skip: no mark for kind", payload.get("kind"))
        return
    channel = os.environ.get("SLACK_CHANNEL_ID", "")
    mention = os.environ.get("SLACK_USER_MENTION", "")
    if not mention:
        slack_api("chat.postMessage", {"channel": channel, "text": clean})
        return
    first = slack_api("chat.postMessage", {"channel": channel, "text": notice.text(mention)})
    if not first:
        return
    time.sleep(DELETE_DELAY)
    slack_api("chat.postMessage", {"channel": channel, "text": clean})
    if first.get("ts"):
        slack_api("chat.delete", {"channel": channel, "ts": first["ts"]})


def _read_stdin() -> dict | None:
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return None
        data = json.loads(sys.stdin.read())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def run_hook(runtime: str) -> None:
    """古いエントリポイント（`--runtime`）。hook の環境の python で `hook.py` の関数を呼ぶ。"""
    import hook_python
    if not hook_python.hook_packages_importable():
        hook_python.exec_hook_python(str(HERE / "hook.py"), ["wait-notify", "--runtime", runtime])
        log("skip: the hook environment is not prepared")
        return
    from hook_lib import wait_notify
    wait_notify.hook(runtime, _read_stdin())


def main(argv: list[str]) -> int:
    try:
        if len(argv) >= 2 and argv[0] == "--send":
            import deps  # 切り離した子は hook の経路ではないので、uv の環境へ起動し直してよい
            deps.require("notify")
            send_notice(json.loads(argv[1]))
        elif len(argv) >= 2 and argv[0] == "--runtime" and argv[1] in RUNTIMES:
            run_hook(argv[1])
        else:
            log("skip: bad arguments", argv)
    except Exception as exc:  # noqa: BLE001 — フックを失敗させない（I9〜I12）
        log("error:", type(exc).__name__, exc)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
