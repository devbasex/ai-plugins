"""pytest-playwright 上で動く Web E2E シナリオテストパッケージ。

利用方法:
- pytest plugin として ``--pwk-config=...`` で読み込む (entry-point 経由で auto-load)
- ``pwk_config`` / ``pwk_role_<id>`` / ``pwk_evidence`` 等の fixture を test に注入
- ``@pytest.mark.page_role(...)`` で accessibility / web vitals を autouse

詳細は SKILL.md を参照。
"""

__version__ = "0.5.0"
