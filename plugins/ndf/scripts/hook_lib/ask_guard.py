"""問い合わせの guard（#2206）。PreToolUse の `AskUserQuestion`（Claude Code）を `lib/ask_policy.py` の判定に掛ける。

| 判定 | 出力 |
| --- | --- |
| `deny`（`enforce` で区分が無い・形に合わない・許されていない） | `permissionDecision: deny` と理由（止めた理由・自分で決める案内・記録の手段・人へ回してよい区分・費用のしきい値） |
| `advise`（`advise` で同じ問い） | `additionalContext` に同じ案内（止めない） |
| `allow`（承認ゲート・レッドライン・許された区分） | `additionalContext` に費用のしきい値などの案内。`permissionDecision` は書かない |
| `off` | 何も出さず、記録も書かない |

判定のたびに `lib/ask_ledger.py` へ 1 行を追記する。追記の失敗は飲む。どの失敗も None（通す）を返す（I8）。

並列の計画（#2436 の 6d）: 判定が通す問いを、動いている計画のレーンか一時レーン（作業ディレクトリの役割のファイルが
`helper`）が出したときは、`parallel.py request-ask` でオーガナイザーへ送り、`deny` で止めて保留の手順を返す。
レーンでない・送れない・上限を越えたときは今どおり通す。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ASK_PY = Path(__file__).resolve().parent.parent / "ask.py"
PARALLEL_PY = Path(__file__).resolve().parent.parent / "parallel.py"
FORWARD_TIMEOUT = 8  # 秒。hook の 10 秒の上限の中に収める
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


def run_request_ask(main: Path, plan: str, lane: str, payload: dict) -> tuple[int, dict]:
    """`parallel.py request-ask` を子プロセスで打つ（上限 FORWARD_TIMEOUT 秒）。(終了コード, 統一 JSON レポート)。"""
    got = subprocess.run(
        [sys.executable, str(PARALLEL_PY), "request-ask", plan, "--lane", lane],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        cwd=str(main),
        timeout=FORWARD_TIMEOUT,
    )
    try:
        return got.returncode, json.loads(got.stdout.strip().splitlines()[-1]) if got.stdout.strip() else {}
    except ValueError:
        return got.returncode or 2, {}


def hold_text(rid: str, plan: str) -> str:
    """オーガナイザーへ送った問いの理由と保留の手順（正本: asking.md の「並列の計画のレーン」）。"""
    import parallel_files

    stop = parallel_files.stop_self() or f'python3 {parallel_files.RELAY} stop "$NDF_RELAY_NAME"'
    loop = f'python3 "{Path(__file__).resolve().parent.parent / "sprint-state.py"}" loop hold-question <ループ> --ref {rid} --plan <止まったプラン> --step <続きのステップ> --parallel {plan}'
    return "\n".join(
        [
            f"ndf: 並列の計画 {plan} のレーンの問いをオーガナイザーへ送った（依頼 {rid}）。この作業を保留し、別の作業へ進め。答えは周回の境で届く。",
            f"開発ループのレーン: {loop} を打ち、周回を質問待ちで締めて次の周回へ進む。",
            f"1 スプリントのレーン・一時レーン: 引継ぎ文書の「次にやること」に続きのコマンドと依頼 {rid} を書き、ndf-next を出さずに、"
            f"最後に {stop} を打って自分のラッパーを止めてセッションを終える（オーガナイザーが起こす）。",
            f"正本: {ASKING_MD}",
        ]
    )


def forward_to_organizer(ev, row_id: str) -> dict | None:
    """レーンか一時レーンの通す問いをオーガナイザーへ送り、送れたら deny を返す。当たらない・送れなければ None（通す）。"""
    name = os.environ.get("NDF_RELAY_NAME")
    if not name:
        return None
    import decl
    import parallel_files

    main = decl.main_dir(ev.cwd) or Path(ev.cwd)
    plan = parallel_files.lane_name_in_plans(main, name, os.environ.get("NDF_RELAY_DIR"))
    if not plan:
        return None
    code, got = run_request_ask(main, plan, name, {"tool_input": ev.tool_input, "cwd": ev.cwd, "ledger_id": row_id})
    rid = (got.get("metrics") or {}).get("id")
    if code != 0 or not rid:
        return None
    from hook_lib.token_guard import deny

    return deny(hold_text(rid, plan))


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
        try:
            held = forward_to_organizer(ev, row_id)
        except Exception:  # noqa: BLE001 — 送れない・上限を越えたときは今どおり通す（I8）
            held = None
        return held or _context(pass_text(verdict, decl))
    except Exception:  # noqa: BLE001 — 宣言・判定・記録のどの失敗も通す（I8）
        return None
