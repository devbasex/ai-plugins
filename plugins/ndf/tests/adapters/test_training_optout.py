"""学習に使う設定なら Off へ書き換えて読み直してから起動すること（#3907 の 5）。

書き込みの API は httpx の `MockTransport` の偽のアカウントで受け、本物のアカウントへは送らない。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import filelock
import httpx
import pytest
from jsonschema import Draft202012Validator

from ndf.adapters import agents, run_log, training, training_cache

TOKEN = "oauth-token-value-123456"
ACCOUNT = "acct-secret-987"
NOW = time.time()
SCHEMA = Path(__file__).resolve().parents[2] / "contracts" / "schemas" / "io" / "training.result.schema.json"


class Account:
    """学習の設定を持つ偽のアカウント。`GET` で今の値を返し、`PATCH` を受けて値を false へ変える。

    `refuse` はキー → 返す状態（2xx でない）、`broken` は通信の失敗にするキー、`ignore` は受け付けても値を変えない、
    `reread` は 2 回目以降の `GET` の状態、`drop` は書き換えた後の `GET` から消すキー。
    """

    def __init__(self, runtime: str, values: dict, refuse=None, broken=(), ignore=False, reread=200, drop=()):
        self.runtime, self.values = runtime, dict(values)
        self.refuse, self.broken, self.ignore, self.reread, self.drop = refuse or {}, set(broken), ignore, reread, set(drop)
        self.requests: list[httpx.Request] = []
        self.gets = 0

    def methods(self) -> list[str]:
        return [r.method for r in self.requests]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET":
            self.gets += 1
            if self.gets > 1 and self.reread != 200:
                return httpx.Response(self.reread, json={})
            shown = {k: v for k, v in self.values.items() if not (self.gets > 1 and k in self.drop)}
            return httpx.Response(200, json=shown if self.runtime == "claude" else {"settings": shown})
        key = request.url.params.get("feature") if self.runtime == "codex" else next(iter(json.loads(request.content)))
        if key in self.broken:
            raise httpx.ConnectError("down")
        if key in self.refuse:
            return httpx.Response(self.refuse[key], json={})
        if not self.ignore:
            self.values[key] = False
        return httpx.Response(202 if self.runtime == "claude" else 200, json={key: False})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def claude_env(tmp_path: Path) -> dict[str, str]:
    conf = tmp_path / "claude-conf"
    conf.mkdir(exist_ok=True)
    (conf / training.CLAUDE_CREDENTIALS).write_text(json.dumps({"claudeAiOauth": {"accessToken": TOKEN}}), encoding="utf-8")
    return {"CLAUDE_CONFIG_DIR": str(conf)}


def codex_env(tmp_path: Path, mode: str = "chatgpt") -> dict[str, str]:
    home = tmp_path / "codex-home"
    home.mkdir(exist_ok=True)
    auth = {"auth_mode": mode, "tokens": {"access_token": TOKEN, "account_id": ACCOUNT}}
    (home / "auth.json").write_text(json.dumps(auth), encoding="utf-8")
    return {"CODEX_HOME": str(home)}


def saved() -> dict:
    return json.loads(training.cache_path().read_text(encoding="utf-8"))


def no_secret(*texts: object) -> None:
    text = "".join(t if isinstance(t, str) else json.dumps(t, ensure_ascii=False) for t in texts)
    assert TOKEN not in text and ACCOUNT not in text


def schema_errors(report: dict) -> list:
    return list(Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8"))["properties"]["items"]).iter_errors(report["items"]))


def fail_saves(monkeypatch) -> None:
    """`training.save` を、呼ばれると `OSError("disk full")` を投げるものへ差し替える。"""

    def save(readings, now, env=None):
        raise OSError("disk full")

    monkeypatch.setattr(training, "save", save)


# ---------------------------------------------------------------- 記録の形（受け入れ条件 1・2・7）


def test_reading_item_adds_rewrites_only_when_rewritten():
    plain = training.Reading("claude", False, "x")
    rewrite = training_cache.Rewrite("grove_enabled", True, None, "2026-11-21T01:00:01+00:00")

    assert set(plain.item("t")) == {"runtime", "training", "reason", "checked_at"}
    assert plain._replace(rewrites=(rewrite,)).item("t")["rewrites"] == [
        {"key": "grove_enabled", "before": True, "after": None, "at": "2026-11-21T01:00:01+00:00"}
    ]


@pytest.mark.parametrize(
    ("element", "ok"),
    [
        ({"key": "k", "before": True, "after": False, "at": "t"}, True),
        ({"key": "k", "before": True, "after": None, "at": "t"}, True),
        ({"key": "k", "before": True, "after": False}, False),
        ({"key": "k", "before": True, "after": False, "at": "t", "token": "x"}, False),
    ],
)
def test_schema_accepts_only_complete_rewrites(element, ok):
    item = {"runtime": "claude", "training": False, "reason": "x", "checked_at": "t", "rewrites": [element]}

    assert (schema_errors({"items": [item]}) == []) is ok


# ---------------------------------------------------------------- claude の書き換え（受け入れ条件 1・5・6）


def test_claude_true_is_rewritten_with_the_read_headers_and_reread(tmp_path):
    account = Account("claude", {"grove_enabled": True})

    got = training.settle("claude", claude_env(tmp_path), account.client(), NOW)

    assert account.methods() == ["GET", "PATCH", "GET"]
    get, patch = account.requests[0], account.requests[1]
    assert str(patch.url) == training.CLAUDE_URL and json.loads(patch.content) == {"grove_enabled": False}
    assert all(patch.headers[h] == get.headers[h] for h in ("Authorization", "anthropic-beta"))
    assert got.training is False and "Off へ書き換えた: grove_enabled" in got.reason
    assert [(r.key, r.before, r.after) for r in got.rewrites] == [("grove_enabled", True, False)]
    assert got.rewrites[0].at
    no_secret(got.reason, got.item("t"))


def test_check_claude_rewrites_and_records(tmp_path):
    env = claude_env(tmp_path)
    account = Account("claude", {"grove_enabled": True})

    got = training.check(["claude"], env, account.client(), NOW)

    assert got["status"] == "ok" and "Off へ書き換えた: claude" in got["summary"]
    out = got["items"][0]["rewrites"]
    kept = saved()["claude"]
    assert kept["training"] is False and kept["rewrites"] == out
    assert out[0]["key"] == "grove_enabled" and out[0]["before"] is True and out[0]["after"] is False and out[0]["at"]
    assert training.refusal("claude", NOW, env) is None
    no_secret(got, training.cache_path().read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("make_env", "values"),
    [
        (claude_env, {"grove_enabled": False}),
        (lambda p: {"CLAUDE_CONFIG_DIR": str(p / "none")}, {"grove_enabled": True}),
        (lambda p: {**claude_env(p), "ANTHROPIC_API_KEY": "sk-x"}, {"grove_enabled": True}),
        (claude_env, {"grove_enabled": "yes"}),
    ],
)
def test_claude_sends_no_write_unless_read_as_training(tmp_path, make_env, values):
    account = Account("claude", values)

    got = training.settle("claude", make_env(tmp_path), account.client(), NOW)

    assert set(account.methods()) <= {"GET"} and len(account.requests) <= 1
    assert got.rewrites == ()
    assert got == training.read_setting("claude", make_env(tmp_path), Account("claude", values).client(), NOW)


# ---------------------------------------------------------------- codex の書き換え（受け入れ条件 2・5）


def test_codex_rewrites_only_the_true_keys(tmp_path):
    account = Account("codex", {"training_allowed": True, "codex_training_allowed": False, "codex_training_allowed_v2": True})

    got = training.settle("codex", codex_env(tmp_path), account.client(), NOW)

    patches = [r for r in account.requests if r.method == "PATCH"]
    assert [dict(r.url.params) for r in patches] == [
        {"feature": "training_allowed", "value": "false"},
        {"feature": "codex_training_allowed_v2", "value": "false"},
    ]
    assert all(str(r.url).startswith(training.CODEX_PATCH_URL) for r in patches)
    assert all(r.headers["ChatGPT-Account-Id"] == ACCOUNT and r.headers["User-Agent"] == training.CODEX_USER_AGENT for r in patches)
    assert account.methods()[-1] == "GET"
    assert got.training is False
    assert (
        got.reason.startswith("chatgpt/settings/user（")
        and "training_allowed・codex_training_allowed・codex_training_allowed_v2" in got.reason
    )
    assert [(r.key, r.before, r.after) for r in got.rewrites] == [
        ("training_allowed", True, False),
        ("codex_training_allowed_v2", True, False),
    ]
    no_secret(got.reason, got.item("t"))


@pytest.mark.parametrize(
    "values",
    [
        {"training_allowed": True, "codex_training_allowed": True, "codex_training_allowed_v2": True},
        {"training_allowed": False, "codex_training_allowed": True},
        {"training_allowed": True},
    ],
)
def test_codex_rewrites_each_true_key_in_one_request(tmp_path, values):
    account = Account("codex", values)

    got = training.settle("codex", codex_env(tmp_path), account.client(), NOW)

    assert got.training is False and [r.key for r in got.rewrites] == [k for k, v in values.items() if v]
    assert account.methods().count("PATCH") == len(got.rewrites)


def test_codex_keeps_sending_after_one_key_is_refused(tmp_path):
    account = Account("codex", {"training_allowed": True, "codex_training_allowed": True}, refuse={"training_allowed": 422})

    got = training.settle("codex", codex_env(tmp_path), account.client(), NOW)

    assert account.methods() == ["GET", "PATCH", "PATCH", "GET"]
    assert got.training is True and "training_allowed が HTTP 422" in got.reason
    assert [(r.key, r.after) for r in got.rewrites] == [("codex_training_allowed", False)]


def test_codex_key_missing_on_reread_is_not_taken_as_off(tmp_path):
    account = Account("codex", {"training_allowed": True, "codex_training_allowed_v2": True}, drop={"codex_training_allowed_v2"})

    got = training.settle("codex", codex_env(tmp_path), account.client(), NOW)

    assert got.training is True and "書き換えた後の読み直しにキーが無い: codex_training_allowed_v2" in got.reason
    assert {r.key: r.after for r in got.rewrites} == {"training_allowed": False, "codex_training_allowed_v2": None}


# ---------------------------------------------------------------- キーごとの書き込みと送った結果の集め方（I-003 の前に固定）

CODEX_ALL_ON = {"training_allowed": True, "codex_training_allowed": True, "codex_training_allowed_v2": True}


def test_each_rewrite_is_stamped_just_before_its_own_write(tmp_path):
    account = Account("codex", CODEX_ALL_ON, refuse={"codex_training_allowed": 422})
    clock = lambda: 1_000_000.0 + len(account.requests)  # noqa: E731 - 送った要求の数で時刻が進む

    got = training.settle("codex", codex_env(tmp_path), account.client(), NOW, clock=clock)

    # 1 番目の PATCH の前は GET 1 回、3 番目の PATCH の前は GET 1 回と PATCH 2 回
    assert [(r.key, r.at) for r in got.rewrites] == [
        ("training_allowed", training._stamp(1_000_001.0)),
        ("codex_training_allowed_v2", training._stamp(1_000_003.0)),
    ]


def test_every_failed_key_is_named_in_order_and_the_rest_are_still_sent(tmp_path):
    account = Account("codex", CODEX_ALL_ON, refuse={"training_allowed": 422}, broken={"codex_training_allowed_v2"})

    got = training.settle("codex", codex_env(tmp_path), account.client(), NOW)

    assert account.methods() == ["GET", "PATCH", "PATCH", "PATCH", "GET"]
    assert got.training is True
    assert got.reason == "training_allowed が HTTP 422・codex_training_allowed_v2 が通信の失敗（ConnectError）"
    assert [(r.key, r.before, r.after) for r in got.rewrites] == [("codex_training_allowed", True, False)]


def test_when_every_write_fails_it_still_rereads_and_keeps_no_rewrite(tmp_path):
    account = Account(
        "codex", {"training_allowed": True, "codex_training_allowed": True}, refuse={"training_allowed": 403, "codex_training_allowed": 500}
    )

    got = training.settle("codex", codex_env(tmp_path), account.client(), NOW)

    assert account.methods() == ["GET", "PATCH", "PATCH", "GET"]
    assert got.training is True and got.reason == "training_allowed が HTTP 403・codex_training_allowed が HTTP 500"
    assert got.rewrites == ()


def test_a_failed_write_is_reported_over_a_failed_reread(tmp_path):
    account = Account("codex", {"training_allowed": True, "codex_training_allowed": True}, refuse={"training_allowed": 422}, reread=500)

    got = training.settle("codex", codex_env(tmp_path), account.client(), NOW)

    assert got.training is True and got.reason == "training_allowed が HTTP 422"
    assert [(r.key, r.before, r.after) for r in got.rewrites] == [("codex_training_allowed", True, None)]


def test_sent_keys_have_no_after_when_the_reread_fails(tmp_path):
    account = Account("codex", CODEX_ALL_ON, reread=500)

    got = training.settle("codex", codex_env(tmp_path), account.client(), NOW)

    assert got.training is True and got.reason == "書き換えた後に読み直せない（HTTP 500）"
    assert [(r.key, r.before, r.after) for r in got.rewrites] == [(k, True, None) for k in CODEX_ALL_ON]


def test_failed_rewrite_is_written_to_the_run_log_with_every_true_key(tmp_path):
    log = run_log.open_run("entry", run_id="r-training-failed", location="repo")
    account = Account("codex", CODEX_ALL_ON, refuse={"codex_training_allowed": 422})

    training.settle("codex", codex_env(tmp_path), account.client(), NOW)

    lines = [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]
    rows = [r["fields"] for r in lines if r.get("event") == "training"]
    assert rows and rows[-1]["state"] == "rewrite-failed" and rows[-1]["keys"] == list(CODEX_ALL_ON)
    no_secret(log.path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("make_env", "values"),
    [
        (codex_env, {"training_allowed": False, "codex_training_allowed": False}),
        (lambda p: codex_env(p, mode="apikey"), {"training_allowed": True}),
        (lambda p: {"CODEX_HOME": str(p / "none")}, {"training_allowed": True}),
        (codex_env, {"training_allowed": True, "codex_training_allowed": "x"}),
    ],
)
def test_codex_sends_no_write_unless_read_as_training(tmp_path, make_env, values):
    account = Account("codex", values)

    got = training.settle("codex", make_env(tmp_path), account.client(), NOW)

    assert set(account.methods()) <= {"GET"} and len(account.requests) <= 1 and got.rewrites == ()


def test_unsupported_runtime_sends_nothing(tmp_path):
    account = Account("claude", {"grove_enabled": True})

    got = training.settle("kiro", {}, account.client(), NOW)

    assert got.training is None and account.requests == []


# ---------------------------------------------------------------- 失敗のとき断る（受け入れ条件 4・6）

FAILURES = [
    (dict(refuse={"grove_enabled": 403}), "grove_enabled が HTTP 403"),
    (dict(broken={"grove_enabled"}), "grove_enabled が通信の失敗（ConnectError）"),
    (dict(ignore=True), "書き換えた後も学習に使う設定のまま: grove_enabled"),
    (dict(reread=500), "書き換えた後に読み直せない（HTTP 500）"),
]


@pytest.mark.parametrize(("failure", "said"), FAILURES)
def test_failed_rewrite_is_recorded_as_training_and_refused_with_where_to_fix(tmp_path, failure, said):
    env = claude_env(tmp_path)
    account = Account("claude", {"grove_enabled": True}, **failure)

    got = training.check(["claude"], env, account.client(), NOW)

    assert got["status"] == "stopped" and got["cause"]["kind"] == "refused"
    assert said in got["items"][0]["reason"] and said in got["summary"]
    assert training_cache.manual_off("claude") in got["summary"] and f"{training.CHECK_COMMAND} --runtime claude" in got["summary"]
    assert saved()["claude"]["training"] is True
    why = training.refusal("claude", NOW, env)
    assert said in why and training_cache.manual_off("claude") in why and f"{training.CHECK_COMMAND} --runtime claude" in why
    no_secret(got, why, training.cache_path().read_text(encoding="utf-8"))


def test_off_by_hand_names_the_place_and_the_command():
    for runtime in ("claude", "codex"):
        text = training_cache.off_by_hand(runtime)
        assert training_cache.manual_off(runtime) in text and f"{training.CHECK_COMMAND} --runtime {runtime}" in text


def test_refusal_of_a_training_record_without_reason_still_names_the_place():
    training.cache_path().write_text(
        json.dumps({"claude": {"training": True, "reason": "", "checked_at": training._stamp(NOW)}}), encoding="utf-8"
    )

    why = training.refusal("claude", NOW)

    assert "学習に使う" in why and training_cache.manual_off("claude") in why


def test_mixed_summary_names_every_runtime_that_stops(tmp_path):
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "none"), **codex_env(tmp_path)}
    codex = Account("codex", {"training_allowed": True}, refuse={"training_allowed": 403})

    got = training.check(["claude", "codex"], env, codex.client(), NOW)

    assert got["cause"]["kind"] == "precondition"
    assert "確かめられない: claude" in got["summary"] and "training_allowed が HTTP 403" in got["summary"]
    assert training_cache.manual_off("codex") in got["summary"] and f"{training.CHECK_COMMAND} --runtime codex" in got["summary"]


def test_rewritten_runtime_is_named_even_when_another_stops(tmp_path):
    env = {**claude_env(tmp_path), "CODEX_HOME": str(tmp_path / "none")}

    def handler(request: httpx.Request) -> httpx.Response:
        return claude.handler(request)

    claude = Account("claude", {"grove_enabled": True})
    got = training.check(["claude", "codex"], env, httpx.Client(transport=httpx.MockTransport(handler)), NOW)

    assert got["cause"]["kind"] == "precondition" and "Off へ書き換えた: claude" in got["summary"]


def test_rewrite_is_written_to_the_run_log(tmp_path):
    log = run_log.open_run("entry", run_id="r-training", location="repo")
    account = Account("claude", {"grove_enabled": True})

    training.settle("claude", claude_env(tmp_path), account.client(), NOW)

    lines = [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]
    rows = [r["fields"] for r in lines if r.get("event") == "training"]
    assert rows and rows[-1]["state"] == "rewritten" and rows[-1]["keys"] == ["grove_enabled"]
    no_secret(log.path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 起動の口のその場の確かめ（受け入れ条件 3）


def test_launch_rewrites_on_the_spot_and_continues(marker_launch, monkeypatch, tmp_path):
    env = codex_env(tmp_path)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    account = Account("codex", {"training_allowed": True})
    client = account.client()
    monkeypatch.setattr(training.httpx, "Client", lambda: client)
    training.cache_path().unlink()
    marker, _, spec = marker_launch("codex")

    status = agents.run_agent(spec, poll=0.05)

    assert status.state == agents.AgentState.DONE and marker.exists()
    kept = saved()["codex"]
    assert kept["training"] is False and kept["rewrites"][0]["key"] == "training_allowed"


def test_launch_refuses_after_a_failed_rewrite(monkeypatch, tmp_path):
    env = claude_env(tmp_path)
    account = Account("claude", {"grove_enabled": True}, refuse={"grove_enabled": 403})
    client = account.client()
    monkeypatch.setattr(training.httpx, "Client", lambda: client)
    training.cache_path().unlink()

    why = training.ensure("claude", env, NOW)

    assert why is not None and "grove_enabled が HTTP 403" in why and training_cache.manual_off("claude") in why
    assert training.ensure("claude", env, NOW) is not None and account.methods().count("PATCH") == 1


# ---------------------------------------------------------------- 並行の保存と確かめ（決定 11）


def test_launch_gives_up_when_another_check_does_not_end(monkeypatch):
    training.cache_path().unlink()
    monkeypatch.setattr(training, "_settle_wait", lambda runtime: 0.1)
    calls: list[str] = []
    with training._settle_lock("codex"):
        why = training.ensure("codex", now=NOW, read=lambda rt, env, now: calls.append(rt) or training.Reading(rt, False, "x"))

    assert calls == [] and "ほかの確かめが終わらない" in why


def test_settle_wait_is_derived_from_the_call_limit():
    assert training._settle_wait("claude") == 27 and training._settle_wait("codex") == 45


def test_check_continues_when_one_runtime_is_busy(monkeypatch, tmp_path):
    monkeypatch.setattr(training, "_settle_wait", lambda runtime: 0.1)
    env = {**claude_env(tmp_path), **codex_env(tmp_path)}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"settings": {"training_allowed": False}})

    with training._settle_lock("claude"):
        got = training.check(["claude", "codex"], env, httpx.Client(transport=httpx.MockTransport(handler)), NOW)

    by = {i["runtime"]: i for i in got["items"]}
    assert got["cause"]["kind"] == "precondition" and by["claude"]["reason"] == "ほかの確かめが終わらない"
    assert by["codex"]["training"] is False and saved()["codex"]["training"] is False


def test_check_reports_a_rewrite_it_could_not_record(monkeypatch, tmp_path):
    env = {**claude_env(tmp_path), **codex_env(tmp_path)}
    real_save = training.save

    def save(readings, now, env=None):
        if readings[0].runtime == "claude":
            raise filelock.Timeout("training.json.lock")
        return real_save(readings, now, env)

    monkeypatch.setattr(training, "save", save)
    claude = Account("claude", {"grove_enabled": True})

    def handler(request: httpx.Request) -> httpx.Response:
        if "chatgpt" in str(request.url):
            return httpx.Response(200, json={"settings": {"training_allowed": False}})
        return claude.handler(request)

    got = training.check(["claude", "codex"], env, httpx.Client(transport=httpx.MockTransport(handler)), NOW)

    by = {i["runtime"]: i for i in got["items"]}
    assert got["cause"]["kind"] == "precondition"
    assert by["claude"]["rewrites"][0]["after"] is False and "記録できなかった（Timeout）" in by["claude"]["reason"]
    assert "記録できなかった: claude" in got["summary"] and "Off へ書き換えた: claude" in got["summary"]
    assert f"{training.CHECK_COMMAND} --runtime claude" in got["summary"]
    assert saved()["codex"]["training"] is False


def test_launch_says_it_rewrote_when_it_cannot_record(monkeypatch):
    training.cache_path().unlink()

    def save(readings, now, env=None):
        raise OSError("disk full")

    monkeypatch.setattr(training, "save", save)
    rewrite = training_cache.Rewrite("training_allowed", True, False, "t")

    why = training.ensure("codex", now=NOW, read=lambda rt, env, now: training.Reading(rt, False, "x", (rewrite,)))

    assert why is not None and "記録できなかった: OSError" in why and "Off へ書き換えて読み直して false: training_allowed" in why


def test_launch_names_only_the_keys_read_back_as_off_when_it_cannot_record(monkeypatch):
    training.cache_path().unlink()
    monkeypatch.setattr(training, "save", lambda readings, now, env=None: (_ for _ in ()).throw(OSError("disk full")))
    rewrites = tuple(
        training_cache.Rewrite(key, True, after, "t")
        for key, after in (("training_allowed", False), ("codex_training_allowed", True), ("codex_training_allowed_v2", None))
    )
    reading = training.Reading("codex", True, "書き換えた後も学習に使う設定のまま: codex_training_allowed", rewrites)

    why = training.ensure("codex", now=NOW, read=lambda rt, env, now: reading)

    assert "Off へ書き換えて読み直して false: training_allowed。" in why
    assert "Off を確かめられない: codex_training_allowed（読み直して true）・codex_training_allowed_v2（読み直して値が読めない）" in why
    assert "確かめた結果: 書き換えた後も学習に使う設定のまま" in why


def test_failed_save_after_a_negative_recheck_drops_the_old_off_record(monkeypatch, tmp_path):
    env = claude_env(tmp_path)
    training.save([training.Reading("claude", False, "前の確かめ")], NOW, env)
    assert training.refusal("claude", NOW, env) is None
    fail_saves(monkeypatch)
    account = Account("claude", {"grove_enabled": True}, refuse={"grove_enabled": 403})

    got = training.check(["claude"], env, account.client(), NOW)

    assert got["cause"]["kind"] == "precondition"
    assert training.verdict("claude", NOW, env).stale and training.refusal("claude", NOW, env) is not None


def test_failed_save_and_failed_lock_still_drop_the_old_off_record_and_keep_the_others(monkeypatch, tmp_path):
    env = {**claude_env(tmp_path), **codex_env(tmp_path)}
    training.save([training.Reading("claude", False, "前の確かめ"), training.Reading("codex", False, "前の確かめ")], NOW, env)
    monkeypatch.setattr(training_cache, "_rewrite_cache", lambda change: (_ for _ in ()).throw(filelock.Timeout("x")))

    got = training.check(["claude"], env, Account("claude", {"grove_enabled": True}, ignore=True).client(), NOW)

    assert got["cause"]["kind"] == "precondition"
    assert training.refusal("claude", NOW, env) is not None and training.refusal("codex", NOW, env) is None


def test_a_concurrent_save_that_writes_back_the_old_record_does_not_undo_the_drop(tmp_path):
    env = {**claude_env(tmp_path), **codex_env(tmp_path)}
    training.save([training.Reading("claude", False, "前の確かめ")], NOW, env)
    read_by_codex = training.cache_path().read_text(encoding="utf-8")

    assert training_cache.forget("claude", NOW)
    # 消す前に読んだ codex の保存が、古い claude の Off を含む内容を書き戻す
    training.cache_path().write_text(read_by_codex, encoding="utf-8")

    assert saved()["claude"]["training"] is False
    assert training.verdict("claude", NOW, env).stale and training.refusal("claude", NOW, env) is not None


def test_a_new_record_after_the_drop_is_used_again(tmp_path):
    env = claude_env(tmp_path)
    training.save([training.Reading("claude", False, "前の確かめ")], NOW, env)
    assert training_cache.forget("claude", NOW)

    training.save([training.Reading("claude", False, "確かめ直した")], NOW, env)

    assert training.refusal("claude", NOW, env) is None


def test_an_unreadable_drop_mark_refuses_and_asks_for_a_recheck(tmp_path):
    env = claude_env(tmp_path)
    training.save([training.Reading("claude", False, "前の確かめ")], NOW, env)
    training_cache.revoked_path("claude").write_text("not a time", encoding="utf-8")

    assert training.verdict("claude", NOW, env).stale and training.refusal("claude", NOW, env) is not None


@pytest.mark.parametrize(
    "broken",
    [b"\xff\xfe", b"\xe3\x81", b"\x80"],
    ids=["invalid-start", "truncated-multibyte", "lone-continuation"],
)
def test_a_drop_mark_with_broken_utf8_refuses_and_asks_for_a_recheck(tmp_path, broken):
    env = claude_env(tmp_path)
    training.save([training.Reading("claude", False, "前の確かめ")], NOW, env)
    training_cache.revoked_path("claude").write_bytes(broken)

    assert training.verdict("claude", NOW, env).stale and training.refusal("claude", NOW, env) is not None


def test_a_drop_reaches_an_off_saved_while_waiting_for_the_lock(tmp_path):
    env = codex_env(tmp_path)
    # 確かめの時刻 NOW を決めた後、排他を待つ間にほかの確かめが新しい Off を保存した
    training.save([training.Reading("codex", False, "ほかの確かめ")], NOW + 60, env)

    assert training_cache.forget("codex", NOW)

    assert training.verdict("codex", NOW + 60, env).stale and training.refusal("codex", NOW + 60, env) is not None


def test_a_drop_that_cannot_read_the_backup_revokes_every_record(monkeypatch, tmp_path):
    env = codex_env(tmp_path)
    # 排他を待つ間にほかの確かめが NOW より新しい Off を保存し、forget の読み取りだけが一時的に失敗する
    training.save([training.Reading("codex", False, "ほかの確かめ")], NOW + 60, env)
    read_text = Path.read_text

    def fail_on_backup(self, *args, **kwargs):
        if self.name == training_cache.CACHE_FILE:
            raise OSError("一時的な読み取りの失敗")
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_on_backup)
    assert training_cache.forget("codex", NOW)
    monkeypatch.setattr(Path, "read_text", read_text)

    assert training.verdict("codex", NOW + 60, env).stale and training.refusal("codex", NOW + 60, env) is not None
    training.save([training.Reading("codex", False, "確かめ直した")], NOW + 120, env)
    assert training.refusal("codex", NOW + 120, env) is None


def test_a_broken_backup_with_a_revoke_all_mark_asks_for_a_recheck(tmp_path):
    env = codex_env(tmp_path)
    training.save([training.Reading("codex", False, "前の確かめ")], NOW, env)
    training.cache_path().write_text("{broken", encoding="utf-8")
    assert training_cache.forget("codex", NOW)

    got = training.verdict("codex", NOW, env)

    assert got.why is not None and got.stale


def test_a_save_between_reading_the_backup_and_the_drop_mark_is_not_read_as_the_old_off(monkeypatch, tmp_path):
    env = claude_env(tmp_path)
    training.save([training.Reading("claude", False, "前の確かめ")], NOW, env)
    assert training_cache.forget("claude", NOW)
    read_json = training_cache._read_json
    raced = []

    def read_then_race(path):
        got = read_json(path)
        if path.name == training_cache.CACHE_FILE and not raced:
            # 古い Off を読んだ直後に、ほかの確かめが On を保存して取り消しのファイルを消す
            raced.append(True)
            training.save([training.Reading("claude", True, "学習に使う")], NOW + 1, env)
        return got

    monkeypatch.setattr(training_cache, "_read_json", read_then_race)

    got = training.verdict("claude", NOW + 1, env)

    assert raced and got.why is not None and "学習に使う設定" in got.why


def test_claude_off_is_bound_to_the_credential_used_for_the_check(tmp_path):
    env = claude_env(tmp_path)
    state = Path(env["CLAUDE_CONFIG_DIR"]) / training_cache.CLAUDE_STATE
    state.write_text(json.dumps({"oauthAccount": {"accountUuid": "acct-a"}}), encoding="utf-8")
    # ログインの切り替えの途中: 認証情報は別のアカウント B のまま、.claude.json は A
    credentials = Path(env["CLAUDE_CONFIG_DIR"]) / training.CLAUDE_CREDENTIALS
    credentials.write_text(json.dumps({"claudeAiOauth": {"accessToken": "token-of-b"}}), encoding="utf-8")
    training.check(["claude"], env, Account("claude", {"grove_enabled": False}).client(), NOW)
    assert training.refusal("claude", NOW, env) is None

    credentials.write_text(json.dumps({"claudeAiOauth": {"accessToken": "token-of-a"}}), encoding="utf-8")

    got = training.verdict("claude", NOW, env)
    assert got.stale and "認証の文脈" in got.why
    assert "token-of" not in training.cache_path().read_text(encoding="utf-8")


def test_credential_switched_during_the_claude_check_is_not_recorded_as_off(tmp_path):
    env = claude_env(tmp_path)
    credentials = Path(env["CLAUDE_CONFIG_DIR"]) / training.CLAUDE_CREDENTIALS
    account = Account("claude", {"grove_enabled": False})

    def handler(request: httpx.Request) -> httpx.Response:
        credentials.write_text(json.dumps({"claudeAiOauth": {"accessToken": "token-of-other"}}), encoding="utf-8")
        return account.handler(request)

    got = training.check(["claude"], env, httpx.Client(transport=httpx.MockTransport(handler)), NOW)

    assert got["cause"]["kind"] == "precondition" and saved()["claude"]["training"] is None


def test_account_switched_during_the_check_is_not_recorded_as_off(tmp_path):
    env = codex_env(tmp_path)
    auth = Path(env["CODEX_HOME"]) / "auth.json"
    account = Account("codex", {"training_allowed": True})

    def handler(request: httpx.Request) -> httpx.Response:
        response = account.handler(request)
        if request.method == "GET" and account.gets > 1:
            switched = {"auth_mode": "chatgpt", "tokens": {"access_token": TOKEN, "account_id": "acct-other"}}
            auth.write_text(json.dumps(switched), encoding="utf-8")
        return response

    got = training.check(["codex"], env, httpx.Client(transport=httpx.MockTransport(handler)), NOW)

    kept = saved()["codex"]
    assert got["cause"]["kind"] == "precondition" and kept["training"] is None and "認証の文脈" in kept["reason"]
    assert training.refusal("codex", NOW, env) is not None
