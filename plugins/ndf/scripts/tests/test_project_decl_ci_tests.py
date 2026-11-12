"""CI テストの宣言（`test.ci.only`）を解析が決める規則（#3056 の I1）。"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))

from project_lib import ci_tests  # noqa: E402
from project_lib.merge import value_digest  # noqa: E402

FILES = {".github/workflows/test.yml", ".github/workflows/lint.yml"}


def exists(path: str) -> bool:
    return path in FILES


def doc(*suites, ci=None) -> dict:
    test = {"suites": list(suites)}
    if ci is not None:
        test["ci"] = ci
    return {"test": test, "analysis": {"written": {}}}


def suite(jobs, kind=None) -> dict:
    s = {"name": "t", "runner": "pytest", "command": "pytest", "ci_jobs": jobs}
    if kind:
        s["kind"] = kind
    return s


def only(d):
    return (d["test"].get("ci") or {}).get("only")


def test_test_suite_with_existing_job_file_declares():
    out, line, digest = ci_tests.declare(doc(suite([".github/workflows/test.yml#unit"])), None, {}, exists)
    assert only(out) is True and digest == value_digest(True)
    assert line == "書いた"


def test_suite_without_kind_counts_as_test():
    out, _, _ = ci_tests.declare(doc(suite([".github/workflows/test.yml"])), None, {}, exists)
    assert only(out) is True


def test_lint_suite_jobs_do_not_declare():
    out, _, digest = ci_tests.declare(doc(suite([".github/workflows/lint.yml"], kind="lint"), suite([])), None, {}, exists)
    assert only(out) is None and digest is None


def test_missing_job_file_does_not_declare():
    out, _, _ = ci_tests.declare(doc(suite([".github/workflows/gone.yml#a"])), None, {}, exists)
    assert only(out) is None and "ci" not in out["test"]


def test_analysis_written_true_is_removed_when_condition_breaks():
    prev = doc(suite([".github/workflows/test.yml"]), ci={"only": True, "check": "pytest"})
    out, line, digest = ci_tests.declare(doc(suite([]), ci={"check": "pytest"}), prev, {ci_tests.KEY: value_digest(True)}, exists)
    assert only(out) is None and out["test"]["ci"] == {"check": "pytest"}
    assert line.startswith("消した") and digest is None


def test_human_false_and_human_true_are_kept():
    for value, jobs in ((False, [".github/workflows/test.yml"]), (True, [])):
        prev = doc(suite(jobs), ci={"only": value})
        out, line, digest = ci_tests.declare(doc(suite(jobs)), prev, {}, exists)
        assert only(out) is value and digest is None
        assert line.startswith("変えない")


def test_unknown_test_is_left_alone():
    d = {"test": {"unknown": "不明"}}
    out, _, digest = ci_tests.declare(d, None, {}, exists)
    assert out == d and digest is None
