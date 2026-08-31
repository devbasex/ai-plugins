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
