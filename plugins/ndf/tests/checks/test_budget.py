"""規則 budget: テストの関数・CI のジョブ・使われない依存・置き換えられる自作の件数を数え、設定の上限と比べる。

一時のディレクトリにリポジトリとプラグインを作り、git と Ruff を起動しない（子のプロセスを持たない）。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from ndf import contracts, identity
from ndf.checks import budget, child_deps, lint, run, spec_model
from ndf.checks.model import CheckError, Scope, load_rules

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
NAME = "ndfdev"
SETTINGS_REL = f"{identity.FIXED_WORDS['ndf_settings_dir']}/project.json"


@dataclass
class Repo:
    repo: Path
    root: Path

    def write(self, rel: str, text: str) -> Path:
        """プラグインの根からの相対のファイルを書く。"""
        return _write(self.root / rel, text)

    def write_repo(self, rel: str, text: str) -> Path:
        """リポジトリの根からの相対のファイルを書く。"""
        return _write(self.repo / rel, text)

    def settings(self, data: dict) -> None:
        self.write_repo(SETTINGS_REL, json.dumps(data, ensure_ascii=False, indent=2))

    def scope(self) -> Scope:
        scope = Scope(self.repo, self.root, NAME)
        scope.sources = run.collect(self.root, self.repo)
        return scope


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def tree(tmp_path: Path) -> Repo:
    """git を起動しない一時のリポジトリとプラグイン。"""
    repo = tmp_path / "repo"
    root = repo / "plugins" / "ndf-dev"
    _write(root / ".claude-plugin" / "plugin.json", json.dumps({"name": NAME, "version": "0"}))
    _write(root / "src" / "ndf" / "__init__.py", '"""p"""\n')
    shutil.copy(PLUGIN_ROOT / "pyproject.toml", root / "pyproject.toml")
    child = {"packages": ["ndf"], "needs": [], "order": 1, "lane": "l", "closed": False}
    _write(root / child_deps.CHILDREN_REL, json.dumps({"children": {"X": child}, "tables": {}}))
    return Repo(repo, root)


@pytest.fixture
def rules() -> dict:
    return load_rules()


@pytest.fixture
def replaceable(tmp_path: Path):
    """置き換えの規則の保存先を一時のコントラクトへ差し替える。規則のファイルを書く関数を返す。"""
    dest = tmp_path / "contracts"
    shutil.copytree(PLUGIN_ROOT / "contracts" / "schemas", dest / "schemas")
    (dest / budget.REPLACEABLE).mkdir()
    contracts.use_dir(dest)

    def put(key: str, find: dict, exempt: list[str] | None = None) -> None:
        item = {"summary": f"{key} の規則", "find": find, "replace_with": "lib", "exempt": exempt if exempt is not None else []}
        _write(dest / budget.REPLACEABLE / f"{key}.json", json.dumps(item, ensure_ascii=False))

    yield put
    contracts.use_dir(None)


# --- count_tests


def test_count_tests_counts_top_level_and_class_methods(tree):
    tree.write(
        "tests/test_a.py",
        "def test_a():\n    pass\n\n\nasync def test_b():\n    pass\n\n\nclass TestC:\n    def test_c(self):\n        pass\n\n"
        "    async def test_d(self):\n        pass\n\n    def helper(self):\n        pass\n\n\ndef helper():\n    pass\n",
    )
    assert budget.count_tests(tree.scope()) == 4


def test_count_tests_skips_nested_functions(tree):
    tree.write("tests/test_a.py", "def test_a():\n    def test_inner():\n        pass\n\n    test_inner()\n")
    assert budget.count_tests(tree.scope()) == 1


def test_count_tests_skips_files_that_are_not_tests(tree):
    tree.write("src/ndf/b.py", "def test_like():\n    pass\n")
    tree.write("helpers/conftest.py", "def test_helper():\n    pass\n")
    tree.write("tests/test_a.py", "def test_a():\n    pass\n")
    assert budget.count_tests(tree.scope()) == 1


def test_count_tests_ignores_unparsable_files_removed_first(tree):
    tree.write("tests/test_bad.py", "def test_a(:\n")
    tree.write("tests/test_a.py", "def test_a():\n    pass\n")
    scope = tree.scope()
    run.unparsable(scope)
    assert budget.count_tests(scope) == 1


# --- count_jobs


def test_count_jobs_sums_two_workflows(tree):
    a = tree.write_repo(".github/workflows/a.yml", "on: push\njobs:\n  x:\n    runs-on: u\n  y:\n    runs-on: u\n")
    b = tree.write_repo(".github/workflows/b.yml", "on: push\njobs:\n  z:\n    runs-on: u\n")
    assert budget.count_jobs([a, b]) == 3


def test_count_jobs_counts_a_matrix_job_once(tree):
    text = "on: push\njobs:\n  x:\n    strategy:\n      matrix:\n        py: ['3.11', '3.13']\n    runs-on: u\n"
    assert budget.count_jobs([tree.write_repo(".github/workflows/a.yml", text)]) == 1


def test_count_jobs_without_jobs_is_zero(tree):
    assert budget.count_jobs([tree.write_repo(".github/workflows/a.yml", "on: push\n")]) == 0


def test_count_jobs_unreadable_yaml_stops(tree):
    with pytest.raises(CheckError):
        budget.count_jobs([tree.write_repo(".github/workflows/a.yml", "jobs: [\n")])


# --- unused_deps

PYPROJECT = 'requires-python = ">=3.11"\n'


def _deps(tree, deps: list[str], locked: list[str], dev: list[str] | None = None, budget_settings: dict | None = None) -> None:
    lines = "".join(f'    "{d}",\n' for d in deps)
    groups = "".join(f'    "{d}",\n' for d in dev) if dev else ""
    tree.write(
        "pyproject.toml", f'[project]\nname = "x"\n{PYPROJECT}dependencies = [\n{lines}]\n\n[dependency-groups]\ndev = [\n{groups}]\n'
    )
    tree.write("uv.lock", "version = 1\n" + "".join(f'\n[[package]]\nname = "{n}"\nversion = "1"\n' for n in locked))
    settings: dict = {"toolchain": {"plugin_pyprojects": ["plugins/ndf-dev/pyproject.toml"]}}
    if budget_settings is not None:
        settings["budget"] = budget_settings
    tree.settings(settings)


def test_unused_deps_counts_dependencies_without_imports(tree):
    _deps(tree, ["httpx==0.28.1", "tabulate==0.10.0"], ["httpx", "tabulate"])
    tree.write("src/ndf/a.py", "import httpx\n")
    assert budget.unused_deps(tree.scope()) == ["tabulate"]


def test_unused_deps_sees_imports_inside_functions(tree):
    _deps(tree, ["httpx==0.28.1", "semver==3.1.1"], ["httpx", "semver"])
    tree.write("src/ndf/a.py", "from httpx import Client\n\n\ndef f():\n    import semver\n\n    return semver, Client\n")
    assert budget.unused_deps(tree.scope()) == []


def test_unused_deps_normalizes_names(tree):
    _deps(tree, ["mdit-py-plugins==0.7.0", "Mdit_Py.Other==1"], ["mdit_py_plugins", "mdit-py-other"])
    tree.write("src/ndf/a.py", "import mdit_py_plugins\n")
    assert budget.unused_deps(tree.scope()) == ["mdit-py-other"]


def test_unused_deps_reads_import_names_from_dep_modules(tree):
    _deps(
        tree,
        ["python-dotenv==1.2.4", "markdown-it-py==4.3.0"],
        ["python-dotenv", "markdown-it-py"],
        budget_settings={"dep_modules": {"python-dotenv": "dotenv", "markdown-it-py": "markdown_it"}},
    )
    tree.write("src/ndf/a.py", "import dotenv\nfrom markdown_it import MarkdownIt\n")
    assert budget.unused_deps(tree.scope()) == []


def test_unused_deps_without_dep_modules_does_not_see_the_alias(tree):
    _deps(tree, ["python-dotenv==1.2.4"], ["python-dotenv"])
    tree.write("src/ndf/a.py", "import dotenv\n")
    assert budget.unused_deps(tree.scope()) == ["python-dotenv"]


def test_unused_deps_skips_dep_exempt(tree):
    _deps(tree, ["ruff==0.17.5"], ["ruff"], budget_settings={"dep_exempt": {"ruff": "子プロセスとして起動する"}})
    assert budget.unused_deps(tree.scope()) == []


def test_unused_deps_skips_dev_and_transitive_dependencies(tree):
    _deps(tree, [], ["pytest-xdist", "anyio"], dev=["pytest-xdist==3.8.0"])
    assert budget.unused_deps(tree.scope()) == []


def test_unused_deps_skips_dependencies_missing_from_the_lock(tree):
    _deps(tree, ["tabulate==0.10.0"], [])
    assert budget.unused_deps(tree.scope()) == []


def test_unused_deps_only_reads_imports_under_src(tree):
    _deps(tree, ["tabulate==0.10.0"], ["tabulate"])
    tree.write("tests/test_a.py", "import tabulate\n")
    assert budget.unused_deps(tree.scope()) == ["tabulate"]


def test_unused_deps_without_plugin_pyprojects_is_empty(tree):
    tree.write("pyproject.toml", '[project]\nname = "x"\ndependencies = ["tabulate"]\n')
    tree.settings({})
    assert budget.unused_deps(tree.scope()) == []


def test_unused_deps_unreadable_definition_stops(tree):
    _deps(tree, ["tabulate"], ["tabulate"])
    tree.write("pyproject.toml", "[project\n")
    with pytest.raises(CheckError):
        budget.unused_deps(tree.scope())


def test_unused_deps_unreadable_lock_stops(tree):
    _deps(tree, ["tabulate"], ["tabulate"])
    (tree.root / "uv.lock").unlink()
    with pytest.raises(CheckError):
        budget.unused_deps(tree.scope())


# --- replaceable_hits

FLOCK = {"kind": "call", "name": "fcntl.flock"}


def test_replaceable_hits_counts_matching_calls(tree, replaceable):
    replaceable("fcntl-flock", FLOCK)
    tree.write("src/ndf/a.py", "import fcntl\n\n\ndef f(fd):\n    fcntl.flock(fd, 1)\n    fcntl.flock(fd, 2)\n    fcntl.lockf(fd, 1)\n")
    hits = budget.replaceable_hits(tree.scope())
    assert [(h.path.rsplit("/", 1)[-1], h.line) for h in hits] == [("a.py", 5), ("a.py", 6)]
    assert all(h.rule == budget.RULE for h in hits)


def test_replaceable_hits_skips_the_exempt_location(tree, replaceable):
    replaceable("fcntl-flock", FLOCK, exempt=["src/ndf/hooks_like.py"])
    tree.write("src/ndf/hooks_like.py", "import fcntl\n\n\ndef f(fd):\n    fcntl.flock(fd, 1)\n")
    tree.write("src/ndf/other.py", "import fcntl\n\n\ndef f(fd):\n    fcntl.flock(fd, 1)\n")
    assert [h.path.rsplit("/", 1)[-1] for h in budget.replaceable_hits(tree.scope())] == ["other.py"]


def test_replaceable_hits_only_reads_product_code(tree, replaceable):
    replaceable("fcntl-flock", FLOCK)
    tree.write("tests/test_a.py", "import fcntl\n\n\ndef test_a(fd):\n    fcntl.flock(fd, 1)\n")
    assert budget.replaceable_hits(tree.scope()) == []


def test_replaceable_hits_counts_table_joins(tree, replaceable):
    replaceable("markdown-table", {"kind": "join", "separator": " | "})
    tree.write("src/ndf/a.py", 'def f(cells):\n    return "| " + " | ".join(cells) + " |", "|".join(cells)\n')
    assert [h.line for h in budget.replaceable_hits(tree.scope())] == [2]


def test_replaceable_hits_counts_retry_loops(tree, replaceable):
    replaceable("retry-loop", {"kind": "loop", "callee": "sleep"})
    tree.write(
        "src/ndf/a.py",
        "import time\n\n\ndef f(g):\n    for _ in range(3):\n        try:\n            return g()\n        except OSError:\n"
        "            time.sleep(1)\n\n\ndef h(g):\n    for _ in range(3):\n        time.sleep(1)\n\n\ndef k(g):\n"
        "    while True:\n        try:\n            return g()\n        except OSError:\n            pass\n",
    )
    assert [h.line for h in budget.replaceable_hits(tree.scope())] == [5]


def test_replaceable_hits_unknown_find_kind_stops(tree, replaceable):
    replaceable("odd", {"kind": "regex", "name": "x"})
    with pytest.raises(CheckError):
        budget.replaceable_hits(tree.scope())


def test_replaceable_hits_unreadable_rule_stops(tree, replaceable, tmp_path):
    replaceable("fcntl-flock", FLOCK)
    (tmp_path / "contracts" / budget.REPLACEABLE / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(CheckError):
        budget.replaceable_hits(tree.scope())


def test_replaceable_hits_rule_missing_a_key_stops(tree, replaceable, tmp_path):
    (tmp_path / "contracts" / budget.REPLACEABLE / "short.json").write_text(json.dumps({"find": FLOCK}), encoding="utf-8")
    with pytest.raises(CheckError):
        budget.replaceable_hits(tree.scope())


def test_shipped_replaceable_rules_are_readable(tree):
    contracts.use_dir(None)
    assert budget.replaceable_hits(tree.scope()) == []


# --- check


def _counts(tests: int = 0, ci_jobs: int = 0, unused: list[str] | None = None, hits: int = 0) -> budget.Counts:
    found = [budget.Finding(budget.RULE, f"plugins/x/src/ndf/m{i}.py", i + 1, "x") for i in range(hits)]
    return budget.Counts(tests, ci_jobs, unused if unused is not None else [], found)


LIMITS = {"tests": 10, "ci_jobs": 3, "unused_deps": 1, "replaceable": 2}


def test_check_within_limits_has_no_finding(tree, rules):
    tree.settings({"budget": LIMITS})
    assert budget.check(tree.scope(), rules, _counts(10, 3, ["a"], 2)) == []


@pytest.mark.parametrize(
    ("counts", "key"),
    [
        (_counts(tests=11), "tests"),
        (_counts(ci_jobs=4), "ci_jobs"),
        (_counts(unused=["a", "b"]), "unused_deps"),
        (_counts(hits=3), "replaceable"),
    ],
)
def test_check_one_over_the_limit_is_one_finding(tree, rules, counts, key):
    tree.settings({"budget": LIMITS})
    found = budget.check(tree.scope(), rules, counts)
    assert len(found) == 1
    assert found[0].rule == budget.RULE
    assert found[0].path == SETTINGS_REL
    assert key in found[0].detail


def test_check_detail_lists_the_first_items(tree, rules):
    tree.settings({"budget": {"unused_deps": 0}})
    names = [f"d{i}" for i in range(budget.DETAIL_HEAD + 2)]
    (found,) = budget.check(tree.scope(), rules, _counts(unused=names))
    assert all(n in found.detail for n in names[: budget.DETAIL_HEAD])
    assert names[budget.DETAIL_HEAD] not in found.detail


def test_check_without_budget_has_no_finding(tree, rules):
    tree.settings({})
    assert budget.check(tree.scope(), rules, _counts(10_000, 1_000, ["a"], 9)) == []


def test_check_compares_only_the_keys_present(tree, rules):
    tree.settings({"budget": {"tests": 1}})
    assert [f.detail.split(" ")[0] for f in budget.check(tree.scope(), rules, _counts(2, 99, ["a"], 9))] == ["tests"]


def test_check_limit_that_is_not_an_integer_stops(tree, rules):
    tree.settings({"budget": {"tests": "4400"}})
    with pytest.raises(CheckError):
        budget.check(tree.scope(), rules, _counts())


def test_check_measures_when_counts_are_not_given(tree, rules):
    tree.write("tests/test_a.py", "def test_a():\n    pass\n\n\ndef test_b():\n    pass\n")
    tree.settings({"budget": {"tests": 1}})
    assert len(budget.check(tree.scope(), rules)) == 1


# --- measure


def test_measure_reads_workflows_from_toolchain(tree):
    tree.write_repo(".github/workflows/a.yml", "on: push\njobs:\n  x:\n    runs-on: u\n")
    tree.write_repo(".github/other.yml", "jobs:\n  y:\n    runs-on: u\n")
    tree.settings({"toolchain": {"workflows": ".github/workflows/*.yml"}})
    assert budget.measure(tree.scope()).ci_jobs == 1


def test_measure_without_toolchain_counts_no_jobs_or_deps(tree):
    tree.write_repo(".github/workflows/a.yml", "on: push\njobs:\n  x:\n    runs-on: u\n")
    tree.settings({"budget": LIMITS})
    counts = budget.measure(tree.scope())
    assert (counts.ci_jobs, counts.unused_deps) == (0, 0)


def test_measure_as_metrics_has_the_four_counts(tree):
    tree.write("tests/test_a.py", "def test_a():\n    pass\n")
    assert budget.measure(tree.scope()).as_metrics() == {"tests": 1, "ci_jobs": 0, "unused_deps": 0, "replaceable": 0}


# --- run.py


def _schema_errors(report: dict) -> list[str]:
    """`ndf check` の結果のスキーマ（`check.result.schema.json`）の誤り。"""
    validator = spec_model.result_validator(run.TOOL)
    assert validator is not None
    return [e.message for e in validator.iter_errors(report)]


@pytest.fixture
def entry(tree, monkeypatch):
    """`ndf check` を git と Ruff を起動せずに打てるようにする。"""
    monkeypatch.setattr(identity, "ROOT", tree.root)
    monkeypatch.setattr(run, "_repo_root", lambda root: tree.repo.resolve())
    monkeypatch.setattr(lint, "run_ruff", lambda rules, paths: [])
    return tree


def test_ndf_check_reports_budget_metrics_without_findings(entry, capsys, last_result):
    entry.write("src/ndf/a.py", "def f(x):\n    return x\n")
    entry.write("tests/test_a.py", "def test_a():\n    pass\n")
    entry.settings({"budget": LIMITS})
    assert run.main([]) == 0
    report = last_result(capsys.readouterr().out)
    assert report["metrics"][budget.RULE] == {"tests": 1, "ci_jobs": 0, "unused_deps": 0, "replaceable": 0}
    assert _schema_errors(report) == []


def test_ndf_check_over_the_limit_fails_and_keeps_the_metrics(entry, capsys, last_result):
    entry.write("tests/test_a.py", "def test_a():\n    pass\n\n\ndef test_b():\n    pass\n")
    entry.settings({"budget": {"tests": 1}})
    assert run.main([]) == 1
    report = last_result(capsys.readouterr().out)
    assert [i["rule"] for i in report["items"]] == [budget.RULE]
    assert report["metrics"][budget.RULE]["tests"] == 2
    assert _schema_errors(report) == []


def test_ndf_check_only_other_rule_does_not_measure(entry, capsys, last_result):
    entry.write_repo(SETTINGS_REL, "{")
    assert run.main(["--only", "I3"]) == 0
    assert budget.RULE not in last_result(capsys.readouterr().out)["metrics"]


def test_check_result_schema_refuses_extra_budget_keys():
    report = {
        "tool": "check",
        "status": "ok",
        "summary": "s",
        "items": [],
        "metrics": {budget.RULE: {"tests": 1, "ci_jobs": 0, "unused_deps": 0, "replaceable": 0, "other": 1}},
    }
    assert _schema_errors(report) != []
