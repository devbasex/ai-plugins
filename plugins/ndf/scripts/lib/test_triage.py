"""落ちたテストの見分け（#1334 F5）。フレーキー・既存失敗・変更起因の 3 つに分ける。

| 分類 | 見分け方 | 扱い |
| --- | --- | --- |
| フレーキー（`flaky`） | 落ちたファイルを今の HEAD で走らせ直すと通る | 取り消さない |
| 既存失敗（`preexisting`） | 着手前の HEAD（一時の detach のworktree）でも落ちる。`init` の既存失敗に載る ID も同じ | 取り消さない |
| 変更起因（`caused`） | 今の HEAD で再び落ち、着手前の HEAD では通る | 直しを試みる |

落ちた ID は JUnit からだけ読む（I6）。読めなければ `fallback_reason` を書いて全体の走らせ直しへ落とす。
**迷ったら変更起因の側へ倒す。** 走らせ直しの JUnit を読めなければ落ちたテストはすべて今も落ちているとみなし、
着手前の HEAD の JUnit を読めなければ既存失敗とはみなさない。cross-refactoring と supervise（`test-run.py`）が同じ関数を使う。
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable, Optional

import junit
import test_strategy as ts

Runner = Callable[[Any, str, int, Optional[pathlib.Path]], tuple[Optional[int], bool]]
SLEEP = time.sleep  # CI の待ちの眠り。テストが差し替える


def run_command(command: Any, cwd: str, timeout: int, log: Optional[pathlib.Path] = None) -> tuple[Optional[int], bool]:
    """語の並びはシェルを通さず、文字列はシェルで走らせる。`(終了コード, 打ち切ったか)`。"""
    sink = open(log, "wb") if log is not None else subprocess.DEVNULL
    try:
        p = subprocess.run(
            command, shell=isinstance(command, str), cwd=cwd, stdout=sink, stderr=subprocess.STDOUT, timeout=timeout, start_new_session=True
        )
        return p.returncode, False
    except subprocess.TimeoutExpired:
        return None, True
    finally:
        if sink is not subprocess.DEVNULL:
            sink.close()


def run_within(limit: float, started: float, run: Callable[[int], tuple[Optional[int], bool]]) -> tuple[Optional[int], bool]:
    """suite 群全体で 1 つの上限 `limit` 秒のうち、`started`（`time.monotonic()`）からの残りの秒で `run` を呼ぶ。

    suite ごとに上限を丸ごと渡すと、N 本の suite で上限の N 倍まで走る。残りが 1 秒未満なら走らせずに
    打ち切ったとみなす（`(None, True)`）。
    """
    left = limit - (time.monotonic() - started)
    return run(int(left)) if left >= 1 else (None, True)


def tracked_files(work: str) -> list[str]:
    """追跡ファイル（`git ls-files`）。リポジトリでない・ディレクトリが無いときは空。"""
    try:
        p = subprocess.run(["git", "ls-files", "-z"], cwd=work, capture_output=True)
    except OSError:
        return []
    return [f for f in p.stdout.decode("utf-8", "replace").split("\0") if f] if p.returncode == 0 else []


def _git(work: str, args: list[str]) -> bool:
    try:
        return subprocess.run(["git", *args], cwd=work, capture_output=True).returncode == 0
    except OSError:
        return False


def clear_junit(work: str, strategy: ts.Strategy) -> None:
    """走らせる前に JUnit の置き場を消す（前の実行の結果を読まないため）。"""
    for suite in strategy.suites:
        if suite.junit:
            try:
                (pathlib.Path(work) / suite.junit).unlink()
            except OSError:
                pass


def read_junit(work: str, strategy: ts.Strategy, tracked: Optional[list[str]] = None) -> tuple[Optional[list[str]], Optional[str]]:
    """suite の JUnit の置き場から落ちた ID を集める。1 つも読めなければ `(None, 理由)`。

    `git check-ignore` が通らない置き場は、読んだ後に消す（作業ツリーを汚さない。決定 4）。
    """
    tracked = tracked if tracked is not None else tracked_files(work)
    places = [s.junit for s in strategy.suites if s.junit]
    if not places:
        return None, "宣言の suites[].junit が無く、JUnit を読めない"
    ids: list[str] = []
    read_any = False
    for place in places:
        path = pathlib.Path(work) / place
        found = junit.read_failed_ids(path, tracked)
        if found is None:
            continue
        read_any = True
        ids.extend(i for i in found if i not in ids)
        if not _git(work, ["check-ignore", "-q", place]):
            try:
                path.unlink()
            except OSError:
                pass
    if not read_any:
        return None, f"JUnit を読めない（{', '.join(places)}）"
    return ids, None


def by_file(ids: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for value in ids:
        out.setdefault(junit.file_of(value), []).append(value)
    return out


def rerun_groups(strategy: ts.Strategy, files: list[str]) -> list[tuple[ts.Suite, list[str]]]:
    """落ちたファイルを受け持つ suite ごとに分ける。受け持つ suite の無いファイルは最初の suite へ。"""
    groups: dict[str, tuple[ts.Suite, list[str]]] = {}
    scoped = strategy.scoped_suites()
    for f in files:
        suite = ts.suite_for(strategy, f) or (scoped[0] if scoped else None)
        if suite is None:
            continue
        groups.setdefault(suite.name, (suite, []))[1].append(f)
    return list(groups.values())


def rerun_words(strategy: ts.Strategy, files: list[str]) -> list[list[str]]:
    """落ちたファイルだけを走らせ直す語の並び（suite ごとに 1 つ。`scope_words` の置き換えだけで組む）。"""
    return [ts.scope_words(str(suite.scope_command), paths) for suite, paths in rerun_groups(strategy, files)]


def failing_in(
    work: str, strategy: ts.Strategy, ids: list[str], timeout: int, log_dir: pathlib.Path, label: str, run: Runner
) -> tuple[list[str], bool]:
    """`ids` のファイルだけを `work` で走らせ直し、まだ落ちている ID と JUnit を読めたかを返す。読めなければその群の全部。"""
    still: list[str] = []
    readable = True
    tracked = tracked_files(work)
    for suite, paths in rerun_groups(strategy, list(by_file(ids))):
        clear_junit(work, strategy)
        code, timed_out = run(ts.scope_words(str(suite.scope_command), paths), work, timeout, log_dir / f"{label}-{suite.name}.log")
        if not timed_out and code == 0:
            continue
        found, _ = read_junit(work, ts.Strategy(strategy.name, strategy.source, [suite]), tracked)
        if timed_out or found is None:
            readable = False
            still.extend(i for i in ids if junit.file_of(i) in set(paths) and i not in still)
            continue
        still.extend(i for i in ids if i in found and i not in still)
    return still, readable


def failing_at(work: str, sha: str, strategy: ts.Strategy, ids: list[str], timeout: int, log_dir: pathlib.Path, run: Runner) -> list[str]:
    """着手前の HEAD（`sha`）の一時のworktreeでも落ちる ID。作れない・読めなければ空（既存失敗とみなさない）。"""
    holder = pathlib.Path(tempfile.mkdtemp(prefix="ndf-baseline-"))
    tree = holder / "tree"
    try:
        if not _git(work, ["worktree", "add", "--detach", "-q", str(tree), sha]):
            return []
        still, readable = failing_in(str(tree), strategy, ids, timeout, log_dir, "baseline", run)
        return still if readable else []
    finally:
        _git(work, ["worktree", "remove", "--force", str(tree)])
        shutil.rmtree(holder, ignore_errors=True)
        _git(work, ["worktree", "prune"])


def classify(
    *,
    work: str,
    strategy: ts.Strategy,
    failed: Optional[list[str]],
    fallback_reason: Optional[str],
    base_sha: Optional[str],
    timeout: int,
    log_dir: pathlib.Path,
    existing_failures: Optional[list[str]] = None,
    run: Runner = run_command,
) -> dict[str, Any]:
    """落ちた ID を 3 つに分ける。`failed` が `None`（JUnit を読めない）なら `fallback_reason` を持つ結果を返す。

    落とした場合の全体の走らせ直しは呼ぶ側が行う（全体テストの置き場が戦略で違うため）。
    """
    log_dir = pathlib.Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    if failed is None:
        return {
            "failed_tests": None,
            "flaky": [],
            "preexisting": [],
            "caused": [],
            "fallback_reason": fallback_reason or "JUnit を読めない",
            "baseline_head": base_sha,
        }
    known = set(existing_failures or [])
    still, _ = failing_in(work, strategy, failed, timeout, log_dir, "rerun", run)
    flaky = [i for i in failed if i not in still]
    at_base = [i for i in still if i in known]
    unknown = [i for i in still if i not in known]
    if unknown and base_sha:
        at_base += failing_at(work, base_sha, strategy, unknown, timeout, log_dir, run)
    caused = [i for i in still if i not in at_base]
    return {
        "failed_tests": list(failed),
        "flaky": flaky,
        "preexisting": [i for i in still if i in at_base],
        "caused": caused,
        "fallback_reason": None,
        "baseline_head": base_sha,
        "rerun_words": rerun_words(strategy, list(by_file(caused))) if caused else [],
    }


# ---------- CI ----------


def wait_check(
    fetch: Callable[[], Optional[str]],
    max_wait: float,
    *,
    sleep: Callable[[float], None] | None = None,
    on_wait: Callable[[float, int], None] | None = None,
) -> tuple[Optional[str], float, int]:
    """チェックの結論を `pending` の間だけ待つ（`lib/waits.py` の `wait_until`）。戻りは（結論か `None`, 待った秒, 照会の回数）。

    上限に届いた・照会に失敗した（`None`）ときは `None` を返し、呼ぶ側は通さない（fail-closed）。
    `sleep` を省けばモジュールの `SLEEP`（テストが差し替える）。
    """
    import waits

    waited = waits.wait_until(fetch, lambda v: v != "pending", max_wait=max_wait, sleep=sleep or SLEEP, on_wait=on_wait)
    value = waited.value if waited.done else None
    return (value if value != "pending" else None), float(waited.waited), int(waited.attempts)


def gh_json(path: str) -> Any:
    """`gh api --method GET <path>` の JSON。照会できなければ `None`（読む要求だけ。I5）。"""
    try:
        p = subprocess.run(["gh", "api", "--method", "GET", path], capture_output=True)
    except OSError:
        return None
    if p.returncode != 0:
        return None
    try:
        import json

        return json.loads(p.stdout.decode("utf-8") or "null")
    except ValueError:
        return None


def gh_raw(path: str) -> bytes:
    """`gh api --method GET <path>` のバイト列（成果物の zip）。取れなければ空。"""
    try:
        p = subprocess.run(["gh", "api", "--method", "GET", path], capture_output=True)
    except OSError:
        return b""
    return p.stdout if p.returncode == 0 else b""


def ci_junit_xmls(
    repo: str, sha: str, checks: list[str], name_glob: Optional[str], fetch_runs: Callable[[], Any] | None = None
) -> list[bytes]:
    """落ちたチェックの GitHub Actions の run の成果物から JUnit の本文を落とす。取れなければ空（見分けは走らせ直しへ落ちる）。

    判定（`gh_checks.check_result`）と同じく同名のチェックを最新の実行へ畳み、成功でなかったチェックの run だけを読む。
    再実行の前の古い run の成果物では分けない。落ちたチェックが複数なら、その全部の run から集める（同じ run は 1 回）。
    """
    import re

    import gh_checks
    import junit

    class _Resp:
        def __init__(self, body):
            self.body = body

    runs = (fetch_runs() if fetch_runs else gh_checks.fetch_check_runs(repo, sha, rest_get=lambda p: _Resp(gh_json(p)))) or []
    xmls: list[bytes] = []
    read: set[str] = set()
    for run in gh_checks.fold_check_runs(runs):
        if str(run.get("name") or "") not in checks or gh_checks.run_result(run) == "success":
            continue
        m = re.search(r"/actions/runs/(\d+)", str(run.get("details_url") or run.get("html_url") or ""))
        if m and m.group(1) not in read:
            read.add(m.group(1))
            xmls.extend(junit.artifact_xmls(gh_json, gh_raw, repo, m.group(1), name_glob))
    return xmls


def merged_failed_ids(xmls: list[bytes], tracked: list[str]) -> tuple[Optional[list[str]], Optional[str]]:
    """複数の JUnit の本文から落ちた ID を集める。1 つも読めなければ `(None, 理由)`。"""
    import junit

    ids: list[str] = []
    read_any = False
    for xml in xmls:
        found = junit.failed_ids(xml, tracked)
        if found is None:
            continue
        read_any = True
        ids.extend(i for i in found if i not in ids)
    return (ids, None) if read_any else (None, "CI の成果物に読める JUnit が無い")
