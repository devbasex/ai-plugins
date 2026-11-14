"""MVV 判定の材料と指示文（純粋な関数。#1502・#2853・#3017）。

- 「テスト」の節は、分岐のための非 0（`on_fail` で次のステップへ進んだ終了コード）を失敗として載せず、後の
  再確認（`rechecks` で同じ検査を打ち直したステップ）の結果で置き換える（#1502）
- 要求文書の検査の終了コードと出力を「テスト」の節の材料に入れる（#2853 の材料の側）
- PR 本文から検査 6 の `MvvFacts.body` と `claimed`（出し直しの指摘への対応表が「記載した」と書く節）を埋める（#2853）
- 指示文は、設計者が決めた判断として読む規則をレッドラインに当てない。レッドラインの一覧を材料に添え、当たる
  操作を boundary に書かせる（機械の検査はパスしか見ないため、指示文と材料の側で直す。#3017）
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from ndf.progress.gate.checks import Changed, MvvFacts, sections

# ステップの結果の区分（「テスト」の節の列）
PASSED, FAILED, BRANCHED = "通った", "落ちた", "分岐した"
# 出し直しの指摘への対応表の節と、「記載した」と書く行から節の名前を取る形
RESPONSE_SECTION = "出し直しの指摘への対応"
_CLAIMED = re.compile(r"「([^」]+)」の節に(?:記載|追記|足)した")
# 出力を材料に載せる上限の行数（長い出力は末尾を残す）
OUTPUT_LINES = 20


@dataclass(frozen=True)
class StepRow:
    """「テスト」の節の 1 行。"""

    name: str
    exit: int
    status: str
    note: str
    output: str = ""


def _status(step: Mapping[str, Any]) -> tuple[str, str]:
    code = int(step["exit"])
    if code == 0:
        return PASSED, ""
    if "on_fail" in step and step["on_fail"]:
        return BRANCHED, f"終了コード {code} で {step['on_fail']} へ進んだ（失敗ではない）"
    return FAILED, ""


def _tail(text: Any) -> str:
    lines = str(text).strip().splitlines() if text else []
    return "\n".join(lines[-OUTPUT_LINES:])


def test_rows(steps: Sequence[Mapping[str, Any]]) -> list[StepRow]:
    """ステップの結果（`name`・`exit`・任意の `on_fail`・`rechecks`・`output`）から「テスト」の節の行を作る。

    後のステップが `rechecks` で同じ検査を打ち直していれば、前の行をその結果で置き換える（行の位置は前の行）。
    """
    rows: dict[str, StepRow] = {}
    for step in steps:
        status, note = _status(step)
        row = StepRow(str(step["name"]), int(step["exit"]), status, note, _tail(step.get("output")))
        target = str(step["rechecks"]) if "rechecks" in step and step["rechecks"] in rows else row.name
        if target != row.name:
            row = StepRow(row.name, row.exit, row.status, f"{target} を打ち直した結果", row.output)
        rows[target] = row
    return list(rows.values())


def test_section(rows: Iterable[StepRow]) -> str:
    """「テスト」の節の Markdown。終了コードと出力（末尾の行）を載せる。"""
    lines = ["## テスト", "", "| ステップ | 終了コード | 結果 | 補足 |", "| --- | ---: | --- | --- |"]
    outputs: list[str] = []
    for r in rows:
        note = r.note.replace("|", "\\|") if r.note else "—"
        lines.append(f"| {r.name} | {r.exit} | {r.status} | {note} |")
        if r.output:
            outputs += ["", f"### {r.name} の出力", "", "```text", r.output, "```"]
    return "\n".join(lines + outputs) + "\n"


def claimed_sections(body: Optional[str]) -> tuple[str, ...]:
    """出し直しの指摘への対応表が「記載した」と書く節の見出し（出た順に重ねずに）。"""
    if not body:
        return ()
    held = sections(body)
    table = held[RESPONSE_SECTION] if RESPONSE_SECTION in held else ""
    seen: dict[str, None] = {}
    for m in _CLAIMED.finditer(table):
        seen.setdefault(m.group(1), None)
    return tuple(seen)


def facts_from_pr(base: MvvFacts, body: Optional[str], changed: Iterable[Changed]) -> MvvFacts:
    """機械の検査の事実に、PR の本文・対応表が書く節・変えたファイルを埋める（検査 6 の呼び手）。"""
    return MvvFacts(
        status=base.status,
        current=base.current,
        approved=base.approved,
        referenced=base.referenced,
        mode=base.mode,
        changed=tuple(changed),
        boundary_paths=base.boundary_paths,
        body=body,
        claimed=claimed_sections(body),
    )


SYSTEM = """あなたは開発の承認ゲートの判定者である。利用者はプロジェクト MVV かスプリントの MVV（Mission / Vision / Value）を承認済みで、
MVV に従う変更なら承認ゲートでの個別の承認を省いてよいと決めている。材料を読み、この変更が NDF の共通原則・プロジェクト MVV・
スプリント MVV に従うかを判定する（優先順位は共通原則のとおり）。

- 共通原則に反せず、Mission と Vision に沿い、Value のどれにも反しないなら follow
- どれかに反するなら not_follow。材料から読み取れず決められないなら unknown（迷ったら unknown）
- デプロイの前に取れない実測は判定の対象にしない。それが無いことだけを理由に unknown にしない
- 設計者（AI）が判断を決め、決定の記録に結論と理由を書いているなら、人が見ていないことだけを理由に unknown にしない
- ただし前の行は verdict の決め方であり、boundary には当てない。材料の「レッドライン」の各行を 1 つずつ照らし、変更が当たる
  操作を含めば boundary にその番号と中身を書く。設計者が決めた判断・推奨・既定値でも、当たる操作は boundary に書く
  （設計文書が名指していなくても書く。boundary が空でなければ承認ゲートは省かれない）
- テストの節の「分岐した」は次のステップへ進むための終了コードで、失敗として読まない
- basis に根拠にした項目の番号（Mission / Vision / Value 3 / C4 / P1 など）を並べる

出力は次の JSON の 1 つだけ。前後に文を書かない。
{"verdict": "follow|not_follow|unknown", "reasons": ["MVV のどの項目に照らしてそう判断したか（1〜5 件）"], "boundary": [], "basis": ["Value 1"]}"""


def prompt(redlines: Mapping[str, str], materials: Mapping[str, str]) -> str:
    """判定の依頼の本文。レッドラインの一覧（番号 → 操作）を材料の先に置く。"""
    parts = ["## レッドライン（必ず人の承認が要る操作）", ""]
    parts += [f"- {k}: {v}" for k, v in redlines.items()]
    for title, text in materials.items():
        parts += ["", f"## {title}", "", text.strip()]
    return "\n".join(parts) + "\n"
