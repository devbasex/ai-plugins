"""accessibility / web_vitals モジュールの page_role 自動判定と Web Vitals 閾値判定のテスト。"""

from __future__ import annotations

from playwright_kit import accessibility, web_vitals


class TestA11yIsAvailable:
    def test_returns_bool(self):
        # Maj-9: 環境に依存するが、bool が返ることを最低保証
        assert isinstance(accessibility.is_available(), bool)


class TestA11yAutoScan:
    def test_lp_triggers(self):
        assert accessibility.should_auto_scan(["lp"]) is True

    def test_form_triggers(self):
        assert accessibility.should_auto_scan(["form"]) is True

    def test_modal_does_not_trigger_by_default(self):
        # modal は変動的なので自動スキャン対象外 (DEFAULT_AUTO_ROLES にない)
        assert accessibility.should_auto_scan(["modal"]) is False

    def test_multiple_roles_any_match(self):
        assert accessibility.should_auto_scan(["modal", "form"]) is True

    def test_empty_list(self):
        assert accessibility.should_auto_scan([]) is False

    def test_custom_auto_roles(self):
        assert accessibility.should_auto_scan(["custom"], auto_roles=frozenset({"custom"})) is True


class TestCwvAutoMeasure:
    def test_lp_triggers(self):
        assert web_vitals.should_auto_measure(["lp"]) is True

    def test_form_does_not_trigger(self):
        # form はインタラクション主体なので CWV 自動計測対象外
        assert web_vitals.should_auto_measure(["form"]) is False


class TestCwvJudge:
    def test_lcp_good(self):
        assert web_vitals.judge("lcp_ms", 2000) == "good"

    def test_lcp_needs_improvement(self):
        assert web_vitals.judge("lcp_ms", 3000) == "needs-improvement"

    def test_lcp_poor(self):
        assert web_vitals.judge("lcp_ms", 5000) == "poor"

    def test_cls_good(self):
        assert web_vitals.judge("cls", 0.05) == "good"

    def test_cls_poor(self):
        assert web_vitals.judge("cls", 0.5) == "poor"

    def test_unknown_metric(self):
        assert web_vitals.judge("foo", 1.0) == "unknown"

    def test_none_value(self):
        assert web_vitals.judge("lcp_ms", None) == "unknown"


class TestCwvPassed:
    def test_all_good(self):
        assert web_vitals.passed({"lcp_ms": 2000, "cls": 0.05}) is True

    def test_needs_improvement_still_passes(self):
        # poor でなければ pass
        assert web_vitals.passed({"lcp_ms": 3000}) is True

    def test_poor_fails(self):
        assert web_vitals.passed({"lcp_ms": 5000}) is False

    def test_empty_metrics_passes(self):
        # 計測失敗 (空 dict) は判定不能なので fail にはしない
        assert web_vitals.passed({}) is True
