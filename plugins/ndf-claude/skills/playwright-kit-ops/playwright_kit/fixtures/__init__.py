"""playwright_kit pytest fixtures (E2E シナリオテスト)。

利用者は通常の pytest テストを書き、`pwk_config` / `pwk_role_<id>` 等の
fixture をパラメタ宣言するだけで NDF の機能 (config / 認証 / evidence /
accessibility / web vitals / overlay / Drive) を享受できる。

各 fixture の実体はサブモジュールに分離する:
- ``auth``          : ``pwk_config`` / ``pwk_role_<id>`` (login 済 storage_state)
- ``evidence``      : ``pwk_evidence`` (HAR / trace / console listeners)
- ``accessibility`` : autouse hook で page_role marker に応じ axe-core を実行
- ``web_vitals``    : autouse hook で page_role marker に応じ Core Web Vitals 計測

pytest plugin (``playwright_kit.pytest_plugin``) から ``pytest_plugins`` で
読み込まれる想定。利用者プロジェクトの ``conftest.py`` で個別 import する必要は無い。
"""

from __future__ import annotations

__all__ = ["auth"]
