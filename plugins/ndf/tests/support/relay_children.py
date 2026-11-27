"""中継のラッパーのテストの補助: 偽の子・起動の手・偽の時計・設定と 1 回の実行（test_relay_wrapper*.py が共有する）。

子は擬似端末の代わりにパイプを持つ偽物で、時計は呼ぶたびに 1 秒進む偽物である（擬似端末も壁時計も待たない。#2962）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ndf.domains.relay import account_decide, accounts, registry, signals, switch_decide, wrapper
from ndf.domains.relay import account_registry as reg

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
        self.gate: int | None = None  # 関所で起動したとき、開ける1 バイトの読み口の写し（子が持つ読み口の代わり）

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
        if self.gate is not None:
            os.close(self.gate)


def no_work(child: FakeChild) -> FakeChild:
    """起動されたとき、子の SessionStart の hook の代わりに作業 0 件の記録を置く子（記録の無い子は引継ぎと見回りの替えを待つ。#4049）。"""
    before = getattr(child, "started", None)

    def started(directory, session_id, launch):
        assert signals.write_busy(directory, launch, session_id, 0, "session")
        if before is not None:
            before(directory, session_id, launch)

    child.started = started
    return child


class Spawner:
    """`markers` は起動の順番 → その子のセッションが Stop の hook で書く次のセグメントのブロック。

    関所（`account_dir.gated`）で包んだ起動引数は、包みを外した claude の起動引数で `calls` に残し、`gated` に起動の順番を残す。
    関所の子の pid はテストのプロセスの pid にする（使用の届けの `attach` が開始時刻を読める）。
    """

    def __init__(self, children, markers=None):
        self.children = list(children)
        self.markers = dict(markers or {})
        self.calls: list[tuple[list[str], dict]] = []
        self.made: list[FakeChild] = []
        self.gated: list[int] = []

    def __call__(self, argv, env, cwd, pass_fds=()):
        child = self.children.pop(0)
        if pass_fds:
            child.gate, child.pid = os.dup(int(argv[4])), os.getpid()
            argv = argv[6:]
            self.gated.append(len(self.made))
        self.calls.append((argv, dict(env)))
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
        guards=account_decide.Guards(85.0, 90.0),
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


class Patrol:
    """見回りを 1 回ずつ打つラッパー。時計は `now` を進めるまで止まり、子の文脈の量は `tokens` に置く（fixture `patrol` が作る）。"""

    def __init__(self, tmp_path: Path, children: int = 3, **changes):
        self.now, self.tokens = 0.0, 0
        self.spawn = Spawner([FakeChild([], None) for _ in range(children)])
        self.w = wrapper.Wrapper(make_settings(tmp_path, **changes), spawn=self.spawn, clock=lambda: self.now)
        self.w.dir.mkdir(parents=True)
        assert self.w._first_start("最初の 1 行") is None

    @property
    def sec(self):
        return self.w.section

    def look(self, between=None) -> switch_decide.Step:
        """見回り 1 回。`between` は手を決めてから打つまでの間に呼ぶ。"""
        self.w._pump(0.0)
        step = switch_decide.decide(self.w._state(False))
        self.w._note(step)
        if between is not None:
            between()
        assert self.w._act(step) is None
        return step

    def later(self, seconds: float, between=None) -> switch_decide.Step:
        self.now += seconds
        return self.look(between)

    def says(self, text: bytes = b"x") -> None:
        """子が画面へ書く。"""
        os.write(self.sec.child.write_fd, text)

    def busy(self, count: int, source: str = "stop", monitors: int = 0) -> None:
        """子の hook の代わりに、今の起動の記録を書く。"""
        assert signals.write_busy(self.w.dir, self.sec.launch, self.sec.session_id, count, source, monitors)

    def state(self) -> tuple[str, str]:
        """状態のファイルの状態と理由。"""
        data = registry.read_state(self.w.dir)
        return data["state"], data["reason"]


def log_rows(w) -> list[dict]:
    return [json.loads(line) for line in (w.dir / registry.LOG_FILE).read_text(encoding="utf-8").splitlines()]


def rows_of(w, kind: str) -> list[dict]:
    return [r for r in log_rows(w) if r["kind"] == kind]


def _json(path: Path, data) -> Path:
    """`data` を JSON で `path` へ書き（親のディレクトリも作る）、`path` を返す。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def register(tmp_path: Path, *names: str, generation: str = "g1") -> Path:
    """`make_settings` の親の環境の共有の設定ディレクトリ（`<tmp>/claude`）に、起動の前の確かめに通るアカウントを登録する。

    値はすべて偽の値。登録簿のパスを返す。
    """
    shared = tmp_path / "claude"
    _json(shared / ".claude.json", {"projects": {}, "mcpServers": {}})
    store = reg.store_dir({"CLAUDE_CONFIG_DIR": str(shared)})
    for n in names:
        acc = store / n
        _json(acc / reg.ACCOUNT_FILE, {"name": n, "email": f"{n}@example.com", "needs_relogin": False, "generation": generation})
        _json(acc / reg.CRED_FILE, {"claudeAiOauth": {"accessToken": f"fake-access-{n}", "refreshToken": f"fake-refresh-{n}"}})
        _json(acc / reg.CONFIG_FILE, {"oauthAccount": {"emailAddress": f"{n}@example.com"}})
    return store


def put_usage(tmp_path: Path, name: str, five: float, seven: float | None = None, generation: str = "g1") -> None:
    """`name` の取得の記録（`<登録簿>/.usage-fetch/<名前>/<世代>.json`）を、今取れた 1 時間後に解ける窓で書く。"""
    store = reg.store_dir({"CLAUDE_CONFIG_DIR": str(tmp_path / "claude")})
    put_usages(store, [account_decide.Usage(name, five, five, seven, None, False)], generation=generation)


def put_usages(store: Path, rows: list[account_decide.Usage], fetched_at: float | None = None, generation: str = "g1") -> None:
    """成功した取得の記録の形で `rows` を登録簿 `store` の下へ書く。上限の行は 5 時間の窓を 100% にする。

    `fetched_at`（無ければ今）を取得の成功の時刻と最後に試した時刻（`at`）にする。
    """
    now = time.time()
    at = now if fetched_at is None else fetched_at
    for u in rows:
        windows = {}
        if u.five is not None or u.limited:
            windows["five_hour"] = {"percent": accounts.FULL if u.limited else float(u.five), "resets_at": now + 3600}
        if u.seven is not None:
            windows["seven_day"] = {"percent": u.seven, "resets_at": now + 86400}
        row = {"at": at, "fetched_at": at, "error": None, "spend_limit_reached": None, "windows": windows}
        _json(accounts.fetch_path(store, u.name, generation), row)
