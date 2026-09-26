#!/usr/bin/env python3
"""merged-steps.py: merged の後片付けの決まった手順。

    python3 merged-steps.py cleanup <PR番号>... [--root <dir>]
    python3 merged-steps.py merge-when-green <PR番号> [--method merge|squash|rebase]
                            [--interval 秒] [--timeout 秒] [--stale-after 秒] [--no-cleanup] [--root <dir>]

cleanup: マージ済みの PR の作業ツリーとローカルブランチを外し、主ディレクトリを取り込む。
merge-when-green: PR が draft なら `gh pr ready` で外し、CI のチェックが全部通るまで待ち
（push で先頭のコミットが変われば待ち直す）、
失敗があれば止まり、通れば `gh pr merge --admin` でマージして cleanup まで行う。
実行が終わったのにチェックが pending のまま --stale-after 秒続けば、そのジョブを 1 度だけ
`gh run rerun --job` で再実行し、再実行でも取り残されれば止まる。実行が終わりジョブに結論が
あれば、チェックの表示が pending のままでも待たずにその結論で扱う。ジョブがランナーを待つ間は、
待ち行列の件数を待ちの 1 周ごとに stderr へ 1 行出す。

    python3 merged-steps.py probe (--pr N | --head <ブランチ>...) [--act] [--root <dir>]

probe: 開いた PR のチェックを読み、強い順に failed（fix）/ stale（取り残し。--act なら再実行して remedied）/
stale_again（再実行しても取り残し）/ settled・queued・running（wait）/ passed・none（judge）の 1 つに分ける。
`metrics` に class・action・prs・queued_runs を持つ。書き込みは --act の再実行だけ。終了コードは 0 = 調べた。

結果は lib/step_result.py の形の 1 行の JSON。終了コードは 0 = ok / 10 = `git branch -D` が要る
ブランチがある（同意が要る。提示物を書く）/ 1 = 取り込み・CI・マージが失敗 / 2 = 読めない。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from step_result import (StepError, approval_present, common_parser, emit, gh_json, git,  # noqa: E402
                         git_root, main_with, repo_slug, result, run)
import gh_parts  # noqa: E402
import repo  # noqa: E402

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


def same_untracked(main_dir, pull):
    """pull を止めた未追跡のファイルが、すべて上流の内容と同じならその一覧を返す。1 つでも違えば空。"""
    if "untracked working tree files would be overwritten" not in pull.stderr:
        return []
    rels = [l.strip() for l in pull.stderr.splitlines() if l.startswith("\t")]
    for rel in rels:
        up = run(["git", "-C", main_dir, "show", f"@{{u}}:{rel}"], check=False)
        path = Path(main_dir) / rel
        if up.returncode != 0 or not path.is_file() or path.read_text(errors="replace") != up.stdout:
            return []
    return rels


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
    # root が消す作業ツリーのこともある（計画の merge のステップ）。以後は主ディレクトリから打つ
    root = main_dir
    slug = repo_slug(root)
    wt_base = Path(os.environ.get("NDF_WORKTREE_BASE") or Path(tempfile.gettempdir()) / "ndf-worktrees")

    for n in prs:
        p = gh_parts.gh(["pr", "view", str(n), "--json", "headRefName,state,mergeCommit"], cwd=root)
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
    base = repo.declared_base(main_dir) or "develop"
    cur = git(main_dir, "branch", "--show-current", check=False).stdout.strip()
    pull_err = None
    if cur != base:
        # 別のブランチへ取り込まないよう、pull はしない
        add("main_dir", main_dir, "kept", f"主ディレクトリが {base} でなく {cur or 'detached'} のため pull しない")
    else:
        pull = run(["git", "-C", main_dir, "pull", "--ff-only"], check=False)
        same = same_untracked(main_dir, pull) if pull.returncode != 0 else []
        if same:
            # 取り込む内容と同じ未追跡のファイル（手元の写し）だけが邪魔をしたときは、消して取り込み直す
            for rel in same:
                (Path(main_dir) / rel).unlink()
                add("untracked", rel, "removed", "取り込む内容と同じ")
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


RUN_JOB_RE = re.compile(r"/actions/runs/(\d+)/job/(\d+)")


def pending_check_runs(rollup):
    """pending の CheckRun のうち、detailsUrl から実行とジョブの番号が取れるものを (名前, run, job) で返す。"""
    out = []
    for c in rollup or []:
        if c.get("__typename") != "CheckRun" or (c.get("status") or "").upper() == "COMPLETED":
            continue
        m = RUN_JOB_RE.search(c.get("detailsUrl") or "")
        if m:
            out.append((c.get("name") or c.get("workflowName") or "?", m.group(1), m.group(2)))
    return out


def probe_checks(root, rollup):
    """pending の CheckRun ごとに、属する実行とジョブの状態を読む。

    返り値: (stale, queued, settled)。stale は実行が completed なのにチェックが pending で、ジョブの結論も
    無い (名前, run, job, attempt)。attempt は実行の試行の番号で、2 以上なら既に再実行している。
    settled は実行が completed でジョブに結論がある (名前, run, job, 結論) で、
    チェックの表示が更新されていないだけなので結論で扱う。queued はジョブが queued のままランナーを
    待つチェックの名前。読めない実行は飛ばす。
    """
    stale, queued, settled, runs = [], [], [], {}
    for name, run_id, job_id in pending_check_runs(rollup):
        if run_id not in runs:
            p = gh_parts.gh(["run", "view", run_id, "--json", "status,attempt,jobs"], cwd=root)
            try:
                runs[run_id] = json.loads(p.stdout) if p.returncode == 0 else None
            except ValueError:
                runs[run_id] = None
        info = runs[run_id]
        if not info:
            continue
        job = next((j for j in info.get("jobs") or [] if str(j.get("databaseId")) == job_id), None)
        if (info.get("status") or "").lower() == "completed":
            conclusion = (job or {}).get("conclusion") or ""
            if conclusion:
                settled.append((name, run_id, job_id, conclusion.lower()))
            else:
                stale.append((name, run_id, job_id, int(info.get("attempt") or 1)))
        elif job is not None and (job.get("status") or "").lower() == "queued":
            queued.append(name)
    return stale, queued, settled


def queued_run_count(root):
    """リポジトリの待ち行列（queued の実行）の件数。読めなければ None。"""
    p = gh_parts.gh(["run", "list", "--status", "queued", "--limit", "200", "--json", "databaseId"], cwd=root)
    try:
        return len(json.loads(p.stdout)) if p.returncode == 0 else None
    except ValueError:
        return None


def pr_state(root, n):
    return gh_json(root, ["pr", "view", str(n), "--json", "state,isDraft,headRefOid,statusCheckRollup,mergeStateStatus"],
                   f"gh pr view {n}")


def watch_stuck_checks(root, n, probed, a, items, stale_since, rerun_done, waits):
    """待ちの 1 周ぶん、pending のチェックが取り残されていないか・ランナー待ちかを見る。

    実行が completed なのにチェックが pending のままの状態が a.stale_after 秒続けば、そのジョブを
    1 度だけ `gh run rerun --job` で再実行する。再実行したチェックが再び取り残されたら止まる。
    ジョブが queued の間は待ち行列の件数を stderr へ 1 行出し、その件数を返す（無ければ None）。
    """
    stale, queued = probed
    now = time.monotonic()
    names = {name for name, *_ in stale}
    for k in [k for k in stale_since if k not in names]:
        del stale_since[k]
    for name, run_id, job_id, *_ in stale:
        since = stale_since.setdefault(name, now)
        if now - since < a.stale_after:
            continue
        if name in rerun_done:
            emit(result(TOOL, "stopped", f"#{n} の取り残されたチェックが再実行でも動かない: {name}",
                        items + [{"kind": "check", "name": name, "result": "stuck", "run": run_id, "job": job_id,
                                  "reason": "取り残されたチェックが再実行でも動かない"}],
                        {"waits": waits},
                        next=f"gh run view {run_id} で実行とジョブの状態を読み、手で再実行するか GitHub の障害を確かめる"))
        p = gh_parts.gh(["run", "rerun", run_id, "--job", job_id], cwd=root)
        if p.returncode != 0:
            emit(result(TOOL, "stopped", f"gh run rerun {run_id} --job {job_id} が失敗: {p.stderr.strip()[:300]}",
                        items + [{"kind": "check", "name": name, "result": "stopped", "run": run_id, "job": job_id,
                                  "reason": p.stderr.strip()[:300]}], {"waits": waits}))
        items.append({"kind": "check", "name": name, "result": "rerun", "run": run_id, "job": job_id})
        rerun_done.add(name)
        del stale_since[name]
    if not queued:
        return None
    count = queued_run_count(root)
    shown = "?" if count is None else count
    print(f"merge-when-green: CI のランナー待ち（待ち行列 {shown} 件、待ち {len(queued)} 件）",
          file=sys.stderr, flush=True)
    return count


def cmd_merge_when_green(a):
    root = git_root(a.root)
    n = a.pr
    deadline = time.monotonic() + a.timeout
    items, waits = [], 0
    green_sha = None  # pending を見ずに通った状態を 1 度見た先頭のコミット。確かめ直して通れば確定とする
    pending_sha = None  # pending を見た先頭のコミット。見た後に全部が通ればチェックは走り終えている
    empty_since = None  # rollup が空のままになった時刻（チェックが載る前か、CI の無いリポジトリか）
    last_sha = None
    stale_since, rerun_done = {}, set()  # 取り残しを見た時刻（チェックの名前ごと）/ 再実行したチェック
    queued_runs = 0
    while True:
        info = pr_state(root, n)
        state, sha = info.get("state"), info.get("headRefOid")
        if state == "MERGED":
            items.append({"kind": "pr", "name": f"#{n}", "result": "already_merged"})
            break
        if state != "OPEN":
            emit(result(TOOL, "stopped", f"#{n} が OPEN でない（{state}）",
                        [{"kind": "pr", "name": f"#{n}", "result": "stopped", "reason": f"state={state}"}]))
        if info.get("isDraft"):
            # draft のままではマージできない。ready で走り出すチェックも待つよう、待ちの前に外す
            p = gh_parts.gh(["pr", "ready", str(n)], cwd=root)
            if p.returncode != 0:
                emit(result(TOOL, "stopped", f"gh pr ready が失敗: {p.stderr.strip()[:300]}",
                            items + [{"kind": "pr", "name": f"#{n}", "result": "stopped",
                                      "reason": p.stderr.strip()[:300]}], {"waits": waits}))
            items.append({"kind": "pr", "name": f"#{n}", "result": "ready"})
        if last_sha is not None and sha != last_sha:
            # push で CI が走り直した。前のコミットで見た結果は使わない
            items.append({"kind": "restart", "name": sha or "?", "result": "rewait",
                          "reason": f"先頭のコミットが {str(last_sha)[:8]} から {str(sha)[:8]} へ変わった"})
            green_sha = pending_sha = empty_since = None
            stale_since, rerun_done = {}, set()
        last_sha = sha
        pending, failed, passed = check_states(info.get("statusCheckRollup"))
        probed = None
        if pending:
            # 実行が終わってジョブに結論があるのに表示が pending のままのチェックは、結論で扱う（待たない）
            stale, queued, settled = probe_checks(root, info.get("statusCheckRollup"))
            probed = (stale, queued)
            for name, run_id, job_id, conclusion in settled:
                if name not in pending:
                    continue
                pending.remove(name)
                (failed if conclusion.upper() in FAIL_CONCLUSIONS else passed).append(name)
                item = {"kind": "check", "name": name, "result": "settled", "run": run_id, "job": job_id,
                        "conclusion": conclusion}
                if item not in items:
                    items.append(item)
        if failed:
            emit(result(TOOL, "stopped", f"#{n} の CI が失敗: {', '.join(failed)}",
                        items + [{"kind": "check", "name": f, "result": "failed"} for f in failed],
                        {"failed": len(failed), "pending": len(pending), "passed": len(passed), "waits": waits},
                        next=f"gh pr checks {n} で失敗を読み、直して push してから打ち直す"))
        wait = a.interval
        if not pending and not passed:
            # チェックがまだ載っていない。--no-checks-after 秒を過ぎても空なら CI の無いリポジトリとみなす
            empty_since = empty_since if empty_since is not None else time.monotonic()
            if time.monotonic() - empty_since >= a.no_checks_after:
                items.append({"kind": "check", "name": "(none)", "result": "no_checks"})
                break
        elif not pending:
            if pending_sha == sha or green_sha == sha:
                items += [{"kind": "check", "name": c, "result": "passed"} for c in passed]
                break
            green_sha = sha  # pending を見ずに通っている。走り出す前のチェックを見落とさないよう、短い間隔で 1 度確かめる
            wait = a.recheck
        else:
            green_sha, pending_sha, empty_since = None, sha, None
            count = watch_stuck_checks(root, n, probed, a, items, stale_since, rerun_done, waits)
            if count is not None:
                queued_runs = count  # 最後に見た待ち行列の件数
        if time.monotonic() >= deadline:
            emit(result(TOOL, "stopped", f"#{n} の CI が {a.timeout} 秒で終わらない（待ち: {', '.join(pending)}）",
                        items + [{"kind": "check", "name": c, "result": "pending"} for c in pending],
                        {"failed": 0, "pending": len(pending), "passed": len(passed), "waits": waits,
                         "queued_runs": queued_runs},
                        next=f"打ち直す: merged-steps.py merge-when-green {n}"))
        waits += 1
        time.sleep(wait)

    if not any(i["kind"] == "pr" and i["result"] == "already_merged" for i in items):
        p = gh_parts.gh(["pr", "merge", str(n), "--admin", f"--{a.method}"], cwd=root)
        if p.returncode != 0:
            emit(result(TOOL, "stopped", f"gh pr merge --admin が失敗: {p.stderr.strip()[:300]}",
                        items + [{"kind": "pr", "name": f"#{n}", "result": "stopped", "reason": p.stderr.strip()[:300]}],
                        {"waits": waits}))
        items.append({"kind": "pr", "name": f"#{n}", "result": "merged", "method": a.method})

    if a.no_cleanup:
        emit(result(TOOL, "ok", f"#{n} をマージした（後片付けは行わない）", items,
                    {"waits": waits, "queued_runs": queued_runs}))
    status, summary, citems, metrics, path, nxt = cleanup(root, [n])
    metrics = {**metrics, "waits": waits, "queued_runs": queued_runs}
    emit(result(TOOL, status, f"#{n} をマージした。{summary}", items + citems, metrics, path, nxt))


# --- probe --------------------------------------------------------------------

# 分類は強い順。PR が 2 つ以上に当たれば上を採り、複数の PR は最も上の分類で全体を表す
PROBE_CLASSES = ("failed", "stale", "stale_again", "settled", "queued", "running", "passed", "none")
PROBE_ACTIONS = {"failed": "fix", "stale": "judge", "stale_again": "judge", "settled": "wait", "queued": "wait",
                 "running": "wait", "passed": "judge", "none": "judge"}


def probe_prs(root, a):
    """調べる開いた PR の番号。--pr はそのまま、--head は開いた PR を探す。"""
    if a.pr:
        return [str(n) for n in a.pr]
    out = []
    for head in a.head or []:
        p = gh_parts.gh(["pr", "list", "--head", head, "--state", "open", "--json", "number"], cwd=root)
        try:
            out += [str(d["number"]) for d in json.loads(p.stdout)] if p.returncode == 0 else []
        except (ValueError, KeyError, TypeError):
            continue
    return list(dict.fromkeys(out))


def probe_one(root, n, act, items):
    """1 本の PR のチェックを分類する。(分類, 手) を返し、根拠を items に足す。読めなければ None。"""
    p = gh_parts.gh(["pr", "view", n, "--json", "number,state,statusCheckRollup"], cwd=root)
    try:
        info = json.loads(p.stdout) if p.returncode == 0 else None
    except ValueError:
        info = None
    if not isinstance(info, dict) or info.get("state") != "OPEN":
        return None
    rollup = info.get("statusCheckRollup")
    pending, failed, passed = check_states(rollup)
    stale, queued, settled = probe_checks(root, rollup) if pending else ([], [], [])
    failed += [s[0] for s in settled if s[3].upper() in FAIL_CONCLUSIONS]
    first = [s for s in stale if s[3] <= 1]
    again = [s for s in stale if s[3] > 1]
    if failed:
        cls = "failed"
        items += [{"kind": "check", "pr": int(n), "name": f, "result": "failed"} for f in failed]
    elif first:
        cls = "stale"
    elif again:
        cls = "stale_again"
        items += [{"kind": "check", "pr": int(n), "name": s[0], "result": "stale_again", "run": s[1], "job": s[2],
                   "attempt": s[3]} for s in again]
    elif settled:
        cls = "settled"
        items += [{"kind": "check", "pr": int(n), "name": s[0], "result": "settled", "run": s[1], "job": s[2],
                   "conclusion": s[3]} for s in settled]
    elif queued:
        cls = "queued"
        items += [{"kind": "check", "pr": int(n), "name": q, "result": "queued"} for q in queued]
    elif pending:
        cls = "running"
        items += [{"kind": "check", "pr": int(n), "name": c, "result": "running"} for c in pending]
    else:
        cls = "passed"
    action = PROBE_ACTIONS[cls]
    if cls == "stale":
        done = True
        for name, run_id, job_id, attempt in first:
            item = {"kind": "check", "pr": int(n), "name": name, "result": "stale", "run": run_id, "job": job_id,
                    "attempt": attempt}
            if act:
                r = gh_parts.gh(["run", "rerun", run_id, "--job", job_id], cwd=root)
                item["result"] = "rerun" if r.returncode == 0 else "rerun_failed"
                if r.returncode != 0:
                    done = False
                    item["reason"] = r.stderr.strip()[:300]
            items.append(item)
        action = "remedied" if act and done else "judge"
    return cls, action


def cmd_probe(a):
    """開いた PR のチェックを読み、取り残し・ランナー待ち・失敗・実行中に分ける（一次の調査）。"""
    if not a.pr and not a.head:
        emit(result(TOOL, "stopped", "--pr か --head が要る"), 2)
    root = git_root(a.root)
    items, found = [], []
    for n in probe_prs(root, a):
        got = probe_one(root, n, a.act, items)
        if got:
            found.append((int(n), *got))
    if not found:
        cls, action = "none", "judge"
    else:
        _, cls, action = min(found, key=lambda f: PROBE_CLASSES.index(f[1]))
    queued_runs = (queued_run_count(root) or 0) if cls == "queued" else 0
    prs = [f[0] for f in found]
    names = ", ".join(i["name"] for i in items if i.get("kind") == "check") or "無し"
    summary = {
        "failed": f"失敗したチェックがある: {names}",
        "stale": ("取り残されたチェックを再実行した: " if action == "remedied" else "取り残されたチェックがある: ") + names,
        "stale_again": f"再実行したチェックが再び取り残された: {names}",
        "settled": f"実行は終わりジョブに結論がある（表示だけが pending）: {names}",
        "queued": f"ジョブがランナーを待っている（待ち行列 {queued_runs} 件）: {names}",
        "running": f"実行中のチェックがある: {names}",
        "passed": "すべてのチェックが通っている（待つ側が抜けていない）",
        "none": "開いた PR が無い・読めない",
    }[cls]
    pr_text = " ".join(f"#{n}" for n in prs)
    emit(result(TOOL, "ok", f"{pr_text} {summary}".strip(), items,
                {"class": cls, "action": action, "prs": prs, "queued_runs": queued_runs}), 0)


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
    m.add_argument("--interval", type=float, default=10.0, help="CI を読み直す間隔（秒）")
    m.add_argument("--recheck", type=float, default=5.0,
                   help="pending を見ずに通っていたとき、確かめ直すまでの間隔（秒）")
    m.add_argument("--no-checks-after", type=float, default=60.0,
                   help="rollup が空のままこの秒数を過ぎたら、CI の無いリポジトリとしてマージする")
    m.add_argument("--timeout", type=float, default=3600.0, help="CI を待つ上限（秒）")
    m.add_argument("--stale-after", type=float, default=300.0,
                   help="実行が終わったのにチェックが pending のまま続けば、ジョブを 1 度だけ再実行するまでの秒数")
    m.add_argument("--no-cleanup", action="store_true", help="マージだけ行い、後片付けをしない")
    m.set_defaults(func=cmd_merge_when_green)
    pr = sub.add_parser("probe", parents=[common_parser()],
                        help="開いた PR のチェックを分類する（遅れの一次の調査）。--act なら取り残しを再実行する")
    pr.add_argument("--pr", type=int, action="append", default=[], metavar="PR番号")
    pr.add_argument("--head", action="append", default=[], metavar="ブランチ")
    pr.add_argument("--act", action="store_true", help="取り残されたジョブを gh run rerun --job で再実行する")
    pr.set_defaults(func=cmd_probe)
    return ap


def main(argv=None):
    return main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    sys.exit(main())
