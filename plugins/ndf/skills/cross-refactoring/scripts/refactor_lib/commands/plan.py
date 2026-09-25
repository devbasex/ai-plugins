"""計画の取り込み（`merge-plan`、#933 の F3）。

実装担当の計画（段・足すテスト・範囲テストの対象・同じ変更か・公開の入出力が
変わりうるか）を読み、Jev が使えるときは段と「同じ変更か」を Jev に問う。そのうえで
順位を決め、配分テーブルで見積もり、想定最大時間に収まる件数を選び、項目ごとの
締め切りを出す。**数え上げと比較はスクリプトが行う**（決定 11）。
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

import jev
import run_metrics
import statefile

from .. import allocation, budget, clock, info, testcmd, timeline
from ..gitfacts import read_result, record_observed_model
from ..items import PLANNED, defer, group_key, item_kind, item_label, key_text
from ..paths import git_out, load_state, work_dir
from ..phases import elapsed_minutes, finish_phase
from ..vocabulary import (
    DEFER_BUDGET,
    DEFER_DUPLICATE,
    DEFER_NO_TARGET,
    JEV_DUPLICATE_CONFIDENCE,
    JEV_RISK_CONFIDENCE,
    JEV_TIER_CONFIDENCE,
)

TIERS = ["low", "medium", "high"]
# 実装担当が段を返さなかった候補の段。**順位の上でも下でもない中ほどに置く。**
DEFAULT_TIER = "medium"


def _read_plan_answers(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """実装担当の計画を `鍵 → 答え` で返す。読めなければ空（全候補を既定で扱う）。

    **計画が読めなくても止めない。** 段は Jev か既定の段で、テストは足さず、範囲テストは
    `--round-test` をそのまま使う形で進める。止めると提案に使った時間が丸ごと無駄になる。
    """
    impl = str(state["implementer"])
    record_observed_model(state, impl, "plan")
    outcome = read_result(state, impl, "plan")
    payload = outcome.payload
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        info(f"⚠ 実装担当 {impl} の計画を読めませんでした（{outcome.reason or 'missing'}）。"
             "段は既定、足すテストは無しとして計画します")
        return {}
    answers: dict[str, dict[str, Any]] = {}
    for entry in payload["items"]:
        if isinstance(entry, dict) and isinstance(entry.get("key"), str):
            answers[entry["key"].strip()] = entry
    return answers


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def _proposal_text(item: dict[str, Any]) -> str:
    """Jev へ送る提案の文。**提案のフィールドと賛同した者の数だけ**（決定 2・非機能の条件）。"""
    return json.dumps({
        "path": item.get("path"), "symbol": item.get("symbol"),
        "smell": item.get("smell"), "technique": item.get("technique"),
        "rationale": item.get("rationale"), "plan": item.get("plan"),
        "agreed_by": len(item.get("proposed_by") or []),
    }, ensure_ascii=False)


def _jev_usable(state: dict[str, Any]) -> bool:
    return (state.get("judge") or {}).get("kind") == "jev"


def _count_failure(state: dict[str, Any]) -> None:
    judge = state.setdefault("judge", {"kind": "runtime", "reason": None, "failures": 0})
    judge["failures"] = int(judge.get("failures") or 0) + 1


def _jev_score(
    state: dict[str, Any], text: str, question: str,
    options: list[str], min_confidence: float,
) -> Optional[str]:
    if not _jev_usable(state):
        return None
    result = jev.ask_score(text, question, options)
    if result is None:
        _count_failure(state)
        return None
    return result[0] if result[1] >= min_confidence else None


def _jev_boolean(
    state: dict[str, Any], text: str, question: str, min_confidence: float,
) -> Optional[tuple[bool, bool]]:
    if not _jev_usable(state):
        return None
    result = jev.ask_boolean(text, question)
    if result is None:
        _count_failure(state)
        return None
    return result[0], result[1] >= min_confidence


def _decide_tiers(state: dict[str, Any], answers: dict[str, dict[str, Any]]) -> None:
    """候補の全件に段を付ける。Jev の確信度が下限に満たなければ実装担当の段を使う。"""
    for item in state["candidates"]:
        answer = answers.get(key_text(item)) or {}
        runtime_tier = answer.get("tier") if answer.get("tier") in TIERS else DEFAULT_TIER
        item["tier"], item["tier_source"] = runtime_tier, "runtime"
        item["tests"] = _strings(answer.get("tests"))
        item["test_targets"] = _strings(answer.get("test_targets"))
        item["risk"] = answer.get("risk") is True
        item["merge_into"] = answer.get("merge_into") if isinstance(answer.get("merge_into"), str) else None
        result = _jev_score(
            state,
            _proposal_text(item),
            "How valuable is it to apply this behavior-preserving refactoring now?",
            TIERS,
            JEV_TIER_CONFIDENCE,
        )
        if result is not None:
            item["tier"], item["tier_source"] = result, "jev"


def _same_change(state: dict[str, Any], a: dict[str, Any], b: dict[str, Any]) -> bool:
    """同じ変更か。Jev が確信度 0.8 以上で真と答えたら統合、それ以外は実装担当の `merge_into`。"""
    result = _jev_boolean(
        state, f"A: {_proposal_text(a)}\nB: {_proposal_text(b)}",
        "Do proposals A and B describe the same code change?", JEV_DUPLICATE_CONFIDENCE,
    )
    if result is not None and result[0] and result[1]:
        return True
    return b.get("merge_into") == key_text(a) or a.get("merge_into") == key_text(b)


def _merge_duplicates(state: dict[str, Any]) -> list[dict[str, Any]]:
    """同じ `path` + `symbol` の組の中だけで「同じ変更か」を問い、統合した残りを返す。

    統合する向きは、並びの後ろ（賛同と重要度が低い側）から前へ。統合された候補は
    `duplicate` で見送り、賛同した者を統合先へ足す。
    """
    candidates = state["candidates"]
    merged_into: dict[str, str] = {}
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in candidates:
        by_group.setdefault(group_key(item), []).append(item)
    for members in by_group.values():
        for j, later in enumerate(members):
            for earlier in members[:j]:
                if earlier["id"] in merged_into:
                    continue
                if _same_change(state, earlier, later):
                    merged_into[later["id"]] = earlier["id"]
                    break
    by_id = {c["id"]: c for c in candidates}
    kept = []
    for item in candidates:
        target_id = merged_into.get(item["id"])
        if target_id is None:
            kept.append(item)
            continue
        target = by_id[target_id]
        for source in item.get("proposed_by") or []:
            if source not in target["proposed_by"]:
                target["proposed_by"].append(source)
        defer(state, item, DEFER_DUPLICATE, f"{target_id} と同じ変更")
    return kept


def _decide_public_io(state: dict[str, Any], items: list[dict[str, Any]]) -> None:
    """採った項目ごとに、公開の入出力が変わりうるか（D5）を**計画の時点で**決める（決定 25）。

    Jev が確信度 0.7 以上で答えればその答え、使えなければ実装担当の `risk` を使う。
    検証の段は、ここで決めた値を読むだけで、LLM へ問わない。差分はまだ無いため、
    Jev へ送るのは提案の文だけである。
    """
    for item in items:
        item["public_io"], item["public_io_source"] = bool(item.get("risk")), "runtime"
        result = _jev_boolean(
            state, _proposal_text(item),
            "Could this refactoring change the public input or output of the code?",
            JEV_RISK_CONFIDENCE,
        )
        if result is not None and result[1]:
            item["public_io"], item["public_io_source"] = bool(result[0]), "jev"


def _allocation_table(state: dict[str, Any]) -> dict[str, Any]:
    """配分テーブルを履歴から集計する（決定 7）。読めなければ初期値で、1 行知らせる（AC20）。"""
    base = run_metrics.metrics_dir()
    rows = allocation.read_history(allocation.history_path(base, str(state["repo"])))
    table = allocation.build_table(rows, allocation.load_defaults())
    if table.get("source") != "history":
        info("ℹ 配分の履歴が無いか読めないため、初期値（#917 の実測）で計画します")
    return table


def _limited_commands(state: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """項目ごとの範囲テストの語の並びを決める。決まらない項目は `no_target` で見送る（AC10b）。"""
    work = work_dir(state)
    source_state = {
        "round_test": (state.get("round_test") or {}).get("command"),
        "baseline_test": (state.get("baseline_test") or {}).get("command"),
        "target_scope": list(state.get("target_scope") or []),
    }
    kept = []
    for item in items:
        words, origin = testcmd.limited_command(
            source_state, item.get("test_targets") or [], work, item.get("tests") or [])
        if words is None:
            defer(state, item, DEFER_NO_TARGET,
                  "範囲テストを組み立てられず、--round-test も無い")
            continue
        item["command"], item["command_source"] = list(words), origin
        kept.append(item)
    return kept


def _plan_items(
    state: dict[str, Any], selected: list[dict[str, Any]], end: Any,
) -> list[dict[str, Any]]:
    """採った候補から項目（`items[]`）を作る。"""
    deadlines = budget.deadlines(selected, end)
    items = []
    for rank, (candidate, deadline) in enumerate(zip(selected, deadlines), start=1):
        test_deadline = deadline.get("test_start_deadline")
        items.append({
            **{k: candidate.get(k) for k in (
                "path", "symbol", "smell", "technique", "severity", "rationale", "plan",
                "estimated_diff_lines", "proposed_by", "tier", "tier_source", "risk",
                "tests", "test_targets", "command", "command_source")},
            "id": f"I-{rank:03d}",
            "candidate_id": candidate["id"],
            "rank": rank,
            "kind": item_kind(candidate),
            "estimate": candidate["estimate"],
            "start_deadline": clock.iso(deadline["start_deadline"]),
            "test_start_deadline": clock.iso(test_deadline) if test_deadline else None,
            "status": PLANNED,
            "commits": {"test": None, "implement": None, "fix": []},
            "seconds": {},
            "fix_count": 0,
            "danger": [],
        })
    return items


def cmd_merge_plan(args: argparse.Namespace) -> None:
    """計画を取り込み、時間に収まる項目と締め切りを決める。

    終了コード: 0 = 項目あり / 2 = 残る項目 0 件（最終ゲートへ）/ 4 = 中断。
    出力: `TESTS_NEEDED=0|1`（テストを足す項目があるか）。

    **叩き直しても計画を作り直さない。** 採用の件数・締め切り・控えはこの時点の予算で
    固定する（再開で予算を変えても食い違わない）。
    """
    path, state = load_state(args.id)
    if state.get("plan"):
        _emit_and_exit(state, replay=True)
        return

    answers = _read_plan_answers(state)
    _decide_tiers(state, answers)
    remaining = _merge_duplicates(state)
    remaining = _limited_commands(state, remaining)

    table = _allocation_table(state)
    for item in remaining:
        item["estimate"] = budget.item_estimate(table, str(item.get("technique")), bool(item["tests"]))
    ranked = sorted(remaining, key=budget.rank_key)

    baseline_seconds = (state.get("baseline_test") or {}).get("seconds")
    reserve = budget.reserve(baseline_seconds, bool(state.get("ci_check")), float(table["fix"]))
    elapsed = elapsed_minutes(state)
    available = budget.available_minutes(int(state["budget_minutes"]), elapsed, reserve)
    selected, skipped = budget.select(ranked, available)
    for item in skipped:
        defer(state, item, DEFER_BUDGET,
              f"見積り {budget.estimate_total(item['estimate']):.1f} 分が残りに入らない")

    end = budget.end_time(clock.parse(state["started_at"]), int(state["budget_minutes"]),
                          budget.reserve_total(reserve))
    state["items"] = _plan_items(state, selected, end)
    _decide_public_io(state, state["items"])
    state["plan"] = {
        "base_sha": git_out(work_dir(state), ["rev-parse", "HEAD"]),
        "elapsed_minutes": round(elapsed, 2),
        "available_minutes": round(available, 2),
        "reserve": reserve,
        "table_source": table.get("source"),
        "table": table,
        "selected": [i["id"] for i in state["items"]],
        "end_at": clock.iso(end),
    }
    # **実行時の値をすべて書き出す**（決定 24）。以後の段は、この表と時計の比較だけで進む。
    state["limits"] = timeline.of_state(state)
    finish_phase(state, "plan")
    if not state["items"]:
        state["phase"] = "final"
    statefile.save(path, state)
    _report(state, available, len(skipped))
    _emit_and_exit(state)


def _report(state: dict[str, Any], available: float, skipped: int) -> None:
    info(f"使える時間 {available:.1f} 分 → 採用 {len(state['items'])} 件 / 時間で見送り {skipped} 件"
         f"（配分: {state['plan']['table_source']}）")
    for item in state["items"]:
        tests = f" + テスト {len(item['tests'])}" if item["tests"] else ""
        info(f"  {item['id']} [{item['tier']}] {item_label(item)} {item['technique']}"
             f"（見積り {budget.estimate_total(item['estimate']):.1f} 分{tests} / "
             f"着手の締め切り {item['start_deadline']}）")


def _emit_and_exit(state: dict[str, Any], replay: bool = False) -> None:
    items = state.get("items") or []
    if replay:
        info(f"↻ 計画は取り込み済みです（項目 {len(items)} 件）")
    statefile.emit(TESTS_NEEDED=1 if any(i.get("tests") for i in items) else 0)
    if not items:
        info("採用できる項目が無いため、最終ゲートへ進みます")
        sys.exit(2)
