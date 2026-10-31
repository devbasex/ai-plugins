#!/usr/bin/env python3
"""承認ゲートの利用者の答え（差し戻し・承認）を記録し、差し戻しを承認ゲートごとに数える（#1833・#2183）。

| 副命令 | 何をする |
| --- | --- |
| `record --gate <名> --what <要約> (--sprint <状態ファイル> \\| --issue N \\| --pr N)... [--outcome rejected\\|approved] [--repo OWNER/REPO] [--at <ISO 8601>] [--root DIR]` | 答えを 1 行追記する（既定は差し戻し）。状態ファイルのあるスプリントは `sprint-state.py gate --by user` が同じ記録へ書く |
| `count [--sprint <状態ファイル>] [--issue N]... [--pr N]... [--repo OWNER/REPO] [--root DIR]` | 渡した範囲のどれかに当たる差し戻しの行を、関門 1・関門 2・ほかに分けて数える。範囲を渡さなければリポジトリのすべての行 |

`--gate` には `関門 1`（設計の承認）か `関門 2`（本番への配布の承認）を渡す。ほかの名前は `other` に数える。
`--repo` を省くと `--root`（既定はカレント）の `origin` の `owner/repo`、読めなければメインディレクトリの絶対パスを使う。

`count` は、範囲に答えの行が 1 つも無く、`--sprint` の状態ファイルにも承認ゲート（関門 1・関門 2）の記録が無ければ、
件数を 0 と書かずに「未記録」（`status: stopped`・件数は `null`・`metrics.recorded` は false）を返す。

終了コード: 0 = 書いた・数えた / 1 = 記録を書けない / 2 = 引数の誤り・範囲が無い・記録を読めない（件数は `null`、
summary は「不明」）/ 3 = 範囲に答えの行が無い（件数は `null`、summary は「未記録」）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import clock  # noqa: E402
import gate_rejections as gr  # noqa: E402
import jsonio  # noqa: E402
import step_result  # noqa: E402

TOOL = "gate-rejections"


def _scope_args(a) -> dict:
    return {"sprint": a.sprint, "issues": a.issue or [], "prs": a.pr or []}


def cmd_record(a) -> tuple[dict, int]:
    repo = gr.ledger_repo(a.root, a.repo)
    try:
        row = gr.make_row(a.gate, a.what, repo=repo, at=a.at or clock.now_iso("utc"), outcome=a.outcome, **_scope_args(a))
    except ValueError as e:
        return step_result.result(TOOL, "stopped", f"記録しない: {e}"), step_result.EXIT_UNREADABLE
    try:
        path = gr.append(row)
    except OSError as e:
        return step_result.result(TOOL, "stopped", f"差し戻しの記録を書けない（{gr.ledger_path()}）: {e}"), step_result.EXIT_VIOLATION
    word = "差し戻し" if row["outcome"] == gr.REJECTED else "承認"
    summary = f"{row['gate']} の{word}を記録した（{row['at']}）"
    return step_result.result(TOOL, "ok", summary, [row], {"path": str(path)}), step_result.EXIT_OK


def cmd_count(a) -> tuple[dict, int]:
    repo = gr.ledger_repo(a.root, a.repo)
    scope = _scope_args(a)
    unknown = dict.fromkeys(gr.COUNT_KEYS)
    try:
        rows = [r for r in gr.read_rejections() if gr.in_scope(r, repo=repo, **scope)]
        answered = bool(rows) or gr.state_answered(a.sprint)
    except jsonio.JsonReadError as e:
        return step_result.result(TOOL, "stopped", f"承認ゲートの差し戻し: 不明（{e}）", [], unknown), step_result.EXIT_UNREADABLE
    if not answered:
        summary = "承認ゲートの差し戻し: 未記録（範囲に承認ゲートの答えの行が無い。0 件とは読まない）"
        return step_result.result(TOOL, "stopped", summary, [], {**unknown, "recorded": False}), step_result.EXIT_PRECONDITION
    c = gr.count_by_gate(rows)
    summary = f"承認ゲートの差し戻し: 関門 1 が {c['gate_1']} 件・関門 2 が {c['gate_2']} 件（ほか {c['other']} 件）"
    return step_result.result(TOOL, "ok", summary, [r for r in rows if gr.is_rejection(r)], {**c, "recorded": True}), step_result.EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="gate-rejections.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("record", "count"):
        s = sub.add_parser(name)
        if name == "record":
            s.add_argument("--gate", required=True, help="承認ゲートの名前（関門 1 / 関門 2）")
            s.add_argument("--what", required=True, help="何を差し戻したか・承認したかの要約")
            s.add_argument("--at", help="答えた日時（ISO 8601。既定は今）")
            s.add_argument("--outcome", choices=gr.OUTCOMES, default=gr.REJECTED, help="利用者の答え（既定は rejected）")
        s.add_argument("--sprint", help="スプリントの状態ファイル")
        s.add_argument("--issue", type=int, action="append", help="課題の番号（繰り返せる）")
        s.add_argument("--pr", type=int, action="append", help="Pull Request の番号（繰り返せる）")
        s.add_argument("--repo", help="OWNER/REPO（既定は --root の origin、読めなければメインディレクトリの絶対パス）")
        s.add_argument("--root", default=".", help="リポジトリの根（既定はカレント）")
    return ap


def main(argv: list[str] | None = None) -> None:
    a = build_parser().parse_args(argv)  # 引数の誤りは argparse が 2 で終える
    out, code = (cmd_record if a.cmd == "record" else cmd_count)(a)
    step_result.emit(out, code)


if __name__ == "__main__":
    main()
