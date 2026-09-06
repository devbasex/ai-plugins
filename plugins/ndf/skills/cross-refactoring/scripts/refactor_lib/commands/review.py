"""検証（テスト）と修正の工程。

`verify-round` / `should-abandon` / `abandon-items` / `merge-fix` を持つ。

**Step 5 の判定はテストの結果で決まる**（#436 決定 3）。2 CLI のレビューは
起動しない。判定の単位は適用ラウンドで、失敗を項目までは特定しない。
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import sys
from typing import Any

import statefile

from .. import die, info
from ..gitfacts import (
    _discard_impl_leftovers,
    _drop_items,
    _find_item,
    _flush_pending_push,
    _git_out,
    _push_head,
    _push_with_retry_marker,
    _read_result,
    _reported_shas,
    _revert_item_commits,
    _round,
    _run_with_timeout,
    _safe_int,
    collect_commit_facts,
    commits_in_range,
    resolved_threads_on_github,
)
from ..outbound import dropped_line, item_lines, plan_line
from ..paths import _load, _result_path, stem_for
from ..rounds import deferred_record
from ..verify import verify_commit_granularity, verify_fix_commit
from ..vocabulary import DEFAULT_TEST_TIMEOUT
from .apply import _phase_after_group, _prepare_fix_phase, _run_drop, current_group


def cmd_verify_round(args: argparse.Namespace) -> None:
    """Step 5 — 適用ラウンドの結果を**テストで**検証する。

    終了コード: 0 = テストが通った / 2 = 落ちた（修正ラウンドへ）。

    **2 CLI のレビューは起動しない**（決定 3）。`--baseline-test` が指す
    コマンドを作業ディレクトリの HEAD で実行し、その合否で決める。

    **失敗をどの項目に紐づけるかは決めない。** 適用ラウンドの中は 1 コミットで
    あり、分離しても取り消せない。判定の単位と取り消しの単位を一致させる。

    **継続的統合では代替しない。** 手元の未 push のコミットではなく push 済みの
    先端に対する結果しか読めないためである。代替できるのは Step 7 だけである。
    """
    path, state = _load(args.id)
    entry = _round(state, args.round)
    group = current_group(entry)
    applied = list((entry.get("apply") or {}).get("applied") or [])
    if not applied:
        die(
            f"適用ラウンド {group['apply_round']} に検証する項目がありません。"
            "先に `merge-apply` を通してください",
            code=2,
        )

    command = (state.get("baseline_test") or {}).get("command") or ""
    work = str(state["worktrees"]["work"])
    timeout = _safe_int(state.get("test_timeout"), DEFAULT_TEST_TIMEOUT)
    code, timed_out = _run_with_timeout(command, work, timeout)
    passed = (not timed_out) and code == 0

    entry.setdefault("verifications", []).append({
        "apply_round": group["apply_round"],
        "fix_round": entry.get("fix_rounds", 0),
        "at": statefile.now(),
        "command": command,
        "status": "pass" if passed else "fail",
        "exit_code": code,
        "timed_out": timed_out,
    })

    if passed:
        for item_id in applied:
            _find_item(state, item_id)["status"] = "done"
        group["status"] = "verified"
        state["phase"] = _phase_after_group(entry)
        statefile.save(path, state)
        info(f"✅ 適用ラウンド {group['apply_round']} のテストが通りました（{command}）")
        # **外へ出す文章の規約**（#436 決定 6-b）。項目は `<ファイル>#<シンボル>`
        # を併記し、改修計画は生の URL で添える。
        for line in item_lines(state, applied):
            info(f"   {line}")
        info(f"   {plan_line(state)}")
        return

    # **修正ラウンドの起点をここで記録する。** 記録せずに戻すと `merge-fix` が
    # 範囲を確定できずに弾かれ、`fix_rounds` が進まない。`should-abandon` は
    # `fix_rounds` で見送りを決めるため、上限へ永久に到達しなくなる。
    _prepare_fix_phase(state, entry)
    statefile.save(path, state)
    if timed_out:
        info(f"❌ テストが {timeout} 秒で終わりませんでした（{command}）")
    else:
        info(f"❌ テストが失敗しました（{command} / 終了コード {code}）")
    info(f"   {plan_line(state)}")
    sys.exit(2)


def cmd_should_abandon(args: argparse.Namespace) -> None:
    """Step 6 — この適用ラウンドの修正の上限に達したか。

    終了コード: 0 = 見送りへ移る / 2 = まだ修正できる。

    `--max-fix-rounds` は**1 つの適用ラウンドあたり**の上限である。数え直しは
    `next-apply-round` が群を開くときに行う。
    """
    _, state = _load(args.id)
    entry = _round(state, args.round)
    limit = state["max_fix_rounds"]
    if entry["fix_rounds"] >= limit:
        info(f"修正ラウンドが上限 {limit} に達しました。未解決の項目を見送ります")
        return
    info(f"修正ラウンド {entry['fix_rounds']} / {limit} — まだ修正します")
    sys.exit(2)


def cmd_abandon_items(args: argparse.Namespace) -> None:
    """Step 6 — テストが通らなかった適用ラウンドを取り消す。

    **取り消しの単位は適用ラウンドである**（決定 2）。群の中は 1 コミットなので、
    どの項目が落としたのかを特定しても分離して取り消せない。**他の群には及ばない**
    （受け入れ条件 A4）。既に検証を通った群は Pull Request に残る。
    """
    path, state = _load(args.id)
    entry = _round(state, args.round)
    group = current_group(entry)
    if not args.dry_run:
        # **やり残した取り消しを push の再送より先に片づける。** 先に push すると、
        # 取り消しが途中の HEAD をそのまま Pull Request へ反映してしまう。
        if entry.get("pending_drop"):
            info("↻ 前回終わらなかった取り消しを再実行します")
            _run_drop(path, state, entry, list(entry["pending_drop"]))
        else:
            _flush_pending_push(path, state, entry)

    # 取り消し自体は `reverted` で冪等だが、見送りの記録は重複しうる。
    if group.get("abandoned") is not None:
        info(f"↻ 適用ラウンド {group['apply_round']} の見送りは処理済みです"
             f"（{len(group['abandoned'])} 件）")
        return

    targets = list((entry.get("apply") or {}).get("applied") or [])
    if not targets:
        info("取り消す項目はありません")
        if not args.dry_run:
            group["abandoned"] = []
            entry["abandoned"] = []
            statefile.save(path, state)
        return

    if args.dry_run:
        _drop_items(state, entry, targets, dry_run=True)
        info("（dry-run）状態ファイルは更新していません")
        return

    _run_drop(path, state, entry, targets)

    already = {d.get("item_id") for d in state["deferred_items"]}
    for item_id in targets:
        item = _find_item(state, item_id)
        item["status"] = "abandoned"
        item.setdefault(
            "failure_reason", "修正ラウンドの上限に達してもテストが通らなかった")
        if item_id in already:
            continue
        state["deferred_items"].append(
            deferred_record(item, item_id, item["failure_reason"]))

    # 見送りの記録と印の解除を**同じ保存で**行う。保存してから push するので、
    # push が失敗しても記録とローカルの git が食い違わない。
    # **内訳は書かない。件数だけ述べ、内訳は改修計画へ譲る**（#436 決定 6-b）。
    info(f"↩ 適用ラウンド {group['apply_round']}: {dropped_line(state, len(targets))}")
    group["abandoned"] = targets
    group["status"] = "dropped"
    entry["abandoned"] = targets
    entry["pending_drop"] = []
    entry["apply_base_sha"] = _git_out(
        state["worktrees"]["work"], ["rev-parse", "HEAD"])
    group["base_sha"] = entry["apply_base_sha"]
    state["phase"] = _phase_after_group(entry)
    statefile.save(path, state)
    _push_head(state)
    entry["pending_push"] = False
    statefile.save(path, state)


def _fix_merge_key(entry: dict[str, Any], result: pathlib.Path) -> str:
    """修正結果の取り込み済み判定に使う鍵を作る。

    **叩き直しても二重に取り込まない。** 修正は同じラウンドで何度も回るため、
    「このラウンドで処理済みか」では判定できない。**入力が前回と同じか**で見る。
    次の修正ラウンドでは結果ファイルが上書きされ、HEAD も進むので鍵が変わる。
    鍵は**試行番号と結果ファイルの内容**から作る。

    - HEAD は混ぜない。検証に失敗して取り消すと HEAD が変わるため、鍵が一致せず
      同じ申告を再処理してしまう。
    - 内容だけでも足りない。次の修正ラウンドが同じ JSON（コミットなし・同じ
      未解決 ID など）を返すと過去のラウンドと衝突し、`fix_rounds` が進まないまま
      同じ修正を起動し続ける。
    - ファイルの更新時刻も使わない。粒度が環境によって違い、書き直しても同じ値に
      なりうる。

    修正の前には必ず `judge-review` が走るので、そこで進めた試行番号が
    **実行単位の識別子**になる。叩き直しただけなら番号は変わらない。
    """
    attempt = entry.get("fix_attempts", 0)
    return f"{attempt}:" + hashlib.sha256(result.read_bytes()).hexdigest()


def _already_merged_fix_result(
    entry: dict[str, Any], merge_key: str
) -> bool:
    """この修正結果を取り込み済みなら真を返し、鍵の一覧を更新する。"""
    merged_keys = entry.setdefault("fix_merged_keys", [])
    if merge_key in merged_keys:
        info(
            f"↻ この修正結果は取り込み済みです"
            f"（修正ラウンド {entry['fix_rounds']}）"
        )
        return True
    return False


def _resolved_fix_thread_ids(payload: dict[str, Any], repo: str, pr: int) -> set[str]:
    """自己申告と GitHub 側の解決状態を突き合わせ、両方が解決と言う ID だけ返す。

    自己申告をそのまま信じない。解決 API に失敗・未実行でも「解決済み」と
    書けてしまい、未解決の指摘が取り消し対象から外れる。GitHub 側の
    `isResolved` と突き合わせ、**両方が解決と言っているものだけ**を反映する。
    """
    raw_claimed = payload.get("resolved_thread_ids")
    # 文字列は 1 文字ずつに分解され、数値や真偽値は反復できずに落ちる。
    # **配列であることを先に確かめる。**
    claimed = {
        t for t in (raw_claimed if isinstance(raw_claimed, list) else [])
        if isinstance(t, str) and t.strip()
    }
    if raw_claimed is not None and not isinstance(raw_claimed, list):
        info(f"⚠ resolved_thread_ids が配列ではありません（{type(raw_claimed).__name__}）。"
             "解決の申告は無かったものとして扱います")
    actual = resolved_threads_on_github(repo, pr)
    if actual is None:
        info("⚠ レビュースレッドの解決状態を取得できませんでした。"
             "自己申告は採用せず、未解決のまま扱います")
        return set()
    resolved = claimed & actual
    for thread_id in sorted(claimed - actual):
        info(f"⚠ {thread_id} は解決済みと申告されましたが、GitHub では未解決です")
    return resolved


def _unassigned_fix_commits(
    work: str, reported_shas: list[str], ordered_range: list[str]
) -> list[str]:
    """範囲内のコミットのうち、どの申告にも含まれていないものを返す。

    適用と同じく、**範囲のコミットは全て申告されていること**を求める。
    申告から漏れた修正コミットは検証を受けないまま Pull Request に残る。
    """
    reported_full = {
        full for full in (
            _git_out(work, ["rev-parse", "--verify", f"{s}^{{commit}}"])
            for s in reported_shas
        ) if full
    }
    return sorted(set(ordered_range) - reported_full)


def _verify_fix_commits(
    facts: list[dict[str, Any]], scope: list[str]
) -> tuple[list[str], list[tuple[str, str]]]:
    """修正コミットを検証し、問題点の一覧と受理した (item_id, sha) を返す。

    **不正なコミットが 1 件でもあれば、修正ラウンドの範囲ごと取り消す。**
    状態を記録しないだけでは、未検証の変更が Pull Request に残り続ける
    （見送りの対象にもならない）。どのコミットが安全かは決められないので、
    適用フェーズの未割当コミットと同じ扱いにする。
    """
    problems: list[str] = []
    accepted: list[tuple[str, str]] = []      # (item_id, sha)
    seen: dict[str, set[str]] = {}            # item_id -> 実在するコミットの集合
    for commit in facts:
        item_id = (commit.get("trailers") or {}).get("Item-Id")
        problem = verify_fix_commit(commit, scope)
        if problem:
            problems.append(problem)
            info(f"❌ 修正コミットが手順を満たしていません: {problem}")
            continue
        seen.setdefault(item_id, set()).add(commit["sha"])
        accepted.append((item_id, commit["sha"]))

    # 粒度は 1 件ずつの検証が済んでから見る。壊れたコミットの理由を
    # 粒度の失敗で覆い隠さない。
    for item_id, shas in seen.items():
        problem = verify_commit_granularity({"item_id": item_id}, len(shas))
        if problem:
            problems.append(problem)
            info(f"❌ 修正コミットが手順を満たしていません: {problem}")
    return problems, accepted


def _mark_resolved_fix_findings(entry: dict[str, Any], resolved: set[str]) -> None:
    """GitHub 側で解決済みになった thread に対応する指摘へ、解決の印を付ける。"""
    for review in entry["reviews"]:
        for finding in review["findings"]:
            if finding.get("thread_id") not in resolved:
                continue
            finding["resolved"] = True


def _record_accepted_fix_commits(
    state: dict[str, Any], accepted: list[tuple[str, str]]
) -> None:
    """検証を通った修正コミットを、対応する改善項目へ紐づける。

    見送り済みなどで項目が見つからないコミットは、紐づけ先が無いので飛ばす。
    """
    for item_id, sha in accepted:
        item = _find_item(state, item_id, required=False)
        if item is not None:
            item.setdefault("commits", []).append(sha)


def _revert_invalid_fix_round(
    path: pathlib.Path,
    state: dict[str, Any],
    entry: dict[str, Any],
    ordered_range: list[str],
) -> set[str]:
    """検証を通らない修正ラウンドの範囲を取り消し、採用する解決スレッドを返す。

    取り消した以上、解決の申告も採らないので**常に空集合を返す**。
    """
    work = state["worktrees"]["work"]
    # **状態へ記録する前に取り消す。** 先に記録すると、取り消し済みのコミットが
    # 状態ファイルに残り、後の見送り処理が同じコミットをもう一度取り消そうとする。
    info("検証を通らない変更を残さないため、この修正ラウンドの範囲を取り消します")
    # **取り消しへ着手する前に印を立てる。** 取り消しは済んだのに push できずに
    # 終わると、未検証の変更が Pull Request に残ったままになる。
    entry["pending_push"] = True
    statefile.save(path, state)
    _revert_item_commits(
        state,
        {"item_id": f"R{entry['round']}-fix{entry['fix_rounds'] + 1}",
         "commits": list(ordered_range)},
        dry_run=False,
    )
    # 取り消し後の状態を新しい起点にし、**その場で保存する**。ここで保存せずに
    # 落ちると、次の実行は古い起点から範囲を取り直して取り消しコミット自体を
    # 「未申告」と判定し、**取り消しを取り消して**しまう。
    entry["fix_base_sha"] = _git_out(work, ["rev-parse", "HEAD"])
    statefile.save(path, state)
    # **push は保存のあと。** ここで push して失敗すると、取り消しコミットは
    # ローカルに残るのに起点の更新が保存されず、叩き直しで二重に取り消してしまう。
    info("⚠ 修正を取り消したため、解決の申告は採用しません")
    return set()


def cmd_merge_fix(args: argparse.Namespace) -> None:
    """Step 6 — 修正結果を取り込み、修正ラウンドを 1 つ進める。"""
    path, state = _load(args.id)
    entry = _round(state, args.round)
    _discard_impl_leftovers(state, state["worktrees"]["work"])
    _flush_pending_push(path, state, entry)
    impl = entry["impl"]
    result = _result_path(state, impl, stem_for(impl, "fix", state["id"], args.round))
    payload = _read_result(result, impl)

    work = state["worktrees"]["work"]
    head_now = _git_out(work, ["rev-parse", "HEAD"]) or ""
    merge_key = _fix_merge_key(entry, result)
    if _already_merged_fix_result(entry, merge_key):
        return
    merged_keys = entry["fix_merged_keys"]

    resolved = _resolved_fix_thread_ids(payload, state["repo"], state["current_pr"])

    # 修正コミットも適用と同じ基準で、**git と実際のテスト実行から**検証する。
    # 結果ファイルの申告で済ませると、手順を満たさない変更が収束済みになれてしまう。
    baseline = state.get("baseline_test") or {}
    # 修正の範囲も**オーケストレータが記録した起点**から取る。起点は
    # `judge-review` が変更要求を返したときの HEAD である。
    ordered_range = commits_in_range(work, entry.get("fix_base_sha"), head_now)
    if ordered_range is None:
        # **修正ラウンドは進める。** 進めないと `should-abandon` が見送りへ移る
        # 条件（`fix_rounds` が上限に達する）を永久に満たさず、修正フェーズと
        # 再レビューを無限に往復する。この修正は採らないので、範囲外の記録は
        # 何も足さない。
        entry["fix_rounds"] += 1
        statefile.save(path, state)
        die(
            "修正の範囲を確定できませんでした"
            f"（起点 {entry.get('fix_base_sha')} / HEAD {head_now}）。"
            "検証できない修正は採りません",
            code=2,
        )
    reported_shas = _reported_shas(payload)
    unassigned = _unassigned_fix_commits(work, reported_shas, ordered_range)

    facts = collect_commit_facts(
        work, reported_shas, set(ordered_range),
        baseline.get("command") or "true", state["head_branch"],
        _safe_int(state.get("test_timeout"), DEFAULT_TEST_TIMEOUT),
    )

    problems, accepted = _verify_fix_commits(facts, state.get("target_scope") or [])

    if unassigned:
        info(
            f"❌ どの申告にも含まれていない修正コミットが {len(unassigned)} 件あります"
            f"（{', '.join(s[:7] for s in unassigned[:5])}）"
        )

    if unassigned or problems:
        resolved = _revert_invalid_fix_round(path, state, entry, ordered_range)
    else:
        _record_accepted_fix_commits(state, accepted)

    _mark_resolved_fix_findings(entry, resolved)

    merged_keys.append(merge_key)
    entry["fix_rounds"] += 1

    entry.setdefault("durations", {})["fix"] = (
        entry.get("durations", {}).get("fix", 0)
        + _safe_int(payload.get("elapsed_seconds"))
    )
    statefile.save(path, state)
    # **取り消したかどうかに関わらず公開する。** 実装担当は push しないため、
    # ここで公開しないと再レビューが Pull Request 上の差分を見られない。
    _push_with_retry_marker(path, state, entry)
    info(
        f"修正を取り込みました（解決 {len(resolved)} スレッド / "
        f"修正ラウンド {entry['fix_rounds']}）。{plan_line(state)}"
    )
