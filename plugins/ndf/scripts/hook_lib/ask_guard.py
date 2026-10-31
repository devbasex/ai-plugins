"""問い合わせの guard（#2206）。PreToolUse の `AskUserQuestion`（Claude Code）を `lib/ask_policy.py` の判定に掛ける。

| 判定 | 出力 |
| --- | --- |
| `deny`（`enforce` で区分が無い・形に合わない・許されていない） | `permissionDecision: deny` と理由（止めた理由・自分で決める案内・記録の手段・人へ回してよい区分・費用のしきい値） |
| `advise`（`advise` で同じ問い） | `additionalContext` に同じ案内（止めない） |
| `allow`（承認ゲート・レッドライン・許された区分） | `additionalContext` に費用のしきい値などの案内。`permissionDecision` は書かない |
| `off` | 何も出さず、記録も書かない |

判定のたびに `lib/ask_ledger.py` へ 1 行を追記する。追記の失敗は飲む。どの失敗も None（通す）を返す（I8）。
"""

from __future__ import annotations

from pathlib import Path

ASK_PY = Path(__file__).resolve().parent.parent / "ask.py"
ASKING_MD = Path(__file__).resolve().parent.parent.parent / "skills" / "development-workflow" / "references" / "asking.md"

STOP_REASONS = {
    "missing": "区分（metadata.source）が無い",
    "malformed": "区分「{source}」が区分の形（ndf-gate:<1|2>・ndf-ask:redline:<番号>・ndf-ask:<名前>）に合わない",
    "not-allowed": "区分「{source}」を .ndf/ask.json が人へ回してよい区分として許していない",
}


def _common_tail(decl) -> list[str]:
    lines = [f"{decl.threshold_usd:g} USD 以下の費用は AI が決める（.ndf/ask.json の cost.threshold_usd）。"]
    if decl.problem:
        lines.append(f".ndf/ask.json を読めなかった（{decl.problem}）。既定（enforce・組み込みの 4 区分）で判定した。")
    return lines


def ask_stop_text(verdict, decl, row_id: str) -> str:
    """`deny` の理由（`advise` の案内にも使う）。"""
    import ask_policy

    reason = STOP_REASONS.get(verdict.kind, "区分が許されていない").format(source=verdict.source)
    listing = "\n".join(f"- {src}: {desc}" for src, desc in ask_policy.passing_sources(decl))
    lines = [
        f"ndf: この AskUserQuestion を止めた。理由: {reason}。",
        "自分で決め、理由を記録して続ける。迷い・情報不足・好みの違いは人へ回す理由にならない。根拠を集めて推奨の案を選ぶ。",
        f'記録の手段: python3 "{ASK_PY}" decide --ref {row_id} --answer "<選んだ答え>" --reason "<理由>"',
        f"人へ回してよい区分（metadata.source に付ける。正本: {ASKING_MD}）。問いがこのどれか（特にレッドラインの操作の承認）に当たるときだけ、"
        f"その区分を付けて出し直す:\n{listing}",
        *_common_tail(decl),
    ]
    return "\n".join(lines)


def pass_text(verdict, decl) -> str:
    lines = []
    if verdict.kind == "allowed":
        lines.append(f"ndf: 区分 {verdict.source} で人へ回した。区分の当てはめが誤っていれば取り下げて自分で決める。")
    return "\n".join(lines + _common_tail(decl))


def _record_verdict(ev, verdict, decl) -> str:
    """判定の行を追記し、その `id` を返す。追記に失敗しても `id` は返す。"""
    import ask_ledger

    row = ask_ledger.verdict_row(
        session=ev.session,
        cwd=ev.cwd,
        runtime="claude",
        source=verdict.source,
        verdict=verdict.decision,
        reason=verdict.kind,
        mode=decl.mode,
        decl_problem=decl.problem,
        tool_input=ev.tool_input,
    )
    try:
        ask_ledger.append_ask_row(row)
    except Exception:  # noqa: BLE001 — 記録を書けなくても判定は変えない（E5）
        pass
    return row["id"]


def _context(text: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}


def ask_decision(ev) -> dict | None:
    try:
        import ask_policy
        from hook_lib.token_guard import deny

        decl = ask_policy.load_ask_declaration(ask_policy.project_root(ev.cwd))
        verdict = ask_policy.judge_ask(ev.tool_input, decl)
        if verdict.decision == "off":
            return None
        row_id = _record_verdict(ev, verdict, decl)
        if verdict.decision == "deny":
            return deny(ask_stop_text(verdict, decl, row_id))
        if verdict.decision == "advise":
            return _context("ndf: mode が advise のため止めていない。\n" + ask_stop_text(verdict, decl, row_id))
        return _context(pass_text(verdict, decl))
    except Exception:  # noqa: BLE001 — 宣言・判定・記録のどの失敗も通す（I8）
        return None
