"""進行表の DB（lib/progress_db.py。#3092 の受け入れ条件 9・10・I1・I2・I14・決定 15〜17）。

同じテストを SQLite と PostgreSQL の 2 つの置き場で流す。PostgreSQL は `NDF_TEST_PG_URL`（CI のサービス）が
あるときだけ流す。
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import progress_db as pdb  # noqa: E402
import progress_model as pm  # noqa: E402

D = pm.EventDraft
PG = os.environ.get("NDF_TEST_PG_URL")


@pytest.fixture(params=["sqlite"] + (["postgresql"] if PG else []))
def store(request, tmp_path):
    if request.param == "sqlite":
        yield pdb.connect(f"sqlite:///{tmp_path}/p.sqlite")
        return
    from sqlalchemy import create_engine, text

    schema = f"t{uuid.uuid4().hex[:10]}"
    admin = create_engine(PG)
    with admin.begin() as c:
        c.execute(text(f"CREATE SCHEMA {schema}"))
    url = PG + ("&" if "?" in PG else "?") + f"options=-csearch_path%3D{schema}"
    yield pdb.connect(url)
    with admin.begin() as c:
        c.execute(text(f"DROP SCHEMA {schema} CASCADE"))


def sprint(conn, name="s1", parent=None, lane_row=None):
    first = D("created", "conductor", None, 1, payload={"lane_row": lane_row} if lane_row else None)
    return pdb.create_table(
        conn,
        "sprint",
        "repo",
        name,
        parent,
        {"mode": "standard"},
        [first, D("created", "conductor", "設計", 1), D("created", "conductor", "実装", 1)],
    )


def test_three_levels_and_I1(store):
    with pdb.transaction(store) as c:
        plan = pdb.create_table(c, "plan", "repo", "p", None, None, [D("created", "organizer", payload={"max_lanes": 2})])
        lane = pdb.create_table(c, "lane", "repo", "p/a", plan, None, [D("created", "organizer")])
        sid = sprint(c, parent=lane, lane_row=1)
    assert [pm.level_of(t) for t in (plan, lane, sid)] == ["plan", "lane", "sprint"]
    for parent in (None, lane):  # 計画の外の単独のスプリントでも断られる
        with pytest.raises(pm.Refused) as e, pdb.transaction(store) as c:
            sprint(c, parent=parent)
        assert e.value.current == sid
    with pdb.transaction(store) as c:
        pdb.append_events(c, sid, [D("cancelled", "conductor", None, 1, reason="やめる")])
    with pytest.raises(pm.Refused) as e, pdb.transaction(store) as c:  # 中止の後の同じ名前
        sprint(c)
    assert e.value.current == sid


def test_append_seq_conflict_dedupe_and_writer(store):
    with pdb.transaction(store) as c:
        sid = sprint(c)
    with pdb.transaction(store) as c:
        assert pdb.append_events(c, sid, [D("started", "workflow", "設計", 1, dedupe_key="x:設計:started")], 3) == [4]
        assert pdb.append_events(c, sid, [D("started", "workflow", "設計", 1, dedupe_key="x:設計:started")]) == [4]  # 重複の鍵
    with pytest.raises(pdb.Conflict), pdb.transaction(store) as c:
        pdb.append_events(c, sid, [D("finished", "workflow", "設計", 1)], 3)
    with pytest.raises(pm.Refused, match="I6"), pdb.transaction(store) as c:
        pdb.append_events(c, sid, [D("finished", "organizer", "設計", 1)])
    with pytest.raises(pm.Refused, match="I4"), pdb.transaction(store) as c:
        pdb.append_events(c, sid, [D("disabled", "conductor", "設計", 1, reason="x")])
    with pytest.raises(pm.Refused, match="決定 17"), pdb.transaction(store) as c:
        pdb.append_events(c, sid, [D("row_ended", "workflow", "1")])
    with pdb.transaction(store) as c:
        assert [e.seq for e in pdb.read_progress_events(c, sid)] == [1, 2, 3, 4]
        assert pdb.read_progress_events(c, sid, 4) == []


def test_transaction_rolls_back_everything(store):
    with pytest.raises(RuntimeError), pdb.transaction(store) as c:
        sprint(c)
        raise RuntimeError("途中で落ちる")
    with pdb.transaction(store) as c:
        assert pdb.progress_table(c, pm.table_id("sprint", "repo", "s1")) is None


def test_revision_keeps_old(store):
    with pdb.transaction(store) as c:
        sid = sprint(c)
        pdb.append_events(c, sid, [D("started", "workflow", "設計", 1), D("finished", "workflow", "設計", 1)])
    with pdb.transaction(store) as c:
        old = pm.fold(pdb.read_progress_events(c, sid), 1)
        rev = pdb.add_revision(
            c,
            sid,
            {"mode": "standard", "rev": 2},
            [
                D("scope_changed", "conductor", None, reason="課題を外す"),
                D("carried", "conductor", "設計", payload={"state": pm.FINISHED}),
                D("created", "conductor", "実装"),
            ],
        )
        assert rev == 2
        assert pdb.add_revision(c, sid, {"rev": 3}, []) == 3
    with pdb.transaction(store) as c:
        ev = pdb.read_progress_events(c, sid)
        assert pm.fold(ev, 1) == old
        assert pm.fold(ev, 2)["設計"].state == pm.FINISHED
        assert pdb.definition(c, sid, 1)[1] == {"mode": "standard"}
        assert pdb.definition(c, sid)[0] == 3


def _plan_with_lane(c):
    plan = pdb.create_table(
        c,
        "plan",
        "repo",
        "p",
        None,
        None,
        [
            D("created", "organizer", payload={"max_lanes": 3}),
            D("lane_added", "organizer", payload={"lane": "a", "touches": []}),
            D("plan_started", "organizer"),
        ],
    )
    lane = pdb.create_table(c, "lane", "repo", "p/a", plan, None, [D("created", "organizer")])
    for n in (1, 2):
        pdb.lane_write(c, lane, D("row_added", "organizer", str(n), payload={"row": n, "issues": [n], "touches": []}))
    sid = sprint(c, parent=lane, lane_row=1)
    pdb.lane_write(c, lane, D("row_started", "conductor", "1", payload={"row": 1, "sprint": sid}))
    return plan, lane, sid


def test_end_lane_row_is_idempotent_and_returns_next(store):
    with pdb.transaction(store) as c:
        plan, lane, sid = _plan_with_lane(c)
    with pdb.transaction(store) as c:
        got = pdb.end_lane_row(c, sid, "completed", "workflow")
        assert got == {"next_row": {"lane": "a", "row": 2, "issues": [2], "args": []}}
        assert pdb.end_lane_row(c, sid, "completed", "workflow") == got  # 流し直しで 2 度呼んでも書かない
    with pdb.transaction(store) as c:
        assert sum(e.kind == "row_ended" for e in pdb.read_progress_events(c, lane)) == 1
        alone = sprint(c, name="alone")
        assert pdb.end_lane_row(c, alone, "completed", "workflow") is None


def test_lane_rules_refuse_and_request(store):
    with pdb.transaction(store) as c:
        plan, lane, sid = _plan_with_lane(c)
    with pytest.raises(pm.Refused, match="I12a"), pdb.transaction(store) as c:
        pdb.lane_write(c, lane, D("row_cancelled", "conductor", "1", payload={"row": 1}))
    with pdb.transaction(store) as c:
        rid = pdb.open_request(c, plan, {"move": 2}, "別のレーンへ", "workflow")
        view, lanes, ids = pdb.plan_and_lanes(c, plan)
    assert view["requests"][rid]["open"] and ids == {"a": lane} and [r["row"] for r in lanes["a"]] == [1, 2]


def test_default_url_is_shared_by_worktrees(tmp_path, monkeypatch):
    monkeypatch.delenv(pdb.ENV_DB, raising=False)
    monkeypatch.delenv("NDF_STATE_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "x"],
        check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@b", "GIT_COMMITTER_NAME": "a", "GIT_COMMITTER_EMAIL": "a@b"},
    )
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(tmp_path / "wt")], check=True)
    a, b = pdb.default_url(repo), pdb.default_url(tmp_path / "wt")
    assert a == b and a.endswith("/.git/ndf/state/progress/ndf-progress.sqlite")
    monkeypatch.setenv(pdb.ENV_DB, "sqlite:///x")
    assert pdb.default_url(repo) == "sqlite:///x"


def test_newer_schema_is_refused(tmp_path):
    url = f"sqlite:///{tmp_path}/v.sqlite"
    st = pdb.connect(url)
    with pdb.transaction(st) as c:
        c.execute(pdb.META.update().values(value=str(pdb.SCHEMA_VERSION + 1)))
    with pytest.raises(pdb.Unreadable, match="新しい"):
        pdb.connect(url)


def test_connect_hides_password():
    with pytest.raises(pdb.Unreadable) as e:
        pdb.connect("nosuchdialect://u:secret@h/db")
    assert "secret" not in str(e.value)


HOLDER = textwrap.dedent(
    """
    import sys, time
    sys.path.insert(0, {lib!r})
    import progress_db as pdb
    st = pdb.connect(sys.argv[1])
    with pdb.run_lock(st, "sprint:x:s"):
        print("held", flush=True)
        time.sleep(60)
    """
)


def test_run_lock_refuses_second_process(store, tmp_path):
    script = tmp_path / "hold.py"
    script.write_text(HOLDER.format(lib=str(LIB)))
    p = subprocess.Popen([sys.executable, str(script), store.url], stdout=subprocess.PIPE, text=True)
    try:
        assert p.stdout.readline().strip() == "held"
        with pytest.raises(pm.Refused, match="I14"), pdb.run_lock(store, "sprint:x:s"):
            pass
        with pdb.run_lock(store, "sprint:x:other"):
            pass
    finally:
        p.kill()
        p.wait()
    with pdb.run_lock(store, "sprint:x:s"):  # 1 つ目を落とすと取れる
        pass
