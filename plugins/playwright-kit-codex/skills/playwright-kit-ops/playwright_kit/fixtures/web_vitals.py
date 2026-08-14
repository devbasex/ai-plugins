"""Web Vitals fixture: ``page_role`` marker に応じた Core Web Vitals 自動計測。

``@pytest.mark.page_role("dashboard")`` 等が付与された test の終了直前に
LCP (Largest Contentful Paint) / CLS (Cumulative Layout Shift) /
TTFB (Time To First Byte) / longest_task (Long Tasks API) を計測する。
"""

from __future__ import annotations

from typing import Iterator

import pytest

from playwright_kit import web_vitals as web_vitals_mod
from playwright_kit.config import Config
from playwright_kit.fixtures.evidence import PwkEvidence


def _page_roles_from_marker(item) -> list[str]:
    roles: list[str] = []
    for marker in item.iter_markers(name="page_role"):
        for arg in marker.args:
            if isinstance(arg, str):
                roles.append(arg)
            elif isinstance(arg, (list, tuple)):
                roles.extend(str(a) for a in arg)
    return roles


@pytest.fixture()
def pwk_web_vitals_measure(page, pwk_evidence: PwkEvidence, _pwk_config_optional):
    """明示呼び出し用: ``metrics = pwk_web_vitals_measure()`` で 1 度計測。"""
    config: Config | None = _pwk_config_optional

    def _measure(*, observe_ms: int | None = None) -> dict[str, float]:
        ms = (
            int(observe_ms)
            if observe_ms is not None
            else (
                int(config.web_vitals.observe_ms)
                if config is not None
                else 5000
            )
        )
        metrics = web_vitals_mod.measure_page(page, observe_ms=ms)
        pwk_evidence.web_vitals_metrics.update(metrics)
        pwk_evidence.web_vitals_passed = web_vitals_mod.passed(pwk_evidence.web_vitals_metrics)
        return metrics

    return _measure


@pytest.fixture(autouse=True)
def _pwk_web_vitals_autouse(request) -> Iterator[None]:
    """``page_role`` marker が付いた test の終了直前に Web Vitals 計測を行う。

    accessibility autouse と同じく ``page`` fixture を要求している test のみ対象。

    Issue #60 fix: 旧版の ``"pwk_evidence" not in request.fixturenames`` ガードを
    廃止。test 引数に ``pwk_evidence`` を書いていなくても ``getfixturevalue``
    経由で lazy 取得し、Web Vitals autouse が走るようにする。

    teardown order 対策 (Issue #61): ``yield`` 後に ``pwk_evidence`` を fetch
    しようとすると LIFO 解放済の AssertionError になるため、setup phase で
    ``ev`` / ``page`` を取得して closure に保持する。
    """

    if "page" not in request.fixturenames:
        yield
        return

    config: Config | None = request.getfixturevalue("_pwk_config_optional")
    if config is None or not config.web_vitals.enabled:
        yield
        return
    page_roles = _page_roles_from_marker(request.node)
    if not page_roles:
        yield
        return
    if not web_vitals_mod.should_auto_measure(
        page_roles, auto_roles=frozenset(config.web_vitals.auto_roles)
    ):
        yield
        return

    # setup phase: closure に保持。
    pwk_evidence: PwkEvidence = request.getfixturevalue("pwk_evidence")
    page = request.getfixturevalue("page")

    yield

    # teardown phase: closure 経由でアクセス。
    try:
        if page.is_closed():
            return
    except Exception:
        return

    metrics = web_vitals_mod.measure_page(page, observe_ms=int(config.web_vitals.observe_ms))
    pwk_evidence.web_vitals_metrics.update(metrics)
    pwk_evidence.web_vitals_passed = web_vitals_mod.passed(pwk_evidence.web_vitals_metrics)

    detail = ", ".join(
        f"{k}={v:.1f}({web_vitals_mod.judge(k, v)})" for k, v in metrics.items()
    ) or "no metrics collected"
    pwk_evidence.log_lines.append(f"[web_vitals autouse] {detail}")

    if not pwk_evidence.web_vitals_passed and config.web_vitals.fail_on_poor:
        pytest.fail(f"[web_vitals] poor metric を検出: {detail}")
