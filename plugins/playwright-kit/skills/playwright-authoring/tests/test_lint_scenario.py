"""scripts/lint_scenario.py の振る舞いをテストする。

再現可能性レビューチェックリストのうち構文木で判定できる 4 項目
（page_role の目印・role と pwk_role_<id> の対・URL の直書き・wait_until）を確かめる。
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "lint_scenario.py"
TEMPLATES = (
    Path(__file__).resolve().parents[2] / "playwright-kit-ops" / "templates"
)


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, check=False,
    )


def lint_source(tmp_path: Path, source: str) -> tuple[int, dict]:
    tests = tmp_path / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_sample.py").write_text(textwrap.dedent(source), encoding="utf-8")
    proc = run(str(tests))
    return proc.returncode, json.loads(proc.stdout)


def rules(out: dict) -> list[str]:
    return sorted(v["rule"] for v in out["violations"])


GOOD = """
    import pytest

    @pytest.mark.page_role("form")
    @pytest.mark.role("admin")
    def test_ok(page, pwk_role_admin, pwk_config):
        page.goto(f"{pwk_config.base_url}/users/new", wait_until="domcontentloaded")
"""


def test_clean_file_passes(tmp_path: Path):
    code, out = lint_source(tmp_path, GOOD)
    assert code == 0
    assert out["violations"] == []
    assert out["tests"] == 1
    assert len(out["checked_rules"]) == 4
    assert len(out["unchecked_items"]) == 4


def test_templates_pass(tmp_path: Path):
    tests = tmp_path / "tests"
    tests.mkdir()
    for tpl in TEMPLATES.glob("test_*.py.template"):
        (tests / tpl.name.removesuffix(".template")).write_text(
            tpl.read_text(encoding="utf-8"), encoding="utf-8"
        )
    proc = run(str(tests))
    assert proc.returncode == 0, proc.stdout


def test_missing_page_role(tmp_path: Path):
    code, out = lint_source(tmp_path, """
        def test_no_marker(page, pwk_config):
            page.goto(pwk_config.base_url, wait_until="load")
    """)
    assert code == 1
    assert rules(out) == ["page_role"]
    assert out["violations"][0]["test"] == "test_no_marker"


def test_page_role_from_module_or_class_marker(tmp_path: Path):
    code, out = lint_source(tmp_path, """
        import pytest

        pytestmark = [pytest.mark.page_role("list")]

        def test_a(page, pwk_config):
            page.goto(pwk_config.base_url, wait_until="load")

        @pytest.mark.page_role("item")
        class TestItem:
            def test_b(self, page, pwk_config):
                page.goto(pwk_config.base_url, wait_until="load")
    """)
    assert code == 0, out


def test_role_marker_without_fixture(tmp_path: Path):
    code, out = lint_source(tmp_path, """
        import pytest

        @pytest.mark.page_role("form")
        @pytest.mark.role("admin")
        def test_x(page, pwk_config):
            page.goto(pwk_config.base_url, wait_until="load")
    """)
    assert code == 1
    assert rules(out) == ["role_pair"]


def test_role_marker_and_fixture_disagree(tmp_path: Path):
    code, out = lint_source(tmp_path, """
        import pytest

        @pytest.mark.page_role("form")
        @pytest.mark.role("admin")
        def test_x(page, pwk_role_viewer, pwk_config):
            page.goto(pwk_config.base_url, wait_until="load")
    """)
    assert code == 1
    assert rules(out) == ["role_pair", "role_pair"]


def test_hardcoded_url(tmp_path: Path):
    code, out = lint_source(tmp_path, """
        import pytest

        BASE = "https://staging.example.com"

        @pytest.mark.page_role("lp")
        def test_x(page):
            page.goto(f"http://localhost:8080/", wait_until="load")
    """)
    assert code == 1
    assert rules(out) == ["hardcoded_url", "hardcoded_url"]


def test_docstring_url_is_ignored(tmp_path: Path):
    code, out = lint_source(tmp_path, '''
        """See https://example.com/docs."""
        import pytest

        @pytest.mark.page_role("lp")
        def test_x(page, pwk_config):
            """Opens https://example.com/ via base_url."""
            page.goto(pwk_config.base_url, wait_until="load")
    ''')
    assert code == 0, out


def test_goto_without_wait_until(tmp_path: Path):
    code, out = lint_source(tmp_path, """
        import pytest

        @pytest.mark.page_role("lp")
        def test_x(page, pwk_config):
            page.goto(pwk_config.base_url)
            page.reload()
    """)
    assert code == 1
    assert rules(out) == ["wait_until", "wait_until"]
    assert {v["line"] for v in out["violations"]} == {6, 7}


def test_syntax_error_is_exit_2(tmp_path: Path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_broken.py").write_text("def test_x(:\n", encoding="utf-8")
    proc = run(str(tests))
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["errors"][0]["file"].endswith("test_broken.py")


def test_missing_path_is_exit_2(tmp_path: Path):
    proc = run(str(tmp_path / "nope"))
    assert proc.returncode == 2
