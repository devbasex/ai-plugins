"""シェルコマンドからの書き込み先の推定を検証する（受け入れ条件 6〜8 の Bash 側）。

対象は直接の書き換え・出力の付け替え・標準入力からの書き出し・複製と移動の 4 形式に
限る。推定できないものは案内を出さないため、終了コード 1 と空の出力になることを
併せて確かめる。
"""
from __future__ import annotations

import pytest

from worktree_helpers import run_lib


def extract(command: str) -> tuple[list[str], int]:
    # 改行を含むコマンドも渡せるよう、ヒアドキュメントで受け渡す。
    # 引数へ埋めると、改行が字面の `\n` になって 1 行に潰れる。
    snippet = (
        "cmd=$(cat <<'WT_EOF'\n" + command + "\nWT_EOF\n)\n"
        'wt_extract_write_target "$cmd"; echo rc=$?'
    )
    got = run_lib(snippet)
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


# --- パスの正規化 -----------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("a/b", "/base/a/b"),
        ("./a/./b", "/base/a/b"),
        ("a/../b", "/base/b"),
        ("a/../../outside", "/outside"),
        ("/abs/path", "/abs/path"),
        ("/abs/../other", "/other"),
    ],
)
def test_lexical_normalization(given: str, expected: str) -> None:
    """`.` と `..` を字面で畳む。残ると「配下か」の判定をすり抜ける。"""
    got = run_lib(f'wt_normalize_path "{given}" "/base"')
    assert got.stdout.strip() == expected, got.stderr


# --- コマンドの区切り --------------------------------------------------------


def test_newline_separates_commands() -> None:
    """改行を空白として捨てると、次の行の語を前のコマンドの対象と取り違える。"""
    targets, _ = extract("cp a.txt b.txt\necho c")
    assert targets == ["b.txt"], targets


def test_semicolon_separates_commands() -> None:
    targets, _ = extract("cp a.txt b.txt ; echo c")
    assert targets == ["b.txt"], targets


def test_multiline_reports_each_command() -> None:
    targets, _ = extract("cp a.txt one.txt\nmv b.txt two.txt\necho done")
    assert targets == ["one.txt", "two.txt"], targets


def test_separator_inside_quotes_is_not_a_break() -> None:
    targets, _ = extract("sed -i 's/a;b/c/' plugins/ndf/README.md")
    assert targets == ["plugins/ndf/README.md"], targets


def test_newline_does_not_become_a_target() -> None:
    targets, rc = extract("echo hi >\nplugins/ndf/README.md")
    assert rc == 1, targets


# --- 空白を挟まないオプション ------------------------------------------------


def test_sed_script_joined_to_the_option() -> None:
    """`-es/a/b/` のように空白を挟まない形。見落とすと最初のファイルを取り違える。"""
    targets, rc = extract("sed -i -es/a/b/ one.md two.md")
    assert rc == 0, targets
    assert targets == ["one.md", "two.md"], targets


def test_sed_file_option_joined() -> None:
    targets, _ = extract("sed -i -fscript.sed one.md two.md")
    assert targets == ["one.md", "two.md"], targets


@pytest.mark.parametrize(
    "command",
    ["cp -tplugins/ndf docs/a.md", "mv -tplugins/ndf docs/a.md docs/b.md"],
)
def test_target_directory_joined_to_the_option(command: str) -> None:
    """`-t<ディレクトリ>` のように空白を挟まない形。"""
    targets, rc = extract(command)
    assert rc == 0, command
    assert targets == ["plugins/ndf"], (command, targets)


# --- issue #173: 実行される部分に絞る ---------------------------------------


def test_a_heredoc_body_is_not_a_write_target() -> None:
    """本文はコマンドとして実行される部分ではない。"""
    command = (
        "cat > report.md <<'EOS'\n"
        "受領: <payload>\n"
        "判定: <期待: 一致>\n"
        "EOS"
    )
    targets, rc = extract(command)
    assert rc == 0, targets
    assert targets == ["report.md"], targets


def test_a_write_after_a_heredoc_is_still_found() -> None:
    """本文の終端より後ろは、また実行される部分に戻る。"""
    command = (
        "cat <<'EOS' > first.md\n"
        "本文 > body.md\n"
        "EOS\n"
        "echo hi > second.md"
    )
    targets, rc = extract(command)
    assert rc == 0, targets
    assert targets == ["first.md", "second.md"], targets


def test_an_indented_heredoc_body_is_dropped() -> None:
    """`<<-` は終端の語の前の tab を無視する。"""
    command = (
        "cat <<-EOS > out.md\n"
        "\t本文 > body.md\n"
        "\tEOS\n"
        "echo hi > after.md"
    )
    targets, rc = extract(command)
    assert rc == 0, targets
    assert targets == ["out.md", "after.md"], targets


def test_a_shift_operator_inside_quotes_does_not_hide_later_writes() -> None:
    """引用符の中の `<<` は本文の始まりではない。"""
    command = (
        "echo 'a << b'\n"
        "echo hi > plugins/ndf/README.md"
    )
    targets, rc = extract(command)
    assert rc == 0, targets
    assert targets == ["plugins/ndf/README.md"], targets


def test_a_here_string_has_no_body() -> None:
    """`<<<` は行の入力を渡す形で、終端の語を持たない。"""
    command = (
        "cat <<<'body' > out.md\n"
        "echo hi > after.md"
    )
    targets, rc = extract(command)
    assert rc == 0, targets
    assert targets == ["out.md", "after.md"], targets


@pytest.mark.parametrize(
    "command",
    [
        'cp guard.sh "$SP/wt-test/guard.sh"',
        "echo hi > $SP/out.md",
        'echo hi | tee "${OUT}/log.txt"',
    ],
)
def test_words_with_an_unexpanded_variable_are_not_targets(command: str) -> None:
    """展開前の変数を含む語は、どのパスを指すか決められない。"""
    targets, rc = extract(command)
    assert rc == 1, targets
    assert targets == [], targets


def test_a_write_inside_an_expanding_heredoc_is_found() -> None:
    """終端の語を引用符で囲まない本文は展開され、コマンド置換が実行される。"""
    command = (
        "cat > report.md <<EOS\n"
        "$(echo data > side-effect.md)\n"
        "EOS"
    )
    targets, rc = extract(command)
    assert rc == 0, targets
    assert "report.md" in targets, targets
    assert "side-effect.md" in targets, targets


def test_an_expanding_heredoc_without_substitution_is_dropped() -> None:
    """展開される本文でも、コマンド置換が無ければ実行される部分ではない。"""
    command = (
        "cat > report.md <<EOS\n"
        "受領: <payload>\n"
        "判定: <期待: 一致>\n"
        "EOS"
    )
    targets, rc = extract(command)
    assert rc == 0, targets
    assert targets == ["report.md"], targets


def test_a_quoted_delimiter_keeps_the_body_inert() -> None:
    """引用符で囲めば本文は展開されない。コマンド置換の字面も実行されない。"""
    command = (
        "cat > report.md <<'EOS'\n"
        "$(echo data > side-effect.md)\n"
        "EOS"
    )
    targets, rc = extract(command)
    assert rc == 0, targets
    assert targets == ["report.md"], targets


def test_a_multi_line_substitution_inside_an_expanding_heredoc_is_found() -> None:
    """コマンド置換は複数行にまたがる。開いてから閉じるまでを残す。"""
    command = (
        "cat > report.md <<EOS\n"
        "$(\n"
        "echo data > side-effect.md\n"
        ")\n"
        "EOS"
    )
    targets, rc = extract(command)
    assert rc == 0, targets
    assert targets == ["report.md", "side-effect.md"], targets


def test_lines_after_a_closed_substitution_are_dropped_again() -> None:
    """置換が閉じたら、その後ろの本文はまた実行されない部分に戻る。"""
    command = (
        "cat > report.md <<EOS\n"
        "$(echo data > side-effect.md)\n"
        "受領: <payload>\n"
        "EOS"
    )
    targets, rc = extract(command)
    assert rc == 0, targets
    assert targets == ["report.md", "side-effect.md"], targets


def test_a_multi_line_backtick_substitution_is_found() -> None:
    """backtick の置換も開閉が行をまたぐ。"""
    command = (
        "cat > report.md <<EOS\n"
        "`\n"
        "echo data > side-effect.md\n"
        "`\n"
        "EOS"
    )
    targets, rc = extract(command)
    assert rc == 0, targets
    assert "side-effect.md" in targets, targets


def test_a_closing_paren_inside_quotes_does_not_end_the_substitution() -> None:
    """置換の中では引用符が効く。囲まれた `)` は閉じ括弧ではない。"""
    command = (
        "cat > report.md <<EOS\n"
        '$(echo "a )"\n'
        "echo data > side-effect.md\n"
        ")\n"
        "EOS"
    )
    targets, rc = extract(command)
    assert rc == 0, targets
    assert "side-effect.md" in targets, targets


def test_a_quoted_paren_on_the_same_line_keeps_the_write() -> None:
    command = 'cat > report.md <<EOS\n$(echo "nested )" > side-effect.md)\nEOS'
    targets, rc = extract(command)
    assert rc == 0, targets
    assert "report.md" in targets, targets
    assert "side-effect.md" in targets, targets


def test_an_apostrophe_in_the_body_is_not_a_quote() -> None:
    """本文そのものでは引用符は字面である。後ろの置換を見落とさない。"""
    command = (
        "cat > report.md <<EOS\n"
        "it's fine\n"
        "$(echo data > side-effect.md)\n"
        "EOS"
    )
    targets, rc = extract(command)
    assert rc == 0, targets
    assert "side-effect.md" in targets, targets


def test_single_quotes_inside_a_substitution_are_honoured() -> None:
    command = (
        "cat > report.md <<EOS\n"
        "$(echo 'a )'\n"
        "echo data > side-effect.md\n"
        ")\n"
        "EOS"
    )
    targets, rc = extract(command)
    assert rc == 0, targets
    assert "side-effect.md" in targets, targets


def test_an_arithmetic_expansion_is_not_a_substitution() -> None:
    """`$((...))` は算術展開で、中の `>` は比較である。"""
    command = (
        "cat > report.md <<EOS\n"
        "$((3 > 2))\n"
        "EOS"
    )
    targets, rc = extract(command)
    assert rc == 0, targets
    assert targets == ["report.md"], targets


def test_a_substitution_after_an_arithmetic_expansion_is_found() -> None:
    """算術展開を読み飛ばしても、同じ行の後ろの置換は拾う。"""
    command = (
        "cat > report.md <<EOS\n"
        "$((3 > 2)) $(echo data > side-effect.md)\n"
        "EOS"
    )
    targets, rc = extract(command)
    assert rc == 0, targets
    assert "side-effect.md" in targets, targets


def test_a_nested_arithmetic_expansion_is_skipped_to_its_end() -> None:
    command = (
        "cat > report.md <<EOS\n"
        "$(( (3 > 2) ? 1 : 0 ))\n"
        "受領: <payload>\n"
        "EOS"
    )
    targets, rc = extract(command)
    assert rc == 0, targets
    assert targets == ["report.md"], targets


def test_a_quoted_delimiter_with_a_space_is_recognised() -> None:
    """終端の語は引用符で空白を含められる。途中で切ると終端を見つけられない。"""
    command = (
        'cat > report.md <<"EOF X"\n'
        "受領: <payload>\n"
        "EOF X\n"
        "echo hi > after.md"
    )
    targets, rc = extract(command)
    assert rc == 0, targets
    assert targets == ["report.md", "after.md"], targets


def test_the_body_outside_a_substitution_is_not_scanned() -> None:
    """展開される本文でも、置換の外は実行されない。字面を書き込み先にしない。"""
    command = (
        "cat > report.md <<EOS\n"
        "変換: 入力 > <期待: 一致>  $(echo data > side-effect.md)\n"
        "EOS"
    )
    targets, rc = extract(command)
    assert rc == 0, targets
    assert targets == ["report.md", "side-effect.md"], targets


def extract_at(command: str, base: str) -> tuple[list[str], int]:
    """相対パスの起点を渡して書き込み先を推定する。

    起点を渡した場合、出力は絶対パスになる。同じコマンドの中で先に実行される
    `cd` を反映するため、字面のままでは解決できない。
    """
    snippet = (
        "cmd=$(cat <<'WT_EOF'\n" + command + "\nWT_EOF\n)\n"
        f'wt_extract_write_target "$cmd" "{base}"; echo rc=$?'
    )
    got = run_lib(snippet)
    lines = [ln for ln in got.stdout.splitlines() if ln]
    rc = int(lines.pop().removeprefix("rc="))
    return lines, rc


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # 同じコマンドの中の `cd` は、後続の相対パスの起点になる。
        ("cd /base/.worktrees/x\nsed -i 's/a/b/' README.md", "/base/.worktrees/x/README.md"),
        ("cd /base/.worktrees/x && sed -i 's/a/b/' README.md", "/base/.worktrees/x/README.md"),
        ("cd .worktrees/x; echo hi > README.md", "/base/.worktrees/x/README.md"),
        ("cd .worktrees/x\ncp a.txt README.md", "/base/.worktrees/x/README.md"),
        ("cd .worktrees/x\necho hi | tee README.md", "/base/.worktrees/x/README.md"),
        # `cd` を跨がない書き込みは、渡された起点のままになる。
        ("echo hi > README.md", "/base/README.md"),
        # 絶対パスは `cd` の影響を受けない。
        ("cd /elsewhere\nsed -i 's/a/b/' /base/README.md", "/base/README.md"),
        # 連続した `cd` は積み上がる。
        ("cd .worktrees\ncd x\nsed -i 's/a/b/' README.md", "/base/.worktrees/x/README.md"),
    ],
)
def test_cd_moves_the_base_of_relative_targets(command: str, expected: str) -> None:
    targets, rc = extract_at(command, "/base")
    assert rc == 0, command
    assert targets == [expected], command


def test_write_before_cd_keeps_the_original_base() -> None:
    """`cd` より前の書き込みは、移動前の位置を指す。"""
    targets, rc = extract_at("echo hi > README.md\ncd .worktrees/x", "/base")
    assert rc == 0
    assert targets == ["/base/README.md"]


def test_cd_argument_position_is_not_a_move() -> None:
    """コマンドの位置に無い `cd` は移動として数えない。"""
    targets, rc = extract_at("echo cd > README.md", "/base")
    assert rc == 0
    assert targets == ["/base/README.md"]


def test_unresolvable_cd_suppresses_relative_targets() -> None:
    """移動先を決められないときは、相対パスの書き込み先を出さない。

    字面のまま起点へ継ぎ足すと、実際には触っていない位置を案内することになる。
    案内は操作を止めないため、黙るほうを選ぶ。
    """
    targets, rc = extract_at('cd "$TARGET"\nsed -i \'s/a/b/\' README.md', "/base")
    assert rc == 1
    assert targets == []


def test_unresolvable_cd_still_reports_absolute_targets() -> None:
    """移動先が不明でも、絶対パスの書き込み先は位置が決まる。"""
    targets, rc = extract_at('cd "$TARGET"\nsed -i \'s/a/b/\' /base/README.md', "/base")
    assert rc == 0
    assert targets == ["/base/README.md"]


def test_base_dir_is_optional() -> None:
    """起点を渡さない呼び方では、字面のまま返す（既存の呼び出し元との互換）。"""
    targets, rc = extract("cd /elsewhere\nsed -i 's/a/b/' README.md")
    assert rc == 0
    assert targets == ["README.md"]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # `||` と `&` はコマンドの区切りである。跨いで走査すると、次のコマンドの
        # 語を書き込み先と取り違える。
        ("cp a.txt b.txt || echo c", ["b.txt"]),
        ("mv a.txt b.txt || echo c", ["b.txt"]),
        ("sed -i 's/a/b/' x.md || echo hi", ["x.md"]),
        ("echo hi | tee x.md || echo done", ["x.md"]),
        ("cp a.txt b.txt & echo c", ["b.txt"]),
        ("sed -i 's/a/b/' x.md & echo hi", ["x.md"]),
        # 区切りを跨いだ先の書き込みは、それ自体として拾う。
        ("cp a.txt b.txt || echo c > d.txt", ["b.txt", "d.txt"]),
    ],
)
def test_separators_stop_the_operand_scan(command: str, expected: list[str]) -> None:
    targets, rc = extract(command)
    assert rc == 0, command
    assert targets == expected, command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # パイプの各区画は部分シェルで動く。`cd` の効果は後続へ残らない。
        ("cd .worktrees/x | sed -i 's/a/b/' README.md", "/base/README.md"),
        ("cd .worktrees/x |& sed -i 's/a/b/' README.md", "/base/README.md"),
        # 背景実行も同じで、`cd` は現在のシェルの位置を変えない。
        ("cd .worktrees/x & sed -i 's/a/b/' README.md", "/base/README.md"),
        # `&&` は同じシェルで続くため、効果は残る。
        ("cd .worktrees/x && cd y | sed -i 's/a/b/' README.md", "/base/.worktrees/x/README.md"),
        # 部分シェルの中の `cd` は、パイプが終わった後にも残らない。
        ("cd .worktrees/x | cat\nsed -i 's/a/b/' README.md", "/base/README.md"),
        # 背景実行にまとめられた `cd` も、後続の位置を変えない。
        ("cd .worktrees/x && cd y & sed -i 's/a/b/' README.md", "/base/README.md"),
    ],
)
def test_cd_does_not_reach_past_a_pipe_or_background(command: str, expected: str) -> None:
    """`cd` の効果を引き継ぐのは `;` / 改行 / `&&` / `||` の後だけである。

    引き継いでしまうと、主ディレクトリへの書き込みを作業ツリー側と取り違えて
    案内を出さない（検知漏れになる）。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 0, command
    assert targets == [expected], command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # まとまりごと背景実行したときも、`cd` は親のシェルの位置を変えない。
        # 中の `;` で `&` の復元先を引き直すと、まとまりの中の移動が外へ漏れる。
        ("{ cd .worktrees/x; } & sed -i 's/a/b/' README.md", "/base/README.md"),
        ("{ cd .worktrees/x; cd y; } & sed -i 's/a/b/' README.md", "/base/README.md"),
        ("if true; then cd .worktrees/x; fi & sed -i 's/a/b/' README.md",
         "/base/README.md"),
        ("while read f; do cd .worktrees/x; done & sed -i 's/a/b/' README.md",
         "/base/README.md"),
        ("for f in a b; do cd .worktrees/x; done & sed -i 's/a/b/' README.md",
         "/base/README.md"),
        ("( cd .worktrees/x; cd y ) & sed -i 's/a/b/' README.md", "/base/README.md"),
        # 入れ子でも、外側のまとまりの入口まで戻す。
        ("{ if true; then cd .worktrees/x; fi; } & sed -i 's/a/b/' README.md",
         "/base/README.md"),
    ],
)
def test_a_backgrounded_group_does_not_move_the_parent(
    command: str, expected: str
) -> None:
    """複合コマンドごと `&` で背景実行しても、中の `cd` は後続へ残らない。

    残ると、主ディレクトリへの書き込みを作業ツリー側と取り違えて案内を出さない
    （検知漏れになる）。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == [expected], command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # まとまりの中では、`cd` の効果はそのまま後続へ及ぶ。
        ("{ cd .worktrees/x; sed -i 's/a/b/' README.md; } & echo done",
         "/base/.worktrees/x/README.md"),
        # まとまりの中の `&` は、その中のひとまとまりの入口へ戻す。
        ("{ cd .worktrees/x; cd y & sed -i 's/a/b/' README.md; }",
         "/base/.worktrees/x/README.md"),
    ],
)
def test_a_group_still_carries_cd_inside_itself(command: str, expected: str) -> None:
    """まとまりの内側の位置は変えない。外へ漏らさないことだけを直す。"""
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == [expected], command



@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # `||` の右辺は左辺が失敗したときに走る。直前の `cd` が唯一の命令なら、
        # 失敗したのはその `cd` であり、右辺の現在地は移動前になる。
        ("cd .worktrees/x || sed -i 's/a/b/' README.md", "/base/README.md"),
        ("cd .worktrees/x || echo hi > README.md", "/base/README.md"),
        ("cd .worktrees/x || echo hi | tee README.md", "/base/README.md"),
        # `cd` を含まない左辺は現在地を変えない。右辺もその位置のままになる。
        ("echo hi || sed -i 's/a/b/' README.md", "/base/README.md"),
        ("cd .worktrees/x; echo hi || sed -i 's/a/b/' README.md",
         "/base/.worktrees/x/README.md"),
        # 移動先が不明でも、絶対パスの書き込み先は位置が決まる。
        ("cd .worktrees/x || sed -i 's/a/b/' /other/README.md", "/other/README.md"),
    ],
)
def test_or_runs_at_the_position_before_the_failed_cd(command: str, expected: str) -> None:
    """`||` の右辺は、直前の `cd` が効いていない位置で走る。

    効いた前提の位置を引き継ぐと、主ディレクトリへの書き込みを作業ツリー側と
    取り違えて案内を出さない（検知漏れになる）。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 0, command
    assert targets == [expected], command


@pytest.mark.parametrize(
    "command",
    [
        # 左辺に命令が 2 つ以上あると、どこで失敗したのかを字面から決められない。
        # `cd .worktrees/x` が失敗したなら `/base`、`cd y` が失敗したなら
        # `/base/.worktrees/x` で走る。
        "cd .worktrees/x && cd y || sed -i 's/a/b/' README.md",
        "cd .worktrees/x && echo hi || sed -i 's/a/b/' README.md",
        # `||` を挟んだ後の現在地も、左辺が成功したかどうかで変わる。
        "cd .worktrees/x || true\nsed -i 's/a/b/' README.md",
        "cd .worktrees/x || cd y\nsed -i 's/a/b/' README.md",
        "true || cd .worktrees/x\nsed -i 's/a/b/' README.md",
    ],
)
def test_an_undecidable_or_suppresses_relative_targets(command: str) -> None:
    """どちらの位置で走るか決められないときは、相対パスの書き込み先を出さない。"""
    targets, rc = extract_at(command, "/base")
    assert rc == 1, (command, targets)
    assert targets == [], command


def test_an_undecidable_or_still_reports_absolute_targets() -> None:
    """位置が決められなくても、絶対パスの書き込み先は変わらない。"""
    targets, rc = extract_at(
        "cd .worktrees/x && cd y || sed -i 's/a/b/' /base/README.md", "/base"
    )
    assert rc == 0, targets
    assert targets == ["/base/README.md"]


def test_a_tilde_cd_suppresses_relative_targets() -> None:
    """チルダ展開の結果は字面から決められない。相対パスの書き込み先を出さない。"""
    targets, rc = extract_at("cd ~/dir\nsed -i 's/a/b/' README.md", "/base")
    assert rc == 1, targets
    assert targets == []


def test_a_tilde_cd_still_reports_absolute_targets() -> None:
    """移動先が不明でも、絶対パスの書き込み先は位置が決まる。"""
    targets, rc = extract_at("cd ~/dir\nsed -i 's/a/b/' /base/README.md", "/base")
    assert rc == 0, targets
    assert targets == ["/base/README.md"]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # `|&` は標準エラー出力も渡すパイプで、これも区切りである。
        ("cp a.txt b.txt |& echo c", ["b.txt"]),
        ("mv a.txt b.txt |& echo c", ["b.txt"]),
        ("sed -i 's/a/b/' x.md |& echo hi", ["x.md"]),
        ("echo hi | tee x.md |& echo done", ["x.md"]),
        ("cp a.txt b.txt |& echo c > d.txt", ["b.txt", "d.txt"]),
    ],
)
def test_a_stderr_pipe_stops_the_operand_scan(command: str, expected: list[str]) -> None:
    targets, rc = extract(command)
    assert rc == 0, command
    assert targets == expected, command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # `then` の後ろは命令の位置である。条件の中で移動した場合も含む。
        ("if true; then cd .worktrees/x; sed -i 's/a/b/' README.md; fi",
         "/base/.worktrees/x/README.md"),
        ("if cd .worktrees/x; then sed -i 's/a/b/' README.md; fi",
         "/base/.worktrees/x/README.md"),
        # `else` / `elif` の後ろも同じシェルで続く。
        ("if false; then true; else cd .worktrees/x; sed -i 's/a/b/' README.md; fi",
         "/base/.worktrees/x/README.md"),
        ("if false; then true; elif cd .worktrees/x; then sed -i 's/a/b/' README.md; fi",
         "/base/.worktrees/x/README.md"),
        # `{` で開くまとまりは、部分シェルではなく同じシェルで動く。
        ("{ cd .worktrees/x; sed -i 's/a/b/' README.md; }", "/base/.worktrees/x/README.md"),
        # `do` の後ろも命令の位置である。
        ("while read f; do cd .worktrees/x; sed -i 's/a/b/' README.md; done",
         "/base/.worktrees/x/README.md"),
        ("until false; do cd .worktrees/x; sed -i 's/a/b/' README.md; done",
         "/base/.worktrees/x/README.md"),
        ("for f in a b; do cd .worktrees/x; sed -i 's/a/b/' README.md; done",
         "/base/.worktrees/x/README.md"),
        # `!` と `time` は同じシェルで続きを走らせる。
        ("! cd .worktrees/x\nsed -i 's/a/b/' README.md", "/base/.worktrees/x/README.md"),
        ("time cd .worktrees/x\nsed -i 's/a/b/' README.md", "/base/.worktrees/x/README.md"),
        # まとまりを閉じた後も、`{` の中の移動は残る（必ず走るため）。
        ("{ cd .worktrees/x; }\nsed -i 's/a/b/' README.md", "/base/.worktrees/x/README.md"),
    ],
)
def test_reserved_words_open_a_command_position(command: str, expected: str) -> None:
    """同じシェルで続きを走らせる予約語の後ろの `cd` は、起点に反映する。

    反映しないと、作業ツリーへ移ってから相対パスで書き換えたときに移動前の位置を
    指した案内が出る（#186 の誤検知）。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == [expected], command


@pytest.mark.parametrize(
    "command",
    [
        # 本体が走ったかどうかは実行時に決まる。閉じた後の現在地は決められない。
        "if true; then cd .worktrees/x; fi\nsed -i 's/a/b/' README.md",
        "while read f; do cd .worktrees/x; done\nsed -i 's/a/b/' README.md",
        "for f in a b; do cd .worktrees/x; done\nsed -i 's/a/b/' README.md",
        # 条件の中の `cd` が失敗したときに `else` / `elif` へ来る。効いていない。
        "if cd .worktrees/x; then true; else sed -i 's/a/b/' README.md; fi",
        "if cd .worktrees/x; then true; elif true; then sed -i 's/a/b/' README.md; fi",
        # `case` も同じである。どの枝が走ったかは実行時に決まるため、`esac` の
        # 後ろの現在地は決められない。
        "case $x in a) cd .worktrees/x ;; esac\nsed -i 's/a/b/' README.md",
        "case $x in a) cd .worktrees/x ;; esac; echo hi > README.md",
        "case $x in a ) cd .worktrees/x ;; esac; echo hi > README.md",
        "case $x in a) true ;; b) cd .worktrees/x ;; esac\ncp a.txt README.md",
    ],
)
def test_a_conditional_block_leaves_the_position_undecidable(command: str) -> None:
    """走ったかどうかが決まらない `cd` の後は、相対パスの書き込み先を出さない。"""
    targets, rc = extract_at(command, "/base")
    assert rc == 1, (command, targets)
    assert targets == [], command


def test_a_conditional_block_still_reports_absolute_targets() -> None:
    """位置が決められなくても、絶対パスの書き込み先は変わらない。"""
    targets, rc = extract_at(
        "if true; then cd .worktrees/x; fi\nsed -i 's/a/b/' /base/README.md", "/base"
    )
    assert rc == 0, targets
    assert targets == ["/base/README.md"]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # 部分シェルの中の `cd` は親のシェルの現在地を変えない。抜けた後は元のまま。
        ("cd .worktrees/x; ( cd y ); sed -i 's/a/b/' README.md",
         "/base/.worktrees/x/README.md"),
        ("( cd .worktrees/x ); sed -i 's/a/b/' README.md", "/base/README.md"),
        ("( cd .worktrees/x ) && sed -i 's/a/b/' README.md", "/base/README.md"),
    ],
)
def test_a_subshell_does_not_move_the_parent(command: str, expected: str) -> None:
    """`(` は命令の位置として数えない。数えると閉じた後の現在地がずれる。"""
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == [expected], command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # 部分シェルの中の `;` や改行の後ろも命令の位置だが、そこにある `cd` も
        # 親のシェルの位置は変えない。
        ("( true; cd .worktrees/x ); sed -i 's/a/b/' README.md", "/base/README.md"),
        ("( cd .worktrees/x; cd y ); sed -i 's/a/b/' README.md", "/base/README.md"),
        ("( cd a\ncd .worktrees/x ); echo hi > README.md", "/base/README.md"),
        # 外側で移動していれば、その位置は保つ。
        ("cd .worktrees/x; ( true; cd y ); sed -i 's/a/b/' README.md",
         "/base/.worktrees/x/README.md"),
        # 入れ子でも同じ。
        ("( ( true; cd .worktrees/x ) ); sed -i 's/a/b/' README.md", "/base/README.md"),
        # 空白を挟まない形でも同じ。`(` と `)` は語として切り出す。
        ("(cd .worktrees/x; cd y); sed -i 's/a/b/' README.md", "/base/README.md"),
        ("(cd .worktrees/x); sed -i 's/a/b/' README.md", "/base/README.md"),
    ],
)
def test_a_subshell_hides_every_cd_inside_it(command: str, expected: str) -> None:
    """部分シェルの中の `cd` は、何番目であっても親のシェルへ漏らさない。

    漏らすと、抜けた後の主ディレクトリへの書き込みを作業ツリー側と取り違えて
    案内を出さない（検知漏れになる）。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == [expected], command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # 部分シェルの中の相対パスは、その中で走った `cd` の後の位置で解決する。
        # 外側の位置で解決すると、作業ツリー側への書き込みを主ディレクトリへの
        # 書き込みとして案内することになる。
        ("( cd .worktrees/x; sed -i 's/a/b/' README.md )",
         ["/base/.worktrees/x/README.md"]),
        ("( true; cd .worktrees/x; sed -i 's/a/b/' README.md )",
         ["/base/.worktrees/x/README.md"]),
        # 空白を挟まない形でも同じ。`(` と `)` を語として切り出す。
        ("(cd .worktrees/x; sed -i 's/a/b/' README.md)",
         ["/base/.worktrees/x/README.md"]),
        ("(cd .worktrees/x && echo hi > README.md)",
         ["/base/.worktrees/x/README.md"]),
        # 入れ子でも、その段の位置で解決する。
        ("( cd .worktrees; ( cd x; sed -i 's/a/b/' README.md ) )",
         ["/base/.worktrees/x/README.md"]),
        # 内側の部分シェルを抜けたら、外側の段の位置へ戻る。
        ("( cd .worktrees; ( cd x ); sed -i 's/a/b/' README.md )",
         ["/base/.worktrees/README.md"]),
        # 中の書き込みと、抜けた後の書き込みは、それぞれの位置で解決する。
        ("( cd .worktrees/x; echo hi > IN.md ); cp a.txt OUT.md",
         ["/base/.worktrees/x/IN.md", "/base/OUT.md"]),
        # `$(` は展開であって部分シェルの入口ではない。その閉じ括弧を部分シェルの
        # 終わりとして数えると、後続の相対パスが外側の位置へ戻ってしまう。
        ('( cd .worktrees/x; echo "$(date)" > README.md )',
         ["/base/.worktrees/x/README.md"]),
        ('( cd .worktrees/x; echo "$(date)" > IN.md ); cp a.txt OUT.md',
         ["/base/.worktrees/x/IN.md", "/base/OUT.md"]),
    ],
)
def test_a_subshell_resolves_relative_paths_at_its_own_cwd(
    command: str, expected: list[str]
) -> None:
    """部分シェルの中の `cd` は、その中の相対パスには効く。

    効かせないと、作業ツリーへ移ってから書き換えたものを主ディレクトリへの
    書き込みとして案内する（誤検知になる）。抜けた後は親の位置へ戻す。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == expected, command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # `case` の見出しの `)` は部分シェルの終わりではない。語の一部として残す。
        ("case $x in a) cp p.txt q.txt;; esac", ["q.txt"]),
        # 関数定義の `()` も部分シェルではない。
        ("f() { cp a.txt b.txt; }", ["b.txt"]),
        # `$(` の中の `)` も語の一部である。
        ("cp a.txt $(basename b).txt", ["b).txt"]),
    ],
)
def test_parentheses_outside_a_subshell_stay_in_the_word(
    command: str, expected: list[str]
) -> None:
    """部分シェルを開いていない `(` と `)` は、語として切り出さない。"""
    targets, rc = extract(command)
    assert rc == 0, command
    assert targets == expected, command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # 配列の代入の `)`。空白を挟むと語から離れる。
        ("( a=( 1 2 ); cd .worktrees/x ); echo hi > README.md", "/base/README.md"),
        # `case` の見出しの `)`。部分シェルの中では対応する `(` が残っているため、
        # 数だけでは語の一部と区別できない。
        ("( case $y in a) true ;; esac; cd .worktrees/x ); cp a.txt README.md",
         "/base/README.md"),
        ("( case $y in a ) true ;; esac; cd .worktrees/x ); cp a.txt README.md",
         "/base/README.md"),
        # 関数定義の `()`。空白を挟む書き方もある。
        ("( f () { :; }; cd .worktrees/x ); cp a.txt README.md", "/base/README.md"),
    ],
)
def test_a_parenthesis_inside_a_subshell_does_not_end_it(
    command: str, expected: str
) -> None:
    """部分シェルの中の `)` を終わりとして数えると、中の `cd` が親へ漏れる。

    漏れると、抜けた後の主ディレクトリへの書き込みを作業ツリー側と取り違えて
    案内を出さない（検知漏れになる）。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == [expected], command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("( cd .worktrees/x; a=( 1 ); sed -i 's/a/b/' README.md )",
         "/base/.worktrees/x/README.md"),
        ("( cd .worktrees/x; case $y in a) sed -i 's/a/b/' README.md ;; esac )",
         "/base/.worktrees/x/README.md"),
        ("( cd .worktrees/x; f () { :; }; cp a.txt README.md )",
         "/base/.worktrees/x/README.md"),
    ],
)
def test_a_parenthesis_inside_a_subshell_keeps_the_inner_cwd(
    command: str, expected: str
) -> None:
    """語の一部の `)` で段を戻すと、中の相対パスが外側の位置で解決される。

    戻すと、作業ツリーへ移ってから書き換えたものを主ディレクトリへの書き込みと
    して案内する（誤検知になる）。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == [expected], command


@pytest.mark.parametrize(
    "command",
    [
        # `&&` の左辺が `cd` でないとき、右辺の `cd` が走ったかは左辺の成否で決まる。
        # リストを抜けた後の現在地も決められない。
        "true && cd .worktrees/x; sed -i 's/a/b/' README.md",
        "true && cd .worktrees/x\nsed -i 's/a/b/' README.md",
        "cd a && true && cd .worktrees/x; sed -i 's/a/b/' README.md",
    ],
)
def test_a_conditional_cd_after_and_leaves_the_position_undecidable(command: str) -> None:
    """`&&` を跨いだ `cd` の後は、相対パスの書き込み先を出さない。"""
    targets, rc = extract_at(command, "/base")
    assert rc == 1, (command, targets)
    assert targets == [], command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # `cd` 自身が `&&` の左辺なら、走ったものとして扱う（既定の前提）。
        ("cd .worktrees/x && cd y; sed -i 's/a/b/' README.md",
         "/base/.worktrees/x/y/README.md"),
        ("cd .worktrees/x && cd y\nsed -i 's/a/b/' README.md",
         "/base/.worktrees/x/y/README.md"),
        # 位置が決められなくても、絶対パスの書き込み先は変わらない。
        ("true && cd .worktrees/x; sed -i 's/a/b/' /base/README.md", "/base/README.md"),
    ],
)
def test_a_cd_on_the_left_of_and_still_carries(command: str, expected: str) -> None:
    """`&&` の左辺の `cd` は走ったものとして扱う。抑止するのは右辺の `cd` だけ。"""
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == [expected], command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # `)` は複合コマンドの終わりで、被演算子の走査はここで止まる。
        ("( sed -i 's/a/b/' x.md )", ["x.md"]),
        ("( cp a.txt b.txt )", ["b.txt"]),
        ("( echo hi | tee x.md )", ["x.md"]),
    ],
)
def test_a_closing_paren_stops_the_operand_scan(command: str, expected: list[str]) -> None:
    """閉じ括弧を被演算子として拾うと、実在しない位置を書き込み先として示す。"""
    targets, rc = extract(command)
    assert rc == 0, command
    assert targets == expected, command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # 空白を詰めて書いた演算子も、区切りとして見えなければならない。
        # 1 語のままだと `b.txt||echo` が被演算子に見え、次のコマンドの `c` を
        # 複製先として拾う。
        ("cp a.txt b.txt||echo c", ["b.txt"]),
        ("mv a.txt b.txt&&echo c", ["b.txt"]),
        ("cp a.txt b.txt&echo c", ["b.txt"]),
        ("cp a.txt b.txt|cat", ["b.txt"]),
        ("sed -i 's/a/b/' x.md|&cat", ["x.md"]),
        ("sed -i 's/a/b/' x.md||echo hi", ["x.md"]),
        ("echo hi|tee x.md||echo done", ["x.md"]),
        # 区切りを跨いだ先の書き込みは、それ自体として拾う。
        ("cp a.txt b.txt||echo c>d.txt", ["b.txt", "d.txt"]),
    ],
)
def test_operators_without_spaces_still_separate(command: str, expected: list[str]) -> None:
    """演算子は空白で囲まれているとは限らない。字句解析の側で切り出す。"""
    targets, rc = extract(command)
    assert rc == 0, command
    assert targets == expected, command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # 引用符の中の演算子は字面であって、区切りではない。
        ("sed -i 's/a|b/c/' x.md", ["x.md"]),
        ("sed -i 's/a&&b/c/' x.md", ["x.md"]),
        ('cp "a&b.txt" c.txt', ["c.txt"]),
        ('cp a.txt "b|c.txt"', ["b|c.txt"]),
    ],
)
def test_operators_inside_quotes_are_not_separators(command: str, expected: list[str]) -> None:
    targets, rc = extract(command)
    assert rc == 0, command
    assert targets == expected, command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # `>&2` の `&` はファイル記述子の複製で、背景実行の演算子ではない。
        # 区切りとして切ると、後続が別のコマンドに見える。
        ("cd .worktrees/x 2>&1\nsed -i 's/a/b/' README.md", "/base/.worktrees/x/README.md"),
        ("cd .worktrees/x >/dev/null 2>&1\ncp a.txt README.md", "/base/.worktrees/x/README.md"),
        ("cd .worktrees/x 2>&1 && echo hi > README.md", "/base/.worktrees/x/README.md"),
    ],
)
def test_file_descriptor_duplication_is_not_a_separator(command: str, expected: str) -> None:
    """`&` を無条件に切ると、`2>&1` の後ろが別のコマンドとして扱われる。"""
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == [expected], command


def test_stderr_redirection_alone_is_not_a_write_target() -> None:
    """`>&2` は既存のファイルへの書き込みではないため、案内の対象にしない。"""
    targets, rc = extract("echo hi >&2")
    assert rc == 1
    assert targets == []


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # 空白を詰めた `&&` の後ろでも、`cd` の効果は残る。
        ("cd .worktrees/x&&sed -i 's/a/b/' README.md", "/base/.worktrees/x/README.md"),
        # パイプの区画は部分シェルで、空白の有無で扱いは変わらない。
        ("cd .worktrees/x|sed -i 's/a/b/' README.md", "/base/README.md"),
        ("cd .worktrees/x&sed -i 's/a/b/' README.md", "/base/README.md"),
    ],
)
def test_cd_tracking_handles_operators_without_spaces(command: str, expected: str) -> None:
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == [expected], command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # 変数代入は命令の前に置ける。間に挟まっても `cd` は移動である。
        ("FOO=bar cd .worktrees/x\nsed -i 's/a/b/' README.md", "/base/.worktrees/x/README.md"),
        ("FOO=bar BAZ=qux cd .worktrees/x\ncp a.txt README.md", "/base/.worktrees/x/README.md"),
        ("cd .worktrees && FOO=1 cd x\necho hi > README.md", "/base/.worktrees/x/README.md"),
        # 代入に見える語でも、命令の位置から続いていなければ単なる引数である。
        ("echo a=b cd x\nsed -i 's/a/b/' README.md", "/base/README.md"),
        ("echo hi a=b cd /elsewhere\ncp a.txt README.md", "/base/README.md"),
    ],
)
def test_assignments_before_a_command_keep_the_command_position(
    command: str, expected: str
) -> None:
    """`FOO=bar cd x` の `cd` を命令として数えないと、移動が起点へ反映されない。"""
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == [expected], command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # `cd` 自身に付いたリダイレクトは、移動する**前**の位置で開かれる。
        ("cd .worktrees/x > README.md", ["/base/README.md"]),
        ("cd .worktrees/x >> README.md", ["/base/README.md"]),
        ("cd .worktrees/x >README.md", ["/base/README.md"]),
        # 移動そのものは後続へ効く。移動前の位置になるのはリダイレクトだけである。
        ("cd .worktrees/x > log.txt\nsed -i 's/a/b/' README.md",
         ["/base/log.txt", "/base/.worktrees/x/README.md"]),
        # 移動先を決められない `cd` でも、リダイレクトは移動前の位置で開かれる。
        ("cd $HOME > README.md", ["/base/README.md"]),
    ],
)
def test_a_redirection_on_cd_itself_opens_before_moving(
    command: str, expected: list[str]
) -> None:
    """`cd x > f` の `f` は移動前の位置で開かれる。

    移動後の位置で解決すると、主ディレクトリ側への書き込みを作業ツリー側と
    取り違えて案内を出さない（検知漏れになる）。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == expected, command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # 見出し (`a)`) の後ろは、その枝の本体が始まる位置である。中の `cd` は追う。
        ("case $x in a) cd .worktrees/x; sed -i 's/a/b/' README.md ;; esac",
         "/base/.worktrees/x/README.md"),
        ("case $x in a|b) cd .worktrees/x; echo hi > README.md ;; esac",
         "/base/.worktrees/x/README.md"),
        ("case $x in *) cd .worktrees/x; cp a.txt README.md ;; esac",
         "/base/.worktrees/x/README.md"),
        ('case $x in "a") cd .worktrees/x; echo hi | tee README.md ;; esac',
         "/base/.worktrees/x/README.md"),
        # 枝どうしは排他である。前の枝の `cd` を次の枝へ持ち越さない。
        ("case $x in a) cd .worktrees/x ;; b) sed -i 's/a/b/' README.md ;; esac",
         "/base/README.md"),
        # 入れ子の `case` でも、内側の枝の入口は内側の `case` の位置である。
        ("case $x in a) cd .worktrees/x; case $y in b) cp a.txt README.md ;; esac ;; esac",
         "/base/.worktrees/x/README.md"),
        # 見出しは `)` の前に空白を置けるほか、先頭に `(` を添える書き方もある。
        ("case $x in a ) cd .worktrees/x; cp a.txt README.md ;; esac",
         "/base/.worktrees/x/README.md"),
        ("case $x in (a) cd .worktrees/x; cp a.txt README.md ;; esac",
         "/base/.worktrees/x/README.md"),
    ],
)
def test_a_case_branch_opens_a_command_position(command: str, expected: str) -> None:
    """`case` の見出しの後ろを命令の位置として数えないと、枝の中の `cd` が漏れる。

    漏らすと、作業ツリーへ移ってから書き換えたものを主ディレクトリへの書き込みと
    して案内する（誤検知になる）。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == [expected], command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # `&>` `&>>` は標準出力と標準エラーをまとめて 1 つのファイルへ向ける形で、
        # `&` は背景実行の演算子ではない。演算子として読むと現在地がまとまりの
        # 入口へ戻り、移動前の位置を指した案内が出る。
        ("cd .worktrees/x && echo hi &> README.md",
         ["/base/.worktrees/x/README.md"]),
        ("cd .worktrees/x && echo hi &>> README.md",
         ["/base/.worktrees/x/README.md"]),
        # まとまりが切れていないため、後続の命令にも移動が効き続ける。
        ("cd .worktrees/x && echo hi &> log.txt && cp a.txt README.md",
         ["/base/.worktrees/x/log.txt", "/base/.worktrees/x/README.md"]),
        # `>& file` `>&file` は `&>` と同義の古い書き方で、後ろの語がファイルになる。
        ("cd .worktrees/x && echo hi >& README.md",
         ["/base/.worktrees/x/README.md"]),
        ("echo hi >& README.md", ["/base/README.md"]),
        ("echo hi >&README.md", ["/base/README.md"]),
        # `cd` 自身に付いたときも、移動する前の位置で開かれる。
        ("cd .worktrees/x &> README.md", ["/base/README.md"]),
        ("cd .worktrees/x >& README.md", ["/base/README.md"]),
        # 記述子の複製と混ざっても、複製の側はファイルを開かない。
        ("cd .worktrees/x 2>&1 && echo hi &> README.md",
         ["/base/.worktrees/x/README.md"]),
    ],
)
def test_combined_stdout_stderr_redirection_is_one_redirect(
    command: str, expected: list[str]
) -> None:
    """`&>` `&>>` `>&` の `&` を演算子として読むと、書き込み先の位置がずれる。"""
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == expected, command


@pytest.mark.parametrize(
    "command",
    [
        # `2>&1` `>&2` は記述子の複製で、ファイルは開かれない。
        "echo hi >&2",
        "echo hi 2>&1",
        # `>&-` は記述子を閉じる。
        "echo hi >&-",
    ],
)
def test_file_descriptor_duplication_opens_no_file(command: str) -> None:
    """複製と閉鎖をまとめて向ける形と取り違えると、実在しない書き込み先を案内する。"""
    targets, rc = extract_at(command, "/base")
    assert rc == 1, (command, targets)
    assert targets == []


def test_and_operator_glued_to_a_redirection_is_still_a_list_separator() -> None:
    """`cmd&&>f` の `&` と `>` は隣り合うが、`&>` ではなく `&&` と `>` である。"""
    targets, rc = extract_at("cd .worktrees/x&&>README.md", "/base")
    assert rc == 0, targets
    assert targets == ["/base/.worktrees/x/README.md"]


@pytest.mark.parametrize(
    "command",
    [
        # `||` を跨いだリストに `cd` があると、左右どちらの経路を通ったかで
        # 現在地が変わる。`&&` の右辺の位置も決められない。
        "cd .worktrees/x || cd .worktrees/y && echo hi > README.md",
        "cd .worktrees/x || echo b && echo hi > README.md",
        "cd .worktrees/x || cd .worktrees/y && cp a.txt README.md",
    ],
)
def test_an_undecidable_or_before_and_suppresses_relative_targets(command: str) -> None:
    """`||` の後の `&&` で判定が抜けると、通っていない経路の相対パスを案内する。"""
    targets, rc = extract_at(command, "/base")
    assert rc == 1, (command, targets)
    assert targets == []


def test_an_undecidable_or_before_and_still_reports_absolute_targets() -> None:
    """絶対パスは現在地に依らないため、案内の対象から外さない。"""
    targets, rc = extract_at(
        "cd .worktrees/x || cd .worktrees/y && echo hi > /abs/README.md", "/base"
    )
    assert rc == 0, targets
    assert targets == ["/abs/README.md"]


def test_an_or_without_cd_keeps_the_position_before_and() -> None:
    """`cd` の無い `||` は現在地を変えない。抑止すると検知漏れになる。"""
    targets, rc = extract_at("echo a || echo b && echo hi > README.md", "/base")
    assert rc == 0, targets
    assert targets == ["/base/README.md"]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # `||` の右辺が後続へ進まない命令なら、そこを過ぎた時点で左辺の成功が
        # 確定している。`cd /main || exit` は主ディレクトリへ移る定番の形である。
        ("cd /main || exit\nsed -i 's/a/b/' README.md", "/main/README.md"),
        ("cd /main || exit 1\nsed -i 's/a/b/' README.md", "/main/README.md"),
        ("cd .worktrees/x || exit\ncp a.txt README.md", "/base/.worktrees/x/README.md"),
        ("cd .worktrees/x || return\ncp a.txt README.md", "/base/.worktrees/x/README.md"),
        ("cd .worktrees/x || break\ncp a.txt README.md", "/base/.worktrees/x/README.md"),
        ("cd .worktrees/x || continue\ncp a.txt README.md", "/base/.worktrees/x/README.md"),
        # `&&` で繋いだ左辺も、抜けた時点ですべて成功している。
        ("cd .worktrees && cd x || exit\ncp a.txt README.md",
         "/base/.worktrees/x/README.md"),
        ("true && cd .worktrees/x || exit\ncp a.txt README.md",
         "/base/.worktrees/x/README.md"),
        # 同じリストの中で続けて使う形も、直前の `cd` の成功が確定する。
        ("cd .worktrees || exit\ncd x || exit\ncp a.txt README.md",
         "/base/.worktrees/x/README.md"),
    ],
)
def test_an_or_with_a_non_continuing_right_side_keeps_the_move(
    command: str, expected: str
) -> None:
    """`cd x || exit` の後は、`cd` が成功した位置で続きが走る。

    抑止すると、作業ツリーへ移ってから相対パスで書き換えたときに案内が出ない
    ばかりか、主ディレクトリへ戻ってからの書き込みも案内できなくなる。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == [expected], command


@pytest.mark.parametrize(
    "command",
    [
        # 部分シェルの `exit` は親のシェルを終わらせない。位置は決められない。
        "cd .worktrees/x || (exit)\ncp a.txt README.md",
        # 先行する `||` で経路が分かれていると、右辺の `exit` では絞れない。
        "true || cd .worktrees/x || exit\ncp a.txt README.md",
    ],
)
def test_a_non_continuing_right_side_does_not_decide_every_form(command: str) -> None:
    """成功が確定しない形では、相対パスの書き込み先を出さない。"""
    targets, rc = extract_at(command, "/base")
    assert rc == 1, (command, targets)
    assert targets == [], command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # `cd dir || { echo ...; exit 1; }` は `|| exit` より広く使われる形である。
        ("cd /main || { echo 'cd failed' >&2; exit 1; }\nsed -i 's/a/b/' README.md",
         "/main/README.md"),
        ("cd .worktrees/x || { echo err; exit 1; }\ncp a.txt README.md",
         "/base/.worktrees/x/README.md"),
        # まとまりの先頭が非継続命令の形。
        ("cd .worktrees/x || { exit 1; }\ncp a.txt README.md",
         "/base/.worktrees/x/README.md"),
        ("cd .worktrees/x || { echo err; return 1; }\ncp a.txt README.md",
         "/base/.worktrees/x/README.md"),
        ("cd .worktrees/x || { echo err; break; }\ncp a.txt README.md",
         "/base/.worktrees/x/README.md"),
        ("cd .worktrees/x || { echo err; continue; }\ncp a.txt README.md",
         "/base/.worktrees/x/README.md"),
        # 複合コマンドを挟んでも、まとまりの直下に非継続命令があれば必ず抜ける。
        ("cd .worktrees/x || { if true; then echo err; fi; exit 1; }\ncp a.txt README.md",
         "/base/.worktrees/x/README.md"),
        # 改行で区切る形も `;` と同じである。
        ("cd .worktrees/x || {\n  echo err\n  exit 1\n}\ncp a.txt README.md",
         "/base/.worktrees/x/README.md"),
        # 予約語と同じ語を引数へ置いても、深さは動かない。
        ('cd .worktrees/x || { echo "done"; exit 1; }\ncp a.txt README.md',
         "/base/.worktrees/x/README.md"),
    ],
)
def test_an_or_with_a_brace_group_that_always_exits_keeps_the_move(
    command: str, expected: str
) -> None:
    """`cd x || { ...; exit 1; }` の後も、`cd` が成功した位置で続きが走る。

    ブレースグループは同じシェルで走るため、その中の `exit` はスクリプトを終える。
    `|| exit` と同じ扱いにしないと、この定番の形で相対パスの案内が出なくなる。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == [expected], command


def test_a_brace_group_body_resolves_at_the_failed_position() -> None:
    """まとまりの中は `cd` が失敗した位置で走る。抜けた後だけが移動後の位置になる。"""
    targets, rc = extract_at(
        "cd .worktrees/x || { echo err > fail.log; exit 1; }\ncp a.txt README.md",
        "/base",
    )
    assert rc == 0, targets
    assert targets == ["/base/fail.log", "/base/.worktrees/x/README.md"]


@pytest.mark.parametrize(
    "command",
    [
        # 非継続命令が条件付きなら、通ったかどうかは実行時に決まる。
        'cd .worktrees/x || { [ -n "$FLAG" ] && exit 1; }\ncp a.txt README.md',
        "cd .worktrees/x || { if [ -n \"$FLAG\" ]; then exit 1; fi; }\ncp a.txt README.md",
        # 非継続命令がまったく無い形は、抜けた後も走る。
        "cd .worktrees/x || { echo err; }\ncp a.txt README.md",
        # 部分シェルの中の `exit` は親のシェルを終わらせない。
        "cd .worktrees/x || { echo err; ( exit 1 ); }\ncp a.txt README.md",
        # 先行する `||` で経路が分かれていると、まとまりの `exit` では絞れない。
        "true || cd .worktrees/x || { echo err; exit 1; }\ncp a.txt README.md",
    ],
)
def test_a_brace_group_that_may_continue_does_not_decide_the_position(command: str) -> None:
    """まとまりを抜けるかどうかが実行時に決まる形では、書き込み先を出さない。"""
    targets, rc = extract_at(command, "/base")
    assert rc == 1, (command, targets)
    assert targets == [], command


def test_a_backgrounded_non_continuing_command_does_not_keep_the_move() -> None:
    """`{ exit 1 & }` は部分シェルで走り、親のシェルは続く。移動後の位置にはならない。"""
    targets, _ = extract_at(
        "cd .worktrees/x || { echo err; exit 1 & }\ncp a.txt README.md", "/base"
    )
    assert "/base/.worktrees/x/README.md" not in targets, targets


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # `FOO+=bar` も命令の前に置ける変数代入である。
        ("FOO+=bar cd .worktrees/x\ncp a.txt README.md", "/base/.worktrees/x/README.md"),
        ("FOO=a BAR+=b cd .worktrees/x\ncp a.txt README.md",
         "/base/.worktrees/x/README.md"),
        # 命令の位置から続いていなければ、代入に見えても単なる引数である。
        ("echo a+=b cd /elsewhere\ncp a.txt README.md", "/base/README.md"),
    ],
)
def test_append_assignments_before_a_command_keep_the_command_position(
    command: str, expected: str
) -> None:
    """`FOO+=bar cd x` の `cd` を命令として数えないと、移動が起点へ反映されない。"""
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == [expected], command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # 予約語の直後の `case` も入口である。数えないと見出しの `)` が枝の始まり
        # として渡らず、枝の中の `cd` が追跡から漏れる。
        ("cd .worktrees/x; if true; then case x in x) cd ../..; cp a.txt README.md ;; esac; fi",
         "/base/README.md"),
        ("cd .worktrees/x; if false; then true; else case x in x) cd ../..; cp a.txt README.md ;; esac; fi",
         "/base/README.md"),
        ("cd .worktrees/x; while true; do case x in x) cd ../..; cp a.txt README.md ;; esac; done",
         "/base/README.md"),
        ("cd .worktrees/x; ! case x in x) cd ../..; cp a.txt README.md ;; esac",
         "/base/README.md"),
    ],
)
def test_a_case_after_a_reserved_word_still_opens(command: str, expected: str) -> None:
    """`then` / `else` / `do` の直後の `case` を入口として数える。

    数えないと見出しを閉じた `)` が `__WT_CASE_END__` にならず、枝の中の `cd` を
    見落として主ディレクトリへの書き込みを案内できない（検知漏れになる）。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == [expected], command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # `command` と `builtin` は現在のシェルで `cd` を走らせる。移動は残る。
        ("cd .worktrees/x; command cd ../..; cp a.txt README.md", "/base/README.md"),
        ("cd .worktrees/x; builtin cd ../..; cp a.txt README.md", "/base/README.md"),
        ("cd .worktrees/x; command -p cd ../..; cp a.txt README.md", "/base/README.md"),
        ("cd .worktrees/x; command -- cd ../..; cp a.txt README.md", "/base/README.md"),
        ("cd .worktrees/x; builtin -- cd ../..; cp a.txt README.md", "/base/README.md"),
        ("cd .worktrees/x; command builtin cd ../..; cp a.txt README.md",
         "/base/README.md"),
        ("command cd .worktrees/x\ncp a.txt README.md", "/base/.worktrees/x/README.md"),
        # `-v` / `-V` は名前を表示するだけで走らせない。移動として数えない。
        ("cd .worktrees/x; command -v cd; cp a.txt README.md",
         "/base/.worktrees/x/README.md"),
        ("cd .worktrees/x; command -V cd; cp a.txt README.md",
         "/base/.worktrees/x/README.md"),
        # 命令の位置でない `command` は単なる引数である。
        ("cd .worktrees/x; echo command cd ../..; cp a.txt README.md",
         "/base/.worktrees/x/README.md"),
    ],
)
def test_command_and_builtin_wrappers_keep_the_command_position(
    command: str, expected: str
) -> None:
    """`command cd` / `builtin cd` の `cd` も移動である。

    被演算子として読み飛ばすと、主ディレクトリへ戻ってからの書き込みを作業ツリー側と
    取り違えて案内を出さない（検知漏れになる）。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == [expected], command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # `"` の中の `\"` は文字列を閉じない。閉じたと読むと、残りがまるごと
        # 1 つの語へ吸い込まれ、後続の `cd` も書き込みも見えなくなる（検知漏れ）。
        ('cd .worktrees/x; echo "a\\"" ; cd ../.. ; cp a.txt README.md',
         "/base/README.md"),
        # 引用符の外の `\ ` は区切りにならない。区切ると語の後半だけを書き込み先
        # として拾い、実在しない位置を案内する。
        ("cp a.txt my\\ file.md", "/base/my file.md"),
        # 引用符の外の `\)` は部分シェルの終わりではない。終わりと読むと、まだ
        # 中にある `cd` を親のものとして数える（誤検知）。
        ("cd .worktrees/x; ( echo a\\) ; cd ../.. ; true ) ; cp a.txt README.md",
         "/base/.worktrees/x/README.md"),
        # `\` + 改行は行継続で、両方が消える。命令の区切りにもならない。
        ("cp a.txt \\\nREADME.md", "/base/README.md"),
        # `'` の中では `\` は字面で、閉じる `'` を隠さない。
        ("cd .worktrees/x; echo 'a\\' ; cd ../.. ; cp a.txt README.md",
         "/base/README.md"),
        # `"` の中で `\` がエスケープとして働くのは `$` `` ` `` `"` `\` と改行に
        # 限られる。それ以外の前では `\` が文字として残る。
        ('cp a.txt "a\\nb.md"', "/base/a\\nb.md"),
        ('cp a.txt "a\\\\b.md"', "/base/a\\b.md"),
        ('cp a.txt "a\\"b.md"', '/base/a"b.md'),
    ],
)
def test_backslash_escapes_are_honoured(command: str, expected: str) -> None:
    """`\\` の次の 1 文字はエスケープされる。**シングルクォートの中を除く。**

    期待値は bash に同じコマンドを走らせ、実際に作られたファイルで確かめた。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == [expected], command


def test_backslash_does_not_hide_a_heredoc_start() -> None:
    """`\\"` を閉じ引用符と読むと、その後の `<<` を本文の始まりとして数えない。

    本文が残ると、実行されない行の語を書き込み先として拾う（誤検知になる）。
    """
    command = 'echo "a\\"" ; cat <<EOF\ncp a.txt README.md\nEOF'
    targets, rc = extract_at(command, "/base")
    assert rc == 1, targets
    assert targets == [], targets


def test_backslash_inside_a_quoted_heredoc_delimiter() -> None:
    """終端の語の `"` の中の `\\"` も引用を閉じない。

    終端を取り違えると本文の終わりを見つけられず、後続の命令まで本文として
    落としてしまう（検知漏れになる）。
    """
    command = 'cat <<"E\\"F"\ncp a.txt README.md\nE"F\ncp b.txt OUT.md'
    targets, rc = extract_at(command, "/base")
    assert rc == 0, targets
    assert targets == ["/base/OUT.md"], targets


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # 右辺のリダイレクトは、左辺が失敗したとき＝**移動前の位置**で開かれる。
        ("cd dir || exit > fail.log", ["/base/fail.log"]),
        ("cd dir || exit 1 >> fail.log", ["/base/fail.log"]),
        ("cd dir || return > fail.log", ["/base/fail.log"]),
        ("cd dir || break > fail.log", ["/base/fail.log"]),
        ("cd dir || continue > fail.log", ["/base/fail.log"]),
        # 続きが走るのは移動した後の位置だけである。両方を別々に解決する。
        ("cd dir || exit > fail.log\ncp a.txt README.md",
         ["/base/fail.log", "/base/dir/README.md"]),
        # 失敗した `cd` を特定できるなら、その手前の位置で開かれる。
        ("cd a; cd b || exit > fail.log", ["/base/a/fail.log"]),
        ("cd a; cd b || exit > fail.log\ncp a.txt README.md",
         ["/base/a/fail.log", "/base/a/b/README.md"]),
        # ブレースグループの形は従来どおり、中を失敗時の位置で解決する。
        ("cd dir || { exit > fail.log; }", ["/base/fail.log"]),
    ],
)
def test_a_redirect_on_the_non_continuing_right_side_opens_before_the_move(
    command: str, expected: list[str]
) -> None:
    """`cd dir || exit > fail.log` の `fail.log` は移動前の位置で開かれる。

    移動後の位置で解決すると、主ディレクトリ側への書き込みを作業ツリー側と
    取り違えて案内を出さない（検知漏れになる）。期待値は bash に同じコマンドを
    走らせ、実際に作られたファイルで確かめた。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 0, (command, targets)
    assert targets == expected, command


def test_a_redirect_after_an_unlocatable_failure_is_not_resolved() -> None:
    """左辺の `cd` が複数あると、どこで失敗したかで位置が変わる。

    決められないものとして相対パスを抑止する。
    """
    targets, rc = extract_at("cd a && cd b || exit > fail.log", "/base")
    assert rc == 1, targets
    assert targets == [], targets


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # `--` 以降はオプションの解釈が止まる。`-dir` は移動先であって `cd -` ではない。
        ("cd -- -dir\nsed -i 's/a/b/' README.md", "/base/-dir/README.md"),
        ("cd -P -- -dir\nsed -i 's/a/b/' README.md", "/base/-dir/README.md"),
        ("cd -- .worktrees/x\nsed -i 's/a/b/' README.md", "/base/.worktrees/x/README.md"),
        # `--` が 2 つ並ぶと、2 つ目は移動先そのものになる。
        ("cd -- --\nsed -i 's/a/b/' README.md", "/base/--/README.md"),
    ],
)
def test_end_of_options_stops_reading_dashes_as_options(command: str, expected: str) -> None:
    targets, rc = extract_at(command, "/base")
    assert rc == 0, command
    assert targets == [expected], command


@pytest.mark.parametrize(
    "command",
    [
        # `--` の無い `cd -` は直前の位置で、字面からは追えない。
        "cd -\nsed -i 's/a/b/' README.md",
        "cd -P -\nsed -i 's/a/b/' README.md",
        # **`--` の後でも `-` は直前の位置を指す。** bash では `-` がオプション
        # ではなく被演算子の綴りとして扱われるため、`-` という名前のディレクトリが
        # あっても `$OLDPWD` へ移る（実測で確認）。
        "cd -- -\nsed -i 's/a/b/' README.md",
        "cd -P -- -\nsed -i 's/a/b/' README.md",
    ],
)
def test_a_bare_dash_still_cannot_be_followed(command: str) -> None:
    targets, rc = extract_at(command, "/base")
    assert rc == 1, command
    assert targets == []


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # 引用符の中の `>` は書き込み先の字面である。印へ置き換える前処理は
        # 引用符を見ないため、印のまま出すと案内の文字列へ内部の印が漏れる。
        ('cp a "b>c"', "/base/b>c"),
        ('cp a "b>>c"', "/base/b>>c"),
        ('sed -i \'s/a/b/\' "x>y.md"', "/base/x>y.md"),
        ("cp a 'b>c'", "/base/b>c"),
        # 元からあった空白は残す。足された空白だけを消す。
        ('cp a "b > c"', "/base/b > c"),
        # `&&` の印も同じく戻す。
        ('cp a "b&&c"', "/base/b&&c"),
        # 引用符の外の `>` は従来どおり出力の付け替えである。
        ("echo hi > out.txt", "/base/out.txt"),
        ("echo hi >> out.txt", "/base/out.txt"),
    ],
)
def test_markers_inside_quotes_are_restored(command: str, expected: str) -> None:
    targets, rc = extract_at(command, "/base")
    assert rc == 0, command
    assert targets == [expected], command


def test_markers_are_restored_without_a_base() -> None:
    """起点を渡さない呼び方でも印を残さない。"""
    targets, rc = extract("cp a \"b>c\"")
    assert rc == 0
    assert targets == ["b>c"]


@pytest.mark.parametrize(
    ("command", "base", "expected"),
    [
        # 起点を渡さない呼び方。番号が書き込み先として並ぶ。
        ("sed -i 's/a/b/' x.md 2>&1", None, ["x.md"]),
        # `cp` と `mv` は最後の被演算子を宛先とするため、番号が宛先の位置を奪う。
        ("cp a b 2>&1", "/base", ["/base/b"]),
        ("mv a b 2>>log", "/base", ["/base/b", "/base/log"]),
        ("tee out.txt 2>&1", "/base", ["/base/out.txt"]),
    ],
)
def test_a_file_descriptor_number_is_not_a_write_target(
    command: str, base: str | None, expected: list[str]
) -> None:
    """`2>&1` の `2` は記述子の番号で、開かれるファイルの名前ではない。

    番号を書き込み先として拾うと、実在しない位置を案内するうえ、`cp` / `mv` では
    本来の宛先がその位置を奪われて出なくなる。
    """
    targets, rc = extract(command) if base is None else extract_at(command, base)
    assert rc == 0, command
    assert targets == expected, command


def test_a_digit_inside_a_word_is_not_a_file_descriptor() -> None:
    """`cat file2>log` の `file2` は語であって、記述子の番号ではない。

    すべて数字の語だけを番号として落とす。落としすぎると、名前が数字で終わる
    ファイルの読み書きを取り違える。
    """
    targets, rc = extract_at("cat file2>log", "/base")
    assert rc == 0
    assert targets == ["/base/log"]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # 前置したリダイレクトの後ろの `cd` は命令であって、被演算子ではない。
        (
            "cd .worktrees/x; >/dev/null cd ../..; cp a README.md",
            ["/base/README.md"],
        ),
        (
            ">/dev/null cd .worktrees/x; cp a README.md",
            ["/base/.worktrees/x/README.md"],
        ),
        # 記述子の番号を添えた前置も同じ形である。
        (
            "2>&1 cd .worktrees/x; cp a README.md",
            ["/base/.worktrees/x/README.md"],
        ),
        # 前置したリダイレクト自身の書き込み先は、移動前の位置で開かれる。
        (
            ">log cd .worktrees/x; cp a README.md",
            ["/base/log", "/base/.worktrees/x/README.md"],
        ),
    ],
)
def test_a_redirection_before_the_command_keeps_the_command_position(
    command: str, expected: list[str]
) -> None:
    """命令名より前に置いたリダイレクトは、後ろの語を被演算子にしない。

    読み飛ばすと `cd` を移動として数えられず、後続の相対パスの起点が移動前の
    位置のままになる（案内が出ない、または移動していない位置を案内する）。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 0, command
    assert targets == expected, command


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # `;&` で落ちた枝は、前の枝の出口の位置から始まる。
        (
            "case x in x) cd .worktrees/x ;& y) cd ../..; cp a README.md ;; esac",
            "/base/README.md",
        ),
        (
            "case x in x) cd .worktrees/x ;& y) cp a README.md ;; esac",
            "/base/.worktrees/x/README.md",
        ),
        # `;;&` は次の見出しを試し直すが、見出しの評価では命令が走らない。
        # 枝の本体が始まる位置は `;&` と同じく前の枝の出口である。
        (
            "case x in x) cd .worktrees/x ;;& y) cp a README.md ;; esac",
            "/base/.worktrees/x/README.md",
        ),
    ],
)
def test_a_case_fallthrough_carries_the_previous_branch(
    command: str, expected: str
) -> None:
    """`;&` と `;;&` の後ろの枝は、前の枝の `cd` を引き継ぐ。

    引き継がないと、前の枝で作業ツリーへ移った後の書き込みを主ディレクトリ側の
    ものとして案内する（誤検知になる）。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 0, command
    assert targets == [expected], command


def test_a_case_break_still_isolates_the_branches() -> None:
    """`;;` で終わる枝どうしは排他で、前の枝の `cd` を引き継がない。"""
    targets, rc = extract_at(
        "case x in x) cd .worktrees/x ;; y) cd ../..; cp a README.md ;; esac", "/base"
    )
    assert rc == 0
    assert targets == ["/README.md"]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # 定義の 4 つの形。いずれも本体は定義した時点では走らない。
        ("f () { cd .worktrees/x; }; cp a.txt README.md", "/base/README.md"),
        ("f() { cd .worktrees/x; }; cp a.txt README.md", "/base/README.md"),
        ("function f { cd .worktrees/x; }; cp a.txt README.md", "/base/README.md"),
        ("f () ( cd .worktrees/x ); cp a.txt README.md", "/base/README.md"),
        # `function` と `()` を併せた形も bash は定義として受け取る。
        (
            "function f () { cd .worktrees/x; }; cp a.txt README.md",
            "/base/README.md",
        ),
        (
            "function f() { cd .worktrees/x; }; cp a.txt README.md",
            "/base/README.md",
        ),
        (
            "function f () ( cd .worktrees/x ); cp a.txt README.md",
            "/base/README.md",
        ),
        # 先に済ませた移動は、本体の `cd` に上書きされない。
        (
            "cd .worktrees/x; f () { cd ../..; }; cp a.txt README.md",
            "/base/.worktrees/x/README.md",
        ),
        (
            "cd .worktrees/x; function f () { cd ../..; }; cp a.txt README.md",
            "/base/.worktrees/x/README.md",
        ),
    ],
)
def test_a_function_body_does_not_move_the_definer(command: str, expected: str) -> None:
    """関数定義の本体の `cd` は、定義した側の現在地を変えない。

    定義した時点で走ったものとして数えると、後続の相対パスの起点が動き、
    案内が出ない（検知漏れ）か、移っていない位置を案内する（誤検知）。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 0, command
    assert targets == [expected], command


def test_a_function_body_still_reports_its_writes() -> None:
    """本体の中の書き込み先は出す。

    定義しただけの本体は走らないが、同じコマンドの中で呼ぶ形があるため、
    出さないと案内の機会を失う。`if` の本体・`case` の枝と同じ扱いである。
    """
    targets, rc = extract_at("f () { cp a.txt README.md; }", "/base")
    assert rc == 0
    assert targets == ["/base/README.md"]


@pytest.mark.parametrize(
    "command",
    [
        "f () { cd .worktrees/x; }; f; cp a.txt README.md",
        "f() { cd .worktrees/x; }; f; cp a.txt README.md",
        "function f { cd .worktrees/x; }; f; cp a.txt README.md",
        # `function` と `()` を併せた形でも、控える名前は `()` を含まない。
        "function f () { cd .worktrees/x; }; f; cp a.txt README.md",
        "function f() { cd .worktrees/x; }; f; cp a.txt README.md",
    ],
)
def test_calling_a_moving_function_leaves_the_position_undecidable(
    command: str,
) -> None:
    """本体で移動する関数を呼んだ後の現在地は、字面から決められない。

    本体の相対パスは呼び出しの時点の現在地から解決されるため、当てはめるには
    本体をもう一度読み直すことになる（1 パス走査の枠を出る）。
    """
    targets, rc = extract_at(command, "/base")
    assert rc == 1, command
    assert targets == [], command


def test_a_brace_group_still_carries_cd_outside_itself() -> None:
    """関数定義ではないまとまりは同じシェルで走り、移動は後続へ残る。"""
    targets, rc = extract_at("{ cd .worktrees/x; }; cp a.txt README.md", "/base")
    assert rc == 0
    assert targets == ["/base/.worktrees/x/README.md"]
