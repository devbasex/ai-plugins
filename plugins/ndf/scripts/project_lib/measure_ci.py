"""CI と GitHub から測る項目（P3・P4・P6 の PR の宛先・P8 の GitHub の分）。標準ライブラリと `gh` だけで動く。

**`gh` へ渡すのは読む要求だけである（I5）。** `gh api` は `--method GET` を付けて打ち、artifact は一時ディレクトリへ落とす。
`gh` の 1 回の呼び出しは、締め切りまでの残りと 30 秒の小さい方で打ち切る（I10）。締め切りに届いた後は起動しない。
外部の語（job・artifact・ruleset）は、ここで宣言の語（CI の分割・所要の出所・必須のチェック）へ直す。
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import statistics
import subprocess
import time

import ci_workflows
import junit
import repo as repo_id
import run_metrics

from .measure_repo import Tree, measured, question, unknown, url_hosts

CALL_LIMIT = 30.0
TEST_STEP = re.compile(r"(?<![a-z])test|pytest|phpunit|jest|vitest|rspec", re.I)
RECORD_NAME = "cross-refactoring-allocation.jsonl"
RECORD_ROWS = 10
CANDIDATE_RUNS = 5  # ワークフローごとに代表を探す成功の run の上限（I5。前の頁は取りに行かない）


class GhUnavailable(Exception):
    """`gh` が使えない（無い・未認証・ネットワークが無い・時間切れ）。理由を持つ。"""


class Gh:
    """締め切りを持つ、読むだけの `gh api` の呼び出し。"""

    def __init__(self, root, repo: str, deadline: float):
        self.root, self.repo, self.deadline = root, repo, deadline
        self.calls = 0

    def left(self) -> float:
        return self.deadline - time.monotonic()

    def raw(self, path: str) -> bytes:
        left = self.left()
        if left <= 0:
            raise GhUnavailable("時間切れ")
        self.calls += 1
        try:
            p = subprocess.run(
                ["gh", "api", "--method", "GET", path], cwd=str(self.root), capture_output=True, timeout=min(CALL_LIMIT, left)
            )
        except FileNotFoundError:
            raise GhUnavailable("gh が無い") from None
        except subprocess.TimeoutExpired:
            raise GhUnavailable("時間切れ") from None
        except OSError as e:
            raise GhUnavailable(f"gh を起動できない: {e}") from None
        if p.returncode != 0:
            err = p.stderr.decode("utf-8", "replace").strip().splitlines()
            raise GhUnavailable(f"gh が失敗した: {(err or ['終了コード ' + str(p.returncode)])[-1][:200]}")
        return p.stdout

    def get(self, path: str):
        body = self.raw(path)
        try:
            return json.loads(body.decode("utf-8") or "null")
        except ValueError:
            raise GhUnavailable(f"gh の出力を読めない: {path}") from None


def _time(value) -> _dt.datetime | None:
    try:
        return _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _span(start, end) -> float | None:
    a, b = _time(start), _time(end)
    return round((b - a).total_seconds(), 1) if a and b else None


def workflow_jobs(tree: Tree) -> dict[str, int]:
    """ワークフローのファイル → `jobs:` の直下の job の数（YAML を読まずに字下げで拾う。`ci_workflows.job_ids`）。"""
    files = sorted(p for p in tree.files if ci_workflows.WORKFLOW_PATH.match(p))
    return {f: len(ci_workflows.job_ids(tree.read(f) or "")) for f in files}


def _junit_of_run(gh: Gh, run: dict) -> dict | None:
    """run の成果物の JUnit（`lib/junit.py` の読み取り。名前の規則は `junit.JUNIT_NAME`）の直列の合計。"""
    seconds = [s for s in (junit.total_seconds(xml) for xml in junit.artifact_xmls(gh.get, gh.raw, gh.repo, run["id"])) if s is not None]
    if not seconds:
        return None
    return {
        "seconds": round(sum(seconds), 1),
        "source": "ci-junit",
        "detail": f"run {run['id']}（{run.get('path')}）の JUnit {len(seconds)} 本の合計",
    }


def _steps_of(jobs: list[dict], run: dict) -> dict | None:
    total, n = 0.0, 0
    for job in jobs:
        for step in job.get("steps") or []:
            if TEST_STEP.search(step.get("name", "")):
                sec = _span(step.get("started_at"), step.get("completed_at"))
                if sec is not None:
                    total += sec
                    n += 1
    if not n or total <= 0:
        return None
    return {"seconds": round(total, 1), "source": "ci-steps", "detail": f"run {run['id']}（{run.get('path')}）のテストの step {n} 個の合計"}


def ndf_record(repo: str | None) -> dict | None:
    """同じ機械の NDF の実行の記録の、実行の行の `whole_test.init` の直近 10 件の中央値（決定 9）。"""
    if not repo:
        return None
    f = run_metrics.metrics_dir() / repo_id.slug(repo) / RECORD_NAME
    values = []
    try:
        lines = f.read_text(encoding="utf-8").splitlines() if f.is_file() else []
    except OSError:
        return None
    for line in lines:
        try:
            row = json.loads(line)
            # 着手前のテストの行（`kind: init_test`。cross-refactoring の #1385）は全体テストの所要に数えない
            if row.get("kind", "run") != "run":
                continue
            v = (row.get("whole_test") or {}).get("init")
        except (ValueError, AttributeError):
            continue
        if isinstance(v, (int, float)) and v > 0:
            values.append(float(v))
    values = values[-RECORD_ROWS:]
    if not values:
        return None
    return {
        "seconds": round(statistics.median(values), 1),
        "source": "ndf-record",
        "detail": f"NDF の実行の記録の直近 {len(values)} 件の中央値",
    }


def _required_checks(gh: Gh, branch: str | None) -> list[str]:
    if not branch:
        return []
    try:
        rules = gh.get(f"repos/{gh.repo}/rules/branches/{branch}") or []
    except GhUnavailable as e:
        if str(e) == "時間切れ":
            raise
        rules = []
    try:
        return ci_workflows.required_contexts(rules) or []
    except ValueError:
        return []


def _test_duration(durations: list[dict], ci_reason: str | None) -> dict:
    """測れた所要の候補があれば measured、無ければ unknown。"""
    if durations:
        return measured({"measured": durations})
    return unknown(ci_reason or "CI に JUnit もテストの step も無く、NDF の実行の記録も無い")


def _measure_merges(gh: Gh, repo: str) -> tuple[dict[str, int], list[str]]:
    """閉じた PR の宛先別のマージ数と、本文に出たホスト名。"""
    pulls = gh.get(f"repos/{repo}/pulls?state=closed&per_page=100") or []
    merges: dict[str, int] = {}
    hosts: set[str] = set()
    for pr in pulls:
        if pr.get("merged_at"):
            ref = (pr.get("base") or {}).get("ref")
            merges[ref] = merges.get(ref, 0) + 1
        hosts |= url_hosts(pr.get("body") or "")
    return merges, sorted(hosts)


def _measure_github_issues(gh: Gh, repo: str) -> int:
    issues = gh.get(f"repos/{repo}/issues?state=all&per_page=100") or []
    return sum(1 for i in issues if "pull_request" not in i)


def measure_ci(tree: Tree, repo: str | None, head: str | None, deadline: float) -> dict:
    """P3・P4 と、P6・P8 の GitHub の分を測る。返すのは `{"ci", "test_duration", "merges", "github_issues", "hosts", "notes"}`。"""
    record = ndf_record(repo)
    durations = [record] if record else []
    out = {"merges": None, "github_issues": None, "hosts": [], "notes": []}
    jobs_static = workflow_jobs(tree)
    if not repo:
        reason = "origin が GitHub のリポジトリを指していない"
        out["ci"] = unknown(reason)
        out["test_duration"] = _test_duration(durations, reason)
        return out
    gh = Gh(tree.root, repo, deadline)
    try:
        ci, found, notes = _measure_runs(gh, jobs_static, head)
        out["ci"] = measured(ci)
        durations += found
        out["notes"] += notes
    except GhUnavailable as e:
        out["ci"] = unknown(str(e))
        out["notes"].append(f"CI を測れない: {e}")
    out["test_duration"] = _test_duration(durations, out["ci"].get("reason"))
    try:
        out["merges"], out["hosts"] = _measure_merges(gh, repo)
        out["github_issues"] = _measure_github_issues(gh, repo)
    except GhUnavailable as e:
        out["notes"].append(f"PR の宛先と GitHub Issues を測れない: {e}")
    return out


def _has_skipped_job(jobs: list[dict]) -> bool:
    """ジョブを飛ばした run か（conclusion が `skipped` のジョブがある）。名前・イベントでは判定しない（I2）。"""
    return any(job.get("conclusion") == "skipped" for job in jobs)


def _representative(gh: Gh, candidates: list[dict]) -> tuple[dict | None, list[dict], int]:
    """新しい順の候補を最大 `CANDIDATE_RUNS` 件見て、ジョブを飛ばしていない最初の run とそのジョブ、見た件数（I1・I5）。"""
    seen = 0
    for run in candidates[:CANDIDATE_RUNS]:
        seen += 1
        jobs = (gh.get(f"repos/{gh.repo}/actions/runs/{run['id']}/jobs?per_page=100") or {}).get("jobs") or []
        if not _has_skipped_job(jobs):
            return run, jobs, seen
    return None, [], seen


def _measure_workflow(path: str, static_jobs: int, run: dict | None, jobs: list[dict]) -> tuple[dict, dict | None]:
    """ワークフロー 1 本の行（パス・job 数・壁時計）と、代表の run のテストの step の所要。run が無ければ静的な job 数だけ。"""
    entry = {"path": path, "jobs": static_jobs}
    if not run:
        return entry, None
    wall = _span(run.get("run_started_at"), run.get("updated_at"))
    if wall is not None:
        entry["wall_seconds"] = wall
    entry["jobs"] = max(entry["jobs"], len(jobs))
    return entry, _steps_of(jobs, run)


def _measure_runs(gh: Gh, jobs_static: dict[str, int], head: str | None) -> tuple[dict, list[dict], list[str]]:
    """ワークフローごとの行・所要の候補・注記。代表の run はジョブを飛ばしていない最新の候補（I1・I4・I5）。"""
    runs = (gh.get(f"repos/{gh.repo}/actions/runs?status=success&per_page=50") or {}).get("workflow_runs") or []
    required = _required_checks(gh, head)
    candidates: dict[str, list[dict]] = {}
    for run in runs:
        candidates.setdefault(run.get("path") or "", []).append(run)
    workflows, notes, junit, steps, timed_out = [], [], None, None, False
    for path in sorted(set(jobs_static) | {p for p in candidates if p}):
        mine = candidates.get(path) or []
        run, jobs = None, []
        if mine and not timed_out:
            try:
                run, jobs, seen = _representative(gh, mine)
            except GhUnavailable as e:
                if str(e) != "時間切れ":
                    raise
                timed_out = True
            else:
                if not run:
                    notes.append(f"飛ばしていない run が無い: {path}（候補 {seen} 件）")
        if mine and timed_out:
            notes.append(f"候補のジョブを取れない（時間切れ）: {path}")
        entry, found = _measure_workflow(path, jobs_static.get(path, 0), run, jobs)
        if found and found["seconds"] > (steps or {}).get("seconds", 0):
            steps = found
        if run:
            junit = junit or _junit_of_run(gh, run)
        workflows.append(entry)
    ci = {"provider": "github-actions" if workflows else "none", "workflows": workflows, "required_checks": required}
    return ci, [d for d in (junit, steps) if d], notes


def measure_issues(repo_part: dict, ci_part: dict) -> dict:
    """P8 の問い。課題の正本の候補と根拠（名前とホスト名だけ。URL は持たない）。"""
    hosts = sorted(set(repo_part["hosts"]) | set(ci_part.get("hosts") or []))
    candidates = []
    if repo_part["markdown_files"]:
        candidates.append("markdown")
    if ci_part.get("github_issues"):
        candidates.append("github")
    if any("redmine" in h for h in hosts):
        candidates.append("redmine")
    if any("redmine" not in h for h in hosts):
        candidates.append("external")
    evidence = [
        {"text": f"issues/*.md: {repo_part['markdown_files']} 本"},
        {"text": f"GitHub Issues（直近 100 件のうち）: {ci_part.get('github_issues', '不明')}"},
        {"text": f"PR の本文と雛形のホスト名: {'・'.join(hosts) or '無し'}"},
    ]
    if ci_part.get("merges") is None and not candidates:
        return unknown("課題の置き場を測れない")
    return question(candidates, evidence)
