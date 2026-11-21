"""指摘の基準と重要度（#3941。純粋な関数）。

- 基準の文は `contracts/data/review-criteria.json`、重要度の尺度と収束の妨げになるか（`blocking`）は
  `contracts/domains/severities.json` が持つ。相手役と修正担当への指示はここで組む
- 相手役は基準に当たる指摘だけを重要度つきで書く。重要度の無い・知らない値の指摘は収束の妨げになる側に倒す
- 重点（基準 3）は `.ndf/review.json` の `version: 1` の `focus` だけを読む。`version: 2`・`3` のプロファイルは
  読まず、形が違えば `ReviewDeclInvalid`（黙って無視しない）
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Optional

from ndf import contracts
from ndf.contracts import facts

TABLE = "data/review-criteria.json"
SEVERITIES = "severities"
# 読む設定のバージョン
DECL_VERSION = 1


class ReviewDeclInvalid(ValueError):
    """`.ndf/review.json` の形が違うか、cross-review が読まないバージョンである。"""


@contracts.cached_reader
def _table() -> dict[str, Any]:
    return dict(contracts.load_json(TABLE))


def severity_of(value: Any) -> Optional[str]:
    """尺度にある重要度ならそのまま、ほか（無い・知らない値）は None。"""
    return value if isinstance(value, str) and value in facts.ids(SEVERITIES) else None


def blocking(severity: Optional[str]) -> bool:
    """収束の妨げになる重要度か。分からない重要度（None）は妨げになる側に倒す。"""
    return severity is None or bool(facts.value(SEVERITIES, severity, "blocking"))


def _rank(severity: Optional[str]) -> int:
    """重さの順位。分からない重要度は収束の妨げになる重さのうち最も軽いものと同じに数える。"""
    if severity is not None:
        return int(facts.value(SEVERITIES, severity, "rank"))
    return min(int(r["rank"]) for r in facts.rows(SEVERITIES, blocking=True))


def heavier(known: Optional[str], raised: Optional[str]) -> Optional[str]:
    """同じ指摘が出し直されたときに残す重要度（重い方。同じ重さなら前の値）。"""
    return raised if _rank(raised) > _rank(known) else known


def focus_of(raw: Any, where: str) -> tuple[str, ...]:
    """`.ndf/review.json` の中身から重点の並び。`version: 1` だけを読み、ほかのバージョンと形の違いは `ReviewDeclInvalid`。"""
    version = raw.get("version") if isinstance(raw, Mapping) else None
    if isinstance(version, bool) or version != DECL_VERSION:
        raise ReviewDeclInvalid(
            f"{where} の version が {version!r}。cross-review は version 1 の focus だけを読む"
            "（profile・profiles のプロファイルは読まない。version 1 の focus に書き直す）"
        )
    focus = raw.get("focus")
    if not isinstance(focus, list) or not all(isinstance(x, str) and x.strip() for x in focus):
        raise ReviewDeclInvalid(f'{where} の focus の形が違う（{{"version": 1, "focus": ["<重点>", ...]}} を書く）')
    return tuple(x.strip() for x in focus)


def criteria_lines(focus: Sequence[str]) -> list[str]:
    """基準の行（番号の順）。重点があるときだけ基準 3 を足す。"""
    t = _table()
    items = dict(t["criteria"])
    if focus:
        items[t["focus_criterion"]] = f"{t['focus_label']}: {' / '.join(focus)}"
    return [f"{n}. {items[n]}" for n in sorted(items, key=int)]


def reviewer_block(focus: Sequence[str]) -> list[str]:
    """相手役への「指摘の基準」のセクション。"""
    t = _table()
    names = "・".join(facts.ids(SEVERITIES))
    blocking_names = "・".join(facts.ids(SEVERITIES, blocking=True))
    light = "・".join(facts.ids(SEVERITIES, blocking=False))
    return [
        "## 指摘の基準",
        "次のどれかに当たるものだけを指摘として書く。見つけたものはこのラウンドですべて書き、次のラウンドへ残さない。",
        *criteria_lines(focus),
        f"書かないもの: {'・'.join(t['not_written'])}",
        f"対応のアクションの無いもの（{'・'.join(t['no_action'])}）は指摘にしない。",
        f"重要度 severity は {names} から選ぶ。基準に当たる指摘は {blocking_names}。{light} は収束を止めず、修正担当へ渡らない。",
    ]


def fixer_block(focus: Sequence[str]) -> list[str]:
    """修正担当への「指摘の基準」のセクション。"""
    t = _table()
    return [
        "## 指摘の基準",
        "指摘ごとに次の基準のどれかに当たるかを確かめ直す。当たれば直す（fixed）。当たらなければ対象のファイルを変えず、"
        "どの基準にも当たらない理由を添えて見送る（deferred）。",
        *criteria_lines(focus),
        f"当たらないもの: {'・'.join(t['not_written'])}",
        t["out_of_scope"],
    ]
