"""`ndf plan` の `claude -p`（U12。#3604 の 2・11、決定 1・8・9・12）。

- 登録済みアカウントの親: 認証の上書きの確かめ・使用の届け（`acquire`・関所・`release`）・上限での 1 回の打ち直し（`retry_env`）
- 従量の接続と API キーの親: `target_env` の確かめと、親の接続の識別（`Built.pin` と `NDF_RELAY_PIN`）
- キーの子: U11a の実機の確かめで 4 つの起動の形がどれも外れたので、`call_claude` は `key_gate` で起動しない
偽の claude は呼ばれた環境と届けを記録するスクリプトで、実の claude・キー・アカウントを使わない。値はすべて偽の値。
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import sys
from pathlib import Path

import pytest

from ndf import identity
from ndf.adapters import claude, launch_slot
from ndf.contracts import facts
from ndf.domains.relay import account_choice, accounts, plan_claude, session_env
from ndf.domains.relay import account_registry as reg
from relay_children import register

FAKE_KEY = "sk-ant-api03-fake-test-key-not-real"
BEDROCK_DECL = "CLAUDE_CODE_USE_BEDROCK=1 AWS_REGION=us-east-1"
# 偽の claude: 呼ばれた環境・起動引数・その時点の届けを `FAKE_OUT` へ足し、`FAKE_LIMITED` のアカウントでは上限で終える
FAKE = """#!{python}
import json, os, pathlib, sys
if sys.argv[1:] == ["--version"]:  # cli_floor の下限の確かめ
    print("2.1.330 (Claude Code)")
    sys.exit(0)
prompt = sys.stdin.read()
out = pathlib.Path(os.environ["FAKE_OUT"])
calls = json.loads(out.read_text()) if out.exists() else []
account = os.environ.get("NDF_CLAUDE_ACCOUNT")
d = pathlib.Path(os.environ["FAKE_LEASES"]) / str(account)
leases = [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))] if d.is_dir() else []
calls.append({{"account": account, "config": os.environ.get("CLAUDE_CONFIG_DIR"), "pid": os.getpid(), "argv": sys.argv[1:],
              "prompt": prompt, "cwd": os.getcwd(), "leases": leases, "pin": os.environ.get("NDF_RELAY_PIN"),
              "key": os.environ.get("ANTHROPIC_API_KEY")}})
out.write_text(json.dumps(calls))
if os.environ.get("FAKE_SLEEP"):
    import time
    time.sleep(float(os.environ["FAKE_SLEEP"]))
if account in os.environ.get("FAKE_LIMITED", "").split(","):
    print(json.dumps({{"is_error": True, "api_error_status": 429, "result": "You've hit your limit"}}))
else:
    print(json.dumps({{"result": json.dumps({{"ok": True}})}}))
"""


@pytest.fixture(autouse=True)
def accounts_on(monkeypatch):
    """`ndf` のエントリポイント（`ndf/__main__.py`）が差し込む中継の確かめを、テストでも差し込む。"""
    monkeypatch.setattr(claude, "relay_checks", plan_claude)


@pytest.fixture(autouse=True)
def launch_allowed(monkeypatch):
    """起動の口（学習に使わない設定の確かめと重い起動のスロット）を通す。アカウントごとの認証の文脈で確かめ直さない（ネットワークを使わない）。"""

    @contextlib.contextmanager
    def gate(agent, *, cwd=None, env=None):
        yield {}

    monkeypatch.setattr(launch_slot, "gate", gate)


@pytest.fixture(autouse=True)
def cli_version(monkeypatch):
    """起動する Claude Code のバージョン（`cli_floor` の記録）。偽のバージョンで、実の CLI を打たない。"""
    monkeypatch.setattr(account_choice, "claude_version", lambda env, cwd, exe_of=None: "2.1.330")


class Fake:
    """偽の claude と、その記録・届けの保存先。"""

    def __init__(self, tmp_path: Path, monkeypatch):
        self.tmp = tmp_path
        self.store = reg.store_dir({"CLAUDE_CONFIG_DIR": str(tmp_path / "claude")})
        self.out = tmp_path / "calls.json"
        self.exe = tmp_path / "fake-claude"
        self.exe.write_text(FAKE.format(python=sys.executable))
        self.exe.chmod(0o755)
        self.cwd = tmp_path / "work"
        self.cwd.mkdir()
        self.mp = monkeypatch
        for name in (*facts.ids("env", child="account-drop"), "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS", "NDF_RELAY_PIN"):
            monkeypatch.delenv(name, raising=False)
        for name in ("NDF_SUPERVISE_CLAUDE_FALLBACK", "NDF_ACCOUNTS_DIR", "NDF_CLAUDE_ACCOUNT", "NDF_SHARED_CONFIG_DIR"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("FAKE_OUT", str(self.out))
        monkeypatch.setenv("FAKE_LEASES", str(self.store / reg.LEASES_DIR))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))

    def calls(self) -> list[dict]:
        return json.loads(self.out.read_text()) if self.out.exists() else []

    def setenv(self, env: dict[str, str]) -> None:
        for k, v in env.items():
            self.mp.setenv(k, v)

    def call(self, *args: str, timeout: float = 30) -> claude.ClaudeReply:
        return claude.call_claude([str(self.exe), *args], "質問", timeout, cwd=self.cwd)

    def leases(self, name: str) -> list[Path]:
        return sorted((self.store / reg.LEASES_DIR / name).glob("*.json"))


def _state() -> Path:
    return identity.load().state_dir


def _account_parent(fake: Fake, name: str = "work1", generation: str = "g1") -> dict[str, str]:
    """ラッパーが登録済みアカウント `name` の子に渡す環境（`ndf plan` はこれを継ぐ）。"""
    parent = {"CLAUDE_CONFIG_DIR": str(fake.tmp / "claude")}
    child = session_env.child_env(parent, _state(), name, decided=True)
    got = session_env.account_env(child, fake.store / name, accounts.usage_path(_state(), name, generation))
    keep = (*facts.ids("env", child="account-set"), "NDF_CLAUDE_ACCOUNT")
    return {k: v for k, v in got.items() if k in keep}


def _project_settings(fake: Fake, data: dict) -> None:
    path = fake.cwd / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


FULL = ("-p", "--output-format", "json")


# ---------------------------------------------------------------- 登録済みアカウントの親（認証の上書き・使用の届け）


def test_3604_11_an_account_parent_launches_with_its_config_and_a_running_lease(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    register(tmp_path, "work1")
    fake.setenv(_account_parent(fake))

    got = fake.call(*FULL)

    assert got.ok and got.answer == {"ok": True}, got.error
    (seen,) = fake.calls()
    assert seen["account"] == "work1" and seen["config"] == str(fake.store / "work1")
    assert seen["cwd"] == str(fake.cwd) and seen["prompt"] == "質問"
    assert [(row["state"], row["child"]["pid"]) for row in seen["leases"]] == [("running", seen["pid"])]
    assert fake.leases("work1") == []


def test_3604_11_minimal_launch_ignores_a_project_api_key_helper(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    register(tmp_path, "work1")
    fake.setenv(_account_parent(fake))
    _project_settings(fake, {"apiKeyHelper": "echo fake-token"})

    got = fake.call(*claude.minimal_args("s"), "--tools", "")

    assert got.ok, got.error
    assert len(fake.calls()) == 1


def test_3604_11_a_project_api_key_helper_refuses_the_full_launch(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    register(tmp_path, "work1")
    fake.setenv(_account_parent(fake))
    _project_settings(fake, {"apiKeyHelper": "echo fake-token"})

    got = fake.call(*FULL)

    assert not got.ok and got.failure is claude.Failure.REFUSED
    assert "apiKeyHelper" in str(got.error)
    assert fake.calls() == [] and fake.leases("work1") == []


@pytest.mark.parametrize("extra", [{"ANTHROPIC_API_KEY": FAKE_KEY}, {"ANTHROPIC_BASE_URL": "https://gw.example.com"}])
def test_3604_11_minimal_launch_still_refuses_auth_in_the_env(tmp_path, monkeypatch, extra):
    fake = Fake(tmp_path, monkeypatch)
    register(tmp_path, "work1")
    fake.setenv({**_account_parent(fake), **extra})

    got = fake.call(*claude.minimal_args("s"), "--tools", "")

    assert not got.ok and got.failure is claude.Failure.REFUSED
    assert FAKE_KEY not in str(got.error)
    assert fake.calls() == []


def test_3604_11_a_parent_without_the_usage_file_does_not_launch(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    register(tmp_path, "work1")
    fake.setenv(_account_parent(fake))
    monkeypatch.delenv("NDF_ACCOUNT_USAGE_FILE")

    got = fake.call(*FULL)

    assert not got.ok and got.failure is claude.Failure.LAUNCH
    assert fake.calls() == [] and fake.leases("work1") == []


def test_3604_11_a_reregistered_account_does_not_launch_on_the_old_generation(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    register(tmp_path, "work1")
    fake.setenv(_account_parent(fake))
    register(tmp_path, "work1", generation="g2")

    got = fake.call(*FULL)

    assert not got.ok and got.failure is claude.Failure.LAUNCH and "reregistered" in str(got.error)
    assert fake.calls() == []


def test_3604_11_another_config_dir_does_not_launch(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    register(tmp_path, "work1", "work2")
    fake.setenv({**_account_parent(fake), "CLAUDE_CONFIG_DIR": str(fake.store / "work2")})

    got = fake.call(*FULL)

    assert not got.ok and got.failure is claude.Failure.LAUNCH and "config_mismatch" in str(got.error)
    assert fake.calls() == []


def test_3604_11_the_lease_is_gone_after_a_timeout(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    register(tmp_path, "work1")
    fake.setenv({**_account_parent(fake), "FAKE_SLEEP": "30"})

    got = fake.call(*FULL, timeout=2)

    assert got.failure is claude.Failure.TIMEOUT
    assert fake.leases("work1") == []


def test_default_login_does_not_load_the_relay_checks(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)

    class Boom:
        def begin(self, *args):
            raise AssertionError("既定のログインで中継の確かめを当てた")

        retry = begin

    monkeypatch.setattr(claude, "relay_checks", Boom())

    got = fake.call(*FULL)

    assert got.ok and fake.calls()[0]["account"] is None


# ---------------------------------------------------------------- 上限での 1 回の打ち直し（決定 9）


def test_d9_a_limited_account_is_retried_once_on_another_account(tmp_path, monkeypatch, capsys):
    fake = Fake(tmp_path, monkeypatch)
    register(tmp_path, "work1", "work2")
    fake.setenv({**_account_parent(fake), "FAKE_LIMITED": "work1"})

    got = fake.call(*FULL)

    assert got.ok, got.error
    first, second = fake.calls()
    assert (first["account"], second["account"]) == ("work1", "work2")
    assert second["config"] == str(fake.store / "work2")
    assert first["leases"] and [row["state"] for row in second["leases"]] == ["running"]
    assert fake.leases("work1") == [] and fake.leases("work2") == []
    assert "work1" in json.loads(accounts.limits_path(_state()).read_text(encoding="utf-8"))
    assert "work2 で打ち直す" in capsys.readouterr().err


def test_d9_the_retry_is_not_retried_again(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    register(tmp_path, "work1", "work2")
    fake.setenv({**_account_parent(fake), "FAKE_LIMITED": "work1,work2"})

    got = fake.call(*FULL)

    assert got.failure is claude.Failure.USAGE_LIMIT
    assert [c["account"] for c in fake.calls()] == ["work1", "work2"]
    assert fake.leases("work1") == [] and fake.leases("work2") == []


def test_d9_without_another_target_the_limit_is_returned(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    register(tmp_path, "work1")
    fake.setenv({**_account_parent(fake), "FAKE_LIMITED": "work1"})

    got = fake.call(*FULL)

    assert got.failure is claude.Failure.USAGE_LIMIT and len(fake.calls()) == 1


def test_d9_a_limited_account_moves_to_the_metered_connection(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    register(tmp_path, "work1")
    fake.setenv({**_account_parent(fake), "FAKE_LIMITED": "work1", "NDF_SUPERVISE_CLAUDE_FALLBACK": BEDROCK_DECL})

    got = fake.call(*FULL)

    assert got.ok, got.error
    second = fake.calls()[1]
    assert second["account"] == reg.METERED and second["pin"] and second["pin"].startswith("metered:")


def test_d9_the_retry_does_not_pick_an_api_key(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    register(tmp_path, "work1")
    fake.setenv({**_account_parent(fake), "FAKE_LIMITED": "work1", "NDF_SUPERVISE_CLAUDE_FALLBACK": f"ANTHROPIC_API_KEY={FAKE_KEY}"})

    got = fake.call(*FULL)

    assert got.failure is claude.Failure.USAGE_LIMIT and len(fake.calls()) == 1


# ---------------------------------------------------------------- 従量の接続の親（確かめと親の接続の識別）


def _metered_parent(fake: Fake, decl: str = BEDROCK_DECL) -> dict[str, str]:
    """ラッパーが従量の接続の子に渡す環境（`target_env` の `Built.env`。`NDF_RELAY_PIN` を持つ）。"""
    # 親の環境はラッパーのもの（`ndf plan` はこの子の環境をそのまま継ぐ。手元の AWS_PROFILE なども親の値のまま入る）
    environ = {**os.environ, "CLAUDE_CONFIG_DIR": str(fake.tmp / "claude"), "NDF_SUPERVISE_CLAUDE_FALLBACK": decl}
    child = session_env.child_env(environ, _state(), reg.METERED, decided=True)
    built = account_choice.target_env(child, reg.METERED, fake.store, _state(), fake.cwd, [str(fake.exe), *FULL])
    assert isinstance(built, account_choice.Built), built
    assert built.pin is not None and built.env["NDF_RELAY_PIN"] == built.pin
    return built.env


def test_d8_a_metered_parent_launches_on_the_same_connection(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    fake.setenv(_metered_parent(fake))

    got = fake.call(*FULL)

    assert got.ok, got.error
    (seen,) = fake.calls()
    assert seen["account"] == reg.METERED and seen["pin"].startswith("metered:")


def test_d8_a_changed_metered_setting_does_not_launch(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    fake.setenv(_metered_parent(fake))
    monkeypatch.setenv("NDF_SUPERVISE_CLAUDE_FALLBACK", "CLAUDE_CODE_USE_BEDROCK=1 AWS_REGION=us-west-2")

    got = fake.call(*FULL)

    assert not got.ok and got.failure is claude.Failure.REFUSED and "metered_changed" in str(got.error)
    assert fake.calls() == []


def test_d8_a_metered_parent_without_the_pin_does_not_launch(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    fake.setenv(_metered_parent(fake))
    monkeypatch.delenv("NDF_RELAY_PIN")

    got = fake.call(*FULL)

    assert got.failure is claude.Failure.REFUSED and "metered_changed" in str(got.error)
    assert fake.calls() == []


def test_d8_a_project_setting_that_moves_the_endpoint_refuses_the_metered_launch(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    fake.setenv(_metered_parent(fake))
    _project_settings(fake, {"env": {"ANTHROPIC_BASE_URL": "https://other.example.com"}})

    got = fake.call(*FULL)

    assert got.failure is claude.Failure.REFUSED and "metered_overridden" in str(got.error)
    assert fake.calls() == []


def test_the_pin_is_not_passed_to_an_account_or_default_child(tmp_path):
    environ = {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude"), "NDF_RELAY_PIN": "metered:abc"}
    assert "NDF_RELAY_PIN" not in session_env.child_env(environ, tmp_path, None, decided=True)
    child = session_env.child_env(environ, tmp_path, "work1", decided=True)
    assert "NDF_RELAY_PIN" not in session_env.account_env({**child, "NDF_RELAY_PIN": "x"}, tmp_path / "w", tmp_path / "u.json")


# ---------------------------------------------------------------- API キーの親（キーの子は起動しない）


def put_book(store: Path, generation: str = "gen-1") -> None:
    store.mkdir(parents=True, exist_ok=True)
    row = {"key": FAKE_KEY, "account": "work1", "added_at": "2026-11-22T00:00:00Z", "generation": generation}
    reg.write(reg.book_path(store), {"version": 1, "use": "near-limit", "keys": {"key-main": row}})


FORMS = [
    list(claude.PRINT_JSON),
    list(claude.PRINT_STREAM),
    claude.minimal_args("s"),
    claude.minimal_args("s", stream=True),
]


@pytest.mark.parametrize("form", FORMS, ids=["json", "stream", "min-json", "min-stream"])
def test_d12_no_launch_form_gets_the_api_key(tmp_path, monkeypatch, form):
    """U11a の実機の確かめ（Claude Code 2.1.330）で、4 つの形はどれも標準入力の前に helper を呼ばなかった。"""
    fake = Fake(tmp_path, monkeypatch)
    register(tmp_path, "work1")
    put_book(fake.store)
    fake.setenv({"NDF_CLAUDE_ACCOUNT": "key:key-main", "NDF_RELAY_PIN": "key:key-main:gen-1"})

    got = fake.call(*form)

    assert not got.ok and got.failure is claude.Failure.REFUSED and "key_gate" in str(got.error)
    assert FAKE_KEY not in str(got.error) and fake.calls() == []


def test_d12_a_metered_api_key_parent_does_not_launch(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    fake.setenv({"NDF_CLAUDE_ACCOUNT": reg.METERED, "NDF_SUPERVISE_CLAUDE_FALLBACK": f"ANTHROPIC_API_KEY={FAKE_KEY}"})

    got = fake.call(*FULL)

    assert got.failure is claude.Failure.REFUSED and "key_gate" in str(got.error)
    assert FAKE_KEY not in str(got.error) and fake.calls() == []


def _key_env(fake: Fake) -> dict[str, str]:
    environ = {"CLAUDE_CONFIG_DIR": str(fake.tmp / "claude")}
    child = session_env.child_env(environ, _state(), "key:key-main", decided=True)
    built = account_choice.target_env(child, "key:key-main", fake.store, _state(), fake.cwd, [str(fake.exe), *FULL])
    assert isinstance(built, account_choice.Built), built
    assert built.pin == "key:key-main:gen-1"
    for path in (built.settings_file, built.pin_file):
        if path is not None:
            path.unlink(missing_ok=True)
    return {**environ, **built.env}


def test_d12_a_nested_key_launch_has_the_pin_but_no_record(tmp_path, monkeypatch):
    """起動の形がキーを渡せるとき（今は無い）の合成: helper と hook に `--pin` があり、`--record`・`--id` が無い。"""
    fake = Fake(tmp_path, monkeypatch)
    put_book(fake.store)
    env = _key_env(fake)

    run = plan_claude.begin([str(fake.exe), *FULL], env, fake.cwd, True)

    assert isinstance(run, claude.AccountRun), run
    settings = json.loads(run.argv[run.argv.index("--settings") + 1])
    hook = settings["hooks"]["ConfigChange"][-1]["hooks"][0]
    for words in (shlex.split(settings["apiKeyHelper"]), shlex.split(hook["command"])):
        assert "--pin" in words and "--record" not in words and "--id" not in words
    assert hook["onFailure"] == "block" and "ANTHROPIC_API_KEY" not in run.env
    pin = Path(shlex.split(settings["apiKeyHelper"])[-1])
    assert pin.exists()
    run.end(True)
    assert not pin.exists()


def test_d12_a_reregistered_key_does_not_launch(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    put_book(fake.store)
    env = _key_env(fake)
    put_book(fake.store, generation="gen-2")

    got = plan_claude.begin([str(fake.exe), *FULL], env, fake.cwd, True)

    assert isinstance(got, claude.AccountRefused) and got.failure is claude.Failure.REFUSED and "key_changed" in got.detail
    assert list(account_choice.settings_path(_state()).glob("*.pin.json")) == []


def test_d12_a_missing_key_does_not_launch(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch)
    put_book(fake.store)
    env = _key_env(fake)
    reg.write(reg.book_path(fake.store), {"version": 1, "use": "near-limit", "keys": {}})

    got = plan_claude.begin([str(fake.exe), *FULL], env, fake.cwd, True)

    assert isinstance(got, claude.AccountRefused) and "key_missing" in got.detail and FAKE_KEY not in got.detail
