"""PR のチェックの読み取りと merge-when-green の待ち（`merged-steps.py` から分けた。#1142 の D7）。

取り残されたチェックの再実行、Runner が付かずに取り消されたジョブの再実行と待ち直し（`InfraWatch`。#1645）、
merge-when-green の 1 回の読み直し（`GreenWatch.poll`）を持つ。チェックの数え方は merged_lib/reading.py の
`read_checks` にある。待ちの間隔は
lib/waits.py の `wait_until` で回す（`GreenWatch.wait`）。
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass

import gh_parts
import waits
from merged_lib.reading import CheckReading, read_checks
from step_result import EXIT_INFRA_WAIT, emit, gh_json, result

TOOL = "merged"


class InfraWatch:
    """基盤待ちの見張り（1 回の merge-when-green の間だけ生きる）。`reruns` は実行ごとの再実行の回数、
    `done_since` は実行が終わったのを見た時刻。同じ実行の再実行は `--infra-reruns` 回まで、実行が終わってから
    `--infra-gap` 秒たった後に、失敗したジョブだけを打つ（`gh run rerun <run> --failed`）。"""

    def __init__(self, a):
        self.limit = int(getattr(a, "infra_reruns", 3))
        self.gap = float(getattr(a, "infra_gap", 300.0))
        self.reruns: dict[str, int] = {}
        self.done_since: dict[str, float] = {}
        self.log: list[dict] = []  # 再実行した 1 回ごとの結果の items

    def _run_done(self, root, run_id) -> bool:
        p = gh_parts.gh(["run", "view", run_id, "--json", "status"], cwd=root)
        try:
            info = json.loads(p.stdout) if p.returncode == 0 else {}
        except ValueError:
            info = {}
        return isinstance(info, dict) and (info.get("status") or "").lower() == "completed"

    def step(self, root, reading: CheckReading, now: float) -> tuple[str, str]:
        """1 回の読み直しの分。("wait", "") で待つ、("reruns", "") は回数を使い切った、
        ("rerun_failed", "<run>: <stderr>") は再実行が失敗した。"""
        for i in [i for i in reading.infra if i.run]:
            if any(x.run == i.run for x in reading.infra[: reading.infra.index(i)]):
                continue  # 同じ実行は 1 度だけ見る
            run_id = i.run
            if not self._run_done(root, run_id):
                self.done_since.pop(run_id, None)
                continue
            if self.reruns.get(run_id, 0) >= self.limit:
                return "reruns", ""
            since = self.done_since.setdefault(run_id, now)
            if now - since < self.gap:
                continue
            p = gh_parts.gh(["run", "rerun", run_id, "--failed"], cwd=root)
            if p.returncode != 0:
                return "rerun_failed", f"{run_id}: {p.stderr.strip()[:300]}"
            self.reruns[run_id] = self.reruns.get(run_id, 0) + 1
            del self.done_since[run_id]
            self.log.append({"kind": "check", "name": i.label, "result": "infra_rerun", "run": run_id, "rerun": self.reruns[run_id]})
        return "wait", ""

    def pending_reruns(self, reading: CheckReading) -> list:
        """再実行した実行のうち、この読み直しで pending の項目（再実行したジョブが Runner を待っている）。"""
        return [i for i in reading.pending if i.run and i.run in self.reruns]


def queued_run_count(root):
    """リポジトリの待ち行列（queued の実行）の件数。読めなければ None。"""
    p = gh_parts.gh(["run", "list", "--status", "queued", "--limit", "200", "--json", "databaseId"], cwd=root)
    try:
        return len(json.loads(p.stdout)) if p.returncode == 0 else None
    except ValueError:
        return None


# 1 回の読みで、待ちに使う状態と、承認ゲート 2 の判定と承認資料に使う宛先・差分の量をまとめて取る（#1336）
PR_FIELDS = "state,isDraft,headRefOid,statusCheckRollup,mergeStateStatus,baseRefName,headRefName,url,title,additions,deletions,changedFiles"


def pr_state(root, n, extra=()):
    """PR の状態。`extra` の項目（merge-gate の `body` など）も同じ 1 回の gh pr view で読む。"""
    return gh_json(root, ["pr", "view", str(n), "--json", ",".join((PR_FIELDS, *extra))], f"gh pr view {n}")


@dataclass
class StuckWatch:
    """取り残されたチェックの監視の状態。PR 番号・結果の items・待ちの回数・取り残しを見た時刻・再実行したチェック。"""

    n: int
    items: list
    waits: int
    stale_since: dict
    rerun_done: set


def watch_stuck_checks(root, probed, a, st: StuckWatch):
    """待ちの 1 周ぶん、pending のチェックが取り残されていないか・ランナー待ちかを見る。

    実行が completed なのにチェックが pending のままの状態が a.stale_after 秒続けば、そのジョブを
    1 度だけ `gh run rerun --job` で再実行する。再実行したチェックが再び取り残されたら止まる。
    ジョブが queued の間は待ち行列の件数を stderr へ 1 行出し、その件数を返す（無ければ None）。
    """
    stale, queued = probed
    now = time.monotonic()
    names = {name for name, *_ in stale}
    for k in [k for k in st.stale_since if k not in names]:
        del st.stale_since[k]
    for name, run_id, job_id, *_ in stale:
        since = st.stale_since.setdefault(name, now)
        if now - since < a.stale_after:
            continue
        _rerun_stale_check(root, name, run_id, job_id, st)
    return _report_queued(root, queued)


def _rerun_stale_check(root, name, run_id, job_id, st: StuckWatch):
    """取り残されたチェック 1 件を 1 度だけ再実行する。再実行済み・再実行の失敗なら emit で止まる。"""
    if name in st.rerun_done:
        emit(
            result(
                TOOL,
                "stopped",
                f"#{st.n} の取り残されたチェックが再実行でも動かない: {name}",
                st.items
                + [
                    {
                        "kind": "check",
                        "name": name,
                        "result": "stuck",
                        "run": run_id,
                        "job": job_id,
                        "reason": "取り残されたチェックが再実行でも動かない",
                    }
                ],
                {"waits": st.waits},
                next=f"gh run view {run_id} で実行とジョブの状態を読み、手で再実行するか GitHub の障害を確かめる",
            )
        )
    p = gh_parts.gh(["run", "rerun", run_id, "--job", job_id], cwd=root)
    if p.returncode != 0:
        emit(
            result(
                TOOL,
                "stopped",
                f"gh run rerun {run_id} --job {job_id} が失敗: {p.stderr.strip()[:300]}",
                st.items
                + [{"kind": "check", "name": name, "result": "stopped", "run": run_id, "job": job_id, "reason": p.stderr.strip()[:300]}],
                {"waits": st.waits},
            )
        )
    st.items.append({"kind": "check", "name": name, "result": "rerun", "run": run_id, "job": job_id})
    st.rerun_done.add(name)
    del st.stale_since[name]


def _report_queued(root, queued):
    """ランナー待ちの件数を stderr へ 1 行出し、待ち行列の件数を返す（待ちが無ければ None）。"""
    if not queued:
        return None
    count = queued_run_count(root)
    shown = "?" if count is None else count
    print(f"merge-when-green: CI のランナー待ち（待ち行列 {shown} 件、待ち {len(queued)} 件）", file=sys.stderr, flush=True)
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
        self.stale_since, self.rerun_done = {}, set()  # 取り残しを見た時刻（チェックのラベルごと）/ 再実行したチェック
        self.seen_jobs = {}  # 照会できた取り消しのジョブの判定（(root, job ID) ごと）。読み直しのたびに照会し直さない
        self.infra = InfraWatch(a)  # Runner が付かなかった取り消しの再実行と待ち直し（先頭のコミットごと）
        self.on_open = None  # 開いた PR を最初に読んだとき、draft を外す前に 1 度だけ呼ぶ（承認ゲート 2 の判定。#1336）

    def wait(self):
        """終わるまで読み直す。読んだ中身（先頭のコミットとチェックの状態）が変わらない間は、間隔を 1.5 倍ずつ
        --interval の 6 倍まで伸ばす。"""
        waits.wait_until(
            self.poll,
            lambda v: v[0] == "done",
            max_wait=float("inf"),
            interval=self.a.interval,
            max_interval=self.a.interval * 6,
            sleep=self.sleep,
        )

    def sleep(self, gap):
        self.waits += 1
        time.sleep(self.a.recheck if self.recheck else gap)

    def _ensure_ready(self):
        """draft のままではマージできない。ready で走り出すチェックも待つよう、待ちの前に外す。失敗なら emit で抜ける。"""
        root, n, items = self.root, self.n, self.items
        p = gh_parts.gh(["pr", "ready", str(n)], cwd=root)
        if p.returncode != 0:
            emit(
                result(
                    TOOL,
                    "stopped",
                    f"gh pr ready が失敗: {p.stderr.strip()[:300]}",
                    items + [{"kind": "pr", "name": f"#{n}", "result": "stopped", "reason": p.stderr.strip()[:300]}],
                    {"waits": self.waits},
                )
            )
        items.append({"kind": "pr", "name": f"#{n}", "result": "ready"})

    def _reset_on_new_sha(self, sha):
        """push で CI が走り直した。前のコミットで見た結果は使わない。"""
        if self.last_sha is None or sha == self.last_sha:
            return
        self.items.append(
            {
                "kind": "restart",
                "name": sha or "?",
                "result": "rewait",
                "reason": f"先頭のコミットが {str(self.last_sha)[:8]} から {str(sha)[:8]} へ変わった",
            }
        )
        self.green_sha = self.pending_sha = self.empty_since = None
        self.stale_since, self.rerun_done = {}, set()
        self.infra = InfraWatch(self.a)

    def _note(self, reading: CheckReading):
        """置き換わった失敗と、表示が pending のまま結論の出たチェックを items に 1 度ずつ残す。"""
        new = [i.item("superseded") for i in reading.superseded]
        new += [
            {"kind": "check", "name": name, "result": "settled", "run": run_id, "job": job_id, "conclusion": conclusion}
            for name, run_id, job_id, conclusion in reading.settled
        ]
        self.items += [i for i in new if i not in self.items]

    def _stop_failed(self, reading: CheckReading):
        """中身の失敗で止まる（終了コード 1）。基盤待ちがあっても再実行しない。"""
        n = self.n
        emit(
            result(
                TOOL,
                "stopped",
                f"#{n} の CI が失敗: {', '.join(i.shown() for i in reading.failed)}",
                self.items + [i.item("failed") for i in reading.failed],
                {
                    "failed": len(reading.failed),
                    "pending": len(reading.pending),
                    "passed": len(reading.passed),
                    "infra": len(reading.infra),
                    "waits": self.waits,
                },
                next=f"gh pr checks {n} で失敗を読み、直して push してから打ち直す",
            )
        )

    def _stop_infra(self, reading: CheckReading, why: str, detail: str = ""):
        """基盤待ちで止まる（終了コード 75）。summary に「CI が失敗」の語を使わない。"""
        n, infra = self.n, self.infra
        stuck = reading.infra + [i for i in infra.pending_reruns(reading) if i not in reading.infra]
        labels = ", ".join(dict.fromkeys(i.label for i in stuck))
        tail = {
            "reruns": f"再実行 {infra.limit} 回で解けない",
            "timeout": f"{self.a.timeout:g} 秒で解けない",
            "rerun_failed": f"gh run rerun {detail.split(':', 1)[0]} --failed が失敗: {detail.split(': ', 1)[-1]}",
        }[why]
        emit(
            result(
                TOOL,
                "stopped",
                f"#{n} の CI の基盤待ち: Runner が付かずに取り消された（{labels}）。{tail}",
                self.items + [i.item("infra_wait", reruns=infra.reruns.get(i.run or "", 0)) for i in stuck],
                {
                    "failed": 0,
                    "pending": len(reading.pending),
                    "passed": len(reading.passed),
                    "infra": len(stuck),
                    "waits": self.waits,
                    "queued_runs": self.queued_runs,
                },
                next=f"GitHub Actions の復旧の後に打ち直す: merged-steps.py merge-when-green {n}",
            ),
            EXIT_INFRA_WAIT,
        )

    def _check_expected(self, sha, state):
        """--expect-head を渡したとき、読んだ先端がその SHA と違えば、待ち続けず・マージ済みでもそこで止める（#815 の I5）。
        待つ間に別の先端でマージされた PR を成功として返さない（release が承認していないマージへタグを打たない）。"""
        expect = getattr(self.a, "expect_head", None)
        if not expect or sha == expect:
            return
        n = self.n
        emit(
            result(
                TOOL,
                "stopped",
                f"#{n} の先端が {expect[:8]} でない（今は {str(sha)[:8]}・{state}）。マージしない",
                self.items + [{"kind": "pr", "name": f"#{n}", "result": "head_moved", "head": sha, "expected": expect}],
                {"waits": self.waits},
            )
        )

    def poll(self):
        """1 回読む。終われば ("done", 理由)、待つなら ("wait", 読んだ中身)。止めるときは emit で抜ける。"""
        root, a, n, items, waits = self.root, self.a, self.n, self.items, self.waits
        info = pr_state(root, n)
        state, sha = info.get("state"), info.get("headRefOid")
        self.recheck = False
        self._check_expected(sha, state)
        if state == "MERGED":
            items.append({"kind": "pr", "name": f"#{n}", "result": "already_merged"})
            return ("done", "merged")
        if state != "OPEN":
            emit(
                result(
                    TOOL,
                    "stopped",
                    f"#{n} が OPEN でない（{state}）",
                    [{"kind": "pr", "name": f"#{n}", "result": "stopped", "reason": f"state={state}"}],
                )
            )
        if self.on_open is not None:
            hook, self.on_open = self.on_open, None
            hook(info)
        if info.get("isDraft"):
            self._ensure_ready()
        self._reset_on_new_sha(sha)
        self.last_sha = sha
        reading = read_checks(root, info.get("statusCheckRollup"), self.seen_jobs)
        self._note(reading)
        if reading.failed:
            self._stop_failed(reading)
        if reading.infra:
            why, detail = self.infra.step(root, reading, time.monotonic())
            items += self.infra.log
            self.infra.log.clear()
            if why != "wait":
                self._stop_infra(reading, why, detail)
        pending = [i.label for i in reading.pending]
        passed = [i.label for i in reading.passed]
        if not pending and not passed and not reading.infra:
            # チェックがまだ載っていない。--no-checks-after 秒を過ぎても空なら CI の無いリポジトリとみなす
            self.empty_since = self.empty_since if self.empty_since is not None else time.monotonic()
            if time.monotonic() - self.empty_since >= a.no_checks_after:
                items.append({"kind": "check", "name": "(none)", "result": "no_checks"})
                return ("done", "no_checks")
        elif not pending and not reading.infra:
            if sha in (self.pending_sha, self.green_sha):
                items += [{"kind": "check", "name": c, "result": "passed"} for c in passed]
                return ("done", "green")
            self.green_sha = sha  # pending を見ずに通っている。走り出す前のチェックを見落とさないよう、短い間隔で 1 度確かめる
            self.recheck = True
        else:
            self.green_sha, self.pending_sha, self.empty_since = None, sha, None
            if pending:
                probed = (reading.stale, reading.queued)
                count = watch_stuck_checks(root, probed, a, StuckWatch(n, items, waits, self.stale_since, self.rerun_done))
                if count is not None:
                    self.queued_runs = count  # 最後に見た待ち行列の件数
        if time.monotonic() >= self.deadline:
            if reading.infra or self.infra.pending_reruns(reading):
                self._stop_infra(reading, "timeout")
            emit(
                result(
                    TOOL,
                    "stopped",
                    f"#{n} の CI が {a.timeout} 秒で終わらない（待ち: {', '.join(pending)}）",
                    items + [{"kind": "check", "name": c, "result": "pending"} for c in pending],
                    {"failed": 0, "pending": len(pending), "passed": len(passed), "waits": waits, "queued_runs": self.queued_runs},
                    next=f"打ち直す: merged-steps.py merge-when-green {n}",
                )
            )
        infra = tuple(sorted(i.label for i in reading.infra))
        return ("wait", (sha, tuple(sorted(pending)), tuple(sorted(passed)), infra, self.recheck))
