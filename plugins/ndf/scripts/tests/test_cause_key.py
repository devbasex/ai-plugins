"""原因の鍵（#2343 の決定 6・受け入れ条件 4）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import cause_key  # noqa: E402


def test_numbers_alone_do_not_split_the_cause() -> None:
    a = cause_key.make("exit=1", "PR #1976 の CI が失敗: runtime-smoke (agy)（run 38353378585）")
    b = cause_key.make("exit=1", "PR #1976 の CI が失敗: runtime-smoke (agy)（run 38360000001）")
    assert a == b and a.startswith("exit=1: ")


def test_a_different_kind_or_text_is_another_cause() -> None:
    text = "CI が失敗: runtime-smoke"
    assert cause_key.make("exit=1", text) != cause_key.make("exit=2", text)
    assert cause_key.make("exit=1", text) != cause_key.make("exit=1", "CI が失敗: lint")


def test_times_urls_paths_and_hashes_are_masked() -> None:
    key = cause_key.make(
        "OSError",
        "2026-11-08T12:00:01+09:00 https://github.com/x/y/actions/runs/1 /tmp/a/b.log src/x.py 3df9e0092ab が残った",
    )
    assert key == "OSError: <時刻> <URL> <パス> <パス> <sha> が残った"


def test_an_empty_text_cannot_be_told_apart() -> None:
    assert cause_key.make("exit=1", "  ") is None
    assert cause_key.brief(None) == "原因を見分けられない"


def test_the_key_is_capped_and_the_brief_is_short() -> None:
    key = cause_key.make("E", "あ" * 500)
    assert len(key) == len("E: ") + cause_key.KEEP
    assert len(cause_key.brief(key)) == cause_key.BRIEF


def test_an_exception_takes_its_type_name() -> None:
    assert cause_key.of_exception(TimeoutError("timed out after 30 s")) == "TimeoutError: timed out after <数> s"
