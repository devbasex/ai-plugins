"""ラッパーの候補を試す起動・指定の読み直し・書き戻し・知らせ（U8。#3604 #3864）。

設計書の「テスト設計」の 3604:1・3604:3・3604:7・3604:8・3604:9・3864:8・3864:11 の行と、詳細設計書の「ラッパー」の
「単体テストの観点」の `_start`・`_launch_target`・`_initial`・`_on_switch`・`_pinned_or`・`account` の行・`_settle`・
`_tell_at_start` と、「共有の替え先」の `build`・`verify`・`target_env` の従量の接続の行を縛る。
値はすべて偽の値で、実のアカウントを使わない。
"""

from __future__ import annotations

import io
import json
import os
import time

import pytest
from ndf.contracts import causes
from ndf.domains.relay import account_choice, account_dir, accounts, cli, launch_inputs, launch_target, session_env, wrapper
from ndf.domains.relay import account_registry as reg
from ndf.domains.relay.account_decide import Usage
from relay_children import FakeChild, Spawner, clock_ticks, make_settings, put_usage, put_usages, register, rows_of


def _run(tmp_path, children, prepare=None, markers=None, spawner=Spawner, **changes):
    s = make_settings(tmp_path, **changes)
    spawn = spawner(children, markers)
    out = io.BytesIO()
    w = wrapper.Wrapper(s, spawn=spawn, clock=clock_ticks(), out=out)
    if prepare:
        prepare(s.state_dir)
    try:
        ended = w.run("最初の 1 行")
    finally:
        for c in spawn.made + spawn.children:
            c.close()
    return ended, spawn, w, out.getvalue().decode()


def _envs(spawn) -> list:
    return [env.get("NDF_CLAUDE_ACCOUNT") for _, env in spawn.calls]


def _two(markers=True):
    return [FakeChild([b"working\r\n"], None), FakeChild([], 0)], ({0: "/goal 次"} if markers else None)


def test_3604_1_pinned_account_runs_in_its_config_dir_through_the_gate(tmp_path):
    # 受け入れ条件 #3604 の 1: 指定 work2 の子は CLAUDE_CONFIG_DIR が <保存先>/work2 の絶対パス・NDF_CLAUDE_ACCOUNT が work2・
    # NDF_SHARED_CONFIG_DIR が親の CLAUDE_CONFIG_DIR。新しい使用率のキャッシュの書き先も渡す。関所で起動し、届けは終わりに消える
    store = register(tmp_path, "work1", "work2")
    ended, spawn, w, _ = _run(tmp_path, [FakeChild([], 0)], lambda state: accounts.use(state, "work2"))
    (argv, env) = spawn.calls[0]
    assert env["CLAUDE_CONFIG_DIR"] == str((store / "work2").absolute()) and os.path.isabs(env["CLAUDE_CONFIG_DIR"])
    assert env["NDF_CLAUDE_ACCOUNT"] == "work2" and env["NDF_SHARED_CONFIG_DIR"] == str(tmp_path / "claude")
    assert env["NDF_ACCOUNT_USAGE_FILE"] == str(accounts.usage_path(tmp_path / "state", "work2", "g1"))
    assert argv[:2] == [wrapper.CLAUDE, "--session-id"] and spawn.gated == [0]
    assert not list(reg.lease_dir(store, "work2").glob("*.json"))
    row = rows_of(w, "account")[0]
    assert (row["from"], row["account"], row["reason"], row["skipped"]) == (None, "work2", "first", [])
    assert ended.cause == ""


def test_3604_3_default_login_child_env_is_the_plain_child_env(tmp_path):
    # 受け入れ条件 #3604 の 3: 指定も環境変数も無い既定のログインの子の環境は今の child_env と同じ（関所を通さない）
    register(tmp_path, "work1")
    ended, spawn, w, _ = _run(tmp_path, [FakeChild([], 0)])
    (_, env) = spawn.calls[0]
    s = make_settings(tmp_path)
    plain = session_env.child_env(s.environ, s.state_dir)
    assert {k: v for k, v in env.items() if not k.startswith("NDF_RELAY_")} == plain
    assert spawn.gated == [] and rows_of(w, "account")[0]["account"] is None


def test_3604_3_a_child_that_fell_back_to_the_default_login_has_no_account_name(tmp_path):
    # 受け入れ条件 #3604 の 3: 環境変数の work1 の用意に落ちて既定のログインへ移った子は NDF_CLAUDE_ACCOUNT が無い
    store = register(tmp_path, "work1")
    (store / "work1" / reg.CRED_FILE).unlink()
    environ = {**make_settings(tmp_path).environ, "NDF_CLAUDE_ACCOUNT": "work1"}
    ended, spawn, w, _ = _run(tmp_path, [FakeChild([], 0)], environ=environ)
    (_, env) = spawn.calls[0]
    assert "NDF_CLAUDE_ACCOUNT" not in env and env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude")
    assert rows_of(w, "account")[0]["account"] is None and rows_of(w, "account")[0]["skipped"] == ["work1"]
    assert rows_of(w, "section")[0]["account"] is None


def _break(store, how):
    acc = store / "work1"
    if how == "no_credentials":
        (acc / reg.CRED_FILE).unlink()
    elif how == "no_refresh":
        (acc / reg.CRED_FILE).write_text(json.dumps({"claudeAiOauth": {"accessToken": "fake-access-work1", "refreshToken": ""}}))
    elif how == "identity_mismatch":
        (acc / reg.CONFIG_FILE).write_text(json.dumps({"oauthAccount": {"emailAddress": "other@example.com"}}))
    elif how == "legacy_config":
        (acc / account_dir.LEGACY_CONFIG_FILE).write_text("{}")
    else:  # not_account_local: 認証ファイルがアカウントの外を指す
        outside = store.parent / "outside.json"
        os.replace(acc / reg.CRED_FILE, outside)
        (acc / reg.CRED_FILE).symlink_to(outside)


@pytest.mark.parametrize("how", ["no_credentials", "no_refresh", "identity_mismatch", "legacy_config", account_dir.NOT_LOCAL])
def test_3604_8_each_reason_skips_the_name_with_one_line_and_starts_the_next(tmp_path, monkeypatch, how):
    # 受け入れ条件 #3604 の 8: 理由ごとに子を起動せず、画面に 1 行と log.jsonl に 1 行が出て、次の候補（指定の work2）で起動する。
    # 外した名前へは prepare を打たない
    store = register(tmp_path, "work1", "work2")
    _break(store, how)
    prepared = []
    real = account_dir.prepare
    monkeypatch.setattr(account_dir, "prepare", lambda st, n, *a: (prepared.append(n), real(st, n, *a))[1])
    environ = {**make_settings(tmp_path).environ, "NDF_CLAUDE_ACCOUNT": "work1"}
    ended, spawn, w, shown = _run(tmp_path, [FakeChild([], 0)], lambda state: accounts.use(state, "work2"), environ=environ)
    assert _envs(spawn) == ["work2"] and prepared == ["work2"]
    line = launch_target.SKIP_LINE.format(name="work1", why=account_dir.REASONS[how].format(name="work1"))
    assert shown.count(line) == 1
    assert [r["reason"] for r in rows_of(w, "account_dir") if r["account"] == "work1"] == [how]
    assert rows_of(w, "account")[0]["skipped"] == ["work1"]


def test_3604_9_each_account_that_needs_relogin_is_told_once_at_start(tmp_path):
    # 受け入れ条件 #3604 の 9: ラッパーの起動で、再登録が要るアカウント 1 つにつき 1 行（トークン・メールアドレス・パスは出さない）
    store = register(tmp_path, "work1", "work2", "work3")
    for n in ("work1", "work3"):
        path = store / n / reg.ACCOUNT_FILE
        path.write_text(json.dumps({**json.loads(path.read_text()), "needs_relogin": True}))
    ended, spawn, w, shown = _run(tmp_path, [FakeChild([], 0)])
    assert [shown.count(launch_target.RELOGIN_LINE.format(name=n)) for n in ("work1", "work2", "work3")] == [1, 0, 1]
    assert "@example.com" not in shown and str(store) not in shown


def test_3604_9_a_broken_metered_setting_is_told_once_at_start(tmp_path):
    store = register(tmp_path)
    store.mkdir(parents=True)
    reg.metered_path(store).write_text("{")
    ended, spawn, w, shown = _run(tmp_path, [FakeChild([], 0)])
    assert shown.count(launch_target.METERED_BROKEN_LINE) == 1


def test_3604_7_settle_runs_at_each_section_end_and_at_the_wrapper_end(tmp_path, monkeypatch):
    # 受け入れ条件 #3604 の 7: 書き戻しはセグメントの終わりとラッパーの終わりで 1 回ずつ呼ばれる。既定のログインでは打たない
    store = register(tmp_path, "work1")
    settled = []
    real = account_dir.settle
    monkeypatch.setattr(account_dir, "settle", lambda st, n, env, g, lease: (settled.append((n, g)), real(st, n, env, g, lease))[1])
    children, markers = _two()
    ended, spawn, w, _ = _run(tmp_path, children, lambda state: accounts.use(state, "work1"), markers)
    assert _envs(spawn) == ["work1", "work1"] and settled == [("work1", "g1"), ("work1", "g1")]
    assert not list(reg.lease_dir(store, "work1").glob("*.json"))


def test_3864_8_a_pin_changed_while_running_applies_to_the_next_section(tmp_path):
    # 受け入れ条件 #3864 の 8: 動いているラッパーで指定を work2 にすると、次のセグメントが work2 で起動する
    register(tmp_path, "work1", "work2")

    class Repin(Spawner):
        def __call__(self, argv, env, cwd, pass_fds=()):
            child = super().__call__(argv, env, cwd, pass_fds)
            accounts.use(tmp_path / "state", "work2")
            return child

    children, markers = _two()
    ended, spawn, w, _ = _run(tmp_path, children, lambda state: accounts.use(state, "work1"), markers, spawner=Repin)
    assert _envs(spawn) == ["work1", "work2"]
    assert [(r["from"], r["account"], r["reason"]) for r in rows_of(w, "account")] == [(None, "work1", "first"), ("work1", "work2", "cut")]


def _cut(tmp_path, usages):
    register(tmp_path, *usages)
    for n, five in usages.items():
        put_usage(tmp_path, n, five)
    environ = {**make_settings(tmp_path).environ, "NDF_CLAUDE_ACCOUNT": "work1"}
    children, markers = _two()
    return _run(tmp_path, children, lambda state: accounts.use(state, "work2"), markers, environ=environ)


def test_3864_8_a_pin_in_the_used_up_group_yields_to_a_roomy_candidate(tmp_path):
    # 受け入れ条件 #3864 の 8: 指定の work2 が使える（上限でも外した間でもない）が組 3（5 時間 86%）で、work3 が組 0（10%）なら work3
    _, spawn, _, _ = _cut(tmp_path, {"work1": 92.0, "work2": 86.0, "work3": 10.0})
    assert _envs(spawn) == ["work1", "work3"]


def test_3864_8_a_pin_wins_when_every_candidate_is_in_the_used_up_group(tmp_path):
    # 受け入れ条件 #3864 の 8: work2 もほかの候補もすべて組 3 なら work2
    _, spawn, _, _ = _cut(tmp_path, {"work1": 92.0, "work2": 86.0, "work3": 90.0})
    assert _envs(spawn) == ["work1", "work2"]


def test_3864_8_a_limited_pin_is_not_used_and_the_pick_runs(tmp_path):
    # 受け入れ条件 #3864 の 8: 指定の work2 が上限なら判断の関数の結果（work3）で起動する
    _, spawn, _, _ = _cut(tmp_path, {"work1": 92.0, "work2": 100.0, "work3": 10.0})
    assert _envs(spawn) == ["work1", "work3"]


@pytest.mark.parametrize("names", [(), ("work1",), ("work1", "work2")], ids=["none", "one", "two"])
def test_3864_11_without_env_or_pin_the_default_login_stays_and_prepare_is_never_called(tmp_path, monkeypatch, names):
    # 受け入れ条件 #3864 の 11 と #3864 の 8: 環境変数も指定も無ければ、登録の数によらず最初も区切りの後も既定のログインで、prepare を打たない
    register(tmp_path, *names)
    monkeypatch.setattr(account_dir, "prepare", lambda *a: pytest.fail("prepare を打った"))
    children, markers = _two()
    ended, spawn, w, _ = _run(tmp_path, children, markers=markers)
    assert _envs(spawn) == [None, None] and spawn.gated == [] and ended.cause == ""


def _limited_then(tmp_path, monkeypatch, failing: set):
    """team の子が上限に当たり、main へ替える。最初の起動より後の `failing` の名前の prepare を競合（lock_timeout）で落とす。"""
    register(tmp_path, "team", "main")
    calls = []
    real = account_dir.prepare

    def prepare(store, name, environ, launch):
        calls.append(name)
        if len(calls) > 1 and name in failing:
            return account_dir.Prepared(False, "lock_timeout", store / name)
        return real(store, name, environ, launch)

    monkeypatch.setattr(account_dir, "prepare", prepare)

    def setup(state):
        put_usages(
            state,
            [Usage("team", 50.0, 50.0, None, None, False), Usage("main", 5.0, 5.0, None, None, False)],
            measured_at=time.time() - 3600,
        )
        accounts.use(state, "team")

    limited = FakeChild([b"  \xe2\x8e\xbf  You've hit your individual spend limit \xc2\xb7 resets 10:40pm\r\n"], None)
    return _run(tmp_path, [limited, FakeChild([], 0)], setup), calls


def test_3604_8_a_limit_target_that_fails_to_prepare_falls_back_to_the_current_account(tmp_path, monkeypatch):
    # 止めた後に替え先の prepare が競合で落ちたとき、上限では既定のログインへ落ちず今のアカウントで起動する
    (ended, spawn, w, shown), calls = _limited_then(tmp_path, monkeypatch, {"main"})
    assert calls == ["team", "main", "team"] and _envs(spawn) == ["team", "team"]
    assert rows_of(w, "account")[-1]["skipped"] == ["main"]


def test_account_unavailable_when_the_limit_target_and_the_current_account_both_fail(tmp_path, monkeypatch, capsys):
    # 「用意が落ちたときの account_unavailable」: 替え先も今のアカウントも用意できなければ、子を起動せずに止まり、既定のログインを起動しない
    (ended, spawn, w, shown), calls = _limited_then(tmp_path, monkeypatch, {"main", "team"})
    assert ended.cause == "account_unavailable" and len(spawn.calls) == 1
    assert "team" in ended.reason and rows_of(w, "end")[-1]["cause"] == "account_unavailable"
    assert cli._ended(ended) == causes.exit_code("account_unavailable")


def test_the_spin_cause_without_a_child_exit_code_has_an_exit_code(capsys):
    # U8 で閉じるもの 2: 子を起動せずに返す Ended("spin") は終了コードを持たないので、停止理由の表の spin の行で終わる
    assert cli._ended(wrapper.Ended("spin", "再起動ループ")) == causes.exit_code("spin")


@pytest.mark.parametrize(("spawned", "again"), [(None, "ndf relay run"), ("1", "ndf relay restart t1")])
def test_the_spin_line_names_a_command_that_exists(tmp_path, spawned, again):
    # U8 で閉じるもの 1: 端末のラッパーは ndf relay run を、名前つきのラッパーは tmux の外から ndf relay restart <名前> を案内する
    environ = {**make_settings(tmp_path).environ, **({"NDF_RELAY_SPAWNED": spawned} if spawned else {})}
    w = wrapper.Wrapper(make_settings(tmp_path, environ=environ), spawn=Spawner([]), clock=clock_ticks())
    line = w._spin_line()
    assert again in line and ("tmux の外" in line) == bool(spawned)
    assert cli._parser().parse_args(again.split()[2:]).action == again.split()[2]


def test_launch_inputs_do_not_probe_again_until_a_source_changes(tmp_path, monkeypatch):
    # 「_inputs」: スタンプが変わらなければ ready を打ち直さず、使用率も読み直さない。登録のファイルが変わった名前にだけ ready を打つ
    store = register(tmp_path, "work1", "work2")
    probes, reads = [], []
    real_ready, real_usages = account_dir.ready, accounts.usages
    monkeypatch.setattr(account_dir, "ready", lambda st, n, *a: (probes.append(n), real_ready(st, n, *a))[1])
    monkeypatch.setattr(accounts, "usages", lambda *a, **k: (reads.append(1), real_usages(*a, **k))[1])
    s = make_settings(tmp_path)
    r = launch_inputs.Reader(store, s.state_dir, s.environ, tmp_path, [wrapper.CLAUDE])
    now = time.time()
    for _ in range(5):
        got = r.read(now, True)
    assert sorted(probes) == ["work1", "work2"] and len(reads) == 1 and got.candidates() == ["work1", "work2"]
    (store / "work2" / reg.CRED_FILE).unlink()
    put_usage(tmp_path, "work1", 10.0)
    got = r.read(now, True)
    assert sorted(probes) == ["work1", "work2", "work2"] and len(reads) == 2 and got.candidates() == ["work1"]


def _metered(tmp_path, settings_env=None):
    store = register(tmp_path)
    reg.save_metered(store, "bedrock", {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "prod"}, {"region": "r"}, time.time())
    if settings_env is not None:
        (tmp_path / "claude" / "settings.json").write_text(json.dumps({"env": settings_env}))
    s = make_settings(tmp_path)
    env = session_env.child_env(s.environ, s.state_dir, reg.METERED, decided=True)
    return account_choice.target_env(env, reg.METERED, store, s.state_dir, tmp_path, [wrapper.CLAUDE])


def test_target_env_builds_the_metered_child_on_the_shared_config_dir(tmp_path):
    got = _metered(tmp_path)
    assert isinstance(got, account_choice.Built), got
    assert got.env["NDF_CLAUDE_ACCOUNT"] == reg.METERED and got.env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude")
    assert got.env["AWS_PROFILE"] == "prod" and got.resolved is not None and got.prepared is None


def test_target_env_refuses_metered_when_a_settings_file_changes_the_profile(tmp_path):
    assert _metered(tmp_path, {"AWS_PROFILE": "other"}) == "metered_overridden"


def test_target_env_without_a_metered_setting_is_unavailable(tmp_path):
    store = register(tmp_path)
    s = make_settings(tmp_path)
    env = session_env.child_env(s.environ, s.state_dir, reg.METERED, decided=True)
    assert account_choice.target_env(env, reg.METERED, store, s.state_dir, tmp_path, []) == "metered_unavailable"


def test_candidates_are_the_registered_accounts_that_pass_ready_and_are_not_held(tmp_path):
    store = register(tmp_path, "work1", "work2", "work3")
    (store / "work3" / reg.CRED_FILE).unlink()
    s = make_settings(tmp_path)
    now = time.time()
    accounts.hold(s.state_dir, "work2", "g1", now + 3600, now)
    assert account_choice.candidates(s.environ, store, s.state_dir, now, tmp_path, [wrapper.CLAUDE]) == ["work1"]
