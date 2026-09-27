"""配備の完了の確かめ方が `release` に書かれていることを固定する（#228）。

`release` は公開の操作を実行するところまでを書いており、それが済んだことをどう確かめるかを
書いていなかった。書いていない部分は実行者がその場で決めることになり、#228 の事例では
ログに出ると見込んだ語を待つループになった。公開の操作は成功していたが、その語はログに
出ないため、待ちだけが残った。

**記載を消したときも落ちる。** 位置を決める語が見つからなければ、読み取れないこととして
失敗させる（`scripts/check-doc-staleness.py` と同じ扱い）。読み取りの関数を文字列に対して
呼べる形にしてあるのは、記載を取り除いた文字列で落ちることを同じ検査で確かめるためである。

`bash` だけは外部コマンドを使う。雛形は構文の検査だけでなく、実際に実行して抜けた理由を
確かめる（#295 の指摘）。リポジトリの根の `conftest.py` の必須コマンドの一覧はこの Skill の
担当の境界の外にあるため、見つからないことをこのファイルの中で失敗として扱う。
"""
from __future__ import annotations

import os
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

# `completion-check.md` が持つ 4 つの節。並びも手順の順序に合わせる。
COMPLETION_SECTIONS = [
    "## ログで判定するときの決まり",
    "## 3 軸を見張るときの雛形",
    "## 待ちの上限と照会の間隔",
    "## 上限に達したとき",
]

TEMPLATE_SECTION = COMPLETION_SECTIONS[1]
LIMIT_SECTION = COMPLETION_SECTIONS[2]

FORM_INDEX = "## 形ごとのファイル"

# #554 が退避の手順へ指示書の検査を足した分（4 行。**詳細は
# `references/instruction-files.md` が持ち、本文には呼び出しと参照への案内だけを置く**）と、
# #623 が足した「配布の記録」（別の実行の終わりの工程が読むブロック）と「まとまりを閉じる」を
# 含む。300 → 320 のときと同じく実測（353 行）へ余地を足して上げる。
SKILL_MD_MAX_LINES = 365


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
            collected.append(line)
            continue
        if not fenced and stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            if level <= depth:
                break
        collected.append(line)
    text = "\n".join(collected).strip()
    assert text, f"節の本文が空である: {heading}"
    return text


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


# --- 条件 1: 形ごとのファイルの索引 ---------------------------------------------------


def test_the_form_index_links_to_every_form_file() -> None:
    """索引の表が実在する `form-*.md` をすべて指す。

    実ファイルが増えても表へ足し忘れると、`SKILL.md` から辿れる先はそのままである。
    ファイルの有無ではなく、**索引から辿り着けるか**を見る。形の数は固定しない。
    """
    listed = [
        target
        for target in link_targets(section(read(DISTRIBUTION_FORMS), FORM_INDEX))
        if target.startswith("form-")
    ]
    assert sorted(listed) == sorted(p.name for p in REFERENCES.glob("form-*.md"))

# --- 条件 3: 形をまたぐ決まり ------------------------------------------------------


def template() -> str:
    """雛形の bash を返す。"""
    blocks = fenced_blocks(section(read(COMPLETION_CHECK), TEMPLATE_SECTION), "bash")
    assert len(blocks) == 1, f"雛形の bash が 1 つでない: {len(blocks)} 個"
    return blocks[0]


def bash_path() -> str:
    found = shutil.which("bash")
    assert found, "bash が見つからない。雛形を確かめられない"
    return found


def run_template(work: str, **variables: str) -> subprocess.CompletedProcess[str]:
    """雛形をそのまま実行する。上限と間隔に 0 を渡すと待機せずに抜ける。"""
    script = Path(work) / "template.sh"
    script.write_text(template(), encoding="utf-8")
    return subprocess.run(
        [bash_path(), str(script)],
        capture_output=True,
        text=True,
        cwd=work,
        env={"PATH": os.environ.get("PATH", ""), **variables},
        timeout=60,
    )


def watch(work: str, log_body: str | None, **overrides: str) -> subprocess.CompletedProcess[str]:
    """ログを用意して雛形を実行する。`None` を渡すとログを作らない。"""
    log = Path(work) / "run.log"
    if log_body is not None:
        log.write_text(log_body, encoding="utf-8")
    variables = {
        "LOG": str(log),
        "DONE": "[done]",
        "FAIL": "[fail]",
        "IDLE": "0",
        "LIMIT": "0",
        **overrides,
    }
    return run_template(work, **variables)


def test_the_template_is_valid_bash() -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".sh", encoding="utf-8") as handle:
        handle.write(template())
        handle.flush()
        done = subprocess.run(
            [bash_path(), "-n", handle.name], capture_output=True, text=True
        )
    assert done.returncode == 0, done.stderr


def test_the_template_leaves_on_the_word_it_was_given() -> None:
    with tempfile.TemporaryDirectory() as work:
        done = watch(work, "publishing\n[done] ok\n")
    assert done.stdout.split() == ["done"], done.stdout


def test_the_template_leaves_on_the_failure_word() -> None:
    with tempfile.TemporaryDirectory() as work:
        done = watch(work, "publishing\n[fail] rejected\n")
    assert done.stdout.split() == ["fail"], done.stdout


def test_the_template_leaves_on_the_limit_word() -> None:
    with tempfile.TemporaryDirectory() as work:
        done = watch(work, "publishing\n", IDLE="3600", LIMIT="0")
    assert done.stdout.split() == ["limit"], done.stdout
    assert not done.stderr, done.stderr


@pytest.mark.parametrize(
    "log_body, overrides, reason",
    [
        # 完了・失敗の語が無く、時間の条件だけが成立する。IDLE を大きくして limit で抜ける。
        ("publishing\n", {"IDLE": "3600", "LIMIT": "0"}, "limit"),
        # 完了と失敗の語が同時に載り、時間の条件も成立する。done が fail・時間より先に選ばれる。
        ("x\n[done] ok\n[fail] no\n", {"IDLE": "0", "LIMIT": "0"}, "done"),
        # ログ内の順序を入れ替えても、grep の照合は行の順序に依らず done を先に選ぶ。
        ("x\n[fail] no\n[done] ok\n", {"IDLE": "0", "LIMIT": "0"}, "done"),
        # 失敗の語だけが載り、時間の条件も成立する。fail が時間より先に選ばれる。
        ("x\n[fail] no\n", {"IDLE": "0", "LIMIT": "0"}, "fail"),
    ],
    ids=["limit", "done-over-fail-and-time", "done-regardless-of-log-order", "fail-over-time"],
)
def test_the_template_chooses_by_priority_when_conditions_coincide(
    log_body: str, overrides: dict[str, str], reason: str
) -> None:
    """複数の条件が同時に成立したときの選択を固定する（done > fail > idle > limit）。

    既存の idle ケース（`test_the_word_is_matched_as_a_fixed_string`）と合わせて優先順位を
    守る。実時間の待機や内部コマンドの呼び出し回数は検証しない。
    """
    with tempfile.TemporaryDirectory() as work:
        done = watch(work, log_body, **overrides)
    assert done.stdout.split() == [reason], done.stdout
    assert done.returncode == 0, done.stderr
    assert not done.stderr, done.stderr


def test_the_word_is_matched_as_a_fixed_string() -> None:
    """`[done]` を正規表現として渡すと `d` だけの行に一致する（#295 の指摘）。"""
    with tempfile.TemporaryDirectory() as work:
        done = watch(work, "starting d job\n")
    assert done.stdout.split() == ["idle"], f"完了の語が誤って一致した: {done.stdout}"


def test_a_word_starting_with_a_hyphen_is_not_read_as_an_option() -> None:
    with tempfile.TemporaryDirectory() as work:
        done = watch(work, "x --done y\n", DONE="--done")
    assert done.stdout.split() == ["done"], done.stdout
    assert not done.stderr, done.stderr


def test_the_template_starts_before_the_log_exists() -> None:
    """対象がログを作る前にループへ入っても、読み取りの誤りを出さない（#295 の指摘）。"""
    with tempfile.TemporaryDirectory() as work:
        done = watch(work, None)
    assert done.stdout.split() == ["idle"], done.stdout
    assert not done.stderr, done.stderr


def test_an_unset_variable_stops_before_the_loop() -> None:
    with tempfile.TemporaryDirectory() as work:
        done = run_template(work, LOG=str(Path(work) / "run.log"), DONE="[done]")
    assert done.returncode != 0, "未設定の変数のまま進んだ"
    assert not done.stdout.split(), done.stdout


# --- 条件 10: 記載を消したときも落ちる ---------------------------------------------


def test_section_keeps_fenced_headings_and_stops_at_the_next_section() -> None:
    """現状固定: 囲みの中の見出しでは節を終えず、囲みの外で区切る。"""
    body = """## 最初の節
本文。
```markdown
## 囲みの中の見出し
雛形の本文。
```
囲みの外の続き。
## 次の節
次の本文。
"""
    result = section(body, "## 最初の節")

    assert result == """本文。
```markdown
## 囲みの中の見出し
雛形の本文。
```
囲みの外の続き。"""


def test_a_missing_section_is_not_passed_over() -> None:
    with pytest.raises(AssertionError):
        section("# 見出しのない文書\n", COMPLETION_SECTIONS[0])


def test_an_empty_section_is_not_passed_over() -> None:
    with pytest.raises(AssertionError):
        section(f"{LIMIT_SECTION}\n\n## 次の節\n", LIMIT_SECTION)


# --- 条件 7: 分量 ------------------------------------------------------------------


def test_the_skill_md_stays_within_its_budget() -> None:
    lines = len(read(SKILL).splitlines())
    assert lines <= SKILL_MD_MAX_LINES, f"SKILL.md が {lines} 行"
