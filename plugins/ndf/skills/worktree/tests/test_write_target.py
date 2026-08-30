"""シェルコマンドからの書き込み先の推定を検証する（受け入れ条件 6〜8 の Bash 側）。

対象は直接の書き換え・出力の付け替え・標準入力からの書き出し・複製と移動の 4 形式に
限る。推定できないものは案内を出さないため、終了コード 1 と空の出力になることを
併せて確かめる。
"""
from __future__ import annotations

import pytest

from worktree_helpers import run_lib


def extract(command: str) -> tuple[list[str], int]:
    got = run_lib(f"wt_extract_write_target {command!r}; echo rc=$?")
    lines = [ln for ln in got.stdout.splitlines() if ln]
    rc = int(lines.pop().removeprefix("rc="))
    return lines, rc


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("sed -i 's/a/b/' plugins/ndf/README.md", "plugins/ndf/README.md"),
        ("sed -i.bak 's/a/b/' plugins/ndf/README.md", "plugins/ndf/README.md"),
        ("sed -ri 's/a b/c/' plugins/ndf/README.md", "plugins/ndf/README.md"),
        ("echo hi > plugins/ndf/README.md", "plugins/ndf/README.md"),
        ("echo hi >plugins/ndf/README.md", "plugins/ndf/README.md"),
        ("echo hi >> plugins/ndf/README.md", "plugins/ndf/README.md"),
        ("echo hi | tee plugins/ndf/README.md", "plugins/ndf/README.md"),
        ("echo hi | tee -a plugins/ndf/README.md", "plugins/ndf/README.md"),
        ("cp a.txt plugins/ndf/README.md", "plugins/ndf/README.md"),
        ("mv -f a.txt plugins/ndf/README.md", "plugins/ndf/README.md"),
        ('echo hi > "plugins/ndf/README.md"', "plugins/ndf/README.md"),
    ],
)
def test_detected_forms(command: str, expected: str) -> None:
    targets, rc = extract(command)
    assert rc == 0, command
    assert expected in targets, (command, targets)


@pytest.mark.parametrize(
    "command",
    [
        "cat plugins/ndf/README.md",
        "grep -r worktree plugins/",
        "sed -n '1,5p' plugins/ndf/README.md",
        "python3 scripts/check-skill-frontmatter.py",
        "ls -la",
        "git status --short",
    ],
)
def test_read_only_commands_are_not_detected(command: str) -> None:
    targets, rc = extract(command)
    assert rc == 1, (command, targets)
    assert targets == []


def test_stderr_redirection_is_not_a_path() -> None:
    """`2>&1` や `>&2` を書き込み先として拾わない。"""
    targets, rc = extract("make build 2>&1")
    assert rc == 1, targets
    targets, rc = extract("echo err >&2")
    assert rc == 1, targets


def test_compound_command_reports_each_target() -> None:
    targets, _ = extract("echo a > one.txt && echo b >> two.txt")
    assert targets == ["one.txt", "two.txt"], targets


def test_devnull_is_ignored() -> None:
    targets, rc = extract("command -v jq > /dev/null 2>&1")
    assert rc == 1, targets


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("sed -i 's/a/b/' one.md two.md", ["one.md", "two.md"]),
        ("sed -i -e 's/a/b/' one.md two.md", ["one.md", "two.md"]),
        ("sed -i --expression='s/a/b/' one.md two.md", ["one.md", "two.md"]),
        ("sed -i 's/a b/c d/' one.md two.md", ["one.md", "two.md"]),
    ],
)
def test_inplace_sed_reports_every_file(command: str, expected: list[str]) -> None:
    """複数ファイルを編集する in-place sed は、全ファイルを書き込み先として返す。"""
    targets, rc = extract(command)
    assert rc == 0, command
    assert targets == expected, (command, targets)


def test_sed_script_with_spaces_is_one_word() -> None:
    """引用符の中の空白でスクリプトが分かれ、ファイル名と取り違えられない。"""
    targets, _ = extract("sed -i 's/foo bar/baz/' plugins/ndf/README.md")
    assert targets == ["plugins/ndf/README.md"], targets


def test_quoted_path_with_space() -> None:
    targets, rc = extract('echo hi > "docs/my notes.md"')
    assert rc == 0
    assert targets == ["docs/my notes.md"], targets


def test_read_only_sed_with_multiple_files() -> None:
    targets, rc = extract("sed -n '1p' one.md two.md")
    assert rc == 1, targets


def test_tee_reports_every_file() -> None:
    """tee は並べたファイルすべてへ書き込む。"""
    targets, rc = extract("echo hi | tee one.md two.md")
    assert rc == 0
    assert targets == ["one.md", "two.md"], targets


def test_tee_with_option_and_multiple_files() -> None:
    targets, _ = extract("echo hi | tee -a one.md two.md")
    assert targets == ["one.md", "two.md"], targets


def test_sed_long_option_takes_a_separate_argument() -> None:
    """`--expression` / `--file` が `=` なしで引数を取る形でも、script をファイルと取り違えない。"""
    targets, _ = extract("sed -i --expression 's/a/b/' plugins/ndf/README.md")
    assert targets == ["plugins/ndf/README.md"], targets
    targets, _ = extract("sed -i -e 's/a/b/' --expression 's/c/d/' plugins/ndf/README.md")
    assert targets == ["plugins/ndf/README.md"], targets


@pytest.mark.parametrize(
    "command",
    [
        "cp -t plugins/ndf docs/a.md",
        "cp --target-directory=plugins/ndf docs/a.md",
        "cp --target-directory plugins/ndf docs/a.md",
        "mv -t plugins/ndf docs/a.md docs/b.md",
    ],
)
def test_target_directory_form_is_the_destination(command: str) -> None:
    """`-t <ディレクトリ>` を付けると宛先が先に来て、後ろの被演算子は複製元になる。"""
    targets, rc = extract(command)
    assert rc == 0, command
    assert targets == ["plugins/ndf"], (command, targets)
