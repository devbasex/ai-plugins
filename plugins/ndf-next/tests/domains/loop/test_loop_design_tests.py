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


def test_a_design_over_several_issues_is_read_for_each_issue(design_tests, repo: Path):
    # `issue-7-8-design.md` は 7 でも 8 でも読み、78 のような番号の一部とは合わせない
    (repo / "issues" / "issue-7-8-design.md").write_text("## テスト設計\n\n`tests/test_kept.py::test_gone`\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "c3"], check=True)
    for n in ("7", "8"):
        got = design_tests("--issue", n)
        assert got["status"] == "stopped" and got["cause"]["kind"] == "check_failed"
    assert design_tests("--issue", "78")["cause"]["kind"] == "not_applicable"


# --- 現状固定（I-001: design_tests.run の分割の前に、結果の形をそのまま記録する） ---


@pytest.fixture
def two_designs(repo: Path) -> Path:
    """同じ issue の設計文書 2 件。同じ名前が両方に出て、形だけの名前（`<`）を 1 件含む。"""
    (repo / "issues" / "issue-5-design.md").write_text(
        "## テスト設計\n\n`tests/test_kept.py::test_kept_rule` と `test_<名前>.py`\n", encoding="utf-8"
    )
    (repo / "issues" / "issue-5-design-b.md").write_text(
        "## テスト設計\n\n`tests/test_kept.py::test_kept_rule` と `tests`\n", encoding="utf-8"
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "c2"], check=True)
    return repo


def test_current_ok_over_two_designs_counts_skipped_and_dedups(design_tests, two_designs: Path):
    got = design_tests("--issue", "5")
    assert got["status"] == "ok"
    assert got["summary"] == (
        "設計文書 2 件（issues/issue-5-design-b.md, issues/issue-5-design.md）。設計に名前の出るテスト 1 件がすべて HEAD にある"
    )
    assert got["items"] == [
        {
            "kind": "test_function",
            "name": "tests/test_kept.py::test_kept_rule",
            "doc": "issues/issue-5-design-b.md",
            "line": 3,
            "section": "テスト設計",
            "result": "present",
        }
    ]
    assert got["metrics"] == {"designs": 2, "names": 1, "skipped": 1, "missing": 0, "unverified": 0, "renamed": 0}


@pytest.fixture
def rel_design(repo: Path) -> str:
    (repo / "rel.md").write_text("## テスト設計\n\n`test_kept_rule` と `test_kept.py`\n", encoding="utf-8")
    return "rel.md"


def test_current_ok_with_a_relative_design_path(design_tests, rel_design: str):
    got = design_tests("--design", rel_design)
    assert got["status"] == "ok"
    assert got["summary"] == "設計文書 1 件（rel.md）。設計に名前の出るテスト 2 件がすべて HEAD にある"
    assert [(i["kind"], i["name"], i["result"]) for i in got["items"]] == [
        ("test_function", "test_kept_rule", "present"),
        ("test_file", "test_kept.py", "present"),
    ]
    assert got["metrics"] == {"designs": 1, "names": 2, "skipped": 0, "missing": 0, "unverified": 0, "renamed": 0}


def test_current_unreadable_design_is_invalid(design_tests, repo: Path):
    got = design_tests("--design", "nope.md")
    assert got["status"] == "stopped" and got["cause"]["kind"] == "invalid"
    assert got["cause"]["detail"].startswith("設計文書か git を読めない: ")
    assert str(repo / "nope.md") in got["cause"]["detail"]
    assert got["items"] == [] and got["metrics"] == {}


def test_current_grep_failure_is_incomplete_with_gaps(design_tests, rel_design: str, monkeypatch: pytest.MonkeyPatch):
    from ndf.domains.loop import design_tests as mod

    monkeypatch.setattr(mod, "_grep", lambda g: lambda names, pathspec: None)
    got = design_tests("--design", rel_design)
    detail = "設計文書 1 件（rel.md）。設計に名前の出るテストのうち 1 件を確かめられなかった: test_kept_rule（rel.md の 3 行・unverified）"
    assert got["status"] == "stopped" and got["cause"] == {"kind": "incomplete", "detail": detail}
    assert got["summary"] == detail
    assert {i["name"]: i["result"] for i in got["items"]} == {"test_kept_rule": "unverified", "test_kept.py": "present"}
    assert got["metrics"] == {"designs": 1, "names": 2, "skipped": 0, "missing": 0, "unverified": 1, "renamed": 0}
    assert got["gaps"] == [{"kind": "test_function", "name": "test_kept_rule", "reason": "rel.md の 3 行を確かめられなかった"}]


def test_bare_names_are_searched_with_one_git_grep(design_tests, repo: Path, monkeypatch: pytest.MonkeyPatch):
    # PR 3537 の指摘: パスの無い名前が N 個あっても、リポジトリ全体の検索は 1 回にまとめる（無い名前は missing のまま）
    from ndf.domains.loop import design_tests as mod

    calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    real = mod._grep

    def counted(g):
        inner = real(g)

        def run(names, pathspec):
            calls.append((tuple(names), tuple(pathspec)))
            return inner(names, pathspec)

        return run

    monkeypatch.setattr(mod, "_grep", counted)
    (repo / "bare.md").write_text("## テスト設計\n\n`test_kept_rule` と `test_stage` と `test_nowhere`\n", encoding="utf-8")
    got = design_tests("--design", "bare.md")
    assert {i["name"]: i["result"] for i in got["items"]} == {
        "test_kept_rule": "present",
        "test_stage": "present",
        "test_nowhere": "missing",
    }
    assert [spec for _, spec in calls] == [mod.WHOLE]
