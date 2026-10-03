"""ワークフローの job id の読み取り（#464 決定 5）。解析の `workflow_jobs` も同じ読み方を使う。"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))

import ci_workflows  # noqa: E402
from project_lib import measure_ci  # noqa: E402

WORKFLOW = """\
name: CI
on:
  push:
    branches: [main]
# jobs: ではない行
jobs:
  # 字下げしたコメントは数えない
  lint:
    runs-on: ubuntu-latest
    steps:
      - run: echo "a:"

  "pytest-shard":
    needs: lint
    strategy:
      matrix:
        shard: [1, 2]
  'smoke':
    runs-on: ubuntu-latest
env:
  X: 1
"""


def test_job_ids_reads_keys_directly_under_jobs():
    assert ci_workflows.job_ids(WORKFLOW) == ["lint", "pytest-shard", "smoke"]


def test_job_ids_without_jobs_is_empty():
    assert ci_workflows.job_ids("name: x\non: push\n") == []
    assert ci_workflows.job_ids("") == []


class FakeTree:
    def __init__(self, files: dict[str, str]):
        self.files = list(files)
        self._files = files

    def read(self, path):
        return self._files.get(path)


def test_workflow_jobs_counts_ids_of_workflow_files_only():
    tree = FakeTree(
        {
            ".github/workflows/ci.yml": WORKFLOW,
            ".github/workflows/b.yaml": "jobs:\n  one:\n    runs-on: x\n",
            ".github/workflows/nested/c.yml": WORKFLOW,
            "docs/ci.yml": WORKFLOW,
        }
    )
    assert measure_ci.workflow_jobs(tree) == {".github/workflows/b.yaml": 1, ".github/workflows/ci.yml": 3}


def test_workflow_jobs_counts_this_repository_like_job_ids():
    """このリポジトリのワークフローで、数と job id の並びの長さが一致する。"""
    root = SCRIPTS.parents[2]
    files = {str(p.relative_to(root)): p.read_text(encoding="utf-8") for p in (root / ".github" / "workflows").glob("*.y*ml")}
    counts = measure_ci.workflow_jobs(FakeTree(files))
    assert counts and all(counts[f] == len(ci_workflows.job_ids(files[f])) > 0 for f in counts)


def test_job_ids_reads_keys_with_trailing_comments():
    text = 'jobs: # CI\n  lint: # 静的解析\n    run: echo "#x"\n  "a#b": # q\n    x: 1\n'
    assert ci_workflows.job_ids(text) == ["lint", "a#b"]
