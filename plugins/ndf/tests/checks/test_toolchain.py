"""規則 toolchain: 配る構成の設定と、ワークフロー・`pyproject.toml`・ランチャーの指定の一致（#3687 の受け入れ条件 3・4）。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from ndf import identity
from ndf.checks import run, toolchain
from ndf.checks.model import CheckError, Scope
from ndf.contracts import toolchain as table

UV = "astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2 # v10.3.3"
PY = "actions/setup-python@6d4fd22b20465ccc0f7354476588e8df7c9ca0e0 # v7.1.0"
COMPAT = ".github/workflows/compat.yml"
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = "plugins/ndf-dev/pyproject.toml"
SETTINGS: dict[str, Any] = {
    "workflows": ".github/workflows/*.yml",
    "python_ci": ["3.11", "3.13"],
    "python_compat": {"versions": ["3.10"], "workflows": [COMPAT]},
    "plugin_pyprojects": [PYPROJECT],
    "launchers": ["plugins/ndf-dev/bin/ndf"],
}


def _workflow(*steps: str) -> str:
    body = "".join(f"      {s}\n" for s in steps)
    return f"name: w\non:\n  pull_request:\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n{body}"


def _uv(version: str | None = None) -> str:
    if version is None:
        version = table.uv_version()
    return f"- uses: {UV}\n        with:\n          enable-cache: true\n          version: {version}"


def _py(version: str) -> str:
    return f'- uses: {PY}\n        with:\n          python-version: "{version}"'


def _launcher(version: str | None = None, digest: str | None = None) -> str:
    version = table.uv_version() if version is None else version
    digest = table.uv_installer_sha256() if digest is None else digest
    return f'#!/usr/bin/env bash\nset -eu\nUV_VERSION="{version}"\n# インストーラの sha256\nUV_INSTALLER_SHA256="{digest}"\n'


class Repo:
    """規則が読むファイルだけを置いた一時のディレクトリ（規則は `Scope` のファイルと設定しか読まないので git を作らない）。"""

    def __init__(self, root: Path) -> None:
        self.root = root

    def file(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def settings(self, value: dict[str, Any] | None) -> None:
        self.file(f"{identity.FIXED_WORDS['ndf_settings_dir']}/project.json", json.dumps({} if value is None else {"toolchain": value}))

    def check(self, rules) -> list:
        return toolchain.check(Scope(self.root, self.root / "plugins" / "ndf-dev", "ndfdev"), rules)


@pytest.fixture
def repo(tmp_path) -> Repo:
    """設定と 3 つの指定箇所がそろったリポジトリ（`pyproject.toml` は本物の写し）。"""
    r = Repo(tmp_path)
    (tmp_path / PYPROJECT).parent.mkdir(parents=True)
    shutil.copy(PLUGIN_ROOT / "pyproject.toml", tmp_path / PYPROJECT)
    r.settings(SETTINGS)
    r.file(".github/workflows/ci.yml", _workflow(_py("3.13"), _uv(), "- run: uv run pytest"))
    r.file(COMPAT, _workflow(_py("3.10")))
    r.file("plugins/ndf-dev/bin/ndf", _launcher())
    return r


def _lines(found) -> list[tuple[str, int]]:
    return [(f.path, f.line) for f in found]


def test_settings_and_the_three_places_agree(repo, rules):
    assert repo.check(rules) == []


def test_registered_as_a_settings_rule():
    assert run.SETTINGS_RULES[toolchain.RULE] is toolchain.check


def test_without_toolchain_nothing_is_read(repo, rules, monkeypatch):
    repo.settings(None)
    repo.file(".github/workflows/ci.yml", "{")
    monkeypatch.setattr(table, "table", lambda: pytest.fail("配る構成の設定を読んだ"))
    assert repo.check(rules) == []


def test_only_the_keys_that_are_present_are_checked(repo, rules):
    repo.settings({"plugin_pyprojects": SETTINGS["plugin_pyprojects"]})
    repo.file(".github/workflows/ci.yml", _workflow(_uv("0.0.1"), "- run: pip install uv"))
    repo.file("plugins/ndf-dev/bin/ndf", _launcher("0.0.1"))
    assert repo.check(rules) == []


def test_a_glob_without_files_has_no_findings(repo, rules):
    repo.settings({**SETTINGS, "workflows": ".github/none/*.yml"})
    assert repo.check(rules) == []


def test_python_version_outside_the_lists_is_a_finding(repo, rules):
    repo.file(".github/workflows/ci.yml", _workflow(_uv(), _py("3.12")))
    assert _lines(repo.check(rules)) == [(".github/workflows/ci.yml", 14)]


def test_compat_version_outside_the_compat_workflows_is_a_finding(repo, rules):
    repo.file(".github/workflows/ci.yml", _workflow(_py("3.10")))
    assert _lines(repo.check(rules)) == [(".github/workflows/ci.yml", 10)]


def test_compat_version_in_a_compat_workflow_passes(repo, rules):
    repo.file(COMPAT, _workflow(_py("3.10"), _py("3.13")))
    assert repo.check(rules) == []


def test_unquoted_version_is_read_as_written(repo, rules):
    # YAML の 3.10 は数として読むと 3.1 になる。書いたとおりの文字で比べる
    repo.file(COMPAT, _workflow(f"- uses: {PY}\n        with:\n          python-version: 3.10"))
    assert repo.check(rules) == []
    repo.file(".github/workflows/ci.yml", _workflow(f"- uses: {PY}\n        with:\n          python-version: 3.10"))
    assert _lines(repo.check(rules)) == [(".github/workflows/ci.yml", 10)]


@pytest.mark.parametrize(
    "with_",
    ["", '\n        with:\n          python-version-file: ".python-version"', "\n        with:\n          cache: pip"],
)
def test_setup_python_without_python_version_is_a_finding(repo, rules, with_):
    # python-version-file の参照先・既定の Python は python_ci の値と比べられない
    repo.file(".github/workflows/ci.yml", _workflow(_uv(), f"- uses: {PY}{with_}"))
    assert _lines(repo.check(rules)) == [(".github/workflows/ci.yml", 12)]


def test_without_python_compat_only_python_ci_is_allowed(repo, rules):
    repo.settings({k: v for k, v in SETTINGS.items() if k != "python_compat"})
    assert _lines(repo.check(rules)) == [(COMPAT, 10)]


@pytest.mark.parametrize(
    ("step", "line"),
    [
        (f"- uses: {UV}", 8),
        (f"- uses: {UV}\n        with:\n          enable-cache: true", 8),
        (None, 11),
    ],
)
def test_setup_uv_without_or_with_another_version_is_a_finding(repo, rules, step, line):
    repo.file(".github/workflows/ci.yml", _workflow(_uv("0.0.1") if step is None else step))
    assert _lines(repo.check(rules)) == [(".github/workflows/ci.yml", line)]


def test_a_setup_uv_version_that_differs_is_reported_at_its_value_with_both_versions(repo, rules):
    repo.file(".github/workflows/ci.yml", _workflow(_uv("0.0.1")))
    (found,) = repo.check(rules)
    assert found.line == 11
    assert "0.0.1" in found.detail
    assert table.uv_version() in found.detail


@pytest.mark.parametrize("value", ['""', '" "', f'" {table.uv_version()}"', f'"{table.uv_version()} 0.0.1"'])
def test_setup_uv_version_is_compared_as_a_whole(repo, rules, value):
    # setup-python と違い、空の値も空白で分けた値も書いたとおりに比べる
    repo.file(".github/workflows/ci.yml", _workflow(_uv(value)))
    assert _lines(repo.check(rules)) == [(".github/workflows/ci.yml", 11)]


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_python_version_is_the_missing_finding_at_its_value(repo, rules, value):
    repo.file(".github/workflows/ci.yml", _workflow(f"- uses: {PY}"))
    (missing,) = repo.check(rules)
    repo.file(".github/workflows/ci.yml", _workflow(_py(value)))
    (blank,) = repo.check(rules)
    assert (missing.line, blank.line) == (8, 10)
    assert blank.detail == missing.detail


def test_every_listed_python_version_is_checked_in_one_finding(repo, rules):
    repo.file(".github/workflows/ci.yml", _workflow(_py("3.11 3.13")))
    assert repo.check(rules) == []
    repo.file(".github/workflows/ci.yml", _workflow(_py("3.8 3.11 3.9")))
    (found,) = repo.check(rules)
    assert found.line == 10
    # 許す値は、書いた値の並びから除いて示す
    assert "3.8 3.9" in found.detail


@pytest.mark.parametrize("with_", ["", '\n        with:\n          python-version: "3.9"'])
def test_the_allowed_python_versions_are_shown_sorted(repo, rules, with_):
    repo.settings({**SETTINGS, "python_ci": ["3.13", "3.11"]})
    for rel, allowed in ((".github/workflows/ci.yml", ["3.11", "3.13"]), (COMPAT, ["3.10", "3.11", "3.13"])):
        repo.file(rel, _workflow(f"- uses: {PY}{with_}"))
        assert [str(allowed) in f.detail for f in repo.check(rules) if f.path == rel] == [True]


def test_each_action_is_checked_only_for_its_own_input(repo, rules):
    uv = _uv() + '\n          python-version: "3.9"'
    py = _py("3.13") + "\n          version: 0.0.1"
    repo.file(".github/workflows/ci.yml", _workflow(uv, py))
    assert repo.check(rules) == []
    # もう一方の入力は自分の入力の代わりにならない
    repo.file(
        ".github/workflows/ci.yml",
        _workflow(
            f'- uses: {UV}\n        with:\n          python-version: "3.13"',
            f"- uses: {PY}\n        with:\n          version: {table.uv_version()}",
        ),
    )
    assert _lines(repo.check(rules)) == [(".github/workflows/ci.yml", 8), (".github/workflows/ci.yml", 11)]


def test_findings_follow_the_steps_and_then_the_run_commands(repo, rules):
    repo.file(".github/workflows/ci.yml", _workflow("- run: pip install uv", _py("3.9"), f"- uses: {UV}", f"- uses: {PY}", _uv("0.0.1")))
    assert [line for _, line in _lines(repo.check(rules))] == [11, 12, 13, 17, 8]


@pytest.mark.parametrize(
    "body",
    [
        "python3 -m pip install --upgrade uv",
        "pip3 install uv==0.5.0",
        "pipx install uv",
        # 引用符の外のエスケープは bash が外す（`uv<0.9`・`uv` になる）
        r"pip install uv\<0.9",
        r"pip install \uv",
        "curl -LsSf https://astral.sh/uv/install.sh | sh",
    ],
)
def test_installing_uv_outside_setup_uv_is_a_finding(repo, rules, body):
    repo.file(".github/workflows/ci.yml", _workflow(_uv(), f"- run: {body}"))
    assert _lines(repo.check(rules)) == [(".github/workflows/ci.yml", 12)]


def test_the_line_inside_a_block_run_is_reported(repo, rules):
    repo.file(".github/workflows/ci.yml", _workflow("- run: |\n          echo a\n          pip install --quiet uv"))
    assert _lines(repo.check(rules)) == [(".github/workflows/ci.yml", 10)]


def test_a_continued_install_is_reported_at_its_first_line(repo, rules):
    run = "- run: |\n          echo a\n          python3 -m pip install \\\n            --quiet \\\n            uv"
    repo.file(".github/workflows/ci.yml", _workflow(run))
    assert _lines(repo.check(rules)) == [(".github/workflows/ci.yml", 10)]


def test_a_url_split_by_an_unquoted_line_continuation_is_one_word(repo, rules):
    # 空白を挟まない行の継続は bash には 1 語（URL は https://astral.sh/uv/install.sh になる）
    run = "- run: |\n          curl -LsSf https://astral.sh\\\n          /uv/install.sh | sh"
    repo.file(".github/workflows/ci.yml", _workflow(_uv(), run))
    assert _lines(repo.check(rules)) == [(".github/workflows/ci.yml", 13)]


@pytest.mark.parametrize(
    "body",
    [
        "|\n          # uv は setup-uv で入れる（pip install uv は使わない）\n          uv sync --frozen",
        "uv sync --frozen  # pip install uv は使わない",
        "echo 'pip install uv は使わない'",
        'echo "curl https://astral.sh/uv/install.sh を打たない"',
    ],
)
def test_comments_and_echoed_text_are_not_findings(repo, rules, body):
    repo.file(".github/workflows/ci.yml", _workflow(_uv(), f"- run: {body}"))
    assert repo.check(rules) == []


@pytest.mark.parametrize(
    "body",
    ['python3 -m pip install --quiet "pathspec==1.1.1"', "uv pip install ruv-tool", "uv sync --frozen", "pip install uv-dynamic"],
)
def test_other_installs_are_not_findings(repo, rules, body):
    repo.file(".github/workflows/ci.yml", _workflow(_uv(), f"- run: {body}"))
    assert repo.check(rules) == []


@pytest.mark.parametrize(("version", "digest", "line"), [("0.0.1", None, 3), (None, "0" * 64, 5)])
def test_launcher_lines_that_differ_are_findings(repo, rules, version, digest, line):
    repo.file("plugins/ndf-dev/bin/ndf", _launcher(version, digest))
    assert _lines(repo.check(rules)) == [("plugins/ndf-dev/bin/ndf", line)]


def test_launcher_without_the_lines_is_a_finding(repo, rules):
    repo.file("plugins/ndf-dev/bin/ndf", "#!/usr/bin/env bash\n")
    assert _lines(repo.check(rules)) == [("plugins/ndf-dev/bin/ndf", 1)] * 2


@pytest.mark.parametrize("key", ["plugin_pyprojects", "launchers"])
def test_a_missing_listed_file_is_a_finding(repo, rules, key):
    repo.settings({**SETTINGS, key: ["plugins/ndf-dev/none"]})
    assert _lines(repo.check(rules)) == [("plugins/ndf-dev/none", 1)]


def test_a_missing_listed_file_has_the_same_finding_under_either_key(repo, rules):
    repo.settings({**SETTINGS, "plugin_pyprojects": ["none"], "launchers": ["none"]})
    found = repo.check(rules)
    assert len(found) == 2
    assert found[0] == found[1]


@pytest.mark.parametrize(
    ("key", "text", "line"),
    [("plugin_pyprojects", '[project]\nrequires-python = ">=3.10"\n', 2), ("launchers", _launcher("0.0.1"), 3)],
)
def test_listed_files_are_checked_in_the_listed_order_past_a_missing_one(repo, rules, key, text, line):
    # グロブは当たるファイルを昇順に 1 つずつ見る。欠けた 1 つは、その前後のファイルの検査を止めない
    for rel in ("z/one", "a/one", "a/two"):
        repo.file(rel, text)
    repo.settings({key: ["z/one", "none-*", "a/*"]})
    assert _lines(repo.check(rules)) == [("z/one", line), ("none-*", 1), ("a/one", line), ("a/two", line)]


def test_a_missing_compat_workflow_is_not_a_finding(repo, rules):
    repo.settings({**SETTINGS, "python_compat": {"versions": ["3.10"], "workflows": [COMPAT, ".github/workflows/none.yml"]}})
    assert repo.check(rules) == []


def test_broken_workflow_stops_with_its_path(repo, rules):
    repo.file(".github/workflows/ci.yml", "jobs: [\n")
    with pytest.raises(CheckError, match="ci.yml"):
        repo.check(rules)


def test_broken_workflow_error_has_the_position_but_not_the_input(repo, rules):
    # Git の外の設定ファイルも読みうるので、入力の抜粋（秘密を含みうる）を出さない
    repo.file(".github/workflows/ci.yml", "env:\n  TOKEN: s3cr3t-value\n  bad: [\n")
    with pytest.raises(CheckError) as caught:
        repo.check(rules)
    assert "s3cr3t" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert ".github/workflows/ci.yml:" in str(caught.value)


def test_broken_contract_stops(repo, rules, monkeypatch):
    def broken():
        raise table.ToolchainError("形が違う")

    monkeypatch.setattr(table, "table", broken)
    with pytest.raises(CheckError, match="形が違う"):
        repo.check(rules)


def test_workflow_tools_reads_pinned_uses_and_steps_without_with(tmp_path):
    path = tmp_path / "w.yml"
    path.write_text(_workflow(f"- uses: {UV}", _py("3.13"), "- run: echo"), encoding="utf-8")
    assert [(t.action, t.line, t.values) for t in toolchain.workflow_tools(path)] == [
        (toolchain.SETUP_UV, 8, {}),
        (toolchain.SETUP_PYTHON, 9, {"python-version": ("3.13", 11)}),
    ]


def test_workflow_tools_stops_on_broken_yaml(tmp_path):
    path = tmp_path / "w.yml"
    path.write_text("a: b: c\n", encoding="utf-8")
    with pytest.raises(CheckError, match="w.yml"):
        toolchain.workflow_tools(path)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('[project]\nname = "x"\nrequires-python = ">=3.11"\n', ((3, 11, 0), 3)),
        ('[project]\nrequires-python = ">=3.11,<4"\n', ((3, 11, 0), 2)),
        ('[project]\nrequires-python = "<4, >= 3.12"\n', ((3, 12, 0), 2)),
        ('[project]\nrequires-python = ">=3.11.1"\n', ((3, 11, 1), 2)),
        ('[project]\nrequires-python = ">=3.11,!=3.11.*"\n', ((3, 12, 0), 2)),
        ('[project]\nrequires-python = ">3.10"\n', ((3, 10, 1), 2)),
        ('[project]\nrequires-python = "~=3.11"\n', ((3, 11, 0), 2)),
        # プレリリースを下限にした条件は同じ番号の正式リリースを許す
        ('[project]\nrequires-python = ">=3.11a"\n', ((3, 11, 0), 2)),
        # 末尾のカンマは PEP 440 で有効
        ('[project]\nrequires-python = ">=3.11,"\n', ((3, 11, 0), 2)),
        # `===` は字の一致で、`3.11` は正式リリースの `3.11.0` と一致しない
        ('[project]\nrequires-python = "===3.11"\n', (None, 2)),
        ('[project]\nrequires-python = "===3.11.0"\n', ((3, 11, 0), 2)),
        ('[project]\nrequires-python = "<4"\n', (None, 2)),
        ('[project]\nname = "x"\n', (None, 1)),
    ],
)
def test_pyproject_floor(tmp_path, text, expected):
    path = tmp_path / "pyproject.toml"
    path.write_text(text, encoding="utf-8")
    assert toolchain.pyproject_floor(path) == expected


@pytest.mark.parametrize(
    ("text", "line"),
    [
        ('[project]\nrequires-python = ">=3.10"\n', 2),
        ('[project]\nname = "x"\n', 1),
        ('[project]\nrequires-python = "<4"\n', 2),
        ('[project]\nrequires-python = ">=3.11.1"\n', 2),
        ('[project]\nrequires-python = ">=3.11,!=3.11.*"\n', 2),
    ],
)
def test_pyproject_floor_that_differs_or_is_missing_is_a_finding(repo, rules, text, line):
    repo.file(PYPROJECT, text)
    assert _lines(repo.check(rules)) == [(PYPROJECT, line)]


@pytest.mark.parametrize("spec", ["=>3.11", ">=3.11x", "~=3"])
def test_unreadable_requires_python_stops(repo, rules, spec):
    repo.file(PYPROJECT, f'[project]\nrequires-python = "{spec}"\n')
    with pytest.raises(CheckError, match="requires-python"):
        repo.check(rules)


def test_the_plugin_pyproject_matches_the_floor():
    # 本物の pyproject.toml が許す最も古い Python は、配る構成の設定の下限の .0
    assert toolchain.pyproject_floor(PLUGIN_ROOT / "pyproject.toml")[0] == (*table.python_floor(), 0)
