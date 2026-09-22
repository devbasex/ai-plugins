"""モデル指定の公開入口に対する現状固定テスト。"""
from __future__ import annotations

import pathlib
import sys

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from models import ModelSpecError, observed_model, parse_model_args


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
