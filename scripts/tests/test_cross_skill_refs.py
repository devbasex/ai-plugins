"""Skill の境界をまたぐ実行の参照の数え方（#285）。

**増えたときに気づく手段が無い状態は、移動の後も同じである。** 共通層を移して残る
参照は 2 件で、どちらも共通層ではなく Skill の本体どうしの参照である（#344）。
一覧へ書いた 2 件と、新しく増えた参照を分けられることを確かめる。

数え方そのものも検査する。**文書のリンクは対象にしない**（読み手への案内であり、
配布した先で解決できなくても手順は動く）。**Skill 名だけを手がかりにすると
`gh pr view` のような別の用途が入る**ため、直後に `scripts` が続く形へ絞る。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECK = ROOT / "scripts" / "check-cross-skill-refs.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_cross_skill_refs", CHECK)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_cross_skill_refs"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def checker():
    return _load()


def _tree(base: pathlib.Path) -> pathlib.Path:
    """検査が読む最小の木。`plugins/ndf/skills/<名前>/` の下だけを見る。"""
    skills = base / "plugins" / "ndf" / "skills"
    for name in ("cross-review", "cross-refactoring", "fix", "pr"):
        (skills / name / "scripts").mkdir(parents=True)
    return base


# ---------- A2: 実物の木 ----------

def test_only_the_known_two_references_remain(checker) -> None:
    found = checker.find_references(ROOT)
    assert sorted((rel, name) for rel, _, name, _ in found) == sorted(checker.EXCEPTIONS)


def test_the_check_passes_on_the_repository(checker) -> None:
    assert checker.main(["--root", str(ROOT)]) == 0


def test_every_exception_carries_an_issue_number(checker) -> None:
    assert all(v.startswith("#") for v in checker.EXCEPTIONS.values())


def test_the_check_is_wired_into_the_validation(checker) -> None:
    """既存の検査から呼ばれていなければ、継続的統合では実行されない。"""
    body = (ROOT / "scripts" / "validate-runtime-plugins.sh").read_text(encoding="utf-8")
    lines = [
        line for line in body.splitlines()
        if "scripts/check-cross-skill-refs.py" in line
        and not line.lstrip().startswith("#")
    ]
    assert lines
    assert all("--root" in line for line in lines)


# ---------- 数え方 ----------

def test_a_new_reference_fails_the_check(checker, tmp_path, monkeypatch) -> None:
    root = _tree(tmp_path)
    (root / "plugins/ndf/skills/pr/scripts/run.sh").write_text(
        'exec "$SKILL_DIR/../cross-review/scripts/state.py" judge "$PR"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(checker, "EXCEPTIONS", {})
    assert checker.main(["--root", str(root)]) == 1
    found = checker.find_references(root)
    assert [(rel, name) for rel, _, name, _ in found] == [
        ("plugins/ndf/skills/pr/scripts/run.sh", "cross-review")
    ]


def test_a_python_path_join_counts(checker, tmp_path) -> None:
    root = _tree(tmp_path)
    (root / "plugins/ndf/skills/pr/scripts/run.py").write_text(
        'p = here.resolve().parents[2] / "fix" / "scripts" / "fetch-pr-comments.sh"\n',
        encoding="utf-8",
    )
    assert [name for _, _, name, _ in checker.find_references(root)] == ["fix"]


def test_a_document_link_does_not_count(checker, tmp_path) -> None:
    root = _tree(tmp_path)
    (root / "plugins/ndf/skills/pr/SKILL.md").write_text(
        "- [手順](../cross-review/docs/01-state-and-review.md) を読む\n"
        "[参照]: ../cross-refactoring/SKILL.md\n",
        encoding="utf-8",
    )
    assert checker.find_references(root) == []


def test_a_skill_name_used_as_a_word_does_not_count(checker, tmp_path) -> None:
    """`pr` / `fix` は副命令や語としても現れる。"""
    root = _tree(tmp_path)
    (root / "plugins/ndf/skills/cross-review/scripts/run.py").write_text(
        'out = _sh(["gh", "pr", "view", str(pr)])\n'
        'note = "fix" if changed else "keep"\n',
        encoding="utf-8",
    )
    assert checker.find_references(root) == []


def test_a_reference_to_the_owning_skill_does_not_count(checker, tmp_path) -> None:
    """自分自身を指す参照は境界をまたがない。"""
    root = _tree(tmp_path)
    (root / "plugins/ndf/skills/fix/scripts/run.sh").write_text(
        'exec "$DIR/../../fix/scripts/fetch-pr-comments.sh"\n', encoding="utf-8"
    )
    assert checker.find_references(root) == []


def test_a_stale_exception_fails_the_check(checker, tmp_path, monkeypatch) -> None:
    """実体の無い例外を残さない。残すと一覧が実態を映さなくなる。"""
    root = _tree(tmp_path)
    monkeypatch.setattr(
        checker, "EXCEPTIONS", {("plugins/ndf/skills/pr/SKILL.md", "fix"): "#344"}
    )
    assert checker.main(["--root", str(root)]) == 1
