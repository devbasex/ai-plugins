#!/usr/bin/env python3
"""pr-steps.py: pr の決まった手順（#858）。LLM に残るのはコミットメッセージ・PR 本文・同意の判断だけ。

    python3 pr-steps.py plan   [--draft] [--base B] [--message MSG] [--force] [--root DIR]
    python3 pr-steps.py commit (--message MSG | --message-file F) [--root DIR]
    python3 pr-steps.py push   [--root DIR]
    python3 pr-steps.py create --title T --body-file F [--draft] [--base B] [--root DIR]
    python3 pr-steps.py update --body-file F [--title T] [--pr N] [--root DIR]
    python3 pr-steps.py report [PR番号] [--root DIR]
    python3 pr-steps.py template --out F [--force]

結果は lib/step_result.py の形の 1 行の JSON。終了コードは 0 = ok / 1 = 違反（閉じる語がコミット
メッセージにある・push や作成が失敗）/ 2 = 読めない / 3 = 前提が無い（既定ブランチにいる・起点が
main 以外で別 Skill へ回す・PR が無い）。`plan` の `metrics` が同意の提示物の材料になる。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from step_result import (EXIT_PRECONDITION, EXIT_UNREADABLE, StepError, common_parser, emit,  # noqa: E402
                         git, git_root, main_with, result, run)
from pr_mode import needs_review, pr_target, split_stages, with_mode_line  # noqa: E402

TOOL = "pr"
SCRIPTS = Path(__file__).resolve().parent
REVIEW_MARK = "<!-- I want to review in Japanese. -->"
CLOSING = re.compile(r"\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*"
                     r"(https?://github\.com/[\w.-]+/[\w.-]+/issues/\d+|[\w.-]+/[\w.-]+#\d+|#\d+)", re.I)
# credential helper が応答しない環境の退避（lib/git-credential.sh と同じ値）
CREDENTIAL_FALLBACK = ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]
CHANGES_HEADING = "## 利用者向けの変化"  # 配布の CHANGELOG と更新案内の材料（release-steps.py notes が読む）
BODY_TEMPLATE = f"""<何を変えたかを 1〜3 文>

## Summary

- <変更の要点>

{CHANGES_HEADING}

- <利用者に何ができるようになるか・使い方が変わる点。今の決まりだけを書く。見える変化が無ければ「無し」>

## Test plan

- [x] `<実行したコマンド>` exit=0
"""
STAT_RE = re.compile(r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?")


# --- 値を読む -------------------------------------------------------------------

def current_branch(root):
    b = git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if not b or b == "HEAD":
        raise StepError("HEAD がブランチを指していない", EXIT_PRECONDITION)
    return b


def default_branch(root):
    p = git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False)
    if p.returncode == 0 and p.stdout.strip():
        return p.stdout.strip().split("/", 1)[-1]
    for b in ("main", "master"):
        if git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{b}", check=False).returncode == 0:
            return b
    return "main"


def declared_base(root):
    f = Path(root) / ".ndf" / "worktree.json"
    if f.is_file():
        try:
            v = json.loads(f.read_text(encoding="utf-8")).get("base_branch")
            return v if isinstance(v, str) and v else None
        except ValueError:
            return None
    return None


def base_ref(root, base):
    """比較に使う ref。origin/<base> があればそれ、無ければローカルの <base>。"""
    for ref in (f"origin/{base}", base):
        if git(root, "rev-parse", "--verify", "--quiet", ref, check=False).returncode == 0:
            return ref
    raise StepError(f"起点 {base} が無い（git fetch origin {base} を先に打つ）", EXIT_PRECONDITION)


def closing_words(text):
    return [m.group(0) for m in CLOSING.finditer(text or "")]


def closing_issues(text):
    return [m.group(2) for m in CLOSING.finditer(text or "")]


def is_graphql_limit(stderr):
    s = (stderr or "").lower()
    return "graphql" in s and "rate limit" in s


def repo_owner_name(root):
    p = run(["gh", "repo", "view", "--json", "owner,name"], cwd=root, check=False)
    if p.returncode == 0:
        try:
            d = json.loads(p.stdout)
            return d["owner"]["login"], d["name"]
        except (ValueError, KeyError, TypeError):
            pass
    url = git(root, "remote", "get-url", "origin", check=False).stdout.strip()
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", url)
    if not m:
        raise StepError("リポジトリの所有者と名前を決められない", EXIT_UNREADABLE)
    return m.group(1), m.group(2)


def existing_pr(root, branch):
    """head が branch の OPEN の PR（{number, url, isDraft, baseRefName}）。無ければ None。上限なら REST。"""
    p = run(["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number,url,isDraft,baseRefName"],
            cwd=root, check=False)
    if p.returncode == 0:
        try:
            prs = json.loads(p.stdout or "[]")
        except ValueError:
            raise StepError("gh pr list の出力を読めない", EXIT_UNREADABLE)
        return prs[0] if prs else None
    if not is_graphql_limit(p.stderr):
        raise StepError(f"gh pr list が失敗: {p.stderr.strip()[:300]}", EXIT_PRECONDITION)
    owner, name = repo_owner_name(root)
    q = run(["gh", "api", f"repos/{owner}/{name}/pulls?head={owner}:{branch}&state=open"], cwd=root, check=False)
    if q.returncode != 0:
        raise StepError(f"REST でも既存の PR を引けない: {q.stderr.strip()[:300]}")
    try:
        prs = json.loads(q.stdout or "[]")
    except ValueError:
        raise StepError("REST の出力を読めない", EXIT_UNREADABLE)
    if not prs:
        return None
    pr = prs[0]
    return {"number": pr["number"], "url": pr["html_url"], "isDraft": pr.get("draft", False),
            "baseRefName": pr.get("base", {}).get("ref", "")}


def diff_numbers(root, ref):
    last = (git(root, "diff", "--shortstat", f"{ref}..HEAD").stdout.strip().splitlines() or [""])[-1]
    m = STAT_RE.search(last)
    files, ins, dels = (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)) if m else (0, 0, 0)
    commits = int(git(root, "rev-list", "--count", f"{ref}..HEAD").stdout.strip() or 0)
    return {"commits": commits, "files": files, "insertions": ins, "deletions": dels, "shortstat": last}


# --- plan -------------------------------------------------------------------------

def cmd_plan(a):
    root = git_root(a.root)
    branch = current_branch(root)
    default = default_branch(root)
    declared = declared_base(root)
    base = a.base or declared or default
    allowed = {default} | ({declared} if declared else set())
    items = [{"kind": "branch", "name": branch, "result": "ok"}, {"kind": "base", "name": base, "result": "ok"}]
    if branch in allowed:
        items[0]["result"] = "redirect"
        emit(result(TOOL, "stopped", f"起点のブランチ {branch} にいる。作業ツリーを用意してから進める", items,
                    {"branch": branch, "base": base, "redirect": "worktree"},
                    next="/ndf:worktree の手順で作業ツリーを用意し、そこへ移る"), EXIT_PRECONDITION)
    # ミッションのブランチ宛て（課題の PR）は宣言の外でも起点として受ける
    if base not in allowed and pr_target(base) != "mission" and not a.force:
        items[1]["result"] = "redirect"
        emit(result(TOOL, "stopped", f"起点 {base} は既定・宣言のブランチと違う。cherry-pick-pr へ回す", items,
                    {"branch": branch, "base": base, "redirect": "cherry-pick-pr"},
                    next=f"/ndf:cherry-pick-pr {base}（続けるなら --force）"), EXIT_PRECONDITION)
    status = [l for l in git(root, "status", "--short").stdout.splitlines() if l.strip()]
    ref = base_ref(root, base)
    nums = diff_numbers(root, ref)
    in_commits = []
    for line in git(root, "log", "--format=%H %B%x00", f"{ref}..HEAD").stdout.split("\x00"):
        line = line.strip()
        if line and closing_words(line):
            in_commits.append(line.split()[0][:12])
    pr = existing_pr(root, branch)
    in_msg = closing_words(a.message or "")
    items.append({"kind": "existing_pr", "name": pr["url"] if pr else "無し",
                  "result": "update" if pr else "create"})
    items.append({"kind": "changes", "name": f"{len(status)} 件の未コミット", "result": "uncommitted" if status else "clean",
                  "files": status[:50]})
    for sha in in_commits:
        items.append({"kind": "commit", "name": sha, "result": "closing_word"})
    metrics = {"branch": branch, "base": base, "base_ref": ref, "default_branch": default, "draft": bool(a.draft),
               "target": pr_target(base), "review": needs_review(base),
               "existing_pr": pr, "uncommitted": len(status), "closing_words_in_message": in_msg,
               "commits_with_closing_words": in_commits, **nums}
    if in_msg:
        emit(result(TOOL, "stopped", f"コミットメッセージに閉じる語がある: {', '.join(in_msg)}（本文だけに書く）",
                    items, metrics, next="閉じる語を外したメッセージで plan をやり直す"))
    action = "既存の PR を更新する" if pr else "新しい PR を作る"
    if not needs_review(base):
        action += "（ミッションのブランチ宛て。実装レビューはミッションの PR で通す）"
    emit(result(TOOL, "ok", f"{branch} → {base}: 未コミット {len(status)} 件・{nums['commits']} コミット・"
                f"{nums['files']} ファイル。{action}", items, metrics))


# --- commit / push ------------------------------------------------------------------

def read_message(a):
    if getattr(a, "message_file", None):
        return Path(a.message_file).read_text(encoding="utf-8").rstrip("\n")
    if a.message:
        return a.message
    raise StepError("--message か --message-file が要る", EXIT_UNREADABLE)


def cmd_commit(a):
    root = git_root(a.root)
    branch = current_branch(root)
    msg = read_message(a)
    words = closing_words(msg)
    if words:
        emit(result(TOOL, "stopped", f"コミットメッセージに閉じる語がある: {', '.join(words)}（本文だけに書く）",
                    [{"kind": "message", "name": msg.splitlines()[0][:80], "result": "closing_word"}],
                    {"branch": branch, "closing_words": words}))
    git(root, "add", "-A")
    if not git(root, "status", "--porcelain").stdout.strip():
        emit(result(TOOL, "ok", "コミットする変更が無い", [], {"branch": branch, "committed": False}))
    run(["git", "-C", str(root), "commit", "-q", "-m", msg])
    sha = git(root, "rev-parse", "HEAD").stdout.strip()
    emit(result(TOOL, "ok", f"コミットした: {sha[:12]} {msg.splitlines()[0][:80]}",
                [{"kind": "commit", "name": sha[:12], "result": "committed"}],
                {"branch": branch, "committed": True, "sha": sha}))


def cmd_push(a):
    root = git_root(a.root)
    branch = current_branch(root)
    p = git(root, "push", "-q", "-u", "origin", "HEAD", check=False)
    retried = False
    if p.returncode != 0:
        retried = True
        p = run(["git", "-C", str(root), *CREDENTIAL_FALLBACK, "push", "-q", "-u", "origin", "HEAD"], check=False)
    metrics = {"branch": branch, "retried_with_credential_fallback": retried}
    if p.returncode != 0:
        emit(result(TOOL, "stopped", f"push が失敗: {p.stderr.strip()[:300]}",
                    [{"kind": "push", "name": branch, "result": "failed"}], metrics))
    emit(result(TOOL, "ok", f"origin/{branch} へ push した" + ("（credential の退避で再試行）" if retried else ""),
                [{"kind": "push", "name": branch, "result": "pushed"}], metrics))


# --- create / update ----------------------------------------------------------------

def body_with_mark(path):
    body = Path(path).read_text(encoding="utf-8")
    if REVIEW_MARK not in body:
        body = body.rstrip("\n") + f"\n\n{REVIEW_MARK}\n"
    return body


def decisions_sync(root, number):
    script = SCRIPTS / "pr-body-decisions.sh"
    if not script.is_file():
        return None
    return run(["bash", str(script), "sync", str(number)], cwd=root, check=False).returncode


def rest_create(root, branch, base, title, body, draft):
    owner, name = repo_owner_name(root)
    payload = {"title": title, "head": branch, "base": base, "body": body, "draft": bool(draft)}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        tmp = f.name
    p = run(["gh", "api", f"repos/{owner}/{name}/pulls", "--input", tmp], cwd=root, check=False)
    if p.returncode != 0:
        raise StepError(f"REST でも作成が失敗: {p.stderr.strip()[:300]}")
    d = json.loads(p.stdout)
    return d["number"], d["html_url"]


def upsert(a, must_exist):
    root = git_root(a.root)
    branch = current_branch(root)
    body = with_mode_line(body_with_mark(a.body_file), getattr(a, "mode", None), split_stages(getattr(a, "stages", None)))
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(body)
        body_path = f.name
    pr = None
    if getattr(a, "pr", None):
        pr = {"number": a.pr, "url": f"#{a.pr}"}
    else:
        pr = existing_pr(root, branch)
    if pr:
        args = ["gh", "pr", "edit", str(pr["number"]), "--body-file", body_path]
        if a.title:
            args += ["--title", a.title]
        p = run(args, cwd=root, check=False)
        if p.returncode != 0:
            raise StepError(f"gh pr edit {pr['number']} が失敗: {p.stderr.strip()[:300]}")
        number, url, action = pr["number"], pr["url"], "updated"
    else:
        if must_exist:
            raise StepError(f"{branch} の OPEN の PR が無い（create を使う）", EXIT_PRECONDITION)
        if not a.title:
            raise StepError("--title が要る", EXIT_UNREADABLE)
        base = a.base or declared_base(root) or default_branch(root)
        args = ["gh", "pr", "create", "--base", base, "--title", a.title, "--body-file", body_path]
        if a.draft:
            args.append("--draft")
        p = run(args, cwd=root, check=False)
        rest = False
        if p.returncode == 0:
            url = (p.stdout.strip().splitlines() or [""])[-1]
            m = re.search(r"/pull/(\d+)", url)
            number = int(m.group(1)) if m else None
        elif is_graphql_limit(p.stderr):
            rest = True
            number, url = rest_create(root, branch, base, a.title, body, a.draft)
        else:
            raise StepError(f"gh pr create が失敗: {p.stderr.strip()[:300]}")
        action = "created_rest" if rest else "created"
    sync_exit = decisions_sync(root, number) if number else None
    metrics = {"number": number, "url": url, "action": action, "branch": branch, "draft": bool(getattr(a, "draft", False)),
               "closing_issues_in_body": closing_issues(body), "decisions_sync_exit": sync_exit}
    items = [{"kind": "pr", "name": url, "result": action}]
    if sync_exit is not None:
        items.append({"kind": "decisions", "name": "pr-body-decisions.sh sync", "result": f"exit={sync_exit}"})
    nxt = None
    if not branch.startswith("design/"):
        has = has_user_changes(body)
        metrics["user_changes"] = has
        items.append({"kind": "section", "name": CHANGES_HEADING, "result": "ok" if has else "missing"})
        if not has:
            nxt = f"本文に {CHANGES_HEADING} の節を足して update する（無いと配布の説明文が題名になる）"
    emit(result(TOOL, "ok", f"PR #{number} を{'更新した' if action == 'updated' else '作った'}: {url}", items, metrics,
                next=nxt))


def has_user_changes(body):
    return any(l.strip() == CHANGES_HEADING for l in (body or "").splitlines())


def cmd_template(a):
    out = Path(a.out)
    if out.exists() and not a.force:
        raise StepError(f"{out} が既にある（書き直すなら --force）", EXIT_PRECONDITION)
    out.write_text(BODY_TEMPLATE, encoding="utf-8")
    emit(result(TOOL, "ok", f"PR 本文の雛形を書いた: {out}", [{"kind": "file", "name": str(out), "result": "written"}],
                {"sections": [l for l in BODY_TEMPLATE.splitlines() if l.startswith("## ")]}))


def cmd_create(a):
    upsert(a, must_exist=False)


def cmd_update(a):
    upsert(a, must_exist=True)


# --- report -----------------------------------------------------------------------

def pr_view(root, number):
    fields = "number,title,url,isDraft,baseRefName,headRefName,body"
    p = run(["gh", "pr", "view", str(number), "--json", fields], cwd=root, check=False)
    if p.returncode == 0:
        try:
            return json.loads(p.stdout)
        except ValueError:
            raise StepError("gh pr view の出力を読めない", EXIT_UNREADABLE)
    if not is_graphql_limit(p.stderr):
        raise StepError(f"gh pr view {number} が失敗: {p.stderr.strip()[:300]}")
    owner, name = repo_owner_name(root)
    q = run(["gh", "api", f"repos/{owner}/{name}/pulls/{number}"], cwd=root, check=False)
    if q.returncode != 0:
        raise StepError(f"REST でも PR を引けない: {q.stderr.strip()[:300]}")
    d = json.loads(q.stdout)
    return {"number": d["number"], "title": d["title"], "url": d["html_url"], "isDraft": d.get("draft", False),
            "baseRefName": d["base"]["ref"], "headRefName": d["head"]["ref"], "body": d.get("body") or ""}


def section(body, heading):
    lines, keep = [], False
    for line in (body or "").splitlines():
        if line.startswith("## "):
            keep = line[3:].strip().lower().startswith(heading.lower())
            continue
        if keep:
            lines.append(line)
    return lines


def cmd_report(a):
    root = git_root(a.root)
    number = a.pr
    if not number:
        pr = existing_pr(root, current_branch(root))
        if not pr:
            raise StepError("報告する PR が無い（番号を渡す）", EXIT_PRECONDITION)
        number = pr["number"]
    v = pr_view(root, number)
    ref = base_ref(root, v["baseRefName"])
    nums = diff_numbers(root, ref)
    summary = next((l.strip().lstrip("- ").strip() for l in section(v["body"], "Summary")
                    if l.strip() and not l.strip().startswith("<!--")), "（Summary が無い）")
    plan_lines = section(v["body"], "Test plan")
    total = sum(1 for l in plan_lines if re.match(r"\s*- \[[ xX]\]", l))
    done = sum(1 for l in plan_lines if re.match(r"\s*- \[[xX]\]", l))
    script = SCRIPTS / "pr-body-decisions.sh"
    dec = run(["bash", str(script), "check", str(number)], cwd=root, check=False).returncode if script.is_file() else None
    dec_text = f"exit={dec}" + ("（本文の決めたことを確かめられていない）" if dec == 2 else "") if dec is not None else "チェックなし"
    text = "\n".join([
        f"PR #{v['number']} {v['title']}", "",
        f"- ベース / ソース: {v['baseRefName']} ← {v['headRefName']}（ドラフト: {'あり' if v.get('isDraft') else 'なし'}）",
        f"- 変更量: {nums['commits']} コミット / {nums['files']} ファイル / +{nums['insertions']} -{nums['deletions']}",
        "- 主な変更: <1〜3 行を書く>",
        f"- PR 本文: {summary} / Test plan {total} 件（実行済み {done} 件）",
        f"- 決めたことの節: `pr-body-decisions.sh check` の {dec_text}", "",
        f"URL: {v['url']}",
    ])
    metrics = {"number": v["number"], "url": v["url"], "title": v["title"], "draft": bool(v.get("isDraft")),
               "base": v["baseRefName"], "head": v["headRefName"], "test_plan_total": total, "test_plan_done": done,
               "decisions_check_exit": dec, **nums}
    emit(result(TOOL, "ok", f"PR #{v['number']} の完了報告の材料", [{"kind": "pr", "name": v["url"], "result": "reported"}],
                metrics, next=text))


# --- 入口 --------------------------------------------------------------------------

def add_mode_args(p):
    p.add_argument("--mode", help="本文の末尾に書くモード（light / standard など）")
    p.add_argument("--stages", help="通した工程をカンマ区切りで（例: 設計,実装,構造改善,実装レビュー,完了判定）")


def build_parser():
    ap = argparse.ArgumentParser(prog="pr-steps.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="対象のリポジトリの根（既定はカレントの git の根）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", parents=[common_parser()], help="ブランチ・起点・既存 PR・変更量を集める")
    p.add_argument("--draft", action="store_true")
    p.add_argument("--base", help="起点のブランチ（既定は .ndf/worktree.json の base_branch か既定ブランチ）")
    p.add_argument("--message", help="使う予定のコミットメッセージ（閉じる語をチェックする）")
    p.add_argument("--force", action="store_true", help="起点が main 以外でも進める")
    p.set_defaults(func=cmd_plan)
    p = sub.add_parser("commit", parents=[common_parser()], help="すべての変更をコミットする")
    p.add_argument("--message")
    p.add_argument("--message-file")
    p.set_defaults(func=cmd_commit)
    p = sub.add_parser("push", parents=[common_parser()], help="push する（credential の退避を含む）")
    p.set_defaults(func=cmd_push)
    p = sub.add_parser("create", parents=[common_parser()], help="PR を作る。既にあれば本文を書き直す")
    p.add_argument("--title")
    p.add_argument("--body-file", required=True)
    p.add_argument("--draft", action="store_true")
    p.add_argument("--base")
    add_mode_args(p)
    p.set_defaults(func=cmd_create)
    p = sub.add_parser("update", parents=[common_parser()], help="既存の PR の本文を書き直す")
    p.add_argument("--title")
    p.add_argument("--body-file", required=True)
    p.add_argument("--pr", type=int)
    add_mode_args(p)
    p.set_defaults(func=cmd_update)
    p = sub.add_parser("report", parents=[common_parser()], help="完了報告の材料を集める")
    p.add_argument("pr", nargs="?", type=int)
    p.set_defaults(func=cmd_report)
    p = sub.add_parser("template", help=f"PR 本文の雛形（Summary・{CHANGES_HEADING}・Test plan）を書き出す")
    p.add_argument("--out", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_template)
    return ap


def main(argv=None):
    return main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    sys.exit(main())
