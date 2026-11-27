"""サブコマンド `ndf models`: 起動のモデルと effort を選び、効果と費用を集計する（#2944）。

    ndf models pick --layer <層> --name <作業の名前> [--runtime claude] [--unit <単位>] [--retry <理由>] [--args]
    ndf models report --since <日付> --until <日付> [--stage <工程>]

`pick` は `adapters.model_choice.launch_choice` の値を返す。既定は、値・出所・割り付け ID と CLI に渡す引数を結果 JSON の
items に載せる。`--args` を付けると、CLI に渡す引数を 1 行に 1 つずつ標準出力へ出す（引用符もエスケープも付けない）。`--args` の
失敗は標準出力に何も出さず、理由を標準エラーに書いて停止理由の終了コード（`precondition` など）で終える。

`report` は使用量ログと実験の記録を、呼ばれた時点で集計し直す（出現率のファイルは読まず、書き換えない）。期間は JST の日付で、
両端の日を含む。集計の順と区分は設計書（issue-2944-design.md）の「`ndf models report` は次の順で集計する」に従う。
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from ndf import cli
from ndf.adapters import launch_settings, model_choice
from ndf.contracts import causes, internal, result
from ndf.domains.records import allocation_decide as ad
from ndf.domains.records import experiments, jsonl, usage
from ndf.domains.records import usage_decide as ud

TOOL = cli.tool_of(__name__)
# 1 行 1 引数で出すフラグ
ARGS_FLAG = "--args"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"ndf {TOOL}", description="起動のモデルと effort を選ぶ")
    sub = p.add_subparsers(dest="command", required=True)
    pick = sub.add_parser("pick", help="層と作業の名前から、起動のモデルと effort を選ぶ")
    pick.add_argument("--layer", required=True, help="起動の層（worker・skill・judge・seat）")
    pick.add_argument("--name", required=True, help="作業の名前（worker の作業の名前・Skill の名前など）")
    pick.add_argument("--runtime", default="claude", help="ランタイム（claude か codex）")
    pick.add_argument("--unit", help="単位の ID（省けば割り付けない）")
    pick.add_argument("--retry", help="同じ失敗への 2 回目以降の起動の理由（省けば引き上げない）")
    pick.add_argument(ARGS_FLAG, action="store_true", help="CLI に渡す引数を 1 行に 1 つずつ出す（省けば結果 JSON）")
    report = sub.add_parser("report", help="使用量ログと実験の記録から、組ごとの効果と費用を集計する")
    report.add_argument("--since", type=date.fromisoformat, required=True, help="期間の最初の日（JST。YYYY-MM-DD）")
    report.add_argument("--until", type=date.fromisoformat, required=True, help="期間の最後の日（JST。YYYY-MM-DD）")
    report.add_argument("--stage", help="工程（impl など。省けばすべて）")
    return p


def _pick_command(a: argparse.Namespace) -> int:
    try:
        got = model_choice.launch_choice(a.layer, a.name, a.runtime, cwd=Path.cwd(), unit=a.unit, retry=a.retry)
    except internal.InternalError as exc:
        if not a.args:
            raise
        print(f"ndf {TOOL} pick: {exc}", file=sys.stderr)
        return causes.exit_code(exc.kind)
    args = got.cli_args()
    if a.args:
        sys.stdout.write("".join(f"{word}\n" for word in args))
        return 0
    item = {**got.row_fields(), "args": args}
    if a.unit is not None:  # 使用量ログの欄は割り付けた起動だけ unit を持つ。pick の結果には渡された単位を載せる
        item["unit"] = a.unit
    if got.stage is not None:
        item["stage"] = got.stage
    return result.emit(result.result(TOOL, result.OK, f"{a.layer} {a.name} の起動の値", items=[item]))


# --- report ------------------------------------------------------------------------------------------

# 起動の値の区分: 工程に続けて、その行の指定値とキー
SEGMENT = ("repo", "runtime", "mode", "requested_model", "effort")
# 効果の平均の幅（正規近似の 95%）の係数
Z95 = 1.96
VERDICT_TEXT = {
    ad.Verdict.INCOMPARABLE: "比較できない",
    ad.Verdict.SHORT: "件数が足りない",
    ad.Verdict.DECIDED: "判断できる",
    ad.Verdict.UNDECIDED: "優劣を判定できない",
}
COLLECTING = "件数を集めている"
UNPRICED_NOTE = "費用を計算できないモデルがあり均等に抽選する"
NO_RECORDS = "記録なし"


@dataclass
class _Tally:
    """区分 1 つの起動の値（行の期間）・リクエスト・ステップ・判定の数。"""

    launches: int = 0
    seconds: float = 0.0
    tokens: ud.Usage = field(default_factory=ud.Usage)
    cost: float = 0.0
    unpriced: int = 0
    mismatched: int = 0
    requests: int = 0
    succeeded: int = 0
    steps: int = 0
    retried: int = 0
    judged: int = 0
    rejudged: int = 0
    returned: int = 0


@dataclass(frozen=True)
class _Span:
    since: datetime
    until: datetime

    def has(self, row: Mapping[str, Any]) -> bool:
        return self.since <= datetime.fromisoformat(row["at"]) <= self.until


def _row_stage(row: Mapping[str, Any]) -> Optional[str]:
    """行の工程（層と作業の名前から引く）。層か名前の無い行と、表に無い組は None（区分に入れない）。"""
    if "layer" not in row or "name" not in row:
        return None
    try:
        return model_choice.stage_of(row["layer"], row["name"])
    except internal.InternalError:  # noqa: NDF401 ファイル: 知らない層の行は工程の無い行として集計に入れない
        return None


def _segment(row: Mapping[str, Any]) -> tuple:
    return (_row_stage(row), *(row[k] if k in row else None for k in SEGMENT))


def _rate(part: int, whole: int) -> Optional[float]:
    return part / whole if whole else None


def _add_launch(t: _Tally, row: Mapping[str, Any], cost: Optional[float]) -> None:
    t.launches += 1
    t.seconds += row["seconds"] if "seconds" in row else 0.0
    used = ud.usage_of(row["usage"])
    t.tokens = t.tokens.plus(used) if used is not None else t.tokens
    if cost is None:
        t.unpriced += 1
    else:
        t.cost += cost
    t.mismatched += "requested_model" in row and "model" in row and row["requested_model"] != row["model"]


def _launch_end(row: Mapping[str, Any]) -> Optional[str]:
    return row["outcome"] if "outcome" in row else None


def _add_request(tally: Mapping[tuple, _Tally], group: Sequence[Mapping[str, Any]], refused: set[str], rule: Mapping[str, Any]) -> None:
    """リクエスト 1 件。区分は拒否された行を除いた最初の行（すべて拒否なら最後の行）、結果は最後の行の `outcome`。

    拒否された行は、モデルを理由にした拒否（`launch_error` の行）の割り付け ID を持ち、成功でない行。すべて拒否なら失敗に数え、
    判定のやり直し率では分母だけに数える。
    """
    success = rule["success_outcomes"]
    rows = sorted(group, key=lambda r: r["request_try"] if "request_try" in r else 1)
    valid = [r for r in rows if not ("assign_id" in r and r["assign_id"] in refused and _launch_end(r) not in success)]
    first = valid[0] if valid else rows[-1]
    t, last = tally[_segment(first)], _launch_end(rows[-1]) if valid else None
    if not valid or (last is not None and last not in rule["uncounted_outcomes"]):
        t.requests += 1
        t.succeeded += last in success
    if first["layer"] == "judge":
        t.judged += 1
        t.rejudged += bool(valid) and _launch_end(first) == "invalid" and len(valid) > 1
        t.returned += sum(_launch_end(r) == "invalid" for r in valid) >= 2


def _add_step(tally: Mapping[tuple, _Tally], group: Sequence[Mapping[str, Any]]) -> None:
    """ステップ 1 つ。区分は `attempt` が最も小さい行、やり直しは `attempt` が 2 以上の行があるか。"""
    t = tally[_segment(min(group, key=lambda r: r["attempt"] if "attempt" in r else 1))]
    t.steps += 1
    t.retried += any("attempt" in r and r["attempt"] >= 2 for r in group)


def _grouped(rows: Sequence[Mapping[str, Any]], key) -> list[list[Mapping[str, Any]]]:
    groups: dict[Any, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return list(groups.values())


def _last_in(group: Sequence[Mapping[str, Any]], span: _Span) -> bool:
    return span.has(max(group, key=lambda r: r["at"]))


def _launch_items(usage_rows: Sequence[Mapping[str, Any]], refused: set[str], span: _Span, stage: Optional[str]) -> list[dict]:
    """起動の値・成功率・やり直し率の区分ごとの値（設計書の「期間と区分」の表）。重なった `launch_id` は 1 行にする。"""
    rule = model_choice.records_rule()
    pairs = [(r, cost) for r, cost in ud.launch_rows(usage_rows) if "layer" in r and _row_stage(r) is not None]
    rows = [r for r, _ in pairs]
    tally: dict[tuple, _Tally] = defaultdict(_Tally)
    for row, cost in pairs:
        if span.has(row):
            _add_launch(tally[_segment(row)], row, cost)
    for group in _grouped(rows, lambda r: r["request_id"] if "request_id" in r else id(r)):
        if _last_in(group, span):
            _add_request(tally, group, refused, rule)
    stepped = [r for r in rows if r["layer"] in rule["step_layers"] and "run_id" in r and "step" in r]
    for group in _grouped(stepped, lambda r: (r["run_id"], r["step"])):
        if _last_in(group, span):
            _add_step(tally, group)
    return [_launch_item(k, t) for k, t in sorted(tally.items(), key=lambda kv: str(kv[0])) if stage in (None, k[0])]


def _launch_item(key: tuple, t: _Tally) -> dict:
    item = {"kind": "launch_group", "stage": key[0]} | {k: v for k, v in zip(SEGMENT, key[1:]) if v is not None}
    item |= {"launch_count": t.launches, "seconds": t.seconds, "tokens": t.tokens.as_dict(), "cost_usd": t.cost}
    item |= {"unpriced_launches": t.unpriced, "mismatched": t.mismatched, "requests": t.requests, "steps": t.steps}
    item |= {"success_rate": _rate(t.succeeded, t.requests), "retry_rate": _rate(t.retried, t.steps)}
    return item | {"judge_requests": t.judged, "judge_retry_rate": _rate(t.rejudged, t.judged), "judge_returned": t.returned}


def _interval(s: ad.Stats) -> Optional[list[float]]:
    """効果の平均の 95% の幅（下限と上限。正規近似で、分散は有効な件数で割る）。単位が無ければ None。"""
    if not s.units:
        return None
    half = Z95 * math.sqrt(s.quality * (1 - s.quality) / s.quality_n_eff)
    return [max(0.0, s.quality - half), min(1.0, s.quality + half)]


def _arm_item(where: model_choice.Where, arm: Mapping[str, str], s: ad.Stats, base_roi: Optional[float]) -> dict:
    roi = s.quality / s.cost if s.units and s.cost else None
    item = {"kind": "arm", **where.fields(), "key": where.key, **dict(arm), "units": s.units}
    item |= {"quality": s.quality if s.units else None, "quality_interval": _interval(s), "cost_usd": s.cost}
    return item | {"unpriced_units": s.units - s.cost_units, "roi": roi, "roi_ratio": roi / base_roi if roi and base_roi else None}


def _arm_note(phase: str, verdict: ad.Verdict) -> str:
    """段階（件数を集めている・費用を計算できない）と優劣の表示の文。"""
    shown = [COLLECTING] if phase == "burn_in" else [UNPRICED_NOTE] if phase == "uniform" else []
    return "・".join([*shown, VERDICT_TEXT[verdict]])


def _arm_values(where: model_choice.Where, assigned: Sequence[Mapping[str, Any]], rows, usage_rows, span: _Span) -> list:
    """`where` のキーの、期間に割り付けた単位の値（`experiments.unit_values` の絞り込みと同じ）。"""
    days = math.ceil((span.until - span.since) / timedelta(days=1)) + 1
    observed = model_choice.observed_models(rows, where, assigned[0]["arms"], span.since)
    found = experiments.unit_values(rows, usage_rows, where=where, now=span.until, window_days=days, observed=observed)
    return [u for u in found if span.since <= u.at <= span.until]


def _experiment_items(where, assigned, rows, usage_rows, span: _Span, allocation: Mapping[str, Any]) -> list[dict]:
    """キー 1 つの組ごとの値。打ち切りの判定 → 候補 → 段階と確率（出現率の計算と同じ順）を、記録から計算し直す。"""
    arms = assigned[0]["arms"]
    units = _arm_values(where, assigned, rows, usage_rows, span)
    seed = experiments.seed_of(where, allocation, units)
    cut = ad.eliminated(units, range(len(arms)), allocation["margin"], allocation["window_days"], random.Random(seed))
    cands = [i for i in model_choice.candidates(arms, rows, None, span.until, where) if i not in cut]
    ids = {a["assign_id"] for a in assigned}
    unpriced = sorted({r["model"] for r in rows if r["kind"] == "unpriced" and r["assign_id"] in ids})
    entry = ad.probabilities(units, cands, allocation, random.Random(seed))
    judge = ad.judgement(units, cands, allocation, random.Random(seed))
    phase = "uniform" if unpriced else entry.phase
    used = ad.comparable(units, cands)
    stats = [ad.weighted([u for u in used if u.arm == i]) for i in range(len(arms))]
    base_roi = stats[0].quality / stats[0].cost if stats[0].units and stats[0].cost else None
    modes = {a["assign_id"]: a["mode"] for a in assigned if "mode" in a}
    best = judge.best or entry.best
    items = []
    for i, arm in enumerate(arms):
        item = _arm_item(where, arm, stats[i], base_roi) | {"arm": i, "phase": phase, "verdict": str(judge.verdict)}
        item["modes"] = {m: sum(modes.get(u.assign_id) == m for u in used if u.arm == i) for m in sorted(set(modes.values()))}
        item["modes"] = {m: n for m, n in item["modes"].items() if n}
        item["note"] = _arm_note(phase, judge.verdict) if stats[i].units else NO_RECORDS
        item |= {"best": best[i]} if best is not None else {}
        item |= {"probability": entry.probs[i]} if phase == "adaptive" and entry.probs is not None else {}
        item |= {"leader": True} if judge.leader == i else {}
        item |= ({"dropped": "cutoff", "dropped_until": internal.stamp(cut[i].timestamp())} if i in cut else
                 {"dropped": "refused"} if i not in cands else {})  # fmt: skip
        items.append(item | ({"unpriced_models": unpriced} if unpriced else {}))
    return items


def report_rows(
    usage_rows: Sequence[Mapping[str, Any]],
    experiment_rows: Sequence[Mapping[str, Any]],
    since: datetime,
    until: datetime,
    stage: Optional[str],
    allocation: Mapping[str, Any],
) -> list[dict]:
    """設計書の「`ndf models report` は次の順で集計する」の順と区分で、起動の区分ごとの値と、実験のキーの組ごとの値を組む。

    `allocation` は計算に使う割り付けの値（今の設定の `floor`・`min_units`・`margin`・`window_days`）。
    """
    span = _Span(since, until)
    refused = {r["assign_id"] for r in experiment_rows if r["kind"] == "launch_error"}
    items = _launch_items(usage_rows, refused, span, stage)
    found = [a for a in model_choice.assigns(experiment_rows).values() if span.has(a) and stage in (None, a["stage"])]
    keys = sorted({(a["repo"], a["stage"], a["runtime"], a["key"]) for a in found})
    for where in (model_choice.Where(*k) for k in keys):
        assigned = [a for a in found if where.of(a)]
        items += _experiment_items(where, assigned, experiment_rows, usage_rows, span, allocation)
    return items


def _report(a: argparse.Namespace) -> int:
    """使用量ログと実験の記録を読み、集計した結果を出す。記録が無ければ「記録なし」。出現率のファイルは書き換えない。"""
    since, until = ud.jst_bounds(a.since, a.until)
    allocation = model_choice.table(launch_settings.launch_root(Path.cwd())).allocation
    try:
        store = model_choice.records_dir()
        usage_rows, broken = jsonl.read_rows(usage.usage_log_path(), kind="usage")
        first = min(since, until - timedelta(days=model_choice.records_rule()["refusal_days"]))
        rows = model_choice.read_experiments(first, store)
    except model_choice.STORE_ERRORS as exc:
        raise internal.InternalError("precondition", f"使用量ログか実験の記録を読めない: {exc}") from exc
    items = report_rows(usage_rows, rows, since, until, a.stage, allocation)
    period = f"{a.since}..{a.until}"
    summary = f"{period} の集計 {len(items)} 件" if items else f"{period}: {NO_RECORDS}"
    metrics = {"usage_rows": len(usage_rows), "experiment_rows": len(rows), "broken_rows": len(broken)}
    return result.emit(result.result(TOOL, result.OK, summary, items=items, metrics=metrics))


def main(argv: list[str]) -> int:
    try:
        a = _parser().parse_args(argv)
    except SystemExit as exc:
        if exc.code in (0, None) or ARGS_FLAG not in argv:
            raise
        return causes.exit_code("invalid")  # --args の形は結果 JSON を出さない。使い方は argparse が標準エラーへ書いた
    return _report(a) if a.command == "report" else _pick_command(a)
