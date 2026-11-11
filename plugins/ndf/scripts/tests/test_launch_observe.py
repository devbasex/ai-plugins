"""起動先の決定の観測の収集（#2930）: 使用率の控え・帳簿の直近 7 日の使用額・状態を持たないこと。"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_accounts as ca  # noqa: E402
import claude_api_keys as ak  # noqa: E402
import launch_observe as lo  # noqa: E402
import usage_ledger  # noqa: E402
from account_fake import accounts, window  # noqa: E402,F401

KEY = "sk-ant-api03-FAKE-OBSERVE-SECRET"


def at(days_ago: float) -> str:
    return datetime.fromtimestamp(time.time() - days_ago * 86400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ledger_rows(directory: Path, rows: list[dict]) -> None:
    d = directory / "claude"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "o__r.jsonl", "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_one_usage_call_per_account_per_decision(accounts, monkeypatch):  # noqa: F811
    """非機能（性能）: 1 回の決定で使用率の取得はアカウントごとに多くて 1 回。控えの寿命の中では呼ばない。"""
    accounts.add("a")
    accounts.add("b")
    lo.decide_now()
    assert len(accounts.fake.usage_calls) == 2
    lo.decide_now()
    assert len(accounts.fake.usage_calls) == 2


def test_cache_past_its_reset_is_fetched_again(accounts, monkeypatch):  # noqa: F811
    """I7・受け入れ条件 9: 控えの寿命の中でも、控えの中のリセット時刻を過ぎていれば取り直す。"""
    ta = accounts.add("a")
    accounts.fake.set_usage(ta, window(10, 2), window(90, 86400))
    snap = lo.observe()
    assert snap.accounts[0].weekly == 90.0 and len(accounts.fake.usage_calls) == 1
    accounts.fake.set_usage(ta, window(10, 3600), window(0, 7 * 86400))
    time.sleep(2.5)
    snap = lo.observe()
    assert snap.accounts[0].weekly == 0.0 and len(accounts.fake.usage_calls) == 2


def test_ledger_counts_only_the_last_seven_days_per_key(tmp_path, accounts, monkeypatch):  # noqa: F811
    """受け入れ条件 12: 8 日前の使用額は合計に入らず、6 日前の分だけが入る。キーの列の無い行は数えない。"""
    monkeypatch.setenv("NDF_USAGE_DIR", str(tmp_path / "usage"))
    accounts.add("main", util7=90)
    ak.add_key("k", KEY, "main", monthly_usd=200.0)
    ledger_rows(
        tmp_path / "usage",
        [
            {"api_key": "k", "cost_usd": 40.0, "at": at(8)},
            {"api_key": "k", "cost_usd": 5.0, "at": at(6)},
            {"cost_usd": 100.0, "at": at(1)},
        ],
    )
    snap = lo.observe(use="near-limit")
    assert snap.ledger_ok and [(k.name, k.spent_7d) for k in snap.keys] == [("k", 5.0)]
    d = lo.decide(snap)
    assert (d.route, d.target, d.evidence["spent_7d"]) == ("api_key", "k", 5.0)


def test_unreadable_ledger_is_reported(tmp_path, accounts, monkeypatch):  # noqa: F811
    """受け入れ条件 17: 帳簿を読めなければ `ledger_ok` が偽で、キーを使わない。"""
    monkeypatch.setenv("NDF_USAGE_DIR", str(tmp_path / "usage"))
    (tmp_path / "usage" / "claude" / "broken.jsonl").mkdir(parents=True)  # 読めないファイル
    accounts.add("main", util7=90)
    ak.add_key("k", KEY, "main", monthly_usd=200.0)
    snap = lo.observe(use="near-limit")
    assert not snap.ledger_ok
    assert lo.decide(snap).rule == "near_limit_ledger_unreadable"


def test_switch_at_setting_is_not_read(accounts, monkeypatch):  # noqa: F811
    """移行性: `NDF_ACCOUNT_SWITCH_AT` を設定していても同じ決定（閾値は `--weekly-at` の宣言だけで決まる）。"""
    accounts.add("a", util7=80)
    accounts.add("b", util5=None)
    before = lo.decide_now()
    monkeypatch.setenv("NDF_ACCOUNT_SWITCH_AT", "50")
    assert lo.decide_now() == before and before.target == "a" and not hasattr(ca, "switch_at")


def test_limit_observation_is_used_without_a_return_mark(accounts):  # noqa: F811
    """受け入れ条件 14: 上限の観測で別のアカウントが選ばれ、戻り先・戻り待ちの印を書かない。"""
    accounts.add("a")
    accounts.add("b", util5=None)
    assert lo.decide_now().target == "a"
    ca.note_limit("a", "five_hour", time.time() + 3600)
    d = lo.decide_now(current="a")
    assert (d.target, d.account_rule) == ("b", "usage_unknown")
    files = {p.name for p in accounts.root.rglob("*") if p.is_file()}
    assert not {"api-key-state.json", "return.json"} & files


def test_ledger_row_has_the_key_name_only_for_key_launches(tmp_path):
    """受け入れ条件 8: 記録にキーの名前を載せ、アカウントで起動した行には列が無い。"""
    rec = usage_ledger.UsageRecord.from_claude({"total_cost_usd": 1.0}, source="s", kind="work", api_key="k")
    assert json.loads(rec.to_json())["api_key"] == "k"
    rec = usage_ledger.UsageRecord.from_claude({"total_cost_usd": 1.0}, source="s", kind="work")
    assert "api_key" not in json.loads(rec.to_json())
