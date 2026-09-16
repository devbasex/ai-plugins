"""`issue-upkeep` の参照 `no-work.md`（「やらない」の判断）の契約。"""
from __future__ import annotations

from _helpers import NO_WORK, flat, table


def test_no_work_has_two_necessary_conditions() -> None:
    """2 つの必要条件と、欠けたときの行き先が表で示されている。

    条件 2 の行き先を「重複」に固定すると、同じ修正レイヤーを指す別の課題が重複として閉じられる。
    行き先は 3 つに分け、「要判断」へ倒す文は見積りである条件 1 だけに掛ける。
    """
    body = NO_WORK.read_text(encoding="utf-8")
    assert "## 2 つの必要条件" in body
    assert "欠けたときの行き先" in body
    assert "抱える費用が、直す費用を下回る" in body
    assert "同じ原因の他の課題へ寄せられない" in body
    rows = table(body, "| # | 条件 | 確かめ方 | 欠けたときの行き先 |")
    second = next(row for row in rows if row[0] == "2")
    assert "重複とルートコーズの両方の突き合わせの結果" in flat(second[2])
    assert second[2].index("重複") < second[2].index("ルートコーズ")
    for outcome in ("重複", "ルートコーズ", "寄せ先が無く、条件は欠けていない"):
        assert outcome in second[3], outcome
    assert "どちらも見積りである" not in flat(body)
    assert "条件 1 は見積りである" in flat(body)


def test_no_work_shows_both_kinds_of_reactivation_condition() -> None:
    """再燃の条件は観測できる形で書く。観測できない書き方の例も示す。"""
    body = NO_WORK.read_text(encoding="utf-8")
    assert "**観測できる**" in body
    assert "観測できない" in body
