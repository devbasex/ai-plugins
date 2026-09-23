"""区間の切れ目で claude を起動し直す中継（#895）。

`relay.py` は 4 つの副命令を持つ。

- `mark`: Stop hook の本体。最後の応答の `ndf-next` のブロックを印 `next.json` へ写す
- `run`: 端末の前景に常駐し、claude を擬似端末の子として起動する。要らなければ素通しする
- `stop`: 動いている中継に停止の印を置く
- `install`: 中継を安定した場所へ置き直し、シェルの設定へ alias を 1 度だけ足す

**利用者の手元の設定は書き換えない。** `install` と `run` は、テストごとの一時の HOME と
`XDG_*` の下でだけ動かす（`isolated_env`）。
"""
from __future__ import annotations

import fcntl
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
RELAY = ROOT / "scripts" / "relay.py"


def isolated_env(tmp_path, **extra):
    """一時の HOME と XDG_* だけを持つ環境。本物の HOME を指さないことを確かめてから返す。"""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    e = {k: v for k, v in os.environ.items()
         if not k.startswith(("NDF_", "XDG_", "CLAUDE")) and k not in ("ZDOTDIR",)}
    e["HOME"] = str(home)
    e.update({k: str(v) for k, v in extra.items()})
    real_home = os.path.expanduser("~")
    assert e["HOME"] != real_home
    return e


def fence(body, info="ndf-next", ticks=3):
    return f"{'`' * ticks}{info}\n{body}\n{'`' * ticks}"


# ---------------------------------------------------------------- mark（AC3 / AC4 / AC4b / AC24）


class Relay:
    """動いている中継に見立てた作業ディレクトリ。`relay.lock` を持ち、`child.pid` を書く。"""

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


def test_mark_not_direct_child_claude_keeps_mark(tmp_path, relay):
    """conductor が Bash から起こした `claude -p` の Stop は、前の印を消さない（AC4b）。"""
    relay.next.write_text('{"command": "keep"}')
    # 名前が claude のラッパーを間に挟む。mark から見て最初の claude はこのラッパーになる
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


def test_mark_clears_previous_when_no_block(relay):
    quiet_ok(mark(relay.dir, stop_input(fence("/goal x"))))
    assert relay.next.exists()
    quiet_ok(mark(relay.dir, stop_input("続きの応答")))
    assert not relay.next.exists()


def test_mark_background_running_clears(relay):
    """背景の処理が動いているあいだは印を書かず、前の印を消す（AC24）。"""
    quiet_ok(mark(relay.dir, stop_input(fence("/goal x"))))
    running = [{"id": "b1", "type": "shell", "status": "running"}]
    quiet_ok(mark(relay.dir, stop_input(fence("/goal x"), background_tasks=running)))
    assert not relay.next.exists()
    done = [{"id": "b1", "type": "shell", "status": "completed"}]
    quiet_ok(mark(relay.dir, stop_input(fence("/goal x"), background_tasks=done)))
    assert relay.next.exists()


def test_mark_file_is_private(relay):
    quiet_ok(mark(relay.dir, stop_input(fence("/goal x"))))
    assert relay.next.stat().st_mode & 0o077 == 0


# ---------------------------------------------------------------- run（擬似端末の上で動かす）

import pty
import struct
import signal
import termios
import threading
import time

FAKE = ROOT / "scripts" / "tests" / "fixtures" / "relay_fake_claude.py"

FAST = {"NDF_RELAY_QUIET": "0.3", "NDF_RELAY_POLL": "0.1", "NDF_RELAY_EXIT_GAP": "0.1",
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
            raise AssertionError(f"中継が終わらない\n画面: {self.text[-2000:]}\n記録: {self.rows()}")

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
    """印が無いまま claude が終わると、中継も何も出さずに同じ終了コードで終わる（AC12・AC19）。"""
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
    """印を受けると /exit と \\r を入力し、更新して次の区間を起動する（AC6・AC8・AC19）。"""
    t = term("--model", "haiku", env={"FAKE_VERSION_AFTER": "2.0.0"})
    t.wait_start(1)
    t.type("mark /goal 次の段\r")
    t.wait_start(2)
    first, second = t.starts()[:2]
    assert second["argv"] == ["/goal 次の段"]
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
    assert s2["command"] == "/goal 次の段"
    assert s2["from_session"] == f"s{first['pid']}"
    assert s2["section"] == 2 and s2["pid"] == second["pid"]
    assert e1["ended_by"] == "mark" and e1["section"] == 1
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
    """利用者の入力から静まりの秒数がたつまで /exit を送らない（AC6）。"""
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
    """印が消えたら（次の Stop にブロックが無い）切り替えない（AC4b・AC6）。"""
    t = term(env={"NDF_RELAY_QUIET": "1"})
    t.wait_start(1)
    t.type("mark x\r")
    d = pathlib.Path(t.starts()[0]["relay_dir"])
    t.wait(lambda: (d / "next.json").exists(), what="印")
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
        "m.Relay._tick = boom\n"
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
        if k.startswith(("NDF_", "XDG_", "CLAUDE")):
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
        mod.cmd_run(args)
    path, argv, env = mod.calls[0]
    assert path == str(claude)
    assert argv == [str(claude)] + args
    diff = {k for k in set(env) | set(before) if env.get(k) != before.get(k)}
    assert diff == {"NDF_RELAY_DEPTH"}
    assert env["NDF_RELAY_DEPTH"] == "1"
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


@pytest.mark.parametrize("case", ["no-pty", "list-fails", "no-ndf", "list-hangs"])
def test_run_cannot_start_says_then_passthrough(mod, tmp_path, monkeypatch, capsys, case):
    claude = tmp_path / "bin" / "claude"
    claude.parent.mkdir()
    body = {"list-fails": "exit 1", "no-ndf": "echo '[{\"id\": \"x@y\", \"version\": \"1\"}]'",
            "list-hangs": "sleep 30"}.get(case, "echo '[{\"id\": \"ndf@m\", \"version\": \"1\"}]'")
    claude.write_text(f"#!/bin/sh\n{body}\n")
    claude.chmod(0o755)
    monkeypatch.setenv("NDF_RELAY_CLAUDE", str(claude))
    monkeypatch.setenv("NDF_RELAY_LIST_TIMEOUT", "0.5")
    monkeypatch.setattr(mod.os, "isatty", lambda fd: True)
    if case == "no-pty":
        monkeypatch.setitem(sys.modules, "pty", None)
    with pytest.raises(Execd):
        mod.cmd_run([])
    err = capsys.readouterr().err
    assert err.startswith("ndf-relay: 中継を始めない（") and err.count("\n") == 1
    assert mod.calls[0][1] == [str(claude)]


def test_resolve_skips_wrappers(mod, tmp_path, monkeypatch):
    """`claude` という名前で中継を呼ぶラッパーを飛ばし、本物を選ぶ（AC20）。"""
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
    assert mod.resolve_claude() == str(real)
    forced = real_claude(tmp_path, "forced")
    monkeypatch.setenv("NDF_RELAY_CLAUDE", str(forced))
    assert mod.resolve_claude() == str(forced)


def test_resolve_keeps_unreadable_claude(mod, tmp_path, monkeypatch):
    """読めない（実行だけできる）claude は中継と見なさずに選ぶ。飛ばし損ねは深さの変数が止める。"""
    real = real_claude(tmp_path)
    real.chmod(0o111)
    if os.access(real, os.R_OK):
        pytest.skip("読み取り権限を外せない（root で実行している）")
    monkeypatch.setenv("PATH", os.pathsep.join([str(real.parent), "/usr/bin"]))
    monkeypatch.delenv("NDF_RELAY_CLAUDE", raising=False)
    assert mod.resolve_claude() == str(real)


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
    t.wait(lambda: (d / "next.json").exists(), what="印")
    wt.rmdir()
    t.wait_start(2)
    assert t.starts()[1]["cwd"] == str(main)
    s2 = events(t.rows(), "start")[1]
    assert s2["cwd"] == str(main) and s2["cwd_fallback"] == str(wt)
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
    """残り 1 枠を 2 つの中継が取り合っても、起動するのは 1 つだけ（AC10）。"""
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
        for t in ts:
            t.wait(lambda t=t: any(r["event"] in ("stop",) for r in t.rows())
                   or len(t.starts()) >= 2, what="どちらかに決まる")
        started = sorted(len(t.starts()) for t in ts)
        assert started == [1, 2]
        loser = next(t for t in ts if len(t.starts()) == 1)
        assert [r["reason"] for r in events(loser.rows(), "stop")] == ["max-starts"]
    finally:
        for t in ts:
            t.close()


def test_run_spin_stops_third(term):
    """3 つ続けて短い区間なら 3 つ目の印で切り替えない。1 つ目の区間も数える（AC11）。"""
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
    assert e1["seconds"] < 1.5  # 印の written_at まで。/exit の後の待ちを含めない
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
    t.wait(lambda: events(t.rows(), "stop"), what="停止の印")
    assert events(t.rows(), "stop")[0]["reason"] == "stop-file"
    assert len(t.starts()) == 1
    t.type("/exit\r")
    t.finish()
    p = subprocess.run([sys.executable, str(RELAY), "stop"], capture_output=True, text=True,
                       env=t.env, timeout=20)
    assert p.returncode == 1


def test_make_relay_dir_is_new_each_time(mod, tmp_path, monkeypatch):
    """pid が同じでも作業ディレクトリは起動ごとに新しい。親が無くても作る（AC26）。"""
    monkeypatch.setattr(mod.os, "getpid", lambda: 4242)
    monkeypatch.setattr(mod.time, "gmtime", lambda *a: time.struct_time((2026, 9, 23, 0, 0, 0, 2, 266, 0)))
    a = mod.make_relay_dir()
    (pathlib.Path(a) / "stop").touch()
    (pathlib.Path(a) / "next.json").write_text("{}")
    b = mod.make_relay_dir()
    assert a != b
    assert os.listdir(b) == []
    assert pathlib.Path(b).stat().st_mode & 0o777 == 0o700


def goal_row(sentinel=False, met=False, at=None):
    a = {"type": "goal_status", "met": met, "condition": "c"}
    if sentinel:
        a["sentinel"] = True
    ts = at or time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    return json.dumps({"type": "attachment", "timestamp": ts, "attachment": a})


def test_run_waits_for_goal_judgement(term):
    """目標のある区間では、印の後の目標の判定の記録がそろうまで /exit を送らない（AC6）。"""
    t = term()
    t.wait_start(1)
    t.type(f"tr {goal_row(sentinel=True, at='2026-01-01T00:00:00.000Z')}\r")
    t.type("mark next\r")
    time.sleep(1.5)
    assert len(t.starts()) == 1
    t.type(f"tr {goal_row(met=False)}\r")  # 判定が止めを拒んだ（応答は続く）
    t.wait_start(2)
    t.type("/exit\r")
    t.finish()


def test_goal_pending_reads_attachment_rows(mod, tmp_path):
    tp = tmp_path / "t.jsonl"
    written = time.time()
    old = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(written - 60))
    new = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(written + 1))
    tp.write_text('{"type": "user"}\n')
    assert not mod.goal_pending(str(tp), written)  # 目標の無い会話
    tp.write_text(goal_row(sentinel=True, at=old) + "\n")
    assert mod.goal_pending(str(tp), written)
    tp.write_text(goal_row(sentinel=True, at=old) + "\n" + goal_row(met=False, at=old) + "\n")
    assert mod.goal_pending(str(tp), written)  # 判定が印より前
    tp.write_text(goal_row(sentinel=True, at=old) + "\n" + goal_row(met=False, at=new) + "\n")
    assert not mod.goal_pending(str(tp), written)
    tp.write_text(goal_row(sentinel=True, at=old) + "\n" + goal_row(met=True, at=old) + "\n")
    assert not mod.goal_pending(str(tp), written)  # 目標は終わっている


# ---------------------------------------------------------------- install（AC21）。一時の HOME だけで動かす


def install(tmp_path, relay=RELAY, **env):
    e = isolated_env(tmp_path, **{"SHELL": "/bin/bash", **env})
    return subprocess.run([sys.executable, str(relay), "install"], capture_output=True,
                          text=True, env=e, timeout=20)


def messages(proc):
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr == ""
    return [json.loads(x)["systemMessage"] for x in proc.stdout.splitlines()]


def home_of(tmp_path):
    return tmp_path / "home"


def test_install_first_then_idempotent(tmp_path):
    home = home_of(tmp_path)
    home.mkdir()
    rc = home / ".bashrc"
    rc.write_text("export A=1\n")
    msgs = messages(install(tmp_path))
    assert len(msgs) == 1 and msgs[0].startswith(f"ndf-relay: {rc} へ alias claude を足した")
    copy = home / ".local" / "share" / "ndf" / "relay.py"
    assert copy.read_bytes() == RELAY.read_bytes()
    assert copy.stat().st_mode & 0o777 == 0o755
    body = rc.read_text()
    assert body.startswith("export A=1\n")
    assert body.count("# >>> ndf relay >>>") == 1 and "# <<< ndf relay <<<" in body
    assert """alias claude='python3 "${XDG_DATA_HOME:-$HOME/.local/share}/ndf/relay.py" run'""" in body
    backups = list(home.glob(".bashrc.ndf-bak-*"))
    assert len(backups) == 1 and backups[0].read_text() == "export A=1\n"
    state = home / ".local" / "state" / "ndf" / "relay"
    assert (state / "rc-added").read_text().splitlines() == [str(rc)]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (rc, copy)}
    assert messages(install(tmp_path)) == []
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (rc, copy)} == before
    # 利用者が囲みを消したら足し直さない
    rc.write_text("export A=1\n")
    assert messages(install(tmp_path)) == []
    assert rc.read_text() == "export A=1\n"


def test_install_alias_sources_real_file(tmp_path):
    """足した alias は bash で読み込め、安定した場所の中継を指す。"""
    home = home_of(tmp_path)
    home.mkdir()
    messages(install(tmp_path))
    e = isolated_env(tmp_path)
    p = subprocess.run(["bash", "-c", f"shopt -s expand_aliases; source {home}/.bashrc; alias claude"],
                       capture_output=True, text=True, env=e, timeout=20)
    assert p.returncode == 0, p.stderr
    assert 'ndf/relay.py" run' in p.stdout


@pytest.mark.parametrize("definition", ["alias claude='x'", "claude () { :; }", "function claude { :; }",
                                        "claude() { :; }", "function claude() { :; }"])
def test_install_existing_definition_skips(tmp_path, definition):
    home = home_of(tmp_path)
    home.mkdir()
    rc = home / ".bashrc"
    rc.write_text(definition + "\n")
    msgs = messages(install(tmp_path))
    assert len(msgs) == 1 and "claude の定義があるため alias を足さない" in msgs[0]
    assert messages(install(tmp_path)) == []
    assert rc.read_text() == definition + "\n"
    assert not list(home.glob(".bashrc.ndf-bak-*"))


def test_install_bash_aliases_definition_skips(tmp_path):
    home = home_of(tmp_path)
    home.mkdir()
    (home / ".bash_aliases").write_text("alias claude=foo\n")
    assert len(messages(install(tmp_path))) == 1
    assert not (home / ".bashrc").exists()


def test_install_zsh_uses_zdotdir(tmp_path):
    home = home_of(tmp_path)
    home.mkdir()
    zd = tmp_path / "zd"
    zd.mkdir()
    msgs = messages(install(tmp_path, SHELL="/usr/bin/zsh", ZDOTDIR=zd))
    assert len(msgs) == 1
    assert "# >>> ndf relay >>>" in (zd / ".zshrc").read_text()
    assert not (home / ".zshrc").exists()


def test_install_other_shell_only_copies(tmp_path):
    home = home_of(tmp_path)
    home.mkdir()
    assert messages(install(tmp_path, SHELL="/usr/bin/fish")) == []
    assert (home / ".local" / "share" / "ndf" / "relay.py").exists()
    assert not (home / ".bashrc").exists()


def test_install_disabled(tmp_path):
    home = home_of(tmp_path)
    home.mkdir()
    assert messages(install(tmp_path, NDF_RELAY_AUTO=0)) == []
    assert list(home.iterdir()) == []


def test_install_replaces_changed_copy(tmp_path):
    home = home_of(tmp_path)
    home.mkdir()
    messages(install(tmp_path))
    newer = tmp_path / "newer" / "relay.py"
    newer.parent.mkdir()
    newer.write_bytes(RELAY.read_bytes() + "\n# 新しい版\n".encode())
    messages(install(tmp_path, relay=newer))
    assert (home / ".local" / "share" / "ndf" / "relay.py").read_bytes() == newer.read_bytes()


def test_install_unwritable_exits_zero(tmp_path):
    home = home_of(tmp_path)
    home.mkdir()
    (home / ".local").write_text("ファイルなのでディレクトリを作れない")
    assert messages(install(tmp_path)) == []


def test_install_concurrent_once(tmp_path):
    home = home_of(tmp_path)
    home.mkdir()
    (home / ".bashrc").write_text("x=1\n")
    e = isolated_env(tmp_path, SHELL="/bin/bash")
    procs = [subprocess.Popen([sys.executable, str(RELAY), "install"], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, env=e) for _ in range(4)]
    outs = [p.communicate(timeout=20) for p in procs]
    assert all(p.returncode == 0 for p in procs)
    assert sum(len(o.splitlines()) for o, _ in outs) == 1
    assert (home / ".bashrc").read_text().count("# >>> ndf relay >>>") == 1
    assert len(list(home.glob(".bashrc.ndf-bak-*"))) == 1


def test_install_respects_lock(tmp_path):
    """install.lock を他が持っているあいだは 2 秒まで待ち、取れなければ何もしない。"""
    home = home_of(tmp_path)
    state = home / ".local" / "state" / "ndf" / "relay"
    state.mkdir(parents=True)
    with open(state / "install.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        t0 = time.time()
        assert messages(install(tmp_path)) == []
        assert time.time() - t0 >= 1.5
    assert not (home / ".bashrc").exists()
