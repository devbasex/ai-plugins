"""ラッパーの見回り: セグメントの切り替え・上限でのアカウントの替え・認証の失敗・バージョンの混ざり（#2438・#3337・#3243・3341-2）。

偽の子と偽の時計は `tests/support/relay_children.py` にある（擬似端末も壁時計も待たない。#2962）。
"""

from __future__ import annotations

import fcntl
import io
import json
import os
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pytest
from ndf.domains.relay import account_registry, accounts, install, markers, registry, signals, stop_signal, wrapper
from ndf.domains.relay.account_decide import Usage
from ndf.domains.relay.plugin_update import Target, Updated
from relay_children import (
    LIMIT_LINE,
    VERSION,
    FakeChild,
    Spawner,
    clock_ticks,
    log_rows,
    make_settings,
    run_wrapper,
)


def test_next_block_starts_a_new_session_with_the_block_and_a_relay_mark(tmp_path):
    def prepare(d, state):
        accounts.write_usages(state, [Usage("team", 95.0, False), Usage("main", 10.0, False)])
        accounts.use(state, "team")

    ended, spawn, w = run_wrapper(
        tmp_path, [FakeChild([b"working\r\n"], None), FakeChild([], 0)], prepare, markers={0: "/goal 次のセグメント"}
    )
    assert ended.cause == "" and "書かずに終わった" in ended.reason
    (first, first_env), (second, second_env) = spawn.calls
    assert first[:2] == [wrapper.CLAUDE, "--session-id"] and first[3:] == ["--model", "x", "最初の 1 行"]
    assert second[0:2] == [wrapper.CLAUDE, "--session-id"] and second[-1] == "/goal 次のセグメント" and second[2] != first[2]
    # カットポイントで使用率が閾値を超えていれば、空いたアカウントで次のセグメントを始める（#3336）
    assert (first_env["NDF_CLAUDE_ACCOUNT"], second_env["NDF_CLAUDE_ACCOUNT"]) == ("team", "main")
    assert first_env["NDF_RELAY_DIR"] == str(w.dir) and first_env["CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION"] == "false"
    assert markers.take(tmp_path / "state", second[2]) is True
    assert signals.read_next(w.dir) is None
    assert spawn.made[0].alive is False
    kinds = [row["kind"] for row in log_rows(w)]
    assert kinds.count("section") == 2 and "account" in kinds and kinds[-1] == "end"
    assert registry.read_state(w.dir)["state"] == "stopped"


def test_usage_limit_resumes_on_another_account_even_with_background_waits(tmp_path):
    # 背景の待ちを残したまま上限に当たっても、替える先で --resume して続ける（#3337）
    def prepare(d, state):
        accounts.write_usages(state, [Usage("team", 100.0, True), Usage("main", 5.0, False)])
        accounts.use(state, "team")

    limited = FakeChild([b"  \xe2\x8e\xbf  You've hit your individual spend limit \xc2\xb7 resets 10:40pm\r\n"], None)
    ended, spawn, w = run_wrapper(tmp_path, [limited, FakeChild([], 0)], prepare)
    (first, _), (second, env) = spawn.calls
    assert second[:3] == [wrapper.CLAUDE, "--resume", first[2]] and second[-1] == "利用上限でアカウントを替えた。中断したところから続ける"
    assert env["NDF_CLAUDE_ACCOUNT"] == "main"
    assert ended.cause == ""


def test_usage_limit_resumes_with_registered_bedrock_when_oauth_is_unavailable(tmp_path):
    store = tmp_path / "accounts"
    bedrock = {
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "AWS_PROFILE": "default",
        "AWS_REGION": "ap-northeast-1",
        "ANTHROPIC_MODEL": "jp.anthropic.claude-opus-5-5",
    }
    account_registry.save_metered(store, "bedrock", bedrock, {"profile": "default"}, time.time())

    def prepare(d, state):
        accounts.write_usages(state, [Usage("team", 100.0, True)])
        accounts.use(state, "team")

    environ = {**make_settings(tmp_path).environ, "NDF_ACCOUNTS_DIR": str(store), "AWS_ACCESS_KEY_ID": "test"}
    ended, spawn, _ = run_wrapper(tmp_path, [FakeChild([LIMIT_LINE], None), FakeChild([], 0)], prepare, environ=environ)
    (first, first_env), (second, second_env) = spawn.calls
    assert "CLAUDE_CODE_USE_BEDROCK" not in first_env
    assert second[:3] == [wrapper.CLAUDE, "--resume", first[2]]
    assert second[second.index("--model") + 1] == bedrock["ANTHROPIC_MODEL"]
    assert {key: second_env[key] for key in bedrock} == bedrock
    assert "NDF_CLAUDE_ACCOUNT" not in second_env
    assert "AWS_ACCESS_KEY_ID" not in second_env
    assert ended.cause == ""


def test_auth_failure_marks_the_lane_stalled(tmp_path, monkeypatch):
    # 認証の失敗で /goal が外れたら、状態を stalled にして理由を残す（#3243）
    child = FakeChild([b"Goal cleared after an unrecoverable error (authentication failed)\r\n", b"", b""], 0)
    seen = []
    real = registry.write_state
    monkeypatch.setattr(registry, "write_state", lambda d, data: (seen.append(data["state"]), real(d, data)))
    ended, _, _ = run_wrapper(tmp_path, [child])
    assert "stalled" in seen and ended.cause == ""


def test_a_second_wrapper_with_the_same_name_is_refused(tmp_path):
    d = registry.wrapper_dir(tmp_path / "state", "t1")
    d.mkdir(parents=True)
    fd = os.open(d / registry.LOCK_FILE, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        ended, spawn, _ = run_wrapper(tmp_path, [])
    finally:
        os.close(fd)
    assert ended.cause == "refused" and spawn.calls == []


def test_old_ndf_wrapper_running_stops_the_new_wrapper(tmp_path):
    # 旧いバージョンのラッパーが動いていれば、新しいバージョンのラッパーは動かずに止まる（3341-2）
    old = tmp_path / "old" / "relay" / "20261113T1"
    old.mkdir(parents=True)
    fd = os.open(old / registry.LOCK_FILE, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        ended, spawn, _ = run_wrapper(tmp_path, [])
    finally:
        os.close(fd)
    assert ended.cause == "precondition" and "旧い NDF" in ended.reason and spawn.calls == []


def test_crashed_child_is_resumed_until_the_daily_limit(tmp_path):
    children = [FakeChild([], 1), FakeChild([], 1), FakeChild([], 1)]
    ended, spawn, _ = run_wrapper(tmp_path, children, max_starts=2)
    assert ended.cause == "work_stopped"
    assert [argv[1] for argv, _ in spawn.calls] == ["--session-id", "--resume", "--resume"]


class StopOnSpawn(Spawner):
    """子を起動したすぐ後に停止シグナルファイルを置く（`ndf relay stop --all` が別の端末から置いた形）。"""

    def __call__(self, argv, env, cwd):
        child = super().__call__(argv, env, cwd)
        stop_signal.location_stop(Path(env["NDF_RELAY_DIR"]))
        return child


def test_stop_signal_lets_the_session_finish_and_starts_no_next_section(tmp_path):
    stale = registry.wrapper_dir(tmp_path / "state", "t1")
    stale.mkdir(parents=True)
    stop_signal.location_stop(stale)  # 前の実行の残りは始めたばかりの実行を止めない
    s = make_settings(tmp_path)
    # 出力の後に黙る見回り（b""）を置き、子が生きたまま静かな時間に入って次のセグメントを保留する形にする
    child = FakeChild([b"a\r\n", b"", b"", b"", b"", b"", b""], 0)
    spawn = StopOnSpawn([child], {0: "/goal 次のセグメント"})
    out = io.BytesIO()
    w = wrapper.Wrapper(s, spawn=spawn, clock=clock_ticks(), out=out)
    try:
        ended = w.run("最初の 1 行")
    finally:
        child.close()
    assert (ended.cause, ended.reason) == ("", wrapper.STOP_REASON)
    assert len(spawn.calls) == 1 and child.exitstatus == 0 and child.written == []  # 替えず・入力も送らず・殺さない
    assert out.getvalue().decode().count(wrapper.HALT_LINE) == 1  # 案内の 1 行は保留の間に一度だけ出る
    assert not stop_signal.has_stop(w.dir)


def test_broken_next_mark_stops_with_the_reason(tmp_path):
    def prepare(d, state):
        d.mkdir(parents=True)
        (d / signals.NEXT_FILE).write_text('{"block": "/goal"}', encoding="utf-8")

    ended, spawn, _ = run_wrapper(tmp_path, [FakeChild([], None)], prepare)
    assert ended.cause == "config_broken" and spawn.made[0].alive is False


# 現状固定（I-004）: その日の起動回数は日付が変わると 0 回から数え直し、状態に今日の日付で書く
def test_starts_from_another_day_are_counted_from_zero(tmp_path):
    s = make_settings(tmp_path, max_starts=2)
    spawn = Spawner([FakeChild([], 1), FakeChild([], 1), FakeChild([], 1)])
    w = wrapper.Wrapper(s, spawn=spawn, clock=clock_ticks())
    assert w.starts == {"date": date.today().isoformat(), "count": 0}
    w.starts = {"date": "2000-01-01", "count": 5}
    try:
        ended = w.run("最初の 1 行")
    finally:
        for c in spawn.made + spawn.children:
            c.close()
    assert ended.cause == "work_stopped"
    assert [argv[1] for argv, _ in spawn.calls] == ["--session-id", "--resume", "--resume"]
    assert registry.read_state(w.dir)["starts"] == {"date": date.today().isoformat(), "count": 2}


# 現状固定（I-013）: ラッパーが自分で読む事実（プロセスの番号・壁時計・使用率のキャッシュの時刻）
def test_state_file_records_the_wrapper_and_the_section(tmp_path):
    ended, spawn, w = run_wrapper(tmp_path, [FakeChild([], 0)])
    (argv, env) = spawn.calls[0]
    got = registry.read_state(w.dir)
    assert got["at"] and isinstance(got["at"], str)
    del got["at"]
    assert got == {
        "name": "t1",
        "version": VERSION,
        "pid": os.getpid(),
        "cwd": str(tmp_path),
        "state": "stopped",
        "reason": ended.reason,
        "section": 1,
        "session_id": argv[2],
        "account": None,
        "starts": {"date": date.today().isoformat(), "count": 0},
    }
    assert "NDF_CLAUDE_ACCOUNT" not in env
    rows = log_rows(w)
    assert [r["kind"] for r in rows] == ["section", "finish", "end"]
    assert rows[0] == {"at": rows[0]["at"], "section": 1, "kind": "section", "session_id": argv[2], "account": None, "resume": False}
    assert rows[-1] == {"at": rows[-1]["at"], "section": 1, "kind": "end", "cause": "", "reason": ended.reason}


def _limited_run(tmp_path, mtime_offset):
    def prepare(d, state):
        accounts.write_usages(state, [Usage("team", 50.0, False), Usage("main", 5.0, False)])
        accounts.use(state, "team")
        at = time.time() + mtime_offset
        os.utime(accounts.usage_path(state), (at, at))

    return run_wrapper(tmp_path, [FakeChild([LIMIT_LINE], None), FakeChild([], 0)], prepare)


def test_usage_measured_before_the_limit_line_does_not_free_the_current_account(tmp_path):
    ended, spawn, w = _limited_run(tmp_path, -3600)
    (first, _), (second, env) = spawn.calls
    assert second[:3] == [wrapper.CLAUDE, "--resume", first[2]]
    assert env["NDF_CLAUDE_ACCOUNT"] == "main" and ended.cause == ""
    assert registry.read_state(w.dir)["starts"]["count"] == 1


def test_usage_measured_after_the_limit_line_resumes_on_the_same_account(tmp_path):
    ended, spawn, w = _limited_run(tmp_path, 3600)
    (first, _), (second, env) = spawn.calls
    assert second[:3] == [wrapper.CLAUDE, "--resume", first[2]]
    assert env["NDF_CLAUDE_ACCOUNT"] == "team" and ended.cause == ""
    reasons = [r["reason"] for r in log_rows(w) if r["kind"] == "account"]
    assert reasons == ["team の上限が解けた。同じアカウントで再開する"]


def test_a_mark_left_by_an_earlier_run_is_dropped_not_used(tmp_path):
    # 前の実行のマーカー（セッションが違う）で、新しい子を終わらせて置き換えない
    def prepare(d, state):
        d.mkdir(parents=True)
        signals.write_next(d, "old-session", "/goal 古いセグメント", True, 0.1)

    ended, spawn, w = run_wrapper(tmp_path, [FakeChild([], 0)], prepare)
    assert len(spawn.calls) == 1 and ended.cause == "" and "書かずに終わった" in ended.reason
    assert signals.read_next(w.dir) is None
    assert "stale_next" in [row["kind"] for row in log_rows(w)]


def test_next_mark_switches_once_its_quiet_time_has_passed_even_while_output_continues(tmp_path):
    # /goal の未達の応答が続いて画面が静かにならなくても、マーカーを最初に書いてから静かな時間が過ぎれば切り替える（#2438）
    chatty = FakeChild([b"goal not met\r\n"] * 50, None)
    later = wrapper.WallClock(now=lambda: time.time() + 60.0)
    ended, spawn, _ = run_wrapper(tmp_path, [chatty, FakeChild([], 0)], markers={0: "/goal 次"}, wall=later, quiet=30.0)
    assert len(spawn.calls) == 2 and spawn.calls[1][0][-1] == "/goal 次"
    assert chatty.outputs and ended.cause == ""


def test_switch_stops_the_child_by_force_without_writing_to_its_terminal(tmp_path):
    # 切り替えは強制終了だけで行い、子の端末へ `/exit` などを書かない（#3814 の受け入れ条件 7）
    first = FakeChild([], None)
    ended, spawn, _ = run_wrapper(tmp_path, [first, FakeChild([], 0)], markers={0: "/goal 次"})
    assert len(spawn.calls) == 2 and ended.cause == ""
    assert first.written == [] and first.exitstatus == -9


class EndedPty(FakeChild):
    """終わった擬似端末: 読むと EIO を返し、select には読める状態を返し続ける。"""

    def __init__(self):
        super().__init__([], 1)
        os.write(self.write_fd, b"x")


def test_an_ended_pty_is_not_read_again_while_waiting(tmp_path, monkeypatch):
    # 子が終わった後の待ち（上限のリセットなど）で、終わった擬似端末を読み続けない
    child = EndedPty()
    reads = []
    real_read = os.read

    def read(fd, size):
        if fd == child.fd:
            reads.append(fd)
            raise OSError(5, "EIO")
        return real_read(fd, size)

    monkeypatch.setattr(wrapper.os, "read", read)
    w = wrapper.Wrapper(make_settings(tmp_path), spawn=Spawner([child]), clock=clock_ticks())
    try:
        w._start(wrapper.Launch(1, "s", None, "", resume=False))
        for _ in range(5):
            w._pump(0.0)
    finally:
        child.close()
    assert len(reads) == 1 and w.section.eof is True


def test_first_only_args_and_resume_stay_on_the_first_section(tmp_path):
    # 最初のセグメントだけ --resume <ID> と -n で続け、次のセグメントは新しい ID とすべてのセッションの引数だけで始める
    ended, spawn, w = run_wrapper(
        tmp_path,
        [FakeChild([b"working\r\n"], None), FakeChild([], 0)],
        markers={0: "/goal 次"},
        first_args=("-n", "作業"),
        first_session="11111111-1111-1111-1111-111111111111",
        first_resume=True,
    )
    (first, _), (second, _) = spawn.calls
    assert first == [wrapper.CLAUDE, "--resume", "11111111-1111-1111-1111-111111111111", "--model", "x", "-n", "作業", "最初の 1 行"]
    assert second[1] == "--session-id" and second[2] != first[2] and second[3:] == ["--model", "x", "/goal 次"]


# --- プラグインの更新と起動先の確認（#3812） ---


class Hands:
    """更新の手・確認の手・起動を、呼ばれた順に `order` へ残す偽物。"""

    def __init__(self, updated=None, targets=None):
        self.order: list[str] = []
        self.updated = list(updated or [])
        self.targets = list(targets or [])

    def update(self):
        self.order.append("update")
        return self.updated.pop(0)

    def confirm(self):
        self.order.append("confirm")
        return self.targets.pop(0)

    def spawner(self, children, markers=None):
        hands = self

        class Ordered(Spawner):
            def __call__(self, argv, env, cwd):
                hands.order.append("spawn")
                return super().__call__(argv, env, cwd)

        return Ordered(children, markers)


def _same(result="up_to_date", reason="新しいバージョンは無い"):
    return Updated(result, VERSION, VERSION, reason, Target(VERSION, Path("/cache/ndf") / VERSION))


def _run_hands(tmp_path, hands, children, markers=None, **changes):
    s = make_settings(tmp_path, update=hands.update, confirm=hands.confirm, **changes)
    spawn = hands.spawner(children, markers)
    out = io.BytesIO()
    w = wrapper.Wrapper(s, spawn=spawn, clock=clock_ticks(), out=out)
    try:
        ended = w.run("最初の 1 行")
    finally:
        for c in spawn.made + spawn.children:
            c.close()
    return ended, spawn, w, out.getvalue().decode()


def _updates(w) -> list[dict]:
    return [r for r in log_rows(w) if r["kind"] == "plugin_update"]


def test_i1_update_runs_before_the_first_section_and_after_each_switch(tmp_path):
    # 受け入れ条件 1・2: 新しいセッションで始めるセグメントは、更新の後にだけ起動する
    hands = Hands(updated=[_same(), _same("updated", "11.0.0 から 11.0.0-dev.4 へ上げた")])
    ended, _, w, _ = _run_hands(tmp_path, hands, [FakeChild([b"working\r\n"], None), FakeChild([], 0)], markers={0: "/goal 次"})
    assert hands.order == ["update", "spawn", "update", "spawn"]
    assert ended.cause == ""
    rows = _updates(w)
    assert [r["result"] for r in rows] == ["up_to_date", "updated"]
    assert set(rows[0]) == {"at", "section", "kind", "result", "from", "to", "launched", "wrapper", "outside", "reason", "seconds"}
    assert rows[0]["section"] is None and rows[0]["launched"] == VERSION and isinstance(rows[0]["seconds"], float)
    assert "/cache" not in json.dumps(rows, ensure_ascii=False)


def test_i3_hands_that_continue_the_same_conversation_only_confirm(tmp_path):
    # 受け入れ条件 4: 子の終了から起動し直す手は更新せず、起動先だけを確かめる
    hands = Hands(updated=[_same()], targets=[Target(VERSION, None)])
    _, _, w, _ = _run_hands(tmp_path, hands, [FakeChild([], 1), FakeChild([], 0)])
    assert hands.order == ["update", "spawn", "confirm", "spawn"]
    assert [(r["result"], r["reason"], r["launched"]) for r in _updates(w)][1] == ("skipped", wrapper.NO_UPDATE_REASON, VERSION)


def test_i3_account_switch_on_the_limit_only_confirms(tmp_path):
    def prepare(d, state):
        accounts.write_usages(state, [Usage("team", 100.0, True), Usage("main", 5.0, False)])
        accounts.use(state, "team")

    hands = Hands(updated=[_same()], targets=[Target(VERSION, None)])
    s = make_settings(tmp_path, update=hands.update, confirm=hands.confirm)
    spawn = hands.spawner([FakeChild([LIMIT_LINE], None), FakeChild([], 0)])
    w = wrapper.Wrapper(s, spawn=spawn, clock=clock_ticks())
    prepare(w.dir, s.state_dir)
    try:
        w.run("最初の 1 行")
    finally:
        for c in spawn.made + spawn.children:
            c.close()
    assert hands.order == ["update", "spawn", "confirm", "spawn"] and spawn.calls[1][0][1] == "--resume"


def test_i3_a_first_section_resumed_by_the_user_only_confirms(tmp_path):
    hands = Hands(targets=[Target(VERSION, None)])
    _run_hands(tmp_path, hands, [FakeChild([], 0)], first_session="11111111-1111-1111-1111-111111111111", first_resume=True)
    assert hands.order == ["confirm", "spawn"]


def test_without_hands_no_plugin_update_row_is_written(tmp_path):
    _, _, w = run_wrapper(tmp_path, [FakeChild([], 0)])
    assert _updates(w) == []


def test_i2_failed_update_still_starts_the_next_section_with_one_line(tmp_path):
    # 受け入れ条件 3: 失敗しても止まらず、記録と端末の 1 行を出す
    hands = Hands(updated=[Updated("failed", None, None, "マーケットプレイスを更新できない: network")])
    ended, spawn, w, out = _run_hands(tmp_path, hands, [FakeChild([], 0)])
    assert hands.order == ["update", "spawn"] and ended.cause == ""
    assert [(r["result"], r["launched"]) for r in _updates(w)] == [("failed", None)]
    line = wrapper.UPDATE_FAILED_LINE.format(reason="マーケットプレイスを更新できない: network", launched="不明なバージョン")
    assert out.count("ndf-relay:") == 1 and line in out


def test_i7_another_major_stops_before_the_next_child_even_when_the_update_failed(tmp_path):
    # 受け入れ条件 11・I8: 起動先は更新の to ではなく確かめた target で判定する
    target = Target("12.0.0", tmp_path / "cache" / "12.0.0")
    hands = Hands(updated=[Updated("failed", None, None, "NDF を更新できない: 終了コード 0・JSON の行が無い", target)])
    ended, spawn, w, out = _run_hands(tmp_path, hands, [FakeChild([], 0)])
    assert ended.cause == "precondition" and spawn.calls == []
    assert wrapper.MAJOR_CHANGED_INSTALL_LINE.format(own=VERSION, launched="12.0.0", root=target.root) in out
    assert registry.read_state(w.dir)["state"] == "stopped"
    # 起動しないので「次のセグメントを起動する」の 1 行は出さない（更新の失敗は記録に残す）
    assert out.count("ndf-relay:") == 1 and [r["result"] for r in _updates(w)] == ["failed"]


# --- 子の PATH を起動先のバージョンにそろえる（#3909） ---

LAUNCHED = "11.9.0"  # 起動先（ラッパーと同じメジャーバージョン）


def _two_installs(tmp_path):
    """ラッパーのバージョン（N）と起動先（M）の根を同じキャッシュの親の下に作り、(N の bin を含む環境, M の根) を返す。"""
    parent = tmp_path / "cache" / "ai-plugins" / "ndf"
    for version in (VERSION, LAUNCHED):
        (parent / version / "bin").mkdir(parents=True)
        (parent / version / "bin" / "ndf").write_text("#!/bin/sh\n", encoding="utf-8")
        (parent / version / "bin" / "ndf").chmod(0o755)
    environ = {
        **make_settings(tmp_path).environ,
        "PATH": os.pathsep.join([str(parent / VERSION / "bin"), "/usr/bin"]),
    }
    return environ, parent / LAUNCHED


def test_the_child_path_has_no_bin_of_another_version_when_the_target_is_known(tmp_path):
    environ, root = _two_installs(tmp_path)
    hands = Hands(updated=[Updated("updated", VERSION, LAUNCHED, "上げた", Target(LAUNCHED, root))])
    _, spawn, _, _ = _run_hands(tmp_path, hands, [FakeChild([], 0)], environ=environ)
    assert spawn.calls[0][1]["PATH"] == os.pathsep.join([str(root / "bin"), "/usr/bin"])


def test_a_hand_that_only_confirms_also_keeps_the_target_for_the_child_path(tmp_path):
    # --resume で続ける手（確認の手）が読んだ起動先でも、子の PATH をそろえる
    environ, root = _two_installs(tmp_path)
    hands = Hands(updated=[Updated("failed", None, None, "network")], targets=[Target(LAUNCHED, root)])
    _, spawn, _, _ = _run_hands(tmp_path, hands, [FakeChild([], 1), FakeChild([], 0)], environ=environ)
    assert hands.order == ["update", "spawn", "confirm", "spawn"]
    assert spawn.calls[0][1]["PATH"] == environ["PATH"]
    assert spawn.calls[1][1]["PATH"] == os.pathsep.join([str(root / "bin"), "/usr/bin"])


def test_the_child_path_is_unchanged_when_the_target_is_unknown(tmp_path):
    environ, _ = _two_installs(tmp_path)
    hands = Hands(updated=[Updated("failed", None, None, "network")])
    _, spawn, _, _ = _run_hands(tmp_path, hands, [FakeChild([], 0)], environ=environ)
    assert spawn.calls[0][1]["PATH"] == environ["PATH"]


# --- 記録にラッパーのバージョンと外で上がったかを足す（#3909） ---

OUTSIDE = "11.0.1"  # 外で上がった先（既知は VERSION）


def _logged(tmp_path, *results):
    """`plugin_update` の行を順に書き、(行の `outside` の並び, ラッパー) を返す。"""
    w = wrapper.Wrapper(make_settings(tmp_path), spawn=Spawner([]), clock=clock_ticks())
    for got in results:
        w._log_update(got, w.clock())
    return [r["outside"] for r in _updates(w)], w


def _row(result, before, after, launched):
    return Updated(result, before, after, "理由", Target(launched, None))


def test_every_plugin_update_row_has_the_wrapper_version_and_outside(tmp_path):
    _, w = _logged(
        tmp_path,
        _row("updated", VERSION, OUTSIDE, OUTSIDE),
        _row("up_to_date", OUTSIDE, OUTSIDE, OUTSIDE),
        _row("skipped", None, None, OUTSIDE),
        _row("failed", None, None, None),
    )
    rows = _updates(w)
    assert [r["result"] for r in rows] == ["updated", "up_to_date", "skipped", "failed"]
    assert all(r["wrapper"] == VERSION and "outside" in r for r in rows)


def test_the_first_row_after_an_outside_update_is_outside(tmp_path):
    outside, w = _logged(tmp_path, _row("up_to_date", OUTSIDE, OUTSIDE, OUTSIDE))
    assert outside == [True] and w.known_version == OUTSIDE


def test_the_row_after_the_wrappers_own_update_is_not_outside(tmp_path):
    outside, _ = _logged(tmp_path, _row("updated", VERSION, OUTSIDE, OUTSIDE), _row("up_to_date", OUTSIDE, OUTSIDE, OUTSIDE))
    assert outside == [False, False]


def test_a_confirm_row_compares_the_launched_version(tmp_path):
    outside, _ = _logged(tmp_path, _row("skipped", None, None, OUTSIDE), _row("skipped", None, None, OUTSIDE))
    assert outside == [True, False]


def test_an_update_between_the_command_and_the_confirm_is_outside(tmp_path):
    # 更新のコマンドが up_to_date（N→N）を返した直後に外で M へ上がり、確認が M を返した
    outside, w = _logged(tmp_path, _row("up_to_date", VERSION, VERSION, OUTSIDE), _row("up_to_date", OUTSIDE, OUTSIDE, OUTSIDE))
    assert outside == [True, False] and w.known_version == OUTSIDE


def test_a_failed_row_whose_launched_version_did_not_change_is_not_outside(tmp_path):
    outside, _ = _logged(tmp_path, _row("failed", VERSION, OUTSIDE, VERSION))
    assert outside == [False]


def test_a_row_without_from_or_launched_is_unknown_and_keeps_the_known_version(tmp_path):
    outside, w = _logged(tmp_path, _row("failed", None, None, None))
    assert outside == [None] and w.known_version == VERSION


def test_a_failed_row_whose_launched_version_changed_is_unknown_and_moves_the_known_version(tmp_path):
    # 更新のコマンドが成功していないので、N から M への差を自分が入れたか外で入ったかを決められない
    outside, w = _logged(tmp_path, _row("failed", VERSION, OUTSIDE, OUTSIDE), _row("up_to_date", OUTSIDE, OUTSIDE, OUTSIDE))
    assert outside == [None, False] and w.known_version == OUTSIDE


def test_the_wrappers_own_update_is_not_outside_when_only_the_confirm_failed(tmp_path):
    # 更新のコマンドが N から M へ上げ、直後の確認だけが失敗した（launched が null）。次の行の from=M は自分が入れたバージョン
    outside, w = _logged(tmp_path, _row("updated", VERSION, OUTSIDE, None), _row("up_to_date", OUTSIDE, OUTSIDE, OUTSIDE))
    assert outside == [False, False] and w.known_version == OUTSIDE


class StopWhileWaiting(Hands):
    """更新・確認の手を待つ間に `ndf relay stop --all` が停止シグナルファイルを置く形。`at` 回目の手で置く。"""

    def __init__(self, relay_dir, at, **kw):
        super().__init__(**kw)
        self.relay_dir, self.at, self.calls = relay_dir, at, 0

    def _maybe_stop(self):
        self.calls += 1
        if self.calls == self.at:
            stop_signal.location_stop(self.relay_dir)

    def update(self):
        self._maybe_stop()
        return super().update()

    def confirm(self):
        self._maybe_stop()
        return super().confirm()


@pytest.mark.parametrize(
    ("at", "children", "markers", "order"),
    [
        (1, [([], 0)], None, ["update"]),  # 最初のセグメントの前
        (2, [([b"working\r\n"], None), ([], 0)], {0: "/goal 次"}, ["update", "spawn", "update"]),  # 切り替え
        (2, [([], 1), ([], 0)], None, ["update", "spawn", "confirm"]),  # 起動し直し
    ],
)
def test_stop_signal_written_while_updating_starts_no_next_child(tmp_path, at, children, markers, order):
    hands = StopWhileWaiting(
        registry.wrapper_dir(tmp_path / "state", "t1"), at, updated=[_same(), _same()], targets=[Target(VERSION, None)]
    )
    ended, _, w, _ = _run_hands(tmp_path, hands, [FakeChild(*c) for c in children], markers=markers)
    assert hands.order == order and (ended.cause, ended.reason) == ("", wrapper.STOP_REASON)
    assert not stop_signal.has_stop(w.dir) and registry.read_state(w.dir)["state"] == "stopped"


def test_failed_update_line_is_not_shown_when_a_stop_signal_holds_the_next_child(tmp_path):
    hands = StopWhileWaiting(registry.wrapper_dir(tmp_path / "state", "t1"), 1, updated=[Updated("failed", None, None, "network")])
    ended, _, w, out = _run_hands(tmp_path, hands, [FakeChild([], 0)])
    assert ended.reason == wrapper.STOP_REASON and "ndf-relay:" not in out and [r["result"] for r in _updates(w)] == ["failed"]


def test_i7_another_major_stops_a_hand_that_does_not_update(tmp_path):
    hands = Hands(updated=[_same()], targets=[Target("12.0.0", tmp_path / "cache" / "12.0.0")])
    ended, spawn, _, _ = _run_hands(tmp_path, hands, [FakeChild([], 1), FakeChild([], 0)])
    assert ended.cause == "precondition" and hands.order == ["update", "spawn", "confirm"] and len(spawn.calls) == 1


def test_i7_same_major_or_unknown_target_starts_the_next_child(tmp_path):
    hands = Hands(updated=[_same()], targets=[Target("11.9.0", None), Target(None, None)])
    ended, spawn, w, _ = _run_hands(tmp_path, hands, [FakeChild([], 1), FakeChild([], 1), FakeChild([], 0)])
    assert len(spawn.calls) == 3 and ended.cause == ""
    assert [r["launched"] for r in _updates(w)] == [VERSION, "11.9.0", None]


def test_i7_stop_points_the_relay_copy_at_the_target_and_the_copy_calls_it(tmp_path):
    # 停止から打ち直しまで: relay.root が起動先を指し、複製は起動先の bin/ndf を呼ぶ
    def plugin(version):
        root = tmp_path / "cache" / version
        ndf = root / "bin" / "ndf"
        ndf.parent.mkdir(parents=True)
        ndf.write_text(f'#!/bin/sh\necho "{version} $*"\n', encoding="utf-8")
        ndf.chmod(0o755)
        return root

    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude"), "XDG_BIN_HOME": str(tmp_path / "bin"), "PATH": str(tmp_path / "bin")}
    got = install.install(env, plugin(VERSION), 9, replace_foreign=False, version=VERSION)
    new = plugin("12.0.0")
    hands = Hands(updated=[Updated("updated", VERSION, "12.0.0", "上げた", Target("12.0.0", new))])
    ended, spawn, _, out = _run_hands(tmp_path, hands, [FakeChild([], 0)], environ={**env, "NDF_STATE_HOME": str(tmp_path / "old")})
    assert ended.cause == "precondition" and spawn.calls == []
    assert wrapper.MAJOR_CHANGED_LINE.format(own=VERSION, launched="12.0.0") in out
    assert (got.copy.path.parent / "relay.version").read_text(encoding="utf-8") == "12.0.0\n"
    launched = subprocess.run([sys.executable, "-I", str(got.copy.path), "ls"], capture_output=True, text=True, timeout=30, check=False)
    assert launched.stdout.strip() == "12.0.0 relay ls"


# --- 子の claude を起動できないとき（#3842） ---

NOT_FOUND = "The command was not found or was not executable: claude."


class FailingSpawner(Spawner):
    """`fail_at` 回目（0 から数える）の起動で `error` を上げる偽物（PATH に claude が無い形）。"""

    def __init__(self, children, error, fail_at=0, markers=None):
        super().__init__(children, markers)
        self.error, self.fail_at = error, fail_at

    def __call__(self, argv, env, cwd):
        if len(self.calls) == self.fail_at:
            self.calls.append((argv, dict(env)))
            raise self.error
        return super().__call__(argv, env, cwd)


def _assert_stopped_with(w, ended, reason, capsys):
    """停止理由・log.jsonl の最後の行・wrapper.json・端末の 1 行が同じ文で、終了コードが 3。"""
    from ndf.contracts import causes
    from ndf.domains.relay import cli as relay_cli

    assert ended == wrapper.Ended("precondition", reason)
    last = log_rows(w)[-1]
    assert (last["kind"], last["cause"], last["reason"]) == ("end", "precondition", reason)
    state = registry.read_state(w.dir)
    assert (state["state"], state["reason"]) == ("stopped", reason)
    capsys.readouterr()
    assert relay_cli._ended(ended) == causes.exit_code("precondition") == 3
    assert capsys.readouterr().err == f"ndf-relay: {reason}\n"


def test_a_child_that_cannot_be_launched_stops_with_one_line_and_exit_code_3(tmp_path, capsys):
    # 受け入れ条件 1・2・5: 偽の起動が FileNotFoundError を出すと precondition で終わり、3 か所の文が同じ
    ended, spawn, w = _run_failing(tmp_path, [], FileNotFoundError(NOT_FOUND))
    _assert_stopped_with(w, ended, wrapper.LAUNCH_FAILED.format(why=NOT_FOUND), capsys)
    assert len(spawn.calls) == 1
    assert markers.take(tmp_path / "state", spawn.calls[0][0][2]) is False  # 置いたマーカーは消してある


def test_each_launch_gets_its_own_launch_id_recorded_as_the_current_launch(tmp_path):
    # 受け入れ条件 6・10（I5・I7 の前提）: 起動ごとに違う ID を NDF_RELAY_LAUNCH で渡し、spawn の前に今の起動として記録する
    seen = []

    class Recording(Spawner):
        def __call__(self, argv, env, cwd):
            seen.append(signals.current_launch(Path(env["NDF_RELAY_DIR"])))
            return super().__call__(argv, env, cwd)

    s = make_settings(tmp_path)
    spawn = Recording([FakeChild([b"working\r\n"], None), FakeChild([], 0)], {0: "/goal 次"})
    w = wrapper.Wrapper(s, spawn=spawn, clock=clock_ticks())
    try:
        w.run("最初の 1 行")
    finally:
        for c in spawn.made:
            c.close()
    launches = [env["NDF_RELAY_LAUNCH"] for _, env in spawn.calls]
    assert len(set(launches)) == 2 and seen == launches
    assert w.section.launch == launches[1] and signals.current_launch(w.dir) == launches[1]


def test_a_launch_that_fails_clears_the_current_launch(tmp_path, capsys):
    # 受け入れ条件 10（I7 の前提）: 起動できなければ今の起動の記録を外す（残った ID の hook に書かせない）
    ended, spawn, w = _run_failing(tmp_path, [], FileNotFoundError(NOT_FOUND))
    assert ended.cause == "precondition" and spawn.calls[0][1]["NDF_RELAY_LAUNCH"]
    assert signals.current_launch(w.dir) is None


def test_a_failed_launch_of_the_next_section_leaves_no_previous_child(tmp_path, capsys):
    # 受け入れ条件 4・5: 次のセグメントの起動に失敗しても、前の子は止めてあり、起動し直さない
    first = FakeChild([b"working\r\n"], None)
    ended, spawn, w = _run_failing(tmp_path, [first], FileNotFoundError(NOT_FOUND), fail_at=1, markers={0: "/goal 次"})
    _assert_stopped_with(w, ended, wrapper.LAUNCH_FAILED.format(why=NOT_FOUND), capsys)
    assert first.alive is False and len(spawn.calls) == 2


def _run_failing(tmp_path, children, error, fail_at=0, markers=None, **changes):
    s = make_settings(tmp_path, **changes)
    spawn = FailingSpawner(children, error, fail_at, markers)
    w = wrapper.Wrapper(s, spawn=spawn, clock=clock_ticks())
    try:
        ended = w.run("最初の 1 行")
    finally:
        for c in spawn.made + spawn.children:
            c.close()
    return ended, spawn, w


def _limit_usages(d, state):
    accounts.write_usages(state, [Usage("team", 100.0, True), Usage("main", 5.0, False)])
    accounts.use(state, "team")


@pytest.mark.parametrize(
    ("children", "markers", "prepare"),
    [
        ([([b"working\r\n"], None)], {0: "/goal 次"}, None),  # switch
        ([([LIMIT_LINE], None)], None, _limit_usages),  # account
        ([([], 1)], None, None),  # restart
    ],
    ids=["switch", "account", "restart"],
)
def test_a_vanished_work_dir_stops_the_next_launch_before_the_update_and_the_spawn(tmp_path, capsys, children, markers, prepare):
    # 受け入れ条件 3・5: 最初の子を起動した後に作業ディレクトリが消えると、次の起動の手はどれも更新も spawn も呼ばない
    work = tmp_path / "work"
    work.mkdir()
    hands = Hands(updated=[_same(), _same()], targets=[Target(VERSION, None)])

    class Vanish(Spawner):
        def __call__(self, argv, env, cwd):
            hands.order.append("spawn")
            child = super().__call__(argv, env, cwd)
            work.rmdir()
            return child

    s = make_settings(tmp_path, cwd=work, update=hands.update, confirm=hands.confirm)
    spawn = Vanish([FakeChild(*c) for c in children], markers)
    w = wrapper.Wrapper(s, spawn=spawn, clock=clock_ticks())
    if prepare:
        prepare(w.dir, s.state_dir)
    try:
        ended = w.run("最初の 1 行")
    finally:
        for c in spawn.made + spawn.children:
            c.close()
    _assert_stopped_with(w, ended, wrapper.LAUNCH_FAILED.format(why=wrapper.NO_CWD.format(cwd=work)), capsys)
    assert hands.order == ["update", "spawn"] and spawn.made[0].alive is False


def test_a_missing_work_dir_starts_no_first_section(tmp_path, capsys):
    # 受け入れ条件 3・5: 最初のセグメントも、作業ディレクトリが無ければ更新も spawn も呼ばない
    gone = tmp_path / "gone"
    hands = Hands(updated=[_same()])
    ended, spawn, w, _ = _run_hands(tmp_path, hands, [FakeChild([], 0)], cwd=gone)
    _assert_stopped_with(w, ended, wrapper.LAUNCH_FAILED.format(why=wrapper.NO_CWD.format(cwd=gone)), capsys)
    assert hands.order == [] and spawn.calls == []


# --- 本物の擬似端末で作業ディレクトリへ入る（#3842） ---


def test_the_real_pty_child_runs_in_the_work_dir(tmp_path):
    # 受け入れ条件 7: 子は作業ディレクトリへ入ってから起動する（preexec_fn で入る形でも今と同じ場所）
    work = (tmp_path / "work").resolve()
    work.mkdir()
    child = wrapper.spawn_pty(["pwd"], {"PATH": os.environ["PATH"]}, work)
    out = b""
    try:
        while True:
            out += child.read(1024)
    except EOFError:
        pass
    finally:
        child.wait()
    assert out.decode().strip() == str(work)


def _run_real_pty(tmp_path, monkeypatch, work, before=None):
    """本物の `spawn_pty` で回す。PATH には何もしない偽の claude を置く（作業ディレクトリへ入れなければ実行されない）。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / wrapper.CLAUDE).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_dir / wrapper.CLAUDE).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    calls = []

    def spawn(argv, env, cwd):
        calls.append(argv)
        if before:
            before()
        return wrapper.spawn_pty(argv, env, cwd)

    w = wrapper.Wrapper(make_settings(tmp_path, cwd=work), spawn=spawn, clock=clock_ticks())
    return w.run("最初の 1 行"), calls, w


@pytest.mark.skipif(os.geteuid() == 0, reason="root は入る権限の無いディレクトリへも入れる")
def test_a_work_dir_without_permission_stops_the_real_pty_launch(tmp_path, monkeypatch, capsys):
    # 受け入れ条件 1・2・3: is_dir() は真でも入れないディレクトリで、子の側の PermissionError を停止理由にする
    work = tmp_path / "locked"
    work.mkdir(mode=0o600)
    try:
        ended, calls, w = _run_real_pty(tmp_path, monkeypatch, work)
    finally:
        work.chmod(0o700)
    reason = wrapper.LAUNCH_FAILED.format(why=wrapper.NO_ENTER.format(cwd=work, why=os.strerror(13)))
    _assert_stopped_with(w, ended, reason, capsys)
    assert len(calls) == 1


def test_a_work_dir_removed_after_the_check_stops_the_real_pty_launch(tmp_path, monkeypatch, capsys):
    # 受け入れ条件 1・2・3: 確かめの後・spawn_pty の前に消えたディレクトリで、子の側の FileNotFoundError を停止理由にする
    work = tmp_path / "work"
    work.mkdir()
    ended, calls, w = _run_real_pty(tmp_path, monkeypatch, work, before=work.rmdir)
    reason = wrapper.LAUNCH_FAILED.format(why=wrapper.NO_ENTER.format(cwd=work, why=os.strerror(2)))
    _assert_stopped_with(w, ended, reason, capsys)
    assert len(calls) == 1
