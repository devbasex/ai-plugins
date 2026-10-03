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
