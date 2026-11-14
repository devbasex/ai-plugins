"""Slack の通知と送信待ちの送り手（`ndf.adapters.notify`）。ネットワークへは出ず、`httpx.MockTransport` で答える。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
from filelock import FileLock, Timeout

from ndf import contracts
from ndf.adapters import notify
from ndf.contracts import limits

NOW = 1_800_000_000.0
# 上限の宣言の値。境と回数を宣言から引き、宣言を変えても境を試し続ける
STALE = limits.seconds("notify.stale")
ATTEMPTS = int(limits.value("notify.send", "attempts"))
INTERVAL = limits.value("notify.send", "interval")
FACTOR = limits.value("notify.send", "factor")


@pytest.fixture
def wall(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """`notify._wall` の代わり。`wall[0]` を書き換えて時刻を進める。"""
    t = [NOW]
    monkeypatch.setattr(notify, "_wall", lambda: t[0])
    return t


@pytest.fixture
def outbox(tmp_path: Path) -> Path:
    return tmp_path / "outbox"


def _write(outbox: Path, name: str, **over: object) -> Path:
    body = {"kind": "question", "key": name, "window": False, "text": f"本文 {name}", "cwd": str(outbox), "created_at": NOW}
    body.update(over)
    outbox.mkdir(parents=True, exist_ok=True)
    path = outbox / f"{name}.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


class _Deliver:
    """送った通知を控え、`fail` に並べた失敗を先頭から 1 回ずつ送出する。"""

    def __init__(self, *fail: notify.PostError, configured: bool = True) -> None:
        self.fail = list(fail)
        self.configured = configured
        self.calls: list[notify.Notice] = []

    def __call__(self, notice: notify.Notice) -> bool:
        self.calls.append(notice)
        if self.fail:
            raise self.fail.pop(0)
        return self.configured


def _settings(**over: str) -> notify.SlackSettings:
    return notify.SlackSettings(**{"token": "xoxb-t", "channel": "C1", "api_base": "https://slack.test", **over})


def _slack(answer: dict, seen: list[httpx.Request] | None = None) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(req)
        return httpx.Response(200, json=answer)

    return httpx.MockTransport(handler)


# ---- slack_call ----


def test_slack_call_posts_with_bearer_token() -> None:
    seen: list[httpx.Request] = []
    got = notify.slack_call("chat.postMessage", {"channel": "C1", "text": "x"}, _settings(), 5, _slack({"ok": True, "ts": "1"}, seen))
    assert got == {"ok": True, "ts": "1"}
    assert str(seen[0].url) == "https://slack.test/api/chat.postMessage"
    assert seen[0].headers["Authorization"] == "Bearer xoxb-t"


@pytest.mark.parametrize(
    ("answer", "reason", "retryable"),
    [
        ({"ok": False, "error": "invalid_auth"}, "Slack chat.postMessage: invalid_auth", False),
        ({"ok": False, "error": "ratelimited"}, "Slack chat.postMessage: ratelimited", True),
        ({"ts": "1"}, "Slack chat.postMessage: ok が偽", False),
    ],
    ids=["auth", "ratelimited", "no-ok"],
)
def test_slack_call_tells_api_errors_apart(answer: dict, reason: str, retryable: bool) -> None:
    with pytest.raises(notify.PostError) as got:
        notify.slack_call("chat.postMessage", {}, _settings(), 5, _slack(answer))
    assert (str(got.value), got.value.retryable) == (reason, retryable)


# ---- slack_settings ----


def test_settings_read_env_file_upward_and_environment_wins(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".env").write_text("SLACK_BOT_TOKEN=from-file\nSLACK_CHANNEL_ID=C-file\nSLACK_USER_MENTION=<@U1>\n", encoding="utf-8")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    got = notify.slack_settings(deep, None, {"SLACK_CHANNEL_ID": "C-env"})
    assert got == notify.SlackSettings(token="from-file", channel="C-env", mention="<@U1>", api_base=notify.SLACK_API_BASE)


def test_settings_stop_at_git_top_and_use_fallback(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SLACK_BOT_TOKEN=outside\nSLACK_CHANNEL_ID=C\n", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    assert notify.slack_settings(repo, None, {}) is None
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / ".env").write_text("SLACK_BOT_TOKEN=plugin\nSLACK_CHANNEL_ID=C2\nNDF_SLACK_API_BASE=https://slack.test/\n", encoding="utf-8")
    got = notify.slack_settings(repo, plugin, {})
    assert (got.token, got.channel, got.api_base) == ("plugin", "C2", "https://slack.test")


# ---- 送信待ちの 1 件 ----


@pytest.mark.parametrize("over", [{"kind": "other"}, {"key": ""}, {"extra": 1}], ids=["kind", "key", "extra"])
def test_read_notice_rejects_wrong_shape(outbox: Path, over: dict) -> None:
    with pytest.raises(notify.NoticeError, match="^形が違う"):
        notify.read_notice(_write(outbox, "a", **over))


def test_message_puts_mention_and_label_first(outbox: Path) -> None:
    notice = notify.read_notice(_write(outbox, "a", kind="approval", text="PR を見る"))
    assert notice.message("<@U1>") == "<@U1> 承認待ち: PR を見る"
    assert notice.message("") == "承認待ち: PR を見る"


@pytest.mark.parametrize(
    ("kind", "label"),
    [("question", "回答待ち"), ("approval", "承認待ち"), ("done", "完了")],
)
def test_each_kind_has_its_label(outbox: Path, kind: str, label: str) -> None:
    notice = notify.read_notice(_write(outbox, "a", kind=kind, text="本文"))
    assert notice.message("") == f"{label}: 本文"


def test_every_kind_the_schema_accepts_can_be_messaged(outbox: Path) -> None:
    kinds = contracts.load_json("schemas/notice.schema.json")["properties"]["kind"]["enum"]
    assert sorted(kinds) == ["approval", "done", "question"]
    for kind in kinds:
        assert notify.read_notice(_write(outbox, kind, kind=kind, text="x")).message("").endswith(": x")


def test_kind_labels_match_the_schema_kinds() -> None:
    schema = contracts.load_json(contracts.schema_path("notice"))
    assert set(notify.KIND_LABELS) == set(schema["properties"]["kind"]["enum"])


# ---- drain ----


def test_drain_sends_in_written_order_and_empties_outbox(outbox: Path, wall: list[float]) -> None:
    _write(outbox, "2")
    _write(outbox, "1")
    deliver = _Deliver()
    out = notify.drain(outbox, deliver, sleep=lambda s: None)
    assert [n.key for n in deliver.calls] == ["1", "2"]
    assert (out.sent, notify.pending(outbox)) == (2, [])


def test_drain_does_not_send_the_same_key_twice(outbox: Path, wall: list[float]) -> None:
    _write(outbox, "a")
    deliver = _Deliver()
    notify.drain(outbox, deliver)
    wall[0] += STALE - 1
    _write(outbox, "b", key="a", created_at=wall[0])
    out = notify.drain(outbox, deliver)
    assert (len(deliver.calls), out.duplicate) == (1, 1)


def test_drain_window_key_is_sent_again_after_window(outbox: Path, wall: list[float]) -> None:
    deliver = _Deliver()
    _write(outbox, "a", window=True)
    notify.drain(outbox, deliver)
    wall[0] += limits.seconds("notify.window") - 1
    _write(outbox, "b", key="a", window=True, created_at=wall[0])
    assert notify.drain(outbox, deliver).duplicate == 1
    wall[0] += 2
    _write(outbox, "c", key="a", window=True, created_at=wall[0])
    assert notify.drain(outbox, deliver).sent == 1
    assert len(deliver.calls) == 2


def test_drain_drops_stale_and_unconfigured(outbox: Path, wall: list[float]) -> None:
    _write(outbox, "old", created_at=NOW - (STALE + 1))
    _write(outbox, "new")
    deliver = _Deliver(configured=False)
    out = notify.drain(outbox, deliver)
    assert [n.key for n in deliver.calls] == ["new"]
    assert (out.stale, out.unconfigured, out.sent, notify.pending(outbox)) == (1, 1, 0, [])


def test_drain_retries_retryable_failures(outbox: Path, wall: list[float]) -> None:
    # 3377-8: 通りうる失敗は上限の宣言の間隔で打ち直す
    _write(outbox, "a")
    slept: list[float] = []
    deliver = _Deliver(notify.PostError("HTTP 500", True), notify.PostError("HTTP 429", True))
    out = notify.drain(outbox, deliver, sleep=slept.append)
    assert (out.sent, len(deliver.calls), slept) == (1, 3, [INTERVAL, INTERVAL * FACTOR])


def test_drain_keeps_notices_when_retries_run_out(outbox: Path, wall: list[float]) -> None:
    _write(outbox, "a")
    _write(outbox, "b")
    deliver = _Deliver(*[notify.PostError("接続できない（ConnectError）", True)] * ATTEMPTS)
    out = notify.drain(outbox, deliver, sleep=lambda s: None)
    assert (out.left, out.sent, len(deliver.calls)) == (2, 0, ATTEMPTS)
    assert [p.name for p in notify.pending(outbox)] == ["a.json", "b.json"]
    again = notify.drain(outbox, _Deliver())
    assert (again.sent, notify.pending(outbox)) == (2, [])


def test_drain_moves_permanent_failures_aside(outbox: Path, wall: list[float]) -> None:
    _write(outbox, "a")
    _write(outbox, "b", kind="other")
    _write(outbox, "c")
    deliver = _Deliver(notify.PostError("Slack chat.postMessage: invalid_auth", False))
    out = notify.drain(outbox, deliver)
    assert out.sent == 1
    assert out.failed[0] == "a.json: Slack chat.postMessage: invalid_auth"
    assert out.failed[1].startswith("b.json: 形が違う")
    assert sorted(p.name for p in (outbox / "unsent").iterdir()) == ["a.json", "b.json"]


def test_drain_sends_notices_written_while_sending(outbox: Path, wall: list[float]) -> None:
    _write(outbox, "a")

    def deliver(notice: notify.Notice) -> bool:
        if notice.key == "a":
            _write(outbox, "b")
        return True

    assert notify.drain(outbox, deliver).sent == 2


def test_only_one_sender_runs(outbox: Path, wall: list[float]) -> None:
    # 3377-8: 送り手が動いている間に起動した送り手は、何も送らずに終わる
    _write(outbox, "a")
    deliver = _Deliver()
    with FileLock(outbox / ".sender.lock"):
        out = notify.drain(outbox, deliver)
    assert (out.busy, deliver.calls, len(notify.pending(outbox))) == (True, [], 1)


def test_drain_does_not_read_temporary_names(outbox: Path, wall: list[float]) -> None:
    _write(outbox, ".writing")
    assert notify.drain(outbox, _Deliver()).sent == 0


class _Lock:
    """`notify.FileLock` の代わり。`script` の先頭から 1 回ずつ、取れる（"ok"）か取れない（"busy"）かを決める。

    排他を外すたびに `on_release` を呼び、外す間際に書かれた 1 件を作る。
    """

    def __init__(self, script: list[str], on_release=lambda n: None) -> None:
        self.script = script
        self.on_release = on_release
        self.acquired = 0

    def __call__(self, path: object) -> _Lock:
        return self

    def acquire(self, timeout: float) -> _Lock:
        if self.script.pop(0) == "busy":
            raise Timeout("lock")
        self.acquired += 1
        return self

    def __enter__(self) -> _Lock:
        return self

    def __exit__(self, *exc: object) -> None:
        self.on_release(self.acquired)


def test_drain_on_missing_outbox_creates_it_and_does_nothing(outbox: Path, wall: list[float]) -> None:
    deep = outbox / "a" / "b"
    out = notify.drain(deep, _Deliver())
    assert deep.is_dir()
    assert out == notify.Drained()


def test_drain_sends_notice_written_as_lock_is_released(outbox: Path, wall: list[float], monkeypatch: pytest.MonkeyPatch) -> None:
    _write(outbox, "a")
    lock = _Lock(["ok", "ok"], on_release=lambda n: n == 1 and _write(outbox, "b"))
    monkeypatch.setattr(notify, "FileLock", lock)
    deliver = _Deliver()
    out = notify.drain(outbox, deliver)
    assert [n.key for n in deliver.calls] == ["a", "b"]
    assert (out.sent, out.busy, lock.acquired, notify.pending(outbox)) == (2, False, 2, [])


def test_drain_leaves_late_notice_when_another_sender_took_the_lock(
    outbox: Path, wall: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 2 回目に排他を取れなかったときは busy にせず、それまでの結果を返す
    _write(outbox, "a")
    lock = _Lock(["ok", "busy"], on_release=lambda n: _write(outbox, "b"))
    monkeypatch.setattr(notify, "FileLock", lock)
    out = notify.drain(outbox, _Deliver())
    assert (out.sent, out.busy, lock.acquired) == (1, False, 1)
    assert [p.name for p in notify.pending(outbox)] == ["b.json"]


def test_drain_does_not_retake_lock_after_retries_run_out(outbox: Path, wall: list[float], monkeypatch: pytest.MonkeyPatch) -> None:
    _write(outbox, "a")
    lock = _Lock(["ok", "ok"])
    monkeypatch.setattr(notify, "FileLock", lock)
    out = notify.drain(outbox, _Deliver(*[notify.PostError("HTTP 503", True)] * ATTEMPTS), sleep=lambda s: None)
    assert (out.left, out.busy, lock.acquired) == (1, False, 1)
    assert [p.name for p in notify.pending(outbox)] == ["a.json"]


def test_drain_on_empty_outbox_takes_lock_once(outbox: Path, wall: list[float], monkeypatch: pytest.MonkeyPatch) -> None:
    lock = _Lock(["ok", "ok"])
    monkeypatch.setattr(notify, "FileLock", lock)
    assert (notify.drain(outbox, _Deliver()), lock.acquired) == (notify.Drained(), 1)


def test_busy_drain_reports_ok_without_sending(outbox: Path, wall: list[float]) -> None:
    _write(outbox, "a")
    with FileLock(outbox / ".sender.lock"):
        out = notify.drain(outbox, _Deliver())
    got = notify._report(out)
    assert (got["status"], got["metrics"]["sent"]) == ("ok", 0)


# ---- 送ったキーの記録と、送れなかった 1 件の保持 ----

KEEP = NOW - limits.seconds("notify.keep")


def _sent_record(outbox: Path) -> object:
    return json.loads((outbox / ".sent.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("record", ["{壊れた", "[1, 2]", '"a"'], ids=["broken", "list", "string"])
def test_unreadable_sent_record_counts_from_empty(outbox: Path, wall: list[float], record: str) -> None:
    _write(outbox, "a")
    (outbox / ".sent.json").write_text(record, encoding="utf-8")
    out = notify.drain(outbox, _Deliver())
    assert (out.sent, out.duplicate) == (1, 0)
    assert _sent_record(outbox) == {"a": NOW}


def test_sent_record_keeps_only_numbers_within_keep_days(outbox: Path, wall: list[float]) -> None:
    outbox.mkdir(parents=True)
    record = {"keep": KEEP, "old": KEEP - 1, "text": "x", "none": None}
    (outbox / ".sent.json").write_text(json.dumps(record), encoding="utf-8")
    _write(outbox, "1", key="keep")
    _write(outbox, "2", key="old")
    _write(outbox, "3", key="text")
    deliver = _Deliver()
    out = notify.drain(outbox, deliver)
    assert [n.key for n in deliver.calls] == ["old", "text"]
    assert (out.sent, out.duplicate) == (2, 1)
    assert _sent_record(outbox) == {"keep": KEEP, "old": NOW, "text": NOW}


def test_sent_record_is_not_written_when_nothing_is_sent(outbox: Path, wall: list[float]) -> None:
    _write(outbox, "a", created_at=NOW - (STALE + 1))
    notify.drain(outbox, _Deliver())
    assert not (outbox / ".sent.json").exists()


def test_unsent_older_than_keep_days_is_removed(outbox: Path, wall: list[float]) -> None:
    unsent = outbox / "unsent"
    unsent.mkdir(parents=True)
    for name, mtime in {"old.json": KEEP - 1, "edge.json": KEEP, "new.json": KEEP + 1}.items():
        (unsent / name).write_text("{}", encoding="utf-8")
        os.utime(unsent / name, (mtime, mtime))
    (unsent / "old.txt").write_text("", encoding="utf-8")
    os.utime(unsent / "old.txt", (KEEP - 1, KEEP - 1))
    notify.drain(outbox, _Deliver())
    assert sorted(p.name for p in unsent.iterdir()) == ["edge.json", "new.json", "old.txt"]


# ---- slack_deliver ----


def test_slack_deliver_posts_message_with_mention(outbox: Path, wall: list[float]) -> None:
    environ = {
        "SLACK_BOT_TOKEN": "t",
        "SLACK_CHANNEL_ID": "C1",
        "SLACK_USER_MENTION": "<@U1>",
        "NDF_SLACK_API_BASE": "https://slack.test",
    }
    seen: list[httpx.Request] = []
    _write(outbox, "a", kind="done", text="終えた")
    out = notify.drain(outbox, notify.slack_deliver(None, _slack({"ok": True}, seen), environ))
    assert out.sent == 1
    assert json.loads(seen[0].content) == {"channel": "C1", "text": "<@U1> 完了: 終えた"}


def test_slack_deliver_without_settings_sends_nothing(outbox: Path, wall: list[float]) -> None:
    (outbox / ".git").mkdir(parents=True)
    _write(outbox, "a")
    seen: list[httpx.Request] = []
    assert notify.drain(outbox, notify.slack_deliver(None, _slack({"ok": True}, seen), {})).unconfigured == 1
    assert seen == []


# ---- notify send ----


def test_send_command_reports_counts(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    assert notify.main(["send"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert (got["tool"], got["status"], got["metrics"]["sent"]) == ("notify", "ok", 0)


def test_send_command_rejects_other_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    assert notify.main(["push"]) != 0
    assert json.loads(capsys.readouterr().out)["cause"]["kind"] == "invalid"
