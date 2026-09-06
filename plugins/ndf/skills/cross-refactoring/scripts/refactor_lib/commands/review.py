"""レビューと修正の工程。

`review-targets` / `judge-review` / `should-abandon` / `abandon-items` /
`merge-fix` を持つ。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from typing import Any, Callable

import statefile

from .. import ABORT, die, info
from ..gitfacts import (
    _discard_impl_leftovers,
    _drop_items,
    _find_item,
    _flush_pending_push,
    _git_out,
    _push_head,
    _push_with_retry_marker,
    _read_result,
    _record_observed_model,
    _reported_shas,
    _revert_item_commits,
    _round,
    _safe_int,
    collect_commit_facts,
    commits_in_range,
    resolved_threads_on_github,
)
from ..paths import _load, _result_path, stem_for
from ..review import (
    _requested_changes,
    _unposted_reviewers,
    judge,
    unresolved_item_ids,
)
from ..verify import verify_commit_granularity, verify_fix_commit
from ..vocabulary import DEFAULT_TEST_TIMEOUT, MAX_INVALID_REVIEWS
from .apply import _prepare_fix_phase, _run_drop


def cmd_review_targets(args: argparse.Namespace) -> None:
    """Step 5 — 次に起動するレビュー担当を返す。

    **初回と再レビューの区別は状態が持つ。** 呼び出し側は同じコマンドを 2 回呼ぶだけで、
    どちらかを引数で伝えない。ラウンドの記録に `fix_reviewers` があれば再レビュー、
    無ければ初回である。**このキーを持たない既存の状態ファイルは初回として読む。**

    差し戻し（`invalid`）はこのキーを書かないため、2 者へ戻る。結果の形が判定に使えない
    状態は修正の成否とは別で、承認した担当の結果も読めていない可能性がある。

    終了コード: 0 = 対象を返した / 4（`ABORT`）= ラウンドが無い、または対象が 0 人。
    """
    _, state = _load(args.id)
    entry = _round(state, args.round)
    targets = entry.get("fix_reviewers")
    if targets is None:
        targets = entry["reviewers"]
    if not targets:
        die(
            f"ラウンド {args.round} の再レビューの対象が 0 人です。"
            "判定できない状態のまま進めません"
        )
    print(f"REVIEW_TARGETS='{' '.join(targets)}'")
    print(f"REVIEW_TARGETS_CSV={','.join(targets)}")


def cmd_judge_review(args: argparse.Namespace) -> None:
    """Step 5 — レビュー担当の判定を取り込む。

    終了コード: 0 = 2 者とも承認 / 2 = 修正へ / 3 = 差し戻して再レビュー。
    """
    path, state = _load(args.id)
    entry = _round(state, args.round)
    reviewers = entry["reviewers"]

    reviews: dict[str, dict[str, Any]] = {}
    # 鍵には**修正の世代**を含める。1 回修正したあとに同じ指摘文が返ってくることは
    # 普通にあり、内容だけで見ると「叩き直し」と区別できず、起点も試行番号も
    # 更新されないまま止まってしまう。
    digest = hashlib.sha256(f"fix{entry.get('fix_rounds', 0)}:".encode("ascii"))
    for name in reviewers:
        result = _result_path(state, name, stem_for(name, "review", state["id"], args.round))
        digest.update(name.encode("utf-8"))
        if not result.exists():
            info(f"⚠ {name} のレビュー結果がありません: {result}")
            continue
        digest.update(result.read_bytes())
        try:
            reviews[name] = json.loads(result.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            info(f"⚠ {name} のレビュー結果が JSON として読めません: {e}")
        _record_observed_model(entry, "reviewer", name, state, "review", args.round)

    # **投稿の確認は結果ファイルの内容では決まらない。** GitHub 側の状態なので、
    # 鍵に入れずに判定を再生すると、投稿が見えるようになった後で叩き直しても
    # 差し戻しを返し続け、進行が止まる。確認の結果まで同じときだけ再生する。
    unposted, post_problems = _unposted_reviewers(state, reviews, reviewers)
    digest.update(("unposted:" + ",".join(sorted(unposted))).encode("utf-8"))

    # **同じレビュー結果で叩き直しても、記録も起点も試行番号も動かさない。**
    # 動かすと、同じ修正結果を別の試行として再処理したり、修正コミットを検証範囲の
    # 外へ追い出したりできてしまう。前回の終了コードだけを再現する。
    review_key = digest.hexdigest()
    for seen in entry.get("review_merged", []):
        if seen.get("key") == review_key:
            info(f"↻ このレビュー結果は判定済みです（前回の終了コード {seen['exit']}）")
            if seen["exit"]:
                sys.exit(seen["exit"])
            return

    verdict, problems, record = _aggregate_review_results(
        entry, reviewers, reviews, post_problems
    )
    statefile.save(path, state)

    def _remember(exit_code: int) -> None:
        entry.setdefault("review_merged", []).append(
            {"key": review_key, "exit": exit_code}
        )

    _handle_review_verdict(
        path, state, entry, reviewers, reviews, unposted,
        verdict, problems, record, _remember,
    )


def _handle_review_verdict(
    path: pathlib.Path,
    state: dict[str, Any],
    entry: dict[str, Any],
    reviewers: list[str],
    reviews: dict[str, dict[str, Any]],
    unposted: list[str],
    verdict: str,
    problems: list[str],
    record: dict[str, Any],
    remember: Callable[[int], None],
) -> None:
    if verdict == "invalid":
        for p in problems:
            info(f"❌ {p}")
        # **差し戻しは絞り込みを解く。** 変更要求で絞った後に形式の誤りが出た場合、
        # 絞ったままだと差し戻しの再レビューが 1 者だけで行われる。結果の形が判定に
        # 使えない状態は修正の成否とは別である。
        entry.pop("fix_reviewers", None)
        entry["invalid_reviews"] = entry.get("invalid_reviews", 0) + 1
        if entry["invalid_reviews"] > MAX_INVALID_REVIEWS:
            # **結果が無いことと、形が違うことを分ける。** 結果を残さなかったのは
            # レビュー担当のプロセスが仕事をしなかったということで、実装担当が
            # 直せる指摘ではない。変更要求へ落とすと、直しようのない指摘を渡された
            # 実装担当が空回りし、承認済みの項目まで見送りへ進む。
            missing = [name for name in reviewers if name not in reviews]
            # **投稿できなかった担当も同じ扱いにする。** 判定は残っていても
            # Pull Request に指摘が無い以上、実装担当が読めるものは存在しない。
            blocked = missing + [name for name in unposted if name not in missing]
            if blocked:
                remember(ABORT)
                statefile.save(path, state)
                die(
                    f"レビュー担当 {' / '.join(blocked)} が結果を残せませんでした"
                    "（結果ファイルの欠落、または投稿の失敗）。"
                    "実装担当への指摘ではないため、進行を中断します。"
                    "原因を直して同じコマンド列を叩き直せば再開できます"
                )
            # 差し戻しを無限に繰り返さない。形式を満たせないレビューが続く以上、
            # このラウンドの成果は検証されていないものとして扱い、変更要求へ落とす。
            # 紐づけ先が決まらないので、取り消しはラウンド全件が対象になる。
            record["findings"].append({
                "reviewer": "cross-refactoring",
                "item_id": None,
                "thread_id": None,
                "summary": (
                    f"レビュー結果の形式が {MAX_INVALID_REVIEWS + 1} 回続けて不正だった: "
                    + " / ".join(problems)
                ),
                "resolved": False,
            })
            # 絞り込みは上で解いたままにする。合成した指摘は誰が出したものでもなく、
            # 再レビューの対象が決まらない。
            # **この出口も修正フェーズの起点を記録する。** 記録せずに変更要求を
            # 返すと `merge-fix` が範囲を確定できずに弾かれ、`fix_rounds` が
            # 進まない。`should-abandon` は `fix_rounds` で見送りを決めるため、
            # 上限へ永久に到達せず修正と再レビューを往復し続ける。
            _prepare_fix_phase(state, entry)
            remember(2)
            statefile.save(path, state)
            info("差し戻しの上限に達したため、変更要求として扱います")
            sys.exit(2)
        remember(3)
        statefile.save(path, state)
        info("レビュー結果を差し戻します。指摘には必ず改善項目 ID を付けてください")
        sys.exit(3)
    if verdict == "approved":
        for item_id in entry["apply"]["applied"]:
            _find_item(state, item_id)["status"] = "done"
        state["phase"] = "propose"
        remember(0)
        statefile.save(path, state)
        info("✅ レビュー担当 2 者とも承認しました")
        return
    # **再レビューの対象を、変更要求を出した担当だけに絞る。** 差し戻し上限からの
    # 落ちこみでは書かない。合成した指摘は誰が出したものでもなく、対象が決まらない。
    entry["fix_reviewers"] = _requested_changes(reviewers, reviews)
    _prepare_fix_phase(state, entry)
    remember(2)
    statefile.save(path, state)
    open_findings = sum(1 for f in record["findings"] if not f["resolved"])
    info(f"変更要求があります（未解決の指摘 {open_findings} 件）")
    sys.exit(2)


def _aggregate_review_results(
    entry: dict[str, Any],
    reviewers: list[str],
    reviews: dict[str, dict[str, Any]],
    post_problems: list[str],
) -> tuple[str, list[str], dict[str, Any]]:
    verdict, problems = judge(reviews, reviewers, entry["items"])
    if post_problems:
        verdict = "invalid"
        problems = problems + post_problems

    # 記録も**型検査済みの値だけ**で作る。`judge()` が invalid と判定した入力でも
    # ここを通るため、無条件に `.get()` を呼ぶと差し戻す前に落ちる。
    record: dict[str, Any] = {"round": len(entry["reviews"]) + 1, "findings": []}
    for name in reviewers:
        review = reviews.get(name)
        review = review if isinstance(review, dict) else {}
        record[name] = review.get("verdict")
        findings = review.get("findings")
        for finding in findings if isinstance(findings, list) else []:
            if not isinstance(finding, dict):
                continue
            record["findings"].append({
                "reviewer": name,
                "item_id": finding.get("item_id"),
                "thread_id": finding.get("thread_id"),
                "summary": finding.get("summary"),
                "resolved": bool(finding.get("resolved")),
            })
    entry["reviews"].append(record)
    # レビュー担当ごとの所要時間は**別々に**持つ。ラウンドの合計を各担当へ配ると、
    # 2 者分を両方に数えることになり、担当同士の比較が成り立たない。
    per_reviewer = entry.setdefault("reviewer_seconds", {})
    for name in reviewers:
        review = reviews.get(name)
        elapsed = review.get("elapsed_seconds") if isinstance(review, dict) else 0
        per_reviewer[name] = per_reviewer.get(name, 0) + _safe_int(elapsed)
    entry.setdefault("durations", {})["review"] = sum(per_reviewer.values())
    return verdict, problems, record


def cmd_should_abandon(args: argparse.Namespace) -> None:
    """Step 6 — 修正ラウンドの上限に達したか。

    終了コード: 0 = 見送りへ移る / 2 = まだ修正できる。
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
    """Step 6 — 未解決の指摘に紐づく改善項目だけを取り消す。

    **合意済みの項目は Pull Request に残す。** これを可能にするために、適用は
    項目ごとに 1 コミットへまとめ、状態ファイルへコミットを記録している。
    """
    path, state = _load(args.id)
    entry = _round(state, args.round)
    if not args.dry_run:
        # **やり残した取り消しを push の再送より先に片づける。** 先に push すると、
        # 取り消しが途中の HEAD をそのまま Pull Request へ反映してしまう。
        if entry.get("pending_drop"):
            info("↻ 前回終わらなかった取り消しを再実行します")
            _run_drop(path, state, entry, list(entry["pending_drop"]))
        else:
            _flush_pending_push(path, state, entry)

    # 取り消し自体は `reverted` で冪等だが、見送りの記録は重複しうる。
    if entry.get("abandoned") is not None:
        info(f"↻ ラウンド {args.round} の見送りは処理済みです"
             f"（{len(entry['abandoned'])} 件）")
        return

    targets, whole_round = unresolved_item_ids(entry["reviews"], entry["apply"]["applied"])
    if whole_round:
        info(
            "どの項目にも紐づかない未解決の指摘があるため、"
            "このラウンドで適用した項目を全件取り消します"
        )
    if not targets:
        info("取り消す項目はありません")
        if not args.dry_run:
            entry["abandoned"] = []
            statefile.save(path, state)
        return

    if args.dry_run:
        _drop_items(state, entry, targets, dry_run=True)
        info("（dry-run）状態ファイルは更新していません")
        return

    result = _run_drop(path, state, entry, targets)
    if result["mode"] == "round":
        info("積み直せなかったため、このラウンドで適用した項目を全件見送ります")
        targets = list(entry["apply"].get("applied") or targets)

    already = {d.get("item_id") for d in state["deferred_items"]}
    for item_id in targets:
        item = _find_item(state, item_id)
        item["status"] = "abandoned"
        item.setdefault("failure_reason", "修正ラウンドの上限に達しても指摘が解決しなかった")
        if item_id in already:
            continue
        state["deferred_items"].append({
            "item_id": item_id,
            "path": item["path"], "symbol": item["symbol"], "smell": item["smell"],
            "round": entry["round"],
            "defer_reason": item["failure_reason"],
        })
        info(f"↩ {item_id} を見送りました")

    # 見送りの記録と印の解除を**同じ保存で**行う。保存してから push するので、
    # push が失敗しても記録とローカルの git が食い違わない。
    entry["abandoned"] = targets
    entry["pending_drop"] = []
    state["phase"] = "propose"
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
    info(f"修正を取り込みました（解決 {len(resolved)} スレッド / 修正ラウンド {entry['fix_rounds']}）")
