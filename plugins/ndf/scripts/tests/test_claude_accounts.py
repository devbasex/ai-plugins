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


def test_unknown_not_candidate_when_known_limited_exists(accounts):
    accounts.add("a", util5=None)
    accounts.add("b", util5=100, util7=10)
    assert ca.choose().name is None


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
    ca.env_for("b", {})
    ca.note_limit("a", "seven_day", None)
    assert (shared.read_bytes(), shared.stat().st_mtime_ns) == before
    assert oct(accounts.root.stat().st_mode & 0o777) == "0o700"
    for d in ("a", "b"):
        assert oct((accounts.root / d).stat().st_mode & 0o777) == "0o700"
        for f in (accounts.root / d).iterdir():
            assert oct(f.stat().st_mode & 0o777) == "0o600", f


def test_env_for_account_and_metered_do_not_mix(accounts, monkeypatch):
    tok = accounts.add("a")
    decl = "CLAUDE_CODE_USE_BEDROCK=1 AWS_PROFILE=p ANTHROPIC_API_KEY=sk-SECRET"
    base = {
        "NDF_SUPERVISE_CLAUDE_FALLBACK": decl,
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "ANTHROPIC_API_KEY": "sk-SECRET",
        "CLAUDE_CODE_OAUTH_TOKEN": "old",
        "X": "y",
    }
    env = ca.env_for("a", base)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == tok and env["NDF_CLAUDE_ACCOUNT"] == "a" and env["X"] == "y"
    assert "CLAUDE_CODE_USE_BEDROCK" not in env and "ANTHROPIC_API_KEY" not in env
    m = ca.env_for("metered", {"NDF_SUPERVISE_CLAUDE_FALLBACK": decl, "CLAUDE_CODE_OAUTH_TOKEN": tok})
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in m and m["NDF_CLAUDE_ACCOUNT"] == "metered"
    assert m["CLAUDE_CODE_USE_BEDROCK"] == "1" and m["AWS_PROFILE"] == "p"


def test_names_ignore_staging_and_reserved(accounts):
    accounts.add("a")
    (accounts.root / ".add-b-1").mkdir()
    (accounts.root / "metered").mkdir()
    (accounts.root / "metered" / "account.json").write_text("{}")
    assert ca.names() == ["a"]
    assert not ca.valid_name("metered") and not ca.valid_name("A") and ca.valid_name("work-1")


def test_register_replaces_and_owner_of(accounts):
    accounts.add("a", email="x@example.com")
    st = Path(ca.staging_dir("b"))
    assert oct(st.stat().st_mode & 0o777) == "0o700" and ca.names() == ["a"]
    assert ca.owner_of("X@example.com") == "a" and ca.owner_of("x@example.com", other_than="a") is None
    ca.register("b", str(st), "y@example.com")
    assert ca.names() == ["a", "b"] and accounts.account("b")["email"] == "y@example.com"
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
