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

    monkeypatch.setattr(contracts, "schema_errors", counting)
    assert limits.table() is limits.table() is limits.table()
    assert checked == [limits.SCHEMA]
    # 保存先を読み直せば、もう 1 回確かめる
    contracts.use_dir(None)
    limits.table()
    assert checked == [limits.SCHEMA] * 2
