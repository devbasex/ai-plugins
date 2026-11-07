"""チェックアウトの記録の移行（#2766）: queue の耐久の記録の鍵の付け替えと、移し先の鍵のロック。"""

from __future__ import annotations

import fcntl
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

import durable  # noqa: E402
import state_checkout as sc  # noqa: E402
import state_home as sh  # noqa: E402

PLAN = "q-aaaaaaaa"  # プランの状態の名前（`<stem>-<プランのパスの鍵>`）


def _repo(tmp_path: Path) -> Path:
    main = tmp_path / "work" / "repo"
    (main / ".git").mkdir(parents=True, exist_ok=True)
    return main


def _done(sv: Path) -> str:
    """queue の実行の鍵の元（`flow.launch_queue` と同じ: 状態ディレクトリの queue-done.json の解決したパス）。"""
    return str((sv / PLAN / "queue-done.json").resolve())


CALLS: list[int] = []


@durable.step
def step_once(x: int) -> int:
    CALLS.append(x)
    return x


@durable.workflow
def plan_once(x: int) -> int:
    return step_once(x)


def _finish_plan_in_old_queue(tmp_path: Path, env: dict, monkeypatch) -> Path:
    """旧の置き場で queue の耐久の記録を開き、プランを 1 つ済ませる。旧の sv を返す。"""
    old_sv = sh.legacy_home(env) / "sv"
    (old_sv / PLAN).mkdir(parents=True)
    (old_sv / PLAN / "plan.json").write_text(json.dumps({"作業場所": str(_repo(tmp_path))}))
    monkeypatch.setenv("NDF_DBOS_DIR", str(sh.legacy_home(env) / "dbos"))
    durable.launch("queue", _done(old_sv))
    try:
        assert durable.output_of(durable.start(durable.resolve("plan-p"), plan_once, 7)) == 7
    finally:
        durable.close()
    assert CALLS == [7]
    return old_sv


def _resume_queue(to: Path, monkeypatch) -> durable.WorkflowRef:
    """移した後の queue の打ち直し（新しい置き場の鍵で開く）。済んだプランの実行の回を返す。"""
    monkeypatch.setenv("NDF_DBOS_DIR", str(to / "dbos"))
    durable.launch("queue", _done(to / "sv"))
    try:
        return durable.resolve("plan-p", finished=lambda out: True)
    finally:
        durable.close()


def test_resumed_queue_after_migrate_opens_the_moved_record(tmp_path, monkeypatch):
    """移した後に同じ queue を打ち直すと、移す前の記録（済んだプラン）を開き、流し直さない。"""
    CALLS.clear()
    env = {"HOME": str(tmp_path / "h")}
    _finish_plan_in_old_queue(tmp_path, env, monkeypatch)
    r = sc.migrate(env, cwd=_repo(tmp_path))
    to = Path(r["checkout"]["to"])
    assert r["status"] == "migrated" and len(r["dbos"]["rekeyed"]) == 1
    ref = _resume_queue(to, monkeypatch)
    assert (ref.id, ref.action, ref.output) == ("plan-p-1", "done", 7)
    assert CALLS == [7]


def test_migrate_rekeys_records_moved_by_an_earlier_migrate(tmp_path, monkeypatch):
    """付け替えの前の版で移し終えた記録（新の sv と旧の鍵の記録）も、もう一度 migrate を打てば付け替える。"""
    CALLS.clear()
    env = {"HOME": str(tmp_path / "h")}
    old_sv = _finish_plan_in_old_queue(tmp_path, env, monkeypatch)
    to = (tmp_path / "work/repo/.git/ndf/state").resolve()
    (to / "sv").mkdir(parents=True)
    os.rename(old_sv / PLAN, to / "sv" / PLAN)
    os.rename(sh.legacy_home(env) / "dbos", to / "dbos")
    r = sc.migrate(env, cwd=_repo(tmp_path))
    assert r["status"] == "migrated" and r["dbos"]["moved"] == [] and len(r["dbos"]["rekeyed"]) == 1
    assert _resume_queue(to, monkeypatch).action == "done" and CALLS == [7]
    assert sc.migrate(env, cwd=_repo(tmp_path))["status"] == "none"  # 何度打ってもよい


def _old_record(tmp_path: Path, env: dict) -> tuple[Path, Path, str]:
    """旧の置き場の耐久の記録 1 本（鍵は `exp-…`）。(旧の dbos, 移し先の dbos, 鍵)。"""
    old = sh.legacy_home(env) / "dbos"
    old.mkdir(parents=True)
    key = "exp-bbbbbbbbbbbb"
    (old / f"{key}.sqlite").write_text("old")
    (old / f"{key}.lock").write_text("")
    to = (tmp_path / "work/repo/.git/ndf/state/dbos").resolve()
    to.mkdir(parents=True)
    return old, to, key


def test_migrate_keeps_a_record_whose_destination_lock_is_held(tmp_path):
    """移し先の同じ鍵のロックを動いている起動が持っていれば、移さず残し、そのロックの実体を置き換えない。"""
    env = {"HOME": str(tmp_path / "h")}
    old, to, key = _old_record(tmp_path, env)
    with open(to / f"{key}.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        inode = os.stat(to / f"{key}.lock").st_ino
        r = sc.migrate(env, cwd=_repo(tmp_path))
        assert os.stat(to / f"{key}.lock").st_ino == inode
    assert r["status"] == "kept"
    assert [k["path"] for k in r["dbos"]["kept"]] == [str(old / f"{key}.sqlite")]
    assert (old / f"{key}.sqlite").read_text() == "old" and not (to / f"{key}.sqlite").exists()
    r = sc.migrate(env, cwd=_repo(tmp_path))  # 起動が終われば次に打てば移る
    assert r["status"] == "migrated" and (to / f"{key}.sqlite").read_text() == "old"


def test_migrate_holds_the_destination_lock_while_moving(tmp_path, monkeypatch):
    """衝突の再確認から一式の移動の完了まで、移し先の同じ鍵のロックを持つ（その間に同じ鍵の起動は取れない）。"""
    import shutil

    env = {"HOME": str(tmp_path / "h")}
    _old, to, key = _old_record(tmp_path, env)
    seen: list[bool] = []
    real_move = shutil.move

    def move_and_try_lock(src, dst):
        fd = os.open(to / f"{key}.lock", os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            seen.append(False)
        except OSError:
            seen.append(True)
        finally:
            os.close(fd)
        return real_move(src, dst)

    monkeypatch.setattr(shutil, "move", move_and_try_lock)
    assert sc.migrate(env, cwd=_repo(tmp_path))["status"] == "migrated"
    assert seen and all(seen)


@pytest.mark.parametrize("taken", [".sqlite", ".sqlite-wal"])
def test_rekey_keeps_the_record_when_the_new_key_is_taken(tmp_path, monkeypatch, taken):
    """付け替え先の鍵の記録が既にあれば付け替えず、理由つきで残す。"""
    env = {"HOME": str(tmp_path / "h")}
    to = (tmp_path / "work/repo/.git/ndf/state").resolve()
    (to / "sv" / PLAN).mkdir(parents=True)
    (to / "dbos").mkdir()
    old_key = durable.launch_key("queue", _done(sh.legacy_home(env) / "sv"))
    new_key = durable.launch_key("queue", _done(to / "sv"))
    (to / "dbos" / f"{old_key}.sqlite").write_text("old")
    (to / "dbos" / f"{new_key}{taken}").write_text("new")
    r = sc.migrate(env, cwd=_repo(tmp_path))
    assert r["status"] == "kept" and r["dbos"]["rekeyed"] == []
    assert [k["path"] for k in r["dbos"]["kept"]] == [str(to / "dbos" / f"{old_key}.sqlite")]
    assert "付け替え先" in r["dbos"]["kept"][0]["reason"]
    assert (to / "dbos" / f"{old_key}.sqlite").read_text() == "old"
