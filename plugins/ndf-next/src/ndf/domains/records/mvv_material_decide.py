"""MVV 判定の材料と指示文（純粋な関数。#1502・#2853・#3017）。

- 「テスト」のセクションは、分岐のための非 0（`on_fail` で次のステップへ進んだ終了コード）を失敗として載せず、後の
  再確認（`rechecks` で同じ検査を打ち直したステップ）の結果で置き換える（#1502）
- 要求文書の検査の終了コードと出力を「テスト」のセクションの材料に入れる（#2853 の材料の側）
- PR 本文から検査 6 の `MvvFacts.body` と `claimed`（出し直しの指摘への対応表が「記載した」と書くセクション）を埋める（#2853）
- 指示文は、設計者が決めた判断として読む規則をレッドラインに当てない。レッドラインの一覧を材料に添え、当たる
  操作を boundary に書かせる（機械の検査はパスしか見ないため、指示文と材料の側で直す。#3017）
- 承認ゲートの直前の判定（#3745）: レッドラインは共通原則とプロジェクト MVV の表から引く
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Optional

from ndf.progress.gate.checks import Changed, MvvFacts, sections

# ステップの結果の区分（「テスト」のセクションの列）
PASSED, FAILED, BRANCHED = "通った", "落ちた", "分岐した"
# 出し直しの指摘への対応表のセクションと、「記載した」と書く行からセクションの名前を取る形
RESPONSE_SECTION = "出し直しの指摘への対応"
_CLAIMED = re.compile(r"「([^」]+)」のセクションに(?:記載|追記|足)した")
# 出力を材料に載せる上限の行数（長い出力は末尾を残す）
OUTPUT_LINES = 20


@dataclass(frozen=True)
class StepRow:
    """「テスト」のセクションの 1 行。"""

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
    """ステップの結果（`name`・`exit`・任意の `on_fail`・`rechecks`・`output`）から「テスト」のセクションの行を作る。

    後のステップが `rechecks` で同じ検査を打ち直していれば、前の行をその結果で置き換える（行の位置は前の行）。
    """
    rows: dict[str, StepRow] = {}
    for step in steps:
        status, note = _status(step)
        row = StepRow(str(step["name"]), int(step["exit"]), status, note, _tail(step.get("output")))
        target = str(step["rechecks"]) if "rechecks" in step and step["rechecks"] in rows else row.name
        if target != row.name:
            row = replace(row, note=f"{target} を打ち直した結果")
        rows[target] = row
    return list(rows.values())


def test_section(rows: Iterable[StepRow]) -> str:
    """「テスト」のセクションの Markdown。終了コードと出力（末尾の行）を載せる。"""
    lines = ["## テスト", "", "| ステップ | 終了コード | 結果 | 補足 |", "| --- | ---: | --- | --- |"]
    outputs: list[str] = []
    for r in rows:
        note = r.note.replace("|", "\\|") if r.note else "—"
        lines.append(f"| {r.name} | {r.exit} | {r.status} | {note} |")
        if r.output:
            outputs += ["", f"### {r.name} の出力", "", "```text", r.output, "```"]
    return "\n".join(lines + outputs) + "\n"


def claimed_sections(body: Optional[str]) -> tuple[str, ...]:
    """出し直しの指摘への対応表が「記載した」と書くセクションの見出し（出た順に重ねずに）。"""
    if not body:
        return ()
    held = sections(body)
    table = held[RESPONSE_SECTION] if RESPONSE_SECTION in held else ""
    seen: dict[str, None] = {}
    for m in _CLAIMED.finditer(table):
        seen.setdefault(m.group(1), None)
    return tuple(seen)


def facts_from_pr(base: MvvFacts, body: Optional[str], changed: Iterable[Changed]) -> MvvFacts:
    """機械の検査の事実に、PR の本文・対応表が書くセクション・変えたファイルを埋める（検査 6 の呼び手）。"""
    return replace(base, changed=tuple(changed), body=body, claimed=claimed_sections(body))


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
- テストのセクションの「分岐した」は次のステップへ進むための終了コードで、失敗として読まない
- basis に根拠にした項目の番号（Mission / Vision / Value 3 / C4 / P1 など）を並べる

出力は次の JSON の 1 つだけ。前後に文を書かない。
{"verdict": "follow|not_follow|unknown", "reasons": ["MVV のどの項目に照らしてそう判断したか（1〜5 件）"], "boundary": [], "basis": ["Value 1"]}"""


def prompt(redlines: Mapping[str, str], materials: Mapping[str, str]) -> str:
    """判定のリクエストの本文。レッドラインの一覧（番号 → 操作）を材料の先に置く。"""
    parts = ["## レッドライン（必ず人の承認が要る操作）", ""]
    parts += [f"- {k}: {v}" for k, v in redlines.items()]
    for title, text in materials.items():
        parts += ["", f"## {title}", "", text.strip()]
    return "\n".join(parts) + "\n"


# --- 承認ゲートの直前の判定（#3745） -----------------------------------------------------------------

# レッドラインの表の行（`| C1 | 操作 | 理由 |`・`| P1 | 操作 | 理由 |`）
_REDLINE_ROW = re.compile(r"^\|[ \t]*([A-Z][0-9]+)[ \t]*\|[ \t]*([^|\n]*?)[ \t]*\|", re.MULTILINE)
# 差分を材料に載せる上限の行数（ファイルごと。長い差分は先頭を残す）
PATCH_LINES = 200


def redlines(*texts: str) -> dict[str, str]:
    """共通原則とプロジェクト MVV の「必ず人の承認が要る操作」の表の行（番号 → 操作）。先に出た番号を残す。"""
    out: dict[str, str] = {}
    for text in texts:
        for no, op in _REDLINE_ROW.findall(text):
            out.setdefault(no, op)
    return out


def patch_material(files: Sequence[Mapping[str, Any]]) -> str:
    """PR の変えたファイルと差分（GitHub の files の行。`filename`・`status`・任意の `patch`）を材料の本文にする。"""
    parts = []
    for f in files:
        lines = str(f["patch"]).splitlines() if f.get("patch") else []
        cut = "\n（以下略）" if len(lines) > PATCH_LINES else ""
        head = f"### {f['filename']}（{f['status']}）"
        parts.append(f"{head}\n\n```diff\n" + "\n".join(lines[:PATCH_LINES]) + f"\n```{cut}" if lines else head)
    return "\n\n".join(parts)


def _cut(f: Mapping[str, Any]) -> bool:
    """ファイル `f` の差分の一部か全部が材料に載らないか。"""
    if not f.get("patch"):
        return f.get("changes") != 0
    return len(str(f["patch"]).splitlines()) > PATCH_LINES


def incomplete_patches(files: Sequence[Mapping[str, Any]]) -> list[str]:
    """差分の全体が材料に載らないファイル（中身を変えたのに `patch` が無い・`PATCH_LINES` を超えて省く）の理由。空なら全体が載る。

    中身を変えない改名（GitHub の `changes` が 0）は `patch` を持たないが、省いた部分が無いので数えない。
    """
    held = [str(f["filename"]) for f in files if _cut(f)]
    return [f"差分の全体を材料に載せられない: {', '.join(held)}"] if held else []


def gate_outcome(gate: str, verdict: Any, skip: bool, why: Sequence[str], head: Optional[str] = None) -> dict[str, Any]:
    """判定のステップの出力（文脈のキー `mvv_gate` の値）。`head` は判定した PR の head（PR を読む前に止めたら無い）。"""
    out = {"gate": gate, "skip": skip, "verdict": verdict if isinstance(verdict, dict) else None, "why": list(why)}
    return {**out, "head": head} if head is not None else out
