"""確定仕様化: 複数の確定仕様をすべてコミットに入れ、語の SSOT を本文で選ぶ（#1715）。"""

from __future__ import annotations

import json
from pathlib import Path

import git_sandbox
import pytest

from ndf import cli
from ndf.domains.records import spec
from ndf.domains.records import spec_decide as sd

G = {
    "contexts": [{"id": "c", "name": "c"}],
    "terms": [
        {"context": "c", "term": "使用量ログ", "meaning": "m", "pending_source": "issues/d.md"},
        {"context": "c", "term": "バージョン", "meaning": "m", "source": "docs/x.md"},
    ],
}


def test_plan_picks_the_only_spec_that_holds_the_term() -> None:
    plan = sd.plan_glossary(G, {"issues/d.md"}, {"docs/a.md": "使用量ログの説明", "docs/b.md": "別"}, {})
    assert plan is not None and plan.sources == {("c", "使用量ログ"): "docs/a.md"}
    applied = sd.apply_plan(G, plan)
    moved = applied["terms"][0]
    assert moved["source"] == "docs/a.md" and "pending_source" not in moved
    assert "pending_source" in G["terms"][0]


@pytest.mark.parametrize("texts", [{"docs/a.md": "無い"}, {"docs/a.md": "使用量ログ", "docs/b.md": "使用量ログ"}])
def test_plan_stops_instead_of_falling_back_to_the_first_spec(texts: dict[str, str]) -> None:
    with pytest.raises(sd.SourceError):
        sd.plan_glossary(G, {"issues/d.md"}, texts, {})


def test_override_wins_and_must_name_a_passed_spec() -> None:
    texts = {"docs/a.md": "使用量ログ", "docs/b.md": "使用量ログ"}
    plan = sd.plan_glossary(G, {"issues/d.md"}, texts, sd.parse_overrides(["使用量ログ=docs/b.md"]))
    assert plan is not None and plan.sources[("c", "使用量ログ")] == "docs/b.md"
    with pytest.raises(ValueError, match="渡した確定仕様に無い"):
        sd.plan_glossary(G, {"issues/d.md"}, texts, {"使用量ログ": "docs/z.md"})


def test_parse_overrides_rejects_missing_separator() -> None:
    with pytest.raises(ValueError):
        sd.parse_overrides(["使用量ログ"])


def test_commit_paths_keep_every_spec() -> None:
    got = sd.commit_paths(["docs/a.md", "docs/b.md"], ["glossary.json", "docs/a.md"], ["issues/d.md"])
    assert got == ["docs/a.md", "docs/b.md", "glossary.json", "issues/d.md"]


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".ndf").mkdir()
    decl = {"source": "glossary.json", "document": "docs/words.md"}
    (tmp_path / ".ndf" / "glossary.json").write_text(json.dumps(decl), encoding="utf-8")
    (tmp_path / "glossary.json").write_text(json.dumps(G, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("使用量ログの説明", encoding="utf-8")
    (tmp_path / "docs" / "b.md").write_text("別", encoding="utf-8")
    (tmp_path / "issues").mkdir()
    (tmp_path / "issues" / "d.md").write_text("設計", encoding="utf-8")
    return tmp_path


def test_finalize_moves_the_source_renders_and_removes_the_design(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    paths = spec.finalize(root, ["docs/a.md", "docs/b.md"], ["issues/d.md"], {})
    assert paths[:2] == ["docs/a.md", "docs/b.md"] and "issues/d.md" in paths and "docs/words.md" in paths
    assert not (root / "issues" / "d.md").exists()
    g = json.loads((root / "glossary.json").read_text(encoding="utf-8"))
    assert g["terms"][0]["source"] == "docs/a.md"


def test_finalize_matches_the_design_written_another_way(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    paths = spec.finalize(root, ["./docs/a.md", str(root / "docs" / "b.md")], ["./issues/d.md"], {})
    assert paths[:2] == ["docs/a.md", "docs/b.md"] and "issues/d.md" in paths
    g = json.loads((root / "glossary.json").read_text(encoding="utf-8"))
    assert g["terms"][0]["source"] == "docs/a.md" and "pending_source" not in g["terms"][0]


def test_finalize_refuses_a_path_outside_the_repo(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    with pytest.raises(spec.SpecError):
        spec.finalize(root, ["docs/a.md"], ["../d.md"], {})


def test_finalize_writes_nothing_when_the_source_is_ambiguous(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "docs" / "b.md").write_text("使用量ログも", encoding="utf-8")
    before = (root / "glossary.json").read_text(encoding="utf-8")
    with pytest.raises(sd.SourceError):
        spec.finalize(root, ["docs/a.md", "docs/b.md"], ["issues/d.md"], {})
    assert (root / "glossary.json").read_text(encoding="utf-8") == before and (root / "issues" / "d.md").exists()


# 現状固定（I-010 の前）: `ndf records spec finalize`（cli._spec）の結果 JSON
@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    git_sandbox.sh(tmp_path, "init", "-q", str(tmp_path / "r"))
    root = _repo(tmp_path / "r")
    git_sandbox.sh(root, "add", "-A")
    git_sandbox.sh(root, "commit", "-q", "-m", "init")
    return root


def _finalize(root: Path, capsys: pytest.CaptureFixture[str], last_result, *argv: str) -> tuple[int, dict]:
    code = cli.main(["records", "--cwd", str(root), "spec", "finalize", *argv])
    return code, last_result(capsys.readouterr().out)


def test_cli_finalize_lists_the_paths_without_committing(git_repo: Path, capsys, last_result) -> None:
    code, got = _finalize(git_repo, capsys, last_result, "--spec", "docs/a.md", "--spec", "docs/b.md", "--remove", "issues/d.md")
    assert code == 0 and got["status"] == "ok" and got["summary"] == "確定仕様化: コミットに入れるパス 6 件"
    assert got["items"] == [
        {"path": "docs/a.md"},
        {"path": "docs/b.md"},
        {"path": "glossary.json"},
        {"path": "docs/words.md"},
        {"path": "docs/words/c.md"},
        {"path": "issues/d.md"},
    ]
    assert git_sandbox.sh(git_repo, "log", "-1", "--format=%s", strip=True) == "init"


def test_cli_finalize_reports_ambiguous_and_missing_terms_as_items(git_repo: Path, capsys, last_result) -> None:
    (git_repo / "docs" / "b.md").write_text("使用量ログも", encoding="utf-8")
    code, got = _finalize(git_repo, capsys, last_result, "--spec", "docs/a.md", "--spec", "docs/b.md", "--remove", "issues/d.md")
    assert code == 3 and got["cause"]["kind"] == "precondition"
    assert got["items"] == [{"term": "使用量ログ", "reason": "ambiguous", "files": ["docs/a.md", "docs/b.md"]}]
    (git_repo / "docs" / "b.md").write_text("別", encoding="utf-8")
    code, got = _finalize(git_repo, capsys, last_result, "--spec", "docs/b.md", "--remove", "issues/d.md")
    assert code == 3 and got["cause"]["kind"] == "precondition"
    assert got["summary"] == "どの確定仕様にも無い語: 使用量ログ。--source-of <語>=<ファイル> で SSOT を明示する"
    assert got["items"] == [{"term": "使用量ログ", "reason": "missing"}]


def test_cli_finalize_stops_on_a_bad_override_or_an_unreadable_spec(git_repo: Path, capsys, last_result) -> None:
    code, got = _finalize(git_repo, capsys, last_result, "--spec", "docs/a.md", "--source-of", "使用量ログ")
    assert code == 3 and got["cause"]["kind"] == "precondition" and got["items"] == []
    assert got["summary"] == "--source-of の形が違う（<語>=<ファイル>）: 使用量ログ"
    code, got = _finalize(git_repo, capsys, last_result, "--spec", "docs/none.md")
    assert code == 3 and got["cause"]["kind"] == "precondition" and got["items"] == []
    assert got["summary"].startswith("確定仕様 docs/none.md を読めない: ")


def test_cli_finalize_with_message_commits_only_the_paths(git_repo: Path, capsys, last_result) -> None:
    (git_repo / "other.md").write_text("利用者の変更", encoding="utf-8")
    git_sandbox.sh(git_repo, "add", "other.md")
    code, got = _finalize(git_repo, capsys, last_result, "--spec", "docs/a.md", "--spec", "docs/b.md",
                          "--remove", "issues/d.md", "-m", "確定仕様化")  # fmt: skip
    assert code == 0 and got["status"] == "ok" and len(got["items"]) == 6
    assert got["metrics"]["commit"] == git_sandbox.sh(git_repo, "rev-parse", "HEAD", strip=True)
    assert git_sandbox.sh(git_repo, "log", "-1", "--format=%s", strip=True) == "確定仕様化"
    # 先にステージされていた別の変更はコミットへ入らず、ステージに残る
    assert git_sandbox.sh(git_repo, "status", "--porcelain", strip=True) == "A  other.md"


def test_finalize_skips_the_glossary_without_its_ndf_settings(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".ndf" / "glossary.json").unlink()
    before = (root / "glossary.json").read_text(encoding="utf-8")
    assert spec.finalize(root, ["docs/a.md"], ["issues/d.md"], {}) == ["docs/a.md", "issues/d.md"]
    assert not (root / "issues" / "d.md").exists() and (root / "glossary.json").read_text(encoding="utf-8") == before
