#!/usr/bin/env python3
"""phase-steps.py: supervise.py の run の段から呼ぶ、決まった手順の段（#827）。

各サブコマンドは最後に 1 行の JSON を標準出力へ出す（{"status":"ok|fail", ...}）。
失敗は終了コード 1、引数の誤りは 2。標準ライブラリだけで書く。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CO_AUTHOR = "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"


class StepError(Exception):
    """段の失敗。JSON の reason へ入れて終了コード 1 で終える。"""


def run(cmd, cwd=None, check=True, env=None):
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise StepError(f"{' '.join(map(str, cmd))} が終了コード {p.returncode}: {p.stderr.strip()[:500]}")
    return p


def git(root, *args, check=True):
    return run(["git", "-C", str(root), *args], check=check)


def git_root(arg):
    if arg:
        return Path(arg).resolve()
    p = run(["git", "rev-parse", "--show-toplevel"], check=False)
    if p.returncode != 0:
        raise StepError("カレントが git の作業ツリーではない（--root を渡す）")
    return Path(p.stdout.strip())


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def commit(root, subject):
    msg = f"{subject}\n\n{CO_AUTHOR}\n"
    git(root, "commit", "-q", "-m", msg)
    return git(root, "rev-parse", "HEAD").stdout.strip()


# --- 作業ツリー ------------------------------------------------------------

def list_worktrees(root):
    """git worktree list --porcelain を [{path, branch, detached}] にする。先頭が主ディレクトリ。"""
    out = git(root, "worktree", "list", "--porcelain").stdout
    items, cur = [], None
    for line in out.splitlines():
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree "):], "branch": None, "detached": False}
            items.append(cur)
        elif cur is None:
            continue
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            cur["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "detached":
            cur["detached"] = True
    return items


def common_git_dir(path):
    p = git(path, "rev-parse", "--git-common-dir").stdout.strip()
    d = Path(p)
    if not d.is_absolute():
        d = (Path(path) / d).resolve()
    return d


def evacuate(path, label):
    """未追跡・無視されたファイルを <git-common-dir>/ndf/worktree-trash/ へ移す。移した先を返す。"""
    common = common_git_dir(path)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
    trash = common / "ndf" / "worktree-trash" / f"{label.replace('/', '__')}-{stamp}"
    trash.mkdir(parents=True, exist_ok=True)
    out = git(path, "status", "--ignored", "--untracked-files=all", "--porcelain=v1", "-z").stdout
    for ent in out.split("\0"):
        if ent[:3] not in ("?? ", "!! "):
            continue
        rel = ent[3:].rstrip("/")
        if not rel:
            continue
        src, dst = Path(path) / rel, trash / rel
        if not os.path.lexists(src):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
    return str(trash)


def remove_worktree(root, path, label):
    """作業ツリーを外す。拒否されたら退避してから --force で外す。(成否, 理由) を返す。"""
    p = git(root, "worktree", "remove", path, check=False)
    if p.returncode == 0:
        return True, None
    try:
        trash = evacuate(path, label)
    except (StepError, OSError) as e:
        return False, f"退避に失敗: {e}"
    p = git(root, "worktree", "remove", "--force", path, check=False)
    if p.returncode == 0:
        return True, f"退避先 {trash}"
    return False, f"worktree remove --force が失敗: {p.stderr.strip()[:300]}"


def repo_slug(root):
    p = run(["gh", "repo", "view", "--json", "owner,name"], cwd=root, check=False)
    if p.returncode == 0:
        try:
            d = json.loads(p.stdout)
            return f"{d['owner']['login']}--{d['name']}"
        except (ValueError, KeyError, TypeError):
            pass
    url = git(root, "remote", "get-url", "origin", check=False).stdout.strip()
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", url)
    return f"{m.group(1)}--{m.group(2)}" if m else None


def base_branch(main_dir):
    f = Path(main_dir) / ".ndf" / "worktree.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("base_branch") or "develop"
    except (OSError, ValueError, AttributeError):
        return "develop"


# --- cleanup ---------------------------------------------------------------

def cmd_cleanup(a):
    root = git_root(a.root)
    removed, deleted, kept = [], [], []
    wts = list_worktrees(root)
    main_dir = wts[0]["path"] if wts else str(root)
    slug = repo_slug(root)
    wt_base = Path(os.environ.get("NDF_WORKTREE_BASE") or Path(tempfile.gettempdir()) / "ndf-worktrees")

    for n in a.prs:
        p = run(["gh", "pr", "view", str(n), "--json", "headRefName,state,mergeCommit"], cwd=root, check=False)
        if p.returncode != 0:
            kept.append({"name": f"#{n}", "reason": f"gh pr view が失敗: {p.stderr.strip()[:200]}"})
            continue
        info = json.loads(p.stdout)
        branch = info.get("headRefName")
        if info.get("state") != "MERGED":
            kept.append({"name": branch or f"#{n}", "reason": f"#{n} が MERGED でない（{info.get('state')}）"})
            continue

        branch_free = True
        for wt in list_worktrees(root):
            if wt["branch"] != branch:
                continue
            if wt["path"] == main_dir:
                kept.append({"name": wt["path"], "reason": "主ディレクトリはこのブランチを checkout しているため外さない"})
                branch_free = False
                continue
            ok, why = remove_worktree(root, wt["path"], branch)
            if ok:
                removed.append(wt["path"])
            else:
                kept.append({"name": wt["path"], "reason": why})
                branch_free = False

        if branch_free and git(root, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False).returncode == 0:
            d = git(root, "branch", "-d", branch, check=False)
            if d.returncode == 0:
                deleted.append(branch)
            else:
                kept.append({"name": branch, "reason": f"git branch -d が拒否: {d.stderr.strip()[:300]}"})

        if slug:
            tmp_wt = wt_base / slug / f"pr{n}"
            if tmp_wt.exists():
                listed = {str(Path(w["path"]).resolve()): w for w in list_worktrees(root)}
                w = listed.get(str(tmp_wt.resolve()))
                if w is None:
                    kept.append({"name": str(tmp_wt), "reason": "この repo の作業ツリーとして登録されていない"})
                elif not w["detached"]:
                    kept.append({"name": str(tmp_wt), "reason": "detached でない"})
                else:
                    ok, why = remove_worktree(root, w["path"], f"pr{n}")
                    (removed.append(w["path"]) if ok else kept.append({"name": w["path"], "reason": why}))

    git(root, "worktree", "prune", check=False)
    base = base_branch(main_dir)
    status = "ok"
    result = {"removed_worktrees": removed, "deleted_branches": deleted, "kept": kept}
    cur = git(main_dir, "branch", "--show-current", check=False).stdout.strip()
    if cur != base:
        # 別のブランチへ取り込まないよう、pull はしない
        kept.append({"name": main_dir, "reason": f"主ディレクトリが {base} でなく {cur or 'detached'} のため pull しない"})
        pull = None
    else:
        pull = run(["git", "-C", main_dir, "pull", "--ff-only"], check=False)
    if pull is not None and pull.returncode != 0:
        status = "fail"
        result["reason"] = f"主ディレクトリの git pull --ff-only が失敗: {pull.stderr.strip()[:300]}"
    emit({"status": status, **result})
    return 0 if status == "ok" else 1


# --- spec-finalize ---------------------------------------------------------

def cmd_spec_finalize(a):
    root = git_root(a.root)
    spec = (root / a.spec).resolve() if not Path(a.spec).is_absolute() else Path(a.spec)
    if not spec.is_file():
        raise StepError(f"確定仕様のファイルが無い: {a.spec}")
    spec_rel = spec.relative_to(root).as_posix()

    for d in a.design:
        dp = Path(d)
        rel = dp.resolve().relative_to(root).as_posix() if dp.is_absolute() else dp.as_posix()
        if not (root / rel).exists():
            raise StepError(f"設計のファイルが無い: {d}")
        git(root, "rm", "-q", "--", rel)

    index = root / "docs" / "specifications" / "README.md"
    if index.is_file():
        text = index.read_text(encoding="utf-8")
        link = os.path.relpath(spec, index.parent).replace(os.sep, "/")
        if f"]({link})" not in text and f"]({spec_rel})" not in text and f"](./{link})" not in text:
            update_index(index, text, spec.name, link, a.title)
            git(root, "add", "--", index.relative_to(root).as_posix())

    git(root, "add", "--", spec_rel)
    if git(root, "diff", "--cached", "--quiet", check=False).returncode == 0:
        raise StepError("コミットする変更が無い")
    sha = commit(root, f"Docs: {spec.name} を確定仕様にする")
    emit({"status": "ok", "commit": sha})
    return 0


def update_index(index, text, name, link, title):
    """索引の表（| [..](..) | .. |）か一覧（- [..](..)）の最後の行の後へ 1 行を足す。"""
    lines = text.split("\n")
    desc = title or name
    last, kind = None, None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("|") and re.search(r"\]\([^)]+\.md\)", s):
            last, kind = i, "table"
        elif re.match(r"^[-*] \[[^\]]+\]\([^)]+\.md\)", s):
            last, kind = i, "list"
    if last is None:
        new = f"- [{name}]({link}) — {desc}"
        body = text.rstrip("\n") + "\n\n" + new + "\n"
    else:
        if kind == "table":
            new = f"| [{name}]({link}) | {desc} |"
        else:
            sep = " — " if " — " in lines[last] else (": " if ": " in lines[last] else " — ")
            bullet = lines[last].lstrip()[0]
            indent = lines[last][: len(lines[last]) - len(lines[last].lstrip())]
            new = f"{indent}{bullet} [{name}]({link}){sep}{desc}"
        lines.insert(last + 1, new)
        body = "\n".join(lines)
    index.write_text(body, encoding="utf-8")


# --- 入口 ------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(prog="phase-steps.py", description=__doc__)
    ap.add_argument("--root", help="対象のリポジトリの根（既定はカレントの git の根）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("cleanup", help="マージ済みの PR の作業ツリーとローカルブランチを片付ける")
    p.add_argument("prs", nargs="+", type=int, metavar="PR番号")
    p.set_defaults(func=cmd_cleanup)

    p = sub.add_parser("spec-finalize", help="設計を消し、確定仕様を索引へ載せてコミットする")
    p.add_argument("--spec", required=True)
    p.add_argument("--design", nargs="+", required=True)
    p.add_argument("--title")
    p.set_defaults(func=cmd_spec_finalize)

    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)  # 引数の誤りは argparse が終了コード 2 で終える
    try:
        return a.func(a)
    except StepError as e:
        emit({"status": "fail", "reason": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
