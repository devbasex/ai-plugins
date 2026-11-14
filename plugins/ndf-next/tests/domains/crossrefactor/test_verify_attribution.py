"""検証の失敗を持ち込んだ項目にだけ帰す（#3339）と、修正の前進の判定。

#3339 の観測（PR #3330）: 項目 I-010 が共有の検査（行数の上限）の違反を持ち込み、後の 10 項目がその違反を自分の失敗として
拾った。修正担当は直す対象を持たず、停滞が 20 回続いて採用 0 件で止まった。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.domains.crossrefactor import verify, verify_decide

# 共有の検査: 渡したファイルが 3 行を超えれば落ちる（行数の上限の検査の形）
CHECK = "import sys\nsys.exit(any(len(open(p).read().splitlines()) > 3 for p in sys.argv[1:]))\n"


def test_failure_present_at_the_item_start_is_not_the_items_own() -> None:
    got = verify_decide.attribute({"lines:glossary.py"}, {"lines:glossary.py", "lines:items.py"})
    assert got.own == frozenset({"lines:items.py"})
    assert got.inherited == frozenset({"lines:glossary.py"})
    assert verify_decide.attribute({"lines:glossary.py"}, {"lines:glossary.py"}).passed


def test_same_failures_twice_is_a_stall_and_a_return_is_an_oscillation() -> None:
    a, b = frozenset({"x:a.py"}), frozenset({"x:b.py"})
    assert verify_decide.fix_progress([a]).kind == verify_decide.Kind.RETRY
    assert verify_decide.fix_progress([a, a]).gave_up
    assert verify_decide.fix_progress([a, b, a]).kind == verify_decide.Kind.OSCILLATING
    assert verify_decide.fix_progress([a, frozenset()]).kind == verify_decide.Kind.PASSED
    with pytest.raises(ValueError):
        verify_decide.fix_progress([])


def _project(gitwork) -> None:
    decl = {"test": {"ci": {"only": True}, "suites": [
        {"name": "lines", "kind": "lint", "command": "python3 check.py", "scope_command": "python3 check.py {paths}", "paths": ["src"]},
        {"name": "pytest", "command": "pytest", "scope_command": "pytest {paths}", "paths": ["."]},
    ]}}  # fmt: skip
    gitwork.write({".ndf/project.json": json.dumps(decl), "check.py": CHECK})


def test_later_item_does_not_take_the_violation_an_earlier_item_brought(gitwork, tmp_path: Path) -> None:
    gitwork.make({"src/glossary.py": "a = 1\n", "src/items.py": "b = 1\n"})
    _project(gitwork)
    gitwork.commit({}, "宣言")
    # I-010: 共有のファイルを上限の外へ伸ばす
    gitwork.commit({"src/glossary.py": "a = 1\na = 2\na = 3\na = 4\n"}, "I-010")
    start_of_next = gitwork.git("rev-parse", "HEAD")
    # I-011: 同じファイルにも触るが、違反は持ち込まない
    gitwork.commit({"src/glossary.py": "a = 1\na = 2\na = 3\na = 5\n", "src/items.py": "b = 2\n"}, "I-011")
    found = verify.suites(gitwork.work)
    assert [s.name for s in found] == ["lines"]  # テストを CI に寄せる宣言では、手元はテストの suite を打たない
    changed = ["src/glossary.py", "src/items.py"]
    now = verify.failing(gitwork.work, found, verify.targets(gitwork.work, found, changed))
    assert now == frozenset({"lines:src/glossary.py"})
    probe = tmp_path / "probe"
    gitwork.git("worktree", "add", "-q", "--detach", str(probe))
    before = verify.at_commit(probe, start_of_next, found, now)
    got = verify_decide.attribute(before, now)
    assert got.passed and got.inherited == frozenset({"lines:src/glossary.py"})
