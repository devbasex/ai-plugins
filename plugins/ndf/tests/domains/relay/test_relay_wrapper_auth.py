"""認証の失敗の確かめと外した間（U10。#3604 #3864 の決定 6・判断 13、受け入れ条件 #3864 の 6・7、I8）。

- 画面の行（`observe.AUTH_FAILED`）は引き金だけ。会話の記録の会話の行の最後が資格情報の失敗の API の失敗の記録で、
  セグメントの起動より後のときだけ認証の失敗と確かめる
- 確かめたら今の登録済みアカウントを `relay.auth-hold` 秒外し、替え先で同じ会話を `--resume` で続ける
- 替え先が無ければ子を残し（stalled）、ログインの形ごとの直し方を同じセグメントで 1 回出す
画面の行は 2026-11-22 に Claude Code 2.1.330 の端末で取った出力（値は伏せた）。値はすべて偽の値で、実のアカウントを使わない。
"""

from __future__ import annotations

import io
import json
import os
import time
from datetime import datetime, timezone

import pytest
from ndf.contracts import limits
from ndf.domains.relay import account_dir, accounts, observe, switch_decide, wrapper, wrapper_auth
from relay_children import LIMIT_LINE, FakeChild, Spawner, clock_ticks, log_rows, make_settings, put_usage, register, rows_of

# 端末に出た行（不正な API キー・OAuth の失敗・U1 の本物の状態の本文）
API_KEY_SCREEN = (
    "401 invalid x-api-key · retrying in 3s · attempt 3/10\r\n"
    "401 invalid x-api-key · retrying in 9s · attempt 10/10\r\n"
    "  ⎿  Invalid API key · Fix external API key\r\n"
    "❯ Once fixed, resend your message\r\n"
)
OAUTH_SCREEN = "● Login expired · Please run /login\r\n❯ Once fixed, resend your message\r\n  Not logged in · run /login\r\n"
FAILED_SCREEN = "  ⎿  Failed to authenticate: OAuth session expired and could not be refreshed\r\n"


class Hooked(FakeChild):
    """起動されたとき `on_start(session_id)` を呼ぶ偽の子（会話の記録を起動の後に置く）。"""

    def __init__(self, outputs, exit_code, on_start=None):
        super().__init__(outputs, exit_code)
        self.on_start = on_start

    def started(self, directory, session_id, launch):
        if self.on_start is not None:
            self.on_start(session_id)


def _iso(at: float) -> str:
    return datetime.fromtimestamp(at, timezone.utc).isoformat()


def put_rows(tmp_path, session_id: str, rows: list[dict]) -> None:
    """会話の記録を置き、最後に会話でない行（`last-prompt`）を足す（失敗の記録の後に続く実測の形）。"""
    path = observe.transcript_path(make_settings(tmp_path).environ, tmp_path, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [*rows, {"type": "last-prompt", "lastPrompt": "x"}]
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def prompt_row(at: float) -> dict:
    return {"type": "user", "timestamp": _iso(at), "message": {"role": "user", "content": "続けて"}}


def failure_row(error: str, status, at: float, text: str = "Failed to authenticate") -> dict:
    """claude が書く API の失敗の記録（`isApiErrorMessage`・`error`・`apiErrorStatus` は最上位の欄）。"""
    row = {
        "type": "assistant",
        "timestamp": _iso(at),
        "isApiErrorMessage": True,
        "error": error,
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }
    return row if status is None else {**row, "apiErrorStatus": status}


def reply_row(text: str, at: float) -> dict:
    message = {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}
    return {"type": "assistant", "timestamp": _iso(at), "message": message}


RETRY_401 = {"type": "system", "subtype": "api_error", "error": {"status": 401, "message": "authentication_error"}}


def _run(tmp_path, children, prepare=None, markers=None):
    s = make_settings(tmp_path)
    spawn = Spawner(children, markers)
    out = io.BytesIO()
    w = wrapper.Wrapper(s, spawn=spawn, clock=clock_ticks(), out=out)
    if prepare:
        prepare(s.state_dir)
    try:
        ended = w.run("最初の 1 行")
    finally:
        for c in spawn.made + spawn.children:
            c.close()
    return ended, spawn, w, out.getvalue().decode("utf-8", errors="replace")


def _envs(spawn) -> list:
    return [env.get("NDF_CLAUDE_ACCOUNT") for _, env in spawn.calls]


def _held(tmp_path) -> dict:
    return accounts.held(tmp_path / "state", time.time(), {"work1": "g1", "work2": "g1"})


def _failing(tmp_path, screen: str, rows_at, names=("work1", "work2"), pin="work1", exit_code=None, tail=()):
    """`pin` で動く子の画面に `screen` を出し、起動の後に `rows_at(今)` の会話の記録を置く。"""

    def prepare(state):
        if names:
            register(tmp_path, *names)
        if pin is not None:
            accounts.use(state, pin)

    def on_start(session_id):
        put_rows(tmp_path, session_id, rows_at(time.time() + 1))

    first = Hooked([screen.encode(), *tail], exit_code, on_start)
    return first, prepare


@pytest.mark.parametrize(
    "screen, error, status",
    [
        (OAUTH_SCREEN, "authentication_failed", None),  # OAuth の失敗（apiErrorStatus が無い）
        (API_KEY_SCREEN, "authentication_failed", 401),  # 不正な API キー（打ち直しの後）
        (FAILED_SCREEN, "authentication_failed", None),  # U1 の本物の状態の本文
        (API_KEY_SCREEN, "unknown", 401),  # apiErrorStatus が 401 だけ
    ],
    ids=["oauth", "api-key", "failed-to-authenticate", "status-401-only"],
)
def test_3864_6_i8_an_auth_failure_holds_work1_and_resumes_on_work2(tmp_path, screen, error, status):
    # 受け入れ条件 #3864 の 6・I8: 画面の引き金から記録の確かめ・hold・work2 の起動まで通る
    first, prepare = _failing(tmp_path, screen, lambda at: [prompt_row(at), RETRY_401, failure_row(error, status, at)])
    began = time.time()
    ended, spawn, w, _ = _run(tmp_path, [first, FakeChild([], 0)], prepare)
    (one, _), (two, _) = spawn.calls
    assert _envs(spawn) == ["work1", "work2"] and ended.cause == ""
    assert two[:3] == [wrapper.CLAUDE, "--resume", one[2]] and two[-1] == switch_decide.AUTH_RESUME_PROMPT
    until = _held(tmp_path)["work1"]
    assert began + limits.seconds("relay.auth-hold") <= until <= time.time() + limits.seconds("relay.auth-hold")
    assert [(r["from"], r["account"], r["reason"]) for r in rows_of(w, "account") if "from" in r][-1] == ("work1", "work2", "auth")


def test_i8_a_held_account_is_neither_the_target_nor_the_pin(tmp_path):
    # I8: 外した間の work1 は、指定のままでも次のカットポイントで選ばれない
    first, prepare = _failing(tmp_path, OAUTH_SCREEN, lambda at: [prompt_row(at), failure_row("authentication_failed", None, at)])
    children = [first, FakeChild([b"working\r\n"], None), FakeChild([], 0)]
    ended, spawn, _, _ = _run(tmp_path, children, prepare, markers={1: "/goal 次"})
    assert _envs(spawn) == ["work1", "work2", "work2"] and ended.cause == ""
    assert "work1" in _held(tmp_path)


def test_3864_6_a_default_login_auth_failure_moves_to_a_registered_account(tmp_path):
    # 既定のログインの認証の失敗も、登録済みアカウントへ替える（既定のログインは外した間に記録しない）
    first, prepare = _failing(
        tmp_path, OAUTH_SCREEN, lambda at: [prompt_row(at), failure_row("authentication_failed", None, at)], names=("work2",), pin=None
    )
    ended, spawn, w, _ = _run(tmp_path, [first, FakeChild([], 0)], prepare)
    assert _envs(spawn) == [None, "work2"] and ended.cause == "" and _held(tmp_path) == {}
    # 理由の文は名前の代わりに「既定のログイン」と書く（log.jsonl に None を出さない）
    assert any(r["reason"].startswith("既定のログイン の認証に失敗した。work2") for r in rows_of(w, "account") if "reason" in r)
    assert not any("None" in (r.get("reason") or "") for r in log_rows(w))


@pytest.mark.parametrize(
    "rows_at",
    [
        lambda at: [prompt_row(at), failure_row("permission_error", 403, at)],  # 権限の制限
        lambda at: [prompt_row(at), RETRY_401, failure_row("permission_error", 403, at)],  # 打ち直しの 401 は読まない
        lambda at: [prompt_row(at), failure_row("rate_limit", 429, at)],
        lambda at: [prompt_row(at), reply_row("Invalid API key と出たら /login する", at)],  # モデルの応答の引用
        lambda at: [prompt_row(at - 120), failure_row("authentication_failed", None, at - 60)],  # 前のセグメントの失敗
        lambda at: [failure_row("authentication_failed", None, at), prompt_row(at)],  # 後ろに利用者の記録
        lambda at: [],  # 記録が読めない
    ],
    ids=["403", "retry-401-then-403", "429", "quote", "older", "user-after", "no-record"],
)
def test_3864_6_the_screen_alone_does_not_hold_or_switch(tmp_path, rows_at):
    # 画面の文字列だけでは替えず、外した間も stalled も書かない
    first, prepare = _failing(tmp_path, FAILED_SCREEN, rows_at, exit_code=0, tail=[b"", b"", b""])
    ended, spawn, w, _ = _run(tmp_path, [first], prepare)
    assert _envs(spawn) == ["work1"] and "書かずに終わった" in ended.reason
    assert _held(tmp_path) == {} and rows_of(w, "stalled") == []


@pytest.mark.parametrize(
    "names, pin, fix, who",
    [
        (("work1",), "work1", "この子を /exit で終えてから ndf relay account add work1 で登録し直す", "work1"),
        ((), None, "claude の中で /login を打つ", "既定のログイン"),
    ],
    ids=["registered", "default-login"],
)
def test_3864_7_without_a_target_the_child_stays_and_the_fix_is_shown_once(tmp_path, monkeypatch, names, pin, fix, who):
    # 受け入れ条件 #3864 の 7: 替え先が無ければ子を残し（stalled）、ログインの形ごとの直し方を同じセグメントで 1 回だけ出す
    rows = lambda at: [prompt_row(at), failure_row("authentication_failed", None, at)]  # noqa: E731
    reads = []
    real = observe.credential_failure
    monkeypatch.setattr(observe, "credential_failure", lambda *a: reads.append(a) or real(*a))
    first, prepare = _failing(tmp_path, OAUTH_SCREEN, rows, names=names, pin=pin, exit_code=0, tail=[b""] * 6)
    ended, spawn, w, shown = _run(tmp_path, [first], prepare)
    assert _envs(spawn) == [pin] and ended.cause == "" and rows_of(w, "stalled")
    assert shown.count(fix) == 1
    # 保存される理由は今のログインの形の名で書く（既定のログインで None を出さない）
    assert all(r["reason"].startswith(f"{who} の認証に失敗し、替える先が無い") for r in rows_of(w, "stalled"))
    # 待つ間、会話の記録が変わらなければ見回りごとに読み直さない
    assert len(reads) == 1


def test_3864_7_while_stalled_a_new_account_is_still_picked_up(tmp_path):
    # 替え先が無く待つ間に登録された work2 は、会話の記録が変わらなくても次の見回りで替え先になる
    rows = lambda at: [prompt_row(at), failure_row("authentication_failed", None, at)]  # noqa: E731

    class Later(FakeChild):
        polls = 0

        def isalive(self):
            self.polls += 1
            if self.polls == 4:
                register(tmp_path, "work1", "work2")
            return super().isalive()

    first, prepare = _failing(tmp_path, OAUTH_SCREEN, rows, names=("work1",))
    child = Later([OAUTH_SCREEN.encode(), *[b""] * 8], None)
    child.on_start = first.on_start
    child.started = first.started.__func__.__get__(child)
    first.close()
    ended, spawn, w, _ = _run(tmp_path, [child, FakeChild([], 0)], prepare)
    assert rows_of(w, "stalled") and _envs(spawn) == ["work1", "work2"] and ended.cause == ""


def test_3864_6_a_hold_that_cannot_take_the_lock_still_moves_to_the_target(tmp_path, monkeypatch):
    # 錠を取れず外した間を書けなくても、log.jsonl に 1 行残して今のアカウントを除いた替え先で続ける
    def hold(*a):
        raise TimeoutError("記録の錠を取れない: held.json")

    monkeypatch.setattr(accounts, "hold", hold)
    first, prepare = _failing(tmp_path, OAUTH_SCREEN, lambda at: [prompt_row(at), failure_row("authentication_failed", None, at)])
    ended, spawn, w, _ = _run(tmp_path, [first, FakeChild([], 0)], prepare)
    assert _envs(spawn) == ["work1", "work2"] and ended.cause == ""
    assert [(r["account"], "錠を取れない" in r["reason"]) for r in rows_of(w, "auth_hold")] == [("work1", True)]


def test_3864_6_an_auth_failure_at_full_usage_is_held_while_the_limit_picks(tmp_path):
    # 会話の末尾が認証の失敗で使用率も 100% なら、上限の手が替え先を選んでも work1 を外した間に記録する
    rows = lambda at: [prompt_row(at), failure_row("authentication_failed", None, at)]  # noqa: E731
    first, prepare = _failing(tmp_path, "", rows, tail=[b""] * 6)
    put_rows_after = first.on_start

    def on_start(session_id):  # 起動の後に使い切り、最初の見回りで上限の行と認証の失敗の行を同じ画面に出す
        put_rows_after(session_id)
        put_usage(tmp_path, "work1", 100.0)
        os.write(first.write_fd, LIMIT_LINE + OAUTH_SCREEN.encode())

    first.on_start = on_start
    ended, spawn, w, _ = _run(tmp_path, [first, FakeChild([], 0)], prepare)
    assert _envs(spawn) == ["work1", "work2"] and ended.cause == ""
    assert [r["reason"] for r in rows_of(w, "account") if "from" in r][-1] == "limit"
    assert "work1" in _held(tmp_path)


def test_3864_7_the_fix_line_follows_the_login_form():
    # 既定のログイン・登録済みアカウント・従量の接続で文が違う
    lines = {name: wrapper_auth.auth_fix(name) for name in (None, "work1", "metered")}
    assert "claude の中で /login を打つ" in lines[None]
    assert "ndf relay account add work1" in lines["work1"]
    assert "ndf relay account check metered" in lines["metered"]
    assert len(set(lines.values())) == 3


@pytest.mark.parametrize("screen", [OAUTH_SCREEN, LIMIT_LINE.decode() + OAUTH_SCREEN], ids=["auth", "limit-and-auth"])
def test_3864_6_when_the_target_cannot_be_prepared_it_stops_without_the_failed_account(tmp_path, monkeypatch, screen):
    # 替え先 work2 が事前の確かめを通った後の用意で落ちれば、認証に失敗した work1 へも既定のログインへも落とさず
    # account_unavailable で止まる（上限の手が選んだ替え先でも同じ）
    monkeypatch.setattr(accounts, "limit_until", lambda line, windows, seen_at, tz: (seen_at - 1, None, None))
    attached = []
    real = account_dir.attach

    def attach(st, n, *a):
        attached.append(n)
        return real(st, n, *a) if len(attached) == 1 else "lock_timeout"

    monkeypatch.setattr(account_dir, "attach", attach)
    first, prepare = _failing(tmp_path, screen, lambda at: [prompt_row(at), failure_row("authentication_failed", None, at)])
    ended, spawn, _, _ = _run(tmp_path, [first, FakeChild([], 0)], prepare)
    assert attached == ["work1", "work2"] and ended.cause == "account_unavailable"
    assert _envs(spawn) == ["work1", "work2"]  # work2 は関所の子で落ちた。3 回目の起動は無い


def test_3864_6_a_lifted_limit_does_not_restart_the_account_whose_auth_failed(tmp_path, monkeypatch):
    # 上限と認証の失敗を同じ見回りで確かめ、外した間を書けず上限がすぐ解けても、上限の手は work1 で再開しない（指定も先頭へ戻さない）
    def hold(*a):
        raise OSError("書けない: held.json")

    monkeypatch.setattr(accounts, "hold", hold)
    monkeypatch.setattr(accounts, "limit_until", lambda line, windows, seen_at, tz: (seen_at - 1, None, None))
    rows = lambda at: [prompt_row(at), failure_row("authentication_failed", None, at)]  # noqa: E731
    first, prepare = _failing(tmp_path, LIMIT_LINE.decode() + OAUTH_SCREEN, rows)
    ended, spawn, w, _ = _run(tmp_path, [first, FakeChild([], 0)], prepare)
    assert _envs(spawn) == ["work1", "work2"] and ended.cause == ""
    assert [r["reason"] for r in rows_of(w, "account") if "from" in r][-1] == "limit"


@pytest.mark.parametrize("lifted", [True, False], ids=["limit-lifted", "limit-active"])
def test_3864_7_a_limit_with_an_auth_failure_and_no_target_shows_the_auth_fix(tmp_path, monkeypatch, lifted):
    # 上限と認証の失敗が同じ見回りで確定し替え先が無ければ、リセットを待つ（wait_reset）と出し続けず、認証の stalled と直し方の 1 行を出す
    if lifted:
        monkeypatch.setattr(accounts, "limit_until", lambda line, windows, seen_at, tz: (seen_at - 1, None, None))
    rows = lambda at: [prompt_row(at), failure_row("authentication_failed", None, at)]  # noqa: E731
    first, prepare = _failing(tmp_path, LIMIT_LINE.decode() + OAUTH_SCREEN, rows, names=("work1",), exit_code=0, tail=[b""] * 6)
    ended, spawn, w, shown = _run(tmp_path, [first], prepare)
    assert _envs(spawn) == ["work1"] and ended.cause == ""
    assert rows_of(w, "stalled") and not rows_of(w, "wait_reset")
    assert shown.count("ndf relay account add work1 で登録し直す") == 1


def test_3864_6_an_unreadable_record_is_read_again_after_the_permission_is_fixed(tmp_path, monkeypatch):
    # 会話の記録を開けなかった見回りの結果を残さず、権限を直した後の見回りで確かめ直して work2 へ替える（スタンプは変わらない）
    rows = lambda at: [prompt_row(at), failure_row("authentication_failed", None, at)]  # noqa: E731
    first, prepare = _failing(tmp_path, OAUTH_SCREEN, rows)
    denied = [True]
    opened = []

    def fake_open(path, *a, **k):  # 認証の確かめが開くときだけ、権限を直すまで開けない
        opened.append(denied[0])
        if denied[0]:
            raise PermissionError(13, "Permission denied", str(path))
        return open(path, *a, **k)

    monkeypatch.setattr(observe, "open", fake_open, raising=False)

    class Later(FakeChild):
        polls = 0

        def isalive(self):
            self.polls += 1
            if self.polls == 4:
                denied[0] = False
            return super().isalive()

    child = Later([OAUTH_SCREEN.encode(), *[b""] * 8], None)
    child.started = lambda directory, session_id, launch: first.on_start(session_id)
    first.close()
    ended, spawn, w, _ = _run(tmp_path, [child, FakeChild([], 0)], prepare)
    assert _envs(spawn) == ["work1", "work2"] and ended.cause == ""
    assert opened.count(True) >= 2 and False in opened  # 開けない間も見回りごとに読み直した


def test_3864_6_when_neither_the_hold_nor_the_log_can_be_written_it_still_moves(tmp_path, monkeypatch):
    # 容量不足で外した間も、auth_hold の行から後の log.jsonl のどの行（替え先の用意の account_dir・終わりの end）も書けなくても、
    # 例外で止めず今のアカウントを除いた替え先を起動して続ける
    def hold(*a):
        raise OSError(28, "No space left on device")

    full = []
    real = wrapper.registry.signals.append_jsonl

    def append_jsonl(path, row):
        if path.name == wrapper.registry.LOG_FILE and (full or row.get("kind") == "auth_hold"):
            full.append(row.get("kind"))
            raise OSError(28, "No space left on device")
        return real(path, row)

    monkeypatch.setattr(accounts, "hold", hold)
    monkeypatch.setattr(wrapper.registry.signals, "append_jsonl", append_jsonl)
    first, prepare = _failing(tmp_path, OAUTH_SCREEN, lambda at: [prompt_row(at), failure_row("authentication_failed", None, at)])
    ended, spawn, w, _ = _run(tmp_path, [first, FakeChild([], 0)], prepare)
    assert _envs(spawn) == ["work1", "work2"] and ended.cause == ""
    assert {"auth_hold", "account_dir", "end"} <= set(full) and not rows_of(w, "auth_hold")
