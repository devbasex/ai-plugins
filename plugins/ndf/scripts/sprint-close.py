#!/usr/bin/env python3
"""sprint-close.py: スプリントを閉じる（progress-tracking の「スプリントを閉じる」の手順）。

    python3 sprint-close.py --record-pr <PR番号|0|unknown> [--final-stage 配布|リリース後テスト|振り返り]
                             [--prs 1,2] [--issues 3,4] [--with-verification]
                             [--label "<マイルストーン>の<工程名>"] [--dry-run] [--root <dir>]

1. 記録の PR の本文とコメントを読み、最後の「## 配布の記録」ブロックを取る
2. スプリントの PR の一覧（--prs、無ければブロックの `スプリント:` の行）の本文から、closing keywords
   （GitHub が課題を自動で閉じるキーワード Closes / Fixes / Resolves）と閉じる予定の参照
   （`最終工程の後に閉じる: #N`。既定ブランチ宛の本文で closing keywords を置き換えた形）が指す課題を集める
   （lib/closing-issues.sh）。--issues の課題（記録のリポジトリ）を足す（`pace: fast` の実装 PR は
   closing keywords を持たない）。PR を読んだのに集めた課題が 0 件なら、何も書かずに止まる（終了コード 2）
3. 閉じる条件 1（本番への配布まで済んだ、または配布なし）と、--with-verification か最終工程がリリース後テストのときは
   条件 2（その課題のリリース後テストの行がすべて合格で、本文の受け入れ条件の番号のすべてに合格の行がある）
   を課題ごとに判定する。`課題` の列が課題を指さない行（`—`・`PR 1869` など）は飛ばす。条件 2 は課題ごとの錠
   （lib/gh_parts.py の issue_lock）の中で状態と本文を 1 回で読んで判定し（本文の `## 受け入れ条件の確認` で
   マージ前に確かめたと記録された条件には合格の行を求めない。#2144）、満たした記録のリポジトリの課題は
   本文の受け入れ条件に印（`[x]`）を付けてから閉じる（規則は lib/acceptance.py。#1683）。
   最後に条件 3（最終工程の記録。--final-stage、省けば振り返り）を判定する。振り返りは課題の本文の
   `振り返り: <コメントの URL>` の行のうち、コメントの識別子が記録の PR の最後の配布の記録のコメントの識別子以上の
   ものがあること（final_stage_gap。#2228）。条件 2 と同じ 1 回の本文の読みを使い、満たさなければ印も書かない
4. 条件を満たす課題ごとに (a) 状態を読む → (b) 記録のリポジトリの課題ならボードを Done →
   (c) 読み直して OPEN なら閉じる → (d) 読み直して CLOSED を確かめる
5. スプリントの PR の題と本文が番号で参照し、2 で集めた課題に入らない開いた課題（参照だけの課題、
   lib/closing.py の referenced_numbers）を kept_open で載せる。閉じず、ボードも書かない

結果は lib/step_result.py の形の 1 行の JSON。items は課題ごとに
{kind:"issue", repo, number, result, reason?, cmd?, checked?, would_check?}。result は
closed / already_closed / failed / kept_open / closed_unverified（既に閉じていたが条件 2 を満たさない。
開き直さない）（--dry-run では would_close）。checked は印を付けた受け入れ条件の番号、would_check は
--dry-run で付ける予定の番号。ボードへ Done を書けなかった（`projects-sync.sh` の結果 JSON が ok でない）ときは
{kind:"board_note"} の項目に理由が載り、課題を閉じる処理は続ける。metrics.final_stage は判定に使った最終工程。
`--record-pr 0` は「本番の記録なし」（最終の検査で変更が無く本番を飛ばした）。配布の記録を読まず、
閉じる条件 1・2 を見ずに --issues の課題を閉じる（条件 3 の振り返りは、比べる配布の記録が無いため行があれば満たす）。--issues と一緒のときだけ受ける。
`--record-pr unknown` は「記録の置き場が分からない」（リリースの経路が雛形でも昇格でもなく、まとめのプランが
記録の PR を決められない）。配布の記録を読まず、どの課題も閉じず、ボードも書かずに --issues の課題を理由つきの
kept_open で返す（終了コード 0）。--issues と一緒のときだけ受ける。
終了コード: 0 = 失敗なし / 1 = 失敗あり（棚卸しへ進まない）/ 2 = 一覧が取れない・
閉じる課題が 0 件・--record-pr 0 / unknown に --issues が無い / 3 = 呼び出しの誤り。
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from contextlib import nullcontext
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
import deps  # noqa: E402

deps.require("md", "locks", "durable", "waits")
from step_result import (
    EXIT_PRECONDITION,
    EXIT_UNREADABLE,
    StepError,
    emit,
    git_root,  # noqa: E402
    main_with,
    result,
    run,
)
import acceptance  # noqa: E402
import gh_call  # noqa: E402
import gh_parts  # noqa: E402
import gh_rest  # noqa: E402
import locks  # noqa: E402
import step_read  # noqa: E402  projects-sync.sh の結果 JSON を読む（#2227）

from dist_record import parse_record  # noqa: E402  記録の形は lib/dist_record.py が持つ（#1273）

sys.path.insert(0, str(HERE))
from sprint_close_lib.collect import read_record, referenced_items, sprint_issues  # noqa: E402
from sprint_close_lib.final_stage import (  # noqa: E402  閉じる条件 3（#2228）
    FINAL_GAP_LEAD,
    FINAL_STAGES,
    NO_BASE,
    STAGE_RETRO,
    STAGE_VERIFY,
    final_stage_gap,
    gap_item,
)

TOOL = "sprint-close"
RECORD_UNKNOWN = "unknown"  # --record-pr の「記録の置き場が分からない」（supervise_lib/sprint_routes.py が渡す。#1875）
UNKNOWN_REASON = "配布の記録の置き場が分からない（リリースの経路が雛形でも昇格でもない。リリース記録を確かめて手で閉じる）"
LOCK_TIMEOUT = 30  # 課題ごとの錠の待ち（body-lock の既定と同じ）


IssueRows = acceptance.IssueRows  # リリース後テストの表は承認の材料と同じ規則で読む（lib/acceptance.py。#1885 の決定 8）
verification_verdicts = acceptance.verification_verdicts


NO_ROWS = "リリース後テストの行が無い"
NOT_PASSED = "リリース後テストに合格でない条件がある（不合格・保留）"
UNVERIFIED = "閉じているが閉じる条件 2 を満たさない: "
UNREADABLE_BODY = "本文を読めない（最終工程の記録を確かめられない）"


def judge_acceptance(rows, criteria, pre_merge=frozenset()):
    """閉じる条件 2 の判定。(満たすか, 満たさない理由, 印を付ける番号) を返す。

    受け入れ条件が 0 個なら表の行の合否だけで決める。pre_merge（本文の `## 受け入れ条件の確認` でマージ前に
    確かめたと記録された番号。#2144）の条件には合格の行を求めない。すべての条件がそれなら行が無くても満たす。"""
    if rows is None and criteria and all(c.number in pre_merge for c in criteria):
        return True, None, [c.number for c in criteria if not c.checked]
    if rows is None:
        return False, NO_ROWS, []
    if not rows.all_pass:
        return False, NOT_PASSED, []
    missing = [c.number for c in criteria if c.number not in rows.passed and c.number not in pre_merge]
    if missing:
        return False, "合格の行が無い受け入れ条件: " + ", ".join(map(str, missing)), []
    return True, None, [c.number for c in criteria if not c.checked]


# --- 閉じる ---------------------------------------------------------------------


def issue_state(root, repo, n):
    p = gh_call.gh(["issue", "view", str(n), "--repo", repo, "--json", "state", "-q", ".state"], cwd=root)
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


class CloseContext:
    """全課題で一定の閉じる処理の材料。notes は課題ごとのボードの記録の失敗を溜める。retry_cmd は今の引数で組んだやり直しの cmd。
    final_stage と since は条件 3（final_stage_gap）の材料。"""

    def __init__(self, root, record_repo, comment, argv=(), final_stage=STAGE_RETRO, since=None):
        self.root, self.record_repo, self.comment = root, record_repo, comment
        self.final_stage, self.since = final_stage, since  # 条件 3 の最終工程と、振り返りの行を比べる基準
        self.notes = []
        self.retry_cmd = shlex.join(["python3", str(Path(__file__).resolve()), *argv])


def _issue_base(repo, n):
    """課題 1 件の結果の項目の共通部分。"""
    return {"kind": "issue", "repo": repo, "number": n}


def _failed(base, reason, ctx):
    """やり直しの cmd を添えた failed の項目。"""
    return {**base, "result": "failed", "reason": reason, "cmd": ctx.retry_cmd}


def _dry_run_result(state):
    """試行（dry_run）の結果: CLOSED なら already_closed、それ以外は would_close。"""
    return "already_closed" if state == "CLOSED" else "would_close"


def close_one(ctx, repo, n):
    root, record_repo, comment, notes = ctx.root, ctx.record_repo, ctx.comment, ctx.notes
    it = _issue_base(repo, n)
    before = issue_state(root, repo, n)
    if before is None:
        return {**it, "result": "failed", "reason": "before を読めない", "cmd": f"gh issue close {n} --repo {repo}"}
    if repo == record_repo:
        s = run(["bash", str(HERE / "projects-sync.sh"), str(n), "status", "Done"], cwd=root, check=False)
        why = step_read.not_ok_reason(s.stdout, s.returncode, s.stderr)
        if why:
            notes.append({"kind": "board_note", "name": f"{repo}#{n}", "result": "note", "reason": why})
    close_out = _close_if_open(root, repo, n, comment) if before == "OPEN" else None
    after = issue_state(root, repo, n)
    return _close_result(it, before, after, close_out)


def _close_if_open(root, repo, n, comment):
    """読み直して OPEN なら閉じ、close の出力（無ければ None）を返す。"""
    if issue_state(root, repo, n) != "OPEN":
        return None
    c = gh_call.gh(["issue", "close", str(n), "--repo", repo, "--comment", comment], cwd=root)
    return (c.stdout + c.stderr).strip()[:300] or None


def _close_result(it, before, after, close_out):
    """before と after から 1 課題の結果を作る（already_closed / closed / failed）。"""
    repo, n = it["repo"], it["number"]
    if before == "CLOSED":
        return {**it, "result": "already_closed"}
    if after == "CLOSED":
        out = {**it, "result": "closed", "cmd": f"gh issue reopen {n} --repo {repo}"}
        if close_out:
            out["reason"] = close_out
        return out
    why = "after を読めない" if after is None else f"閉じた後も {after}"
    if close_out:
        why += f"（close の出力: {close_out}）"
    return {**it, "result": "failed", "reason": why, "cmd": f"gh issue close {n} --repo {repo}"}


def _record_repo(a, root):
    if a.repo:
        return a.repo
    p = gh_call.gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], cwd=root)
    record_repo = p.stdout.strip()
    if p.returncode != 0 or not record_repo:
        raise StepError("記録のリポジトリを決められない（--repo を渡す）", EXIT_PRECONDITION)
    return record_repo


def _read_sprint_record(a, root, record_repo):
    if a.record_pr == 0:
        return {"found": True, "stage": "配布なし（本番の記録なし）", "sprint_prs": [], "verify_block": None}, []
    if a.record_pr == RECORD_UNKNOWN:
        return {"found": False, "stage": "記録の置き場が分からない", "sprint_prs": [], "verify_block": None}, []
    rec = parse_record(*read_record(root, record_repo, a.record_pr))
    return rec, a.prs or rec["sprint_prs"]


def _closing_blocker(a, rec, record_repo):
    stage = rec["stage"] or ""
    kept_all = None
    if a.record_pr == 0:
        pass  # 本番の記録なし: 閉じる条件（配布とリリース後テスト）を見ない
    elif a.record_pr == RECORD_UNKNOWN:
        kept_all = UNKNOWN_REASON  # 記録を読めないので閉じない（#1875）
    elif not rec["found"] or not stage:
        kept_all = "配布の記録が読めない"
    elif not (stage.startswith("本番") or stage.startswith("配布なし")):
        kept_all = "本番への配布の前（本番への配布の後に閉じる）"
    verdicts = None
    verify = a.with_verification or a.final_stage == STAGE_VERIFY  # 最終工程がリリース後テストなら必ず見る（I4）
    if kept_all is None and verify and a.record_pr != 0:
        blk = rec["verify_block"]
        if blk is None:
            kept_all = (
                "配布なしの後のリリース後テストの記録が無い" if stage.startswith("配布なし") else "本番の版のリリース後テストの記録が無い"
            )
        else:
            verdicts = verification_verdicts(blk, record_repo)
            if verdicts is None:
                kept_all = "条件と課題の対応が読めない"
    return kept_all, verdicts


def _read_state_body(root, repo, n):
    """状態と本文を 1 回で読む。読めなければ None。"""
    p = gh_call.gh(["issue", "view", str(n), "--repo", repo, "--json", "state,body"], cwd=root)
    try:
        d = json.loads(p.stdout) if p.returncode == 0 else None
    except ValueError:
        d = None
    if not isinstance(d, dict) or not d.get("state"):
        return None
    return d["state"], d.get("body") or ""


def _judge_and_mark(a, ctx, repo, n, rows):
    """錠の中で状態と本文を読み、条件 2 と条件 3 を判定し、満たせば印を書く。(結果の項目か None, 印を付けた番号) を返す。"""
    base = _issue_base(repo, n)
    got = _read_state_body(ctx.root, repo, n)
    if got is None:
        return _failed(base, UNREADABLE_BODY, ctx), []
    state, body = got
    ok, why, to_check = judge_acceptance(rows, acceptance.criteria(body), acceptance.pre_merge_numbers(body))
    own = repo == ctx.record_repo  # 他のリポジトリの課題の本文は書かない（I7）
    if not ok:
        if state == "CLOSED":
            return {**base, "result": "closed_unverified", "reason": UNVERIFIED + why}, []
        return {**base, "result": "kept_open", "reason": why}, []
    gap = final_stage_gap(ctx.final_stage, body, ctx.since)
    if gap:  # 条件 3 を満たさない課題の本文は書かない
        return gap_item(base, state, gap), []
    if a.dry_run:
        out = {**base, "result": _dry_run_result(state)}
        return ({**out, "would_check": to_check} if own and to_check else out), []
    if not (own and to_check):
        return None, []
    new = acceptance.check_criteria(body, to_check)
    if not acceptance.only_marks(body, new) or not gh_rest.update_body(repo, n, new):
        return _failed(base, "受け入れ条件の印を書けない", ctx), []
    return None, to_check


def _close_verified(a, ctx, repo, n, rows):
    """--with-verification の 1 課題。本文の読みから書きまでを課題ごとの錠の中で行い、閉じるのは錠の外で行う。"""
    lock = gh_parts.issue_lock(repo, n, LOCK_TIMEOUT) if repo == ctx.record_repo and not a.dry_run else nullcontext()
    try:
        with lock:
            done, checked = _judge_and_mark(a, ctx, repo, n, rows)
    except locks.LockTimeout:
        return _failed(_issue_base(repo, n), "本文の錠を取れない", ctx)
    if done is not None:
        return done
    out = close_one(ctx, repo, n)
    return {**out, "checked": checked} if checked else out


def _close_item(a, ctx, repo, n, kept_all, verdicts):
    base = _issue_base(repo, n)
    if kept_all:
        return {**base, "result": "kept_open", "reason": kept_all}
    if verdicts is not None:
        return _close_verified(a, ctx, repo, n, verdicts.get((repo, n)))
    if ctx.final_stage == STAGE_RETRO:
        got = _read_state_body(ctx.root, repo, n)
        if got is None:  # 記録の置き場を読めないときは記録があるとは読まない（I2）
            return _failed(base, UNREADABLE_BODY, ctx)
        state, body = got
        gap = final_stage_gap(ctx.final_stage, body, ctx.since)
        if gap:
            return gap_item(base, state, gap)
        if a.dry_run:
            return {**base, "result": _dry_run_result(state)}
    if a.dry_run:
        st = issue_state(ctx.root, repo, n)
        return {
            **base,
            "result": _dry_run_result(st),
            **({"reason": "状態を読めない"} if st is None else {}),
        }
    return close_one(ctx, repo, n)


def _close_summary(items, prs, dry_run, referenced):
    kinds = ("closed", "already_closed", "failed", "closed_unverified", "kept_open", "would_close")
    count = {k: sum(1 for i in items if i["result"] == k) for k in kinds}
    metrics = {"issues": len(items), **count, "referenced_only": referenced, "prs": len(prs)}
    summary = (
        f"閉じた {count['closed']} 件・既に閉じていた {count['already_closed']} 件・"
        f"失敗 {count['failed']} 件・閉じているが条件を満たさない {count['closed_unverified']} 件・"
        f"開いたまま {count['kept_open']} 件（うち参照だけ {referenced} 件）"
    )
    if dry_run:
        summary += f"（試行。閉じる予定 {count['would_close']} 件）"
    return count, metrics, summary


def _stop_no_issues(prs, refs):
    """スプリントの PR を読んだのに閉じる課題が 0 件。課題もボードも書かずに止まる（参照の状態は読まない）。"""
    seen = f"（PR が参照した番号: {' '.join(f'#{n}' for n in refs)}）" if refs else ""
    _stop_close(
        f"閉じる課題が 0 件（スプリントの PR {len(prs)} 本に closing keywords（GitHub が課題を自動で閉じるキーワード"
        " Closes / Fixes / Resolves）が無く、--issues も無い）",
        f"閉じる課題を --issues で渡して打ち直す{seen}",
        {"issues": 0, "prs": len(prs)},
    )


def _stop_close(summary, nxt, metrics=None):
    """閉じる前に止まる（items は空・終了コードは EXIT_UNREADABLE）。"""
    emit(
        result(TOOL, "stopped", summary, [], {"issues": 0} if metrics is None else metrics, next=nxt),
        EXIT_UNREADABLE,
    )


def _collect_close_issues(a, root, record_repo, prs):
    """閉じる課題（スプリントの PR が閉じる課題と --issues）と、PR が参照しただけの番号を集める。0 件なら止まる。"""
    given = [(record_repo, n) for n in a.issues or []]
    if not prs and not given:
        _stop_close("スプリントの PR の一覧が取れない。推測せず運用者に一覧を聞く", "運用者に一覧を聞き、--prs で渡して打ち直す")
    issues, refs = sprint_issues(root, record_repo, prs) if prs else ([], [])
    issues += [k for k in given if k not in issues]
    if not issues:
        _stop_no_issues(prs, refs)
    return issues, refs


def _emit_close_result(a, items, notes, metrics, summary, count, decisions):
    """結果を出す: 失敗があれば止める → 記録の置き場が分からなければ閉じない旨 → 通常（最終工程の記録の欠けを案内）。"""
    if count["failed"]:
        emit(
            result(
                TOOL,
                "stopped",
                summary,
                items + notes,
                metrics,
                next="失敗した課題の cmd でやり直す。棚卸しへ進まない",
                decisions=decisions,
            )
        )
    if a.record_pr == RECORD_UNKNOWN:
        summary = f"記録の置き場が分からないため閉じない（リリースの経路が雛形でも昇格でもない）: {summary}"
        nxt = "リリース記録とリリース後テストの記録を確かめ、受け入れ条件を満たした課題を手で閉じる"
        emit(result(TOOL, "ok", summary, items + notes, metrics, next=nxt, decisions=decisions))
    nxt = None
    if any(FINAL_GAP_LEAD in (i.get("reason") or "") for i in items):
        nxt = "最終工程の記録が無い課題は、その工程（振り返りなど）を通してから打ち直す。前の版で工程を通った課題は確かめて手で閉じる"
    emit(result(TOOL, "ok", summary, items + notes, metrics, next=nxt, decisions=decisions))


def cmd_close(a):
    if a.record_pr in (0, RECORD_UNKNOWN) and not a.issues:
        what = "0（本番の記録なし）" if a.record_pr == 0 else f"{RECORD_UNKNOWN}（記録の置き場が分からない）"
        _stop_close(f"--record-pr {what} は --issues と一緒に渡す", "閉じる課題を --issues で渡して打ち直す")
    root = git_root(a.root)
    record_repo = _record_repo(a, root)
    rec, prs = _read_sprint_record(a, root, record_repo)
    issues, refs = _collect_close_issues(a, root, record_repo, prs)

    kept_all, verdicts = _closing_blocker(a, rec, record_repo)

    items = []
    comment = f"スプリント（{a.label}）を通りました" if a.label else "スプリントの終わりの工程を通りました"
    since = NO_BASE if a.record_pr == 0 else rec.get("dist_comment_id")
    ctx = CloseContext(root, record_repo, comment, a.argv, a.final_stage, since)
    notes = ctx.notes
    for repo, n in issues:
        items.append(_close_item(a, ctx, repo, n, kept_all, verdicts))
    refs_items = referenced_items(root, record_repo, refs, issues)
    items += refs_items

    count, metrics, summary = _close_summary(items, prs, a.dry_run, referenced=len(refs_items))
    metrics["final_stage"] = a.final_stage
    decisions = [
        {"decision": f"最終工程を {a.final_stage} として判定した", "basis": f"--final-stage {a.final_stage}（省略時は{STAGE_RETRO}）"}
    ]
    _emit_close_result(a, items, notes, metrics, summary, count, decisions)


class Parser(argparse.ArgumentParser):
    def error(self, message):  # 呼び出しの誤りは 3
        self.print_usage(sys.stderr)
        print(f"{self.prog}: {message}", file=sys.stderr)
        raise SystemExit(EXIT_PRECONDITION)


def number_list(s):
    try:
        return [int(x.strip().lstrip("#")) for x in s.replace(" ", ",").split(",") if x.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError(f"PR 番号の並びでない: {s}")


def record_pr_arg(s):
    """--record-pr の値: 0 以上の整数か unknown。"""
    if s.strip() == RECORD_UNKNOWN:
        return RECORD_UNKNOWN
    try:
        n = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"PR 番号・0・{RECORD_UNKNOWN} のどれでもない: {s}")
    if n < 0:
        raise argparse.ArgumentTypeError(f"PR 番号・0・{RECORD_UNKNOWN} のどれでもない: {s}")
    return n


def build_parser():
    ap = Parser(prog="sprint-close.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--record-pr",
        type=record_pr_arg,
        required=True,
        help="配布の記録を置いた PR の番号。0 は本番の記録なし、unknown は記録の置き場が分からない（どちらも --issues と一緒に渡す）",
    )
    ap.add_argument("--prs", type=number_list, help="スプリントの PR の番号（カンマ区切り）。省けば配布の記録から読む")
    ap.add_argument(
        "--issues",
        type=number_list,
        help="閉じる課題の番号（カンマ区切り）。PR の closing keywords（GitHub が課題を自動で閉じるキーワード Closes / Fixes / Resolves）が指す課題と和を取る",
    )
    ap.add_argument("--repo", help="記録のリポジトリ（owner/name）。省けば gh repo view で決める")
    ap.add_argument("--with-verification", action="store_true", help="リリース後テストを通る経路（閉じる条件 2 を見る）")
    ap.add_argument(
        "--final-stage",
        choices=FINAL_STAGES,
        default=STAGE_RETRO,
        help="その実行の最終工程（閉じる条件 3）。省けば振り返り。リリース後テストは条件 2 を必ず見る",
    )
    ap.add_argument("--label", help="閉じるときのコメントに入れる「<マイルストーン>の<工程名>」")
    ap.add_argument("--dry-run", action="store_true", help="状態を読むだけで、ボードも課題も書かない")
    ap.add_argument("--root", help="対象のリポジトリの根（既定はカレントの git の根）")
    ap.set_defaults(func=cmd_close)
    return ap


def main(argv=None):
    ap = build_parser()
    ap.set_defaults(argv=list(sys.argv[1:] if argv is None else argv))  # やり直しの cmd に並べる今の引数
    return main_with(ap, lambda a: TOOL, argv)


if __name__ == "__main__":
    sys.exit(main())
