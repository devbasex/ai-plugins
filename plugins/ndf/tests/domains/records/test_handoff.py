"""引継ぎ文書の大きさ（#1785）と、書いたパスの実在（#1835）と、見出しの頭・必須・順（#3843）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.domains.records import handoff
from ndf.domains.records import handoff_decide as hd

LIMITS = hd.Limits(max_lines=40, max_bytes=2000)
BODY = (
    "# 引継ぎ\n\n## 進め方（利用者の指示）\n\n- pace: auto\n\n## 課題の順\n\n- #1\n\n"
    "## 現在地\n\n`docs/a.md` を作成済み。\n\n## 次にやること\n\n- 続ける\n\n## 次に実行するコマンド\n\n- 続きから\n"
)
FULL = (
    "## 進め方（利用者の指示）\n\n- a\n\n## 課題の順\n\n- b\n\n## 現在地\n\n- c\n\n## 次にやること\n\n- d\n\n"
    "## 今の会話の進み\n\n- e\n\n## 運用の知見\n\n- f\n\n## 次に実行するコマンド\n\n- g\n"
)


def _without(text: str, name: str) -> str:
    """`## <name>` のセクションを除いた本文。"""
    return "\n".join("\n".join(body) for n, body in hd.sections(text) if n != name) + "\n"


def _swap(text: str, a: str, b: str) -> str:
    return text.replace(f"## {a}\n", "## @@\n").replace(f"## {b}\n", f"## {a}\n").replace("## @@\n", f"## {b}\n")


def test_check_body_passes_template_sections_with_existing_paths() -> None:
    got = hd.check_body(BODY, lambda p: True, LIMITS)
    assert got.passed and got.lines == len(BODY.splitlines())


def test_check_body_reports_missing_paths_and_unknown_sections() -> None:
    text = BODY + "\n## セッション 95 の記録\n\n- 古い\n"
    got = hd.check_body(text, lambda p: False, LIMITS)
    assert not got.passed
    assert got.missing_paths == ["docs/a.md"]
    assert got.violations == ["テンプレートに無いセクション: セッション 95 の記録"]


def test_check_body_reports_lines_and_bytes_over_the_limits() -> None:
    text = BODY + "\n".join("x" * 200 for _ in range(40)) + "\n"
    got = hd.check_body(text, lambda p: True, LIMITS)
    assert any("行数" in v for v in got.violations) and any("バイト数" in v for v in got.violations)


def test_path_candidates_skip_urls_commands_and_placeholders() -> None:
    text = "`https://x/y.md` `--resume` `<id>.json` `plugins/ndf/src` `README.md` `a b` を作成済み"
    assert hd.path_candidates(text) == ["plugins/ndf/src", "README.md"]


def test_path_candidates_come_only_from_lines_that_say_created() -> None:
    text = "バージョンは `10.17.x`（`ndf--v10.17.51`）。`origin/develop` の `facts.value` と `result.OK` を読む\n`docs/b.md` を作成した\n"
    assert hd.path_candidates(text) == ["docs/b.md"]


def test_absolute_paths_are_candidates_and_missing_ones_fail(tmp_path: Path) -> None:
    gone = tmp_path / "none.md"
    assert hd.path_candidates(f"`{gone}` `/` を作成済み") == [str(gone)]
    report = handoff.check_file(_write(tmp_path / "h.md", f"## 現在地\n\n`{gone}` を作成済み。\n"), tmp_path, LIMITS)
    assert "必須のセクションが無い: 課題の順" in report.violations
    assert report.missing_paths == [str(gone)]


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_trim_keeps_only_template_sections_and_ignores_fenced_headings() -> None:
    text = BODY + "\n## 前の会話の進み\n\n- 済み\n\n## 運用の知見\n\n```\n## コードの中\n```\n"
    kept, moved = hd.trim(text)
    assert [n for n, _ in hd.sections(kept) if n] == [*hd.REQUIRED_SECTIONS, "運用の知見"]
    assert len(moved) == 1 and moved[0].startswith("## 前の会話の進み")


@pytest.mark.parametrize(
    ("heading", "name"),
    [
        ("現在地", "現在地"),
        ("現在地（2026-11-19）", "現在地"),
        ("次にやること 2026-11-19", "次にやること"),
        ("進め方（利用者の指示）（11-19）", "進め方（利用者の指示）"),
        ("現在地\u3000（全角の空白）", "現在地"),
        ("現在地(x)", "現在地"),
        ("現在地メモ", None),
        ("前の会話の進み", None),
        ("セッション 95 の記録", None),
    ],
)
def test_section_of_matches_the_head_followed_by_a_space_or_an_open_paren(heading: str, name: str | None) -> None:
    assert hd.section_of(heading) == name


@pytest.mark.parametrize("name", hd.TEMPLATE_SECTIONS)
def test_section_of_maps_every_template_name_to_itself(name: str) -> None:
    assert hd.section_of(name) == name


@pytest.mark.parametrize(
    ("heading", "name"),
    [
        ("", None),
        ("現在", None),
        ("次に", None),
        ("進め方", None),
        ("現在地 ", "現在地"),
        ("現在地\t", "現在地"),
        ("現在地（", "現在地"),
        ("現在地(", "現在地"),
        ("現在地）", None),
        ("現在地-x", None),
        ("現在地:", None),
        ("次に実行するコマンド（続き）", "次に実行するコマンド"),
        (" 現在地", None),
    ],
)
def test_section_of_boundaries_of_the_exact_and_head_match(heading: str, name: str | None) -> None:
    assert hd.section_of(heading) == name


def test_required_sections_are_a_template_subsequence() -> None:
    assert [n for n in hd.TEMPLATE_SECTIONS if n in hd.REQUIRED_SECTIONS] == list(hd.REQUIRED_SECTIONS)


def test_check_body_passes_the_full_template_and_dated_headings_without_the_optional_ones() -> None:
    assert hd.check_body(FULL, lambda p: True, LIMITS).passed
    lean = _without(_without(FULL, "今の会話の進み"), "運用の知見")
    dated = lean.replace("## 現在地\n", "## 現在地（2026-11-19）\n").replace("## 次にやること\n", "## 次にやること 2026-11-19\n")
    got = hd.check_body(dated, lambda p: True, LIMITS)
    assert got.passed, got.violations


@pytest.mark.parametrize("name", hd.REQUIRED_SECTIONS)
def test_check_body_reports_each_missing_required_section(name: str) -> None:
    got = hd.check_body(_without(FULL, name), lambda p: True, LIMITS)
    assert got.violations == [f"必須のセクションが無い: {name}"]


def test_check_body_reports_reversed_order_and_overlap_by_the_head() -> None:
    got = hd.check_body(_swap(FULL, "課題の順", "現在地"), lambda p: True, LIMITS)
    assert got.violations == ["セクションの順が逆: 課題の順 が 現在地 より前にある"]
    twice = FULL.replace("## 次にやること\n", "## 現在地（2026-11-19）\n\n- 2 つ目\n\n## 次にやること\n")
    assert hd.check_body(twice, lambda p: True, LIMITS).violations == ["セクションが重なる: 現在地"]


def test_trim_keeps_dated_headings_and_moves_lookalikes() -> None:
    text = FULL.replace("## 現在地\n", "## 現在地（2026-11-19）\n").replace("## 次にやること\n", "## 次にやること 2026-11-19\n")
    assert hd.trim(text) == (text, [])
    kept, moved = hd.trim(text + "\n## 現在地メモ\n\n- x\n\n## 前の会話の進み\n\n- y\n")
    assert [m.splitlines()[0] for m in moved] == ["## 現在地メモ", "## 前の会話の進み"]
    assert hd.check_body(kept, lambda p: True, LIMITS).passed


def test_trim_file_writes_history_first_and_check_file_resolves_from_root(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("x", encoding="utf-8")
    doc = tmp_path / "handoff.md"
    doc.write_text(BODY + "\n## 前の会話の進み\n\n- 退避する行\n", encoding="utf-8")
    history = tmp_path / "hist" / "handoff-history.md"
    assert handoff.trim_file(doc, history) == ["## 前の会話の進み"]
    assert "退避する行" in history.read_text(encoding="utf-8") and "退避する行" not in doc.read_text(encoding="utf-8")
    assert handoff.trim_file(doc, history) == []
    assert handoff.check_file(doc, tmp_path, handoff.body_limits()).passed


def test_trim_file_refuses_a_history_that_is_the_body_itself(tmp_path: Path) -> None:
    doc = tmp_path / "handoff.md"
    text = BODY + "\n## 前の会話の進み\n\n- 済み\n"
    doc.write_text(text, encoding="utf-8")
    (tmp_path / "link.md").symlink_to(doc)
    for same in (doc, tmp_path / "." / "handoff.md", tmp_path / "link.md"):
        with pytest.raises(OSError):
            handoff.trim_file(doc, same)
    assert doc.read_text(encoding="utf-8") == text


def test_trim_file_keeps_the_body_when_history_cannot_be_written(tmp_path: Path) -> None:
    doc = tmp_path / "handoff.md"
    text = BODY + "\n## 前の会話の進み\n\n- 済み\n"
    doc.write_text(text, encoding="utf-8")
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="utf-8")
    with pytest.raises(OSError):
        handoff.trim_file(doc, blocker / "history.md")
    assert doc.read_text(encoding="utf-8") == text
