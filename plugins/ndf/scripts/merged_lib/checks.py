"""PR のチェックの読み取りと merge-when-green の待ち（`merged-steps.py` から分けた。#1142 の D7）。

チェックの状態の分類（pending・失敗・通過）、実行とジョブの結論の読み取り、取り残されたチェックの再実行、
merge-when-green の 1 回の読み直し（`GreenWatch.poll`）を持つ。待ちの間隔は
lib/waits.py の `wait_until` で回す（`GreenWatch.wait`）。
"""
from __future__ import annotations

import json
import re
import sys
import time

import gh_parts
import waits
from step_result import emit, gh_json, result

TOOL = "merged"


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


class GreenWatch:
    """merge-when-green の 1 回の読み直し（`poll`）と、その間の状態。待ちの間隔は lib/waits.py の `wait_until` が持ち、
    読んだ中身が変わらない間は伸ばす。pending を見ずに通ったときの確かめ直しだけは --recheck の間隔で眠る。"""

    def __init__(self, root, a):
        self.root, self.a, self.n = root, a, a.pr
        self.deadline = time.monotonic() + a.timeout
        self.items, self.waits, self.queued_runs = [], 0, 0
        self.green_sha = None  # pending を見ずに通った状態を 1 度見た先頭のコミット。確かめ直して通れば確定とする
        self.pending_sha = None  # pending を見た先頭のコミット。見た後に全部が通ればチェックは走り終えている
        self.empty_since = None  # rollup が空のままになった時刻（チェックが載る前か、CI の無いリポジトリか）
        self.last_sha, self.recheck = None, False
        self.stale_since, self.rerun_done = {}, set()  # 取り残しを見た時刻（チェックの名前ごと）/ 再実行したチェック

    def wait(self):
        """終わるまで読み直す。読んだ中身（先頭のコミットとチェックの状態）が変わらない間は、間隔を 1.5 倍ずつ
        --interval の 6 倍まで伸ばす。"""
        waits.wait_until(self.poll, lambda v: v[0] == "done", max_wait=float("inf"), interval=self.a.interval,
                         max_interval=self.a.interval * 6, sleep=self.sleep)

    def sleep(self, gap):
        self.waits += 1
        time.sleep(self.a.recheck if self.recheck else gap)

    def poll(self):
        """1 回読む。終われば ("done", 理由)、待つなら ("wait", 読んだ中身)。止めるときは emit で抜ける。"""
        root, a, n, items, waits = self.root, self.a, self.n, self.items, self.waits
        info = pr_state(root, n)
        state, sha = info.get("state"), info.get("headRefOid")
        self.recheck = False
        if state == "MERGED":
            items.append({"kind": "pr", "name": f"#{n}", "result": "already_merged"})
            return ("done", "merged")
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
        if self.last_sha is not None and sha != self.last_sha:
            # push で CI が走り直した。前のコミットで見た結果は使わない
            items.append({"kind": "restart", "name": sha or "?", "result": "rewait",
                          "reason": f"先頭のコミットが {str(self.last_sha)[:8]} から {str(sha)[:8]} へ変わった"})
            self.green_sha = self.pending_sha = self.empty_since = None
            self.stale_since, self.rerun_done = {}, set()
        self.last_sha = sha
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
        if not pending and not passed:
            # チェックがまだ載っていない。--no-checks-after 秒を過ぎても空なら CI の無いリポジトリとみなす
            self.empty_since = self.empty_since if self.empty_since is not None else time.monotonic()
            if time.monotonic() - self.empty_since >= a.no_checks_after:
                items.append({"kind": "check", "name": "(none)", "result": "no_checks"})
                return ("done", "no_checks")
        elif not pending:
            if sha in (self.pending_sha, self.green_sha):
                items += [{"kind": "check", "name": c, "result": "passed"} for c in passed]
                return ("done", "green")
            self.green_sha = sha  # pending を見ずに通っている。走り出す前のチェックを見落とさないよう、短い間隔で 1 度確かめる
            self.recheck = True
        else:
            self.green_sha, self.pending_sha, self.empty_since = None, sha, None
            count = watch_stuck_checks(root, n, probed, a, items, self.stale_since, self.rerun_done, waits)
            if count is not None:
                self.queued_runs = count  # 最後に見た待ち行列の件数
        if time.monotonic() >= self.deadline:
            emit(result(TOOL, "stopped", f"#{n} の CI が {a.timeout} 秒で終わらない（待ち: {', '.join(pending)}）",
                        items + [{"kind": "check", "name": c, "result": "pending"} for c in pending],
                        {"failed": 0, "pending": len(pending), "passed": len(passed), "waits": waits,
                         "queued_runs": self.queued_runs},
                        next=f"打ち直す: merged-steps.py merge-when-green {n}"))
        return ("wait", (sha, tuple(sorted(pending)), tuple(sorted(passed)), self.recheck))
