"""上限の設定の読み手は、1 プロセスで何回読んでも形を 1 回だけ確かめる（#3684 の受け入れ条件 2。退行を止める）。"""

from __future__ import annotations

from ndf import contracts
from ndf.contracts import limits


def test_the_settings_are_validated_once_for_three_reads(monkeypatch):
    checked: list[str] = []
    real = contracts.schema_errors

    def counting(instance, rel):
        checked.append(rel)
        return real(instance, rel)

    contracts.use_dir(None)
    monkeypatch.setattr(contracts, "schema_errors", counting)
    first = limits.table()
    once = len(checked)
    assert limits.table() is first and limits.table() is first
    assert once > 0 and len(checked) == once
    # 全件を読んだ後の 1 方針の参照も、確かめ直さない
    assert limits.policy("test-run.test") is first["test-run.test"] and len(checked) == once
    # 保存先を読み直せば、もう 1 回確かめる
    contracts.use_dir(None)
    limits.table()
    assert len(checked) == once * 2
