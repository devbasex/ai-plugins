"""`ndf project` の測定の 3 モジュール（リポジトリ・CI・ブランチ運用）の振る舞い（#3569）。"""

from __future__ import annotations

import subprocess
import time
from collections import Counter
from pathlib import Path

import pytest

from ndf.domains.project import measure_ci, measure_flow, measure_repo


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    files = {
        "composer.json": '{"require": {"php": "^8.3", "laravel/framework": "^10.10"}, "require-dev": {"phpunit/phpunit": "^10"}}',
        "app/a.php": "<?php\n",
        "app/b.php": "<?php\n",
        "compose.yml": "services:\n  app:\n    image: php:8.3\n  db:\n    image: mysql:8\n",
        "phpunit.xml": "<phpunit/>\n",
        ".env": "TOKEN=ghp_abcdefghijklmnopqrstuvwxyz\n",
        "AGENTS.md": "テストは docker compose exec app phpunit で打つ\n@docs/guide.md\n",
        "docs/guide.md": "<!-- NDF_PLUGIN_GUIDE_START -->\n",
        ".github/workflows/ci.yml": "on: push\njobs:\n  test:  # テスト\n    runs-on: x\n  lint:\n    runs-on: x\n",
    }
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text, encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "init")
    return path


def _tree(repo: Path, seconds: float = 30) -> measure_repo.Tree:
    return measure_repo.Tree(repo, time.monotonic() + seconds)


def test_tree_skips_secret_files_and_masks_values(repo):
    tree = _tree(repo)
    assert tree.skipped == [".env"]
    assert tree.read(".env") is None
    assert measure_repo.mask({"a": ["ok", "key=ghp_abcdefghijklmnopqrstuvwxyz"]}) == {"a": ["ok", measure_repo.MASK]}
    assert measure_repo.has_secret({"x": "AKIAABCDEFGHIJKLMNOP"})
    assert not measure_repo.has_secret({"x": "PASSWORD=testpassword"})


def test_tree_past_deadline_raises_time_up(repo):
    with pytest.raises(measure_repo.TimeUp):
        _tree(repo, -1).read("AGENTS.md")


def test_repo_items(repo):
    tree = _tree(repo)
    deps = measure_repo.dependencies(tree)
    langs = measure_repo.measure_languages(tree, deps)["value"]
    assert langs[0] == {"name": "php", "frameworks": ["laravel 10"], "files": 2, "version": "8.3"}
    services, names = measure_repo.measure_services(tree)
    assert services["value"] == {"container": True, "compose_files": ["compose.yml"], "databases": ["mysql"]}
    assert names == ["app", "db"]
    test = measure_repo.measure_test(tree, deps, names)
    assert test["status"] == "question"
    assert test["candidates"]["runners"][0]["runner"] == "phpunit"
    instructions = measure_repo.measure_instructions(tree)["value"]
    assert instructions["imports"] == [{"from": "AGENTS.md", "to": "docs/guide.md"}]
    assert "NDF_PLUGIN_GUIDE_START" in instructions["notes"][0]


def test_measure_marks_failed_items_unknown(repo):
    items: dict = {}

    def step(key, fn):
        if key == "services":
            items[key] = measure_repo.unknown("測れない")
            return None
        return fn()

    issues = measure_repo.measure(_tree(repo), step, items)
    assert items["services"]["status"] == "unknown"
    assert items["test"]["candidates"]["compose_services"] == []
    assert issues == {"markdown_files": 0, "templates": [], "hosts": []}


def test_compose_services_reads_names_and_images():
    text = "version: '3'\nservices:\n  # comment\n  web:\n    image: 'nginx'\n  redis:\n    build: .\nvolumes:\n  x:\n"
    assert measure_repo.compose_services(text) == [("web", "nginx"), ("redis", "")]


def test_ci_reading_parts(repo):
    assert measure_ci.workflow_jobs(_tree(repo)) == {".github/workflows/ci.yml": 2}
    rules = [
        {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "b"}, {"context": "a"}]}},
        {"type": "x"},
    ]
    assert measure_ci.required_contexts(rules) == ["a", "b"]
    assert measure_ci.junit_seconds(b'<testsuites><testsuite time="1.5"/><testsuite time="2"/></testsuites>') == 3.5
    assert measure_ci.junit_seconds(b"<not-xml") is None
    assert measure_ci.span("2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z") == 60.0


def test_ci_without_github_is_unknown(repo):
    got = measure_ci.measure(_tree(repo), None, None, time.monotonic() + 5, {"NDF_STATE_HOME": str(repo / "state")})
    assert got["ci"]["status"] == "unknown"
    assert got["test_duration"]["status"] == "unknown"
    assert measure_ci.measure_issues({"markdown_files": 2, "hosts": ["redmine.example.com"]}, got)["candidates"] == ["markdown", "redmine"]


def test_ndf_record_takes_the_median_of_run_rows(tmp_path):
    d = tmp_path / "metrics" / "o--r"
    d.mkdir(parents=True)
    rows = ['{"whole_test": {"init": 10}}', '{"kind": "init_test", "whole_test": {"init": 999}}', "broken", '{"whole_test": {"init": 30}}']
    (d / measure_ci.RECORD_NAME).write_text("\n".join(rows), encoding="utf-8")
    got = measure_ci.ndf_record("o/r", {"NDF_STATE_HOME": str(tmp_path)})
    assert got is not None and got["seconds"] == 20.0 and got["source"] == "ndf-record"


def test_flow_infer_and_table():
    prs = [{"base": "main", "head": "sprint/a"} for _ in range(3)] + [{"base": "main", "head": "design/x"} for _ in range(3)]
    got = measure_flow.infer(prs, "main", "main", ["whole"] * 3, [])
    assert got["integrate"]["value"] == "check" and got["integrate"]["confidence"] == "高"
    assert got["doc_prs"]["value"] == "base"
    assert got["start"]["value"] == "base"  # 動いているスプリントブランチが無ければ取り込み方の表が許す起点
    assert measure_flow.as_item(got, [])["status"] == "measured"
    assert measure_flow.confidence(2, 3) == "中"
    assert measure_flow.confidence(1, 2) is None
    assert "unknown" in measure_flow.infer_field(Counter(), "c", "x")


def test_flow_answer_problem():
    assert measure_flow.answer_problem({"start": "base", "integrate": "check", "doc_prs": "base", "promotion": "whole"}) is None
    assert "start" in measure_flow.answer_problem({"start": "production", "promotion": "whole"})
    assert "reviews.release" in measure_flow.answer_problem({"promotion": "whole", "reviews": {"release": {"checks": [], "diff": "none"}}})
    assert measure_flow.answer_problem({"promotion": "nope"}) is not None
    assert measure_flow.bad_answer({"unknown": "x"}) is None


def test_flow_drift_against_default_and_reviews():
    item = {"fields": {"promotion": {"value": "selected", "confidence": "高", "evidence": []}, "start": {"unknown": "x"}}}
    item["reviews"] = {"impl": {"value": {"checks": ["cross-review"], "diff": "pr"}}, "design": {"value": {"checks": [], "diff": "none"}}}
    rows = measure_flow.drift(None, item)
    assert [r["name"] for r in rows] == ["branch_flow.promotion", "branch_flow.reviews.design"]
    assert rows[0]["declared"] == "whole"
    with pytest.raises(ValueError):
        measure_flow.drift({"branch_flow": {"promotion": "nope"}}, item)


def test_pr_kind():
    assert measure_flow.pr_kind("feat/issue-1-x", "develop", "develop", "main") == "impl"
    assert measure_flow.pr_kind("sprint/a", "develop", "develop", "main") == "sprint"
    assert measure_flow.pr_kind("release/v1.0.0", "main", "develop", "main") == "release"
    assert measure_flow.pr_kind("review/release/v1.0.0-base", "main", "develop", "main") is None
    assert measure_flow.pr_kind("spec/x", "develop", "develop", "main") == "spec"
