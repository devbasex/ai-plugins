"""`ndf project` の測定の 3 モジュール（リポジトリ・CI・ブランチ運用）の振る舞い（#3569）。"""

from __future__ import annotations

import subprocess
import time
from collections import Counter
from pathlib import Path

import pytest

from ndf.domains.project import items as forms
from ndf.domains.project import measure_ci, measure_flow, measure_repo, secret


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
    assert secret.mask({"a": ["ok", "key=ghp_abcdefghijklmnopqrstuvwxyz"]}) == {"a": ["ok", secret.MASK]}
    assert secret.has_secret({"x": "AKIAABCDEFGHIJKLMNOP"})
    assert not secret.has_secret({"x": "PASSWORD=testpassword"})


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
            items[key] = forms.unknown("測れない")
            return None
        return fn()

    issues = measure_repo.measure(_tree(repo), step, items)
    assert items["services"]["status"] == "unknown"
    assert items["test"]["candidates"]["compose_services"] == []
    assert issues == {"markdown_files": 0, "templates": [], "hosts": []}


def test_compose_services_reads_names_and_images():
    text = "version: '3'\nservices:\n  # comment\n  web:\n    image: 'nginx'\n  redis:\n    build: .\nvolumes:\n  x:\n"
    assert measure_repo.compose_services(text) == [("web", "nginx"), ("redis", "")]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 空行・行頭のコメントの行（字下げの有無を問わず）は飛ばす。行末のコメントは落とさないため、`web:  # …` は名前にならない
        (
            'services:\n\n  # 先頭のコメント\n  web:  # 行末のコメント\n    image: nginx  # 版\n  "db":\n    image: "postgres:16"\n'
            "#root comment\n  cache:\n    image: redis\n",
            [("db", "postgres:16"), ("cache", "redis")],
        ),
        # `services:` の行末の空白は無視する。次の根の行で終わる
        ("version: 3\nservices:   \n  a:\n    image: x\nvolumes:\n  data:\n", [("a", "x")]),
        ("x:\n  services:\n    nope:\n", []),
        ("services: # コメント\n  a:\n", []),
        # 最初の行の字下げを直下とし、値を持つ行は名前にしない
        ("services:\n    deep:\n      image: i\n    two: {}\n", [("deep", "i")]),
        ("", []),
    ],
)
def test_compose_services_skips_blank_and_comment_lines(text, expected):
    """現状固定（I-030）: `services:` の中の空行・コメントの行・行末のコメントの扱い。"""
    assert measure_repo.compose_services(text) == expected


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


def _small_repo(tmp_path, files: dict[str, str]) -> Path:
    path = tmp_path / "small"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text, encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "init")
    return path


def test_measure_materials_libraries_analytics_and_readme_evidence(tmp_path):
    """現状固定（I-006）: 依存の言語ごとの並び・処理系の行の除外・解析の SDK・最初の README の冒頭 8 行。"""
    long_line = "x" * 250
    readme = "\n".join(["# 題", "", long_line] + [f"行{i}" for i in range(10)]) + "\n"
    path = _small_repo(tmp_path, {"README.ja.md": readme, "README.md": "# 英語\n", "docs/README.md": "# 下\n"})
    deps = {
        "php": {"php": "^8.3", "laravel/framework": "^10", "sentry/sdk": "^3"},
        "javascript": {"node": ">=20", "@datadog/browser": "1", "react": "18"},
        "python": {"python": ">=3.11", "requests": "2"},
        "go": {"x/y": "1"},
    }
    got = measure_repo.measure_materials(_tree(path), deps)
    assert got == {
        "status": "question",
        "candidates": {
            "product": None,
            "needs": {"usage": [], "market": []},
            "seeds": {
                "libraries": [
                    {"name": "laravel/framework", "ecosystem": "packagist", "alternatives": []},
                    {"name": "sentry/sdk", "ecosystem": "packagist", "alternatives": []},
                    {"name": "@datadog/browser", "ecosystem": "npm", "alternatives": []},
                    {"name": "react", "ecosystem": "npm", "alternatives": []},
                    {"name": "requests", "ecosystem": "pypi", "alternatives": []},
                    {"name": "x/y", "ecosystem": "go", "alternatives": []},
                ],
                "environment": [],
            },
            "deferred_milestones": [],
        },
        "evidence": [{"file": "README.ja.md", "text": t} for t in ["# 題", "x" * 200] + [f"行{i}" for i in range(6)]]
        + [{"text": "packagist: sentry/sdk"}, {"text": "npm: @datadog/browser"}],
    }


def test_measure_materials_caps_libraries_and_evidence(tmp_path):
    """現状固定（I-006）: ライブラリは MAX_LIBRARIES 件まで・解析の SDK は上限の後も数え、根拠は MAX_EVIDENCE 件まで。"""
    path = _small_repo(tmp_path, {"a.txt": "a\n"})
    deps = {"javascript": {f"lib{i:02d}": "1" for i in range(measure_repo.MAX_LIBRARIES)} | {f"zz-sentry-{i:02d}": "1" for i in range(25)}}
    got = measure_repo.measure_materials(_tree(path), deps)
    libraries = got["candidates"]["seeds"]["libraries"]
    assert [x["name"] for x in libraries] == [f"lib{i:02d}" for i in range(measure_repo.MAX_LIBRARIES)]
    assert got["evidence"] == [{"text": f"npm: zz-sentry-{i:02d}"} for i in range(forms.MAX_EVIDENCE)]
    empty = measure_repo.measure_materials(_tree(path), {})
    assert empty["evidence"] == [] and empty["candidates"]["seeds"]["libraries"] == []


def test_flow_drift_rows_for_fields_and_reviews():
    """現状固定（I-016）: 4 項目とレビューの単位のずれの行の形。書いたレビューの行は 1 つの値と、書いていない種類は既定値の 2 列と比べる。"""
    worktree = {"branch_flow": {**measure_flow.default_flow(), "reviews": {"impl": {"checks": [], "diff": "none"}}}}
    item = {
        "fields": {
            "start": {"value": "base"},
            "integrate": {"value": "x", "confidence": "低", "evidence": [{"path": "a"}]},
            "doc_prs": {"confidence": "高"},
            "promotion": {"value": "selected", "evidence": "not a list"},
        },
        "reviews": {
            "impl": {"value": {"checks": ["cross-review"], "diff": "pr"}, "confidence": "中"},
            "sprint": {"value": {"checks": ["cross-refactoring", "behavior"], "diff": "unchecked"}},
            "release": {"value": {"checks": ["x"], "diff": "none"}, "evidence": [1]},
            "spec": {"unknown": "測れない"},
        },
    }
    assert measure_flow.drift(worktree, item) == [
        {
            "kind": "drift",
            "name": "branch_flow.integrate",
            "declared": "check",
            "measured": "x",
            "confidence": "低",
            "evidence": [{"path": "a"}],
            "proposal": "branch_flow.integrate を x にする（同じ promotion の行が許す組み合わせに揃えてから write で書く）",
        },
        {
            "kind": "drift",
            "name": "branch_flow.promotion",
            "declared": "whole",
            "measured": "selected",
            "confidence": None,
            "evidence": [],
            "proposal": "branch_flow.promotion を selected にする（同じ promotion の行が許す組み合わせに揃えてから write で書く）",
        },
        {
            "kind": "drift",
            "name": "branch_flow.reviews.impl",
            "declared": {"checks": [], "diff": "none"},
            "measured": {"checks": ["cross-review"], "diff": "pr"},
            "confidence": "中",
            "evidence": [],
            "proposal": "branch_flow.reviews.impl を {'checks': ['cross-review'], 'diff': 'pr'} にする（成り立たない組み合わせは check が直すキーと理由を出す）",
        },
        {
            "kind": "drift",
            "name": "branch_flow.reviews.release",
            "declared": {"既定値": [{"checks": [], "diff": "none"}, {"checks": [], "diff": "none"}]},
            "measured": {"checks": ["x"], "diff": "none"},
            "confidence": None,
            "evidence": [1],
            "proposal": "branch_flow.reviews.release を {'checks': ['x'], 'diff': 'none'} にする（成り立たない組み合わせは check が直すキーと理由を出す）",
        },
    ]
    assert measure_flow.drift(None, None) == []


def _dated_commit(repo: Path, day: int, tag: str) -> None:
    stamp = f"2026-01-{day:02d}T00:00:00Z"
    env = {"GIT_COMMITTER_DATE": stamp, "GIT_AUTHOR_DATE": stamp, "PATH": "/usr/bin:/bin"}
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", tag], check=True, env=env)
    _git(repo, "tag", tag)


def test_tree_words_and_its_readers(tmp_path):
    """現状固定（I-027）: `Tree.words` の分け方・失敗で空・締め切りで TimeUp と、それを読む既定ブランチ・ブランチ・タグの測定。"""
    path = _small_repo(tmp_path, {"a.txt": "a\n"})
    _git(path, "config", "user.email", "t@example.com")
    for day, tag in ((2, "v1"), (4, "v3"), (3, "v2"), (5, "v4")):
        _dated_commit(path, day, tag)
    _git(path, "branch", "develop")
    tree = _tree(path)
    assert tree.words("tag", "--sort=-creatordate") == ["v4", "v3", "v2", "v1"]
    assert tree.words("rev-parse", "--verify", "nope") == []
    assert tree.words("for-each-ref", "--format=%(refname:short) x", "refs/heads") == ["develop", "x", "main", "x"]
    with pytest.raises(measure_repo.TimeUp):
        _tree(path, -1).words("tag")
    assert measure_repo.default_branch(tree) == "main"
    assert measure_repo.measure_delivery(tree)["candidates"]["tags"] == {"count": 4, "latest": ["v4", "v3", "v2"]}
    branches = measure_repo.measure_branches(tree)
    assert branches == {
        "status": "question",
        "candidates": {"base": ["main", "develop"], "production": ["main"]},
        "evidence": [
            {"text": "main の最後のコミット: 2026-01-05T00:00:00Z"},
            {"text": "develop の最後のコミット: 2026-01-05T00:00:00Z"},
        ],
    }
    _git(path, "branch", "-m", "main", "trunk")
    _git(path, "branch", "-D", "develop")
    tree = _tree(path)
    assert measure_repo.default_branch(tree) is None
    assert measure_repo.measure_branches(tree)["status"] == "unknown"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("on: push\njobs:\n  test:  # テスト\n    runs-on: x\n  lint:\n    runs-on: x\n", ["test", "lint"]),
        # 引用した id・コメントの中の `#`・引用の中の `#`・空行とコメントだけの行
        (
            "jobs:\n\n  # 先頭のコメント\n  \"build-a\":\n    steps:\n      - run: echo '#x'\n  'b#c':\n    runs-on: x\n",
            ["build-a", "b#c"],
        ),
        # 字下げの深い行・値を持つ行は id にしない。`jobs:` の後の根の行で終わる
        ("jobs:\n    deep:\n      x: 1\n    two: {}\n    three:\nenv:\n  other:\n", ["deep", "three"]),
        ("name: x\non:\n  jobs:\n    nope:\n", []),
        ("jobs: # コメント\n  a:\n", ["a"]),
        ("jobs:#x\n  a:\n", []),
        ("", []),
    ],
)
def test_job_ids_reads_ids_under_jobs(text, expected):
    """現状固定（I-030）: `jobs:` の直下の job id を字下げで拾う規則。"""
    assert measure_ci.job_ids(text) == expected


# --- 現状固定: measure_flow.collect_flow（I-016。標本の集め方・推した 4 項目・レビューの単位の組み立て） ---


class _FlowTree:
    """git が何も答えない木（リリースブランチの判定は None、スプリントブランチは無し）。"""

    remote = "origin"

    def git(self, *args: str):
        return None

    def refs(self, prefix: str) -> list[str]:
        return []


def _pull(n: int, head: str, base: str, merged: bool = True, sha="abc") -> dict:
    return {
        "number": n,
        "head": {"ref": head},
        "base": {"ref": base},
        "merged_at": "2026-01-01T00:00:00Z" if merged else None,
        "merge_commit_sha": sha,
    }


class _FlowGh:
    repo = "o/r"

    def __init__(self, pulls: list, marks: dict, rulesets=None, fail_marks: bool = False) -> None:
        self.pulls, self.marks, self.rulesets, self.fail_marks = pulls, marks, rulesets, fail_marks
        self.paths: list[str] = []

    def get(self, path: str):
        self.paths.append(path)
        if path.startswith("repos/o/r/pulls?"):
            return self.pulls
        if path == "repos/o/r/rulesets":
            if self.rulesets is None:
                raise measure_ci.GhUnavailable("forbidden")
            return self.rulesets
        if self.fail_marks:
            raise measure_ci.GhUnavailable("no")
        return self.marks.get(path, [])


_PR_CMD = measure_flow.PR_COMMAND
_RV_CMD = measure_flow.REVIEWS_COMMAND


def _flow_pulls() -> list:
    return [
        _pull(1, "sprint/a", "develop"),
        _pull(2, "sprint/b", "develop"),
        _pull(3, "sprint/c", "develop"),
        _pull(4, "develop", "main"),
        _pull(5, "develop", "main"),
        _pull(6, "develop", "main"),
        _pull(7, "design/x", "develop"),
        _pull(8, "design/y", "develop"),
        _pull(9, "spec/z", "develop"),
        _pull(10, "feat/issue-1", "develop", merged=False),
        "junk",
    ]


def test_collect_flow_current_behavior_measured_when_all_high():
    marks = {f"repos/o/r/pulls/{n}/reviews?per_page=100": [{"body": "## 🤖 cross-review ok"}] for n in range(1, 10)}
    gh = _FlowGh(_flow_pulls(), marks, rulesets=[{"name": "main"}])
    item = measure_flow.collect_flow(_FlowTree(), gh, "develop", "main")
    assert gh.paths[0] == "repos/o/r/pulls?state=closed&per_page=100"
    assert item["status"] == "measured"
    assert item["value"] == {"start": "base", "integrate": "check", "doc_prs": "base", "promotion": "whole"}
    f = item["fields"]
    assert f["integrate"] == {
        "value": "check",
        "confidence": "高",
        "evidence": [{"command": _PR_CMD, "text": "head が sprint/* で宛先が develop の Pull Request 3 件: check 3"}],
    }
    assert f["doc_prs"]["evidence"] == [{"command": _PR_CMD, "text": "設計 PR・確定仕様化の PR の宛先 3 件: base 3"}]
    assert f["promotion"]["evidence"] == [
        {"command": _PR_CMD + " と git rev-list --first-parent", "text": "main 宛ての Pull Request の head 3 件: whole 3"},
        {"command": "gh api repos/o/r/rulesets", "text": "ルールセット: main"},
    ]
    assert f["start"]["value"] == "base" and f["start"]["confidence"] == "高"
    assert f["start"]["evidence"][-1] == {"command": measure_flow.TABLE_COMMAND, "text": "promotion = whole が許す start は base だけ"}
    rv = item["reviews"]
    assert set(rv) == set(measure_flow.REVIEW_KINDS)
    assert rv["sprint"] == {
        "value": {"checks": ["cross-review"], "diff": "unchecked"},
        "confidence": "高",
        "evidence": [{"command": _RV_CMD, "text": "sprint の PR の印 3 件: cross-review 3"}],
    }
    assert rv["impl"] == {"unknown": "impl の PR の印の標本が無い", "evidence": [{"command": _RV_CMD, "text": "0 件"}]}
    assert rv["design"]["unknown"] == "標本が 2 件で 3 件に満たない"
    assert rv["spec"]["unknown"] == "標本が 1 件で 3 件に満たない"


def test_collect_flow_current_behavior_unreadable_marks_are_counted():
    gh = _FlowGh(_flow_pulls(), {}, rulesets=[], fail_marks=True)
    item = measure_flow.collect_flow(_FlowTree(), gh, "develop", "main")
    assert item["status"] == "measured"
    assert item["fields"]["promotion"]["evidence"][-1] == {"command": "gh api repos/o/r/rulesets", "text": "ルールセット: 無し"}
    rv = item["reviews"]
    assert rv["sprint"] == {
        "unknown": "sprint の PR の印の標本が無い",
        "evidence": [{"command": _RV_CMD, "text": "0 件"}, {"command": _RV_CMD, "text": "読めない PR 3 件"}],
    }
    assert rv["design"]["evidence"][-1] == {"command": _RV_CMD, "text": "読めない PR 2 件"}
    assert rv["impl"]["evidence"] == [{"command": _RV_CMD, "text": "0 件"}]


def test_collect_flow_current_behavior_release_and_unreadable_rulesets():
    pulls = [
        _pull(1, "release/v1.0.0", "main"),
        _pull(2, "release/v1.1.0", "main", sha=None),
        _pull(3, "review/release/v1.0.0", "release/v1.0.0", merged=False),
        _pull(4, "feat/issue-9", "develop"),
    ]
    marks = {
        "repos/o/r/pulls/3/reviews?per_page=100": [{"body": "## 🤖 cross-review"}],
        "repos/o/r/issues/4/comments?per_page=100": [{"body": "x <!-- cross-refactoring plan -->"}],
    }
    item = measure_flow.collect_flow(_FlowTree(), _FlowGh(pulls, marks), "develop", "main")
    assert item["status"] == "question"
    assert item["candidates"] == {"branch_flow": {}}
    assert item["evidence"] == []
    f = item["fields"]
    assert f["promotion"] == {
        "unknown": "main 宛ての Pull Request の headの標本が無い",
        "evidence": [
            {"command": _PR_CMD + " と git rev-list --first-parent", "text": "0 件"},
            {"command": "gh api repos/o/r/rulesets", "text": "読めない: forbidden"},
        ],
    }
    assert f["start"]["unknown"] == "本番に入っていないスプリントブランチの起点の標本が無い"
    rv = item["reviews"]
    assert rv["impl"]["evidence"] == [{"command": _RV_CMD, "text": "impl の PR の印 1 件: cross-refactoring 1"}]
    assert rv["release"] == {
        "unknown": "標本が 2 件で 3 件に満たない",
        "evidence": [{"command": _RV_CMD, "text": "release の PR の印 2 件: cross-review 1・(無し) 1"}],
    }


def test_collect_flow_current_behavior_pulls_unavailable_raises():
    class _Down:
        repo = "o/r"

        def get(self, path):
            raise measure_ci.GhUnavailable("down")

    with pytest.raises(measure_ci.GhUnavailable):
        measure_flow.collect_flow(_FlowTree(), _Down(), "develop", "main")
