#!/usr/bin/env python3
"""review_criteria.py: 指摘の基準の正本と、レビューの重点の宣言（`.ndf/review.json`）の読み取り（#1287）。

レビュー担当への指示（cross-review の `launch-reviewer.sh`）・修正担当の文脈のファイル（`fix-steps.py context`）・
見送りの返信（`fix-steps.py finalize`）は、どれもここの定数から組む。標準ライブラリだけで書く。

    review_criteria.py reviewer [--root <worktree>]   レビュー担当への節を標準出力へ
    review_criteria.py fixer    [--root <worktree>]   修正担当への節を標準出力へ

`--root` を省くと宣言を読まない既定の節（基準 3 の無い形）を出す。宣言が読めないときは理由を標準エラーへ出し、
終了コードは 0 のまま基準 1・2・4 の節を出す。

宣言の形（ほかの鍵は無視する）:

    {"version": 1, "focus": ["<重点の名前>", ...]}
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NamedTuple

DECL_PATH = (".ndf", "review.json")

# 基準の番号は、重点の宣言が無くても詰めない（設計の決定 4）。3 は宣言があるときだけ現れる
CRITERIA = {
    1: "利用者が普通に使う経路で、誤動作する・止まる・データを壊す",
    2: "秘密・認証認可・利用者のデータ・戻せない操作に触れる（起きる確率によらず書く）",
    4: "設計の文書で、実装する人が違うものを作ってしまう食い違い",
}
FOCUS_LABEL = "このプロジェクトのレビューの重点に当たる"
FOCUS_CRITERION = 3
CRITERION_NUMBERS = tuple(sorted({*CRITERIA, FOCUS_CRITERION}))

# 見送りの種類（値 → 返信の括弧に入る名前）。「書かないもの」と 1 対 1（設計の決定 6）
WAIVE_KINDS = {
    "wording": "字句や言い回しの修正",
    "unlikely": "まず起きない条件での異常処理",
    "doc_mismatch": "実装に影響しない文書の食い違い",
    "alignment": "番号や表記の揃え",
    "preference": "好みの設計",
}
NOT_WRITTEN = "・".join(("字句や言い回し", "まず起きない条件での異常処理", "実装に影響しない文書どうしの食い違い",
                         "番号や表記の揃え", "好みの設計"))
REPLY = "直しません。この指摘は、利用者が実際に使って困る不具合ではないためです（{inner}）。使って困る場面が出たら、そのときに直します。"
REPLY_FOCUS = "。レビューの重点（{names}）にも当たりません"


class Focus(NamedTuple):
    """重点の宣言の読み取り結果。`none` と `unreadable` では `names` が空。"""
    status: str  # declared | none | unreadable
    names: tuple[str, ...] = ()
    error: str | None = None


NO_FOCUS = Focus("none")


def load_focus(root) -> Focus:
    """`<root>/.ndf/review.json` を読む。例外を上げない。"""
    path = Path(root).joinpath(*DECL_PATH)
    try:
        if not path.exists():
            return NO_FOCUS
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return Focus("unreadable", (), f"{path} を読めない: {e}")
    focus = data.get("focus") if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(focus, list) \
            or not all(isinstance(x, str) and x.strip() for x in focus):
        return Focus("unreadable", (), f"{path} の形が違う（{{\"version\": 1, \"focus\": [\"...\"]}} を書く）")
    if not focus:
        return NO_FOCUS
    return Focus("declared", tuple(x.strip() for x in focus))


def focus_from(status, names, error=None) -> Focus:
    """状態ファイル・振り分けの JSON に写した値から `Focus` を組み直す（形が違えば宣言なし）。"""
    names = tuple(x for x in (names or []) if isinstance(x, str) and x.strip())
    if status == "declared" and names:
        return Focus("declared", names)
    if status == "unreadable":
        return Focus("unreadable", (), error if isinstance(error, str) else None)
    return NO_FOCUS


def _criteria_lines(focus: Focus) -> list[str]:
    lines = [f"1. {CRITERIA[1]}", f"2. {CRITERIA[2]}"]
    if focus.status == "declared" and focus.names:
        lines.append(f"{FOCUS_CRITERION}. {FOCUS_LABEL}: {' / '.join(focus.names)}")
    lines.append(f"4. {CRITERIA[4]}")
    return lines


def reviewer_block(focus: Focus = NO_FOCUS) -> str:
    """レビュー担当への「指摘の基準」の節。宣言があるときだけ基準 3 の行を持つ。"""
    return "\n".join([
        "## 指摘の基準",
        "次のどれかに当たるものだけを、重要度 `critical` か `major` で書く（`minor` / `nit` は使わない）。"
        "上のレビュー観点は探す場所で、書く基準はここである。"
        "見つけたものはこのラウンドですべて書く。",
        *_criteria_lines(focus),
        f"書かないもの: {NOT_WRITTEN}",
    ])


def fixer_block(focus: Focus = NO_FOCUS) -> str:
    """修正担当への「指摘の基準」の節。基準・書かないもの・見送りの種類・返信の雛形。"""
    kinds = [f"- `{k}`: {v}" for k, v in WAIVE_KINDS.items()]
    return "\n".join([
        "## 指摘の基準",
        "",
        "届いた重要度のラベルによらず、指摘ごとに次のどれかに当たるかを判定し直す。",
        "当たるものは `critical` / `major` とし、`criterion` に番号を書いて直す（`fixed`）。",
        "当たらないものは `minor` / `nit` とし、コードを変えずに `decision: \"waived\"` と `waive_kind` を書く"
        "（`criterion` は書かない）。",
        "",
        *_criteria_lines(focus),
        "",
        f"書かないもの（見送る）: {NOT_WRITTEN}",
        "",
        "見送りの種類（`waive_kind`）:",
        *kinds,
        "",
        "見送りの返信は `finalize` が雛形から組む:",
        "",
        "> " + waiver_reply("doc_mismatch", focus.names if focus.status == "declared" else ()),
    ])


def waiver_reply(kind: str, names=()) -> str:
    """見送りの返信の本文。重点の名前があるときだけ重点の句を足す。不明な種類は ValueError。"""
    if kind not in WAIVE_KINDS:
        raise ValueError(f"見送りの種類が {'/'.join(WAIVE_KINDS)} のどれでもない: {kind!r}")
    inner = WAIVE_KINDS[kind]
    names = [n for n in (names or ()) if isinstance(n, str) and n.strip()]
    if names:
        inner += REPLY_FOCUS.format(names=" / ".join(names))
    return REPLY.format(inner=inner)


def as_state(focus: Focus) -> dict:
    """状態ファイルの `review_criteria` の形。"""
    return {"status": focus.status, "focus": list(focus.names), "error": focus.error,
            "reviewer_block": reviewer_block(focus)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("block", choices=("reviewer", "fixer"))
    ap.add_argument("--root", help="宣言を読む worktree の根（省くと宣言を読まない）")
    a = ap.parse_args(argv)
    focus = load_focus(a.root) if a.root else NO_FOCUS
    if focus.status == "unreadable":
        print(f"レビューの重点の宣言を読めないため、基準 1・2・4 だけで続ける: {focus.error}", file=sys.stderr)
    print(reviewer_block(focus) if a.block == "reviewer" else fixer_block(focus))
    return 0


if __name__ == "__main__":
    sys.exit(main())
