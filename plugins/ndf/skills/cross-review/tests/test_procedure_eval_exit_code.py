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
]

# 行頭が `eval "$(` で始まる呼び出しだけを拾う。落とし穴を説明する地の文には
# 触れないよう、インデントを許して行頭に限る。
_DIRECT_EVAL = re.compile(r'^\s*eval\s+"\$\(', re.MULTILINE)


@pytest.mark.parametrize("path", _PROCEDURE_FILES, ids=lambda p: p.name)
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
