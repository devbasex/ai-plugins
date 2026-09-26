"""副命令 `judge` と、その続きの `flush`（#1142 の C2）。"""
from __future__ import annotations

import argparse
import sys
from typing import Any

import review_lib  # noqa: E402
import monitor_outcome  # noqa: E402
from review_lib import (  # noqa: E402
    ci as ci_mod, findings as findings_mod, matching, participants as participants_mod, posts, store)


def cmd_flush(args: argparse.Namespace) -> None:
    """待ち行列に積んだ投稿を流す。**終了コードは常に 0**（工程を止めない）。"""
    pr = args.pr
    q = posts._queue(pr)
    before = q.count()
    result = q.flush()
    for item in result.sent + result.skipped:
        posts._confirm_flushed(pr, item)
    print(f"PENDING_BEFORE={before}")
    print(f"PENDING_SENT={len(result.sent)}")
    print(f"PENDING_SKIPPED={len(result.skipped)}")
    print(f"PENDING_DROPPED={len(result.dropped)}")
    print(f"PENDING_REMAINING={result.remaining}")
    for item in result.dropped:
        review_lib.info(f"⚠️ 送れない項目を飛ばしました ({item.get('kind')} #{item.get('seq')}):"
             f" {item.get('last_error') or ''}")
    if result.remaining:
        reason = (result.failed or {}).get("last_error", "")
        review_lib.info(
            f"⏳ 待ち行列に {result.remaining} 件残っています"
            f"{'（まだ上限です）' if result.rate_limited else ''}"
            f"{f': {reason}' if reason and not result.rate_limited else ''}"
        )
    else:
        review_lib.info(f"✅ 待ち行列は空です（送った {len(result.sent)} 件）")


def _no_result_reasons(last: dict[str, Any], no_result: list[str]) -> dict[str, str]:
    """結果なしの担当ごとの理由を集め、`NO_RESULT_REASONS` を出す。

    どの出口でも進行側が理由を読めるように、先頭で 1 度だけ出す（#729 の AC14）。
    """
    reasons = {
        a: (last.get(a) or {}).get("no_result_reason") or "missing" for a in no_result
    }
    print(f"NO_RESULT_REASONS='{' '.join(f'{a}={r}' for a, r in reasons.items())}'")
    return reasons


def _abort_no_result_round(pr: int, st: dict[str, Any], msg: str) -> None:
    """結果なしのラウンドを異常終了させる共通処理。

    `final=error` にして保存し、終了コード 1 で止める。最終スイープを通してから
    完了報告へ進むよう促す文言は呼び出し側が渡す。
    """
    st["final"] = "error"
    st["ended_at"] = review_lib._now()
    store._save(pr, st)
    review_lib.die(msg, code=1)


def _record_relaunch(
    pr: int, st: dict[str, Any], last: dict[str, Any], pending: list[str]
) -> None:
    """同じラウンドで起動し直す担当を記録し、シェル向けの出力を出す。"""
    last["relaunched"] = (last.get("relaunched") or []) + pending
    store._save(pr, st)
    print(f"RELAUNCH_AGENTS='{' '.join(pending)}'")
    print(f"RELAUNCH_AGENTS_CSV={','.join(pending)}")
    # 互換のために残す。**`both` は codex / agy の 2 者だけを指す語**であるため、
    # 担当がそれ以外を含むラウンドでは CSV の側を使う。
    print(f"RELAUNCH_TARGET={'both' if len(pending) == 2 else pending[0]}")
    review_lib.info(
        f"→ 結果を残さなかったレビュアーがいる: {' '.join(pending)}。"
        "同じラウンドで 1 度だけ起動し直す。"
    )


def _handle_no_result_round(
    pr: int, st: dict[str, Any], last: dict[str, Any], no_result: list[str]
) -> None:
    """結果なしの担当があるラウンドの出口を決める。

    先に理由の行（`NO_RESULT_REASONS`）を出す。どの出口でも進行側が理由を読めるようにする
    ためである（#729 の AC14）。**起動し直しの可否は結末の共通層だけが決める**
    （`monitor_outcome.relaunch_same_agent`）。可否が偽の理由が 1 つでもあれば、誰も起動し直さず
    誤りの終わりへ進む。起動し直しても解けない理由で待つのは、相手の CLI の枠と時間を使うだけ
    である（#619）。骨組みは既存の 1 の枝で受けるため、終了コードは増えない（決定 12）。
    """
    last["verdict"] = "no_result"
    reasons = _no_result_reasons(last, no_result)
    blocked = [a for a, r in reasons.items()
               if not monitor_outcome.relaunch_same_agent(r)]
    if blocked:
        for a in blocked:
            detail = (last.get(a) or {}).get("monitor_detail")
            review_lib.info(f"  {a}: reason={reasons[a]}" + (f" detail={detail}" if detail else ""))
        _abort_no_result_round(
            pr, st,
            f"起動し直しても解けない理由で結果が残りませんでした: {' '.join(blocked)}。"
            " 同じラウンドで起動し直さずに中断します。最終スイープを通してから"
            "完了報告へ進んでください",
        )
    relaunched = last.get("relaunched") or []
    pending = [a for a in no_result if a not in relaunched]
    if not pending:
        # 2 度続けて結果が残らないのは、対象や負荷ではなく実行環境の側の事象である。
        _abort_no_result_round(
            pr, st,
            f"起動し直した後も結果が残りませんでした: {' '.join(no_result)}。"
            " 実行環境の側の問題として中断します。最終スイープを通してから"
            "完了報告へ進んでください",
        )
    _record_relaunch(pr, st, last, pending)
    sys.exit(7)


def _finalize_converged_round(
    pr: int,
    st: dict[str, Any],
    last: dict[str, Any],
    findings_measurable: bool,
) -> None:
    ci = ci_mod._round_ci(st, last, pr)
    last["ci"] = ci
    print(f"CI_VERDICT={ci['verdict']}")
    if ci["verdict"] == "code_failure":
        last["verdict"] = "changes_requested"
        store._save(pr, st)
        review_lib.info(
            f"→ 両方 APPROVE だが継続的統合が失敗している: {' '.join(ci['failed'])}。"
            "修正へ。"
        )
        sys.exit(2)
    last["verdict"] = "approved"
    st["final"] = "approved"
    st["ended_at"] = review_lib._now()
    store._save(pr, st)
    if ci["verdict"] == "meta_only":
        review_lib.info(f"⚠ {ci['note']}")
    elif ci["verdict"] == "pending":
        review_lib.info(
            f"⚠ 未完了のチェックジョブが残ったまま収束する: {' '.join(ci['pending'])}。"
            "完了は待たない"
        )
    elif ci["verdict"] == "unverified":
        review_lib.info(f"⚠ 継続的統合を確かめられないまま収束する: {ci['reason']}")
    review_lib.info(
        "✅ 新しい指摘が出なくなった。収束。" if findings_measurable
        else "✅ 全員が承認した。収束。"
    )
    sys.exit(0)


def _print_judge_status(
    reviewers: list[str],
    intents: dict[str, str],
    new_findings: int,
    findings_measurable: bool,
    carried_count: int,
    pending_posts: int,
) -> None:
    """`cmd_judge` の冒頭で出す状態表示の print 群。"""
    print("REVIEWER_INTENTS='" + " ".join(
        f"{a}={intents[a]}" for a in reviewers) + "'")
    print(f"NEW_FINDINGS={new_findings if findings_measurable else '-'}")
    print(f"CARRIED_OVER_THREADS={carried_count}")
    print(f"PENDING_POSTS={pending_posts}")


def _evaluate_convergence(
    carried: dict[str, Any] | None,
    round_passes: bool,
    findings_measurable: bool,
    new_findings: int,
) -> bool:
    """このラウンドが収束したかを判定する。

    **新規の指摘が 0 件なら収束する。** 全員 `APPROVE` は最も止まらない参加者に
    律速される。同じ論点の再提出では止まり、新しい観点が出るあいだは回る。
    """
    return carried is None and (
        round_passes or (findings_measurable and new_findings == 0)
    )


def _collect_reviewer_intents(
    st: dict[str, Any], last: dict[str, Any], only: str | None,
) -> tuple[list[str], dict[str, str], bool]:
    """このラウンドの担当を洗い出し、各担当の intent と pass 判定を集計する。"""
    reviewers = participants_mod._round_reviewers(st, last.get("round", 1))
    intents = {a: participants_mod._agent_intent(last, a, only) for a in reviewers}
    round_passes = participants_mod._round_passes(last, only, reviewers)
    return reviewers, intents, round_passes


def _finalize_round_if_converged(
    pr: int,
    st: dict[str, Any],
    last: dict[str, Any],
    converged: bool,
    findings_measurable: bool,
    pending_posts: int,
) -> None:
    """収束していれば、待ち行列の有無に応じて確定させて終了する。

    収束していなければ何もせず戻る（呼び出し側が修正のラウンドへ進める）。
    """
    if not converged:
        return
    if last.get("stage") == "model" and not pending_posts:
        # モデルの段の APPROVE では抜けない（#1111）。確定したモデルを前提に、詳細の段へ進む
        last["verdict"] = "model_confirmed"
        store._save(pr, st)
        review_lib.info("→ モデルの段が承認された。修正を挟まずに詳細の段のラウンドへ進む。")
        print("MODEL_CONFIRMED=1")
        sys.exit(2)
    if pending_posts:
        # **届いていない投稿があるあいだは収束させない。** 修正するものは無いので
        # 修正の工程（2）へは回さず、流し直す先（8）へ分ける。
        last["verdict"] = "queued"
        store._save(pr, st)
        review_lib.info(
            f"→ 待ち行列に {pending_posts} 件残っている。"
            "流し切るまで収束させない（`state.py flush` で流す）。"
        )
        sys.exit(8)
    _finalize_converged_round(pr, st, last, findings_measurable)


def cmd_judge(args: argparse.Namespace) -> None:
    """Step 3 — intent ベース pass 判定。

    出口は 5 つある。**結果を取り込めていないラウンドは、収束も修正も決められない。**
    そのため結果なしのチェックを、通ったかどうかの判定より先に置く。

    収束の枝に入る直前で、継続的統合のチェックジョブを 1 度だけ照会する（#327）。
    code-related の失敗があれば**中断せず**終了コード 2 で修正のラウンドへ回す。
    収束の直前は修正の機会が残っている段であり、そこで中断すると直せる失敗まで
    人手へ戻すことになる。中断は上限のラウンド数・振動の検知・`merge-fix` が受け持つ。

    Exit code: 0=approved, 2=continue, 7=結果なしのため起動し直す,
               8=待ち行列に投稿が残っている, 1=error
    """
    pr = args.pr
    posts._auto_flush(pr)
    st = store._load(pr)
    if not st.get("rounds"):
        review_lib.die("state.rounds が空。`state.py start-round` を先に呼んでください")
    last = st["rounds"][-1]
    only = st.get("only")

    reviewers, intents, round_passes = _collect_reviewer_intents(st, last, only)

    carried = findings_mod._carried_over_pending(st)
    carried_count = (st.get("carried_over") or {}).get("count", 0)
    new_findings, findings_measurable = matching._new_finding_count(st, pr)
    pending_posts = posts._pending_posts(pr)

    _print_judge_status(
        reviewers, intents, new_findings, findings_measurable,
        carried_count, pending_posts,
    )

    no_result = participants_mod._no_result_agents(last, only, reviewers)
    if no_result:
        _handle_no_result_round(pr, st, last, no_result)

    converged = _evaluate_convergence(
        carried, round_passes, findings_measurable, new_findings,
    )

    _finalize_round_if_converged(
        pr, st, last, converged, findings_measurable, pending_posts,
    )

    last["verdict"] = "changes_requested"
    store._save(pr, st)
    if carried is not None:
        review_lib.info(
            f"→ 引き継いだ指摘が {carried_count} 件残っている。"
            "修正の工程を 1 度通すまで収束させない。"
        )
    else:
        review_lib.info(
            "→ " + " ".join(f"{a}={intents[a]}" for a in reviewers)
            + (f"（新しい指摘 {new_findings} 件）" if findings_measurable else "")
            + "。修正へ。"
        )
    sys.exit(2)
