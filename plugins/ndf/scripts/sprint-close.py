#!/usr/bin/env python3
"""sprint-close.py: スプリントを閉じる（progress-tracking の「スプリントを閉じる」の手順）。

    python3 sprint-close.py --record-pr <PR番号> [--prs 1,2] [--issues 3,4] [--with-verification]
                             [--label "<マイルストーン>の<工程名>"] [--dry-run] [--root <dir>]

1. 記録の PR の本文とコメントを読み、最後の「## 配布の記録」ブロックを取る
2. スプリントの PR の一覧（--prs、無ければブロックの `スプリント:` の行）の本文から、closing keywords
   （GitHub が課題を自動で閉じるキーワード Closes / Fixes / Resolves）が指す課題を集める
   （lib/closing-issues.sh）。--issues の課題（記録のリポジトリ）を足す（`pace: fast` の実装 PR は
   closing keywords を持たない）。PR を読んだのに集めた課題が 0 件なら、何も書かずに止まる（終了コード 2）
3. 閉じる条件 1（本番への配布まで済んだ、または配布なし）と、--with-verification のときは
   条件 2（その課題のリリース後テストの行がすべて合格で、本文の受け入れ条件の番号のすべてに合格の行がある）
   を課題ごとに判定する。`課題` の列が課題を指さない行（`—`・`PR 1869` など）は飛ばす。条件 2 は課題ごとの錠
   （lib/gh_parts.py の issue_lock）の中で状態と本文を 1 回で読んで判定し、満たした記録のリポジトリの課題は
   本文の受け入れ条件に印（`[x]`）を付けてから閉じる（規則は lib/acceptance.py。#1683）
4. 条件を満たす課題ごとに (a) 状態を読む → (b) 記録のリポジトリの課題ならボードを Done →
   (c) 読み直して OPEN なら閉じる → (d) 読み直して CLOSED を確かめる
5. スプリントの PR の題と本文が番号で参照し、2 で集めた課題に入らない開いた課題（参照だけの課題、
   lib/closing.py の referenced_numbers）を kept_open で載せる。閉じず、ボードも書かない

結果は lib/step_result.py の形の 1 行の JSON。items は課題ごとに
{kind:"issue", repo, number, result, reason?, cmd?, checked?, would_check?}。result は
closed / already_closed / failed / kept_open / closed_unverified（既に閉じていたが条件 2 を満たさない。
開き直さない）（--dry-run では would_close）。checked は印を付けた受け入れ条件の番号、would_check は
--dry-run で付ける予定の番号。ボードの NOTE は {kind:"board_note"} の項目に載る。
`--record-pr 0` は「本番の記録なし」（最終の検査で変更が無く本番を飛ばした）。配布の記録を読まず、
閉じる条件も見ずに --issues の課題を閉じる。--issues と一緒のときだけ受ける。
終了コード: 0 = 失敗なし / 1 = 失敗あり（棚卸しへ進まない）/ 2 = 一覧が取れない・
閉じる課題が 0 件・--record-pr 0 に --issues が無い / 3 = 呼び出しの誤り。
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
import deps  # noqa: E402

deps.require("md", "locks")
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
import md  # noqa: E402
from closing import referenced_numbers  # noqa: E402

from dist_record import parse_record  # noqa: E402  記録の形は lib/dist_record.py が持つ（#1273）

TOOL = "sprint-close"
LOCK_TIMEOUT = 30  # 課題ごとの錠の待ち（body-lock の既定と同じ）


# --- 記録の読み取り -------------------------------------------------------------


def read_record(root, repo, n):
    """記録の PR の本文とコメントを投稿の順に 1 つの文字列にする。"""
    p = gh_call.gh(["pr", "view", str(n), "--repo", repo, "--json", "body,comments"], cwd=root)
    if p.returncode != 0:
        raise StepError(f"記録の PR #{n} を読めない: {p.stderr.strip()[:300]}", EXIT_UNREADABLE)
    try:
        d = json.loads(p.stdout)
    except ValueError:
        raise StepError(f"記録の PR #{n} の出力を読めない", EXIT_UNREADABLE)
    parts = [d.get("body") or ""] + [(c or {}).get("body") or "" for c in d.get("comments") or []]
    return "\n".join(parts)


class IssueRows(NamedTuple):
    """1 課題のリリース後テストの行のまとめ。passed は合格の行にあり、合格でない行に無い受け入れ条件の番号。"""

    all_pass: bool
    passed: frozenset


_ISSUE_CELL = re.compile(r"^(?:([A-Za-z0-9._-]+/[A-Za-z0-9._-]+))?#(\d+)")
_CRITERION_CELL = re.compile(r"^(\d+)\.")


def verification_verdicts(block, record_repo):
    """表を課題でまとめ、{(repo, number): IssueRows} を返す。表が読めない・課題を指す行が無ければ None。

    表は lib/md.py（GFM）で読む。欠けたセルは空として読むため、`結果` の欠けた行は合格でない。
    `課題` の列の頭が課題の参照でない行（導入の確認の `—`・`PR 1869` など）は飛ばす（#1683 の I1）。
    `受け入れ条件` の列の頭の `<番号>.` を受け入れ条件の番号として読む。"""
    found = md.tables(block)
    if not found:
        return None
    head = [c.strip() for c in found[0].header]
    if "課題" not in head or "結果" not in head:
        return None
    ci, ri = head.index("課題"), head.index("結果")
    ai = head.index("受け入れ条件") if "受け入れ条件" in head else None
    acc = {}
    for row in found[0].rows:
        cs = [c.strip() for c in row]  # md は行のセルを見出しの数へ揃える
        m = _ISSUE_CELL.match(cs[ci])
        if not m:
            continue
        key = (m.group(1) or record_repo, int(m.group(2)))
        ok = cs[ri].startswith("合格")
        all_pass, passed, failed = acc.get(key, (True, set(), set()))
        num = _CRITERION_CELL.match(cs[ai]) if ai is not None else None
        if num:
            (passed if ok else failed).add(int(num.group(1)))
        acc[key] = (all_pass and ok, passed, failed)
    if not acc:
        return None
    return {k: IssueRows(a, frozenset(p - f)) for k, (a, p, f) in acc.items()}


NO_ROWS = "リリース後テストの行が無い"
NOT_PASSED = "リリース後テストに合格でない条件がある（不合格・保留）"
UNVERIFIED = "閉じているが閉じる条件 2 を満たさない: "


def judge_acceptance(rows, criteria):
    """閉じる条件 2 の判定。(満たすか, 満たさない理由, 印を付ける番号) を返す。

    受け入れ条件が 0 個なら表の行の合否だけで決める。"""
    if rows is None:
        return False, NO_ROWS, []
    if not rows.all_pass:
        return False, NOT_PASSED, []
    missing = [c.number for c in criteria if c.number not in rows.passed]
    if missing:
        return False, "合格の行が無い受け入れ条件: " + ", ".join(map(str, missing)), []
    return True, None, [c.number for c in criteria if not c.checked]


# --- 課題の収集 -----------------------------------------------------------------


def _read_sprint_pr(root, repo, n):
    """スプリントの PR の題と本文を 1 回で読む。"""
    p = gh_call.gh(["pr", "view", str(n), "--repo", repo, "--json", "title,body"], cwd=root)
    if p.returncode != 0:
        raise StepError(f"スプリントの PR #{n} を読めない: {p.stderr.strip()[:300]}", EXIT_UNREADABLE)
    try:
        d = json.loads(p.stdout)
    except ValueError:
        raise StepError(f"スプリントの PR #{n} の出力を読めない", EXIT_UNREADABLE)
    return d.get("title") or "", d.get("body") or ""


def sprint_issues(root, repo, prs):
    """(closing keywords が指す課題, 題と本文が参照する記録のリポジトリの番号) を返す。

    closing keywords は本文だけから読む（GitHub は題の closing keywords で課題を閉じない）。"""
    seen, out, refs = set(), [], []
    for n in prs:
        title, body = _read_sprint_pr(root, repo, n)
        refs += [r for r in referenced_numbers(title + "\n" + body, repo) if r not in refs]
        c = subprocess.run(
            ["bash", str(HERE / "lib" / "closing-issues.sh"), "--repo", repo], cwd=root, input=body, capture_output=True, text=True
        )
        for ln in c.stdout.splitlines():
            if "\t" not in ln:
                continue
            r, num = ln.split("\t", 1)
            key = (r.strip(), int(num.strip()))
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out, refs


# --- 閉じる ---------------------------------------------------------------------


def issue_state(root, repo, n):
    p = gh_call.gh(["issue", "view", str(n), "--repo", repo, "--json", "state", "-q", ".state"], cwd=root)
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


class CloseContext:
    """全課題で一定の閉じる処理の材料。notes は課題ごとのボードの NOTE を溜める。retry_cmd は今の引数で組んだやり直しの cmd。"""

    def __init__(self, root, record_repo, comment, argv=()):
        self.root, self.record_repo, self.comment = root, record_repo, comment
        self.notes = []
        self.retry_cmd = shlex.join(["python3", str(Path(__file__).resolve()), *argv])


def close_one(ctx, repo, n):
    root, record_repo, comment, notes = ctx.root, ctx.record_repo, ctx.comment, ctx.notes
    it = {"kind": "issue", "repo": repo, "number": n}
    before = issue_state(root, repo, n)
    if before is None:
        return {**it, "result": "failed", "reason": "before を読めない", "cmd": f"gh issue close {n} --repo {repo}"}
    if repo == record_repo:
        s = run(["bash", str(HERE / "projects-sync.sh"), str(n), "status", "Done"], cwd=root, check=False)
        for ln in (s.stdout + s.stderr).splitlines():
            if "NOTE" in ln:
                notes.append({"kind": "board_note", "name": f"{repo}#{n}", "result": "note", "reason": ln.strip()})
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
    rec = parse_record(read_record(root, record_repo, a.record_pr))
    return rec, a.prs or rec["sprint_prs"]


def _closing_blocker(a, rec, record_repo):
    stage = rec["stage"] or ""
    kept_all = None
    if a.record_pr == 0:
        pass  # 本番の記録なし: 閉じる条件（配布とリリース後テスト）を見ない
    elif not rec["found"] or not stage:
        kept_all = "配布の記録が読めない"
    elif not (stage.startswith("本番") or stage.startswith("配布なし")):
        kept_all = "本番への配布の前（本番への配布の後に閉じる）"
    verdicts = None
    if kept_all is None and a.with_verification and a.record_pr != 0:
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
    """錠の中で状態と本文を読み、条件 2 を判定し、満たせば印を書く。(結果の項目か None, 印を付けた番号) を返す。"""
    base = {"kind": "issue", "repo": repo, "number": n}
    got = _read_state_body(ctx.root, repo, n)
    if got is None:
        return {**base, "result": "failed", "reason": "本文を読めない", "cmd": ctx.retry_cmd}, []
    state, body = got
    ok, why, to_check = judge_acceptance(rows, acceptance.criteria(body))
    own = repo == ctx.record_repo  # 他のリポジトリの課題の本文は書かない（I7）
    if not ok:
        if state == "CLOSED":
            return {**base, "result": "closed_unverified", "reason": UNVERIFIED + why}, []
        return {**base, "result": "kept_open", "reason": why}, []
    if a.dry_run:
        out = {**base, "result": "already_closed" if state == "CLOSED" else "would_close"}
        return ({**out, "would_check": to_check} if own and to_check else out), []
    if not (own and to_check):
        return None, []
    new = acceptance.check_criteria(body, to_check)
    if not acceptance.only_marks(body, new) or not gh_rest.update_body(repo, n, new):
        return {**base, "result": "failed", "reason": "受け入れ条件の印を書けない", "cmd": ctx.retry_cmd}, []
    return None, to_check


def _close_verified(a, ctx, repo, n, rows):
    """--with-verification の 1 課題。本文の読みから書きまでを課題ごとの錠の中で行い、閉じるのは錠の外で行う。"""
    lock = gh_parts.issue_lock(repo, n, LOCK_TIMEOUT) if repo == ctx.record_repo and not a.dry_run else nullcontext()
    try:
        with lock:
            done, checked = _judge_and_mark(a, ctx, repo, n, rows)
    except locks.LockTimeout:
        return {"kind": "issue", "repo": repo, "number": n, "result": "failed", "reason": "本文の錠を取れない", "cmd": ctx.retry_cmd}
    if done is not None:
        return done
    out = close_one(ctx, repo, n)
    return {**out, "checked": checked} if checked else out


def _close_item(a, ctx, repo, n, kept_all, verdicts):
    base = {"kind": "issue", "repo": repo, "number": n}
    if kept_all:
        return {**base, "result": "kept_open", "reason": kept_all}
    if verdicts is not None:
        return _close_verified(a, ctx, repo, n, verdicts.get((repo, n)))
    if a.dry_run:
        st = issue_state(ctx.root, repo, n)
        return {
            **base,
            "result": "already_closed" if st == "CLOSED" else "would_close",
            **({"reason": "状態を読めない"} if st is None else {}),
        }
    return close_one(ctx, repo, n)


NOT_GIVEN = "closing keywords（GitHub が課題を自動で閉じるキーワード Closes / Fixes / Resolves）も --issues も無い参照"


def _referenced_items(root, repo, refs, issues):
    """参照だけの課題の行。1 番号につき状態と URL を 1 回読み、Pull Request と閉じた課題は行にしない。"""
    items = []
    for n in refs:
        if (repo, n) in issues:
            continue
        p = gh_call.gh(["issue", "view", str(n), "--repo", repo, "--json", "state,url", "-q", "[.state, .url] | @tsv"], cwd=root)
        state, _, url = p.stdout.strip().partition("\t")
        base = {"kind": "issue", "repo": repo, "number": n, "result": "kept_open"}
        if p.returncode != 0 or not state:
            items.append({**base, "reason": f"状態を読めない（{NOT_GIVEN}）"})
        elif "/pull/" not in url and state != "CLOSED":
            items.append({**base, "reason": NOT_GIVEN})
    return items


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
    emit(
        result(
            TOOL,
            "stopped",
            f"閉じる課題が 0 件（スプリントの PR {len(prs)} 本に closing keywords（GitHub が課題を自動で閉じるキーワード"
            " Closes / Fixes / Resolves）が無く、--issues も無い）",
            [],
            {"issues": 0, "prs": len(prs)},
            next=f"閉じる課題を --issues で渡して打ち直す{seen}",
        ),
        EXIT_UNREADABLE,
    )


def cmd_close(a):
    if a.record_pr == 0 and not a.issues:
        emit(
            result(
                TOOL,
                "stopped",
                "--record-pr 0（本番の記録なし）は --issues と一緒に渡す",
                [],
                {"issues": 0},
                next="閉じる課題を --issues で渡して打ち直す",
            ),
            EXIT_UNREADABLE,
        )
    root = git_root(a.root)
    record_repo = _record_repo(a, root)
    given = [(record_repo, n) for n in a.issues or []]
    rec, prs = _read_sprint_record(a, root, record_repo)
    if not prs and not given:
        emit(
            result(
                TOOL,
                "stopped",
                "スプリントの PR の一覧が取れない。推測せず運用者に一覧を聞く",
                [],
                {"issues": 0},
                next="運用者に一覧を聞き、--prs で渡して打ち直す",
            ),
            EXIT_UNREADABLE,
        )
    issues, refs = sprint_issues(root, record_repo, prs) if prs else ([], [])
    issues += [k for k in given if k not in issues]
    if not issues:
        _stop_no_issues(prs, refs)

    kept_all, verdicts = _closing_blocker(a, rec, record_repo)

    items = []
    comment = f"スプリント（{a.label}）を通りました" if a.label else "スプリントの終わりの工程を通りました"
    ctx = CloseContext(root, record_repo, comment, a.argv)
    notes = ctx.notes
    for repo, n in issues:
        items.append(_close_item(a, ctx, repo, n, kept_all, verdicts))
    refs_items = _referenced_items(root, record_repo, refs, issues)
    items += refs_items

    count, metrics, summary = _close_summary(items, prs, a.dry_run, referenced=len(refs_items))
    if count["failed"]:
        emit(result(TOOL, "stopped", summary, items + notes, metrics, next="失敗した課題の cmd でやり直す。棚卸しへ進まない"))
    emit(result(TOOL, "ok", summary, items + notes, metrics))


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


def build_parser():
    ap = Parser(prog="sprint-close.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--record-pr", type=int, required=True, help="配布の記録を置いた PR の番号。0 は本番の記録なし（--issues と一緒に渡す）"
    )
    ap.add_argument("--prs", type=number_list, help="スプリントの PR の番号（カンマ区切り）。省けば配布の記録から読む")
    ap.add_argument(
        "--issues",
        type=number_list,
        help="閉じる課題の番号（カンマ区切り）。PR の closing keywords（GitHub が課題を自動で閉じるキーワード Closes / Fixes / Resolves）が指す課題と和を取る",
    )
    ap.add_argument("--repo", help="記録のリポジトリ（owner/name）。省けば gh repo view で決める")
    ap.add_argument("--with-verification", action="store_true", help="リリース後テストを通る経路（閉じる条件 2 を見る）")
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
