"""確定仕様化: 複数の確定仕様をすべてコミットに入れ、語の正本を本文で選ぶ（#1715）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.domains.records import spec
from ndf.domains.records import spec_decide as sd

G = {
    "contexts": [{"id": "c", "name": "c"}],
    "terms": [
        {"context": "c", "term": "帳簿", "meaning": "m", "pending_source": "issues/d.md"},
        {"context": "c", "term": "版", "meaning": "m", "source": "docs/x.md"},
    ],
}


def test_plan_picks_the_only_spec_that_holds_the_term() -> None:
    plan = sd.plan_glossary(G, {"issues/d.md"}, {"docs/a.md": "帳簿の説明", "docs/b.md": "別"}, {})
    assert plan is not None and plan.sources == {("c", "帳簿"): "docs/a.md"}
    applied = sd.apply_plan(G, plan)
    moved = applied["terms"][0]
    assert moved["source"] == "docs/a.md" and "pending_source" not in moved
    assert "pending_source" in G["terms"][0]


@pytest.mark.parametrize("texts", [{"docs/a.md": "無い"}, {"docs/a.md": "帳簿", "docs/b.md": "帳簿"}])
def test_plan_stops_instead_of_falling_back_to_the_first_spec(texts: dict[str, str]) -> None:
    with pytest.raises(sd.SourceError):
        sd.plan_glossary(G, {"issues/d.md"}, texts, {})


def test_override_wins_and_must_name_a_passed_spec() -> None:
    texts = {"docs/a.md": "帳簿", "docs/b.md": "帳簿"}
    plan = sd.plan_glossary(G, {"issues/d.md"}, texts, sd.parse_overrides(["帳簿=docs/b.md"]))
    assert plan is not None and plan.sources[("c", "帳簿")] == "docs/b.md"
    with pytest.raises(ValueError, match="渡した確定仕様に無い"):
        sd.plan_glossary(G, {"issues/d.md"}, texts, {"帳簿": "docs/z.md"})


def test_parse_overrides_rejects_missing_separator() -> None:
    with pytest.raises(ValueError):
        sd.parse_overrides(["帳簿"])


def test_commit_paths_keep_every_spec() -> None:
    got = sd.commit_paths(["docs/a.md", "docs/b.md"], ["glossary.json", "docs/a.md"], ["issues/d.md"])
    assert got == ["docs/a.md", "docs/b.md", "glossary.json", "issues/d.md"]


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".ndf").mkdir()
    decl = {"source": "glossary.json", "document": "docs/glossary.md"}
    (tmp_path / ".ndf" / "glossary.json").write_text(json.dumps(decl), encoding="utf-8")
    (tmp_path / "glossary.json").write_text(json.dumps(G, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("帳簿の説明", encoding="utf-8")
    (tmp_path / "docs" / "b.md").write_text("別", encoding="utf-8")
    (tmp_path / "issues").mkdir()
    (tmp_path / "issues" / "d.md").write_text("設計", encoding="utf-8")
    return tmp_path


def test_finalize_moves_the_source_renders_and_removes_the_design(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    paths = spec.finalize(root, ["docs/a.md", "docs/b.md"], ["issues/d.md"], {})
    assert paths[:2] == ["docs/a.md", "docs/b.md"] and "issues/d.md" in paths and "docs/glossary.md" in paths
    assert not (root / "issues" / "d.md").exists()
    g = json.loads((root / "glossary.json").read_text(encoding="utf-8"))
    assert g["terms"][0]["source"] == "docs/a.md"


def test_finalize_writes_nothing_when_the_source_is_ambiguous(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "docs" / "b.md").write_text("帳簿も", encoding="utf-8")
    before = (root / "glossary.json").read_text(encoding="utf-8")
    with pytest.raises(sd.SourceError):
        spec.finalize(root, ["docs/a.md", "docs/b.md"], ["issues/d.md"], {})
    assert (root / "glossary.json").read_text(encoding="utf-8") == before and (root / "issues" / "d.md").exists()
