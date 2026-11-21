"""中継のラッパーのテストの補助: 偽の子・起動の手・偽の時計・設定と 1 回の実行（test_relay_wrapper*.py が共有する）。

子は擬似端末の代わりにパイプを持つ偽物で、時計は呼ぶたびに 1 秒進む偽物である（擬似端末も壁時計も待たない。#2962）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ndf.domains.relay import registry, signals, switch_decide, wrapper

VERSION = "11.0.0-dev.4"
LIMIT_LINE = b"  \xe2\x8e\xbf  You've hit your individual spend limit \xc2\xb7 resets 10:40pm\r\n"


class FakeChild:
    """`outputs` を順に画面へ出し、出し切ったら `exit_code` で終わる（None なら生き続ける）。

    `signal` を渡せば、終わるとき `PtyProcess` と同じく `exitstatus` を None にしてシグナルの番号を `signalstatus` に置く。
    """

    def __init__(self, outputs: list[bytes], exit_code: int | None, signal: int | None = None):
        self.read_fd, self.write_fd = os.pipe()
        self.fd = self.read_fd
        self.outputs = list(outputs)
        self.exit_code = exit_code
        self.signal = signal
        self.alive = True
        self.exitstatus = None
        self.signalstatus = None
        self.written: list[bytes] = []
        self.pid = 4242

    def isalive(self):
        if self.outputs:
            os.write(self.write_fd, self.outputs.pop(0))
        elif self.alive and self.signal is not None:
            self.alive, self.signalstatus = False, self.signal
        elif self.alive and self.exit_code is not None:
            self.alive, self.exitstatus = False, self.exit_code
        return self.alive

    def write(self, data):
        self.written.append(data)

    def terminate(self, force=False):
        self.alive, self.exitstatus = False, -9
        return True

    def close(self):
        os.close(self.read_fd)
        os.close(self.write_fd)


class Spawner:
    """`markers` は起動の順番 → その子のセッションが Stop の hook で書く次のセグメントのブロック。"""

    def __init__(self, children, markers=None):
        self.children = list(children)
        self.markers = dict(markers or {})
        self.calls: list[tuple[list[str], dict]] = []
        self.made: list[FakeChild] = []

    def __call__(self, argv, env, cwd):
        self.calls.append((argv, dict(env)))
        child = self.children.pop(0)
        relay_dir = Path(env["NDF_RELAY_DIR"])
        session_id = argv[2]
        block = self.markers.get(len(self.made))
        if block is not None:
            signals.write_next(relay_dir, session_id, block, True, 0.1)
        started = getattr(child, "started", None)
        if started is not None:
            started(relay_dir, session_id, env["NDF_RELAY_LAUNCH"])
        self.made.append(child)
        return child


class AskingChild(FakeChild):
    """hook の代わりに、`script` の見回りの回（isalive を呼ばれた回数）で質問を書く（値が ID）か消す（値が None）。

    `exit_at` 回目の見回りで終了コード 0 で終わる（None なら生き続ける）。`terminate` を受けた回と、そのとき質問のファイルが
    在ったかを残す。
    """

    def __init__(self, script: dict, exit_at: int | None = None):
        super().__init__([b"working\r\n"], None)
        self.script = dict(script)
        self.exit_at = exit_at
        self.polls = 0
        self.where: tuple[Path, str, str] | None = None
        self.killed: tuple[int, bool] | None = None

    def started(self, directory: Path, session_id: str, launch: str) -> None:
        self.where = (directory, session_id, launch)

    def isalive(self):
        self.polls += 1
        directory, session_id, launch = self.where
        if self.polls in self.script:
            qid = self.script[self.polls]
            if qid is None:
                (directory / signals.QUESTION_FILE).unlink(missing_ok=True)
            else:
                payload = {"tool_use_id": qid, "tool_input": {"questions": [{"question": qid}]}}
                assert signals.write_question(directory / signals.QUESTION_FILE, session_id, payload, 0.1, launch=launch)
        if self.exit_at is not None and self.polls >= self.exit_at and self.alive:
            self.alive, self.exitstatus = False, 0
        return super().isalive()

    def terminate(self, force=False):
        self.killed = (self.polls, signals.question_present(self.where[0]))
        return super().terminate(force)


def clock_ticks():
    now = [0.0]

    def tick():
        now[0] += 1.0
        return now[0]

    return tick


def make_settings(tmp_path: Path, **changes) -> wrapper.Settings:
    values = dict(
        state_dir=tmp_path / "state",
        version=VERSION,
        name="t1",
        cwd=tmp_path,
        # 偽の設定の保存先（メジャーバージョンで止まるときに本物の ~/.claude/ndf の複製を向け直さない）
        environ={"NDF_STATE_HOME": str(tmp_path / "old"), "CLAUDE_CONFIG_DIR": str(tmp_path / "claude")},
        args=("--model", "x"),
        tick=0.0,
        quiet=2.0,
        stall=1000.0,
        # 偽の時計は呼ぶたびに 1 秒しか進まず、どのセグメントも短い。再起動ループの検出を確かめるテストだけが秒を渡す
        spin=switch_decide.Spin(3, 0.0),
        context_limit=800_000,
        usage_threshold=90.0,
        transcript_bytes=4096,
    )
    values.update(changes)
    return wrapper.Settings(**values)


def run_wrapper(tmp_path, children, prepare=None, markers=None, wall=None, **changes):
    s = make_settings(tmp_path, **changes)
    spawn = Spawner(children, markers)
    w = wrapper.Wrapper(s, spawn=spawn, clock=clock_ticks(), wall=wall)
    if prepare:
        prepare(w.dir, s.state_dir)
    try:
        ended = w.run("最初の 1 行")
    finally:
        for c in spawn.made + spawn.children:
            c.close()
    return ended, spawn, w


def log_rows(w) -> list[dict]:
    return [json.loads(line) for line in (w.dir / registry.LOG_FILE).read_text(encoding="utf-8").splitlines()]


def rows_of(w, kind: str) -> list[dict]:
    return [r for r in log_rows(w) if r["kind"] == kind]
