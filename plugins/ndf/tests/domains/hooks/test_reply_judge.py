"""判定のリクエスト（hook の側）の単体テスト（#4000 の決定 1・3・6・7・10・11）。

実時間の経過と実プロセスの起動をテストの仕様にしない。子の起動は偽の `Popen`・偽の `killpg`・差し替えた猶予の待ち
（子の標準入出力は同じプロセスの中のパイプ）、`/proc` は偽の読み手、時刻は引数で渡す。設定（`limits.json` の判定の行）はまだ無いので、設定を読む口を差し替えて与える。
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any, NamedTuple, Optional

import pytest

from ndf.domains.hooks import reply_judge, tables

# 詳細設計の「内部のデータと定数」の `hook.stop-wait.judge`（行が足されるまでテストの中だけで与える）
JUDGE: dict[str, Any] = {
    "model": "haiku",
    "seconds": 5,
    "max_chars": 1600,
    "rest_seconds": 600,
    "lock_seconds": 0.2,
    "rounds": 3,
    "auth_markers": ["Not logged in", "Invalid API key"],
    "endpoint": {
        "entrypoints": ["cli", "sdk-cli"],
        "ancestors": 8,
        "cli_exe": ["*/claude/versions/*"],
        "cli_script": ["*/@anthropic-ai/claude-code/cli.js"],
        "flags": ["--settings", "--setting-sources"],
        "keys": ["awsAuthRefresh", "awsCredentialExport", "gcpAuthRefresh"],
        "env_names": ["ANTHROPIC_", "CLAUDE_CONFIG_DIR"],
    },
}

HOOK_PID = 100
CLAUDE_EXE = "/home/u/.local/share/claude/versions/2.1.330"
CLI_JS = "/opt/n/lib/node_modules/@anthropic-ai/claude-code/cli.js"
ENV = {"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PROJECT_DIR": "/w/p"}
OTHER_ENV = {"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PROJECT_DIR": "/w/other"}
LAUNCH = reply_judge.Launch(argv=["claude", "-p"], project_dir="/w/p")
GATEWAY = reply_judge.Launch(argv=["claude", "--settings", "gw.json"], project_dir="/w/p")
OTHER = reply_judge.Launch(argv=["claude", "-p"], project_dir="/w/other")


@pytest.fixture(autouse=True)
def judge_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """設定を読む口を差し替える（`limits.json` の行は別の課題が足す）。"""
    monkeypatch.setattr(reply_judge, "_judge", JUDGE.__getitem__)


class Proc(NamedTuple):
    """偽の `/proc` の 1 プロセス。"""

    ppid: int
    exe: str
    cmdline: list[str]
    comm: str = "claude"


def fake_proc(tree: dict[int, Proc]):
    """偽の `/proc` の読み手（`read`・`readlink`）。実の `/proc` は読まない。"""

    def entry(path: Path) -> Proc:
        found = tree.get(int(path.parent.name))
        if found is None:
            raise FileNotFoundError(str(path))
        return found

    def read(path: Path) -> bytes:
        proc = entry(path)
        if path.name == "stat":
            return f"{path.parent.name} ({proc.comm}) S {proc.ppid} 1 1 0 -1".encode()
        return b"\0".join(a.encode("utf-8") for a in proc.cmdline) + b"\0"

    def readlink(path: Path) -> str:
        return entry(path).exe

    return read, readlink


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
        os.write(out_write, out.encode("utf-8"))
        # finished が偽なら標準出力を閉じない（子が回答しないまま残る）
        self._out_write: Optional[int] = None if finished else out_write
        if finished:
            os.close(out_write)

    def sent(self) -> dict[str, Any]:
        """子の標準入力に渡った JSON。"""
        return json.loads(self._sent.read().decode("utf-8"))

    def wait(self, timeout: Optional[float] = None) -> int:
        self.log.append(("wait", timeout))
        return self.returncode

    def close(self) -> None:
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


# ask


def test_ask_reads_the_answer_and_passes_the_parent_launch() -> None:
    judged, proc, spawner, _ = ask_with(json.dumps({"kind": "wait", "calls": 1}) + "\n")
    assert proc is not None
    sent = proc.sent()
    assert judged.kind == "wait" and judged.failure is None and judged.calls == 1
    assert sent["parent_argv"] == ["claude", "-p"] and sent["project_dir"] == "/w/p" and sent["seconds"] == 5.0
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


# parent_launch


def test_parent_launch_finds_the_native_claude_code() -> None:
    read, readlink = fake_proc(chain(1))
    found = reply_judge.parent_launch(ENV, HOOK_PID, read=read, readlink=readlink)
    assert found == reply_judge.Launch(argv=["claude", "-p"], project_dir="/w/p")


def test_parent_launch_finds_the_node_launch() -> None:
    tree = {HOOK_PID: Proc(ppid=101, exe="/bin/bash", cmdline=["/bin/bash"], comm="bash")}
    tree[101] = Proc(ppid=1, exe="/usr/bin/node", cmdline=["node", CLI_JS, "-p"], comm="node")
    read, readlink = fake_proc(tree)
    found = reply_judge.parent_launch(ENV, HOOK_PID, read=read, readlink=readlink)
    assert found is not None and found.argv == ["node", CLI_JS, "-p"]


@pytest.mark.parametrize(("depth", "wanted"), [(8, ["claude", "-p"]), (9, None)])
def test_parent_launch_stops_at_the_generation_limit(depth: int, wanted: Optional[list[str]]) -> None:
    read, readlink = fake_proc(chain(depth))
    found = reply_judge.parent_launch(ENV, HOOK_PID, read=read, readlink=readlink)
    assert (found.argv if found else None) == wanted


def test_parent_launch_stops_at_a_node_launch_with_a_relative_script() -> None:
    # 相対パスは起動した時の作業ディレクトリが分からないので、外側の Claude Code の引数を採らない
    tree = {
        HOOK_PID: Proc(ppid=101, exe="/usr/bin/node", cmdline=["node", "cli.js", "--settings=gw.json"], comm="node"),
        101: Proc(ppid=1, exe=CLAUDE_EXE, cmdline=["claude"]),
    }
    read, readlink = fake_proc(tree)
    assert reply_judge.parent_launch(ENV, HOOK_PID, read=read, readlink=readlink) is None


def test_parent_launch_takes_the_nearest_claude_code_not_the_shell() -> None:
    tree = {
        HOOK_PID: Proc(ppid=101, exe="/bin/bash", cmdline=["/bin/bash", "-c", "claude --settings x.json"], comm="bash"),
        101: Proc(ppid=102, exe=CLAUDE_EXE, cmdline=["claude", "--settings=gw.json"]),
        102: Proc(ppid=1, exe=CLAUDE_EXE, cmdline=["claude"]),
    }
    read, readlink = fake_proc(tree)
    found = reply_judge.parent_launch(ENV, HOOK_PID, read=read, readlink=readlink)
    assert found is not None and found.argv == ["claude", "--settings=gw.json"]


def test_parent_launch_does_not_take_a_process_with_claude_in_its_arguments() -> None:
    tree = {
        HOOK_PID: Proc(ppid=101, exe="/usr/bin/python3", cmdline=["python3", "run.py"], comm="python3"),
        101: Proc(ppid=1, exe="/usr/bin/python3", cmdline=["python3", "x.py", "claude"], comm="python3"),
    }
    read, readlink = fake_proc(tree)
    assert reply_judge.parent_launch(ENV, HOOK_PID, read=read, readlink=readlink) is None


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
    read, readlink = fake_proc(chain(1))
    assert reply_judge.parent_launch(env, HOOK_PID, read=read, readlink=readlink) is None


def test_parent_launch_accepts_an_absolute_config_dir() -> None:
    read, readlink = fake_proc(chain(1))
    env = {**ENV, "CLAUDE_CONFIG_DIR": "/home/u/.claude"}
    assert reply_judge.parent_launch(env, HOOK_PID, read=read, readlink=readlink) is not None


def test_parent_launch_returns_none_without_proc() -> None:
    def read(path: Path) -> bytes:
        raise FileNotFoundError(str(path))

    def readlink(path: Path) -> str:
        raise FileNotFoundError(str(path))

    assert reply_judge.parent_launch(ENV, HOOK_PID, read=read, readlink=readlink) is None


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
    first = reply_judge.Launch(argv=["claude", "-p", "最初の質問", "--settings", "a.json"], project_dir="/w/p")
    second = reply_judge.Launch(argv=["claude", "--settings=b.json", "-p", "次の質問"], project_dir="/w/p")
    assert reply_judge.endpoint_scope(ENV, first) == reply_judge.endpoint_scope(ENV, second)
    keyed = {**ENV, "ANTHROPIC_API_KEY": "sk-1"}
    assert reply_judge.endpoint_scope(keyed, first) == reply_judge.endpoint_scope({**ENV, "ANTHROPIC_API_KEY": "sk-2"}, first)
    # 位置引数に書いた --settings はオプションではない
    quoted = reply_judge.Launch(argv=["claude", "-p", "--", "--settings"], project_dir="/w/p")
    assert reply_judge.endpoint_scope(ENV, quoted) == reply_judge.endpoint_scope(ENV, LAUNCH)


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
    monkeypatch.setattr(reply_judge, "_judge", {**JUDGE, "lock_seconds": 0}.__getitem__)
    path = reply_judge.rest_path(tmp_path)
    path.parent.mkdir(parents=True)
    with open(path.with_name(reply_judge.REST_LOCK_NAME), "w") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        reply_judge.mark_rest(tmp_path, "launch", 1000.0, ENV, LAUNCH)
    assert path.exists() is False


def test_mark_rest_does_not_write_secret_values(tmp_path: Path) -> None:
    gateway = reply_judge.Launch(argv=["claude", "--settings", '{"env": {"ANTHROPIC_API_KEY": "sk-arg"}}'], project_dir="/w/p")
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
