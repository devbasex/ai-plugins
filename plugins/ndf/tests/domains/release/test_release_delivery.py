"""バージョンの無いリリース記録の確かめ 8: 届け先ごとの反映の確かめ（#4580）。

GitHub は偽物、HTTP は `httpx.MockTransport`、子のプロセスは偽の `run`、時刻は偽の `now`、待機は偽の `sleep` で、
子のプロセスを起動せず本物の時間を待たない。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

from ndf.domains.release import delivery_confirm, record_decide

from ._fakes import FakeHub, section

AFTER = "c3" + "0" * 38
WEB, WORKER = "web", "worker"
OBSERVE = {"url": "https://example.invalid/version", "pointer": "/build/commit"}
SUCCESS = {"status": "completed", "conclusion": "success"}
FAILURE = {"status": "completed", "conclusion": "failure"}
RUNNING = {"status": "in_progress", "conclusion": None}


class FakeGit:
    """`git cat-file --batch` の応答（名前 → 中身。無い名前は missing）を返す偽の `Git`。"""

    def __init__(self, objects: dict[str, bytes], cwd: Path | None = None) -> None:
        self.objects = objects
        self.cwd = cwd
        self.calls: list[tuple[str, ...]] = []

    def run_bytes(self, *args: str, stdin: bytes | None = None, codes: Any = (0,)) -> bytes:
        self.calls.append(args)
        out = b""
        for name in (stdin or b"").decode().splitlines():
            if name in self.objects:
                body = self.objects[name]
                out += f"{'0' * 40} blob {len(body)}\n".encode() + body + b"\n"
            else:
                out += f"{name} missing\n".encode()
        return out


def _project(deliveries: list[dict] | None) -> bytes:
    got = section("web-develop")
    if deliveries is not None:
        got["release"]["deliveries"] = deliveries
    return json.dumps({"version": 1, "branch_strategy": got}).encode()


def _row(name: str, **more: Any) -> dict:
    return {"target": name, "kind": "auto", "trigger": "deploy", "versioned": False, **more}


def _git(project: bytes | None) -> FakeGit:
    objects = {f"{AFTER}^{{commit}}": b"tree"}
    if project is not None:
        objects[f"{AFTER}:.ndf/project.json"] = project
    return FakeGit(objects)


def test_deliveries_at_reads_the_push_time_setting_once_and_classifies_it() -> None:
    rows = [_row(WEB, result={"workflow": "deploy.yml"}, paths=["web/**"], observe=OBSERVE), _row(WORKER)]
    repo = _git(_project(rows))
    got = delivery_confirm.deliveries_at(repo, AFTER)
    assert got.kind == delivery_confirm.FOUND and [d["target"] for d in got.deliveries] == [WEB, WORKER] and not got.no_command
    assert repo.calls == [("cat-file", "--batch")]
    assert delivery_confirm.deliveries_at(FakeGit({}), AFTER).kind == delivery_confirm.MISSING_REF
    assert delivery_confirm.deliveries_at(_git(None), AFTER).kind == delivery_confirm.ABSENT
    assert delivery_confirm.deliveries_at(_git(_project(None)), AFTER).kind == delivery_confirm.ABSENT
    broken = delivery_confirm.deliveries_at(_git(b"{"), AFTER)
    assert broken.kind == delivery_confirm.BROKEN and "JSON" in broken.reason
    shape = delivery_confirm.deliveries_at(_git(_project([_row(WEB, paths="web/**")])), AFTER)
    assert shape.kind == delivery_confirm.BROKEN and "/release/deliveries/0/paths" in shape.reason


def test_deliveries_at_reads_through_reserved_env_names_and_marks_the_delivery() -> None:
    """予約した名前（GH_TOKEN など）だけの誤りは止まらずに読み、その届け先をコマンドを実行しない届け先にする。"""
    rows = [
        _row(WEB, result={"command": "./status {revision}", "env": ["AWS_PROFILE", "GH_TOKEN"]}),
        _row(WORKER, result={"env": ["GIT_DIR"]}),
    ]
    got = delivery_confirm.deliveries_at(_git(_project(rows)), AFTER)
    assert got.kind == delivery_confirm.RESERVED and got.no_command == {WEB, WORKER}
    mixed = rows + [_row("db", paths=[""])]
    assert delivery_confirm.deliveries_at(_git(_project(mixed)), AFTER).kind == delivery_confirm.BROKEN


class FakeRun:
    """偽の `subprocess.run`。呼ばれた引数を残し、終了コード（か例外）を順に返す。"""

    def __init__(self, *codes: int | Exception) -> None:
        self.codes = list(codes)
        self.calls: list[tuple[Any, dict]] = []

    def __call__(self, args: Any, **kw: Any) -> subprocess.CompletedProcess:
        self.calls.append((args, kw))
        code = self.codes.pop(0) if len(self.codes) > 1 else self.codes[0]
        if isinstance(code, Exception):
            raise code
        return subprocess.CompletedProcess(args, code, "", "")


class Clock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def _reach(
    hub: FakeHub | None = None, run: FakeRun | None = None, clock: Clock | None = None, handler: Any = None
) -> delivery_confirm.Reach:
    clock = clock or Clock()
    transport = httpx.MockTransport(handler) if handler is not None else None
    return delivery_confirm.Reach(hub or FakeHub(), "main", Path("/tmp/tree"), clock.now, clock.sleep, run or FakeRun(0), transport)


@pytest.mark.parametrize(
    ("code", "status", "conclusion"),
    [(0, "completed", "success"), (1, "completed", "failure"), (75, "in_progress", None), (2, "unknown", None)],
)
def test_command_result_maps_exit_codes_of_the_contract(monkeypatch, code: int, status: str, conclusion: str | None) -> None:
    monkeypatch.setenv("AWS_PROFILE", "ro")
    monkeypatch.setenv("GH_TOKEN", "never-passed")
    monkeypatch.setenv("PATH", "/bin")
    run, clock = FakeRun(code), Clock()
    got = delivery_confirm.command_result(_reach(run=run, clock=clock), "./status {revision}", ["AWS_PROFILE"], AFTER, clock.t + 40)
    assert dict(got) == {"status": status, "conclusion": conclusion}
    args, kw = run.calls[0]
    assert args == f"./status {AFTER}" and kw["cwd"] == Path("/tmp/tree") and kw["timeout"] == 40 and kw["shell"] is True
    assert kw["env"] == {"PATH": "/bin", "AWS_PROFILE": "ro"} or set(kw["env"]) <= {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TZ",
        "TMPDIR",
        "AWS_PROFILE",
    }
    assert "GH_TOKEN" not in kw["env"]


def test_command_result_does_not_run_past_the_deadline_and_treats_a_timeout_as_unsettled() -> None:
    run, clock = FakeRun(0), Clock()
    assert delivery_confirm.command_result(_reach(run=run, clock=clock), "./s", [], AFTER, clock.t) == delivery_confirm.UNSETTLED
    assert run.calls == []
    run = FakeRun(subprocess.TimeoutExpired("./s", 3))
    assert delivery_confirm.command_result(_reach(run=run, clock=clock), "./s", [], AFTER, clock.t + 3) == delivery_confirm.UNSETTLED


def test_checked_out_adds_and_removes_a_worktree_without_hooks(monkeypatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "never-passed")
    run = FakeRun(0)
    with delivery_confirm.after_tree(_reach(run=run), FakeGit({}, Path("/repo")), AFTER) as path:
        assert path.name == "tree"
    assert not path.parent.exists()
    (add, add_kw), (remove, _remove_kw) = run.calls
    assert add[:6] == ["git", "-c", "core.hooksPath=/dev/null", "worktree", "add", "--detach"] and add[-1] == AFTER
    assert remove[3:6] == ["worktree", "remove", "--force"] and add_kw["cwd"] == Path("/repo") and "GH_TOKEN" not in add_kw["env"]


CD = _row(WEB, result={"workflow": "deploy.yml"}, observe=OBSERVE)


def test_confirm_delivery_writes_cd_when_the_push_run_succeeds() -> None:
    hub = FakeHub(cd_runs=[[{**SUCCESS, "id": 9}]])
    got = delivery_confirm.confirm_delivery(_reach(hub), CD, AFTER, observed=False, wait=True, runnable=True)
    assert (got.state, got.kind) == ("ok", "cd") and hub.run_queries == [("deploy.yml", "main", AFTER, 1800.0)]


def test_confirm_delivery_tries_the_live_check_at_once_while_cd_runs() -> None:
    """CD が未完了で --observed があれば、待たずに実環境の確認を 1 回試す。pointer の値が after なら observed。"""
    hub = FakeHub(cd_runs=[[RUNNING]])
    clock = Clock()
    got = delivery_confirm.confirm_delivery(
        _reach(hub, clock=clock, handler=lambda r: httpx.Response(200, json={"build": {"commit": AFTER}})), CD, AFTER, True, True, True
    )
    assert (got.state, got.kind, got.evidence) == ("ok", "observed", record_decide.Evidence(OBSERVE["url"], AFTER)) and clock.slept == []


def test_confirm_delivery_does_not_wait_with_several_environments() -> None:
    hub = FakeHub(cd_runs=[[RUNNING]])
    got = delivery_confirm.confirm_delivery(_reach(hub), CD, AFTER, observed=False, wait=False, runnable=True)
    assert got.state == "pending" and got.reason and len(hub.run_queries) == 1


def test_confirm_delivery_waits_until_cd_settles() -> None:
    hub = FakeHub(cd_runs=[[], [RUNNING], [FAILURE]])
    clock = Clock()
    got = delivery_confirm.confirm_delivery(_reach(hub, clock=clock), CD, AFTER, observed=False, wait=True, runnable=True)
    assert got.state == "failed" and clock.slept == [30, 30] and len(hub.run_queries) == 3


def test_confirm_delivery_keeps_one_deadline_and_cuts_the_last_wait() -> None:
    """期限は 1 回目の照会の直前に 1 回だけ決め、照会・実環境の確認には残り時間を、待機には間隔と残り時間の短い方を渡す。"""
    hub = FakeHub(cd_runs=[[RUNNING]])
    clock = Clock()
    seen: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"build": {"commit": "b9" + "0" * 38}})

    delivery = {**CD, "result": {"workflow": "deploy.yml", "timeout_seconds": 50}}
    got = delivery_confirm.confirm_delivery(_reach(hub, clock=clock, handler=handler), delivery, AFTER, True, True, True)
    assert got.state == "pending" and "待ちの上限" in got.reason
    assert [q[3] for q in hub.run_queries] == [50, 20] and seen == [50, 20] and clock.slept == [30, 20]


def test_confirm_delivery_runs_the_command_and_skips_it_for_reserved_env() -> None:
    command = _row(WEB, result={"command": "./status {revision}"})
    run = FakeRun(75, 0)
    clock = Clock()
    got = delivery_confirm.confirm_delivery(_reach(run=run, clock=clock), command, AFTER, observed=False, wait=True, runnable=True)
    assert (got.state, got.kind) == ("ok", "cd") and len(run.calls) == 2 and clock.slept == [30]
    run = FakeRun(1)
    assert delivery_confirm.confirm_delivery(_reach(run=run), command, AFTER, False, True, True).state == "failed"
    run = FakeRun(0)
    got = delivery_confirm.confirm_delivery(_reach(run=run), command, AFTER, observed=False, wait=True, runnable=False)
    assert got.state == "pending" and run.calls == []


def test_confirm_delivery_attests_a_delivery_without_a_result() -> None:
    hub = FakeHub()
    got = delivery_confirm.confirm_delivery(_reach(hub), _row(WEB, kind="manual"), AFTER, observed=False, wait=True, runnable=True)
    assert (got.state, got.kind) == ("ok", "attested") and hub.run_queries == []


def test_observe_treats_unreadable_answers_as_unconfirmed() -> None:
    reach = _reach(handler=lambda r: httpx.Response(200, text="not json"))
    assert delivery_confirm.live_value(reach, CD, 5)[0] is record_decide.MISSING
    reach = _reach(handler=lambda r: httpx.Response(500))
    assert delivery_confirm.live_value(reach, CD, 5) == (record_decide.MISSING, OBSERVE["url"])
    assert delivery_confirm.live_value(reach, _row(WEB), 5)[0] is record_decide.MISSING
