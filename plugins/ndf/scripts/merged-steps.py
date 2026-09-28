#!/usr/bin/env python3
"""merged-steps.py: merged の後片付けの決まった手順。

    python3 merged-steps.py cleanup <PR番号>... [--root <dir>]
    python3 merged-steps.py merge-gate --base <宛先> [--pr <PR番号>] [--root <dir>]
    python3 merged-steps.py merge-when-green <PR番号> [--gate-approved user|mvv] [--method merge|squash|rebase]
                            [--interval 秒] [--timeout 秒] [--stale-after 秒] [--no-cleanup] [--root <dir>]
    python3 merged-steps.py promote --head <ベースブランチ> --base <本番チャネル> [--prepare] [--gate-approved user|mvv]

cleanup: マージ済みの PR の作業ツリーとローカルブランチを外し、主ディレクトリを取り込む。
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
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import deps  # noqa: E402

deps.require("waits")
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
import delivery  # noqa: E402
import gh_parts  # noqa: E402
import repo  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from merged_lib.checks import (
    FAIL_CONCLUSIONS,
    GreenWatch,
    check_states,
    probe_checks,  # noqa: E402
    pr_state,
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
    """作業ツリーを外す。拒否されたら未追跡・無視のファイルを退避してから --force で外す。(成否, 理由) を返す。

    追跡ファイルの未コミットの変更は退避できず --force が消すため、残っていれば外さず kept にする。
    """
    if git(root, "worktree", "remove", path, check=False).returncode == 0:
        return True, None
    dirty = git(path, "status", "--porcelain=v1", "--untracked-files=no").stdout.splitlines()
    if dirty:
        names = ", ".join(line[3:] for line in dirty[:5]) + ("…" if len(dirty) > 5 else "")
        return False, f"追跡ファイルに未コミットの変更が {len(dirty)} 件ある（{names}）ため --force で外さない"
    try:
        trash = evacuate(path, label)
    except (StepError, OSError) as e:
        return False, f"退避に失敗: {e}"
    p = git(root, "worktree", "remove", "--force", path, check=False)
    if p.returncode == 0:
        return True, f"退避先 {trash}"
    return False, f"worktree remove --force が失敗: {p.stderr.strip()[:300]}"


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


def _remove_pr_tmp_worktree(root, tmp_wt, n, add):
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
        ok, why = remove_worktree(root, w["path"], f"pr{n}")
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
        _delete_branch(root, branch, add)

    if slug:
        _remove_pr_tmp_worktree(root, wt_base / slug / f"pr{n}", n, add)


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


# --- 承認ゲート 2（自動反映の本番チャネルへのマージ。#1336） ------------------------

GATE = "production-merge"
GATE_ROLLBACK = (
    "マージのコミットを revert する Pull Request を同じ宛先へマージする。本番系には revert が反映されるまで変更が残り、"
    "その間に利用者が触れた結果（データの書き込みなど）は revert で戻らない"
)


def gate_stop(n, verdict, info=None, next_cmd=None, plan_step="merge-approved"):
    """承認の無い本番系へのマージを止める結果（status: gate・終了コード 10）を出して終える。承認資料を書く。"""
    info = info or {}
    target = verdict.target or "（不明）"
    what = f"#{n}" if n else f"宛先 {target} へのマージ"
    lead = (
        f"{what} の宛先 {target} は自動反映の本番チャネルである"
        if verdict.value == delivery.PRODUCTION
        else f"{what} が本番系へ出るかを決められない（{verdict.reason}）"
    )
    change = (
        f"{info.get('changedFiles', '?')} ファイル・+{info.get('additions', '?')} / -{info.get('deletions', '?')} 行"
        if info
        else "（Pull Request を渡していないため読んでいない）"
    )
    target_row = {"url": info.get("url") or what}
    if info.get("title"):
        target_row["title"] = info["title"]
    if info.get("headRefName"):
        target_row["base_head"] = f"{target} ← {info['headRefName']}"
    path = approval_present(
        TOOL,
        f"merge-{n or target}",
        title=f"{what} のマージ（本番系への反映。承認ゲート 2）",
        targets=[target_row],
        change=change,
        judge=[("判定", verdict.value), ("理由", verdict.reason), *[(i["name"], i["result"]) for i in verdict.items], ("宛先", target)],
        consent=[f"{what} を {target} へマージし、本番系へ反映する"],
        rollback=GATE_ROLLBACK,
    )
    cmd = next_cmd or (f"merge-when-green {n} --gate-approved user" if n else "merge-when-green <PR番号> --gate-approved user")
    items = verdict.items + [{"kind": "pr", "name": what, "result": "gate", "base": target}]
    emit(
        result(
            TOOL,
            "gate",
            f"{lead}。承認ゲート 2 の承認が要る",
            items,
            {"gate": GATE, "verdict": verdict.value, "target": target},
            path,
            f"承認を得たら {cmd}（プランなら run <プラン> --from {plan_step}）",
        )
    )


def cmd_merge_gate(a):
    """宛先へのマージが承認ゲート 2 に当たるかだけを判定する（gh を呼ぶのは承認資料に --pr の中身を載せるときだけ）。"""
    root = git_root(a.root)
    verdict = delivery.judge_target(delivery.load(root), a.base)
    if verdict.stops:
        info = pr_state(root, a.pr) if a.pr else None
        gate_stop(a.pr, verdict, info)
    emit(
        result(
            TOOL,
            "ok",
            f"宛先 {a.base} へのマージは承認ゲート 2 に当たらない（{verdict.reason}）",
            verdict.items,
            {"verdict": verdict.value, "target": a.base},
        )
    )


# --- merge-when-green ---------------------------------------------------------


def cmd_merge_when_green(a, next_cmd=None, plan_step="merge-approved"):
    root = git_root(a.root)
    n = a.pr
    decl = delivery.load(root)  # 判定は保存しない。呼ぶたびに宣言から作り直す
    watch = GreenWatch(root, a)
    judged = {}

    def gate(info):
        # CI を待つ前・draft を外す前に、最初の読みの宛先で判定する（gh の呼び出しを足さない）
        verdict = delivery.judge_target(decl, info.get("baseRefName") or "")
        judged["verdict"] = verdict
        if verdict.stops and not a.gate_approved:
            gate_stop(n, verdict, info, next_cmd, plan_step)

    watch.on_open = gate
    watch.wait()
    items, waits, queued_runs = watch.items, watch.waits, watch.queued_runs
    verdict = judged.get("verdict")
    gate_metrics = {"verdict": verdict.value, "target": verdict.target} if verdict else {}

    if not any(i["kind"] == "pr" and i["result"] == "already_merged" for i in items):
        pin = ["--match-head-commit", watch.last_sha] if watch.last_sha else []  # 緑を確かめた先頭だけをマージする
        p = gh_parts.gh(["pr", "merge", str(n), "--admin", f"--{a.method}", *pin], cwd=root)
        if p.returncode != 0:
            emit(
                result(
                    TOOL,
                    "stopped",
                    f"gh pr merge --admin が失敗: {p.stderr.strip()[:300]}",
                    items + [{"kind": "pr", "name": f"#{n}", "result": "stopped", "reason": p.stderr.strip()[:300]}],
                    {"waits": waits},
                )
            )
        merged = {"kind": "pr", "name": f"#{n}", "result": "merged", "method": a.method, "head": watch.last_sha}
        if a.gate_approved:
            merged["gate_approved"] = a.gate_approved  # 誰が承認ゲート 2 を通したか（監査で辿る。#1336 の I8）
        items.append(merged)

    if a.no_cleanup:
        emit(
            result(
                TOOL,
                "ok",
                f"#{n} をマージした（後片付けは行わない）",
                items,
                {"waits": waits, "queued_runs": queued_runs, **gate_metrics},
            )
        )
    status, summary, citems, metrics, path, nxt = cleanup(root, [n])
    metrics = {**metrics, "waits": waits, "queued_runs": queued_runs, **gate_metrics}
    emit(result(TOOL, status, f"#{n} をマージした。{summary}", items + citems, metrics, path, nxt))


# --- promote（昇格の Pull Request。#1336） ------------------------------------


def _open_promote_pr(root, head, base):
    """開いた昇格の Pull Request（head → base）の番号。無ければ None。"""
    p = gh_parts.gh(["pr", "list", "--head", head, "--base", base, "--state", "open", "--json", "number"], cwd=root)
    if p.returncode != 0:
        raise StepError(f"gh pr list が失敗: {p.stderr.strip()[:300]}")
    try:
        prs = json.loads(p.stdout or "[]")
    except ValueError:
        prs = []
    return prs[0]["number"] if prs else None


def cmd_promote(a):
    """ベースブランチ（--head）から本番チャネル（--base）への昇格の Pull Request を作り（あれば使い）、承認ゲート 2 の後にマージする。
    head はベースブランチなので、マージの後に後片付けをしない（#1336 の I7）。"""
    root = git_root(a.root)
    n = _open_promote_pr(root, a.head, a.base)
    created = False
    if n is None:
        title = f"昇格: {a.head} → {a.base}"
        body = f"{a.head} の変更を本番チャネル {a.base} へ入れる（merged-steps.py promote が作った）。マージは承認ゲート 2 の後に行う。"
        p = gh_parts.gh(["pr", "create", "--head", a.head, "--base", a.base, "--title", title, "--body", body], cwd=root)
        if p.returncode != 0:
            err = (p.stderr or "").strip()
            if "No commits between" in err:
                emit(result(TOOL, "ok", f"{a.head} から {a.base} へ昇格する変更が無い", [], {"pr": None, "target": a.base}))
            raise StepError(f"gh pr create が失敗: {err[:300]}")
        n = int((p.stdout or "").strip().rstrip("/").rsplit("/", 1)[-1])
        created = True
    item = {"kind": "pr", "name": f"#{n}", "result": "created" if created else "found", "base": a.base, "head_branch": a.head}
    if a.prepare:
        info = pr_state(root, n)
        verdict = delivery.judge_target(delivery.load(root), info.get("baseRefName") or a.base)
        path = None
        if verdict.stops:
            path = approval_present(
                TOOL,
                f"promote-{n}",
                title=f"#{n} の昇格（{a.head} → {a.base}。承認ゲート 2）",
                targets=[{"url": info.get("url") or f"#{n}", "title": info.get("title"), "base_head": f"{a.base} ← {a.head}"}],
                change=f"{info.get('changedFiles', '?')} ファイル・+{info.get('additions', '?')} / -{info.get('deletions', '?')} 行",
                judge=[("判定", verdict.value), ("理由", verdict.reason), *[(i["name"], i["result"]) for i in verdict.items]],
                consent=[f"#{n} を {a.base} へマージし、本番系へ反映する"],
                rollback=GATE_ROLLBACK,
            )
        emit(
            result(
                TOOL,
                "ok",
                f"昇格の Pull Request #{n} を用意した（{a.head} → {a.base}）",
                verdict.items + [item],
                {"pr": n, "verdict": verdict.value, "target": a.base},
                path,
            )
        )
    a.pr, a.no_cleanup = n, True
    cmd_merge_when_green(a, next_cmd=f"promote --head {a.head} --base {a.base} --gate-approved user", plan_step="promote-approved")


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
        items += [
            {"kind": "check", "pr": int(n), "name": s[0], "result": "stale_again", "run": s[1], "job": s[2], "attempt": s[3]} for s in again
        ]
    elif settled:
        cls = "settled"
        items += [
            {"kind": "check", "pr": int(n), "name": s[0], "result": "settled", "run": s[1], "job": s[2], "conclusion": s[3]}
            for s in settled
        ]
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
            item = {"kind": "check", "pr": int(n), "name": name, "result": "stale", "run": run_id, "job": job_id, "attempt": attempt}
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
    emit(
        result(TOOL, "ok", f"{pr_text} {summary}".strip(), items, {"class": cls, "action": action, "prs": prs, "queued_runs": queued_runs}),
        0,
    )


def add_wait_args(m):
    """merge-when-green と promote が受ける、待ちと承認の引数。"""
    m.add_argument("--method", choices=("merge", "squash", "rebase"), default="merge")
    m.add_argument("--interval", type=float, default=10.0, help="CI を読み直す間隔（秒）")
    m.add_argument("--recheck", type=float, default=5.0, help="pending を見ずに通っていたとき、確かめ直すまでの間隔（秒）")
    m.add_argument(
        "--no-checks-after", type=float, default=60.0, help="rollup が空のままこの秒数を過ぎたら、CI の無いリポジトリとしてマージする"
    )
    m.add_argument("--timeout", type=float, default=3600.0, help="CI を待つ上限（秒）")
    m.add_argument(
        "--stale-after",
        type=float,
        default=300.0,
        help="実行が終わったのにチェックが pending のまま続けば、ジョブを 1 度だけ再実行するまでの秒数",
    )
    m.add_argument(
        "--gate-approved",
        choices=("user", "mvv"),
        help="承認ゲート 2 を通した担い手（利用者 / MVV 判定）。自動反映の本番チャネルへのマージは、これが無ければ止まる",
    )


def build_parser():
    ap = argparse.ArgumentParser(prog="merged-steps.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="対象のリポジトリの根（既定はカレントの git の根）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("cleanup", parents=[common_parser()], help="マージ済みの PR の作業ツリーとローカルブランチを片付ける")
    p.add_argument("prs", nargs="+", type=int, metavar="PR番号")
    p.set_defaults(func=cmd_cleanup)
    g = sub.add_parser(
        "merge-gate", parents=[common_parser()], help="宛先へのマージが承認ゲート 2（自動反映の本番チャネル）に当たるかを判定する"
    )
    g.add_argument("--base", required=True, help="Pull Request の宛先のブランチ")
    g.add_argument("--pr", type=int, help="承認資料に URL と差分の量を載せる Pull Request")
    g.set_defaults(func=cmd_merge_gate)
    m = sub.add_parser("merge-when-green", parents=[common_parser()], help="CI が通るまで待ち、--admin でマージして後片付けまで行う")
    m.add_argument("pr", type=int, metavar="PR番号")
    add_wait_args(m)
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
    add_wait_args(pm)
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
