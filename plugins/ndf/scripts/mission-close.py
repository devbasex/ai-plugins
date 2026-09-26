#!/usr/bin/env python3
"""mission-close.py: ミッションを閉じる（progress-tracking の「ミッションを閉じる」の手順）。

    python3 mission-close.py --record-pr <PR番号> [--prs 1,2] [--issues 3,4] [--with-verification]
                             [--label "<マイルストーン>の<工程名>"] [--dry-run] [--root <dir>]

1. 記録の PR の本文とコメントを読み、最後の「## 配布の記録」ブロックを取る
2. ミッションの PR の一覧（--prs、無ければブロックの `ミッション:` の行）の本文から、閉じる語が
   指す課題を集める（lib/closing-issues.sh）。--issues の課題（記録のリポジトリ）を足す
   （`pace: fast` の実装 PR は閉じる語を持たない）
3. 閉じる条件 1（本番への配布まで済んだ、または配布なし）と、--with-verification のときは
   条件 2（その課題の受け入れ条件がすべて合格）を課題ごとに判定する
4. 条件を満たす課題ごとに (a) 状態を読む → (b) 記録のリポジトリの課題ならボードを Done →
   (c) 読み直して OPEN なら閉じる → (d) 読み直して CLOSED を確かめる

結果は lib/step_result.py の形の 1 行の JSON。items は課題ごとに
{kind:"issue", repo, number, result, reason?, cmd?}。result は
closed / already_closed / failed / kept_open（--dry-run では would_close）。ボードの NOTE は
{kind:"board_note"} の項目に載る。
`--record-pr 0` は「本番の記録なし」（最終の検査で変更が無く本番を飛ばした）。配布の記録を読まず、
閉じる条件も見ずに --issues の課題を閉じる。--issues と一緒のときだけ受ける。
終了コード: 0 = 失敗なし / 1 = 失敗あり（issue-upkeep へ進まない）/ 2 = 一覧が取れない・
--record-pr 0 に --issues が無い / 3 = 呼び出しの誤り。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
import deps  # noqa: E402

deps.require("md")
from step_result import (EXIT_PRECONDITION, EXIT_UNREADABLE, StepError, emit, git_root,  # noqa: E402
                         main_with, result, run)
import gh_call  # noqa: E402
import md  # noqa: E402

TOOL = "mission-close"
DIST = "## 配布の記録"
VERIFY = "## リリース後テスト"


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


def parse_record(text):
    """最後の配布の記録と、使うリリース後テストのブロックを読む。

    戻り値: {"found", "stage", "version", "mission_prs", "verify_block"}。
    verify_block は本番なら対象の版が一致する最後のブロック、配布なしなら最後の配布の記録より
    後の最後のブロック。無ければ None。
    """
    lines = text.splitlines()
    sections = [s for s in md.md_sections(text) if lines[s.heading.line].startswith("#")]
    dists = [s for s in sections if lines[s.heading.line].startswith(DIST)]
    dist_start = dists[-1].heading.line if dists else None
    out = {"found": dist_start is not None, "stage": None, "version": None, "mission_prs": [],
           "verify_block": None}
    if dist_start is None:
        return out
    block = lines[dists[-1].start:dists[-1].end]
    for ln in block:
        if ln.startswith("段階: ") and out["stage"] is None:
            out["stage"] = ln[len("段階: "):].strip()
        elif ln.startswith(("ミッション: ", "まとまり: ")):  # 旧い記録（まとまり）も読む
            out["mission_prs"] = [int(x) for x in re.findall(r"#(\d+)", ln)]
    if (out["stage"] or "").startswith("本番"):
        for ln in block:
            if ln.startswith("版: ") and "→" in ln:
                out["version"] = re.sub(r"\s*（.*$", "", ln.split("→", 1)[1]).strip()
                break

    blocks = []  # (開始行, 対象の版, 行の並び)
    verify_heads = {s.heading.line for s in sections if lines[s.heading.line].startswith(VERIFY)}
    cur = None
    for i, ln in enumerate(lines):
        if i in verify_heads:
            cur = {"start": i, "ver": None, "lines": [ln]}
            continue
        if cur is None:
            continue
        cur["lines"].append(ln)
        if ln.startswith("対象の版: ") and cur["ver"] is None:
            cur["ver"] = re.sub(r"\s*（.*$", "", ln[len("対象の版: "):]).strip()
        if ln.startswith("合否:"):
            blocks.append(cur)
            cur = None
    stage = out["stage"] or ""
    if stage.startswith("配布なし"):
        after = [b for b in blocks if b["start"] > dist_start]
        out["verify_block"] = "\n".join(after[-1]["lines"]) if after else None
    elif out["version"]:
        hit = [b for b in blocks if b["ver"] == out["version"]]
        out["verify_block"] = "\n".join(hit[-1]["lines"]) if hit else None
    return out


def verification_verdicts(block, record_repo):
    """表を課題でまとめ、{(repo, number): 全部合格か} を返す。表が読めなければ None。

    表は lib/md.py（GFM）で読む。欠けたセルは空として読むため、`結果` の欠けた行は合格でない。"""
    found = md.tables(block)
    if not found:
        return None
    head = [c.strip() for c in found[0].header]
    if "課題" not in head or "結果" not in head:
        return None
    ci, ri = head.index("課題"), head.index("結果")
    verdicts = {}
    for row in found[0].rows:
        cs = [c.strip() for c in row]  # md は行のセルを見出しの数へ揃える
        m = re.search(r"(?:([A-Za-z0-9._-]+/[A-Za-z0-9._-]+))?#(\d+)", cs[ci])
        if not m:
            return None
        key = (m.group(1) or record_repo, int(m.group(2)))
        verdicts[key] = verdicts.get(key, True) and cs[ri].startswith("合格")
    return verdicts


# --- 課題の収集 -----------------------------------------------------------------

def mission_issues(root, repo, prs):
    seen, out = set(), []
    for n in prs:
        p = gh_call.gh(["pr", "view", str(n), "--repo", repo, "--json", "body", "-q", ".body"], cwd=root)
        if p.returncode != 0:
            raise StepError(f"ミッションの PR #{n} を読めない: {p.stderr.strip()[:300]}", EXIT_UNREADABLE)
        c = subprocess.run(["bash", str(HERE / "lib" / "closing-issues.sh"), "--repo", repo],
                           cwd=root, input=p.stdout, capture_output=True, text=True)
        for ln in c.stdout.splitlines():
            if "\t" not in ln:
                continue
            r, num = ln.split("\t", 1)
            key = (r.strip(), int(num.strip()))
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


# --- 閉じる ---------------------------------------------------------------------

def issue_state(root, repo, n):
    p = gh_call.gh(["issue", "view", str(n), "--repo", repo, "--json", "state", "-q", ".state"], cwd=root)
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def close_one(root, repo, n, record_repo, comment, notes):
    it = {"kind": "issue", "repo": repo, "number": n}
    before = issue_state(root, repo, n)
    if before is None:
        return {**it, "result": "failed", "reason": "before を読めない",
                "cmd": f"gh issue close {n} --repo {repo}"}
    if repo == record_repo:
        s = run(["bash", str(HERE / "projects-sync.sh"), str(n), "status", "Done"], cwd=root, check=False)
        for ln in (s.stdout + s.stderr).splitlines():
            if "NOTE" in ln:
                notes.append({"kind": "board_note", "name": f"{repo}#{n}", "result": "note", "reason": ln.strip()})
    close_out = None
    if before == "OPEN":
        now = issue_state(root, repo, n)
        if now == "OPEN":
            c = gh_call.gh(["issue", "close", str(n), "--repo", repo, "--comment", comment], cwd=root)
            close_out = (c.stdout + c.stderr).strip()[:300] or None
    after = issue_state(root, repo, n)
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


def cmd_close(a):
    if a.record_pr == 0 and not a.issues:
        emit(result(TOOL, "stopped", "--record-pr 0（本番の記録なし）は --issues と一緒に渡す", [], {"issues": 0},
                    next="閉じる課題を --issues で渡して打ち直す"), EXIT_UNREADABLE)
    root = git_root(a.root)
    record_repo = a.repo
    if not record_repo:
        p = gh_call.gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], cwd=root)
        record_repo = p.stdout.strip()
        if p.returncode != 0 or not record_repo:
            raise StepError("記録のリポジトリを決められない（--repo を渡す）", EXIT_PRECONDITION)
    given = [(record_repo, n) for n in a.issues or []]
    if a.record_pr == 0:
        rec = {"found": True, "stage": "配布なし（本番の記録なし）", "mission_prs": [], "verify_block": None}
        prs = []
    else:
        rec = parse_record(read_record(root, record_repo, a.record_pr))
        prs = a.prs or rec["mission_prs"]
    if not prs and not given:
        emit(result(TOOL, "stopped", "ミッションの PR の一覧が取れない。推測せず運用者に一覧を聞く",
                    [], {"issues": 0}, next="運用者に一覧を聞き、--prs で渡して打ち直す"), EXIT_UNREADABLE)
    issues = mission_issues(root, record_repo, prs) if prs else []
    issues += [k for k in given if k not in issues]

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
            kept_all = ("配布なしの後のリリース後テストの記録が無い" if stage.startswith("配布なし")
                        else "本番の版のリリース後テストの記録が無い")
        else:
            verdicts = verification_verdicts(blk, record_repo)
            if verdicts is None:
                kept_all = "条件と課題の対応が読めない"

    items, notes = [], []
    comment = f"ミッション（{a.label}）を通りました" if a.label else "ミッションの終わりの工程を通りました"
    for repo, n in issues:
        base = {"kind": "issue", "repo": repo, "number": n}
        if kept_all:
            items.append({**base, "result": "kept_open", "reason": kept_all})
            continue
        if verdicts is not None and not verdicts.get((repo, n), False):
            why = ("リリース後テストの行が無い" if (repo, n) not in verdicts
                   else "リリース後テストに合格でない条件がある（不合格・保留）")
            items.append({**base, "result": "kept_open", "reason": why})
            continue
        if a.dry_run:
            st = issue_state(root, repo, n)
            items.append({**base, "result": "already_closed" if st == "CLOSED" else "would_close",
                          **({"reason": "状態を読めない"} if st is None else {})})
            continue
        items.append(close_one(root, repo, n, record_repo, comment, notes))

    count = {k: sum(1 for i in items if i["result"] == k)
             for k in ("closed", "already_closed", "failed", "kept_open", "would_close")}
    metrics = {"issues": len(items), **count, "prs": len(prs)}
    summary = (f"閉じた {count['closed']} 件・既に閉じていた {count['already_closed']} 件・"
               f"失敗 {count['failed']} 件・開いたまま {count['kept_open']} 件")
    if a.dry_run:
        summary += f"（試行。閉じる予定 {count['would_close']} 件）"
    if count["failed"]:
        emit(result(TOOL, "stopped", summary, items + notes, metrics,
                    next="失敗した課題の cmd でやり直す。issue-upkeep へ進まない"))
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
    ap = Parser(prog="mission-close.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--record-pr", type=int, required=True,
                    help="配布の記録を置いた PR の番号。0 は本番の記録なし（--issues と一緒に渡す）")
    ap.add_argument("--prs", type=number_list, help="ミッションの PR の番号（カンマ区切り）。省けば配布の記録から読む")
    ap.add_argument("--issues", type=number_list, help="閉じる課題の番号（カンマ区切り）。PR の閉じる語と和を取る")
    ap.add_argument("--repo", help="記録のリポジトリ（owner/name）。省けば gh repo view で決める")
    ap.add_argument("--with-verification", action="store_true", help="リリース後テストを通る経路（閉じる条件 2 を見る）")
    ap.add_argument("--label", help="閉じるときのコメントに入れる「<マイルストーン>の<工程名>」")
    ap.add_argument("--dry-run", action="store_true", help="状態を読むだけで、ボードも課題も書かない")
    ap.add_argument("--root", help="対象のリポジトリの根（既定はカレントの git の根）")
    ap.set_defaults(func=cmd_close)
    return ap


def main(argv=None):
    return main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    sys.exit(main())
