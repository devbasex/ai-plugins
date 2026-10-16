"""継続的統合のジョブと宣言の突き合わせ（#464 の AC5〜AC7・I3・I4）。"""

from __future__ import annotations

import importlib

import pytest

SUITE = {"name": "lint", "runner": "check-lint", "kind": "lint", "command": "bash scripts/check-lint.sh"}
CI = {"provider": "github-actions", "workflows": [{"path": ".github/workflows/a.yml", "jobs": 2}]}


@pytest.fixture
def cov(refactor):
    return importlib.import_module("refactor_lib.ci_coverage")


def _decl(ci=CI, ci_jobs=(), ci_exempt=()):
    decl = {"test": {"suites": [{**SUITE, "ci_jobs": list(ci_jobs)}], "ci_exempt": list(ci_exempt)}}
    if ci is not None:
        decl["ci"] = ci
    return decl


@pytest.fixture
def work(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "b.yaml").write_text("jobs:\n  z:\n    runs-on: x\n", encoding="utf-8")
    (wf / "a.yml").write_text("jobs:\n  lint:\n    runs-on: x\n  build:\n    runs-on: x\n", encoding="utf-8")
    (wf / "notes.md").write_text("jobs:\n  ignored:\n", encoding="utf-8")
    return tmp_path


def test_jobs_missing_from_the_declaration_are_listed_in_path_and_job_order(cov, work):
    got = cov.compare_jobs(_decl(ci_jobs=[".github/workflows/a.yml#lint"]), work)
    assert got.as_state() == {
        "status": "compared",
        "reason": "",
        "jobs": 3,
        "undeclared": [".github/workflows/a.yml#build", ".github/workflows/b.yaml#z"],
    }
    lines = got.lines()
    assert "宣言に無いもの 2 件" in lines[0] and "継続的統合のジョブ 3 件" in lines[0]
    assert lines[1:3] == ["   - .github/workflows/a.yml#build", "   - .github/workflows/b.yaml#z"]


def test_file_ids_and_exempt_jobs_cover_every_job(cov, work):
    exempt = [{"job": ".github/workflows/b.yaml#z", "reason": "本文を見る"}]
    got = cov.compare_jobs(_decl(ci_jobs=[".github/workflows/a.yml"], ci_exempt=exempt), work)
    assert got.status == "compared" and got.undeclared == [] and got.lines() == []


def test_matching_is_by_exact_string_only(cov, work):
    """glob も接頭辞も当てない。"""
    got = cov.compare_jobs(_decl(ci_jobs=[".github/workflows/*.yml", ".github/workflows/a.yml#lin"]), work)
    assert len(got.undeclared) == 3


@pytest.mark.parametrize(
    ("ci", "reason"),
    [
        (None, "ci が無い"),
        ({"unknown": "測れない"}, "ci が無い"),
        ({**CI, "provider": "gitlab"}, "provider が github-actions でない（gitlab）"),
    ],
)
def test_other_ci_is_skipped_with_a_reason(cov, work, ci, reason):
    got = cov.compare_jobs(_decl(ci=ci), work)
    assert got.as_state() == {"status": "skipped", "reason": reason, "jobs": 0, "undeclared": []}
    assert got.lines() == [f"ℹ 継続的統合のジョブと宣言を突き合わせられませんでした（{reason}）"]


def test_an_unreadable_workflow_is_skipped_with_its_path(cov, work):
    (work / ".github" / "workflows" / "c.yml").write_bytes(b"jobs:\n  \xff\xfe:\n")
    got = cov.compare_jobs(_decl(), work)
    assert got.status == "skipped" and got.reason.startswith("ワークフローを読めない（.github/workflows/c.yml: ")


def test_a_broken_declaration_does_not_raise(cov, work):
    """突き合わせは起動を止めない（I3）。"""
    got = cov.compare_jobs({"ci": CI, "test": {"suites": "x", "ci_exempt": [1, {"job": 2}]}}, work)
    assert got.status in ("compared", "skipped")


def test_no_workflow_directory_compares_zero_jobs(cov, tmp_path):
    got = cov.compare_jobs(_decl(), tmp_path)
    assert got.as_state() == {"status": "compared", "reason": "", "jobs": 0, "undeclared": []}
