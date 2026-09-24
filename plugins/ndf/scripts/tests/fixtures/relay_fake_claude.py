#!/usr/bin/env python3
"""relay.py の試験で本物の claude の代わりに起動する偽の claude（#895）。

- `plugin ...` の副命令: 呼び出しを `FAKE_DIR/calls.jsonl` へ記録する。`list --json` は
  `ndf@mk` の要素を返す。版は `plugin update` が呼ばれた後なら `FAKE_VERSION_AFTER`、
  前なら `FAKE_VERSION`（既定 1.0.0）。`FAKE_FAIL` に `list` / `update` / `marketplace` を
  含めると、その副命令が終了コード 1 で終わる。`FAKE_HANG` に含めると終わらない。
  `FAKE_BREAK_ON_LIST=N` なら N 回目の `list` の後に自分の実行権限を外す（次の exec が失敗する）
- それ以外: 対話の区間として動く。起動の記録（argv・cwd・pid・環境の一部）を
  `FAKE_DIR/starts.jsonl` へ書き、端末を raw にして受けたバイトを `FAKE_DIR/input-<pid>` へ
  書く。行（`\\r` で終わる）ごとに次の命令を解く

| 行 | すること |
| --- | --- |
| `/exit` | 終了コード 0 で終わる（`FAKE_IGNORE_EXIT=1` なら無視する） |
| `mark <中身>` | `relay.py mark` を Stop hook と同じ形で呼び、`<中身>` の `ndf-next` のブロックを出す |
| `quit <n>` | 終了コード n で終わる |
| `size` | 端末の大きさを `FAKE_DIR/size-<pid>` へ書く |
| `tr <JSON>` | 会話の記録（`FAKE_DIR/transcript-<pid>.jsonl`）へ 1 行足す |
| `q open` / `q close` | `relay.py question open` / `close` を `AskUserQuestion` の hook と同じ形で呼び、出力を `FAKE_DIR/question-<pid>.jsonl` へ書く |
| `answer [mark <中身>]` | 質問に答えた後の Stop を模す。`relay.py mark` を呼び（中身が無ければブロック無し）、終了コード 0 で終わる |
| `unq` | 質問の印だけを消す |

`FAKE_EXIT_QUESTION=1` なら、最初の `/exit` で終わらずに質問の印を置く（書かれた `/exit` が質問の
答えの後に働く形を模す）。このとき SIGTERM を受けたら `FAKE_DIR/sigterm-<pid>` を書いて 143 で終わる。
受けたバイトは読んだ単位ごとに `FAKE_DIR/chunks-<pid>.jsonl` へも書く。

最初の位置引数が `mark ` で始まれば、起動の直後にその行を 1 度実行する。
"""
import fcntl
import json
import os
import signal
import struct
import subprocess
import sys
import termios
import time
import tty

D = os.environ["FAKE_DIR"]
RELAY = os.environ["FAKE_RELAY"]


def log(name, row):
    with open(os.path.join(D, name), "a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def plugin(args):
    log("calls.jsonl", {"args": args, "env_relay_dir": os.environ.get("NDF_RELAY_DIR")})
    kind = "list" if args[:1] == ["list"] else "marketplace" if args[:1] == ["marketplace"] else "update"
    if kind in os.environ.get("FAKE_HANG", "").split(","):
        time.sleep(3600)
    if kind in os.environ.get("FAKE_FAIL", "").split(","):
        sys.exit(1)
    if kind == "list":
        calls = [json.loads(x) for x in open(os.path.join(D, "calls.jsonl"))]
        updated = any(c["args"][:1] == ["update"] for c in calls)
        ver = os.environ.get("FAKE_VERSION_AFTER" if updated else "FAKE_VERSION") \
            or os.environ.get("FAKE_VERSION") or "1.0.0"
        print(json.dumps([{"id": "other@x", "version": "9"}, {"id": "ndf@mk", "version": ver}]))
        lists = sum(c["args"][:1] == ["list"] for c in calls)
        if str(lists) == os.environ.get("FAKE_BREAK_ON_LIST"):
            os.chmod(sys.argv[0], 0o644)  # 次の exec を失敗させる
    sys.exit(0)


def transcript():
    return os.path.join(D, f"transcript-{os.getpid()}.jsonl")


def do_mark(body):
    msg = f"次の区間:\n\n```ndf-next\n{body}\n```" if body is not None else "ブロックは無い"
    data = {"session_id": f"s{os.getpid()}", "transcript_path": transcript(), "cwd": os.getcwd(),
            "stop_hook_active": False, "last_assistant_message": msg, "background_tasks": []}
    subprocess.run([sys.executable, RELAY, "mark"], input=json.dumps(data), text=True)


PENDING = []


def handle(line):
    if line == "/exit":
        if os.environ.get("FAKE_EXIT_QUESTION") == "1" and not PENDING:
            PENDING.append(1)
            open(os.path.join(os.environ["NDF_RELAY_DIR"], "question"), "w").close()
        elif os.environ.get("FAKE_IGNORE_EXIT") != "1":
            sys.exit(0)
    elif line.startswith("mark "):
        do_mark(line[5:])
    elif line.startswith("quit "):
        sys.exit(int(line[5:]))
    elif line == "size":
        ws = fcntl.ioctl(0, termios.TIOCGWINSZ, b"\0" * 8)
        rows, cols = struct.unpack("HHHH", ws)[:2]
        with open(os.path.join(D, f"size-{os.getpid()}"), "w") as f:
            f.write(f"{rows} {cols}")
    elif line.startswith("tr "):
        with open(transcript(), "a") as f:
            f.write(line[3:] + "\n")
    elif line.startswith("q "):
        p = subprocess.run([sys.executable, RELAY, "question", line[2:]], input="{}", text=True,
                           capture_output=True)
        log(f"question-{os.getpid()}.jsonl", {"action": line[2:], "stdout": p.stdout, "code": p.returncode})
    elif line == "answer" or line.startswith("answer mark "):
        do_mark(line[12:] if line.startswith("answer mark ") else None)
        sys.exit(0)
    elif line == "unq":
        try:
            os.unlink(os.path.join(os.environ["NDF_RELAY_DIR"], "question"))
        except OSError:
            pass


def main():
    args = sys.argv[1:]
    if args[:1] == ["plugin"]:
        plugin(args[1:])
    if os.environ.get("FAKE_IGNORE_EXIT") == "1":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if os.environ.get("FAKE_EXIT_QUESTION") == "1":
        def on_term(*_):
            open(os.path.join(D, f"sigterm-{os.getpid()}"), "w").close()
            os._exit(143)
        signal.signal(signal.SIGTERM, on_term)
    log("starts.jsonl", {"argv": args, "cwd": os.getcwd(), "pid": os.getpid(),
                         "relay_dir": os.environ.get("NDF_RELAY_DIR"),
                         "claudecode": os.environ.get("CLAUDECODE"),
                         "depth": os.environ.get("NDF_RELAY_DEPTH")})
    open(transcript(), "a").close()
    if args and args[0].startswith("mark "):
        do_mark(args[0][5:])
    tty.setraw(0)
    buf = b""
    raw = open(os.path.join(D, f"input-{os.getpid()}"), "ab", buffering=0)
    while True:
        data = os.read(0, 1024)
        if not data:
            return
        raw.write(data)
        log(f"chunks-{os.getpid()}.jsonl", {"hex": data.hex()})
        buf += data
        while b"\r" in buf:
            line, buf = buf.split(b"\r", 1)
            handle(line.decode(errors="replace"))


if __name__ == "__main__":
    main()
