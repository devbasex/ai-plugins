"""ログのパーサーの一覧（`ndf.testing.ci_logs`）。pytest のログの読み方の現状固定は `tests/adapters/test_ci.py` にある。"""

from __future__ import annotations

import pytest

from ndf.testing import ci_logs


def test_pytest_is_the_only_registered_parser() -> None:
    assert ci_logs.parser_of("pytest") is ci_logs.pytest_failures


@pytest.mark.parametrize("runner", ["vitest", "jest", "phpunit", "playwright", None])
def test_unregistered_runner_has_no_parser(runner: str | None) -> None:
    # 受け入れ条件 8: 登録の無い runner に pytest の形を当てない
    assert ci_logs.parser_of(runner) is None


def test_a_parser_added_to_the_table_is_found(monkeypatch: pytest.MonkeyPatch) -> None:
    # 受け入れ条件 9: 一覧へ 1 行足せば引ける
    monkeypatch.setitem(ci_logs.PARSERS, "fake", lambda text: [text])
    parser = ci_logs.parser_of("fake")
    assert parser is not None and parser("x::y") == ["x::y"]


def test_pytest_failures_ignores_lines_without_a_python_file() -> None:
    log = "FAILED web/a.test.ts > breaks\nERROR tests/test_a.py\nFAILED tests/test_a.py::t - x\nFAILED tests/test_a.py::t - x\n"
    assert ci_logs.pytest_failures(log) == ["tests/test_a.py", "tests/test_a.py::t"]
