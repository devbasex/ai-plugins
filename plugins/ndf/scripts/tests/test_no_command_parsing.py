"""#1334 AC1・I2 — cross-refactoring と supervise の配布物に、テストのコマンドの文字列を解析する処理が無い。

実行器の名前・差し替えの位置・落ちたテストの ID をコマンドの文字列や出力の形から取り出す処理（旧 `testcmd.py`）と、
`--lf` で落ちたテストだけを走らせ直す処理を戻すと落ちる。コマンドへの加工は `{paths}` の置き換えだけである。
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

NDF = Path(__file__).resolve().parents[2]
LIB = NDF / "scripts" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import test_strategy as ts  # noqa: E402

ROOTS = [NDF / "scripts", NDF / "skills" / "cross-refactoring" / "scripts"]
BANNED = [
    re.compile(r"\bdef (runner_index|is_known|build_limited|target_indices|failed_nodes|rerun_command)\b"),
    re.compile(r"\bKNOWN_RUNNERS\b"),
    re.compile(r"\btestcmd\b"),
    re.compile(r"--lf\b"),
]


def _shipped_sources() -> list[Path]:
    out = []
    for root in ROOTS:
        for path in root.rglob("*.py"):
            parts = set(path.relative_to(root).parts)
            if "tests" in parts or "__pycache__" in parts:
                continue
            out.append(path)
    return out


def test_no_shipped_source_parses_the_test_command():
    hits = []
    for path in _shipped_sources():
        text = path.read_text(encoding="utf-8")
        for pattern in BANNED:
            for m in pattern.finditer(text):
                hits.append(f"{path.relative_to(NDF)}: {m.group(0)}")
    assert not (NDF / "skills" / "cross-refactoring" / "scripts" / "refactor_lib" / "testcmd.py").exists()
    assert hits == []


def test_the_scope_words_are_the_template_words_with_paths_replaced():
    """範囲テストの語は、雛形の語の並びから `{paths}` の語だけを対象に差し替えたもの。並べ替えも足しもしない。"""
    template = "docker compose exec -T app ./vendor/bin/phpunit --log-junit build/ndf/junit.xml {paths} --colors=never"
    words = shlex.split(ts.fill(template, ["tests/Unit/A.php", "tests/Unit/B.php"]))
    before, after = template.split(" {paths} ")
    assert words == before.split() + ["tests/Unit/A.php", "tests/Unit/B.php"] + after.split()
