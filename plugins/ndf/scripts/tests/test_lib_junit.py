"""`lib/junit.py` と `lib/test_triage.py` — JUnit から落ちた ID を読み、3 つに分ける（#1334 AC6・I6・決定 4・5）。"""

from __future__ import annotations

import io
import pathlib
import shlex
import subprocess
import sys
import zipfile

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import junit  # noqa: E402
import test_strategy as ts  # noqa: E402
import test_triage  # noqa: E402

# pytest の `-o junit_family=xunit1`（`testcase` が `file` を持つ）。
PYTEST_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<testsuite errors="1" failures="1" name="pytest" skipped="0" tests="3" time="1.5">
<testcase classname="tests.sub.test_a" file="tests/sub/test_a.py" line="3" name="test_ok" time="0.001"/>
<testcase classname="tests.sub.test_a" file="tests/sub/test_a.py" line="7" name="test_bad" time="0.002"><failure message="assert 1 == 2">x</failure></testcase>
<testcase classname="tests.test_b" file="tests/test_b.py" line="1" name="test_err" time="0.003"><error message="boom">y</error></testcase>
</testsuite>
"""

# PHPUnit（`file` が絶対パス。CI とコンテナで根が違う）。
PHPUNIT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="Unit" file="/var/www/html/tests/Unit/Services/UserServiceTest.php" tests="2" failures="1" time="2.5">
    <testcase name="testFind" file="/var/www/html/tests/Unit/Services/UserServiceTest.php" class="UserServiceTest" classname="Tests.Unit.Services.UserServiceTest" time="0.5"/>
    <testcase name="testSave" file="/var/www/html/tests/Unit/Services/UserServiceTest.php" class="UserServiceTest" classname="Tests.Unit.Services.UserServiceTest" time="0.5">
      <failure type="PHPUnit\\Framework\\ExpectationFailedException">Failed asserting</failure>
    </testcase>
  </testsuite>
</testsuites>
"""

TRACKED = ["tests/sub/test_a.py", "tests/test_b.py", "tests/Unit/Services/UserServiceTest.php", "app/Services/UserService.php"]


def test_pytest_xunit1_failures_are_read_as_file_classname_name():
    assert junit.failed_ids(PYTEST_XML, TRACKED) == [
        "tests/sub/test_a.py::tests.sub.test_a::test_bad",
        "tests/test_b.py::tests.test_b::test_err",
    ]
    assert junit.total_seconds(PYTEST_XML) == 1.5


def test_phpunit_absolute_paths_are_mapped_to_the_tracked_file():
    assert junit.failed_ids(PHPUNIT_XML, TRACKED) == [
        "tests/Unit/Services/UserServiceTest.php::Tests.Unit.Services.UserServiceTest::testSave"
    ]
    ci_root = PHPUNIT_XML.replace(b"/var/www/html/", b"/home/runner/work/carmo/carmo/")
    assert junit.failed_ids(ci_root, TRACKED) == junit.failed_ids(PHPUNIT_XML, TRACKED)
    assert junit.total_seconds(PHPUNIT_XML) == 2.5


def test_an_unmatched_absolute_path_is_dropped_and_bad_xml_is_none():
    assert junit.failed_ids(PHPUNIT_XML, ["other.php"]) == []
    assert junit.failed_ids(b"<not xml", TRACKED) is None
    assert junit.relative_file("/a/b/tests/test_b.py", TRACKED) == "tests/test_b.py"
    assert junit.relative_file("./tests/test_b.py", TRACKED) == "tests/test_b.py"
    assert junit.relative_file("tests/nope.py", TRACKED) is None


def test_ci_artifacts_are_picked_by_glob_or_by_the_default_name_rule():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("junit.xml", PHPUNIT_XML)
        z.writestr("readme.txt", "x")
    payload = buf.getvalue()
    arts = {
        "artifacts": [
            {"id": 1, "name": "junit-dlite-3", "size_in_bytes": 10},
            {"id": 2, "name": "coverage", "size_in_bytes": 10},
            {"id": 3, "name": "junit-old", "expired": True},
        ]
    }
    calls = []

    def get(path):
        calls.append(path)
        return arts

    def raw(path):
        calls.append(path)
        return payload

    assert junit.artifact_xmls(get, raw, "acme/demo", 7, "junit-*") == [PHPUNIT_XML]
    assert calls == ["repos/acme/demo/actions/runs/7/artifacts?per_page=100", "repos/acme/demo/actions/artifacts/1/zip"]
    assert junit.artifact_xmls(get, raw, "acme/demo", 7, None) == [PHPUNIT_XML]


def _check_run(run_id, name, conclusion, stamp):
    url = f"https://github.com/acme/demo/actions/runs/{run_id}/job/{run_id}0"
    return {"id": run_id, "name": name, "status": "completed", "conclusion": conclusion, "completed_at": stamp, "details_url": url}


def test_ci_junit_is_read_from_the_latest_failed_run_of_every_failed_check(monkeypatch):
    """判定と同じ最新の run から読む。再実行の前の古い run と、成功したチェックの run は読まない。"""
    read: list = []
    monkeypatch.setattr(junit, "artifact_xmls", lambda get, raw, repo, run_id, glob: read.append(run_id) or [run_id.encode()])
    runs = [
        _check_run(1, "unit", "failure", "2026-01-01T00:00:00Z"),  # 再実行の前
        _check_run(2, "unit", "failure", "2026-01-01T01:00:00Z"),
        _check_run(3, "e2e", "failure", "2026-01-01T01:00:00Z"),
        _check_run(4, "lint", "failure", "2026-01-01T00:00:00Z"),
        _check_run(5, "lint", "success", "2026-01-01T01:00:00Z"),  # 再実行で通った
    ]

    got = test_triage.ci_junit_xmls("acme/demo", "sha", ["unit", "e2e", "lint"], None, fetch_runs=lambda: runs)

    assert read == ["2", "3"]
    assert got == [b"2", b"3"]


# ---------- 見分け（fake の実行器で 3 つに分ける） ----------


def _repo(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.st"], cwd=work, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=work, check=True)
    for rel in ("tests/sub/test_a.py", "tests/test_b.py"):
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text("", encoding="utf-8")
    (work / ".gitignore").write_text("out/\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=work, check=True)
    return work


def _strategy(prefix=""):
    return ts.Strategy(
        "local-full",
        "test.strategy",
        [ts.Suite("py", f"{prefix}pytest -q", f"{prefix}pytest -q {{paths}}", junit="out/junit.xml", paths=["."])],
    )


def _write_junit(cwd, failing):
    cases = "".join(f'<testcase classname="c" file="{f}" name="{n}"><failure/></testcase>' for f, n in failing)
    out = pathlib.Path(cwd) / "out"
    out.mkdir(exist_ok=True)
    (out / "junit.xml").write_bytes(f'<testsuite tests="{len(failing)}">{cases}</testsuite>'.encode())


def _runner(plan):
    """`plan[パス] = 落ちる名前の並び`。`{paths}` の語で走らせ直されたファイルの分だけ JUnit を書く。"""
    seen = []

    def run(command, cwd, timeout, log):
        seen.append(shlex.split(command))
        files = [w for w in shlex.split(command) if w.startswith("tests/")]
        failing = [(f, n) for f in files for n in plan.get(f, [])]
        _write_junit(cwd, failing)
        return (1 if failing else 0), False

    run.seen = seen
    return run


FAILED = ["tests/sub/test_a.py::c::test_bad", "tests/test_b.py::c::test_err"]


@pytest.mark.parametrize("prefix", ["", "env X=1 ", "uv run ", "docker compose exec -T app "])
def test_failures_are_split_into_flaky_preexisting_and_caused(tmp_path, prefix):
    """AC5・AC6 — HEAD で通る → フレーキー、着手前でも落ちる → 既存失敗、HEAD だけ落ちる → 変更起因。前置きは結果を変えない。"""
    work = _repo(tmp_path)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True).stdout.strip()
    (work / "tests" / "test_b.py").write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "change"], cwd=work, check=True)
    # test_a::test_bad は HEAD で通る（フレーキー）。test_b::test_err は HEAD でも着手前でも落ちる（既存失敗）。
    calls = {"n": 0}

    def run(command, cwd, timeout, log):
        calls["n"] += 1
        at_head = str(cwd) == str(work)
        files = [w for w in shlex.split(command) if w.startswith("tests/")]
        failing = [("tests/test_b.py", "test_err")] if "tests/test_b.py" in files else []
        if not at_head and "tests/sub/test_a.py" in files:
            failing.append(("tests/sub/test_a.py", "test_bad"))
        _write_junit(cwd, failing)
        return (1 if failing else 0), False

    out = test_triage.classify(
        work=str(work),
        strategy=_strategy(prefix),
        failed=FAILED,
        fallback_reason=None,
        base_sha=base,
        timeout=30,
        log_dir=tmp_path / "logs",
        run=run,
    )
    assert out["flaky"] == ["tests/sub/test_a.py::c::test_bad"]
    assert out["preexisting"] == ["tests/test_b.py::c::test_err"]
    assert out["caused"] == [] and out["fallback_reason"] is None
    assert not any(
        "ndf-baseline-" in line
        for line in subprocess.run(["git", "worktree", "list"], cwd=work, capture_output=True, text=True).stdout.splitlines()
    )


def test_a_failure_absent_at_the_base_is_caused_and_gets_a_rerun_command(tmp_path):
    work = _repo(tmp_path)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True).stdout.strip()

    def run(command, cwd, timeout, log):
        failing = [("tests/test_b.py", "test_err")] if str(cwd) == str(work) and "tests/test_b.py" in command else []
        _write_junit(cwd, failing)
        return (1 if failing else 0), False

    out = test_triage.classify(
        work=str(work),
        strategy=_strategy(),
        failed=FAILED,
        fallback_reason=None,
        base_sha=base,
        timeout=30,
        log_dir=tmp_path / "logs",
        run=run,
    )
    assert out["caused"] == ["tests/test_b.py::c::test_err"]
    assert out["flaky"] == ["tests/sub/test_a.py::c::test_bad"]
    assert out["rerun_commands"] == ["pytest -q tests/test_b.py"]


def test_known_existing_failures_are_preexisting_without_a_base_run(tmp_path):
    work = _repo(tmp_path)
    run = _runner({"tests/test_b.py": ["test_err"]})
    out = test_triage.classify(
        work=str(work),
        strategy=_strategy(),
        failed=FAILED,
        fallback_reason=None,
        base_sha=None,
        timeout=30,
        log_dir=tmp_path / "logs",
        existing_failures=["tests/test_b.py::c::test_err"],
        run=run,
    )
    assert out["preexisting"] == ["tests/test_b.py::c::test_err"] and out["caused"] == []
    assert len(run.seen) == 1, "着手前の HEAD が無くても、init の既存失敗に載る ID は既存失敗"


def test_without_junit_the_result_carries_the_fallback_reason(tmp_path):
    out = test_triage.classify(
        work=str(tmp_path),
        strategy=_strategy(),
        failed=None,
        fallback_reason="JUnit が無い",
        base_sha=None,
        timeout=1,
        log_dir=tmp_path / "l",
    )
    assert out["failed_tests"] is None and out["fallback_reason"] == "JUnit が無い"


def test_read_junit_deletes_a_junit_file_that_git_does_not_ignore(tmp_path):
    work = _repo(tmp_path)
    strategy = ts.Strategy("local-full", "x", [ts.Suite("py", "pytest", "pytest {paths}", junit="junit.xml")])
    (work / "junit.xml").write_bytes(PYTEST_XML)
    ids, reason = test_triage.read_junit(str(work), strategy)
    assert ids == ["tests/sub/test_a.py::tests.sub.test_a::test_bad", "tests/test_b.py::tests.test_b::test_err"] and reason is None
    assert not (work / "junit.xml").exists()
    assert test_triage.read_junit(str(work), ts.Strategy("local-full", "x", [ts.Suite("py", "pytest")]))[0] is None


def test_wait_check_waits_while_pending_and_returns_none_at_the_limit():
    answers = iter(["pending", "pending", "success"])
    got, waited, attempts = test_triage.wait_check(lambda: next(answers), 600, sleep=lambda s: None)
    assert got == "success" and attempts == 3 and waited > 0
    got, waited, attempts = test_triage.wait_check(lambda: "pending", 15, sleep=lambda s: None)
    assert got is None and attempts >= 1
    assert test_triage.wait_check(lambda: None, 15, sleep=lambda s: None)[0] is None


def test_phpunit_ci_junit_is_split_like_pytest(tmp_path):
    """AC6 — PHPUnit の CI の JUnit（絶対パス）から落ちた ID を取り、既存失敗と変更起因に分ける。手順は pytest と同じ。"""
    work = tmp_path / "carmo"
    (work / "tests" / "Unit" / "Services").mkdir(parents=True)
    (work / "tests" / "Unit" / "Services" / "UserServiceTest.php").write_text("<?php\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=work, check=True)
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "-c", "user.email=t@e.st", "-c", "user.name=t", "commit", "-qm", "init"], cwd=work, check=True)
    ci_xml = PHPUNIT_XML.replace(b"/var/www/html/", b"/home/runner/work/carmo/carmo/")
    failed, reason = test_triage.merged_failed_ids([b"<broken", ci_xml], test_triage.tracked_files(str(work)))
    assert reason is None
    assert failed == ["tests/Unit/Services/UserServiceTest.php::Tests.Unit.Services.UserServiceTest::testSave"]
    suite = ts.Suite(
        "phpunit",
        "docker compose exec -T app phpunit",
        "docker compose exec -T app phpunit {paths}",
        junit="build/junit.xml",
        paths=["tests"],
    )
    strategy = ts.Strategy("local-scoped-ci-whole", "test.strategy", [suite])
    seen = []

    def run(command, cwd, timeout, log):
        seen.append(shlex.split(command))
        return 1, False  # JUnit を書かない走らせ直しは、そのファイルの ID がまだ落ちているとみなす

    known = test_triage.classify(
        work=str(work),
        strategy=strategy,
        failed=failed,
        fallback_reason=None,
        base_sha=None,
        timeout=30,
        log_dir=tmp_path / "logs",
        existing_failures=list(failed),
        run=run,
    )
    assert known["preexisting"] == failed and known["caused"] == []
    caused = test_triage.classify(
        work=str(work),
        strategy=strategy,
        failed=failed,
        fallback_reason=None,
        base_sha=None,
        timeout=30,
        log_dir=tmp_path / "logs",
        run=run,
    )
    assert caused["caused"] == failed
    assert seen[0] == ["docker", "compose", "exec", "-T", "app", "phpunit", "tests/Unit/Services/UserServiceTest.php"]
    assert test_triage.merged_failed_ids([b"<broken"], [])[0] is None
