"""宣言の `test.suites[].ci_jobs` と `test.ci_exempt` の形（#464 の I1・I2）。

`project-decl.py check` は宣言を `model.validate_decl` で読み、形に合わなければ落とす。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))

import schema  # noqa: E402
from project_lib import model  # noqa: E402

SUITE = {"name": "lint", "runner": "check-lint", "kind": "lint", "command": "bash scripts/check-lint.sh"}


def decl_test(**extra) -> dict:
    return {"suites": [SUITE], **extra}


@pytest.mark.parametrize("job", [".github/workflows/lint.yml", ".github/workflows/lint.yml#lint", "ci/a.yaml#build-1"])
def test_job_ids_of_both_forms_are_accepted(job):
    model.validate_item("test", decl_test(suites=[{**SUITE, "ci_jobs": [job]}], ci_exempt=[{"job": job, "reason": "理由"}]))


@pytest.mark.parametrize("job", ["lint", ".github/workflows/lint.yml#", ".github/workflows/lint.yml#a#b", "lint.yml#a b"])
def test_job_ids_outside_the_two_forms_are_rejected(job):
    with pytest.raises(schema.ShapeError):
        model.validate_item("test", decl_test(suites=[{**SUITE, "ci_jobs": [job]}]))
    with pytest.raises(schema.ShapeError):
        model.validate_item("test", decl_test(ci_exempt=[{"job": job, "reason": "理由"}]))


@pytest.mark.parametrize("exempt", [{"job": "a.yml"}, {"job": "a.yml", "reason": ""}, {"job": "a.yml", "reason": "  "}])
def test_exempt_needs_a_reason(exempt):
    with pytest.raises(schema.ShapeError):
        model.validate_item("test", decl_test(ci_exempt=[exempt]))


def test_declarations_without_the_keys_still_read():
    """`ci_jobs` と `ci_exempt` は省いてよい。"""
    model.validate_item("test", decl_test())


def test_this_repository_declaration_is_valid():
    model.validate_decl(json.loads((SCRIPTS.parents[2] / ".ndf" / "project.json").read_text(encoding="utf-8")))


class FakeGh:
    """`_measure_runs` が読む `Gh` の面（`repo` と `get`）だけを持つ。"""

    repo = "o/r"

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[str] = []

    def get(self, path: str):
        self.calls.append(path)
        return self.responses.get(path)


def test_measure_runs_keeps_the_current_shape(monkeypatch):
    """現状固定: workflow ごとの最新の run・job 数・step の合計・必須チェックの形。"""
    from project_lib import measure_ci

    monkeypatch.setattr(measure_ci, "_junit_of_run", lambda gh, run: None)
    run = {"id": 7, "path": ".github/workflows/ci.yml", "run_started_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:02:00Z"}
    older = {**run, "id": 6}
    step = {"name": "pytest", "started_at": "2026-01-01T00:00:10Z", "completed_at": "2026-01-01T00:01:10Z"}
    gh = FakeGh(
        {
            "repos/o/r/actions/runs?status=success&per_page=50": {"workflow_runs": [run, older]},
            "repos/o/r/actions/runs/7/jobs?per_page=100": {"jobs": [{"steps": [step]}, {"steps": []}, {"steps": []}]},
            "repos/o/r/rules/branches/main": [
                {
                    "type": "required_status_checks",
                    "parameters": {"required_status_checks": [{"context": "b"}, {"context": "a"}, {"context": "a"}]},
                }
            ],
        }
    )
    ci, found = measure_ci._measure_runs(gh, {".github/workflows/ci.yml": 2, ".github/workflows/lint.yml": 1}, "main")
    assert ci == {
        "provider": "github-actions",
        "workflows": [
            {"path": ".github/workflows/ci.yml", "jobs": 3, "wall_seconds": 120.0},
            {"path": ".github/workflows/lint.yml", "jobs": 1},
        ],
        "required_checks": ["a", "b"],
    }
    assert [d["source"] for d in found] == ["ci-steps"]
    assert found[0]["seconds"] == 60.0
    assert "repos/o/r/actions/runs/6/jobs?per_page=100" not in gh.calls


def test_measure_runs_without_runs_or_workflows_reports_none():
    """現状固定: run も workflow も無ければ provider は none で、head が無ければ必須チェックを読まない。"""
    from project_lib import measure_ci

    gh = FakeGh({})
    ci, found = measure_ci._measure_runs(gh, {}, None)
    assert ci == {"provider": "none", "workflows": [], "required_checks": []}
    assert found == []
    assert gh.calls == ["repos/o/r/actions/runs?status=success&per_page=50"]
