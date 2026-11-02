"""統括役（#2346）: 同じコンテナの複数のラッパーを 1 つの統括役から起動し、1 つの表で見て 1 本ずつ止める。

前半は統括役の判定（名前・重複・上限・引き継ぎ・権限）を状態ディレクトリだけで確かめる。後半は擬似端末と
使い捨ての tmux（`TMUX_TMPDIR` を一時のディレクトリへ向け、利用者の tmux サーバに触れない）で、起動・一覧・停止・
引き継ぎを確かめる。
"""

from __future__ import annotations

import fcntl
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import step_result  # noqa: E402
import wait_notice  # noqa: E402
from relay_lib import hub as hb  # noqa: E402
from relay_lib import hub_client as hc  # noqa: E402
from relay_lib import tmux as tx  # noqa: E402

from test_relay import RELAY, Term, fence, isolated_env, stop_input  # noqa: E402


@pytest.fixture()
def state(tmp_path, monkeypatch):
    """状態ディレクトリを一時の置き場へ向け、tmux は無いものとして始める。"""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("NDF_RELAY_TMUX", "ndf-no-tmux")
    monkeypatch.delenv("NDF_RELAY_MAX_NAMED", raising=False)
    root = tmp_path / "state" / "ndf" / "relay"
    root.mkdir(parents=True)
    return root


class Dir:
    """作業ディレクトリ 1 つ。`running` なら `relay.lock` を持つ。"""

    def __init__(self, root, key, name=None, running=True, section=1, question=False, **start):
        self.path = root / key
        self.path.mkdir(mode=0o700)
        if name:
            (self.path / "name").write_text(name + "\n")
        row = {"event": "start", "at": "2026-11-02T00:00:00.000Z", "section": section, "command": "最初", "cwd": str(root), **start}
        (self.path / "log.jsonl").write_text(json.dumps(row, ensure_ascii=False) + "\n")
        if question:
            (self.path / "question").touch()
        self.lock = None
        (self.path / "relay.lock").touch()
        if running:
            self.lock = open(self.path / "relay.lock", "a")
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release(self):
        if self.lock is not None:
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            self.lock.close()
            self.lock = None


@pytest.fixture()
def dirs(state):
    made = []

    def make(key, **kw):
        d = Dir(state, key, **kw)
        made.append(d)
        return d

    yield make
    for d in made:
        d.release()


def judge_now(name, sessions=None):
    ws = hb.scan()
    return hb.Hub.judge(name, hb.live(ws, hb.read_registry(), sessions or {}))


# ---------------------------------------------------------------- 判定（I2・I3・I7・AC6・AC15・AC16）


@pytest.mark.parametrize("name", ["a.b", "A", "x" * 33, "-a", "a:b", ""])
def test_bad_name_is_refused(state, name):
    r = judge_now(name)
    assert r is not None and r.reason == hb.BAD_NAME and r.code == 3


def test_running_name_is_taken(dirs):
    dirs("d1", name="lroot")
    r = judge_now("lroot")
    assert r.reason == hb.NAME_TAKEN and r.code == 3


def test_default_limit_is_three_and_counts_every_running_wrapper(dirs):
    """上限の既定は 3。統括役・名前つき・名前の無い今のラッパーを、動いているものすべてで数える。"""
    assert hb.max_named() == 3
    dirs("front", name="t1")
    dirs("named", name="lroot")
    dirs("plain")
    dirs("gone", name="old", running=False)
    r = judge_now("lflow")
    assert r.reason == hb.LIMIT and r.code == 3
    assert "3" in r.message and "t1" in r.message and "lroot" in r.message and "-" in r.message


def test_limit_counts_launched_before_lock(dirs, monkeypatch):
    """登録簿の行と tmux のセッションだけが在る（起動した直後でロックの前）ラッパーも数える。"""
    monkeypatch.setenv("NDF_RELAY_MAX_NAMED", "2")
    dirs("front", name="t1")
    hb.ensure_dirs()
    row = {"session": "a", "cwd": "/", "args": [], "created_at": "x", "by": "block:x"}
    pathlib.Path(hb.hub_path(hb.REGISTRY)).write_text(json.dumps({"version": 1, "entries": {"a": row}}))
    assert judge_now("b", sessions={"a": {"attached": False, "activity": None, "pid": None}}).reason == hb.LIMIT
    # セッションが無い（起動に失敗した・止まった）行は数えない。上限を通って tmux の有無の判定へ進む
    assert judge_now("b", sessions={}).reason == hb.NO_TMUX


def test_auto_name_takes_smallest_free_number():
    assert hb.Hub.auto_name({"t1"}) == "t2"
    assert hb.Hub.auto_name({"t1", "t2", "t4"}) == "t3"


# ---------------------------------------------------------------- 統括役の役目（I1・AC21・AC22）


def test_only_one_hub_claims(state):
    first = hb.Hub.claim()
    try:
        assert first is not None
        assert hb.Hub.claim() is None
        assert hb.hub_running()
    finally:
        first.release()
    assert not hb.hub_running()
    again = hb.Hub.claim()
    assert again is not None
    again.release()


def test_hub_dir_is_not_a_relay_dir(state):
    """統括役の置き場は一覧・停止・起動の数の走査に作業ディレクトリと読まれない。"""
    h = hb.Hub.claim()
    try:
        assert hb.scan() == []
        assert subprocess.run([sys.executable, str(RELAY), "stop"], env=_env(state), capture_output=True).returncode == 1
    finally:
        h.release()


def _env(state, **extra):
    e = isolated_env(state.parents[2], XDG_STATE_HOME=state.parents[1], **extra)
    return e


# ---------------------------------------------------------------- 受付箱（I4・I8・決定 14）


def test_request_without_hub_times_out_and_withdraws(state, monkeypatch):
    monkeypatch.setenv("NDF_RELAY_HUB_WAIT", "0.2")
    assert hc.request("spawn", "a", "/", ["--", "x"], "spawn") is None
    assert os.listdir(hb.hub_path(hb.REQUESTS)) == []
    assert os.stat(hb.hub_path(hb.REQUESTS)).st_mode & 0o777 == 0o700


def test_hub_skips_request_that_others_can_read(state):
    rid = hc.put_request("spawn", "a", "/", ["--", "x"], "spawn")
    p = hb.hub_path(hb.REQUESTS, f"{rid}.json")
    assert os.stat(p).st_mode & 0o777 == 0o600
    os.chmod(p, 0o644)
    h = hb.Hub(None)
    h.serve_once()
    assert not os.path.exists(hb.hub_path(hb.RESULTS, f"{rid}.json"))
    log = [json.loads(x) for x in pathlib.Path(hb.hub_path(hb.HUB_LOG)).read_text().splitlines()]
    assert log[-1]["event"] == "refused"


def test_hub_answers_refusal_and_keeps_registry_untouched(dirs):
    dirs("d1", name="lroot")
    rid = hc.put_request("spawn", "lroot", "/", ["--", "x"], "spawn")
    hb.Hub(None).serve_once()
    res = json.loads(pathlib.Path(hb.hub_path(hb.RESULTS, f"{rid}.json")).read_text())
    assert step_result.validate_result(res, 3) == []
    assert res["items"][0]["result"] == hb.NAME_TAKEN
    assert not os.path.exists(hb.hub_path(hb.REGISTRY))


def test_block_request_cannot_add_options(dirs, monkeypatch):
    """起動のブロック・spawn の入力は `--` の後の 1 つの入力だけになる（選択肢を足させない）。"""
    seen = {}
    monkeypatch.setattr(hb.tmux, "binary", lambda: "/bin/true")
    monkeypatch.setattr(hb.tmux, "new_session", lambda name, cwd, argv, env: seen.update(argv=argv, env=env))
    rid = hc.put_request("spawn", "a", "/", ["--dangerously-skip-permissions"], "block:/x")
    hb.Hub(None).serve_once()
    assert seen["argv"][-2:] == ["--", "--dangerously-skip-permissions"]
    assert seen["env"] == {"NDF_RELAY_NAME": "a", "NDF_RELAY_SPAWNED": "1"}
    res = json.loads(pathlib.Path(hb.hub_path(hb.RESULTS, f"{rid}.json")).read_text())
    assert res["status"] == "ok"
    assert hb.read_registry()["a"]["by"] == "block:/x"


# ---------------------------------------------------------------- 起動のブロック（AC5・決定 6）


def test_spawn_block_places_request_without_ending_section(tmp_path, monkeypatch):
    from test_relay import FakeRelayDir, mark

    r = FakeRelayDir(tmp_path)
    try:
        msg = "3 本を始める\n\n" + fence("/goal lroot", "ndf-spawn lroot") + "\n\n" + fence("/goal lflow", "ndf-spawn lflow")
        p = mark(r.dir, stop_input(msg), env={"XDG_STATE_HOME": str(tmp_path / "state")})
        assert p.returncode == 0, p.stderr
        assert not r.next.exists()
        box = tmp_path / "state" / "ndf" / "relay" / "hub" / "requests"
        reqs = sorted((json.loads(f.read_text()) for f in box.iterdir()), key=lambda q: q["name"])
        assert sorted((q["name"], q["args"]) for q in reqs) == [("lflow", ["--", "/goal lflow"]), ("lroot", ["--", "/goal lroot"])]
        assert all(q["by"] == f"block:{r.dir}" for q in reqs)
    finally:
        r.release()


# ---------------------------------------------------------------- 一覧（AC9・AC10・AC18・I10）


def test_ls_json_lists_state_from_files(dirs, capsys):
    dirs("a", name="lroot", account="work1", plugin_version="10.27.7")
    dirs("b", name="lflow", question=True, section=3)
    dirs("c")
    dirs("d", name="old", running=False)  # 登録簿に無い止まった名前は出さない
    assert hc.cmd_ls(["--json"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    res = json.loads(out)
    assert step_result.validate_result(res, 0) == [] and res["tool"] == "relay-ls"
    rows = {it["name"]: it for it in res["items"]}
    assert set(rows) == {"lroot", "lflow", "-"}
    assert rows["lroot"]["state"] == "running" and rows["lroot"]["account"] == "work1"
    assert rows["lroot"]["ndf_version"] == "10.27.7" and rows["lroot"]["sections"] == 1
    assert rows["lflow"]["state"] == "awaiting_approval" and rows["lflow"]["awaiting_approval"] is True
    assert rows["lflow"]["sections"] == 3
    for it in res["items"]:
        assert set(it) == {"name", "state", "awaiting_approval", "sections", "cost_usd", "at", "account", "ndf_version", "attached", "hub"}
        assert it["cost_usd"] is None and "最初" not in json.dumps(it, ensure_ascii=False)
    assert res["metrics"] == {"running": 2, "awaiting_approval": 1, "stopped": 0, "limit": 3}
    assert hc.cmd_ls([]) == 0
    table = capsys.readouterr().out.splitlines()
    assert table[0].split()[0] == "名前" and len(table) == 4


def test_ls_question_cleared_returns_to_running(dirs):
    d = dirs("a", name="lroot", question=True)
    assert {it["name"]: it["state"] for it in hc.list_items()}["lroot"] == "awaiting_approval"
    (d.path / "question").unlink()
    assert {it["name"]: it["state"] for it in hc.list_items()}["lroot"] == "running"


# ---------------------------------------------------------------- 個別の操作（AC11・AC13・決定 10）


@pytest.mark.parametrize("cmd", [["stop", "nope"], ["restart", "nope"], ["attach", "nope"]])
def test_unknown_name_changes_nothing(state, cmd):
    p = subprocess.run([sys.executable, str(RELAY), *cmd], env=_env(state), capture_output=True, text=True, timeout=30)
    assert p.returncode == 3, p.stderr
    assert "その名前は無い" in p.stderr
    assert not os.path.exists(hb.hub_path(hb.REQUESTS)) or os.listdir(hb.hub_path(hb.REQUESTS)) == []


def _holder(path):
    """`relay.lock` を持ち、停止シグナルファイルを見たら終わるラッパーに見立てたプロセス。"""
    code = (
        "import fcntl,os,sys,time\n"
        "d=sys.argv[1]; f=open(os.path.join(d,'relay.lock'),'a'); fcntl.flock(f,fcntl.LOCK_EX)\n"
        "open(os.path.join(d,'relay.pid'),'w').write(str(os.getpid()))\n"
        "while not os.path.exists(os.path.join(d,'stop')): time.sleep(0.05)\n"
    )
    p = subprocess.Popen([sys.executable, "-c", code, str(path)])
    end = time.time() + 10
    while not (path / "relay.pid").exists() and time.time() < end:
        time.sleep(0.05)
    return p


def test_stop_name_stops_only_that_one(dirs, capsys):
    a = dirs("a", name="a", running=False)
    b = dirs("b", name="b", running=False)
    pa, pb = _holder(a.path), _holder(b.path)
    try:
        assert hc.cmd_stop_named("b", True) == 0
        res = json.loads(capsys.readouterr().out)
        assert res["tool"] == "relay-stop" and res["status"] == "ok"
        pb.wait(10)
        assert {it["name"]: it["state"] for it in hc.list_items()} == {"a": "running"}
        assert not (a.path / "stop").exists()
    finally:
        for p in (pa, pb):
            p.kill()
            p.wait()


def test_restart_input_uses_last_section(dirs, tmp_path):
    row = {"args": ["--model", "x", "--", "最初"], "cwd": str(tmp_path)}
    w1 = hb.by_name(hb.scan()).get("none")
    assert hb.restart_input("a", row, w1) == (str(tmp_path), ["--model", "x", "--", "最初"])
    wt = tmp_path / "wt"
    wt.mkdir()
    dirs("a", name="a", running=False, section=2, command="/goal 続き", cwd=str(wt))
    w2 = hb.by_name(hb.scan())["a"]
    assert hb.restart_input("a", row, w2) == (str(wt), ["--model", "x", "/goal 続き"])


# ---------------------------------------------------------------- 待ち通知（AC19・決定 7）


def test_wait_notice_names_wrapper_and_attach():
    loc = wait_notice.build_locator("claude", {"session_id": "s1"}, {"NDF_RELAY_NAME": "lroot"}, "h", "/w")
    assert loc.lines()[0] == "ラッパー: lroot（relay.py attach lroot）"
    plain = wait_notice.build_locator("claude", {"session_id": "s1"}, {}, "h", "/w")
    assert not any(line.startswith("ラッパー:") for line in plain.lines())


# ---------------------------------------------------------------- tmux を通す振る舞い（AC2〜AC4・AC6・AC7・AC11・AC16・AC20・AC21・AC23）

needs_tmux = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux が無い")


@pytest.fixture()
def hubenv(tmp_path):
    """統括役を擬似端末で起動する環境。tmux のソケットは使い捨ての `TMUX_TMPDIR`（短いパス）に置く。"""
    sock = tempfile.mkdtemp(prefix="ndf-tests-tmux-", dir="/tmp")
    made = []
    env = {"NDF_RELAY_TMUX": "tmux", "TMUX_TMPDIR": sock, "NDF_RELAY_HUB_WAIT": "15"}

    def start(**kw):
        t = Term(tmp_path, env={**env, **kw.pop("env", {})}, **kw)
        made.append(t)
        return t

    yield start
    for t in made:
        t.close()
    subprocess.run(["tmux", "-L", tx.SOCKET, "kill-server"], env={**os.environ, "TMUX_TMPDIR": sock}, capture_output=True)
    shutil.rmtree(sock, ignore_errors=True)


def relay_cli(t, *args, timeout=40):
    return subprocess.run([sys.executable, str(RELAY), *args], env=t.env, capture_output=True, text=True, timeout=timeout, cwd=t.fake_dir)


def ls_rows(t):
    p = relay_cli(t, "ls", "--json")
    assert p.returncode == 0, p.stderr
    return {it["name"]: it for it in json.loads(p.stdout)["items"]}


def wait_state(t, name, state, timeout=30):
    t.wait(lambda: (ls_rows(t).get(name) or {}).get("state") == state, timeout, f"{name} が {state}")


@needs_tmux
def test_spawn_ls_stop_and_hub_handover(hubenv):
    hub = hubenv()
    hub.wait_start(1)
    p = relay_cli(hub, "spawn", "--name", "lroot", "--json", "--", "mark", "/goal 次")
    assert p.returncode == 0, p.stdout + p.stderr
    assert json.loads(p.stdout)["status"] == "ok"
    wait_state(hub, "lroot", "running")
    # 最初の入力を受けた区間 1 と、ndf-next で起動した区間 2（端末の結び付いていないラッパーの切り替え。AC3・AC8）
    hub.wait(lambda: any(s["argv"][-1:] == ["/goal 次"] for s in hub.starts()), 40, "lroot の区間 2")
    first = next(s for s in hub.starts() if s["argv"][-2:] == ["--", "mark /goal 次"])
    rows = ls_rows(hub)
    assert rows["t1"]["hub"] is True and rows["t1"]["attached"] is None
    assert rows["lroot"]["hub"] is False and rows["lroot"]["attached"] is False
    assert first["relay_dir"] != hub.starts()[0]["relay_dir"]

    dup = relay_cli(hub, "spawn", "--name", "lroot", "--", "x")
    assert dup.returncode == 3 and "名前が重なる" in dup.stderr

    # 統括役を kill しても名前つきは動き続け、次の claude が統括役になる（AC21）
    hub.proc.kill()
    hub.proc.wait(5)
    hub.wait(lambda: "t1" not in ls_rows(hub), 20, "統括役のラッパーが一覧から消える")
    assert ls_rows(hub)["lroot"]["state"] == "running"
    nxt = hubenv()
    nxt.wait(lambda: hb_pid(nxt) == nxt.proc.pid, 20, "次の claude が統括役になる")
    assert "lroot" in ls_rows(nxt)

    st = relay_cli(nxt, "stop", "lroot")
    assert st.returncode == 0, st.stderr
    wait_state(nxt, "lroot", "stopped")
    assert ls_rows(nxt)["t1"]["state"] == "running"


def hb_pid(t):
    p = pathlib.Path(t.env["HOME"]) / ".local" / "state" / "ndf" / "relay" / "hub" / "hub.json"
    try:
        return json.loads(p.read_text())["pid"]
    except (OSError, ValueError, KeyError):
        return None


@needs_tmux
def test_second_claude_attaches_and_survives_terminal_close(hubenv):
    hub = hubenv()
    hub.wait_start(1)
    second = hubenv()
    hub.wait(lambda: (ls_rows(hub).get("t2") or {}).get("attached") is True, 40, "2 本目が t2 へ結び付く")
    assert ls_rows(hub)["t2"]["state"] == "running"
    second.close()  # 端末を閉じる（AC7）
    hub.wait(lambda: ls_rows(hub)["t2"]["attached"] is False, 20, "端末が外れる")
    assert ls_rows(hub)["t2"]["state"] == "running"


@needs_tmux
def test_limit_refuses_from_terminal_and_spawn(hubenv):
    hub = hubenv(env={"NDF_RELAY_MAX_NAMED": "2"})
    hub.wait_start(1)
    assert relay_cli(hub, "spawn", "--name", "a", "--", "x").returncode == 0
    p = relay_cli(hub, "spawn", "--name", "b", "--json", "--", "x")
    res = json.loads(p.stdout)
    assert p.returncode == 3 and res["items"][0]["result"] == "limit" and "上限 2" in res["summary"]
    third = hubenv(env={"NDF_RELAY_MAX_NAMED": "2"})
    assert third.finish() == 3
    assert "上限 2" in third.text


@needs_tmux
def test_killed_named_wrapper_shows_stopped(hubenv):
    hub = hubenv()
    hub.wait_start(1)
    assert relay_cli(hub, "spawn", "--name", "a", "--", "x").returncode == 0
    wait_state(hub, "a", "running")
    d = next(it for it in hb_scan(hub) if it[1] == "a")[0]
    os.kill(int((d / "relay.pid").read_text()), 9)
    wait_state(hub, "a", "stopped")
    assert ls_rows(hub)["t1"]["state"] == "running"


def hb_scan(t):
    root = pathlib.Path(t.env["HOME"]) / ".local" / "state" / "ndf" / "relay"
    return [(d, (d / "name").read_text().strip() if (d / "name").exists() else None) for d in root.iterdir() if d.name != "hub" and d.is_dir()]


def test_no_tmux_runs_second_in_foreground(tmp_path):
    """tmux が PATH に無ければ、2 本目は 1 行出して今の前景の起動になる（AC23）。"""
    hub = Term(tmp_path, env={"NDF_RELAY_TMUX": "ndf-no-tmux"})
    second = None
    try:
        hub.wait_start(1)
        second = Term(tmp_path, env={"NDF_RELAY_TMUX": "ndf-no-tmux"})
        second.wait(lambda: len(hub.starts()) >= 2, 20, "2 本目が前景で起動する")
        assert "tmux が PATH に無いため統括しない" in second.text
    finally:
        for t in (hub, second):
            if t is not None:
                t.close()
