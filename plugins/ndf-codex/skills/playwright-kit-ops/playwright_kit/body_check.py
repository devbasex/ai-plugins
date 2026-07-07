"""ページ本文の文字列マッチ検出 (PHP / SSR エラー検出, v0.4.0)。

JavaScript ランタイム由来の console.error / pageerror では拾えない、
サーバ側で HTML 本文に直接出力された "Fatal error" / "Warning:" 等の
エラー文字列を、Playwright の ``page.on("response", ...)`` で拾った
HTML 本文に対して substring match で検出する純粋関数群。

旧 v0.2.x の自前 YAML runner にあった ``body_check`` 機能の再実装で、
PHP プロジェクトのフロント漏れ ``Fatal error`` / ``STRICT:`` 等を
テスト失敗として捕捉する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class BodyViolation:
    """1 件の body_check ヒット。"""

    url: str
    category: str  # "fatal" / "warning" / "not_found"
    pattern: str
    snippet: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "category": self.category,
            "pattern": self.pattern,
            "snippet": self.snippet,
        }


def _snippet(body: str, pattern: str, *, around: int = 60) -> str:
    """body 中の pattern 周辺 ``around`` 文字を取り出して表示用に整形する。"""
    idx = body.find(pattern)
    if idx < 0:
        return pattern
    start = max(0, idx - around)
    end = min(len(body), idx + len(pattern) + around)
    snippet = body[start:end].replace("\n", " ").replace("\r", " ").replace("\t", " ")
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(body) else ""
    return prefix + snippet + suffix


def scan_body(
    body: str,
    url: str,
    *,
    fatal_patterns: Iterable[str] = (),
    warning_patterns: Iterable[str] = (),
    warning_head_chars: int = 300,
    not_found_patterns: Iterable[str] = (),
) -> list[BodyViolation]:
    """``body`` に該当パターンが出現すれば ``BodyViolation`` のリストで返す。

    - ``fatal_patterns`` / ``not_found_patterns`` は body 全体を substring で走査
    - ``warning_patterns`` は ``body[:warning_head_chars]`` (= 先頭 N 文字 /
      code points) のみを走査。本文中の説明文や入力例の "Notice:" 等を許容する
      ための head 検索。bytes ではなく code points で切るのは、日本語ページで
      300 bytes ≒ 100 字相当となり実用にならないため。

    パターン文字列は **substring 比較** (正規表現ではない)。空文字列は無視する。
    """
    violations: list[BodyViolation] = []

    if not body:
        return violations

    head_size = max(0, int(warning_head_chars))
    head = body[:head_size] if head_size else ""

    for pat in fatal_patterns:
        if pat and pat in body:
            violations.append(
                BodyViolation(
                    url=url,
                    category="fatal",
                    pattern=pat,
                    snippet=_snippet(body, pat),
                )
            )

    if head:
        for pat in warning_patterns:
            if pat and pat in head:
                violations.append(
                    BodyViolation(
                        url=url,
                        category="warning",
                        pattern=pat,
                        snippet=_snippet(head, pat),
                    )
                )

    for pat in not_found_patterns:
        if pat and pat in body:
            violations.append(
                BodyViolation(
                    url=url,
                    category="not_found",
                    pattern=pat,
                    snippet=_snippet(body, pat),
                )
            )

    return violations


def is_html_response(content_type: str | None) -> bool:
    """``Content-Type`` ヘッダから HTML レスポンスか判定する。"""
    if not content_type:
        return False
    return "html" in content_type.lower()
