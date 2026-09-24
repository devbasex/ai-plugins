#!/usr/bin/env python3
"""merged-steps.py: merged の後片付けの決まった手順。

    python3 merged-steps.py cleanup <PR番号>... [--root <dir>]
    python3 merged-steps.py merge-when-green <PR番号> [--method merge|squash|rebase]
                            [--interval 秒] [--timeout 秒] [--no-cleanup] [--root <dir>]

cleanup: マージ済みの PR の作業ツリーとローカルブランチを外し、主ディレクトリを取り込む。
merge-when-green: CI の検査が全部通るまで待ち（push で先頭のコミットが変われば待ち直す）、
失敗があれば止まり、通れば `gh pr merge --admin` でマージして cleanup まで行う。

結果は lib/step_result.py の形の 1 行の JSON。終了コードは 0 = ok / 10 = `git branch -D` が要る
ブランチがある（同意が要る。提示物を書く）/ 1 = 取り込み・CI・マージが失敗 / 2 = 読めない。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from step_result import (StepError, approval_present, common_parser, emit, gh_json, git,  # noqa: E402
                         git_root, main_with, repo_slug, result, run)

TOOL = "merged"


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
    d = Path(git(path, "rev-parse", "--git-common-dir").stdout.strip())
    return d if d.is_absolute() else (Path(path) / d).resolve()


def evacuate(path, label):
    """未追跡・無視されたファイルを <git-common-dir>/ndf/worktree-trash/ へ移す。移した先を返す。"""
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
    trash = common_git_dir(path) / "ndf" / "worktree-trash" / f"{label.replace('/', '__')}-{stamp}"
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
        # 作業ツリーが /tmp（tmpfs）にあると、.git の下へはファイルシステムをまたぐ。
        # os.replace は EXDEV で落ちるため、コピーと削除へ切り替わる shutil.move を使う
        shutil.move(str(src), str(dst))
    return str(trash)


def remove_worktree(root, path, label):
    """作業ツリーを外す。拒否されたら退避してから --force で外す。(成否, 理由) を返す。"""
    if git(root, "worktree", "remove", path, check=False).returncode == 0:
        return True, None
    try:
        trash = evacuate(path, label)
    except (StepError, OSError) as e:
        return False, f"退避に失敗: {e}"
    p = git(root, "worktree", "remove", "--force", path, check=False)
    if p.returncode == 0:
        return True, f"退避先 {trash}"
    return False, f"worktree remove --force が失敗: {p.stderr.strip()[:300]}"


def base_branch(main_dir):
    try:
        f = Path(main_dir) / ".ndf" / "worktree.json"
        return json.loads(f.read_text(encoding="utf-8")).get("base_branch") or "develop"
    except (OSError, ValueError, AttributeError):
        return "develop"


def cleanup(root, prs):
    """後片付けを行い、(status, summary, items, metrics, presentation_path, next) を返す。"""
    items = []

    def add(kind, name, res, reason=None, **extra):
        it = {"kind": kind, "name": name, "result": res, **extra}
        if reason:
            it["reason"] = reason
        items.append(it)

    wts = list_worktrees(root)
    main_dir = wts[0]["path"] if wts else str(root)
    slug = repo_slug(root)
    wt_base = Path(os.environ.get("NDF_WORKTREE_BASE") or Path(tempfile.gettempdir()) / "ndf-worktrees")

    for n in prs:
        p = run(["gh", "pr", "view", str(n), "--json", "headRefName,state,mergeCommit"], cwd=root, check=False)
        if p.returncode != 0:
            add("pr", f"#{n}", "kept", f"gh pr view が失敗: {p.stderr.strip()[:200]}")
            continue
        try:
            info = json.loads(p.stdout)
        except ValueError:
            add("pr", f"#{n}", "kept", "gh pr view の出力を読めない")
            continue
        branch = info.get("headRefName")
        if info.get("state") != "MERGED":
            add("pr", branch or f"#{n}", "kept", f"#{n} が MERGED でない（{info.get('state')}）")
            continue

        branch_free = True
        for wt in list_worktrees(root):
            if wt["branch"] != branch:
                continue
            if wt["path"] == main_dir:
                add("worktree", wt["path"], "kept", "主ディレクトリはこのブランチを checkout しているため外さない")
                branch_free = False
                continue
            ok, why = remove_worktree(root, wt["path"], branch)
            add("worktree", wt["path"], "removed" if ok else "kept", why)
            branch_free = branch_free and ok

        if branch_free:
            if git(root, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", check=False).returncode == 0:
                sha = git(root, "rev-parse", f"refs/heads/{branch}").stdout.strip()
                d = git(root, "branch", "-d", branch, check=False)
                if d.returncode == 0:
                    add("branch", branch, "deleted", sha=sha, restore=f"git branch {branch} {sha}")
                else:
                    add("branch", branch, "stopped", f"git branch -d が拒否: {d.stderr.strip()[:300]}", sha=sha)
            else:
                # 無いローカルブランチは削除済みとして報告し、止めない（#769）
                add("branch", branch, "absent")

        if slug:
            tmp_wt = wt_base / slug / f"pr{n}"
            if tmp_wt.exists():
                listed = {str(Path(w["path"]).resolve()): w for w in list_worktrees(root)}
                w = listed.get(str(tmp_wt.resolve()))
                if w is None:
                    add("worktree", str(tmp_wt), "kept", "この repo の作業ツリーとして登録されていない")
                elif not w["detached"]:
                    add("worktree", str(tmp_wt), "kept", "detached でない")
                else:
                    ok, why = remove_worktree(root, w["path"], f"pr{n}")
                    add("worktree", w["path"], "removed" if ok else "kept", why)

    git(root, "worktree", "prune", check=False)
    base = base_branch(main_dir)
    cur = git(main_dir, "branch", "--show-current", check=False).stdout.strip()
    pull_err = None
    if cur != base:
        # 別のブランチへ取り込まないよう、pull はしない
        add("main_dir", main_dir, "kept", f"主ディレクトリが {base} でなく {cur or 'detached'} のため pull しない")
    else:
        pull = run(["git", "-C", main_dir, "pull", "--ff-only"], check=False)
        if pull.returncode != 0:
            pull_err = f"主ディレクトリの git pull --ff-only が失敗: {pull.stderr.strip()[:300]}"
            add("main_dir", main_dir, "stopped", pull_err)
        else:
            add("main_dir", main_dir, "pulled")

    count = {k: sum(1 for i in items if i["result"] == k) for k in ("removed", "deleted", "absent", "kept", "stopped")}
    metrics = {"removed_worktrees": count["removed"], "deleted_branches": count["deleted"],
               "absent_branches": count["absent"], "kept": count["kept"], "stopped": count["stopped"]}
    summary = (f"作業ツリー {count['removed']} 件を外し、ブランチ {count['deleted']} 件を消した"
               f"（残した {count['kept']} 件・止まった {count['stopped']} 件）")
    if pull_err:
        return "stopped", pull_err, items, metrics, None, None
    need_force = [i for i in items if i["kind"] == "branch" and i["result"] == "stopped"]
    if need_force:
        names = [i["name"] for i in need_force]
        path = approval_present(
            TOOL, "-".join(str(n) for n in prs), title="未マージのコミットを持つブランチの削除",
            targets=[{"url": f"{i['name']}（{i['sha'][:8]}）"} for i in need_force],
            change=f"ブランチ {len(names)} 件",
            judge=[(i["name"], i["reason"]) for i in need_force],
            consent=[f"`git branch -D {n}` で消す" for n in names],
            rollback="\n".join(f"- `git branch {i['name']} {i['sha']}`" for i in need_force))
        return "gate", summary, items, metrics, path, "同意を得たら git branch -D " + " ".join(names)
    return "ok", summary, items, metrics, None, None


def cmd_cleanup(a):
    status, summary, items, metrics, path, nxt = cleanup(git_root(a.root), a.prs)
    emit(result(TOOL, status, summary, items, metrics, path, nxt))


# --- merge-when-green ---------------------------------------------------------

FAIL_CONCLUSIONS = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}
FAIL_STATES = {"FAILURE", "ERROR"}


def check_states(rollup):
    """statusCheckRollup を (pending, failed, passed) の名前の並びに分ける。"""
    pending, failed, passed = [], [], []
    for c in rollup or []:
        if c.get("__typename") == "StatusContext" or ("state" in c and "status" not in c):
            name, st = c.get("context") or "?", (c.get("state") or "").upper()
            if st in FAIL_STATES:
                failed.append(name)
            elif st == "SUCCESS":
                passed.append(name)
            else:
                pending.append(name)
            continue
        name = c.get("name") or c.get("workflowName") or "?"
        if (c.get("status") or "").upper() != "COMPLETED":
            pending.append(name)
        elif (c.get("conclusion") or "").upper() in FAIL_CONCLUSIONS:
            failed.append(name)
        else:
            passed.append(name)  # SUCCESS / NEUTRAL / SKIPPED
    return pending, failed, passed


def pr_state(root, n):
    return gh_json(root, ["pr", "view", str(n), "--json", "state,headRefOid,statusCheckRollup,mergeStateStatus"],
                   f"gh pr view {n}")


def cmd_merge_when_green(a):
    root = git_root(a.root)
    n = a.pr
    deadline = time.monotonic() + a.timeout
    items, waits = [], 0
    green_sha = None  # 通った状態を 1 度見た先頭のコミット。2 度続けて通れば確定とする
    last_sha = None
    while True:
        info = pr_state(root, n)
        state, sha = info.get("state"), info.get("headRefOid")
        if state == "MERGED":
            items.append({"kind": "pr", "name": f"#{n}", "result": "already_merged"})
            break
        if state != "OPEN":
            emit(result(TOOL, "stopped", f"#{n} が OPEN でない（{state}）",
                        [{"kind": "pr", "name": f"#{n}", "result": "stopped", "reason": f"state={state}"}]))
        if last_sha is not None and sha != last_sha:
            # push で CI が走り直した。前のコミットで見た結果は使わない
            items.append({"kind": "restart", "name": sha or "?", "result": "rewait",
                          "reason": f"先頭のコミットが {str(last_sha)[:8]} から {str(sha)[:8]} へ変わった"})
            green_sha = None
        last_sha = sha
        pending, failed, passed = check_states(info.get("statusCheckRollup"))
        if failed:
            emit(result(TOOL, "stopped", f"#{n} の CI が失敗: {', '.join(failed)}",
                        items + [{"kind": "check", "name": f, "result": "failed"} for f in failed],
                        {"failed": len(failed), "pending": len(pending), "passed": len(passed), "waits": waits},
                        next=f"gh pr checks {n} で失敗を読み、直して push してから打ち直す"))
        if not pending:
            if green_sha == sha:
                items += [{"kind": "check", "name": c, "result": "passed"} for c in passed]
                break
            green_sha = sha  # 走り出す前の検査を見落とさないよう、もう 1 度確かめる
        else:
            green_sha = None
        if time.monotonic() >= deadline:
            emit(result(TOOL, "stopped", f"#{n} の CI が {a.timeout} 秒で終わらない（待ち: {', '.join(pending)}）",
                        items + [{"kind": "check", "name": c, "result": "pending"} for c in pending],
                        {"failed": 0, "pending": len(pending), "passed": len(passed), "waits": waits},
                        next=f"打ち直す: merged-steps.py merge-when-green {n}"))
        waits += 1
        time.sleep(a.interval)

    if not any(i["kind"] == "pr" for i in items):
        cmd = ["gh", "pr", "merge", str(n), "--admin", f"--{a.method}"]
        p = run(cmd, cwd=root, check=False)
        if p.returncode != 0:
            emit(result(TOOL, "stopped", f"gh pr merge --admin が失敗: {p.stderr.strip()[:300]}",
                        items + [{"kind": "pr", "name": f"#{n}", "result": "stopped", "reason": p.stderr.strip()[:300]}],
                        {"waits": waits}))
        items.append({"kind": "pr", "name": f"#{n}", "result": "merged", "method": a.method})

    if a.no_cleanup:
        emit(result(TOOL, "ok", f"#{n} をマージした（後片付けは行わない）", items, {"waits": waits}))
    status, summary, citems, metrics, path, nxt = cleanup(root, [n])
    metrics = {**metrics, "waits": waits}
    emit(result(TOOL, status, f"#{n} をマージした。{summary}", items + citems, metrics, path, nxt))


def build_parser():
    ap = argparse.ArgumentParser(prog="merged-steps.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="対象のリポジトリの根（既定はカレントの git の根）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("cleanup", parents=[common_parser()], help="マージ済みの PR の作業ツリーとローカルブランチを片付ける")
    p.add_argument("prs", nargs="+", type=int, metavar="PR番号")
    p.set_defaults(func=cmd_cleanup)
    m = sub.add_parser("merge-when-green", parents=[common_parser()],
                       help="CI が通るまで待ち、--admin でマージして後片付けまで行う")
    m.add_argument("pr", type=int, metavar="PR番号")
    m.add_argument("--method", choices=("merge", "squash", "rebase"), default="merge")
    m.add_argument("--interval", type=float, default=30.0, help="CI を読み直す間隔（秒）")
    m.add_argument("--timeout", type=float, default=3600.0, help="CI を待つ上限（秒）")
    m.add_argument("--no-cleanup", action="store_true", help="マージだけ行い、後片付けをしない")
    m.set_defaults(func=cmd_merge_when_green)
    return ap


def main(argv=None):
    return main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    sys.exit(main())
