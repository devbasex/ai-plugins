"""従量の接続への退避の部品（U11a の 2 本目。#3604 #3864 の受け入れ条件 #3864 の 9・決定 8・I9）。

- 従量の接続の設定の検証（詳細設計書の「従量の接続の設定の検証」）と、子の環境（`metered_env`。API キーの設定はキーを外す）
- `build`・`verify`・`fallbacks`・`target_env`: API キーの設定の `--settings` の合成（helper と `config-gate` の hook）・確かめ・固定のファイル
- helper の固定のファイルの確かめ（`--pin`）と `config-gate`（別冊の「確かめた設定への固定」「反映の前に拒む」）
値はすべて偽の値で、実のキーとアカウントを使わない。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shlex
import subprocess
import sys

import pytest

from ndf.contracts import limits
from ndf.domains.relay import account_choice, account_dir, cli, key_helper, session_env
from ndf.domains.relay import account_registry as reg
from relay_children import make_settings, register

FAKE_KEY = "sk-ant-api03-fake-test-key-not-real"
FP = hashlib.sha256(FAKE_KEY.encode()).hexdigest()
KEY_DECL = f"ANTHROPIC_API_KEY={FAKE_KEY}"
BEDROCK_DECL = "CLAUDE_CODE_USE_BEDROCK=1 AWS_REGION=us-east-1"


@pytest.fixture(autouse=True)
def cli_version(monkeypatch):
    """起動する Claude Code のバージョン（`cli_floor` の記録）。偽のバージョンで、実の CLI を打たない。"""
    monkeypatch.setattr(account_choice, "claude_version", lambda env, cwd: "2.1.330")


def _env(tmp_path, decl=KEY_DECL, **extra):
    s = make_settings(tmp_path)
    environ = {**s.environ, **extra}
    if decl is not None:
        environ["NDF_SUPERVISE_CLAUDE_FALLBACK"] = decl
    return s, environ, session_env.child_env(environ, s.state_dir, reg.METERED, decided=True)


def _settings_of(argv: list[str]) -> dict:
    values = [argv[i + 1] for i, w in enumerate(argv) if w == "--settings"]
    assert len(values) == 1, argv
    raw = values[0]
    return json.loads(raw if raw.lstrip().startswith("{") else open(raw, encoding="utf-8").read())


def _user_settings(tmp_path, data) -> None:
    path = tmp_path / "claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------- 従量の接続の設定の検証


@pytest.mark.parametrize(
    "decl",
    [
        BEDROCK_DECL,
        KEY_DECL,
        f"{KEY_DECL} ANTHROPIC_BASE_URL=https://gw.example.com",
        "CLAUDE_CODE_USE_BEDROCK=1 CLAUDE_CODE_USE_VERTEX=0",
        f"CLAUDE_CODE_USE_BEDROCK=0 {KEY_DECL}",
    ],
    ids=["bedrock", "api-key", "api-key-with-base-url", "bedrock-with-vertex-off", "bedrock-off-with-key"],
)
def test_3864_9_a_valid_metered_setting_is_used(tmp_path, decl):
    store = register(tmp_path)
    _, environ, _ = _env(tmp_path, decl)
    got = account_choice.metered_decl(store, environ)
    assert isinstance(got, tuple) and got[1] is False and got[0] == dict(t.split("=", 1) for t in shlex.split(decl))


@pytest.mark.parametrize(
    "decl, code",
    [
        ("ANTHROPIC_API_KEY='unclosed", "unparsable"),
        (f"{KEY_DECL} stray", "unparsable"),
        (f"{KEY_DECL} lower_case=1", "unparsable"),
        ("", "empty"),
        ("   ", "empty"),
        (f"{KEY_DECL} CLAUDE_CONFIG_DIR=/tmp/x", "fixed"),
        (f"{BEDROCK_DECL} NDF_RELAY_DIR=/tmp/x", "fixed"),
        ("AWS_REGION=us-east-1", "unclassified"),
        ("CLAUDE_CODE_USE_BEDROCK=1 CLAUDE_CODE_USE_VERTEX=1", "unclassified"),
        ("CLAUDE_CODE_USE_BEDROCK=0", "unclassified"),
        ("ANTHROPIC_API_KEY=", "unclassified"),
        ("CLAUDE_CODE_USE_VERTEX=maybe", "unclassified"),
        (f"{KEY_DECL} ANTHROPIC_CUSTOM_HEADERS=X-A:1", "headers"),
    ],
)
def test_3864_9_a_broken_metered_setting_is_treated_as_no_setting(tmp_path, decl, code):
    # 1 つでも外れれば設定なし。理由は符号だけで、値を含まない
    store = register(tmp_path)
    _, environ, _ = _env(tmp_path, decl)
    assert account_choice.metered_decl(store, environ) == code


def test_3864_9_no_metered_setting_is_none(tmp_path):
    store = register(tmp_path)
    _, environ, _ = _env(tmp_path, None)
    assert account_choice.metered_decl(store, environ) is None


def test_3864_9_a_saved_metered_setting_is_read_as_saved(tmp_path):
    store = register(tmp_path)
    reg.save_metered(store, "bedrock", {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "prod"}, {"model": "x"}, 0.0)
    _, environ, _ = _env(tmp_path, None)
    assert account_choice.metered_decl(store, environ) == ({"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "prod"}, True)


# ---------------------------------------------------------------- 子の環境


def test_3864_9_metered_env_of_an_api_key_setting_drops_the_key_and_the_parent_endpoints(tmp_path):
    # 別冊の `metered_env`（API キーの設定）: キーを環境へ入れず、親だけにある接続先・ヘッダー・資格情報の選択を残さない
    parent = {"ANTHROPIC_BASE_URL": "https://parent.example.com", "ANTHROPIC_CUSTOM_HEADERS": "X-P:1", "AWS_PROFILE": "p"}
    _, _, env = _env(tmp_path, **parent)
    extra = {"ANTHROPIC_API_KEY": FAKE_KEY, "ANTHROPIC_BASE_URL": "https://gw.example.com"}
    got = session_env.metered_env(env, extra, saved=False)
    assert "ANTHROPIC_API_KEY" not in got and got["ANTHROPIC_BASE_URL"] == "https://gw.example.com"
    assert "ANTHROPIC_CUSTOM_HEADERS" not in got and "AWS_PROFILE" not in got
    # キーは設定の変数（helper が自分の環境から読む。別冊の `api-key-helper metered <指紋>` の行）のほかには入らない
    others = {k: v for k, v in got.items() if k != "NDF_SUPERVISE_CLAUDE_FALLBACK"}
    assert got["NDF_CLAUDE_ACCOUNT"] == reg.METERED and FAKE_KEY not in json.dumps(others)


def test_3864_9_metered_env_of_a_provider_setting_keeps_the_parent_credentials_choice(tmp_path):
    _, _, env = _env(tmp_path, BEDROCK_DECL, AWS_PROFILE="p")
    got = session_env.metered_env(env, {"CLAUDE_CODE_USE_BEDROCK": "1"}, saved=False)
    assert got["AWS_PROFILE"] == "p" and got["CLAUDE_CODE_USE_BEDROCK"] == "1"


# ---------------------------------------------------------------- build・verify


def test_3864_9_build_puts_the_metered_helper_and_the_gate_hook_into_one_settings(tmp_path):
    store = register(tmp_path)
    s, _, env = _env(tmp_path)
    got = account_choice.build(env, reg.METERED, store, s.state_dir, tmp_path, ["claude", "--settings", '{"model": "x"}'])
    assert not isinstance(got, str)
    child, argv = got
    data = _settings_of(argv)
    assert data["model"] == "x"
    assert data["apiKeyHelper"] == shlex.join([account_choice.NDF_EXE, "relay", "account", "api-key-helper", "metered", FP])
    (group,) = data["hooks"]["ConfigChange"]
    (hook,) = group["hooks"]
    assert hook["type"] == "command" and hook["onFailure"] == "block"
    assert hook["timeout"] == limits.value(key_helper.POLICY, "gate")
    assert shlex.split(hook["command"])[1:] == ["relay", "account", "config-gate"]
    assert FAKE_KEY not in json.dumps(argv) and "ANTHROPIC_API_KEY" not in child


def test_3864_9_build_keeps_other_hooks_and_appends_to_an_existing_config_change(tmp_path):
    store = register(tmp_path)
    s, _, env = _env(tmp_path)
    mine = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}], "ConfigChange": [{"hooks": []}]}}
    child, argv = account_choice.build(env, reg.METERED, store, s.state_dir, tmp_path, ["claude", "--settings", json.dumps(mine)])
    data = _settings_of(argv)
    assert data["hooks"]["Stop"] == mine["hooks"]["Stop"] and len(data["hooks"]["ConfigChange"]) == 2


@pytest.mark.parametrize(
    "argv, code",
    [
        (["claude", "--settings", '{"apiKeyHelper": "x"}'], "key_overridden"),
        (["claude", "--settings", "{}", "--settings", "{}"], "key_unresolved"),
        (["claude", "--settings", '{"hooks": []}'], "key_unresolved"),
    ],
    ids=["own-helper", "two-settings", "hooks-not-object"],
)
def test_3864_9_build_refuses_settings_it_cannot_compose(tmp_path, argv, code):
    store = register(tmp_path)
    s, _, env = _env(tmp_path)
    assert account_choice.build(env, reg.METERED, store, s.state_dir, tmp_path, argv) == code


def test_3864_9_build_of_a_provider_setting_adds_no_settings(tmp_path):
    store = register(tmp_path)
    s, _, env = _env(tmp_path, BEDROCK_DECL)
    child, argv = account_choice.build(env, reg.METERED, store, s.state_dir, tmp_path, ["claude"])
    assert argv == ["claude"] and child["CLAUDE_CODE_USE_BEDROCK"] == "1"


def _fallbacks(tmp_path, decl=KEY_DECL, tried=frozenset(), argv=("claude",)):
    store = register(tmp_path)
    s, environ, _ = _env(tmp_path, decl)
    metered = account_choice.metered_decl(store, environ)
    metered = metered if isinstance(metered, tuple) else None
    return account_choice.fallbacks(store, environ, metered, tmp_path, list(argv), tried)


def test_3864_9_fallbacks_checks_the_metered_connection(tmp_path):
    (probe,) = _fallbacks(tmp_path)
    assert probe.name == reg.METERED and probe.reason is None
    assert str(reg.metered_path(register(tmp_path))) in probe.stamps


def test_3864_9_fallbacks_skips_a_tried_connection(tmp_path):
    assert _fallbacks(tmp_path, tried=frozenset({reg.METERED})) == []


@pytest.mark.parametrize(
    "settings",
    [
        {"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}},
        {"env": {"ANTHROPIC_CUSTOM_HEADERS": "X-A:1"}},
        {"env": {"NDF_SUPERVISE_CLAUDE_FALLBACK": "ANTHROPIC_API_KEY=sk-ant-api03-fake-other"}},
        {"env": {"ANTHROPIC_AUTH_TOKEN": "fake-token"}},
    ],
    ids=["provider", "headers", "declaration-replaced", "auth-token"],
)
def test_3864_9_fallbacks_drops_metered_overridden_by_a_settings_file(tmp_path, settings):
    _user_settings(tmp_path, settings)
    (probe,) = _fallbacks(tmp_path)
    assert probe.reason == "metered_overridden"
    assert str(tmp_path / "claude" / "settings.json") in probe.stamps


def test_3864_9_fallbacks_lets_the_same_provider_in_a_settings_file_through(tmp_path):
    _user_settings(tmp_path, {"env": {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-east-1"}})
    (probe,) = _fallbacks(tmp_path, BEDROCK_DECL)
    assert probe.reason is None


@pytest.mark.parametrize(
    "case",
    ["old-cli", "unknown-cli", "safe-mode", "bare", "hooks-off", "simple"],
)
def test_i9_verify_refuses_a_key_child_without_a_working_gate(tmp_path, monkeypatch, case):
    # 別冊の「起動できるバージョン」「起動の前の確かめ」: 関所の働かないキーの子を起動しない
    argv = ["claude"]
    if case == "old-cli":
        monkeypatch.setattr(account_choice, "claude_version", lambda env, cwd: "2.1.294")
    elif case == "unknown-cli":
        monkeypatch.setattr(account_choice, "claude_version", lambda env, cwd: None)
    elif case == "safe-mode":
        argv.append("--safe-mode")
    elif case == "bare":
        argv.append("--bare")
    elif case == "hooks-off":
        _user_settings(tmp_path, {"disableAllHooks": True})
    else:
        _user_settings(tmp_path, {"env": {"CLAUDE_CODE_SIMPLE": "1"}})
    (probe,) = _fallbacks(tmp_path, argv=argv)
    assert probe.reason == "key_gate"


def test_i9_the_check_and_the_launch_carry_the_same_helper_except_the_pin_tail(tmp_path):
    # 別冊の単体テストの観点 (vi): 確かめ用の起動引数は --pin・--record・--id を持たず、target_env の起動引数は持つ
    store = register(tmp_path)
    s, _, env = _env(tmp_path)
    check = account_choice.build(env, reg.METERED, store, s.state_dir, tmp_path, ["claude"])[1]
    helper = shlex.split(_settings_of(check)["apiKeyHelper"])
    assert not {"--pin", "--record", "--id"} & set(helper)
    record = (tmp_path / "rec.jsonl", "id-1")
    got = account_choice.key_target(env, store, s.state_dir, tmp_path, ["claude"], record)
    assert isinstance(got, account_choice.Built), got
    data = _settings_of(got.argv)
    words = shlex.split(data["apiKeyHelper"])
    assert words[: len(helper)] == helper
    assert words[len(helper) :] == ["--pin", str(got.pin_file), "--record", str(record[0]), "--id", "id-1"]
    gate = shlex.split(data["hooks"]["ConfigChange"][-1]["hooks"][0]["command"])
    assert gate[-6:] == words[-6:]
    assert got.pin_file.is_file() and (got.pin_file.stat().st_mode & 0o777) == 0o600
    assert got.pin_file.parent == account_choice.settings_path(s.state_dir)


def test_i9_target_env_writes_a_settings_file_and_keeps_the_original(tmp_path):
    store = register(tmp_path)
    s, _, env = _env(tmp_path)
    own = tmp_path / "own.json"
    own.write_text(json.dumps({"env": {"BASH_DEFAULT_TIMEOUT_MS": "1000"}}), encoding="utf-8")
    before = own.read_text()
    got = account_choice.target_env(env, reg.METERED, store, s.state_dir, tmp_path, ["claude", "--settings", "own.json"])
    assert isinstance(got, account_choice.Built), got
    value = got.argv[got.argv.index("--settings") + 1]
    assert value == str(got.settings_file) and got.settings_file.parent == account_choice.settings_path(s.state_dir)
    assert (got.settings_file.stat().st_mode & 0o777) == 0o600 and own.read_text() == before
    assert "1000" not in json.dumps(got.argv) and _settings_of(got.argv)["env"] == {"BASH_DEFAULT_TIMEOUT_MS": "1000"}
    assert got.settings_file.name.removesuffix(".json") == got.pin_file.name.removesuffix(".pin.json")


def test_i9_target_env_refused_leaves_no_files(tmp_path):
    store = register(tmp_path)
    s, _, env = _env(tmp_path)
    (tmp_path / "own.json").write_text("{}", encoding="utf-8")
    _user_settings(tmp_path, {"env": {"ANTHROPIC_BASE_URL": "https://gateway.example.com"}})
    got = account_choice.target_env(env, reg.METERED, store, s.state_dir, tmp_path, ["claude", "--settings", "own.json"])
    assert got == "metered_overridden"
    assert list((account_choice.settings_path(s.state_dir)).iterdir()) == []


def test_i9_target_env_removes_only_the_leftovers_of_ended_launches(tmp_path):
    # 一時ファイルと固定のファイルの残骸: 起動した側が終わり、そのパスを起動引数に持つプロセスも無いものだけを消す
    store = register(tmp_path)
    s, _, env = _env(tmp_path)
    d = account_choice.settings_path(s.state_dir)
    d.mkdir(parents=True, mode=0o700)
    dead = d / "999999999-1.00-00000000000000aa"
    stale = [d / f"{dead.name}.json", d / f"{dead.name}.pin.json", d / f"{dead.name}.pin.ok"]
    for p in stale:
        p.write_text("{}")
    live_stem = "999999999-1.00-00000000000000bb"
    used = d / f"{live_stem}.pin.json"
    used.write_text("{}")
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", json.dumps({"apiKeyHelper": f"x --pin {used}"})])
    me = d / f"{os.getpid()}-{reg.proc_start(os.getpid()):.2f}-00000000000000cc.json"
    me.write_text("{}")
    try:
        got = account_choice.target_env(env, reg.METERED, store, s.state_dir, tmp_path, ["claude"])
        assert isinstance(got, account_choice.Built), got
        assert not any(p.exists() for p in stale) and used.exists() and me.exists()
    finally:
        holder.kill()
        holder.wait()


# ---------------------------------------------------------------- helper の固定のファイルの確かめと config-gate


def _launched(tmp_path, monkeypatch, record=True):
    """target_env が作った固定のファイルと、helper・config-gate を起動する環境（子の環境に寄せる）。"""
    store = register(tmp_path)
    s, _, env = _env(tmp_path)
    d = tmp_path / "relay"
    d.mkdir(mode=0o700)
    os.chmod(d, 0o700)
    rec = key_helper.create(d) if record else None
    got = account_choice.key_target(env, store, s.state_dir, tmp_path, ["claude"], (rec.path, rec.helper_id) if rec else None)
    assert isinstance(got, account_choice.Built), got
    for k, v in got.env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)
    tail = ["--pin", str(got.pin_file)] + (["--record", str(rec.path), "--id", rec.helper_id] if rec else [])
    return got, rec, tail


def _rows(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_i9_the_helper_gives_the_key_while_the_settings_match_the_pin(tmp_path, monkeypatch, capsys):
    got, rec, tail = _launched(tmp_path, monkeypatch)
    assert cli.main(["account", "api-key-helper", "metered", FP, *tail]) == 0
    assert capsys.readouterr().out == FAKE_KEY
    _user_settings(tmp_path, {"env": {"BASH_DEFAULT_TIMEOUT_MS": "1000"}})  # 比べる範囲の外
    assert cli.main(["account", "api-key-helper", "metered", FP, *tail]) == 0


@pytest.mark.parametrize(
    "settings",
    [
        {"env": {"ANTHROPIC_BASE_URL": "https://gateway.example.com"}},
        {"env": {"CLAUDE_CODE_SIMPLE": "1"}},
        {"env": {"CLAUDE_CODE_SAFE_MODE": "1"}},
        {"disableAllHooks": True},
    ],
    ids=["base-url", "simple", "safe-mode", "hooks-off"],
)
def test_i9_the_helper_refuses_after_the_settings_change_since_the_check(tmp_path, monkeypatch, capsys, settings):
    # 別冊の単体テストの観点 (v): 確かめの後に利用者の設定を変えると、最初の helper が `cause: pin` で出さない
    got, rec, tail = _launched(tmp_path, monkeypatch)
    _user_settings(tmp_path, settings)
    assert cli.main(["account", "api-key-helper", "metered", FP, *tail]) == 1
    out = capsys.readouterr()
    assert out.out == "" and FAKE_KEY not in out.err
    assert _rows(rec.path)[-1] == {"id": rec.helper_id, "at": _rows(rec.path)[-1]["at"], "out": False, "cause": "pin"}
    assert key_helper.watch(rec.path.parent, rec).source == key_helper.SETTINGS_FILE


def test_i9_the_helper_without_a_pin_gives_nothing(tmp_path, monkeypatch, capsys):
    got, rec, tail = _launched(tmp_path, monkeypatch)
    assert cli.main(["account", "api-key-helper", "metered", FP, *tail[2:]]) == 1
    os.chmod(got.pin_file, 0o644)
    assert cli.main(["account", "api-key-helper", "metered", FP, "--pin", str(got.pin_file)]) == 1
    assert capsys.readouterr().out == ""


def test_i9_the_helper_of_a_nested_plan_marks_the_payout(tmp_path, monkeypatch, capsys):
    # `--record` の無い起動（入れ子の `ndf plan`）は、出す前に同じ stem の `.pin.ok` を作る
    got, _, tail = _launched(tmp_path, monkeypatch, record=False)
    assert cli.main(["account", "api-key-helper", "metered", FP, *tail]) == 0
    assert capsys.readouterr().out == FAKE_KEY
    assert got.pin_file.with_name(got.pin_file.name.removesuffix(".json") + ".ok").is_file()


def _gate(monkeypatch, tail, payload) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    return cli.main(["account", "config-gate", *tail])


def test_i9_config_gate_refuses_a_change_of_the_endpoint_and_writes_the_gate_row(tmp_path, monkeypatch):
    # 別冊の単体テストの観点 (iv): 接続先を変える保存は反映の前に拒み（終了コード 2 と gate の行）、ほかの保存は通す
    got, rec, tail = _launched(tmp_path, monkeypatch)
    path = tmp_path / "claude" / "settings.json"
    _user_settings(tmp_path, {"permissions": {"allow": ["Bash(ls:*)"]}})
    assert _gate(monkeypatch, tail, {"source": "user_settings", "file_path": str(path)}) == 0
    assert _gate(monkeypatch, tail, {"source": "skills", "file_path": str(tmp_path / ".claude" / "skills" / "x")}) == 0
    assert _gate(monkeypatch, tail, {"source": "policy_settings", "file_path": "/etc/claude-code/managed-settings.json"}) == 0
    assert not any("gate" in r for r in _rows(rec.path))
    _user_settings(tmp_path, {"env": {"ANTHROPIC_BASE_URL": "https://gateway.example.com"}})
    assert _gate(monkeypatch, tail, {"source": "user_settings", "file_path": str(path)}) == 2
    assert {"id": rec.helper_id, "gate": "user_settings"} in _rows(rec.path)
    assert key_helper.watch(rec.path.parent, rec).source == "user_settings"


@pytest.mark.parametrize(
    "payload",
    [{"source": "user_settings", "file_path": "/nowhere/settings.json"}, {"source": "project_settings"}, "not json"],
    ids=["unknown-path", "no-path", "broken-input"],
)
def test_i9_config_gate_refuses_what_it_cannot_check(tmp_path, monkeypatch, payload):
    got, rec, tail = _launched(tmp_path, monkeypatch)
    if isinstance(payload, str):
        monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
        assert cli.main(["account", "config-gate", *tail]) == 2
    else:
        assert _gate(monkeypatch, tail, payload) == 2


def test_i9_config_gate_of_a_nested_plan_refuses_without_a_row(tmp_path, monkeypatch):
    got, _, tail = _launched(tmp_path, monkeypatch, record=False)
    _user_settings(tmp_path, {"env": {"ANTHROPIC_BASE_URL": "https://gateway.example.com"}})
    path = tmp_path / "claude" / "settings.json"
    assert _gate(monkeypatch, tail, {"source": "user_settings", "file_path": str(path)}) == 2


def test_i9_effective_env_keeps_the_gate_view_of_each_settings_file(tmp_path):
    # `Resolved.pins`: 読んだか探した設定ファイルごとに比べる範囲の SHA-256（無いファイルは空の object と同じ）
    _user_settings(tmp_path, {"env": {"ANTHROPIC_BASE_URL": "https://a.example.com", "BASH_DEFAULT_TIMEOUT_MS": "1"}, "model": "x"})
    r = account_dir.effective_env({}, tmp_path / "claude", tmp_path, ["claude"], pin=True)
    user = str(tmp_path / "claude" / "settings.json")
    assert r.pins[user] == key_helper.view_sha({"env": {"ANTHROPIC_BASE_URL": "https://a.example.com"}})
    assert r.pins[str(tmp_path / ".claude" / "settings.json")] == key_helper.view_sha({})


def test_i9_effective_env_makes_the_pins_only_for_a_key_child_check(tmp_path):
    # 固定の SHA-256 と一覧はキーの子の照合（`pin=True`）でだけ作る
    _user_settings(tmp_path, {"env": {"ANTHROPIC_BASE_URL": "https://a.example.com"}})
    r = account_dir.effective_env({}, tmp_path / "claude", tmp_path, ["claude"])
    assert r.pins == {} and r.dirs == {}


def test_i9_the_pinned_listing_is_the_one_the_settings_were_read_from(tmp_path, monkeypatch):
    # 管理の設定のディレクトリは 1 回だけ列挙し、その一覧を固定する（別の列挙で読んでいないファイルを一覧に入れない）
    d = account_dir.MANAGED_DIR
    d.mkdir(parents=True)
    (d / "a.json").write_text("{}", encoding="utf-8")
    calls = []
    real = key_helper.listing

    def listing(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(key_helper, "listing", listing)
    r = account_dir.effective_env({}, tmp_path / "claude", tmp_path, ["claude"], pin=True)
    assert r.dirs == {str(d): ["a.json"]} and calls == [d]


def _symlinked_user_settings(tmp_path, data) -> None:
    """dotfiles の管理と同じく、利用者の設定を別の場所の実体への symlink にする。"""
    real = tmp_path / "dotfiles" / "settings.json"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text(json.dumps(data), encoding="utf-8")
    path = tmp_path / "claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(real)


def test_i9_a_symlinked_user_settings_passes_the_helper_and_the_gate(tmp_path, monkeypatch, capsys):
    # 確かめ（`_layer`）と照合（`file_sha`）がどちらも symlink を辿る。設定が同じなら最初の払い出しが通り、保存も通す
    _symlinked_user_settings(tmp_path, {"permissions": {"allow": ["Bash(ls:*)"]}})
    got, rec, tail = _launched(tmp_path, monkeypatch)
    assert cli.main(["account", "api-key-helper", "metered", FP, *tail]) == 0
    assert capsys.readouterr().out == FAKE_KEY
    path = tmp_path / "claude" / "settings.json"
    assert _gate(monkeypatch, tail, {"source": "user_settings", "file_path": str(path)}) == 0
    (tmp_path / "dotfiles" / "settings.json").write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://gw.example.com"}}))
    assert _gate(monkeypatch, tail, {"source": "user_settings", "file_path": str(path)}) == 2
    assert cli.main(["account", "api-key-helper", "metered", FP, *tail]) == 1


def test_i9_a_settings_file_that_cannot_be_pinned_drops_the_key_child_before_the_stop(tmp_path):
    # 照らせない設定ファイル（JSON の object でない）では最初の払い出しが拒まれるので、旧子を止める前に候補から外す
    path = tmp_path / "claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]", encoding="utf-8")
    (probe,) = _fallbacks(tmp_path)
    assert probe.reason == "key_pin" and str(path) in probe.stamps


def test_i9_config_gate_passes_a_managed_change_it_cannot_refuse(tmp_path, monkeypatch):
    # 別冊の「拒めない変更」: 管理の設定の変更は Claude Code が拒ませないので、固定から外れても通し、gate の行を書かない
    # （検出は見回りと次の払い出しの helper の固定の確かめに残す）
    got, rec, tail = _launched(tmp_path, monkeypatch)
    managed = account_dir.MANAGED_FILES[0]
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://gw.example.com"}}), encoding="utf-8")
    assert _gate(monkeypatch, tail, {"source": "policy_settings", "file_path": str(managed)}) == 0
    assert not any("gate" in r for r in _rows(rec.path))
    assert not key_helper.pin_ok(got.pin_file)  # 次の払い出しの固定の確かめでは外れる


@pytest.mark.parametrize("how", ["source-fixed", "source-broken"])
def test_3864_9_fallbacks_depends_on_the_original_settings_file(tmp_path, how):
    # 合成で JSON の文字列に置き換わる `--settings` の元のファイルも、通っても外れても依存先に入る
    own = tmp_path / "own.json"
    own.write_text("{}" if how == "source-fixed" else "[", encoding="utf-8")
    (probe,) = _fallbacks(tmp_path, argv=["claude", "--settings", "own.json"])
    assert (probe.reason is None) == (how == "source-fixed")
    assert str(own) in probe.stamps
