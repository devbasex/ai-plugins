"""前景の sleep とプランを起こす副命令の判定（ht_shparse.py の構文木の上。T2 の試作）。

- sleep_deny(cmd, limit): 前景の sleep を止めるか（lib/token_guard_sleep.py の should_deny と同じ規則）
- plan_command(cmd): supervise.py の queue / run を起動するか（token-guard.sh の plan_command と同じ規則）
"""
from __future__ import annotations

import re

from ht_shparse import SEQ, SUBST, field, parse, statements, value


# --- 前景の sleep --------------------------------------------------------------

UNIT = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
WRAPPERS = {"exec", "command", "nohup", "env", "nice", "timeout", "time"}
SHELLS = {"bash", "sh", "zsh", "dash"}
SHELL_OPT_WITH_ARG = re.compile(r"[-+][A-Za-bd-z]*[oO]")
ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[[^]]*\])?\+?=")


def seconds(w: str) -> float | None:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd]?)", w)
    return float(m.group(1)) * UNIT[m.group(2)] if m else None


def sleep_deny(cmd: str, limit: float, in_loop: bool = False, depth: int = 0) -> bool:
    if depth > 5 or not cmd:
        return False
    return _sleep(parse(cmd), limit, in_loop, False, depth)


def _sleep(n, limit, loop, bg, depth) -> bool:
    t = n.type
    if t in ("heredoc_body", "comment"):
        return False
    if t == "command":
        return (not bg) and _sleep_cmd(n, limit, loop, depth)
    if t == "while_statement":
        for c in n.children:
            if c.is_named and _sleep(c, limit, loop or c.type == "do_group", bg, depth):
                return True
        return False
    if t in SEQ or t == "subshell":
        return any(_sleep(c, limit, loop, bg or b, depth) for c, b in statements(n))
    return any(_sleep(c, limit, loop, bg or (c.next_sibling is not None and c.next_sibling.type == "&"), depth)
               for c in n.children if c.is_named)


def _sleep_cmd(n, limit, loop, depth) -> bool:
    words = [value(c) for c in n.children if field(n, c) in ("name", "argument")]
    for c in n.children:  # 置換の中は前景で動く
        if c.type in ("file_redirect", "heredoc_redirect"):
            continue
        subs = [c] if c.type in SUBST else list(_substs(c))
        if any(_sleep(s, limit, loop, False, depth) for s in subs):
            return True
    i = 0
    while i < len(words):
        w = words[i]
        if w in WRAPPERS:
            i += 1
            while i < len(words) and (words[i][:1] == "-" or seconds(words[i]) is not None or ASSIGN.match(words[i])):
                i += 1
            continue
        break
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
            if rest[j].startswith("-") and "c" in rest[j].lstrip("-") and not rest[j].startswith("--"):
                return j + 1 < len(rest) and sleep_deny(rest[j + 1], limit, loop, depth + 1)
            j += 1
        return False
    if name == "eval":
        return sleep_deny(" ".join(rest), limit, loop, depth + 1)
    return False


def _substs(n):
    for c in n.children:
        if c.type in SUBST:
            yield c
        elif c.child_count:
            yield from _substs(c)


# --- プランを起こす副命令 --------------------------------------------------------

def plan_command(cmd: str) -> bool:
    if "supervise.py" not in cmd:
        return False
    return _plan(parse(cmd))


def _plan(n) -> bool:
    if n.type == "heredoc_body":
        return False
    if n.type == "command":
        words = [value(c) for c in n.children if field(n, c) in ("name", "argument")]
        i = 0
        while i < len(words) and (words[i] in ("env", "nohup") or ASSIGN.match(words[i])):
            i += 1
        if i < len(words) and re.fullmatch(r"\S*python3?", words[i]):
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 1
        if i + 1 < len(words) and words[i].endswith("supervise.py") and words[i + 1] in ("queue", "run"):
            return True
    return any(_plan(c) for c in n.children if c.child_count)
