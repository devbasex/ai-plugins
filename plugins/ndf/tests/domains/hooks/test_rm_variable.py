"""空かもしれない変数の `rm` を止める guard（#3877 の決定 4）。

展開が効く位置（引用の外か二重引用の中）の `$NAME`・`${NAME}` を、`rm` の削除の対象だけで見て止める。`${NAME:?}`・
固定の既定値と代替値・引用やエスケープで文字のまま渡るドル記号・特殊パラメータは通す。
"""

from __future__ import annotations

import pytest

from ndf.domains.hooks import budget, dispatch
from ndf.domains.hooks.context import HookContext, parse_shell
from ndf.domains.hooks.counter import Counter
from ndf.domains.hooks.guards import rm_variable

from ._hooks import advise_text, bash, call_entry, deny_reason

# 要求仕様の受け入れ条件の原文と同じ形（Claude Code の確認が出た実物のコマンド）
ORIGINAL = "rm -f $W/$n.md.done $W/$n.result.json"


@pytest.mark.parametrize(
    ("command", "word"),
    [
        ('rm -f "$W/$n.report.md.done"', '"$W/$n.report.md.done"'),
        # tree-sitter-bash は引用の無い 2 つめの変数の前で語を割る（`$W/$` と `n.md.done`）。構文上の 1 語として読む
        (ORIGINAL, "$W/$n.md.done"),
        # ディレクトリの変数だけを直しても、名前の変数が空なら `${W:?}/.md.done` を消す
        ("rm -f ${W:?}/$n.md.done", "${W:?}/$n.md.done"),
        ("rm -f ${W:?}/${n:?}.md.done", None),
        ("rm -rf /tmp/x", None),
        ('rm -f "${W:?}/x"', None),
        ('rm -f "${W:-/tmp}/x"', None),
        ('rm -f "${W:+/tmp}/x"', None),
        # 既定値・代替値の中の展開は評価される
        ('rm -rf "${DIR:-$FALLBACK}/cache"', '"${DIR:-$FALLBACK}/cache"'),
        ('rm -rf "${DIR:+$SUBDIR}/cache"', '"${DIR:+$SUBDIR}/cache"'),
        ('rm -f "${W:-${F:?}}/x"', None),
        ("ls $W", None),
        ("rm -f 'cost$5.txt'", None),
        ("rm -f cost\\$5.txt", None),
        ('rm -f "cost\\$5.txt"', None),
        ("rm -f 'a$b'\"$X\"", "'a$b'\"$X\""),
        ('/bin/rm -f "$W/x"', '"$W/x"'),
        # ラッパーの後ろと `bash -c` の中の rm
        ('command rm -f "$W/x"', '"$W/x"'),
        ('timeout 5 rm "$W"', '"$W"'),
        ("bash -c 'rm -f \"$W/x\"'", '"$W/x"'),
        # オプションの値は削除の対象でない。`--` の後は `-` で始まっても対象
        ('rm --interactive="$mode" /tmp/x', None),
        ('rm -- -"$W"', '-"$W"'),
        # ANSI-C 引用の中の `\'` は引用を閉じない。`$$` は PID で、後ろの字は変数の名前でない
        ("rm -f $'it\\'s$x'", None),
        ('rm -f "$$NAME"', None),
        ("", None),
    ],
    ids=[
        "quoted-report",
        "original-unquoted",
        "required-dir-only",
        "required-both",
        "literal-path",
        "required",
        "default",
        "alternate",
        "variable-default",
        "variable-alternate",
        "required-default",
        "not-rm",
        "single-quoted-dollar",
        "escaped-dollar",
        "escaped-dollar-in-quotes",
        "mixed-quotes",
        "rm-with-a-path",
        "command-wrapper",
        "timeout-wrapper",
        "bash-c",
        "option-value",
        "after-double-dash",
        "ansi-c-quote",
        "pid",
        "empty",
    ],
)
def test_risky_removal_returns_the_word_that_can_expand_to_nothing(command, word):
    assert rm_variable.risky_removal(parse_shell(command)) == word


@pytest.mark.parametrize(
    ("word", "found"),
    [
        ('"$W/x"', ["$W"]),
        ("a${B}c", ["${B}"]),
        ("'$W'", []),
        ("\\$W", []),
        ('"\\$W"', []),
        ("\"'$W'\"", ["$W"]),
        ("'a$b", []),
        ('"$W/$n"', ["$W", "$n"]),
        ('"${D:-$F}"', ["${D:-$F}", "$F"]),
        ("$'a\\'$b'$c", ["$c"]),
        ('"$$N$1$c"', ["$c"]),
    ],
    ids=[
        "double-quoted",
        "braced",
        "single-quoted",
        "escaped",
        "escaped-in-quotes",
        "quote-char-in-quotes",
        "unclosed",
        "two",
        "default-operand",
        "ansi-c",
        "special-parameters",
    ],
)
def test_expanded_variables_reads_the_word_from_the_left(word, found):
    assert rm_variable.expanded_variables(word) == found


def test_the_guard_denies_and_tells_how_to_rewrite(tmp_path):
    reason = deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, ORIGINAL)))
    assert reason is not None
    assert "$W" in reason and "ndf worker prepare" in reason and "${" in reason


def test_the_guard_passes_a_fixed_path(tmp_path):
    assert call_entry("claude", "PreToolUse", bash(tmp_path, "rm -f /tmp/x")) is None


def test_a_failure_inside_the_guard_lets_the_call_through(tmp_path, monkeypatch):
    def boom(_script):
        raise RuntimeError("壊れた")

    monkeypatch.setattr(rm_variable, "risky_removal", boom)
    assert deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, ORIGINAL))) is None


def _specs() -> list:
    return [s for s in dispatch.load_specs() if s.name == "rm-variable"]


def test_the_table_row_is_not_counted_and_keeps_denying_after_the_limit(tmp_path):
    # 上限（hook.deny-repeat）を超えて同じコマンドを打っても、deny_key が none の行は案内へ下がらない
    specs = _specs()
    assert len(specs) == 1 and specs[0].deny_key == "none" and not specs[0].fail_closed
    ctx = HookContext("claude", "PreToolUse", bash(tmp_path, 'rm -f "$W/x"'))
    kinds = []
    with Counter.open("s-3877", tmp_path) as counter:
        for _ in range(4):
            got = dispatch.run(ctx, specs, budget.budget_for("claude", "PreToolUse"), counter, tmp_path)
            kinds.append(got.verdict.kind)
        assert counter.data["denies"] == {}
    assert kinds == ["deny"] * 4
    assert advise_text(call_entry("claude", "PreToolUse", bash(tmp_path, 'rm -f "$W/x"'))) is None
