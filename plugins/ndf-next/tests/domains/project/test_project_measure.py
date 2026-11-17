"""`ndf project` の測定の 2 モジュール（リポジトリ・CI）の振る舞い（#3569）。"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from ndf.domains.crossrefactor import allocation
from ndf.domains.project import items as forms
from ndf.domains.project import measure_ci, measure_repo, secret


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
    (d / allocation.HISTORY_NAME).write_text("\n".join(rows), encoding="utf-8")
    got = measure_ci.ndf_record("o/r", {"NDF_STATE_HOME": str(tmp_path)})
    assert got is not None and got["seconds"] == 20.0 and got["source"] == "ndf-record"


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


def _dated_commit(repo: Path, day: int, tag: str) -> None:
    stamp = f"2026-01-{day:02d}T00:00:00Z"
    env = {"GIT_COMMITTER_DATE": stamp, "GIT_AUTHOR_DATE": stamp, "PATH": "/usr/bin:/bin"}
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", tag], check=True, env=env)
    _git(repo, "tag", tag)


def test_tree_words_and_its_readers(tmp_path):
    """現状固定（I-027）: `Tree.words` の分け方・失敗で空・締め切りで TimeUp。"""
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


# --- 現状固定: measure_repo.dependencies（I-021。言語ごとの依存の定義の読み方と処理系の版の行） ---


def test_dependencies_current_behavior_reads_each_ecosystem(tmp_path):
    files = {
        "composer.json": '{"require": {"php": "^8.2", "laravel/framework": "^11"}, "require-dev": {"phpunit/phpunit": "^10"}}',
        "web/package.json": '{"dependencies": {"react": "^18"}, "devDependencies": {"vitest": "1"}, "engines": {"node": ">=20"}}',
        "a/b/package.json": '{"dependencies": {"deep": "1"}}',  # 2 段下は読まない
        "pyproject.toml": (
            '[project]\nrequires-python = ">=3.12"\ndependencies = ["Django>=5", "requests[socks]~=2.0"]\n'
            '[project.optional-dependencies]\ndev = ["pytest==8"]\n[dependency-groups]\nlint = ["Ruff>=0.5"]\n'
            '[tool.poetry.dependencies]\nFlask = "^3"\n[tool.poetry.group.test.dependencies]\nHypothesis = "*"\n'
        ),
        "requirements.txt": "# c\n-r other.txt\nDjango==4\nnumpy>=1 ; python_version>'3'\nhttps://x/y.whl\ngit+https://a/b\n\n",
        "requirements-dev.txt": "black\n",
    }
    deps = measure_repo.dependencies(_tree(_small_repo(tmp_path, files)))
    assert list(deps) == ["php", "javascript", "python"]
    assert deps["php"] == {"php": "^8.2", "laravel/framework": "^11", "phpunit/phpunit": "^10"}
    assert deps["javascript"] == {"react": "^18", "vitest": "1", "node": ">=20"}
    assert deps["python"] == {
        "django": "Django>=5",  # pyproject.toml が requirements*.txt より先
        "requests": "requests[socks]~=2.0",
        "pytest": "pytest==8",
        "ruff": "Ruff>=0.5",
        "flask": "^3",
        "hypothesis": "*",
        "python": ">=3.12",
        "black": "black",
        "numpy": "numpy>=1 ; python_version>'3'",
    }


@pytest.mark.parametrize(
    "files",
    [{"x.txt": "1"}, {"composer.json": "{bad", "pyproject.toml": "bad = = ="}],
)
def test_dependencies_current_behavior_empty_when_missing_or_broken(tmp_path, files):
    assert measure_repo.dependencies(_tree(_small_repo(tmp_path, files))) == {"php": {}, "javascript": {}, "python": {}}


# --- 現状固定: Gh.raw と Gh.get（I-020 の前に今の振る舞いを記録する） -----------------------------------------------
# `gh` は PATH に置いた偽のスクリプトで差し替える。呼び出しの手段（subprocess）には結合しない。


def _fake_gh(tmp_path: Path, monkeypatch, script: str, mode: int = 0o755) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/sh\n" + script, encoding="utf-8")
    gh.chmod(mode)
    monkeypatch.setenv("PATH", str(bin_dir))
    return bin_dir


def test_gh_raw_current_behavior_passes_a_read_only_get_in_root(tmp_path, monkeypatch):
    log = tmp_path / "args"
    _fake_gh(tmp_path, monkeypatch, f'printf "%s\\n" "$PWD" "$@" > {log}\nprintf \'{{"a": 1}}\'\n')
    root = tmp_path / "root"
    root.mkdir()
    gh = measure_ci.Gh(root, "o/r", time.monotonic() + 10)
    assert gh.raw("repos/o/r") == b'{"a": 1}'
    assert log.read_text(encoding="utf-8").splitlines() == [str(root), "api", "--method", "GET", "repos/o/r"]
    assert gh.get("repos/o/r") == {"a": 1}


def test_gh_get_current_behavior_empty_is_none_and_broken_is_unavailable(tmp_path, monkeypatch):
    _fake_gh(tmp_path, monkeypatch, "exit 0\n")
    assert measure_ci.Gh(tmp_path, "o/r", time.monotonic() + 10).get("x") is None
    _fake_gh(tmp_path, monkeypatch, "printf 'not json'\n")
    with pytest.raises(measure_ci.GhUnavailable) as got:
        measure_ci.Gh(tmp_path, "o/r", time.monotonic() + 10).get("repos/x")
    assert str(got.value) == "gh の出力を読めない: repos/x"


def test_gh_raw_current_behavior_past_deadline_does_not_start(tmp_path, monkeypatch):
    log = tmp_path / "called"
    _fake_gh(tmp_path, monkeypatch, f"{shutil.which('touch')} {log}\n")
    with pytest.raises(measure_ci.GhUnavailable) as got:
        measure_ci.Gh(tmp_path, "o/r", time.monotonic() - 1).raw("x")
    assert str(got.value) == measure_ci.TIME_UP == "時間切れ"
    assert not log.exists()


def test_gh_raw_current_behavior_times_out_at_the_deadline(tmp_path, monkeypatch):
    _fake_gh(tmp_path, monkeypatch, f"exec {shutil.which('sleep')} 5\n")
    with pytest.raises(measure_ci.GhUnavailable) as got:
        measure_ci.Gh(tmp_path, "o/r", time.monotonic() + 0.3).raw("x")
    assert str(got.value) == "時間切れ"


def test_gh_raw_current_behavior_without_gh(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    with pytest.raises(measure_ci.GhUnavailable) as got:
        measure_ci.Gh(tmp_path, "o/r", time.monotonic() + 10).raw("x")
    assert str(got.value) == "gh が無い"


def test_gh_raw_current_behavior_gh_that_cannot_start(tmp_path, monkeypatch):
    _fake_gh(tmp_path, monkeypatch, "exit 0\n", mode=0o644)
    with pytest.raises(measure_ci.GhUnavailable) as got:
        measure_ci.Gh(tmp_path, "o/r", time.monotonic() + 10).raw("x")
    assert str(got.value).startswith("gh を起動できない: ")


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        ("printf 'first\\nlast line\\n' >&2\nexit 1\n", "gh が失敗した: last line"),
        ("exit 4\n", "gh が失敗した: 終了コード 4"),
        ("printf '  \\n' >&2\nexit 2\n", "gh が失敗した: 終了コード 2"),
        ("printf '%0300d' 0 >&2\nexit 1\n", "gh が失敗した: " + "0" * 200),
    ],
)
def test_gh_raw_current_behavior_failure_keeps_the_last_stderr_line(tmp_path, monkeypatch, script, expected):
    _fake_gh(tmp_path, monkeypatch, script)
    with pytest.raises(measure_ci.GhUnavailable) as got:
        measure_ci.Gh(tmp_path, "o/r", time.monotonic() + 10).raw("x")
    assert str(got.value) == expected
