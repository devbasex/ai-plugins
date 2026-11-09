"""登録済み API キー（#2855）: 部品 `lib/claude_api_keys.py`・確かめの要求・`relay.py account` の副命令のテスト。

実際の API へは要求を打たない。確かめの宛先は `NDF_API_KEY_CHECK_URL` で偽物へ向ける。
"""

from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_accounts as ca  # noqa: E402
import claude_api_keys as ak  # noqa: E402
import claude_usage as cu  # noqa: E402
from account_fake import accounts  # noqa: E402,F401
from test_relay_account import aws, run  # noqa: E402,F401

KEY_A = "sk-ant-api03-FAKE-VALUE-A-SECRET"
KEY_B = "sk-ant-api03-FAKE-VALUE-B-SECRET"
CREDIT_BODY = {
    "type": "error",
    "error": {"type": "invalid_request_error", "message": "Your credit balance is too low to access the Anthropic API."},
}
AUTH_BODY = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}


class FakeMessages:
    """`x-api-key` ごとに Messages API の応答を返す偽物。知らないキーは 401。"""

    def __init__(self):
        self.replies: dict[str, tuple[int, dict]] = {}
        self.calls: list[dict] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                key = self.headers.get("x-api-key") or ""
                fake.calls.append({"key": key, "body": body, "version": self.headers.get("anthropic-version")})
                status, reply = fake.replies.get(key, (401, AUTH_BODY))
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1/messages"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def ok(self, key):
        self.replies[key] = (200, {"type": "message", "content": []})


@pytest.fixture()
def messages(monkeypatch):
    fake = FakeMessages()
    monkeypatch.setenv("NDF_API_KEY_CHECK_URL", fake.url)
    yield fake
    fake.server.shutdown()


def relay(tmp_path, accounts, aws, messages, *args, stdin=None, **env):  # noqa: F811
    return run(tmp_path, accounts, aws, *args, stdin=stdin, NDF_API_KEY_CHECK_URL=messages.url, **env)


def book(accounts):  # noqa: F811
    return accounts.root / ak.BOOK_FILE


def no_leak(*procs, values=(KEY_A, KEY_B)):
    for p in procs:
        for v in values:
            assert v not in p.stdout and v not in p.stderr


# ---------------------------------------------------------------- 確かめの要求


def test_check_api_key_reads_each_result(messages):
    messages.ok(KEY_A)
    messages.replies["credit"] = (400, CREDIT_BODY)
    messages.replies["other"] = (404, {"type": "error", "error": {"type": "not_found_error", "message": "model: x"}})
    assert cu.check_api_key(KEY_A).result == "ok"
    assert cu.check_api_key("nope").result == "auth_failed"
    assert cu.check_api_key("credit").result == "credit_low"
    other = cu.check_api_key("other")
    assert (other.result, other.status, other.error_type) == ("unclassified", 404, "not_found_error")
    assert messages.calls[0]["body"]["max_tokens"] == 1 and messages.calls[0]["version"]


def test_check_api_key_network_failure(monkeypatch):
    monkeypatch.setenv("NDF_API_KEY_CHECK_URL", "http://127.0.0.1:9/v1/messages")
    assert cu.check_api_key(KEY_A, timeout=2).result == "network"


def test_credit_wording_matches_both_official_forms():
    assert cu.is_credit_low("Your credit balance is too low to access the Anthropic API.")
    assert cu.is_credit_low("Credit balance too low · Add funds")
    assert not cu.is_credit_low("You've hit your session limit")


# ---------------------------------------------------------------- 登録簿（I1・I2・I9・I10）


def test_add_two_keys_replace_and_remove(accounts):  # noqa: F811
    accounts.add("gmail")
    accounts.add("main")
    accounts.add("other")
    assert ak.add_key("k-gmail", KEY_A, "gmail") is False
    assert ak.add_key("k-main", KEY_B, "main") is False
    assert [k.name for k in ak.keys()] == ["k-gmail", "k-main"]
    assert stat.S_IMODE(os.stat(book(accounts)).st_mode) == 0o600
    gen = ak.get("k-main").generation
    assert ak.add_key("k-main", KEY_B, "other") is True  # 同じ名前は置き換わる（アカウントを替えても）
    assert ak.get("k-main").account == "other" and ak.get("k-main").generation != gen
    assert ak.remove_key("k-gmail") and not ak.remove_key("k-gmail")
    assert ak.key_names() == ["k-main"]


@pytest.mark.parametrize(
    ("name", "account", "reason"),
    [
        ("main", "gmail", "name_conflict"),
        ("metered", "gmail", "name_conflict"),
        ("k", "ghost", "not_registered"),
        ("k2", "main", "account_taken"),
    ],
)
def test_add_refuses(accounts, name, account, reason):  # noqa: F811
    accounts.add("gmail")
    accounts.add("main")
    ak.add_key("k1", KEY_A, "main")
    with pytest.raises(ak.Refused) as e:
        ak.add_key(name, KEY_B, account)
    assert e.value.reason == reason


def test_state_update_with_old_generation_is_ignored(accounts):  # noqa: F811
    accounts.add("main")
    ak.add_key("k", KEY_A, "main")
    old = ak.get("k")
    ak.add_key("k", KEY_B, "main")
    assert ak.note_state(old.name, old.generation, ak.AUTH_FAILED) is False
    assert ak.state_of(ak.get("k")).state == ak.OK and ak.pick_key().name == "k"
    (accounts.root / ak.STATE_FILE).write_text(json.dumps({"k": {"state": "auth_failed"}}))  # 世代の無い行
    assert ak.state_of(ak.get("k")).state == ak.OK


def test_pick_by_name_and_state(accounts):  # noqa: F811
    accounts.add("x")
    accounts.add("y")
    ak.add_key("b", KEY_B, "y")
    ak.add_key("a", KEY_A, "x")
    assert ak.pick_key().name == "a" and ak.pick_key().name == "a"
    a = ak.get("a")
    ak.note_state("a", a.generation, ak.CREDIT_LOW)
    assert ak.pick_key().name == "b"
    ak.note_state("a", a.generation, ak.CREDIT_LOW, now=time.time())
    rows = json.loads((accounts.root / ak.STATE_FILE).read_text())
    rows["a"]["since"] = cu.iso_utc(time.time() - ak.CREDIT_HOLD - 1)  # 保留が過ぎた
    (accounts.root / ak.STATE_FILE).write_text(json.dumps(rows))
    assert ak.pick_key().name == "a"
    ak.note_state("a", a.generation, ak.CREDIT_LOW)  # 保留が過ぎて 1 回試し、尽きたまま
    assert ak.pick_key().name == "b"
    ak.note_state("a", a.generation, ak.AUTH_FAILED)
    assert ak.pick_key().name == "b" and ak.pick_key(exclude={"b"}) is None


def test_use_is_off_without_or_with_broken_book(accounts):  # noqa: F811
    assert ak.use_setting() == "off" and ak.problem() is None
    accounts.add("main")
    ak.add_key("k", KEY_A, "main")
    for v in ak.USES:
        ak.set_use(v)
        assert ak.use_setting() == v
    book(accounts).write_text("{broken")
    assert ak.use_setting() == "off" and ak.problem() and ak.pick_key() is None and ak.key_names() == []


def test_key_env_only_for_worker_not_for_wrapper_section(accounts, monkeypatch):  # noqa: F811
    """受け入れ条件 5・I5: worker の子には値が 1 本入り、OAuth のトークンと AUTH_TOKEN は無い。ラッパーの区間の環境には値が無い。"""
    accounts.add("a")
    accounts.add("b")
    ak.add_key("k", KEY_A, "a")
    ak.set_use("always")
    monkeypatch.setenv(ca.TOKEN_ENV, "oauth-SECRET")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "auth-SECRET")
    monkeypatch.setenv(ca.NAME_ENV, "a")
    monkeypatch.setenv("CLAUDE_CODE_USE_FOUNDRY", "1")
    env = ak.env_with_key(ak.pick_key(), dict(os.environ))
    assert env[ak.KEY_ENV] == KEY_A and env[ak.NAME_ENV] == "k"
    assert ca.TOKEN_ENV not in env and "ANTHROPIC_AUTH_TOKEN" not in env and ca.NAME_ENV not in env
    assert "CLAUDE_CODE_USE_FOUNDRY" not in env  # 認証の優先順位でキーより上に来る
    # 外した親のアカウントは、キーを外したときの戻り先として名前だけ残る。呼び手の今のアカウントがあればそちら
    assert env[ak.BACK_ENV] == "a"
    assert ak.env_with_key(ak.pick_key(), dict(os.environ), "b")[ak.BACK_ENV] == "b"
    assert ak.BACK_ENV not in ak.env_with_key(ak.pick_key(), dict(os.environ), ca.METERED)
    from relay_lib import claude as relay_claude

    wrapped = relay_claude.section_env(dict(os.environ), "a")
    assert KEY_A not in json.dumps(wrapped)


# ---------------------------------------------------------------- relay.py account の副命令


def test_cli_add_list_check_remove_without_leaking(tmp_path, accounts, aws, messages):  # noqa: F811
    """受け入れ条件 1・3・4・11・12: 標準入力とファイルから登録し、一覧・確かめ直し・外すまで、値はどこにも出ない。"""
    accounts.add("gmail")
    accounts.add("main")
    messages.ok(KEY_A)
    messages.ok(KEY_B)
    a = relay(tmp_path, accounts, aws, messages, "add-api-key", "k-gmail", "--account", "gmail", stdin=KEY_A + "\n")
    assert a.returncode == 0, a.stderr
    assert "k-gmail" in a.stdout and "spend limit" in a.stderr
    kf = tmp_path / "key.txt"
    kf.write_text(KEY_B + "\n")
    b = relay(tmp_path, accounts, aws, messages, "add-api-key", "k-main", "--account", "main", "--key-file", str(kf), "--json")
    assert json.loads(b.stdout) == {"ok": True, "command": "add-api-key", "name": "k-main", "account": "main", "replaced": False}
    assert stat.S_IMODE(os.stat(book(accounts)).st_mode) == 0o600
    lj = relay(tmp_path, accounts, aws, messages, "list", "--json")
    rows = [r for r in json.loads(lj.stdout) if r["kind"] == "api_key"]
    assert [(r["name"], r["account"], r["state"]) for r in rows] == [("k-gmail", "gmail", "ok"), ("k-main", "main", "ok")]
    lt = relay(tmp_path, accounts, aws, messages, "list")
    assert "k-gmail" in lt.stdout and "使える" in lt.stdout
    k = ak.get("k-main")
    ak.note_state("k-main", k.generation, ak.CREDIT_LOW)
    assert "残高切れ" in relay(tmp_path, accounts, aws, messages, "list").stdout
    c = relay(tmp_path, accounts, aws, messages, "check", "k-main")
    assert c.returncode == 0 and ak.state_of(ak.get("k-main")).state == ak.OK
    r = relay(tmp_path, accounts, aws, messages, "remove", "k-gmail")
    assert r.returncode == 0 and ak.key_names() == ["k-main"]
    no_leak(a, b, lj, lt, c, r)
    for f in accounts.root.iterdir():
        if f.is_file() and f.name != ak.BOOK_FILE:
            assert KEY_A not in f.read_text(errors="replace") and KEY_B not in f.read_text(errors="replace")


@pytest.mark.parametrize(("reply", "reason"), [((401, AUTH_BODY), "auth_failed"), ((400, CREDIT_BODY), "credit_low"), (None, "network")])
def test_cli_add_check_failure_saves_nothing(tmp_path, accounts, aws, messages, reply, reason):  # noqa: F811
    """受け入れ条件 2・I3: 確かめが通らなければ理由を出して 1 で終わり、登録簿を作らない。"""
    accounts.add("main")
    extra = {}
    if reply:
        messages.replies[KEY_A] = reply
    else:
        extra["NDF_API_KEY_CHECK_URL"] = "http://127.0.0.1:9/v1/messages"
    p = run(
        tmp_path,
        accounts,
        aws,
        "add-api-key",
        "k",
        "--account",
        "main",
        "--json",
        stdin=KEY_A + "\n",
        **{"NDF_API_KEY_CHECK_URL": messages.url, **extra},
    )
    assert p.returncode == 1 and json.loads(p.stdout)["reason"] == reason
    assert not book(accounts).exists()
    no_leak(p)


@pytest.mark.parametrize(
    ("args", "stdin", "code", "reason"),
    [
        (["k", KEY_A, "--account", "main"], KEY_A + "\n", 2, "invalid_name"),  # 値を引数で渡すと引数の誤り
        ([KEY_A, "--account", "main"], KEY_A + "\n", 2, "invalid_name"),  # 値を名前の位置へ渡しても値を写さない
        (["main", "--account", "main"], KEY_A + "\n", 1, "name_conflict"),
        (["metered", "--account", "main"], KEY_A + "\n", 1, "name_conflict"),
        (["k", "--account", "ghost"], KEY_A + "\n", 1, "not_registered"),
        (["k2", "--account", "main"], KEY_A + "\n", 1, "account_taken"),
        (["k", "--account", "gmail"], "", 2, "missing_args"),
    ],
)
def test_cli_add_refusals(tmp_path, accounts, aws, messages, args, stdin, code, reason):  # noqa: F811
    accounts.add("main")
    accounts.add("gmail")
    ak.add_key("k1", KEY_B, "main")
    messages.ok(KEY_A)
    p = relay(tmp_path, accounts, aws, messages, "add-api-key", *args, "--json", stdin=stdin)
    assert p.returncode == code
    if reason != "invalid_name":
        assert json.loads(p.stdout)["reason"] == reason
    assert ak.key_names() == ["k1"]
    no_leak(p)


def test_cli_oauth_add_refuses_a_key_name(tmp_path, accounts, aws, messages):  # noqa: F811
    accounts.add("main")
    ak.add_key("k1", KEY_A, "main")
    p = relay(tmp_path, accounts, aws, messages, "add", "k1", "--json")
    assert p.returncode == 1 and json.loads(p.stdout)["reason"] == "name_conflict"


def test_cli_api_key_use(tmp_path, accounts, aws, messages):  # noqa: F811
    """受け入れ条件 6: 宣言が無ければ off。3 つの値を設定し読み返せる。違う値は 2。"""
    show = relay(tmp_path, accounts, aws, messages, "api-key-use", "--json")
    assert json.loads(show.stdout)["use"] == "off"
    for v in ak.USES:
        assert relay(tmp_path, accounts, aws, messages, "api-key-use", v).returncode == 0
        assert json.loads(relay(tmp_path, accounts, aws, messages, "api-key-use", "--json").stdout)["use"] == v
    assert relay(tmp_path, accounts, aws, messages, "api-key-use", "sometimes").returncode == 2


def test_cli_check_failure_keeps_state(tmp_path, accounts, aws, messages):  # noqa: F811
    """受け入れ条件 12: 確かめ直しが失敗すれば状態を変えず、成功すれば ok へ戻す。"""
    accounts.add("main")
    ak.add_key("k", KEY_A, "main")
    k = ak.get("k")
    ak.note_state("k", k.generation, ak.CREDIT_LOW)
    messages.replies[KEY_A] = (400, CREDIT_BODY)
    p = relay(tmp_path, accounts, aws, messages, "check", "k", "--json")
    assert p.returncode == 1 and json.loads(p.stdout)["reason"] == "credit_low"
    assert ak.state_of(ak.get("k")).state == ak.CREDIT_LOW
    messages.ok(KEY_A)
    assert relay(tmp_path, accounts, aws, messages, "check", "k").returncode == 0
    assert ak.state_of(ak.get("k")).state == ak.OK
    no_leak(p)
