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
from typing import Any, NamedTuple


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

    **タイムゾーンの有無が揃わない記録も読めないものとして扱う。** offset-aware と
    offset-naive の引き算は `TypeError` を投げる。受け取らないと、費用の 1 項目の
    ために測定そのものが落ちる。
    """
    started = _parse_time(st.get("started_at"))
    ended = _parse_time(st.get("ended_at"))
    if started is None or ended is None:
        return None
    try:
        elapsed = (ended - started).total_seconds()
    except TypeError:
        return None
    return int(round(elapsed))


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


class Oracle(NamedTuple):
    """上限の方式の結果。

    `finding_ids` は**修正された指摘の集合**で、他の方式が拾えた件数
    （`matched`）を数えるときの突き合わせ先になる。
    """

    finding_ids: set[str]
    unmatched: int
    ambiguous: int


class ResolvedPositionSource(NamedTuple):
    round_no: int
    pr: int | None
    positions: list[Any]


def _representatives(st: dict[str, Any]) -> list[dict[str, Any]]:
    """母集合は代表だけである。

    統合された側（`merged_into` を持つ要素）を一緒に数えると、同じ指摘が
    2 件になる。`state.py` の区分・反証・集計も同じ規則で数えている。
    """
    findings = st.get("review_findings")
    if not isinstance(findings, list):
        return []
    return [f for f in findings if isinstance(f, dict) and not f.get("merged_into")]


def _matches(finding: dict[str, Any], pr: int | None, round_no: int,
             path: str, line: int) -> bool:
    """その解決が指しうる指摘かどうか。

    **同じ Pull Request の指摘に限る。** `review_findings[].round` は状態
    ファイル全体の通し番号であるため、ラウンドだけで絞ると、ローテーション前の
    Pull Request の指摘へ結ばれる。

    **そのうえで、解決を記録したラウンド以下の指摘に限る。** それより後の
    指摘は、解決した時点でまだ存在しない。
    """
    finding_round = _as_int(finding.get("round"))
    if finding_round is None or finding_round > round_no:
        return False
    if _as_int(finding.get("pr")) != pr:
        return False
    return finding.get("path") == path and _as_int(finding.get("line")) == line


def _has_recorded_positions(st: dict[str, Any]) -> bool:
    """位置の記録（`resolved_thread_positions`）を持つラウンドがあるか（#156）。

    **空の一覧は「持つ」である。** 解決したスレッドが 0 件だったことと、記録の
    項目そのものが無いこと（この変更より前に取った記録）は別である。前者は
    上限の方式が 0 件、後者は計算できない。
    """
    for round_rec in _rounds(st):
        fix = round_rec.get("fix")
        if isinstance(fix, dict) and isinstance(
                fix.get("resolved_thread_positions"), list):
            return True
    return False


def _find_best_match(
    representatives: list[dict[str, Any]],
    pr: int | None,
    round_no: int,
    path: str,
    line: int,
) -> tuple[str | None, bool]:
    """解決位置に対応する指摘 ID と、曖昧だったかを返す。"""
    candidates = [
        f for f in representatives if _matches(f, pr, round_no, path, line)
    ]
    if not candidates:
        return None, False
    newest = max(_as_int(f.get("round")) or 0 for f in candidates)
    newest_candidates = [
        f for f in candidates if (_as_int(f.get("round")) or 0) == newest
    ]
    if len(newest_candidates) > 1:
        return None, True
    finding_id = newest_candidates[0].get("finding_id")
    if finding_id is None:
        # **`str(None)` を返さない。** 呼び出し側の `finding_id is None` の検査を
        # すり抜け、上限の方式の集合へ文字列 `"None"` が入る。上限が 1 件多く
        # 見え、他の方式の再現率がその分だけ低く出る。
        return None, False
    return str(finding_id), False


def _resolved_position_sources(st: dict[str, Any]) -> list[ResolvedPositionSource]:
    """resolved_thread_positions を持つ round を、照合に必要な値へ変換する。"""
    rounds = _rounds(st)
    sources: list[ResolvedPositionSource] = []
    for index, round_rec in enumerate(rounds):
        fix = round_rec.get("fix")
        positions = (
            fix.get("resolved_thread_positions") if isinstance(fix, dict) else None
        )
        if isinstance(positions, list):
            sources.append(ResolvedPositionSource(
                _round_no(rounds, index),
                _as_int(round_rec.get("pr")),
                positions,
            ))
    return sources


def _resolved_position(position: Any) -> tuple[str | None, int | None]:
    """位置要素から path と line を取り出す。欠けていれば None を返す。"""
    if not isinstance(position, dict):
        return None, None
    path = position.get("path")
    line = _as_int(position.get("line"))
    return (path, line) if path else (None, line)


def _add_oracle_match(
    oracle: Oracle,
    representatives: list[dict[str, Any]],
    pr: int | None,
    round_no: int,
    path: str | None,
    line: int | None,
) -> Oracle:
    """1 件の resolved position を Oracle 集計へ反映する。"""
    if path is None or line is None:
        # 位置の欠けた要素も落とさない（`_thread_positions` が残す）。
        return Oracle(oracle.finding_ids, oracle.unmatched + 1, oracle.ambiguous)
    finding_id, is_ambiguous = _find_best_match(
        representatives, pr, round_no, path, line)
    if is_ambiguous:
        return Oracle(oracle.finding_ids, oracle.unmatched, oracle.ambiguous + 1)
    if finding_id is None:
        return Oracle(oracle.finding_ids, oracle.unmatched + 1, oracle.ambiguous)
    return Oracle(oracle.finding_ids | {finding_id}, oracle.unmatched, oracle.ambiguous)


def _oracle(st: dict[str, Any]) -> Oracle | None:
    """解決したスレッドの位置と指摘の位置を結び、修正された指摘を集める。

    **突き合わせは `(path, line)` で行う。** 絞り込んだ後になお複数が一致する
    ときはラウンドが最も新しいものを採り（行番号は修正で動くため、古い側へ結ぶと
    別の指摘を数える）、その中に 2 件以上あるときはどれとも結ばずに `ambiguous`
    へ数える。どの指摘とも一致しなかったものは `unmatched` へ数える。

    **どちらも `found` には数えない。** 落としたことが出力から見えないと、
    再現率が実際より高く出ていることに気づけない。
    """
    if not _has_recorded_positions(st):
        # **この変更より前に取った記録では計算できない。** 値を 0 で埋めると、
        # 「修正が 1 件も無かった実行」として比較へ混ざる。
        return None
    oracle = Oracle(set(), 0, 0)
    representatives = _representatives(st)
    for source in _resolved_position_sources(st):
        for position in source.positions:
            path, line = _resolved_position(position)
            oracle = _add_oracle_match(
                oracle, representatives, source.pr, source.round_no, path, line)
    return oracle


def _oracle_output(oracle: Oracle | None) -> dict[str, Any]:
    """**キーは常に置き、決まらない値は `null` にする。**

    省くと、読む側が「0 件」と「計算できない」を区別できないうえ、欠けたキーを
    読んで落ちる。**`reason` は値が `null` のときだけ置く**（決まった値に添えると、
    読む側が例外の有無を毎回見分けることになる）。
    """
    if oracle is None:
        return {
            "found": None, "unmatched": None, "ambiguous": None,
            "reason": "no_resolved_thread_positions",
        }
    return {
        "found": len(oracle.finding_ids),
        "unmatched": oracle.unmatched,
        "ambiguous": oracle.ambiguous,
    }


def _origin_runtimes(finding: dict[str, Any]) -> list[str]:
    """提案した担当の一覧。**持たない指摘は取り込み時の担当 1 者として読む。**

    この値は統合のときに初めて付く（`_merge_duplicates` / `_merge_declared_duplicates`
    の `setdefault`）。取り込んだ直後の指摘と、3 本目より前に取った記録は持たない。

    **無いものを「0 者」として読むと、比較対象の過去の記録の 1 者だけの方式が
    全件 0 になる。** 変更の前後を比べるのがこの測定の目的であり、前の側が
    数えられないと目的そのものが立たない。`state.py` 自身も同じ場面で
    `finding.get("origin_runtimes") or [finding.get("agent")]` と読んでいる。
    """
    origins = finding.get("origin_runtimes") or [finding.get("agent")]
    return [str(name) for name in origins if name]


def _recall(matched: int, base: int | None) -> float | None:
    """再現率。**分母が 0 か決まらないときは出さない。**

    `0.0` にすると「拾えなかった」と読めるが、実際は比べる相手がいない。

    小数第 2 位で丸める。**丸めても検算できる**のは、分子（`matched`）と分母
    （`oracle` の件数）を出力へ出しているためである。
    """
    if base is None or base == 0:
        return None
    return round(matched / base, 2)


def _method_output(finding_ids: set[str], oracle_ids: set[str] | None) -> dict[str, Any]:
    """方式 1 つの結果。

    **分子は `found` ではない。** 採用集合には修正されなかった指摘も却下された
    指摘も入る。`found` をそのまま割ると 1.0 を超える。拾えた件数は上限の方式の
    集合との積集合で数え、`finding_id` で突き合わせる。
    """
    if oracle_ids is None:
        return {"found": len(finding_ids), "matched": None, "of_oracle": None}
    matched = len(finding_ids & oracle_ids)
    return {
        "found": len(finding_ids),
        "matched": matched,
        "of_oracle": _recall(matched, len(oracle_ids)),
    }


def _single(representatives: list[dict[str, Any]],
            oracle_ids: set[str] | None) -> dict[str, Any]:
    """1 者だけの方式。**担当ごとに 1 通り出す。**

    1 者だけの結果は誰を選ぶかで変わる。1 つの数字にまとめると、選び方が結果に
    混ざる。**統合された側の要素は数えない**（統合の前後で値が変わらないよう、
    代表の `origin_runtimes` で判定する）。
    """
    per_agent: dict[str, set[str]] = {}
    for finding in representatives:
        for agent in _origin_runtimes(finding):
            per_agent.setdefault(agent, set()).add(str(finding.get("finding_id")))
    return {
        agent: _method_output(ids, oracle_ids)
        for agent, ids in sorted(per_agent.items())
    }


def _majority(representatives: list[dict[str, Any]],
              oracle_ids: set[str] | None) -> dict[str, Any]:
    """多数決の方式。**3 本目の統合の結果を読む。**

    `origin_runtimes` が 2 者以上の指摘を採る。**位置が近いだけの組を自分で
    数え直さない**（3 本目が近傍かつ本文の一致で統合しており、同じ判定を 2 か所に
    持つと片方だけが古くなる）。統合し損ねた組は `duplicate_candidates` に残り、
    この方式には入らない。
    """
    finding_ids = {
        str(finding.get("finding_id"))
        for finding in representatives
        if len(set(_origin_runtimes(finding))) >= 2
    }
    return _method_output(finding_ids, oracle_ids)


# 3 本目の区分のうち、この変更の方式が採る 2 つ（`state.py` の
# `COUNTED_CLASSIFICATIONS` と同じ）。**残る 3 つは採らない。**
COUNTED_CLASSIFICATIONS = ("verified_blocking", "needs_human_judgment")


def _evidence_rounds(st: dict[str, Any]) -> set[int]:
    """証拠集約（統合・実行検証・反証）を通ったラウンドの印。

    印の無いラウンドの指摘は区分を持たないか、持っていても反証を結ぶ前の値である。
    """
    marked: set[int] = set()
    for value in st.get("evidence_rounds") or []:
        round_no = _as_int(value)
        if round_no is not None:
            marked.add(round_no)
    return marked


def _all_rounds_marked(st: dict[str, Any], marked: set[int]) -> bool:
    """記録のラウンドがすべて印を持つか。

    すべて持つなら、この変更の方式の分母は他の 3 つと同じ集合になる。
    """
    rounds = _rounds(st)
    if not rounds:
        return False
    return all(_round_no(rounds, i) in marked for i in range(len(rounds)))


def _proposed(st: dict[str, Any], representatives: list[dict[str, Any]],
              oracle_ids: set[str] | None) -> dict[str, Any]:
    """この変更の方式。**読むのは証拠集約を通ったラウンドだけである。**

    印の無いラウンドを母集合へ入れると、区分の付かない指摘が
    `insufficient_evidence` として落ち、方式の再現率が実際より低く出る。

    **分母も印のあるラウンドに限る。** 分子だけを絞ると、印の混ざった記録で
    再現率が過小に出る。印の無い round 1 と印のある round 2 に修正された指摘が
    1 件ずつあるとき、採れるのは round 2 の 1 件だけであり、全ラウンドの上限
    （2 件）で割ると**拾えるものを全部拾っても 0.5 にしかならない**。

    **分母が全ラウンドと違うことは出力へ出す。** 添えないと、読む側がこの方式の
    再現率を他の 3 つと同じ分母の値として読む。
    """
    marked = _evidence_rounds(st)
    if not marked:
        return {
            "found": None, "matched": None, "of_oracle": None,
            "oracle_scope": None, "oracle_base": None,
            "reason": "no_evidence_rounds",
        }
    finding_ids = {
        str(finding.get("finding_id"))
        for finding in representatives
        if _as_int(finding.get("round")) in marked
        and finding.get("classification") in COUNTED_CLASSIFICATIONS
    }
    if oracle_ids is None:
        base_ids = None
    else:
        rounds_by_id = {
            str(finding.get("finding_id")): _as_int(finding.get("round"))
            for finding in representatives
        }
        base_ids = {
            fid for fid in oracle_ids if rounds_by_id.get(fid) in marked
        }
    result = _method_output(finding_ids, base_ids)
    result["oracle_scope"] = (
        "all_rounds" if _all_rounds_marked(st, marked) else "evidence_rounds"
    )
    result["oracle_base"] = None if base_ids is None else len(base_ids)
    return result


def _methods(st: dict[str, Any]) -> dict[str, Any]:
    """4 つの方式を、同じ `review_findings[]` から違う規則で読む。

    **上限の方式が上限を表す。** 実際に修正された指摘の集合であり、どの方式でも
    これを超えられない。そのため先に計算し、残りの方式が拾えた件数の
    突き合わせ先にする。
    """
    representatives = _representatives(st)
    oracle = _oracle(st)
    oracle_ids = None if oracle is None else oracle.finding_ids
    return {
        "single": _single(representatives, oracle_ids),
        "majority": _majority(representatives, oracle_ids),
        "proposed": _proposed(st, representatives, oracle_ids),
        "oracle": _oracle_output(oracle),
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
        "methods": _methods(st),
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
