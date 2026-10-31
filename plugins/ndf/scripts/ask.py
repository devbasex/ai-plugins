#!/usr/bin/env python3
"""問い合わせの記録の副命令（#2206）。記録の置き場と形は `lib/ask_ledger.py` にある。

| 副命令 | 何をする |
| --- | --- |
| `decide --answer <答え> --reason <理由> [--ref <id>] [--question <問い>]` | 代わりの判断を 1 行追記する。hook が止めた問いは `--ref`（止めた理由に載る `id`）で指す。手順が AI に決めさせた場面は `--question` で問いを書く |
| `summary [--since <ISO 8601>] [--cwd <パス>]` | 判定ごと・区分ごと・代わりの判断の `ref` の有無ごとの件数と、代わりの判断が無い止めた問いの件数 |

出力は統一の JSON レポート（`lib/step_result.py`）。終了コード: 0 = 書いた・数えた（記録が無ければ 0 件）/
1 = 引数の誤り・`--ref` が記録に無く `--question` も無い・答えか理由が空・書けない。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import ask_ledger  # noqa: E402
import step_result  # noqa: E402

TOOL = "ask"


def _refused(summary: str) -> tuple[dict, int]:
    return step_result.result(TOOL, "stopped", summary), 1


def cmd_decide(a) -> tuple[dict, int]:
    answer, reason = (a.answer or "").strip(), (a.reason or "").strip()
    if not answer or not reason:
        return _refused("記録しない: --answer と --reason を空にしない")
    if not a.ref and not a.question:
        return _refused("記録しない: --ref か --question のどちらかを渡す")
    ref_row = ask_ledger.find_row(a.ref) if a.ref else None
    if a.ref and ref_row is None and not a.question:
        return _refused(f"記録しない: --ref {a.ref} の判定の行が記録に無い。--question で問いを書く")
    question = a.question or "\n".join((ref_row or {}).get("questions") or [])
    if not question.strip():
        return _refused("記録しない: 問いが空（--question で問いを書く）")
    row = ask_ledger.decision_row(
        ref=a.ref or "",
        question=question,
        answer=a.answer,
        reason=a.reason,
        session=(ref_row or {}).get("session") or "",
        cwd=(ref_row or {}).get("cwd") or os.path.realpath(os.getcwd()),
    )
    try:
        path = ask_ledger.append_ask_row(row)
    except OSError as e:
        return _refused(f"代わりの判断の記録を書けない（{ask_ledger.ask_ledger_path()}）: {e}")
    return step_result.result(TOOL, "ok", f"代わりの判断を記録した（{row['id']}）", [row], {"path": str(path)}), 0


def cmd_summary(a) -> tuple[dict, int]:
    rows = ask_ledger.read_ask_rows()
    if a.since:
        rows = [r for r in rows if str(r.get("at") or "") >= a.since]
    if a.cwd:
        rows = [r for r in rows if str(r.get("cwd") or "").startswith(a.cwd)]
    m = ask_ledger.summarize_asks(rows)
    v = m["verdict"]
    text = f"問い合わせ: 止めた {v['deny']} 件・通した {v['allow']} 件・案内だけ {v['advise']} 件、代わりの判断が無い止めた問い {m['unanswered']} 件"
    return step_result.result(TOOL, "ok", text, [], m), 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ask.py", description="問い合わせの記録（#2206）")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decide", help="代わりの判断を記録する")
    d.add_argument("--answer", required=True)
    d.add_argument("--reason", required=True)
    d.add_argument("--ref")
    d.add_argument("--question")
    s = sub.add_parser("summary", help="判定と代わりの判断を数える")
    s.add_argument("--since")
    s.add_argument("--cwd")
    return p


def main(argv: list[str]) -> int:
    try:
        a = build_parser().parse_args(argv)
    except SystemExit as e:
        if e.code == 0:  # --help
            raise
        obj, code = _refused("引数の誤り")
        step_result.emit(obj, code)
    obj, code = cmd_decide(a) if a.cmd == "decide" else cmd_summary(a)
    step_result.emit(obj, code)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
