"""シェルコマンドからの書き込み先の推定を検証する（受け入れ条件 6〜8 の Bash 側）。

対象は直接の書き換え・出力の付け替え・標準入力からの書き出し・複製と移動の 4 形式に
限る。推定できないものは案内を出さないため、終了コード 1 と空の出力になることを
併せて確かめる。
"""
from __future__ import annotations

import pytest

from conftest import run_lib


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
