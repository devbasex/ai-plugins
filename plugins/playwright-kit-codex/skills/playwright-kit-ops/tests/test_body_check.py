"""body_check (v0.4.0) の純粋関数 / config / fixture ロジックのユニットテスト。

Playwright を起動しない部分のみ。E2E は Phase 3 以降の smoke で別途検証する。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from playwright_kit.body_check import (
    BodyViolation,
    is_html_response,
    scan_body,
)
from playwright_kit.config import (
    AccessibilityConfig,
    BasicAuth,
    BodyCheckConfig,
    Config,
    WebVitalsConfig,
    PlaywrightConfig,
    ReportConfig,
    RunnerConfig,
    _body_check_from_raw,
)
from playwright_kit.fixtures.body_check import (
    _build_response_handler,
    _format_violation_summary,
    _write_jsonl,
)
from playwright_kit.fixtures.evidence import PwkEvidence


# ---------------------------------------------------------------------
# scan_body
# ---------------------------------------------------------------------


def test_scan_body_detects_fatal_anywhere_in_body():
    body = "<html><body><p>ok</p>...<br>Fatal error: bang</body></html>"
    violations = scan_body(
        body,
        url="https://e.example/x.php",
        fatal_patterns=["Fatal error", "Uncaught"],
    )
    assert len(violations) == 1
    v = violations[0]
    assert v.category == "fatal"
    assert v.pattern == "Fatal error"
    assert "Fatal error: bang" in v.snippet


def test_scan_body_warning_only_in_head_chars():
    """warning_patterns は head N 文字 (code points) に限ってマッチさせる。"""
    head_pollution = "STRICT: warning at top\n" + ("a" * 1000) + " STRICT: deep"
    violations = scan_body(
        head_pollution,
        url="https://e.example/u.php",
        warning_patterns=["STRICT:"],
        warning_head_chars=100,
    )
    # head のヒットだけ拾う (deep 側は無視)
    assert len(violations) == 1
    assert violations[0].category == "warning"
    assert violations[0].pattern == "STRICT:"


def test_scan_body_warning_head_uses_code_points_not_bytes():
    """warning_head_chars は **文字数** (code point) で切る。

    PLAN18 のフィールド名は ``warning_head_bytes`` だが、説明文は
    「先頭 300 文字」と書かれており、実用的にも日本語ページで bytes だと
    100 字しか見られないため文字数を採用している。
    日本語 (3 byte/字) を 200 字並べた後 ``Notice:`` を置いても、head
    閾値 300 文字の中に収まれば検出できる。
    """
    body = ("あ" * 200) + "Notice: header leak"
    violations = scan_body(
        body,
        url="https://e.example/jp.php",
        warning_patterns=["Notice:"],
        warning_head_chars=300,
    )
    # 文字数換算なので head 内 (200 字 < 300 字)
    assert len(violations) == 1
    assert violations[0].pattern == "Notice:"


def test_scan_body_warning_head_excludes_beyond_chars():
    """head 閾値より後ろにある warning は拾わない (文字数換算で正しく弾く)。"""
    body = ("a" * 350) + "Notice: too late"
    violations = scan_body(
        body,
        url="https://e.example/x.php",
        warning_patterns=["Notice:"],
        warning_head_chars=300,
    )
    assert violations == []


def test_scan_body_warning_ignored_when_only_in_body_tail():
    """warning_patterns が head に出ていなければ拾わない (説明文の Notice: は許容)。"""
    body = ("a" * 500) + "Notice: something explanatory mid-body"
    violations = scan_body(
        body,
        url="https://e.example/u.php",
        warning_patterns=["Notice:"],
        warning_head_chars=300,
    )
    assert violations == []


def test_scan_body_not_found_anywhere():
    body = "<html>... File not found ...</html>"
    violations = scan_body(
        body,
        url="https://e.example/x.php",
        not_found_patterns=["File not found"],
    )
    assert len(violations) == 1
    assert violations[0].category == "not_found"


def test_scan_body_returns_empty_for_empty_body():
    assert scan_body("", url="https://e/x", fatal_patterns=["Fatal error"]) == []


def test_scan_body_ignores_empty_pattern_strings():
    """空文字列パターンは false-positive を防ぐため無視。"""
    body = "anything"
    assert scan_body(body, url="x", fatal_patterns=["", "  "]) == []


def test_scan_body_handles_multiple_categories():
    body = "STRICT: top\nFatal error inside\nFile not found at end"
    violations = scan_body(
        body,
        url="https://e.example/y.php",
        fatal_patterns=["Fatal error"],
        warning_patterns=["STRICT:"],
        not_found_patterns=["File not found"],
        warning_head_chars=300,
    )
    cats = {v.category for v in violations}
    assert cats == {"fatal", "warning", "not_found"}


def test_body_violation_to_dict_round_trip():
    v = BodyViolation(
        url="https://e/x", category="fatal", pattern="Fatal error", snippet="snip"
    )
    d = v.to_dict()
    assert d == {
        "url": "https://e/x",
        "category": "fatal",
        "pattern": "Fatal error",
        "snippet": "snip",
    }


# ---------------------------------------------------------------------
# is_html_response
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "ctype,expected",
    [
        ("text/html", True),
        ("text/html; charset=utf-8", True),
        ("Application/XHTML+XML", True),
        ("application/json", False),
        ("image/png", False),
        ("", False),
        (None, False),
    ],
)
def test_is_html_response(ctype, expected):
    assert is_html_response(ctype) is expected


# ---------------------------------------------------------------------
# BodyCheckConfig loader
# ---------------------------------------------------------------------


def test_body_check_from_raw_defaults_when_empty():
    """body_check セクション未記述時は dataclass の default 値が効く。

    config を書かなくても PHP プロジェクトで素直に動くよう、default で
    enabled=True + PHP 系パターンを内蔵する。
    """
    cfg = _body_check_from_raw({})
    assert cfg.enabled is True
    assert cfg.warning_head_chars == 300
    assert cfg.fail_on_match is True
    # 内蔵 PHP 系 default パターン
    assert "Fatal error" in cfg.fatal_patterns
    assert "STRICT:" in cfg.warning_patterns
    assert "File not found" in cfg.not_found_patterns


def test_body_check_from_raw_explicit_empty_disables_category():
    """``fatal_patterns: []`` を明示すれば default を上書きして空にできる。"""
    cfg = _body_check_from_raw({"fatal_patterns": []})
    assert cfg.fatal_patterns == []
    # 他カテゴリは default のまま
    assert cfg.warning_patterns != []


def test_body_check_from_raw_accepts_legacy_warning_head_bytes_alias():
    """旧フィールド名 ``warning_head_bytes`` も alias として受理する。"""
    cfg = _body_check_from_raw({"warning_head_bytes": 250})
    assert cfg.warning_head_chars == 250


def test_body_check_from_raw_new_name_takes_priority_over_alias():
    """新旧両方が指定されたら新名 ``warning_head_chars`` を優先する。"""
    cfg = _body_check_from_raw(
        {"warning_head_chars": 400, "warning_head_bytes": 100}
    )
    assert cfg.warning_head_chars == 400


def test_body_check_from_raw_full():
    raw = {
        "enabled": True,
        "fatal_patterns": ["Fatal error", "Uncaught"],
        "warning_patterns": ["STRICT:"],
        "warning_head_chars": 200,
        "not_found_patterns": ["File not found"],
        "fail_on_match": False,
    }
    cfg = _body_check_from_raw(raw)
    assert cfg.enabled is True
    assert cfg.fatal_patterns == ["Fatal error", "Uncaught"]
    assert cfg.warning_patterns == ["STRICT:"]
    assert cfg.warning_head_chars == 200
    assert cfg.not_found_patterns == ["File not found"]
    assert cfg.fail_on_match is False


def test_body_check_from_raw_coerces_non_str_patterns():
    raw = {"fatal_patterns": [123, "Fatal error"]}
    cfg = _body_check_from_raw(raw)
    assert cfg.fatal_patterns == ["123", "Fatal error"]


def test_config_default_body_check_is_enabled():
    """``Config`` の default で body_check は有効、PHP 系 default パターンが効く。"""
    cfg = Config(
        base_url="https://example.com",
        basic_auth=BasicAuth(user="", password=""),
        verify_tls=False,
        roles={},
        playwright=PlaywrightConfig.defaults(),
        runner=RunnerConfig(),
        report=ReportConfig(),
        config_path=Path("/tmp/scenario.config.yaml"),
        accessibility=AccessibilityConfig(),
        web_vitals=WebVitalsConfig(),
    )
    assert cfg.body_check.enabled is True
    assert "Fatal error" in cfg.body_check.fatal_patterns


# ---------------------------------------------------------------------
# fixture helpers
# ---------------------------------------------------------------------


def _make_config_with_body_check(**kwargs) -> Config:
    bc = BodyCheckConfig(
        enabled=kwargs.pop("enabled", True),
        fatal_patterns=kwargs.pop("fatal_patterns", ["Fatal error"]),
        warning_patterns=kwargs.pop("warning_patterns", ["STRICT:"]),
        warning_head_chars=kwargs.pop("warning_head_chars", 300),
        not_found_patterns=kwargs.pop("not_found_patterns", []),
        fail_on_match=kwargs.pop("fail_on_match", True),
    )
    return Config(
        base_url="https://example.com",
        basic_auth=BasicAuth(user="", password=""),
        verify_tls=False,
        roles={},
        playwright=PlaywrightConfig.defaults(),
        runner=RunnerConfig(),
        report=ReportConfig(),
        config_path=Path("/tmp/scenario.config.yaml"),
        accessibility=AccessibilityConfig(),
        web_vitals=WebVitalsConfig(),
        body_check=bc,
    )


def _make_response(*, url: str, body: str, content_type: str = "text/html") -> MagicMock:
    resp = MagicMock()
    resp.url = url
    resp.headers = {"content-type": content_type}
    resp.text.return_value = body
    return resp


def test_response_handler_records_violation_for_html(tmp_path: Path):
    cfg = _make_config_with_body_check()
    ev = PwkEvidence(case_dir=tmp_path, config=cfg, enabled=True)
    handler = _build_response_handler(cfg.body_check, ev)

    handler(
        _make_response(
            url="https://e.example/u.php",
            body="STRICT:漏れ\n<body>...</body>",
        )
    )
    assert len(ev.body_check_violations) == 1
    assert ev.body_check_violations[0]["pattern"] == "STRICT:"
    assert ev.body_check_violations[0]["url"] == "https://e.example/u.php"


def test_response_handler_skips_non_html(tmp_path: Path):
    cfg = _make_config_with_body_check()
    ev = PwkEvidence(case_dir=tmp_path, config=cfg, enabled=True)
    handler = _build_response_handler(cfg.body_check, ev)

    # JSON response with the same string must NOT be inspected
    handler(
        _make_response(
            url="https://e.example/api",
            body='{"x":"Fatal error"}',
            content_type="application/json",
        )
    )
    assert ev.body_check_violations == []


def test_response_handler_swallows_text_failure(tmp_path: Path):
    """response.text() が失敗しても test を落とさず log_lines に残らない (静かに skip)。"""
    cfg = _make_config_with_body_check()
    ev = PwkEvidence(case_dir=tmp_path, config=cfg, enabled=True)
    handler = _build_response_handler(cfg.body_check, ev)

    resp = MagicMock()
    resp.url = "https://e.example/x"
    resp.headers = {"content-type": "text/html"}
    resp.text.side_effect = Exception("boom")

    # 例外で test を潰さないこと
    handler(resp)
    assert ev.body_check_violations == []


def test_write_jsonl_outputs_one_line_per_violation(tmp_path: Path):
    cfg = _make_config_with_body_check()
    ev = PwkEvidence(case_dir=tmp_path, config=cfg, enabled=True)
    ev.body_check_violations = [
        {"url": "u1", "category": "fatal", "pattern": "Fatal error", "snippet": "..."},
        {"url": "u2", "category": "warning", "pattern": "STRICT:", "snippet": "..."},
    ]
    _write_jsonl(ev)

    jsonl_path = tmp_path / "body_check.jsonl"
    assert jsonl_path.exists()
    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_write_jsonl_noop_when_no_violations(tmp_path: Path):
    cfg = _make_config_with_body_check()
    ev = PwkEvidence(case_dir=tmp_path, config=cfg, enabled=True)
    _write_jsonl(ev)
    assert not (tmp_path / "body_check.jsonl").exists()


def test_format_violation_summary_truncates_long_lists():
    vs = [
        {"url": f"u{i}", "category": "fatal", "pattern": "Fatal error", "snippet": "x"}
        for i in range(10)
    ]
    s = _format_violation_summary(vs, limit=3)
    assert "+7 more" in s
    # head 3 件は含まれる
    assert "u0" in s and "u1" in s and "u2" in s
    # 末尾 (u9) は含まれない
    assert "u9" not in s


def test_pwk_evidence_default_body_check_violations_is_empty(tmp_path: Path):
    cfg = _make_config_with_body_check()
    ev = PwkEvidence(case_dir=tmp_path, config=cfg, enabled=True)
    assert ev.body_check_violations == []
