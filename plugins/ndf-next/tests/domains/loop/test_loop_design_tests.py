"""`ndf loop design-tests`: 設計に名前の出るテストが HEAD にあるか（#1241 の確認 (a)・#2118）。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

import pytest

from ndf.contracts import result
from ndf.domains.loop import cli as loop_cli
from ndf.domains.loop import design_tests_decide as dt

# #2118 の再現の形（issue-2086-design.md）: 構成要素で改名を宣言し、採らなかった案とテスト設計に改名の前の名前を書く
DESIGN = """# 設計

## 構成要素

| 項目 | 変更 | 中身 |
| --- | --- | --- |
| Skill の名前 | 改名 | `document-restructuring` → `document-rebuild`。テストも新しい名前へ揃える |

## 決定の記録

### 決定 5: 手順 4 で組み直しを呼ぶ

採らなかった案: 工程表に行を足す案。工程名を読むテスト（`test_never_built.py`）を変える。

### 決定 6: 目安を置く

`tests/test_kept.py::test_kept_rule` で確かめる。

## テスト設計

| 条件 | 確かめ方 |
| --- | --- |
| AC 22 | `test_document_restructuring_stage.py` を含む全体テスト |

```text
`test_inside_fence.py`
```
"""


def test_names_in_rejected_alternatives_and_fences_are_not_picked():
    found, _ = dt.extract("d.md", DESIGN, set())
    assert [f.span for f in found] == ["tests/test_kept.py::test_kept_rule", "test_document_restructuring_stage.py"]


def test_declared_renames_give_the_new_names_with_underscores():
    pairs = dt.renames([DESIGN])
    assert pairs == [("document-restructuring", "document-rebuild")]
    assert dt.variants("test_document_restructuring_stage.py", pairs) == ["test_document_rebuild_stage.py"]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    where = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(where)], check=True)
    (where / "tests").mkdir()
    (where / "tests" / "test_kept.py").write_text("def test_kept_rule():\n    pass\n", encoding="utf-8")
    (where / "tests" / "test_document_rebuild_stage.py").write_text("def test_stage():\n    pass\n", encoding="utf-8")
    (where / "issues").mkdir()
    (where / "issues" / "issue-2086-design.md").write_text(DESIGN, encoding="utf-8")
    subprocess.run(["git", "-C", str(where), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(where), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "c"], check=True)
    return where


@pytest.fixture
def design_tests(repo: Path, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]) -> Callable[..., dict]:
    def run(*argv: str) -> dict[str, Any]:
        loop_cli.main(["--cwd", str(repo), "design-tests", *argv])
        got = last_result(capsys.readouterr().out)
        assert result.errors(got) == []
        return got

    return run


def test_a_test_renamed_as_the_design_declared_is_not_a_stop(design_tests):
    # #2118 の再現: 設計どおりに改名したテストは止まりにせず、改名の後の名前を載せる
    got = design_tests("--issue", "2086")
    assert got["status"] == "ok" and got["metrics"]["renamed"] == 1
    row = next(i for i in got["items"] if i["name"] == "test_document_restructuring_stage.py")
    assert row["result"] == "renamed" and row["renamed_to"] == "test_document_rebuild_stage.py"


def test_a_missing_test_stops_with_every_name_in_full(design_tests, repo: Path):
    doc = repo / "d.md"
    long_name = "test_" + "a" * 150 + ".py"
    doc.write_text(f"## テスト設計\n\n`{long_name}` と `tests/test_kept.py::test_gone`\n", encoding="utf-8")
    got = design_tests("--design", str(doc))
    assert got["status"] == "stopped" and got["cause"]["kind"] == "check_failed"
    assert long_name in got["cause"]["detail"] and "tests/test_kept.py::test_gone" in got["cause"]["detail"]
    assert {i["name"]: i["result"] for i in got["items"]} == {long_name: "missing", "tests/test_kept.py::test_gone": "missing"}


def test_no_design_document_is_not_applicable(design_tests):
    got = design_tests("--issue", "9")
    assert got["status"] == "stopped" and got["cause"]["kind"] == "not_applicable"
