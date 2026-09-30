"""登録済みアカウントの部品 `lib/claude_accounts.py` のテスト（#1389）。"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_accounts as ca  # noqa: E402
import claude_settings as cs  # noqa: E402
import claude_usage as cu  # noqa: E402
from account_fake import accounts, scoped_limit, window  # noqa: E402,F401


def test_choose_picks_lowest_of_larger_utilization(accounts):
    accounts.add("a", util5=15, util7=3)
    accounts.add("b", util5=2, util7=41)
    accounts.add("c", util5=20, util7=20)
    assert ca.choose().name == "a"
    assert ca.choose(exclude={"a"}).name == "c"


def test_choose_tie_breaks_on_earlier_five_hour_reset(accounts):
    ta = accounts.add("a", util5=None)
    tb = accounts.add("b", util5=None)
    accounts.fake.set_usage(ta, window(30, 7200), window(10))
    accounts.fake.set_usage(tb, window(30, 600), window(10))
    assert ca.choose().name == "b"


@pytest.mark.parametrize(
    "limited",
    [
        dict(util5=100, util7=5),
        dict(util5=5, util7=100),
        dict(util5=5, util7=5, spend=True),
    ],
)
def test_choose_skips_limited(accounts, limited):
    accounts.add("a", **limited)
    accounts.add("b", util5=80, util7=80)
    c = ca.choose()
    assert c.name == "b"
    assert c.earliest[0] == "a"


def test_choose_none_reports_earliest_reset(accounts):
    ta = accounts.add("a", util5=None)
    tb = accounts.add("b", util5=None)
    accounts.fake.set_usage(ta, window(100, 7200), window(10))
    accounts.fake.set_usage(tb, window(100, 600), window(10))
    c = ca.choose()
    assert c.name is None and c.earliest[0] == "b"
    assert c.earliest[1] == pytest.approx(time.time() + 600, abs=5)


def test_unknown_is_candidate_only_when_none_known(accounts):
    accounts.add("a", util5=None)  # 取得先が 503（残量不明）
    accounts.add("b", util5=70, util7=70)
    assert ca.choose().name == "b"
    assert ca.choose(exclude={"b"}).name == "a"


def test_unknown_is_candidate_when_only_limited_are_known(accounts):
    """上限に達したアカウントの残量が読めても、上限に達していない候補に読めるものが無ければ読めないものを選ぶ。"""
    accounts.add("a", util5=None)
    accounts.add("b", util5=100, util7=10)
    assert ca.choose().name == "a"


def test_observed_limit_excludes_until_reset(accounts):
    accounts.add("a", util5=None)
    accounts.add("b", util5=None)
    now = time.time()
    ca.note_limit("a", "five_hour", now + 3600, now)
    assert ca.choose().name == "b"
    # 取得先が使えなくても、リセット時刻を過ぎたら候補に戻る（受け入れ条件 13）
    assert ca.choose(exclude={"b"}, now=now + 3700).name == "a"
    assert accounts.account("a")["limit"]["type"] == "five_hour"


def test_note_limit_does_not_register(accounts):
    ca.note_limit("ghost", "five_hour", None)
    assert not (accounts.root / "ghost").exists() and ca.names() == []


def test_usage_fetched_once_per_interval_across_processes(accounts):
    ta = accounts.add("a")
    accounts.add("b")
    for _ in range(3):
        ca.choose()
        ca.rows()
    code = "import sys; sys.path.insert(0, sys.argv[1]); import claude_accounts as ca; ca.choose(); ca.rows()"
    subprocess.run([sys.executable, "-c", code, str(SCRIPTS / "lib")], check=True, env=dict(os.environ))
    assert accounts.fake.usage_calls.count(ta) == 1
    assert len(accounts.fake.usage_calls) == 2


def test_failed_fetch_counts_for_interval(accounts):
    accounts.add("a", util5=None)
    ca.usage("a")
    ca.usage("a")
    assert len(accounts.fake.usage_calls) == 1
    assert json.loads((accounts.root / "a" / "usage.json").read_text())["error"] == "http-503"


def test_expired_token_is_refreshed_then_used(accounts):
    accounts.add("a", expires_in=-60, util5=None)
    accounts.add("b", util5=90, util7=90)
    accounts.fake.refresh["a-refresh-SECRET"] = (
        200,
        {"access_token": "a-new-SECRET", "refresh_token": "a-refresh2-SECRET", "expires_in": 28800},
    )
    accounts.fake.set_usage("a-new-SECRET", window(5), window(5))
    rows = {r["name"]: r for r in ca.rows()}
    assert rows["a"]["state"] == "使える" and rows["a"]["five_hour"]["utilization"] == 5
    c = accounts.creds("a")
    assert c["accessToken"] == "a-new-SECRET" and c["refreshToken"] == "a-refresh2-SECRET"
    assert c["expiresAt"] / 1000 > time.time() + 28000
    assert ca.choose().name == "a"
    assert oct((accounts.root / "a" / ".credentials.json").stat().st_mode & 0o777) == "0o600"


def test_rejected_refresh_needs_relogin(accounts):
    accounts.add("a", expires_in=-60, util5=None)
    accounts.add("b", util5=90, util7=90)
    rows = {r["name"]: r for r in ca.rows()}
    assert rows["a"]["state"] == "再登録が要る"
    assert accounts.account("a")["needs_relogin"] is True
    assert ca.choose().name == "b"


def test_refresh_token_expired_needs_relogin_without_calling(accounts):
    accounts.add("a", expires_in=-60, refresh_in=-10, util5=None)
    assert ca.token("a") is None
    assert accounts.fake.refresh_calls == [] and accounts.account("a")["needs_relogin"] is True


def test_concurrent_refresh_calls_endpoint_once(accounts):
    accounts.add("a", expires_in=60)  # 期限の 60 分前を切っている
    accounts.fake.refresh["a-refresh-SECRET"] = (200, {"access_token": "a-new-SECRET", "expires_in": 28800})
    got = []
    ts = [threading.Thread(target=lambda: got.append(ca.token("a"))) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert got == ["a-new-SECRET", "a-new-SECRET"]
    assert accounts.fake.refresh_calls == ["a-refresh-SECRET"]


def test_keep_account_is_not_refreshed(accounts):
    accounts.add("a", expires_in=600)  # 期限の 60 分前を切っているが過ぎていない
    accounts.add("b", expires_in=-60, util5=None)
    accounts.fake.refresh["b-refresh-SECRET"] = (200, {"access_token": "b-new", "expires_in": 28800})
    assert ca.choose(exclude={"b"}, keep={"a"}).name == "a"
    assert ca.choose(exclude={"a"}, keep={"b"}).name is None  # 期限の過ぎた keep は更新せず外す
    assert accounts.fake.refresh_calls == []


def test_permissions_and_shared_credentials_untouched(accounts):
    accounts.add("a")
    accounts.add("b", expires_in=60)
    accounts.fake.refresh["b-refresh-SECRET"] = (200, {"access_token": "b-new-SECRET", "expires_in": 28800})
    os.chmod(accounts.root / "a" / "account.json", 0o644)
    os.chmod(accounts.root, 0o755)
    shared = accounts.shared / ".credentials.json"
    before = (shared.read_bytes(), shared.stat().st_mtime_ns)
    ca.rows()
    ca.choose()
    ca.account_env("b", {})
    ca.note_limit("a", "seven_day", None)
    assert (shared.read_bytes(), shared.stat().st_mtime_ns) == before
    assert oct(accounts.root.stat().st_mode & 0o777) == "0o700"
    for d in ("a", "b"):
        assert oct((accounts.root / d).stat().st_mode & 0o777) == "0o700"
        for f in (accounts.root / d).iterdir():
            assert oct(f.stat().st_mode & 0o777) == "0o600", f


def test_account_env_and_metered_do_not_mix(accounts, monkeypatch):
    tok = accounts.add("a")
    decl = "CLAUDE_CODE_USE_BEDROCK=1 AWS_PROFILE=p ANTHROPIC_API_KEY=sk-SECRET"
    base = {
        "NDF_SUPERVISE_CLAUDE_FALLBACK": decl,
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "ANTHROPIC_API_KEY": "sk-SECRET",
        "CLAUDE_CODE_OAUTH_TOKEN": "old",
        "X": "y",
    }
    env = ca.account_env("a", base)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == tok and env["NDF_CLAUDE_ACCOUNT"] == "a" and env["X"] == "y"
    assert "CLAUDE_CODE_USE_BEDROCK" not in env and "ANTHROPIC_API_KEY" not in env
    m = ca.account_env("metered", {"NDF_SUPERVISE_CLAUDE_FALLBACK": decl, "CLAUDE_CODE_OAUTH_TOKEN": tok})
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in m and m["NDF_CLAUDE_ACCOUNT"] == "metered"
    assert m["CLAUDE_CODE_USE_BEDROCK"] == "1" and m["AWS_PROFILE"] == "p"


def test_account_env_drops_undeclared_auth(accounts):
    """宣言が無くても、認証の優先順位でトークンより上に来る変数はアカウントの子から外す。"""
    tok = accounts.add("a")
    base = {k: "1" for k in ca.FOREIGN_AUTH_ENV}
    env = ca.account_env("a", base)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == tok and not set(ca.FOREIGN_AUTH_ENV) & set(env)


def test_metered_env_keeps_only_declared_auth():
    """従量の接続は、宣言より優先される親の認証の変数を外してから宣言を重ねる。"""
    base = {k: "1" for k in ca.FOREIGN_AUTH_ENV}
    base["NDF_SUPERVISE_CLAUDE_FALLBACK"] = "ANTHROPIC_API_KEY=sk-SECRET"
    env = ca.account_env("metered", base)
    assert env["ANTHROPIC_API_KEY"] == "sk-SECRET"
    assert not {"ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"} & set(env)


def test_token_with_short_life_is_not_given(accounts):
    """更新しないトークンは、期限まで min_left 秒以下なら渡さない。"""
    tok = accounts.add("a", expires_in=600)
    assert ca.token("a", None) == tok
    assert ca.token("a", None, min_left=1800) is None


@pytest.mark.parametrize("status", [429, 408, 500])
def test_transient_refresh_failure_keeps_account(accounts, status):
    """一時的な失敗（429・408・5xx）は再登録を求めず、期限の前なら今のトークンを使う。"""
    tok = accounts.add("a", expires_in=60)
    accounts.fake.refresh["a-refresh-SECRET"] = (status, {"error": "busy"})
    assert ca.token("a") == tok
    assert not accounts.account("a").get("needs_relogin")


def test_names_ignore_staging_and_reserved(accounts):
    accounts.add("a")
    (accounts.root / ".add-b-1").mkdir()
    (accounts.root / "metered").mkdir()
    (accounts.root / "metered" / "account.json").write_text("{}")
    assert ca.names() == ["a"]
    assert not ca.valid_name("metered") and not ca.valid_name("A") and ca.valid_name("work-1")


def test_register_replaces(accounts):
    accounts.add("a", email="x@example.com")
    st = Path(ca.staging_dir("b"))
    assert oct(st.stat().st_mode & 0o777) == "0o700" and ca.names() == ["a"]
    ca.register("b", str(st), "y@example.com")
    assert ca.names() == ["a", "b"] and accounts.account("b")["email"] == "y@example.com"
    assert "org_id" not in accounts.account("b")  # 組織が分からなければ書かない
    ca.unregister("b")
    assert ca.names() == ["a"]


def test_usage_shape_change_is_unknown(accounts):
    tok = accounts.add("a", util5=None)
    accounts.fake.usage[tok] = (200, {"five_hour": "x"})
    u = ca.usage("a")
    assert u.error == "shape" and u.score() is None and ca.load_account("a").state(time.time()) == "残量不明"


def test_spend_state(accounts):
    accounts.add("a", spend=True)
    assert ca.rows()[0]["state"] == "支出上限"
    assert ca.load_account("a").limited_until(time.time()) == math.inf


@pytest.mark.parametrize(
    "text, kind",
    [
        ("You've hit your session limit · resets 2:10am (UTC)", "five_hour"),
        ("You've hit your weekly limit", "seven_day"),
        ("You've hit your individual spend limit", "spend"),
        ("API Error: 429", "unknown"),
    ],
)
def test_kind_of_text(text, kind):
    assert cu.kind_of_text(text) == kind


def test_fallback_env_reads_given_environ():
    assert ca.fallback_env({"NDF_SUPERVISE_CLAUDE_FALLBACK": "A=1 'B=2 3' junk"}) == {"A": "1", "B": "2 3"}


def _runner(monkeypatch, section):
    sys.path.insert(0, str(SCRIPTS))
    from supervise_lib import claude as sc

    monkeypatch.setenv(ca.NAME_ENV, section)
    return sc, sc.ClaudeRunner(object())


def test_supervise_waits_when_others_are_only_limited(accounts, monkeypatch):
    """区間のトークンの残りが打ち切りより短く、他が上限なだけなら止めずに解除まで待たせる。"""
    accounts.add("a", expires_in=600)
    tb = accounts.add("b", util5=None)
    accounts.fake.set_usage(tb, window(100, 1800), window(5))
    sc, r = _runner(monkeypatch, "a")
    with pytest.raises(sc.AccountsLimited) as e:
        r.child_env(1800, {})
    assert e.value.resets_at is not None and e.value.resets_at > time.time()


def test_supervise_stops_when_no_candidate(accounts, monkeypatch):
    """替えるアカウントが 1 つも無ければ（再登録が要る）認証で止まる。"""
    accounts.add("a", expires_in=600)
    accounts.add("b")
    acc = accounts.account("b")
    acc["needs_relogin"] = True
    accounts.write(accounts.root / "b" / "account.json", acc)
    sc, r = _runner(monkeypatch, "a")
    with pytest.raises(sc.AuthUnavailable):
        r.child_env(1800, {})


# ---------------------------------------------------------------- アカウントのスコープ（#1523）

ABSENT = object()  # `scopes` のキーが無い


def _set_scopes(accounts, name, scopes):
    o = accounts.creds(name)
    o.pop("scopes")
    if scopes is not ABSENT:
        o["scopes"] = scopes
    accounts.write(accounts.root / name / ".credentials.json", {"claudeAiOauth": o})


def test_account_env_carries_scopes_of_the_store(accounts):
    """受け入れ条件 3・I1: 子のスコープは置き場の並びのまま。共有の設定ディレクトリからは読まず、取得先も呼ばない。"""
    scopes = ["user:profile", "user:mcp_servers", "user:inference"]
    accounts.add("a", scopes=scopes)
    accounts.write(accounts.shared / ".credentials.json", {"claudeAiOauth": {"accessToken": "x", "scopes": ["user:shared"]}})
    env = ca.account_env("a", {k: "1" for k in ca.FOREIGN_AUTH_ENV})
    assert env[ca.SCOPES_ENV] == " ".join(scopes) and "user:mcp_servers" in env[ca.SCOPES_ENV].split()
    assert not set(ca.FOREIGN_AUTH_ENV) & set(env)
    assert accounts.fake.usage_calls == [] and accounts.fake.refresh_calls == []


def test_account_env_reads_scopes_after_refresh(accounts):
    """I1: トークンを更新したら、更新の応答で書き直された後のスコープを渡す（更新の宛先は 1 回だけ呼ぶ）。"""
    accounts.add("a", expires_in=60, scopes=["user:inference"])
    accounts.fake.refresh["a-refresh-SECRET"] = (
        200,
        {"access_token": "a-new-SECRET", "expires_in": 28800, "scope": "user:inference user:mcp_servers"},
    )
    env = ca.account_env("a", {})
    assert (env[ca.TOKEN_ENV], env[ca.SCOPES_ENV]) == ("a-new-SECRET", "user:inference user:mcp_servers")
    assert accounts.fake.refresh_calls == ["a-refresh-SECRET"] and accounts.fake.usage_calls == []


def test_account_env_replaces_scopes_of_previous_account(accounts):
    """受け入れ条件 4・I2: 前のアカウントのスコープを残さない（上書きするか、読めなければ外す）。"""
    accounts.add("a", scopes=["user:inference", "user:mcp_servers"])
    accounts.add("b", scopes=["user:inference"])
    accounts.add("c")
    _set_scopes(accounts, "c", ABSENT)
    base = ca.account_env("a", {})
    assert ca.account_env("b", base)[ca.SCOPES_ENV] == "user:inference"
    assert ca.SCOPES_ENV not in ca.account_env("c", base)


@pytest.mark.parametrize("bad", [ABSENT, None, "user:inference", [], ["user:inference", 1], ["user:inference user:mcp_servers"], [""]])
def test_broken_scopes_start_without_the_variable(accounts, bad):
    """受け入れ条件 5・I3: `scopes` が無い・壊れていても子を起動し、スコープの変数を外す。再登録は求めない。"""
    tok = accounts.add("a")
    _set_scopes(accounts, "a", bad)
    env = ca.account_env("a", {ca.SCOPES_ENV: "user:inference user:mcp_servers"})
    assert env[ca.TOKEN_ENV] == tok and ca.SCOPES_ENV not in env
    assert not accounts.account("a")["needs_relogin"]


def test_relay_and_supervise_build_the_same_account_env(accounts, monkeypatch):
    """受け入れ条件 2・8: ラッパーと supervise.py の子の環境は認証の変数が同じで、置き場の場所を変えない。"""
    accounts.add("a", scopes=["user:inference", "user:mcp_servers"])
    accounts.add("b")
    sc, r = _runner(monkeypatch, "a")
    from relay_lib import claude as relay_claude

    base = dict(os.environ)
    store = ca.store_dir()
    wrapped, worker = relay_claude.section_env(base, "a"), r.child_env(0, {})
    keys = (ca.TOKEN_ENV, ca.SCOPES_ENV, ca.NAME_ENV, *ca.FOREIGN_AUTH_ENV, "CLAUDE_CONFIG_DIR", "NDF_ACCOUNTS_DIR")
    assert {k: wrapped.get(k) for k in keys} == {k: worker.get(k) for k in keys}
    assert wrapped[ca.SCOPES_ENV] == "user:inference user:mcp_servers"
    assert (wrapped["CLAUDE_CONFIG_DIR"], wrapped["NDF_ACCOUNTS_DIR"]) == (base["CLAUDE_CONFIG_DIR"], base["NDF_ACCOUNTS_DIR"])
    monkeypatch.setattr(ca.os, "environ", wrapped)
    assert ca.store_dir() == store


# ---------------------------------------------------------------- 残りの量で選ぶ（#1453）

MAX20, MAX5 = "default_claude_max_20x", "default_claude_max_5x"
TEAM_SPEND = {"percent": 100, "severity": "critical"}  # 2026-09-28 の nyle-team の応答（spend_limit_reached は偽）


def test_choose_by_remaining_prefers_larger_capacity(accounts):
    """AC1: 5x の 40% と 20x の 50% では、残りの量の大きい 20x を選ぶ（31.5 < 105）。"""
    accounts.add("small", tier=MAX5, util5=40, util7=0)
    accounts.add("large", tier=MAX20, util5=50, util7=0)
    c = ca.choose()
    assert (c.name, c.score, c.remaining) == ("large", 50.0, 105.0)


def test_weekly_capacity_uses_its_own_table(accounts, monkeypatch):
    """AC2: 週の枠の大きさだけを差し替えると選ぶアカウントが入れ替わる。"""
    monkeypatch.setitem(ca.CAPACITY, "t1", {"five_hour": 1000.0, "seven_day": 100.0})
    monkeypatch.setitem(ca.CAPACITY, "t2", {"five_hour": 1000.0, "seven_day": 200.0})
    accounts.add("a", tier="t1", util5=0, util7=10)  # 90
    accounts.add("b", tier="t2", util5=0, util7=60)  # 80
    assert ca.choose().name == "a"
    monkeypatch.setitem(ca.CAPACITY, "t1", {"five_hour": 1000.0, "seven_day": 50.0})  # 45
    assert ca.choose().name == "b"


def test_scoped_week_sets_remaining_and_score(accounts):
    """AC4・I5: モデル別の週の枠が週の枠より高ければ残りの量と使用率を決め、100 以上なら候補から外す。"""
    accounts.add("a", tier=MAX20, capacity={"five_hour": 1000}, util5=10, util7=38, extra={"limits": [scoped_limit(58)]})
    acc = ca.load_account("a") if ca.usage("a") else None
    assert acc.usage.score() == 58.0
    assert acc.remaining() == pytest.approx(1100 * 0.42)
    assert ca.choose().remaining == pytest.approx(1100 * 0.42)
    accounts.add("b", tier=MAX20, util5=0, util7=0, extra={"limits": [scoped_limit(100)]})
    accounts.add("c", util5=80, util7=80)
    c = ca.choose(exclude={"a"})
    assert c.name == "c" and c.earliest[0] == "b"


def test_spend_percent_or_critical_is_spend_limit(accounts):
    """AC5・I4: spend.percent 100・severity critical なら spend_limit_reached が偽でも支出上限。"""
    accounts.add("team", tier=MAX5, util5=0, util7=25, spend=False, extra={"spend": TEAM_SPEND})
    accounts.add("other", util5=80, util7=80)
    c = ca.choose()
    assert c.name == "other" and c.earliest == ("team", math.inf)
    row = next(r for r in ca.rows() if r["name"] == "team")
    assert row["state"] == "支出上限" and row["spend_limit_reached"] is True and row["spend"] == {"percent": 100.0, "severity": "critical"}


@pytest.mark.parametrize("spend", [{"percent": 100.5, "severity": None}, {"percent": 10, "severity": "critical"}])
def test_spend_reached_by_either_field(spend):
    u = cu.Usage(five_hour={"utilization": 1.0}, spend_limit_reached=False, spend=spend)
    assert u.spend_reached() is True and u.limited_until(time.time()) == math.inf


def test_unknown_capacity_and_unknown_usage_are_ordered_after(accounts):
    """AC6・I3: 残りの量の分かる群 → 使用率の群 → 残量不明（読める候補が無いときだけ）。同じ入力には同じ名前。"""
    accounts.add("a", tier=MAX20, util5=90 - 1, util7=0)  # 残りの量 23.1
    accounts.add("b", util5=1, util7=1)  # tier 無し
    accounts.add("c", tier="unknown_tier", util5=0, util7=0)
    accounts.add("d", util5=None)  # 残量不明
    assert [ca.choose().name for _ in range(2)] == ["a", "a"]
    assert ca.choose(exclude={"a"}).name == "c"
    assert ca.choose(exclude={"a", "c"}).name == "b"
    assert ca.choose(exclude={"a", "b", "c"}).name == "d"


def test_below_threshold_is_tried_before_larger_remaining(accounts, monkeypatch):
    """I3（閾値）: 閾値以上の大きい残りの量より閾値未満の候補を先に返し、そのトークンを得られなければ閾値以上を返す。"""
    accounts.add("big", tier=MAX20, capacity={"five_hour": 10000}, util5=95, util7=0)  # 500
    accounts.add("small", tier=MAX5, util5=40, util7=0)  # 31.5
    c = ca.choose()
    assert c.name == "small" and c.score < ca.switch_at()
    real = ca.token
    monkeypatch.setattr(ca, "token", lambda n, *a, **k: None if n == "small" else real(n, *a, **k))
    assert ca.choose().name == "big"


def test_remaining_is_clamped_and_none_without_capacity():
    """I1・I2: 100 を超える枠は 0、枠の大きさが無ければ None。宣言の無い枠は表、モデル別の週は週の枠の大きさ。"""
    u = cu.Usage(five_hour={"utilization": 120.0}, seven_day={"utilization": 0.0})
    assert ca.Account("a", "", False, None, u, tier=MAX20).remaining() == 0.0
    assert ca.Account("a", "", False, None, u).remaining() is None
    u = cu.Usage(five_hour={"utilization": 50.0}, seven_day={"utilization": 0.0}, scoped=[{"model": "Fable", "utilization": 90.0}])
    acc = ca.Account("a", "", False, None, u, tier=MAX20, declared={"seven_day": 500.0})
    assert acc.capacity() == {"five_hour": 210.0, "seven_day": 500.0}
    assert acc.remaining() == pytest.approx(50.0)


def test_broken_limits_and_spend_keep_windows(accounts):
    """I6: limits・spend の形が違っても 5 時間の枠と週の枠は読め、その項目だけが None。"""
    tok = accounts.add("a", util5=None)
    accounts.fake.set_usage(tok, window(20), window(30), extra={"limits": "x", "spend": "y"})
    u = ca.usage("a")
    assert u.error is None and u.score() == 30.0 and u.scoped is None and u.spend is None
    accounts.fake.set_usage(tok, window(20), window(30), extra={"limits": [{"kind": "weekly_scoped", "percent": "x"}]})
    assert cu.parse_usage(accounts.fake.usage[tok][1], time.time()).scoped is None


def test_old_usage_json_and_response_still_read(accounts):
    """AC9・I7: limits・spend の無い応答と旧い形の usage.json を読める。"""
    tok = accounts.add("a", tier=MAX20, util5=50, util7=0)
    assert ca.usage("a").scoped == [] and ca.load_account("a").remaining() == 105.0
    old = {
        "fetched_at": cu.iso_utc(time.time()),
        "five_hour": window(50),
        "seven_day": window(0),
        "spend_limit_reached": False,
        "error": None,
    }
    (accounts.root / "a" / "usage.json").write_text(json.dumps(old))
    acc = ca.load_account("a")
    assert acc.usage.scoped is None and acc.usage.spend is None and acc.usage.spend_reached() is False
    assert acc.remaining() == 105.0 and ca.choose().name == "a"
    assert tok


def test_set_capacity_writes_only_account_json(accounts):
    """F4・I8: 宣言は account.json だけに書き、`None` で外すと表の値へ戻る。"""
    accounts.add("a", tier=MAX5)
    creds = (accounts.root / "a" / ".credentials.json").read_bytes()
    assert ca.set_capacity("a", {"five_hour": None, "seven_day": 900}).capacity() == {"five_hour": 52.5, "seven_day": 900.0}
    assert accounts.account("a")["capacity"] == {"seven_day": 900.0}
    assert ca.set_capacity("a", {"five_hour": None, "seven_day": None}).capacity() == {"five_hour": 52.5, "seven_day": 640.0}
    assert accounts.account("a")["capacity"] is None
    assert (accounts.root / "a" / ".credentials.json").read_bytes() == creds
    assert ca.set_capacity("zz", {"five_hour": 1}) is None


# ---------------------------------------------------------------- 従量の接続の宣言を --settings でも渡す（#1543）


def _settings_of(args: list[str]) -> list[dict]:
    """引数の `--settings` の値を JSON として並べる（`--settings=値` の形も読む）。"""
    out = []
    for i, a in enumerate(args):
        if a == "--":
            break
        if i and args[i - 1] == "--settings":
            continue
        if a == "--settings":
            out.append(json.loads(args[i + 1]))
        elif a.startswith("--settings="):
            out.append(json.loads(a.split("=", 1)[1]))
    return out


def test_metered_settings_passes_declaration(accounts):  # noqa: F811
    """利用者の settings.json の env は子の環境変数より優先されるため、宣言を `--settings` の env でも渡す。"""
    decl = {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "default", "AWS_REGION": "ap-northeast-1"}
    ca.save_metered("bedrock", decl, {"profile": "default"})
    env = ca.account_env(ca.METERED, {})
    args = cs.metered_settings(["--model", "m", "続き"], env, str(accounts.root))
    assert _settings_of(args) == [{"env": decl}] and args[-3:] == ["--model", "m", "続き"]


def test_metered_settings_leaves_accounts_alone(accounts):  # noqa: F811
    accounts.add("a")
    ca.save_metered("bedrock", {"AWS_PROFILE": "p"}, {"profile": "p"})
    args = ["--settings", '{"model": "x"}', "p"]
    assert cs.metered_settings(args, ca.account_env("a", {}), "/") == args
    assert cs.metered_settings(args, {}, "/") == args


def test_metered_settings_merges_existing_settings(accounts, tmp_path):  # noqa: F811
    """既存の `--settings`（JSON・ファイル・`=` の形）は 1 つにまとめ、宣言のキーだけを宣言で上書きする。
    Claude Code は `--settings` が複数あると最後の 1 つだけを使うため、並べずにまとめる。"""
    ca.save_metered("bedrock", {"AWS_PROFILE": "default", "AWS_REGION": "ap-northeast-1"}, {"profile": "default"})
    env = ca.account_env(ca.METERED, {})
    user = {"model": "x", "env": {"AWS_REGION": "us-east-1", "KEEP": "1"}}
    want = {"model": "x", "env": {"AWS_REGION": "ap-northeast-1", "KEEP": "1", "AWS_PROFILE": "default"}}
    got = cs.metered_settings(["--settings", json.dumps(user), "p"], env, str(tmp_path))
    assert _settings_of(got) == [want] and got[-1] == "p"
    (tmp_path / "s.json").write_text(json.dumps(user))
    got = cs.metered_settings(["--settings=s.json", "p"], env, str(tmp_path))
    assert _settings_of(got) == [want] and "--settings=s.json" not in got
    got = cs.metered_settings(["--settings", "{}", "--", "--settings", "{}"], env, str(tmp_path))
    assert got[-2:] == ["--settings", "{}"] and _settings_of(got) == [{"env": {"AWS_PROFILE": "default", "AWS_REGION": "ap-northeast-1"}}]


def test_metered_settings_keeps_unreadable_settings(accounts, tmp_path):  # noqa: F811
    """読めない既存の `--settings` は触らない（Claude Code の読み込みの失敗をそのまま見せる）。"""
    ca.save_metered("bedrock", {"AWS_PROFILE": "p"}, {"profile": "p"})
    args = ["--settings", "missing.json", "p"]
    assert cs.metered_settings(args, ca.account_env(ca.METERED, {}), str(tmp_path)) == args


def test_metered_settings_never_carries_secrets(accounts):  # noqa: F811
    """環境変数の宣言が資格情報を持っていても、引数には載せない（環境変数だけで渡す）。"""
    env = ca.account_env(ca.METERED, {ca.FALLBACK_ENV: "CLAUDE_CODE_USE_BEDROCK=1 ANTHROPIC_API_KEY=sk-SECRET AWS_SESSION_TOKEN=t-SECRET"})
    args = cs.metered_settings([], env, "/")
    assert "SECRET" not in " ".join(args) and _settings_of(args) == [{"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}}]
    assert cs.metered_settings([], ca.account_env(ca.METERED, {ca.FALLBACK_ENV: "ANTHROPIC_API_KEY=sk-SECRET"}), "/") == []


@pytest.mark.parametrize("full", [False, True])
def test_supervise_passes_metered_declaration_as_settings(accounts, monkeypatch, full):  # noqa: F811
    """supervise.py の claude -p も、従量の接続の子へ宣言を `--settings` で渡す（#1543）。起動の語は先頭に残す。"""
    sys.path.insert(0, str(SCRIPTS))
    from supervise_lib import claude as sc

    ca.save_metered("bedrock", {"AWS_REGION": "ap-northeast-1"}, {"region": "ap-northeast-1"})
    monkeypatch.setenv("NDF_SUPERVISE_CLAUDE", "python3 fake.py")
    seen = []
    monkeypatch.setattr(
        sc, "run_ticking", lambda cmd, *a, **k: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0, '{"result": "2"}', "")
    )
    sc.call_claude("s", "p", None, str(accounts.root), 10, full=full, child_env=ca.account_env(ca.METERED, {}))
    sc.call_claude("s", "p", None, str(accounts.root), 10, full=full, child_env=None)
    assert seen[0][:2] == ["python3", "fake.py"] and _settings_of(seen[0]) == [{"env": {"AWS_REGION": "ap-northeast-1"}}]
    assert "--settings" not in seen[1]
