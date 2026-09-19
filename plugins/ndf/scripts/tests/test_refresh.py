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
