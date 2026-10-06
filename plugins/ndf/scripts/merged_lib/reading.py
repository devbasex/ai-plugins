"""PR のチェックの読み（#1645）。merge-when-green・probe・release（merge-when-green 経由）が同じ数え方を使う。

同じチェック（CheckRun は workflow の名前と名前の組、StatusContext は context）を束ね、最新の項目だけで結論を
決める。新しさは lib/gh_checks.py の `newness`（test-run.py の畳み方と同じ規則）。古い項目の失敗は「置き換わった
失敗」として分ける。取り消しの項目にだけ REST のジョブの照会を打ち、Runner が付かなかった取り消し（ステップ 0 件・
Runner の名前が空）を中身の失敗と分ける。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import gh_checks
import gh_parts

FAIL_CONCLUSIONS = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}
FAIL_STATES = {"FAILURE", "ERROR"}
RUN_JOB_RE = re.compile(r"/actions/runs/(\d+)/job/(\d+)")
UNREADABLE_JOB = "取り消しのジョブを照会できない"


@dataclass
class CheckItem:
    """statusCheckRollup の 1 件。`key` は同じチェックの鍵（CheckRun は workflow の名前と名前の組、StatusContext は
    context）、`label` は `<workflowName> / <name>`（workflow の名前が無ければ名前）。`state` は
    pending / failed / passed / cancelled。`run` と `job` は detailsUrl から読む（無ければ None）。"""

    key: tuple
    label: str
    state: str
    run: str | None = None
    job: str | None = None
    started: str | None = None
    completed: str | None = None
    reason: str | None = None

    def item(self, result_word, **extra) -> dict:
        """結果の items の 1 件。"""
        d = {"kind": "check", "name": self.label, "result": result_word}
        d.update({k: v for k, v in (("run", self.run), ("job", self.job), ("reason", self.reason)) if v})
        d.update(extra)
        return d

    def shown(self) -> str:
        """summary に書く形（run の番号があれば添える）。"""
        return f"{self.label}（run {self.run}）" if self.run else self.label


def _conclusion_state(conclusion: str) -> str:
    c = (conclusion or "").upper()
    if c == "CANCELLED":
        return "cancelled"
    return "failed" if c in FAIL_CONCLUSIONS else "passed"  # SUCCESS / NEUTRAL / SKIPPED は通過


def to_item(c: dict) -> CheckItem:
    """rollup の 1 件を CheckItem にする。"""
    if c.get("__typename") == "StatusContext" or ("state" in c and "status" not in c):
        name, st = c.get("context") or "?", (c.get("state") or "").upper()
        state = "failed" if st in FAIL_STATES else "passed" if st == "SUCCESS" else "pending"
        return CheckItem(("context", name), name, state, started=c.get("startedAt") or c.get("createdAt"))
    name = c.get("name") or c.get("workflowName") or "?"
    workflow = c.get("workflowName") or ""
    label = f"{workflow} / {name}" if workflow and c.get("name") else name
    if (c.get("status") or "").upper() != "COMPLETED":
        state = "pending"
    else:
        state = _conclusion_state(c.get("conclusion") or "")
    m = RUN_JOB_RE.search(c.get("detailsUrl") or "")
    run, job = (m.group(1), m.group(2)) if m else (None, None)
    return CheckItem(("run", workflow, name), label, state, run, job, c.get("startedAt"), c.get("completedAt"))


def fold_rollup(rollup):
    """rollup を同じチェックごとの最新の項目へ束ねる（純粋）。(最新の並び, 置き換わった失敗の並び) を返す。
    新しさは lib/gh_checks.py の `newness`（test-run.py の畳み方と同じ規則）。"""
    items = [to_item(c) for c in rollup or [] if isinstance(c, dict)]
    latest, older = gh_checks.fold_latest(items, lambda i: i.key, lambda i: (i.completed, i.started, i.job))
    return latest, [i for i in older if i.state in ("failed", "cancelled")]


@dataclass
class CheckReading:
    """1 回の読み直しの結果。`failed` は中身の失敗（ステップのある取り消しと照会できない取り消しを含む）、
    `infra` は Runner が付かなかった取り消し、`superseded` は置き換わった失敗。`stale` / `queued` / `settled` は
    `probe_checks` の 3 つ（pending の項目だけを見る）。"""

    pending: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    passed: list = field(default_factory=list)
    infra: list = field(default_factory=list)
    superseded: list = field(default_factory=list)
    stale: list = field(default_factory=list)
    queued: list = field(default_factory=list)
    settled: list = field(default_factory=list)


def runner_never_came(root, item: CheckItem) -> bool:
    """取り消しのジョブを REST で照会し、ステップが 0 件で Runner の名前が空なら真（Runner が付かなかった取り消し）。
    照会できなければ item.reason に書いて偽を返す（基盤待ちへ倒さない）。"""
    job = None
    if item.job:
        p = gh_parts.gh(["api", f"repos/{{owner}}/{{repo}}/actions/jobs/{item.job}"], cwd=root)
        try:
            job = json.loads(p.stdout) if p.returncode == 0 else None
        except ValueError:
            job = None
    if not isinstance(job, dict):
        item.reason = UNREADABLE_JOB
        return False
    return not (job.get("steps") or []) and not (job.get("runner_name") or "")


def read_checks(root, rollup) -> CheckReading:
    """rollup を束ねて最新の項目で分ける。pending の項目は実行とジョブを読み（`probe_checks`）、実行が終わって
    結論のあるものは結論で扱う。取り消しの項目にだけジョブの照会を打つ（取り消しが無ければ gh を足さない）。"""
    latest, superseded = fold_rollup(rollup)
    r = CheckReading(superseded=superseded)
    pending = [i for i in latest if i.state == "pending"]
    if pending:
        r.stale, r.queued, r.settled = probe_checks(root, pending)
        done = {(run, job): conclusion for _label, run, job, conclusion in r.settled}
        for i in pending:
            if (i.run, i.job) in done:
                i.state = _conclusion_state(done[(i.run, i.job)])
    for i in latest:
        if i.state == "cancelled":
            (r.infra if runner_never_came(root, i) else r.failed).append(i)
        else:
            {"pending": r.pending, "failed": r.failed, "passed": r.passed}[i.state].append(i)
    return r


def probe_checks(root, pending):
    """pending の CheckItem ごとに、属する実行とジョブの状態を読む。

    返り値: (stale, queued, settled)。stale は実行が completed なのにチェックが pending で、ジョブの結論も
    無い (ラベル, run, job, attempt)。attempt は実行の試行の番号で、2 以上なら既に再実行している。
    settled は実行が completed でジョブに結論がある (ラベル, run, job, 結論) で、
    チェックの表示が更新されていないだけなので結論で扱う。queued はジョブが queued のままランナーを
    待つチェックのラベル。実行とジョブの番号が無い項目・読めない実行は飛ばす。
    """
    stale, queued, settled, runs = [], [], [], {}
    for item in pending:
        name, run_id, job_id = item.label, item.run, item.job
        if not run_id or not job_id:
            continue
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
