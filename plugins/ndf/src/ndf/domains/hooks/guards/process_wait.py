"""プロセス名の待ちの guard（#3851）。`pgrep -f` / `pkill -f` を `while` / `until` のループの条件か本体に置いた呼び出しを止める。

`-f`（`--full`・束ねた `-af` を含む）はコマンド行の全体と照らすため、待つ側のシェルのコマンド行にも一致して終わらない。
前景でも背景でも止め、終わりは完了マーカーのファイルか Monitor で待つよう案内する。ループの外の単発の呼び出しと
`-f` を付けない呼び出しは止めない。`bash -c` 系・`eval` の中身とラッパーのコマンドの後ろも見る（`ShellScript.waits`）。
"""

from __future__ import annotations

import os
import signal

from ndf.domains.hooks.verdict import Verdict, deny

# 値を取る短いオプション（procps-ng 4.0.4 の `--help`。`q` は pkill だけ）と長いオプション
_PGREP_VALUE_SHORT = "dgGOPstuUFrq"
_PGREP_VALUE_LONG = frozenset(
    {
        "--delimiter",
        "--pgroup",
        "--group",
        "--older",
        "--parent",
        "--session",
        "--signal",
        "--terminal",
        "--euid",
        "--uid",
        "--pidfile",
        "--runstates",
        "--cgroup",
        "--ns",
        "--nslist",
        "--queue",
    }
)
_COMMANDS = ("pgrep", "pkill")
_SIGNAL_NAMES = frozenset({s.name for s in signal.Signals} | {s.name[3:] for s in signal.Signals})


def check(ctx, svc) -> Verdict | None:
    for cmd in ctx.shell.waits:
        name = os.path.basename(cmd.body_argv[0]) if cmd.body_argv else ""
        if name in _COMMANDS and (cmd.loop_body or cmd.loop_condition) and _full_match(name, cmd.body_argv[1:]):
            return deny("pgrep -f / pkill -f は待つ側のコマンド行にも一致して終わらない。終わりは完了マーカーのファイルか Monitor で待つ")
    return None


def _full_match(name: str, args: tuple[str, ...]) -> bool:
    """`pgrep` / `pkill` の引数を getopt の順で読み、オプションとして `-f` か `--full` を持つか。`--` の後ろと値は見ない。"""
    values = _PGREP_VALUE_SHORT if name == "pkill" else _PGREP_VALUE_SHORT.replace("q", "")
    skip = False
    for word in args:
        if skip or not word.startswith("-") or word == "-":
            skip = False
            continue
        if word == "--":
            return False
        if word.startswith("--"):
            option, eq, _ = word.partition("=")
            if option == "--full":
                return True
            skip = option in _PGREP_VALUE_LONG and not eq
        elif name == "pkill" and (word[1:].isdigit() or word[1:] in _SIGNAL_NAMES):
            continue
        else:
            found, skip = _short_bundle(word, values)
            if found:
                return True
    return False


def _short_bundle(word: str, values: str) -> tuple[bool, bool]:
    """短いオプションの束の（`f` を持つか, 次の語が値か）。値を取る文字の後ろは値として読み終える。"""
    for j, ch in enumerate(word[1:], start=2):
        if ch == "f":
            return True, False
        if ch in values:
            return False, not word[j:]
    return False, False
