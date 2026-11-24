"""判定のリクエスト（hook の側）の単体テスト（#4000 の決定 1・3・6・7・10・11）。

実時間の経過と実プロセスの起動をテストの仕様にしない。子の起動は偽の `Popen`・偽の `killpg`・差し替えた猶予の待ち
（子の標準入出力は同じプロセスの中のパイプ）、`/proc` は偽の読み手、時刻は引数で渡す。設定はプラグインの `contracts/limits/hook.stop-wait.judge.json` を読む。
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import sys
import types
from pathlib import Path
from typing import Any, NamedTuple, Optional

import pytest

from ndf.domains.hooks import reply_judge, tables

HOOK_PID = 100
CLAUDE_EXE = "/home/u/.local/share/claude/versions/2.1.330"
CLI_JS = "/opt/n/lib/node_modules/@anthropic-ai/claude-code/cli.js"
# 偽の `stat` が返す実行ファイルの [st_dev, st_ino]
EXE_ID = [2049, 777]
ENV = {"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PROJECT_DIR": "/w/p"}
OTHER_ENV = {"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PROJECT_DIR": "/w/other"}
LAUNCH = reply_judge.Launch(argv=["claude", "-p"], project_dir="/w/p", exe=CLAUDE_EXE, exe_id=EXE_ID)
GATEWAY = reply_judge.Launch(argv=["claude", "--settings", "gw.json"], project_dir="/w/p", exe=CLAUDE_EXE, exe_id=EXE_ID)
OTHER = reply_judge.Launch(argv=["claude", "-p"], project_dir="/w/other", exe=CLAUDE_EXE, exe_id=EXE_ID)


class Proc(NamedTuple):
    """偽の `/proc` の 1 プロセス。"""

    ppid: int
    exe: str
    cmdline: list[str]
    comm: str = "claude"


def fake_proc(tree: dict[int, Proc]):
    """偽の `/proc` の読み手（`read`・`readlink`・`stat`）。実の `/proc` は読まない。読んだパスを `seen` に残す。"""

    seen: list[str] = []

    def entry(path: Path) -> Proc:
        found = tree.get(int(path.parent.name))
        if found is None:
            raise FileNotFoundError(str(path))
        return found

    def read(path: Path) -> bytes:
        seen.append(str(path))
        proc = entry(path)
        if path.name == "stat":
            return f"{path.parent.name} ({proc.comm}) S {proc.ppid} 1 1 0 -1".encode()
        return b"\0".join(a.encode("utf-8") for a in proc.cmdline) + b"\0"

    def readlink(path: Path) -> str:
        return entry(path).exe

    def stat(path: Path) -> os.stat_result:
        entry(path)
        return os.stat_result((0o100755, EXE_ID[1], EXE_ID[0], 1, 0, 0, 0, 0, 0, 0))

    return {"read": read, "readlink": readlink, "stat": stat}, seen


def launch_of(env: dict[str, str], tree: dict[int, Proc]) -> Optional[reply_judge.Launch]:
    """偽の `/proc` で `parent_launch` を 1 回打つ。"""
    readers, _ = fake_proc(tree)
    return reply_judge.parent_launch(env, HOOK_PID, **readers)


def chain(depth: int) -> dict[int, Proc]:
    """hook のプロセスから `depth` 代目に Claude Code がいる組み合わせ。"""
    tree = {HOOK_PID + i: Proc(ppid=HOOK_PID + i + 1, exe="/bin/bash", cmdline=["/bin/bash"]) for i in range(depth)}
    tree[HOOK_PID + depth] = Proc(ppid=1, exe=CLAUDE_EXE, cmdline=["claude", "-p"])
    return tree


class FakeProc:
    """偽の子プロセス。実プロセスを起動せず、標準入出力は同じプロセスの中のパイプ。送信と回収の順を `log` に残す。"""

    def __init__(self, log: list[tuple[Any, ...]], out: str = "", code: int = 0, finished: bool = True):
        self.pid = 4242
        self.returncode = code
        self.log = log
        in_read, in_write = os.pipe()
        out_read, out_write = os.pipe()
        self.stdin = os.fdopen(in_write, "wb")
        self.stdout = os.fdopen(out_read, "rb")
        self._sent = os.fdopen(in_read, "rb")
        self._received: Optional[bytes] = None
        os.write(out_write, out.encode("utf-8"))
        # finished が偽なら標準出力を閉じない（子が回答しないまま残る）
        self._out_write: Optional[int] = None if finished else out_write
        if finished:
            os.close(out_write)

    def sent(self) -> dict[str, Any]:
        """子の標準入力に渡った JSON。`close` の後も読めるよう、読んだ中身を残す。"""
        if self._received is None:
            self._received = self._sent.read()
        return json.loads(self._received.decode("utf-8"))

    def wait(self, timeout: Optional[float] = None) -> int:
        self.log.append(("wait", timeout))
        return self.returncode

    def close(self) -> None:
        if self._received is None and self.stdin.closed:
            # 書き手（ask）が標準入力を閉じた後なので、終わりまで読んでも止まらない
            self._received = self._sent.read()
        self._sent.close()
        if self._out_write is not None:
            os.close(self._out_write)


class Spawner:
    """偽の `Popen`。起動の引数を残し、用意したプロセスを返す。"""

    def __init__(self, proc: Optional[FakeProc] = None, error: Optional[BaseException] = None):
        self.proc = proc
        self.error = error
        self.seen: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> FakeProc:
        self.seen.append((list(args), kwargs))
        if self.error is not None:
            raise self.error
        assert self.proc is not None
        return self.proc


class Killer:
    """偽の `killpg`。組の番号と送った種類を `log` に残す。"""

    def __init__(self, log: list[tuple[Any, ...]]) -> None:
        self.log = log

    def __call__(self, pid: int, sig: int) -> None:
        self.log.append(("kill", pid, sig))


def ask_with(
    out: Optional[str] = None,
    code: int = 0,
    finished: bool = True,
    error: Optional[BaseException] = None,
    env: Optional[dict[str, str]] = None,
    seconds: float = 5.0,
):
    """偽の `popen`・`killpg`・猶予の待ちで `ask` を 1 回打ち、結果・偽のプロセス・起動の引数・送信と回収の順を返す。"""
    log: list[tuple[Any, ...]] = []
    proc = None if out is None else FakeProc(log, out=out, code=code, finished=finished)
    spawner = Spawner(proc, error)
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(reply_judge, "_pause", lambda s: log.append(("sleep", s)))
            judged = reply_judge.ask("本文", seconds, env if env is not None else {}, LAUNCH, popen=spawner, killpg=Killer(log))
    finally:
        if proc is not None:
            proc.close()
    return judged, proc, spawner, log


KILL = ("kill", 4242, signal.SIGKILL)
TERM = ("kill", 4242, signal.SIGTERM)


# judge_text


def test_judge_text_keeps_the_question_line_before_a_long_report() -> None:
    lines = ["この方針で進めてよいか。", *[f"- 報告 {i}" for i in range(9)]]
    text = "\n".join(lines)
    out = reply_judge.judge_text(text, [False] * len(lines), 1600)
    assert out.splitlines()[0] == "この方針で進めてよいか。" and len(out.splitlines()) == 10


def test_judge_text_drops_blank_lines_and_code_blocks() -> None:
    lines = ["報告する。", "", "```", "これでよいか。", "```", "以上。"]
    code = [False, False, True, True, True, False]
    assert reply_judge.judge_text("\n".join(lines), code, 1600) == "報告する。\n以上。"


def test_judge_text_counts_lines_like_markdown() -> None:
    # U+2028 は Markdown では改行でない。行がずれるとコードの行を本文と取り違える
    text = "報告する。\u2028続き\n```\n秘密の値\n```"
    assert reply_judge.judge_text(text, [False, True, True, True], 1600) == "報告する。\u2028続き"


def test_judge_text_is_empty_when_the_flags_do_not_match() -> None:
    assert reply_judge.judge_text("報告する。\n```\n秘密の値\n```", [False, True], 1600) == ""


@pytest.mark.parametrize(("size", "kept"), [(1600, 1600), (1601, 1600)])
def test_judge_text_returns_the_tail_within_the_limit(size: int, kept: int) -> None:
    text = "あ" * size
    out = reply_judge.judge_text(text, [False], 1600)
    assert len(out) == kept and text.endswith(out)


def test_judge_text_keeps_the_delegated_heading_within_the_limit() -> None:
    text = "## このままマージしてよいですか？\n" + "あ" * 2000
    out = reply_judge.judge_text(text, [False, False], 1600, ["このままマージしてよいですか？"])
    assert len(out) == 1600 and out.startswith("## このままマージしてよいですか？\n") and text.endswith(out.splitlines()[1])


def test_judge_text_keeps_a_heading_pushed_out_by_the_lead() -> None:
    # 先頭に古い見出しを置いて末尾が縮んでも、当初の末尾にあった直近の見出しも残す
    first, second = "最初の質問はどうしますか？", "二つ目の質問はどうしますか？"
    text = f"## {first}\n" + "あ" * 500 + f"\n## {second}\n" + "い" * 1570
    out = reply_judge.judge_text(text, [False] * 4, 1600, [first, second])
    assert len(out) == 1600 and out.splitlines()[:2] == [f"## {first}", f"## {second}"]


def test_judge_text_keeps_a_later_heading_after_one_quoted_in_the_tail() -> None:
    # 先行する見出しの文が末尾に引用されて残っても、その後の見出しが外れれば先頭へ足し、半分枠では後ろの見出しを残す
    first, second = "このままマージしてよいですか？", "本番へデプロイしてよいですか？"
    old = [f"古い質問 {i} はどうしますか？" for i in range(60)]
    text = "\n".join([*(f"## {h}" for h in old), "あ" * 900, f"## {first}", "い" * 30, f"## {second}", "う" * 780, f"議事メモ: {first}"])
    out = reply_judge.judge_text(text, [False] * (len(old) + 6), 1600, [*old, first, second])
    lead, _, rest = out.partition("\nううう")
    assert len(out) == 1600 and lead.endswith(f"## {second}") and text.endswith(rest)


def test_judge_text_does_not_repeat_a_heading_already_in_the_tail() -> None:
    text = "あ" * 2000 + "\n## このままマージしてよいですか？\n以上。"
    out = reply_judge.judge_text(text, [False, False, False], 1600, ["このままマージしてよいですか？"])
    assert out == text[-1600:]


# ask


def test_ask_reads_the_answer_and_passes_the_parent_launch() -> None:
    judged, proc, spawner, _ = ask_with(json.dumps({"kind": "wait", "calls": 1}) + "\n")
    assert proc is not None
    sent = proc.sent()
    assert judged.kind == "wait" and judged.failure is None and judged.calls == 1
    assert sent["parent_argv"] == ["claude", "-p"] and sent["project_dir"] == "/w/p" and sent["seconds"] == 5.0
    assert sent["parent_exe"] == CLAUDE_EXE and sent["parent_exe_id"] == EXE_ID
    (args, kwargs) = spawner.seen[0]
    assert args == [sys.executable, "-P", "-m", reply_judge.CHILD_MODULE] and kwargs["start_new_session"] is True


def test_ask_drops_the_session_variables_and_keeps_the_endpoint() -> None:
    env = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s1", "ANTHROPIC_BASE_URL": "https://api.anthropic.com"}
    _, _, spawner, _ = ask_with(json.dumps({"kind": "report", "calls": 1}), env=env)
    child = spawner.seen[0][1]["env"]
    assert child["MAX_THINKING_TOKENS"] == "0" and child["PYTHONPATH"] == str(tables.PLUGIN_ROOT / "src")
    assert "CLAUDECODE" not in child and "CLAUDE_CODE_SESSION_ID" not in child
    assert child["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"


def test_ask_kills_the_group_before_reaping_after_a_normal_answer() -> None:
    # 回収した後に組の番号へ送ると、再利用された別の組に届きうる
    _, _, _, log = ask_with(json.dumps({"kind": "report", "calls": 1}))
    assert log == [KILL, ("wait", None)]


def test_ask_keeps_an_answer_read_before_the_kill() -> None:
    judged, _, _, _ = ask_with(json.dumps({"kind": "report", "calls": 1}), code=-signal.SIGKILL)
    assert judged.kind == "report"


def test_ask_kills_the_group_on_timeout_even_if_the_leader_already_ended() -> None:
    # 偽の wait はすぐ返る（判定の子だけが SIGTERM で先に終わった）。組に残る claude へ SIGKILL が届いてから回収する
    judged, _, _, log = ask_with("", finished=False, seconds=0.0)
    assert judged.kind is None and judged.failure == "timeout"
    assert log == [TERM, ("sleep", reply_judge.GROUP_GRACE), KILL, ("wait", None)]


def test_ask_stops_the_group_and_reraises_an_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(*_: Any) -> None:
        raise KeyboardInterrupt

    log: list[tuple[Any, ...]] = []
    monkeypatch.setattr(reply_judge, "_exchange", interrupted)
    monkeypatch.setattr(reply_judge, "_pause", lambda s: log.append(("sleep", s)))
    proc = FakeProc(log)
    try:
        with pytest.raises(KeyboardInterrupt):
            reply_judge.ask("本文", 5.0, {}, LAUNCH, popen=Spawner(proc), killpg=Killer(log))
    finally:
        proc.close()
    assert log == [TERM, ("sleep", reply_judge.GROUP_GRACE), KILL, ("wait", None)]


def test_ask_kills_the_group_before_reaping_when_the_grace_is_interrupted(monkeypatch: pytest.MonkeyPatch) -> None:
    # 猶予の待ちの中で締め切りの SIGALRM が来ても、SIGKILL を送ってから回収する
    log: list[tuple[Any, ...]] = []

    def alarmed(seconds: float) -> None:
        log.append(("sleep", seconds))
        raise TimeoutError("締め切り")

    monkeypatch.setattr(reply_judge, "_pause", alarmed)
    proc = FakeProc(log, finished=False)
    try:
        with pytest.raises(TimeoutError):
            reply_judge.ask("本文", 0.0, {}, LAUNCH, popen=Spawner(proc), killpg=Killer(log))
    finally:
        proc.close()
    assert log == [TERM, ("sleep", reply_judge.GROUP_GRACE), KILL, ("wait", None)]


def test_ask_reports_a_launch_failure() -> None:
    judged, _, _, log = ask_with(error=OSError("python が無い"))
    assert judged.failure == "launch" and judged.calls == 0 and log == []


@pytest.mark.parametrize(
    ("out", "code", "failure"),
    [
        ("", 1, "exit"),
        ("ただの文", 0, "shape"),
        ('{"kind": "maybe", "calls": 1}', 0, "shape"),
        ('{"failure": "no_slot", "calls": 0}', 0, "no_slot"),
    ],
)
def test_ask_maps_the_failures(out: str, code: int, failure: str) -> None:
    judged, _, _, _ = ask_with(out, code=code)
    assert judged.kind is None and judged.failure == failure


# _awaited


class StreamProc(FakeProc):
    """回収の時点で標準入出力が開いているかを `log` に残す偽の子プロセス。"""

    def wait(self, timeout: Optional[float] = None) -> int:
        self.log.append(("wait", timeout, self.stdin.closed, self.stdout.closed))
        return self.returncode


WAIT_OPEN = ("wait", None, False, False)


def awaited_with(monkeypatch: pytest.MonkeyPatch, exchange: Any, killpg: Any = None, code: int = 0):
    """`_exchange` を差し替えて `_awaited` を 1 回打ち、結果（または投げた例外）・偽のプロセス・順・`_exchange` の引数を返す。"""
    log: list[tuple[Any, ...]] = []
    seen: list[tuple[bytes, float]] = []

    def fake_exchange(_proc: Any, payload: bytes, deadline: float) -> Optional[bytes]:
        seen.append((payload, deadline))
        return exchange()

    monkeypatch.setattr(reply_judge, "_exchange", fake_exchange)
    monkeypatch.setattr(reply_judge, "_pause", lambda s: log.append(("sleep", s)))
    # 起動の時刻 100.0 の後の時計にする（実の時計は CI の起動直後に 100 秒未満のことがあり、所要の秒が負になる）
    monkeypatch.setattr(reply_judge, "time", types.SimpleNamespace(monotonic=lambda: 101.0))
    proc = StreamProc(log, code=code)
    try:
        try:
            result: Any = reply_judge._awaited(proc, "本文", 5.0, 100.0, killpg if killpg is not None else Killer(log))
        except BaseException as error:  # noqa: BLE001 投げた例外も結果として比べる
            result = error
    finally:
        proc.close()
    return result, proc, log, seen


def test_awaited_passes_the_payload_and_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, _, seen = awaited_with(monkeypatch, lambda: b'{"kind": "report", "calls": 1}')
    assert seen == [("本文".encode("utf-8"), 105.0)]


def test_awaited_kills_without_grace_after_an_answer_and_closes_the_streams_after_reaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    judged, proc, log, _ = awaited_with(monkeypatch, lambda: b'{"kind": "wait", "calls": 2}', code=-signal.SIGKILL)
    assert isinstance(judged, reply_judge.Judged) and (judged.kind, judged.failure, judged.calls) == ("wait", None, 2)
    assert log == [KILL, WAIT_OPEN] and proc.stdin.closed and proc.stdout.closed


def test_awaited_reads_the_exit_code_after_reaping(monkeypatch: pytest.MonkeyPatch) -> None:
    judged, _, _, _ = awaited_with(monkeypatch, lambda: b"", code=1)
    assert isinstance(judged, reply_judge.Judged) and judged.failure == "exit"


def test_awaited_decodes_a_broken_answer_as_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    judged, _, _, _ = awaited_with(monkeypatch, lambda: b"\xff\xfe")
    assert isinstance(judged, reply_judge.Judged) and judged.failure == "shape"


def test_awaited_stops_gently_on_timeout_and_closes_the_streams_after_reaping(monkeypatch: pytest.MonkeyPatch) -> None:
    judged, proc, log, _ = awaited_with(monkeypatch, lambda: None, code=-signal.SIGTERM)
    assert isinstance(judged, reply_judge.Judged)
    assert (judged.kind, judged.failure, judged.calls) == (None, "timeout", 0) and judged.ms >= 0
    assert log == [TERM, ("sleep", reply_judge.GROUP_GRACE), KILL, WAIT_OPEN]
    assert proc.stdin.closed and proc.stdout.closed


@pytest.mark.parametrize("error", [KeyboardInterrupt(), OSError("読めない"), TimeoutError("締め切り")])
def test_awaited_stops_gently_and_reraises_the_same_error(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    def broken() -> Optional[bytes]:
        raise error

    raised, proc, log, _ = awaited_with(monkeypatch, broken)
    assert raised is error
    assert log == [TERM, ("sleep", reply_judge.GROUP_GRACE), KILL, WAIT_OPEN]
    assert proc.stdin.closed and proc.stdout.closed


@pytest.mark.parametrize("answer", [b'{"kind": "report", "calls": 1}', None])
def test_awaited_reaps_and_closes_the_streams_when_the_kill_fails(monkeypatch: pytest.MonkeyPatch, answer: Optional[bytes]) -> None:
    # 組の番号が無い・権限が無いの他の失敗は投げ直すが、回収と標準入出力を閉じることは済ませる
    def refused(pid: int, sig: int) -> None:
        raise RuntimeError(sig)

    raised, proc, log, _ = awaited_with(monkeypatch, lambda: answer, killpg=refused)
    assert isinstance(raised, RuntimeError) and raised.args == (signal.SIGKILL,)
    assert log[-1] == WAIT_OPEN and proc.stdin.closed and proc.stdout.closed


@pytest.mark.parametrize("gone", [ProcessLookupError, PermissionError])
def test_awaited_returns_the_answer_when_the_group_is_already_gone(monkeypatch: pytest.MonkeyPatch, gone: type[BaseException]) -> None:
    def missing(pid: int, sig: int) -> None:
        raise gone

    judged, _, log, _ = awaited_with(monkeypatch, lambda: b'{"kind": "report", "calls": 1}', killpg=missing)
    assert isinstance(judged, reply_judge.Judged) and judged.kind == "report" and log == [WAIT_OPEN]


# parent_launch


def test_parent_launch_finds_the_native_claude_code() -> None:
    found = launch_of(ENV, chain(1))
    assert found == reply_judge.Launch(argv=["claude", "-p"], project_dir="/w/p", exe=CLAUDE_EXE, exe_id=EXE_ID)


@pytest.mark.parametrize("script", [CLI_JS, "node_modules/@anthropic-ai/claude-code/cli.js", "/usr/local/bin/claude"])
def test_parent_launch_stops_at_a_node_launch(script: str) -> None:
    # node の上の Claude Code は起動引数からも読み込んだスクリプトからも確証できないので、親と認めず外側も見ない
    tree = {
        HOOK_PID: Proc(ppid=101, exe="/bin/bash", cmdline=["/bin/bash"], comm="bash"),
        101: Proc(ppid=102, exe="/usr/bin/node", cmdline=["node", script, "-p"], comm="node"),
        102: Proc(ppid=1, exe=CLAUDE_EXE, cmdline=["claude"]),
    }
    readers, seen = fake_proc(tree)
    assert reply_judge.parent_launch(ENV, HOOK_PID, **readers) is None
    # node の起動引数は読まない
    assert "/proc/101/cmdline" not in seen


@pytest.mark.parametrize(("depth", "wanted"), [(8, ["claude", "-p"]), (9, None)])
def test_parent_launch_stops_at_the_generation_limit(depth: int, wanted: Optional[list[str]]) -> None:
    found = launch_of(ENV, chain(depth))
    assert (found.argv if found else None) == wanted


def test_parent_launch_refuses_a_deleted_executable() -> None:
    # 親の起動後に実行ファイルが消えたか置き換わったので、親が読み込んだ実体を確かめられない
    tree = chain(1)
    tree[HOOK_PID + 1] = tree[HOOK_PID + 1]._replace(exe=f"{CLAUDE_EXE} (deleted)")
    assert launch_of(ENV, tree) is None


def test_parent_launch_takes_the_nearest_claude_code_not_the_shell() -> None:
    tree = {
        HOOK_PID: Proc(ppid=101, exe="/bin/bash", cmdline=["/bin/bash", "-c", "claude --settings x.json"], comm="bash"),
        101: Proc(ppid=102, exe=CLAUDE_EXE, cmdline=["claude", "--settings=gw.json"]),
        102: Proc(ppid=1, exe=CLAUDE_EXE, cmdline=["claude"]),
    }
    found = launch_of(ENV, tree)
    assert found is not None and found.argv == ["claude", "--settings=gw.json"]


def test_parent_launch_keeps_empty_arguments_in_the_middle() -> None:
    # 空の値を落とすと --settings が --system-prompt の値に読まれ、子が送信先を確かめられない起動を拒めない
    tree = chain(1)
    tree[HOOK_PID + 1] = tree[HOOK_PID + 1]._replace(cmdline=["claude", "--system-prompt", "", "--settings", "gw.json"])
    found = launch_of(ENV, tree)
    assert found is not None and found.argv == ["claude", "--system-prompt", "", "--settings", "gw.json"]
    flagged = reply_judge.Launch(argv=["claude", "--settings", "gw.json"], project_dir="/w/p", exe=CLAUDE_EXE, exe_id=EXE_ID)
    assert reply_judge.endpoint_scope(ENV, found) == reply_judge.endpoint_scope(ENV, flagged)


def test_parent_launch_does_not_take_a_process_with_claude_in_its_arguments() -> None:
    tree = {
        HOOK_PID: Proc(ppid=101, exe="/usr/bin/python3", cmdline=["python3", "run.py"], comm="python3"),
        101: Proc(ppid=1, exe="/usr/bin/python3", cmdline=["python3", "x.py", "claude"], comm="python3"),
    }
    assert launch_of(ENV, tree) is None


@pytest.mark.parametrize(
    "env",
    [
        {"CLAUDE_CODE_ENTRYPOINT": "sdk-ts", "CLAUDE_PROJECT_DIR": "/w/p"},
        {"CLAUDE_CODE_ENTRYPOINT": "cli"},
        {"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PROJECT_DIR": "p"},
        {"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PROJECT_DIR": "/w/p", "CLAUDE_CONFIG_DIR": "./cfg"},
    ],
)
def test_parent_launch_refuses_launches_it_cannot_compare(env: dict[str, str]) -> None:
    assert launch_of(env, chain(1)) is None


def test_parent_launch_accepts_an_absolute_config_dir() -> None:
    assert launch_of({**ENV, "CLAUDE_CONFIG_DIR": "/home/u/.claude"}, chain(1)) is not None


def test_parent_launch_returns_none_without_proc() -> None:
    def missing(path: Path) -> Any:
        raise FileNotFoundError(str(path))

    assert reply_judge.parent_launch(ENV, HOOK_PID, read=missing, readlink=missing, stat=missing) is None


# resting / endpoint_resting / mark_rest


@pytest.mark.parametrize("failure", ["launch", "refused", "usage_limit", "auth"])
def test_mark_rest_writes_for_launch_failures(tmp_path: Path, failure: str) -> None:
    reply_judge.mark_rest(tmp_path, failure, 1000.0, ENV, LAUNCH)
    assert reply_judge.resting(tmp_path, 1000.0) is True
    assert reply_judge.resting(tmp_path, 1500.0) is True


@pytest.mark.parametrize(("now", "wanted"), [(1599.0, True), (1600.0, False), (1601.0, False)])
def test_resting_ends_when_the_rest_is_over(tmp_path: Path, now: float, wanted: bool) -> None:
    reply_judge.mark_rest(tmp_path, "launch", 1000.0, ENV, LAUNCH)
    assert reply_judge.resting(tmp_path, now) is wanted


def test_endpoint_rest_is_kept_to_its_own_target(tmp_path: Path) -> None:
    reply_judge.mark_rest(tmp_path, "endpoint", 1000.0, ENV, GATEWAY)
    assert reply_judge.resting(tmp_path, 1000.0) is False
    assert reply_judge.endpoint_resting(tmp_path, 1000.0, ENV, GATEWAY) is True
    assert reply_judge.endpoint_resting(tmp_path, 1599.0, ENV, GATEWAY) is True
    assert reply_judge.endpoint_resting(tmp_path, 1600.0, ENV, GATEWAY) is False
    # 同じリポジトリの通常の起動・別のリポジトリ・別の接続先の変数は休まない
    assert reply_judge.endpoint_resting(tmp_path, 1000.0, ENV, LAUNCH) is False
    assert reply_judge.endpoint_resting(tmp_path, 1000.0, OTHER_ENV, OTHER) is False
    gateway_env = {**ENV, "ANTHROPIC_BASE_URL": "https://gw.example"}
    assert reply_judge.endpoint_resting(tmp_path, 1000.0, gateway_env, GATEWAY) is False


def test_endpoint_rest_ignores_the_prompt_and_the_values() -> None:
    # 子は endpoint.flags の名前があれば値によらず拒むので、プロンプトと値は対象を分けない
    first = reply_judge.Launch(
        argv=["claude", "-p", "最初の質問", "--settings", "a.json"], project_dir="/w/p", exe=CLAUDE_EXE, exe_id=EXE_ID
    )
    second = reply_judge.Launch(argv=["claude", "--settings=b.json", "-p", "次の質問"], project_dir="/w/p", exe=CLAUDE_EXE, exe_id=EXE_ID)
    assert reply_judge.endpoint_scope(ENV, first) == reply_judge.endpoint_scope(ENV, second)
    keyed = {**ENV, "ANTHROPIC_API_KEY": "sk-1"}
    assert reply_judge.endpoint_scope(keyed, first) == reply_judge.endpoint_scope({**ENV, "ANTHROPIC_API_KEY": "sk-2"}, first)
    # 位置引数に書いた --settings はオプションではない
    quoted = reply_judge.Launch(argv=["claude", "-p", "--", "--settings"], project_dir="/w/p", exe=CLAUDE_EXE, exe_id=EXE_ID)
    assert reply_judge.endpoint_scope(ENV, quoted) == reply_judge.endpoint_scope(ENV, LAUNCH)


def test_endpoint_rest_is_split_by_the_parent_executable(tmp_path: Path) -> None:
    # 旧版のセッションが実行ファイルの差し替えを検出して書いた休みで、新版のセッションの判定を止めない
    reply_judge.mark_rest(tmp_path, "endpoint", 1000.0, ENV, LAUNCH)
    upgraded = LAUNCH._replace(exe="/home/u/.local/share/claude/versions/2.1.331", exe_id=[2049, 778])
    assert reply_judge.endpoint_resting(tmp_path, 1000.0, ENV, upgraded) is False
    assert reply_judge.endpoint_resting(tmp_path, 1000.0, ENV, LAUNCH._replace(exe_id=[2049, 778])) is False
    assert reply_judge.endpoint_resting(tmp_path, 1000.0, ENV, LAUNCH) is True


def test_endpoint_rest_with_flags_is_not_split_by_the_parent_executable(tmp_path: Path) -> None:
    # endpoint.flags があれば子は実行ファイルの同一性によらず拒むので、更新の後も同じ休みで子を起動しない
    reply_judge.mark_rest(tmp_path, "endpoint", 1000.0, ENV, GATEWAY)
    upgraded = GATEWAY._replace(exe="/home/u/.local/share/claude/versions/2.1.331", exe_id=[2049, 778])
    assert reply_judge.endpoint_resting(tmp_path, 1000.0, ENV, upgraded) is True


def test_endpoint_rest_is_split_by_the_config_dir() -> None:
    other = {**ENV, "CLAUDE_CONFIG_DIR": "/home/u/.claude-b"}
    assert reply_judge.endpoint_scope({**ENV, "CLAUDE_CONFIG_DIR": "/home/u/.claude-a"}, LAUNCH) != reply_judge.endpoint_scope(
        other, LAUNCH
    )


def test_endpoint_rest_is_split_by_the_anthropic_profile_dir() -> None:
    # 子はこの保存先からプロファイルを読み、読めなければ拒むので、保存先が違えば別の対象にする
    broken = {**ENV, "ANTHROPIC_CONFIG_DIR": "/home/u/.anthropic-broken"}
    fixed = {**ENV, "ANTHROPIC_CONFIG_DIR": "/home/u/.anthropic"}
    assert reply_judge.endpoint_scope(broken, LAUNCH) != reply_judge.endpoint_scope(fixed, LAUNCH)
    # endpoint.flags があれば子はプロファイルを読む前に拒むので、保存先で対象を分けない
    flagged = reply_judge.Launch(argv=["claude", "--settings", "a.json"], project_dir="/w/p", exe=CLAUDE_EXE, exe_id=EXE_ID)
    assert reply_judge.endpoint_scope(broken, flagged) == reply_judge.endpoint_scope(fixed, flagged)


def test_mark_rest_writes_the_marker_for_the_owner_only(tmp_path: Path) -> None:
    reply_judge.mark_rest(tmp_path, "endpoint", 1000.0, ENV, GATEWAY)
    assert reply_judge.rest_path(tmp_path).stat().st_mode & 0o777 == 0o600


def test_mark_rest_keeps_the_rests_already_written(tmp_path: Path) -> None:
    reply_judge.mark_rest(tmp_path, "endpoint", 1000.0, ENV, GATEWAY)
    reply_judge.mark_rest(tmp_path, "launch", 1100.0, OTHER_ENV, OTHER)
    reply_judge.mark_rest(tmp_path, "endpoint", 1200.0, OTHER_ENV, OTHER)
    assert reply_judge.endpoint_resting(tmp_path, 1300.0, ENV, GATEWAY) is True
    assert reply_judge.resting(tmp_path, 1300.0) is True
    assert reply_judge.endpoint_resting(tmp_path, 1300.0, OTHER_ENV, OTHER) is True


def test_mark_rest_does_not_write_while_another_stop_holds_the_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 錠を取れなければ書かない（別のセッションの書き込みを古い中身で上書きしない）
    real = reply_judge.judge_setting
    monkeypatch.setattr(reply_judge, "judge_setting", lambda key: 0 if key == "lock_seconds" else real(key))
    path = reply_judge.rest_path(tmp_path)
    path.parent.mkdir(parents=True)
    with open(path.with_name(reply_judge.REST_LOCK_NAME), "w") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        reply_judge.mark_rest(tmp_path, "launch", 1000.0, ENV, LAUNCH)
    assert path.exists() is False


def test_mark_rest_does_not_write_secret_values(tmp_path: Path) -> None:
    gateway = reply_judge.Launch(
        argv=["claude", "--settings", '{"env": {"ANTHROPIC_API_KEY": "sk-arg"}}'], project_dir="/w/p", exe=CLAUDE_EXE, exe_id=EXE_ID
    )
    reply_judge.mark_rest(tmp_path, "endpoint", 1000.0, {**ENV, "ANTHROPIC_API_KEY": "sk-secret"}, gateway)
    text = reply_judge.rest_path(tmp_path).read_text(encoding="utf-8")
    assert "sk-secret" not in text and "sk-arg" not in text


@pytest.mark.parametrize("failure", ["timeout", "no_slot", "exit", "shape"])
def test_mark_rest_ignores_the_other_failures(tmp_path: Path, failure: str) -> None:
    reply_judge.mark_rest(tmp_path, failure, 1000.0, ENV, LAUNCH)
    assert reply_judge.rest_path(tmp_path).exists() is False
    assert reply_judge.resting(tmp_path, 1000.0) is False


def test_resting_is_false_without_a_marker(tmp_path: Path) -> None:
    assert reply_judge.resting(tmp_path, 1000.0) is False
    assert reply_judge.endpoint_resting(tmp_path, 1000.0, ENV, LAUNCH) is False


def test_resting_is_false_for_a_broken_marker(tmp_path: Path) -> None:
    path = reply_judge.rest_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("壊れた", encoding="utf-8")
    assert reply_judge.resting(tmp_path, 1000.0) is False
    assert reply_judge.endpoint_resting(tmp_path, 1000.0, ENV, LAUNCH) is False


def test_mark_rest_does_not_raise_on_an_unwritable_state_dir(tmp_path: Path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("", encoding="utf-8")
    reply_judge.mark_rest(blocked / "state", "launch", 1000.0, ENV, LAUNCH)
    assert reply_judge.resting(blocked / "state", 1000.0) is False
