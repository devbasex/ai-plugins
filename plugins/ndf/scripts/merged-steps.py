#!/usr/bin/env python3
"""merged-steps.py: merged の後片付けの決まった手順。

    python3 merged-steps.py cleanup <PR番号>... [--root <dir>]
    python3 merged-steps.py sweep-trash --ref <本番に出たコミット> [--root <dir>]
    python3 merged-steps.py merge-gate (--base <宛先> | --pr <PR番号>) [--pr <PR番号>] [--root <dir>]
    python3 merged-steps.py merge-when-green <PR番号> [--gate-approved user|mvv] [--method merge|squash|rebase]
                            [--interval 秒] [--timeout 秒] [--stale-after 秒] [--no-cleanup] [--root <dir>]
    python3 merged-steps.py promote --head <ベースブランチ> --base <本番チャネル> [--prepare] [--gate-approved user|mvv]

cleanup: マージ済みの PR の作業ツリーとローカルブランチを外し、主ディレクトリを取り込む。
`git worktree remove` が拒否した作業ツリーは、未追跡・無視されたファイルを退避してから外す。作り直せる生成物
（`.venv`・`node_modules`・`__pycache__`・`target` など）は退避せずに捨てる（merged_lib/trash.py）。
sweep-trash: 本番に出たコミット（--ref）に含まれるブランチの退避先を消す。本番リリースの後片付けが呼ぶ。
merge-when-green: PR が draft なら `gh pr ready` で外し、CI のチェックが全部通るまで待ち
（push で先頭のコミットが変われば待ち直す）、
失敗があれば止まり、通れば `gh pr merge --admin` でマージして cleanup まで行う。
最初の読みで宛先（baseRefName）を判定し、自動反映の本番チャネルか判定できない宛先なら、--gate-approved が無い限り
CI を待たずに承認ゲート 2 で止まる（status: gate・metrics.gate: production-merge。判定は lib/delivery.py。#1336）。
merge-gate: 宛先の判定だけを行う（0 = 進めてよい / 10 = 承認ゲート 2）。
promote: 昇格の Pull Request（ベースブランチ → 本番チャネル）を探すか作り、merge-when-green と同じ判定と待ちで
マージする（後片付けはしない）。--prepare は用意して承認資料を書くところで 0 で終える。
実行が終わったのにチェックが pending のまま --stale-after 秒続けば、そのジョブを 1 度だけ
`gh run rerun --job` で再実行し、再実行でも取り残されれば止まる。実行が終わりジョブに結論が
あれば、チェックの表示が pending のままでも待たずにその結論で扱う。ジョブがランナーを待つ間は、
待ち行列の件数を待ちの 1 周ごとに stderr へ 1 行出す。

    python3 merged-steps.py probe (--pr N | --head <ブランチ>...) [--act] [--root <dir>]

probe: 開いた PR のチェックを読み、強い順に failed（fix）/ stale（取り残し。--act なら再実行して remedied）/
stale_again（再実行しても取り残し）/ settled・queued・running（wait）/ passed・none（judge）の 1 つに分ける。
`metrics` に class・action・prs・queued_runs を持つ。書き込みは --act の再実行だけ。終了コードは 0 = 調べた。

結果は lib/step_result.py の形の 1 行の JSON。終了コードは 0 = ok / 10 = 承認ゲート 2（metrics.gate が
production-merge）か、`git branch -D` が要るブランチがある（同意が要る。どちらも提示物を書く）/
1 = 取り込み・CI・マージが失敗 / 2 = 読めない。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import deps  # noqa: E402

deps.require("waits", "durable")
from step_result import (
    StepError,
    approval_present,
    common_parser,
    emit,
    git,  # noqa: E402
    git_root,
    main_with,
    repo_slug,
    result,
    run,
)
import gh_parts  # noqa: E402
import repo  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from merged_lib import merge, trash  # noqa: E402
from merged_lib.checks import (
    FAIL_CONCLUSIONS,
    check_states,
    probe_checks,  # noqa: E402
    queued_run_count,
)

TOOL = "merged"


def list_worktrees(root):
    """git worktree list --porcelain を [{path, branch, detached}] にする。先頭が主ディレクトリ。"""
    out = git(root, "worktree", "list", "--porcelain").stdout
    items, cur = [], None
    for line in out.splitlines():
        if line.startswith("worktree "):
            cur = {"path": line[len("worktree ") :], "branch": None, "detached": False}
            items.append(cur)
        elif cur is None:
            continue
        elif line.startswith("branch "):
            ref = line[len("branch ") :]
            cur["branch"] = ref[len("refs/heads/") :] if ref.startswith("refs/heads/") else ref
        elif line == "detached":
            cur["detached"] = True
    return items


def remove_worktree(root, path, label, merge_commit=None):
    """作業ツリーを外す。拒否されたら未追跡・無視のファイルを退避してから --force で外す。(成否, 理由) を返す。

    追跡ファイルの未コミットの変更は退避できず --force が消すため、残っていれば外さず kept にする。
    理由は `退避先 <パス>` で始まり、作り直せる生成物を捨てたら `（捨てた: <相対パス>, …）` を続ける。
    """
    if git(root, "worktree", "remove", path, check=False).returncode == 0:
        return True, None
    dirty = git(path, "status", "--porcelain=v1", "--untracked-files=no").stdout.splitlines()
    if dirty:
        names = ", ".join(line[3:] for line in dirty[:5]) + ("…" if len(dirty) > 5 else "")
        return False, f"追跡ファイルに未コミットの変更が {len(dirty)} 件ある（{names}）ため --force で外さない"
    try:
        dest, discarded = trash.evacuate(path, label, merge_commit)
    except (StepError, OSError) as e:
        return False, f"退避に失敗: {e}"
    p = git(root, "worktree", "remove", "--force", path, check=False)
    if p.returncode != 0:
        return False, f"worktree remove --force が失敗: {p.stderr.strip()[:300]}"
    why = f"退避先 {dest}" if dest else "退避するものは無かった"
    return True, why + (f"（捨てた: {', '.join(discarded)}）" if discarded else "")


def same_untracked(main_dir, pull):
    """pull を止めた未追跡のファイルが、すべて上流とバイト列で同じ（CRLF と LF も別物）ならその一覧を返す。1 つでも違えば空。"""
    if "untracked working tree files would be overwritten" not in pull.stderr:
        return []
    rels = [l.strip() for l in pull.stderr.splitlines() if l.startswith("\t")]
    for rel in rels:
        up = subprocess.run(["git", "-C", str(main_dir), "show", f"@{{u}}:{rel}"], capture_output=True)
        if up.returncode != 0 or not (path := Path(main_dir) / rel).is_file() or path.read_bytes() != up.stdout:
            return []
    return rels


def _recorder():
    """後片付けの記録（items）と、1 件を足す add を返す。"""
    items = []

    def add(kind, name, res, reason=None, **extra):
        it = {"kind": kind, "name": name, "result": res, **extra}
        if reason:
            it["reason"] = reason
        items.append(it)

    return items, add


def _delete_branch(root, branch, add):
    """ローカルブランチを git branch -d で消し、deleted / stopped / absent を記録する。"""
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


def _remove_pr_tmp_worktree(root, tmp_wt, n, add, merge_commit=None):
    """PR の一時作業ツリー（wt_base/slug/pr<n>）を、登録済みで detached のときだけ外す。"""
    if not tmp_wt.exists():
        return
    listed = {str(Path(w["path"]).resolve()): w for w in list_worktrees(root)}
    w = listed.get(str(tmp_wt.resolve()))
    if w is None:
        add("worktree", str(tmp_wt), "kept", "この repo の作業ツリーとして登録されていない")
    elif not w["detached"]:
        add("worktree", str(tmp_wt), "kept", "detached でない")
    else:
        ok, why = remove_worktree(root, w["path"], f"pr{n}", merge_commit)
        add("worktree", w["path"], "removed" if ok else "kept", why)


def _cleanup_pr(root, main_dir, slug, wt_base, n, add):
    """PR 1 件分の作業ツリーとブランチを片付ける。"""
    p = gh_parts.gh(["pr", "view", str(n), "--json", "headRefName,state,mergeCommit"], cwd=root)
    if p.returncode != 0:
        add("pr", f"#{n}", "kept", f"gh pr view が失敗: {p.stderr.strip()[:200]}")
        return
    try:
        info = json.loads(p.stdout)
    except ValueError:
        add("pr", f"#{n}", "kept", "gh pr view の出力を読めない")
        return
    branch = info.get("headRefName")
    if info.get("state") != "MERGED":
        add("pr", branch or f"#{n}", "kept", f"#{n} が MERGED でない（{info.get('state')}）")
        return

    merge_commit = (info.get("mergeCommit") or {}).get("oid")
    branch_free = True
    for wt in list_worktrees(root):
        if wt["branch"] != branch:
            continue
        if wt["path"] == main_dir:
            add("worktree", wt["path"], "kept", "主ディレクトリはこのブランチを checkout しているため外さない")
            branch_free = False
            continue
        ok, why = remove_worktree(root, wt["path"], branch, merge_commit)
        add("worktree", wt["path"], "removed" if ok else "kept", why)
        branch_free = branch_free and ok

    if branch_free:
        _delete_branch(root, branch, add)

    if slug:
        _remove_pr_tmp_worktree(root, wt_base / slug / f"pr{n}", n, add, merge_commit)


def _update_main_dir(main_dir, add):
    """主ディレクトリが base にいれば pull --ff-only する。失敗の理由（無ければ None）を返す。"""
    base = repo.base_branch(main_dir)
    if not base:
        add(
            "main_dir", main_dir, "kept", "起点が決まらない（宣言の base_branch も origin の HEAD も main / master も無い）ため pull しない"
        )
        return None
    cur = git(main_dir, "branch", "--show-current", check=False).stdout.strip()
    if cur != base:
        # 別のブランチへ取り込まないよう、pull はしない
        add("main_dir", main_dir, "kept", f"主ディレクトリが {base} でなく {cur or 'detached'} のため pull しない")
        return None
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
        return pull_err
    add("main_dir", main_dir, "pulled")
    return None


def _cleanup_result(items, prs, pull_err):
    """記録から (status, summary, items, metrics, presentation_path, next) を組み立てる。"""
    count = {k: sum(1 for i in items if i["result"] == k) for k in ("removed", "deleted", "absent", "kept", "stopped")}
    metrics = {
        "removed_worktrees": count["removed"],
        "deleted_branches": count["deleted"],
        "absent_branches": count["absent"],
        "kept": count["kept"],
        "stopped": count["stopped"],
    }
    summary = (
        f"作業ツリー {count['removed']} 件を外し、ブランチ {count['deleted']} 件を消した"
        f"（残した {count['kept']} 件・止まった {count['stopped']} 件）"
    )
    if pull_err:
        return "stopped", pull_err, items, metrics, None, None
    need_force = [i for i in items if i["kind"] == "branch" and i["result"] == "stopped"]
    if need_force:
        names = [i["name"] for i in need_force]
        path = approval_present(
            TOOL,
            "-".join(str(n) for n in prs),
            title="未マージのコミットを持つブランチの削除",
            targets=[{"url": f"{i['name']}（{i['sha'][:8]}）"} for i in need_force],
            change=f"ブランチ {len(names)} 件",
            judge=[(i["name"], i["reason"]) for i in need_force],
            consent=[f"`git branch -D {n}` で消す" for n in names],
            rollback="\n".join(f"- `git branch {i['name']} {i['sha']}`" for i in need_force),
        )
        return "gate", summary, items, metrics, path, "同意を得たら git branch -D " + " ".join(names)
    return "ok", summary, items, metrics, None, None


def cleanup(root, prs):
    """後片付けを行い、(status, summary, items, metrics, presentation_path, next) を返す。"""
    items, add = _recorder()
    wts = list_worktrees(root)
    main_dir = wts[0]["path"] if wts else str(root)
    # root が消す作業ツリーのこともある（計画の merge のステップ）。以後は主ディレクトリから打つ
    root = main_dir
    slug = repo_slug(root)
    wt_base = Path(os.environ.get("NDF_WORKTREE_BASE") or Path(tempfile.gettempdir()) / "ndf-worktrees")

    for n in prs:
        _cleanup_pr(root, main_dir, slug, wt_base, n, add)

    git(root, "worktree", "prune", check=False)
    pull_err = _update_main_dir(main_dir, add)
    return _cleanup_result(items, prs, pull_err)


def cmd_cleanup(a):
    status, summary, items, metrics, path, nxt = cleanup(git_root(a.root), a.prs)
    emit(result(TOOL, status, summary, items, metrics, path, nxt))


def cmd_sweep_trash(a):
    root = git_root(a.root)
    if git(root, "rev-parse", "-q", "--verify", f"{a.ref}^{{commit}}", check=False).returncode != 0:
        raise StepError(f"--ref {a.ref} がコミットとして読めない", 2)
    items, metrics = trash.sweep(root, a.ref)
    summary = f"本番に出た退避先 {metrics['swept_trash']} 件を消した（残した {metrics['kept_trash'] + metrics['unledgered_trash']} 件）"
    emit(result(TOOL, "ok", summary, items, metrics))


# --- merge-gate・merge-when-green・promote（本体は merged_lib/merge.py） ----------------


def cmd_merge_gate(a):
    merge.merge_gate(a)


def cmd_merge_when_green(a):
    merge.merge_when_green(a, cleanup)


def cmd_promote(a):
    merge.promote(a, cleanup)


# --- probe --------------------------------------------------------------------

# 分類は強い順。PR が 2 つ以上に当たれば上を採り、複数の PR は最も上の分類で全体を表す
PROBE_CLASSES = ("failed", "stale", "stale_again", "settled", "queued", "running", "passed", "none")
PROBE_ACTIONS = {
    "failed": "fix",
    "stale": "judge",
    "stale_again": "judge",
    "settled": "wait",
    "queued": "wait",
    "running": "wait",
    "passed": "judge",
    "none": "judge",
}


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


def _check_item(n, name, result, **extra) -> dict:
    """probe の根拠の 1 件（PR n のチェック name）。"""
    return {"kind": "check", "pr": int(n), "name": name, "result": result, **extra}


def _classify_checks(root, n, rollup):
    """rollup のチェックを分類する。(分類, 根拠) を返す。stale の根拠は取り残された初回のチェックそのもの。"""
    pending, failed, passed = check_states(rollup)
    stale, queued, settled = probe_checks(root, rollup) if pending else ([], [], [])
    failed += [name for name, _run, _job, conclusion in settled if conclusion.upper() in FAIL_CONCLUSIONS]
    first = [s for s in stale if s[3] <= 1]  # s[3] は試行回数
    again = [(name, run, job, attempt) for name, run, job, attempt in stale if attempt > 1]
    if failed:
        return "failed", [_check_item(n, f, "failed") for f in failed]
    if first:
        return "stale", first
    if again:
        return "stale_again", [
            _check_item(n, name, "stale_again", run=run, job=job, attempt=attempt) for name, run, job, attempt in again
        ]
    if settled:
        return "settled", [
            _check_item(n, name, "settled", run=run, job=job, conclusion=conclusion) for name, run, job, conclusion in settled
        ]
    if queued:
        return "queued", [_check_item(n, q, "queued") for q in queued]
    if pending:
        return "running", [_check_item(n, c, "running") for c in pending]
    return "passed", []


def _rerun_stale(root, n, first, act):
    """取り残されたチェックを --act なら再実行する。(根拠, 手) を返す。"""
    items = []
    for name, run_id, job_id, attempt in first:
        item = _check_item(n, name, "stale", run=run_id, job=job_id, attempt=attempt)
        if act:
            r = gh_parts.gh(["run", "rerun", run_id, "--job", job_id], cwd=root)
            item["result"] = "rerun" if r.returncode == 0 else "rerun_failed"
            if r.returncode != 0:
                item["reason"] = r.stderr.strip()[:300]
        items.append(item)
    return items, "remedied" if act and all(i["result"] == "rerun" for i in items) else "judge"


def probe_one(root, n, act, items):
    """1 本の PR のチェックを分類する。(分類, 手) を返し、根拠を items に足す。読めなければ None。"""
    p = gh_parts.gh(["pr", "view", n, "--json", "number,state,statusCheckRollup"], cwd=root)
    try:
        info = json.loads(p.stdout) if p.returncode == 0 else None
    except ValueError:
        info = None
    if not isinstance(info, dict) or info.get("state") != "OPEN":
        return None
    cls, found = _classify_checks(root, n, info.get("statusCheckRollup"))
    action = PROBE_ACTIONS[cls]
    if cls == "stale":
        found, action = _rerun_stale(root, n, found, act)
    items += found
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
    emit(
        result(TOOL, "ok", f"{pr_text} {summary}".strip(), items, {"class": cls, "action": action, "prs": prs, "queued_runs": queued_runs}),
        0,
    )


def build_parser():
    ap = argparse.ArgumentParser(prog="merged-steps.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="対象のリポジトリの根（既定はカレントの git の根）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("cleanup", parents=[common_parser()], help="マージ済みの PR の作業ツリーとローカルブランチを片付ける")
    p.add_argument("prs", nargs="+", type=int, metavar="PR番号")
    p.set_defaults(func=cmd_cleanup)
    t = sub.add_parser("sweep-trash", parents=[common_parser()], help="本番に出たブランチの退避先（worktree-trash）を消す")
    t.add_argument("--ref", required=True, help="本番に出たコミット（タグ・本番チャネルのブランチ・マージコミット）")
    t.set_defaults(func=cmd_sweep_trash)
    g = sub.add_parser(
        "merge-gate", parents=[common_parser()], help="宛先へのマージが承認ゲート 2（自動反映の本番チャネル）に当たるかを判定する"
    )
    g.add_argument("--base", help="Pull Request の宛先のブランチ（省けば --pr の宛先を読む）")
    g.add_argument("--pr", type=int, help="承認資料に URL と差分の量を載せる Pull Request")
    g.set_defaults(func=cmd_merge_gate)
    m = sub.add_parser("merge-when-green", parents=[common_parser()], help="CI が通るまで待ち、--admin でマージして後片付けまで行う")
    m.add_argument("pr", type=int, metavar="PR番号")
    merge.add_wait_args(m)
    m.add_argument("--no-cleanup", action="store_true", help="マージだけ行い、後片付けをしない")
    m.set_defaults(func=cmd_merge_when_green)
    pm = sub.add_parser(
        "promote",
        parents=[common_parser()],
        help="昇格の Pull Request（ベースブランチ → 本番チャネル）を作り、承認ゲート 2 の後にマージする",
    )
    pm.add_argument("--head", required=True, help="昇格させるブランチ（ベースブランチ）")
    pm.add_argument("--base", required=True, help="本番チャネル")
    pm.add_argument("--prepare", action="store_true", help="Pull Request を用意して承認資料を書くところで終える（マージしない）")
    pm.add_argument("--out", help="--prepare の承認資料の置き場（既定は提示物の置き場）")
    merge.add_wait_args(pm)
    pm.set_defaults(func=cmd_promote)
    pr = sub.add_parser(
        "probe", parents=[common_parser()], help="開いた PR のチェックを分類する（遅れの一次の調査）。--act なら取り残しを再実行する"
    )
    pr.add_argument("--pr", type=int, action="append", default=[], metavar="PR番号")
    pr.add_argument("--head", action="append", default=[], metavar="ブランチ")
    pr.add_argument("--act", action="store_true", help="取り残されたジョブを gh run rerun --job で再実行する")
    pr.set_defaults(func=cmd_probe)
    return ap


def main(argv=None):
    return main_with(build_parser(), lambda a: TOOL, argv)


if __name__ == "__main__":
    sys.exit(main())
