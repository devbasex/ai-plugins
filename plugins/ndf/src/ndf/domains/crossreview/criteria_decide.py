"""指摘の基準と重要度（#3941。純粋な関数）。

- 基準の文は `contracts/data/review-criteria.json`、重要度の尺度と収束の妨げになるか（`blocking`）は
  `contracts/domains/severities.json` が持つ。相手役と修正担当への指示はここで組む
- 相手役は基準に当たる指摘だけを重要度つきで書き、critical・major には起きることの 1 文（`impact`）を添える。ジャーナルへ入れるときは
  1 文の無い critical・major を minor へ下げ、重要度の無い・知らない値の指摘は 1 文があれば major、無ければ minor にする
  （#4014。`effective_severity`）。分からない重要度（None）を
  収束の妨げになる側に倒すのは、ジャーナルへ入れる処理を通らない行（引き継いだ行・前のバージョンの行）を読むときである
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
    return int(facts.value(SEVERITIES, severity if severity is not None else _lightest_blocking(), "rank"))


def _lightest_blocking() -> str:
    """収束の妨げになる重さのうち最も軽いもの（major）。"""
    return str(min(facts.rows(SEVERITIES, blocking=True), key=lambda r: int(r["rank"]))["id"])


def _heaviest_light() -> str:
    """収束の妨げにならない重さのうち最も重いもの（minor）。"""
    return str(max(facts.rows(SEVERITIES, blocking=False), key=lambda r: int(r["rank"]))["id"])


def _has_impact(impact: object) -> bool:
    """起きることの 1 文を持つか（空白を除いて空でない文字列。`journal._text` と同じ規則）。"""
    return isinstance(impact, str) and bool(impact.strip())


def effective_severity(severity: Optional[str], impact: object) -> str:
    """ジャーナルの行に書く重要度（#4014）。`severity` は `severity_of` を通した値。1 文の無い収束の妨げになる重さと
    重要度の無い指摘は、妨げにならない重さのうち最も重いもの（minor）へ下げ、1 文のある重要度の無い指摘は妨げになる
    重さのうち最も軽いもの（major）にする。妨げにならない重さは 1 文の有無によらずそのまま。"""
    if severity is not None and not blocking(severity):
        return severity
    if not _has_impact(impact):
        return _heaviest_light()
    return severity if severity is not None else _lightest_blocking()


def downgraded(row: Mapping[str, Any]) -> bool:
    """ジャーナルへ入れるときに minor へ下げた行か（#4014）。`raised_severity` の無い行（引き継いだ行・前のバージョンの行）は偽。"""
    if "raised_severity" not in row:
        return False
    return blocking(severity_of(row["raised_severity"])) and row.get("severity") == _heaviest_light()


def heavier(known: Optional[str], raised: Optional[str]) -> Optional[str]:
    """同じ指摘が出し直されたときに残す重要度（重い方。同じ重さなら前の値）。"""
    return raised if _rank(raised) > _rank(known) else known


def heavier_raised(known: Optional[str], raised: Optional[str]) -> Optional[str]:
    """同じ指摘が出し直されたときに残す元の重要度（`raised_severity`）。収束の判定の `heavier` と違い、尺度にある値を
    無い値（None）より優先し、両方あれば重い方（同じ重さなら前の値）を残す。ジャーナルへ入れる順によらず同じ値になる（#4014）。"""
    if known is None or raised is None:
        return raised if known is None else known
    return heavier(known, raised)


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
    impact = t["impact"]["reviewer"].format(
        blocking="・".join(facts.ids(SEVERITIES, blocking=True)),
        light_first=_heaviest_light(),
        light="・".join(facts.ids(SEVERITIES, blocking=False)),
    )
    return [
        "## 指摘の基準",
        "次のどれかに当たるものだけを指摘として書く。見つけたものはこのラウンドですべて書き、次のラウンドへ残さない。",
        *criteria_lines(focus),
        f"書かないもの: {'・'.join(t['not_written'])}",
        f"対応のアクションの無いもの（{'・'.join(t['no_action'])}）は指摘にしない。",
        f"重要度 severity は {names} から選ぶ。{impact}",
    ]


def fixer_impact(row: Mapping[str, Any]) -> str:
    """修正担当へ渡す指摘の行の末尾に添える起きることの 1 文（行が `impact` を持たなければ空。#4014）。"""
    impact = row.get("impact")
    return f"— {_table()['impact']['fixer_label']}: {impact.strip()}" if _has_impact(impact) else ""


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
