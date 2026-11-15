"""CI と GitHub から測る宣言の項目（P3・P4・P6 の PR の宛先・P8 の GitHub の分。#3569。旧い `project_lib/measure_ci.py` と
`lib/ci_workflows.py`・`lib/junit.py` の読み取りの移し先）。

**`gh` へ渡すのは読む要求だけである。** `gh api` は `--method GET` を付けて打ち、artifact はメモリで開く。
`gh` の 1 回の呼び出しは、締め切りまでの残りと 30 秒の小さい方で打ち切る。締め切りに届いた後は起動しない。
外部の語（job・artifact・ruleset）は、ここで宣言の語（CI の分割・所要の出所・必須のチェック）へ直す。
"""

from __future__ import annotations

import datetime as dt
import io
import json
import re
import statistics
import subprocess
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Mapping, Optional

from ndf.domains.project.measure_repo import CALL_LIMIT, Tree, measured, obj, question, seq, unknown, url_hosts
from ndf.domains.relay import registry

TEST_STEP = re.compile(r"(?<![a-z])test|pytest|phpunit|jest|vitest|rspec", re.I)
RECORD_NAME = "cross-refactoring-allocation.jsonl"
CANDIDATE_RUNS = 5  # ワークフローごとに代表を探す成功の run の上限（前の頁は取りに行かない）
RECENT = 10  # NDF の実行の記録の中央値に使う直近の件数
WORKFLOW_PATH = re.compile(r"\.github/workflows/[^/]+\.ya?ml$")
JUNIT_NAME = re.compile(r"junit|test-?result|test-?report|phpunit|pytest|jest|vitest", re.I)
ARTIFACT_BYTES = 50 * 1024 * 1024
TIME_UP = "時間切れ"


class GhUnavailable(Exception):
    """`gh` が使えない（無い・未認証・ネットワークが無い・時間切れ）。理由を持つ。"""


class Gh:
    """締め切りを持つ、読むだけの `gh api` の呼び出し。"""

    def __init__(self, root: Path, repo: str, deadline: float) -> None:
        self.root, self.repo, self.deadline = root, repo, deadline

    def raw(self, path: str) -> bytes:
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise GhUnavailable(TIME_UP)
        try:
            done = subprocess.run(["gh", "api", "--method", "GET", path], cwd=str(self.root), capture_output=True, timeout=min(CALL_LIMIT, left))
        except FileNotFoundError:
            raise GhUnavailable("gh が無い") from None
        except subprocess.TimeoutExpired:
            raise GhUnavailable(TIME_UP) from None
        except OSError as exc:
            raise GhUnavailable(f"gh を起動できない: {exc}") from None
        if done.returncode != 0:
            err = done.stderr.decode("utf-8", "replace").strip().splitlines()
            raise GhUnavailable(f"gh が失敗した: {(err if err else ['終了コード ' + str(done.returncode)])[-1][:200]}")
        return done.stdout

    def get(self, path: str) -> Any:
        body = self.raw(path).decode("utf-8")
        try:
            return json.loads(body) if body else None
        except ValueError:
            raise GhUnavailable(f"gh の出力を読めない: {path}") from None


def _time(value: Any) -> Optional[dt.datetime]:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:  # noqa: NDF401 ネットワーク: 時刻の無い・読めない応答は所要を出さない
        return None


def span(start: Any, end: Any) -> Optional[float]:
    a, b = _time(start), _time(end)
    return round((b - a).total_seconds(), 1) if a and b else None


def _strip_comment(line: str) -> str:
    """行末のコメント（引用の外で、空白の後に続く `#` から先）を落とす。"""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()


def job_ids(text: str) -> list[str]:
    """ワークフローの本文 → `jobs:` の直下の job id の並び（書いた順。YAML を読まずに字下げで拾う）。"""
    out: list[str] = []
    inside, indent = False, None
    for line in (_strip_comment(raw) for raw in text.splitlines()):
        lead = len(line) - len(line.lstrip())
        if line.strip() and lead == 0:
            inside = line == "jobs:"
        elif line.strip() and inside:
            indent = lead if indent is None else indent
            if lead == indent and line.endswith(":"):
                out.append(line.strip()[:-1].strip().strip("\"'"))
    return out


def required_contexts(rules: Any) -> list[str]:
    """`rules/branches/<branch>` の応答（規則の配列）→ 必須のチェックの名前（重複なく整列）。"""
    out = {
        c["context"]
        for r in seq(rules)
        if isinstance(r, dict) and r.get("type") == "required_status_checks"
        for c in seq(obj(r.get("parameters")).get("required_status_checks"))
        if isinstance(c, dict) and c.get("context")
    }
    return sorted(out)


def workflow_jobs(tree: Tree) -> dict[str, int]:
    """ワークフローのファイル → `jobs:` の直下の job の数。"""
    return {f: len(job_ids(tree.text(f))) for f in sorted(p for p in tree.files if WORKFLOW_PATH.match(p))}


def junit_seconds(xml_bytes: bytes) -> Optional[float]:
    """JUnit の XML 1 本の直列の所要（根の `time`、無ければ直下の `testsuite` の `time` の合計）。"""
    try:
        root = ET.fromstring(xml_bytes)  # noqa: S314 CI の成果物の JUnit を読むだけで、外部実体は展開しない
        if root.get("time"):
            return float(root.get("time"))
        suites = root.findall("testsuite")
        return sum(float(s.get("time") if s.get("time") else 0) for s in suites) if suites else None
    except (ET.ParseError, ValueError):  # noqa: NDF401 ネットワーク: 読めない成果物は所要の候補にしない
        return None


def _xmls(data: bytes) -> list[bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return [z.read(n) for n in z.namelist() if n.lower().endswith(".xml")]
    except zipfile.BadZipFile:
        return []


def _junit_of_run(gh: Gh, run: dict[str, Any]) -> Optional[dict[str, Any]]:
    """run の成果物の JUnit の直列の合計。"""
    arts = seq(obj(gh.get(f"repos/{gh.repo}/actions/runs/{run['id']}/artifacts?per_page=100")).get("artifacts"))
    picked = [a for a in arts if isinstance(a, dict) and not a.get("expired") and JUNIT_NAME.search(str(a.get("name")))]
    seconds: list[float] = []
    size = 0
    for a in picked:
        size += int(a["size_in_bytes"]) if a.get("size_in_bytes") else 0
        if size > ARTIFACT_BYTES:
            break
        seconds += [s for s in map(junit_seconds, _xmls(gh.raw(f"repos/{gh.repo}/actions/artifacts/{a['id']}/zip"))) if s is not None]
    if not seconds:
        return None
    return {"seconds": round(sum(seconds), 1), "source": "ci-junit", "detail": f"run {run['id']}（{run.get('path')}）の JUnit {len(seconds)} 本の合計"}


def _steps_of(jobs: list[dict[str, Any]], run: dict[str, Any]) -> Optional[dict[str, Any]]:
    secs = [
        s
        for job in jobs
        for step in seq(job.get("steps"))
        if TEST_STEP.search(str(step.get("name")))
        for s in [span(step.get("started_at"), step.get("completed_at"))]
        if s is not None
    ]
    total = sum(secs)
    if not secs or total <= 0:
        return None
    return {"seconds": round(total, 1), "source": "ci-steps", "detail": f"run {run['id']}（{run.get('path')}）のテストの step {len(secs)} 個の合計"}


def _record_value(line: str) -> Optional[float]:
    try:
        row = json.loads(line)
    except ValueError:  # noqa: NDF401 ファイル: 読めない行は飛ばす
        return None
    # 着手前のテストの行（`kind: init_test`）は全体テストの所要に数えない
    if not isinstance(row, dict) or row.get("kind") not in (None, "run"):
        return None
    v = obj(row.get("whole_test")).get("init")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def ndf_record(repo: Optional[str], environ: Mapping[str, str]) -> Optional[dict[str, Any]]:
    """同じ機械の NDF の実行の記録の、実行の行の `whole_test.init` の直近 10 件の中央値。"""
    if not repo:
        return None
    f = registry.legacy_state_root(environ) / "metrics" / repo.replace("/", "--") / RECORD_NAME
    try:
        lines = f.read_text(encoding="utf-8").splitlines() if f.is_file() else []
    except OSError:  # noqa: NDF401 ファイル: 読めない記録は所要の候補にしない
        return None
    values = [v for v in map(_record_value, lines) if v is not None][-RECENT:]
    if not values:
        return None
    return {"seconds": round(statistics.median(values), 1), "source": "ndf-record", "detail": f"NDF の実行の記録の直近 {len(values)} 件の中央値"}


def _required_checks(gh: Gh, branch: Optional[str]) -> list[str]:
    if not branch:
        return []
    try:
        return required_contexts(gh.get(f"repos/{gh.repo}/rules/branches/{branch}"))
    except GhUnavailable as exc:
        if str(exc) == TIME_UP:
            raise
        return []


def _test_duration(durations: list[dict[str, Any]], ci_reason: Optional[str]) -> dict[str, Any]:
    """測れた所要の候補があれば measured、無ければ unknown。"""
    if durations:
        return measured({"measured": durations})
    return unknown(ci_reason if ci_reason else "CI に JUnit もテストの step も無く、NDF の実行の記録も無い")


def _measure_merges(gh: Gh) -> tuple[dict[str, int], list[str]]:
    """閉じた PR の宛先別のマージ数と、本文に出たホスト名。"""
    pulls = [p for p in seq(gh.get(f"repos/{gh.repo}/pulls?state=closed&per_page=100")) if isinstance(p, dict)]
    merges: dict[str, int] = {}
    for pr in pulls:
        if pr.get("merged_at"):
            ref = obj(pr.get("base")).get("ref")
            merges[ref] = merges[ref] + 1 if ref in merges else 1
    hosts = {h for pr in pulls for h in url_hosts(str(pr["body"]) if pr.get("body") else "")}
    return merges, sorted(hosts)


def _has_skipped_job(jobs: list[dict[str, Any]]) -> bool:
    """ジョブを飛ばした run か（conclusion が `skipped` のジョブがある）。名前・イベントでは判定しない。"""
    return any(job.get("conclusion") == "skipped" for job in jobs)


def _representative(gh: Gh, candidates: list[dict[str, Any]]) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]], int]:
    """新しい順の候補を最大 `CANDIDATE_RUNS` 件見て、ジョブを飛ばしていない最初の run とそのジョブ、見た件数。"""
    seen = 0
    for run in candidates[:CANDIDATE_RUNS]:
        seen += 1
        jobs = [j for j in seq(obj(gh.get(f"repos/{gh.repo}/actions/runs/{run['id']}/jobs?per_page=100")).get("jobs")) if isinstance(j, dict)]
        if not _has_skipped_job(jobs):
            return run, jobs, seen
    return None, [], seen


def _workflow_entry(path: str, static_jobs: int, run: Optional[dict[str, Any]], jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """ワークフロー 1 本の行（パス・job 数・壁時計）。run が無ければ静的な job 数だけ。"""
    entry: dict[str, Any] = {"path": path, "jobs": static_jobs}
    if run:
        wall = span(run.get("run_started_at"), run.get("updated_at"))
        if wall is not None:
            entry["wall_seconds"] = wall
        entry["jobs"] = max(static_jobs, len(jobs))
    return entry


class _Runs:
    """ワークフローごとの代表の run を選び、行・所要の候補・注記を集める。"""

    def __init__(self, gh: Gh) -> None:
        self.gh = gh
        self.notes: list[str] = []
        self.timed_out = False
        self.junit: Optional[dict[str, Any]] = None
        self.steps: Optional[dict[str, Any]] = None

    def pick(self, path: str, mine: list[dict[str, Any]]) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]]]:
        if not mine:
            return None, []
        if self.timed_out:
            self.notes.append(f"候補のジョブを取れない（時間切れ）: {path}")
            return None, []
        try:
            run, jobs, seen = _representative(self.gh, mine)
        except GhUnavailable as exc:
            if str(exc) != TIME_UP:
                raise
            self.timed_out = True
            self.notes.append(f"候補のジョブを取れない（時間切れ）: {path}")
            return None, []
        if not run:
            self.notes.append(f"飛ばしていない run が無い: {path}（候補 {seen} 件）")
        return run, jobs

    def take(self, run: Optional[dict[str, Any]], jobs: list[dict[str, Any]]) -> None:
        if not run:
            return
        found = _steps_of(jobs, run)
        if found and found["seconds"] > (self.steps["seconds"] if self.steps else 0):
            self.steps = found
        if self.junit is None:
            self.junit = _junit_of_run(self.gh, run)


def _measure_runs(gh: Gh, jobs_static: dict[str, int], head: Optional[str]) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """ワークフローごとの行・所要の候補・注記。代表の run はジョブを飛ばしていない最新の候補。"""
    runs = [r for r in seq(obj(gh.get(f"repos/{gh.repo}/actions/runs?status=success&per_page=50")).get("workflow_runs")) if isinstance(r, dict)]
    required = _required_checks(gh, head)
    candidates: dict[str, list[dict[str, Any]]] = {}
    for run in runs:
        candidates.setdefault(str(run["path"]) if run.get("path") else "", []).append(run)
    state = _Runs(gh)
    workflows = []
    for path in sorted(set(jobs_static) | {p for p in candidates if p}):
        run, jobs = state.pick(path, candidates[path] if path in candidates else [])
        state.take(run, jobs)
        workflows.append(_workflow_entry(path, jobs_static[path] if path in jobs_static else 0, run, jobs))
    ci = {"provider": "github-actions" if workflows else "none", "workflows": workflows, "required_checks": required}
    return ci, [d for d in (state.junit, state.steps) if d], state.notes


def measure(tree: Tree, repo: Optional[str], head: Optional[str], deadline: float, environ: Mapping[str, str]) -> dict[str, Any]:
    """P3・P4 と、P6・P8 の GitHub の分を測る。返すのは `{"ci", "test_duration", "merges", "github_issues", "hosts", "notes"}`。"""
    record = ndf_record(repo, environ)
    durations = [record] if record else []
    out: dict[str, Any] = {"merges": None, "github_issues": None, "hosts": [], "notes": []}
    if not repo:
        reason = "origin が GitHub のリポジトリを指していない"
        return {**out, "ci": unknown(reason), "test_duration": _test_duration(durations, reason)}
    gh = Gh(tree.root, repo, deadline)
    try:
        ci, found, notes = _measure_runs(gh, workflow_jobs(tree), head)
        out["ci"] = measured(ci)
        durations += found
        out["notes"] += notes
    except GhUnavailable as exc:
        out["ci"] = unknown(str(exc))
        out["notes"].append(f"CI を測れない: {exc}")
    out["test_duration"] = _test_duration(durations, out["ci"].get("reason"))
    try:
        out["merges"], out["hosts"] = _measure_merges(gh)
        issues = seq(gh.get(f"repos/{repo}/issues?state=all&per_page=100"))
        out["github_issues"] = sum(1 for i in issues if isinstance(i, dict) and "pull_request" not in i)
    except GhUnavailable as exc:
        out["notes"].append(f"PR の宛先と GitHub Issues を測れない: {exc}")
    return out


def measure_issues(repo_part: dict[str, Any], ci_part: dict[str, Any]) -> dict[str, Any]:
    """P8 の問い。課題の正本の候補と根拠（名前とホスト名だけ。URL は持たない）。"""
    hosts = sorted(set(repo_part["hosts"]) | set(seq(ci_part.get("hosts"))))
    found = {
        "markdown": bool(repo_part["markdown_files"]),
        "github": bool(ci_part.get("github_issues")),
        "redmine": any("redmine" in h for h in hosts),
        "external": any("redmine" not in h for h in hosts),
    }
    candidates = [k for k, hit in found.items() if hit]
    issues = ci_part["github_issues"] if ci_part.get("github_issues") is not None else "不明"
    evidence = [
        {"text": f"issues/*.md: {repo_part['markdown_files']} 本"},
        {"text": f"GitHub Issues（直近 100 件のうち）: {issues}"},
        {"text": f"PR の本文と雛形のホスト名: {'・'.join(hosts) if hosts else '無し'}"},
    ]
    if ci_part.get("merges") is None and not candidates:
        return unknown("課題の置き場を測れない")
    return question(candidates, evidence)
