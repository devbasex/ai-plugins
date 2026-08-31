"""手順の bash が、スクリプトの終了コードを潰さないことを検査する。

`eval "$(スクリプト)"` は、スクリプトが異常終了しても出力が空なら `eval` 自身の
終了コードが 0 になる。`start-round` が後始末の未了で exit 5 を返しても止まらず、
前のラウンドの `$ROUND` のまま Step 2 へ進むため、ループが止まらなくなる。

手順は変数で受け、終了コードを見てから `eval` する形で書く。
"""
from __future__ import annotations

import pathlib
import re

import pytest

_SKILL_DIR = pathlib.Path(__file__).resolve().parent.parent
_PROCEDURE_FILES = [
    _SKILL_DIR / "SKILL.md",
    _SKILL_DIR / "docs" / "01-state-and-review.md",
    _SKILL_DIR / "docs" / "02-fix-and-rotation.md",
    # `fix` は `cross-review` の Step 5 が呼ぶ工程で、同じ state.py を同じ形で叩く。
    # 検査対象へ含めないと、こちら側だけ `eval "$(...)"` が戻ってくる。
    _SKILL_DIR.parent / "fix" / "SKILL.md",
]

# 行頭が `eval "$(` で始まる呼び出しだけを拾う。落とし穴を説明する地の文には
# 触れないよう、インデントを許して行頭に限る。
_DIRECT_EVAL = re.compile(r'^\s*eval\s+"\$\(', re.MULTILINE)


@pytest.mark.parametrize("path", _PROCEDURE_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_procedure_does_not_eval_a_command_substitution_directly(path: pathlib.Path) -> None:
    found = _DIRECT_EVAL.findall(path.read_text(encoding="utf-8"))

    assert not found, (
        f"{path.name}: `eval \"$(...)\"` は終了コードを 0 に潰す。"
        " 変数で受けてから eval すること"
    )


def test_start_round_propagates_the_exit_code() -> None:
    """`start-round` の呼び出しは、exit 1 で抜けそれ以外は止める形で書く。"""
    text = (_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

    assert 'ROUND_VARS=$("$SCRIPTS/state.py" start-round "$STATE_PR")' in text
    assert 'exit "$RC"' in text


def test_unresolved_threads_propagates_the_exit_code() -> None:
    """`fix` の `unresolved-threads` も変数で受けてから eval する形で書く。"""
    text = (_SKILL_DIR.parent / "fix" / "SKILL.md").read_text(encoding="utf-8")

    assert 'UNRESOLVED_VARS=$(' in text
    assert 'eval "$UNRESOLVED_VARS"' in text


# `verify-sweep` は exit 6（残件あり）だけを続行として扱い、それ以外の終了コードは
# 完了報告へ進ませない。`|| [ $? -eq 6 ]` の形は失敗のステータスを返すだけで、
# `set -e` の無いこの手順では次の `report` が実行される。
_SWEEP_FILES = [
    _SKILL_DIR / "SKILL.md",
    _SKILL_DIR / "docs" / "02-fix-and-rotation.md",
]
_SWALLOWED_SWEEP = re.compile(r'verify-sweep[^\n]*\|\|\s*\[\s*\$\?\s+-eq\s+6\s*\]\s*$', re.MULTILINE)


@pytest.mark.parametrize("path", _SWEEP_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_verify_sweep_does_not_swallow_an_unexpected_failure(path: pathlib.Path) -> None:
    text = path.read_text(encoding="utf-8")

    assert not _SWALLOWED_SWEEP.search(text), (
        f"{path.name}: `verify-sweep ... || [ $? -eq 6 ]` は exit 6 以外の失敗も飲む。"
        " 終了コードを変数で受け、6 以外は exit すること"
    )
    assert 'exit "$RC"' in text
