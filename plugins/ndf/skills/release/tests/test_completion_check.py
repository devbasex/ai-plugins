"""配備の完了の確かめ方が `release` に書かれていることを固定する（#228）。

`release` は公開の操作を実行するところまでを書いており、それが済んだことをどう確かめるかを
書いていなかった。書いていない部分は実行者がその場で決めることになり、#228 の事例では
ログに出ると見込んだ語を待つループになった。公開の操作は成功していたが、その語はログに
出ないため、待ちだけが残った。

検査の対象は 3 つに分かれる。

| 何を | どこを見るか |
| --- | --- |
| 形ごとに違う値 | `references/form-*.md` の「完了の事実」の項目 |
| 形をまたぐ決まり | `references/completion-check.md` の 4 つの節 |
| 工程への結び付け | `SKILL.md` の手順 4・完了の判定・出力物・形ごとに変わるものの列挙 |

**記載を消したときも落ちる。** 位置を決める語が見つからなければ、読み取れないこととして
失敗させる（`scripts/check-doc-staleness.py` と同じ扱い）。読み取りの関数を文字列に対して
呼べる形にしてあるのは、記載を取り除いた文字列で落ちることを同じ検査で確かめるためである。

`bash -n` だけは外部コマンドを使う。リポジトリの根の `conftest.py` の必須コマンドの一覧は
この Skill の担当の境界の外にあるため、見つからないことをこのファイルの中で失敗として扱う。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL = SKILL_DIR / "SKILL.md"
REFERENCES = SKILL_DIR / "references"
DISTRIBUTION_FORMS = REFERENCES / "distribution-forms.md"
COMPLETION_CHECK = REFERENCES / "completion-check.md"
FORM_FILES = sorted(REFERENCES.glob("form-*.md"))

# `completion-check.md` が持つ 4 つの節。並びも手順の順序に合わせる。
COMPLETION_SECTIONS = [
    "## ログで判定するときの決まり",
    "## 3 軸を見張るときの雛形",
    "## 待ちの上限と照会の間隔",
    "## 上限に達したとき",
]

STEP_FOUR = "### 4. 公開する"
CRITERIA = "## 完了の判定"
OUTPUT = "## 出力物"
RULES_SECTION = COMPLETION_SECTIONS[0]
TEMPLATE_SECTION = COMPLETION_SECTIONS[1]
LIMIT_SECTION = COMPLETION_SECTIONS[2]

SKILL_MD_MAX_LINES = 300
MARKDOWN_MAX_LINES = 500


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def section(body: str, heading: str) -> str:
    """見出しから、同じか浅い深さの次の見出しまでを返す。\n\n    囲みの中の行は見出しとして数えない。出力物の雛形は Markdown の見出しを含む。\n    """
    depth = len(heading) - len(heading.lstrip("#"))
    lines = body.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        raise AssertionError(f"見出しが見つからない: {heading}")
    collected: list[str] = []
    fenced = False
    for line in lines[start + 1 :]:
        stripped = line.lstrip()
        if stripped.startswith("```"):
            fenced = not fenced
        elif not fenced and stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            if level <= depth:
                break
        collected.append(line)
    text = "\n".join(collected).strip()
    assert text, f"節の本文が空である: {heading}"
    return text


def enumeration(body: str) -> list[str]:
    """「形ごとに変わるもの」の列挙を、書かれた並びのまま返す。"""
    flat = body.replace("\n", "").replace("**", "")
    found = re.search(r"形ごとに変わるのは、?(.+?)の\s*(\d+)\s*つ", flat)
    assert found, "「形ごとに変わるもの」の列挙を読み取れない"
    items = [item.strip() for item in found.group(1).split("・")]
    assert all(items), "列挙に空の項目がある"
    assert len(items) == int(found.group(2)), (
        f"列挙の数と書かれた個数が食い違う: {len(items)} 個に対して {found.group(2)} と書かれている"
    )
    return items


def completion_facts(body: str) -> list[str]:
    """「完了の事実」の項目の本文を返す。続きの行も本文に含める。"""
    lines = body.splitlines()
    found: list[str] = []
    for index, line in enumerate(lines):
        head = re.match(r"- \*\*完了の事実\*\*[:：](.*)$", line.strip())
        if not head:
            continue
        parts = [head.group(1)]
        for follow in lines[index + 1 :]:
            if not follow.startswith(" ") or not follow.strip():
                break
            parts.append(follow.strip())
        found.append(" ".join(parts).strip())
    assert found, "「完了の事実」の項目が見つからない"
    assert all(found), "「完了の事実」の項目に本文が無い"
    return found


def table_rows(body: str, heading: str) -> list[list[str]]:
    """節の中の最初の表の本文の行を、セルの並びとして返す。"""
    rows: list[list[str]] = []
    seen_header = False
    for line in section(body, heading).splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if rows:
                break
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not seen_header:
            seen_header = True
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    assert rows, f"表を読み取れない: {heading}"
    return rows


def fenced_blocks(body: str, language: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] | None = None
    for line in body.splitlines():
        stripped = line.strip()
        if current is None:
            if stripped == f"```{language}":
                current = []
        elif stripped == "```":
            blocks.append("\n".join(current))
            current = None
        else:
            current.append(line)
    return blocks


def link_targets(body: str) -> list[str]:
    return re.findall(r"\[[^\]]+\]\(([^)]+)\)", body)


# --- 条件 1: 形ごとの「完了の事実」 -------------------------------------------------


def test_the_form_files_are_the_five_known_forms() -> None:
    assert [path.name for path in FORM_FILES] == [
        "form-desktop.md",
        "form-mobile.md",
        "form-package-plugin.md",
        "form-procedure.md",
        "form-service.md",
    ]


@pytest.mark.parametrize("path", FORM_FILES, ids=lambda path: path.name)
def test_every_form_file_states_one_completion_fact(path: Path) -> None:
    facts = completion_facts(read(path))
    assert len(facts) == 1, f"{path.name} の「完了の事実」が {len(facts)} 個ある"


def test_the_number_of_completion_facts_matches_the_number_of_form_files() -> None:
    total = sum(len(completion_facts(read(path))) for path in FORM_FILES)
    assert total == len(FORM_FILES)


def test_the_service_form_covers_the_image_registry() -> None:
    """公開の操作の途中で作られる配布物にも同じ考え方を当てる（#228 の事例）。"""
    body = read(REFERENCES / "form-service.md")
    assert "イメージ" in body


# --- 条件 2: 「形ごとに変わるもの」の列挙 ------------------------------------------


def test_the_two_enumerations_are_the_same_five_items_in_the_same_order() -> None:
    assert enumeration(read(SKILL)) == enumeration(read(DISTRIBUTION_FORMS))


def test_the_enumeration_has_five_items_including_the_completion_check() -> None:
    items = enumeration(read(SKILL))
    assert len(items) == 5
    assert "完了の確かめ方" in items


# --- 条件 3: 形をまたぐ決まり ------------------------------------------------------


@pytest.mark.parametrize("heading", COMPLETION_SECTIONS)
def test_the_completion_check_has_the_section(heading: str) -> None:
    assert section(read(COMPLETION_CHECK), heading)


def test_the_sections_are_written_in_this_order() -> None:
    body = read(COMPLETION_CHECK)
    positions = [body.index(heading) for heading in COMPLETION_SECTIONS]
    assert positions == sorted(positions)


def test_the_rules_table_separates_required_from_recommended() -> None:
    handling = [row[1] for row in table_rows(read(COMPLETION_CHECK), RULES_SECTION)]
    assert handling.count("必須") == 2, f"必須の決まりが 2 つでない: {handling}"
    assert handling.count("推奨") == 1, f"推奨の決まりが 1 つでない: {handling}"


def test_the_limit_section_states_the_minimum_interval() -> None:
    """照会のループに待機を必須とし、既定の秒数を書く。"""
    body = section(read(COMPLETION_CHECK), LIMIT_SECTION)
    assert "5 秒" in body
    assert "必須" in body


def test_the_template_is_valid_bash() -> None:
    blocks = fenced_blocks(section(read(COMPLETION_CHECK), TEMPLATE_SECTION), "bash")
    assert len(blocks) == 1, f"雛形の bash が 1 つでない: {len(blocks)} 個"
    assert "sleep" in blocks[0], "照会のループに待機が無い"
    bash = shutil.which("bash")
    assert bash, "bash が見つからない。雛形を確かめられない"
    with tempfile.NamedTemporaryFile("w", suffix=".sh", encoding="utf-8") as handle:
        handle.write(blocks[0])
        handle.flush()
        done = subprocess.run([bash, "-n", handle.name], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


# --- 条件 4〜6: 工程への結び付け ----------------------------------------------------


def test_step_four_points_to_the_shared_rules_and_the_form_files() -> None:
    body = section(read(SKILL), STEP_FOUR)
    assert "references/completion-check.md" in link_targets(body)
    assert "form-" in body, "形ごとのファイルを指していない"
    assert "完了の事実" in body


def test_the_completion_criteria_requires_the_completion_fact() -> None:
    bullets = [
        line for line in section(read(SKILL), CRITERIA).splitlines() if line.startswith("- ")
    ]
    assert any("完了の事実" in line for line in bullets), "完了の事実を求める項目が無い"


def test_the_report_template_has_a_line_for_the_checked_value() -> None:
    blocks = fenced_blocks(section(read(SKILL), OUTPUT), "markdown")
    assert blocks, "出力物の雛形が見つからない"
    lines = [line for line in blocks[0].splitlines() if line.startswith("完了の確認:")]
    assert len(lines) == 1, "出力物の雛形に「完了の確認」の行が無い"
    assert "<" in lines[0] and ">" in lines[0], "確かめた値を書く欄になっていない"


# --- 条件 7: 分量 ------------------------------------------------------------------


def test_the_skill_md_stays_within_its_budget() -> None:
    lines = len(read(SKILL).splitlines())
    assert lines <= SKILL_MD_MAX_LINES, f"SKILL.md が {lines} 行"


@pytest.mark.parametrize(
    "path", sorted(SKILL_DIR.rglob("*.md")), ids=lambda path: path.name
)
def test_every_markdown_stays_within_the_file_budget(path: Path) -> None:
    lines = len(read(path).splitlines())
    assert lines <= MARKDOWN_MAX_LINES, f"{path.name} が {lines} 行"


# --- 条件 10: 記載を消したときも落ちる ---------------------------------------------


def test_a_missing_section_is_not_passed_over() -> None:
    with pytest.raises(AssertionError):
        section("# 見出しのない文書\n", COMPLETION_SECTIONS[0])


def test_an_empty_section_is_not_passed_over() -> None:
    with pytest.raises(AssertionError):
        section(f"{LIMIT_SECTION}\n\n## 次の節\n", LIMIT_SECTION)


def test_a_missing_enumeration_is_not_passed_over() -> None:
    with pytest.raises(AssertionError):
        enumeration("形ごとに変わるものは書かれていない。\n")


def test_a_miscounted_enumeration_is_not_passed_over() -> None:
    with pytest.raises(AssertionError):
        enumeration("形ごとに変わるのは、起点の決め方・公開の仕方の 5 つである。\n")


def test_a_missing_completion_fact_is_not_passed_over() -> None:
    with pytest.raises(AssertionError):
        completion_facts("- **起点**: 直前の版のタグ\n")


def test_an_empty_completion_fact_is_not_passed_over() -> None:
    with pytest.raises(AssertionError):
        completion_facts("- **完了の事実**:\n")


def test_a_missing_table_is_not_passed_over() -> None:
    with pytest.raises(AssertionError):
        table_rows(f"{RULES_SECTION}\n\n表の無い本文。\n", RULES_SECTION)
