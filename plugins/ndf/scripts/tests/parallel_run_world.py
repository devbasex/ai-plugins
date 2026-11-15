"""並列の進行の耐久ワークフローのテストに共通する試験基盤: 偽の `relay.py`（`FAKE`）と一時リポジトリの fixture `env`、
耐久ワークフローの起動・知らせの読み取り・待ちの操作。

本物の DBOS（SQLite。置き場は根の conftest.py が `NDF_DBOS_DIR` へ向ける）で流し、リレーハブの CLI は
`NDF_PARALLEL_RELAY` の差し替え（呼び出しを記録し、`ls` の状態をファイルから返す）で、git とリリースプランの作りは
monkeypatch で差し替える。`test_parallel_run.py` ほか並列の計画のテストが使う（テストとしては集めない）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
# 読み込む順は元の test_parallel_run.py と同じに保つ（耐久ステップの登録を DBOS の起動より前に済ませる）
import dbos  # noqa: E402,F401
import durable  # noqa: E402
import parallel_answer  # noqa: E402,F401
import parallel_gate2  # noqa: E402,F401
import parallel_lane as pl  # noqa: E402
import parallel_ledger  # noqa: E402,F401
import parallel_helper  # noqa: E402,F401
import parallel_plan as pp  # noqa: E402
import parallel_release as prl  # noqa: E402
import parallel_reply  # noqa: E402,F401
import parallel_run as prun  # noqa: E402

PLAN = {
    "version": 1,
    "name": "p1",
    "rules": [],
    "lanes": [
        {"name": "la", "input": {"kind": "sprint", "issues": [1]}, "plans": 1, "touches": ["a/"]},
        {"name": "lb", "input": {"kind": "sprint", "issues": [2]}, "plans": 1, "touches": ["b/"], "after": ["la"]},
    ],
}

TEMPLATE_DECL = {"release": {"form": "package-plugin", "plugin": "ndf"}}
# この印の後の記録の道（終わりの記録で数える・提示 1 で確定する。#3058・#3070）は test_parallel_end.py で確かめる。
# ここの流れのテストは印の無い記録の道で確かめる（別のプロセスで記録するテストも同じ値にする）
LEGACY_MARKS = ("parallel-lane-end", "parallel-approved-sha")


def legacy_patched(orig):
    return lambda n: False if n in LEGACY_MARKS else orig(n)


FAKE = textwrap.dedent(
    """
    import json, os, sys
    log, states = os.environ["FAKE_LOG"], os.environ["FAKE_STATES"]
    with open(log, "a") as f:
        f.write(json.dumps(sys.argv[1:]) + "\\n")
    sub = sys.argv[1]
    if sub == "ls" and os.path.exists(os.environ.get("FAKE_LS_FAIL", "")):
        print("ls: hub に届かない", file=sys.stderr)
        sys.exit(3)
    if sub == "ls":
        st = json.load(open(states)) if os.path.exists(states) else {}
        rows = [{"name": n, **(s if isinstance(s, dict) else {"state": s})} for n, s in st.items()]
        print(json.dumps({"items": rows, "metrics": {"hub": True, "tmux": True}}))
    elif sub == "spawn" and sys.argv[3] in os.environ.get("FAKE_REFUSE", "").split():
        print(json.dumps({"items": [{"result": "limit"}], "metrics": {}}))
        sys.exit(1)
    else:
        # 一時レーンの起動と再起動は一覧へ running で現れる。FAKE_DOWN の回数だけは現れない（#2936 の受け入れ条件 3）
        name = sys.argv[3] if sub == "spawn" else sys.argv[2] if sub == "restart" else None
        if name and (sub == "restart" or "--role" in sys.argv):
            down, used = int(os.environ.get("FAKE_DOWN") or 0), os.environ["FAKE_LOG"] + ".down"
            n = int(open(used).read()) if os.path.exists(used) else 0
            if n < down:
                open(used, "w").write(str(n + 1))
            else:
                import time
                st = json.load(open(states)) if os.path.exists(states) else {}
                now = time.time()
                at = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now % 1 * 1000):03d}Z"
                st[name] = {"state": "running", "at": at, "role": "helper" if sub == "spawn" else None}
                json.dump(st, open(states + ".tmp", "w"))
                os.replace(states + ".tmp", states)
        print(json.dumps({"items": [{"result": "started"}], "metrics": {}}))
    """
)


@pytest.fixture
def env(tmp_path, monkeypatch):
    main = tmp_path / "repo"
    (main / ".ndf").mkdir(parents=True)
    (main / ".ndf" / "supervise.json").write_text(json.dumps(TEMPLATE_DECL))
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    plan = pp.check_data(PLAN)[0]
    pp.write_all(main, plan, PLAN)
    fake = tmp_path / "fake_relay.py"
    fake.write_text(FAKE)
    log, states = tmp_path / "relay.log", tmp_path / "states.json"
    monkeypatch.setenv("NDF_PARALLEL_RELAY", f"{sys.executable} {fake}")
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_STATES", str(states))
    monkeypatch.setenv("FAKE_LS_FAIL", str(tmp_path / "ls.fail"))
    monkeypatch.setenv("NDF_PARALLEL_POLL", "0.2")
    monkeypatch.setenv("NDF_PARALLEL_LAUNCH_SECONDS", "1")
    monkeypatch.setattr(durable, "patched", legacy_patched(durable.patched))
    heads = {"v": "sha1"}
    monkeypatch.setattr(prun, "git_head", lambda _m: heads["v"])
    monkeypatch.setattr(prun, "dev_version", lambda _m: "1.2.3")

    def builder(_main, _name, d, _v, **_kw):
        return {"steps": []}, {"steps": []}

    monkeypatch.setattr(prl, "build_plans", lambda m, n, s, k, v, builder_=None, **kw: _build(m, n, s, k, v, builder, **kw))
    yield {"main": main, "log": log, "states": states, "heads": heads}
    _stop_running(main)
    durable.close()


_SEND_TO = durable.send_to  # テストの monkeypatch に左右されない後片付けの送り手


def _stop_running(main):
    """テストが残した耐久ワークフローを止め、スレッドが抜けるのを待つ。

    close() は DBOS を止めてもワークフローのスレッドを止めない。残すと次のテストの同じ ID（parallel-p1）の起動が
    重複として打ち切られ、前のテストのスレッドが次のテストの記録を読み書きする。
    env の後片付けは monkeypatch を戻す前に走るため、テストが差し替えた `durable.send_to` ではなく元の関数で送る。"""
    wid = pl.workflow_id("p1")
    try:
        if durable.status(wid) not in pl.RUNNING:
            return
        _SEND_TO(pl.KIND, pl.record_identity(main, "p1"), wid, {"kind": "stop", "force": True}, "inbox", f"{wid}:inbox:stop:force")
        end = time.monotonic() + 15
        while time.monotonic() < end and durable.status(wid) in pl.RUNNING:
            time.sleep(0.1)
    except Exception:  # 後片付けの失敗でテストの結果を変えない
        pass


def _build(m, n, s, k, v, builder, **kw):
    return prl.__dict__["_orig_build"](m, n, s, k, v, builder, **kw)


prl._orig_build = prl.build_plans


def calls(log: Path, sub: str) -> list[list[str]]:
    return [c for c in (json.loads(ln) for ln in log.read_text().splitlines()) if c[0] == sub] if log.exists() else []


def event(main, name, key):
    return durable.event(pl.workflow_id(name), key)


def until(pred, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = pred()
        if got:
            return got
        time.sleep(0.1)
    raise AssertionError("条件が成り立たないまま上限に達した")


def start(main):
    prun.open_parallel_record(main, "p1")
    durable.start(durable.WorkflowRef(pl.workflow_id("p1"), 1, "start"), prun.parallel_workflow, str(main), "p1")


def spawned(log):
    return [c[2] for c in calls(log, "spawn")]


def lanes_spawned(log):
    """計画のレーンの起動（一時レーンの起動を除く）。"""
    return [c[2] for c in calls(log, "spawn") if "--role" not in c]


def helper_spawns(log):
    """耐久ワークフローが打った一時レーンの起動（#2936）。"""
    return [c[2] for c in calls(log, "spawn") if "--role" in c and c[c.index("--role") + 1] == "helper"]


def helper_up(states, name):
    """偽の relay.py が起動したレーンを一覧へ書くまで待つ（書く前に上書きすると、後から running に戻る）。"""
    until(lambda: states.exists() and name in json.loads(states.read_text() or "{}"))


def set_state(states, name, state, at=None):
    """一覧の 1 行を書き換える（他の行は残す）。`at` を省けば今の時刻。"""
    st = json.loads(states.read_text()) if states.exists() else {}
    now = time.time()  # 一覧の at はミリ秒まで持つ（秒で切ると、打った直後の止まりを打つ前の行と読む）
    st[name] = {"state": state, "at": at or time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now % 1 * 1000):03d}Z"}
    states.write_text(json.dumps(st))


def resume(main, msg, key):
    durable.send_to(pl.KIND, pl.record_identity(main, "p1"), pl.workflow_id("p1"), msg, "resume", key)


def stop(main):
    wid = pl.workflow_id("p1")
    durable.send_to(pl.KIND, pl.record_identity(main, "p1"), wid, {"kind": "stop"}, "inbox", f"{wid}:inbox:stop")
    return durable.output_of(wid)


ASK = {"questions": [{"question": "gh の認証を直してよいか", "options": [{"label": "済んだ"}, {"label": "まだ"}]}],
       "metadata": {"source": "ndf-ask:human-action"}}  # fmt: skip


def questions(main):
    return event(main, "p1", "questions") or {"items": []}


OLD = "2000-01-01T00:00:00Z"


def read(main, rid):
    return until(lambda: pl.read_answer(main, "p1", rid)[0])


def build_question(main):
    return until(lambda: next((q for q in (event(main, "p1", "questions") or {}).get("items") or [] if q.get("kind") == "build"), None))
