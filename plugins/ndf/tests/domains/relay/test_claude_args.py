"""claude の引数の読み分け（relay.md の「ラッパーを挟まない起動」・「セッションをまたいで設定を保つ」）。"""

from __future__ import annotations

import pytest

from ndf.domains.relay import claude_args

ALIAS = "--dangerously-skip-permissions"  # devbase の alias が先頭に付ける


@pytest.mark.parametrize(
    ("args", "environ", "tty", "notice"),
    [
        ([ALIAS, "-p", "要約して"], {}, True, False),
        ([ALIAS, "--version"], {}, True, False),
        (["--help"], {}, True, False),
        (["-c", "--version"], {}, True, False),
        ([ALIAS, "mcp", "list"], {}, True, False),
        (["doctor"], {}, True, False),
        ([ALIAS], {}, False, False),
        ([ALIAS], {"NDF_RELAY": "0"}, True, False),
        ([ALIAS, "-c"], {}, True, True),
        ([ALIAS, "--resume"], {}, True, True),
        (["--fork-session", "--resume", "abc"], {}, True, True),
        (["--tmux=classic"], {}, True, True),
        (["質問", "もう 1 つ"], {}, True, True),
    ],
)
def test_launches_the_wrapper_cannot_hold_pass_through(args, environ, tty, notice):
    got = claude_args.parse(args, environ, tty)
    assert got.passthrough and got.notice is notice


def test_session_options_are_split_from_the_ones_every_session_keeps():
    got = claude_args.parse(
        [ALIAS, "--model", "opus", "--add-dir", "a", "b", "--resume", "abc", "-n", "作業", "--settings=x.json", "最初の質問"],
        {},
        True,
    )
    assert got.passthrough is None
    assert got.carried == (ALIAS, "--model", "opus", "--add-dir", "a", "b", "--settings=x.json")
    assert (got.first, got.resume, got.session_id, got.prompt) == (("-n", "作業"), "abc", None, "最初の質問")


def test_variadic_values_and_double_dash():
    # 値を複数取るオプションは次のオプションまで読み、`--` から後ろは最初のプロンプト
    got = claude_args.parse(["--add-dir", "a", "b", "--session-id", "s1", "--", "-で始まる質問"], {}, True)
    assert (got.carried, got.session_id, got.prompt) == (("--add-dir", "a", "b"), "s1", "-で始まる質問")


def test_unknown_options_keep_their_value():
    got = claude_args.parse(["--new-option", "v", "--new-flag"], {}, True)
    assert got.passthrough is None and got.carried == ("--new-option", "v", "--new-flag") and got.prompt is None
