"""判定のリクエスト（hook の側）の単体テスト（#4000 の決定 1・3・6・7・10・11）。

実時間の経過と実プロセスの起動をテストの仕様にしない。子の起動は偽の `Popen`・偽の `killpg`、`/proc` は偽の
読み手、時刻は引数で渡す。設定（`limits.json` の判定の行）はまだ無いので、設定を読む口を差し替えて与える。
"""

from __future__ import annotations

import json
import signal
import subprocess
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
LAUNCH = reply_judge.Launch(argv=["claude", "-p"], project_dir="/w/p")


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
    """偽の子プロセス。実プロセスを起動せず、渡った引数を残す。"""

    def __init__(self, out: str = "", code: int = 0, raises: Optional[BaseException] = None, hang: bool = False):
        self.pid = 4242
        self.returncode = code
        self.out = out
        self.raises = raises
        self.hang = hang
        self.sent: list[tuple[Any, Any]] = []
        self.waits: list[Optional[float]] = []

    def communicate(self, data: Any = None, timeout: Optional[float] = None) -> tuple[str, str]:
        self.sent.append((data, timeout))
        if self.raises is not None:
            raise self.raises
        return self.out, ""

    def wait(self, timeout: Optional[float] = None) -> int:
        self.waits.append(timeout)
        if timeout is not None and self.hang:
            raise subprocess.TimeoutExpired("judge", timeout)
        return self.returncode


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
    """偽の `killpg`。組の番号と送った種類を残す。"""

    def __init__(self) -> None:
        self.sent: list[tuple[int, int]] = []

    def __call__(self, pid: int, sig: int) -> None:
        self.sent.append((pid, sig))


def ask_with(proc: Optional[FakeProc], error: Optional[BaseException] = None, env: Optional[dict[str, str]] = None):
    """偽の `popen`・`killpg` で `ask` を 1 回打ち、結果と残した引数を返す。"""
    spawner, killer = Spawner(proc, error), Killer()
    judged = reply_judge.ask("本文", 5.0, env if env is not None else {}, LAUNCH, popen=spawner, killpg=killer)
    return judged, spawner, killer


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


@pytest.mark.parametrize(("size", "kept"), [(1600, 1600), (1601, 1600)])
def test_judge_text_returns_the_tail_within_the_limit(size: int, kept: int) -> None:
    text = "あ" * size
    out = reply_judge.judge_text(text, [False], 1600)
    assert len(out) == kept and text.endswith(out)


# ask


def test_ask_reads_the_answer_and_passes_the_parent_launch() -> None:
    proc = FakeProc(out=json.dumps({"kind": "wait", "calls": 1}) + "\n")
    judged, spawner, _ = ask_with(proc)
    sent = json.loads(proc.sent[0][0])
    assert judged.kind == "wait" and judged.failure is None and judged.calls == 1
    assert sent["parent_argv"] == ["claude", "-p"] and sent["project_dir"] == "/w/p" and sent["seconds"] == 5.0
    assert proc.sent[0][1] == 5.0
    (args, kwargs) = spawner.seen[0]
    assert args == [sys.executable, "-P", "-m", reply_judge.CHILD_MODULE] and kwargs["start_new_session"] is True


def test_ask_drops_the_session_variables_and_keeps_the_endpoint() -> None:
    env = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "s1", "ANTHROPIC_BASE_URL": "https://api.anthropic.com"}
    _, spawner, _ = ask_with(FakeProc(out=json.dumps({"kind": "report", "calls": 1})), env=env)
    child = spawner.seen[0][1]["env"]
    assert child["MAX_THINKING_TOKENS"] == "0" and child["PYTHONPATH"] == str(tables.PLUGIN_ROOT / "src")
    assert "CLAUDECODE" not in child and "CLAUDE_CODE_SESSION_ID" not in child
    assert child["ANTHROPIC_BASE_URL"] == "https://api.anthropic.com"


def test_ask_kills_the_group_after_a_normal_answer() -> None:
    _, _, killer = ask_with(FakeProc(out=json.dumps({"kind": "report", "calls": 1})))
    assert killer.sent == [(4242, signal.SIGKILL)]


def test_ask_stops_the_group_on_timeout() -> None:
    proc = FakeProc(raises=subprocess.TimeoutExpired("judge", 5.0), hang=True)
    judged, _, killer = ask_with(proc)
    assert judged.kind is None and judged.failure == "timeout"
    assert killer.sent == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]
    assert proc.waits == [0.2, None]


def test_ask_stops_the_group_and_reraises_an_interrupt() -> None:
    proc = FakeProc(raises=KeyboardInterrupt(), hang=True)
    killer = Killer()
    with pytest.raises(KeyboardInterrupt):
        reply_judge.ask("本文", 5.0, {}, LAUNCH, popen=Spawner(proc), killpg=killer)
    assert killer.sent == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)] and proc.waits == [0.2, None]


def test_ask_reports_a_launch_failure() -> None:
    judged, _, killer = ask_with(None, error=OSError("python が無い"))
    assert judged.failure == "launch" and judged.calls == 0 and killer.sent == []


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
    judged, _, _ = ask_with(FakeProc(out=out, code=code))
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


# resting / mark_rest


@pytest.mark.parametrize("failure", ["launch", "refused", "usage_limit", "auth", "endpoint"])
def test_mark_rest_writes_for_launch_failures(tmp_path: Path, failure: str) -> None:
    reply_judge.mark_rest(tmp_path, failure, 1000.0)
    assert reply_judge.resting(tmp_path, 1000.0) is True
    assert reply_judge.resting(tmp_path, 1500.0) is True


@pytest.mark.parametrize(("now", "wanted"), [(1599.0, True), (1600.0, False), (1601.0, False)])
def test_resting_ends_when_the_rest_is_over(tmp_path: Path, now: float, wanted: bool) -> None:
    reply_judge.mark_rest(tmp_path, "launch", 1000.0)
    assert reply_judge.resting(tmp_path, now) is wanted


@pytest.mark.parametrize("failure", ["timeout", "no_slot", "exit", "shape"])
def test_mark_rest_ignores_the_other_failures(tmp_path: Path, failure: str) -> None:
    reply_judge.mark_rest(tmp_path, failure, 1000.0)
    assert reply_judge.rest_path(tmp_path).exists() is False
    assert reply_judge.resting(tmp_path, 1000.0) is False


def test_resting_is_false_without_a_marker(tmp_path: Path) -> None:
    assert reply_judge.resting(tmp_path, 1000.0) is False


def test_resting_is_false_for_a_broken_marker(tmp_path: Path) -> None:
    path = reply_judge.rest_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("壊れた", encoding="utf-8")
    assert reply_judge.resting(tmp_path, 1000.0) is False


def test_mark_rest_does_not_raise_on_an_unwritable_state_dir(tmp_path: Path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("", encoding="utf-8")
    reply_judge.mark_rest(blocked / "state", "launch", 1000.0)
    assert reply_judge.resting(blocked / "state", 1000.0) is False
