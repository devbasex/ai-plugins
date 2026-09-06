"""提案の取り込みと、適用結果の検証。

`merge-proposals` と `merge-apply` を持つ。検証を通らなかったラウンドの取り消しと、
修正フェーズの用意もここで行う。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

import statefile

from .. import die, info
from ..gitfacts import (
    _current_round,
    _discard_impl_leftovers,
    _drop_items,
    _find_item,
    _flush_pending_push,
    _git_out,
    _push_head,
    _read_result,
    _record_observed_model,
    _reported_shas,
    _revert_item_commits,
    _round,
    _safe_int,
    collect_commit_facts,
    commits_in_range,
)
from ..paths import _load, _result_path, stem_for
from ..proposals import merge_proposals
from ..verify import verify_apply_item
from ..vocabulary import DEFAULT_TEST_TIMEOUT


def _load_runtime_proposals(
    state: dict[str, Any], entry: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    """各ランタイムの提案結果ファイルを読み込み、JSON を解析する。

    1 者が結果ファイルを欠かした・壊れた JSON を返した場合も、その者の提案を
    無かったものとして扱い、全体の統合は続ける。
    """
    proposals: dict[str, list[dict[str, Any]]] = {}
    for runtime in state["runtimes"]:
        result = _result_path(
            state, runtime,
            stem_for(runtime, "propose", state["id"], entry["round"]),
        )
        if not result.exists():
            info(f"⚠ {runtime} の提案結果がありません: {result}")
            continue
        try:
            payload = json.loads(result.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            info(f"⚠ {runtime} の提案結果が JSON として読めません: {e}")
            continue
        if not isinstance(payload, dict):
            # 配列や数値のまま `payload.get(...)` を呼ぶと落ちる。
            # 提案は無かったものとして続ける（1 者の不調で全体を止めない）。
            info(
                f"⚠ {runtime} の提案結果が JSON オブジェクトではありません"
                f"（{type(payload).__name__}）。提案なしとして扱います"
            )
            proposals[runtime] = []
            entry["proposed"][runtime] = 0
            continue
        items = payload.get("items")
        proposals[runtime] = [i for i in items if isinstance(i, dict)] \
            if isinstance(items, list) else []
        entry["proposed"][runtime] = len(proposals[runtime])
    return proposals


def _update_state_from_merged_proposals(
    path: pathlib.Path,
    state: dict[str, Any],
    entry: dict[str, Any],
    adopted: list[dict[str, Any]],
    deferred: list[dict[str, Any]],
) -> None:
    """統合済みの提案から状態オブジェクトを更新し、次回ラウンドの起点を準備する。"""
    # 収束判定に使う「前ラウンドとの重複率」。見送りも含めた提案全体で測る。
    current_keys = [(i["path"], i["symbol"], i["smell"]) for i in adopted + deferred]
    entry["proposal_keys"] = [list(k) for k in current_keys]
    entry["merged"] = len(current_keys)
    entry["adopted"] = len(adopted)
    entry["deferred"] = len(deferred)

    round_no = entry["round"]
    for n, item in enumerate(adopted, start=1):
        item_id = f"R{round_no}-{n:03d}"
        state["items"].append({
            "item_id": item_id,
            "round": round_no,
            **item,
            "status": "pending",
            "commits": [],
        })
        entry["items"].append(item_id)
    for item in deferred:
        state["deferred_items"].append({**item, "round": round_no})

    # 適用の起点は**オーケストレータ側で**確定させる。実装担当の申告に委ねると、
    # 欠落・不正時に範囲検査が無効になり、過去の任意のコミットが実在扱いになる。
    # 提案は読むだけなので、この時点の HEAD が着手前の状態である。
    entry["apply_base_sha"] = _git_out(state["worktrees"]["work"], ["rev-parse", "HEAD"])

    state["phase"] = "apply" if adopted else "converged"
    if not adopted:
        # 呼び出し側は終了コード 2 で繰り返しを抜けるため、`advance` を通らない。
        # 終了理由をここで確定させないと、報告が「未終了」のままになる。
        state["final"] = "no_more_proposals"
        state["ended_at"] = statefile.now()
    statefile.save(path, state)


def cmd_merge_proposals(args: argparse.Namespace) -> None:
    """Step 3 — 提案をマージして改善項目を作る。

    終了コード: 0 = 採用あり / 2 = 採用 0 件（提案ラウンドの繰り返しを終える）。

    **同じラウンドで叩き直しても二重に項目を作らない。** 進行を止めても再開できる
    ことが前提なので、統合済みなら前回と同じ結果をそのまま返す。
    """
    path, state = _load(args.id)
    entry = _current_round(state)

    if entry.get("proposal_keys") is not None:
        info(
            f"↻ 提案ラウンド {entry['round']} は統合済みです"
            f"（採用 {entry.get('adopted', 0)} 件 / 見送り {entry.get('deferred', 0)} 件）"
        )
        for item_id in entry.get("items", []):
            item = _find_item(state, item_id, required=False)
            if item is not None:
                info(f"  {item_id} [{item['severity']}] {item['path']}#{item['symbol']}")
        if not entry.get("adopted"):
            sys.exit(2)
        return

    proposals = _load_runtime_proposals(state, entry)

    excluded = {
        (d["path"], d["symbol"], d["smell"]) for d in state["deferred_items"]
    }
    adopted, deferred = merge_proposals(
        proposals,
        threshold=state["severity_threshold"],
        max_items=state["max_items_per_round"],
        excluded_keys=excluded,
    )

    _update_state_from_merged_proposals(path, state, entry, adopted, deferred)
    info(
        f"提案 {sum(entry['proposed'].values())} 件 → 統合 {entry['merged']} 件 → "
        f"採用 {entry['adopted']} 件 / 見送り {entry['deferred']} 件"
    )
    for item_id in entry["items"]:
        item = _find_item(state, item_id)
        info(
            f"  {item_id} [{item['severity']}] {item['path']}#{item['symbol']} "
            f"{item['smell']} → {item['technique']} "
            f"(合意 {len(item['proposed_by'])} / 見積 {item['estimated_diff_lines']} 行)"
        )
    if not adopted:
        info("採用 0 件のため、提案ラウンドの繰り返しを終えます")
        sys.exit(2)


def cmd_merge_apply(args: argparse.Namespace) -> None:
    """Step 4 — 適用結果を検証して取り込む。

    終了コード: 0 = 1 件以上成功 / 2 = 全件失敗（次の提案ラウンドへ進む）。

    **1 件の失敗でラウンドを止めない。** 失敗した項目だけを見送りにして、
    残りは採用する。
    """
    path, state = _load(args.id)
    entry = _round(state, args.round)
    if not args.dry_run:
        _discard_impl_leftovers(state, state["worktrees"]["work"])
        _resume_incomplete_apply(path, state, entry)

    # **叩き直しても同じ判定を返す。** 取り込み済みで再実行すると、前回作った
    # 取り消しコミットが「未割当」と判定され、成功した項目まで巻き込んで
    # ラウンド全体を取り消してしまう。
    if (entry.get("apply") or {}).get("merged_at"):
        applied_before = entry["apply"].get("applied") or []
        info(
            f"↻ ラウンド {args.round} の適用は取り込み済みです"
            f"（採用 {len(applied_before)} 件 / 失敗 "
            f"{len(entry['apply'].get('failed') or [])} 件）"
        )
        if not applied_before:
            sys.exit(2)
        return

    payload, work, head_branch, test_command, head_sha, ordered_range, in_range = (
        _load_apply_context(path, state, entry, args)
    )

    reported, unknown_ids = _collect_apply_reports(payload, entry)

    _validate_apply_commit_ownership(
        path, state, entry, args, reported, unknown_ids, work,
        ordered_range, in_range, head_sha,
    )

    applied, failed = _verify_apply_items(
        path, state, entry, args, reported, work, in_range, test_command, head_branch,
    )

    entry["apply"] = {
        "applied": applied,
        "failed": failed,
        # 起点はオーケストレータが記録したもの。申告は記録にも残さない。
        "base_sha": entry.get("apply_base_sha"),
        "head_sha": head_sha,
        # **取り込み済みの印は最後に立てる。** 取り消しより先に立てると、取り消しに
        # 失敗して中断したときに、次の実行が処理済みガードで素通りしてしまい、
        # 検証を通っていない変更が Pull Request に残り続ける。
        "merged_at": None,
    }
    entry.setdefault("durations", {})["apply"] = _safe_int(
        payload.get("elapsed_seconds")
    )
    state["phase"] = "review" if applied else "propose"

    # `--dry-run` では git も状態ファイルも触らない。片方だけ進むと、確認の
    # つもりで実行した利用者の進行が壊れる。
    if args.dry_run:
        if failed:
            _drop_items(state, entry, failed, dry_run=True)
        info("（dry-run）状態ファイルは更新していません")
        applied = list(entry["apply"]["applied"])
    elif failed:
        # `merged_at` は `_apply_drop` が取り消しの完了時点で立てる。
        applied = _apply_drop(path, state, entry, failed)
    else:
        # **全項目が通ったときも進行側が公開する。** 実装担当は push しないため、
        # ここで公開しないとレビュー担当が Pull Request 上の差分へ指摘を書けない。
        entry["apply"]["merged_at"] = statefile.now()
        entry["pending_push"] = True
        statefile.save(path, state)
        _push_head(state)
        entry["pending_push"] = False
        statefile.save(path, state)

    if not applied:
        info("全項目が失敗したため、このラウンドのレビューは行いません")
        sys.exit(2)


def _load_apply_context(
    path: pathlib.Path,
    state: dict[str, Any],
    entry: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[dict[str, Any], pathlib.Path, str, str, str, list[str], set[str]]:
    impl = entry["impl"]
    result = _result_path(state, impl, stem_for(impl, "apply", state["id"], args.round))
    payload = _read_result(result, impl)

    _record_observed_model(entry, "impl", impl, state, "apply", args.round)

    # 着手前のテストが**成功と確認できていない限り**適用結果を採らない。
    # `red` だけでなく `unknown`（確認していない）も拒否する。確認していない状態を
    # 通すと、「壊したのか元から壊れていたのか」を判別する手段が無いまま進む。
    baseline = state.get("baseline_test") or {}
    if baseline.get("status") != "green":
        for item_id in entry["items"]:
            _find_item(state, item_id)["status"] = "blocked"
        if not args.dry_run:
            statefile.save(path, state)
        die(
            f"着手前のテストが成功と確認できていません（status={baseline.get('status')}）。"
            "適用へ着手しません（全項目を blocked）",
            code=2,
        )

    # 検証の材料は git から取る。結果ファイルから使うのは
    # 「どのコミットがどの項目のものか」という対応付けだけ。
    work = state["worktrees"]["work"]
    head_branch = state["head_branch"]
    test_command = baseline["command"]
    head_sha = _git_out(work, ["rev-parse", "HEAD"]) or ""
    # 起点は `merge-proposals` が記録したもの。**実装担当の申告は使わない。**
    ordered_range = commits_in_range(work, entry.get("apply_base_sha"), head_sha)
    in_range = set(ordered_range or [])
    if ordered_range is None:
        # 範囲を確定できないなら、何も検証できない。素通しにせず失敗させる。
        for item_id in entry["items"]:
            _find_item(state, item_id)["status"] = "blocked"
        if not args.dry_run:
            statefile.save(path, state)
        die(
            "適用の範囲を確定できませんでした"
            f"（起点 {entry.get('apply_base_sha')} / HEAD {head_sha}）。"
            "検証できない適用は採りません",
            code=2,
        )
    return payload, work, head_branch, test_command, head_sha, ordered_range, in_range


def _collect_apply_reports(
    payload: dict[str, Any],
    entry: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    # 申告は**このラウンドの改善項目のものだけ**を採る。架空の項目 ID へ割り当てられた
    # コミットを数に入れると、割り当て済みに見えるのに項目別の検証にも入らず、
    # そのまま Pull Request に残せてしまう。
    round_items = set(entry["items"])
    reported: dict[str, dict[str, Any]] = {}
    unknown_ids: list[str] = []
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        info(f"⚠ 適用結果の items が配列ではありません（{type(raw_items).__name__}）")
        raw_items = []
    for r in raw_items:
        if not isinstance(r, dict):
            continue
        item_id = r.get("item_id")
        if item_id in round_items:
            reported[item_id] = r
        elif item_id is not None:
            unknown_ids.append(str(item_id))
    return reported, unknown_ids


def _detect_commit_owners(
    work: pathlib.Path, reported: dict[str, dict[str, Any]]
) -> tuple[dict[str, str], list[str]]:
    """申告コミットを完全 SHA へ正規化し、所有項目と重複申告を特定する。

    **1 コミットの所有項目は 1 つだけ。** 同じコミットを 2 つの項目が申告すると、
    片方が失敗して取り消したときに、もう片方は成功のまま残る。状態ファイルと
    実際の差分が食い違い、どちらが正しいか決められなくなる。

    判定は**完全な SHA へ正規化してから**行う。申告の文字列をそのまま鍵にすると、
    一方が完全 SHA、他方が短縮 SHA で同じコミットを指したときに重複を見逃す。
    """
    owner_of: dict[str, str] = {}
    duplicated: list[str] = []
    for item_id, r in reported.items():
        for sha in _reported_shas(r):
            full = _git_out(work, ["rev-parse", "--verify", f"{sha}^{{commit}}"])
            if full is None:
                continue          # 実在しない申告は項目ごとの検証で落ちる
            if full in owner_of and owner_of[full] != item_id:
                duplicated.append(full)
            owner_of.setdefault(full, item_id)
    return owner_of, duplicated


def _build_ownership_error_reason(
    unassigned: list[str], unknown_ids: list[str], duplicated: list[str]
) -> str:
    """所有権検査の失敗理由を組み立てる。"""
    causes = []
    if unassigned:
        causes.append(
            f"どの改善項目にも割り当てられていないコミットが {len(unassigned)} 件"
            f"（{', '.join(s[:7] for s in unassigned[:5])}）"
        )
    if unknown_ids:
        causes.append(
            f"このラウンドに無い改善項目 ID の申告"
            f"（{', '.join(unknown_ids[:5])}）"
        )
    if duplicated:
        causes.append(
            f"複数の項目が同じコミットを申告しています"
            f"（{', '.join(s[:7] for s in duplicated[:5])}）"
        )
    return (
        "、".join(causes)
        + "。検証を回避した変更や、状態と実差分の食い違いを Pull Request に"
          "残さないため、ラウンドごと取り消します"
    )


def _revert_unverified_apply_round(
    path: pathlib.Path,
    state: dict[str, Any],
    entry: dict[str, Any],
    args: argparse.Namespace,
    work: pathlib.Path,
    ordered_range: list[str],
    unassigned: list[str],
    unknown_ids: list[str],
    duplicated: list[str],
    head_sha: str,
) -> None:
    """検証を通らない適用ラウンドの範囲を取り消し、状態と公開を反映する。"""
    # 範囲全体を取り消す。どのコミットが安全かを決められない以上、
    # 起点まで戻すのが最も確実である。順序は `_revert_item_commits` が
    # git の履歴から決め直す。
    whole_round = {
        "item_id": f"R{entry['round']}-range",
        "commits": list(ordered_range),
    }
    if not args.dry_run:
        # **取り消しへ着手する前に印を立てる。** 取り消しは済んだのに push
        # できずに終わると、未検証の変更が Pull Request に残ったままになる。
        entry["pending_push"] = True
        statefile.save(path, state)
    _revert_item_commits(state, whole_round, args.dry_run)
    if not args.dry_run:
        # 取り消し後の状態を新しい起点にする。叩き直しても範囲が空になり、
        # 取り消しコミット自体を「未割当」として再び戻すことがない。
        entry["apply_base_sha"] = _git_out(work, ["rev-parse", "HEAD"])
    entry["apply"] = {
        "applied": [], "failed": list(entry["items"]),
        "base_sha": entry.get("apply_base_sha"), "head_sha": head_sha,
        "unassigned_commits": unassigned,
        "unknown_item_ids": unknown_ids,
        "duplicated_commits": duplicated,
        "merged_at": statefile.now(),
    }
    state["phase"] = "propose"
    if args.dry_run:
        info("（dry-run）状態ファイルは更新していません")
    else:
        # 項目別の失敗と同じく、**ここで取り消した項目も「対象外」に残す**。
        # 残さないと同じ提案が次のラウンドで再び採用される。
        _defer_abandoned_items(state, entry)
        statefile.save(path, state)
        _push_head(state)
        entry["pending_push"] = False
        statefile.save(path, state)


def _validate_apply_commit_ownership(
    path: pathlib.Path,
    state: dict[str, Any],
    entry: dict[str, Any],
    args: argparse.Namespace,
    reported: dict[str, dict[str, Any]],
    unknown_ids: list[str],
    work: pathlib.Path,
    ordered_range: list[str],
    in_range: set[str],
    head_sha: str,
) -> None:
    # **範囲のコミットは全て、いずれかの改善項目に割り当てられていること。**
    # 申告から漏れたコミットはテストもトレーラーも差分予算も検査されず、そのまま
    # Pull Request に残る。都合の悪い変更を申告しないだけで検査を回避できてしまう。
    owner_of, duplicated = _detect_commit_owners(work, reported)

    unassigned = sorted(in_range - set(owner_of))
    if not (unassigned or unknown_ids or duplicated):
        return

    reason = _build_ownership_error_reason(unassigned, unknown_ids, duplicated)
    info(f"❌ {reason}")
    for item_id in entry["items"]:
        it = _find_item(state, item_id)
        it["status"] = "abandoned"
        it["failure_reason"] = reason
    _revert_unverified_apply_round(
        path, state, entry, args, work, ordered_range,
        unassigned, unknown_ids, duplicated, head_sha,
    )
    sys.exit(2)


def _verify_apply_items(
    path: pathlib.Path,
    state: dict[str, Any],
    entry: dict[str, Any],
    args: argparse.Namespace,
    reported: dict[str, dict[str, Any]],
    work: pathlib.Path,
    in_range: set[str],
    test_command: str,
    head_branch: str,
) -> tuple[list[str], list[str]]:
    applied: list[str] = []
    failed: list[str] = []
    scope = state.get("target_scope") or []
    # **判定はその都度残す。** まとめて最後に保存すると、取り消しの途中で中断した
    # ときに適用の記録が一切残らず、どのコミットが検証を通ったのかを状態から
    # 復元できなくなる。再開可能性は収束ループの前提なので、ここが崩れると
    # 中断からの復帰手段が無くなる。
    progress: list[dict[str, Any]] = []
    entry["apply_progress"] = progress
    for item_id in entry["items"]:
        item = _find_item(state, item_id)
        got = reported.get(item_id)
        if got is None:
            problem = "適用結果に項目がありません"
            facts: list[dict[str, Any]] = []
        else:
            facts = collect_commit_facts(
                work, _reported_shas(got), in_range, test_command, head_branch,
                _safe_int(state.get("test_timeout"), DEFAULT_TEST_TIMEOUT),
            )
            problem = verify_apply_item(item, facts, scope)
        if problem:
            item["status"] = "abandoned"
            item["failure_reason"] = problem
            item["test_failed"] = bool(got and "テストが成功していません" in problem)
            item["budget_exceeded"] = bool(got and "差分予算" in problem)
            item["out_of_scope"] = bool(got and "対象範囲の外" in problem)
            # 取り消しは全項目の判定が出そろってから**まとめて**行う。項目ごとに
            # その場で戻すと、まだ判定していない項目のコミットと競合する。
            item["commits"] = _reported_shas(got)
            failed.append(item_id)
            info(f"❌ {item_id}: {problem}")
        else:
            item["status"] = "reviewing"
            item["commits"] = _reported_shas(got)
            item["diff_lines"] = sum(_safe_int(c.get("diff_lines")) for c in facts)
            applied.append(item_id)
            info(f"✅ {item_id}: {len(item['commits'])} コミット / {item['diff_lines']} 行")
        progress.append({
            "item_id": item_id, "at": statefile.now(),
            "result": "failed" if problem else "ok",
            "reason": problem, "commits": list(item.get("commits") or []),
        })
        if not args.dry_run:
            statefile.save(path, state)
    return applied, failed


def _defer_abandoned_items(state: dict[str, Any], entry: dict[str, Any]) -> None:
    """このラウンドで取り消した項目を「対象外」として記録する。

    記録しないと、**同じ提案が次のラウンドで再び採用され、同じ理由で失敗する**。
    実測では適用で失敗した項目が 3 ランタイム全員から再提案され、合意数が最大に
    なって最優先で採用された。手順書が「同じ提案が毎ラウンド出続けて収束しない」
    として禁じている状態そのものである。

    除外の鍵は `path` + `symbol` + `smell` なので、その 3 つを必ず残す。
    """
    already = {d.get("item_id") for d in state["deferred_items"]}
    for item_id in entry["items"]:
        item = _find_item(state, item_id, required=False)
        if item is None or item.get("status") != "abandoned" or item_id in already:
            continue
        state["deferred_items"].append({
            "item_id": item_id,
            "path": item["path"], "symbol": item["symbol"], "smell": item["smell"],
            "round": entry["round"],
            "defer_reason": item.get("failure_reason") or "適用結果の検証を通らなかった",
        })


def _run_drop(
    path: pathlib.Path, state: dict[str, Any], entry: dict[str, Any],
    targets: list[str],
) -> dict[str, Any]:
    """取り消しを、中断しても再開できる形で実行する。

    `pending_drop` と `pending_push` を立ててから入り、**戻ったらすぐ保存する**。
    保存しないまま落ちると、積み直しで変わった SHA と取り消し済みの印が失われ、
    次の実行は**履歴に無い SHA を相手に**取り消しをやり直すことになる。

    印はここでは消さない。**呼び出し側が完了の記録と同じ保存で消す。** 先に消すと、
    完了を記録する前に落ちたときに、次の実行が「取り消し済みだが未完了」の状態を
    見分けられなくなる。
    """
    entry["pending_drop"] = list(targets)
    entry["pending_push"] = True
    statefile.save(path, state)
    result = _drop_items(state, entry, list(targets))
    statefile.save(path, state)
    return result


def _apply_drop(
    path: pathlib.Path, state: dict[str, Any], entry: dict[str, Any],
    failed: list[str],
) -> list[str]:
    """検証に失敗した項目を取り消し、採用として残る項目 ID を返す。

    **中断しても再開できる形で記録する。** 失敗の位置で必要な再開が変わるため、
    印は次の順で切り替える。

    | 中断した位置 | 残る印 | 次の実行がすること |
    | --- | --- | --- |
    | 取り消しの途中 | `pending_drop` あり / `merged_at` なし | 取り消しをやり直す |
    | 取り消し後・push 前 | `pending_drop` なし / `merged_at` あり / `pending_push` あり | **push の再送だけ** |

    取り消しより先に `merged_at` を立てると、取り消しに失敗したときに次の実行が
    処理済みガードで素通りし、**再試行できない**。逆に push まで終えるまで
    `merged_at` を立てないと、push だけ失敗したときに次の実行が適用の検証をやり直し、
    取り消しと積み直しのコミットを「未割当」と判定してラウンドごと巻き込む。
    """
    work = state["worktrees"]["work"]
    result = _run_drop(path, state, entry, failed)
    applied = list(entry["apply"].get("applied") or [])
    if result["mode"] == "round":
        # 積み直せなかった。合意済みの項目も含めて全件捨てる。
        for item_id in entry["items"]:
            it = _find_item(state, item_id)
            it["status"] = "abandoned"
            it.setdefault(
                "failure_reason",
                "残す項目を積み直せなかったため、ラウンドごと取り消した",
            )
        applied = []
        entry["apply"]["applied"] = []
        entry["apply"]["failed"] = list(entry["items"])
        # 取り消し後の状態を新しい起点にする（叩き直しでの二重取り消しを防ぐ）。
        entry["apply_base_sha"] = _git_out(work, ["rev-parse", "HEAD"])
        state["phase"] = "propose"

    # 取り消した項目は「対象外」として残す。次のラウンドで同じ提案が採用され、
    # 同じ理由で失敗するのを防ぐ。
    _defer_abandoned_items(state, entry)
    # **取り消しが済んだことを push より先に、印の解除と同じ保存で永続化する。**
    # 保存せずに push して失敗すると、次の実行が適用の検証をやり直し、取り消しと
    # 積み直しのコミットを「未割当」と判定してラウンドごと巻き込んでしまう。
    # `pending_push` は残るので、次の実行は push の再送だけを行う。
    entry["pending_drop"] = []
    entry["apply"]["merged_at"] = statefile.now()
    statefile.save(path, state)
    _push_head(state)
    entry["pending_push"] = False
    statefile.save(path, state)
    return applied


def _resume_incomplete_apply(
    path: pathlib.Path, state: dict[str, Any], entry: dict[str, Any]
) -> None:
    """前回終わらなかった取り消しと push を、処理済みの判定より**先に**片づける。

    取り消しをやり残したまま push だけ先に流すと、検証を通っていない HEAD が
    Pull Request へ反映されてしまう。**取り消しの再実行を先に行う。**
    """
    if entry.get("pending_drop"):
        info("↻ 前回終わらなかった取り消しを再実行します")
        _apply_drop(path, state, entry, list(entry["pending_drop"]))
        return
    _flush_pending_push(path, state, entry)


def _prepare_fix_phase(state: dict[str, Any], entry: dict[str, Any]) -> None:
    """変更要求を返す前に、修正フェーズが要る記録を残す。

    **変更要求の出口は 2 つある**（通常の判定と、差し戻し上限からの落ちこみ）。
    どちらも同じ記録が要るため、書き漏らしが起きないよう 1 箇所へ集める。

    - `fix_base_sha`: 修正の範囲の起点。無いと `merge-fix` が範囲を確定できず、
      `fix_rounds` が進まないまま修正と再レビューを往復し続ける
    - `fix_attempts`: 試行番号。`merge-fix` が「叩き直し」と「次のラウンド」を
      区別するのに使う
    """
    entry["fix_base_sha"] = _git_out(state["worktrees"]["work"], ["rev-parse", "HEAD"])
    entry["fix_attempts"] = entry.get("fix_attempts", 0) + 1
