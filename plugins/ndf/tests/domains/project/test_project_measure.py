"""`ndf project` の測定の 2 モジュール（リポジトリ・CI）の振る舞い（#3569）。"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ndf.domains.project import items as forms
from ndf.domains.project import cli, measure_ci, measure_repo, secret


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


# --- 退行（#4521 の受け入れ条件 6・7）: 残る項目（test・materials・ci）と指示書の案内は、測定を絞る前と同じ ---


def _add_commit(repo: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add")


def _measure_whole(repo: Path, monkeypatch) -> dict:
    """GitHub の origin を持つリポジトリを `measure_project` で測る。`gh` は呼ばず、必須のチェックだけを返す。"""
    _git(repo, "remote", "add", "origin", "https://github.com/o/r.git")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    monkeypatch.setattr(measure_ci.Gh, "get", lambda self, path: _RULES)
    return cli.measure_project(repo, 30)


_SECRET_NOTE = "秘密の名前のファイルを開かなかった: .env"


def _old_guide_note(where: str) -> str:
    return f"古い NDF の案内 {where}（NDF_PLUGIN_GUIDE_START）。今の NDF を指さないので、読み込みを外すか消す"


def test_regression_test_question_materials_ci_and_old_guide_note(repo, monkeypatch):
    _add_commit(
        repo,
        {
            "composer.json": '{"require": {"php": "^8.3", "sentry/sdk": "^3"}, "require-dev": {"phpunit/phpunit": "^10"}, '
            '"scripts": {"test": "phpunit"}}',
            "web/docker-compose.yml": "services:\n  web:\n    image: nginx\n  db:\n    image: mysql:8\n",
            "README.md": "# 題\n\n説明\n",
        },
    )
    got = _measure_whole(repo, monkeypatch)
    assert got["items"]["test"] == {
        "status": "question",
        "candidates": {
            "runners": [{"runner": "phpunit", "config": ["phpunit.xml"], "dependency": ["phpunit/phpunit"]}],
            "commands": ["composer.json: test = phpunit"],
            "compose_services": ["app", "db", "web"],
        },
        "evidence": [{"path": "AGENTS.md", "line": 1, "text": "テストは docker compose exec app phpunit で打つ"}],
    }
    assert got["items"]["materials"] == {
        "status": "question",
        "candidates": {
            "product": None,
            "needs": {"usage": [], "market": []},
            "seeds": {
                "libraries": [{"name": n, "ecosystem": "packagist", "alternatives": []} for n in ("phpunit/phpunit", "sentry/sdk")],
                "environment": [],
            },
            "deferred_milestones": [],
        },
        "evidence": [{"file": "README.md", "text": "# 題"}, {"file": "README.md", "text": "説明"}, {"text": "packagist: sentry/sdk"}],
    }
    assert got["items"]["ci"] == forms.measured({"required_checks": ["pytest"]})
    assert got["notes"] == [_old_guide_note("docs/guide.md を AGENTS.md が読み込んでいる"), _SECRET_NOTE]


def test_regression_measured_test_and_missing_instructions_note(repo, monkeypatch):
    for name in ("AGENTS.md", "composer.json", "phpunit.xml"):
        _git(repo, "rm", "-q", name)
    _git(repo, "commit", "-qm", "rm")
    got = _measure_whole(repo, monkeypatch)
    assert got["items"]["test"] == forms.measured({"suites": []})
    assert got["notes"] == ["指示書（AGENTS.md・CLAUDE.md など）が無い。NDF と各ランタイムが読む指示書を置くとよい", _SECRET_NOTE]


def test_regression_old_guide_in_an_instruction_file_note(repo, monkeypatch):
    _add_commit(repo, {"AGENTS.md": "<!-- NDF_PLUGIN_GUIDE_START -->\n"})
    assert _measure_whole(repo, monkeypatch)["notes"] == [_old_guide_note("AGENTS.md が指示書にある"), _SECRET_NOTE]


@pytest.mark.parametrize("agents", ["@docs/guide.md\n", None])
def test_regression_write_prints_the_instruction_notes(repo, tmp_path_factory, capsys, agents):
    if agents is None:
        _git(repo, "rm", "-q", "AGENTS.md")
        _git(repo, "commit", "-qm", "rm")
    else:
        _add_commit(repo, {"AGENTS.md": agents + "テストの話\n"})
    out = tmp_path_factory.mktemp("out") / "m.json"
    assert cli.main(["measure", "--root", str(repo), "--out", str(out)]) == 0
    capsys.readouterr()
    assert cli.main(["write", "--root", str(repo), "--measure", str(out), "--dry-run"]) == 0
    expected = (
        _old_guide_note("docs/guide.md を AGENTS.md が読み込んでいる")
        if agents
        else "指示書（AGENTS.md・CLAUDE.md など）が無い。NDF と各ランタイムが読む指示書を置くとよい"
    )
    assert f"案内: {expected}" in capsys.readouterr().out.splitlines()


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


def test_measure_puts_only_test_and_materials_and_returns_the_instruction_notes(repo):
    items: dict = {}
    notes = measure_repo.measure(_tree(repo), lambda key, fn: fn(), items)
    assert list(items) == ["test", "materials"]
    assert items["test"]["candidates"]["compose_services"] == ["app", "db"]
    assert len(notes) == 1 and "NDF_PLUGIN_GUIDE_START" in notes[0]


@pytest.mark.parametrize("failed", ["dependencies", "services", "instructions"])
def test_measure_keeps_measuring_after_a_failed_step(repo, failed):
    items: dict = {}

    def step(key, fn):
        return None if key == failed else fn()

    notes = measure_repo.measure(_tree(repo), step, items)
    assert list(items) == ["test", "materials"]
    assert items["test"]["candidates"]["compose_services"] == ([] if failed == "services" else ["app", "db"])
    assert (notes == []) == (failed == "instructions")


def test_service_names_are_sorted_and_unique_and_skip_unreadable_files(tmp_path):
    files = {
        "compose.yml": "services:\n  web:\n    image: x\n  db:\n    image: y\n",
        "sub/docker-compose.yml": "services:\n  db:\n  app:\n",
        "docker-compose.big.yml": "services:\n  big:\n" + "#" * measure_repo.MAX_READ + "\n",
    }
    assert measure_repo.service_names(_tree(_small_repo(tmp_path, files))) == ["app", "db", "web"]


def test_service_names_without_compose_files_is_empty(tmp_path):
    assert measure_repo.service_names(_tree(_small_repo(tmp_path, {"a.txt": "a\n"}))) == []


def test_compose_services_reads_only_the_names():
    text = "version: '3'\nservices:\n  # comment\n  web:\n    image: 'nginx'\n  redis:\n    build: .\nvolumes:\n  x:\n"
    assert measure_repo.compose_services(text) == ["web", "redis"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 空行・行頭のコメントの行（字下げの有無を問わず）は飛ばす。行末のコメントは落とさないため、`web:  # …` は名前にならない
        (
            'services:\n\n  # 先頭のコメント\n  web:  # 行末のコメント\n    image: nginx  # バージョン\n  "db":\n    image: "postgres:16"\n'
            "#root comment\n  cache:\n    image: redis\n",
            ["db", "cache"],
        ),
        # `services:` の行末の空白は無視する。次の根の行で終わる
        ("version: 3\nservices:   \n  a:\n    image: x\nvolumes:\n  data:\n", ["a"]),
        ("x:\n  services:\n    nope:\n", []),
        ("services: # コメント\n  a:\n", []),
        # 最初の行の字下げを直下とし、値を持つ行は名前にしない
        ("services:\n    deep:\n      image: i\n    two: {}\n", ["deep"]),
        ("", []),
    ],
)
def test_compose_services_skips_blank_and_comment_lines(text, expected):
    """現状固定（I-030）: `services:` の中の空行・コメントの行・行末のコメントの扱い。"""
    assert measure_repo.compose_services(text) == expected


def test_required_contexts_reads_the_required_status_checks():
    rules = [
        {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "b"}, {"context": "a"}]}},
        {"type": "x"},
    ]
    assert measure_ci.required_contexts(rules) == ["a", "b"]


# --- measure_ci.measure: GitHub の API は必須のチェックの 1 か所だけを呼ぶ（#4521 の受け入れ条件 5） ---

_RULES = [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "pytest"}]}}]


def _patch_gh(monkeypatch, fail: Exception | None = None) -> list[str]:
    """`Gh.get` を差し替え、呼ばれたパスを返す並びに記録する。`fail` があれば投げる。"""
    called: list[str] = []

    def fake_get(self, path):
        called.append(path)
        if fail:
            raise fail
        return _RULES

    monkeypatch.setattr(measure_ci.Gh, "get", fake_get)
    return called


def _measure(tmp_path, repo="o/r", head="main"):
    tree = SimpleNamespace(root=tmp_path)
    return measure_ci.measure(tree, repo, head, time.monotonic() + 30)


def test_measure_calls_only_the_branch_rules(tmp_path, monkeypatch):
    called = _patch_gh(monkeypatch)
    got = _measure(tmp_path)
    assert called == ["repos/o/r/rules/branches/main"]
    assert got == {"ci": forms.measured({"required_checks": ["pytest"]}), "notes": []}


def test_measure_project_calls_only_the_branch_rules(repo, monkeypatch):
    called: list[str] = []
    _git(repo, "remote", "add", "origin", "https://github.com/o/r.git")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    monkeypatch.setattr(measure_ci.Gh, "get", lambda self, path: called.append(path) or [])
    got = cli.measure_project(repo, 30)
    assert called == ["repos/o/r/rules/branches/main"]
    assert list(got["items"]) == ["test", "ci", "materials"]


def test_measure_without_head_skips_the_branch_rules_and_makes_ci_unknown(tmp_path, monkeypatch):
    called = _patch_gh(monkeypatch)
    got = _measure(tmp_path, head=None)
    assert called == []
    assert got["ci"]["status"] == "unknown"  # 空の一覧として書くと、解析が前に書いた必須のチェックを消す
    assert [n for n in got["notes"] if n.startswith("CI を測れない: リモートの既定ブランチが分からない")]


def test_measure_without_github_is_unknown_and_calls_nothing(tmp_path, monkeypatch):
    called = _patch_gh(monkeypatch)
    got = _measure(tmp_path, repo=None)
    assert called == []
    assert got["ci"]["status"] == "unknown" and got["notes"] == []


@pytest.mark.parametrize("reason", [measure_ci.TIME_UP, "gh が失敗した: 401"])
def test_measure_failure_on_the_branch_rules_makes_ci_unknown(tmp_path, monkeypatch, reason):
    _patch_gh(monkeypatch, measure_ci.GhUnavailable(reason))
    got = _measure(tmp_path)
    assert got == {"ci": forms.unknown(reason), "notes": [f"CI を測れない: {reason}"]}


# --- measure_repo.measure_materials ---


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


# --- 現状固定: measure_repo.dependencies（I-021。言語ごとの依存の定義の読み方と処理系のバージョンの行） ---


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
