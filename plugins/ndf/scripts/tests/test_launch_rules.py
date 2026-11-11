"""起動先の決定（#2930・#2931 を取り込む）: 観測のスナップショット → 決定の表のテスト。

各行は issues/issue-2930-design.md の「テスト設計」の表の行に対応する。決定の関数は I/O を持たないので、観測を直に組む。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import launch_rules as lr  # noqa: E402
from launch_rules import AccountObs, KeyObs, Snapshot  # noqa: E402

H = 3600.0
NOW = 1_800_000_000.0


def five(util, reset_in_h=1.0):
    """5 時間の枠の観測（今から `reset_in_h` 時間後にリセット）。"""
    return (float(util), NOW + reset_in_h * H)


def acc(name, util5=10, weekly=10.0, reset5_h=1.0, **kw):
    """5 時間の枠のあるアカウント。`util5` が None なら 5 時間の枠の無いアカウント。"""
    f = None if util5 is None else five(util5, reset5_h)
    return AccountObs(name, five_hour=f, weekly=weekly, weekly_resets=NOW + 24 * H, **kw)


def snap(*accounts, keys=(), use="off", weekly_at=85.0, current=None, ledger_ok=True, exclude_keys=()):
    return Snapshot(NOW, use, weekly_at, current, tuple(accounts), tuple(keys), ledger_ok, frozenset(exclude_keys))


HOT = dict(weekly=90.0)  # 週の使用率が T（85%）以上


# ---------------------------------------------------------------- API キーへ逃がす条件


@pytest.mark.parametrize(
    "case, s, route, target, rule",
    [
        # 受け入れ条件 1: 5 時間の上限だけに当たり、週が T 未満のアカウントがあれば API キーを使わない（別のアカウントで起動）
        (
            "ac1_five_hour_limit_only",
            snap(acc("a", util5=100, weekly=40.0, limited_until=NOW + H), acc("b", weekly=50.0), keys=[KeyObs("k", 200.0)], use="near-limit"),
            "account",
            "b",
            "account",
        ),
        # 受け入れ条件 1: 全アカウントが 5 時間の上限で、週が T 未満なら待つ
        (
            "ac1_all_five_hour_limited",
            snap(acc("a", util5=100, weekly=40.0, limited_until=NOW + H), keys=[KeyObs("k", 200.0)], use="near-limit"),
            "wait",
            None,
            "wait_reset",
        ),
        # 受け入れ条件 2: すべてのアカウントの週が T 以上ならだけ API キー
        ("ac2_all_hot", snap(acc("a", **HOT), acc("b", weekly=86.0), keys=[KeyObs("k", 200.0)], use="near-limit"), "api_key", "k", "near_limit_key"),
        ("ac2_one_below", snap(acc("a", **HOT), acc("b", weekly=84.9), keys=[KeyObs("k", 200.0)], use="near-limit"), "account", "b", "account"),
        # 不明は T 未満と同じく発動を止める（決定 9）
        ("i2_unknown_blocks", snap(acc("a", **HOT), acc("b", weekly=None), keys=[KeyObs("k", 200.0)], use="near-limit"), "account", "a", "account"),
        # 再登録が要るアカウントは発動の判定から外す（決定 9）
        (
            "i2_relogin_is_ignored",
            snap(acc("a", **HOT), acc("b", weekly=None, relogin=True, launchable=False), keys=[KeyObs("k", 200.0)], use="near-limit"),
            "api_key",
            "k",
            "near_limit_key",
        ),
        # 受け入れ条件 4・I3: 使用額が利用枠と等しいキーは選ばない
        (
            "ac4_equal_budget",
            snap(acc("a", **HOT), keys=[KeyObs("k", 30.0, 7.0)], use="near-limit"),
            "account",
            "a",
            "near_limit_budget_used_up",
        ),
        # 受け入れ条件 5: すべてのキーが使い切ったら、アカウントの扱いへ戻り、規則の名前が示す
        (
            "ac5_all_used_up_waits",
            snap(acc("a", util5=100, limited_until=NOW + H, **HOT), keys=[KeyObs("k", 30.0, 9.0)], use="near-limit"),
            "wait",
            None,
            "near_limit_budget_used_up",
        ),
        # 受け入れ条件 11: 月の額の宣言が無いキーは選ばない
        ("ac11_undeclared", snap(acc("a", **HOT), keys=[KeyObs("k", None)], use="near-limit"), "account", "a", "near_limit_no_declared_key"),
        # 受け入れ条件 17: 帳簿を読めなければキーを使わない
        (
            "ac17_ledger_unreadable",
            snap(acc("a", **HOT), keys=[KeyObs("k", 200.0)], use="near-limit", ledger_ok=False),
            "account",
            "a",
            "near_limit_ledger_unreadable",
        ),
        # 受け入れ条件 10: この呼び出しで外したキーは選ばない
        (
            "ac10_excluded",
            snap(acc("a", **HOT), keys=[KeyObs("a1", 200.0), KeyObs("b1", 200.0, 40.0)], use="near-limit", exclude_keys={"a1"}),
            "api_key",
            "b1",
            "near_limit_key",
        ),
        # off ではキーを使わない（受け入れ条件 25 の前提）
        ("off", snap(acc("a", **HOT), keys=[KeyObs("k", 200.0)], use="off"), "account", "a", "account"),
        # always は名前の順の先頭（受け入れ条件 26 の前提）。月の額も週の使用率も問わない
        ("always", snap(acc("a"), keys=[KeyObs("z"), KeyObs("m")], use="always"), "api_key", "m", "always_key"),
        ("always_all_excluded", snap(acc("a"), keys=[KeyObs("m")], use="always", exclude_keys={"m"}), "account", "a", "account"),
    ],
)
def test_key_rules(case, s, route, target, rule):
    d = lr.decide(s)
    assert (d.route, d.target, d.rule) == (route, target, rule), case


def test_threshold_is_declared():
    """受け入れ条件 3: 同じ観測で、宣言した T で決定が変わる。"""
    s = snap(acc("a", weekly=80.0), keys=[KeyObs("k", 200.0)], use="near-limit", weekly_at=70.0)
    assert lr.decide(s).route == "api_key"
    s = snap(acc("a", weekly=80.0), keys=[KeyObs("k", 200.0)], use="near-limit", weekly_at=90.0)
    assert lr.decide(s).route == "account"


def test_weekly_budget():
    """受け入れ条件 6: 週の利用枠 = 月の額 × 7 ÷ 30。"""
    assert round(lr.weekly_budget(200.0), 2) == 46.67
    assert round(lr.weekly_budget(100.0), 2) == 23.33


@pytest.mark.parametrize(
    "a_spent, b_spent, want",
    [
        (30.0, 5.0, "b"),  # A 30 / 46.67、B 5 / 23.33 → B（割合でも額でも B）
        (10.0, 6.0, "a"),  # A 10 / 46.67（21%）、B 6 / 23.33（26%）→ A（額の小さい順なら B になる）
    ],
)
def test_keys_are_used_evenly(a_spent, b_spent, want):
    """受け入れ条件 7: 使用の割合（直近 7 日の使用額 ÷ 週の利用枠）の最も小さいキー。"""
    s = snap(acc("a", **HOT), keys=[KeyObs("a", 200.0, a_spent), KeyObs("b", 100.0, b_spent)], use="near-limit")
    d = lr.decide(s)
    assert (d.target, d.rule) == (want, "near_limit_key")
    assert d.evidence["weekly_budget"] == pytest.approx(lr.weekly_budget(200.0 if want == "a" else 100.0), abs=1e-3)


def test_previous_route_does_not_matter_after_a_reset():
    """受け入れ条件 9: 1 つのアカウントの週がリセットされた観測では、前回キーだったかに依らずキーを使わない。"""
    hot = snap(acc("a", **HOT), acc("b", **HOT), keys=[KeyObs("k", 200.0)], use="near-limit")
    assert lr.decide(hot).route == "api_key"
    reset = snap(acc("a", **HOT), acc("b", weekly=0.0), keys=[KeyObs("k", 200.0)], use="near-limit")
    d = lr.decide(reset)
    assert (d.route, d.target) == ("account", "b")


# ---------------------------------------------------------------- アカウントの選び方


@pytest.mark.parametrize(
    "case, accounts, current, target, account_rule",
    [
        # 受け入れ条件 13: 5 時間の枠のあるアカウントを先に
        ("ac13_five_hour_first", [acc("gmail", util5=None, weekly=2.0), acc("team", weekly=50.0)], None, "team", "five_hour_first"),
        # 受け入れ条件 13: 5 時間の枠があっても週が T 以上なら、5 時間の枠の無いアカウントが先
        ("ac13_over_threshold", [acc("gmail", util5=None, weekly=2.0), acc("team", **HOT)], None, "gmail", "weekly_only"),
        # 受け入れ条件 14: 上限のアカウントを候補に残さない
        ("ac14_limited", [acc("a", util5=100, limited_until=NOW + H), acc("b", util5=None)], "a", "b", "weekly_only"),
        # 受け入れ条件 15 の例 1: 残り 30%・リセット 1 時間後の A と、残り 30%・4 時間後の B では A
        ("ac15_example1", [acc("A", util5=70, reset5_h=1), acc("B", util5=70, reset5_h=4)], None, "A", "five_hour_first"),
        # 受け入れ条件 15 の例 2: リセットまで持たない残り 5% の A と、持つ残り 30% の B では B
        ("ac15_example2", [acc("A", util5=95, reset5_h=1), acc("B", util5=70, reset5_h=1)], None, "B", "five_hour_first"),
        # どれもリセットまで持たなければ、使い切るまでの時間の長いもの
        ("ac15_none_lasts", [acc("A", util5=90, reset5_h=4), acc("B", util5=50, reset5_h=4.9)], None, "A", "five_hour_first"),
        # 使用率 0 はリセットまで持つ
        ("ac15_zero_lasts", [acc("A", util5=0, reset5_h=3), acc("B", util5=70, reset5_h=4)], None, "A", "five_hour_first"),
        # 受け入れ条件 16: 読めるアカウントに候補があれば残量不明は選ばない
        ("ac16_unknown_after", [acc("a", weekly=None), acc("b", **HOT)], None, "b", "over_threshold"),
        ("ac16_unknown_only", [acc("a", weekly=None), acc("b", util5=100, limited_until=NOW + H)], None, "a", "usage_unknown"),
        # 組 3 の中は週の使用率の小さい順（決定 8）
        ("group3_order", [acc("a", weekly=95.0), acc("b", weekly=88.0)], None, "b", "over_threshold"),
        # 受け入れ条件 18・I5: 今が組 2 で組 1 の候補があれば替える
        ("ac18_switch_up", [acc("gmail", util5=None, weekly=2.0), acc("team", weekly=10.0)], "gmail", "team", "five_hour_first"),
        # I5: 今が組 1 で同じ組の別の候補が先頭でも替えない
        ("i5_keep_current", [acc("A", util5=70, reset5_h=1), acc("B", util5=70, reset5_h=4)], "B", "B", "keep_current"),
        # I4: 起動できない・再登録が要るアカウントは候補に入らない
        ("i4_not_launchable", [acc("a", launchable=False), acc("b", weekly=50.0)], None, "b", "five_hour_first"),
    ],
)
def test_account_groups(case, accounts, current, target, account_rule):
    d = lr.decide(snap(*accounts, current=current))
    assert (d.route, d.target, d.rule, d.account_rule) == ("account", target, "account", account_rule), case


def test_example_values_in_the_design():
    """設計の「選ぶ順の組」の例の値（使い切るまでの時間）。"""
    lasts, to_empty = lr.lasts_until_reset(70.0, NOW + H, lr.FIVE_HOUR_SECONDS, NOW)
    assert lasts and to_empty / H == pytest.approx(1.714, abs=1e-3)
    lasts, to_empty = lr.lasts_until_reset(70.0, NOW + 4 * H, lr.FIVE_HOUR_SECONDS, NOW)
    assert not lasts and to_empty / H == pytest.approx(0.429, abs=1e-3)
    lasts, to_empty = lr.lasts_until_reset(95.0, NOW + H, lr.FIVE_HOUR_SECONDS, NOW)
    assert not lasts and to_empty / H == pytest.approx(0.211, abs=1e-3)


def test_wait_until_is_the_earliest_release():
    """I4: 候補が無ければ待つ。解除の時刻は上限にあるアカウントの最も早いもの。上限にあるものが無ければ None。"""
    d = lr.decide(snap(acc("a", limited_until=NOW + 2 * H), acc("b", limited_until=NOW + H), acc("c", relogin=True, launchable=False)))
    assert (d.route, d.rule, d.wait_until) == ("wait", "wait_reset", NOW + H)
    d = lr.decide(snap(acc("a", relogin=True, launchable=False)))
    assert (d.route, d.wait_until) == ("wait", None)


# ---------------------------------------------------------------- 決定の出し方


def test_same_snapshot_gives_the_same_decision():
    """受け入れ条件 20・I1: 同じスナップショットから同じ決定（規則の名前・根拠の値を含む）。"""
    s = snap(acc("a", **HOT), acc("b", weekly=88.0), keys=[KeyObs("k", 200.0, 3.0)], use="near-limit")
    assert lr.decide(s) == lr.decide(s)
    assert lr.decide(s).evidence == {
        "monthly_usd": 200.0,
        "spent_7d": 3.0,
        "weekly_budget": 46.6667,
        "ratio": 0.0643,
        "weekly_at": 85.0,
        "weekly_min": 88.0,
    }


def test_decision_module_has_no_io_imports():
    """受け入れ条件 20: 決定の関数のモジュールは時刻・ファイル・API の部品を import しない。"""
    tree = ast.parse((SCRIPTS / "lib" / "launch_rules.py").read_text(encoding="utf-8"))
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert names <= {"__future__", "math", "dataclasses"}


def test_rules_and_groups_are_tables():
    """規則と組は名前の並びで持つ（設計の表の順）。"""
    assert [n for n, _ in lr.RULES] == [
        "always_key",
        "near_limit_key",
        "near_limit_ledger_unreadable",
        "near_limit_no_declared_key",
        "near_limit_budget_used_up",
        "account",
    ]
    assert [g.name for g in lr.GROUPS] == ["five_hour_first", "weekly_only", "over_threshold", "usage_unknown"]
    assert lr.WEEKLY_AT_DEFAULT == 85.0  # 受け入れ条件 24（決定 10）
