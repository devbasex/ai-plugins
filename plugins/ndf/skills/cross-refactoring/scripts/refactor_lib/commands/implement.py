"""テストの追加と実装の取り込み（`merge-tests` / `merge-implement`、#933 の F4 F5）。

**検証の材料は git から取る。** 結果ファイルの申告は使わない。項目とコミットの対応は
トレーラー `Item-Id` だけで決め（実装計画 I4）、所要はコミットの時刻で測る（決定 8）。
**コミットの時刻で項目を見送らない**（項目ごとの期限は持たない。#1743 決定 9）。

| 見つけたもの | 扱い |
| --- | --- |
| どの項目にも属さないコミット（`Item-Id` が無い・改修計画に無い） | そのコミットだけを取り消す |
| 手順を外れたコミット（範囲の外・トレーラー欠け・2 コミット以上・差分予算・文言固定テスト・期待値の変更） | 項目を取り消す（`status: reverted`。見送りには入れない。I5） |
| コミットの無い項目 | 持ち越し（`carried`）にして項目のコミットを取り消す。次の `readopt` が採り直すか `not_done` で見送る（I15） |
| 足したテストが今のコードで落ちた項目 | `test_failed` で見送り、テストのコミットを取り消す（決定 13） |

取り込みの対象は今の巡（`readopt.round`）の未了の項目だけで、取り消し済みの判定も巡ごとに働く（#1743 決定 10）。
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

import project_decl
import statefile
import test_strategy as ts

from .. import clock, die, info, launch, ledger, targets, timeline
from ..gitfacts import (
    collect_commit_facts,
    commit_time,
    commits_in_range,
    discard_impl_leftovers,
    note_stopped,
    record_observed_model,
    safe_int,
    tracked_markdown,
)
from ..items import (
    CARRIED,
    DEFERRED,
    IMPLEMENTED,
    PLANNED,
    TESTED,
    current_round,
    defer,
    find_item,
    in_current_round,
    item_label,
    live_items,
)
from ..paths import work_dir
from ..paths import git_out, load_state
from ..phases import finish_phase, phase_record
from ..undo import drop, resume_pending_drop
from ..verify import (
    verify_commit_basics,
    verify_diff_budget,
    collect_test_changes,
    doc_wording_tests,
    pending_test_judgements,
    verify_test_changes,
)
from ..vocabulary import DEFER_TEST_FAILED


@dataclass
class Intake:
    """1 つの手順分の取り込みの結論。取り消しは最後に 1 度だけまとめて行う。"""

    extra: list[str] = field(default_factory=list)  # どの項目にも属さないコミット
    rejected: dict[str, str] = field(default_factory=dict)  # 項目 → 手順を外れた理由
    carried: dict[str, str] = field(default_factory=dict)  # 項目 → 持ち越しの理由（コミットが無い）
    test_failed: dict[str, str] = field(default_factory=dict)  # 項目 → テストの結果
    accepted: dict[str, dict[str, Any]] = field(default_factory=dict)  # 項目 → コミットの事実


def _phase_commits(state: dict[str, Any], phase: str) -> list[str]:
    """手順の起点から HEAD までのコミットを**古い順**で返す。確定できなければ中断。"""
    work = work_dir(state)
    base = phase_record(state, phase).get("base_sha")
    head = git_out(work, ["rev-parse", "HEAD"]) or ""
    ordered = commits_in_range(work, base, head)
    if ordered is None:
        die(f"{phase} の範囲を確定できません（起点 {base} / HEAD {head}）。検証できない変更は採りません")
        raise SystemExit(4)
    return list(reversed(ordered))


def _facts(state: dict[str, Any], shas: list[str]) -> list[dict[str, Any]]:
    """コミットの事実（トレーラー・ファイル・差分行数・テストの差分）。テストは走らせない。"""
    return collect_commit_facts(
        work_dir(state),
        shas,
        set(shas),
        "",
        state["head_branch"],
    )


def _group_by_item(
    state: dict[str, Any],
    facts: list[dict[str, Any]],
    allowed: Any,
    intake: Intake,
) -> dict[str, list[dict[str, Any]]]:
    """コミットを `Item-Id` で項目へ振り分ける。属さないものは `intake.extra` へ。"""
    by_item: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        item_id = str((fact.get("trailers") or {}).get("Item-Id") or "").strip()
        item = find_item(state, item_id, required=False) if item_id else None
        if not ledger.is_live(item) or not allowed(item):
            intake.extra.append(fact["sha"])
            continue
        by_item.setdefault(item_id, []).append(fact)
    return by_item


def _record_seconds(
    state: dict[str, Any],
    phase: str,
    key: str,
    accepted: dict[str, dict[str, Any]],
) -> None:
    """項目の所要。起点は最初の項目なら手順の開始、2 件目からは直前の項目のコミット（設計）。"""
    previous = phase_record(state, phase).get("started_at")
    ordered = sorted(accepted.items(), key=lambda kv: clock.parse(kv[1]["time"]) or clock.now())
    for item_id, fact in ordered:
        seconds = clock.seconds_between(previous, fact["time"])
        find_item(state, item_id)["seconds"][key] = None if seconds is None else round(max(seconds, 0.0), 1)
        previous = fact["time"]


def _settle(
    path: pathlib.Path,
    state: dict[str, Any],
    intake: Intake,
    label: str,
) -> None:
    """取り消しと見送りを 1 度にまとめて行う。"""
    for item_id, reason in intake.rejected.items():
        find_item(state, item_id)["failure_reason"] = reason
    for item_id, reason in {**intake.carried, **intake.test_failed}.items():
        find_item(state, item_id)["failure_reason"] = reason
    targets = sorted({*intake.rejected, *intake.carried, *intake.test_failed})
    # **同じ巡の同じ取り込みの取り消しを 2 度行わない。** 途中で落ちた取り消しは入口の
    # `resume_pending_drop` がやり直している。済んだ取り消しをもう一度行うと、範囲の
    # 逆再生と積み直しで履歴だけが伸びる。巡が違えば別の取り込みである（#1743）。
    this_round = current_round(state)
    done = any(d.get("reason") == label and int(d.get("round") or 1) == this_round for d in state.get("drops") or [])
    if (targets or intake.extra) and not done:
        drop(path, state, targets, label)
    # 見送った項目は `drop` が付けた `reverted` を `deferred` へ改める。取り消しと見送りの
    # 両方に数えると、報告の件数の和が項目の数を超える（設計の状態遷移）。
    for item_id, reason in intake.test_failed.items():
        item = find_item(state, item_id)
        defer(state, item, DEFER_TEST_FAILED, reason)
        item["status"] = DEFERRED
    # コミットの無い項目は見送らずに持ち越す（`readopt` が採り直すか `not_done` にする。I15）
    for item_id in intake.carried:
        find_item(state, item_id)["status"] = CARRIED
    for item_id, reason in intake.rejected.items():
        info(f"❌ {item_id} {item_label(find_item(state, item_id))}: {reason}")
    if intake.extra:
        info(f"↩ どの項目にも属さないコミット {len(intake.extra)} 件を取り消しました")


def _remember(path: pathlib.Path, state: dict[str, Any], phase: str, intake: Intake) -> None:
    """取り込みの結論を、取り消しの前に保存する。

    **取り消しの途中で落ちて再開すると、範囲に取り消しと積み直しのコミットが混ざる。**
    積み直したコミットは元と同じ `Item-Id` を持つため、読み直すと 1 項目に 2 コミットと
    数えてしまう。結論を先に残し、再開ではそれを使って取り消しと見送りだけをやり直す。
    """
    record = state.setdefault("phases", {}).setdefault(phase, {})
    record["intake"] = {
        "extra": list(intake.extra),
        "rejected": dict(intake.rejected),
        "carried": dict(intake.carried),
        "test_failed": dict(intake.test_failed),
    }
    statefile.save(path, state)


def _recalled(state: dict[str, Any], phase: str) -> Optional[Intake]:
    saved = phase_record(state, phase).get("intake")
    if not isinstance(saved, dict):
        return None
    return Intake(
        extra=list(saved.get("extra") or []),
        rejected=dict(saved.get("rejected") or {}),
        carried=dict(saved.get("carried") or {}),
        test_failed=dict(saved.get("test_failed") or {}),
    )


def _prepare(path: pathlib.Path, state: dict[str, Any]) -> None:
    """取り込みの前の片づけ。やり残した取り消しを先に済ませ、未コミットの変更を捨てる。

    **push はしない。** 公開は最終ゲートへ入る時点にまとめる（#1399）。
    """
    discard_impl_leftovers(state, work_dir(state))
    resume_pending_drop(path, state)


def _finish(path: pathlib.Path, state: dict[str, Any], phase: str) -> None:
    """残る項目が 0 件なら検証を飛ばして採り直しの判定へ移す（終了コード 2）。最終ゲートへ移すのは `readopt` だけ。"""
    finish_phase(state, phase)
    if not live_items(state):
        state["phase"] = "readopt"
    statefile.save(path, state)
    if not live_items(state):
        info("残る項目が 0 件のため、検証を飛ばして採り直しの判定へ進みます")
        sys.exit(2)


# ---------- テストの追加 ----------


def _added_test_commands(state: dict[str, Any], item: dict[str, Any], files: list[str]) -> list[str]:
    """足したテストを今のコードで走らせるコマンド（テストの種別の suite だけ。suite ごとに 1 つ。#1483 I9）。

    項目の範囲テストが対象から組み立てたものならそれを使う。ラウンドテストをそのまま使う項目は、
    戦略に雛形（`scope_command`）があれば足したテストのファイルを `{paths}` へ入れ、無ければ
    ラウンドテストのうちテストの種別のものをそのまま走らせる。静的解析の結果では判定しない。
    """
    own = [r.command for r in targets.item_runs(item) if r.kind == ts.TEST]
    if item.get("command_source") == "targets":
        return own
    work = work_dir(state)
    strategy = timeline.strategy_of(state)
    tests = [f for f in files if targets.valid_targets([f], work, list(state.get("target_scope") or []))]
    if tests:
        built = targets.scope_runs_for(strategy, tests)
        if built:
            return [r.command for r in built]
    return own


def _run_added_tests(path: pathlib.Path, state: dict[str, Any], intake: Intake) -> None:
    """足したテストが今のコードで通るかを確かめる（決定 13）。同じコマンドの並びは 1 回だけ走らせる。

    起動の失敗なら項目を落とさずに止まる（#1483 I8）。
    """
    work = work_dir(state)
    timeout = timeline.state_test_timeout(state)
    results: dict[tuple[str, ...], bool] = {}
    for item_id, fact in intake.accepted.items():
        commands = _added_test_commands(state, find_item(state, item_id), list(fact.get("files") or []))
        if not commands:
            continue
        key = tuple(commands)
        if key not in results:
            log = pathlib.Path(state["tmp_dir"]) / f"add-tests-{item_id}.log"
            result, last = targets.run_commands(commands, work, timeout, log)
            if result.launch_failed:
                launch.stop(path, state, "tests", last, result, log)
            results[key] = result.status == ts.PASSED
        if not results[key]:
            intake.test_failed[item_id] = f"足したテストが今のコードで通りません（{targets.command_text(commands)}）"
            intake.extra.append(fact["sha"])


def _intake_phase(
    state: dict[str, Any],
    live_predicate: Any,
    problem_fn: Any,
    phase: str,
    intake: Intake,
) -> Intake:
    label = "テストの追加" if phase == "add-tests" else "実装"
    facts = _facts(state, _phase_commits(state, phase))
    for fact in facts:
        fact["time"] = commit_time(work_dir(state), fact["sha"])
    by_item = _group_by_item(state, facts, live_predicate, intake)
    for item in live_items(state):
        if not live_predicate(item):
            continue
        commits = by_item.get(item["id"]) or []
        if not commits:
            intake.carried[item["id"]] = f"{label}の終わりまでにコミットが無い"
            continue
        problem = problem_fn(item, commits)
        if problem:
            intake.rejected[item["id"]] = problem
        else:
            intake.accepted[item["id"]] = commits[0]
            continue
        # 採らないコミットは項目の記録に載らない。取り消しの対象として明示する。
        intake.extra.extend(c["sha"] for c in commits)
    return intake


def _intake_tests(state: dict[str, Any]) -> Intake:
    intake = Intake()
    scope = list(state.get("target_scope") or [])
    tracked = tracked_markdown(work_dir(state))
    return _intake_phase(
        state,
        # 今の巡で採った、テストを足す未了の項目だけ。前の巡で検証を終えた項目のコミットに触れない（#1743）
        lambda item: bool(item.get("tests")) and item.get("status") == PLANNED and in_current_round(state, item),
        lambda _item, commits: _test_commit_problem(commits, scope, tracked, state),
        "add-tests",
        intake,
    )


def _test_commit_problem(
    commits: list[dict[str, Any]],
    scope: list[str],
    tracked: list[str],
    state: dict[str, Any],
) -> Optional[str]:
    """テストの追加のコミットが手順を満たすか。**テスト以外のファイルを触らない。**"""
    from ..gitfacts import is_test_path

    if len(commits) > 1:
        return f"テストの追加が {len(commits)} コミットあります（1 項目 = 1 コミット）"
    commit = commits[0]
    problem = verify_commit_basics(commit, scope, "コミットが範囲にありません", check_test=False)
    if problem:
        return problem
    others = [f for f in commit.get("files") or [] if not is_test_path(f)]
    if others:
        return f"テストの追加がテスト以外のファイルを変えています（{', '.join(others[:5])}）"
    hits = _wording_hits(commits, tracked, state)
    if hits:
        return _doc_wording_reason(hits)
    return None


def cmd_merge_tests(args: argparse.Namespace) -> None:
    """テストの追加を取り込む。

    終了コード: 0 = 取り込んだ / 2 = 残る項目 0 件（採り直しの判定へ）/ 4 = 範囲を確定できない。
    """
    path, state = load_state(args.id)
    _prepare(path, state)
    if phase_record(state, "add-tests").get("ended_at"):
        info("↻ テストの追加は取り込み済みです")
        if not live_items(state):
            sys.exit(2)
        return
    intake = _recalled(state, "add-tests")
    if intake is None:
        record_observed_model(state, str(state["implementer"]), "add-tests")
        note_stopped(state, str(state["implementer"]), "add-tests")
        intake = _intake_tests(state)
        _run_added_tests(path, state, intake)
        for item_id, fact in intake.accepted.items():
            if item_id in intake.test_failed:
                continue
            item = find_item(state, item_id)
            item["commits"]["test"] = fact["sha"]
            item["status"] = TESTED
        _record_seconds(state, "add-tests", "test", {k: v for k, v in intake.accepted.items() if k not in intake.test_failed})
        _remember(path, state, "add-tests", intake)
    else:
        info("↻ テストの追加の結論は記録済みです。取り消しと見送りだけをやり直します")
    _settle(path, state, intake, "テストの追加の取り込み")
    kept = [i for i in live_items(state) if i.get("status") == TESTED]
    info(
        f"テストの追加: 採用 {len(kept)} 件 / test_failed {len(intake.test_failed)} 件 / "
        f"持ち越し {len(intake.carried)} 件 / 手順違反 {len(intake.rejected)} 件"
    )
    _finish(path, state, "add-tests")


# ---------- 実装 ----------


def _wording_hits(commits: list[dict[str, Any]], tracked: list[str], state: dict[str, Any]) -> list[tuple[str, str]]:
    """`.md` の文言テストの拒否。宣言の `ndf_policies.reject_md_wording_tests` が `true` のときだけ当てる（決定 8）。"""
    work = work_dir(state)
    if not project_decl.policy(work, "reject_md_wording_tests"):
        return []
    return doc_wording_tests(commits, tracked, work)


def _doc_wording_reason(hits: list[tuple[str, int]]) -> str:
    return "文書の文言を固定するテストは足さない（" + "、".join(f"{p}: {l}" for p, l in hits) + "）"


def _implement_problem(
    item: dict[str, Any],
    commits: list[dict[str, Any]],
    scope: list[str],
    tracked: list[str],
    state: dict[str, Any],
) -> Optional[str]:
    """実装のコミットが手順を満たすか。適用のチェック（v10.17.x）を項目の単位で掛ける。"""
    if len(commits) > 1:
        return f"実装が {len(commits)} コミットあります（1 改善項目 = 1 コミット）"
    problem = verify_commit_basics(commits[0], scope, "コミットが範囲にありません", check_test=False)
    if problem:
        return problem
    problem = verify_test_changes(collect_test_changes(commits))
    if problem:
        return problem
    hits = _wording_hits(commits, tracked, state)
    if hits:
        return _doc_wording_reason(hits)
    return verify_diff_budget([item], commits)


def _intake_implement(state: dict[str, Any]) -> Intake:
    intake = Intake()
    work = work_dir(state)
    scope = list(state.get("target_scope") or [])
    tracked = tracked_markdown(work)
    return _intake_phase(
        state,
        lambda item: item.get("status") in (PLANNED, TESTED),
        lambda item, commits: _implement_problem(item, commits, scope, tracked, state),
        "implement",
        intake,
    )


def cmd_merge_implement(args: argparse.Namespace) -> None:
    """実装を取り込む。

    終了コード: 0 = 取り込んだ / 2 = 残る項目 0 件（採り直しの判定へ）/ 4 = 範囲を確定できない。

    **テストの差分のうち一次の判定で決まらないものは、最終ゲートのレビューへ引き継ぐ**
    （`review_test_judgements`。決定 25）。改修計画の後に判断のために LLM を起動しない。
    """
    path, state = load_state(args.id)
    _prepare(path, state)
    if phase_record(state, "implement").get("ended_at"):
        info("↻ 実装は取り込み済みです")
        if not live_items(state):
            sys.exit(2)
        return
    intake = _recalled(state, "implement")
    if intake is None:
        record_observed_model(state, str(state["implementer"]), "implement")
        note_stopped(state, str(state["implementer"]), "implement")
        intake = _intake_implement(state)
        for item_id, fact in intake.accepted.items():
            item = find_item(state, item_id)
            item["commits"]["implement"] = fact["sha"]
            item["status"] = IMPLEMENTED
            item["diff_lines"] = safe_int(fact.get("diff_lines"))
            pending = pending_test_judgements([fact])
            if pending:
                item["review_test_judgements"] = pending
        _record_seconds(state, "implement", "implement", intake.accepted)
        _remember(path, state, "implement", intake)
    else:
        info("↻ 実装の結論は記録済みです。取り消しと見送りだけをやり直します")
    _settle(path, state, intake, "実装の取り込み")
    carried = [i["id"] for i in live_items(state) if i.get("review_test_judgements")]
    info(f"実装: 採用 {len(intake.accepted)} 件 / 持ち越し {len(intake.carried)} 件 / 手順違反 {len(intake.rejected)} 件")
    if carried:
        info(
            f"{len(carried)} 件のテストの差分は機械で決まらないため、最終ゲートのレビューへ"
            f"引き継ぎます（改修計画に載ります）: {', '.join(carried)}"
        )
    _finish(path, state, "implement")
