"""状態の置き場（#2766）: 持ち主ごとの 3 つの根・旧の置き場を読まないこと・migrate の移行・シェルの同じ順。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

import state_checkout as sc  # noqa: E402
import state_home as sh  # noqa: E402

WF = SCRIPTS.parent / "skills" / "development-workflow" / "scripts" / "lib" / "workflow-common.sh"


def _repo(tmp_path: Path) -> Path:
    """`.git` を持つチェックアウトと、その worktree（`.git` がファイル）。worktree を返す。"""
    main = tmp_path / "work" / "repo"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    (main / ".git" / "worktrees" / "wt" / "commondir").write_text("../..\n")
    wt = main / ".worktrees" / "wt"
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt'}\n")
    return wt


def test_three_roots_by_default(tmp_path):
    wt = _repo(tmp_path)
    env = {"HOME": str(tmp_path / "h")}
    assert sh.user_home(env) == tmp_path / "h/.claude/ndf/state"
    assert sh.container_home(env) == tmp_path / "h/.local/state/ndf"
    # worktree から見ても、チェックアウトの git の共通ディレクトリの下になる
    assert sh.checkout_home(wt, env) == (tmp_path / "work/repo/.git/ndf/state").resolve()
    # git の外はコンテナの根
    (tmp_path / "plain").mkdir()
    assert sh.checkout_home(tmp_path / "plain", env) == tmp_path / "h/.local/state/ndf"


@pytest.mark.parametrize("extra, want", [({"XDG_STATE_HOME": "/x"}, "/x/ndf"), ({"XDG_STATE_HOME": "/x", "NDF_STATE_HOME": "/n"}, "/n")])
def test_explicit_root_wins_for_all_three(tmp_path, extra, want):
    wt = _repo(tmp_path)
    env = {"HOME": str(tmp_path / "h"), **extra}
    assert sh.user_home(env) == sh.container_home(env) == sh.checkout_home(wt, env) == Path(want)


def test_two_checkouts_of_the_same_path_do_not_share(tmp_path):
    """同じ利用者の根を共有する 2 つのコンテナでも、チェックアウトの記録は git ディレクトリごとに分かれる。"""
    a, b = _repo(tmp_path / "c1"), _repo(tmp_path / "c2")
    env = {"HOME": str(tmp_path / "h")}
    assert sh.checkout_home(a, env) != sh.checkout_home(b, env)


def test_migrate_merges_ledgers_and_leaves_container_state(tmp_path):
    env = {"HOME": str(tmp_path)}
    old, new = sh.legacy_home(env), sh._user_default(env)
    (old / "usage/claude").mkdir(parents=True)
    (old / "usage/claude/o__r.jsonl").write_text('{"a":1}\n')
    (old / "ask.jsonl").write_text('{"q":1}\n')
    (old / "stages").mkdir()
    (old / "stages/o__r__1.json").write_text("{}")
    (old / "relay").mkdir()
    (old / "relay/hub.json").write_text("{}")
    (new / "usage/claude").mkdir(parents=True)
    (new / "usage/claude/o__r.jsonl").write_text('{"b":2}\n')
    (new / "stages").mkdir()
    (new / "stages/o__r__1.json").write_text('{"newer":1}')

    r = sh.migrate(env)
    assert r["status"] == "kept"
    assert (new / "usage/claude/o__r.jsonl").read_text() == '{"b":2}\n{"a":1}\n'  # 時刻の無い行は追記の並び（新 → 旧）
    assert (new / "ask.jsonl").read_text() == '{"q":1}\n'
    assert r["kept"] == [str(old / "stages/o__r__1.json")]  # 上書きの形は旧に残す
    assert (new / "stages/o__r__1.json").read_text() == '{"newer":1}'
    assert (old / "relay/hub.json").exists()  # コンテナの状態は動かさない
    assert not (old / "usage/claude/o__r.jsonl").exists() and not (old / "ask.jsonl").exists()
    assert (old / "ask.jsonl.migrated").read_text() == '{"q":1}\n'  # 旧の台帳は消さずに名前を変えて残す
    assert sh.migrate(env)["status"] == "kept"  # 打ち直しても足し直さない
    assert (new / "usage/claude/o__r.jsonl").read_text() == '{"b":2}\n{"a":1}\n'
    assert not (old / "usage").exists() and not (old / "ask.jsonl.migrated").exists()  # 移り切った残りは次の回に消す


def test_migrate_takes_ledger_name_off_before_reading(tmp_path, monkeypatch):
    """旧の台帳は名前を外してから読む。読んだ後に旧の版が追記した行は旧の名前で作り直され、次の migrate で移る。"""
    env = {"HOME": str(tmp_path)}
    old, new = sh.legacy_home(env), sh._user_default(env)
    old.mkdir(parents=True)
    (old / "ask.jsonl").write_text('{"q":1}\n')
    real_rename = sh.os.rename

    def rename_then_append(src, dst):
        real_rename(src, dst)
        Path(src).write_text('{"q":2}\n')  # 名前を外した直後に旧の版が追記する

    monkeypatch.setattr(sh.os, "rename", rename_then_append)
    assert sh.migrate(env)["status"] == "migrated"
    monkeypatch.setattr(sh.os, "rename", real_rename)
    assert (old / "ask.jsonl").read_text() == '{"q":2}\n'  # 追記された行は消えない
    assert sh.migrate(env)["status"] == "migrated"
    assert (new / "ask.jsonl").read_text() == '{"q":1}\n{"q":2}\n'
    assert not (old / "ask.jsonl.migrated").exists()  # 前の回の残りは新に入り切ったので消す
    assert (old / "ask.jsonl.migrated-1").read_text() == '{"q":2}\n'  # 旧の台帳を足した回は残す
    assert sh.migrate(env)["status"] == "none"
    assert not any(old.iterdir())


def test_migrate_keeps_rows_written_to_an_open_old_ledger_after_migrate(tmp_path):
    """旧の版の書き手が migrate の後に開いた fd へ足した行は `.migrated` に残り、次の migrate が足す（重複させない）。
    新の台帳に中身がすべて入った `.migrated` だけを消す。"""
    env = {"HOME": str(tmp_path)}
    old, new = sh.legacy_home(env), sh._user_default(env)
    old.mkdir(parents=True)
    (old / "ask.jsonl").write_text(_row("2026-11-01T00:00:00Z", q=1))
    with (old / "ask.jsonl").open("a") as writer:  # 旧の版の書き手（開いたまま）
        assert sh.migrate(env)["status"] == "migrated"
        writer.write(_row("2026-11-02T00:00:00Z", q=2))
    assert [json.loads(line)["q"] for line in (new / "ask.jsonl").read_text().splitlines()] == [1]
    assert sh.migrate(env)["status"] == "migrated"
    assert [json.loads(line)["q"] for line in (new / "ask.jsonl").read_text().splitlines()] == [1, 2]
    assert not (old / "ask.jsonl.migrated").exists()


def test_migrate_keeps_rows_written_to_an_old_ledger_opened_while_a_migrated_one_exists(tmp_path, monkeypatch):
    """`.migrated` が既にあっても旧の台帳は名前を外して残す。読んだ後に、それ以前に開いた fd へ足された行は
    次の migrate で新の台帳へ入る（消さない）。"""
    env = {"HOME": str(tmp_path)}
    old, new = sh.legacy_home(env), sh._user_default(env)
    old.mkdir(parents=True)
    (old / "ask.jsonl.migrated").write_text(_row("2026-11-01T00:00:00Z", q=1))
    (old / "ask.jsonl").write_text(_row("2026-11-02T00:00:00Z", q=2))
    writer = (old / "ask.jsonl").open("a")  # 旧の版の書き手（migrate の前から開いたまま）
    real_merge = sh._merge_into

    def merge_then_append(dst, sources):
        added = real_merge(dst, sources)
        writer.write(_row("2026-11-03T00:00:00Z", q=3))  # 読んだ後に足す
        writer.close()
        return added

    monkeypatch.setattr(sh, "_merge_into", merge_then_append)
    assert sh.migrate(env)["status"] == "migrated"
    monkeypatch.setattr(sh, "_merge_into", real_merge)
    assert [json.loads(line)["q"] for line in (new / "ask.jsonl").read_text().splitlines()] == [1, 2]
    assert sh.migrate(env)["status"] == "migrated"
    assert [json.loads(line)["q"] for line in (new / "ask.jsonl").read_text().splitlines()] == [1, 2, 3]
    assert sh.migrate(env)["status"] == "none"
    assert not any(old.iterdir())


def test_migrate_keeps_a_migrated_ledger_not_yet_in_the_new_one(tmp_path, monkeypatch):
    """足し合わせた後で `.migrated` に行が足されれば（新に入り切っていない）消さず、次の migrate で足してから消す。"""
    env = {"HOME": str(tmp_path)}
    old, new = sh.legacy_home(env), sh._user_default(env)
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "ask.jsonl.migrated").write_text(_row("2026-11-01T00:00:00Z", q=1) + _row("2026-11-02T00:00:00Z", q=2))
    (new / "ask.jsonl").write_text(_row("2026-11-01T00:00:00Z", q=1))
    real_merge = sh._merge_into

    def merge_then_append(dst, sources):
        added = real_merge(dst, sources)
        with sources[0].open("a") as f:  # 足し合わせた直後に旧の版の書き手が足す
            f.write(_row("2026-11-03T00:00:00Z", q=3))
        return added

    monkeypatch.setattr(sh, "_merge_into", merge_then_append)
    assert sh.migrate(env)["status"] == "migrated"
    monkeypatch.setattr(sh, "_merge_into", real_merge)
    assert [json.loads(line)["q"] for line in (new / "ask.jsonl").read_text().splitlines()] == [1, 2]  # q=1 を 2 度足さない
    assert (old / "ask.jsonl.migrated").exists()
    assert sh.migrate(env)["status"] == "migrated"
    assert [json.loads(line)["q"] for line in (new / "ask.jsonl").read_text().splitlines()] == [1, 2, 3]
    assert not (old / "ask.jsonl.migrated").exists()


def test_migrate_stops_while_another_migrate_holds_the_lock(tmp_path):
    """別の migrate（共有する利用者の置き場の別のコンテナを含む）がロックを持っていれば、待たずに止まり何も移さない。"""
    import fcntl

    env = {"HOME": str(tmp_path)}
    old, new = sh.legacy_home(env), sh._user_default(env)
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "ask.jsonl").write_text('{"q":1}\n')
    with open(new / sh.LOCK, "a") as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = sh.migrate(env)
    assert r["status"] == "busy" and r["held"] == [str(new / sh.LOCK)] and r["moved"] == []
    assert (old / "ask.jsonl").read_text() == '{"q":1}\n' and not (new / "ask.jsonl").exists()
    assert sh.migrate(env)["status"] == "migrated"  # ロックが外れれば移す


def test_migrate_none_with_explicit_root(tmp_path):
    (tmp_path / ".local/state/ndf").mkdir(parents=True)
    (tmp_path / ".local/state/ndf/ask.jsonl").write_text("x\n")
    assert sh.migrate({"HOME": str(tmp_path), "NDF_STATE_HOME": str(tmp_path / "n")})["status"] == "none"
    # XDG_STATE_HOME が旧の根と同じなら、旧の版も新の版も同じ置き場に書いている
    assert sh.migrate({"HOME": str(tmp_path), "XDG_STATE_HOME": str(tmp_path / ".local/state")})["status"] == "none"
    assert (tmp_path / ".local/state/ndf/ask.jsonl").read_text() == "x\n"
    assert sh.migrate({"HOME": str(tmp_path / "empty")})["status"] == "none"


def test_migrate_with_xdg_moves_ledgers_the_old_version_wrote_to_the_fixed_place(tmp_path):
    """旧の版の問い・MVV の記録は XDG_STATE_HOME を見ずに `~/.local/state/ndf` へ書いていた。XDG を指定していても移す。"""
    env = {"HOME": str(tmp_path), "XDG_STATE_HOME": str(tmp_path / "xdg")}
    old, new = sh.legacy_home(env), sh.user_home(env)
    (old / "project-mvv/r").mkdir(parents=True)
    (old / "project-mvv/r/v1.md").write_text("mvv")
    for name in sh.FIXED_KINDS:
        if name.endswith(".jsonl"):
            (old / name).write_text('{"at":"2026-11-01T00:00:00Z","old":1}\n')
    (old / "usage").mkdir()  # XDG を見ていた台帳は旧の版も XDG の下に書いた。固定の置き場のものは動かさない
    (old / "usage/u.jsonl").write_text('{"u":1}\n')
    new.mkdir(parents=True)
    (new / "ask.jsonl").write_text('{"at":"2026-11-02T00:00:00Z","new":1}\n')

    r = sh.migrate(env)
    assert r["status"] == "migrated" and r["to"] == str(new)
    assert (new / "ask.jsonl").read_text() == '{"at":"2026-11-01T00:00:00Z","old":1}\n{"at":"2026-11-02T00:00:00Z","new":1}\n'
    for name in sh.FIXED_KINDS:
        if name.endswith(".jsonl"):
            assert '"old":1' in (new / name).read_text()
    assert (new / "project-mvv/r/v1.md").read_text() == "mvv"
    assert (old / "usage/u.jsonl").exists() and not (new / "usage").exists()
    assert r["checkout"] == {"to": None, "reason": None}  # 耐久の記録・プランの状態は旧の版も XDG の下にあった
    assert sh.migrate(env)["status"] == "none"  # 打ち直しても足し直さない
    assert (new / "ask.jsonl").read_text().count("\n") == 2


@pytest.mark.parametrize("extra", [{}, {"XDG_STATE_HOME": "/x"}, {"XDG_STATE_HOME": "/x", "NDF_STATE_HOME": "/n"}])
def test_shell_user_root_matches(tmp_path, extra):
    env = {"HOME": str(tmp_path), "PATH": os.environ["PATH"], **extra}
    out = subprocess.run(["bash", "-c", f". '{WF}' && ndf_user_state_home"], env=env, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == str(sh.user_home(env))


def test_cli_path_reports_three_roots(tmp_path):
    wt = _repo(tmp_path)
    env = {"HOME": str(tmp_path / "h"), "PATH": os.environ["PATH"]}
    out = subprocess.run([sys.executable, str(SCRIPTS / "state-home.py"), "path"], env=env, cwd=wt, capture_output=True, text=True)
    assert out.returncode == 0
    m = json.loads(out.stdout)["metrics"]
    assert m == {
        "checkout": str((tmp_path / "work/repo/.git/ndf/state").resolve()),
        "user": str(tmp_path / "h/.claude/ndf/state"),
        "container": str(tmp_path / "h/.local/state/ndf"),
    }


def _row(at: str, **kw) -> str:
    return json.dumps({"at": at, **kw}) + "\n"


def test_migrate_orders_rows_by_time(tmp_path):
    """旧と新の行を時刻（`at`）の順に並べて書き直す。時刻の書き方（`Z`・時差・時差なし）が混ざっても順に並び、時刻の無い行は直前の行の隣に残る。"""
    env = {"HOME": str(tmp_path)}
    old, new = sh.legacy_home(env), sh._user_default(env)
    (old / "usage/claude").mkdir(parents=True)
    (new / "usage/claude").mkdir(parents=True)
    (old / "usage/claude/o__r.jsonl").write_text(
        _row("2026-11-01T00:00:00Z", n=1) + '{"n":"1b"}\n' + _row("2026-11-03T09:00:00+09:00", n=3)
    )
    (new / "usage/claude/o__r.jsonl").write_text(_row("2026-11-02T00:00:00", n=2) + _row("2026-11-05T00:00:00Z", n=5))
    assert sh.migrate(env)["status"] == "migrated"
    got = [json.loads(line)["n"] for line in (new / "usage/claude/o__r.jsonl").read_text().splitlines()]
    assert got == [1, "1b", 2, 3, 5]
    assert sh.migrate(env)["status"] == "none"  # 打ち直しても足し直さない
    assert len((new / "usage/claude/o__r.jsonl").read_text().splitlines()) == 5


def test_unknown_streak_counts_by_time_after_migrate(tmp_path):
    """旧の台帳の古い「判定できない」は、新の台帳の新しい判定より前に並ぶ。連続の回数は新しい判定で途切れる（PR 2767 の round 4）。"""
    import project_mvv_signals as pms

    env = {"HOME": str(tmp_path)}
    old, new = sh.legacy_home(env), sh._user_default(env)
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    ref = {"sha256": "s"}
    (old / "mvv-gate.jsonl").write_text(
        "".join(_row(f"2026-11-0{i}T00:00:00Z", verdict="unknown", sprint=f"sp{i}", gate="design", project_mvv=ref) for i in (1, 2, 3))
    )
    (new / "mvv-gate.jsonl").write_text(_row("2026-11-06T00:00:00Z", verdict="follow", sprint="sp9", gate="design", project_mvv=ref))
    assert sh.migrate(env)["status"] == "migrated"
    path = new / "mvv-gate.jsonl"
    assert pms.unknown_streak(pms.read_jsonl(path), "s") == 0
    pms.append_jsonl(path, {"at": "2026-11-07T00:00:00Z", "verdict": "unknown", "sprint": "sp10", "gate": "design", "project_mvv": ref})
    assert pms.unknown_streak(pms.read_jsonl(path), "s") == 1


def test_migrate_keeps_rows_appended_while_rewriting(tmp_path, monkeypatch):
    """書き直しの間に別のプロセスが移し先へ追記した行（移し先を作り直して書く）を落とさず、末尾に残す。"""
    env = {"HOME": str(tmp_path)}
    old, new = sh.legacy_home(env), sh._user_default(env)
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "ask.jsonl").write_text(_row("2026-11-01T00:00:00Z", q=1))
    (new / "ask.jsonl").write_text(_row("2026-11-02T00:00:00Z", q=2))
    real_link = sh.os.link
    calls = []

    def write_then_link(src, dst):
        if not calls:  # 一時ファイルを置く直前に、新の版が移し先を作り直して追記する
            Path(dst).write_text(_row("2026-11-07T00:00:00Z", q=3))
        calls.append(dst)
        real_link(src, dst)

    monkeypatch.setattr(sh.os, "link", write_then_link)
    assert sh.migrate(env)["status"] == "migrated"
    assert len(calls) == 2
    assert [json.loads(line)["q"] for line in (new / "ask.jsonl").read_text().splitlines()] == [1, 2, 3]
    assert sorted(p.name for p in new.iterdir()) == [sh.LOCK, "ask.jsonl"]  # 名前を外した台帳も一時ファイルも残さない


@pytest.mark.parametrize("stop", ["before_link", "after_link"])
def test_migrate_resumes_a_stopped_rewrite(tmp_path, monkeypatch, stop):
    """移し先の名前を外した後（置く前・置いた後）で止まっても、次の migrate が欠かさず二重にせず元の名前の台帳へ戻す。"""
    env = {"HOME": str(tmp_path)}
    old, new = sh.legacy_home(env), sh._user_default(env)
    (old / "checks").mkdir(parents=True)
    (new / "checks").mkdir(parents=True)
    (old / "checks/o__r.jsonl").write_text(_row("2026-11-01T00:00:00Z", c=1))
    (new / "checks/o__r.jsonl").write_text(_row("2026-11-02T00:00:00Z", c=2))
    real_link = sh.os.link

    def stopped(src, dst):
        if stop == "after_link":
            real_link(src, dst)
        raise KeyboardInterrupt

    monkeypatch.setattr(sh.os, "link", stopped)
    with pytest.raises(KeyboardInterrupt):
        sh.migrate(env)
    monkeypatch.setattr(sh.os, "link", real_link)
    (new / "checks/o__r.jsonl").parent.mkdir(exist_ok=True)
    with (new / "checks/o__r.jsonl").open("a") as f:  # 止まった後に新の版が書いた
        f.write(_row("2026-11-07T00:00:00Z", c=3))
    assert sh.migrate(env)["status"] == "migrated"
    assert [json.loads(line)["c"] for line in (new / "checks/o__r.jsonl").read_text().splitlines()] == [1, 2, 3]
    assert sorted(p.name for p in (new / "checks").iterdir()) == ["o__r.jsonl"]
    assert not old.exists() or not any(old.rglob("*"))


# ---- 旧の置き場を読まない（2026-11-07 の決定。更新したら各コンテナで一度 migrate を打つ） ----


def test_user_home_never_reads_the_old_place(tmp_path):
    env = {"HOME": str(tmp_path)}
    old = tmp_path / ".local/state/ndf"
    for kind in sh.USER_KINDS:
        (old / kind).mkdir(parents=True)
    assert sh.user_home(env) == tmp_path / ".claude/ndf/state"
    out = subprocess.run(
        ["bash", "-c", f". '{WF}' && ndf_user_state_home"],
        env={**env, "PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == str(tmp_path / ".claude/ndf/state")


def test_durable_record_path_never_reads_the_old_place(tmp_path, monkeypatch):
    import durable_keys

    for name in ("NDF_DBOS_DIR", "XDG_STATE_HOME", "NDF_STATE_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    monkeypatch.chdir(_repo(tmp_path))
    key = durable_keys.launch_key("parallel", "p3")
    old = tmp_path / "h/.local/state/ndf/dbos" / f"{key}.sqlite"
    old.parent.mkdir(parents=True)
    old.write_text("x")
    assert durable_keys.record_path("parallel", "p3") == (tmp_path / "work/repo/.git/ndf/state/dbos").resolve() / f"{key}.sqlite"


# ---- migrate: チェックアウトの記録（dbos/・sv/）を打った作業ツリーのチェックアウトの根へ移す ----


def _old_records(tmp_path: Path, env: dict) -> Path:
    """旧の置き場の耐久の記録 2 本（1 本は -wal 付き）とプランの状態 2 つ。旧の根を返す。"""
    old = sh.legacy_home(env)
    (old / "dbos").mkdir(parents=True)
    for key in ("parallel-aaaaaaaaaaaa", "exp-bbbbbbbbbbbb"):
        (old / "dbos" / f"{key}.sqlite").write_text(key)
        (old / "dbos" / f"{key}.lock").write_text("")
    (old / "dbos/parallel-aaaaaaaaaaaa.sqlite-wal").write_text("wal")
    for name in ("1-design-0e3fcff9", "plan-ca92eb3d"):
        (old / "sv" / name / "work").mkdir(parents=True)
        (old / "sv" / name / "state.json").write_text("{}")
    return old


def test_migrate_moves_checkout_records_to_the_checkout_root(tmp_path):
    env = {"HOME": str(tmp_path / "h")}
    wt = _repo(tmp_path)
    old = _old_records(tmp_path, env)
    to = (tmp_path / "work/repo/.git/ndf/state").resolve()
    r = sc.migrate_all(env, cwd=wt)
    assert r["status"] == "migrated"
    assert r["checkout"] == {"to": str(to), "reason": None}
    assert sorted(r["dbos"]["moved"]) == [str(to / "dbos/exp-bbbbbbbbbbbb.sqlite"), str(to / "dbos/parallel-aaaaaaaaaaaa.sqlite")]
    assert sorted(r["sv"]["moved"]) == [str(to / "sv/1-design-0e3fcff9"), str(to / "sv/plan-ca92eb3d")]
    assert r["dbos"]["kept"] == r["sv"]["kept"] == []
    assert sorted(p.name for p in (to / "dbos").iterdir()) == [
        "exp-bbbbbbbbbbbb.lock",
        "exp-bbbbbbbbbbbb.sqlite",
        "parallel-aaaaaaaaaaaa.lock",
        "parallel-aaaaaaaaaaaa.sqlite",
        "parallel-aaaaaaaaaaaa.sqlite-wal",
    ]
    assert (to / "sv/plan-ca92eb3d/state.json").exists() and (to / "sv/plan-ca92eb3d/work").is_dir()
    assert not (old / "dbos").exists() and not (old / "sv").exists()
    again = sc.migrate_all(env, cwd=wt)  # 2 回目は移すものが無い
    assert again["status"] == "none" and again["dbos"]["moved"] == again["sv"]["moved"] == []


def test_migrate_keeps_checkout_records_whose_name_is_taken(tmp_path):
    env = {"HOME": str(tmp_path / "h")}
    wt = _repo(tmp_path)
    old = _old_records(tmp_path, env)
    to = (tmp_path / "work/repo/.git/ndf/state").resolve()
    (to / "dbos").mkdir(parents=True)
    (to / "dbos/parallel-aaaaaaaaaaaa.sqlite-shm").write_text("")  # 移し先に同じ鍵の組の 1 つがある（ロックだけなら移す）
    (to / "sv/plan-ca92eb3d").mkdir(parents=True)
    r = sc.migrate_all(env, cwd=wt)
    assert r["status"] == "kept"
    assert [k["path"] for k in r["dbos"]["kept"]] == [str(old / "dbos/parallel-aaaaaaaaaaaa.sqlite")]
    assert [k["path"] for k in r["sv"]["kept"]] == [str(old / "sv/plan-ca92eb3d")]
    assert all(k["reason"] for k in r["dbos"]["kept"] + r["sv"]["kept"])
    assert (old / "dbos/parallel-aaaaaaaaaaaa.sqlite-wal").exists()  # 組ごと残す
    assert (to / "dbos/exp-bbbbbbbbbbbb.sqlite").exists() and (to / "sv/1-design-0e3fcff9").is_dir()


def test_migrate_moves_nothing_while_a_durable_record_is_locked(tmp_path):
    """旧の dbos/ のロックが 1 つでも持たれていれば（動いているプラン）、台帳も含めて何も移さず止まり、持たれている鍵を出す。"""
    import fcntl

    env = {"HOME": str(tmp_path / "h")}
    wt = _repo(tmp_path)
    old = _old_records(tmp_path, env)
    (old / "ask.jsonl").write_text('{"q":1}\n')
    with open(old / "dbos/exp-bbbbbbbbbbbb.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        r = sc.migrate_all(env, cwd=wt)
    assert r["status"] == "busy" and r["held"] == [str(old / "dbos/exp-bbbbbbbbbbbb.lock")]
    assert r["moved"] == r["dbos"]["moved"] == r["sv"]["moved"] == []
    assert (old / "ask.jsonl").exists() and (old / "dbos/parallel-aaaaaaaaaaaa.sqlite").exists() and (old / "sv/plan-ca92eb3d").is_dir()
    assert sc.migrate_all(env, cwd=wt)["status"] == "migrated"  # 止めてから打てば移る


def test_migrate_outside_a_work_tree_moves_only_ledgers(tmp_path):
    env = {"HOME": str(tmp_path / "h")}
    old = _old_records(tmp_path, env)
    (old / "ask.jsonl").write_text('{"q":1}\n')
    (tmp_path / "plain").mkdir()
    r = sc.migrate_all(env, cwd=tmp_path / "plain")
    assert r["status"] == "migrated"
    assert r["checkout"]["to"] is None and "git の作業ツリー" in r["checkout"]["reason"]
    assert r["sv"] == {"moved": [], "kept": []} and r["dbos"] == {"moved": [], "kept": []}
    assert (old / "dbos/exp-bbbbbbbbbbbb.sqlite").exists() and (old / "sv/plan-ca92eb3d").is_dir()
    assert (tmp_path / "h/.claude/ndf/state/ask.jsonl").exists()


def test_cli_migrate_reports_checkout_records(tmp_path):
    env = {"HOME": str(tmp_path / "h"), "PATH": os.environ["PATH"]}
    wt = _repo(tmp_path)
    _old_records(tmp_path, {"HOME": env["HOME"]})
    out = subprocess.run([sys.executable, str(SCRIPTS / "state-home.py"), "migrate"], env=env, cwd=wt, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["metrics"]["dbos_moved"] == 2 and got["metrics"]["sv_moved"] == 2 and got["metrics"]["moved"] == 0
    assert sorted(i["moved"] for i in got["items"] if i["kind"] == "sv") == [
        str((tmp_path / "work/repo/.git/ndf/state/sv") / n) for n in ("1-design-0e3fcff9", "plan-ca92eb3d")
    ]


def test_moved_plan_state_link_is_repointed(tmp_path, monkeypatch):
    """移す前に作った `<プラン>-state`（旧の実体を指すリンク）は、移した後の起動で移し先の実体を指し直す。"""
    sys.path.insert(0, str(SCRIPTS))
    from supervise_lib import paths

    for name in ("NDF_SV_STATE_DIR", "XDG_STATE_HOME", "NDF_STATE_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    wt = _repo(tmp_path)
    monkeypatch.chdir(wt)
    monkeypatch.setattr(paths, "under_temp", lambda p: Path(p).is_relative_to(tmp_path / "tmp"))
    plan = tmp_path / "tmp/plan.json"
    plan.parent.mkdir()
    plan.write_text("{}")
    link = plan.parent / "plan-state"
    real = paths.sv_state_home() / f"plan-{paths.hashlib.sha256(str(plan.resolve()).encode()).hexdigest()[:8]}"
    old_real = sh.legacy_home({"HOME": str(tmp_path / "h")}) / "sv" / real.name
    old_real.mkdir(parents=True)
    (old_real / "state.json").write_text("{}")
    link.symlink_to(old_real, target_is_directory=True)
    sc.migrate_all({"HOME": str(tmp_path / "h")}, cwd=wt)
    assert paths.state_dir_of(str(plan)) == link
    assert link.resolve() == real.resolve() and (link / "state.json").exists()


def test_migrate_moves_plan_states_to_the_checkout_it_runs_in(tmp_path):
    """プランの状態は、`plan.json` のパスによらず打った作業ツリーのチェックアウトへ移す（実行時の `sv_state_home` と同じ規則）。"""
    env = {"HOME": str(tmp_path / "h")}
    wt = _repo(tmp_path)
    _repo(tmp_path / "other")  # 別のリポジトリ（旧の置き場に同居していた）
    to = (tmp_path / "work/repo/.git/ndf/state/sv").resolve()
    old = sh.legacy_home(env) / "sv"
    (old / "6-impl-bbbbbbbb").mkdir(parents=True)
    (old / "6-impl-bbbbbbbb/plan.json").write_text(json.dumps({"作業場所": str(tmp_path / "other/work/repo")}, ensure_ascii=False))
    r = sc.migrate_all(env, cwd=wt)
    assert r["status"] == "migrated" and r["sv"]["moved"] == [str(to / "6-impl-bbbbbbbb")]
    assert not (tmp_path / "other/work/repo/.git/ndf").exists()


def test_migrate_keeps_restricted_mode_of_ledgers(tmp_path):
    """並べ直した台帳は、移行元・移行先の厳しい方の権限を保つ（umask によらない。問いと答えを持つ ask.jsonl は 0600）。"""
    env = {"HOME": str(tmp_path)}
    old, new = sh.legacy_home(env), sh._user_default(env)
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    for root, at in ((old, "2026-11-01T00:00:00Z"), (new, "2026-11-02T00:00:00Z")):
        (root / "ask.jsonl").write_text(_row(at))
        (root / "ask.jsonl").chmod(0o600)
    (old / "metrics").mkdir()
    (old / "metrics/m.jsonl").write_text(_row("2026-11-01T00:00:00Z"))
    (old / "metrics/m.jsonl").chmod(0o640)
    mask = os.umask(0o022)
    try:
        assert sh.migrate(env)["status"] == "migrated"
    finally:
        os.umask(mask)
    assert (new / "ask.jsonl").stat().st_mode & 0o777 == 0o600
    assert (new / "metrics/m.jsonl").stat().st_mode & 0o777 == 0o640  # 移し先が無いときは移行元の権限


def test_migrate_keeps_rows_appended_to_open_ledgers_after_reading(tmp_path, monkeypatch):
    """読んだ後に、名前を外した台帳（移し先・旧）を既に開いていた書き手が足した行を、消さずに新しい台帳へ残す。"""
    env = {"HOME": str(tmp_path)}
    old, new = sh.legacy_home(env), sh._user_default(env)
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "ask.jsonl").write_text(_row("2026-11-01T00:00:00Z", q=1))
    (new / "ask.jsonl").write_text(_row("2026-11-02T00:00:00Z", q=2))
    writer_new = (new / "ask.jsonl").open("a")  # 現行の版の書き手（移し先を開いたまま）
    writer_old = (old / "ask.jsonl").open("a")  # 旧の版の書き手
    real_link = sh.os.link

    def link_then_append(src, dst):
        real_link(src, dst)
        if writer_new.closed:
            return
        for w, q in ((writer_new, 3), (writer_old, 4)):  # 並べ直したものを置いた直後に、開いた fd へ追記する
            w.write(_row("2026-11-07T00:00:00Z", q=q))
            w.close()

    monkeypatch.setattr(sh.os, "link", link_then_append)
    assert sh.migrate(env)["status"] == "migrated"
    assert [json.loads(line)["q"] for line in (new / "ask.jsonl").read_text().splitlines()] == [1, 2, 3]
    assert sorted(p.name for p in new.iterdir()) == [sh.LOCK, "ask.jsonl"]
    assert sh.migrate(env)["status"] == "migrated"  # 旧の版の書き手の行は `.migrated` に残り、次の回に足す
    assert [json.loads(line)["q"] for line in (new / "ask.jsonl").read_text().splitlines()] == [1, 2, 3, 4]
