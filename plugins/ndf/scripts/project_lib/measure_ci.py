"""CI と GitHub から測る項目（P3・P4・P6 の PR の宛先・P8 の GitHub の分）。標準ライブラリと `gh` だけで動く。

**`gh` へ渡すのは読む要求だけである（I5）。** `gh api` は `--method GET` を付けて打ち、artifact は一時ディレクトリへ落とす。
`gh` の 1 回の呼び出しは、締め切りまでの残りと 30 秒の小さい方で打ち切る（I10）。締め切りに届いた後は起動しない。
外部の語（job・artifact・ruleset）は、ここで宣言の語（CI の分割・所要の出所・必須のチェック）へ直す。
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import re
import statistics
import subprocess
import time
import xml.etree.ElementTree as ET
import zipfile

import repo as repo_id
import run_metrics

from .measure_repo import Tree, measured, question, unknown, url_hosts

CALL_LIMIT = 30.0
ARTIFACT_BYTES = 50 * 1024 * 1024
JUNIT_NAME = re.compile(r"junit|test-?result|test-?report|phpunit|pytest|jest|vitest", re.I)
TEST_STEP = re.compile(r"(?<![a-z])test|pytest|phpunit|jest|vitest|rspec", re.I)
RECORD_NAME = "cross-refactoring-allocation.jsonl"
RECORD_ROWS = 10


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
    """ワークフローのファイル → `jobs:` の直下の job の数（YAML を読まずに字下げで拾う）。"""
    out = {}
    for f in sorted(p for p in tree.files if re.match(r"\.github/workflows/[^/]+\.ya?ml$", p)):
        n, inside, indent = 0, False, None
        for line in (tree.read(f) or "").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            lead = len(line) - len(line.lstrip())
            if lead == 0:
                inside = line.rstrip() == "jobs:"
                continue
            if inside:
                indent = lead if indent is None else indent
                if lead == indent and line.strip().endswith(":"):
                    n += 1
        out[f] = n
    return out


def junit_seconds(xml_bytes: bytes) -> float | None:
    """JUnit の XML 1 本の直列の所要（根の `time`、無ければ直下の `testsuite` の `time` の合計）。"""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    if root.get("time"):
        try:
            return float(root.get("time"))
        except ValueError:
            return None
    total = 0.0
    for child in root.findall("testsuite"):
        try:
            total += float(child.get("time") or 0)
        except ValueError:
            continue
    return total if root.findall("testsuite") else None


def _junit_of_run(gh: Gh, run: dict) -> dict | None:
    arts = (gh.get(f"repos/{gh.repo}/actions/runs/{run['id']}/artifacts?per_page=100") or {}).get("artifacts") or []
    picked = [a for a in arts if JUNIT_NAME.search(a.get("name", "")) and not a.get("expired")]
    if not picked:
        return None
    total, files, size = 0.0, 0, 0
    for a in picked:
        size += int(a.get("size_in_bytes") or 0)
        if size > ARTIFACT_BYTES:
            break
        data = gh.raw(f"repos/{gh.repo}/actions/artifacts/{a['id']}/zip")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for name in z.namelist():
                    if name.lower().endswith(".xml"):
                        sec = junit_seconds(z.read(name))
                        if sec is not None:
                            total += sec
                            files += 1
        except zipfile.BadZipFile:
            continue
    if not files:
        return None
    return {"seconds": round(total, 1), "source": "ci-junit", "detail": f"run {run['id']}（{run.get('path')}）の JUnit {files} 本の合計"}


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
    """同じ機械の NDF の実行の記録の `whole_test.init` の直近 10 件の中央値（決定 9）。"""
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
            v = (json.loads(line).get("whole_test") or {}).get("init")
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
    out = []
    try:
        rules = gh.get(f"repos/{gh.repo}/rules/branches/{branch}") or []
    except GhUnavailable as e:
        if str(e) == "時間切れ":
            raise
        rules = []
    for r in rules if isinstance(rules, list) else []:
        if r.get("type") == "required_status_checks":
            out += [c.get("context") for c in (r.get("parameters") or {}).get("required_status_checks") or [] if c.get("context")]
    return sorted(set(out))


def measure_ci(tree: Tree, repo: str | None, head: str | None, deadline: float) -> dict:
    """P3・P4 と、P6・P8 の GitHub の分を測る。返すのは `{"ci", "test_duration", "merges", "github_issues", "hosts", "notes"}`。"""
    record = ndf_record(repo)
    durations = [record] if record else []
    out = {"merges": None, "github_issues": None, "hosts": [], "notes": []}
    jobs_static = workflow_jobs(tree)
    if not repo:
        reason = "origin が GitHub のリポジトリを指していない"
        out["ci"] = unknown(reason)
        out["test_duration"] = measured({"measured": durations}) if durations else unknown(reason)
        return out
    gh = Gh(tree.root, repo, deadline)
    try:
        ci, found = _measure_runs(gh, jobs_static, head)
        out["ci"] = measured(ci)
        durations += found
    except GhUnavailable as e:
        out["ci"] = unknown(str(e))
        out["notes"].append(f"CI を測れない: {e}")
    out["test_duration"] = (
        measured({"measured": durations})
        if durations
        else unknown(out["ci"].get("reason") or "CI に JUnit もテストの step も無く、NDF の実行の記録も無い")
    )
    try:
        pulls = gh.get(f"repos/{repo}/pulls?state=closed&per_page=100") or []
        merges: dict[str, int] = {}
        for pr in pulls:
            if pr.get("merged_at"):
                ref = (pr.get("base") or {}).get("ref")
                merges[ref] = merges.get(ref, 0) + 1
            out["hosts"] = sorted(set(out["hosts"]) | url_hosts(pr.get("body") or ""))
        out["merges"] = merges
        issues = gh.get(f"repos/{repo}/issues?state=all&per_page=100") or []
        out["github_issues"] = sum(1 for i in issues if "pull_request" not in i)
    except GhUnavailable as e:
        out["notes"].append(f"PR の宛先と GitHub Issues を測れない: {e}")
    return out


def _measure_runs(gh: Gh, jobs_static: dict[str, int], head: str | None) -> tuple[dict, list[dict]]:
    runs = (gh.get(f"repos/{gh.repo}/actions/runs?status=success&per_page=50") or {}).get("workflow_runs") or []
    latest: dict[str, dict] = {}
    for run in runs:
        latest.setdefault(run.get("path") or "", run)
    workflows, junit, steps = [], None, None
    for path in sorted(set(jobs_static) | {p for p in latest if p}):
        entry = {"path": path, "jobs": jobs_static.get(path, 0)}
        run = latest.get(path)
        if run:
            wall = _span(run.get("run_started_at"), run.get("updated_at"))
            if wall is not None:
                entry["wall_seconds"] = wall
            jobs = (gh.get(f"repos/{gh.repo}/actions/runs/{run['id']}/jobs?per_page=100") or {}).get("jobs") or []
            entry["jobs"] = max(entry["jobs"], len(jobs))
            found = _steps_of(jobs, run)
            if found and found["seconds"] > (steps or {}).get("seconds", 0):
                steps = found
            junit = junit or _junit_of_run(gh, run)
        workflows.append(entry)
    ci = {"provider": "github-actions" if workflows else "none", "workflows": workflows, "required_checks": _required_checks(gh, head)}
    return ci, [d for d in (junit, steps) if d]


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
