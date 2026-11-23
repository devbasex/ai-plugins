"""CI の結果の読み取りと、落ちたチェックの分類（外部との接続の層。SF3・SF4）。

読み手は CI の状態を `gh pr checks` の文や Markdown から読まず、この関数が返す停止理由の種類だけで次の手を
決める。種類とルートは停止理由の表（`contracts/causes.json`）が持つ。

| 読んだ結果 | 種類 |
| --- | --- |
| 落ちたテストのチェック（分割ジョブ `<名前> (k/n)` を含む） | `test_failed` |
| 落ちたテスト以外のチェック | `check_failed` |
| 終わっていないチェック | `ci_pending` |
| ランナーの割り当ての待ちだけが `ci.runner-wait` を超えた・終わったのに結論の無いチェック | `infra_wait` |
| 一覧が出そろった後も現れない必須のチェック | `precondition` |
| マージが落ちた後で、落ちたチェックが無い | `not_ci` |
"""

from __future__ import annotations

import fnmatch
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple, Optional, TypeVar

from ndf.adapters import git
from ndf.adapters.github import GitHub, GitHubError, text_of
from ndf.contracts import facts, limits
from ndf.testing import ci_logs, declared, junit

T = TypeVar("T")


class State(StrEnum):
    """チェックの状態（`Check.state`）。"""

    DONE = "done"
    QUEUED = "queued"
    RUNNING = "running"
    STALE = "stale"  # 終わった時刻があるのに結論が無い


# 読んだ結果（`Reading.verdict`）。語彙・優先する順・停止理由の種類は表が持つ（members の名前は id の大文字）
VERDICTS = "ci-verdicts"
Verdict = StrEnum("Verdict", [(i.upper(), i) for i in facts.ids(VERDICTS)])
# 通過として読む結論（SKIPPED・NEUTRAL を通す）。ほかの結論で終わったチェックは落ちたと数える。表は contracts/domains/ci-outcomes.json
PASSING = frozenset(facts.ids("ci-outcomes", outcome="passed"))
# ランナーの割り当てを待つ状態（ランナーの待ちは呼ぶ側の待ちの予算に数えない。#2719）。待つ状態のうち実行中と結果の届いていない状態を除く
QUEUED_STATES = frozenset(facts.ids("ci-outcomes", outcome="pending")) - {"in_progress", "expected"}
# ファイルを決められない testcase の理由を、理由の文に並べる件数（残りは「ほか N 件」）
UNRESOLVED_SHOWN = 3
# 分割ジョブの名前の末尾（`pytest (0/2)`）
_SHARD = re.compile(r"\s*\(\d+/\d+\)$")


class Check(NamedTuple):
    """1 つのチェックの最新の実行。`state` は `queued`・`running`・`done`・`stale`（終わった時刻があるのに結論が無い）。"""

    name: str
    state: str
    conclusion: str
    job: int

    @property
    def failed(self) -> bool:
        return self.state == State.DONE and self.conclusion not in PASSING


def _state_of(status: str, conclusion: str, completed: str) -> str:
    """check run の `status`・`conclusion`・`completed_at`（小文字・空文字に揃えた値）から `Check.state` を決める。"""
    if status == "completed" or (completed and conclusion):
        return State.DONE
    if completed:
        return State.STALE
    if status in QUEUED_STATES:
        return State.QUEUED
    return State.RUNNING


def check_of(run: dict) -> Check:
    """REST の check run 1 件を読む。

    `status` が `completed` でなくても、終わった時刻と結論があれば終わったとして読む（GitHub の状態の不整合。#2690）。
    終わった時刻があり結論の無いものは `stale` にし、待ち続けない。
    """
    status, conclusion, completed = text_of(run["status"]).lower(), text_of(run["conclusion"]).lower(), text_of(run["completed_at"])
    state = _state_of(status, conclusion, completed)
    return Check(text_of(run["name"]), state, conclusion if state == State.DONE else "", int(run["id"]))


def _newness(run: dict, index: int) -> tuple:
    """新しさ（大きいほど新しい）。時刻の無い実行（まだ始まっていない再実行）は最も新しい。"""
    stamp = max(text_of(run["completed_at"]), text_of(run["started_at"]))
    return (0 if stamp else 1, stamp, int(run["id"]), index)


def latest(runs: Iterable[dict]) -> list[Check]:
    """同じ名前のチェックを最新の実行 1 件へ畳む（再実行で通ったチェックは通ったとして読む）。並びは名前の順。"""
    best: dict[str, tuple[tuple, dict]] = {}
    for i, run in enumerate(runs):
        name, rank = text_of(run["name"]), _newness(run, i)
        if name not in best or rank >= best[name][0]:
            best[name] = (rank, run)
    return [check_of(best[name][1]) for name in sorted(best)]


def _unprefixed(name: str) -> str:
    """ワークフローの名前の前置き `<ワークフロー> / ` を最後の ` / ` で外した名前（空白と分割ジョブの末尾は残す）。"""
    return name.rpartition(" / ")[2]


def bare_name(name: str) -> str:
    """検証に使う名前。分割ジョブの末尾 `(k/n)` と、ワークフローの名前の前置き `<ワークフロー> / ` を外す。"""
    return _SHARD.sub("", _unprefixed(name)).strip()


def bare_names(names: Iterable[str]) -> set[str]:
    """検証に使う名前（`bare_name`）の集合。テストのチェックの名前を 1 度だけ揃えて、分類の間で使い回す。"""
    return {bare_name(n) for n in names}


def _short_name(name: str) -> str:
    """ワークフローの名前の前置き `<ワークフロー> / ` を外し、前後の空白を除いた名前（分割ジョブの末尾は保つ）。"""
    return _unprefixed(name).strip()


def required_key(name: str) -> str:
    """必須のチェックの検証のキー。分割ジョブを名指しした（`pytest (1/2)`）ものはその分割を保ち、ほかは `bare_name`。

    名指しした分割を畳むと、`pytest (0/2)` だけが通った時点で `pytest (1/2)` も揃ったと読んでしまう。
    """
    short = _short_name(name)
    return short if _SHARD.search(short) else bare_name(name)


def _fulfils(check_name: str, key: str) -> bool:
    """チェックが必須のキー（`required_key`）に当たるか。分割を名指ししたキーはその分割だけに当たる。"""
    return _short_name(check_name) == key if _SHARD.search(key) else bare_name(check_name) == key


def fulfils(check_name: str, required: str) -> bool:
    """チェックが必須のチェック `required` に当たるか（`read` と同じ検証。発行元の検証もこれで揃える。#3506）。"""
    return _fulfils(check_name, required_key(required))


def is_test_check(name: str, tests: Iterable[str]) -> bool:
    """チェックがテストのチェックか。名前の一致か、分割ジョブ（`pytest (1/2)`）の一致で数える（#3153）。"""
    return bare_name(name) in bare_names(tests)


def _partition(items: Iterable[T], name_of: Callable[[T], str], test_names: set[str]) -> tuple[list[T], list[T]]:
    """(テストのチェック, テスト以外) に 1 回の走査で分ける。並びと重なりは保つ。"""
    hits: list[T] = []
    others: list[T] = []
    for item in items:
        (hits if bare_name(name_of(item)) in test_names else others).append(item)
    return hits, others


class Reading(NamedTuple):
    """必須のチェックを 1 回読んだ結果。"""

    failed_tests: list[Check]
    failed_other: list[Check]
    running: list[str]
    queued: list[str]
    stale: list[str]
    missing: list[str]
    settled: bool

    @property
    def verdict(self) -> str:
        """`test_failed`・`check_failed`・`pending`・`missing`・`stale`・`passed` のどれか（先の方を優先する）。"""
        order = [
            (Verdict.TEST_FAILED, self.failed_tests),
            (Verdict.CHECK_FAILED, self.failed_other),
            (Verdict.PENDING, self.running or self.queued or (self.missing and not self.settled)),
            (Verdict.MISSING, self.missing),
            (Verdict.STALE, self.stale),
        ]
        return next((name for name, hit in order if hit), Verdict.PASSED)

    @property
    def only_queued(self) -> bool:
        """終わっていないチェックが、すべてランナーの割り当てを待っている。"""
        return bool(self.queued) and not self.running


def _match_required(checks: list[Check], wanted: set[str]) -> tuple[list[Check], set[str]]:
    """(必須のキーのどれかに当たるチェック, 当たるチェックのあった必須のキー)。検証は `_fulfils`。"""
    mine = [c for c in checks if any(_fulfils(c.name, w) for w in wanted)]
    present = {w for w in wanted if any(_fulfils(c.name, w) for c in mine)}
    return mine, present


def read(runs: Iterable[dict], required: Iterable[str], tests: Iterable[str]) -> Reading:
    """必須のチェック（分割ジョブを含む）の今の状態を読む。

    一覧にまだ現れない必須のチェックは、ほかのチェックが終わるまでは push の直後として待ち（`pending`）、
    一覧のチェックがすべて終わった後も現れなければ `missing` にする（#2317）。
    """
    checks, test_names = latest(runs), bare_names(tests)
    wanted = {required_key(r) for r in required}
    mine, present = _match_required(checks, wanted)
    failed_tests, failed_other = _partition((c for c in mine if c.failed), lambda c: c.name, test_names)
    return Reading(
        failed_tests=failed_tests,
        failed_other=failed_other,
        running=[c.name for c in mine if c.state == State.RUNNING],
        queued=[c.name for c in mine if c.state == State.QUEUED],
        stale=[c.name for c in mine if c.state == State.STALE],
        missing=sorted(wanted - present),
        settled=bool(checks) and all(c.state in (State.DONE, State.STALE) for c in checks),
    )


def split_failed(names: Iterable[str], tests: Iterable[str]) -> tuple[list[str], list[str]]:
    """落ちたチェックの名前を (テストのチェック, テスト以外) に分ける（SF4）。"""
    return _partition(names, lambda n: n, bare_names(tests))


class Classifier(NamedTuple):
    """読んだ結果を停止理由の種類へ分ける前提。`tests` はテストのチェックの名前、`merge_failed` はマージの段階が
    落ちたと読んだチェックの名前（マージの後の判定だけが渡す）。"""

    tests: Iterable[str]
    merge_failed: Optional[Iterable[str]] = None

    def cause(self, reading: Reading) -> Optional[str]:
        """読んだ結果の停止理由の種類。通っていれば None。

        マージの段階がテストのチェックの落ちを読んでいれば、読み直しでまとめのチェックが終わっていなくても
        `test_failed` にする（#3153）。マージの後で落ちたチェックがどこにも無ければ `not_ci`（CI 以外の理由で断られた）。
        """
        after_merge = self.merge_failed is not None
        merged_tests, merged_other = split_failed(self.merge_failed if self.merge_failed is not None else [], self.tests)
        if merged_tests:
            return "test_failed"
        verdict = reading.verdict
        if merged_other and verdict in (Verdict.PENDING, Verdict.PASSED):
            return "check_failed"
        if verdict == Verdict.PASSED:
            return "not_ci" if after_merge else None
        return str(facts.value(VERDICTS, verdict, "cause"))


def cause_of(reading: Reading, tests: Iterable[str], merge_failed: Optional[Iterable[str]] = None) -> Optional[str]:
    """読んだ結果の停止理由の種類（`Classifier.cause`）。通っていれば None。"""
    return Classifier(tests, merge_failed).cause(reading)


def files_of(nodes: Iterable[str]) -> list[str]:
    """node id のテストのファイル（重ねない・昇順）。CI の修正が push の前に手元で打つ範囲（#3188）。"""
    return sorted({n.partition("::")[0] for n in nodes})


class ReadSource(StrEnum):
    """落ちたテストの読み元（読み取りの記録の `source`）。"""

    JUNIT = "junit"
    LOG = "log"
    NONE = "none"


@dataclass(frozen=True)
class FailedTestSource:
    """落ちたテストの組み立ての入力。

    `junit_artifacts` は JUnit の成果物の名前の glob（設定に無ければ None）、`suites` はテストの suite（`runner` と `ci_jobs`）、
    `tracked` は作業ディレクトリが追跡するファイル（根からの相対パス）。
    """

    junit_artifacts: Optional[str] = None
    suites: tuple[Mapping[str, Any], ...] = ()
    tracked: frozenset[str] = frozenset()

    @classmethod
    def from_work(cls, cwd: Path) -> FailedTestSource:
        """作業場所から根へ探した設定ファイルの glob と suite、リポジトリ全体の追跡するファイルを読む。

        設定ファイルを読めなければ glob も suite も無いとして、git を読めなければ追跡するファイルを空として続ける。
        """
        ci_section = declared.test_section(cwd).get("ci")
        glob = ci_section.get("junit_artifacts") if isinstance(ci_section, dict) else None
        try:
            listed = git.Git(cwd).run("ls-files", "-z", "--full-name", ":/")
        except git.GitError:  # git でない場所は JUnit のファイルを決めず、ログへ移る
            listed = ""
        return cls(
            junit_artifacts=glob if isinstance(glob, str) and glob else None,
            suites=tuple(declared.load_suites(cwd)),
            tracked=frozenset(p for p in listed.split("\0") if p),
        )


class FailedTestRead(NamedTuple):
    """落ちたテストのチェック 1 件の読み取りの記録（CI の結果 JSON の `metrics.failed_test_reads` の 1 要素）。

    `run_id` はジョブが属するワークフローの実行、`runner` は当たる suite の `runner`、`found` は読めた件数
    （読み元 `junit` なら実行の成果物から読めた件数）、`reason` は読めなかった理由（読めたときは空）。
    """

    check: str
    run_id: Optional[int]
    runner: Optional[str]
    source: ReadSource
    found: int
    reason: str

    @classmethod
    def unread(cls, check: str, run_id: Optional[int], runner: Optional[str], reason: str) -> FailedTestRead:
        """読めなかった記録（読み元 `none`・0 件）。"""
        return cls(check, run_id, runner, ReadSource.NONE, 0, reason)

    def as_metric(self) -> dict:
        return {
            "check": self.check,
            "run_id": self.run_id,
            "runner": self.runner,
            "source": str(self.source),
            "found": self.found,
            "reason": self.reason,
        }


class FailedTests(NamedTuple):
    """組み立ての出力。`nodes` は落ちたテスト（重ねない・昇順）、`reads` はチェックごとの記録（入力の順）、
    `job_errors` はジョブかワークフローの実行を読めなかった理由。"""

    nodes: list[str]
    reads: list[FailedTestRead]
    job_errors: list[str]


class _RunJunit(NamedTuple):
    """1 つのワークフローの実行の成果物の読み取り。`ok` は「成果物から読めた」、`fetched` は取得を試みた ZIP の本数。"""

    nodes: tuple[str, ...]
    ok: bool
    reason: str
    fetched: int


class _Run(NamedTuple):
    """実行ごとに 1 回だけ読む事実。`error` はワークフローの実行を読めなかった理由。"""

    error: str
    runner: Optional[str]
    runner_reason: str
    junit: Optional[_RunJunit]


def _workflow_file(path: str) -> str:
    """ワークフローの実行の `path`（`.github/workflows/<ファイル>@<ref>`）から `@` 以降を外す。"""
    return path.partition("@")[0]


def runner_of(workflow_file: str, suites: Sequence[Mapping[str, Any]]) -> tuple[Optional[str], str]:
    """ワークフローのファイルに `ci_jobs`（`#` より前）が当たる suite の `runner` を 1 つに決める。

    返すのは (`runner`, 理由)。当たらないか `runner` が 2 種類以上なら `runner` は None で、理由を持つ。
    """
    runners: set[str] = set()
    for suite in suites:
        jobs = suite.get("ci_jobs")
        if not isinstance(jobs, list) or not any(isinstance(j, str) and j.partition("#")[0] == workflow_file for j in jobs):
            continue
        runner = suite.get("runner")
        runners.add(runner if isinstance(runner, str) else "")
    if not runners:
        return None, f"ワークフロー {workflow_file} に当たる suite が無い（ci_jobs）"
    if len(runners) > 1:
        return None, f"ワークフロー {workflow_file} に runner の違う suite が当たる: {'・'.join(sorted(runners))}"
    only = runners.pop()
    return (only, "") if only else (None, f"ワークフロー {workflow_file} に当たる suite に runner が無い")


def _artifact_size(art: Mapping[str, Any]) -> int:
    size = art.get("size_in_bytes")
    return size if isinstance(size, int) and not isinstance(size, bool) else 0


def _read_zips(github: GitHub, hits: list[dict], tracked: frozenset[str]) -> _RunJunit:
    """当たる成果物を順に取得して読む。読めなければ `ok` 偽で理由を返す。"""
    nodes: set[str] = set()
    unresolved: list[str] = []
    fetched = 0
    for art in hits:  # noqa: NDF201 上限: 成果物は取得のスロット（github.failed-logs）の残り本数まで（呼び手が比べ済み）
        fetched += 1
        name = text_of(art.get("name"))
        try:
            for xml in junit.xml_members(github.artifact_zip(int(art["id"]))):
                got = junit.junit_failures(xml, tracked)
                nodes.update(got.nodes)
                unresolved.extend(got.unresolved)
        except (GitHubError, junit.JunitError) as exc:
            return _RunJunit((), False, f"成果物 {name} を読めない: {exc}", fetched)
    if unresolved:
        rest = f" ほか {len(unresolved) - UNRESOLVED_SHOWN} 件" if len(unresolved) > UNRESOLVED_SHOWN else ""
        return _RunJunit((), False, "; ".join(unresolved[:UNRESOLVED_SHOWN]) + rest, fetched)
    if not nodes:
        return _RunJunit((), False, "成果物に落ちたテストが無い", fetched)
    return _RunJunit(tuple(sorted(nodes)), True, "", fetched)


def _read_run_junit(github: GitHub, run_id: int, glob: str, tracked: frozenset[str], room: int) -> _RunJunit:
    """1 つのワークフローの実行の成果物から落ちたテストを読み、その実行を「成果物から読めた」とするかを決める。

    当たる成果物が残りの取得のスロット `room` を超えれば、ZIP を 1 本も取得しない（一部だけ読んでも使わない）。
    """
    try:
        arts = github.run_artifacts(run_id)
    except GitHubError as exc:
        return _RunJunit((), False, f"成果物の一覧を読めない: {exc}", 0)
    hits = [a for a in arts if fnmatch.fnmatchcase(text_of(a.get("name")), glob) and not a.get("expired")]
    if not hits:
        return _RunJunit((), False, f"JUnit の成果物（{glob}）に当たる成果物が無い", 0)
    if len(hits) > room:
        return _RunJunit((), False, f"当たる成果物 {len(hits)} 本が取得のスロットの残り {room} 本を超える（上限 github.failed-logs）", 0)
    big = [a for a in hits if _artifact_size(a) > junit.MAX_ARTIFACT_BYTES]
    if big:
        name = text_of(big[0].get("name"))
        return _RunJunit((), False, f"成果物 {name} が {_artifact_size(big[0])} バイトで上限 {junit.MAX_ARTIFACT_BYTES} を超える", 0)
    return _read_zips(github, hits, tracked)


def _joined(*reasons: str) -> str:
    return "; ".join(r for r in reasons if r)


class _Assembly:
    """`failed_tests_of` の 1 回の組み立ての途中の状態（実行ごとの読み取りと、残りの取得のスロット）。"""

    def __init__(self, github: GitHub, source: FailedTestSource, room: int) -> None:
        self.github = github
        self.source = source
        self.room = room
        self.runs: dict[int, _Run] = {}
        self.nodes: set[str] = set()
        self.job_errors: list[str] = []

    def _run(self, run_id: int) -> _Run:
        """実行の事実（ワークフローのファイル・`runner`・成果物の読み取り）を 1 回だけ読む。"""
        if run_id in self.runs:
            return self.runs[run_id]
        try:
            path = text_of(self.github.workflow_run(run_id).get("path"))
        except GitHubError as exc:
            found = _Run(str(exc), None, "", None)
        else:
            runner, why = runner_of(_workflow_file(path), self.source.suites)
            got = None
            if self.source.junit_artifacts is not None:
                got = _read_run_junit(self.github, run_id, self.source.junit_artifacts, self.source.tracked, self.room)
                self.room -= got.fetched
            found = _Run("", runner, why, got)
        self.runs[run_id] = found
        return found

    def read(self, check: Check) -> FailedTestRead:
        """チェック 1 件を、成果物を先に、ログを後に読む。"""
        try:
            job = self.github.job(check.job)
        except GitHubError as exc:
            self.job_errors.append(str(exc))
            return FailedTestRead.unread(check.name, None, None, str(exc))
        try:
            run_id = int(job["run_id"])
        except (KeyError, TypeError, ValueError):
            why = f"ジョブ {check.job} の応答に run_id が無い"
            self.job_errors.append(why)
            return FailedTestRead.unread(check.name, None, None, why)
        run = self._run(run_id)
        if run.error:
            self.job_errors.append(run.error)
            return FailedTestRead.unread(check.name, run_id, None, run.error)
        if run.junit is not None and run.junit.ok:
            self.nodes.update(run.junit.nodes)
            return FailedTestRead(check.name, run_id, run.runner, ReadSource.JUNIT, len(run.junit.nodes), "")
        return self._read_log(check, run_id, run)

    def _read_log(self, check: Check, run_id: int, run: _Run) -> FailedTestRead:
        art = run.junit.reason if run.junit is not None else ""
        if run.runner is None:
            return FailedTestRead.unread(check.name, run_id, None, _joined(art, run.runner_reason))
        parser = ci_logs.parser_of(run.runner)
        if parser is None:
            reason = _joined(art, f"runner {run.runner} のログのパーサーが無い")
            return FailedTestRead.unread(check.name, run_id, run.runner, reason)
        try:
            log_text = self.github.job_log(check.job)
        except GitHubError as exc:
            return FailedTestRead.unread(check.name, run_id, run.runner, _joined(art, f"ログを読めない: {exc}"))
        found = parser(log_text)
        self.nodes.update(found)
        return FailedTestRead(check.name, run_id, run.runner, ReadSource.LOG, len(found), art)


def failed_tests_of(github: GitHub, checks: Iterable[Check], source: FailedTestSource) -> FailedTests:
    """落ちたテストのチェックごとに、JUnit の成果物を先に、ログを後に読み、落ちたテストと読み取りの記録を組む。

    - 読むチェックと取得する ZIP の本数は、どちらも `github.failed-logs` の件数まで。超えたチェックは読み元 `none`
    - 成果物の読み元は実行ごとに決める（成果物は実行に付き、どのジョブの XML かは分からない）
    - ジョブかワークフローの実行を読めないチェックだけを読み元 `none` にし、ほかのチェックは読み続ける（`GitHubError` を外へ投げない）
    """
    cap = int(limits.value("github.failed-logs", "count"))
    listed = list(checks)
    assembly = _Assembly(github, source, cap)
    # 上限: ジョブ github.failed-logs 件まで（limits.json）
    reads = [assembly.read(c) for c in listed[:cap]]
    over = f"読むチェックの上限 github.failed-logs（{cap} 件）を超えた"
    reads += [FailedTestRead.unread(c.name, None, None, over) for c in listed[cap:]]
    return FailedTests(sorted(assembly.nodes), reads, assembly.job_errors)


@dataclass
class WatchResult:
    """待ちの結果。`runner_exhausted` はランナーの割り当ての待ちが `ci.runner-wait` を超えたこと。"""

    reading: Reading
    rounds: int
    elapsed: float
    queued_for: float
    runner_exhausted: bool = False

    def cause(self, tests: Iterable[str], merge_failed: Optional[Iterable[str]] = None) -> Optional[str]:
        return self.cause_by(Classifier(tests, merge_failed))

    def cause_by(self, classifier: Classifier) -> Optional[str]:
        """停止理由の種類。ランナーの割り当ての待ちだけで上限を超えた `ci_pending` は `infra_wait`。"""
        kind = classifier.cause(self.reading)
        return "infra_wait" if kind == "ci_pending" and self.runner_exhausted else kind


def _next_interval(previous: Optional[Reading], reading: Reading, interval: float, policy: limits.Policy) -> float:
    """この周の眠りの間隔。最初の周は最初の値、変化が無い間は前の間隔に倍率をかけて伸ばす（頭打ちは `max_interval`）。

    読んだ結果が変わった（チェックが動いた）周は、倍率 1 ステップだけ縮める（最初の値より短くしない。不足 g）。
    最初の値まで戻すと、チェックが順に終わる CI では変化のたびに 10 秒へ戻って伸びず、読み取りの回数が増える。
    """
    if previous is None:
        return float(policy.interval)
    if reading != previous:
        return max(float(policy.interval), interval / float(policy.factor))
    return min(interval * float(policy.factor), float(policy.max_interval))


@dataclass
class Watch:
    """必須のチェックの結論が出るまで読み直す。

    - 間隔は方針 `test-triage.check-wait`（最初の間隔・倍率・頭打ち）。読んだ結果が変わらない間は伸ばし、変われば
      最初の間隔へ戻す（不足 g）。待ちの合計は呼ぶ側が `budget` で渡す
    - 最後の眠りは上限までの残りに縮め、上限の時刻に最後の 1 回を読む（上限より前に打ち切らない。#2224）
    - 終わっていないチェックがすべてランナーの割り当てを待つ間は `budget` に数えず、`ci.runner-wait` で別に数える（#2719）
    - 読むたびに `on_round` へ読んだ結果と経過の秒を渡す（待つ間に何を待っているかを出す。#2317）
    - `decided` が真を返したら、結論の出ていないチェックが残っていても待たずに返す（マージの段階から引き継いだ
      失敗で停止理由が決まっているとき、読み直しに API と待ちを使わない）
    - 予算は `started`（`clock` の値）から数える。省けば作った時点。前の段階で待ち始めた呼び手は、その時刻を渡して
      同じ予算を引き継ぐ。始まりの時刻は `started` で読める
    """

    reader: Callable[[], Reading]
    budget: float
    on_round: Optional[Callable[[Reading, float], None]] = None
    decided: Optional[Callable[[Reading], bool]] = None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    started: Optional[float] = None
    _queued: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        # 省いたら作った時点から数える（`run` の前の API の呼び出しも `remaining` で同じ予算に入れる）
        if self.started is None:
            self.started = self.clock()

    def remaining(self) -> float:
        """待ちに使える残りの秒。ランナーの割り当ての待ちは予算に数えない（API の上限の待ちの締め切りに使う）。"""
        return self._remaining_at(self.clock())

    def _remaining_at(self, now: float) -> float:
        """時刻 `now` での待ちに使える残りの秒（ランナーの割り当ての待ちを予算に数えない）。"""
        return self.budget - (now - self.started - self._queued)

    def run(self) -> WatchResult:
        policy = limits.policy("test-triage.check-wait")
        runner_cap = limits.seconds("ci.runner-wait")
        interval, rounds = float(policy.interval), 0
        previous: Optional[Reading] = None
        while True:
            rounds += 1
            reading, now, done = self._round(rounds)
            if done is not None:
                return done
            interval = _next_interval(previous, reading, interval, policy)
            previous = reading
            wait = self._next_wait(reading, interval, now, runner_cap)
            if wait <= 0:
                return self._exhausted_result(reading, rounds, now, runner_cap)
            self.sleep(wait)
            if reading.only_queued:
                # 眠りは頼んだ秒より短くならない。時計の差が丸めで縮んでも、頼んだ秒は数える
                self._queued += max(self.clock() - now, wait)

    def _round(self, rounds: int) -> tuple[Reading, float, Optional[WatchResult]]:
        """1 周（`rounds` 周目）: 読み、経過の秒を `on_round` へ渡し、結論が出た（か `decided` が真）なら結果を返す。

        返すのは読んだ結果・読んだ時刻・待ちを終える結果（読み直すなら None）。
        """
        reading = self.reader()
        now = self.clock()
        elapsed = now - self.started
        if self.on_round is not None:
            self.on_round(reading, elapsed)
        if reading.verdict != Verdict.PENDING or (self.decided is not None and self.decided(reading)):
            return reading, now, WatchResult(reading, rounds, elapsed, self._queued)
        return reading, now, None

    def _exhausted_result(self, reading: Reading, rounds: int, now: float, runner_cap: float) -> WatchResult:
        """待ちが上限に達したときの結果。ランナーの割り当ての待ちだけで上限を超えたら `runner_exhausted`。"""
        exhausted = reading.only_queued and self._queued >= runner_cap
        return WatchResult(reading, rounds, now - self.started, self._queued, exhausted)

    def _next_wait(self, reading: Reading, interval: float, now: float, runner_cap: float) -> float:
        """時刻 `now` から次に眠る秒。上限までの残りに縮める。0 以下なら上限に達した。"""
        if reading.only_queued:
            return min(interval, runner_cap - self._queued)
        return min(interval, self._remaining_at(now))


def watch_head(github: GitHub, sha: str, required: list[str], tests: list[str]) -> Callable[[], Reading]:
    """コミット `sha` の必須のチェックを読む関数（`Watch.reader` に渡す）。読めなければ `GitHubError`。"""
    return lambda: read(github.check_runs(sha), required, tests)
