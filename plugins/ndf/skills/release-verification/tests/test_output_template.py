"""release-verification の出力物テンプレート契約を固定するテスト。"""
from __future__ import annotations

from pathlib import Path
import re

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL = SKILL_DIR / "SKILL.md"


def release_verification_output_template() -> str:
    """`release-verification` の「出力物」節にある markdown 雛形を返す。

    節は `## 出力物` から次の実在の節見出しまで。**節の中の ```markdown``` の柵で
    囲まれた雛形だけを取り出す。** 柵の中の `## リリース後テスト` は雛形の一部であり、
    節の境目ではない。柵で切ると、その混同を避けられる。
    """
    body = SKILL.read_text(encoding="utf-8")
    section = body.split("\n## 出力物\n", maxsplit=1)[1]
    found = re.search(r"^```markdown\n(.*?)^```$", section, re.MULTILINE | re.DOTALL)
    assert found, f"出力物の markdown 雛形を読み取れない: {section[:200]}"
    return found.group(1)


def test_the_release_verification_template_carries_the_target_version_once() -> None:
    """現状固定: 雛形は `対象の版:` の行を 1 つ持つ。

    「まとまりを閉じる」の「配布の記録」の読み取りは、この行で本番の版のブロックを
    選ぶ。行が無い・複数あると、どの版を確かめたのかが決まらない。
    """
    template = release_verification_output_template()
    targets = [line for line in template.splitlines() if line.startswith("対象の版:")]

    assert len(targets) == 1, template
    assert "<配布した版>" in targets[0], targets[0]


def test_the_release_verification_template_starts_the_table_with_the_issue_column() -> None:
    """現状固定: 表の先頭の列が `課題` で、`結果` の列も持つ。

    課題ごとに閉じる判定を読むため、行がどの課題の受け入れ条件だったかを先頭の列で
    引く。結果の列が合否を持つ。どちらが欠けても課題別の閉じる判定へ渡せない。
    """
    template = release_verification_output_template()
    rows = [line for line in template.splitlines() if line.startswith("| ")]
    # 見出しの行・区切りの行・各データ行。
    assert len(rows) >= 3, rows

    header = [cell.strip() for cell in rows[0].strip("|").split("|")]
    assert header[0] == "課題", header
    assert header[-1] == "結果", header
    assert "受け入れ条件" in header, header


def test_the_release_verification_template_maps_each_condition_to_an_issue() -> None:
    """現状固定: 各受け入れ条件の行が、課題の列と結果の列を対応付けて読める。

    区切りの行を除いた各データ行で、先頭の課題の列が `#<番号>` の形を持ち、結果の列が
    合格・保留のいずれかの語を持つ。これがまとまりの課題別の閉じる判定へ渡す形である。
    """
    template = release_verification_output_template()
    rows = [line for line in template.splitlines() if line.startswith("| ")]
    header = [cell.strip() for cell in rows[0].strip("|").split("|")]
    issue_at = header.index("課題")
    result_at = header.index("結果")

    # 見出しの行と区切りの行（各セルが `---`）を除いた残りがデータ行。
    def is_separator(row: str) -> bool:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        return all(set(cell) == {"-"} for cell in cells)

    data = [
        [cell.strip() for cell in row.strip("|").split("|")]
        for row in rows[1:]
        if not is_separator(row)
    ]
    assert data, rows

    issues = set()
    for cells in data:
        assert re.match(r"^#\d+$", cells[issue_at]), cells
        assert re.search(r"合格|保留", cells[result_at]), cells
        issues.add(cells[issue_at])

    # 雛形は複数の課題（#561 / #623）を、それぞれの受け入れ条件の行へ対応付けて示す。
    assert issues == {"#561", "#623"}, issues
