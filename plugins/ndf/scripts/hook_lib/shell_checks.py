"""Bash のコマンドの 2 つの判定（token の guard が使う。#829・#1142 の決定 20）。構文木は `lib/shparse.py` が作る。

- `sleep_deny(cmd, limit)`: 前景の `sleep` で待つか。ループの本体の `sleep` と、`limit` 秒を超える `sleep` を当てる。
  `&` で背景へ回したもの・ヒアドキュメントとコメントの中は当てない。置換の中・`bash -c` / `sh -c`・`eval` の中は前景で動く
- `plan_command(cmd)`: `supervise.py` の `queue` か `run`（プランを起こす副命令）を起動するか。コマンドの位置で起動して
  いるものだけを見る（`echo` の引数・引用やヒアドキュメントの中の文字列は拾わない）。インタプリタのフラグ（`python3 -u`）と
  `env`・`nohup`・変数の代入は挟んでよい
"""
from __future__ import annotations

import re

import shparse as sp

UNIT = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
WRAPPERS = frozenset({"exec", "command", "nohup", "env", "nice", "timeout", "time"})
SHELLS = frozenset({"bash", "sh", "zsh", "dash"})
SHELL_OPT_WITH_ARG = re.compile(r"[-+][A-Za-bd-z]*[oO]")
ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[[^]]*\])?\+?=")
SECONDS = re.compile(r"(\d+(?:\.\d+)?)([smhd]?)")
PYTHON = re.compile(r"\S*python3?")
MAX_DEPTH = 5


def seconds(word: str) -> float | None:
    m = SECONDS.fullmatch(word)
    return float(m.group(1)) * UNIT[m.group(2)] if m else None


def _words(n: sp.Node) -> list[sp.Word]:
    return [sp.unquote(c) for c in n.children if sp.field_of(n, c) in ("name", "argument")]


def sleep_deny(cmd: str, limit: float, in_loop: bool = False, depth: int = 0) -> bool:
    if depth > MAX_DEPTH or not cmd:
        return False
    return _sleep(sp.parse_bash(cmd), limit, in_loop, False, depth)


def _sleep(n: sp.Node, limit: float, loop: bool, bg: bool, depth: int) -> bool:
    t = n.type
    if t in ("heredoc_body", "comment"):
        return False
    if t == "command":
        return (not bg) and _sleep_command(n, limit, loop, depth)
    if t == "while_statement":
        return any(_sleep(c, limit, loop or c.type == "do_group", bg, depth) for c in n.children if c.is_named)
    if t in sp.STATEMENT_LISTS or t == "subshell":
        return any(_sleep(c, limit, loop, bg or b, depth) for c, b in sp.statement_list(n))
    return any(_sleep(c, limit, loop, bg or (c.next_sibling is not None and c.next_sibling.type == "&"), depth)
               for c in n.children if c.is_named)


def _sleep_command(n: sp.Node, limit: float, loop: bool, depth: int) -> bool:
    for c in n.children:  # 置換の中は前景で動く
        if c.type in ("file_redirect", "heredoc_redirect"):
            continue
        subs = [c] if c.type in sp.SUBSTITUTIONS else list(sp.substitutions(c))
        if any(_sleep(s, limit, loop, False, depth) for s in subs):
            return True
    words = _words(n)
    i = 0
    while i < len(words) and words[i] in WRAPPERS:
        i += 1
        while i < len(words) and (words[i][:1] == "-" or seconds(words[i]) is not None or ASSIGN.match(words[i])):
            i += 1
    if i >= len(words):
        return False
    name, rest = words[i], words[i + 1:]
    if name == "sleep" and rest:
        sec = seconds(rest[0])
        return loop or (sec is not None and sec > limit)
    if name in SHELLS:
        j = 0
        while j < len(rest) and rest[j][:1] in "-+":
            if SHELL_OPT_WITH_ARG.fullmatch(rest[j]):
                j += 2
                continue
            if rest[j].startswith("-") and not rest[j].startswith("--") and "c" in rest[j].lstrip("-"):
                return j + 1 < len(rest) and sleep_deny(rest[j + 1], limit, loop, depth + 1)
            j += 1
        return False
    if name == "eval":
        return sleep_deny(" ".join(rest), limit, loop, depth + 1)
    return False


def plan_command(cmd: str) -> bool:
    if "supervise.py" not in cmd:
        return False
    return _plan(sp.parse_bash(cmd))


def _plan(n: sp.Node) -> bool:
    if n.type == "heredoc_body":
        return False
    if n.type == "command":
        words = _words(n)
        i = 0
        while i < len(words) and (words[i] in ("env", "nohup") or ASSIGN.match(words[i])):
            i += 1
        if i < len(words) and PYTHON.fullmatch(words[i]):
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 1
        if i + 1 < len(words) and words[i].endswith("supervise.py") and words[i + 1] in ("queue", "run"):
            return True
    return any(_plan(c) for c in n.children if c.child_count)
