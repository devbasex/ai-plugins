"""差分の分類（lib/diff_kinds.py・#2925 の決定 1・3）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import diff_kinds as dk  # noqa: E402


def _decl(root: Path, docs=None, glossary=None) -> Path:
    (root / ".ndf").mkdir(parents=True, exist_ok=True)
    body = {"version": 1}
    if docs is not None:
        body["docs"] = docs
    (root / ".ndf" / "project.json").write_text(json.dumps(body), encoding="utf-8")
    if glossary is not None:
        (root / ".ndf" / "glossary.json").write_text(json.dumps(glossary), encoding="utf-8")
    return root


def test_defaults_without_any_decl_hold_only_markdown_and_no_project_paths(tmp_path):
    # AC11・I5: 宣言も用語集の設定も無いプロジェクトでは Markdown だけが文書で、ai-plugins のパスを持たない
    decl = dk.load_docs_decl(tmp_path)
    assert decl.files == ("**/*.md",)
    k = dk.classify_files(["README.md", "docs/glossary/glossary.json", "src/a.py"], decl)
    assert k.docs == ["README.md"]
    assert k.code == ["docs/glossary/glossary.json", "src/a.py"]


def test_default_files_add_the_glossary_files_from_the_glossary_decl(tmp_path):
    # 決定 3: 用語集の機械向けの形は、用語集の設定の source から既定へ入る
    root = _decl(tmp_path, glossary={"source": "terms/g.json", "document": "terms/g.md"})
    k = dk.classify_files(["terms/g.json", "terms/g.md"], dk.load_docs_decl(root))
    assert k.docs_only() and k.code == []


def test_docs_only_needs_classified_docs_and_no_code(tmp_path):
    decl = dk.load_docs_decl(tmp_path)
    assert dk.classify_files(["a.md", "b/c.md"], decl).docs_only()
    assert not dk.classify_files(["a.md", "x.py"], decl).docs_only()
    assert not dk.classify_files([], decl).docs_only()


def test_each_file_lands_in_exactly_one_of_docs_and_code(tmp_path):
    # I1
    files = ["a.md", "plugins/x/skills/s/SKILL.md", "x.py", "tests/test_x.py", "c.json"]
    k = dk.classify_files(files, dk.load_docs_decl(tmp_path))
    assert sorted(k.docs + k.code) == sorted(files)
    assert not set(k.docs) & set(k.code)


def test_behavior_patterns_do_not_pull_non_docs_out_of_code(tmp_path):
    # I3: 振る舞いのパターンに当たっても、文書のパターンに当たらなければコード
    root = _decl(tmp_path, docs={"behavior": ["plugins/**"]})
    k = dk.classify_files(["plugins/x/skills/s/SKILL.md", "plugins/x/skills/s/run.py"], dk.load_docs_decl(root))
    assert k.behavior == ["plugins/x/skills/s/SKILL.md"]
    assert k.code == ["plugins/x/skills/s/run.py"]


def test_default_behavior_is_skill_bodies_and_agent_definitions(tmp_path):
    k = dk.classify_files(
        ["p/skills/s/SKILL.md", "p/skills/s/references/r.md", "p/agents/w.md", "p/agents/x/notes.md", "docs/a.md"],
        dk.load_docs_decl(tmp_path),
    )
    assert k.behavior == ["p/skills/s/SKILL.md", "p/skills/s/references/r.md", "p/agents/w.md"]


def test_tests_are_only_those_of_tested_docs_in_the_diff(tmp_path):
    # I4: 差分に無い文書の対応からは入らない。重複は 1 つ
    rows = [
        {"paths": ["docs/glossary.md"], "tests": ["t/test_g.py", "t/test_lint.py"]},
        {"paths": ["docs/mvv/*.md"], "tests": ["t/test_mvv.py", "t/test_lint.py"]},
        {"paths": ["docs/x.md"], "tests": ["t/test_x.py"]},
    ]
    root = _decl(tmp_path, docs={"tested": rows})
    k = dk.classify_files(["docs/glossary.md", "docs/mvv/a.md", "docs/other.md"], dk.load_docs_decl(root))
    assert k.tested == ["docs/glossary.md", "docs/mvv/a.md"]
    assert k.tests == ["t/test_g.py", "t/test_lint.py", "t/test_mvv.py"]


def test_declared_item_replaces_the_default(tmp_path):
    root = _decl(tmp_path, docs={"files": ["docs/**"]}, glossary={"source": "g.json"})
    k = dk.classify_files(["README.md", "docs/a.json", "g.json"], dk.load_docs_decl(root))
    assert k.docs == ["docs/a.json"] and k.code == ["README.md", "g.json"]


@pytest.mark.parametrize(
    "docs",
    [
        "x",
        {"files": "**/*.md"},
        {"files": []},
        {"tested": [{"paths": ["a.md"], "tests": []}]},
        {"tested": [{"paths": ["a.md"]}]},
        {"other": 1},
    ],
)
def test_broken_decl_is_unreadable_and_classifies_everything_as_code(tmp_path, docs):
    # AC5・I2: 読めない宣言で文書だけと判定しない
    root = _decl(tmp_path, docs=docs)
    with pytest.raises(dk.DeclError):
        dk.load_docs_decl(root)
    k = dk.classify_root(root, ["a.md"])
    assert k.unknown and k.code == ["a.md"] and not k.docs_only()


def test_unreadable_project_json_is_unreadable(tmp_path):
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "project.json").write_text("{", encoding="utf-8")
    assert dk.classify_root(tmp_path, ["a.md"]).unknown


def test_missing_file_list_is_not_docs_only(tmp_path):
    # AC5・I2
    k = dk.classify_files(None, dk.load_docs_decl(tmp_path))
    assert k.unknown and not k.docs_only()


def test_unknown_marker_is_the_same_as_no_decl(tmp_path):
    root = _decl(tmp_path, docs={"unknown": "決まらなかった"})
    assert dk.load_docs_decl(root).files == ("**/*.md",)


def test_as_json_has_all_fields(tmp_path):
    got = json.loads(dk.classify_files(["a.md"], dk.load_docs_decl(tmp_path)).as_json())
    assert set(got) == {"docs", "behavior", "tested", "code", "tests", "unknown"}
