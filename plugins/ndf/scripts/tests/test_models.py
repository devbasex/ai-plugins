"""モデル指定の公開入口に対する現状固定テスト。"""
from __future__ import annotations

import pathlib
import sys

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from models import ModelSpecError, parse_model_args


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
