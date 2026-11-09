"""リレーハブ（#2346）: 同じコンテナの複数のラッパーを 1 つのリレーハブから起動し、1 つの表で見て 1 本ずつ止める。

前半はリレーハブの判定（名前・重複・上限・引き継ぎ・権限）を状態ディレクトリだけで確かめる。後半は擬似端末と
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

from test_relay import FAKE, RELAY, Term, fence, isolated_env, stop_input  # noqa: E402
from account_fake import accounts, window  # noqa: E402,F401
import claude_accounts as ca  # noqa: E402
from relay_lib import pin  # noqa: E402
from relay_lib import session_env as relay_session_env  # noqa: E402


@pytest.fixture()
def state(tmp_path, monkeypatch):
    """状態ディレクトリを一時の置き場へ向け、tmux は無いものとして始める。"""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("NDF_RELAY_TMUX", "ndf-no-tmux")
    monkeypatch.delenv("NDF_RELAY_MAX_NAMED", raising=False)
    monkeypatch.setenv("NDF_RELAY_LAUNCH_CHECK", "0")
    monkeypatch.setenv("NDF_ACCOUNTS_DIR", str(tmp_path / "accounts"))  # 利用者の置き場（`account use` の指定）を読まない
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
    ws = hb.scan_wrappers()
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
    """上限の既定は 3。リレーハブ・名前つき・名前の無い今のラッパーを、動いているものすべてで数える。"""
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


# ---------------------------------------------------------------- リレーハブの役目（I1・AC21・AC22）


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
    """リレーハブの置き場は一覧・停止・起動の数の走査に作業ディレクトリと読まれない。"""
    h = hb.Hub.claim()
    try:
        assert hb.scan_wrappers() == []
        assert subprocess.run([sys.executable, str(RELAY), "stop"], env=_env(state), capture_output=True).returncode == 1
    finally:
        h.release()


def _env(state, **extra):
    e = isolated_env(state.parents[2], XDG_STATE_HOME=state.parents[1], **extra)
    return e


# ---------------------------------------------------------------- 受付箱（I4・I8・決定 14）


def test_request_without_hub_times_out_and_withdraws(state, monkeypatch):
    monkeypatch.setenv("NDF_RELAY_HUB_WAIT", "0.2")
    assert hc.send_request("spawn", "a", "/", ["--", "x"], "spawn") is None
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
        assert set(it) == {
            "name",
            "state",
            "awaiting_approval",
            "sections",
            "cost_usd",
            "at",
            "account",
            "ndf_version",
            "attached",
            "hub",
            "role",
        }
        assert it["cost_usd"] is None and "最初" not in json.dumps(it, ensure_ascii=False)
    assert res["metrics"] == {
        "running": 2,
        "awaiting_approval": 1,
        "stopped": 0,
        "limit": 3,
        "hub": False,
        "tmux": False,
        "pinned_account": None,
    }
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


def test_restart_of_the_hub_itself_does_not_stop_it(dirs, capsys, monkeypatch):
    # リレーハブを止めると再起動の依頼を受ける者が居なくなる。止めずに断り、区間の切り替え（/ndf:restart）を案内する
    t1 = dirs("t1", name="t1")
    monkeypatch.setattr(hb, "hub_running", lambda: True)
    monkeypatch.setattr(hb, "hub_relay_dir", lambda: str(t1.path))
    stopped = []
    monkeypatch.setattr(hc, "stop_named", lambda name: stopped.append(name) or True)
    assert hc.cmd_restart(["t1", "--json"]) == 3
    res = json.loads(capsys.readouterr().out)
    assert res["status"] == "stopped" and res["items"][0]["result"] == "hub_self" and stopped == []
    assert not (t1.path / "stop").exists()


def test_restart_input_uses_last_section(dirs, tmp_path):
    row = {"args": ["--model", "x", "--", "最初"], "cwd": str(tmp_path)}
    w1 = hb.by_name(hb.scan_wrappers()).get("none")
    assert hb.restart_input("a", row, w1) == (str(tmp_path), ["--model", "x", "--", "最初"])
    wt = tmp_path / "wt"
    wt.mkdir()
    dirs("a", name="a", running=False, section=2, command="/goal 続き", cwd=str(wt))
    w2 = hb.by_name(hb.scan_wrappers())["a"]
    assert hb.restart_input("a", row, w2) == (str(wt), ["--model", "x", "/goal 続き"])


# ---------------------------------------------------------------- 待ち通知（AC19・決定 7）


def test_wait_notice_names_wrapper_and_attach():
    loc = wait_notice.build_locator("claude", {"session_id": "s1"}, {"NDF_RELAY_NAME": "lroot"}, "h", "/w")
    assert loc.lines()[0] == "ラッパー: lroot（relay.py attach lroot）"
    plain = wait_notice.build_locator("claude", {"session_id": "s1"}, {}, "h", "/w")
    assert not any(line.startswith("ラッパー:") for line in plain.lines())


# ---------------------------------------------------------------- tmux を通す振る舞い（AC2〜AC4・AC6・AC7・AC11・AC16・AC20・AC21・AC23）


@pytest.fixture()
def hubenv(tmp_path):
    """リレーハブを擬似端末で起動する環境。tmux のソケットは使い捨ての `TMUX_TMPDIR`（短いパス）に置く。"""
    sock = tempfile.mkdtemp(prefix="ndf-tests-tmux-", dir="/tmp")
    made = []
    # CI には TERM が無く、tmux の attach が端末の種類を決められずに失敗する。擬似端末に実際の端末と同じく TERM を与える
    env = {
        "NDF_RELAY_TMUX": "tmux",
        "TMUX_TMPDIR": sock,
        "NDF_RELAY_HUB_WAIT": "15",
        "TERM": "xterm-256color",
        "NDF_RELAY_LAUNCH_CHECK": "0.2",
    }

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


def hb_pid(t):
    p = pathlib.Path(t.env["HOME"]) / ".local" / "state" / "ndf" / "relay" / "hub" / "hub.json"
    try:
        return json.loads(p.read_text())["pid"]
    except (OSError, ValueError, KeyError):
        return None


def hb_scan(t):
    root = pathlib.Path(t.env["HOME"]) / ".local" / "state" / "ndf" / "relay"
    return [
        (d, (d / "name").read_text().strip() if (d / "name").exists() else None) for d in root.iterdir() if d.name != "hub" and d.is_dir()
    ]


class TestWithTmux:
    """tmux を使う振る舞い。tmux の有無は根の `conftest.py` の前提のコマンドの一覧がこのクラスの単位で決める。"""

    def test_spawn_ls_stop_and_hub_handover(self, hubenv):
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

        # リレーハブを kill しても名前つきは動き続け、次の claude がリレーハブになる（AC21）
        hub.proc.kill()
        hub.proc.wait(5)
        hub.wait(lambda: "t1" not in ls_rows(hub), 20, "リレーハブのラッパーが一覧から消える")
        assert ls_rows(hub)["lroot"]["state"] == "running"
        nxt = hubenv()
        nxt.wait(lambda: hb_pid(nxt) == nxt.proc.pid, 20, "次の claude がリレーハブになる")
        assert "lroot" in ls_rows(nxt)

        st = relay_cli(nxt, "stop", "lroot")
        assert st.returncode == 0, st.stderr
        wait_state(nxt, "lroot", "stopped")
        assert ls_rows(nxt)["t1"]["state"] == "running"

    def test_second_claude_attaches_and_survives_terminal_close(self, hubenv):
        hub = hubenv()
        hub.wait_start(1)
        second = hubenv()
        hub.wait(lambda: (ls_rows(hub).get("t2") or {}).get("attached") is True, 40, "2 本目が t2 へ結び付く")
        assert ls_rows(hub)["t2"]["state"] == "running"
        second.close()  # 端末を閉じる（AC7）
        hub.wait(lambda: ls_rows(hub)["t2"]["attached"] is False, 20, "端末が外れる")
        assert ls_rows(hub)["t2"]["state"] == "running"

    def test_limit_refuses_from_terminal_and_spawn(self, hubenv):
        """上限は AI の起動（spawn）を断り、端末から打った起動は断らずに知らせの 1 行を出して起動する（#2862 の AC1・AC9）。"""
        hub = hubenv(env={"NDF_RELAY_MAX_NAMED": "2"})
        hub.wait_start(1)
        assert relay_cli(hub, "spawn", "--name", "a", "--", "x").returncode == 0
        p = relay_cli(hub, "spawn", "--name", "b", "--json", "--", "x")
        res = json.loads(p.stdout)
        assert p.returncode == 3 and res["items"][0]["result"] == "limit" and "上限 2" in res["summary"]
        third = hubenv(env={"NDF_RELAY_MAX_NAMED": "2"})
        third.wait(lambda: "上限 2 を超えて起動する" in third.text, 40, "上限を超えた知らせの 1 行")
        hub.wait(lambda: (ls_rows(hub).get("t2") or {}).get("state") == "running", 40, "端末の 2 本目が一覧に出る")

    def test_killed_named_wrapper_shows_stopped(self, hubenv):
        hub = hubenv()
        hub.wait_start(1)
        assert relay_cli(hub, "spawn", "--name", "a", "--", "x").returncode == 0
        wait_state(hub, "a", "running")
        # 一覧の running は起動した直後（ロックの前）の登録簿の行でも立つ。ラッパーが pid を書くまで待つ
        hub.wait(lambda: any(n == "a" and (d / "relay.pid").exists() for d, n in hb_scan(hub)), 30, "a が pid を書く")
        d = next(d for d, n in hb_scan(hub) if n == "a" and (d / "relay.pid").exists())
        os.kill(int((d / "relay.pid").read_text()), 9)
        wait_state(hub, "a", "stopped")
        assert ls_rows(hub)["t1"]["state"] == "running"

    # 親の claude のセッションの環境を持つ専用サーバー（#2656）。グローバルな環境にこれらを持ったまま、レーンを起動する

    def test_parent_env_server_spawn_and_manual_choose_pinned_account(self, hubenv, accounts, tmp_path):  # noqa: F811
        """#2656 の受け入れ条件 1・3・4・8・13: `spawn` と手で打つ `new-session` が新しい区間 1 で始まり、指定の
        アカウントで起動する。環境の `NDF_RELAY_DIR` は動いているリレーハブのラッパーを指す。グローバルな環境の役割と
        費用の記録先はレーンへ残らない。"""
        accounts.add("main", util5=10)
        accounts.add("gmail", util5=20)
        pin.set_pinned("gmail")
        hub = _account_hub(hubenv, accounts, tmp_path)
        hub.wait_start(1)
        hub_dir = hub.starts()[0]["relay_dir"]
        _parent_server(hub, accounts, NDF_RELAY_DIR=hub_dir, NDF_RELAY_DEPTH="1")
        p = relay_cli(hub, "spawn", "--name", "lspawn", "--json", "--", "x")
        assert p.returncode == 0, p.stdout + p.stderr
        _manual(hub, "lman")
        for name in ("lspawn", "lman"):
            _assert_lane_chose(hub, name, "gmail", hub_dir)

    def test_parent_env_server_restart_depth2_and_limited_pinned(self, hubenv, accounts, tmp_path):  # noqa: F811
        """#2656 の受け入れ条件 2・5・6・13: 深さ 2 のグローバルな環境でも終了コード 127 にならず、指定の `main` が上限なら
        `gmail` で起動する。止めたレーンの `restart` も同じ結果になる。"""
        accounts.add("main", util5=100)
        accounts.add("gmail", util5=20)
        pin.set_pinned("main")
        hub = _account_hub(hubenv, accounts, tmp_path)
        hub.wait_start(1)
        hub_dir = hub.starts()[0]["relay_dir"]
        _parent_server(hub, accounts, NDF_RELAY_DIR=hub_dir, NDF_RELAY_DEPTH="2")
        assert relay_cli(hub, "spawn", "--name", "lspawn", "--", "x").returncode == 0
        _manual(hub, "lman")
        first = _assert_lane_chose(hub, "lspawn", "gmail", hub_dir)
        _assert_lane_chose(hub, "lman", "gmail", hub_dir)
        assert relay_cli(hub, "stop", "lspawn").returncode == 0
        wait_state(hub, "lspawn", "stopped")
        p = relay_cli(hub, "restart", "lspawn")
        assert p.returncode == 0, p.stdout + p.stderr
        hub.wait(lambda: len(_lane_dirs(hub, "lspawn")) == 2, 40, "restart の新しい作業ディレクトリ")
        again = _assert_lane_chose(hub, "lspawn", "gmail", hub_dir)
        assert again != first

    def test_parent_env_server_keeps_user_settings_without_accounts(self, hubenv, tmp_path):
        """#2656 の受け入れ条件 9・10: アカウントを登録していない構成で、利用者の設定と認証の宣言は子まで届き、
        元の設定ディレクトリが空なら `CLAUDE_CONFIG_DIR` を持たない（親のアカウントの設定ディレクトリにならない）。"""
        hub = hubenv(env={"NDF_RELAY_CLAUDE": _env_dump_claude(tmp_path), "NDF_ACCOUNTS_DIR": str(tmp_path / "no-accounts")})
        hub.wait_start(1)
        hub_dir = hub.starts()[0]["relay_dir"]
        user = {"NDF_ACCOUNT_SWITCH_AT": "80", "CLAUDE_CODE_OAUTH_TOKEN": "user-token", "CLAUDE_CODE_USE_BEDROCK": "1"}
        _parent_server(hub, None, NDF_RELAY_DIR=hub_dir, NDF_RELAY_DEPTH="1", **user)
        assert relay_cli(hub, "spawn", "--name", "lspawn", "--", "x").returncode == 0
        _manual(hub, "lman")
        for name in ("lspawn", "lman"):
            hub.wait(lambda n=name: _lane_start(hub, n) is not None, 40, f"{name} の区間 1")
            env = _child_env(hub, _lane_start(hub, name))
            assert {k: env.get(k) for k in user} == user
            assert "CLAUDE_CONFIG_DIR" not in env and "NDF_CLAUDE_ACCOUNT" not in env


# 親の claude のセッションの環境（#2656 の受け入れ条件の「親の環境を持つサーバー」）。値に目印 MARK を入れ、画面と記録に出ないかを見る
PARENT_SERVER = {
    "CLAUDECODE": "1",
    "CLAUDE_CODE_SESSION_ID": "parent-session-MARK",
    "CLAUDE_CODE_ENTRYPOINT": "cli",
    "NDF_CLAUDE_ACCOUNT": "main",
    "NDF_RELAY_ROLE": "helper",
    "NDF_RELAY_PLAN": "parent-plan-MARK",
    "NDF_USAGE_PLAN": "parent-plan-MARK",
    "NDF_USAGE_STEP": "3",
}
LANE_DROPPED = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "NDF_RELAY_ROLE", "NDF_RELAY_PLAN", "NDF_USAGE_PLAN", "NDF_USAGE_STEP")


def _env_dump_claude(tmp_path):
    """偽の claude の前に、起動ごとの環境を `FAKE_DIR/envs.jsonl` へ書く包み。pid は exec の後も同じ。"""
    p = tmp_path / "env-dump" / "claude"
    p.parent.mkdir(exist_ok=True)
    p.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        "if sys.argv[1:2] != ['plugin']:\n"
        "    with open(os.path.join(os.environ['FAKE_DIR'], 'envs.jsonl'), 'a') as f:\n"
        "        f.write(json.dumps({'pid': os.getpid(), 'env': dict(os.environ)}) + '\\n')\n"
        f"os.execv(sys.executable, [sys.executable, {str(FAKE)!r}, *sys.argv[1:]])\n"
    )
    p.chmod(0o755)
    return p


def _account_hub(hubenv, accounts, tmp_path):  # noqa: F811
    return hubenv(env={**accounts.env(), "NDF_ACCOUNT_CHECK_INTERVAL": "1", "NDF_RELAY_CLAUDE": _env_dump_claude(tmp_path)})


def _parent_server(hub, accounts, **extra):  # noqa: F811
    """親のセッションの環境を持つ専用サーバーを、リレーハブより先に起こす（サーバーを起こした側の環境がグローバルな環境になる）。"""
    env = {k: v for k, v in hub.env.items() if k != "TMUX"}
    env.update(PARENT_SERVER)
    if accounts is not None:
        env.update(CLAUDE_CONFIG_DIR=str(accounts.root / "main"), NDF_SHARED_CONFIG_DIR=str(accounts.shared))
    else:
        env.update(CLAUDE_CONFIG_DIR="/parent/accounts/main", NDF_SHARED_CONFIG_DIR="")
    env.update({k: str(v) for k, v in extra.items()})
    p = subprocess.run(
        ["tmux", "-L", tx.SOCKET, "new-session", "-d", "-s", "parent", "--", "sleep", "600"], env=env, capture_output=True, text=True
    )
    assert p.returncode == 0, p.stderr
    shown = subprocess.run(["tmux", "-L", tx.SOCKET, "show-environment", "-g", "NDF_RELAY_DIR"], env=env, capture_output=True, text=True)
    assert shown.stdout.strip() == f"NDF_RELAY_DIR={extra['NDF_RELAY_DIR']}"


def _manual(hub, name):
    """手で起動する形（受け入れ条件 3）。クライアントの環境はサーバーへ届かないため、何を持っていても同じである。"""
    argv = [sys.executable, str(RELAY), "run", "--", "x"]
    opts = ["-e", f"NDF_RELAY_NAME={name}", "-e", "NDF_RELAY_SPAWNED=1"]
    env = {k: v for k, v in hub.env.items() if k != "TMUX"}
    p = subprocess.run(
        ["tmux", "-L", tx.SOCKET, "new-session", "-d", "-s", name, *opts, "--", *argv], env=env, capture_output=True, text=True
    )
    assert p.returncode == 0, p.stderr


def _lane_dirs(hub, name):
    return sorted((d for d, n in hb_scan(hub) if n == name), key=lambda d: d.stat().st_mtime)


def _lane_start(hub, name):
    """名前 `name` の最新の作業ディレクトリで起動した区間 1。無ければ None。"""
    dirs = _lane_dirs(hub, name)
    if not dirs:
        return None
    return next((s for s in hub.starts() if s["relay_dir"] == str(dirs[-1])), None)


def _child_env(hub, start):
    p = hub.fake_dir / "envs.jsonl"
    rows = [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
    return next(r["env"] for r in rows if r["pid"] == start["pid"])


def _assert_lane_chose(hub, name, account, parent_dir):
    """レーン `name` が新しい作業ディレクトリの区間 1 を `account` で始め、親のセッションの環境を子へ残さない。"""
    hub.wait(lambda: _lane_start(hub, name) is not None, 40, f"{name} の区間 1")
    s = _lane_start(hub, name)
    assert s["relay_dir"] != parent_dir and s["depth"] == "1"
    assert s["account"] == account
    hub.wait(lambda: (ls_rows(hub).get(name) or {}).get("sections") == 1, 20, f"{name} の区間の数が 1")
    env = _child_env(hub, s)
    assert not set(LANE_DROPPED) & set(env), set(LANE_DROPPED) & set(env)
    assert not (pathlib.Path(s["relay_dir"]) / hb.ROLE_FILE).exists()
    logs = "".join(p.read_text() for p in pathlib.Path(s["relay_dir"]).glob("*.jsonl"))
    assert "MARK" not in logs and "MARK" not in hub.text  # 外した値を記録と画面へ出さない（I6）
    return s["relay_dir"]


def test_all_limited_choice_is_the_same_with_parent_env(accounts, monkeypatch):  # noqa: F811
    """#2656 の受け入れ条件 7: すべて上限のとき、親のセッションの環境を外した後の選び方の結果は、持たないときと同じ。"""
    monkeypatch.setenv("NDF_ACCOUNT_CHECK_INTERVAL", "0")
    accounts.add("main", util5=100)
    accounts.add("gmail", util5=100)
    clean = ca.choose()
    for k, v in {**PARENT_SERVER, "CLAUDE_CONFIG_DIR": str(accounts.root / "main"), "NDF_SHARED_CONFIG_DIR": str(accounts.shared)}.items():
        monkeypatch.setenv(k, v)
    relay_session_env.drop_parent_env({})
    assert os.environ["CLAUDE_CONFIG_DIR"] == str(accounts.shared)
    got = ca.choose()
    assert (got.name, got.earliest and got.earliest[0]) == (clean.name, clean.earliest and clean.earliest[0])


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux が無い")
def test_session_value_reads_only_the_launch_env(tmp_path, monkeypatch):
    """#2656 の決定 4: `session_value` は起動の `-e` で付いた値だけを読み、グローバルな環境の値は読まない。"""
    sock = tempfile.mkdtemp(prefix="ndf-tests-tmux-", dir="/tmp")
    monkeypatch.setenv("TMUX_TMPDIR", sock)
    monkeypatch.setenv("NDF_RELAY_TMUX", "tmux")
    monkeypatch.setenv("NDF_TEST_GLOBAL", "glob")  # サーバーを起こす側の環境（グローバルな環境へ入る）
    try:
        assert tx.new_session("a", str(tmp_path), ["sleep", "30"], {"NDF_RELAY_ROLE": "helper", "NDF_RELAY_PLAN": ""}) is None
        assert tx.new_session("b", str(tmp_path), ["sleep", "30"], {}) is None
        assert tx.session_value("a", "NDF_RELAY_ROLE") == "helper"
        assert tx.session_value("a", "NDF_RELAY_PLAN") == ""
        assert tx.session_value("b", "NDF_RELAY_ROLE") is None
        assert tx.session_value("a", "NDF_TEST_GLOBAL") is None
        assert tx.session_value("zz", "NDF_RELAY_ROLE") is None
    finally:
        subprocess.run(["tmux", "-L", tx.SOCKET, "kill-server"], env={**os.environ, "TMUX_TMPDIR": sock}, capture_output=True)
        shutil.rmtree(sock, ignore_errors=True)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux が無い")
def test_session_gone_with_a_real_tmux(tmp_path, monkeypatch):
    """実物の tmux: 消えた置き場の python を渡しても `new-session` は成功し、セッションは残らない（#2583 の再現）。
    サーバごと居ないときも消えたと読み、動いているセッションは消えたと読まない。"""
    sock = tempfile.mkdtemp(prefix="ndf-tests-tmux-", dir="/tmp")
    monkeypatch.setenv("TMUX_TMPDIR", sock)
    monkeypatch.setenv("NDF_RELAY_TMUX", "tmux")
    try:
        assert tx.new_session("gone", str(tmp_path), ["/無い/.venv/bin/python", "relay.py"], {}) is None
        end = time.monotonic() + 5
        while time.monotonic() < end and not tx.session_gone("gone"):
            time.sleep(0.05)
        assert tx.session_gone("gone")
        assert tx.new_session("stays", str(tmp_path), ["sleep", "30"], {}) is None
        assert not tx.session_gone("stays")
    finally:
        subprocess.run(["tmux", "-L", tx.SOCKET, "kill-server"], env={**os.environ, "TMUX_TMPDIR": sock}, capture_output=True)
        shutil.rmtree(sock, ignore_errors=True)


def test_session_gone_is_false_when_tmux_cannot_be_asked(monkeypatch):
    """tmux を呼べないときは消えたと決めない（動いているセッションを失敗と誤らない）。"""
    monkeypatch.setattr(hb.tmux, "_tmux", lambda *a: None)
    assert not tx.session_gone("a")


def test_tmux_class_has_no_skip_of_its_own():
    """tmux の有無は根の前提のコマンドの一覧だけが決め、クラスの各件は自分で読み飛ばさない（#2346）。"""
    holders = [sys.modules[__name__], TestWithTmux, *(v for k, v in vars(TestWithTmux).items() if k.startswith("test_"))]
    names = [m.name for h in holders for m in getattr(h, "pytestmark", [])]
    assert not {"skip", "skipif"} & set(names), names


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


# ---------------------------------------------------------------- Hub.process の分岐（現状固定）


@pytest.fixture()
def launched(state, monkeypatch):
    """tmux があるものとし、起動の呼び出しを記録する（起動は成功する）。"""
    seen = []
    monkeypatch.setattr(hb.tmux, "binary", lambda: "/bin/true")
    monkeypatch.setattr(hb.tmux, "sessions", lambda: {})
    monkeypatch.setattr(
        hb.tmux, "new_session", lambda name, cwd, argv, env: seen.append({"name": name, "cwd": cwd, "argv": argv, "env": env})
    )
    hb.ensure_dirs()
    return seen


def _write_registry(entries):
    pathlib.Path(hb.hub_path(hb.REGISTRY)).write_text(json.dumps({"version": 1, "entries": entries}))


def test_process_unknown_kind_is_bad_request(launched):
    res = hb.Hub(None).process({"kind": "nope", "name": "a"})
    assert res["tool"] == "relay-hub" and res["status"] == "stopped"
    assert res["items"] == [{"name": "a", "result": "bad-request"}] and res["metrics"] == {"exit_code": 2}
    assert "知らない依頼の種類: nope" in res["summary"]
    assert launched == []


def test_process_non_dict_request_shape_is_bad_request(launched):
    res = hb.Hub(None).process({})
    assert res["items"] == [{"name": None, "result": "bad-request"}] and res["metrics"]["exit_code"] == 2


@pytest.mark.parametrize("name", ["missing", None, 5])
def test_process_restart_of_unknown_name_is_not_found(launched, name):
    res = hb.Hub(None).process({"kind": "restart", "name": name})
    assert res["tool"] == "relay-restart" and res["status"] == "stopped"
    assert res["items"][0]["result"] == hb.NOT_FOUND and res["metrics"]["exit_code"] == 3
    assert f"その名前は無い: {name}" in res["summary"]
    assert launched == []


def test_process_spawn_by_terminal_keeps_args_and_cwd(launched, tmp_path):
    res = hb.Hub(None).process(
        {"kind": "spawn", "name": "a", "cwd": str(tmp_path), "args": ["--model", "x", 3, "--", "y"], "by": "terminal"}
    )
    assert res["tool"] == "relay-spawn" and res["status"] == "ok" and res["metrics"]["exit_code"] == 0
    assert res["items"] == [{"name": "a", "result": "started"}]
    (call,) = launched
    assert call["cwd"] == str(tmp_path)
    assert call["argv"][-5:] == ["run", "--model", "x", "--", "y"]
    assert call["env"] == {"NDF_RELAY_NAME": "a", "NDF_RELAY_SPAWNED": "1"}
    row = hb.read_registry()["a"]
    assert row["args"] == ["--model", "x", "--", "y"] and row["by"] == "terminal" and row["cwd"] == str(tmp_path)
    assert "role" not in row and "plan" not in row


def test_process_spawn_joins_args_into_one_prompt(launched):
    hb.Hub(None).process({"kind": "spawn", "name": "a", "cwd": "/", "args": ["--", "x", "y"], "by": "spawn"})
    hb.Hub(None).process({"kind": "spawn", "name": "b", "cwd": "/", "args": ["x", "y"], "by": "spawn"})
    assert [c["argv"][-2:] for c in launched] == [["--", "x y"], ["--", "x y"]]


def test_process_spawn_missing_cwd_falls_back_to_home(launched, tmp_path):
    hb.Hub(None).process({"kind": "spawn", "name": "a", "cwd": str(tmp_path / "nope"), "args": [], "by": "spawn"})
    hb.Hub(None).process({"kind": "spawn", "name": "b", "cwd": 7, "by": "spawn"})
    assert [c["cwd"] for c in launched] == [os.path.expanduser("~")] * 2
    assert launched[0]["argv"][-1] == "run"


def test_process_spawn_without_name_takes_auto_name(launched, dirs):
    dirs("front", name="t2")
    res = hb.Hub(None).process({"kind": "spawn", "cwd": "/", "args": [], "by": "terminal"})
    assert res["items"] == [{"name": "t3", "result": "started"}]
    assert launched[0]["name"] == "t3"


def test_process_spawn_helper_role_and_plan(launched):
    res = hb.Hub(None).process({"kind": "spawn", "name": "h", "cwd": "/", "args": [], "by": "spawn", "role": "helper", "plan": "p1"})
    assert res["status"] == "ok"
    assert launched[0]["env"] == {"NDF_RELAY_NAME": "h", "NDF_RELAY_SPAWNED": "1", "NDF_RELAY_ROLE": "helper", "NDF_RELAY_PLAN": "p1"}
    row = hb.read_registry()["h"]
    assert row["role"] == "helper" and row["plan"] == "p1"


def test_process_spawn_other_role_is_ignored(launched):
    hb.Hub(None).process({"kind": "spawn", "name": "o", "cwd": "/", "args": [], "by": "spawn", "role": "organizer", "plan": "p1"})
    assert launched[0]["env"] == {"NDF_RELAY_NAME": "o", "NDF_RELAY_SPAWNED": "1"}
    assert "role" not in hb.read_registry()["o"]


def test_process_spawn_refusal_from_judge_does_not_launch(launched):
    res = hb.Hub(None).process({"kind": "spawn", "name": "Bad.Name", "cwd": "/", "args": [], "by": "spawn"})
    assert res["status"] == "stopped" and res["items"][0]["result"] == hb.BAD_NAME and res["metrics"]["exit_code"] == 3
    assert launched == [] and not os.path.exists(hb.hub_path(hb.REGISTRY))


def test_process_tmux_failure_is_refused_and_registry_untouched(launched, monkeypatch):
    monkeypatch.setattr(hb.tmux, "new_session", lambda name, cwd, argv, env: "duplicate session")
    res = hb.Hub(None).process({"kind": "spawn", "name": "a", "cwd": "/", "args": [], "by": "spawn"})
    assert res["status"] == "stopped" and res["items"][0]["result"] == hb.TMUX_FAILED
    assert res["metrics"]["exit_code"] == 1 and res["summary"] == "duplicate session"
    assert not os.path.exists(hb.hub_path(hb.REGISTRY))


def test_process_session_gone_after_launch_is_refused_and_not_registered(launched, monkeypatch):
    """作ったセッションがすぐ消えたら「起動した」と返さず、登録もしない（#2563・#2583 の受け入れ条件 2）。"""
    monkeypatch.setattr(hb.tmux, "session_gone", lambda name: True)
    res = hb.Hub(None).process({"kind": "spawn", "name": "a", "cwd": "/", "args": [], "by": "spawn"})
    assert res["status"] == "stopped" and res["items"][0]["result"] == hb.TMUX_FAILED
    assert res["metrics"]["exit_code"] == 1 and "a" in res["summary"]
    assert not os.path.exists(hb.hub_path(hb.REGISTRY))


def test_process_restart_gone_keeps_the_registry_row(launched, tmp_path, monkeypatch):
    """起こし直しで消えたら、前の登録の行はそのまま残す（次の起こし直しの入力になる）。"""
    row = {"session": "a", "cwd": str(tmp_path), "args": [], "created_at": "x", "by": "spawn"}
    _write_registry({"a": row})
    monkeypatch.setattr(hb.tmux, "session_gone", lambda name: True)
    res = hb.Hub(None).process({"kind": "restart", "name": "a", "by": "restart"})
    assert res["metrics"]["exit_code"] == 1 and hb.read_registry()["a"] == row


def test_launch_waits_the_check_seconds_before_looking(launched, monkeypatch):
    """セッションの確かめの前に `NDF_RELAY_LAUNCH_CHECK` 秒待つ。tmux に聞けなければ起動した扱いのまま。"""
    monkeypatch.setenv("NDF_RELAY_LAUNCH_CHECK", "1.5")
    order = []
    monkeypatch.setattr(hb.time, "sleep", lambda s: order.append(("sleep", s)))
    monkeypatch.setattr(hb.tmux, "session_gone", lambda name: order.append(("look", name)) or False)
    res = hb.Hub(None).process({"kind": "spawn", "name": "a", "cwd": "/", "args": [], "by": "spawn"})
    assert order == [("sleep", 1.5), ("look", "a")] and res["status"] == "ok"


def test_launch_check_seconds_default_and_broken_value(monkeypatch):
    monkeypatch.delenv("NDF_RELAY_LAUNCH_CHECK", raising=False)
    assert hb.launch_check_seconds() == 2
    monkeypatch.setenv("NDF_RELAY_LAUNCH_CHECK", "x")
    assert hb.launch_check_seconds() == 2


def test_process_restart_uses_registry_row(launched, tmp_path):
    row = {"session": "a", "cwd": str(tmp_path), "args": ["--", "最初"], "created_at": "x", "by": "spawn"}
    _write_registry({"a": row})
    res = hb.Hub(None).process({"kind": "restart", "name": "a", "by": "restart"})
    assert res["tool"] == "relay-restart" and res["status"] == "ok" and res["items"] == [{"name": "a", "result": "started"}]
    (call,) = launched
    assert call["cwd"] == str(tmp_path) and call["argv"][-2:] == ["--", "最初"]
    assert call["env"] == {"NDF_RELAY_NAME": "a", "NDF_RELAY_SPAWNED": "1"}
    assert hb.read_registry()["a"]["by"] == "restart"
    log = [json.loads(x) for x in pathlib.Path(hb.hub_path(hb.HUB_LOG)).read_text().splitlines()]
    assert log[-1]["event"] == "restarted" and log[-1]["name"] == "a"


def test_process_restart_keeps_helper_role_and_registry_plan(launched, tmp_path):
    row = {"session": "h", "cwd": str(tmp_path), "args": [], "created_at": "x", "by": "spawn", "role": "helper", "plan": "p9"}
    _write_registry({"h": row})
    hb.Hub(None).process({"kind": "restart", "name": "h", "by": "restart"})
    assert launched[0]["env"]["NDF_RELAY_ROLE"] == "helper" and launched[0]["env"]["NDF_RELAY_PLAN"] == "p9"
    # 依頼の plan は登録簿の plan より先に使う
    hb.Hub(None).process({"kind": "restart", "name": "h", "by": "restart", "plan": "p10"})
    assert launched[1]["env"]["NDF_RELAY_PLAN"] == "p10"
    assert hb.read_registry()["h"]["plan"] == "p10"


def test_process_restart_of_running_name_is_taken(launched, dirs, tmp_path):
    dirs("d1", name="a")
    _write_registry({"a": {"session": "a", "cwd": str(tmp_path), "args": [], "created_at": "x", "by": "spawn"}})
    res = hb.Hub(None).process({"kind": "restart", "name": "a"})
    assert res["items"][0]["result"] == hb.NAME_TAKEN and launched == []


def test_write_name_keeps_the_spawned_role(tmp_path, monkeypatch):
    """`spawn --role helper --plan <計画名>` で起動したラッパーは、役割のファイルを作業ディレクトリへ書く（#2436）。"""
    import parallel_files

    monkeypatch.setenv("NDF_RELAY_NAME", "ph-p1-1")
    monkeypatch.setenv("NDF_RELAY_ROLE", "helper")
    monkeypatch.setenv("NDF_RELAY_PLAN", "p1")
    hc.write_name(str(tmp_path), None)
    assert hb.read_role(str(tmp_path))["plan"] == "p1"
    assert parallel_files.helper_plan_of(str(tmp_path)) == "p1"
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.delenv("NDF_RELAY_ROLE")
    hc.write_name(str(other), None)
    assert hb.read_role(str(other)) is None


# ---------------------------------------------------------------- 人の起動（#2862）


SESSION = {"attached": False, "activity": None, "pid": None}


def _row(by, **extra):
    return {"session": extra.pop("session", None), "cwd": "/", "args": [], "created_at": "x", "by": by, **extra}


def _full(dirs):
    """上限 3 に数えるラッパーが 3 本（リレーハブ・名前つき・名前の無いもの）。"""
    dirs("front", name="t1")
    dirs("named", name="lcore")
    dirs("plain")


def _hub_log():
    return [json.loads(x) for x in pathlib.Path(hb.hub_path(hb.HUB_LOG)).read_text().splitlines()]


def test_terminal_request_is_started_over_the_limit_with_notice(launched, dirs):
    """AC1・AC8: 上限に達していても人の起動は起動し、知らせと記録の `over_limit` を残す。"""
    _full(dirs)
    res = hb.Hub(None).process({"kind": "spawn", "cwd": "/", "args": [], "by": "terminal"})
    assert res["status"] == "ok" and res["metrics"]["exit_code"] == 0
    item = res["items"][0]
    assert item["result"] == "started" and item["name"] == "t2"
    assert "上限 3" in item["notice"] and "t1, lcore, -" in item["notice"]
    started = _hub_log()[-1]
    assert started["event"] == "started" and started["reason"] == "terminal"
    assert started["over_limit"] == {"cap": 3, "running": ["t1", "lcore", "-"]}
    assert hb.read_registry()["t2"]["by"] == "terminal"


def test_terminal_request_under_the_limit_has_no_notice(launched, dirs):
    dirs("front", name="t1")
    res = hb.Hub(None).process({"kind": "spawn", "cwd": "/", "args": [], "by": "terminal"})
    assert res["items"] == [{"name": "t2", "result": "started"}]
    assert "over_limit" not in _hub_log()[-1]


@pytest.mark.parametrize("by", ["spawn", "restart", "block:/x"])
def test_ai_requests_are_refused_at_the_limit(launched, dirs, by):
    """AC2: AI の起動は今と同じく `limit`・終了コード 3 で断る。記録に `over_limit` を書かない。"""
    _full(dirs)
    _write_registry({"r": _row("spawn", session="r")})
    kind = "restart" if by == "restart" else "spawn"
    res = hb.Hub(None).process({"kind": kind, "name": "r", "cwd": "/", "args": [], "by": by})
    assert res["items"][0]["result"] == hb.LIMIT and res["metrics"]["exit_code"] == 3 and launched == []
    assert all("over_limit" not in row for row in _hub_log())


def test_human_wrappers_are_not_counted(dirs, monkeypatch):
    """AC3・I3: 登録簿の行の `by` が `terminal` でセッションの在るラッパーは数えない。セッションが無ければ外さない。"""
    dirs("front", name="t1")
    dirs("lane", name="lcore")
    dirs("h2", name="t2")
    dirs("h3", name="t3")
    hb.ensure_dirs()
    _write_registry({"lcore": _row("spawn", session="lcore"), "t2": _row("terminal", session="t2"), "t3": _row("terminal", session="t3")})
    monkeypatch.setattr(hb.tmux, "binary", lambda: "/bin/true")
    now = hb.live(hb.scan_wrappers(), hb.read_registry(), {"lcore": SESSION, "t2": SESSION, "t3": SESSION})
    assert now.count == 2 and now.humans == 2 and sorted(now.names) == ["lcore", "t1", "t2", "t3"]
    assert hb.Hub.judge("lproc", now, by="spawn") is None
    # 行が残っていてもセッションが無ければ人の起動として外さない
    gone = hb.live(hb.scan_wrappers(), hb.read_registry(), {"lcore": SESSION})
    assert gone.count == 4 and gone.humans == 0
    assert hb.Hub.judge("lproc", gone, by="spawn").reason == hb.LIMIT


def _w(name, role=None):
    return hb.Wrapper(f"/x/{name}", name, True, False, None, None, role)


def test_human_organizer_is_taken_once(state):
    """I1: 人の起動の `organizer` は人の起動として 1 回だけ外し、`organizer` の 1 本は人の起動でないものから外す。"""
    reg = {"t2": _row("terminal", session="t2")}
    sessions = {"t2": SESSION}
    now = hb.live([_w("t1"), _w("t2", "organizer"), _w("a"), _w("b")], reg, sessions)
    assert now.count == 3 and now.humans == 1
    assert hb.Hub.judge("c", now, by="spawn").reason == hb.LIMIT
    # 人の起動でない organizer が 2 本あっても、外すのは 1 本だけ
    two = hb.live([_w("t1"), _w("t2", "organizer"), _w("o1", "organizer"), _w("o2", "organizer"), _w("a")], reg, sessions)
    assert two.count == 3 and two.humans == 1


@pytest.mark.parametrize(
    ("name", "taken", "tmux_ok", "reason"),
    [("Bad.Name", False, True, hb.BAD_NAME), ("t1", True, True, hb.NAME_TAKEN), ("t2", False, False, hb.NO_TMUX)],
)
def test_terminal_request_keeps_other_refusals(state, monkeypatch, name, taken, tmux_ok, reason):
    """AC5: 人の起動でも名前の形・重複・tmux の有無では断る。"""
    monkeypatch.setattr(hb.tmux, "binary", lambda: "/bin/true" if tmux_ok else None)
    now = hb.Live(5, ["t1"], {"t1"} if taken else set())
    assert hb.Hub.judge(name, now, by="terminal").reason == reason


def test_restart_of_a_human_wrapper_counts_it_again(launched, tmp_path):
    """I4: 人の起動で始めた名前を restart で起動し直すと、行の `by` は `restart` になり数える。"""
    _write_registry({"t2": _row("terminal", session="t2", cwd=str(tmp_path))})
    hb.Hub(None).process({"kind": "restart", "name": "t2", "by": "restart"})
    reg = hb.read_registry()
    assert reg["t2"]["by"] == "restart"
    now = hb.live([], reg, {"t2": SESSION})
    assert now.count == 1 and now.humans == 0


def test_ai_paths_never_put_terminal(state, monkeypatch, tmp_path):
    """AC6・I5: `relay.py spawn`・`restart`・起動のブロックが置く依頼の `by` は `terminal` でない。"""
    seen = []
    monkeypatch.setattr(hc, "send_request", lambda kind, name, cwd, args, by, extra=None: seen.append(by) or None)
    monkeypatch.setattr(hb, "hub_running", lambda: True)
    hc.cmd_spawn(["--name", "a", "--", "x"])
    hb.ensure_dirs()
    _write_registry({"a": _row("spawn", session="a")})
    hc.cmd_restart(["a"])
    hc.place_blocks(fence("/goal x", "ndf-spawn lb"), "/", str(tmp_path))
    box = pathlib.Path(hb.hub_path(hb.REQUESTS))
    seen += [json.loads(f.read_text())["by"] for f in box.iterdir()]
    assert seen[:2] == ["spawn", "restart"] and seen[2] == f"block:{tmp_path}"
    assert hb.TERMINAL not in seen


def test_ask_hub_says_the_notice_before_attach(monkeypatch):
    """AC1: 通った結果に知らせがあれば、結び付く前に 1 行出す。"""
    order = []
    res = hb.hub_result("spawn", "ok", "x", "t2", "started", 0)
    res["items"][0]["notice"] = "上限 3 を超えて起動する"
    monkeypatch.delenv("NDF_RELAY_SPAWNED", raising=False)
    monkeypatch.setattr(hc.tmux, "binary", lambda: "/bin/true")
    monkeypatch.setattr(hc, "terminal_request", lambda args: ("attach", res))
    monkeypatch.setattr(hc.tmux, "exec_attach", lambda name: order.append(("attach", name)))
    hc.ask_hub([], lambda msg: order.append(("say", msg)))
    assert order == [("say", "上限 3 を超えて起動する"), ("attach", "t2")]


class _Passthrough(Exception):
    pass


@pytest.mark.parametrize(
    ("spawned", "relay_dir", "tty0", "tty1", "asked"),
    [
        (False, False, True, True, True),
        (True, False, True, True, False),
        (False, True, True, True, False),
        (False, False, False, True, False),
        (False, False, True, False, False),
    ],
)
def test_terminal_request_only_from_a_human_terminal(state, monkeypatch, spawned, relay_dir, tty0, tty1, asked):
    """AC7・I5: `by: terminal` の依頼は、起動の印も `NDF_RELAY_DIR` も無く、標準入力と標準出力が端末のときだけ置く。"""
    from relay_lib import run as rr

    seen = []
    for k in ("NDF_RELAY_SPAWNED", "NDF_RELAY_DIR", "NDF_RELAY", "NDF_RELAY_DEPTH", rr.ho.HANDOVER_ENV):
        monkeypatch.delenv(k, raising=False)
    if spawned:
        monkeypatch.setenv("NDF_RELAY_SPAWNED", "1")
    if relay_dir:
        monkeypatch.setenv("NDF_RELAY_DIR", "/x")
    monkeypatch.setattr(rr.session_env, "drop_parent_env", lambda env: None)
    monkeypatch.setattr(rr.cl, "resolve_claude", lambda: "/bin/claude")
    monkeypatch.setattr(rr.cl, "passthrough", lambda claude, args: (_ for _ in ()).throw(_Passthrough()))
    monkeypatch.setattr(rr.os, "isatty", lambda fd: {0: tty0, 1: tty1}.get(fd, False))
    monkeypatch.setattr(rr, "pty_available", lambda: True)
    monkeypatch.setattr(hc, "claim_hub", lambda: None)
    monkeypatch.setattr(hc.tmux, "binary", lambda: "/bin/true")
    monkeypatch.setattr(hc, "send_request", lambda kind, name, cwd, args, by, extra=None: seen.append(by) or None)
    monkeypatch.setattr(rr.cl, "read_plugin", lambda claude: (_ for _ in ()).throw(_Passthrough()))
    with pytest.raises(_Passthrough):
        rr.cmd_run([])
    assert seen == ([hb.TERMINAL] if asked else [])
