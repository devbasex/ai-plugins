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

import assignment
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
from ..proposals import assign_apply_rounds, merge_proposals
from ..verify import verify_apply_round
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


def _assign_apply_rounds_to_state(
    state: dict[str, Any],
    entry: dict[str, Any],
    adopted: list[dict[str, Any]],
) -> None:
    """採用した項目を適用ラウンド（群）へ分け、状態へ記録する。

    **適用の担当は適用ラウンドごとに進む。** 提案ラウンド単位で 1 者に固定すると、
    群の数だけ 1 者が連続で適用することになり負荷が偏る。群の中の項目は互いに
    独立しているため、1 つの群を適用するのに要る前提はその群の中で閉じている。

    輪番の通し番号は `state["apply_seq"]` が持つ。**提案ラウンドの番号ではない。**
    1 つの提案ラウンドが複数の群を持てば、輪番はその分だけ進む。
    """
    entry["apply_rounds"] = []
    entry["apply_round"] = 0
    seq = _safe_int(state.get("apply_seq"))
    for n, group in enumerate(assign_apply_rounds(adopted), start=1):
        seq += 1
        impl, _ = assignment.assign(seq, state["host"])
        for item in group:
            item["apply_round"] = n
        entry["apply_rounds"].append({
            "apply_round": n,
            "impl": impl,
            "impl_model": {"requested": state["models"].get(impl), "observed": None},
            "items": [i["item_id"] for i in group],
            "status": "pending",
            "base_sha": None,
            "head_sha": None,
            "fix_rounds": 0,
        })
    state["apply_seq"] = seq


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
        item["item_id"] = f"R{round_no}-{n:03d}"
    _assign_apply_rounds_to_state(state, entry, adopted)
    for item in adopted:
        state["items"].append({
            "round": round_no,
            **item,
            "status": "pending",
            "commits": [],
        })
        entry["items"].append(item["item_id"])
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


def apply_groups(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """このラウンドの適用ラウンド（群）の一覧。

    群を持たない状態ファイル（この版より前）は、**ラウンド全体を 1 つの群**として
    読み、その場で記録する。中断から再開したときに、群の単位が実行のたびに
    変わらないようにするためである。
    """
    groups = entry.get("apply_rounds")
    if groups:
        return groups
    entry["apply_rounds"] = [{
        "apply_round": 1,
        "impl": entry.get("impl"),
        "impl_model": entry.get("impl_model") or {"requested": None, "observed": None},
        "items": list(entry.get("items") or []),
        "status": "pending",
        "base_sha": entry.get("apply_base_sha"),
        "head_sha": None,
        "fix_rounds": entry.get("fix_rounds", 0),
    }]
    entry.setdefault("apply_round", 1)
    return entry["apply_rounds"]


def current_group(entry: dict[str, Any]) -> dict[str, Any]:
    """進行中の適用ラウンド。まだ開いていなければ最初の群を返す。"""
    groups = apply_groups(entry)
    current = entry.get("apply_round") or 1
    for group in groups:
        if group.get("apply_round") == current:
            return group
    return groups[-1]


def cmd_next_apply_round(args: argparse.Namespace) -> None:
    """Step 4 — 次の適用ラウンドを開き、実装担当と対象の項目を返す。

    終了コード: 0 = 群を開いた / 1 = 残りの群が無い（提案ラウンドへ戻る）。

    **群の起点はここで確定させる。** 後続の群は先行の群を適用した後の作業ツリーを
    読むため、起点はその時点の HEAD になる。取り消しの範囲もこの起点で決まる。

    **修正ラウンドの数え直しも群ごとである。** `--max-fix-rounds` は 1 つの適用
    ラウンドあたりの上限だからである。
    """
    path, state = _load(args.id)
    entry = _round(state, args.round)
    groups = apply_groups(entry)

    # **`applied` の群も開き直す。** 適用は取り込んだが検証まで進めずに落ちた場合、
    # 飛ばすとその群の項目が採用でも取り消しでもないまま残る。再開できることは
    # 収束ループの前提である。
    opened = next(
        (g for g in groups if g.get("status") in {"pending", "applied"}), None
    )
    if opened is None:
        info(f"提案ラウンド {args.round} の適用ラウンドは残っていません")
        sys.exit(1)

    entry["apply_round"] = opened["apply_round"]
    if opened.get("status") == "pending":
        # 起点は**オーケストレータ側で**確定させる。実装担当の申告に委ねると、
        # 欠落・不正時に範囲検査が無効になり、過去の任意のコミットが実在扱いになる。
        head = _git_out(state["worktrees"]["work"], ["rev-parse", "HEAD"])
        opened["base_sha"] = head
        entry["apply_base_sha"] = head
        entry["fix_rounds"] = 0
        entry["apply"] = {
            "apply_round": opened["apply_round"],
            "applied": [], "failed": [],
            "base_sha": head, "head_sha": None, "merged_at": None,
        }
    else:
        # 取り込み済みの群を開き直した。**起点も修正の回数も動かさない。**
        info(f"↻ 適用ラウンド {opened['apply_round']} は取り込み済みです（検証から再開）")
        entry["apply_base_sha"] = opened.get("base_sha")
    state["phase"] = "apply"
    statefile.save(path, state)

    info(
        f"--- 適用ラウンド {opened['apply_round']} / {len(groups)} "
        f"（実装 {opened['impl']} / 項目 {', '.join(opened['items'])}）---"
    )
    statefile.emit(
        APPLY_ROUND=opened["apply_round"],
        APPLY_ROUNDS=len(groups),
        IMPL=opened["impl"],
        IMPL_MODEL=(opened.get("impl_model") or {}).get("requested"),
        APPLY_ITEMS=" ".join(opened["items"]),
        APPLY_ITEMS_CSV=",".join(opened["items"]),
    )


def cmd_merge_apply(args: argparse.Namespace) -> None:
    """Step 4 — 適用ラウンド 1 つ分の適用結果を検証して取り込む。

    終了コード: 0 = 取り込んだ / 2 = この群を取り消した（次の群へ進む）。

    **適用そのものが通らないときは修正ラウンドを回さない**（競合・対象が消えて
    いる・手順を外れた）。修正ラウンドはテストの失敗を直す工程であり、前提その
    ものが消えた項目には直す対象が無い。取り消したうえで除外の一覧へ記録する。

    **群の中は 1 コミットなので、1 件の失敗が群の全件を取り消す**（決定 2）。
    他の群には及ばない（受け入れ条件 A4）。
    """
    path, state = _load(args.id)
    entry = _round(state, args.round)
    group = current_group(entry)
    if not args.dry_run:
        _discard_impl_leftovers(state, state["worktrees"]["work"])
        _resume_incomplete_apply(path, state, entry)

    # **叩き直しても同じ判定を返す。** 取り込み済みで再実行すると、前回作った
    # 取り消しコミットが「未割当」と判定され、群ごと取り消してしまう。
    record = entry.get("apply") or {}
    if record.get("merged_at") and record.get("apply_round", group["apply_round"]) \
            == group["apply_round"]:
        applied_before = record.get("applied") or []
        info(
            f"↻ 適用ラウンド {group['apply_round']} の適用は取り込み済みです"
            f"（採用 {len(applied_before)} 件 / 失敗 "
            f"{len(record.get('failed') or [])} 件）"
        )
        if not applied_before:
            sys.exit(2)
        return

    payload, work, head_sha, ordered_range, in_range = _load_apply_context(
        path, state, entry, group, args
    )

    reported, unknown_ids = _collect_apply_reports(payload, group)

    _validate_apply_commit_ownership(
        path, state, entry, group, args, reported, unknown_ids, work,
        ordered_range, in_range, head_sha,
    )

    applied, failed = _verify_apply_group(
        path, state, entry, group, args, reported, work, in_range,
    )

    entry["apply"] = {
        "apply_round": group["apply_round"],
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
    group["head_sha"] = head_sha
    durations = entry.setdefault("durations", {})
    durations["apply"] = durations.get("apply", 0) + _safe_int(
        payload.get("elapsed_seconds")
    )

    # `--dry-run` では git も状態ファイルも触らない。片方だけ進むと、確認の
    # つもりで実行した利用者の進行が壊れる。
    if args.dry_run:
        if failed:
            _drop_items(state, entry, failed, dry_run=True)
        info("（dry-run）状態ファイルは更新していません")
        applied = list(entry["apply"]["applied"])
    elif failed:
        # `merged_at` は `_apply_drop` が取り消しの完了時点で立てる。
        applied = _apply_drop(path, state, entry, group, failed)
    else:
        # **全項目が通ったときも進行側が公開する。** 実装担当は push しないため、
        # ここで公開しないと Pull Request 上の差分が古いままになる。
        group["status"] = "applied"
        # 次は `verify-round` がテストで検証する。ここではまだ群を閉じない。
        state["phase"] = "verify"
        entry["apply"]["merged_at"] = statefile.now()
        entry["pending_push"] = True
        statefile.save(path, state)
        _push_head(state)
        entry["pending_push"] = False
        statefile.save(path, state)

    if not applied:
        info("この適用ラウンドは取り消しました。検証は行いません")
        sys.exit(2)


def _load_apply_context(
    path: pathlib.Path,
    state: dict[str, Any],
    entry: dict[str, Any],
    group: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[dict[str, Any], pathlib.Path, str, list[str], set[str]]:
    impl = group.get("impl") or entry["impl"]
    result = _result_path(state, impl, stem_for(impl, "apply", state["id"], args.round))
    payload = _read_result(result, impl)

    _record_observed_model(entry, "impl", impl, state, "apply", args.round)

    # 着手前のテストが**成功と確認できていない限り**適用結果を採らない。
    # `red` だけでなく `unknown`（確認していない）も拒否する。確認していない状態を
    # 通すと、「壊したのか元から壊れていたのか」を判別する手段が無いまま進む。
    baseline = state.get("baseline_test") or {}
    if baseline.get("status") != "green":
        for item_id in group["items"]:
            _find_item(state, item_id)["status"] = "blocked"
        if not args.dry_run:
            statefile.save(path, state)
        die(
            f"着手前のテストが成功と確認できていません（status={baseline.get('status')}）。"
            "適用へ着手しません（全項目を blocked）",
            code=2,
        )

    # 検証の材料は git から取る。結果ファイルから使うのは
    # 「どのコミットがこの群のものか」という対応付けだけ。
    work = state["worktrees"]["work"]
    head_sha = _git_out(work, ["rev-parse", "HEAD"]) or ""
    # 起点は `next-apply-round` が記録したもの。**実装担当の申告は使わない。**
    ordered_range = commits_in_range(work, entry.get("apply_base_sha"), head_sha)
    in_range = set(ordered_range or [])
    if ordered_range is None:
        # 範囲を確定できないなら、何も検証できない。素通しにせず失敗させる。
        for item_id in group["items"]:
            _find_item(state, item_id)["status"] = "blocked"
        if not args.dry_run:
            statefile.save(path, state)
        die(
            "適用の範囲を確定できませんでした"
            f"（起点 {entry.get('apply_base_sha')} / HEAD {head_sha}）。"
            "検証できない適用は採りません",
            code=2,
        )
    return payload, work, head_sha, ordered_range, in_range


def _collect_apply_reports(
    payload: dict[str, Any],
    group: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    # 申告は**この適用ラウンドの改善項目のものだけ**を採る。架空の項目 ID へ
    # 割り当てられたコミットを数に入れると、割り当て済みに見えるのに検証にも
    # 入らず、そのまま Pull Request に残せてしまう。
    round_items = set(group["items"])
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


def _reported_commit_shas(
    work: pathlib.Path, reported: dict[str, dict[str, Any]]
) -> list[str]:
    """申告コミットを完全な SHA へ正規化し、重複を除いて順に返す。

    **同じコミットを群の全項目が申告するのが正しい形である**（決定 2）。群の中は
    1 コミットにまとめるため、重複した申告は誤りではない。判定は**完全な SHA へ
    正規化してから**行う。申告の文字列をそのまま鍵にすると、一方が完全 SHA、
    他方が短縮 SHA で同じコミットを指したときに別物として数えてしまう。
    """
    shas: list[str] = []
    for r in reported.values():
        for sha in _reported_shas(r):
            full = _git_out(work, ["rev-parse", "--verify", f"{sha}^{{commit}}"])
            if full is None:
                full = sha        # 実在しない申告は群の検証で落ちる
            if full not in shas:
                shas.append(full)
    return shas


def _build_ownership_error_reason(
    unassigned: list[str], unknown_ids: list[str]
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
            f"この適用ラウンドに無い改善項目 ID の申告"
            f"（{', '.join(unknown_ids[:5])}）"
        )
    return (
        "、".join(causes)
        + "。検証を回避した変更や、状態と実差分の食い違いを Pull Request に"
          "残さないため、この適用ラウンドを取り消します"
    )


def _revert_unverified_apply_round(
    path: pathlib.Path,
    state: dict[str, Any],
    entry: dict[str, Any],
    group: dict[str, Any],
    args: argparse.Namespace,
    work: pathlib.Path,
    ordered_range: list[str],
    unassigned: list[str],
    unknown_ids: list[str],
    head_sha: str,
) -> None:
    """検証を通らない適用ラウンドの範囲を取り消し、状態と公開を反映する。"""
    # 範囲全体を取り消す。どのコミットが安全かを決められない以上、
    # 起点まで戻すのが最も確実である。順序は `_revert_item_commits` が
    # git の履歴から決め直す。
    whole_round = {
        "item_id": f"R{entry['round']}-A{group['apply_round']}",
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
        group["base_sha"] = entry["apply_base_sha"]
    entry["apply"] = {
        "apply_round": group["apply_round"],
        "applied": [], "failed": list(group["items"]),
        "base_sha": entry.get("apply_base_sha"), "head_sha": head_sha,
        "unassigned_commits": unassigned,
        "unknown_item_ids": unknown_ids,
        "merged_at": statefile.now(),
    }
    group["status"] = "dropped"
    state["phase"] = _phase_after_group(entry)
    if args.dry_run:
        info("（dry-run）状態ファイルは更新していません")
    else:
        # 項目別の失敗と同じく、**ここで取り消した項目も「対象外」に残す**。
        # 残さないと同じ提案が次のラウンドで再び採用される。
        _defer_abandoned_items(state, group)
        statefile.save(path, state)
        _push_head(state)
        entry["pending_push"] = False
        statefile.save(path, state)


def _validate_apply_commit_ownership(
    path: pathlib.Path,
    state: dict[str, Any],
    entry: dict[str, Any],
    group: dict[str, Any],
    args: argparse.Namespace,
    reported: dict[str, dict[str, Any]],
    unknown_ids: list[str],
    work: pathlib.Path,
    ordered_range: list[str],
    in_range: set[str],
    head_sha: str,
) -> None:
    # **範囲のコミットは全て、この適用ラウンドの申告に含まれていること。**
    # 申告から漏れたコミットはトレーラーも範囲も差分予算も検査されず、そのまま
    # Pull Request に残る。都合の悪い変更を申告しないだけで検査を回避できてしまう。
    reported_full = set(_reported_commit_shas(work, reported))

    unassigned = sorted(in_range - reported_full)
    if not (unassigned or unknown_ids):
        return

    reason = _build_ownership_error_reason(unassigned, unknown_ids)
    info(f"❌ {reason}")
    for item_id in group["items"]:
        it = _find_item(state, item_id)
        it["status"] = "abandoned"
        it["failure_reason"] = reason
    _revert_unverified_apply_round(
        path, state, entry, group, args, work, ordered_range,
        unassigned, unknown_ids, head_sha,
    )
    sys.exit(2)


def _verify_apply_group(
    path: pathlib.Path,
    state: dict[str, Any],
    entry: dict[str, Any],
    group: dict[str, Any],
    args: argparse.Namespace,
    reported: dict[str, dict[str, Any]],
    work: pathlib.Path,
    in_range: set[str],
) -> tuple[list[str], list[str]]:
    """適用ラウンドをまとめて検証し `(採用, 失敗)` を返す。

    **判定は全件同時である**（決定 3）。群の中は 1 コミットなので、失敗を項目まで
    特定しても取り消しは分離できない。
    """
    scope = state.get("target_scope") or []
    items = [_find_item(state, i) for i in group["items"]]

    # **群の全項目が同じコミットを申告する。** 申告の無い項目は、適用されたことを
    # 確かめる手がかりが無い。群の中は 1 コミットなので、1 件の欠落が群の全件を
    # 巻き込む（「群の中の道連れ」）。
    missing = [
        i for i in group["items"] if not _reported_shas(reported.get(i) or {})
    ]
    shas = _reported_commit_shas(work, reported)
    # **テストはここで走らせない。** 適用そのものが通ったかだけを見る
    # （テストコマンドを空で渡すと `collect_commit_facts` は実行しない）。
    facts = collect_commit_facts(
        work, shas, in_range, "", state["head_branch"],
        _safe_int(state.get("test_timeout"), DEFAULT_TEST_TIMEOUT),
    )
    if missing:
        problem = (
            f"適用結果に項目がありません: {', '.join(missing)}"
            "（群の全項目を 1 つのコミットへまとめ、各項目へ同じ SHA を申告します）"
        )
    else:
        problem = verify_apply_round(items, facts, scope)

    diff_lines = sum(_safe_int(c.get("diff_lines")) for c in facts)
    for item in items:
        item["commits"] = list(shas)
        if problem:
            item["status"] = "abandoned"
            item["failure_reason"] = problem
            item["budget_exceeded"] = "差分予算" in problem
            item["out_of_scope"] = "対象範囲の外" in problem
        else:
            item["status"] = "applied"
            item["diff_lines"] = diff_lines

    entry.setdefault("apply_progress", []).append({
        "apply_round": group["apply_round"], "at": statefile.now(),
        "result": "failed" if problem else "ok",
        "reason": problem, "commits": list(shas),
    })
    if problem:
        info(f"❌ 適用ラウンド {group['apply_round']}: {problem}")
    else:
        info(
            f"✅ 適用ラウンド {group['apply_round']}: "
            f"{len(shas)} コミット / {diff_lines} 行 / 項目 {len(items)} 件"
        )
    if not args.dry_run:
        statefile.save(path, state)
    if problem:
        return [], list(group["items"])
    return list(group["items"]), []


def _phase_after_group(entry: dict[str, Any]) -> str:
    """この群を終えた後のフェーズ。残りの群があれば適用を続ける。"""
    remaining = [
        g for g in entry.get("apply_rounds") or []
        if g.get("status") == "pending"
    ]
    return "apply" if remaining else "propose"


def _defer_abandoned_items(state: dict[str, Any], group: dict[str, Any]) -> None:
    """この適用ラウンドで取り消した項目を「対象外」として記録する。

    記録しないと、**同じ提案が次のラウンドで再び採用され、同じ理由で失敗する**。
    実測では適用で失敗した項目が 3 ランタイム全員から再提案され、合意数が最大に
    なって最優先で採用された。手順書が「同じ提案が毎ラウンド出続けて収束しない」
    として禁じている状態そのものである。

    除外の鍵は `path` + `symbol` + `smell` なので、その 3 つを必ず残す。
    """
    already = {d.get("item_id") for d in state["deferred_items"]}
    for item_id in group["items"]:
        item = _find_item(state, item_id, required=False)
        if item is None or item.get("status") != "abandoned" or item_id in already:
            continue
        state["deferred_items"].append({
            "item_id": item_id,
            "path": item["path"], "symbol": item["symbol"], "smell": item["smell"],
            "round": item.get("round"),
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
    group: dict[str, Any], failed: list[str],
) -> list[str]:
    """検証に失敗した適用ラウンドを取り消し、採用として残る項目 ID を返す。

    **中断しても再開できる形で記録する。** 失敗の位置で必要な再開が変わるため、
    印は次の順で切り替える。

    | 中断した位置 | 残る印 | 次の実行がすること |
    | --- | --- | --- |
    | 取り消しの途中 | `pending_drop` あり / `merged_at` なし | 取り消しをやり直す |
    | 取り消し後・push 前 | `pending_drop` なし / `merged_at` あり / `pending_push` あり | **push の再送だけ** |

    取り消しより先に `merged_at` を立てると、取り消しに失敗したときに次の実行が
    処理済みガードで素通りし、**再試行できない**。逆に push まで終えるまで
    `merged_at` を立てないと、push だけ失敗したときに次の実行が適用の検証をやり直し、
    取り消しと積み直しのコミットを「未割当」と判定して群ごと巻き込む。
    """
    work = state["worktrees"]["work"]
    result = _run_drop(path, state, entry, failed)
    applied = list(entry["apply"].get("applied") or [])
    if result["mode"] == "round":
        # 積み直せなかった。この群の全件を捨てる。**他の群には及ばない。**
        for item_id in group["items"]:
            it = _find_item(state, item_id)
            it["status"] = "abandoned"
            it.setdefault(
                "failure_reason",
                "残す項目を積み直せなかったため、適用ラウンドごと取り消した",
            )
        applied = []
        entry["apply"]["applied"] = []
        entry["apply"]["failed"] = list(group["items"])
    if not applied:
        # 取り消し後の状態を新しい起点にする（叩き直しでの二重取り消しを防ぐ）。
        entry["apply_base_sha"] = _git_out(work, ["rev-parse", "HEAD"])
        group["base_sha"] = entry["apply_base_sha"]
        group["status"] = "dropped"
    else:
        group["status"] = "applied"
    state["phase"] = _phase_after_group(entry)

    # 取り消した項目は「対象外」として残す。次のラウンドで同じ提案が採用され、
    # 同じ理由で失敗するのを防ぐ。
    _defer_abandoned_items(state, group)
    # **取り消しが済んだことを push より先に、印の解除と同じ保存で永続化する。**
    # 保存せずに push して失敗すると、次の実行が適用の検証をやり直し、取り消しと
    # 積み直しのコミットを「未割当」と判定して群ごと巻き込んでしまう。
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
        _apply_drop(path, state, entry, current_group(entry),
                    list(entry["pending_drop"]))
        return
    _flush_pending_push(path, state, entry)


def _prepare_fix_phase(state: dict[str, Any], entry: dict[str, Any]) -> None:
    """修正ラウンドへ入る前に、必要な記録を残す。

    - `fix_base_sha`: 修正の範囲の起点。無いと `merge-fix` が範囲を確定できず、
      `fix_rounds` が進まないまま修正と検証を往復し続ける
    - `fix_attempts`: 試行番号。`merge-fix` が「叩き直し」と「次のラウンド」を
      区別するのに使う
    """
    entry["fix_base_sha"] = _git_out(state["worktrees"]["work"], ["rev-parse", "HEAD"])
    entry["fix_attempts"] = entry.get("fix_attempts", 0) + 1
