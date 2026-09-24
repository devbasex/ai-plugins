"""テストの追加と実装の取り込み（`merge-tests` / `merge-implement`、#933 の F4 F5）。

**検証の材料は git から取る。** 結果ファイルの申告は使わない。項目とコミットの対応は
トレーラー `Item-Id` だけで決め（実装計画 I4）、所要と締め切りはコミットの時刻で
判定する（決定 8・決定 12）。

| 見つけたもの | 扱い |
| --- | --- |
| どの項目にも属さないコミット（`Item-Id` が無い・計画に無い） | そのコミットだけを取り消す |
| 手順を外れたコミット（範囲の外・トレーラー欠け・2 コミット以上・差分予算・文言固定テスト・期待値の変更） | 項目を取り消す（`status: reverted`。見送りには入れない。I5） |
| コミットの無い項目・完了の締め切りを過ぎてコミットした項目 | `not_done` で見送り、項目のコミットを取り消す（AC12） |
| 足したテストが今のコードで落ちた項目 | `test_failed` で見送り、テストのコミットを取り消す（決定 13） |
"""
from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

import statefile

from .. import clock, die, info, testcmd, timeline
from ..gitfacts import (
    collect_commit_facts,
    commit_time,
    commits_in_range,
    discard_impl_leftovers,
    flush_pending_push,
    push_with_retry_marker,
    note_stopped,
    record_observed_model,
    run_with_timeout,
    safe_int,
    tracked_markdown,
)
from ..items import (
    DEFERRED,
    IMPLEMENTED,
    LIVE,
    PLANNED,
    TESTED,
    defer,
    find_item,
    item_label,
    live_items,
)
from ..paths import work_dir
from ..paths import git_out, load_state
from ..phases import finish_phase
from ..undo import drop, resume_pending_drop
from ..verify import (
    verify_commit_basics,
    verify_diff_budget,
    collect_test_changes,
    doc_wording_tests,
    pending_test_judgements,
    verify_test_changes,
)
from ..vocabulary import DEFER_NOT_DONE, DEFER_TEST_FAILED


@dataclass
class Intake:
    """1 フェーズ分の取り込みの結論。取り消しは最後に 1 度だけまとめて行う。"""

    extra: list[str] = field(default_factory=list)            # どの項目にも属さないコミット
    rejected: dict[str, str] = field(default_factory=dict)     # 項目 → 手順を外れた理由
    not_done: dict[str, str] = field(default_factory=dict)     # 項目 → 締め切りの理由
    test_failed: dict[str, str] = field(default_factory=dict)  # 項目 → テストの結果
    accepted: dict[str, dict[str, Any]] = field(default_factory=dict)  # 項目 → コミットの事実


def _phase_commits(state: dict[str, Any], phase: str) -> list[str]:
    """フェーズの起点から HEAD までのコミットを**古い順**で返す。確定できなければ中断。"""
    work = work_dir(state)
    base = ((state.get("phases") or {}).get(phase) or {}).get("base_sha")
    head = git_out(work, ["rev-parse", "HEAD"]) or ""
    ordered = commits_in_range(work, base, head)
    if ordered is None:
        die(f"{phase} の範囲を確定できません（起点 {base} / HEAD {head}）。"
            "検証できない変更は採りません")
        raise SystemExit(4)
    return list(reversed(ordered))


def _facts(state: dict[str, Any], shas: list[str]) -> list[dict[str, Any]]:
    """コミットの事実（トレーラー・ファイル・差分行数・テストの差分）。テストは走らせない。"""
    return collect_commit_facts(
        work_dir(state), shas, set(shas), "", state["head_branch"],
    )


def _group_by_item(
    state: dict[str, Any], facts: list[dict[str, Any]], allowed: Any, intake: Intake,
) -> dict[str, list[dict[str, Any]]]:
    """コミットを `Item-Id` で項目へ振り分ける。属さないものは `intake.extra` へ。"""
    by_item: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        item_id = str((fact.get("trailers") or {}).get("Item-Id") or "").strip()
        item = find_item(state, item_id, required=False) if item_id else None
        if item is None or item.get("status") not in LIVE or not allowed(item):
            intake.extra.append(fact["sha"])
            continue
        by_item.setdefault(item_id, []).append(fact)
    return by_item


def _deadline_passed(item: dict[str, Any], key: str, estimate_key: str, when: Optional[str]) -> bool:
    """完了の締め切り（着手の締め切り + その項目の見積り）を過ぎてコミットしたか（AC12）。

    **着手の時刻は git から求められないため、完了の時刻で保守的に判定する**（設計）。
    """
    start = clock.parse(item.get(key))
    done = clock.parse(when)
    if start is None or done is None:
        return False
    limit = start + _dt.timedelta(minutes=float((item.get("estimate") or {}).get(estimate_key) or 0))
    return done > limit


def _record_seconds(
    state: dict[str, Any], phase: str, key: str, accepted: dict[str, dict[str, Any]],
) -> None:
    """項目の所要。起点は最初の項目ならフェーズの開始、2 件目からは直前の項目のコミット（設計）。"""
    previous = ((state.get("phases") or {}).get(phase) or {}).get("started_at")
    ordered = sorted(accepted.items(), key=lambda kv: clock.parse(kv[1]["time"]) or clock.now())
    for item_id, fact in ordered:
        seconds = clock.seconds_between(previous, fact["time"])
        find_item(state, item_id)["seconds"][key] = None if seconds is None else round(max(seconds, 0.0), 1)
        previous = fact["time"]


def _settle(
    path: pathlib.Path, state: dict[str, Any], intake: Intake, label: str,
) -> None:
    """取り消しと見送りを 1 度にまとめて行う。"""
    for item_id, reason in intake.rejected.items():
        find_item(state, item_id)["failure_reason"] = reason
    for item_id, reason in {**intake.not_done, **intake.test_failed}.items():
        find_item(state, item_id)["failure_reason"] = reason
    targets = sorted({*intake.rejected, *intake.not_done, *intake.test_failed})
    # **同じ取り込みの取り消しを 2 度行わない。** 途中で落ちた取り消しは入口の
    # `resume_pending_drop` がやり直している。済んだ取り消しをもう一度行うと、範囲の
    # 逆再生と積み直しで履歴だけが伸びる。
    done = any(d.get("reason") == label for d in state.get("drops") or [])
    if (targets or intake.extra) and not done:
        drop(path, state, targets, label, intake.extra)
    # 見送った項目は `drop` が付けた `reverted` を `deferred` へ改める。取り消しと見送りの
    # 両方に数えると、報告の件数の和が項目の数を超える（設計の状態遷移）。
    for reason_code, found in ((DEFER_NOT_DONE, intake.not_done),
                               (DEFER_TEST_FAILED, intake.test_failed)):
        for item_id, reason in found.items():
            item = find_item(state, item_id)
            defer(state, item, reason_code, reason)
            item["status"] = DEFERRED
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
        "extra": list(intake.extra), "rejected": dict(intake.rejected),
        "not_done": dict(intake.not_done), "test_failed": dict(intake.test_failed),
    }
    statefile.save(path, state)


def _recalled(state: dict[str, Any], phase: str) -> Optional[Intake]:
    saved = ((state.get("phases") or {}).get(phase) or {}).get("intake")
    if not isinstance(saved, dict):
        return None
    return Intake(extra=list(saved.get("extra") or []), rejected=dict(saved.get("rejected") or {}),
                  not_done=dict(saved.get("not_done") or {}),
                  test_failed=dict(saved.get("test_failed") or {}))


def _prepare(path: pathlib.Path, state: dict[str, Any]) -> None:
    """取り込みの前の片づけ。やり残した取り消しと公開を先に済ませ、未コミットの変更を捨てる。"""
    discard_impl_leftovers(state, work_dir(state))
    resume_pending_drop(path, state)
    flush_pending_push(path, state, state)


def _finish(path: pathlib.Path, state: dict[str, Any], phase: str) -> None:
    finish_phase(state, phase)
    if not live_items(state):
        state["phase"] = "final"
    statefile.save(path, state)
    if state.get("pending_push"):
        push_with_retry_marker(path, state, state)
    if not live_items(state):
        info("残る項目が 0 件のため、最終ゲートへ進みます")
        sys.exit(2)


# ---------- テストの追加 ----------

def _test_words(state: dict[str, Any], item: dict[str, Any], files: list[str]) -> Optional[list[str]]:
    """足したテストを今のコードで走らせる語の並び。

    項目の限ったテストが対象から組み立てたものならそれを使う。`--round-test` をそのまま
    使う項目は、元が既知の実行器なら足したテストのファイルへ差し替え、無理なら
    `--round-test` をそのまま走らせる。
    """
    if item.get("command_source") == "targets":
        return list(item["command"])
    work = work_dir(state)
    source = (state.get("round_test") or {}).get("command") or \
        (state.get("baseline_test") or {}).get("command")
    tests = [f for f in files if testcmd.valid_targets([f], work, list(state.get("target_scope") or []))]
    if source and tests:
        built = testcmd.build(source, tests, work)
        if built is not None:
            return built
    return list(item.get("command") or []) or None


def _run_added_tests(state: dict[str, Any], intake: Intake) -> None:
    """足したテストが今のコードで通るかを確かめる（決定 13）。同じ語の並びは 1 回だけ走らせる。"""
    work = work_dir(state)
    timeout = timeline.state_test_timeout(state)
    results: dict[tuple[str, ...], bool] = {}
    for item_id, fact in intake.accepted.items():
        words = _test_words(state, find_item(state, item_id), list(fact.get("files") or []))
        if not words:
            continue
        key = tuple(words)
        if key not in results:
            log = pathlib.Path(state["tmp_dir"]) / f"add-tests-{item_id}.log"
            code, timed_out = run_with_timeout(list(words), work, timeout, output=log)
            results[key] = (not timed_out) and code == 0
        if not results[key]:
            intake.test_failed[item_id] = f"足したテストが今のコードで通りません（{' '.join(words)}）"
            intake.extra.append(fact["sha"])


def _intake_tests(state: dict[str, Any]) -> Intake:
    intake = Intake()
    facts = _facts(state, _phase_commits(state, "add-tests"))
    for fact in facts:
        fact["time"] = commit_time(work_dir(state), fact["sha"])
    by_item = _group_by_item(state, facts, lambda item: bool(item.get("tests")), intake)
    scope = list(state.get("target_scope") or [])
    tracked = tracked_markdown(work_dir(state))
    for item in live_items(state):
        if not item.get("tests"):
            continue
        commits = by_item.get(item["id"]) or []
        if not commits:
            intake.not_done[item["id"]] = "テストの追加の締め切りまでにコミットが無い"
            continue
        problem = _test_commit_problem(commits, scope, tracked, state)
        if problem:
            intake.rejected[item["id"]] = problem
        elif _deadline_passed(item, "test_start_deadline", "test", commits[0]["time"]):
            intake.not_done[item["id"]] = "テストの追加の完了の締め切りを過ぎてコミットした"
        else:
            intake.accepted[item["id"]] = commits[0]
            continue
        # 採らないコミットは項目の記録に載らない。取り消しの対象として明示する。
        intake.extra.extend(c["sha"] for c in commits)
    return intake


def _test_commit_problem(
    commits: list[dict[str, Any]], scope: list[str], tracked: list[str], state: dict[str, Any],
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
    hits = doc_wording_tests(commits, tracked, work_dir(state))
    if hits:
        return _doc_wording_reason(hits)
    return None


def cmd_merge_tests(args: argparse.Namespace) -> None:
    """テストの追加を取り込む。

    終了コード: 0 = 取り込んだ / 2 = 残る項目 0 件（最終ゲートへ）/ 4 = 範囲を確定できない。
    """
    path, state = load_state(args.id)
    _prepare(path, state)
    if ((state.get("phases") or {}).get("add-tests") or {}).get("ended_at"):
        info("↻ テストの追加は取り込み済みです")
        if not live_items(state):
            sys.exit(2)
        return
    intake = _recalled(state, "add-tests")
    if intake is None:
        record_observed_model(state, str(state["implementer"]), "add-tests")
        note_stopped(state, str(state["implementer"]), "add-tests")
        intake = _intake_tests(state)
        _run_added_tests(state, intake)
        for item_id, fact in intake.accepted.items():
            if item_id in intake.test_failed:
                continue
            item = find_item(state, item_id)
            item["commits"]["test"] = fact["sha"]
            item["status"] = TESTED
        _record_seconds(state, "add-tests", "test",
                        {k: v for k, v in intake.accepted.items() if k not in intake.test_failed})
        _remember(path, state, "add-tests", intake)
    else:
        info("↻ テストの追加の結論は記録済みです。取り消しと見送りだけをやり直します")
    _settle(path, state, intake, "テストの追加の取り込み")
    kept = [i for i in live_items(state) if i.get("status") == TESTED]
    info(f"テストの追加: 採用 {len(kept)} 件 / test_failed {len(intake.test_failed)} 件 / "
         f"not_done {len(intake.not_done)} 件 / 手順違反 {len(intake.rejected)} 件")
    _finish(path, state, "add-tests")


# ---------- 実装 ----------

def _doc_wording_reason(hits: list[tuple[str, int]]) -> str:
    return "文書の文言を固定するテストは足さない（" + "、".join(f"{p}: {l}" for p, l in hits) + "）"


def _implement_problem(
    item: dict[str, Any], commits: list[dict[str, Any]], scope: list[str],
    tracked: list[str], state: dict[str, Any],
) -> Optional[str]:
    """実装のコミットが手順を満たすか。適用の検査（v10.17.x）を項目の単位で掛ける。"""
    if len(commits) > 1:
        return f"実装が {len(commits)} コミットあります（1 改善項目 = 1 コミット）"
    problem = verify_commit_basics(commits[0], scope, "コミットが範囲にありません", check_test=False)
    if problem:
        return problem
    problem = verify_test_changes(collect_test_changes(commits))
    if problem:
        return problem
    hits = doc_wording_tests(commits, tracked, work_dir(state))
    if hits:
        return _doc_wording_reason(hits)
    return verify_diff_budget([item], commits)


def _intake_implement(state: dict[str, Any]) -> Intake:
    intake = Intake()
    work = work_dir(state)
    facts = _facts(state, _phase_commits(state, "implement"))
    for fact in facts:
        fact["time"] = commit_time(work, fact["sha"])
    by_item = _group_by_item(state, facts, lambda item: item.get("status") in (PLANNED, TESTED), intake)
    scope = list(state.get("target_scope") or [])
    tracked = tracked_markdown(work)
    for item in live_items(state):
        if item.get("status") not in (PLANNED, TESTED):
            continue
        commits = by_item.get(item["id"]) or []
        if not commits:
            intake.not_done[item["id"]] = "実装の締め切りまでにコミットが無い"
            continue
        problem = _implement_problem(item, commits, scope, tracked, state)
        if problem:
            intake.rejected[item["id"]] = problem
        elif _deadline_passed(item, "start_deadline", "implement", commits[0]["time"]):
            intake.not_done[item["id"]] = "実装の完了の締め切りを過ぎてコミットした"
        else:
            intake.accepted[item["id"]] = commits[0]
            continue
        # 採らないコミットは項目の記録に載らない。取り消しの対象として明示する。
        intake.extra.extend(c["sha"] for c in commits)
    return intake


def cmd_merge_implement(args: argparse.Namespace) -> None:
    """実装を取り込む。

    終了コード: 0 = 取り込んだ / 2 = 残る項目 0 件（最終ゲートへ）/ 4 = 範囲を確定できない。

    **テストの差分のうち段 1 で決まらないものは、最終ゲートのレビューへ引き継ぐ**
    （`review_test_judgements`。決定 25）。計画の後に判断のために LLM を起動しない。
    """
    path, state = load_state(args.id)
    _prepare(path, state)
    if ((state.get("phases") or {}).get("implement") or {}).get("ended_at"):
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
    info(f"実装: 採用 {len(intake.accepted)} 件 / not_done {len(intake.not_done)} 件 / "
         f"手順違反 {len(intake.rejected)} 件")
    if carried:
        info(f"{len(carried)} 件のテストの差分は機械で決まらないため、最終ゲートのレビューへ"
             f"引き継ぎます（改修計画に載ります）: {', '.join(carried)}")
    _finish(path, state, "implement")
