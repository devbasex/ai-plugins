"""テストの追加と実装の取り込み（`merge-tests` / `merge-implement`、#933 の F4 F5）。

**検証の材料は git から取る。** 結果ファイルの申告は使わない。項目とコミットの対応は
トレーラー `Item-Id` だけで決め（実装計画 I4）、所要はコミットの時刻で測る（決定 8）。
**コミットの時刻で項目を見送らない**（項目ごとの期限は持たない。#1743 決定 9）。

**手順の違反で項目を取り消さない**（#2457 の原則 3・棚卸し #20〜#26）。違反は項目の `sendbacks[]` に残し、直させるものは
修正の繰り返しへ回す（差し戻し）。

| 見つけたもの | 扱い |
| --- | --- |
| どの項目にも属さないコミット（`Item-Id` が無い・改修計画に無い） | そのコミットだけを `git revert` で打ち消し、差し戻しに残す |
| 範囲の外の既存のファイルの変更 | 取り消さない。項目の `outside_scope` に残し、報告とコードレビューへ引き継ぐ |
| トレーラーの欠け・テストの追加のコミットの数やテスト以外のファイル | 取り消さない。差し戻しに残して報告に出す |
| 期待値の欠け・文言を固定するテスト（実装） | 取り消さない。項目を `failing` にして修正へ差し戻す |
| 足したテストが今のコードで落ちた項目 | 取り消さない。差し戻しに残し、実装の後の検証が修正へ回す |
| コミットの無い項目 | 持ち越し（`carried`）。次の `readopt` が採り直すか `not_done` で見送る（I15） |

実装の項目は同じ `Item-Id` のコミットを何件でも 1 項目として採り、所要は最後のコミットの時刻で測る（#1814 決定 6）。
範囲は項目の全コミットを 1 つの単位として `scope_check` が判定する（#1814 決定 11）。

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
    FAILING,
    IMPLEMENTED,
    PLANNED,
    TESTED,
    current_round,
    find_item,
    in_current_round,
    item_label,
    live_items,
)
from ..pathkinds import is_test_path
from ..paths import git_out, load_state, work_dir
from ..phases import finish_phase, phase_record
from ..scope_check import judge_commits, note_outside
from .. import undo
from ..undo import resume_pending_drop
from ..verify import (
    verify_commit_basics,
    collect_test_changes,
    doc_wording_tests,
    pending_test_judgements,
    verify_test_changes,
)


@dataclass
class Intake:
    """1 つの手順分の取り込みの結論。取り消しは最後に 1 度だけまとめて行う。"""

    extra: list[str] = field(default_factory=list)  # どの項目にも属さないコミット
    rejected: dict[str, str] = field(default_factory=dict)  # 項目 → 手順を外れた理由
    carried: dict[str, str] = field(default_factory=dict)  # 項目 → 持ち越しの理由（コミットが無い）
    test_failed: dict[str, str] = field(default_factory=dict)  # 項目 → テストの結果（差し戻しに残すだけ）
    sendbacks: dict[str, list[dict[str, str]]] = field(default_factory=dict)  # 項目 → 差し戻し（種類・理由）
    send_to_fix: dict[str, str] = field(default_factory=dict)  # 項目 → 修正へ差し戻す理由（期待値の欠け・文言を固定するテスト）
    accepted: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # 項目 → コミットの事実（古い順）
    rewrites: dict[str, list[dict[str, str]]] = field(default_factory=dict)  # 項目 → 呼び手の書き換えとして通したファイル


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
    accepted: dict[str, list[dict[str, Any]]],
) -> None:
    """項目の所要。起点は最初の項目なら手順の開始、2 件目からは直前の項目のコミット（設計）。

    終わりは項目の**最後のコミット**の時刻（#1814 AC6a）。2 件目以降の作業も所要に数える。
    """
    previous = phase_record(state, phase).get("started_at")
    ends = {item_id: commits[-1]["time"] for item_id, commits in accepted.items()}
    for item_id, at in sorted(ends.items(), key=lambda kv: clock.parse(kv[1]) or clock.now()):
        seconds = clock.seconds_between(previous, at)
        find_item(state, item_id)["seconds"][key] = None if seconds is None else round(max(seconds, 0.0), 1)
        previous = at


def _settle(
    path: pathlib.Path,
    state: dict[str, Any],
    intake: Intake,
    label: str,
) -> None:
    """どの項目にも属さないコミットだけを打ち消し、持ち越しを記録する。項目は取り消さない（#2457）。"""
    this_round = current_round(state)
    done = any(d.get("reason") == label and int(d.get("round") or 1) == this_round for d in state.get("drops") or [])
    if intake.extra and not done:
        _revert_extra(path, state, intake.extra, label, this_round)
    # コミットの無い項目は見送らずに持ち越す（`readopt` が採り直すか `not_done` にする。I15）
    for item_id, reason in intake.carried.items():
        item = find_item(state, item_id)
        item["failure_reason"] = reason
        item["status"] = CARRIED
    for item_id, rows in intake.sendbacks.items():
        for row in rows:
            info(f"↪ {item_id} {item_label(find_item(state, item_id))}: {row['reason']}（差し戻し）")
    statefile.save(path, state)


def _revert_extra(path: pathlib.Path, state: dict[str, Any], extra: list[str], label: str, this_round: int) -> None:
    """どの項目にも属さないコミットを、新しい順に `git revert` で打ち消す（棚卸し #22。`reset --hard` は使わない）。"""
    undo.revert_unattributed(path, state, extra, label)
    info(f"↩ どの項目にも属さないコミット {len(extra)} 件を git revert で打ち消しました（Item-Id を付け直して出し直させる差し戻し）")


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
        "sendbacks": dict(intake.sendbacks),
        "send_to_fix": dict(intake.send_to_fix),
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
        sendbacks=dict(saved.get("sendbacks") or {}),
        send_to_fix=dict(saved.get("send_to_fix") or {}),
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
    tests = [f for f in files if targets.valid_targets([f], work, list(state.get("target_scope") or []), item.get("tests") or [], strategy)]
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
    results: dict[tuple[str, ...], bool] = {}
    for item_id, commits in intake.accepted.items():
        fact = commits[0]
        commands = _added_test_commands(state, find_item(state, item_id), list(fact.get("files") or []))
        if not commands:
            continue
        key = tuple(commands)
        if key not in results:
            log = pathlib.Path(state["tmp_dir"]) / f"add-tests-{item_id}.log"
            # H までの残りは起動の直前に取る。ループの前に 1 度だけ取ると、後のコマンドが H を越えて走る（#1932 原則 1）
            result, last = targets.run_commands(commands, work, timeline.state_test_timeout(state), log)
            if result.launch_failed:
                launch.stop(path, state, "tests", last, result, log)
            results[key] = result.status == ts.PASSED
        if not results[key]:
            reason = f"足したテストが今のコードで通りません（{targets.command_text(commands)}）"
            intake.test_failed[item_id] = reason
            intake.sendbacks.setdefault(item_id, []).append({"kind": "test_failed", "reason": reason})


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
            # 取り消さない（#2457）。差し戻しに残し、期待値の欠けと文言を固定するテストは修正へ回す
            kind, text, to_fix = problem if isinstance(problem, tuple) else ("procedure", str(problem), False)
            intake.sendbacks.setdefault(item["id"], []).append({"kind": kind, "reason": text})
            if to_fix:
                intake.send_to_fix[item["id"]] = text
        intake.accepted[item["id"]] = commits
    return intake


def _intake_tests(state: dict[str, Any]) -> Intake:
    intake = Intake()
    tracked = tracked_markdown(work_dir(state))
    return _intake_phase(
        state,
        # 今の巡で採った、テストを足す未了の項目だけ。前の巡で検証を終えた項目のコミットに触れない（#1743）
        lambda item: bool(item.get("tests")) and item.get("status") == PLANNED and in_current_round(state, item),
        lambda _item, commits: _test_commit_problem(commits, tracked, state),
        "add-tests",
        intake,
    )


def _test_commit_problem(
    commits: list[dict[str, Any]],
    tracked: list[str],
    state: dict[str, Any],
) -> Optional[str]:
    """テストの追加のコミットの手順の違反（種類, 理由, 修正へ回すか）。無ければ `None`。取り消しの理由にしない（#2457）。"""
    commit = commits[0]
    if len(commits) > 1:
        return ("commits", f"テストの追加が {len(commits)} コミットあります（1 項目 = 1 コミット）", False)
    problem = verify_commit_basics(commit, "コミットが範囲にありません", check_test=False)
    if problem:
        return ("trailers", problem, False)
    others = [f for f in commit.get("files") or [] if not is_test_path(f, work_dir(state))]
    if others:
        return ("non_test_files", f"テストの追加がテスト以外のファイルを変えています（{', '.join(others[:5])}）", False)
    hits = _wording_hits(commits, tracked, state)
    if hits:
        return ("doc_wording", _doc_wording_reason(hits), False)
    return None


def _merge_phase(
    args: argparse.Namespace,
    phase: str,
    label: str,
    intake_fn: Any,
    apply_fn: Any,
    report_fn: Any,
) -> None:
    """取り込みの骨組み。済みの判定・結論の読み直し・取り消しと見送り・手順の終わりを持つ。

    手順に固有なのは、取り込み（`intake_fn`）・採った項目への書き込み（`apply_fn`）・集計の案内（`report_fn`）だけ。
    """
    path, state = load_state(args.id)
    _prepare(path, state)
    if phase_record(state, phase).get("ended_at"):
        info(f"↻ {label}は取り込み済みです")
        if not live_items(state):
            sys.exit(2)
        return
    intake = _recalled(state, phase)
    if intake is None:
        record_observed_model(state, str(state["implementer"]), phase)
        note_stopped(state, str(state["implementer"]), phase)
        intake = intake_fn(state)
        apply_fn(path, state, intake)
        _remember(path, state, phase, intake)
    else:
        info(f"↻ {label}の結論は記録済みです。取り消しと見送りだけをやり直します")
    _settle(path, state, intake, f"{label}の取り込み")
    report_fn(state, intake)
    _finish(path, state, phase)


def _apply_tests(path: pathlib.Path, state: dict[str, Any], intake: Intake) -> None:
    """足したテストを走らせ、通った項目にテストのコミットを記録する。"""
    _run_added_tests(path, state, intake)
    for item_id, commits in intake.accepted.items():
        item = find_item(state, item_id)
        item["commits"]["test"] = commits[0]["sha"]
        item["commits"]["test_extra"] = [c["sha"] for c in commits[1:]]
        item["status"] = TESTED
        _note_sendbacks(item, intake.sendbacks.get(item_id))
    _record_seconds(state, "add-tests", "test", intake.accepted)


def _note_sendbacks(item: dict[str, Any], rows: Optional[list[dict[str, str]]]) -> None:
    for row in rows or []:
        item.setdefault("sendbacks", []).append({**row, "at": statefile.now()})


def _report_tests(state: dict[str, Any], intake: Intake) -> None:
    kept = [i for i in live_items(state) if i.get("status") == TESTED]
    info(
        f"テストの追加: 採用 {len(kept)} 件 / 今のコードで落ちたテスト {len(intake.test_failed)} 件 / "
        f"持ち越し {len(intake.carried)} 件 / 差し戻し {sum(len(v) for v in intake.sendbacks.values())} 件"
    )


def cmd_merge_tests(args: argparse.Namespace) -> None:
    """テストの追加を取り込む。

    終了コード: 0 = 取り込んだ / 2 = 残る項目 0 件（採り直しの判定へ）/ 4 = 範囲を確定できない。
    """
    _merge_phase(args, "add-tests", "テストの追加", _intake_tests, _apply_tests, _report_tests)


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
    tracked: list[str],
    state: dict[str, Any],
    intake: Optional[Intake] = None,
) -> Optional[str]:
    """実装のコミット（同じ `Item-Id` の全件）の手順の違反（種類, 理由, 修正へ回すか）。無ければ `None`。

    取り消しの理由にしない（#2457）。範囲の外の変更は `outside_scope` に残すだけ（棚卸し #24）。実差分の行数では判定しない（#1814 決定 4）。
    """
    verdict = judge_commits(state, [c["sha"] for c in commits])
    note_outside(item, verdict)
    if verdict.rewrites and intake is not None:
        intake.rewrites[item["id"]] = verdict.rewrites
    problem = verify_test_changes(collect_test_changes(commits))
    if problem:
        return ("expectations", problem, True)
    hits = _wording_hits(commits, tracked, state)
    if hits:
        return ("doc_wording", _doc_wording_reason(hits) + "。そのテストだけを外す", True)
    for commit in commits:
        problem = verify_commit_basics(commit, "コミットが範囲にありません", check_test=False)
        if problem:
            return ("trailers", problem, False)
    return None


def _intake_implement(state: dict[str, Any]) -> Intake:
    intake = Intake()
    tracked = tracked_markdown(work_dir(state))
    return _intake_phase(
        state,
        lambda item: item.get("status") in (PLANNED, TESTED),
        lambda item, commits: _implement_problem(item, commits, tracked, state, intake),
        "implement",
        intake,
    )


def _apply_implement(_path: pathlib.Path, state: dict[str, Any], intake: Intake) -> None:
    """採った項目に実装のコミットと、最終ゲートへ引き継ぐ判定を記録する。"""
    for item_id, commits in intake.accepted.items():
        item = find_item(state, item_id)
        item["commits"]["implement"] = [c["sha"] for c in commits]
        item["status"] = IMPLEMENTED
        # 実差分は記録だけ（判定に使わない。#1814 決定 4）
        item["diff_lines"] = sum(safe_int(c.get("diff_lines")) for c in commits)
        pending = pending_test_judgements(commits)
        if pending:
            item["review_test_judgements"] = pending
        if intake.rewrites.get(item_id):
            item["review_scope_judgements"] = intake.rewrites[item_id]
        _note_sendbacks(item, intake.sendbacks.get(item_id))
        if item_id in intake.send_to_fix:
            # 期待値の欠け・文言を固定するテストは修正へ差し戻す（棚卸し #25・#26）
            item["status"] = FAILING
            item["failed_check"] = {
                "kind": "sendback",
                "suite": "sendback",
                "command": "",
                "files": [],
                "tests": [],
                "note": intake.send_to_fix[item_id],
            }
    _record_seconds(state, "implement", "implement", intake.accepted)


def _report_implement(state: dict[str, Any], intake: Intake) -> None:
    carried = [i["id"] for i in live_items(state) if i.get("review_test_judgements")]
    info(
        f"実装: 採用 {len(intake.accepted)} 件 / 持ち越し {len(intake.carried)} 件 / 差し戻し {sum(len(v) for v in intake.sendbacks.values())} 件"
    )
    if carried:
        info(
            f"{len(carried)} 件のテストの差分は機械で決まらないため、最終ゲートのレビューへ"
            f"引き継ぎます（改修計画に載ります）: {', '.join(carried)}"
        )


def cmd_merge_implement(args: argparse.Namespace) -> None:
    """実装を取り込む。

    終了コード: 0 = 取り込んだ / 2 = 残る項目 0 件（採り直しの判定へ）/ 4 = 範囲を確定できない。

    **テストの差分のうち一次の判定で決まらないものは、最終ゲートのレビューへ引き継ぐ**
    （`review_test_judgements`。決定 25）。改修計画の後に判断のために LLM を起動しない。
    """
    _merge_phase(args, "implement", "実装", _intake_implement, _apply_implement, _report_implement)
