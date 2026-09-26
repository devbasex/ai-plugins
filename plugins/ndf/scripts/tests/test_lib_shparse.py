"""シェルの字句・構文の解析の包み（lib/shparse.py・#1142 の決定 19・20）。uv の環境の外では test_wrappers_uv_env.py が流し直す。

試行 T2（docs/ndf-experiments.md の hook-trial の行）で見つけた構文木の癖 5 つを包みが直すことと、
読めない 2 つの形の扱いを固定する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
pytest.importorskip("tree_sitter_bash")
import shparse  # noqa: E402


def first(cmd: str):
    return shparse.statement_list(shparse.parse_bash(cmd))[0].node


def test_trailing_redirect_of_a_list_is_reattached_to_the_last_command():
    r = shparse.split_redirects(first("a && b > out.txt"))
    assert r.body.type == "list" and r.reattach
    assert [shparse.node_text(x) for x in r.redirects] == ["> out.txt"]
    r = shparse.split_redirects(first("{ a; } > out.txt"))
    assert not r.reattach  # 複合コマンドのリダイレクトは入る前の位置で開く


def test_words_after_a_redirect_operand_are_command_arguments():
    r = shparse.split_redirects(first("echo x > f extra args"))
    assert shparse.command_words(r.body, r.redirects) == ["echo", "x", "extra", "args"]
    n = first("cat <<EOF more\nbody\nEOF")
    r = shparse.split_redirects(n)
    assert shparse.command_words(r.body, r.redirects) == ["cat", "more"]


def test_read_write_operator_is_joined():
    r = shparse.split_redirects(first("cat <> f")).redirects[0]
    assert shparse.redirect_operator(r) == "<>"
    assert shparse.unquote(shparse.redirect_destination(r)) == "f"
    assert shparse.redirect_operator(shparse.split_redirects(first("x &>> 'a b'")).redirects[0]) == "&>>"


def test_time_command_and_builtin_are_skipped_for_the_name():
    words = shparse.command_words(first("time command -p cp a b"))
    assert words[shparse.command_name_index(words)] == "cp"
    assert shparse.command_name_index(["builtin", "--", "cd", "x"]) == 2


def test_heredoc_body_skips_arithmetic_and_reads_backticks():
    n = first("cat <<EOF\n$((1+2)) `touch x` $(touch y)\nEOF")
    assert [shparse.node_text(x) for x in shparse.substitutions(n)] == ["touch x", "$(touch y)"]
    assert list(shparse.substitutions(first("cat <<'EOF'\n$(touch y) `z`\nEOF"))) == []
    assert [shparse.node_text(x) for x in shparse.substitutions(first("echo \"$(a)\" <(b)"))] == ["$(a)", "<(b)"]


def test_unreadable_forms():
    assert shparse.has_parse_error("! case x in a) ;; esac")
    assert not shparse.has_parse_error("! true")
    words = shparse.command_words(first("cp a.txt $(basename b).txt"))
    assert words[-1].startswith("$")  # 展開を含む語は `$` を含んだまま返る


def test_unquote_tilde_background_and_newline_after_operator():
    w = shparse.unquote(first("~/x").child_by_field_name("name"))
    assert w == "~/x" and w.tilde
    w = shparse.unquote(first("'~/x'").child_by_field_name("name"))
    assert w == "~/x" and not w.tilde
    sts = shparse.statement_list(shparse.parse_bash("a & b; # c\nd"))
    assert [(shparse.node_text(s.node), s.background) for s in sts] == [("a", True), ("b", False), ("d", False)]
    r = shparse.split_redirects(first("echo >\nf")).redirects[0]
    assert shparse.destination_after_newline(r, shparse.redirect_destination(r))
    n = first("echo x")
    assert [shparse.field_of(n, c) for c in n.children] == ["name", "argument"]
    assert shparse.field_of(n, first("echo y").children[0]) is None  # 別の木の節
