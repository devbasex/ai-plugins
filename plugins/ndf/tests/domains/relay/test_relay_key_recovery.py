"""API キーの復帰先と設定の変更の検出（U11b。#3604 #3864 の決定 12）。

- 登録簿の API キー（`key:<名前>`）を、従量の接続の後の復帰先にする（使い方が `off` でないキーだけ）。キーは子の環境へ入れず、
  `--settings` の `apiKeyHelper`（`api-key-helper <名前> --generation <世代>`）で渡す
- helper は登録簿の世代が起動のときと同じで、使い方が `off` でないときだけキーを出す
- 動いているキーの子で、登録簿のキーの削除・登録し直し・`off` は止めてよい時点を待って止め、接続先・資格情報・helper の
  変更は検出した見回りで止める（別冊の「設定の変更でキーの子を止める範囲」）
値はすべて偽の値で、実のキー・アカウント・ネットワークを使わない。
"""

from __future__ import annotations

import io
import json
import os
import shlex

import pytest

from types import SimpleNamespace

from ndf.domains.relay import account_choice, account_decide, account_dir, cli, key_helper, launch_inputs, session_env, wrapper
from ndf.domains.relay import wrapper_guard, wrapper_stop
from ndf.domains.relay import account_registry as reg
from relay_children import LIMIT_LINE, FakeChild, Spawner, clock_ticks, make_settings, register

KEY_MAIN = "sk-ant-api03-fake-key-main-not-real"
KEY_NEW = "sk-ant-api03-fake-key-new-not-real"
KEY_TWO = "sk-ant-api03-fake-key-two-not-real"
GATEWAY = "https://gateway.example.com"


@pytest.fixture(autouse=True)
def cli_version(monkeypatch):
    """起動する Claude Code のバージョン（`cli_floor` の記録）。偽のバージョンで、実の CLI を打たない。"""
    monkeypatch.setattr(account_choice, "claude_version", lambda env, cwd, exe_of=None: "2.1.330")


def put_book(store, use="near-limit", keys=None):
    """API キーの登録簿を書く。`keys` は名前 → (キーの値, 世代)。"""
    keys = {"key-main": (KEY_MAIN, "gen-1")} if keys is None else keys
    store.mkdir(parents=True, exist_ok=True)
    rows = {n: {"key": k, "account": "work1", "added_at": "2026-11-22T00:00:00Z", "generation": g} for n, (k, g) in keys.items()}
    reg.write(reg.book_path(store), {"version": 1, "use": use, "keys": rows})


def _user_settings(tmp_path, data) -> None:
    path = tmp_path / "claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _settings_of(argv: list[str]) -> dict:
    raw = argv[argv.index("--settings") + 1]
    return json.loads(raw if raw.lstrip().startswith("{") else open(raw, encoding="utf-8").read())


def _child(tmp_path, name="key-main", **extra):
    s = make_settings(tmp_path)
    environ = {**s.environ, **extra}
    return s, environ, session_env.child_env(environ, s.state_dir, f"key:{name}", decided=True)


# ---------------------------------------------------------------- 登録簿（recovery_keys・key_generation・key_value）


def test_d12_recovery_keys_lists_the_keys_by_name_unless_the_use_is_off(tmp_path):
    put_book(tmp_path, keys={"zeta": (KEY_TWO, "g-z"), "alpha": (KEY_MAIN, "g-a")})
    assert reg.recovery_keys(tmp_path) == ["alpha", "zeta"]
    put_book(tmp_path, use="off", keys={"alpha": (KEY_MAIN, "g-a")})
    assert reg.recovery_keys(tmp_path) == []


def test_d12_recovery_keys_is_empty_without_or_with_a_broken_book(tmp_path):
    assert reg.recovery_keys(tmp_path) == []
    reg.book_path(tmp_path).write_text('{"keys": []}', encoding="utf-8")
    assert reg.recovery_keys(tmp_path) == []


def test_d12_key_value_gives_the_key_only_for_the_same_generation(tmp_path):
    put_book(tmp_path)
    assert reg.row_generation(reg.recovery_book(tmp_path)["key-main"]) == "gen-1"
    assert reg.key_value(tmp_path, "key-main", "gen-1") == KEY_MAIN
    put_book(tmp_path, keys={"key-main": (KEY_NEW, "gen-2")})  # 同じ名前を登録し直した
    with pytest.raises(reg.AccountError) as old:
        reg.key_value(tmp_path, "key-main", "gen-1")
    assert KEY_MAIN not in str(old.value) and KEY_NEW not in str(old.value)
    with pytest.raises(reg.AccountError):
        reg.key_value(tmp_path, "nothing", "gen-2")
    put_book(tmp_path, use="off", keys={"key-main": (KEY_NEW, "gen-2")})
    with pytest.raises(reg.AccountError):
        reg.key_value(tmp_path, "key-main", "gen-2")


# ---------------------------------------------------------------- build・verify・fallbacks・target_env


def test_d12_build_gives_the_key_through_the_helper_with_the_generation(tmp_path):
    store = register(tmp_path)
    put_book(store)
    s, _, env = _child(tmp_path)
    got = account_choice.build(env, "key:key-main", store, s.state_dir, tmp_path, ["claude"])
    assert not isinstance(got, str), got
    child, argv = got
    data = _settings_of(argv)
    want = shlex.join([account_choice.NDF_EXE, "relay", "account", "api-key-helper", "key-main", "--generation", "gen-1"])
    assert data["apiKeyHelper"] == want and data["hooks"]["ConfigChange"]
    assert child["NDF_CLAUDE_ACCOUNT"] == "key:key-main" and "ANTHROPIC_API_KEY" not in child
    assert KEY_MAIN not in json.dumps(argv) and KEY_MAIN not in json.dumps(child)


def test_d12_fallbacks_puts_the_api_keys_after_the_metered_connection(tmp_path):
    store = register(tmp_path)
    put_book(store, keys={"k2": (KEY_TWO, "g2"), "k1": (KEY_MAIN, "g1")})
    s, environ, _ = _child(tmp_path, NDF_SUPERVISE_CLAUDE_FALLBACK="CLAUDE_CODE_USE_BEDROCK=1 AWS_REGION=us-east-1")
    metered = account_choice.metered_decl(store, environ)
    probes = account_choice.fallbacks(account_choice.read_before(store), environ, metered, tmp_path, ["claude"], frozenset())
    assert [(p.name, p.reason) for p in probes] == [(reg.METERED, None), ("key:k1", None), ("key:k2", None)]
    assert str(reg.book_path(store)) in probes[1].stamps
    tried = account_choice.fallbacks(account_choice.read_before(store), environ, metered, tmp_path, ["claude"], frozenset({"key:k1"}))
    assert [p.name for p in tried] == [reg.METERED, "key:k2"]


def test_d12_fallbacks_has_no_api_key_while_the_use_is_off(tmp_path):
    store = register(tmp_path)
    put_book(store, use="off")
    _, environ, _ = _child(tmp_path)
    assert account_choice.fallbacks(account_choice.read_before(store), environ, None, tmp_path, ["claude"], frozenset()) == []


@pytest.mark.parametrize(
    "extra, code",
    [
        ({"ANTHROPIC_BASE_URL": GATEWAY}, "key_overridden"),
        ({"ANTHROPIC_BASE_URL": "https://api.anthropic.com/"}, None),
        ({"ANTHROPIC_CUSTOM_HEADERS": "X-Fake: secret-header-value"}, "key_overridden"),
        ({"ANTHROPIC_BASE_URL": "/"}, "key_overridden"),
        ({"ANTHROPIC_BASE_URL": "///"}, "key_overridden"),
    ],
    ids=["gateway", "default-url", "headers", "slash-only", "slashes-only"],
)
def test_d12_a_registry_key_goes_only_to_the_default_endpoint(tmp_path, extra, code):
    store = register(tmp_path)
    put_book(store)
    _, environ, _ = _child(tmp_path, **extra)
    (probe,) = account_choice.fallbacks(account_choice.read_before(store), environ, None, tmp_path, ["claude"], frozenset())
    assert probe.name == "key:key-main" and probe.reason == code
    assert "secret-header-value" not in str(probe.reason)


@pytest.mark.parametrize("settings", [{"env": {"ANTHROPIC_AUTH_TOKEN": "fake-token"}}, {"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}}])
def test_d12_settings_that_bring_other_credentials_refuse_the_registry_key(tmp_path, settings):
    store = register(tmp_path)
    put_book(store)
    _user_settings(tmp_path, settings)
    _, environ, _ = _child(tmp_path)
    (probe,) = account_choice.fallbacks(account_choice.read_before(store), environ, None, tmp_path, ["claude"], frozenset())
    assert probe.reason == "key_overridden"


def test_d12_target_env_refuses_a_missing_or_off_key(tmp_path):
    store = register(tmp_path)
    s, _, env = _child(tmp_path)
    assert account_choice.target_env(env, "key:key-main", store, s.state_dir, tmp_path, ["claude"]) == "key_missing"
    put_book(store, use="off")
    assert account_choice.target_env(env, "key:key-main", store, s.state_dir, tmp_path, ["claude"]) == "key_off"


def _launched(tmp_path, monkeypatch):
    store = register(tmp_path)
    put_book(store)
    s, _, env = _child(tmp_path)
    d = tmp_path / "relay"
    d.mkdir(mode=0o700)
    os.chmod(d, 0o700)
    rec = key_helper.create(d)
    got = account_choice.key_target(env, store, s.state_dir, tmp_path, ["claude"], (rec.path, rec.helper_id))
    assert isinstance(got, account_choice.Built), got
    assert got.generation == "gen-1" and got.pin_file is not None and got.pin_file.is_file()
    for k, v in got.env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.chdir(tmp_path)
    return store, rec, ["--pin", str(got.pin_file), "--record", str(rec.path), "--id", rec.helper_id]


# ---------------------------------------------------------------- api-key-helper <名前> --generation <世代>


def test_d12_the_registry_helper_gives_only_the_key_of_its_generation(tmp_path, monkeypatch, capsys):
    store, rec, tail = _launched(tmp_path, monkeypatch)
    assert cli.main(["account", "api-key-helper", "key-main", "--generation", "gen-1", *tail]) == 0
    assert capsys.readouterr().out == KEY_MAIN
    put_book(store, keys={"key-main": (KEY_NEW, "gen-2")})  # 動いている間に同じ名前を登録し直した
    assert cli.main(["account", "api-key-helper", "key-main", "--generation", "gen-1", *tail]) == 1
    out = capsys.readouterr()
    assert out.out == "" and KEY_NEW not in out.err and KEY_MAIN not in out.err
    assert json.loads(rec.path.read_text().splitlines()[-1])["out"] is False


def test_d12_the_registry_helper_gives_nothing_after_off(tmp_path, monkeypatch, capsys):
    store, _, tail = _launched(tmp_path, monkeypatch)
    put_book(store, use="off")
    assert cli.main(["account", "api-key-helper", "key-main", "--generation", "gen-1", *tail]) == 1
    assert capsys.readouterr().out == ""


def test_d12_the_registry_helper_needs_the_generation(tmp_path, monkeypatch, capsys):
    _, _, tail = _launched(tmp_path, monkeypatch)
    assert cli.main(["account", "api-key-helper", "key-main", *tail]) == 1
    assert capsys.readouterr().out == ""


def test_r1_1_a_key_removed_while_the_helper_waits_leaves_an_out_false_row(tmp_path, monkeypatch, capsys):
    # 使い方・世代・値は錠の中の 1 回の読み取りで照らす。消されたキーでは例外で抜けず、`out: false` の行を残す
    store, rec, tail = _launched(tmp_path, monkeypatch)
    put_book(store, keys={})
    assert cli.main(["account", "api-key-helper", "key-main", "--generation", "gen-1", *tail]) == 1
    assert capsys.readouterr().out == ""
    assert json.loads(rec.path.read_text().splitlines()[-1])["out"] is False


def test_r1_1_the_helper_reads_the_key_inside_the_record_lock(tmp_path, monkeypatch, capsys):
    _, rec, tail = _launched(tmp_path, monkeypatch)
    real, seen = key_helper.pay, []

    def pay(path, helper_id, key, clock, pinned=None):
        seen.append(callable(key))
        return real(path, helper_id, key, clock, pinned=pinned)

    monkeypatch.setattr(key_helper, "pay", pay)
    assert cli.main(["account", "api-key-helper", "key-main", "--generation", "gen-1", *tail]) == 0
    assert capsys.readouterr().out == KEY_MAIN and seen == [True]


def test_r1_4_the_fallbacks_follow_the_accounts_after_a_limit(tmp_path):
    # 先頭の従量の接続の用意が落ちても、残りの API キーを試せるように起動順の後ろへ並べる（今の接続先と tried は除く）
    g = wrapper_guard.GuardSteps()
    g.s = SimpleNamespace(guards=account_decide.Guards(80.0, 50.0, 90.0))
    g._pinned_or = lambda pick, reason, current, now: pick
    ok = launch_inputs.LaunchCache({}, None)
    inputs = launch_inputs.Inputs(fallback={reg.METERED: ok, "key:k1": ok, "key:k2": ok}, tried=frozenset({"key:k2"}))
    sec = SimpleNamespace(account="work1", limit_lost=False, auth_at=None)
    pick = account_decide.Pick("switch", reg.METERED, "")
    assert g._names(sec, pick, "limit", inputs, 0.0) == [reg.METERED, "key:k1"]
    assert g._names(sec, pick, "guard", inputs, 0.0) == [reg.METERED]


def test_r1_5_off_after_the_choice_is_caught_before_the_old_child_stops(tmp_path):
    store = register(tmp_path)
    put_book(store)
    s, environ, _ = _child(tmp_path)
    reader = launch_inputs.Reader(store, s.state_dir, environ, tmp_path, ["claude"])
    assert "key:key-main" in reader.read(0.0, True).fallbacks()
    assert reader.recheck_fallback("key:key-main") is None
    put_book(store, use="off")
    assert reader.recheck_fallback("key:key-main") == "key_missing"
    assert "key:key-main" not in reader.inputs.fallbacks()


@pytest.mark.parametrize("use", ["sometimes", 1, True, ""])
def test_r2_2_an_unknown_use_gives_no_recovery_key(tmp_path, monkeypatch, capsys, use):
    # 使い方は always・near-limit だけを許す。手編集の誤った値では候補にも払い出しにも出さず、動いている子は止める時点を待つ
    store = register(tmp_path)
    put_book(store, use=use)
    assert reg.recovery_keys(store) == []
    s, environ, child = _child(tmp_path)
    assert account_choice.fallbacks(account_choice.read_before(store), environ, None, tmp_path, ["claude"], frozenset()) == []
    wait, _, _ = account_choice.drift("key:key-main", child, ["claude"], tmp_path, store, account_choice.Started("gen-1", None))
    assert wait == "key_off"
    with pytest.raises(reg.AccountError):
        reg.key_value(store, "key-main", "gen-1")


def test_r2_1_a_claude_link_moved_to_another_file_is_judged_again(tmp_path, monkeypatch):
    # 関所の働くバージョンかは `claude` の指す実行ファイルで決まる。リンクの付け替え（旧ファイルは残る）と未検出からの変化で判定し直す
    from ndf.adapters import cli_floor
    from ndf.adapters.claude import CLAUDE

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    versions = {}
    for name, version in (("claude-old", "2.1.294"), ("claude-new", "2.1.330")):
        exe = bin_dir / name
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)
        versions[str(exe.resolve())] = version
    monkeypatch.setattr(
        account_choice,
        "claude_version",
        lambda env, cwd, exe_of=None: versions.get((exe_of() if exe_of else cli_floor.resolve(CLAUDE, env, cwd)) or ""),
    )
    store = register(tmp_path)
    put_book(store)
    s, environ, _ = _child(tmp_path, PATH=str(bin_dir))
    reader = launch_inputs.Reader(store, s.state_dir, environ, tmp_path, ["claude"])
    assert "key:key-main" not in reader.read(0.0, True).fallbacks()  # 未検出
    link = bin_dir / "claude"
    link.symlink_to(bin_dir / "claude-new")
    assert reader.recheck_fallback("key:key-main") is None
    link.unlink()
    link.symlink_to(bin_dir / "claude-old")
    assert reader.recheck_fallback("key:key-main") == "key_gate"
    link.unlink()
    link.symlink_to(bin_dir / "claude-new")
    assert reader.recheck_fallback("key:key-main") is None


def _versions_by_exe(monkeypatch, tmp_path):
    """`claude-old`（関所が働かない）と `claude-new` を別のディレクトリに置き、実パスでバージョンを返す偽の `claude_version`。"""
    from ndf.adapters import cli_floor
    from ndf.adapters.claude import CLAUDE

    dirs, versions = {}, {}
    for name, version in (("old", "2.1.294"), ("new", "2.1.330")):
        d = tmp_path / f"bin-{name}"
        d.mkdir()
        exe = d / "claude"
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)
        dirs[name], versions[str(exe.resolve())] = d, version
    monkeypatch.setattr(
        account_choice,
        "claude_version",
        lambda env, cwd, exe_of=None: versions.get((exe_of() if exe_of else cli_floor.resolve(CLAUDE, env, cwd)) or ""),
    )
    return dirs


def test_r3_1_claude_is_looked_up_only_when_a_candidate_depends_on_the_gate(tmp_path, monkeypatch):
    # キーの子の候補が無ければ `PATH` を探さない。キーの候補があれば 1 回の判定で 1 回だけ探し、キャッシュが効けば探し直さない
    from ndf.adapters import cli_floor

    calls = []
    real = cli_floor.resolve
    monkeypatch.setattr(cli_floor, "resolve", lambda *a, **k: calls.append(a) or real(*a, **k))
    store = register(tmp_path)
    s, environ, _ = _child(tmp_path)
    reader = launch_inputs.Reader(store, s.state_dir, environ, tmp_path, ["claude"])
    reader.read(0.0, True)
    reader.read(1.0, True)
    assert reader.recheck_fallback("key:key-main") == "key_missing"
    assert calls == []
    put_book(store)
    assert "key:key-main" in reader.read(2.0, True).fallbacks()
    assert len(calls) == 1


def test_r3_2_the_fallback_is_judged_with_the_child_path(tmp_path, monkeypatch):
    # 候補選定と止める直前の確かめは、実際の子と同じく起動先の NDF にそろえた `PATH` で `claude` を解決する
    dirs = _versions_by_exe(monkeypatch, tmp_path)
    store = register(tmp_path)
    put_book(store)
    s, environ, _ = _child(tmp_path, PATH=str(dirs["new"]))
    root = tmp_path / "ndf-root"
    monkeypatch.setattr(
        session_env, "with_launched_ndf", lambda env, keep: {**env, "PATH": str(dirs["old"])} if keep == root else dict(env)
    )
    reader = launch_inputs.Reader(store, s.state_dir, environ, tmp_path, ["claude"])
    assert "key:key-main" in reader.read(0.0, True).fallbacks()
    reader.launched_root = root
    assert reader.recheck_fallback("key:key-main") == "key_gate"
    assert "key:key-main" not in reader.read(1.0, True).fallbacks()


def test_r4_6_a_cache_hit_without_a_key_candidate_does_not_rebuild_the_child_path(tmp_path, monkeypatch):
    # 関所に依る候補が無いキャッシュの当たりでは、`PATH` の各要素のリンクをたどる環境の整形（with_launched_ndf）も打たない
    calls = []
    real = session_env.with_launched_ndf
    monkeypatch.setattr(session_env, "with_launched_ndf", lambda env, keep: calls.append(keep) or real(env, keep))
    store = register(tmp_path)
    s, environ, _ = _child(tmp_path)
    reader = launch_inputs.Reader(store, s.state_dir, environ, tmp_path, ["claude"])
    reader.launched_root = tmp_path / "ndf-root"
    reader.read(0.0, True)
    first = len(calls)
    reader.read(1.0, True)
    assert reader.recheck_fallback("key:key-main") == "key_missing"
    assert len(calls) == first


@pytest.mark.parametrize("value", [None, "", 123], ids=["missing", "empty", "not-a-string"])
def test_r4_5_a_row_without_a_usable_key_is_not_a_candidate(tmp_path, value):
    # 世代が有効でもキーの値が使えない行は、払い出しと同じ検証で候補から外す（旧子を止める前に外れる）
    store = register(tmp_path)
    put_book(store)
    book = reg.read_book(store)
    if value is None:
        del book["keys"]["key-main"]["key"]
    else:
        book["keys"]["key-main"]["key"] = value
    reg.write(reg.book_path(store), book)
    s, environ, child = _child(tmp_path)
    probes = account_choice.fallbacks(account_choice.read_before(store), environ, None, tmp_path, ["claude"], frozenset())
    assert [(p.name, p.reason) for p in probes] == [("key:key-main", "key_missing")]
    assert account_choice.target_env(child, "key:key-main", store, s.state_dir, tmp_path, ["claude"]) == "key_missing"
    reader = launch_inputs.Reader(store, s.state_dir, environ, tmp_path, ["claude"])
    assert "key:key-main" not in reader.read(0.0, True).fallbacks()
    with pytest.raises(reg.AccountError):
        reg.key_value(store, "key-main", "gen-1")


def test_r4_4_a_reregistered_key_stays_in_the_order_behind_another_first(tmp_path):
    # 同じ名前の登録し直しで止めたキーの子は、別の候補が先頭に立っても新しい世代のその名前を起動順に残す（先頭の用意が落ちても続ける）
    g = wrapper_guard.GuardSteps()
    g.s = SimpleNamespace(guards=account_decide.Guards(80.0, 50.0, 90.0))
    g._pinned_or = lambda pick, reason, current, now: pick
    ok = launch_inputs.LaunchCache({}, None)
    inputs = launch_inputs.Inputs(fallback={"key:k1": ok, "key:k2": ok})
    pick = account_decide.Pick("switch", "key:k2", "")
    sec = SimpleNamespace(account="key:k1", limit_lost=False, auth_at=None, rekeyed=True)
    assert g._names(sec, pick, "revoked", inputs, 0.0) == ["key:k2", "key:k1"]
    sec.rekeyed = False
    assert g._names(sec, pick, "revoked", inputs, 0.0) == ["key:k2"]


def test_r5_2_a_metered_key_declaration_with_its_own_path_is_judged_with_that_path(tmp_path, monkeypatch):
    # 従量の API キーの設定が `PATH` を重ねれば、候補選定も止める直前の確かめもその `PATH` の `claude` で関所のバージョンを読む
    dirs = _versions_by_exe(monkeypatch, tmp_path)
    decl_dir = tmp_path / "bin-decl"
    decl_dir.mkdir()
    link = decl_dir / "claude"
    link.symlink_to(dirs["old"] / "claude")
    store = register(tmp_path)
    decl = f"ANTHROPIC_API_KEY={KEY_MAIN} PATH={decl_dir}"
    s, environ, _ = _child(tmp_path, PATH=str(dirs["new"]), NDF_SUPERVISE_CLAUDE_FALLBACK=decl)
    reader = launch_inputs.Reader(store, s.state_dir, environ, tmp_path, ["claude"])
    assert reg.METERED not in reader.read(0.0, True).fallbacks()
    assert reader.recheck_fallback(reg.METERED) == "key_gate"
    link.unlink()
    link.symlink_to(dirs["new"] / "claude")
    assert reader.recheck_fallback(reg.METERED) is None
    assert reg.METERED in reader.read(1.0, True).fallbacks()


def test_r5_3_a_key_value_removed_while_the_child_runs_is_a_removed_key(tmp_path):
    # 世代を残したまま値だけが消えた行は、払い出しと同じ検証で消されたキー（止めてよい時点を待つ当たり）として扱う
    store = register(tmp_path)
    put_book(store)
    book = reg.read_book(store)
    del book["keys"]["key-main"]["key"]
    reg.write(reg.book_path(store), book)
    _, _, child = _child(tmp_path)
    wait, _, _ = account_choice.drift("key:key-main", child, ["claude"], tmp_path, store, account_choice.Started("gen-1", None))
    assert wait == "key_removed"


def test_r1_7_the_sweep_reads_proc_only_when_a_leftover_can_go(tmp_path, monkeypatch):
    d = tmp_path / "settings"
    d.mkdir()
    reads = []
    monkeypatch.setattr(account_choice, "_cmdlines", lambda: reads.append(1) or [])
    account_choice._sweep(d)
    mine = d / f"{os.getpid()}-{reg.proc_start(os.getpid()):.2f}-{'0' * 16}{account_choice.PIN_SUFFIX}"
    mine.write_text("{}", encoding="utf-8")
    account_choice._sweep(d)
    assert reads == [] and mine.exists()
    gone = d / f"999999999-1.00-{'1' * 16}{account_choice.PIN_SUFFIX}"
    gone.write_text("{}", encoding="utf-8")
    account_choice._sweep(d)
    assert reads == [1] and not gone.exists() and mine.exists()


# ---------------------------------------------------------------- ラッパー


class KeyChild(FakeChild):
    """見回りの回（isalive を呼ばれた回数）で `script[回](wrapper)` を打つ偽のキーの子。止めたときの `end_now` を残す。"""

    def __init__(self, script, outputs=(b"working\r\n",)):
        super().__init__(list(outputs), None)
        self.script, self.polls, self.w = dict(script), 0, None
        self.events: list = []
        self.stopped_with: list = []

    def isalive(self):
        if self.alive:
            self.polls += 1
            act = self.script.get(self.polls)
            if act is not None:
                act(self.w)
        return super().isalive()

    def terminate(self, force=False):
        self.events.append("stop")
        self.stopped_with.append(self.w.section.end_now)
        return super().terminate(force)


def _run(tmp_path, children, initial, environ=None, book=True, names=(), args=None):
    store = register(tmp_path, *names)
    if book:
        put_book(store)
    more = {} if args is None else {"args": args}
    s = make_settings(tmp_path, environ={**make_settings(tmp_path).environ, **(environ or {})}, **more)
    spawn = Spawner(children)
    out = io.BytesIO()
    w = wrapper.Wrapper(s, spawn=spawn, clock=clock_ticks(), out=out)
    w._initial = lambda now: initial
    for c in children:
        if isinstance(c, KeyChild):
            c.w = w
    drift = w._drift

    def spy(sec):
        drift(sec)
        first = children[0]
        if sec.must_end and isinstance(first, KeyChild) and "hit" not in first.events:
            first.events.append("hit")

    w._drift = spy
    try:
        ended = w.run("最初の 1 行")
    finally:
        for c in spawn.made + spawn.children:
            c.close()
    return ended, spawn, w, out.getvalue().decode("utf-8", errors="replace")


def _helper_words(argv):
    return shlex.split(_settings_of(argv)["apiKeyHelper"])


def test_d12_a_limit_without_any_other_target_resumes_on_the_api_key(tmp_path):
    # 登録済みアカウントがすべて上限で従量の接続が無く、使い方が near-limit のキーがあれば、そのキーで同じ会話を続ける
    ended, spawn, w, _ = _run(tmp_path, [FakeChild([LIMIT_LINE], None), FakeChild([], 0)], wrapper.Choice(("work1",)), names=("work1",))
    (one, _), (two, env2) = spawn.calls
    assert env2["NDF_CLAUDE_ACCOUNT"] == "key:key-main" and "ANTHROPIC_API_KEY" not in env2
    assert two[two.index("--resume") + 1] == one[2]
    words = _helper_words(two)
    assert words[3:7] == ["api-key-helper", "key-main", "--generation", "gen-1"] and "--record" in words
    assert KEY_MAIN not in json.dumps(two) and KEY_MAIN not in json.dumps(env2)


def test_d12_off_while_the_key_child_runs_waits_for_the_end_point(tmp_path):
    # `off` は止めてよい時点か relay.end-wait まで待つ（end_now を立てない）。替え先が無ければ account_unavailable
    def off(w):
        put_book(w.store, use="off")

    first = KeyChild({3: off})
    ended, spawn, w, out = _run(tmp_path, [first], wrapper.Choice(("key:key-main",), fixed=True))
    assert ended.cause == "account_unavailable" and len(spawn.calls) == 1
    assert first.stopped_with == [False]
    assert w.section.end_cause == "drift" and "key:key-main" not in w.tried
    assert json.loads((w.dir / "wrapper.json").read_text())["state"] != "stalled"


@pytest.mark.parametrize(
    "settings",
    [{"env": {"ANTHROPIC_BASE_URL": GATEWAY}}, {"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}}, {"env": {"ANTHROPIC_AUTH_TOKEN": "fake-token"}}],
    ids=["base-url", "provider", "auth-token"],
)
def test_d12_an_endpoint_or_credential_change_stops_the_key_child_in_the_same_tick(tmp_path, settings):
    def change(w):
        _user_settings(tmp_path, settings)

    first = KeyChild({3: change})
    ended, spawn, w, out = _run(tmp_path, [first], wrapper.Choice(("key:key-main",), fixed=True))
    assert ended.cause == "account_unavailable" and len(spawn.calls) == 1
    assert first.stopped_with == [True]
    after = first.events[first.events.index("hit") + 1 :]
    assert after[:1] == ["stop"]
    assert "key:key-main" not in w.tried and GATEWAY not in out and "fake-token" not in out


def test_d12_a_reregistered_key_stops_and_resumes_on_its_new_generation(tmp_path):
    # 同じ名前の登録し直しは止めてよい時点を待って止め、新しい世代のその名前を替え先の候補に残す
    def rekey(w):
        put_book(w.store, keys={"key-main": (KEY_NEW, "gen-2")})

    first = KeyChild({3: rekey})
    ended, spawn, w, _ = _run(tmp_path, [first, FakeChild([], 0)], wrapper.Choice(("key:key-main",), fixed=True))
    assert first.stopped_with == [False]
    (one, _), (two, env2) = spawn.calls
    assert env2["NDF_CLAUDE_ACCOUNT"] == "key:key-main" and two[two.index("--resume") + 1] == one[one.index("--session-id") + 1]
    assert _helper_words(two)[5:7] == ["--generation", "gen-2"]


@pytest.mark.parametrize("replaced", ["https://gw-b.example.com", ""], ids=["replaced", "emptied"])
def test_d12_a_metered_key_child_stops_when_its_declared_endpoint_is_replaced(tmp_path, replaced):
    decl = f"ANTHROPIC_API_KEY={KEY_MAIN} ANTHROPIC_BASE_URL=https://gw-a.example.com"

    def change(w):
        _user_settings(tmp_path, {"env": {"ANTHROPIC_BASE_URL": replaced}})

    first = KeyChild({3: change})
    ended, spawn, w, out = _run(
        tmp_path, [first], wrapper.Choice((reg.METERED,), fixed=True), environ={"NDF_SUPERVISE_CLAUDE_FALLBACK": decl}, book=False
    )
    assert ended.cause == "account_unavailable" and first.stopped_with == [True]
    assert first.events[first.events.index("hit") + 1 :][:1] == ["stop"]
    assert wrapper_stop.DRIFT_LINE.split("（")[0] in out and "gw-b" not in out


def _managed(data, dropin=False):
    """管理の設定（`managed-settings.json` か `managed-settings.d` の 1 ファイル）を書く（conftest が一時の場所へ向けてある）。"""
    path = account_dir.MANAGED_DIR / "90-fake.json" if dropin else account_dir.MANAGED_FILES[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize(
    "data, dropin",
    [
        ({"apiKeyHelper": "/bin/echo fake"}, False),
        ({"env": {"ANTHROPIC_BASE_URL": GATEWAY}}, False),
        ({"apiKeyHelper": "/bin/echo fake"}, True),
    ],
    ids=["managed-helper", "managed-base-url", "dropin-helper"],
)
@pytest.mark.parametrize("target", ["key:key-main", reg.METERED])
def test_d12_a_managed_settings_change_stops_the_key_child_in_the_same_tick(tmp_path, data, dropin, target):
    # 別冊の「設定の変更でキーの子を止める範囲」の 2 行目: 管理の設定が helper より上に別の apiKeyHelper を置く・接続先を足すと、
    # 検出した見回りで止める（config-gate は管理の設定を拒めないので、見回りの検出が受け持つ）
    def change(w):
        _managed(data, dropin)

    first = KeyChild({3: change})
    environ = {"NDF_SUPERVISE_CLAUDE_FALLBACK": f"ANTHROPIC_API_KEY={KEY_MAIN}"} if target == reg.METERED else None
    ended, spawn, w, out = _run(tmp_path, [first], wrapper.Choice((target,), fixed=True), environ=environ)
    assert ended.cause == "account_unavailable" and first.stopped_with == [True]
    assert first.events[first.events.index("hit") + 1 :][:1] == ["stop"]
    assert GATEWAY not in out and "/bin/echo" not in out


def test_r4_3_a_helper_replaced_in_the_settings_file_stops_the_key_child(tmp_path):
    # ファイルで渡した `--settings` の apiKeyHelper だけを置き換えても、起動のときの helper と比べて検出した見回りで止める
    src = tmp_path / "my-settings.json"
    src.write_text(json.dumps({"model": "x"}), encoding="utf-8")

    def swap(w):
        path = w.section.target.settings_file
        data = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**data, "apiKeyHelper": "/bin/echo fake"}), encoding="utf-8")

    first = KeyChild({3: swap})
    ended, spawn, w, out = _run(tmp_path, [first], wrapper.Choice(("key:key-main",), fixed=True), args=("--settings", str(src)))
    ((one, _),) = spawn.calls
    assert one[one.index("--settings") + 1] != str(src)  # 合成の一時ファイルで渡した
    assert ended.cause == "account_unavailable" and first.stopped_with == [True]
    assert first.events[first.events.index("hit") + 1 :][:1] == ["stop"]
    assert "/bin/echo" not in out
