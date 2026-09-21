"""`lib/refresh.py` の `compare` の現状固定テスト（cross-refactoring R2-001）。

`compare` は取得失敗・前回記録なし・一致・不一致の 4 分岐を持つ純関数だが、
instructions-check のテストは `refresh.fetch` をスタブへ差し替えるため本体を通らない。
ここでは **現状の戻り値をそのまま正解として記録する**。正しさの主張ではない。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys


LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"


def _load_refresh():
    # `from __future__ import annotations` 下の dataclass は `sys.modules` から
    # 自モジュールを引くため、登録してから実行する。
    spec = importlib.util.spec_from_file_location("ndf_lib_refresh_unit", LIB / "refresh.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_compare_returns_undecidable_when_fetch_failed():
    refresh = _load_refresh()
    result = refresh.FetchResult(url="https://example.test/x", ok=False, error="HTTP 404")
    assert refresh.compare(result, "sha256:aa") == "判定できない"


def test_compare_reports_no_previous_record_when_previous_is_none():
    refresh = _load_refresh()
    result = refresh.FetchResult(url="https://example.test/x", ok=True, fingerprint="sha256:aa")
    assert refresh.compare(result, None) == "前回の記録が無い"


def test_compare_reports_no_previous_record_when_previous_is_empty():
    refresh = _load_refresh()
    result = refresh.FetchResult(url="https://example.test/x", ok=True, fingerprint="sha256:aa")
    assert refresh.compare(result, "") == "前回の記録が無い"


def test_compare_reports_unchanged_when_fingerprint_matches():
    refresh = _load_refresh()
    result = refresh.FetchResult(url="https://example.test/x", ok=True, fingerprint="sha256:aa")
    assert refresh.compare(result, "sha256:aa") == "変わっていない"


def test_compare_reports_changed_when_fingerprint_differs():
    refresh = _load_refresh()
    result = refresh.FetchResult(url="https://example.test/x", ok=True, fingerprint="sha256:aa")
    assert refresh.compare(result, "sha256:bb") == "変わった"


class _FakeResponse:
    """`opener` が返す応答の疑似実装。1 度本文を返し、次は空で終わる。"""

    def __init__(self, body: bytes):
        self._body = body
        self._done = False

    def read(self, _size: int) -> bytes:
        if self._done:
            return b""
        self._done = True
        return self._body

    def close(self) -> None:
        pass


def _opener_success_and_failure(failing_urls: set[str]):
    """URL ごとに成功／失敗を出し分ける opener を作る。

    失敗させたい URL では例外を投げ、それ以外は本文を返す。`fetch` は失敗を
    例外にせず理由へ畳むため、この差し替えで成功と失敗を混ぜられる。
    """

    def opener(url, timeout=None):  # noqa: ARG001 - timeout は使わない
        if url in failing_urls:
            raise OSError("接続できない")
        return _FakeResponse(url.encode("utf-8"))

    return opener


def test_refresh_returns_one_row_per_source_and_counts_failures():
    refresh = _load_refresh()
    sources = [
        {"name": "alpha", "url": "https://example.test/a", "checked_at": "2026-01-01", "claim": "A の主張"},
        {"name": "beta", "url": "https://example.test/b", "checked_at": "2026-01-02", "claim": "B の主張"},
        {"name": "gamma", "url": "https://example.test/c", "checked_at": "2026-01-03", "claim": "C の主張"},
    ]
    opener = _opener_success_and_failure({"https://example.test/b"})

    lines, failed = refresh.refresh(sources, timeout=1.0, opener=opener)

    # 返る行数は source 数と一致する（取れなかった URL も黙って落とさない）。
    assert len(lines) == len(sources)
    # 失敗件数は失敗した source の数と一致する。
    assert failed == 1


def test_refresh_row_carries_name_and_fetch_state():
    refresh = _load_refresh()
    sources = [
        {"name": "alpha", "url": "https://example.test/a", "checked_at": "2026-01-01", "claim": "A の主張"},
        {"name": "beta", "url": "https://example.test/b", "checked_at": "2026-01-02", "claim": "B の主張"},
    ]
    opener = _opener_success_and_failure({"https://example.test/b"})

    lines, _failed = refresh.refresh(sources, timeout=1.0, opener=opener)

    # 各行に source の name が含まれる。
    assert "alpha" in lines[0]
    assert "beta" in lines[1]
    # 取得の成否が行へ現れる（成功／失敗をそのまま固定する）。
    assert "取得できた" in lines[0]
    assert "取得できなかった" in lines[1]
