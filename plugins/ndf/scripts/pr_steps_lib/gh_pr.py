"""PR を gh で読み書きする部品（`pr-steps.py` から分けた）。上限（rate limit）なら REST へ退避する。

書く先の PR と宛先を決める `target_pr`、本文ファイルで作る・更新する `send_body`、既存の PR と report の読み取りを持つ。
出す（emit）のは呼び手である。
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import gh_parts
import repo
from step_result import EXIT_PRECONDITION, EXIT_UNREADABLE, StepError


def repo_owner_name(root):
    p = gh_parts.gh(["repo", "view", "--json", "owner,name"], cwd=root)
    if p.returncode == 0:
        try:
            d = json.loads(p.stdout)
            return d["owner"]["login"], d["name"]
        except (ValueError, KeyError, TypeError):
            pass
    slug = repo.owner_repo(root)
    if not slug:
        raise StepError("リポジトリの所有者と名前を決められない", EXIT_UNREADABLE)
    owner, _, name = slug.partition("/")
    return owner, name


def existing_pr(root, branch):
    """head が branch の OPEN の PR（{number, url, isDraft, baseRefName}）。無ければ None。上限なら REST。"""
    p = gh_parts.gh(["pr", "list", "--head", branch, "--state", "open", "--json", "number,url,isDraft,baseRefName"], cwd=root)
    if p.returncode == 0:
        try:
            prs = json.loads(p.stdout or "[]")
        except ValueError:
            raise StepError("gh pr list の出力を読めない", EXIT_UNREADABLE)
        return prs[0] if prs else None
    if not gh_parts.is_rate_limited(p.stderr):
        raise StepError(f"gh pr list が失敗: {p.stderr.strip()[:300]}", EXIT_PRECONDITION)
    owner, name = repo_owner_name(root)
    q = gh_parts.gh(["api", f"repos/{owner}/{name}/pulls?head={owner}:{branch}&state=open"], cwd=root)
    if q.returncode != 0:
        raise StepError(f"REST でも既存の PR を引けない: {q.stderr.strip()[:300]}")
    try:
        prs = json.loads(q.stdout or "[]")
    except ValueError:
        raise StepError("REST の出力を読めない", EXIT_UNREADABLE)
    if not prs:
        return None
    pr = prs[0]
    return {
        "number": pr["number"],
        "url": pr["html_url"],
        "isDraft": pr.get("draft", False),
        "baseRefName": pr.get("base", {}).get("ref", ""),
    }


def pr_for_report(root, number):
    fields = "number,title,url,isDraft,baseRefName,headRefName,body"
    p = gh_parts.gh(["pr", "view", str(number), "--json", fields], cwd=root)
    if p.returncode == 0:
        try:
            return json.loads(p.stdout)
        except ValueError:
            raise StepError("gh pr view の出力を読めない", EXIT_UNREADABLE)
    if not gh_parts.is_rate_limited(p.stderr):
        raise StepError(f"gh pr view {number} が失敗: {p.stderr.strip()[:300]}")
    owner, name = repo_owner_name(root)
    q = gh_parts.gh(["api", f"repos/{owner}/{name}/pulls/{number}"], cwd=root)
    if q.returncode != 0:
        raise StepError(f"REST でも PR を引けない: {q.stderr.strip()[:300]}")
    d = json.loads(q.stdout)
    return {
        "number": d["number"],
        "title": d["title"],
        "url": d["html_url"],
        "isDraft": d.get("draft", False),
        "baseRefName": d["base"]["ref"],
        "headRefName": d["head"]["ref"],
        "body": d.get("body") or "",
    }


def target_pr(root, branch, a, must_exist):
    """書く先の PR と宛先。(既存の PR か None, 宛先のブランチか None) を返す。

    宛先は、既存の PR なら baseRefName、新しく作るなら --base か起点（#2228 の決定 9）。"""
    if getattr(a, "pr", None):
        return {"number": a.pr, "url": f"#{a.pr}"}, pr_for_report(root, a.pr).get("baseRefName") or None
    pr = existing_pr(root, branch)
    if pr:
        return pr, pr.get("baseRefName") or None
    if must_exist:
        raise StepError(f"{branch} の OPEN の PR が無い（create を使う）", EXIT_PRECONDITION)
    if not a.title:
        raise StepError("--title が要る", EXIT_UNREADABLE)
    return None, a.base or repo.base_branch(root) or "main"


def send_body(root, branch, body, body_path, a, pr, base):
    """gh へ本文ファイルを渡して PR を作る・更新する。(番号, URL, action) を返す。"""
    if pr:
        args = ["pr", "edit", str(pr["number"]), "--body-file", body_path]
        if a.title:
            args += ["--title", a.title]
        p = gh_parts.gh(args, cwd=root)
        if p.returncode != 0:
            raise StepError(f"gh pr edit {pr['number']} が失敗: {p.stderr.strip()[:300]}")
        return pr["number"], pr["url"], "updated"
    args = ["pr", "create", "--base", base, "--title", a.title, "--body-file", body_path]
    if a.draft:
        args.append("--draft")
    p = gh_parts.gh(args, cwd=root)
    if p.returncode == 0:
        url = (p.stdout.strip().splitlines() or [""])[-1]
        m = re.search(r"/pull/(\d+)", url)
        return (int(m.group(1)) if m else None), url, "created"
    if gh_parts.is_rate_limited(p.stderr):
        number, url = rest_create(root, branch, base, a.title, body, a.draft)
        return number, url, "created_rest"
    raise StepError(f"gh pr create が失敗: {p.stderr.strip()[:300]}")


def rest_create(root, branch, base, title, body, draft):
    owner, name = repo_owner_name(root)
    payload = {"title": title, "head": branch, "base": base, "body": body, "draft": bool(draft)}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        tmp = f.name
    try:
        p = gh_parts.gh(["api", f"repos/{owner}/{name}/pulls", "--input", tmp], cwd=root)
    finally:
        Path(tmp).unlink(missing_ok=True)  # 本文を /tmp に残さない
    if p.returncode != 0:
        raise StepError(f"REST でも作成が失敗: {p.stderr.strip()[:300]}")
    d = json.loads(p.stdout)
    return d["number"], d["html_url"]
