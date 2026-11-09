"""書き込み先の推定の公開入出力を固定する（現状固定テスト）。

固定した入出力は、シェルの `wt_extract_write_target` を段へ分ける前に採った値で、#1142 の決定 20 で
tree-sitter-bash の上へ移した `hook_lib/write_target.py:shell_targets` が同じ値を返す。

**正しさを主張しない。** 走査を段（前処理・字句化・追跡・抽出）へ分けるとき、公開
入口の振る舞いが変わっていないことだけを検出するために置く。期待値は分割の前の
実装を実際に動かして採った値である。

固定するのは、走査が持つ状態のうち分割で跨ぐもの（現在地の追跡、複合構文の入れ子、
リダイレクトの解決済みの位置）が結果へ現れる形と、書き込みの 4 形式である。

| 固定する入力 | 何を通すか |
| --- | --- |
| 各書き込み形式 | `sed -i` / `>` / `>>` / `tee` / `cp` / `mv` |
| `cd` | 相対パスの起点の移動と、決められない移動先 |
| パイプ・背景実行 | 区画ごとの現在地の巻き戻し |
| 部分シェル | 中の移動を外へ漏らさない隔離 |
| `case` | 枝ごとに入口の位置へ戻す |
| 関数定義 | 本体の移動を外へ漏らさず、呼び出しの後は決めない |
| 複合構文・リダイレクト | `if` の中の移動、命令名より前のリダイレクト、記述子の複製 |
"""

from __future__ import annotations

import pathlib
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
for _p in (SCRIPTS / "lib", SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
from hook_lib import write_target  # noqa: E402

# (名前, コマンド, 起点, 期待する書き込み先, 期待する終了コード)
# 起点が空文字のときは第 2 引数を渡さない呼び方（出力は字面のまま）。
CASES = [
    # --- 書き込みの 4 形式 ---
    ("sed_inplace", "sed -i 's/a/b/' docs/a.md", "", ["docs/a.md"], 0),
    ("redirect", "echo hi > docs/a.md", "", ["docs/a.md"], 0),
    ("append", "echo hi >> docs/a.md", "", ["docs/a.md"], 0),
    ("tee", "echo hi | tee docs/a.md docs/b.md", "", ["docs/a.md", "docs/b.md"], 0),
    ("cp", "cp src.md docs/a.md", "", ["docs/a.md"], 0),
    ("mv", "mv -f src.md docs/a.md", "", ["docs/a.md"], 0),
    ("cp_target_dir", "cp -t docs/ a.md b.md", "", ["docs/"], 0),
    ("read_only", "cat docs/a.md", "", [], 1),
    ("empty", "", "", [], 1),
    ("fd_dup", "make build 2>&1", "", [], 1),
    ("heredoc", "cat > report.md <<EOS\nx > y\nEOS", "", ["report.md"], 0),
    ("sed_after_redirect", "sed -i 's/a/b/' x.md >log y.md", "", ["x.md", "y.md", "log"], 0),
    # --- 現在地の追跡 ---
    ("cd_then_write", "cd .worktrees/x\nsed -i 's/a/b/' README.md", "/base", ["/base/.worktrees/x/README.md"], 0),
    ("unresolvable_cd", "cd \"$TARGET\"\nsed -i 's/a/b/' README.md", "/base", [], 1),
    ("redirect_before_command", ">/dev/null cd .worktrees/x\necho hi > README.md", "/base", ["/base/.worktrees/x/README.md"], 0),
    ("cd_or_exit", "cd .worktrees/x || exit 1\necho hi > README.md", "/base", ["/base/.worktrees/x/README.md"], 0),
    ("cd_or_group_exit", "cd .worktrees/x || { echo ng; exit 1; }\necho hi > README.md", "/base", ["/base/.worktrees/x/README.md"], 0),
    # --- 部分シェルになる区画（パイプ・背景実行・`( )`） ---
    ("cd_in_pipe", "cd .worktrees/x | true\necho hi > README.md", "/base", ["/base/README.md"], 0),
    ("pipe_segment_cd", "cd .worktrees/x && echo hi | tee README.md", "/base", ["/base/.worktrees/x/README.md"], 0),
    ("background_job", "cd .worktrees/x & echo hi > README.md", "/base", ["/base/README.md"], 0),
    ("subshell_cd", "( cd .worktrees/x; echo hi > in.md )\necho hi > out.md", "/base", ["/base/.worktrees/x/in.md", "/base/out.md"], 0),
    # --- 複合構文 ---
    (
        "case_branches",
        "case $1 in\n  a) cd .worktrees/x; echo hi > a.md ;;\n  b) echo hi > b.md ;;\nesac",
        "/base",
        ["/base/.worktrees/x/a.md", "/base/b.md"],
        0,
    ),
    ("if_block_cd", "if true; then cd .worktrees/x; fi\necho hi > README.md", "/base", [], 1),
    # --- 関数定義 ---
    (
        "function_def",
        "f() {\n  cd .worktrees/x\n  echo hi > inner.md\n}\necho hi > outer.md",
        "/base",
        ["/base/.worktrees/x/inner.md", "/base/outer.md"],
        0,
    ),
    ("function_call_after_move", "f() { cd .worktrees/x; }\nf\necho hi > after.md", "/base", [], 1),
    # --- 文の種類ごとの振り分け（_Scan.stmt の各枝） ---
    ("bare_redirect", "> a.md", "/base", ["/base/a.md"], 0),
    ("bare_redirect_after_cd", "cd x\n> a.md", "/base", ["/base/x/a.md"], 0),
    ("negated", "! echo hi > a.md", "/base", ["/base/a.md"], 0),
    ("negated_cd", "! cd x\necho hi > a.md", "/base", ["/base/x/a.md"], 0),
    ("group_cd", "{ cd x; echo hi > a.md; }\necho hi > b.md", "/base", ["/base/x/a.md", "/base/x/b.md"], 0),
    ("group_redirect", "{ cd x; echo hi; } > a.md", "/base", ["/base/a.md"], 0),
    ("subshell_redirect", "( cd x; echo hi ) > a.md", "/base", ["/base/a.md"], 0),
    ("list_trailing_redirect", "cd x && echo hi > a.md", "/base", ["/base/x/a.md"], 0),
    ("pipe_trailing_redirect", "echo hi | cat > a.md", "/base", ["/base/a.md"], 0),
    ("for_loop", "for f in a b; do echo hi > $f.md; echo x > c.md; done", "/base", ["/base/c.md"], 0),
    ("for_loop_cd", "for f in a b; do cd x; done\necho hi > a.md", "/base", [], 1),
    ("c_for_loop", "for ((i=0;i<3;i++)); do echo hi > a.md; done", "/base", ["/base/a.md"], 0),
    ("while_loop", "while read l; do echo $l >> a.md; done < in.txt", "/base", ["/base/a.md"], 0),
    ("while_redirect", "while true; do cd x; done > a.md", "/base", ["/base/a.md"], 0),
    ("until_loop", "until false; do echo hi > a.md; done", "/base", ["/base/a.md"], 0),
    ("if_else", "if true; then echo > a.md; else echo > b.md; fi", "/base", ["/base/a.md", "/base/b.md"], 0),
    ("assign_subst", "X=$(echo hi > a.md)", "/base", ["/base/a.md"], 0),
    ("declare_subst", "local y=$(cd x; echo hi > a.md)\necho hi > b.md", "/base", ["/base/x/a.md", "/base/b.md"], 0),
    ("test_cmd", "[[ -f a.md ]] && echo hi > b.md", "/base", ["/base/b.md"], 0),
    ("function_no_move", "f() { echo hi > in.md; }\nf\necho hi > after.md", "/base", ["/base/in.md", "/base/after.md"], 0),
    ("function_redirect", "f() { echo hi; } > a.md", "/base", [], 1),
]


def _extract(command: str, base: str) -> tuple[list[str], int]:
    """公開入口だけを通す。"""
    lines = write_target.shell_targets(command, base)
    return lines, 0 if lines else 1


@pytest.mark.parametrize(
    ("command", "base", "targets", "rc"),
    [pytest.param(*case[1:], id=case[0]) for case in CASES],
)
def test_the_public_entry_point_keeps_its_output(
    command: str,
    base: str,
    targets: list[str],
    rc: int,
) -> None:
    assert _extract(command, base) == (targets, rc)


@pytest.mark.parametrize(
    ("command", "targets", "rc"),
    [
        pytest.param("cp a ~/.local/state/x.md", ["/home/u/.local/state/x.md"], 0, id="tilde_slash"),
        pytest.param("echo hi > ~", ["/home/u"], 0, id="tilde_alone"),
        pytest.param("echo hi > ~other/x.md", [], 1, id="tilde_user"),
        # 引用符・エスケープの中の `~` は展開されず、現在地の下の名前になる。
        pytest.param('cp a "~/.local/x"', ["/base/~/.local/x"], 0, id="tilde_double_quoted"),
        pytest.param("cp a '~/.local/x'", ["/base/~/.local/x"], 0, id="tilde_single_quoted"),
        pytest.param("cp a \\~/.local/x", ["/base/~/.local/x"], 0, id="tilde_escaped"),
        pytest.param('echo hi > "~"', ["/base/~"], 0, id="tilde_alone_quoted"),
        # `~` から最初の引用されていない `/` までに引用が 1 つでもあれば展開されない。
        pytest.param('cp a ""~/.local/x', ["/base/~/.local/x"], 0, id="tilde_after_empty_quote"),
        pytest.param('cp a ~"/.local/x"', ["/base/~/.local/x"], 0, id="tilde_prefix_quoted"),
        pytest.param('cp a ~/".local/x"', ["/home/u/.local/x"], 0, id="tilde_quoted_after_slash"),
        pytest.param('cp "" ~/.local/x', ["/home/u/.local/x"], 0, id="tilde_after_quoted_word"),
    ],
)
def test_a_leading_tilde_is_the_home_directory(
    command: str,
    targets: list[str],
    rc: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # bash は引用符の無い語の先頭の `~` を $HOME へ展開する。起点へ継ぎ足すと、
    # リポジトリの外への書き込みを主ディレクトリの編集として案内する。
    # `~user` は利用者の家を引かないと決められないため出さない。
    monkeypatch.setenv("HOME", "/home/u")
    assert _extract(command, "/base") == (targets, rc)


# `cmd || <止める文>` の右辺の判定（`_noncontinuing`）を公開入口から固定する。
# 期待値は現状の実装を動かして採った値で、正しさを主張しない。
# 右辺を止める文と見なすと、続く書き込みは `cd` の後の位置になる（`/base/x/a.md`）。
# 見なさないと、`cd` が走ったかを決められず何も出さない（`[]`）。
NONCONTINUING_CASES = [
    # --- 単独の命令（`redirected_statement` の剥がしと、命令名の照合） ---
    ("exit_redirected", "cd x || exit 1 2>/dev/null\necho hi > a.md", ["/base/x/a.md"]),
    ("return", "cd x || return\necho hi > a.md", ["/base/x/a.md"]),
    ("break", "cd x || break\necho hi > a.md", ["/base/x/a.md"]),
    ("continue", "cd x || continue\necho hi > a.md", ["/base/x/a.md"]),
    ("quoted_exit", 'cd x || "exit" 1\necho hi > a.md', ["/base/x/a.md"]),
    ("other_command", "cd x || echo ng\necho hi > a.md", []),
    ("subshell_exit", "cd x || ( exit 1 )\necho hi > a.md", []),
    ("chained_or", "false || cd x || exit 1\necho hi > a.md", []),
    ("exit_writes_on_failure", "cd x || exit 1 > err.md\necho hi > a.md", ["/base/err.md", "/base/x/a.md"]),
    # --- `{ …; }`（中の文ごとの剥がしと、命令名の照合） ---
    ("group_redirected", "cd x || { echo ng; exit 1; } 2>/dev/null\necho hi > a.md", ["/base/x/a.md"]),
    ("group_inner_redirected", "cd x || { echo ng >&2; exit 1 >&2; }\necho hi > a.md", ["/base/x/a.md"]),
    # 末尾でなくても、中のどれかの文が止める命令なら止める文と見なす（現状の振る舞い）。
    ("group_exit_first", "cd x || { exit 1; echo ng; }\necho hi > a.md", ["/base/x/a.md"]),
    # `&&` の連なりは先頭の命令だけを見る（現状の振る舞い）。
    ("group_list_exit_first", "cd x || { exit 1 && echo ng; }\necho hi > a.md", ["/base/x/a.md"]),
    ("group_list_exit_last", "cd x || { echo ng && exit 1; }\necho hi > a.md", []),
    ("group_background_exit", "cd x || { exit 1 & }\necho hi > a.md", []),
    ("group_no_exit", "cd x || { echo ng; }\necho hi > a.md", []),
    ("group_writes_on_failure", "cd x || { echo ng > err.md; exit 1; }\necho hi > a.md", ["/base/err.md", "/base/x/a.md"]),
]


@pytest.mark.parametrize(
    ("command", "targets"),
    [pytest.param(*case[1:], id=case[0]) for case in NONCONTINUING_CASES],
)
def test_the_right_side_of_or_that_stops_keeps_its_output(command: str, targets: list[str]) -> None:
    assert write_target.shell_targets(command, "/base") == targets


# リダイレクトの読み方（`_Scan.redirects`）の各枝を公開入口から固定する。
# 期待値は現状の実装を動かして採った値で、正しさを主張しない。
REDIRECT_CASES = [
    # --- 書き込みの演算子（WRITE_OPS） ---
    ("amp_gt", "make &> a.md", ["/base/a.md"]),
    ("amp_gtgt", "make &>> a.md", ["/base/a.md"]),
    ("clobber", "echo hi >| a.md", ["/base/a.md"]),
    ("read_write", "exec 3<> a.md", ["/base/a.md"]),
    ("fd_prefix_write", "make 2> err.md", ["/base/err.md"]),
    ("multiple_in_order", "echo hi > a.md 2> b.md >> c.md", ["/base/a.md", "/base/b.md", "/base/c.md"]),
    ("after_cd", "cd x\necho hi 2> a.md", ["/base/x/a.md"]),
    ("parent_dir", "echo hi > ../a.md", ["/a.md"]),
    # --- 読み込みの演算子は書き込み先にならない ---
    ("read_redirect", "cat < a.md", []),
    ("read_redirect_subst", "cat < $(echo hi > a.md)", []),
    # --- `>&` の記述子の複製と閉じる形は出さず、ファイル名なら出す ---
    ("dup_file", "echo hi >& a.md", ["/base/a.md"]),
    ("dup_close", "echo hi >&-", []),
    ("dup_digit", "echo hi 1>&2", []),
    # --- 書き込み先に数えない宛先 ---
    ("dev_null", "echo hi > /dev/null", []),
    ("dev_stderr", "echo hi > /dev/stderr", []),
    ("variable_dest", "echo hi > $OUT", []),
    ("newline_after_operator", "echo hi >\na.md", []),
    # --- 宛先の中の置換は部分シェルとして読む ---
    ("dest_subst", 'echo hi > "$(echo hi > a.md; echo b.md)"', ["/base/a.md"]),
    # --- file_redirect 以外（ヒアストリング）は中の置換だけを見る ---
    ("herestring_subst", 'cat <<< "$(echo hi > a.md)"', ["/base/a.md"]),
    ("herestring_plain", "cat <<< hi", []),
    # --- ヒアドキュメント（付いたリダイレクトと本文の置換） ---
    ("heredoc_redirect_after", "cat <<EOS > a.md\nx\nEOS", ["/base/a.md"]),
    ("heredoc_subst", "cat <<EOS > a.md\n$(echo hi > b.md)\nEOS", ["/base/a.md", "/base/b.md"]),
    ("heredoc_quoted_no_subst", "cat <<'EOS' > a.md\n$(echo hi > b.md)\nEOS", ["/base/a.md"]),
    ("heredoc_subst_cd_isolated", "cat <<EOS\n$(cd x; echo hi > b.md)\nEOS\necho hi > c.md", ["/base/x/b.md", "/base/c.md"]),
]


@pytest.mark.parametrize(
    ("command", "targets"),
    [pytest.param(*case[1:], id=case[0]) for case in REDIRECT_CASES],
)
def test_each_kind_of_redirect_keeps_its_output(command: str, targets: list[str]) -> None:
    assert write_target.shell_targets(command, "/base") == targets
