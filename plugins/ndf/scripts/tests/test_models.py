"""モデル指定の公開入口に対する現状固定テスト。"""
from __future__ import annotations

import pathlib
import sys

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from models import (
    ModelSpecError,
    is_measurable,
    mismatch_warning,
    observed_model,
    parse_model_args,
    separation_reason,
)


@pytest.mark.parametrize(
    ("values", "message_part"),
    [
        (["codex-default"], "codex-default"),
        (["unknown=default"], "unknown"),
        (["codex="], "codex="),
        (["codex=first", "codex=second"], "codex"),
    ],
)
def test_parse_model_args_rejects_invalid_specs(
    values: list[str], message_part: str
) -> None:
    """現状固定。不正指定は初期化時に原因を示す例外として返る。"""
    with pytest.raises(ModelSpecError) as exc_info:
        parse_model_args(values)

    assert message_part in str(exc_info.value)


@pytest.mark.parametrize(
    ("runtime", "stdout_text"),
    [
        ("codex", ""),
        ("claude", '{"result": "ok"}'),
        ("claude", '{"modelUsage": {broken json'),
    ],
)
def test_observed_model_returns_none_when_model_cannot_be_observed(
    runtime: str, stdout_text: str
) -> None:
    """現状固定。公開出力からモデルを特定できない経路は None を返す。"""
    assert observed_model(runtime, stdout_text) is None


def test_observed_model_selects_model_with_most_input_tokens() -> None:
    """現状固定。複数モデルでは入力トークンが最大のモデル名を返す。"""
    stdout_text = """{
        "modelUsage": {
            "claude-sonnet": {"inputTokens": 120},
            "claude-opus": {"inputTokens": 450},
            "claude-haiku": {"inputTokens": 30}
        }
    }"""

    assert observed_model("claude", stdout_text) == "claude-opus"


@pytest.mark.parametrize(
    ("runtime", "model", "expected"),
    [
        (
            "kiro",
            None,
            "kiro の auto はラウンドごとに違うモデルが動きうる",
        ),
        (
            "kiro",
            "auto",
            "kiro の auto はラウンドごとに違うモデルが動きうる",
        ),
        (
            "codex",
            None,
            "codex はモデルを指定しておらず、実際に動いたモデルも取得できない",
        ),
        ("claude", None, None),
        ("kiro", "claude-sonnet", None),
        ("codex", "gpt-5", None),
    ],
)
def test_separation_reason_current_behavior(
    runtime: str, model: str | None, expected: str | None
) -> None:
    """現状固定。kiro の auto / 未指定かつ実測不可 / 分離しないの 3 分岐を記録する。"""
    assert separation_reason(runtime, model) == expected


@pytest.mark.parametrize(
    ("runtime", "model", "expected"),
    [
        ("claude", None, True),
        ("codex", "gpt-5", True),
        ("kiro", "auto", False),
        ("kiro", None, False),
        ("codex", None, False),
    ],
)
def test_is_measurable_current_behavior(
    runtime: str, model: str | None, expected: bool
) -> None:
    """現状固定。分離理由の有無に対応する計測可否を記録する。"""
    assert is_measurable(runtime, model) is expected


@pytest.mark.parametrize(
    ("requested", "observed"),
    [
        ("gpt-5", None),
        (None, "gpt-5"),
        (None, None),
        ("gpt-5", "gpt-5"),
    ],
)
def test_mismatch_warning_returns_none_when_no_conflict(
    requested: str | None, observed: str | None
) -> None:
    """現状固定。実測なし / 指定なし / 両方なし / 一致では None を返す。"""
    assert mismatch_warning("claude", requested, observed) is None


def test_mismatch_warning_reports_conflict() -> None:
    """現状固定。指定値と実測値が食い違うと警告文字列を返す。"""
    assert mismatch_warning("claude", "claude-opus", "claude-sonnet") == (
        "⚠ claude: 指定したモデル claude-opus と実際に動いたモデル claude-sonnet が"
        "食い違っています。比較には使えません"
    )
