#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""cross-review の効果を、状態ファイル 1 つから測る（#156）。

**測るために回し直さない。** 収束ループが残した記録を読み、方式ごとの結果と、
費用（ラウンド数・レビュワーの起動回数・実時間）と、収束の様子を JSON で出す。

**収束ループの外にある。** 状態ファイルを読むだけで、GitHub へは問い合わせない。
測定が失敗しても収束の進行は止まらない。

**1 回の実行が測るのは 1 つの状態ファイルである。** 集計の単位は測る目的で変わる
（変更の前後・担当ごと・リポジトリごと）ため、複数を渡して束ねる形は作らない。
出力は 0 か 1 の値で出し、束ねるのは測る側が行う。

    measure.py <状態ファイルのパス> [--output <パス>]

測る指標と、比較として読むときの限界は
[../docs/06-evidence.md](../docs/06-evidence.md) にある。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import sys
from typing import Any


# **担当の名前は 4 つである。** `reviewers` を持たない古い記録で、結果を残した
# 担当を数えるために使う（`state.py` の `LEGACY_AGENTS` は 2 者で、母集合を
# 広げる前の既定値である。ここは記録にある値だけを数えるため一覧を広く取る）。
AGENT_NAMES = ("codex", "agy", "claude", "kiro")


def _as_int(value: Any) -> int | None:
    """数として読めるときだけ int を返す。**読めない値を 0 にしない。**"""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rounds(st: dict[str, Any]) -> list[dict[str, Any]]:
    rounds = st.get("rounds")
    return [r for r in rounds if isinstance(r, dict)] if isinstance(rounds, list) else []


def _round_no(rounds: list[dict[str, Any]], index: int) -> int:
    """そのラウンドの通し番号。記録に無ければ並びから補う。"""
    recorded = _as_int(rounds[index].get("round"))
    return recorded if recorded is not None else index + 1


def _state_file_pr(st: dict[str, Any]) -> int | None:
    """状態ファイルの鍵（`init` した番号）。

    **`current_pr` ではない。** ローテーションを経ると `current_pr` は進むが、
    状態ファイルの名前も `rounds[]` の並びも最初の番号のままである。
    """
    for entry in st.get("pr_history") or []:
        if isinstance(entry, dict):
            pr = _as_int(entry.get("pr"))
            if pr is not None:
                return pr
    return _as_int(st.get("current_pr"))


def _prs(st: dict[str, Any]) -> list[int]:
    """`pr_history[]` の順に、この状態ファイルが含む Pull Request の全件。"""
    prs: list[int] = []
    for entry in st.get("pr_history") or []:
        if not isinstance(entry, dict):
            continue
        pr = _as_int(entry.get("pr"))
        if pr is not None and pr not in prs:
            prs.append(pr)
    if prs:
        return prs
    current = _as_int(st.get("current_pr"))
    return [current] if current is not None else []


def _parse_time(value: Any) -> _dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _dt.datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def _wall_clock_seconds(st: dict[str, Any]) -> int | None:
    """`started_at` と `ended_at` の差。**終わっていない実行では出さない。**

    片方でも読めなければ `None` を返す。0 で埋めると、終わっていない実行が
    「一瞬で終わった実行」として合計へ混ざる。
    """
    started = _parse_time(st.get("started_at"))
    ended = _parse_time(st.get("ended_at"))
    if started is None or ended is None:
        return None
    return int(round((ended - started).total_seconds()))


def _reviewer_count(round_rec: dict[str, Any]) -> int:
    """そのラウンドのレビュワーの数。

    **`reviewers` を正とする**（ラウンドを開くときに決めて残す値である）。
    持たない古い記録では、結果を残した担当の数で代える。
    """
    reviewers = round_rec.get("reviewers")
    if isinstance(reviewers, list) and reviewers:
        return len(reviewers)
    return sum(1 for name in AGENT_NAMES if isinstance(round_rec.get(name), dict))


def _cost(st: dict[str, Any]) -> dict[str, Any]:
    """費用はローテーション全体の合計である。

    Pull Request ごとの内訳は出さない（測る側が `rounds[].pr` を数えて得る）。
    **トークンの量は測らない**（状態ファイルが持たず、CLI ごとに取り方が違う）。
    """
    rounds = _rounds(st)
    return {
        "rounds": len(rounds),
        "reviewer_launches": sum(_reviewer_count(r) for r in rounds),
        "wall_clock_seconds": _wall_clock_seconds(st),
    }


def _convergence(st: dict[str, Any]) -> dict[str, Any]:
    """収束の様子。**測るのは 1 回の収束であるため、値は 0 か 1 である。**"""
    final = st.get("final")
    return {
        "final": final,
        "oscillation": 1 if final == "oscillation" else 0,
        "max_rounds": 1 if final == "max_rounds" else 0,
    }


def measure(st: dict[str, Any]) -> dict[str, Any]:
    """状態ファイルの中身から測定の結果を組み立てる。

    **キーは常に置く。** 読む側が「0 件」と「計算できない」を区別できるように
    するためと、欠けたキーを読んで落ちないようにするためである。
    """
    if not isinstance(st, dict):
        st = {}
    return {
        "pr": _state_file_pr(st),
        "prs": _prs(st),
        "rounds": len(_rounds(st)),
        "methods": {},
        "cost": _cost(st),
        "convergence": _convergence(st),
    }


def _load(path: pathlib.Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"状態ファイルを読めません: {path} ({exc})")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"状態ファイルの JSON を読めません: {path} ({exc})")
    if not isinstance(data, dict):
        raise SystemExit(f"状態ファイルの中身が辞書ではありません: {path}")
    return data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="cross-review の状態ファイル 1 つから効果を測る（#156）")
    parser.add_argument("state_file", help="cross-review-pr<番号>-state.json のパス")
    parser.add_argument("--output", help="書き出し先。省略すると標準出力へ出す")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    result = measure(_load(pathlib.Path(args.state_file)))
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        pathlib.Path(args.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
