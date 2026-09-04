"""副コマンドの説明が実装と一致していることを固定する（#329）。

振動の検知は v9.8.0（#246）で「位置・近傍 3 行・本文の 3 つの一致」へ変わったが、
説明は `path:line` の重複率のまま残っていた。同じ一覧がモジュールの docstring と
`argparse` の `help` の 2 か所にあり、片方だけが実装から離れていた。

`argparse` の parser は `main()` の中で組み立てられるため、`--help` を副プロセスで
実行して出力を見る。利用者が読むものをそのまま検査できる。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

STATE_PY = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "state.py"

SUBCOMMANDS = (
    "init",
    "start-round",
    "read-result",
    "unresolved-threads",
    "judge",
    "check-oscillation",
    "merge-fix",
    "should-rotate",
    "set-current-pr",
    "verify-sweep",
    "report",
)

# `SKILL.md` の骨組みが分岐に使う終了コード。実装から読み取った値を書く。
BRANCHING_EXIT_CODES = {
    "start-round": ("1", "5", "8"),
    "judge": ("0", "2", "7"),
    "check-oscillation": ("2", "4"),
}


@pytest.fixture(scope="module")
def help_text() -> str:
    r = subprocess.run(
        [sys.executable, str(STATE_PY), "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout


def _entry(help_text: str, name: str) -> str:
    """副コマンドの一覧から、その 1 件の説明を取り出す。

    `argparse` は幅で折り返すため、次の副コマンドの行が来るまでを 1 件として読む。
    """
    lines = help_text.splitlines()
    starts = [
        i for i, line in enumerate(lines)
        if line.strip().startswith(f"{name} ") or line.strip() == name
    ]
    assert starts, f"{name} の説明が --help に無い"
    start = starts[-1]
    out = [lines[start].strip()]
    for line in lines[start + 1:]:
        stripped = line.strip()
        if not stripped or any(
            stripped.startswith(f"{other} ") or stripped == other
            for other in SUBCOMMANDS
        ):
            break
        out.append(stripped)
    return " ".join(out)


def test_the_help_does_not_name_the_old_criterion(help_text: str) -> None:
    """`path:line` の重複率は v9.8.0 で使われなくなった基準である。"""
    assert "path:line" not in help_text


def test_the_help_names_the_current_criterion(help_text: str) -> None:
    assert "同じ箇所" in _entry(help_text, "check-oscillation")


@pytest.mark.parametrize("name", SUBCOMMANDS)
def test_the_subcommand_list_appears_once(help_text: str, name: str) -> None:
    """一覧は `argparse` だけが持つ。docstring へ写すと片方だけが古くなる。

    `usage` の行と `positional arguments` の選択肢の行は、名前を `,` で連ねた 1 行で
    出る。説明を伴う一覧の側だけを数える。
    """
    described = [
        line for line in help_text.splitlines()
        if line.startswith("    ") and line.strip().startswith(f"{name} ")
    ]
    assert len(described) == 1, f"{name} の説明が {len(described)} 回現れる"


@pytest.mark.parametrize("name,codes", sorted(BRANCHING_EXIT_CODES.items()))
def test_the_branching_exit_codes_are_documented(
    help_text: str, name: str, codes: tuple[str, ...]
) -> None:
    entry = _entry(help_text, name)
    for code in codes:
        assert f"{code}=" in entry, f"{name} の説明に {code} が無い: {entry}"
