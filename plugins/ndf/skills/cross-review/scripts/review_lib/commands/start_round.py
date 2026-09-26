"""副命令 `start-round`（#1142 の C2）。"""
from __future__ import annotations

import argparse
from typing import Any

import review_lib  # noqa: E402
from review_lib import (  # noqa: E402
    github, participants as participants_mod, posts, review_focus, store, workspace as workspace_mod)


def _resolve_previous_verdict(st: dict[str, Any], prev: dict[str, Any]) -> str | None:
    """保存されていない旧形式の判定を、ラウンドの結果から復元する。"""
    verdict = prev.get("verdict")
    if verdict is not None:
        return verdict
    reviewers = prev.get("reviewers") or participants_mod._round_reviewers(st, prev.get("round") or 1)
    if participants_mod._no_result_agents(prev, st.get("only"), reviewers):
        return "no_result"
    return ("approved" if participants_mod._round_passes(prev, st.get("only"), reviewers)
            else "changes_requested")


def _require_fix_for_changes(round_no: Any, verdict: str | None,
                             fix: dict[str, Any] | None) -> None:
    """修正必須の判定に修正記録が伴うことを確かめる。"""
    if verdict != "changes_requested" or fix:
        return
    review_lib.die(
        f"round {round_no} は修正必須の判定でしたが、修正の記録がありません。"
        " 返信と Resolve が飛ばされている可能性があります。"
        " `/ndf:fix` を実行して戻り値ファイルを作り、`merge-fix` を通してから"
        " 次のラウンドを開始してください",
        code=5,
    )


def _verify_resolved_threads(st: dict[str, Any], prev: dict[str, Any],
                             fix: dict[str, Any] | None) -> None:
    """Resolve 済みとの申告を GitHub の未解決スレッドと突き合わせる。"""
    round_no = prev.get("round")
    claimed = (fix or {}).get("resolved_thread_ids") or []
    if not claimed:
        return
    claimed_pr = int(prev.get("pr") or st.get("current_pr") or 0)
    threads = github._fetch_unresolved_threads(str(st.get("repo") or ""), claimed_pr)
    if threads is None:
        review_lib.info(
            f"⚠ round {round_no} で Resolve したと申告されたスレッドの状態を確認できません"
            " — チェックを飛ばして続行します"
        )
        return
    open_ids = {t["id"] for t in threads}
    still_open = [i for i in claimed if i in open_ids]
    if still_open:
        review_lib.die(
            f"round {round_no} で Resolve したと申告されたスレッドが未解決のまま残っています: "
            f"{' '.join(still_open)}。返信と Resolve を済ませてから次のラウンドを開始してください",
            code=5,
        )


def _guard_previous_round(st: dict[str, Any], prev: dict[str, Any]) -> None:
    """前のラウンドの後始末が終わっているかを確かめる。

    進行側が手で修正して次のラウンドへ進めると、修正の工程（Step 5）が担う返信と
    Resolve が飛ばされる。飛ばされたまま進むと、未解決の指摘が残ったまま承認へ到達する。

    止めるのは次の 2 つ。

    1. 前のラウンドが修正必須の判定なのに、修正の記録が無い
    2. 前のラウンドで Resolve したと申告されたスレッドが、GitHub 側で未解決のまま

    未解決の指摘を取得できないときはチェックを行わず、確認できなかったことを残して進む。
    取得の失敗で止めると、GitHub 側の一時的な不調でループが進まなくなる。

    スレッドの状態は、申告が行われた Pull Request（`prev["pr"]`）へ問い合わせる。
    ローテーションを挟んだラウンドでは Step 6 の `set-current-pr` が先に走るため、
    `current_pr` は既に新しい Pull Request を指している。そちらへ問い合わせると、
    旧 Pull Request のスレッドが未解決のままでも一覧に現れずチェックが素通りする。
    """
    fix = prev.get("fix")
    verdict = _resolve_previous_verdict(st, prev)
    _require_fix_for_changes(prev.get("round"), verdict, fix)
    _verify_resolved_threads(st, prev, fix)


def _sync_before_round(st: dict[str, Any], pr: int) -> workspace_mod.HeadRef | None:
    """ラウンドを開く前に、レビュー用の作業ツリーを Pull Request の head へ揃える。

    同期は作られるときと再開するときにしか行われていなかった。修正を作業ツリーの外で
    行って push すると、次のラウンドは 1 つ前の内容をレビューする。**どの経路で push
    されても同じ差分がレビューされる状態にする**（#217）。

    **ラウンドのエントリを開く前に行う。** 途中で止まったときにラウンドが半端に開かれず、
    原因を取り除いた後に同じラウンド番号から再開できる。

    「同期の対象が無い」と「同期できない」は分けて扱う。作業ツリーが失われている状態は
    この後の `launch-codex.sh` / `launch-agy.sh` が表に出すため、ここで止めても
    分かることは増えない。
    """
    wt = str(st.get("worktree_path") or "")
    if not wt:
        review_lib.info("⚠ state に worktree_path が無い — 作業ツリーの同期を飛ばして続行します")
        return None
    if not workspace_mod._is_registered_worktree(wt):
        review_lib.info(f"⚠ 登録済みの作業ツリーではない ({wt}) — 同期を飛ばして続行します")
        return None
    head = workspace_mod._resolve_head_ref(pr, repo=str(st.get("repo") or "") or None)
    workspace_mod._sync_worktree(wt, pr, head, strict=True)
    # 解決したブランチ名を書き戻す。巻き直しの後は state の値が古いため、再開の経路も
    # ここで書かれた値を読む。保存はこの後の round エントリの追加と同じ書き込みで済む。
    st["head_branch"] = head.branch
    return head


def cmd_start_round(args: argparse.Namespace) -> None:
    """Step 1 — round 開始判定。"""
    posts._auto_flush(args.pr)
    st = store._load(args.pr)
    total = len(st["rounds"])
    max_r = st["max_rounds"]
    if total >= max_r:
        st["final"] = "max_rounds"
        st["ended_at"] = review_lib._now()
        store._save(args.pr, st)
        review_lib.die(f"max_rounds={max_r} 到達。中断。", code=1)
    # 上限に達していれば、そこでループが終わる。後始末のチェックはその後で意味を持たない。
    if total > 0:
        _guard_previous_round(st, st["rounds"][-1])

    pr = st["current_pr"]
    head = _sync_before_round(st, pr)

    round_no = total + 1
    round_in_pr = sum(1 for r in st["rounds"] if r["pr"] == pr) + 1

    # 既存コメントのスナップショットを取り直す（#542 の決定 6）。通しの 1 ラウンド目は `init` が取った
    # 直後のため取り直さない。失敗しても前のスナップショットのまま進める（前のスナップショットでも今と同じ条件で
    # レビューできる）。**round エントリを保存する前に取る。** 保存の後で取ると、取得の
    # 途中の割り込みで結果の無い round だけが残り、再実行が前のラウンドのチェックで止まる。
    if round_no >= 2:
        error = github._fetch_existing_comments(
            str(st.get("repo") or ""), int(pr), store._existing_comments_path(args.pr), strict=True)
        if error is not None:
            review_lib.info(f"⚠ 既存コメントのスナップショットを取り直せませんでした（{error}）。前のスナップショットのまま進めます")

    # round エントリを開く。head の commit を記録するのは、起動スクリプト 2 本と
    # 収束の判定が同じ値を読むためである。**2 本が同じ値を別々に取っていた分が 0 になる。**
    # **担当はラウンドを開くときに決めて残す。** 後から輪番を引き直すと、状態ファイルの
    # 記録と実際に起動した担当がずれる。
    reviewers = participants_mod._round_reviewers(st, round_no)
    entry: dict[str, Any] = {
        "round": round_no,
        "pr": pr,
        "started_at": review_lib._now(),
        "reviewers": reviewers,
    }
    if head is not None:
        entry["head_sha"] = head.oid
    stage = review_focus._round_stage(st, round_no)
    if stage is not None:
        entry["stage"] = stage
    st["rounds"].append(entry)
    store._save(args.pr, st)

    review_lib.info(f"=== Round {round_no} / {max_r} (PR #{pr}, round_in_pr={round_in_pr}"
         f", レビュー: {' + '.join(reviewers)}) ===")
    print(f"ROUND={round_no}")
    print(f"REVIEWERS='{' '.join(reviewers)}'")
    if stage is not None:
        print(f"STAGE={stage}")
    print(f"REVIEWERS_CSV={','.join(reviewers)}")
    print(f"ROUND_IN_PR={round_in_pr}")
    print(f"PR={pr}")
    print(f"MAX_ROUNDS={max_r}")
    print(f"ROTATE_AFTER={st['rotate_after']}")
