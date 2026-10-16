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
def test_parse_model_args_rejects_invalid_specs(values: list[str], message_part: str) -> None:
    """現状固定。不正指定は初期化時に原因を示す例外として返る。"""
    with pytest.raises(ModelSpecError) as exc_info:
        parse_model_args(values)

    assert message_part in str(exc_info.value)


def _claude_launch(tmp_path: pathlib.Path, stdout_text: str) -> pathlib.Path:
    stem = tmp_path / "claude-implement-rf1"
    pathlib.Path(f"{stem}-launch.json").write_text(
        '{"runtime": "claude", "workdir": "/w", "started_at": "2026-10-02T00:00:00Z"}', encoding="utf-8"
    )
    pathlib.Path(f"{stem}-stdout.log").write_text(stdout_text, encoding="utf-8")
    return stem


@pytest.mark.parametrize(
    ("stdout_text", "reason"),
    [
        ("", "no_model_field"),
        ('{"result": "ok"}', "no_model_field"),
        ('{"modelUsage": {broken json', "unreadable"),
    ],
)
def test_observed_model_returns_a_reason_when_model_cannot_be_observed(tmp_path: pathlib.Path, stdout_text: str, reason: str) -> None:
    """出力からモデルを特定できない経路は、実測値を持たず理由を返す。"""
    observation = observed_model("claude", _claude_launch(tmp_path, stdout_text))
    assert (observation.model, observation.reason) == (None, reason)


def test_observed_model_selects_model_with_most_tokens(tmp_path: pathlib.Path) -> None:
    """複数モデルでは 4 種のトークンの和が最大のモデル名を返す。"""
    stdout_text = """{
        "modelUsage": {
            "claude-sonnet": {"inputTokens": 120},
            "claude-opus": {"inputTokens": 10, "cacheReadInputTokens": 900},
            "claude-haiku": {"inputTokens": 450}
        }
    }"""

    assert observed_model("claude", _claude_launch(tmp_path, stdout_text)).model == "claude-opus"


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
        ("claude", None, "claude はモデルを指定しておらず、実際に動いたモデルも取得できない"),
        ("kiro", "claude-sonnet", None),
        ("codex", "gpt-5", None),
    ],
)
def test_separation_reason_current_behavior(runtime: str, model: str | None, expected: str | None) -> None:
    """実測値が無いとき、kiro の auto / 未指定 / 分離しないの 3 分岐になる。"""
    assert separation_reason(runtime, model) == expected


@pytest.mark.parametrize(
    ("runtime", "model", "expected"),
    [
        ("claude", None, False),
        ("codex", "gpt-5", True),
        ("kiro", "auto", False),
        ("kiro", None, False),
        ("codex", None, False),
    ],
)
def test_is_measurable_current_behavior(runtime: str, model: str | None, expected: bool) -> None:
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
def test_mismatch_warning_returns_none_when_no_conflict(requested: str | None, observed: str | None) -> None:
    """現状固定。実測なし / 指定なし / 両方なし / 一致では None を返す。"""
    assert mismatch_warning("claude", requested, observed) is None


def test_mismatch_warning_reports_conflict() -> None:
    """現状固定。指定値と実測値が食い違うと警告文字列を返す。"""
    assert mismatch_warning("claude", "claude-opus", "claude-sonnet") == (
        "⚠ claude: 指定したモデル claude-opus と実際に動いたモデル claude-sonnet が食い違っています。比較には使えません"
    )
