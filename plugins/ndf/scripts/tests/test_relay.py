"""カットポイントで claude を起動し直すラッパー（#895）。

`relay.py` の副命令:

- `mark`: Stop hook の本体。最後の応答の `ndf-next` のブロックを合図 `next.json` へ写す
- `run`: 端末の前景に常駐し、claude を擬似端末の子として起動する。要らなければ素通しする
- `stop`: 動いているラッパーに停止の合図を置く
- `install` / `uninstall` / `status`: `/ndf:install-wrapper` の本体（#928）
- `startup`: SessionStart hook の本体。在る複製を置き直し、10.17.4〜10.17.6 の自動の囲みを 1 度だけ知らせる
- `question open` / `close`: 質問の表示中の合図（関門を越えない守り）

**利用者の手元の設定は書き換えない。** 導入の副命令と `run` は、テストごとの一時の HOME と
`XDG_*`・`CLAUDE_CONFIG_DIR` の下でだけ動かす（`isolated_env`）。
"""
from __future__ import annotations

import ast
import errno
import fcntl
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
RELAY = ROOT / "scripts" / "relay.py"
sys.path.insert(0, str(ROOT / "scripts"))
from relay_lib import claude as relay_claude  # noqa: E402
from relay_lib import common as relay_common  # noqa: E402
from relay_lib import install as relay_install  # noqa: E402
from relay_lib import mark as relay_mark  # noqa: E402
from relay_lib import proc as relay_proc  # noqa: E402
from relay_lib import record as relay_record  # noqa: E402
from relay_lib import run as relay_run  # noqa: E402
from relay_lib import shellrc as relay_shellrc  # noqa: E402
from relay_lib import version_dir as relay_version_dir  # noqa: E402


def isolated_env(tmp_path, **extra):
    """一時の HOME と XDG_* だけを持つ環境。本物の HOME を指さないことを確かめてから返す。

    **`DEVBASE_SHELLRC_DIR` も落とす。** 残すと install / uninstall が本物の置き場の `ndf-relay.sh` を書き換え、消す。
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    e = {k: v for k, v in os.environ.items()
         if not k.startswith(("NDF_", "XDG_", "CLAUDE", "DEVBASE_")) and k not in ("ZDOTDIR",)}
    e["HOME"] = str(home)
    e.update({k: str(v) for k, v in extra.items()})
    real_home = os.path.expanduser("~")
    assert e["HOME"] != real_home
    return e


def fence(body, info="ndf-next", ticks=3):
    return f"{'`' * ticks}{info}\n{body}\n{'`' * ticks}"


# ---------------------------------------------------------------- mark（AC3 / AC4 / AC4b / AC24）


class Relay:
    """動いているラッパーに見立てた作業ディレクトリ。`relay.lock` を持ち、`child.pid` を書く。"""

    def __init__(self, tmp_path, child_pid=None):
        self.dir = tmp_path / "relay-dir"
        self.dir.mkdir(mode=0o700)
        self.lock = open(self.dir / "relay.lock", "a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # mark の親はこのテストのプロセスなので、既定では直接の子の claude に当たる
        (self.dir / "child.pid").write_text(str(child_pid or os.getpid()))

    def release(self):
        fcntl.flock(self.lock, fcntl.LOCK_UN)
        self.lock.close()

    @property
    def next(self):
        return self.dir / "next.json"


@pytest.fixture()
def relay(tmp_path):
    r = Relay(tmp_path)
    yield r
    if not r.lock.closed:
        r.release()


def stop_input(message, **extra):
    d = {"session_id": "sess-1", "transcript_path": "/tmp/t.jsonl", "cwd": "/work/x",
         "stop_hook_active": False, "last_assistant_message": message,
         "background_tasks": []}
    d.update(extra)
    return json.dumps(d)


def mark(env_dir, data, env=None, argv0=None):
    e = {k: v for k, v in os.environ.items() if not k.startswith("NDF_")}
    if env_dir is not None:
        e["NDF_RELAY_DIR"] = str(env_dir)
    if env:
        e.update(env)
    cmd = [sys.executable, str(RELAY), "mark"]
    return subprocess.run(cmd, input=data, capture_output=True, text=True, env=e, timeout=20)


def quiet_ok(proc):
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    assert proc.stderr == ""


def test_mark_writes_next(relay):
    msg = "承認を受けて設計をマージした。\n\n" + fence("/goal /ndf:development-workflow #895")
    quiet_ok(mark(relay.dir, stop_input(msg)))
    data = json.loads(relay.next.read_text())
    assert set(data) == {"command", "cwd", "session_id", "transcript_path", "written_at"}
    assert data["command"] == "/goal /ndf:development-workflow #895"
    assert data["cwd"] == "/work/x"
    assert data["session_id"] == "sess-1"
    assert data["transcript_path"] == "/tmp/t.jsonl"
    assert data["written_at"].endswith("Z")


def test_mark_multiline_command(relay):
    quiet_ok(mark(relay.dir, stop_input(fence("/goal a\n\n二行目"))))
    assert json.loads(relay.next.read_text())["command"] == "/goal a\n\n二行目"


def test_mark_ignores_quoted_block(relay):
    msg = "例:\n\n" + fence(fence("/goal x"), info="markdown", ticks=4)
    quiet_ok(mark(relay.dir, stop_input(msg)))
    assert not relay.next.exists()


def test_mark_writes_even_when_stop_hook_active(relay):
    quiet_ok(mark(relay.dir, stop_input(fence("/goal x"), stop_hook_active=True)))
    assert relay.next.exists()


@pytest.mark.parametrize("case", ["no-dir", "not-running", "zero", "two", "quoted-only",
                                  "broken-json", "not-direct-child", "text-info"])
def test_mark_does_nothing(tmp_path, relay, case):
    msg = fence("/goal x")
    env_dir = relay.dir
    data = stop_input(msg)
    if case == "no-dir":
        env_dir = None
    elif case == "not-running":
        relay.release()
    elif case == "zero":
        data = stop_input("ブロックは無い")
    elif case == "two":
        data = stop_input(msg + "\n\n" + fence("/goal y"))
    elif case == "quoted-only":
        data = stop_input(fence(msg, info="", ticks=4))
    elif case == "broken-json":
        data = "{not json"
    elif case == "text-info":
        data = stop_input(fence("/goal x", info="text"))
    elif case == "not-direct-child":
        (relay.dir / "child.pid").write_text("1")
    quiet_ok(mark(env_dir, data))
    assert not relay.next.exists()


@pytest.mark.parametrize("data", ["[]", '"text"', "null"])
def test_mark_ignores_non_object_json(relay, data):
    """構文上は正しくても最上位がオブジェクトでなければ、前の合図も質問の合図も残す。"""
    relay.next.write_text('{"command": "keep"}')
    question = relay.dir / "question"
    question.write_text("q")
    quiet_ok(mark(relay.dir, data))
    assert relay.next.read_text() == '{"command": "keep"}'
    assert question.read_text() == "q"


def test_mark_not_direct_child_claude_keeps_mark(tmp_path, relay):
    """conductor が Bash から起こした `claude -p` の Stop は、前の合図を消さない（AC4b）。"""
    relay.next.write_text('{"command": "keep"}')
    # 名前が claude の別のスクリプトを間に挟む。mark から見て最初の claude はこのスクリプトになる
    wrapper = tmp_path / "bin" / "claude"
    wrapper.parent.mkdir()
    wrapper.write_text(f"#!{sys.executable}\nimport subprocess, sys\n"
                       f"sys.exit(subprocess.run([{sys.executable!r}, {str(RELAY)!r}, 'mark']).returncode)\n")
    wrapper.chmod(0o755)
    e = {k: v for k, v in os.environ.items() if not k.startswith("NDF_")}
    e["NDF_RELAY_DIR"] = str(relay.dir)
    for msg in ("ブロックは無い", fence("/goal other")):
        p = subprocess.run([str(wrapper)], input=stop_input(msg), capture_output=True,
                           text=True, env=e, timeout=20)
        quiet_ok(p)
        assert relay.next.read_text() == '{"command": "keep"}'


def test_mark_keeps_previous_when_no_block(relay):
    """ブロックの無い Stop（目標が未達で応答が続いた）でも、背景の処理が無ければ前の合図を残す。"""
    quiet_ok(mark(relay.dir, stop_input(fence("/goal x"))))
    before = relay.next.read_text()
    quiet_ok(mark(relay.dir, stop_input("続きの応答")))
    assert relay.next.read_text() == before


def test_mark_clears_previous_when_two_blocks(relay):
    quiet_ok(mark(relay.dir, stop_input(fence("/goal x"))))
    quiet_ok(mark(relay.dir, stop_input(fence("a") + "\n" + fence("b"))))
    assert not relay.next.exists()


def test_mark_background_running_clears(relay):
    """背景の処理が動いているあいだは合図を書かず、前の合図を消す（AC24）。"""
    quiet_ok(mark(relay.dir, stop_input(fence("/goal x"))))
    running = [{"id": "b1", "type": "shell", "status": "running"}]
    p = mark(relay.dir, stop_input(fence("/goal x"), background_tasks=running))
    assert p.returncode == 0 and json.loads(p.stdout)["decision"] == "block"
    assert not relay.next.exists()
    done = [{"id": "b1", "type": "shell", "status": "completed"}]
    quiet_ok(mark(relay.dir, stop_input(fence("/goal x"), background_tasks=done)))
    assert relay.next.exists()


def log_rows(relay, event):
    path = relay.dir / "log.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    return [r for r in rows if r.get("event") == event]


WATCH = {"id": "bxl0kh7gi", "type": "shell", "status": "running", "description": "見張り",
         "command": "until grep -q '\"event\": \"attention\"' q.log; do sleep 30; done " + "x" * 100}


def test_mark_holds_stop_once_and_lists_tasks(relay):
    """背景の作業が残る ndf-next の Stop は 1 度だけ止め、id とコマンドの先頭を示す（#1035）。"""
    (relay.dir / "log.jsonl").write_text(json.dumps({"event": "start", "section": 2}) + "\n")
    data = stop_input(fence("/goal x"), background_tasks=[WATCH, {"id": "b2", "status": "completed"}])
    p = mark(relay.dir, data)
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["decision"] == "block"
    assert "bxl0kh7gi" in out["reason"] and WATCH["command"][:80] in out["reason"]
    assert WATCH["command"][:81] not in out["reason"] and "b2" not in out["reason"]
    assert "TaskStop" in out["reason"] and "pkill -f" in out["reason"] and "止めずに終わりを待って" in out["reason"]
    # 同じ候補の 2 度目は止めない
    quiet_ok(mark(relay.dir, data))
    rows = log_rows(relay, "mark_skipped")
    assert [(r["section"], r["reason"], r["held"]) for r in rows] == [(2, "background", True),
                                                                      (2, "background", False)]
    assert rows[0]["tasks"] == [{"id": "bxl0kh7gi", "type": "shell", "command": WATCH["command"][:80]}]
    # TaskStop の後に出し直した ndf-next で合図が書かれる
    quiet_ok(mark(relay.dir, stop_input(fence("/goal x"))))
    assert json.loads(relay.next.read_text())["command"] == "/goal x"


def test_mark_does_not_hold_when_stop_hook_active(relay):
    quiet_ok(mark(relay.dir, stop_input(fence("/goal x"), background_tasks=[WATCH], stop_hook_active=True)))
    assert not relay.next.exists()
    assert [r["held"] for r in log_rows(relay, "mark_skipped")] == [False]


def test_mark_holds_again_for_another_candidate(relay):
    for body in ("/goal x", "/goal y"):
        p = mark(relay.dir, stop_input(fence(body), background_tasks=[WATCH]))
        assert json.loads(p.stdout)["decision"] == "block"


def test_mark_holds_with_count_when_tasks_lack_id(relay):
    p = mark(relay.dir, stop_input(fence("/goal x"), background_tasks=[{"status": "running"}]))
    assert "1 件" in json.loads(p.stdout)["reason"]


def test_mark_skipped_logs_two_blocks_without_holding(relay):
    quiet_ok(mark(relay.dir, stop_input(fence("a") + "\n" + fence("b"))))
    rows = log_rows(relay, "mark_skipped")
    assert [(r["reason"], r["held"], r["tasks"]) for r in rows] == [("blocks", False, [])]


def test_mark_background_without_block_logs_nothing(relay):
    quiet_ok(mark(relay.dir, stop_input("続きの応答", background_tasks=[WATCH])))
    assert log_rows(relay, "mark_skipped") == []


def test_mark_file_is_private(relay):
    quiet_ok(mark(relay.dir, stop_input(fence("/goal x"))))
    assert relay.next.stat().st_mode & 0o077 == 0


def test_mark_missing_fields(relay):
    """cwd などが無い・null なら合図の値は空文字、応答が null ならブロック 0 件として合図を残し、質問の合図を消す。"""
    quiet_ok(mark(relay.dir, json.dumps({"last_assistant_message": fence("/goal x"), "cwd": None})))
    data = json.loads(relay.next.read_text())
    assert set(data) == {"command", "cwd", "session_id", "transcript_path", "written_at"}
    assert data["command"] == "/goal x"
    assert data["cwd"] == data["session_id"] == data["transcript_path"] == ""
    question = relay.dir / "question"
    question.write_text("q")
    quiet_ok(mark(relay.dir, json.dumps({"last_assistant_message": None})))
    assert relay.next.exists()
    assert not question.exists()


# ---------------------------------------------------------------- run（擬似端末の上で動かす）

import pty
import struct
import signal
import termios
import threading
import time

FAKE = ROOT / "scripts" / "tests" / "fixtures" / "relay_fake_claude.py"

FAST = {"NDF_RELAY_QUIET": "0.3", "NDF_RELAY_POLL": "0.1",
        "NDF_RELAY_EXIT_WAIT": "5", "NDF_RELAY_TERM_WAIT": "2"}


class Term:
    """擬似端末の上で `relay.py run` を動かす。画面の出力は裏のスレッドで読み続ける。"""

    def __init__(self, tmp_path, args=(), env=None, cwd=None, rows=24, cols=80, cmd=None):
        self.fake_dir = tmp_path / "fake"
        self.fake_dir.mkdir(exist_ok=True)
        e = isolated_env(tmp_path, NDF_RELAY_CLAUDE=FAKE, FAKE_DIR=self.fake_dir,
                         FAKE_RELAY=RELAY, **FAST)
        e.update({k: str(v) for k, v in (env or {}).items()})
        self.env = e
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.before = termios.tcgetattr(slave)
        self.slave = slave
        cmd = cmd or [sys.executable, str(RELAY), "run"]
        self.proc = subprocess.Popen([*cmd, *args], stdin=slave,
                                     stdout=slave, stderr=slave, env=e, cwd=cwd or tmp_path,
                                     start_new_session=True)
        self.out = b""
        self.lock = threading.Lock()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        while True:
            try:
                data = os.read(self.master, 65536)
            except OSError:
                return
            if not data:
                return
            with self.lock:
                self.out += data

    @property
    def text(self):
        with self.lock:
            return self.out.decode(errors="replace")

    def type(self, s):
        os.write(self.master, s.encode() if isinstance(s, str) else s)

    def wait(self, pred, timeout=15, what=""):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return
            time.sleep(0.05)
        raise AssertionError(f"待ちが切れた: {what}\n画面: {self.text[-2000:]}\n"
                             f"記録: {self.rows()}")

    def starts(self):
        p = self.fake_dir / "starts.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def calls(self):
        p = self.fake_dir / "calls.jsonl"
        return [json.loads(x)["args"] for x in p.read_text().splitlines()] if p.exists() else []

    def relay_dirs(self):
        root = pathlib.Path(self.env["HOME"]) / ".local" / "state" / "ndf" / "relay"
        return sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []

    def rows(self):
        out = []
        for d in self.relay_dirs():
            p = d / "log.jsonl"
            if p.exists():
                out += [json.loads(x) for x in p.read_text().splitlines()]
        return out

    def child_input(self, n):
        pid = self.starts()[n]["pid"]
        p = self.fake_dir / f"input-{pid}"
        return p.read_bytes() if p.exists() else b""

    def wait_start(self, n, timeout=15):
        self.wait(lambda: len(self.starts()) >= n, timeout, f"{n} 回目の起動")
        # 子が端末を raw にするまで少し待つ（raw の前の入力は行の編集に掛かる）
        time.sleep(0.3)

    def finish(self, timeout=20):
        try:
            return self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            raise AssertionError(f"ラッパーが終わらない\n画面: {self.text[-2000:]}\n記録: {self.rows()}")

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(5)
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass



@pytest.fixture()
def term(tmp_path):
    made = []

    def make(*args, **kw):
        t = Term(tmp_path, args, **kw)
        made.append(t)
        return t
    yield make
    for t in made:
        t.close()


def events(rows, kind):
    return [r for r in rows if r["event"] == kind]


def test_run_no_mark_exits_with_child_code(term):
    """合図が無いまま claude が終わると、ラッパーも何も出さずに同じ終了コードで終わる（AC12・AC19）。"""
    t = term()
    t.wait_start(1)
    t.type("quit 3\r")
    assert t.finish() == 3
    assert "ndf-relay" not in t.text
    assert t.starts()[0]["argv"] == []
    rows = t.rows()
    assert [r["event"] for r in rows] == ["start", "end"]
    assert rows[1]["ended_by"] == "no-mark"
    assert set(rows[1]) == {"event", "at", "section", "pid", "seconds", "ended_by"}


def test_run_first_section_gets_args_and_clean_env(term):
    t = term("--model", "haiku", "-c", env={"CLAUDECODE": "1"})
    t.wait_start(1)
    s = t.starts()[0]
    assert s["argv"] == ["--model", "haiku", "-c"]
    assert s["claudecode"] is None
    assert s["depth"] == "1"
    assert s["relay_dir"] and pathlib.Path(s["relay_dir"]).stat().st_mode & 0o777 == 0o700
    t.type("/exit\r")
    assert t.finish() == 0


def test_run_signal_exit_code(term):
    t = term()
    t.wait_start(1)
    os.kill(t.starts()[0]["pid"], signal.SIGTERM)
    assert t.finish() == 143


def test_run_switches_to_next_section(term, tmp_path):
    """合図を受けると /exit と \\r を入力し、更新して次の区間を起動する（AC6・AC8・AC19）。"""
    t = term("--model", "haiku", env={"FAKE_VERSION_AFTER": "2.0.0"})
    t.wait_start(1)
    t.type("mark /goal 次のステップ\r")
    t.wait_start(2)
    first, second = t.starts()[:2]
    assert second["argv"] == ["--model", "haiku", "/goal 次のステップ"]
    assert t.child_input(0).endswith(b"/exit\r")
    assert t.calls() == [["list", "--json"], ["marketplace", "update", "mk"],
                         ["update", "ndf@mk", "-y"], ["list", "--json"]]
    assert "── ndf-relay: 区間 2 ──" in t.text
    rows = t.rows()
    assert [r["event"] for r in rows] == ["start", "end", "start"]
    s1, e1, s2 = rows
    assert set(s1) == {"event", "at", "section", "pid", "command", "from_session",
                       "plugin_version", "cwd"}
    assert s1["command"] == "--model haiku"
    assert s1["plugin_version"] == "1.0.0"
    assert s2["plugin_version"] == "2.0.0"
    assert s2["command"] == "/goal 次のステップ"
    assert s2["from_session"] == f"s{first['pid']}"
    assert s2["section"] == 2 and s2["pid"] == second["pid"]
    assert e1["ended_by"] == "mark" and e1["section"] == 1
    t.type("quit 0\r")
    assert t.finish() == 0


def test_run_carries_policy_args_through_shell_function(term, tmp_path):
    """alias の展開 → シェルの関数 → ラッパーと渡った引数のうち、起動の方針だけを 2 つ目の
    区間へ引き継ぐ。区間ごとの引数（会話・名前・最初のプロンプト・`--` 以後）は落とす（#936）。"""
    assert relay_cmd(tmp_path, "install", NDF_RELAY_CLAUDE=FAKE).returncode == 0
    shellrc = cfg(tmp_path) / "shellrc"
    script = ("shopt -s expand_aliases\n"
              f". {shellrc}\n"
              "alias claude='claude --dangerously-skip-permissions'\n"
              "claude --model x --add-dir a b --resume id -c --session-id u -n nm 最初 -- --verbose\n")
    t = term(cmd=["bash", "--norc", "--noprofile", "-c", script])
    t.wait_start(1)
    first = t.starts()[0]
    assert first["argv"] == ["--dangerously-skip-permissions", "--model", "x", "--add-dir", "a", "b",
                             "--resume", "id", "-c", "--session-id", "u", "-n", "nm", "最初",
                             "--", "--verbose"]
    t.type("mark /goal 次のステップ\r")
    t.wait_start(2)
    carried = ["--dangerously-skip-permissions", "--model", "x", "--add-dir", "a", "b"]
    assert t.starts()[1]["argv"] == [*carried, "/goal 次のステップ"]
    s2 = events(t.rows(), "start")[1]
    assert s2["command"] == "/goal 次のステップ"
    assert s2["carried"] == carried
    t.type("quit 0\r")
    assert t.finish() == 0


def test_run_chain_three_sections(term):
    """人が何も入力せずに 3 つ目の区間まで起動する（AC17 の試験用の形）。"""
    t = term()
    t.wait_start(1)
    t.type("mark mark 三つ目\r")
    t.wait_start(3, timeout=25)
    assert [s["argv"] for s in t.starts()] == [[], ["mark 三つ目"], ["三つ目"]]
    rows = t.rows()
    assert len(events(rows, "start")) == 3
    assert [r["ended_by"] for r in events(rows, "end")] == ["mark", "mark"]
    t.type("/exit\r")
    assert t.finish() == 0


def test_run_waits_for_user_input_quiet(term):
    """利用者の入力から静止の秒数がたつまで /exit を送らない（AC6）。"""
    t = term(env={"NDF_RELAY_QUIET": "1.5"})
    t.wait_start(1)
    t.type("mark x\r")
    for _ in range(6):
        time.sleep(0.4)
        t.type("a")  # 打っている途中
    assert len(t.starts()) == 1
    assert b"/exit" not in t.child_input(0)
    t.wait_start(2)


def test_run_waits_for_transcript_quiet(term):
    t = term(env={"NDF_RELAY_QUIET": "1.5"})
    t.wait_start(1)
    t.type("mark x\r")
    tp = t.fake_dir / f"transcript-{t.starts()[0]['pid']}.jsonl"
    for _ in range(6):
        time.sleep(0.4)
        with open(tp, "a") as f:
            f.write('{"type": "assistant"}\n')
    assert len(t.starts()) == 1
    t.wait_start(2)


def test_run_mark_removed_cancels(term):
    """合図が消えたら（次の Stop にブロックが無い）切り替えない（AC4b・AC6）。"""
    t = term(env={"NDF_RELAY_QUIET": "1"})
    t.wait_start(1)
    t.type("mark x\r")
    d = pathlib.Path(t.starts()[0]["relay_dir"])
    t.wait(lambda: (d / "next.json").exists(), what="合図")
    (d / "next.json").unlink()
    time.sleep(2)
    assert len(t.starts()) == 1
    t.type("quit 0\r")
    assert t.finish() == 0


def test_run_forwards_keys_and_winsize(term):
    """キー入力（Ctrl-C を含む）はそのまま子へ届き、大きさの変化も伝わる（AC7）。"""
    t = term(rows=30, cols=100)
    t.wait_start(1)
    t.type(b"ab\x03cd")
    t.wait(lambda: t.child_input(0) == b"ab\x03cd", what="子への入力")
    t.type("\rsize\r")
    pid = t.starts()[0]["pid"]
    size = t.fake_dir / f"size-{pid}"
    t.wait(size.exists, what="大きさ")
    assert size.read_text() == "30 100"
    size.unlink()
    fcntl.ioctl(t.master, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    os.kill(t.proc.pid, signal.SIGWINCH)
    time.sleep(0.3)
    t.type("size\r")
    t.wait(size.exists, what="大きさ")
    assert size.read_text() == "40 120"
    t.type("/exit\r")
    t.finish()


@pytest.mark.parametrize("how", ["exit", "sigterm", "sighup"])
def test_run_restores_terminal(term, how):
    t = term()
    t.wait_start(1)
    assert termios.tcgetattr(t.slave) != t.before  # raw になっている
    if how == "exit":
        t.type("/exit\r")
    else:
        os.kill(t.proc.pid, signal.SIGTERM if how == "sigterm" else signal.SIGHUP)
    t.finish()
    assert termios.tcgetattr(t.slave) == t.before


def test_run_exception_keeps_child_and_restores(term, tmp_path):
    """本体の例外は捕まえて stop の行と 1 行に変え、子を巻き込まない（非機能・AC7）。"""
    wrapper = tmp_path / "wrap.py"
    wrapper.write_text(
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('relay', {str(RELAY)!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "def boom(*a, **k): raise RuntimeError('boom')\n"
        "import relay_lib.run\n"
        "relay_lib.run.Relay._tick = boom\n"
        "sys.exit(m.main(sys.argv[1:]))\n")
    t = term(cmd=[sys.executable, str(wrapper), "run"])
    t.wait_start(1)
    t.wait(lambda: "ndf-relay: 次の区間を起動しない" in t.text, what="例外の 1 行")
    assert events(t.rows(), "stop")[0]["reason"] == "error"
    t.type(b"ok")
    t.wait(lambda: t.child_input(0) == b"ok", what="例外の後も入力が届く")
    t.type("\rquit 4\r")
    assert t.finish() == 4
    assert len(events(t.rows(), "stop")) == 1
    assert termios.tcgetattr(t.slave) == t.before


# ---------------------------------------------------------------- 素通しと本物の claude（AC15 / AC20）

import importlib.util  # noqa: E402


class Execd(Exception):
    pass


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    """relay.py を読み込み、os.execve を差し替える。環境は一時の HOME にそろえる。"""
    spec = importlib.util.spec_from_file_location("relay_under_test", RELAY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for k in list(os.environ):
        # DEVBASE_SHELLRC_DIR と ZDOTDIR も落とす（isolated_env と同じ。本物の読み込み先を見ない）
        if k.startswith(("NDF_", "XDG_", "CLAUDE", "DEVBASE_")) or k == "ZDOTDIR":
            monkeypatch.delenv(k, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    calls = []

    def fake_execve(path, argv, env):
        calls.append((path, argv, dict(env)))
        raise Execd()
    monkeypatch.setattr(m.os, "execve", fake_execve)
    m.calls = calls
    return m


@pytest.mark.parametrize("args, expected", [
    (["--dangerously-skip-permissions"], ["--dangerously-skip-permissions"]),
    (["--model", "x", "最初"], ["--model", "x"]),
    (["--model=x", "--resume=abc", "最初"], ["--model=x"]),
    (["--add-dir", "a", "b", "--verbose"], ["--add-dir", "a", "b", "--verbose"]),
    (["--mcp-config", "a.json", "b.json", "--permission-mode", "plan"],
     ["--mcp-config", "a.json", "b.json", "--permission-mode", "plan"]),
    (["--resume", "id", "--model", "x"], ["--model", "x"]),
    (["-r", "--model", "x"], ["--model", "x"]),
    (["-c", "--dangerously-skip-permissions", "最初"], ["--dangerously-skip-permissions"]),
    (["--session-id", "u", "--fork-session", "-n", "nm", "--name", "nm2", "--bg", "--background",
      "--tmux", "--from-pr", "12", "--teleport", "--cloud", "desc", "--continue"], []),
    (["--dangerously-skip-permissions", "--", "--model", "x"], ["--dangerously-skip-permissions"]),
    (["--debug", "api", "--verbose", "最初"], ["--debug", "api", "--verbose"]),
    (["--unknown-opt", "v", "最初"], ["--unknown-opt", "v"]),
    (["-nfoo", "-rabc", "--session-id=u", "--model", "x"], ["--model", "x"]),
    (["-w", "wt", "--worktree", "--model", "x"], ["--model", "x"]),
    (["--system-prompt", "--guard", "最初"], ["--system-prompt", "--guard"]),
    (["--system-prompt", "-guard", "最初"], ["--system-prompt", "-guard"]),
    (["-n", "-x", "--model", "m"], ["--model", "m"]),
    ([], []),
])
def test_carried_args(mod, args, expected):
    """最初の区間の引数から、2 つ目以降の区間へ引き継ぐものを選ぶ（#936）。"""
    assert relay_claude.carried_args(args) == expected


@pytest.mark.parametrize("tasks", [None, {"status": "running"}, "running"])
def test_background_running_ignores_non_list_inputs(mod, tasks):
    assert relay_mark.background_running(tasks) is False


def test_background_running_ignores_non_dict_items(mod):
    assert relay_mark.background_running(["running", 1, None]) is False


def test_background_running_detects_running_dict(mod):
    tasks = [{"status": "queued"}, {"status": "running"}]

    assert relay_mark.background_running(tasks) is True


def test_fallback_cwd_uses_nearest_existing_parent(mod, tmp_path):
    parent = tmp_path / "a"
    parent.mkdir()

    assert relay_common.fallback_cwd(str(parent / "b" / "c")) == str(parent)


def test_fallback_cwd_uses_existing_parent_when_worktree_main_is_gone(mod, tmp_path):
    cwd = tmp_path / "gone" / ".worktrees" / "x" / "y"

    assert relay_common.fallback_cwd(str(cwd)) == str(tmp_path)


@pytest.mark.parametrize("cwd", ["nope/deeper", ""])
def test_fallback_cwd_uses_home_when_relative_path_has_no_existing_parent(mod, tmp_path, cwd):
    assert relay_common.fallback_cwd(cwd) == str(tmp_path / "home")


def real_claude(tmp_path, name="real"):
    p = tmp_path / name / "claude"
    p.parent.mkdir(parents=True)
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(0o755)
    return p


@pytest.mark.parametrize("case", ["relay-off", "under-relay", "print", "help", "subcommand",
                                  "stdin-pipe"])
def test_run_passthrough(mod, tmp_path, monkeypatch, capsys, case):
    claude = real_claude(tmp_path)
    monkeypatch.setenv("NDF_RELAY_CLAUDE", str(claude))
    monkeypatch.setattr(mod.os, "isatty", lambda fd: case != "stdin-pipe")
    args = {"print": ["-p", "hi"], "help": ["--help"], "subcommand": ["mcp", "list"]}.get(
        case, ["--model", "haiku"])
    if case == "relay-off":
        monkeypatch.setenv("NDF_RELAY", "0")
    if case == "under-relay":
        monkeypatch.setenv("NDF_RELAY_DIR", str(tmp_path))
    before = dict(os.environ)
    with pytest.raises(Execd):
        relay_run.cmd_run(args)
    path, argv, env = mod.calls[0]
    assert path == str(claude)
    assert argv == [str(claude)] + args
    diff = {k for k in set(env) | set(before) if env.get(k) != before.get(k)}
    assert diff == {"NDF_RELAY_DEPTH"}
    assert env["NDF_RELAY_DEPTH"] == "1"
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


@pytest.mark.parametrize("case", ["no-pty", "list-fails", "no-ndf", "list-hangs", "list-bad-json"])
def test_run_cannot_start_says_then_passthrough(mod, tmp_path, monkeypatch, capsys, case):
    claude = tmp_path / "bin" / "claude"
    claude.parent.mkdir()
    body = {"list-fails": "exit 1", "no-ndf": "echo '[{\"id\": \"x@y\", \"version\": \"1\"}]'",
            "list-hangs": "sleep 30", "list-bad-json": "echo 'not json at all'"}.get(
        case, "echo '[{\"id\": \"ndf@m\", \"version\": \"1\"}]'")
    claude.write_text(f"#!/bin/sh\n{body}\n")
    claude.chmod(0o755)
    monkeypatch.setenv("NDF_RELAY_CLAUDE", str(claude))
    monkeypatch.setenv("NDF_RELAY_LIST_TIMEOUT", "0.5")
    monkeypatch.setattr(mod.os, "isatty", lambda fd: True)
    if case == "no-pty":
        monkeypatch.setitem(sys.modules, "pty", None)
    with pytest.raises(Execd):
        relay_run.cmd_run([])
    err = capsys.readouterr().err
    assert err.startswith("ndf-relay: ラッパーを始めない（") and err.count("\n") == 1
    assert mod.calls[0][1] == [str(claude)]


def test_run_relay_dir_oserror_says_then_passthrough(mod, tmp_path, monkeypatch, capsys):
    """現状固定: 一覧は読めてもラッパー用ディレクトリを作れなければ、案内を標準エラーへ 1 回出して
    実体へ素通しする（cmd_run の make_relay_dir が OSError になる経路）。"""
    claude = tmp_path / "bin" / "claude"
    claude.parent.mkdir()
    claude.write_text("#!/bin/sh\necho '[{\"id\": \"ndf@m\", \"version\": \"1\"}]'\n")
    claude.chmod(0o755)
    monkeypatch.setenv("NDF_RELAY_CLAUDE", str(claude))
    monkeypatch.setenv("NDF_RELAY_LIST_TIMEOUT", "5")
    monkeypatch.setattr(mod.os, "isatty", lambda fd: True)

    def boom():
        raise OSError(errno.EACCES, "no dir")
    monkeypatch.setattr(relay_record, "make_relay_dir", boom)
    with pytest.raises(Execd):
        relay_run.cmd_run([])
    err = capsys.readouterr().err
    assert err == "ndf-relay: ラッパーを始めない（作業ディレクトリを作れない）。カットポイントでは示されたコマンドを手で入力する\n"
    assert mod.calls[0][1] == [str(claude)]


def test_resolve_skips_wrappers(mod, tmp_path, monkeypatch):
    """`claude` という名前でラッパーを呼ぶ別のスクリプトを飛ばし、本物を選ぶ（AC20）。"""
    wrapper = tmp_path / "wrap" / "claude"
    wrapper.parent.mkdir()
    wrapper.write_text(f'#!/bin/sh\nexec python3 "{RELAY}" run "$@"\n')
    wrapper.chmod(0o755)
    link = tmp_path / "link" / "claude"
    link.parent.mkdir()
    link.symlink_to(RELAY)
    real = real_claude(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join([str(wrapper.parent), str(link.parent),
                                                str(real.parent), "/usr/bin"]))
    assert relay_claude.resolve_claude() == str(real)
    forced = real_claude(tmp_path, "forced")
    monkeypatch.setenv("NDF_RELAY_CLAUDE", str(forced))
    assert relay_claude.resolve_claude() == str(forced)


def test_resolve_keeps_unreadable_claude(mod, tmp_path, monkeypatch):
    """読めない（実行だけできる）claude はラッパーと見なさずに選ぶ。飛ばし損ねは深さの変数が止める。"""
    real = real_claude(tmp_path)
    real.chmod(0o111)
    if os.access(real, os.R_OK):
        pytest.skip("読み取り権限を外せない（root で実行している）")
    monkeypatch.setenv("PATH", os.pathsep.join([str(real.parent), "/usr/bin"]))
    monkeypatch.delenv("NDF_RELAY_CLAUDE", raising=False)
    assert relay_claude.resolve_claude() == str(real)


def test_run_nested_stops_127(tmp_path):
    e = isolated_env(tmp_path, NDF_RELAY_DEPTH=2, NDF_RELAY_CLAUDE=real_claude(tmp_path))
    p = subprocess.run([sys.executable, str(RELAY), "run"], capture_output=True, text=True,
                       env=e, timeout=20)
    assert p.returncode == 127
    assert p.stderr.startswith("ndf-relay: 起動が入れ子になっている（") and p.stderr.count("\n") == 1


def test_run_no_real_claude_127(tmp_path):
    e = isolated_env(tmp_path, PATH=str(tmp_path / "empty"))
    p = subprocess.run([sys.executable, str(RELAY), "run", "-p", "x"], capture_output=True,
                       text=True, env=e, timeout=20)
    assert p.returncode == 127
    assert "本物の claude が見つからない" in p.stderr


def test_run_passthrough_real_exec(tmp_path):
    """素通しは exec で置き換わり、終了コードと出力がそのまま返る。"""
    claude = tmp_path / "bin" / "claude"
    claude.parent.mkdir()
    claude.write_text('#!/bin/sh\necho "args:$*"\nexit 7\n')
    claude.chmod(0o755)
    e = isolated_env(tmp_path, NDF_RELAY_CLAUDE=claude)
    p = subprocess.run([sys.executable, str(RELAY), "run", "-p", "a b"], capture_output=True,
                       text=True, env=e, timeout=20)
    assert (p.returncode, p.stdout, p.stderr) == (7, "args:-p a b\n", "")


# ---------------------------------------------------------------- 切り替えの細部・上限・止め方（AC9〜AC14 / AC26）


def test_run_cwd_fallback_to_main(term, tmp_path):
    main = tmp_path / "main"
    wt = main / ".worktrees" / "design" / "x"
    wt.mkdir(parents=True)
    t = term(env={"NDF_RELAY_QUIET": "1.5"}, cwd=wt)
    t.wait_start(1)
    t.type("mark next\r")
    d = pathlib.Path(t.starts()[0]["relay_dir"])
    t.wait(lambda: (d / "next.json").exists(), what="合図")
    wt.rmdir()
    t.wait_start(2)
    assert t.starts()[1]["cwd"] == str(main)
    s2 = events(t.rows(), "start")[1]
    assert s2["cwd"] == str(main) and s2["cwd_fallback"] == str(wt)
    t.type("/exit\r")
    t.finish()


@pytest.mark.parametrize("case", ["nearest-parent", "home"])
def test_run_cwd_fallback_outside_worktree(term, tmp_path, case):
    launch = tmp_path / "launch"
    launch.mkdir()
    t = term(env={"NDF_RELAY_QUIET": "1.5"}, cwd=launch)
    t.wait_start(1)
    t.type("mark next\r")
    d = pathlib.Path(t.starts()[0]["relay_dir"])
    mark = d / "next.json"
    t.wait(mark.exists, what="合図")
    data = json.loads(mark.read_text())
    if case == "nearest-parent":
        original = str(launch / "gone" / "deeper")
        expected = str(launch)
    else:
        original = "gone/deeper"
        expected = t.env["HOME"]
    data["cwd"] = original
    mark.write_text(json.dumps(data))
    t.wait_start(2)
    assert t.starts()[1]["cwd"] == expected
    s2 = events(t.rows(), "start")[1]
    assert s2["cwd"] == expected and s2["cwd_fallback"] == original
    t.type("/exit\r")
    t.finish()


def seed_starts(home, n, name="old"):
    d = pathlib.Path(home) / ".local" / "state" / "ndf" / "relay" / name
    d.mkdir(parents=True, exist_ok=True)
    at = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    with open(d / "log.jsonl", "a") as f:
        for i in range(n):
            f.write(json.dumps({"event": "start", "at": at, "section": i + 1}) + "\n")


def test_run_max_starts_keeps_section(term):
    t = term()
    seed_starts(t.env["HOME"], 20)
    t.wait_start(1)
    t.type("mark next\r")
    t.wait(lambda: "ndf-relay: 次の区間を起動しない" in t.text, what="上限の 1 行")
    time.sleep(0.5)
    assert len(t.starts()) == 1
    assert b"/exit" not in t.child_input(0)
    assert events(t.rows(), "stop")[0]["reason"] == "max-starts"
    assert "/exit して示されたコマンドを手で入力する" in t.text
    t.type("quit 5\r")
    assert t.finish() == 5
    assert events(t.rows(), "end")[0]["ended_by"] == "no-mark"


def test_run_max_starts_race_one_wins(tmp_path):
    """残り 1 枠を 2 つのラッパーが取り合っても、起動するのは 1 つだけ（AC10）。"""
    home = tmp_path / "shared-home"
    home.mkdir()
    ts = []
    try:
        for name in ("a", "b"):
            (tmp_path / name).mkdir()
            ts.append(Term(tmp_path / name, env={"HOME": home}))
        seed_starts(home, 17)  # 2 つの 1 つ目の区間で 19。残り 1 枠
        for t in ts:
            t.wait_start(1)
        for t in ts:
            t.type("mark next\r")
        # HOME を共有するので t.rows() は両方の記録を含む。自分のラッパーの記録だけを見る。
        # 負けた側の stop は勝った側の start の記録の後に出るが、勝った側の子が
        # starts.jsonl へ書くのはさらに後になりうる。両方が決まるまで待つ
        def own(t):
            p = pathlib.Path(t.starts()[0]["relay_dir"]) / "log.jsonl"
            return [json.loads(x) for x in p.read_text().splitlines()]

        def decided(t):
            return len(t.starts()) >= 2 or events(own(t), "stop")

        ts[0].wait(lambda: all(decided(t) for t in ts), what="両方が決まる")
        started = sorted(len(t.starts()) for t in ts)
        assert started == [1, 2], [own(t) for t in ts]
        loser = next(t for t in ts if len(t.starts()) == 1)
        assert [r["reason"] for r in events(own(loser), "stop")] == ["max-starts"]
    finally:
        for t in ts:
            t.close()


def test_run_spin_stops_third(term):
    """3 つ続けて短い区間なら 3 つ目の合図で切り替えない。1 つ目の区間も数える（AC11）。"""
    t = term(env={"NDF_RELAY_SPIN": "3"})
    t.wait_start(1)
    t.type("mark mark mark 終点\r")
    t.wait_start(3, timeout=25)
    t.wait(lambda: events(t.rows(), "stop"), what="空回りの stop")
    assert events(t.rows(), "stop")[0]["reason"] == "spin"
    time.sleep(0.5)
    assert len(t.starts()) == 3
    assert b"/exit" not in t.child_input(2)
    t.type("/exit\r")
    assert t.finish() == 0


def test_run_spin_broken_by_long_section(term):
    t = term(env={"NDF_RELAY_SPIN": "3"})
    t.wait_start(1)
    t.type("mark B\r")
    t.wait_start(2)
    time.sleep(3.5)  # 2 つ目の区間を長くする
    t.type("mark mark C\r")
    t.wait_start(4, timeout=25)
    assert not events(t.rows(), "stop")
    t.type("/exit\r")
    t.finish()


def test_run_sigkill_when_child_ignores_exit(term):
    t = term(env={"FAKE_IGNORE_EXIT": "1", "NDF_RELAY_EXIT_WAIT": "0.5",
                  "NDF_RELAY_TERM_WAIT": "0.5"})
    t.wait_start(1)
    t.type("mark next\r")
    t.wait_start(2)
    e1 = events(t.rows(), "end")[0]
    assert e1["ended_by"] == "sigkill"
    assert e1["seconds"] < 1.5  # 合図の written_at まで。/exit の後の待ちを含めない
    os.kill(t.starts()[1]["pid"], signal.SIGKILL)
    t.finish()


def test_run_update_failed_prints_next_command(term):
    t = term(env={"FAKE_FAIL": "update"})
    t.wait_start(1)
    t.type("mark /goal 続き\r")
    assert t.finish() == 2
    assert "ndf-relay: 次の区間を起動できない（" in t.text
    assert "次のコマンド:" in t.text and "/goal 続き" in t.text
    rows = t.rows()
    assert [r["event"] for r in rows] == ["start", "end", "stop"]
    assert rows[-1]["reason"] == "update-failed"


def test_run_update_hang_times_out(term):
    t = term(env={"FAKE_HANG": "update", "NDF_RELAY_UPDATE_TIMEOUT": "0.5"})
    t.wait_start(1)
    t.type("mark x\r")
    assert t.finish() == 2
    assert events(t.rows(), "stop")[0]["reason"] == "update-failed"


def test_run_next_start_failed(term):
    t = term()
    t.close()
    copy = t.fake_dir / "claude-copy.py"
    copy.write_bytes(FAKE.read_bytes())
    copy.chmod(0o755)
    t2 = term(env={"FAKE_BREAK_ON_LIST": "2", "NDF_RELAY_CLAUDE": copy})
    t2.wait_start(1)
    t2.type("mark /goal 続き\r")
    assert t2.finish() == 2
    rows = t2.rows()
    assert [r["event"] for r in rows] == ["start", "end", "stop"]
    assert rows[-1]["reason"] == "start-failed" and rows[-1]["errno"] == 13
    assert "/goal 続き" in t2.text


def test_run_first_start_failed(term):
    t = term()
    t.close()
    copy = t.fake_dir / "claude-copy.py"
    copy.write_bytes(FAKE.read_bytes())
    copy.chmod(0o755)
    t2 = term(env={"FAKE_BREAK_ON_LIST": "1", "NDF_RELAY_CLAUDE": copy})
    assert t2.finish() == 127
    assert "ndf-relay: claude を起動できない（" in t2.text
    rows = t2.rows()
    assert [(r["event"], r.get("reason")) for r in rows] == [("stop", "start-failed")]


def test_stop_marks_running_relays_only(term, tmp_path):
    t = term()
    t.wait_start(1)
    root = pathlib.Path(t.env["HOME"]) / ".local" / "state" / "ndf" / "relay"
    dead = root / "dead"
    dead.mkdir()
    (dead / "relay.lock").touch()
    (dead / "relay.pid").write_text("999999")
    alive_other = root / "reused"
    alive_other.mkdir()
    (alive_other / "relay.lock").touch()
    (alive_other / "relay.pid").write_text(str(os.getpid()))
    p = subprocess.run([sys.executable, str(RELAY), "stop"], capture_output=True, text=True,
                       env=t.env, timeout=20)
    assert p.returncode == 0
    assert p.stdout.split() == [str(t.proc.pid)]
    assert not (dead / "stop").exists() and not (alive_other / "stop").exists()
    t.type("mark next\r")
    t.wait(lambda: events(t.rows(), "stop"), what="停止の合図")
    assert events(t.rows(), "stop")[0]["reason"] == "stop-file"
    assert len(t.starts()) == 1
    t.type("/exit\r")
    t.finish()
    p = subprocess.run([sys.executable, str(RELAY), "stop"], capture_output=True, text=True,
                       env=t.env, timeout=20)
    assert p.returncode == 1


def test_stop_without_root_exits_1(tmp_path):
    """状態の根が無ければ 1 で終わり、何も出さず、根も作らない。"""
    state = tmp_path / "state"
    env = isolated_env(tmp_path, XDG_STATE_HOME=state)
    p = subprocess.run([sys.executable, str(RELAY), "stop"], capture_output=True, text=True,
                       env=env, timeout=20)
    assert p.returncode == 1
    assert p.stdout == ""
    assert not (state / "ndf" / "relay").exists()


def test_stop_prints_dir_name_when_pid_missing_or_empty(tmp_path):
    """動いているラッパーの relay.pid が無い・空なら、作業ディレクトリの名前を出す。"""
    state = tmp_path / "state"
    env = isolated_env(tmp_path, XDG_STATE_HOME=state)
    root = state / "ndf" / "relay"
    locks = []
    try:
        for name in ("a-nopid", "b-emptypid"):
            d = root / name
            d.mkdir(parents=True)
            f = open(d / "relay.lock", "a")
            locks.append(f)
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        (root / "b-emptypid" / "relay.pid").write_text("")
        p = subprocess.run([sys.executable, str(RELAY), "stop"], capture_output=True, text=True,
                           env=env, timeout=20)
    finally:
        for f in locks:
            fcntl.flock(f, fcntl.LOCK_UN)
            f.close()
    assert p.returncode == 0, p.stderr
    assert p.stdout.splitlines() == ["a-nopid", "b-emptypid"]
    assert (root / "a-nopid" / "stop").exists()
    assert (root / "b-emptypid" / "stop").exists()


def test_make_relay_dir_is_new_each_time(mod, tmp_path, monkeypatch):
    """pid が同じでも作業ディレクトリは起動ごとに新しい。親が無くても作る（AC26）。"""
    monkeypatch.setattr(mod.os, "getpid", lambda: 4242)
    monkeypatch.setattr(relay_record.time, "gmtime", lambda *a: time.struct_time((2026, 9, 23, 0, 0, 0, 2, 266, 0)))
    a = relay_record.make_relay_dir()
    (pathlib.Path(a) / "stop").touch()
    (pathlib.Path(a) / "next.json").write_text("{}")
    b = relay_record.make_relay_dir()
    assert a != b
    assert os.listdir(b) == []
    assert pathlib.Path(b).stat().st_mode & 0o777 == 0o700


def goal_row(sentinel=False, met=False, at=None):
    a = {"type": "goal_status", "met": met, "condition": "c"}
    if sentinel:
        a["sentinel"] = True
    ts = at or time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    return json.dumps({"type": "attachment", "timestamp": ts, "attachment": a})


def iso_now():
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t * 1000) % 1000:03d}Z"


def mark_then_goal_unmet(t):
    """合図を書き、目標が未達の判定の行を足し、ブロックの無い Stop を模す。合図の場所を返す。"""
    t.type("mark next\r")
    d = pathlib.Path(t.starts()[0]["relay_dir"])
    t.wait(lambda: (d / "next.json").exists(), what="合図")
    time.sleep(0.05)
    t.type(f"tr {goal_row(met=False, at=iso_now())}\r")
    t.type("stop\r")
    return d


def test_run_switches_when_goal_unmet(term):
    """目標が未達で応答が続き、続いた応答の Stop にブロックが無くても、会話の記録が動き続けても
    切り替える。Esc を書いてから /exit を書く。"""
    t = term(env={"NDF_RELAY_QUIET": "1", "NDF_RELAY_ESC_WAIT": "0.5"})
    t.wait_start(1)
    tp = t.fake_dir / f"transcript-{t.starts()[0]['pid']}.jsonl"
    mark_then_goal_unmet(t)
    stop = threading.Event()

    def keep_writing():
        while not stop.is_set():
            with open(tp, "a") as f:
                f.write(json.dumps({"type": "assistant", "timestamp": iso_now(),
                                    "message": {"content": [{"type": "text", "text": "続き"}]}}) + "\n")
            time.sleep(0.2)
    th = threading.Thread(target=keep_writing, daemon=True)
    th.start()
    try:
        t.wait_start(2)
    finally:
        stop.set()
    assert t.starts()[1]["argv"] == ["next"]
    got = t.child_input(0)
    assert got.endswith(b"\x1b/exit\r") and got.count(b"\x1b") == 1
    t.type("/exit\r")
    t.finish()


@pytest.mark.parametrize("case", ["user", "question", "background"])
def test_run_goal_unmet_cancelled(term, case):
    """合図の後に利用者が入力したとき・質問が出たとき・背景の処理が起動したときは切り替えない。"""
    t = term(env={"NDF_RELAY_QUIET": "0.5", "NDF_RELAY_ESC_WAIT": "0.3"})
    t.wait_start(1)
    t.type("mark next\r")
    d = pathlib.Path(t.starts()[0]["relay_dir"])
    t.wait(lambda: (d / "next.json").exists(), what="合図")
    time.sleep(0.05)
    if case == "user":
        row = {"type": "user", "timestamp": iso_now(), "message": {"content": "続けて"}}
        t.type(f"tr {json.dumps(row, ensure_ascii=False)}\r")
    elif case == "question":
        t.type("q open\r")
        t.wait(lambda: (d / "question").exists(), what="質問の合図")
        t.type("q close\r")
    else:
        row = {"type": "assistant", "timestamp": iso_now(), "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "x", "run_in_background": True}}]}}
        t.type(f"tr {json.dumps(row)}\r")
    t.type(f"tr {goal_row(met=False, at=iso_now())}\r")
    t.type("stop\r")
    time.sleep(2.5)
    assert len(t.starts()) == 1
    assert b"/exit" not in t.child_input(0)
    t.type("quit 0\r")
    assert t.finish() == 0


def test_run_does_not_wait_for_goal_judgement(term):
    """目標の判定を待たない（#994）。`/goal clear` の行（met と sentinel の両方）が残っても静止だけで切り替える。"""
    t = term()
    t.wait_start(1)
    t.type(f"tr {goal_row(sentinel=True, met=True, at='2026-01-01T00:00:00.000Z')}\r")
    t.type("mark next\r")
    t.wait_start(2)
    t.type("/exit\r")
    t.finish()


# ---------------------------------------------------------------- 導入（#928）。一時の HOME・XDG_*・CLAUDE_CONFIG_DIR だけで動かす


OLD_ALIAS = """alias claude='python3 "${XDG_DATA_HOME:-$HOME/.local/share}/ndf/relay.py" run'"""
OLD_BLOCK = ("# >>> ndf relay >>>\n"
             "# ndf の中継（区間の切れ目で claude を自動で起動し直す）。消せば元に戻る。\n"
             f"{OLD_ALIAS}\n"
             "# <<< ndf relay <<<\n")


def plugin_root(tmp_path, version, name="plugin"):
    """版を持つプラグインのルートに relay.py を写す（古い版・新しい版の模擬）。"""
    root = tmp_path / name
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin").mkdir(exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "ndf", "version": version}))
    body = RELAY.read_bytes() + f"\n# 版 {version}\n".encode()
    (root / "scripts" / "relay.py").write_bytes(body)
    shutil.copytree(ROOT / "scripts" / "relay_lib", root / "scripts" / "relay_lib", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    (root / "scripts" / "lib").mkdir(exist_ok=True)
    for name in ("clock.py", "jsonio.py"):
        shutil.copyfile(ROOT / "scripts" / "lib" / name, root / "scripts" / "lib" / name)
    return root / "scripts" / "relay.py"


def relay_cmd(tmp_path, sub, relay=RELAY, env_extra=None, **env):
    e = isolated_env(tmp_path, **{"SHELL": "/bin/bash", **env})
    e.update(env_extra or {})
    return subprocess.run([sys.executable, str(relay), *sub.split()], capture_output=True,
                          text=True, env=e, timeout=30)


def home_of(tmp_path):
    return tmp_path / "home"


def cfg(tmp_path):
    return home_of(tmp_path) / ".claude" / "ndf"


def state(tmp_path):
    return home_of(tmp_path) / ".local" / "state" / "ndf" / "relay"


def snapshot(*paths):
    return {str(p): (p.read_bytes(), p.stat().st_mtime_ns) if p.exists() else None for p in paths}


def tree(root):
    root = pathlib.Path(root)
    return sorted(str(p.relative_to(root)) for p in root.rglob("*")) if root.exists() else []


@pytest.fixture()
def home(tmp_path):
    h = home_of(tmp_path)
    h.mkdir()
    return h


# -- install（AC3〜AC5・AC27・AC28）


def test_install_bash_first_then_idempotent(tmp_path, home):
    rc = home / ".bashrc"
    rc.write_text("export A=1\n")
    p = relay_cmd(tmp_path, "install")
    assert p.returncode == 0, p.stdout + p.stderr
    assert f"{rc} から {cfg(tmp_path) / 'shellrc'} を読むようにした" in p.stdout
    assert "次に開くシェルから効く" in p.stdout
    assert "source" not in p.stdout  # シェルへ貼るコマンドを示さない
    copy = cfg(tmp_path) / "relay.py"
    assert copy.read_bytes() == RELAY.read_bytes() and copy.stat().st_mode & 0o777 == 0o755
    assert (cfg(tmp_path) / "relay.version").read_text().strip() == \
        json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
    shellrc = (cfg(tmp_path) / "shellrc").read_text()
    assert "function claude {" in shellrc
    assert '"$HOME/.claude/ndf/relay.py" run "$@"' in shellrc and 'command claude "$@"' in shellrc
    body = rc.read_text()
    assert body == ("export A=1\n\n# >>> ndf relay >>>\n"
                    "# ndf のラッパー（/ndf:install-wrapper uninstall で外れる）\n"
                    '[ -f "$HOME/.claude/ndf/shellrc" ] && . "$HOME/.claude/ndf/shellrc"\n'
                    "# <<< ndf relay <<<\n")
    backups = list(home.glob(".bashrc.ndf-bak-*"))
    assert len(backups) == 1 and backups[0].read_text() == "export A=1\n"
    assert (state(tmp_path) / "rc-added").read_text().splitlines() == [str(rc)]
    assert (state(tmp_path) / "rc-user").read_text().splitlines() == [str(rc)]
    before = snapshot(rc, copy)
    assert relay_cmd(tmp_path, "install").returncode == 0
    assert snapshot(rc, copy) == before
    assert len(list(home.glob(".bashrc.ndf-bak-*"))) == 1


def test_install_from_empty_home_and_no_trailing_newline(tmp_path, home):
    (home / ".bashrc").write_text("x=1")
    assert relay_cmd(tmp_path, "install").returncode == 0
    assert (home / ".bashrc").read_text().startswith("x=1\n\n# >>> ndf relay >>>\n")
    shutil.rmtree(home)
    home.mkdir()
    assert relay_cmd(tmp_path, "install").returncode == 0
    assert (home / ".bashrc").read_text().startswith("# >>> ndf relay >>>\n")


def test_install_even_if_rc_added_recorded(tmp_path, home):
    """10.17.4〜10.17.6 の rc-added があっても明示の導入は足す（利用者が消した後でも打てば足る）。"""
    rc = home / ".bashrc"
    rc.write_text("a\n")
    state(tmp_path).mkdir(parents=True)
    (state(tmp_path) / "rc-added").write_text(f"{rc}\n")
    assert relay_cmd(tmp_path, "install").returncode == 0
    assert "# >>> ndf relay >>>" in rc.read_text()
    assert (state(tmp_path) / "rc-added").read_text().splitlines() == [str(rc)]
    assert (state(tmp_path) / "rc-user").read_text().splitlines() == [str(rc)]


def test_install_rewrites_old_block_inner_only(tmp_path, home):
    rc = home / ".bashrc"
    before = "head\n" + OLD_BLOCK + "tail\n"
    rc.write_text(before)
    assert relay_cmd(tmp_path, "install").returncode == 0
    body = rc.read_text()
    assert body.startswith("head\n# >>> ndf relay >>>\n") and body.endswith("# <<< ndf relay <<<\ntail\n")
    assert OLD_ALIAS not in body and '. "$HOME/.claude/ndf/shellrc"' in body
    assert [b.read_text() for b in home.glob(".bashrc.ndf-bak-*")] == [before]


def test_install_zsh_uses_zdotdir(tmp_path, home):
    zd = tmp_path / "zd"
    zd.mkdir()
    assert relay_cmd(tmp_path, "install", SHELL="/usr/bin/zsh", ZDOTDIR=zd).returncode == 0
    assert "# >>> ndf relay >>>" in (zd / ".zshrc").read_text()
    assert not (home / ".zshrc").exists() and not (home / ".bashrc").exists()


@pytest.mark.parametrize("definition", ["alias claude='x'", "claude () { :; }", "function claude { :; }",
                                        "claude() { :; }", "function claude() { :; }"])
def test_install_existing_definition_skips(tmp_path, home, definition):
    rc = home / ".bashrc"
    rc.write_text(definition + "\n")
    p = relay_cmd(tmp_path, "install")
    assert p.returncode == 1
    assert "claude の定義があるため足さない" in p.stdout
    assert rc.read_text() == definition + "\n"
    assert not list(home.glob(".bashrc.ndf-bak-*"))
    assert not state(tmp_path).exists()


def test_install_bash_aliases_definition_skips(tmp_path, home):
    (home / ".bash_aliases").write_text("alias claude=foo\n")
    assert relay_cmd(tmp_path, "install").returncode == 1
    assert not (home / ".bashrc").exists()


def test_install_other_shell_writes_nothing(tmp_path, home):
    p = relay_cmd(tmp_path, "install", SHELL="/usr/bin/fish")
    assert p.returncode == 1
    assert "fish には足さない" in p.stdout and '. "$HOME/.claude/ndf/shellrc"' in p.stdout
    assert tree(home) == []


def test_install_unclosed_block_writes_nothing(tmp_path, home):
    (home / ".zshrc").write_text("# >>> ndf relay >>>\nalias claude=x\n")
    (home / ".bashrc").write_text("a\n")
    p = relay_cmd(tmp_path, "install")
    assert p.returncode == 1 and "閉じが無い" in p.stdout
    assert sorted(tree(home)) == [".bashrc", ".zshrc"]


def test_install_unquotable_path_writes_nothing(tmp_path, home):
    p = relay_cmd(tmp_path, "install", CLAUDE_CONFIG_DIR=tmp_path / "it's")
    assert p.returncode == 1 and "引用できない文字" in p.stdout
    assert not (tmp_path / "it's").exists() and tree(home) == []


def test_install_claude_config_dir(tmp_path, home):
    c = tmp_path / "cc"
    assert relay_cmd(tmp_path, "install", CLAUDE_CONFIG_DIR=c).returncode == 0
    assert (c / "ndf" / "relay.py").exists() and (c / "ndf" / "shellrc").exists()
    assert f'"{c}/ndf/relay.py" run' in (c / "ndf" / "shellrc").read_text()
    assert f'. "{c}/ndf/shellrc"' in (home / ".bashrc").read_text()


def test_install_lock_busy_changes_nothing(tmp_path, home):
    (home / ".bashrc").write_text("a\n")
    cfg(tmp_path).mkdir(parents=True)
    with open(cfg(tmp_path) / "copy.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        p = relay_cmd(tmp_path, "install")
    assert p.returncode == 3
    assert (home / ".bashrc").read_text() == "a\n"
    assert not (cfg(tmp_path) / "relay.py").exists()
    assert not (state(tmp_path) / "rc-added").exists()


def test_install_config_dir_oserror_changes_nothing(tmp_path, home):
    """現状固定: 設定ディレクトリを作れなければ（親が通常ファイル）、書けない旨を出して
    終了コード 3 で終わり、シェル設定・複製・版ファイル・記録ファイルを新たに作らない
    （cmd_install の os.makedirs(config_dir()) が OSError になる経路）。"""
    rc = home / ".bashrc"
    rc.write_text("a\n")
    # CLAUDE_CONFIG_DIR の親を通常ファイルにする。config_dir() = <file>/sub/ndf の makedirs が失敗する
    parent = tmp_path / "afile"
    parent.write_text("x\n")
    before = snapshot(rc)
    p = relay_cmd(tmp_path, "install", CLAUDE_CONFIG_DIR=parent / "sub")
    assert p.returncode == 3
    assert "書けない（" in p.stdout
    # シェル設定は変わらない
    assert snapshot(rc) == before
    # 設定ディレクトリ配下の成果物（複製・版ファイル・rc）は作られない
    assert not (parent / "sub").exists()
    assert not (cfg(tmp_path) / "relay.py").exists()
    assert not (cfg(tmp_path) / "relay.version").exists()
    assert not (cfg(tmp_path) / "shellrc").exists()
    # 記録ファイルも作られない
    assert not (state(tmp_path) / "rc-added").exists()
    assert not (state(tmp_path) / "rc-user").exists()


def shell_run(tmp_path, script, rcfile):
    """隔離した HOME で、rcfile を読んだ対話でない bash に script を実行させる。"""
    e = isolated_env(tmp_path)
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    (fake / "claude").write_text("#!/bin/sh\necho REAL \"$@\"\n")
    (fake / "claude").chmod(0o755)
    (fake / "python3").write_text("#!/bin/sh\necho RELAY \"$@\"\n")
    (fake / "python3").chmod(0o755)
    e["PATH"] = f"{fake}:/usr/bin:/bin"
    return subprocess.run(["bash", "--norc", "-c", f"shopt -s expand_aliases\n. {rcfile}\n{script}"],
                          capture_output=True, text=True, env=e, timeout=20)


def test_install_function_falls_back_when_copy_removed(tmp_path, home):
    rc = home / ".bashrc"
    assert relay_cmd(tmp_path, "install").returncode == 0
    p = shell_run(tmp_path, "claude a b", rc)
    assert p.stdout.startswith(f"RELAY {cfg(tmp_path)}/relay.py run a b"), p.stderr
    (cfg(tmp_path) / "relay.py").unlink()
    assert shell_run(tmp_path, "claude a b", rc).stdout.strip() == "REAL a b"


def test_install_function_receives_alias_args(tmp_path, home):
    """先に alias claude='claude --x' がある rc の後にラッパーの rc を読むと、--x がラッパーへ渡る。"""
    assert relay_cmd(tmp_path, "install").returncode == 0
    pre = tmp_path / "pre.sh"
    pre.write_text("alias claude='claude --x'\n")
    p = shell_run(tmp_path, f". {home}/.bashrc\nclaude y", pre)
    assert p.stdout.strip() == f"RELAY {cfg(tmp_path)}/relay.py run --x y", p.stderr


def test_install_devbase_loader(tmp_path, home):
    dl = tmp_path / "shellrc.d"
    dl.mkdir()
    rc = home / ".bashrc"
    rc.write_text("a\n")
    before = snapshot(rc)
    assert relay_cmd(tmp_path, "install", DEVBASE_SHELLRC_DIR=dl).returncode == 0
    assert (dl / "ndf-relay.sh").read_text().endswith('[ -f "$HOME/.claude/ndf/shellrc" ] && . "$HOME/.claude/ndf/shellrc"\n')
    assert snapshot(rc) == before
    # 作り直しの模擬（状態の親を消す）でも、読み込み先のファイルから関数が定義される
    shutil.rmtree(home / ".local", ignore_errors=True)
    p = shell_run(tmp_path, "type claude", dl / "ndf-relay.sh")
    assert "claude is a function" in p.stdout, p.stderr


def test_install_devbase_rewrites_old_block(tmp_path, home):
    dl = tmp_path / "shellrc.d"
    dl.mkdir()
    rc = home / ".bashrc"
    rc.write_text("a\n" + OLD_BLOCK)
    assert relay_cmd(tmp_path, "install", DEVBASE_SHELLRC_DIR=dl).returncode == 0
    assert OLD_ALIAS not in rc.read_text() and rc.read_text().startswith("a\n# >>> ndf relay >>>\n")
    assert (dl / "ndf-relay.sh").exists()


def test_install_devbase_loader_with_non_bash_zsh_shell(tmp_path, home):
    """現状固定: loader あり・SHELL が bash でも zsh でもない（shell_rc が None）とき、
    既存の定義を探す先は rc_files + .bash_aliases へ切り替わるが、読み込み先は loader に置く。
    devbase の導入テストは SHELL=/bin/bash、fish のテストは loader 無し（読み込み先なし）のため、
    この組み合わせは通っていない。"""
    dl = tmp_path / "shellrc.d"
    dl.mkdir()
    rc = home / ".bashrc"
    rc.write_text("a\n")
    before = snapshot(rc)
    p = relay_cmd(tmp_path, "install", SHELL="/usr/bin/fish", DEVBASE_SHELLRC_DIR=dl)
    assert p.returncode == 0, p.stdout + p.stderr
    # 読み込み先は loader。bash と zsh 以外でも loader があれば置く
    assert (dl / "ndf-relay.sh").read_text().endswith(
        '[ -f "$HOME/.claude/ndf/shellrc" ] && . "$HOME/.claude/ndf/shellrc"\n')
    # ラッパーの本体と rc は置かれる
    assert (cfg(tmp_path) / "relay.py").read_bytes() == RELAY.read_bytes()
    assert (cfg(tmp_path) / "shellrc").exists()
    # .bashrc は囲みの対象ではないため変わらない（loader へ置くので snapshot と一致）
    assert snapshot(rc) == before
    # 現状固定: 読み込み先が loader のときは rc-added / rc-user を書かない
    #   （_install_locked の touched.append は else 側だけにあり、loader の分岐では追記しない）
    assert not (state(tmp_path) / "rc-added").exists()
    assert not (state(tmp_path) / "rc-user").exists()


def test_install_non_bash_zsh_shell_with_bash_aliases_definition_skips(tmp_path, home):
    """現状固定: loader あり・SHELL が fish でも、探す先が rc_files + .bash_aliases に切り替わるため、
    ~/.bash_aliases の claude 定義を見つけて終了コード 1 で何も書かない。"""
    dl = tmp_path / "shellrc.d"
    dl.mkdir()
    (home / ".bash_aliases").write_text("alias claude=foo\n")
    p = relay_cmd(tmp_path, "install", SHELL="/usr/bin/fish", DEVBASE_SHELLRC_DIR=dl)
    assert p.returncode == 1
    assert "claude の定義があるため足さない" in p.stdout
    # 何も書かない: loader も本体も rc も作らない
    assert not (dl / "ndf-relay.sh").exists()
    assert not (home / ".claude").exists()
    assert not state(tmp_path).exists()
    assert tree(home) == [".bash_aliases"]


# -- uninstall（AC6）


def test_uninstall_removes_blocks_and_files(tmp_path, home):
    dl = tmp_path / "shellrc.d"
    dl.mkdir()
    rc, zrc = home / ".bashrc", home / ".zshrc"
    rc.write_text("a\n")
    assert relay_cmd(tmp_path, "install").returncode == 0
    zrc.write_text("z1\n" + OLD_BLOCK + "z2\n" + OLD_BLOCK)
    old = home / ".local" / "share" / "ndf" / "relay.py"
    old.parent.mkdir(parents=True)
    old.write_text("old")
    (dl / "ndf-relay.sh").write_text("x\n")
    st = state(tmp_path)
    (st / "rc-noticed").write_text(f"{zrc}\n")
    (st / "rc-skipped").write_text(f"{zrc}\nother\n")
    p = relay_cmd(tmp_path, "uninstall", DEVBASE_SHELLRC_DIR=dl)
    assert p.returncode == 0, p.stdout
    assert rc.read_text() == "a\n\n"  # 足した空行も残す（囲みの外は変えない）
    assert zrc.read_text() == "z1\nz2\n"
    for f in (cfg(tmp_path) / "relay.py", cfg(tmp_path) / "relay.version", cfg(tmp_path) / "shellrc",
              old, dl / "ndf-relay.sh"):
        assert not f.exists(), f
    assert (st / "rc-noticed").read_text() == ""
    assert (st / "rc-skipped").read_text() == "other\n"
    assert set((st / "rc-added").read_text().splitlines()) == {str(rc), str(zrc)}
    assert set((st / "rc-user").read_text().splitlines()) == {str(rc), str(zrc)}
    assert "unalias claude" in p.stdout
    assert len(list(home.glob(".zshrc.ndf-bak-*"))) == 1
    assert relay_cmd(tmp_path, "uninstall").stdout.strip() == "ndf-relay: 外すものが無い"


def test_uninstall_unclosed_changes_nothing(tmp_path, home):
    rc, zrc = home / ".bashrc", home / ".zshrc"
    rc.write_text("a\n" + OLD_BLOCK)
    zrc.write_text("# >>> ndf relay >>>\n")
    cfg(tmp_path).mkdir(parents=True)
    (cfg(tmp_path) / "relay.py").write_text("c")
    before = snapshot(rc, zrc, cfg(tmp_path) / "relay.py")
    p = relay_cmd(tmp_path, "uninstall")
    assert p.returncode == 1 and "閉じが無い" in p.stdout
    assert snapshot(rc, zrc, cfg(tmp_path) / "relay.py") == before
    assert not state(tmp_path).exists()


def test_uninstall_from_1017_4_state(tmp_path, home):
    """10.17.4〜10.17.6 の自動の導入だけの状態（rc-added あり・<親> 無し）から外れ、<親> を作らない。"""
    rc = home / ".bashrc"
    rc.write_text("a\n\n" + OLD_BLOCK)
    st = state(tmp_path)
    st.mkdir(parents=True)
    (st / "rc-added").write_text(f"{rc}\n")
    assert relay_cmd(tmp_path, "uninstall").returncode == 0
    assert rc.read_text() == "a\n\n"
    assert not (home / ".claude").exists()
    assert (st / "rc-added").read_text().splitlines() == [str(rc)]


def test_uninstall_lock_busy_changes_nothing(tmp_path, home):
    rc = home / ".bashrc"
    assert relay_cmd(tmp_path, "install").returncode == 0
    before = snapshot(rc, cfg(tmp_path) / "relay.py")
    with open(cfg(tmp_path) / "copy.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        assert relay_cmd(tmp_path, "uninstall").returncode == 3
    assert snapshot(rc, cfg(tmp_path) / "relay.py") == before


# -- status（AC7）


def test_status_reports_and_writes_nothing(tmp_path, home):
    rc = home / ".bashrc"
    rc.write_text(OLD_BLOCK)
    st = state(tmp_path)
    st.mkdir(parents=True)
    (st / "rc-added").write_text(f"{rc}\n")
    before = tree(home)
    p = relay_cmd(tmp_path, "status")
    assert p.returncode == 0
    assert "直の alias（10.17.4〜10.17.6 の形）。10.17.4〜10.17.6 が自動で足した" in p.stdout
    assert f"複製 {cfg(tmp_path)}/relay.py: 無し" in p.stdout
    assert tree(home) == before
    assert relay_cmd(tmp_path, "install").returncode == 0
    p = relay_cmd(tmp_path, "status")
    assert "読み込みの行" in p.stdout and "10.17.4〜10.17.6 が自動で足した" not in p.stdout
    assert f"複製 {cfg(tmp_path)}/relay.py: 今の版と同じ" in p.stdout


# -- macOS の bash はログインシェルの設定へ足す（#966）


@pytest.fixture()
def mac(mod, tmp_path, monkeypatch):
    """relay.py を macOS の bash として動かす。HOME は一時ディレクトリ（mod が用意する）。"""
    for k in ("DEVBASE_SHELLRC_DIR", "ZDOTDIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setattr(sys, "platform", "darwin")
    assert mod.os.path.expanduser("~") == str(home_of(tmp_path))
    return mod


def test_mac_install_adds_to_bash_profile_and_uninstall_removes(mac, tmp_path, capsys):
    home = home_of(tmp_path)
    assert relay_install.cmd_install() == 0
    profile = home / ".bash_profile"
    assert "# >>> ndf relay >>>" in profile.read_text()
    assert not (home / ".bashrc").exists()
    assert f"{profile} から" in capsys.readouterr().out
    assert relay_install.cmd_status() == 0
    assert "警告" not in capsys.readouterr().out
    assert relay_install.cmd_uninstall() == 0
    assert "# >>> ndf relay >>>" not in profile.read_text()


def test_mac_status_warns_when_only_bashrc_has_loader(mac, tmp_path, capsys):
    home = home_of(tmp_path)
    (home / ".bashrc").write_text("# >>> ndf relay >>>\n" + relay_shellrc.loader_body() + "# <<< ndf relay <<<\n")
    (home / ".bash_profile").write_text("export A=1\n# . ~/.bashrc\n")
    assert relay_install.cmd_status() == 0
    assert "警告" in capsys.readouterr().out
    (home / ".bash_profile").write_text('[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"\n')
    assert relay_install.cmd_status() == 0
    assert "警告" not in capsys.readouterr().out


# -- status は今のセッションとラッパーとの位置を示す（#1187）


def session_lines(m, capsys):
    before = {p for p in home_of_now().rglob("*")}
    assert relay_install.cmd_status() == 0
    assert {p for p in home_of_now().rglob("*")} == before
    return [x for x in capsys.readouterr().out.splitlines() if x.startswith("ndf-relay: このセッション:")]


def home_of_now():
    return pathlib.Path(os.environ["HOME"])


def test_status_session_relay(mod, tmp_path, monkeypatch, capsys):
    # 関数で呼ぶ status の親は pytest の親。そこを child.pid にすると最初に当たる claude がラッパーの子になる
    r = Relay(tmp_path, child_pid=os.getppid())
    (r.dir / "relay.pid").write_text("4321")
    monkeypatch.setenv("NDF_RELAY_DIR", str(r.dir))
    try:
        assert session_lines(mod, capsys) == [
            f"ndf-relay: このセッション: ラッパー経由（relay PID 4321、claude PID {os.getppid()}）"]
    finally:
        r.release()


def test_status_session_no_dir(mod, capsys):
    assert session_lines(mod, capsys) == [
        "ndf-relay: このセッション: ラッパーを通らずに起動（NDF_RELAY_DIR が無い）"]


def test_status_session_no_dir_hints_when_loader_is_in_rc(mod, tmp_path, capsys):
    home = home_of(tmp_path)
    (home / ".bashrc").write_text("# >>> ndf relay >>>\n" + relay_shellrc.loader_body() + "# <<< ndf relay <<<\n")
    assert session_lines(mod, capsys) == [
        "ndf-relay: このセッション: ラッパーを通らずに起動（NDF_RELAY_DIR が無い）。"
        f"{home / '.bashrc'} に読み込みの行はあるので、このセッションは読み込みの前に開いたシェル、"
        "または IDE から起動した"]


def test_status_session_no_dir_hints_when_loader_is_in_zshrc(mod, tmp_path, capsys):
    home = home_of(tmp_path)
    (home / ".zshrc").write_text("# >>> ndf relay >>>\n" + relay_shellrc.loader_body() + "# <<< ndf relay <<<\n")
    assert f"{home / '.zshrc'} に読み込みの行はある" in session_lines(mod, capsys)[0]


def test_status_session_no_dir_hints_when_devbase_loader_exists(mod, tmp_path, monkeypatch, capsys):
    dl = tmp_path / "shellrc.d"
    dl.mkdir()
    (dl / "ndf-relay.sh").write_text(relay_shellrc.loader_body())
    monkeypatch.setenv("DEVBASE_SHELLRC_DIR", str(dl))
    assert f"{dl / 'ndf-relay.sh'} に読み込みの行はある" in session_lines(mod, capsys)[0]


def test_status_session_not_running(mod, tmp_path, monkeypatch, capsys):
    r = Relay(tmp_path)
    r.release()
    monkeypatch.setenv("NDF_RELAY_DIR", str(r.dir))
    assert session_lines(mod, capsys) == [
        "ndf-relay: このセッション: ラッパーは終わっている（NDF_RELAY_DIR はあるがラッパーが動いていない）"]


def test_status_session_not_child(mod, tmp_path, monkeypatch, capsys):
    r = Relay(tmp_path, child_pid=999999)
    monkeypatch.setenv("NDF_RELAY_DIR", str(r.dir))
    try:
        assert session_lines(mod, capsys) == [
            "ndf-relay: このセッション: ラッパーの直接の子ではない（fork・bg-pty-host・別の入口。#1016）"]
    finally:
        r.release()


def test_status_session_cannot_trace_parents(mod, tmp_path, monkeypatch, capsys):
    # /proc も ps も使えない環境。ほかの行はそのまま出す
    r = Relay(tmp_path, child_pid=999999)
    monkeypatch.setenv("NDF_RELAY_DIR", str(r.dir))
    monkeypatch.setattr(relay_proc, "proc_info", lambda pid: None)
    try:
        assert relay_install.cmd_status() == 0
        out = capsys.readouterr().out
        assert "ndf-relay: このセッション: 判定できない（親のプロセスをたどれない）" in out.splitlines()
        assert "ndf-relay: 読み込み先:" in out and "ndf-relay: 複製 " in out
    finally:
        r.release()


def test_mac_uninstall_removes_blocks_in_both_files(mac, tmp_path):
    home = home_of(tmp_path)
    block = "# >>> ndf relay >>>\n" + relay_shellrc.loader_body() + "# <<< ndf relay <<<\n"
    (home / ".bashrc").write_text("a=1\n" + block)
    (home / ".bash_profile").write_text("b=1\n" + block)
    assert relay_install.cmd_uninstall() == 0
    assert (home / ".bashrc").read_text() == "a=1\n"
    assert (home / ".bash_profile").read_text() == "b=1\n"


@pytest.mark.parametrize("name", [".bash_profile", ".bash_login", ".profile"])
def test_bash_definition_in_login_file_is_not_overridden(mod, tmp_path, monkeypatch, capsys, name):
    monkeypatch.delenv("DEVBASE_SHELLRC_DIR", raising=False)
    monkeypatch.setenv("SHELL", "/bin/bash")
    home = home_of(tmp_path)
    (home / name).write_text("alias claude='claude --dangerously-skip-permissions'\n")
    assert relay_install.cmd_install() == 1
    assert f"{home / name} に claude の定義がある" in capsys.readouterr().out
    assert not (home / ".bashrc").exists()


def test_mac_does_not_create_bash_profile_that_shadows_profile(mac, tmp_path, capsys):
    home = home_of(tmp_path)
    (home / ".profile").write_text("export A=1\n")
    assert relay_install.cmd_install() == 1
    assert not (home / ".bash_profile").exists()
    assert f"{home / '.profile'} を読んでいる" in capsys.readouterr().out


def test_linux_bash_still_adds_to_bashrc(mod, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DEVBASE_SHELLRC_DIR", raising=False)
    monkeypatch.setenv("SHELL", "/bin/bash")
    monkeypatch.setattr(sys, "platform", "linux")
    home = home_of(tmp_path)
    assert relay_install.cmd_install() == 0
    assert "# >>> ndf relay >>>" in (home / ".bashrc").read_text()
    assert not (home / ".bash_profile").exists()
    assert relay_install.cmd_status() == 0
    out = capsys.readouterr().out
    assert ".bash_profile" not in out and "警告" not in out


# -- startup（AC1・AC9〜AC11・AC29）


HOOK_STARTUP = next(h["command"] for e in json.loads((ROOT / "hooks" / "claude.json").read_text())
                    ["hooks"]["SessionStart"] for h in e["hooks"] if "relay.py" in h["command"])


def run_hook(tmp_path, command, root=ROOT, **env):
    e = isolated_env(tmp_path, SHELL="/bin/bash", CLAUDE_PLUGIN_ROOT=root, **env)
    return subprocess.run(["sh", "-c", command], capture_output=True, text=True, env=e, timeout=30)


def test_hook_definition_no_install():
    hooks = json.loads((ROOT / "hooks" / "claude.json").read_text())["hooks"]
    cmds = [h["command"] for e in hooks["SessionStart"] for h in e["hooks"]]
    assert not any("relay.py install" in c for c in cmds)
    entry = next(e for e in hooks["SessionStart"] if any("relay.py" in h["command"] for h in e["hooks"]))
    assert entry["matcher"] == "startup|resume"
    for kind, action in (("PreToolUse", "open"), ("PostToolUse", "close")):
        e = next(e for e in hooks[kind] if e["matcher"] == "AskUserQuestion")
        assert e["hooks"][0]["timeout"] == 10 and f"question {action}" in e["hooks"][0]["command"]


def test_startup_hook_writes_nothing_on_clean_home(tmp_path, home):
    rc = home / ".bashrc"
    rc.write_text("a\n")
    before = snapshot(rc)
    p = run_hook(tmp_path, HOOK_STARTUP)
    assert p.returncode == 0 and p.stdout == ""
    assert snapshot(rc) == before and tree(home) == [".bashrc"]


def test_startup_notices_auto_block_once(tmp_path, home):
    rc = home / ".bashrc"
    rc.write_text(OLD_BLOCK)
    st = state(tmp_path)
    st.mkdir(parents=True)
    (st / "rc-added").write_text(f"{rc}\n")
    before = snapshot(rc)
    p = run_hook(tmp_path, HOOK_STARTUP)
    msg = json.loads(p.stdout)["systemMessage"]
    assert f"{rc} の alias claude は 10.17.4〜10.17.6 が自動で足したもの" in msg
    assert "/ndf:install-wrapper uninstall" in msg and "作り直した後" not in msg
    assert run_hook(tmp_path, HOOK_STARTUP).stdout == ""
    assert snapshot(rc) == before


def test_startup_notice_devbase_hint(tmp_path, home):
    rc = home / ".bashrc"
    rc.write_text(OLD_BLOCK)
    state(tmp_path).mkdir(parents=True)
    (state(tmp_path) / "rc-added").write_text(f"{rc}\n")
    p = run_hook(tmp_path, HOOK_STARTUP, DEVBASE_SHELLRC_DIR=tmp_path)
    assert "コンテナを作り直した後も使うなら /ndf:install-wrapper" in json.loads(p.stdout)["systemMessage"]


def test_startup_notice_concurrent_once(tmp_path, home):
    rc = home / ".bashrc"
    rc.write_text(OLD_BLOCK)
    state(tmp_path).mkdir(parents=True)
    (state(tmp_path) / "rc-added").write_text(f"{rc}\n")
    e = isolated_env(tmp_path, SHELL="/bin/bash")
    procs = [subprocess.Popen([sys.executable, str(RELAY), "startup"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, env=e) for _ in range(4)]
    outs = [p.communicate(timeout=20)[0] for p in procs]
    assert all(p.returncode == 0 for p in procs)
    assert sum(len(o.splitlines()) for o in outs) == 1


@pytest.mark.parametrize("case", ["removed", "user", "explicit"])
def test_startup_no_notice(tmp_path, home, case):
    rc = home / ".bashrc"
    rc.write_text(OLD_BLOCK if case != "removed" else "a\n")
    state(tmp_path).mkdir(parents=True)
    (state(tmp_path) / "rc-added").write_text(f"{rc}\n")
    if case == "user":
        (state(tmp_path) / "rc-user").write_text(f"{rc}\n")
    if case == "explicit":
        rc.write_text("a\n")
        (state(tmp_path) / "rc-added").unlink()
        assert relay_cmd(tmp_path, "install").returncode == 0
    assert run_hook(tmp_path, HOOK_STARTUP).stdout == ""


def test_startup_refreshes_existing_copies_only(tmp_path, home):
    old = home / ".local" / "share" / "ndf" / "relay.py"
    old.parent.mkdir(parents=True)
    old.write_text("# 10.17.4〜10.17.6 の複製\n")
    rc = home / ".bashrc"
    rc.write_text("a\n")
    before = snapshot(rc)
    assert run_hook(tmp_path, HOOK_STARTUP).returncode == 0
    assert old.read_bytes() == RELAY.read_bytes()
    assert not (home / ".claude").exists()  # 無い複製は作らない
    cfg(tmp_path).mkdir(parents=True)
    (cfg(tmp_path) / "relay.py").write_text("# 古い複製\n")
    (cfg(tmp_path) / "relay.version").write_text("10.17.4\n")
    assert run_hook(tmp_path, HOOK_STARTUP).returncode == 0
    assert (cfg(tmp_path) / "relay.py").read_bytes() == RELAY.read_bytes()
    assert snapshot(rc) == before and not (cfg(tmp_path) / "shellrc").exists()


@pytest.mark.parametrize("copy_ver,plugin_ver,replaced", [
    ("10.17.5-dev.2", "10.17.5-dev.10", True),
    ("10.17.5-dev.2", "10.17.5-dev.1", False),
    ("10.18.0-rc.1", "10.18.0-dev.3", False),
    ("10.18.0-rc.1", "10.18.0", True),
    ("10.17.5", "10.17.5-dev.9", False),
    ("10.17.6", "10.17.5", False),
    (None, "10.17.5", True),
    ("壊れた", "10.17.5", True),
])
def test_startup_never_downgrades(tmp_path, home, copy_ver, plugin_ver, replaced):
    relay = plugin_root(tmp_path, plugin_ver)
    cfg(tmp_path).mkdir(parents=True)
    (cfg(tmp_path) / "relay.py").write_text("# 複製\n")
    if copy_ver is not None:
        (cfg(tmp_path) / "relay.version").write_text(copy_ver + "\n")
    assert relay_cmd(tmp_path, "startup", relay=relay).returncode == 0
    got = (cfg(tmp_path) / "relay.py").read_bytes()
    assert (got == relay.read_bytes()) is replaced
    if replaced:
        assert (cfg(tmp_path) / "relay.version").read_text().strip() == plugin_ver


def test_startup_records_version_when_copy_is_same(tmp_path, home):
    """中身が同じ複製でも、版の記録が古ければ今の版へ書き直す（中身が変わらない版を入れたとき）。"""
    relay = plugin_root(tmp_path, "10.17.25")
    cfg(tmp_path).mkdir(parents=True)
    (cfg(tmp_path) / "relay.py").write_bytes(relay.read_bytes())
    (cfg(tmp_path) / "relay.version").write_text("10.17.24\n")
    assert relay_cmd(tmp_path, "startup", relay=relay).returncode == 0
    assert (cfg(tmp_path) / "relay.version").read_text().strip() == "10.17.25"


def test_explicit_install_can_downgrade(tmp_path, home):
    new = plugin_root(tmp_path, "10.18.0", "new")
    old = plugin_root(tmp_path, "10.17.5", "old")
    assert relay_cmd(tmp_path, "install", relay=new).returncode == 0
    assert relay_cmd(tmp_path, "install", relay=old).returncode == 0
    assert (cfg(tmp_path) / "relay.py").read_bytes() == old.read_bytes()
    assert (cfg(tmp_path) / "relay.version").read_text().strip() == "10.17.5"


def test_startup_concurrent_new_wins(tmp_path, home):
    """状態の親を分けた（2 つのコンテナの模擬）新旧の startup が同時に走っても、複製は新しい版で終わる。"""
    new = plugin_root(tmp_path, "10.18.0", "new")
    old = plugin_root(tmp_path, "10.17.5", "old")
    cfg(tmp_path).mkdir(parents=True)
    (cfg(tmp_path) / "relay.py").write_text("# 複製\n")
    (cfg(tmp_path) / "relay.version").write_text("10.17.0\n")
    procs = []
    for i, r in enumerate([old, new, old, new]):
        e = isolated_env(tmp_path, XDG_STATE_HOME=tmp_path / f"st{i}")
        procs.append(subprocess.Popen([sys.executable, str(r), "startup"], env=e))
    for p in procs:
        assert p.wait(20) == 0
    assert (cfg(tmp_path) / "relay.py").read_bytes() == new.read_bytes()
    assert (cfg(tmp_path) / "relay.version").read_text().strip() == "10.18.0"


def test_startup_after_uninstall_creates_nothing(tmp_path, home):
    assert relay_cmd(tmp_path, "install").returncode == 0
    assert relay_cmd(tmp_path, "uninstall").returncode == 0
    assert relay_cmd(tmp_path, "startup").returncode == 0
    assert not (cfg(tmp_path) / "relay.py").exists()


def test_startup_unwritable_exits_zero(tmp_path, home):
    (home / ".local").write_text("ファイルなのでディレクトリを作れない")
    cfg(tmp_path).mkdir(parents=True)
    (cfg(tmp_path) / "relay.py").write_text("x")
    p = relay_cmd(tmp_path, "startup")
    assert p.returncode == 0 and p.stdout == ""


def test_version_key(mod):
    k = relay_version_dir.version_key
    assert k("10.17.5-dev.2") < k("10.17.5-dev.10") < k("10.17.5-rc.1") < k("10.17.5") < k("10.17.6-dev.1")
    assert k("x") is None and k(None) is None


# ---------------------------------------------------------------- 関門を越えない守り（AC23〜AC26b）


def question_rows(t, n=0):
    p = t.fake_dir / f"question-{t.starts()[n]['pid']}.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_guard_question_blocks_exit(term):
    """G1: 合図の後に質問が出たら /exit を書かない。答えた後の Stop がブロックを出せば切り替わる。"""
    t = term(env={"NDF_RELAY_QUIET": "1.5"})
    t.wait_start(1)
    t.type("mark 次\r")  # 合図の後に応答が再開して質問が出た形
    t.type("q open\r")
    t.wait(lambda: question_rows(t), what="質問の合図")
    assert question_rows(t)[0] == {"action": "open", "stdout": "", "code": 0}
    d = pathlib.Path(t.starts()[0]["relay_dir"])
    assert (d / "question").exists() and (d / "next.json").exists()
    time.sleep(3)
    assert len(t.starts()) == 1 and b"/exit" not in t.child_input(0)
    t.type("q close\r")
    t.wait(lambda: not (d / "question").exists(), what="合図が消える")
    t.type("mark 次\r")
    t.wait_start(2)
    assert t.child_input(0).endswith(b"/exit\r")
    t.type("/exit\r")
    t.finish()


def test_guard_mark_clears_question(term):
    """Esc で取り消して PostToolUse が来なくても、次の Stop（mark）が質問の合図を消す。"""
    t = term(env={"NDF_RELAY_QUIET": "0.5"})
    t.wait_start(1)
    t.type("q open\r")
    d = pathlib.Path(t.starts()[0]["relay_dir"])
    t.wait(lambda: (d / "question").exists(), what="質問の合図")
    t.type("mark 次\r")
    t.wait_start(2)
    t.type("/exit\r")
    t.finish()


def iso_after(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + seconds))


def test_guard_reply_after_mark_blocks(term):
    """G2: 合図より後の assistant の行があれば、その合図では書かない。attachment の行では止まらない。"""
    t = term(env={"NDF_RELAY_QUIET": "0.5"})
    t.wait_start(1)
    t.type("mark 次\r")
    d = pathlib.Path(t.starts()[0]["relay_dir"])
    t.wait(lambda: (d / "next.json").exists(), what="合図")
    t.type("tr " + json.dumps({"type": "assistant", "timestamp": iso_after(2)}) + "\r")
    time.sleep(2.5)
    assert len(t.starts()) == 1 and b"/exit" not in t.child_input(0)
    t.type("mark 次\r")  # 次の Stop が合図を書き直す
    t.wait_start(2)
    t.type("/exit\r")
    t.finish()


def test_guard_attachment_row_does_not_block(term):
    t = term(env={"NDF_RELAY_QUIET": "0.5"})
    t.wait_start(1)
    t.type("mark 次\r")
    d = pathlib.Path(t.starts()[0]["relay_dir"])
    t.wait(lambda: (d / "next.json").exists(), what="合図")
    t.type("tr " + json.dumps({"type": "attachment", "timestamp": iso_after(2),
                               "attachment": {"type": "other"}}) + "\r")
    t.wait_start(2)
    t.type("/exit\r")
    t.finish()


def test_guard_single_write(term):
    """G3: /exit と改行は 1 回の write で届く。"""
    t = term()
    t.wait_start(1)
    t.type("mark 次\r")
    t.wait_start(2)
    pid = t.starts()[0]["pid"]
    chunks = [bytes.fromhex(json.loads(x)["hex"])
              for x in (t.fake_dir / f"chunks-{pid}.jsonl").read_text().splitlines()]
    assert chunks[-1] == b"/exit\r"
    t.type("/exit\r")
    t.finish()


def test_guard_lock_held_then_question_appears(term):
    """G3: 質問の hook がロックを持つ間は書かない。持つ間に合図が置かれたら、確かめ直しで書かない。"""
    t = term(env={"NDF_RELAY_QUIET": "0.3"})
    t.wait_start(1)
    d = pathlib.Path(t.starts()[0]["relay_dir"])
    with open(d / "question.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        t.type("mark 次\r")
        t.wait(lambda: (d / "next.json").exists(), what="合図")
        time.sleep(1.5)
        assert b"/exit" not in t.child_input(0)
        (d / "question").write_text("")
    time.sleep(1.5)
    assert len(t.starts()) == 1 and b"/exit" not in t.child_input(0)
    t.type("q close\r")
    t.type("mark 次\r")
    t.wait_start(2)
    t.type("/exit\r")
    t.finish()


def test_guard_question_open_waits_for_relay_lock(tmp_path, relay):
    """ラッパーが question.lock を持つ間、question open は放されるまで待ち、放された後に合図を作る。"""
    lock = open(relay.dir / "question.lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX)
    p = subprocess.Popen([sys.executable, str(RELAY), "question", "open"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True,
                         env={**isolated_env(tmp_path), "NDF_RELAY_DIR": str(relay.dir)})
    p.stdin.write("{}")
    p.stdin.close()
    time.sleep(1)
    assert not (relay.dir / "question").exists()
    lock.close()
    assert p.wait(10) == 0
    assert p.stdout.read() == ""
    assert (relay.dir / "question").exists()
    assert (relay.dir / "question").stat().st_mode & 0o777 == 0o600


def question(tmp_path, env_dir, action="open"):
    e = isolated_env(tmp_path)
    if env_dir is not None:
        e["NDF_RELAY_DIR"] = str(env_dir)
    command = [sys.executable, str(RELAY), "question"]
    if action is not None:
        command.append(action)
    return subprocess.run(command, input="{}",
                          capture_output=True, text=True, env=e, timeout=20)


@pytest.mark.parametrize("case", ["no-dir", "not-running", "not-direct-child"])
def test_question_outside_relay_does_nothing(tmp_path, relay, case):
    env_dir = relay.dir
    if case == "no-dir":
        env_dir = None
    elif case == "not-running":
        relay.release()
    else:
        (relay.dir / "child.pid").write_text("999999")
    quiet_ok(question(tmp_path, env_dir))
    assert not (relay.dir / "question").exists()


def test_question_denies_when_lock_busy(tmp_path, relay):
    with open(relay.dir / "question.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        t0 = time.time()
        p = question(tmp_path, relay.dir)
        assert time.time() - t0 < 4.5
    assert p.returncode == 0
    out = json.loads(p.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and out["hookEventName"] == "PreToolUse"
    assert not (relay.dir / "question").exists()


def test_question_denies_when_unwritable(tmp_path, relay):
    relay.dir.chmod(0o500)
    try:
        p = question(tmp_path, relay.dir)
    finally:
        relay.dir.chmod(0o700)
    assert p.returncode == 0
    assert json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_question_close_removes(tmp_path, relay):
    (relay.dir / "question").write_text("")
    quiet_ok(question(tmp_path, relay.dir, "close"))
    assert not (relay.dir / "question").exists()


@pytest.mark.parametrize("action", ["bogus", None])
def test_question_unknown_or_missing_action_keeps_mark(tmp_path, relay, action):
    mark = relay.dir / "question"
    quiet_ok(question(tmp_path, relay.dir, action))
    assert not mark.exists()

    mark.write_text("keep")
    quiet_ok(question(tmp_path, relay.dir, action))
    assert mark.read_text() == "keep"


def test_guard_exit_queued_behind_question(term):
    """AC25b: /exit の後に質問が出たら SIGTERM までの秒を数えず、count.lock を放す。
    答えの後の Stop が合図を書き直していれば、その中身で起動する。"""
    t = term(env={"FAKE_EXIT_QUESTION": "1", "NDF_RELAY_EXIT_WAIT": "1", "NDF_RELAY_TERM_WAIT": "1"})
    t.wait_start(1)
    t.type("mark 前の中身\r")
    d = pathlib.Path(t.starts()[0]["relay_dir"])
    t.wait(lambda: (d / "question").exists(), what="/exit の後の質問")
    time.sleep(2.5)
    pid = t.starts()[0]["pid"]
    assert not (t.fake_dir / f"sigterm-{pid}").exists()
    with open(pathlib.Path(t.env["HOME"]) / ".local" / "state" / "ndf" / "relay" / "count.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)  # 別のラッパーが取れる
        fcntl.flock(f, fcntl.LOCK_UN)
    t.type("answer mark 答えの後\r")
    t.wait_start(2)
    assert t.starts()[1]["argv"] == ["答えの後"]
    t.type("quit 0\r")
    t.finish()


def test_guard_exit_queued_then_no_mark(term):
    """答えの後の Stop が合図を消していれば、次の区間を起動せず子の終了コードで終わる。"""
    t = term(env={"FAKE_EXIT_QUESTION": "1", "NDF_RELAY_EXIT_WAIT": "1"})
    t.wait_start(1)
    t.type("mark 前\r")
    d = pathlib.Path(t.starts()[0]["relay_dir"])
    t.wait(lambda: (d / "question").exists(), what="/exit の後の質問")
    t.type("answer\r")
    assert t.finish() == 0
    assert len(t.starts()) == 1
    assert events(t.rows(), "end")[-1]["ended_by"] == "no-mark"


def test_guard_exit_wait_resumes_after_question(term):
    """質問の合図が消えた後から数え始め、SIGTERM に至る。"""
    t = term(env={"FAKE_EXIT_QUESTION": "1", "NDF_RELAY_EXIT_WAIT": "1", "NDF_RELAY_TERM_WAIT": "1"})
    t.wait_start(1)
    t.type("mark 前\r")
    d = pathlib.Path(t.starts()[0]["relay_dir"])
    t.wait(lambda: (d / "question").exists(), what="/exit の後の質問")
    pid = t.starts()[0]["pid"]
    time.sleep(2)
    assert not (t.fake_dir / f"sigterm-{pid}").exists()
    t.type("unq\r")
    t.wait(lambda: (t.fake_dir / f"sigterm-{pid}").exists(), timeout=10, what="SIGTERM")
    t.wait_start(2)  # 合図は残っているので、読み直した合図で起動する
    assert events(t.rows(), "end")[0]["ended_by"] == "sigterm"
    t.type("quit 0\r")
    t.finish()


def test_relay_quiet_defaults_to_five_seconds(mod, tmp_path, monkeypatch):
    """`NDF_RELAY_QUIET` が無ければ静止の待ちは 5 秒（#964）。有れば値に従う。"""
    relay_dir = tmp_path / "relay"
    relay_dir.mkdir()
    r = relay_run.Relay("claude", str(relay_dir), "m", "v", None, None)
    os.close(r.lock_fd)
    assert r.quiet == 5
    monkeypatch.setenv("NDF_RELAY_QUIET", "0.3")
    r = relay_run.Relay("claude", str(relay_dir), "m", "v", None, None)
    os.close(r.lock_fd)
    assert r.quiet == 0.3


# ---------------------------------------------------------------- notice（#980 AC1〜AC3c）

WAIT_TAIL = "キー入力やスクロールをせずに、そのまま待つ（切り替わらずに ndf-relay: で始まる 1 行が出たら、その案内に従う）"
NOTICE_OUTSIDE = ("/exit してから claude を起動し、下の中身を最初の入力として貼り付ける"
                  "（/ndf:install-wrapper でラッパーを入れると自動になる）")
NOTICE_SOON = "まもなく自動で新しい会話へ切り替わる。" + WAIT_TAIL
NOTICE_ENDED = ("ラッパーは既に終わっている。"
                "/exit してから claude を起動し、下の中身を最初の入力として貼り付ける")


def notice_not_child(pid):
    return (f"ラッパーは元の会話（子 pid {pid}）しか見ていないため、この会話で出した ndf-next は自動では拾われない。"
            "元の会話へ戻って同じ ndf-next を出すか、元の会話を /exit してから"
            " claude を起動し、下の中身を最初の入力として貼り付ける")


NOTICE_INF = ("NDF_RELAY_QUIET が有限でないため、ラッパーは自動で切り替えない。"
              "/exit してから claude を起動し、下の中身を最初の入力として貼り付ける")


def notice_in(n):
    return f"約 {n} 秒後に自動で新しい会話へ切り替わる。" + WAIT_TAIL


def notice(env_dir, quiet=None):
    e = {k: v for k, v in os.environ.items() if not k.startswith("NDF_")}
    if env_dir is not None:
        e["NDF_RELAY_DIR"] = str(env_dir)
    if quiet is not None:
        e["NDF_RELAY_QUIET"] = quiet
    return subprocess.run([sys.executable, str(RELAY), "notice"], capture_output=True,
                          text=True, env=e, timeout=20)


def notice_lines(proc):
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    lines = proc.stdout.splitlines()
    assert len(lines) == 2, proc.stdout
    return lines


@pytest.mark.parametrize("quiet,expected", [
    (None, notice_in(5)),
    ("6.4", notice_in(6)),
    ("abc", notice_in(5)),
    ("2.5", notice_in(2)),
    ("0", NOTICE_SOON),
    ("-3", NOTICE_SOON),
    ("nan", NOTICE_SOON),
    ("-inf", NOTICE_SOON),
    ("inf", NOTICE_INF),
], ids=["default", "6.4", "abc", "2.5", "0", "-3", "nan", "-inf", "inf"])
def test_notice_under_relay(relay, quiet, expected):
    first, second = notice_lines(notice(relay.dir, quiet))
    assert first == "relay"
    assert second == expected
    assert "約 0 秒後" not in second


def test_notice_without_relay_dir():
    assert notice_lines(notice(None)) == ["outside", NOTICE_OUTSIDE]


def test_notice_relay_not_running(relay):
    relay.release()
    assert notice_lines(notice(relay.dir)) == ["outside", NOTICE_ENDED]


def test_notice_not_direct_child(tmp_path):
    r = Relay(tmp_path, child_pid=1)
    try:
        assert notice_lines(notice(r.dir, "5")) == ["outside", notice_not_child(1)]
    finally:
        r.release()


def test_notice_not_direct_child_without_child_pid(relay):
    # fork したセッションで child.pid が読めなくても、原因と対処を書く（#1016）
    (relay.dir / "child.pid").write_text("x")
    second = notice_lines(notice(relay.dir))[1]
    assert second.startswith("ラッパーは元の会話しか見ていないため")


@pytest.mark.parametrize("case,expected", [
    ("no-dir", NOTICE_OUTSIDE),
    ("not-running", NOTICE_ENDED),
    ("not-child", notice_not_child(999999)),
], ids=["no-dir", "not-running", "not-child"])
def test_notice_outside_reason(relay, case, expected):
    # 外である理由ごとに 2 行目が変わる。1 行目は outside のまま（#1016）
    env_dir = relay.dir
    if case == "no-dir":
        env_dir = None
    elif case == "not-running":
        relay.release()
    else:
        (relay.dir / "child.pid").write_text("999999")
    assert notice_lines(notice(env_dir)) == ["outside", expected]
    assert is_child(env_dir).returncode == 1


def test_notice_broken_relay_dir_is_outside(tmp_path):
    f = tmp_path / "not-a-dir"
    f.write_text("x")
    assert notice_lines(notice(f)) == ["outside", NOTICE_OUTSIDE]


def is_child(env_dir):
    e = {k: v for k, v in os.environ.items() if not k.startswith("NDF_")}
    if env_dir is not None:
        e["NDF_RELAY_DIR"] = str(env_dir)
    return subprocess.run([sys.executable, str(RELAY), "is-child"], capture_output=True,
                          text=True, env=e, timeout=20)


@pytest.mark.parametrize("case,expected", [
    ("relay", "relay"),
    ("no-dir", "outside"),
    ("not-running", "outside"),
    ("not-direct-child", "outside"),
], ids=["relay", "no-dir", "not-running", "not-direct-child"])
def test_is_child_matches_notice(tmp_path, relay, case, expected):
    env_dir = relay.dir
    if case == "no-dir":
        env_dir = None
    elif case == "not-running":
        relay.release()
    elif case == "not-direct-child":
        (relay.dir / "child.pid").write_text("999999")
    first, _ = notice_lines(notice(env_dir))
    assert first == expected
    assert (is_child(env_dir).returncode == 0) == (first == "relay")


# ---------------------------------------------------------------- バージョンディレクトリ（#1142 の C6。F10・I2・I9）


def version_dirs(base):
    base = pathlib.Path(base)
    return sorted(p.name for p in base.iterdir() if p.name.startswith("relay-")) if base.exists() else []


def current_of(base):
    return (pathlib.Path(base) / "relay.current").read_text().strip()


def dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def test_install_places_version_dir_and_copy_runs_from_it(tmp_path, home):
    (home / ".bashrc").write_text("")
    assert relay_cmd(tmp_path, "install").returncode == 0
    ver = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
    name = current_of(cfg(tmp_path))
    assert name.startswith(f"relay-{ver}-") and version_dirs(cfg(tmp_path)) == [name]
    vd = cfg(tmp_path) / name
    for rel in ("relay_lib/__init__.py", "relay_lib/run.py", "lib/clock.py", "lib/jsonio.py"):
        assert (vd / rel).is_file(), rel
    manifest = (vd / "MANIFEST").read_text()
    assert hashlib.sha256(manifest.encode()).hexdigest()[:8] == name.rsplit("-", 1)[1]
    rels = []
    for line in manifest.splitlines():
        digest, rel = line.split("  ", 1)
        rels.append(rel)
        assert hashlib.sha256((vd / rel).read_bytes()).hexdigest() == digest, rel
    assert "relay_lib/common.py" in rels and not any("__pycache__" in r for r in rels)
    # 複製のランチャーは隣に relay_lib が無くても、relay.current のバージョンディレクトリで動く
    p = relay_cmd(tmp_path, "notice", relay=cfg(tmp_path) / "relay.py")
    assert p.returncode == 0 and p.stdout.splitlines()[0] == "outside", p.stderr
    before = [x for x in tree(cfg(tmp_path)) if "__pycache__" not in x]
    assert relay_cmd(tmp_path, "install").returncode == 0
    assert [x for x in tree(cfg(tmp_path)) if "__pycache__" not in x] == before  # 同じ中身なら作り直さない


def test_startup_switches_version_dir(tmp_path, home):
    old = plugin_root(tmp_path, "10.17.5", "old")
    new = plugin_root(tmp_path, "10.18.0", "new")
    assert relay_cmd(tmp_path, "install", relay=old).returncode == 0
    first = current_of(cfg(tmp_path))
    assert first.startswith("relay-10.17.5-")
    assert relay_cmd(tmp_path, "startup", relay=new).returncode == 0
    second = current_of(cfg(tmp_path))
    assert second.startswith("relay-10.18.0-")
    assert version_dirs(cfg(tmp_path)) == sorted([first, second])  # 指されない古いものも 2 つまでは残す
    p = relay_cmd(tmp_path, "notice", relay=cfg(tmp_path) / "relay.py")
    assert p.returncode == 0 and p.stdout.splitlines()[0] == "outside", p.stderr


def test_version_dir_prune_keeps_two_and_in_use(tmp_path):
    base = tmp_path / "ndf"
    base.mkdir()
    names = [f"relay-10.17.{i}-0000000{i}" for i in range(5)]
    for n in names:
        (base / n).mkdir()
    (base / names[0] / f"inuse-{os.getpid()}").touch()  # 動いている run が使う
    dead = dead_pid()
    (base / names[1] / f"inuse-{dead}").touch()  # 終わった run の残り
    for i, n in enumerate(names):
        os.utime(base / n, (1000 + i, 1000 + i))
    (base / f"relay-10.17.9-deadbeef.tmp-{dead}").mkdir()  # 落ちた書きかけ
    name = relay_version_dir.VersionDir(str(base)).ensure("10.18.0")
    assert current_of(base) == name
    assert version_dirs(base) == sorted([names[0], names[3], names[4], name])
    assert (base / names[0] / f"inuse-{os.getpid()}").exists()


def test_version_dir_half_written_is_not_pointed(tmp_path, monkeypatch):
    base = tmp_path / "ndf"
    base.mkdir()
    first = relay_version_dir.VersionDir(str(base)).ensure("10.17.5")
    real = shutil.copyfile
    n = []

    def boom(src, dst, *a, **k):
        n.append(src)
        if len(n) > 2:
            raise OSError("disk full")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(relay_version_dir.shutil, "copyfile", boom)
    with pytest.raises(OSError):
        relay_version_dir.VersionDir(str(base)).ensure("10.18.0")
    monkeypatch.undo()
    assert current_of(base) == first
    assert version_dirs(base) == [first]  # 書きかけは残らず、指されもしない
    second = relay_version_dir.VersionDir(str(base)).ensure("10.18.0")
    assert current_of(base) == second and version_dirs(base) == sorted([first, second])


def test_version_dir_claim_marks_only_version_dirs(tmp_path):
    base = tmp_path / "ndf"
    base.mkdir()
    name = relay_version_dir.VersionDir(str(base)).ensure("10.18.0")
    got = relay_version_dir.claim_inuse(str(base / name))
    assert got == str(base / name / f"inuse-{os.getpid()}") and os.path.exists(got)
    relay_version_dir.release_inuse(got)
    assert not os.path.exists(got)
    assert relay_version_dir.claim_inuse(str(ROOT / "scripts")) is None  # プラグインのキャッシュには置かない


def test_uninstall_removes_version_dirs(tmp_path, home):
    (home / ".bashrc").write_text("")
    assert relay_cmd(tmp_path, "install").returncode == 0
    assert version_dirs(cfg(tmp_path))
    p = relay_cmd(tmp_path, "uninstall")
    assert p.returncode == 0, p.stdout
    assert version_dirs(cfg(tmp_path)) == [] and not (cfg(tmp_path) / "relay.current").exists()


def test_startup_old_copy_runs_from_its_version_dir(tmp_path, home):
    old = home / ".local" / "share" / "ndf" / "relay.py"
    old.parent.mkdir(parents=True)
    old.write_text("# 10.17.4〜10.17.6 の複製\n")
    assert run_hook(tmp_path, HOOK_STARTUP).returncode == 0
    assert old.read_bytes() == RELAY.read_bytes()
    p = relay_cmd(tmp_path, "notice", relay=old)
    assert p.returncode == 0 and p.stdout.splitlines()[0] == "outside", p.stderr


def test_launcher_without_relay_lib_says_so(tmp_path, home):
    lone = tmp_path / "lone" / "relay.py"
    lone.parent.mkdir()
    lone.write_bytes(RELAY.read_bytes())
    p = relay_cmd(tmp_path, "notice", relay=lone)
    assert p.returncode == 1 and p.stderr.startswith("ndf-relay:"), p.stderr


def test_version_dir_imports_only_stdlib_and_itself():
    """バージョンディレクトリの中身とランチャーは、標準ライブラリとバージョンディレクトリの中だけを import する（I2）。"""
    allowed = set(sys.stdlib_module_names) | {"clock", "jsonio", "relay_lib", "__future__"}
    files = [RELAY, ROOT / "scripts" / "lib" / "clock.py", ROOT / "scripts" / "lib" / "jsonio.py",
             *sorted((ROOT / "scripts" / "relay_lib").glob("*.py"))]
    for f in files:
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                names = [node.module or ""]
            else:
                continue
            for n in names:
                assert n.split(".")[0] in allowed, (f.name, n)
