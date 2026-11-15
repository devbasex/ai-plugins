"""引数の取り出し（`ndf.checks.spec_args`）: 副命令の main を引数を読む直前で止め、argparse の定義から引数の木を作る。"""

from __future__ import annotations

import argparse
import sys
import types

import pytest

from ndf import cli
from ndf.checks import spec_args
from ndf.checks.model import CheckError
from ndf.contracts import facts


def _module(monkeypatch, name: str, main) -> str:
    mod = types.ModuleType(name)
    mod.main = main
    monkeypatch.setitem(sys.modules, name, mod)
    return name


def _two_level_main(argv):
    parser = argparse.ArgumentParser(prog="x")
    parser.add_argument("--top", type=int, default=3, help="上の引数")
    sub = parser.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add")
    add.add_argument("name")
    add.add_argument("--tag", action="append", choices=["a", "b"])
    rm = sub.add_parser("rm")
    rm.add_argument("--force", action="store_true")
    parser.parse_args(argv)
    return 0


def test_extract_reads_options_and_subcommands(monkeypatch):
    tree = spec_args.extract(_module(monkeypatch, "fake_two_level", _two_level_main))
    assert tree is not None
    assert [a.name for a in tree.options] == ["--top"]
    assert tree.options[0].type == "integer" and tree.options[0].default == 3
    assert sorted(tree.subcommands) == ["add", "rm"]
    name, tag = tree.subcommands["add"].options
    assert (name.positional, name.required, name.type) == (True, True, "string")
    assert (tag.type, tag.items, tag.choices) == ("array", "string", ("a", "b"))
    force = tree.subcommands["rm"].options[0]
    assert (force.type, force.default, force.required) == ("boolean", None, False)


def test_extract_restores_parse_known_args_and_returns_none_without_parser(monkeypatch):
    before = argparse.ArgumentParser.parse_known_args
    assert spec_args.extract(_module(monkeypatch, "fake_no_parser", lambda argv: 0)) is None
    assert spec_args.extract(_module(monkeypatch, "fake_exit", lambda argv: sys.exit(2))) is None
    assert argparse.ArgumentParser.parse_known_args is before


def test_extract_unimportable_module_is_check_error():
    with pytest.raises(CheckError):
        spec_args.extract("ndf.no_such_module_for_spec_args")


def test_arg_of_skips_help_and_drops_suppressed_default():
    parser = argparse.ArgumentParser()
    action = parser.add_argument("--x", default=argparse.SUPPRESS)
    assert spec_args.arg_of(parser._actions[0]) is None
    assert spec_args.arg_of(action).default is None


def test_remainder_positional_is_an_optional_array():
    parser = argparse.ArgumentParser()
    got = spec_args.arg_of(parser.add_argument("rest", nargs=argparse.REMAINDER))
    assert (got.type, got.items, got.required) == ("array", "string", False)


def test_append_with_many_values_is_an_array_of_arrays():
    parser = argparse.ArgumentParser()
    staged = spec_args.arg_of(parser.add_argument("--then", action="append", nargs="+"))
    flat = spec_args.arg_of(parser.add_argument("--tag", action="append"))
    assert (staged.type, staged.items, staged.repeated) == ("array", "string", True)
    assert (flat.type, flat.items, flat.repeated) == ("array", "string", False)
    assert parser.parse_args(["--then", "a", "b", "--then", "c"]).then == [["a", "b"], ["c"]]


def test_from_manual_builds_the_tree_from_rows():
    rows = [{"id": "n send", "command": "n", "path": "send", "args": ["--to:string:required:宛先"], "meaning": "m"}]
    tree = spec_args.from_manual(rows)
    assert tree.options == () and list(tree.subcommands) == ["send"]
    to = tree.subcommands["send"].options[0]
    assert (to.name, to.type, to.required, to.help) == ("--to", "string", True, "宛先")
    assert spec_args.from_manual([{"id": "n", "command": "n", "meaning": "m"}]) == spec_args.ArgTree()


def test_every_command_has_arguments_from_argparse_or_the_manual_table():
    manual = {r["command"] for r in facts.rows("manual-args")}
    for name, module in cli.COMMANDS.items():
        extracted = spec_args.extract(module) is not None
        assert extracted != (name in manual), name
