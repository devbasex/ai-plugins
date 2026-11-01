"""シェルのエントリポイントから、引数・環境変数・ファイルの書き・終了コード・設定の読みを拾う。

環境変数は「ファイルの中で代入されない大文字の変数」、引数は `case` の `--名前)`、書きは `>`・`>>` の
文字列のファイル名。`source` した先は追わない（設計の決定 5）。
"""

from __future__ import annotations

import re
from pathlib import Path

from contracts_lib.extract_py import FILE_NAME

VAR = re.compile(r"\$\{?([A-Z_][A-Z0-9_]*)")
ASSIGN = re.compile(r"(?:^|[\s;(]|\bexport\s+|\blocal\s+|\bdeclare\s+(?:-\w+\s+)*|\breadonly\s+)([A-Z_][A-Z0-9_]*)\+?=")
READ_VARS = re.compile(r"\b(?:read|for|mapfile|readarray|getopts)\s+(?:-\w+\s+)*([A-Z_][A-Z0-9_ ]*)")
CASE_ARM = re.compile(r"^\s*\(?((?:--?[\w-]+\|?)+)\)")
REDIRECT = re.compile(r"(?<![0-9&<])>>?\s*\"?([^\s\"';|&)<>]+)")
EXIT = re.compile(r"\bexit\s+([0-9]+)\b")
BUILTINS = {
    "BASH_SOURCE",
    "BASH_REMATCH",
    "BASH_VERSION",
    "BASHPID",
    "FUNCNAME",
    "LINENO",
    "PIPESTATUS",
    "IFS",
    "OPTARG",
    "OPTIND",
    "PPID",
    "PWD",
    "OLDPWD",
    "RANDOM",
    "SECONDS",
    "EPOCHSECONDS",
    "EPOCHREALTIME",
    "UID",
    "EUID",
    "HOSTNAME",
    "SHLVL",
    "REPLY",
    "COLUMNS",
    "LINES",
    "BASH_LINENO",
    "HOSTTYPE",
    "OSTYPE",
    "MACHTYPE",
    "GROUPS",
    "BASH",
}


def _code_lines(text: str) -> list[tuple[int, str]]:
    out, heredoc = [], None
    for i, line in enumerate(text.splitlines(), 1):
        if heredoc is not None:
            if line.strip() == heredoc:
                heredoc = None
            continue
        s = line.strip()
        if s.startswith("#"):
            continue
        m = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", line)
        if m:
            heredoc = m.group(1)
        out.append((i, line))
    return out


def summary(text: str) -> str:
    for line in text.splitlines()[1:12]:
        s = line.lstrip("#").strip()
        if line.startswith("#") and s and not s.startswith(("shellcheck", "-*-")):
            return s
    return ""


def facts(root: Path, rel: str, settings: dict[str, dict]) -> dict:
    text = (root / rel).read_text(errors="ignore")
    lines = _code_lines(text)
    assigned = set()
    for _, line in lines:
        assigned |= set(ASSIGN.findall(line))
        for m in READ_VARS.findall(line):
            assigned |= set(m.split())
    env, opts, writes, opens, exits = {}, set(), {}, {}, {"0": ""}
    for i, line in lines:
        for v in VAR.findall(line):
            if v not in assigned and v not in BUILTINS:
                env.setdefault(v, f"{rel}:{i}")
        m = CASE_ARM.match(line)
        if m:
            opts |= {("", o) for o in m.group(1).split("|") if o.startswith("--")}
        for w in REDIRECT.findall(line):
            name = w.rsplit("/", 1)[-1]
            name = re.sub(r"\$\{?\w+\}?", "*", name)
            if FILE_NAME.match(name) and name not in settings and not w.startswith("/dev/"):
                writes.setdefault(name, f"{rel}:{i}")
        for name in settings:
            if f".ndf/{name}" in line and rel not in settings[name].get("readers", ()):
                opens.setdefault((rel, name, "<shell>"), f"{rel}:{i}")
        for c in EXIT.findall(line):
            exits.setdefault(c, "")
    return {
        "summary": summary(text),
        "argv_manual": False,
        "options": opts,
        "positionals": set(),
        "env": env,
        "env_dynamic": {},
        "writes": writes,
        "setting_keys": {},
        "setting_opens": opens,
        "exits": exits,
        "stdin": False,
        "stdout": "text",
        "unparsed": [],
    }
