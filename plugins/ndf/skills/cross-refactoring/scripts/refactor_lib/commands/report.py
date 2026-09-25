"""実行の終わりと報告（`finalize` / `status` / `report`、#933 の F8 と AC26）。"""
from __future__ import annotations

import argparse
from collections import Counter
from typing import Any

import models as models_lib
import run_metrics
import statefile

from .. import allocation, clock, info, timeline
from ..items import item_label
from ..measure import summary_extra
from ..outbound import plan_reference
from ..plan import baseline_line
from ..paths import load_state
from ..phases import phase_record
from ..vocabulary import DEFER_REASONS

# `cross-review` の最終ステータスのうち、追記してよいもの。
APPROVED = "approved"


def _gate_passed(state: dict[str, Any]) -> bool:
    return (state.get("final_gate") or {}).get("status") == "passed"


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
        why = ("cross-review の最終ステータスが渡されていない" if standalone and status is None
               else f"最終ゲートが通っていない（{gate.get('status') or gate.get('mode')} / {status}）")
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


def _print_header(state: dict[str, Any]) -> None:
    budget_seconds = int(state.get("budget_minutes") or 0) * 60
    elapsed = _elapsed_seconds(state)
    judge = state.get("judge") or {}
    whole = state.get("whole_test") or {}
    gate = state.get("final_gate") or {}
    print(f"# cross-refactoring 実行報告 — {state['repo']} #{state['current_pr']}")
    print()
    print(f"- 対象範囲: {', '.join(state['target_scope']) or '（未指定）'}")
    print(f"- 想定最大時間: {state.get('budget_minutes')} 分 / 所要: {elapsed / 60:.1f} 分"
          f"（差 {(budget_seconds - elapsed) / 60:+.1f} 分。cross-review を除く）")
    print(f"- 実装担当: {state.get('implementer')}（{state.get('implementer_reason')}）"
          f" / モデル: {models_lib.label((state.get('implementer_model') or {}).get('requested'))}")
    jev_line = "使った" if judge.get("kind") == "jev" else f"使わなかった（{judge.get('reason')}）"
    print(f"- 判断に Jev を: {jev_line} / 呼び出しの失敗 {judge.get('failures', 0)} 回")
    print(f"- 着手前の全体のテスト: {baseline_line(state.get('baseline_test') or {})}")
    if whole.get("ran"):
        print(f"- 検証の中の全体のテスト: 走らせた（危険フラグ {', '.join(whole.get('flags') or [])} / "
              f"{whole.get('status')}{_whole_detail(whole)}）")
    else:
        print("- 検証の中の全体のテスト: 走らせなかった（危険フラグが立たなかった）")
    print(f"- 最終ゲート: {gate.get('mode') or '—'}（{gate.get('status') or '未実行'}"
          f"{' / 検証の結果を使い回した' if gate.get('whole_test_reused') else ''}"
          f" / 修正 {gate.get('fix_rounds', 0)} 回）")
    print(f"- 監視が止めた手順: {_stopped_line(state)}")
    print(f"- 配分テーブル: {(state.get('plan') or {}).get('table_source') or '—'}")
    print(f"- 改修計画: {plan_reference(state)}")


def _stopped_line(state: dict[str, Any]) -> str:
    """手順の上限で監視が CLI を止めた手順（決定 23）。止めていなければ「なし」。"""
    stopped = [f"{name}（上限 {record['stopped'].get('timeout')} 秒）"
               for name, record in (state.get("phases") or {}).items()
               if isinstance(record, dict) and record.get("stopped")]
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
    "narrowed": "直らず、危険フラグの項目を新しい順に取り消した",
    "reverted_all": "落ちたテストを取り出せず、危険フラグの項目をまとめて取り消した",
}


def _whole_detail(whole: dict[str, Any]) -> str:
    """落ちたときの見分けと結末。取り出せなかったときは理由を添える。"""
    if whole.get("status") != "fail":
        return ""
    if not whole.get("resolution"):
        return " / 危険フラグの項目を取り消した" if whole.get("reverted") else ""
    counts = ""
    if whole.get("failed_tests") is not None:
        counts = (f" / 揺れ {len(whole.get('flaky') or [])}・元からの失敗 "
                  f"{len(whole.get('preexisting') or [])}・変更が原因 {len(whole.get('caused') or [])}")
    why = f"（{whole['unparsed_reason']}）" if whole.get("unparsed_reason") else ""
    return f"{counts} / {_RESOLUTIONS.get(whole['resolution'], whole['resolution'])}{why}"


def _phase_table(state: dict[str, Any]) -> str:
    lines = ["| 手順 | 所要（分） |", "| --- | ---: |"]
    for name in ("propose", "plan", "add-tests", "implement", "verify", "fix"):
        record = phase_record(state, name)
        seconds = record.get("seconds")
        lines.append(f"| {name} | {'—' if seconds is None else f'{seconds / 60:.1f}'} |")
    final = (state.get("final_gate") or {}).get("whole_test_seconds")
    lines.append(f"| 最終ゲートの全体のテスト | {'—' if final is None else f'{final / 60:.1f}'} |")
    return "\n".join(lines)


def _item_table(state: dict[str, Any]) -> str:
    items = state.get("items") or []
    if not items:
        return "（改善項目なし）"
    lines = [
        "| ID | 対象 | 兆候 | 手法 | 等級 | 見積り（分） | 状態 | 危険フラグ | 修正 |",
        "| --- | --- | --- | --- | --- | ---: | --- | --- | ---: |",
    ]
    for item in items:
        estimate = sum(float(v or 0) for v in (item.get("estimate") or {}).values())
        lines.append(
            f"| {item['id']} | {item_label(item)} | {item.get('smell')} | {item.get('technique')} | "
            f"{item.get('tier')} | {estimate:.1f} | {item.get('status')} | "
            f"{', '.join(item.get('danger') or []) or '—'} | {item.get('fix_count', 0)} |"
        )
    return "\n".join(lines)


def _print_participants(state: dict[str, Any]) -> None:
    """「参加した者」の節（#727 の F6）。"""
    print("## 参加した者")
    print()
    p = state.get("participants") or {}

    def _names(values: Any) -> str:
        return " / ".join(values) if values else "なし"

    unavailable = p.get("unavailable") or {}
    failed = (" / ".join(f"{n}（{d}）" for n, d in unavailable.items()) if unavailable
              else "確認を飛ばした（NDF_SKIP_AUTH_CHECK）" if p.get("probe_skipped") else "なし")
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
    """見送りの件数（理由別）。**内訳は改修計画にある**（#436 決定 6-b）。"""
    deferred = state.get("deferred_items") or []
    counts = Counter(d.get("defer_reason") for d in deferred)
    reverted = [i for i in state.get("items") or [] if i.get("status") == "reverted"]
    print("## 見送った提案と取り消した項目")
    print()
    print(f"- 採用: {sum(1 for i in state.get('items') or [] if i.get('status') == 'verified')} 件"
          f" / 取り消し: {len(reverted)} 件 / 見送り: {len(deferred)} 件")
    print("- 見送りの理由別: " + " / ".join(f"{r} {counts.get(r, 0)}" for r in DEFER_REASONS))
    print(f"- 内訳: 改修計画にある — {plan_reference(state)}")


def _kind_table(state: dict[str, Any]) -> str:
    """種類別の件数と所要（実装計画 I10）。履歴へ書く行と同じ値。"""
    row = allocation.build_row(state)
    lines = ["| 種類 | 件数 | 秒 |", "| --- | ---: | ---: |"]
    for kind, value in sorted((row.get("kinds") or {}).items()):
        lines.append(f"| {kind} | {value.get('count')} | {value.get('seconds')} |")
    verify, fix = row.get("verify") or {}, row.get("fix") or {}
    lines.append(f"| verify | {verify.get('items')} | {verify.get('seconds')} |")
    lines.append(f"| fix | {fix.get('launches')} | {fix.get('seconds')} |")
    return "\n".join(lines)
