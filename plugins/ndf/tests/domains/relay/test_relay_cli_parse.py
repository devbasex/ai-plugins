"""`ndf relay` の引数の読み取り（_parse）が、組み合わせの検査で何を断り何を通すかを固定する。"""

from __future__ import annotations

import pytest

from ndf.contracts import causes
from ndf.domains.relay import cli, context_records, context_thresholds

SEAT_NEEDS = "--seat は --replace を、--seated は --replace・--seat・--name をすべて要する"
REPLACE_NEEDS = "--replace は --seat を要する"
HELD_NEEDS = "--held は --replace を要する"
RECORD_NEEDS = "--verdict 再起動 には --reason を、--event には --reason を付けない"


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["spawn", "--seat", "s"], SEAT_NEEDS),
        (["spawn", "--seat", "s", "--name", "l1"], SEAT_NEEDS),
        (["spawn", "--seated"], SEAT_NEEDS),
        (["spawn", "--seated", "--name", "l1"], SEAT_NEEDS),
        (["spawn", "--seated", "--seat", "s", "--name", "l1"], SEAT_NEEDS),
        (["spawn", "--replace", "--seated", "--name", "l1"], SEAT_NEEDS),
        (["spawn", "--replace", "--seat", "s", "--seated"], SEAT_NEEDS),
        (["spawn", "--replace"], REPLACE_NEEDS),
        (["spawn", "--replace", "--name", "l1"], REPLACE_NEEDS),
        (["spawn", "--replace", "--seat", "", "--name", "l1"], REPLACE_NEEDS),
        (["spawn", "--held", "w-b", "--name", "l1"], HELD_NEEDS),
    ],
)
def test_spawn_refuses_each_bad_combination_with_its_reason(argv, message, capsys):
    with pytest.raises(SystemExit) as exc:
        cli._parse(argv)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("usage: ndf relay ")
    assert err.rstrip("\n").endswith(f"error: {message}")
    # 先に当たった理由の 1 つだけを出す
    assert err.count("error:") == 1


@pytest.mark.parametrize(
    ("argv", "own"),
    [
        (["spawn"], {"replace": False, "seat": None, "seated": False, "name": None}),
        (["spawn", "--name", "l1"], {"replace": False, "seat": None, "seated": False, "name": "l1"}),
        (["spawn", "--replace", "--seat", "s"], {"replace": True, "seat": "s", "seated": False, "name": None}),
        (["spawn", "--replace", "--seat", "s", "--name", "l1"], {"replace": True, "seat": "s", "seated": False, "name": "l1"}),
        (["spawn", "--seat", "s", "--replace", "--name", "l1", "--seated"], {"replace": True, "seat": "s", "seated": True, "name": "l1"}),
        (["spawn", "--name=l1", "--replace", "--seat=s", "--seated"], {"replace": True, "seat": "s", "seated": True, "name": "l1"}),
        (["spawn", "--seat", "", "--name", "l1"], {"replace": False, "seat": "", "seated": False, "name": "l1"}),
    ],
)
def test_spawn_accepts_the_allowed_combinations(argv, own, capsys):
    args = cli._parse(argv)
    assert {key: getattr(args, key) for key in own} == own
    assert args.rest == []
    assert capsys.readouterr().err == ""


def test_the_seat_check_only_applies_to_spawn():
    # run には --seat が無いので claude の引数として渡る
    args = cli._parse(["run", "--seat", "s"])
    assert args.rest == ["--seat", "s"]


def test_options_after_the_claude_args_are_not_checked():
    # 先頭の ndf のオプションの後ろは claude の引数で、組み合わせの検査に入らない
    args = cli._parse(["spawn", "--name", "l1", "hello", "--seat", "s"])
    assert (args.seat, args.replace) == (None, False)
    assert args.rest == ["hello", "--seat", "s"]


@pytest.mark.parametrize(
    "tail",
    [
        ["--verdict", context_thresholds.RESTART],
        ["--event", context_records.MARKS[0], "--reason", context_thresholds.REASONS[0]],
    ],
)
def test_context_record_refuses_a_mismatched_reason(tail, capsys):
    with pytest.raises(SystemExit) as exc:
        cli._parse(["context", "record", "--work", "w", *tail])
    assert exc.value.code == 2
    assert capsys.readouterr().err.rstrip("\n").endswith(f"error: {RECORD_NEEDS}")


@pytest.mark.parametrize("tail", [["--policy", "current"], ["--root", "."]])
def test_context_resolve_refuses_the_removed_options(tail, capsys):
    # #3995 の 3995:3: 再起動の方針を消したので、resolve の --policy・--root は引数の誤り
    with pytest.raises(SystemExit) as exc:
        cli._parse(["context", "resolve", *tail])
    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["spawn", "--seat", "s"],
        ["spawn", "--replace", "--name", "l1"],
    ],
)
def test_main_turns_a_refused_combination_into_the_invalid_exit_code(argv, capsys):
    assert cli.main(argv) == causes.exit_code("invalid")
    assert capsys.readouterr().out == ""
