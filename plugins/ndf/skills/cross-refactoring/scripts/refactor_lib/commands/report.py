"""実行の終わりと報告（`finalize` / `status` / `report`、#933 の F8 と AC26）。"""

from __future__ import annotations

import argparse
from collections import Counter
from typing import Any

import assignment
import mdtable
import models as models_lib
import run_metrics
import statefile

from .. import allocation, clock, culprit, info, launch, ledger, stop_revert, timeline
from ..codemetrics_view import record_lines
from ..items import item_label
from ..measure import summary_extra
from ..outbound import plan_reference
from ..plan import baseline_line, counts_line, existing_failures_line, status_label, strategy_lines
from ..paths import load_state
from ..phases import phase_record
from ..vocabulary import DEFER_REASONS

# `cross-review` の最終ステータスのうち、追記してよいもの。
APPROVED = "approved"


def _gate_passed(state: dict[str, Any]) -> bool:
    return ledger.adoption_confirmed(state)


def cmd_finalize(args: argparse.Namespace) -> None:
    """最終ゲートが通った実行だけ、履歴へ 1 行追記する（AC17）。**失敗しても 0 で終わる。**

    | 起動のされ方 | 追記する条件 |
    | --- | --- |
    | 単独 | 最終ゲートのチェックが通り、`--review-status` が `approved` |
    | 工程の 1 つ | 最終ゲートが通った（全体のテストか継続的統合） |

    **単独起動で `--review-status` を渡さなければ追記しない。** その時点では
    `cross-review` の合否が決まっておらず、収束しなかった実行が履歴に混ざる。
    """
    path, state = load_state(args.id)
    gate = state.setdefault("final_gate", {"fix_rounds": 0, "checks": []})
    standalone = not state.get("workflow_step")
    status = getattr(args, "review_status", None)
    if status is not None:
        gate["review_status"] = status
    state["phase"] = "done"
    state.setdefault("ended_at", statefile.now())
    ok = _gate_passed(state) and (not standalone or status == APPROVED)
    if state.get("history_written"):
        info("↻ 履歴へは追記済みです")
    elif not ok:
        why = (
            "cross-review の最終ステータスが渡されていない"
            if standalone and status is None
            else f"最終ゲートが通っていない（{gate.get('status') or gate.get('mode')} / {status}）"
        )
        info(f"ℹ 配分の履歴へは追記しません（{why}）")
    else:
        _append_history(state)
    statefile.save(path, state)


def _append_history(state: dict[str, Any]) -> None:
    try:
        target = allocation.history_path(run_metrics.metrics_dir(), str(state["repo"]))
        allocation.append_row(target, allocation.build_row(state))
    except OSError as exc:
        info(f"⚠ 配分の履歴へ追記できませんでした（{exc}）。進行は止めません")
        return
    state["history_written"] = True
    info(f"📈 配分の履歴へ 1 行追記しました: {target}")


def cmd_status(args: argparse.Namespace) -> None:
    """現在の状態を人が読む形で出す。"""
    _, state = load_state(args.id)
    print(f"# cross-refactoring rf{state['id']}（{state['repo']} #{state['current_pr']}）")
    print(f"ホスト: {state['host']}（{state['host_detection']}）")
    print(f"参加者（提案）: {' / '.join(state['runtimes'])} / 実装担当: {state.get('implementer')}")
    print(f"手順: {state.get('phase')} / 想定最大時間: {state.get('budget_minutes')} 分")
    print()
    print(_item_table(state))


def cmd_report(args: argparse.Namespace) -> None:
    """完了報告（AC26）。手順別の所要・想定最大時間との差・採用と見送りの件数（理由別）・
    全体のテスト・Jev の使用を並べる。"""
    path, state = load_state(args.id)
    _print_header(state)
    print()
    print("## 手順別の所要")
    print()
    print(_phase_table(state))
    print()
    print("## 改善項目")
    print()
    print(_item_table(state))
    print()
    _print_participants(state)
    print()
    _print_deferred(state)
    print()
    # 計画と同じ行を出す（AC20）。組み立ては `codemetrics_view` の 1 か所にある。
    print("\n".join(record_lines(state)).rstrip())
    print()
    _print_fixed_values()
    if args.metrics:
        print()
        print("## 種類別の件数と所要")
        print()
        print(_kind_table(state))
    # **最後の行に置く**（#662 の AC23）。作業ツリーを消した後に要約を探す手がかりになる。
    print()
    print(run_metrics.report_line(path, state, "cross-refactoring", summary_extra))


def _elapsed_seconds(state: dict[str, Any]) -> float:
    """`init` の開始から、最終ゲートの全体のテストの終わりまで（非機能の条件）。

    `cross-review` の所要は含めない。終わりは最終ゲートの最後のチェック、無ければ検証の
    終わり、それも無ければ今。
    """
    gate = state.get("final_gate") or {}
    checks = gate.get("checks") or []
    end = checks[-1].get("at") if checks else None
    end = end or phase_record(state, "verify").get("ended_at")
    return max(clock.seconds_between(state.get("started_at"), end or clock.now()) or 0.0, 0.0)


def _implementer_model_text(state: dict[str, Any]) -> str:
    """実装担当のモデルの表示。実測値があればそれを出し、無ければ指定値か分離の理由を出す（#759）。"""
    model = state.get("implementer_model") or {}
    requested, observed = model.get("requested"), model.get("observed")
    if observed:
        return f"{observed}（実測）"
    reason = models_lib.separation_reason(str(state.get("implementer") or ""), requested, observed)
    if reason:
        return f"{models_lib.label(None)}（分離: {reason}）"
    return f"{requested}（指定。実測できず）"


def _whole_test_lines(whole: dict[str, Any]) -> list[str]:
    """検証の中の全体のテストと、その原因の項目の行。"""
    lines: list[str] = []
    if whole.get("ran"):
        lines.append(
            f"- 検証の中の全体のテスト: 走らせた（危険フラグ {', '.join(whole.get('flags') or [])} / "
            f"{whole.get('status')}{_whole_detail(whole)}）"
        )
    elif whole.get("deferred"):
        deferred = whole["deferred"]
        lines.append(
            f"- 検証の中の全体のテスト: 最終ゲートへ寄せた（危険フラグ {', '.join(deferred.get('flags') or [])} / 項目 {', '.join(deferred.get('items') or [])}）"
        )
    else:
        lines.append("- 検証の中の全体のテスト: 走らせなかった（危険フラグが立たなかった）")
    if whole.get("culprit"):
        lines.append(f"- 検証の中の全体のテストの原因の項目: {culprit.verdict_line(whole['culprit'])}")
    return lines


def _final_gate_lines(gate: dict[str, Any]) -> list[str]:
    """最終ゲートの見分け・原因・打ち切りの後の取り消し・結果・静的解析の行。"""
    lines: list[str] = []
    triage = gate.get("triage") or {}
    if triage:
        lines.append(f"- 最終ゲートの見分け: {_triage_line(triage)}")
    if gate.get("culprit"):
        lines.append(f"- 最終ゲートの原因の項目: {culprit.verdict_line(gate['culprit'])}")
    if gate.get("stop_revert"):
        lines.append(f"- 打ち切りの後の取り消し: {stop_revert.record_line(gate['stop_revert'])}")
    lines.append(
        f"- 最終ゲート: {gate.get('mode') or '—'}（{gate.get('status') or '未実行'}"
        f"{' / 検証の結果を使い回した' if gate.get('whole_test_reused') else ''}"
        f" / 修正 {gate.get('fix_rounds', 0)} 回）"
    )
    for lint in gate.get("lint") or []:
        lines.append(f"- 最終ゲートの静的解析 {lint.get('suite')}: {lint.get('verdict')}（{lint.get('reason')}）")
    return lines


def _reassigned_lines(state: dict[str, Any]) -> list[str]:
    """結果なしの記録（`no_results`。#919）の振り替えと中断を 1 行ずつ。記録が無ければ空。"""
    lines = []
    for e in state.get("no_results") or []:
        if e.get("decision") not in (assignment.REASSIGN, assignment.ABORT):
            continue
        src = assignment.Assignee(str(e.get("seat") or ""), e.get("account") or None).label()
        dst = assignment.Assignee(str(e["to"]), e.get("to_account") or None).label() if e.get("to") else "なし（中断）"
        lines.append(f"- 振り替え: {e.get('step')}（試行 {e.get('attempt')}）: {src} → {dst}（{e.get('reason')}）")
    return lines


def _print_header(state: dict[str, Any]) -> None:
    budget_seconds = int(state.get("budget_minutes") or 0) * 60
    elapsed = _elapsed_seconds(state)
    judge = state.get("judge") or {}
    whole = state.get("whole_test") or {}
    gate = state.get("final_gate") or {}
    print(f"# cross-refactoring 実行報告 — {state['repo']} #{state['current_pr']}")
    print()
    print(f"- 件数: {counts_line(state)}")
    print(f"- 対象範囲: {', '.join(state['target_scope']) or '（未指定）'}")
    print(
        f"- 想定最大時間: {state.get('budget_minutes')} 分 / 所要: {elapsed / 60:.1f} 分"
        f"（差 {(budget_seconds - elapsed) / 60:+.1f} 分。cross-review を除く）"
    )
    print(f"- 実装担当: {state.get('implementer')}（{state.get('implementer_reason')}） / モデル: {_implementer_model_text(state)}")
    jev_line = "使った" if judge.get("kind") == "jev" else f"使わなかった（{judge.get('reason')}）"
    print(f"- 判断に Jev を: {jev_line} / 呼び出しの失敗 {judge.get('failures', 0)} 回")
    for line in strategy_lines(state):
        print(line)
    baseline = state.get("baseline_test") or {}
    print(f"- 着手前のテスト（{baseline.get('mode') or 'whole'}）: {baseline_line(baseline)} / 既存失敗 {existing_failures_line(baseline)}")
    for line in _whole_test_lines(whole) + _final_gate_lines(gate):
        print(line)
    if state.get("launch_failure"):
        print(f"- {launch.line(state['launch_failure'])}")
    print(f"- 監視が止めた手順: {_stopped_line(state)}")
    for line in _reassigned_lines(state):
        print(line)
    print(f"- 配分テーブル: {(state.get('plan') or {}).get('table_source') or '—'}")
    print(f"- 改修計画: {plan_reference(state)}")


def _stopped_line(state: dict[str, Any]) -> str:
    """手順の上限で監視が CLI を止めた手順（決定 23）。止めていなければ「なし」。"""
    stopped = [
        f"{name}（上限 {record['stopped'].get('timeout')} 秒）"
        for name, record in (state.get("phases") or {}).items()
        if isinstance(record, dict) and record.get("stopped")
    ]
    return " / ".join(stopped) or "なし"


def _print_fixed_values() -> None:
    """想定最大時間から導かず、固定のまま残した値（決定 24）。"""
    print("## 固定のまま残した値")
    print()
    for name, value, why in timeline.FIXED_VALUES:
        print(f"- {name}: {value}（{why}）")


# 全体のテストが落ちたときの結末（決定 22）。
_RESOLUTIONS = {
    "kept": "変更が原因の失敗は無く、取り消さなかった",
    "fixing": "直しの途中",
    "fixed": "直して通った",
    "narrowed": "直らず、原因の項目から順に取り消した",
    "reverted_all": "落ちたテストを見分けられず、危険フラグの項目をまとめて取り消した",
    "deferred": "全体テストを CI に任せる戦略のため、最終ゲートへ寄せた",
}


def _triage_line(triage: dict[str, Any]) -> str:
    """見分けの結果の 1 行。JUnit で見分けられなかったときは落とした理由。"""
    if triage.get("fallback_reason"):
        return f"全体の走らせ直しに落とした（{triage['fallback_reason']}）"
    return f"フレーキー {len(triage.get('flaky') or [])}・既存失敗 {len(triage.get('preexisting') or [])}・変更起因 {len(triage.get('caused') or [])}"


def _whole_detail(whole: dict[str, Any]) -> str:
    """落ちたときの見分けと結末。取り出せなかったときは理由を添える。"""
    if whole.get("status") != "fail":
        return ""
    if not whole.get("resolution"):
        return " / 危険フラグの項目を取り消した" if whole.get("reverted") else ""
    counts = f" / {_triage_line(whole)}" if whole.get("failed_tests") is not None or whole.get("fallback_reason") else ""
    return f"{counts} / {_RESOLUTIONS.get(whole['resolution'], whole['resolution'])}"


def _phase_table(state: dict[str, Any]) -> str:
    def minutes(seconds: Any) -> str:
        return "—" if seconds is None else f"{seconds / 60:.1f}"

    rows = [
        (name, minutes(phase_record(state, name).get("seconds"))) for name in ("propose", "plan", "add-tests", "implement", "verify", "fix")
    ]
    rows.append(("最終ゲートの全体のテスト", minutes((state.get("final_gate") or {}).get("whole_test_seconds"))))
    return mdtable.table_markdown(["手順", "所要（分）"], rows, align=["left", "right"])


def _item_table(state: dict[str, Any]) -> str:
    items = state.get("items") or []
    if not items:
        return "（改善項目なし）"
    rows = []
    for item in items:
        estimate = sum(float(v or 0) for v in (item.get("estimate") or {}).values())
        rows.append(
            (
                item["id"],
                item_label(item),
                str(item.get("smell")),
                str(item.get("technique")),
                str(item.get("tier")),
                f"{estimate:.1f}",
                status_label(state, item),
                ", ".join(item.get("danger") or []) or "—",
                str(item.get("fix_count", 0)),
            )
        )
    return mdtable.table_markdown(
        ["ID", "対象", "兆候", "手法", "等級", "見積り（分）", "状態", "危険フラグ", "修正"],
        rows,
        align=["left", "left", "left", "left", "left", "right", "left", "left", "right"],
    )


def _print_participants(state: dict[str, Any]) -> None:
    """「参加した者」の節（#727 の F6）。"""
    print("## 参加した者")
    print()
    p = state.get("participants") or {}

    def _names(values: Any) -> str:
        return " / ".join(values) if values else "なし"

    unavailable = p.get("unavailable") or {}
    failed = (
        " / ".join(f"{n}（{d}）" for n, d in unavailable.items())
        if unavailable
        else "確認を飛ばした（NDF_SKIP_AUTH_CHECK）"
        if p.get("probe_skipped")
        else "なし"
    )
    print(f"- 母集合: {_names(p.get('pool'))}")
    print(f"- 使える者: {_names(p.get('available'))}")
    print(f"- --exclude で外した者: {_names(p.get('excluded'))}")
    print(f"- --exclude で指定したが既定の母集合に無かった者: {_names(p.get('ignored_exclude'))}")
    print(f"- --include で足した者: {_names(p.get('included'))}")
    print(f"- 確認を通らなかった者: {failed}")
    changes = state.get("resume_changes") or []
    if not changes:
        print("- 再開で変えた値: なし")
        return
    print("- 再開で変えた値:")
    for c in changes:
        print(f"  - {c.get('at')} {c.get('field')}: {_change_value(c.get('from'))} → {_change_value(c.get('to'))}")


def _change_value(value: Any) -> str:
    if isinstance(value, dict) and "available" in value:
        return " / ".join(value.get("available") or []) or "なし"
    return str(value)


def _print_deferred(state: dict[str, Any]) -> None:
    """件数の行と、見送った提案の総数と理由別の件数。**内訳は改修計画にある**（#436 決定 6-b）。

    件数の行の「見送り」は見送った改善項目の数で、結果 JSON の `metrics.deferred` と同じ `ledger.tally` から出す。
    計画に入らなかった提案を含む数（`deferred_items`）は「見送った提案」として別の行に出す（#1660）。
    """
    deferred = state.get("deferred_items") or []
    counts = Counter(d.get("defer_reason") for d in deferred)
    t = ledger.tally(state)
    print("## 見送った提案と取り消した項目")
    print()
    # 最終ゲートを経ていない実行は、残った改善項目を採用と表さない（I8）。数え方は `ledger.tally` が持つ
    adopted = f"{t.adopted} 件" if t.confirmed else f"未確定（最終ゲートを経ていない。残った改善項目 {t.unconfirmed} 件）"
    print(f"- 採用: {adopted} / 取り消し: {t.reverted} 件 / 見送り: {t.deferred} 件")
    by_reason = " / ".join(f"{r} {counts.get(r, 0)}" for r in DEFER_REASONS)
    print(f"- 見送った提案: {len(deferred)} 件（理由別: {by_reason}）")
    print(f"- 内訳: 改修計画にある — {plan_reference(state)}")


def _kind_table(state: dict[str, Any]) -> str:
    """種類別の件数と所要（実装計画 I10）。履歴へ書く行と同じ値。"""
    row = allocation.build_row(state)
    verify, fix = row.get("verify") or {}, row.get("fix") or {}
    rows = [(kind, value.get("count"), value.get("seconds")) for kind, value in sorted((row.get("kinds") or {}).items())]
    rows += [("verify", verify.get("items"), verify.get("seconds")), ("fix", fix.get("launches"), fix.get("seconds"))]
    return mdtable.table_markdown(["種類", "件数", "秒"], [(k, str(c), str(s)) for k, c, s in rows], align=["left", "right", "right"])
