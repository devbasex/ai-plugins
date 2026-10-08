"""副命令 `judge` と、その続きの `flush`（#1142 の C2）。"""

from __future__ import annotations

import argparse
import shlex
import sys
from typing import Any

import review_lib  # noqa: E402
import assignee_env  # noqa: E402
import assignment  # noqa: E402
from review_lib import (  # noqa: E402
    ci as ci_mod,
    findings as findings_mod,
    matching,
    participants as participants_mod,
    posts,
    store,
)


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
    print(f"PENDING_RATE_LIMITED={'1' if result.rate_limited else '0'}")
    print(f"PENDING_TRANSIENT={'1' if result.transient else '0'}")
    # 止まった理由に最後の失敗を載せるため（drive.py の投稿待ち、#1843）。値は引用して 1 語にする。
    print(f"PENDING_LAST_ERROR={shlex.quote(str((result.failed or {}).get('last_error') or ''))}")
    for item in result.dropped:
        review_lib.info(f"⚠️ 送れない項目を飛ばしました ({item.get('kind')} #{item.get('seq')}): {item.get('last_error') or ''}")
    if result.remaining:
        reason = (result.failed or {}).get("last_error", "")
        why = "（まだ上限です）" if result.rate_limited else "（一時的な失敗です）" if result.transient else ""
        review_lib.info(
            f"⏳ 待ち行列に {result.remaining} 件残っています{why}{f': {reason}' if reason and not result.rate_limited else ''}"
        )
    else:
        review_lib.info(f"✅ 待ち行列は空です（送った {len(result.sent)} 件）")


def _no_result_reasons(last: dict[str, Any], no_result: list[str]) -> dict[str, str]:
    """結果なしの担当ごとの理由を集め、`NO_RESULT_REASONS` を出す。

    どの出口でも進行側が理由を読めるように、先頭で 1 度だけ出す（#729 の AC14）。
    """
    reasons = {a: (last.get(a) or {}).get("no_result_reason") or "missing" for a in no_result}
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


def _print_relaunch(pending: list[str]) -> None:
    """同じ席で起動し直す担当のシェル向けの出力（形と意味は #919 の前と同じ）。"""
    print(f"RELAUNCH_AGENTS='{' '.join(pending)}'")
    print(f"RELAUNCH_AGENTS_CSV={','.join(pending)}")
    # 互換のために残す。**`both` は codex / agy の 2 者だけを指す語**であるため、
    # 担当がそれ以外を含むラウンドでは CSV の側を使う。
    print(f"RELAUNCH_TARGET={'both' if len(pending) == 2 else pending[0]}")
    review_lib.info(f"→ 結果を残さなかったレビュアーがいる: {' '.join(pending)}。同じラウンドで 1 度だけ起動し直す。")


def _decide_seats(st: dict[str, Any], last: dict[str, Any], no_result: list[str], reasons: dict[str, str]):
    """結果なしの席ごとに規則（`assignment.after_no_result`）へ答えを求め、記録（`no_results`）へ追記する。

    judge は答えを実行するだけで、起動し直すか・誰へ振り替えるかを自分で決めない（#919 の AC5）。
    記録は追記だけで、打ち直した judge は前の件を読んで次の答え（起動し直しの次は振り替え、候補が尽きれば
    中断）へ進むため、繰り返しは必ず止まる。席は先に決めた席の振り替え先を `busy` に入れて決める（I6）。
    """
    log = st.setdefault("no_results", [])
    round_no = int(last.get("round") or 1)
    seats = list(last.get("reviewers") or no_result)
    accounts = participants_mod.seat_accounts(last)
    participants = st.get("participants") or {}
    pick = assignee_env.account_picker()
    decisions = []
    for seat in no_result:
        failed = assignment.Assignee(seat, accounts.get(seat))
        d = assignment.after_no_result(
            failed,
            reasons[seat],
            available=list(participants.get("available") or []),
            log=log,
            step="review",
            attempt=round_no,
            host=str(st.get("host") or ""),
            busy=[s for s in seats if s != seat],
            only=bool(st.get("only")),
            initial_account=assignee_env.initial_account(),
            pick_account=pick,
        )
        log.append(assignment.no_result_entry("review", round_no, failed, reasons[seat], d, review_lib._now()))
        if d.action == assignment.REASSIGN and d.to is not None:
            seats[seats.index(seat)] = d.to.seat
            accounts[d.to.seat] = d.to.account
        decisions.append((failed, reasons[seat], d))
    return seats, accounts, decisions


def _tried_line(st: dict[str, Any], round_no: int) -> str:
    """このラウンドで試した担当と理由（中断のメッセージ。AC9）。"""
    rows = [e for e in st.get("no_results") or [] if e.get("step") == "review" and e.get("attempt") == round_no]
    return " ".join(
        f"{assignment.Assignee(e['seat'], e.get('account') or None).label()}={e.get('reason')}→{e.get('decision')}" for e in rows
    )


def _apply_aborts(pr: int, st: dict[str, Any], last: dict[str, Any], aborted: list, round_no: int) -> None:
    """`abort` の席があれば理由を出し、試した担当と理由を並べて中断する（無ければ何もしない）。"""
    if not aborted:
        return
    for failed, reason in aborted:
        detail = (last.get(failed.seat) or {}).get("monitor_detail")
        review_lib.info(f"  {failed.label()}: reason={reason}" + (f" detail={detail}" if detail else ""))
    _abort_no_result_round(
        pr,
        st,
        f"結果が残らず、振り替え先もありません: {_tried_line(st, round_no)}。"
        " 中断します。最終スイープを通してから完了報告へ進んでください",
    )


def _apply_relaunch(last: dict[str, Any], pending: list[str]) -> None:
    """同じ席で起動し直す担当を `relaunched` へ重ねずに足す。"""
    if pending:
        last["relaunched"] = (last.get("relaunched") or []) + [a for a in pending if a not in (last.get("relaunched") or [])]


def _apply_reassign(last: dict[str, Any], moved: list, seats: list[str], accounts: dict) -> None:
    """振り替えた席を担当と席の記録へ反映し、`reassigned` へ重ねずに足す。"""
    if not moved:
        return
    last["reviewers"] = seats
    last["seats"] = participants_mod.seat_records(seats, accounts)
    done = {(m.get("from"), m.get("to"), m.get("to_account")) for m in last.get("reassigned") or []}
    for f, to, r in moved:
        if (f.seat, to.seat, to.account or "") not in done:
            last.setdefault("reassigned", []).append({"from": f.seat, "to": to.seat, "to_account": to.account or "", "reason": r})


def _handle_no_result_round(pr: int, st: dict[str, Any], last: dict[str, Any], no_result: list[str]) -> None:
    """結果なしの担当があるラウンドの出口を決める。

    先に理由の行（`NO_RESULT_REASONS`）を出す。どの出口でも進行側が理由を読めるようにする
    ためである（#729 の AC14）。席ごとの答えは規則（`assignment.after_no_result`）だけが決める（#919）。

    | 答え | 出口 |
    | --- | --- |
    | どれかが `abort` | `final = error`・終了コード 1。メッセージに試した担当と理由を並べる |
    | `relaunch` / `reassign` だけ | 席を振り替え先へ書き換え、`RELAUNCH_*`（同じ席で起動し直す席）と `REASSIGNED` を出して 7 |

    振り替えた席の元の欄（`rounds[].<元の席>`）は残す。正は `no_results` で、`rounds[].reassigned` は報告の写し。
    """
    last["verdict"] = "no_result"
    reasons = _no_result_reasons(last, no_result)
    round_no = int(last.get("round") or 1)
    seats, accounts, decisions = _decide_seats(st, last, no_result, reasons)
    aborted = [(f, r) for f, r, d in decisions if d.action == assignment.ABORT]
    _apply_aborts(pr, st, last, aborted, round_no)
    pending = [f.seat for f, _, d in decisions if d.action == assignment.RELAUNCH]
    moved = [(f, d.to, r) for f, r, d in decisions if d.action == assignment.REASSIGN and d.to is not None]
    _apply_relaunch(last, pending)
    _apply_reassign(last, moved, seats, accounts)
    store._save(pr, st)
    if pending:
        _print_relaunch(pending)
    if moved:
        print("REASSIGNED='" + " ".join(f"{f.seat}={('claude@' + to.account) if to.account else to.seat}:{r}" for f, to, r in moved) + "'")
        review_lib.info("→ 結果を残さなかった席を振り替える: " + " ".join(f"{f.label()}→{to.label()}（{r}）" for f, to, r in moved))
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
        review_lib.info(f"→ 両方 APPROVE だが継続的統合が失敗している: {' '.join(ci['failed'])}。修正へ。")
        sys.exit(2)
    last["verdict"] = "approved"
    st["final"] = "approved"
    st["ended_at"] = review_lib._now()
    store._save(pr, st)
    if ci["verdict"] == "meta_only":
        review_lib.info(f"⚠ {ci['note']}")
    elif ci["verdict"] == "pending":
        review_lib.info(f"⚠ 未完了のチェックジョブが残ったまま収束する: {' '.join(ci['pending'])}。完了は待たない")
    elif ci["verdict"] == "unverified":
        review_lib.info(f"⚠ 継続的統合を確かめられないまま収束する: {ci['reason']}")
    review_lib.info("✅ 新しい指摘が出なくなった。収束。" if findings_measurable else "✅ 全員が承認した。収束。")
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
    print("REVIEWER_INTENTS='" + " ".join(f"{a}={intents[a]}" for a in reviewers) + "'")
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
    return carried is None and (round_passes or (findings_measurable and new_findings == 0))


def _collect_reviewer_intents(
    st: dict[str, Any],
    last: dict[str, Any],
    only: str | None,
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
        review_lib.info(f"→ 待ち行列に {pending_posts} 件残っている。流し切るまで収束させない（`state.py flush` で流す）。")
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

    Exit code: 0=approved, 2=continue, 7=結果なしのため起動し直す・振り替える,
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
        reviewers,
        intents,
        new_findings,
        findings_measurable,
        carried_count,
        pending_posts,
    )

    no_result = participants_mod._no_result_agents(last, only, reviewers)
    if no_result:
        _handle_no_result_round(pr, st, last, no_result)

    converged = _evaluate_convergence(
        carried,
        round_passes,
        findings_measurable,
        new_findings,
    )

    _finalize_round_if_converged(
        pr,
        st,
        last,
        converged,
        findings_measurable,
        pending_posts,
    )

    last["verdict"] = "changes_requested"
    store._save(pr, st)
    if carried is not None:
        review_lib.info(f"→ 引き継いだ指摘が {carried_count} 件残っている。修正の工程を 1 度通すまで収束させない。")
    else:
        review_lib.info(
            "→ "
            + " ".join(f"{a}={intents[a]}" for a in reviewers)
            + (f"（新しい指摘 {new_findings} 件）" if findings_measurable else "")
            + "。修正へ。"
        )
    sys.exit(2)
