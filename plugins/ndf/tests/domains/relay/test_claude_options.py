"""`claude_options.take` の分岐ごとの読み方を固定する（I-020 の書き換え前の振る舞い）。"""

from __future__ import annotations

import pytest

from ndf.domains.relay.claude_options import Option, take

TABLE = {
    "flags": frozenset({"--print", "-p"}),
    "variadic": frozenset({"--add-dir"}),
    "value": frozenset({"--model"}),
}


@pytest.mark.parametrize(
    ("args", "i", "want"),
    [
        # `--名前=値` は 1 語で読む
        (["--model=opus", "x"], 0, (Option("--model", ("--model=opus",), "opus"), 1)),
        # `-` 1 つの `=` は分けない（表に無い語として次を見る）
        (["-m=opus", "x"], 0, (Option("-m=opus", ("-m=opus", "x"), "x"), 2)),
        (["-m=opus"], 0, (Option("-m=opus", ("-m=opus",), None), 1)),
        # flags は次の語を取らない
        (["--print", "hello"], 0, (Option("--print", ("--print",), None), 1)),
        (["-p"], 0, (Option("-p", ("-p",), None), 1)),
        # variadic は次の `-` 始まりまで取る
        (["--add-dir", "a", "b", "--print"], 0, (Option("--add-dir", ("--add-dir", "a", "b"), None), 3)),
        (["--add-dir", "a", "b"], 0, (Option("--add-dir", ("--add-dir", "a", "b"), None), 3)),
        (["--add-dir", "--print"], 0, (Option("--add-dir", ("--add-dir",), None), 1)),
        # value は次の語が `-` 始まりでも取る
        (["--model", "opus"], 0, (Option("--model", ("--model", "opus"), "opus"), 2)),
        (["--model", "-x"], 0, (Option("--model", ("--model", "-x"), "-x"), 2)),
        # value でも次の語が無ければ値なし
        (["--model"], 0, (Option("--model", ("--model",), None), 1)),
        # 表に無い語は次の語が `-` 始まりでなければ値ごと取る
        (["--unknown", "v", "w"], 0, (Option("--unknown", ("--unknown", "v"), "v"), 2)),
        (["--unknown", "--print"], 0, (Option("--unknown", ("--unknown",), None), 1)),
        (["--unknown", "-"], 0, (Option("--unknown", ("--unknown",), None), 1)),
        (["--unknown"], 0, (Option("--unknown", ("--unknown",), None), 1)),
        # 空の語は `-` 始まりでないので値になる
        (["--unknown", ""], 0, (Option("--unknown", ("--unknown", ""), ""), 2)),
        # 途中の位置から読む
        (["x", "--unknown", "v"], 1, (Option("--unknown", ("--unknown", "v"), "v"), 3)),
        (["x", "--model"], 1, (Option("--model", ("--model",), None), 2)),
    ],
)
def test_take_reads_one_option(args, i, want):
    assert take(TABLE, args, i) == want
