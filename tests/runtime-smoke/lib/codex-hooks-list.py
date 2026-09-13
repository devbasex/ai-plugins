#!/usr/bin/env python3
"""Codex の app-server に hooks/list を 1 度だけ問い合わせ、応答の result を標準出力へ書く。

判定はしない。受け取った応答をそのまま渡し、合否は呼び出し側（assert-hook-definitions.sh）が決める。
CODEX_HOME にマーケットプレイスの登録とプラグインの導入が済んでいることを前提とする。

終了コード:
  0  応答を受け取った
  1  応答が error を持つ / result.data が無い / app-server が応答前に終わった / 時間切れ
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from collections import deque

TIMEOUT_SECONDS = 30
HOOKS_LIST_ID = 2


def fail(message, stderr_tail):
    sys.stderr.write(message + "\n")
    if stderr_tail:
        sys.stderr.write("app-server stderr (tail):\n")
        sys.stderr.write("".join(stderr_tail))
    sys.exit(1)


def await_hooks_list(lines, reader_done, stderr_tail):
    deadline = time.monotonic() + TIMEOUT_SECONDS
    received = []
    while True:
        while lines:
            line = lines.popleft()
            received.append(line)
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") != HOOKS_LIST_ID:
                continue
            body = "".join(received)
            if "error" in message:
                fail("hooks/list returned an error:\n" + body, stderr_tail)
            result = message.get("result")
            if not isinstance(result, dict) or "data" not in result:
                fail("hooks/list response has no result.data:\n" + body, stderr_tail)
            json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
            return
        if reader_done.is_set() and not lines:
            fail("codex app-server exited before answering hooks/list:\n"
                 + "".join(received), stderr_tail)
        if time.monotonic() > deadline:
            fail(f"codex app-server did not answer hooks/list within {TIMEOUT_SECONDS}s:\n"
                 + "".join(received), stderr_tail)
        time.sleep(0.05)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", default=os.getcwd())
    args = parser.parse_args()

    proc = subprocess.Popen(
        ["codex", "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=args.cwd,
    )

    # 標準エラーは読み続けないとパイプが詰まる。失敗の説明に使う末尾だけを残す。
    stderr_tail = deque(maxlen=20)
    threading.Thread(
        target=lambda: stderr_tail.extend(proc.stderr), daemon=True
    ).start()

    def send(message):
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    lines = deque()
    reader_done = threading.Event()

    def read_stdout():
        for line in proc.stdout:
            lines.append(line)
        reader_done.set()

    threading.Thread(target=read_stdout, daemon=True).start()

    try:
        send({"id": 1, "method": "initialize",
              "params": {"clientInfo": {"name": "runtime-smoke", "version": "0.0.0"}}})
        send({"method": "initialized"})
        send({"id": HOOKS_LIST_ID, "method": "hooks/list", "params": {"cwds": [args.cwd]}})
        await_hooks_list(lines, reader_done, stderr_tail)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


if __name__ == "__main__":
    main()
