"""現状固定: 宣言のスキーマで同じ形を繰り返し書いた箇所が、文書をどう判定するか。

期待値は今のスキーマの判定である。正しさを主張しない。繰り返しを 1 つの定義へまとめても（`$ref`）、
同じ文書を同じように通す・落とすことを確かめる。スキーマの書き方（`$defs` の名前や置き場所）は固定しない。
"""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator

from ndf import identity

SKILLS = identity.ROOT / "skills"
PROJECT = SKILLS / "development-workflow" / "schemas" / "project.schema.json"
WORKTREE = SKILLS / "worktree" / "schemas" / "worktree.schema.json"


def validator(path) -> Draft202012Validator:
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def valid(v: Draft202012Validator, doc: dict) -> bool:
    return not list(v.iter_errors(doc))


# ---- project.schema.json の CI のジョブの識別子（I-015） ----
# `test.suites[].ci_jobs[]` と `test.ci_exempt[].job` が同じ形（`<ワークフロー>.yml[#<ジョブ>]`）を受ける

# NOTE: 末尾の改行 1 つは通る（Python の正規表現の `$` が末尾の改行の前でも一致する。jsonschema の判定のまま固定する）
JOBS_OK = ["ci.yml", "ci.yaml", "ci.yml#test", ".github/workflows/ci.yml#lint-fix", "a/b.c.yml#x.y", "ci.yml\n"]
JOBS_NG = ["", "ci", "ci.json", "ci.yml#", "ci.yml#a#b", "ci yml.yml", "ci.yml#te st", "#job", "ci.YML", " ci.yml"]


def project_with(suite_jobs: list | None = None, exempt: list | None = None) -> dict:
    test: dict = {"suites": [{"name": "pytest", "runner": "pytest", "command": "pytest"}]}
    if suite_jobs is not None:
        test["suites"][0]["ci_jobs"] = suite_jobs
    if exempt is not None:
        test["ci_exempt"] = exempt
    return {"version": 1, "test": test}


@pytest.mark.parametrize("job", JOBS_OK)
def test_ci_job_ids_accepted_in_both_places(job: str):
    v = validator(PROJECT)
    assert valid(v, project_with(suite_jobs=[job]))
    assert valid(v, project_with(exempt=[{"job": job, "reason": "手元で走らせない"}]))


@pytest.mark.parametrize("job", JOBS_NG)
def test_ci_job_ids_rejected_in_both_places(job: str):
    v = validator(PROJECT)
    assert not valid(v, project_with(suite_jobs=[job]))
    assert not valid(v, project_with(exempt=[{"job": job, "reason": "手元で走らせない"}]))


@pytest.mark.parametrize("job", [1, None, ["ci.yml"]])
def test_ci_job_ids_must_be_strings(job: object):
    v = validator(PROJECT)
    assert not valid(v, project_with(suite_jobs=[job]))
    assert not valid(v, project_with(exempt=[{"job": job, "reason": "r"}]))


def test_ci_jobs_list_shape():
    v = validator(PROJECT)
    assert valid(v, project_with(suite_jobs=[]))
    assert valid(v, project_with(suite_jobs=["ci.yml", "ci.yml"]))  # 重なりは落とさない
    assert valid(v, project_with())  # 書かなくてよい
    assert not valid(v, project_with(suite_jobs="ci.yml"))


@pytest.mark.parametrize(
    ("exempt", "ok"),
    [
        ({"job": "ci.yml", "reason": "r"}, True),
        ({"job": "ci.yml", "reason": ""}, False),
        ({"job": "ci.yml", "reason": "   "}, False),
        ({"job": "ci.yml"}, False),
        ({"reason": "r"}, False),
        ({"job": "ci.yml", "reason": "r", "extra": 1}, False),
    ],
)
def test_ci_exempt_entry_shape(exempt: dict, ok: bool):
    assert valid(validator(PROJECT), project_with(exempt=[exempt])) is ok
