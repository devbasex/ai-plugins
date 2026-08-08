"""accessibility fixture: ``page_role`` marker に応じた axe-core 自動スキャン。

Web アクセシビリティ (WCAG 準拠) を ``axe-core`` で機械検査する fixture。
``@pytest.mark.page_role("form")`` 等が付与された test 関数の終了直前に
axe-core を自動実行する。

利用方法:
- ``@pytest.mark.page_role("form")`` を test に付与すれば autouse 経由で
  axe-core が走る (config.accessibility.auto_roles に該当する場合のみ)
- 違反があれば ``config.accessibility.fail_on_violations`` (default True) に従い
  ``pytest.fail`` する
- 明示的に scan したい場合は ``pwk_accessibility_scan`` fixture を直接呼ぶ
"""

from __future__ import annotations

from collections import Counter
from typing import Iterator

import pytest

from playwright_kit import accessibility as accessibility_mod
from playwright_kit.config import Config
from playwright_kit.fixtures.evidence import PwkEvidence


def _page_roles_from_marker(item) -> list[str]:
    """test item から ``page_role`` marker の引数 (役割名 list) を集約する。"""
    roles: list[str] = []
    for marker in item.iter_markers(name="page_role"):
        for arg in marker.args:
            if isinstance(arg, str):
                roles.append(arg)
            elif isinstance(arg, (list, tuple)):
                roles.extend(str(a) for a in arg)
    return roles


@pytest.fixture()
def pwk_accessibility_scan(page, pwk_evidence: PwkEvidence, _pwk_config_optional):
    """明示呼び出し用: ``violations = pwk_accessibility_scan()`` で 1 度スキャン。"""
    config: Config | None = _pwk_config_optional

    def _scan(*, tags: tuple[str, ...] | None = None) -> list[dict]:
        if not accessibility_mod.is_available():
            pwk_evidence.log_lines.append(
                "[accessibility] axe-playwright-python 未インストール — SKIP "
                "(`uv sync --extra a11y` で有効化)"
            )
            return []
        actual_tags = (
            tuple(tags)
            if tags is not None
            else (
                tuple(config.accessibility.tags)
                if config is not None
                else accessibility_mod.DEFAULT_TAGS
            )
        )
        violations = accessibility_mod.scan_page(page, tags=actual_tags)
        pwk_evidence.axe_violations.extend(violations)
        return violations

    return _scan


@pytest.fixture(autouse=True)
def _pwk_accessibility_autouse(request) -> Iterator[None]:
    """``page_role`` marker が付いた test の終了直前に axe-core を実行する。

    ``page`` fixture を **要求している test のみ** 対象。autouse fixture が
    無条件に ``page`` を要求すると、pytest-playwright が全 test を browser
    parametrize してしまうため、ここでは ``request.fixturenames`` を見て
    必要な test だけ取得する。

    Issue #60 fix: 旧版の ``"pwk_evidence" not in request.fixturenames`` ガードを
    廃止。test 引数に ``pwk_evidence`` を書いていなくても ``getfixturevalue``
    経由で lazy 取得し、accessibility autouse が走るようにする。

    teardown order 対策 (Issue #61): pytest fixture の teardown は LIFO のため、
    ``yield`` 後に ``getfixturevalue("pwk_evidence")`` を呼ぶと「既に解放済」
    AssertionError が発生する。setup phase で ``ev`` / ``page`` を取得して
    closure に保持し、teardown phase はその参照のみを使う。
    """

    # ``page`` を要求していない (= browser を使わない) test では何もしない。
    # これにより pure pytest test の挙動に影響を与えない。
    if "page" not in request.fixturenames:
        yield
        return

    config: Config | None = request.getfixturevalue("_pwk_config_optional")
    if config is None or not config.accessibility.enabled:
        yield
        return
    page_roles = _page_roles_from_marker(request.node)
    if not page_roles:
        yield
        return
    if not accessibility_mod.should_auto_scan(
        page_roles, auto_roles=frozenset(config.accessibility.auto_roles)
    ):
        yield
        return

    # setup phase: closure に必要なオブジェクトを束ねる。
    pwk_evidence: PwkEvidence = request.getfixturevalue("pwk_evidence")
    page = request.getfixturevalue("page")

    yield

    # teardown phase: closure に保持した ev / page のみを参照する。
    if not accessibility_mod.is_available():
        pwk_evidence.log_lines.append(
            "[accessibility autouse] axe-playwright-python 未インストール — SKIP"
        )
        return

    try:
        if page.is_closed():
            return
    except Exception:
        return

    violations = accessibility_mod.scan_page(page, tags=tuple(config.accessibility.tags))
    pwk_evidence.axe_violations.extend(violations)
    if not violations:
        return

    impacts = Counter(v.get("impact") or "unknown" for v in violations)
    impact_summary = ", ".join(f"{k}={n}" for k, n in impacts.most_common())
    pwk_evidence.log_lines.append(
        f"[accessibility autouse] {len(violations)} violations: {impact_summary}"
    )

    if config.accessibility.fail_on_violations:
        pytest.fail(
            f"[accessibility] {len(violations)} 件の axe-core 違反 "
            f"[{impact_summary}]: "
            + ", ".join(
                f"{v.get('id')}({v.get('impact', '?')})" for v in violations[:5]
            )
        )
