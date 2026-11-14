"""副命令 `ndf loop`: 記録の読み書き・断りの結果 JSON・ゲートの答えの先端。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

import pytest

from ndf.contracts import result
from ndf.domains.loop import cli as loop_cli
from ndf.domains.loop import record_decide as rec
from ndf.domains.loop import store


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    where = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(where)], check=True)
    return where


@pytest.fixture
def loop(repo: Path, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]) -> Callable[..., dict]:
    def run(*argv: str) -> dict[str, Any]:
        loop_cli.main(["--cwd", str(repo), *argv])
        return last_result(capsys.readouterr().out)

    return run


def _candidates(tmp_path: Path, rows: list[dict]) -> str:
    path = tmp_path / "candidates.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return str(path)


def _loop_argv(repo: Path, *args: str) -> list[str]:
    """次の 1 手の argv: 対象のリポジトリ（解決済みの `--cwd`）を引き継ぐ。"""
    return [result.ENTRY, "loop", "--cwd", os.path.realpath(repo), *args]


def test_lap_lifecycle_writes_events_and_next(loop: Callable[..., dict], tmp_path: Path, repo: Path) -> None:
    assert loop("start", "demo", "--session", "s1")["next"]["argv"] == _loop_argv(repo, "select", "demo", "--session", "s1")
    got = loop("select", "demo", "--session", "s1", "--candidates", _candidates(tmp_path, [{"issue": 4}, {"issue": 5}]), "--size", "1")
    assert got["status"] == "ok" and [i["issue"] for i in got["items"] if i["chosen"]] == [4]
    assert got["next"] == {"argv": _loop_argv(repo, "sprint", "demo", "--session", "s1"), "ask": ["mode", "basis"]}
    assert loop("sprint", "demo", "--session", "s1", "--mode", "standard", "--basis", "API を変える")["status"] == "ok"
    assert loop("created", "demo", "40")["status"] == "ok"
    closed = loop("lap", "demo", "--session", "s1", "--failed", "--version", "1.0.0")
    assert closed["metrics"]["outcome"] == rec.DONE
    view = loop("get", "demo", "--field", "laps")["items"][0]["laps"][0]
    assert (view["mode"], view["basis"], view["created"], view["released"]) == ("standard", "API を変える", [40], ["1.0.0"])
    events = store.open_store("demo", repo).read()
    assert [e["kind"] for e in events][-3:] == [rec.VERSION_SHIPPED, rec.LAP_CLOSED, rec.LEASE_TAKEN]


def test_refusal_is_stopped_with_kind_and_next(loop: Callable[..., dict], repo: Path) -> None:
    """断りは stopped の結果 JSON で、種類と次の 1 手を持つ（SF11）。"""
    loop("start", "demo", "--session", "s1")
    got = loop("start", "demo", "--session", "s2")
    assert got["status"] == "stopped" and got["cause"]["kind"] == "refused"
    assert got["next"]["argv"] == _loop_argv(repo, "get", "demo")
    nolap = loop("sprint", "demo", "--session", "s1", "--mode", "standard", "--basis", "x")
    assert nolap["cause"]["kind"] == "precondition" and nolap["next"]["argv"][4] == "select"


def test_next_of_get_targets_the_repo_and_asks_for_the_session(
    loop: Callable[..., dict], tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get は打ったセッションを知らないので、セッションの要る次の 1 手は session を打つ側に埋めさせる。

    次の 1 手は対象のリポジトリを --cwd で持ち、別のリポジトリで打っても対象の記録だけを書く。
    """
    loop("start", "demo", "--session", "s1")
    got = loop("get", "demo")
    assert got["next"] == {"argv": _loop_argv(repo, "select", "demo"), "ask": ["candidates", "session"]}
    other = tmp_path / "other"
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    monkeypatch.chdir(other)
    filled = [*got["next"]["argv"][2:], "--candidates", _candidates(tmp_path, [{"issue": 7}]), "--session", "s1"]
    assert loop_cli.main(filled) == 0
    assert store.open_store("demo", repo).read()[-1]["kind"] == rec.LAP_OPENED
    assert store.open_store("demo", other).read() == []


def test_issue_2714_answer_reads_the_head_once(loop: Callable[..., dict], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    heads = iter(["a" * 40, "a" * 40, "c" * 40])
    calls: list[int] = []

    def head_of(cwd: Path, pr: int) -> str:
        calls.append(pr)
        return next(heads)

    monkeypatch.setattr(loop_cli, "head_of", head_of)
    loop("start", "demo", "--session", "s1")
    loop("select", "demo", "--session", "s1", "--candidates", _candidates(tmp_path, [{"issue": 1}]))
    loop("gate", "demo", "--pr", "9")
    ok = loop("answer", "demo", "--pr", "9", "--answer", "revise", "--note", "直す")
    assert ok["metrics"]["head"] == "a" * 40 and len(calls) == 2
    moved = loop("answer", "demo", "--pr", "9", "--answer", "approved")
    assert moved["cause"]["kind"] == "precondition" and len(calls) == 3
    assert loop("get", "demo", "--field", "rejections")["items"][0]["rejections"][0]["note"] == "直す"


def test_issue_2435_stop_now_is_read_as_stopped(loop: Callable[..., dict], tmp_path: Path) -> None:
    loop("start", "demo", "--session", "s1")
    loop("select", "demo", "--session", "s1", "--candidates", _candidates(tmp_path, [{"issue": 1}]))
    assert loop("stop", "demo", "--by", "human", "--now")["status"] == "ok"
    got = loop("get", "demo")
    assert got["metrics"]["status"] == rec.HALTED and got["items"][0]["stop"]["reason"] == rec.USER_STOP
    assert "next" not in got


def test_broken_record_is_invalid(loop: Callable[..., dict], repo: Path) -> None:
    where = store.open_store("demo", repo)
    where.directory.mkdir(parents=True)
    where.events_path.write_text("{not json\n", encoding="utf-8")
    got = loop("get", "demo")
    assert got["status"] == "stopped" and got["cause"]["kind"] == "invalid"
    assert loop("get", "../x")["cause"]["kind"] == "invalid"


def test_close_plan_and_review_args(loop: Callable[..., dict]) -> None:
    plan = loop("close-plan", "--pace", "normal", "--route", "merged-by-check", "--mvv", "--record-pr", "0")
    assert plan["metrics"] == {"record_pr": "0", "verify": False, "pace": "normal"}
    review = loop("review-args", "--purpose", "code", "--pr", "12", "--state-dir", "/s", "--base", "develop")
    assert review["items"][0]["review"] == [
        result.ENTRY,
        "cross-review",
        "run",
        "--pr",
        "12",
        "--state-dir",
        "/s",
        "--base",
        "develop",
        "--purpose",
        "code",
    ]
    assert loop("close-plan", "--pace", "slow", "--route", "none")["cause"]["kind"] == "invalid"
