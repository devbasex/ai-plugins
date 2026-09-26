"""副命令 `check-oscillation`・`should-rotate`・`set-current-pr`（#1142 の C2）。

ループを止めるか・PR を巻き直すか・巻き直した PR へ移るかを決める。
"""
from __future__ import annotations

import argparse
import sys

import review_lib  # noqa: E402
import gh_call  # noqa: E402
from review_lib import matching, store  # noqa: E402


def cmd_check_oscillation(args: argparse.Namespace) -> None:
    """Step 4 — 同じ箇所の指摘の重なりを計算。

    前ラウンドと現ラウンドで重なりが 50% 以上なら final=oscillation で中断。
    rotation 直後は round_in_pr<2 なのでスキップ。

    同じ箇所かどうかは 3 つの一致で測り、いずれか 1 つで結びつけば同じ箇所として数える。
    位置の完全一致だけで測ると、指摘の趣旨が同じでも行が 1 行ずれれば別の指摘として
    数える。レビューを行うのは codex / agy であり、同じ箇所を指すときに選ぶ行は毎回
    同じとは限らない。修正で行が前後にずれた場合も一致しない。

    | 一致 | 条件 |
    | --- | --- |
    | 位置の一致 | ファイルが同じで、行が同じ |
    | 近傍の一致 | ファイルが同じで、行の差が `OSCILLATION_NEAR_LINES` 以内 |
    | 本文の一致 | ファイルが同じで、正規化した本文が同じ |
    """
    pr = args.pr
    st = store._load(pr)
    rounds = st["rounds"]
    current_pr = st["current_pr"]
    same_pr = [r for r in rounds if r["pr"] == current_pr]
    if len(same_pr) < 2:
        review_lib.info("⏭ round_in_pr<2: 振動検知スキップ")
        sys.exit(2)  # continue

    prev = matching._finding_keys(st, pr, same_pr[-2]["round"])
    curr = matching._finding_keys(st, pr, same_pr[-1]["round"])
    if not curr:
        review_lib.info("⏭ 現ラウンドの payload なし: 振動検知スキップ")
        sys.exit(2)

    overlap = matching._oscillation_overlap(curr, prev)
    review_lib.info(
        f"振動検知: overlap={overlap.overlap_count}/{overlap.total}"
        f" ({overlap.ratio:.0%})"
        f" 位置={overlap.exact} 近傍={overlap.near} 本文={overlap.same_body}"
    )

    if overlap.ratio >= 0.5:
        st["final"] = "oscillation"
        st["ended_at"] = review_lib._now()
        store._save(pr, st)
        review_lib.die(f"振動検知 — 同一箇所が {overlap.ratio:.0%} 重複。中断。", code=4)
    sys.exit(2)


def cmd_should_rotate(args: argparse.Namespace) -> None:
    """Step 6 — PR ローテーション要否。Exit 0=rotate, 2=keep.

    判定は ``round_in_pr >= rotate_after && total < max_rounds`` のみで、
    rotate-pr.sh の ``--mode light|squash`` どちらでも同じ条件を使う。
    state.json の key は ``STATE_PR`` (最初に init した PR 番号) で固定なので、
    light モードで head_branch が変わらない場合でも整合する。
    """
    pr = args.pr
    st = store._load(pr)
    current_pr = st["current_pr"]
    round_in_pr = sum(1 for r in st["rounds"] if r["pr"] == current_pr)
    total = len(st["rounds"])
    rotate_after = st["rotate_after"]
    max_r = st["max_rounds"]
    if round_in_pr >= rotate_after and total < max_r:
        review_lib.info(f"🔄 PR #{current_pr} が {round_in_pr} round 経過 — ローテーション必要")
        print(f"CURRENT_PR={current_pr}")
        print(f"ROUND_IN_PR={round_in_pr}")
        sys.exit(0)
    sys.exit(2)


def _head_branch_of(pr: int) -> str | None:
    """Pull Request の head branch を取り直す。取れなければ `None` を返す。"""
    name = gh_call.gh(["pr", "view", str(pr), "--json", "headRefName", "-q", ".headRefName"]).stdout.strip()
    return name or None


def cmd_set_current_pr(args: argparse.Namespace) -> None:
    """PR ローテーション完了後の state 更新。

    rotate-pr.sh の light / squash どちらでも、新 PR 番号を受け取って
    ``current_pr`` を切り替え、``pr_history`` に新 PR エントリを追加する。
    state.json のファイル名は ``STATE_PR`` (= ``args.pr``) ベースで不変なので、
    light モードで head_branch が変わらないケースでも問題なく追跡できる。
    """
    pr = args.pr  # 旧 PR (state file の key)
    new_pr = args.new_pr
    st = store._load(pr)
    old_pr = st["current_pr"]
    now = review_lib._now()
    # 旧 PR の history を closed に
    for h in st["pr_history"]:
        if h["pr"] == old_pr and h["closed_at"] is None:
            h["closed_at"] = now
            h["rounds"] = sum(1 for r in st["rounds"] if r["pr"] == old_pr)
            break
    st["pr_history"].append({"pr": new_pr, "opened_at": now, "closed_at": None, "rounds": 0})
    st["current_pr"] = new_pr

    # **巻き直しは新しい枝を作ることがある。** `squash` は `<枝名>-r<時刻>` を push する。
    # 状態ファイルの `head_branch` を更新しないと、巻き直しの直後に再開したときに
    # 巻き直し前の枝へ作業ツリーを合わせようとする（#244）。
    #
    # 枝名は引数で受け取る。巻き直しのスクリプトは light / squash のどちらでも
    # `NEW_BRANCH=` を出力する。渡されなかったときは新しい Pull Request から取り直し、
    # それも取れなければ既存の値を残す。**取り直せないことで進行を止めない。**
    # ラウンドの開始時の同期が毎回取り直すため、次のラウンドで書き戻される。
    head_branch = getattr(args, "head_branch", None)
    if not head_branch:
        head_branch = _head_branch_of(new_pr)
    if head_branch:
        st["head_branch"] = head_branch
    else:
        review_lib.info(f"⚠ PR #{new_pr} の head branch を取得できませんでした。前の値を残します")

    store._save(pr, st)
    review_lib.info(f"✅ current_pr: {old_pr} → {new_pr} (head_branch={st.get('head_branch')})")
