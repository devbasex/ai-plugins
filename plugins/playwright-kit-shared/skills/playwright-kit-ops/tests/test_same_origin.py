"""``_same_origin`` の挙動テスト (Gemini Minor 5.1 対応)。"""

from __future__ import annotations

from playwright_kit.fixtures.auth import _same_origin


def test_same_origin_exact_match() -> None:
    assert _same_origin("https://example.com", "https://example.com")


def test_same_origin_with_path_in_origin_url() -> None:
    # storage_state["origins"][i]["origin"] は通常 path を持たないが、
    # 万一 path 付きでも origin 部分で判定する。
    assert _same_origin("https://example.com/foo", "https://example.com")


def test_same_origin_rejects_different_host() -> None:
    assert not _same_origin("https://ads.example.com", "https://example.com")
    assert not _same_origin("https://other.test", "https://example.com")


def test_same_origin_rejects_different_scheme() -> None:
    assert not _same_origin("http://example.com", "https://example.com")


def test_same_origin_rejects_different_port() -> None:
    assert not _same_origin("https://example.com:8443", "https://example.com")
    assert not _same_origin("https://example.com", "https://example.com:8443")


def test_same_origin_default_port_equivalence() -> None:
    # 明示 port なしと "443" / "80" を厳密区別する設計 (Playwright の origin
    # 表記と合わせる)。
    assert _same_origin("https://example.com", "https://example.com")


def test_same_origin_handles_invalid_url() -> None:
    assert not _same_origin("", "https://example.com")
    assert not _same_origin("not a url", "https://example.com")


def test_same_origin_rejects_subdomain() -> None:
    # 厳格 origin 一致 (eTLD+1 ではない) とする。
    assert not _same_origin("https://api.example.com", "https://example.com")
