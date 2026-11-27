"""工程の入力と、プランのテンプレートの差し込みを決められないときの例外（`ndf.progress.invoke` から分けた）。

上げるのも結果 JSON へ分けるのも `invoke` で、呼び手は `invoke` の名前で受ける。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Optional

from ndf.core import route


class StageInputError(Exception):
    """工程の入力を文脈から渡せない。`kind` は停止理由の語、`items` は結果 JSON の項目。"""

    kind = "invalid"

    @property
    def items(self) -> list[dict[str, str]]:
        return []


class StageInputMissing(StageInputError):
    """工程の入力のキーがスプリントの文脈に無い。`keys` は足りないキー。"""

    kind = "input_missing"

    def __init__(self, keys: Sequence[str]) -> None:
        super().__init__(f"工程の入力のキーが文脈に無い: {', '.join(keys)}")
        self.keys = tuple(keys)

    @property
    def items(self) -> list[dict[str, str]]:
        return [{"key": k} for k in self.keys]


class StageInputInvalid(StageInputError):
    """工程の入力の値が文脈のスキーマの形に合わない。`errors` は誤りの一覧。"""

    kind = "invalid"

    def __init__(self, errors: Sequence[str]) -> None:
        super().__init__(f"工程の入力の形が合わない: {'; '.join(errors)}")
        self.errors = tuple(errors)

    @property
    def items(self) -> list[dict[str, str]]:
        return [{"error": e} for e in self.errors]


class TemplateValuesMissing(route.RouteError):
    """テンプレートの差し込みの値がどの出所からも決まらない。`missing` は足りない差し込みの名前 → 出所、`details` は名前 →
    行へ足すキー（#4337 の決定 7。`rebuild` の既定を決められない理由・設定のキー・値の形）。"""

    def __init__(self, template_name: str, missing: Mapping[str, str], details: Optional[Mapping[str, Mapping[str, str]]] = None) -> None:
        listed = ", ".join(f"{n}（{s}）" for n, s in missing.items())
        super().__init__(f"テンプレート {template_name} の差し込みの値が無い: {listed}")
        self.missing = dict(missing)
        given = details if details is not None else {}
        self.details = {n: dict(given[n]) for n in self.missing if n in given}

    @property
    def items(self) -> list[dict[str, str]]:
        return [
            {"key": n, "source": s, **self.details[n]} if n in self.details else {"key": n, "source": s} for n, s in self.missing.items()
        ]


class KindInputsMissing(route.RouteError):
    """プランの作業（`{{worker}}` の後ろの名前）が読む入力か作業場所が決まらない。`missing` は名前 → 出所。"""

    def __init__(self, template_name: str, missing: Mapping[str, str]) -> None:
        listed = ", ".join(f"{n}（{s}）" for n, s in missing.items())
        super().__init__(f"テンプレート {template_name} の作業の入力が無い: {listed}")
        self.missing = dict(missing)

    @property
    def items(self) -> list[dict[str, str]]:
        return [{"key": n, "source": s} for n, s in self.missing.items()]
