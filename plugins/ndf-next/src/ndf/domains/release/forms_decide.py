"""設定のリリースの形を、リリースの形の表（`contracts/domains/release-forms.json`）から引く（純粋な関数。#3615 の決定 1）。

扱わない形（表の `supported` が偽）では `release-steps` の `materials`・`assemble`・`publish` が何もせずに止まり、形の名前と
表の在りかと 11.x へ回す理由を出す（I1）。形ごとに違うのは承認資料の届き方の 1 行だけで、バージョンの読み書きは保存先の
アダプター（`version_store`）が持つ。
"""

from __future__ import annotations

from typing import NamedTuple

from ndf.contracts import facts

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
    why: str


def _where() -> str:
    return f"plugins/ndf-next/contracts/{facts.location(TABLE)}"


def form_of(name: str) -> Form:
    """設定の形の名前の行。表に無い形・扱わない形なら `FormUnsupported`。"""
    known = facts.ids(TABLE)
    if name not in known:
        raise FormUnsupported(f"リリースの形 {name!r} はリリースの形の表（{_where()}）に無い。取れる形: {', '.join(known)}")
    row = facts.row(TABLE, name)
    form = Form(row["id"], row["label"], row["reach"], row["supported"], row.get("why", ""))
    if not form.supported:
        raise FormUnsupported(f"リリースの形 {name}（{form.label}）は release-steps が扱わない（{_where()}）: {form.why}")
    return form


def reach_line(form: Form) -> str:
    """承認資料の届き方の 1 行。"""
    return f"- 届き方: {form.label}: {form.reach}"
