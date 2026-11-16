"""hook のテストの前提。中継・オーガナイザー・通知・止め方の変数を、テストを流す人の値から切り離す。"""

from __future__ import annotations

import pytest

from ._hooks import ENV_NAMES


@pytest.fixture(autouse=True)
def _hook_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
