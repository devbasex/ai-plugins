"""JSON と設定の形の検証・型の変換の包み（#1142 の決定 19・種類 5）。pydantic を呼ぶのはこのモジュールだけである。

形は pydantic のモデル（`Shape` を継ぎ、知らない項目を拒む）で書き、`load_shape()` で検証する。pydantic の
`ValidationError` は、どこの何が誤りかを日本語の 1 行にした `ShapeError` へ直す。

    class Step(schema.Shape):
        id: str
        on_fail: str | None = None

    step = schema.load_shape(Step, data, where="steps[0]")
    # 誤り: ShapeError("steps[0].id: 必須の項目が無い")

寛容な読み（語彙に無い値を既定の値へ下げる）は、型に `lenient_choice()` を付けて書く。

    severity: schema.lenient_choice(("high", "medium", "low"), "low") = "low"

使う側は `deps.require("schema")` を先に呼ぶ。
"""
from __future__ import annotations

from typing import Annotated, Any, Iterable, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, ValidationError

M = TypeVar("M", bound=BaseModel)

# pydantic の誤りの種類 → 日本語の理由。`{}` には期待した値が入る
_REASONS = {
    "missing": "必須の項目が無い",
    "extra_forbidden": "知らない項目",
    "string_type": "文字列にする",
    "int_type": "整数にする",
    "int_parsing": "整数にする",
    "int_from_float": "整数にする",
    "float_type": "数にする",
    "float_parsing": "数にする",
    "bool_type": "真偽値にする",
    "bool_parsing": "真偽値にする",
    "list_type": "配列にする",
    "dict_type": "オブジェクトにする",
    "model_type": "オブジェクトにする",
    "literal_error": "次のどれかにする: {expected}",
    "enum": "次のどれかにする: {expected}",
    "greater_than": "{gt} より大きくする",
    "greater_than_equal": "{ge} 以上にする",
    "less_than": "{lt} より小さくする",
    "less_than_equal": "{le} 以下にする",
    "string_too_short": "{min_length} 文字以上にする",
    "too_short": "{min_length} 個以上にする",
    "too_long": "{max_length} 個以下にする",
    "string_pattern_mismatch": "形が合わない（{pattern}）",
    "value_error": "{error}",
}


class ShapeError(ValueError):
    """形の誤り。`str()` は日本語の 1 行、`problems` は `(場所, 理由)` の並び。"""

    def __init__(self, message: str, problems: list[tuple[str, str]]):
        super().__init__(message)
        self.problems = problems


class Shape(BaseModel):
    """知らない項目を拒み、代入でも検証するモデルの基底。"""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _where(where: str, loc: Iterable[Any]) -> str:
    out = where
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out or "（全体）"


def _shape_reason(err: dict) -> str:
    template = _REASONS.get(err.get("type", ""))
    if template is None:
        return str(err.get("msg", "形が合わない"))
    ctx = dict(err.get("ctx") or {})
    ctx["expected"] = str(ctx.get("expected", "")).replace(" or ", " / ")
    ctx["error"] = str(ctx.get("error", err.get("msg", ""))).removeprefix("Value error, ")
    try:
        return template.format(**ctx)
    except (KeyError, IndexError):
        return str(err.get("msg", "形が合わない"))


def shape_message(exc: ValidationError, where: str = "") -> ShapeError:
    """pydantic の誤りを、`<場所>: <理由>` を `／` でつないだ 1 行の `ShapeError` にする。"""
    problems = []
    for err in exc.errors(include_url=False):
        loc = _where(where, err.get("loc", ()))
        reason = _shape_reason(err)
        if err.get("type") not in ("missing", "extra_forbidden") and "input" in err:
            reason += f"（値: {err['input']!r}）"
        problems.append((loc, reason))
    return ShapeError("／".join(f"{loc}: {reason}" for loc, reason in problems), problems)


def load_shape(model: type[M], data: Any, where: str = "") -> M:
    """`data` を `model` で検証して返す。誤りは `ShapeError`（日本語の 1 行）。"""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise shape_message(exc, where) from None


def dump_shape(obj: BaseModel, *, drop_none: bool = False) -> dict:
    """モデルを JSON にできる辞書へ戻す（`drop_none` で None の項目を落とす）。"""
    return obj.model_dump(mode="json", exclude_none=drop_none)


def lenient_choice(allowed: Iterable[str], fallback: str) -> Any:
    """語彙に無い値（文字列でない値を含む）を `fallback` へ下げる文字列の型。前後の空白と大文字は揃える。"""
    vocab = tuple(allowed)
    if fallback not in vocab:
        raise ValueError(f"下げる先 {fallback!r} が語彙 {vocab} に無い")

    def pick(v: Any) -> str:
        s = v.strip().lower() if isinstance(v, str) else None
        return s if s in vocab else fallback

    return Annotated[str, BeforeValidator(pick)]
