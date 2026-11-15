"""計画のレーンの問いがオーガナイザーへ届く（#3035 の受け入れ条件 1・2・4）。

中継の spawn が組む環境（`hub.spawn_env`）から、hook（AskUserQuestion の guard）・中継のラッパー・`ndf lanes question` を
通して、計画の進行表の依頼と `ndf lanes wait` の知らせまでを確かめる。環境変数は手で足さない。
子のセッションは偽物で、hook と副命令は同じプロセスで呼ぶ。
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from ndf.domains.hooks import main as hook_main
from ndf.domains.relay import hub, lane_question, registry, signals, wrapper
from ndf.progress import command, plan
from ndf.progress.lane import launch
from ndf.progress.plan import cli as plan_cli
from ndf.progress.plan import run as plan_run
from plan_decls import decl, doc

PLAN = "p6"
QUESTIONS = [{"question": "設計を承認するか", "header": "設計", "options": [], "multiSelect": False}]
SAMPLE = Path(__file__).resolve().parents[1] / "domains" / "hooks" / "samples" / "claude"
# 実行した人の中継・オーガナイザー・通知の値を読まない
OUTER_ENV = (
    "NDF_RELAY_DIR",
    "NDF_RELAY_PLAN",
    "NDF_RELAY_NAME",
    "NDF_ORGANIZER_DIR",
    "NDF_ORGANIZER_LANE",
    "SLACK_BOT_TOKEN",
    "SLACK_CHANNEL_ID",
)


@pytest.fixture(autouse=True)
def _outer_env(monkeypatch):
    for name in OUTER_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(plan_run, "detach", lambda name, cwd: 4242)


class _Done:
    def __init__(self, stdout: str, code: int) -> None:
        self.stdout, self.returncode, self.stderr = stdout, code, ""


def _lanes(capsys, *argv: str) -> dict:
    code = plan_cli.main(list(argv))
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0, out
    return out


def _in_process(capsys):
    """`ndf <副命令>` を同じプロセスの `ndf lanes` で流す runner（`subcommand.call` へ渡す）。"""

    def run(cmd, **kw):
        assert cmd[1] == "lanes" and kw["cwd"]
        code = plan_cli.main(cmd[2:])
        return _Done(capsys.readouterr().out, code)

    return run


def _ask_payload(cwd: Path) -> dict:
    """AskUserQuestion の PreToolUse の入力（Claude Code の見本の形）。"""
    path = next(SAMPLE.glob("*/PreToolUse.json"))
    data = json.loads(path.read_text(encoding="utf-8").replace("<cwd>", str(cwd)).replace("<transcript>", str(cwd / "t.jsonl")))
    return {**data, "tool_name": "AskUserQuestion", "tool_input": {"questions": QUESTIONS}}


class _AskingChild:
    """起動したら AskUserQuestion を出し（hook を子の環境で呼ぶ）、1 行を出して終わる偽の子。"""

    def __init__(self, env: dict, cwd: Path, monkeypatch) -> None:
        with monkeypatch.context() as m:
            for name, value in env.items():
                if name.startswith("NDF_"):
                    m.setenv(name, value)
            out = io.StringIO()
            assert hook_main.run_entry(["entry.py", "claude", "PreToolUse"], io.StringIO(json.dumps(_ask_payload(cwd))), out) == 0
        self.read_fd, self.write_fd = os.pipe()
        self.fd = self.read_fd
        self.outputs = [b"asking\r\n"]
        self.alive, self.exitstatus = True, None

    def isalive(self):
        if self.outputs:
            os.write(self.write_fd, self.outputs.pop(0))
        elif self.alive:
            self.alive, self.exitstatus = False, 0
        return self.alive

    def write(self, data):
        return None

    def terminate(self, force=False):
        self.alive, self.exitstatus = False, -9

    def close(self):
        os.close(self.read_fd)
        os.close(self.write_fd)


def _settings(state: Path, cwd: Path, name: str, env: dict, ask) -> wrapper.Settings:
    return wrapper.Settings(
        state_dir=state,
        version="11.0.0-dev.4",
        name=name,
        cwd=cwd,
        environ=env,
        args=(),
        tick=0.0,
        quiet=2.0,
        stall=1000.0,
        max_starts=3,
        context_limit=800_000,
        usage_threshold=90.0,
        transcript_bytes=4096,
        ask_plan=ask,
    )


def _run_lane(tmp_path, repo, capsys, monkeypatch, entry: dict, name: str) -> wrapper.Wrapper:
    """spawn の環境でラッパーを組み、問いを出す子を 1 区間だけ流す。"""
    state = tmp_path / "relay-state"
    env = hub.spawn_env({"PATH": os.environ.get("PATH", "")}, state, name, entry)
    ask = lane_question.plan_asker(env, name, repo, tmp_path / "plugin", 5.0, _in_process(capsys))
    made: list[_AskingChild] = []

    def spawn(argv, child_env, cwd):
        made.append(_AskingChild(dict(child_env), cwd, monkeypatch))
        return made[-1]

    ticks = iter(range(1, 10_000))
    w = wrapper.Wrapper(_settings(state, repo, name, env, ask), spawn=spawn, clock=lambda: float(next(ticks)))
    try:
        w.run("最初の 1 行")
    finally:
        for c in made:
            c.close()
    return w


def _plan(tmp_path, capsys, *lanes) -> str:
    """計画を始め、`ndf lanes` が計画を引くリポジトリの識別を返す。"""
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(doc(*lanes)), encoding="utf-8")
    _lanes(capsys, "start", str(path))
    cwd = Path.cwd()
    return command.repo_id(cwd, command.store_root_of(cwd))


@pytest.mark.parametrize("after", [(), ("a",)], ids=["immediate", "after"])
def test_lane_question_reaches_the_plan_and_lanes_wait(repo, tmp_path, capsys, monkeypatch, after):
    lanes = [decl("a", [[1]]), decl("b", [[2]], touches=("src/b",), after=after)]
    repo_id = _plan(tmp_path, capsys, *lanes)
    name = launch.wrapper_name(repo_id, PLAN, "b")
    w = _run_lane(tmp_path, repo, capsys, monkeypatch, {"role": launch.ROLE, "plan": PLAN, "cwd": str(repo)}, name)
    assert not signals.has_question(w.dir)
    logged = [json.loads(line) for line in (w.dir / registry.LOG_FILE).read_text(encoding="utf-8").splitlines()]
    assert [r["ok"] for r in logged if r["kind"] == "plan_question"] == [True]
    got = _lanes(capsys, "wait", PLAN)
    asked = [i for i in got["items"] if i["kind"] == "request_opened"]
    assert len(asked) == 1
    body = asked[0]["payload"]["body"]
    assert (asked[0]["payload"]["lane"], body["kind"], body["questions"]) == ("b", plan.QUESTION_KIND, QUESTIONS)
    status = _lanes(capsys, "status", PLAN)
    assert [r["lane"] for r in status["metrics"]["open_requests"]] == ["b"]
    # オーガナイザーの答えで依頼が閉じる（受け入れ条件 3）
    _lanes(capsys, "request", "close", PLAN, asked[0]["row_key"], "--reason", "承認する")
    status = _lanes(capsys, "status", PLAN)
    assert status["metrics"]["open_requests"] == [] and status["metrics"]["closed_requests"][0]["closed"] == "承認する"


def test_relay_outside_a_plan_keeps_the_question_to_the_relay_only(repo, tmp_path, capsys, monkeypatch):
    # 計画のレーンでない中継（NDF_RELAY_PLAN が無い）は計画へ書かない（受け入れ条件 4）
    _plan(tmp_path, capsys, decl("a", [[1]]))
    env = hub.spawn_env({}, tmp_path / "relay-state", "t1", {"role": "lane", "cwd": str(repo)})
    assert "NDF_RELAY_PLAN" not in env
    assert lane_question.plan_asker(env, "t1", repo, tmp_path / "plugin", 5.0, _in_process(capsys)) is None
    w = _run_lane(tmp_path, repo, capsys, monkeypatch, {"role": "lane", "cwd": str(repo)}, "t1")
    logged = [json.loads(line) for line in (w.dir / registry.LOG_FILE).read_text(encoding="utf-8").splitlines()]
    assert [r for r in logged if r["kind"] == "plan_question"] == []
    assert _lanes(capsys, "status", PLAN)["metrics"]["open_requests"] == []
