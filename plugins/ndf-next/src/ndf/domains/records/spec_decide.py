"""確定仕様化の判断（純粋な関数。N8a・#1715）。

設計を確定仕様へ溶かすとき、書き換えた確定仕様のファイルはすべてコミットに入れる（1 つ目だけを入れて残りを
落とさない）。消す設計を `pending_source` に持つ語は、その語を本文に含む確定仕様のファイルを SSOT にする。
含むファイルが 0 か 2 以上なら先頭へ倒さず止め、`--source-of <語>=<ファイル>` で明示させる。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


class SourceError(ValueError):
    """SSOT を 1 つに決められない語がある。`missing` は含むファイルが無い語、`ambiguous` は語 → 含むファイルの一覧。"""

    def __init__(self, missing: Sequence[str], ambiguous: Mapping[str, Sequence[str]]) -> None:
        self.missing = list(missing)
        self.ambiguous = {k: list(v) for k, v in ambiguous.items()}
        parts = []
        if self.missing:
            parts.append("どの確定仕様にも無い語: " + "、".join(self.missing))
        if self.ambiguous:
            parts.append("複数の確定仕様にある語: " + "、".join(f"{k}（{' / '.join(v)}）" for k, v in self.ambiguous.items()))
        super().__init__("。".join(parts) + "。--source-of <語>=<ファイル> で SSOT を明示する")


@dataclass(frozen=True)
class GlossaryPlan:
    """用語集の書き換え。`sources` は語（コンテキスト・語の組）→ 新しい SSOT のパス。"""

    sources: dict[tuple[str, str], str] = field(default_factory=dict)


def parse_overrides(values: Iterable[str]) -> dict[str, str]:
    """`<語>=<ファイル>` の並びを辞書にする。`=` が無い値は `ValueError`。"""
    out: dict[str, str] = {}
    for v in values:
        term, sep, path = v.partition("=")
        if not sep or not term or not path:
            raise ValueError(f"--source-of の形が違う（<語>=<ファイル>）: {v}")
        out[term] = path
    return out


def _owners(term: str, texts: Mapping[str, str]) -> list[str]:
    return [p for p, text in texts.items() if term in text]


def _assign(
    moving: Iterable[Mapping[str, Any]], texts: Mapping[str, str], overrides: Mapping[str, str]
) -> tuple[dict[tuple[str, str], str], list[str], dict[str, list[str]]]:
    """語ごとの SSOT の振り分け（1 つ → SSOT・複数 → ambiguous・無し → missing）。"""
    sources: dict[tuple[str, str], str] = {}
    missing: list[str] = []
    ambiguous: dict[str, list[str]] = {}
    for t in moving:
        term = t["term"]
        owners = [overrides[term]] if term in overrides else _owners(term, texts)
        if len(owners) == 1:
            sources[(t["context"], term)] = owners[0]
        elif owners:
            ambiguous[term] = owners
        else:
            missing.append(term)
    return sources, missing, ambiguous


def plan_glossary(g: Mapping[str, Any], removed: set[str], texts: Mapping[str, str], overrides: Mapping[str, str]) -> GlossaryPlan | None:
    """消す設計（`removed`）を `pending_source` に持つ語の SSOT を、本文に語を含む確定仕様のファイルへ移す。

    `texts` は渡した確定仕様のパス → 本文。移す語が無ければ None。決められない語があれば `SourceError`（書く前に判定する）。
    `overrides` の語は、本文に含むかを問わず指定のファイルにする（指定のファイルは渡した確定仕様のどれか）。
    """
    moving = [t for t in g["terms"] if "pending_source" in t and t["pending_source"] in removed]
    if not moving:
        return None
    unknown = sorted(p for p in overrides.values() if p not in texts)
    if unknown:
        raise ValueError("--source-of のファイルが渡した確定仕様に無い: " + "、".join(unknown))
    sources, missing, ambiguous = _assign(moving, texts, overrides)
    if missing or ambiguous:
        raise SourceError(missing, ambiguous)
    return GlossaryPlan(sources)


def apply_plan(g: Mapping[str, Any], plan: GlossaryPlan) -> dict[str, Any]:
    """計画どおりに語の `source` を書き換え、`pending_source` を外した用語集を返す（元は変えない）。"""
    terms = []
    for t in g["terms"]:
        key = (t["context"], t["term"])
        if key in plan.sources:
            t = {k: v for k, v in t.items() if k != "pending_source"} | {"source": plan.sources[key]}
        terms.append(t)
    return {**g, "terms": terms}


def commit_paths(specs: Sequence[str], glossary_files: Iterable[str], removed: Iterable[str]) -> list[str]:
    """コミットに入れるパス。渡した確定仕様はすべて、用語集の文書と SSOT、消した設計を入れる（重なりは 1 回）。"""
    seen: dict[str, None] = {}
    for p in [*specs, *glossary_files, *removed]:
        seen.setdefault(p, None)
    return list(seen)
