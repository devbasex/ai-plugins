#!/usr/bin/env python3
"""CI のテストの所要を読む（#4331）。標準ライブラリと `gh` だけに頼り、実行機の `python3` で動く。

    python3 scripts/ci_test_durations.py run <run の ID> [--top <件数>]
    python3 scripts/ci_test_durations.py trend <XML のディレクトリ> --run <ID> --run-number <番号> --attempt <試行> --out <JSON>

`run` は 1 回の実行の成果物 `pytest-junit-<番号>`・`pytest-ndf-junit-<番号>` を取り、根と NDF のそれぞれの合計・遅い順の上位・
ジョブごとの所要を出す。根か NDF の XML が 1 つも読めなければ 1、引数の誤りと `gh` を使えないときは 2 で終わる。

`trend` は `main` への push の中央値のジョブ（`.github/workflows/pytest.yml` の `pytest (所要)`）が打つ。この回の合計を集計の JSON に書き、
過去の回の集計（成果物 `ci-test-duration-total`）と合わせた直近 `runs` 回の中央値を上限と比べる。超えたら警告の注記を出すが、
終了コードは引数と設定の誤りのときだけ 2 で、ほかは 0 である。回数・上限・上位の件数は `scripts/ci-test-duration.json` に置く。
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ファイルの直接実行（scripts が sys.path の先頭）と `python3 -m scripts.ci_test_durations` の両方で読めるようにする
if __package__:
    from .pytest_shard import NDF_JUNIT, ROOT_JUNIT
else:
    from pytest_shard import NDF_JUNIT, ROOT_JUNIT

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "scripts" / "ci-test-duration.json"
# 成果物の名前・試行の番号のファイル・手順の名前は .github/workflows/pytest.yml と同じ値（YAML から渡す経路が無い）
# 成果物の名前の頭（根と NDF）は scripts/pytest_shard.py の定数を正本にする
ROOT_ARTIFACT = ROOT_JUNIT
NDF_ARTIFACT = NDF_JUNIT
ARTIFACT_NAME = re.compile(rf"^(?:{re.escape(ROOT_ARTIFACT)}|({re.escape(NDF_ARTIFACT)}))(\d+)$")
ARTIFACT_LIMIT = 16 * 1024 * 1024
TOTAL_LIMIT = 64 * 1024 * 1024
TOTAL_ARTIFACT = "ci-test-duration-total"
ATTEMPT_FILE = "shard-attempt-{index}.txt"
SHARD_JOB = re.compile(r"^pytest \((\d+)/(\d+)\)$")
ROOT_STEP = "根のテスト"
NDF_STEP = "NDF のテスト"
WORKFLOW = "pytest.yml"
PER_PAGE = 100
PAGE_LIMIT = 10
# リポジトリの成果物の保存期間（actions/permissions/artifact-and-log-retention の days の写し。中央値のジョブの権限では読めない）
RETENTION_DAYS = 90
CALL_TIMEOUT = 30
JOB_BUDGET = 60
UPLOAD_RESERVE = 5
STARTED_ENV = "CI_TEST_DURATION_STARTED"
CHUNK = 64 * 1024
KINDS = {"root": "根", "ndf": "NDF"}
ABSENT = {"root": "根の JUnit XML が無い", "ndf": "NDF の JUnit XML が無い"}
STOPS = {
    "enough": "そろった",
    "zip_limit": "ZIP の取得の上限",
    "page_limit": "ページの上限",
    "retention": "保存期間",
    "exhausted": "候補が尽きた",
    "list_error": "一覧を取れない",
    "time_limit": "時間の上限",
}
REPO_API = "repos/{owner}/{repo}"


class ConfigError(Exception):
    pass


class GhError(Exception):
    """`gh` の呼び出しの失敗。メッセージは API のパスと標準エラーの先頭の 1 行。"""


class GhUnavailable(GhError):
    pass


class GhTimeUp(GhError):
    pass


class ArtifactTooLarge(GhError):
    def __init__(self, received: int) -> None:
        super().__init__(f"受信の上限を超えた（{received} バイト）")
        self.received = received


class Gh:
    """`gh api --method GET` を締め切りつきで打つ。呼び出しの数を種類（`jobs`・`pages`・`zips`・`artifacts`）ごとに数える。"""

    def __init__(self, deadline: float | None = None, clock: Callable[[], float] = time.time) -> None:
        self.deadline = deadline
        self.clock = clock
        self.calls: Counter[str] = Counter()

    def _timeout(self) -> float:
        if self.deadline is None:
            return CALL_TIMEOUT
        left = self.deadline - self.clock()
        if left <= 0:
            raise GhTimeUp("時間の上限")
        return min(CALL_TIMEOUT, left)

    @staticmethod
    def _timed_out(path: str, timeout: float) -> GhError:
        return GhTimeUp(f"{path}: 時間の上限") if timeout < CALL_TIMEOUT else GhError(f"{path}: {CALL_TIMEOUT} 秒を過ぎた")

    @staticmethod
    def _failed(path: str, stderr: bytes) -> GhError:
        lines = stderr.decode("utf-8", "replace").strip().splitlines()
        return GhError(f"{path}: {lines[0] if lines else 'gh が失敗した'}")

    def _begin(self, path: str, kind: str) -> tuple[list[str], float]:
        """締め切りを確かめて呼び出しを数え、`gh` の起動の引数とこの呼び出しの待ち時間を返す。"""
        timeout = self._timeout()
        self.calls[kind] += 1
        return ["gh", "api", "--method", "GET", path], timeout

    @staticmethod
    def _launch(start: Callable[[], Any]) -> Any:
        """`gh` を起動する。`gh` が無ければ `GhUnavailable` にする。"""
        try:
            return start()
        except FileNotFoundError as e:
            raise GhUnavailable("gh が無い") from e

    @staticmethod
    def _read_limited(proc: subprocess.Popen, limit: int) -> list[bytes]:
        """標準出力を `limit` バイトまで読む。超えたら `gh` を止めて `ArtifactTooLarge` にする。"""
        chunks: list[bytes] = []
        size = 0
        while chunk := proc.stdout.read(CHUNK):
            size += len(chunk)
            if size > limit:
                proc.kill()
                proc.wait()
                raise ArtifactTooLarge(size)
            chunks.append(chunk)
        return chunks

    def _run(self, path: str, kind: str) -> bytes:
        cmd, timeout = self._begin(path, kind)
        try:
            p = self._launch(lambda: subprocess.run(cmd, capture_output=True, timeout=timeout))
        except subprocess.TimeoutExpired as e:
            raise self._timed_out(path, timeout) from e
        if p.returncode != 0:
            raise self._failed(path, p.stderr)
        return p.stdout

    def get(self, path: str, kind: str) -> Any:
        data = self._run(path, kind)
        try:
            return json.loads(data)
        except ValueError as e:
            raise GhError(f"{path}: 応答が JSON でない") from e

    def raw(self, path: str, kind: str, limit: int | None = None) -> bytes:
        if limit is None:
            return self._run(path, kind)
        cmd, timeout = self._begin(path, kind)
        with tempfile.TemporaryFile() as err:
            proc = self._launch(lambda: subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err))
            fired: list[bool] = []
            timer = threading.Timer(timeout, lambda: (fired.append(True), proc.kill()))
            timer.start()
            try:
                chunks = self._read_limited(proc, limit)
                code = proc.wait()
            finally:
                timer.cancel()
            if fired:
                raise self._timed_out(path, timeout)
            if code != 0:
                err.seek(0)
                raise self._failed(path, err.read())
        return b"".join(chunks)


@dataclass
class Part:
    kind: str
    index: int
    cases: list[tuple[str, float]] = field(default_factory=list)
    attempt: int | None = None
    error: str | None = None


@dataclass
class Match:
    total: int
    accepted: list[int] = field(default_factory=list)
    missing: list[int] = field(default_factory=list)
    stale: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    shard_attempts: list[int | None] = field(default_factory=list)


@dataclass
class Search:
    totals: list[tuple[int, float]] = field(default_factory=list)
    stop: str = ""
    unreadable: list[str] = field(default_factory=list)


def read_config(path: Path = CONFIG) -> dict[str, int | float]:
    """所要の設定（`runs`・`limit_seconds`・`top`）を読み、型を確かめる。"""
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigError(f"{path}: 読めない（{e}）") from e
    if not isinstance(cfg, dict):
        raise ConfigError(f"{path}: オブジェクトでない")

    def positive_int(key: str) -> bool:
        return isinstance(cfg.get(key), int) and not isinstance(cfg[key], bool) and cfg[key] >= 1

    bad = [k for k in ("runs", "top") if not positive_int(k)]
    limit = cfg.get("limit_seconds")
    if not (isinstance(limit, (int, float)) and not isinstance(limit, bool) and limit > 0):
        bad.append("limit_seconds")
    if bad:
        raise ConfigError(f"{path}: {', '.join(bad)} が無いか値が誤り（runs・top は 1 以上の整数、limit_seconds は正の数）")
    return {"runs": cfg["runs"], "limit_seconds": float(limit), "top": cfg["top"]}


def shard_jobs(jobs: list[dict]) -> tuple[int, dict[int, dict]]:
    """名前が `pytest (<番号>/<総数>)` のジョブを拾い、総数と番号ごとのジョブを返す。"""
    total = 0
    by: dict[int, dict] = {}
    for job in jobs:
        m = SHARD_JOB.match(str(job.get("name", "")))
        if m:
            by[int(m[1])] = job
            total = max(total, int(m[2]))
    return total, by


def _case_seconds(text: str | None) -> float:
    """`testcase` の `time` を秒にする。無ければ 0。有限の非負の数でなければ `ValueError`。"""
    seconds = float(text or 0)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"time={text!r} は有限の非負の秒でない")
    return seconds


def parse_junit(data: bytes) -> list[tuple[str, float]]:
    """`testcase` ごとに、テストの id（`classname::name`）と秒を返す。壊れた XML は `ET.ParseError`、壊れた秒は `ValueError`。"""
    root = ET.fromstring(data)
    return [(f"{c.get('classname', '')}::{c.get('name', '')}", _case_seconds(c.get("time"))) for c in root.iter("testcase")]


def read_part(d: Path, kind: str, index: int) -> Part:
    """成果物 1 つのディレクトリから、試行の番号と XML の項目を読んで Part にする。"""
    part = Part(kind, index)
    try:
        part.attempt = int((d / ATTEMPT_FILE.format(index=part.index)).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        part.attempt = None
    xmls = sorted(d.rglob("*.xml"))
    if not xmls:
        part.error = "JUnit XML が無い"
    for xml in xmls:
        try:
            part.cases.extend(parse_junit(xml.read_bytes()))
        except (OSError, ET.ParseError, ValueError) as e:
            part.error = f"{xml.name} を読めない（{e}）"
            break
    return part


def artifact_part(name: str) -> tuple[str, int] | None:
    """成果物の名前から `(根か NDF, ジョブの番号)` を読む。名前が合わなければ `None`。"""
    m = ARTIFACT_NAME.match(name)
    if not m:
        return None
    return ("ndf" if m[1] else "root", int(m[2]))


def read_parts(xml_dir: Path) -> dict[tuple[str, int], Part]:
    """成果物の名前ごとのディレクトリから、`(根か NDF, ジョブの番号)` ごとに XML の項目と試行の番号を読む。"""
    parts: dict[tuple[str, int], Part] = {}
    if not xml_dir.is_dir():
        return parts
    for d in sorted(p for p in xml_dir.iterdir() if p.is_dir()):
        key = artifact_part(d.name)
        if key is None:
            continue
        part = read_part(d, *key)
        parts[(part.kind, part.index)] = part
    return parts


def match_attempts(parts: dict[tuple[str, int], Part], total: int, jobs: dict[int, dict]) -> Match:
    """ジョブの番号ごとに、2 つの部品がそろい、試行の番号が一覧の `run_attempt` と同じかを照合する。"""
    match = Match(total, shard_attempts=[None] * total)
    for i in range(total):
        pair = [parts.get((k, i)) for k in KINDS]
        job = jobs.get(i)
        if job is None or any(p is None or p.error for p in pair):
            match.missing.append(i)
            continue
        want = job.get("run_attempt")
        if any(p.attempt != want for p in pair):
            match.stale.append(i)
            continue
        match.accepted.append(i)
        match.shard_attempts[i] = want
        if job.get("conclusion") != "success":
            match.failed.append(i)
    return match


def _numbers(xs: list[int]) -> str:
    return "、".join(str(x) for x in xs)


def _sums(parts: dict[tuple[str, int], Part], accepted: list[int]) -> dict[str, tuple[float, int]]:
    out = {}
    for kind in KINDS:
        cases = [c for i in accepted if (kind, i) in parts for c in parts[(kind, i)].cases]
        out[kind] = (sum(t for _, t in cases), len(cases))
    return out


def current_total(xml_dir: Path, gh: Gh, run_id: int, attempt: int) -> tuple[dict, list[str]]:
    """この回の集計の JSON と、読めなかったものの説明を返す。"""
    parts = read_parts(xml_dir)
    guess = max((i for _, i in parts), default=-1) + 1

    def body(
        shard_attempts: list[int | None],
        root: float | None,
        ndf: float | None,
        total: float | None,
        reason: str | None,
    ) -> dict:
        return {
            "run": run_id,
            "attempt": attempt,
            "shard_attempts": shard_attempts,
            "valid": reason is None,
            "root_seconds": root,
            "ndf_seconds": ndf,
            "total_seconds": total,
            "reason": reason,
        }

    def invalid(reason: str, total: int, attempts: list[int | None] | None = None) -> tuple[dict, list[str]]:
        shard_attempts = attempts if attempts is not None else [None] * total
        return body(shard_attempts, None, None, None, reason), [f"この回（run {run_id}）: {reason}"]

    try:
        listed = latest_jobs(gh, run_id)
    except GhTimeUp:
        return invalid("ジョブの一覧を取れない（時間の上限）", guess)
    except (GhError, KeyError, TypeError):
        return invalid("ジョブの一覧を取れない", guess)
    total, jobs = shard_jobs(listed)
    if total == 0:
        return invalid("テストのジョブが一覧に無い", guess)
    match = match_attempts(parts, total, jobs)
    reasons = []
    if match.missing:
        reasons.append(f"欠けたジョブ: {_numbers(match.missing)}")
        reasons += [f"読めない XML（{KINDS[p.kind]} {p.index}）: {p.error}" for p in parts.values() if p.error]
    if match.stale:
        reasons.append(f"古い試行のジョブ: {_numbers(match.stale)}")
    if match.failed:
        reasons.append(f"失敗したジョブ: {_numbers(match.failed)}")
    if reasons:
        return invalid("。".join(reasons), total, match.shard_attempts)
    sums = _sums(parts, match.accepted)
    root, ndf = sums["root"][0], sums["ndf"][0]
    return body(match.shard_attempts, round(root, 1), round(ndf, 1), round(root + ndf, 1), None), []


def _when(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _total_of(data: bytes, run_id: int) -> tuple[dict | None, str | None]:
    """過去の回の集計の ZIP から集計を読む。読めなければ理由を返す。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [n for n in zf.namelist() if n.endswith(".json")]
            if not names:
                return None, "ZIP に JSON が無い"
            body = json.loads(zf.read(names[0]))
    except (zipfile.BadZipFile, ValueError, zlib.error, EOFError) as e:
        return None, f"集計を読めない（{e}）"
    if not isinstance(body, dict) or any(k not in body for k in ("run", "attempt", "valid", "total_seconds")):
        return None, "集計のキーが足りない"
    if body["run"] != run_id:
        return None, f"集計の run が {body['run']}"
    return body, None


def past_totals(gh: Gh, run_number: int, need: int, now: datetime) -> Search:
    """過去の回の有効な集計を、実行番号の大きい順に `need` 回まで集める。"""
    search = Search()
    pages = zips = 0
    runs_page = arts_page = 0
    runs_done = arts_done = False
    candidates: deque[dict] = deque()
    arts: dict[int, dict] = {}
    oldest: datetime | None = None
    horizon = now - timedelta(days=RETENTION_DAYS)

    def stop(reason: str) -> Search:
        search.stop = reason
        return search

    def fetch(path: str, key: str, label: str) -> tuple[list, str | None]:
        """一覧を 1 ページ取る。取れなければ止める理由を返す。"""
        nonlocal pages
        if pages >= PAGE_LIMIT:
            return [], "page_limit"
        pages += 1
        try:
            return gh.get(path, "pages")[key], None
        except GhTimeUp:
            return [], "time_limit"
        except (GhError, KeyError, TypeError) as e:
            search.unreadable.append(f"{label}: {e}")
            return [], "list_error"

    while True:
        if len(search.totals) >= need:
            return stop("enough")
        if not candidates:
            if runs_done:
                return stop("exhausted")
            runs_page += 1
            path = (
                f"{REPO_API}/actions/workflows/{WORKFLOW}/runs?branch=main&event=push&status=success&per_page={PER_PAGE}&page={runs_page}"
            )
            runs, reason = fetch(path, "workflow_runs", "実行の一覧")
            if reason:
                return stop(reason)
            runs_done = len(runs) < PER_PAGE
            candidates.extend(sorted((r for r in runs if r["run_number"] < run_number), key=lambda r: -r["run_number"]))
            continue
        run = candidates[0]
        created = _when(run["created_at"])
        if created < horizon:
            return stop("retention")
        art = arts.get(run["id"])
        if art is None:
            if not arts_done and (oldest is None or oldest > created):
                arts_page += 1
                path = f"{REPO_API}/actions/artifacts?name={TOTAL_ARTIFACT}&per_page={PER_PAGE}&page={arts_page}"
                page, reason = fetch(path, "artifacts", "成果物の一覧")
                if reason:
                    return stop(reason)
                arts_done = len(page) < PER_PAGE
                for a in page:
                    if not a.get("expired") and (a.get("workflow_run") or {}).get("id") is not None:
                        arts.setdefault(a["workflow_run"]["id"], a)
                if page:
                    low = min(_when(a["created_at"]) for a in page)
                    oldest = low if oldest is None else min(oldest, low)
                continue
            candidates.popleft()
            continue
        if zips >= 2 * need:
            return stop("zip_limit")
        zips += 1
        try:
            data = gh.raw(f"{REPO_API}/actions/artifacts/{art['id']}/zip", "zips")
        except GhTimeUp:
            return stop("time_limit")
        except GhError as e:
            search.unreadable.append(f"run {run['id']}: {e}")
            candidates.popleft()
            continue
        body, reason = _total_of(data, run["id"])
        candidates.popleft()
        if body is None:
            search.unreadable.append(f"run {run['id']}: {reason}")
        elif body["valid"] and body["attempt"] == run.get("run_attempt") and isinstance(body["total_seconds"], (int, float)):
            search.totals.append((run["id"], float(body["total_seconds"])))


def verdict(totals: list[float], cfg: dict) -> dict:
    """合計の中央値・上限・差と、超えたか（等しいときは超えない）を返す。"""
    median = statistics.median(totals)
    limit = cfg["limit_seconds"]
    return {"count": len(totals), "median": round(median, 1), "limit": limit, "diff": round(median - limit, 1), "over": median > limit}


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def render_trend(
    current: dict, unreadable: list[str], search: Search | None, judged: dict | None, calls: Counter[str]
) -> tuple[str, list[str], str]:
    """標準出力の本文・注記の行・ジョブの要約の Markdown を作る。"""
    lines: list[str] = []
    md: list[str] = ["## テストの所要", ""]
    notes: list[str] = []
    if current["valid"]:
        lines.append(
            f"この回（run {current['run']}）: 合計 {current['total_seconds']} 秒（根 {current['root_seconds']}・NDF {current['ndf_seconds']}）"
        )
    else:
        lines.append(f"この回（run {current['run']}）: 無効な集計（{current['reason']}）")
    counted = [(current["run"], current["total_seconds"])] if current["valid"] else []
    if search is not None:
        counted += search.totals
    if counted:
        md += ["| run | 合計の秒 |", "| --- | ---: |"]
        for run_id, seconds in counted:
            lines.append(f"  run {run_id}: {seconds} 秒")
            md.append(f"| {run_id} | {seconds} |")
        md.append("")
    if judged is not None:
        text = f"直近 {judged['count']} 回の中央値 {judged['median']} 秒・上限 {judged['limit']} 秒・差 {judged['diff']} 秒"
        lines.append(text)
        md.append(f"- {text}")
        if judged["over"]:
            notes.append(
                "::warning title=テストの所要::"
                + _escape(f"直近 {judged['count']} 回の中央値 {judged['median']} 秒が上限 {judged['limit']} 秒を {judged['diff']} 秒超えた")
            )
    elif current["valid"] and search is not None:
        text = f"そろった回: {len(counted)} 回（止めた理由: {STOPS.get(search.stop, search.stop)}）。判定しない"
        lines.append(text)
        md.append(f"- {text}")
        notes.append("::notice title=テストの所要::" + _escape(text))
    unread = list(unreadable) + (search.unreadable if search is not None else [])
    if unread:
        text = "読めなかったもの: " + "、".join(unread)
        lines.append(text)
        md.append(f"- {text}")
        notes.append("::warning title=テストの所要の読み取り::" + _escape(text))
    lines.append(f"GitHub API の呼び出し: jobs {calls['jobs']}・pages {calls['pages']}・zips {calls['zips']}")
    return "\n".join(lines), notes, "\n".join(md) + "\n"


def _extract(data: bytes, name: str, kind: str, index: int, dest: Path) -> None:
    """検証を通ったエントリだけを `dest/<名前>/` へ書き出す。壊れた ZIP はディレクトリごと消す。"""
    prefix = NDF_ARTIFACT if kind == "ndf" else ROOT_ARTIFACT
    allowed = {f"{prefix}{index}.xml", ATTEMPT_FILE.format(index=index)}
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError):
        sys.stderr.write(f"成果物 {name} を読めない（壊れた ZIP）\n")
        return
    with zf:
        entries = [i for i in zf.infolist() if i.filename in allowed and not i.is_dir()]
        if sum(i.file_size for i in entries) > ARTIFACT_LIMIT:
            sys.stderr.write(f"成果物 {name} を読み飛ばした（大きさの上限 {ARTIFACT_LIMIT} バイトを超えた）\n")
            return
        out = dest / name
        out.mkdir(parents=True, exist_ok=True)
        try:
            for info in entries:
                target = (out / info.filename).resolve()
                if not target.is_relative_to(dest.resolve()):
                    continue
                with zf.open(info) as src, open(target, "wb") as dst:
                    while chunk := src.read(CHUNK):
                        dst.write(chunk)
        except (zipfile.BadZipFile, OSError, zlib.error, EOFError):
            shutil.rmtree(out, ignore_errors=True)
            sys.stderr.write(f"成果物 {name} を読めない（壊れた ZIP）\n")


def latest_jobs(gh: Gh, run_id: int) -> list[dict]:
    """1 回の実行の最新の試行のジョブの一覧を取る。"""
    return gh.get(f"{REPO_API}/actions/runs/{run_id}/jobs?filter=latest&per_page={PER_PAGE}", "jobs")["jobs"]


def fetch_run(gh: Gh, run_id: int, dest: Path) -> list[dict]:
    """1 回の実行の `pytest-*junit-*` の成果物を `dest` の下へ展開し、最新の試行のジョブの一覧を返す。"""
    jobs = latest_jobs(gh, run_id)
    total, _ = shard_jobs(jobs)
    arts = gh.get(f"{REPO_API}/actions/runs/{run_id}/artifacts?per_page={PER_PAGE}", "artifacts")["artifacts"]
    received = 0
    for a in sorted(arts, key=lambda a: str(a.get("name", ""))):
        name = str(a.get("name", ""))
        key = artifact_part(name)
        if key is None or key[1] >= total or a.get("expired"):
            continue
        room = min(ARTIFACT_LIMIT, TOTAL_LIMIT - received)
        if int(a.get("size_in_bytes") or 0) > room:
            sys.stderr.write(f"成果物 {name} を読み飛ばした（大きさの上限 {room} バイトを超えた）\n")
            continue
        try:
            data = gh.raw(f"{REPO_API}/actions/artifacts/{a['id']}/zip", "zips", limit=room)
        except ArtifactTooLarge as e:
            received += e.received
            sys.stderr.write(f"成果物 {name} を読み飛ばした（大きさの上限 {room} バイトを超えた）\n")
            continue
        except GhUnavailable:
            raise
        except GhError as e:
            sys.stderr.write(f"成果物 {name} を取れない（{e}）\n")
            continue
        received += len(data)
        _extract(data, name, *key, dest)
    return jobs


def _seconds_between(item: dict | None) -> float | None:
    if not item or not item.get("started_at") or not item.get("completed_at"):
        return None
    return (_when(item["completed_at"]) - _when(item["started_at"])).total_seconds()


def _cell(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f}"


def render_run(parts: dict[tuple[str, int], Part], match: Match, jobs: dict[int, dict], top: int) -> str:
    """`run` の出力（先頭の行・合計・遅い順の上位・ジョブごとの所要）を作る。"""
    lines = []
    head = []
    if match.missing:
        head.append(f"欠けたジョブ: {_numbers(match.missing)}")
    if match.stale:
        head.append(f"古い試行のジョブ: {_numbers(match.stale)}")
    if head:
        lines.append(f"部分集計（{'、'.join(head)}）")
    if match.failed:
        lines.append(f"失敗したジョブを含む（{_numbers(match.failed)}）")
    sums = _sums(parts, match.accepted)
    for kind, label in KINDS.items():
        lines.append(f"{label}: 合計 {sums[kind][0]:.1f} 秒（{sums[kind][1]} 件）")
    lines.append(f"合計: {sums['root'][0] + sums['ndf'][0]:.1f} 秒（{sums['root'][1] + sums['ndf'][1]} 件）")
    cases = [(t, KINDS[k], tid) for i in match.accepted for k in KINDS if (k, i) in parts for tid, t in parts[(k, i)].cases]
    lines += ["", f"遅い順の上位 {top} 件:"]
    lines += [f"  {t:7.2f} 秒  {label}  {tid}" for t, label, tid in sorted(cases, key=lambda c: (-c[0], c[2]))[:top]]
    lines += ["", "ジョブ  結果  全体  根のテスト  NDF のテスト（秒）"]
    whole: dict[int, float] = {}
    for i in range(match.total):
        job = jobs.get(i)
        steps = {s.get("name"): s for s in (job or {}).get("steps") or []}
        seconds = _seconds_between(job)
        if seconds is not None:
            whole[i] = seconds
        conclusion = (job or {}).get("conclusion") or "-"
        lines.append(
            f"  {i}  {conclusion}  {_cell(seconds)}  {_cell(_seconds_between(steps.get(ROOT_STEP)))}"
            f"  {_cell(_seconds_between(steps.get(NDF_STEP)))}"
        )
    if whole:
        longest = max(whole, key=lambda i: (whole[i], -i))
        lines.append(f"ジョブの所要の合計: {sum(whole.values()):.0f} 秒・最も長いジョブ: {longest}（{whole[longest]:.0f} 秒）")
    return "\n".join(lines)


def cmd_run(args: argparse.Namespace, gh: Gh, cfg: dict[str, int | float]) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        try:
            listed = fetch_run(gh, args.run_id, Path(tmp))
        except (GhError, KeyError, TypeError) as e:
            sys.stderr.write(f"run {args.run_id} を読めない: {e}\n")
            return 2
        parts = read_parts(Path(tmp))
        absent = [ABSENT[kind] for kind in KINDS if not any(p.kind == kind and not p.error for p in parts.values())]
        if absent:
            sys.stderr.write("".join(f"{line}\n" for line in absent))
            return 1
        total, jobs = shard_jobs(listed)
        print(render_run(parts, match_attempts(parts, total, jobs), jobs, args.top or int(cfg["top"])))
    return 0


def _deadline() -> float:
    try:
        started = float(int(os.environ[STARTED_ENV]))
    except (KeyError, ValueError):
        started = time.time()
    return started + JOB_BUDGET - UPLOAD_RESERVE


def cmd_trend(args: argparse.Namespace, gh: Gh, cfg: dict[str, int | float]) -> int:
    current, unreadable = current_total(Path(args.xml_dir), gh, args.run, args.attempt)
    search = judged = None
    if current["valid"]:
        need = int(cfg["runs"]) - 1
        totals = [current["total_seconds"]]
        if need > 0:
            search = past_totals(gh, args.run_number, need, datetime.now(timezone.utc))
            totals += [t for _, t in search.totals]
        if len(totals) >= cfg["runs"]:
            judged = verdict(totals, cfg)
    try:
        Path(args.out).write_text(json.dumps(current, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    except OSError as e:
        sys.stderr.write(f"{args.out} を書けない: {e}\n")
        unreadable = [*unreadable, f"集計を書けない（{e}）"]
    text, notes, md = render_trend(current, unreadable, search, judged, gh.calls)
    print(text)
    for note in notes:
        print(note)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(md)
    return 0


def main(argv: list[str] | None = None, gh: Gh | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="verb", required=True)
    r = sub.add_parser("run")
    r.add_argument("run_id", type=int)
    r.add_argument("--top", type=int)
    t = sub.add_parser("trend")
    t.add_argument("xml_dir")
    t.add_argument("--run", type=int, required=True)
    t.add_argument("--run-number", type=int, required=True)
    t.add_argument("--attempt", type=int, required=True)
    t.add_argument("--out", required=True)
    a = p.parse_args(argv)
    try:
        cfg = read_config(CONFIG)
    except ConfigError as e:
        sys.stderr.write(f"{e}\n")
        return 2
    if a.verb == "run":
        return cmd_run(a, gh or Gh(), cfg)
    return cmd_trend(a, gh or Gh(deadline=_deadline()), cfg)


if __name__ == "__main__":
    sys.exit(main())
