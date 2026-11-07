"""状態の置き場（#2766）: 持ち主ごとの 3 つの根・旧の記録の引き継ぎ・台帳の移行・シェルの同じ順。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

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


def test_legacy_entry_keeps_running_records(tmp_path):
    env = {"HOME": str(tmp_path)}
    new = tmp_path / "repo/.git/ndf/state/dbos/parallel-x.sqlite"
    old = tmp_path / ".local/state/ndf/dbos/parallel-x.sqlite"
    assert sh.legacy_entry(new, "dbos", new.name, env) == new  # 旧にも無ければ新
    old.parent.mkdir(parents=True)
    old.write_text("x")
    assert sh.legacy_entry(new, "dbos", new.name, env) == old  # 旧だけにあれば旧を続ける
    new.parent.mkdir(parents=True)
    new.write_text("y")
    assert sh.legacy_entry(new, "dbos", new.name, env) == new  # 新にあれば新
    assert sh.legacy_entry(tmp_path / "z", "dbos", "parallel-x.sqlite", {**env, "XDG_STATE_HOME": "/x"}) == tmp_path / "z"


def test_durable_record_path_follows_legacy(tmp_path, monkeypatch):
    import durable_keys

    monkeypatch.delenv("NDF_DBOS_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("NDF_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    wt = _repo(tmp_path)
    monkeypatch.chdir(wt)
    key = durable_keys.launch_key("parallel", "p3")
    new_dir = (tmp_path / "work/repo/.git/ndf/state/dbos").resolve()
    assert durable_keys.record_path("parallel", "p3") == new_dir / f"{key}.sqlite"
    old = tmp_path / "h/.local/state/ndf/dbos" / f"{key}.sqlite"
    old.parent.mkdir(parents=True)
    old.write_text("x")
    assert durable_keys.record_path("parallel", "p3") == old


def test_user_home_reads_legacy_ledgers_until_migrate(tmp_path):
    """更新した直後（利用者の根が無く旧に台帳がある）は旧の根を読み書きし、migrate の後は利用者の根へ移る。"""
    env = {"HOME": str(tmp_path)}
    old, new = tmp_path / ".local/state/ndf", tmp_path / ".claude/ndf/state"
    (old / "relay").mkdir(parents=True)
    assert sh.user_home(env) == new  # コンテナの状態だけなら利用者の根
    (old / "stages").mkdir()
    (old / "stages/o__r__1.json").write_text("{}")
    assert sh.user_home(env) == old  # 移す前の通過記録を見失わない
    assert sh.migrate(env)["status"] == "migrated"
    assert sh.user_home(env) == new
    assert (new / "stages/o__r__1.json").exists()


def test_user_home_falls_back_per_kind(tmp_path):
    """`~/.claude` を共有する別のコンテナが利用者の根に別の種類を書いても、この種類の旧の記録は旧の根から読む。"""
    env = {"HOME": str(tmp_path)}
    old, new = tmp_path / ".local/state/ndf", tmp_path / ".claude/ndf/state"
    (old / "stages").mkdir(parents=True)
    (new / "usage").mkdir(parents=True)  # 別のコンテナが書いた
    assert sh.user_home(env, ("stages",)) == old
    assert sh.user_home(env, ("usage",)) == new
    (new / "stages").mkdir()
    assert sh.user_home(env, ("stages",)) == new  # 利用者の根に同じ種類があればそちら
    out = subprocess.run(
        ["bash", "-c", f". '{WF}' && ndf_user_state_home usage && ndf_user_state_home checks && ndf_user_state_home"],
        env={**env, "PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.split() == [str(sh.user_home(env, (k,) if k else sh.USER_KINDS)) for k in ("usage", "checks", "")]


def test_migrate_merges_ledgers_and_leaves_container_state(tmp_path):
    env = {"HOME": str(tmp_path)}
    old, new = sh.legacy_home(env), sh.user_home(env)
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
    assert not (old / "usage").exists() and not (old / "ask.jsonl").exists()
    assert sh.migrate(env)["status"] == "kept"  # 打ち直しても足し直さない
    assert (new / "usage/claude/o__r.jsonl").read_text() == '{"b":2}\n{"a":1}\n'


def test_migrate_takes_ledger_name_off_before_reading(tmp_path, monkeypatch):
    """旧の台帳は名前を外してから読む。読んだ後に旧の版が追記した行は旧の名前で作り直され、次の migrate で移る。"""
    env = {"HOME": str(tmp_path)}
    old, new = sh.legacy_home(env), sh.user_home(env)
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
    assert not old.exists() or not any(old.iterdir())


@pytest.mark.parametrize("appended", [False, True])
def test_migrate_resumes_a_stopped_move(tmp_path, appended):
    """名前を外した後で止まった移行の残り（`.migrating-<pid>`）は、次の migrate が欠かさず二重にせず足す。"""
    env = {"HOME": str(tmp_path)}
    old, new = sh.legacy_home(env), sh.user_home(env)
    (old / "usage/claude").mkdir(parents=True)
    (old / "ask.jsonl.migrating-999999999").write_text('{"q":1}\n')
    (old / "ask.jsonl").write_text('{"q":2}\n')  # 止まった後に旧の版が書いた
    (old / "usage/claude/o__r.jsonl.migrating-999999999").write_text('{"a":1}\n')
    new.mkdir(parents=True)
    if appended:  # 足した後、消す前に止まった
        (new / "ask.jsonl").write_text('{"q":1}\n')
    assert sh.migrate(env)["status"] == "migrated"
    assert (new / "ask.jsonl").read_text() == '{"q":1}\n{"q":2}\n'
    assert (new / "usage/claude/o__r.jsonl").read_text() == '{"a":1}\n'
    assert not old.exists() or not any(old.rglob("*"))


def test_migrate_leaves_a_move_in_progress(tmp_path):
    """名前を外したプロセスが生きている（動いている migrate）残りには触らない。"""
    env = {"HOME": str(tmp_path)}
    old = sh.legacy_home(env)
    old.mkdir(parents=True)
    held = old / f"ask.jsonl.migrating-{os.getppid()}"
    held.write_text('{"q":1}\n')
    assert sh.migrate(env)["status"] == "kept" and held.exists()


def test_migrate_none_with_explicit_root(tmp_path):
    (tmp_path / ".local/state/ndf").mkdir(parents=True)
    (tmp_path / ".local/state/ndf/ask.jsonl").write_text("x\n")
    assert sh.migrate({"HOME": str(tmp_path), "XDG_STATE_HOME": "/x"})["status"] == "none"
    assert sh.migrate({"HOME": str(tmp_path / "empty")})["status"] == "none"


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


@pytest.mark.parametrize("kind", sh.USER_KINDS)
def test_shell_user_root_follows_legacy_ledger(tmp_path, kind):
    """シェルも旧の根の台帳の種類（`USER_KINDS`）を Python と同じに見る。"""
    (tmp_path / ".local/state/ndf" / kind).mkdir(parents=True)
    env = {"HOME": str(tmp_path), "PATH": os.environ["PATH"]}
    out = subprocess.run(["bash", "-c", f". '{WF}' && ndf_user_state_home"], env=env, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == str(sh.user_home(env)) == str(tmp_path / ".local/state/ndf")


def _row(at: str, **kw) -> str:
    return json.dumps({"at": at, **kw}) + "\n"


def test_migrate_orders_rows_by_time(tmp_path):
    """旧と新の行を時刻（`at`）の順に並べて書き直す。時刻の書き方（`Z`・時差・時差なし）が混ざっても順に並び、時刻の無い行は直前の行の隣に残る。"""
    env = {"HOME": str(tmp_path)}
    old, new = sh.legacy_home(env), sh.user_home(env)
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
    old, new = sh.legacy_home(env), sh.user_home(env)
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
    old, new = sh.legacy_home(env), sh.user_home(env)
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
    assert sorted(p.name for p in new.iterdir()) == ["ask.jsonl"]  # 名前を外した台帳も一時ファイルも残さない


@pytest.mark.parametrize("stop", ["before_link", "after_link"])
def test_migrate_resumes_a_stopped_rewrite(tmp_path, monkeypatch, stop):
    """移し先の名前を外した後（置く前・置いた後）で止まっても、次の migrate が欠かさず二重にせず元の名前の台帳へ戻す。"""
    env = {"HOME": str(tmp_path)}
    old, new = sh.legacy_home(env), sh.user_home(env)
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
