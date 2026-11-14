"""引継ぎ文書の大きさ（#1785）と、書いたパスの実在（#1835）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.domains.records import handoff
from ndf.domains.records import handoff_decide as hd

LIMITS = hd.Limits(max_lines=20, max_bytes=2000)
BODY = "# 引継ぎ\n\n## 現在地\n\n`docs/a.md` を作成済み。\n\n## 次にやること\n\n- 続ける\n"


def test_check_body_passes_template_sections_with_existing_paths() -> None:
    got = hd.check_body(BODY, lambda p: True, LIMITS)
    assert got.passed and got.lines == len(BODY.splitlines())


def test_check_body_reports_missing_paths_and_unknown_sections() -> None:
    text = BODY + "\n## セッション 95 の記録\n\n- 古い\n"
    got = hd.check_body(text, lambda p: False, LIMITS)
    assert not got.passed
    assert got.missing_paths == ["docs/a.md"]
    assert got.violations == ["雛形に無い節: セッション 95 の記録"]


def test_check_body_reports_lines_and_bytes_over_the_limits() -> None:
    text = BODY + "\n".join("x" * 200 for _ in range(30)) + "\n"
    got = hd.check_body(text, lambda p: True, LIMITS)
    assert any("行数" in v for v in got.violations) and any("バイト数" in v for v in got.violations)


def test_path_candidates_skip_urls_commands_and_placeholders() -> None:
    text = "`https://x/y.md` `--resume` `<id>.json` `plugins/ndf-next/src` `README.md` `a b`"
    assert hd.path_candidates(text) == ["plugins/ndf-next/src", "README.md"]


def test_absolute_paths_are_candidates_and_missing_ones_fail(tmp_path: Path) -> None:
    gone = tmp_path / "none.md"
    assert hd.path_candidates(f"`{gone}` `/`") == [str(gone)]
    report = handoff.check_file(_write(tmp_path / "h.md", f"## 現在地\n\n`{gone}` を作成済み。\n"), tmp_path, LIMITS)
    assert report.missing_paths == [str(gone)]


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_trim_keeps_only_template_sections_and_ignores_fenced_headings() -> None:
    text = BODY + "\n## 前の会話の進み\n\n- 済み\n\n## 運用の知見\n\n```\n## コードの中\n```\n"
    kept, moved = hd.trim(text)
    assert [n for n, _ in hd.sections(kept) if n] == ["現在地", "次にやること", "運用の知見"]
    assert len(moved) == 1 and moved[0].startswith("## 前の会話の進み")


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


def test_trim_file_keeps_the_body_when_history_cannot_be_written(tmp_path: Path) -> None:
    doc = tmp_path / "handoff.md"
    text = BODY + "\n## 前の会話の進み\n\n- 済み\n"
    doc.write_text(text, encoding="utf-8")
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="utf-8")
    with pytest.raises(OSError):
        handoff.trim_file(doc, blocker / "history.md")
    assert doc.read_text(encoding="utf-8") == text
