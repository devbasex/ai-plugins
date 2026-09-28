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
import claude_usage as cu  # noqa: E402
from account_fake import accounts, window  # noqa: E402,F401


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
