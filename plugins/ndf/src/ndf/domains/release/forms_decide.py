"""設定のリリースの形を、リリースの形の表（`contracts/domains/release-forms.json`）から引く（純粋な関数。#3615 の決定 1）。

扱わない形（表の `supported` が偽）では `release-steps` の `materials`・`assemble`・`publish` が何もせずに止まり、形の名前と
表の在りかと 11.x へ回す理由を出す（I1）。形ごとに違うのは承認資料の届き方の 1 行だけで、バージョンの読み書きは保存先の
アダプター（`version_store`）が持つ。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple, Optional

from ndf.contracts import facts
from ndf.core import production_merge
from ndf.domains.release import version_decide

# リリースの形の表
TABLE = "release-forms"


class FormUnsupported(Exception):
    """設定のリリースの形を扱わない（表で扱わないとした形か、表に無い形）。"""


class Form(NamedTuple):
    """リリースの形の表の 1 行。"""

    id: str
    label: str
    reach: str
    supported: bool
    why: Optional[str]
    # 承認の質問で問う操作（#4446。扱う形の行だけが持つ）
    approve: Optional[str] = None


def _where() -> str:
    """表の在りか（`contracts/` からの相対）。"""
    return f"contracts/{facts.location(TABLE)}"


def form_of(name: str) -> Form:
    """設定の形の名前の行。表に無い形・扱わない形なら `FormUnsupported`。"""
    known = facts.ids(TABLE)
    if name not in known:
        raise FormUnsupported(f"リリースの形 {name!r} はリリースの形の表（{_where()}）に無い。取れる形: {', '.join(known)}")
    row = facts.row(TABLE, name)
    form = Form(row["id"], row["label"], row["reach"], row["supported"], facts.column(TABLE, "why").get(name), row.get("approve"))
    if not form.supported:
        raise FormUnsupported(f"リリースの形 {name}（{form.label}）は release-steps が扱わない（{_where()}）: {form.why}")
    return form


def reach_line(form: Form) -> str:
    """承認資料の届き方の 1 行。"""
    return f"- 届き方: {form.label}: {form.reach}"


class Delivery(NamedTuple):
    """設定の届け先（`release.deliveries`）の 1 行のうち、承認資料に載せるもの。"""

    target: str
    kind: str
    trigger: str


def reaches(row: Mapping[str, Any], channel: str) -> bool:
    """届け先の行が承認資料のチャネル（`production`・`dev`）に当たるか（`channel` が `null`・省略の行はどれにも当たる）。"""
    # 開発版はベースブランチから届く（届け先の行の channel の語は base）。選び方の規則は production_merge.on_channel にある
    return production_merge.on_channel(row, "base" if channel == version_decide.DEV else channel)


def deliveries_of(rows: Sequence[Mapping[str, Any]], channel: str) -> tuple[Delivery, ...]:
    """設定の届け先の行のうち、承認資料のチャネル（`production`・`dev`）に当たる行（`channel` が `null` の行を含む）。"""
    return tuple(Delivery(r["target"], r["kind"], r["trigger"]) for r in rows if reaches(r, channel))


def delivery_line(d: Delivery) -> str:
    """承認資料の届き方の、届け先 1 行（`manual` は人が手で届ける）。"""
    how = "手動" if d.kind == "manual" else "自動"
    return f"- {d.target}（{how}）: {d.trigger}"
