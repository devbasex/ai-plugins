"""`monitor._scan_early_fatal` の現状固定テスト（構造改善 R3-005 の前置き）。

致命の照合を条件の連鎖から表引きへ置き換える前に、現状の戻り値を記録する。
利用上限・致命・警告の見た目の致命・いずれも無しの 4 経路を通す。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

SPEC = importlib.util.spec_from_file_location("monitor", LIB / "monitor.py")
assert SPEC is not None and SPEC.loader is not None
monitor = importlib.util.module_from_spec(SPEC)
# `monitor` は `@dataclass` を使うため、exec 前に sys.modules へ登録する
# （dataclasses がクラスのモジュールを名前で引く）。
sys.modules[SPEC.name] = monitor
SPEC.loader.exec_module(monitor)


def _err_log(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    path = tmp_path / "agent-err.log"
    path.write_text(text, encoding="utf-8")
    return path


def test_usage_limit_only_hits(tmp_path) -> None:
    """利用上限の文言だけがあるとき、その行を返す。"""
    path = _err_log(tmp_path, "Monthly request limit reached\n")

    assert monitor._scan_early_fatal(path) == "Monthly request limit reached"


def test_early_error_only_hits(tmp_path) -> None:
    """致命（認証失敗）だけがあるとき、その行を返す。"""
    path = _err_log(tmp_path, "Authentication failed\n")

    assert monitor._scan_early_fatal(path) == "Authentication failed"


def test_warning_shaped_only_hits(tmp_path) -> None:
    """警告の見た目の致命だけがあるとき、その行を返す。"""
    path = _err_log(
        tmp_path,
        "is rejected because it matches one or more rules on the denied list\n",
    )

    assert monitor._scan_early_fatal(path) == (
        "is rejected because it matches one or more rules on the denied list"
    )


def test_no_fatal_returns_none(tmp_path) -> None:
    """致命の文言が無いときは `None` を返す。"""
    path = _err_log(tmp_path, "just some ordinary progress output\n")

    assert monitor._scan_early_fatal(path) is None
