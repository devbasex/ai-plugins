"""ndf.core のテストが共有する fixture。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ndf.core import durable


@pytest.fixture
def root(tmp_path):
    """チェックアウトの保存先。終わったら `prepare_store` が持った共有のロックを放す。"""
    path = tmp_path / "ndf" / "state"
    yield path
    durable.release_store(path)


@pytest.fixture
def clock(monkeypatch):
    """`time.sleep` が進める偽の時計。`sleeps` は待った秒の並び、`on_sleep` は待つたびに呼ぶ関数。"""
    fake = SimpleNamespace(now=100.0, sleeps=[], on_sleep=lambda: None)

    def sleep(seconds):
        fake.sleeps.append(seconds)
        fake.now += seconds
        fake.on_sleep()

    monkeypatch.setattr("time.monotonic", lambda: fake.now)
    monkeypatch.setattr("time.sleep", sleep)
    return fake
