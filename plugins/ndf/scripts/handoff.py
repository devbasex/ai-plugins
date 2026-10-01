#!/usr/bin/env python3
r"""引継ぎ文書（`.ndf/handoff/<名>.md`）のパスの解決・作る・探す・置き換え・検査・消す（#1560）。

規則の正本は `skills/development-workflow/references/handoff.md`、節の見出しと順の唯一の定義は
`scripts/data/handoff-template.md` にある。LLM を呼ばない。置き場は常にメインディレクトリ
（`git rev-parse --git-common-dir` の親）の `.ndf/handoff/` で、`--root` が worktree でも worktree の `.ndf/` には書かない。
文書はコミットせず、`.gitignore` にも書かない。

| 副命令 | 何をする | 終了コード |
| --- | --- | --- |
| `path <名かパス> [--exists]` | 本体と履歴の絶対パス | 0 解決した / 1 `--exists` で本体が無い |
| `init <名> --title <表示名>` | 本体が無ければ雛形から作る。あれば変えない | 0 |
| `find --text <再開の入力>` | 入力の語に一致する本体を探す | 0 ちょうど 1 本 / 1 0 本 / 4 2 本以上 |
| `next <名>` | 標準入力の再開コマンドで「次に実行するコマンド」の節を置き換える | 0 置き換えた / 1 節が無い |
| `check <名> [--trim] [--max-lines N]` | 節の形と行数（既定 300）を確かめる。`--trim` は先に「前の会話の進み」を履歴へ移す | 0 通る / 1 形の違反 / 5 行数だけ超える |
| `remove <名>` | 本体と履歴を消し、消したパスを返す | 0 |

共通: 名が `sprint-<名>`・`milestone-<番号>`・`issue-<番号>` の形でない・パスが `.ndf/handoff/` の下でない・
読めない・書けない → 2。git の中でない・メインディレクトリに `.ndf/` が無い → 3。
出力は `lib/step_result.py` の形の 1 行の JSON（`tool` は `handoff`）。

    python3 handoff.py init milestone-26 --title "マイルストーン 26"
    printf '%s\n' '/goal /ndf:development-workflow .ndf/handoff/milestone-26.md の続きから' | python3 handoff.py next milestone-26
    python3 handoff.py check milestone-26 --trim
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import deps  # noqa: E402

deps.require("md")
import handoff_doc  # noqa: E402
import repo  # noqa: E402
import step_result  # noqa: E402

TOOL = "handoff"
TEMPLATE = Path(__file__).resolve().parent / "data" / "handoff-template.md"
HANDOFF_DIR = Path(".ndf") / "handoff"
HISTORY_SUFFIX = "-history"
NAME_RE = re.compile(r"^(?:issue-[0-9]+|milestone-[0-9]+|sprint-[0-9A-Za-z][0-9A-Za-z_-]*)$")
COMMAND_SECTION = "次に実行するコマンド"
DEMOTED_SECTION = "前の会話の進み"
OPTIONAL_MARK = "<!-- 任意"
DEFAULT_MAX_LINES = 300

EXIT_FOUND_MANY = 4
EXIT_TOO_LONG = 5


class Failure(Exception):
    def __init__(self, summary: str, code: int = step_result.EXIT_UNREADABLE):
        super().__init__(summary)
        self.code = code


def emit_result(summary: str, items=None, metrics=None, code: int = 0) -> int:
    """結果の 1 行の JSON を出して終了コードを返す。0 以外は `stopped`（4・5 は各副命令の定めた否定の結果）。"""
    status = "ok" if code == 0 else "stopped"
    print(json.dumps(step_result.result(TOOL, status, summary, items, metrics), ensure_ascii=False))
    return code


# --- 名とパス -----------------------------------------------------------------


def handoff_name(name: str) -> str:
    if not NAME_RE.match(name) or name.endswith(HISTORY_SUFFIX):
        raise Failure(f"名が sprint-<名>・milestone-<番号>・issue-<番号> の形でない: {name}")
    return name


def name_of(arg: str) -> str:
    """名、または `.ndf/handoff/<名>.md` で終わるパスから名を取る。"""
    if "/" not in arg and not arg.endswith(".md"):
        return handoff_name(arg)
    p = Path(arg)
    if p.suffix != ".md" or p.parent.parts[-2:] != HANDOFF_DIR.parts:
        raise Failure(f"パスが .ndf/handoff/<名>.md の形でない: {arg}")
    return handoff_name(p.stem)


def handoff_dir(root) -> Path:
    main = repo.main_dir(root or ".")
    if main is None:
        raise Failure(f"git のリポジトリの中でない: {root or '.'}", step_result.EXIT_PRECONDITION)
    if not (main / ".ndf").is_dir():
        raise Failure(f"メインディレクトリに .ndf/ が無い（development-workflow の手順 0 が作る）: {main}", step_result.EXIT_PRECONDITION)
    return main / HANDOFF_DIR


def paths_of(root, name: str) -> tuple[Path, Path]:
    d = handoff_dir(root)
    return d / f"{name}.md", d / f"{name}{HISTORY_SUFFIX}.md"


def named_paths(a) -> tuple[str, Path, Path]:
    """副命令の引数の名を検証し、(名, 本体, 履歴) を返す。"""
    name = handoff_name(a.name)
    return (name, *paths_of(a.root, name))


def read_doc(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError as e:
        raise Failure(f"読めない: {p}（{e}）")


def write_doc(p: Path, text: str) -> None:
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    except OSError as e:
        raise Failure(f"書けない: {p}（{e}）")


# --- 副命令 -------------------------------------------------------------------


def cmd_path(a) -> int:
    name = name_of(a.target)
    body, history = paths_of(a.root, name)
    item = {"name": name, "path": str(body), "history": str(history), "exists": body.is_file()}
    if a.exists and not body.is_file():
        return emit_result(f"本体が無い: {body}", [item], code=step_result.EXIT_VIOLATION)
    return emit_result(f"{name}: {body}", [item])


def cmd_init(a) -> int:
    _, body, _ = named_paths(a)
    if body.is_file():
        return emit_result(f"既にある（変えない）: {body}", [{"path": str(body), "created": False}])
    write_doc(body, read_doc(TEMPLATE).replace("{title}", a.title))
    return emit_result(f"雛形から作った: {body}", [{"path": str(body), "created": True}])


def match_words(name: str) -> re.Pattern:
    """名ごとの一致の規則（設計の `find`）。"""
    kind, _, key = name.partition("-")
    if kind == "issue":
        return re.compile(rf"#{key}(?![0-9])")
    if kind == "milestone":
        return re.compile(rf"(?:マイルストーン\s*|milestone\s*|milestone-){key}(?![0-9])", re.I)
    k = re.escape(key)
    return re.compile(rf"sprint-{k}(?![0-9A-Za-z-])|(?<![0-9A-Za-z-]){k}(?![0-9A-Za-z-])")


def cmd_find(a) -> int:
    d = handoff_dir(a.root)
    names = sorted(p.stem for p in d.glob("*.md") if NAME_RE.match(p.stem) and not p.stem.endswith(HISTORY_SUFFIX)) if d.is_dir() else []
    hits = [str(d / f"{n}.md") for n in names if match_words(n).search(a.text)]
    items = [{"path": h} for h in hits]
    if len(hits) == 1:
        return emit_result(f"一致した: {hits[0]}", items)
    if not hits:
        return emit_result("一致する本体が無い", items, code=step_result.EXIT_VIOLATION)
    return emit_result(f"一致する本体が {len(hits)} 本ある（読まずに利用者へ聞く）", items, code=EXIT_FOUND_MANY)


def command_block(stdin_text: str) -> str:
    lines = stdin_text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        raise Failure("標準入力に再開コマンドが無い")
    body = "\n".join(lines)
    runs = [len(r) for r in re.findall(r"`+", body)]
    fence = "`" * max(3, max(runs, default=0) + 1)
    return f"{fence}ndf-next\n{body}\n{fence}\n"


def cmd_next(a) -> int:
    _, body, _ = named_paths(a)
    block = command_block(sys.stdin.read())
    text = read_doc(body)
    found = handoff_doc.find_section(text, COMMAND_SECTION)
    if found is None:
        return emit_result(f"見出しに「{COMMAND_SECTION}」を含む節が無い（文書を変えない）: {body}", code=step_result.EXIT_VIOLATION)
    tail = "\n" if found.body_end < len(text) else ""
    out = handoff_doc.replace_body(text, COMMAND_SECTION, "\n" + block + tail)
    if out != text:
        write_doc(body, out)
    return emit_result(f"節「{COMMAND_SECTION}」を置き換えた: {body}", [{"path": str(body), "block": block, "changed": out != text}])


def template_sections() -> list[tuple[str, bool]]:
    """雛形の `##` の節の (見出し, 必須か) を順に。本文に `<!-- 任意` がある節は任意。"""
    text = read_doc(TEMPLATE)
    out = []
    for title, _ in handoff_doc.level_headings(text, 2):
        found = handoff_doc.find_section(text, title)
        out.append((title, OPTIONAL_MARK not in text[found.body_start : found.body_end]))
    return out


def violations(text: str) -> list[dict]:
    tmpl = template_sections()
    seen: dict[int, int] = {}
    out = []
    last = -1
    for title, line in handoff_doc.level_headings(text, 2):
        idx = next((i for i, (t, _) in enumerate(tmpl) if title.startswith(t)), None)
        if idx is None:
            out.append({"kind": "unknown", "section": title, "line": line + 1})
            continue
        if idx in seen:
            out.append({"kind": "duplicate", "section": title, "line": line + 1})
            continue
        if idx < last:
            out.append({"kind": "order", "section": title, "line": line + 1})
        seen[idx] = line
        last = max(last, idx)
    out += [{"kind": "missing", "section": t} for i, (t, required) in enumerate(tmpl) if required and i not in seen]
    return out


def trim(text: str, history: Path, name: str) -> tuple[str, list[str]]:
    """「前の会話の進み」の節をすべて履歴の末尾へ移す。移した見出しを返す。"""
    moved, parts = [], []
    while (found := handoff_doc.find_section(text, DEMOTED_SECTION)) is not None:
        parts.append(text[found.head_start : found.body_end].rstrip("\n") + "\n")
        moved.append(handoff_doc.heading_text(found.head_line))
        text = text[: found.head_start] + text[found.body_end :]
    if parts:
        old = (
            read_doc(history)
            if history.is_file()
            else f"# 引継ぎの履歴: {name}\n\n本体は [{name}.md]({name}.md) にある。終わった項目を古い順に足す。\n"
        )
        write_doc(history, old.rstrip("\n") + "\n\n" + "\n".join(parts))
    return text, moved


def cmd_check(a) -> int:
    name, body, history = named_paths(a)
    text = read_doc(body)
    moved: list[str] = []
    if a.trim:
        new, moved = trim(text, history, name)
        if new != text:
            write_doc(body, new)
            text = new
    found = violations(text)
    lines = len(text.splitlines())
    item = {"path": str(body), "violations": found, "lines": lines, "limit": a.max_lines, "moved": moved}
    if found:
        return emit_result(f"節の形の違反が {len(found)} 件: {body}", [item], code=step_result.EXIT_VIOLATION)
    if lines > a.max_lines:
        return emit_result(f"本体が {lines} 行で上限 {a.max_lines} 行を {lines - a.max_lines} 行超える: {body}", [item], code=EXIT_TOO_LONG)
    return emit_result(f"節の形と行数（{lines}/{a.max_lines}）が通る: {body}", [item])


def cmd_remove(a) -> int:
    removed = []
    for p in named_paths(a)[1:]:
        if p.is_file():
            try:
                p.unlink()
            except OSError as e:
                raise Failure(f"消せない: {p}（{e}）")
            removed.append(str(p))
    return emit_result(f"{len(removed)} 本を消した", [{"path": p} for p in removed])


def build_parser() -> argparse.ArgumentParser:
    common = step_result.common_parser()
    ap = argparse.ArgumentParser(prog="handoff.py", description=__doc__.split("\n")[0], parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("path", parents=[common])
    s.add_argument("target")
    s.add_argument("--exists", action="store_true")
    s.set_defaults(func=cmd_path)
    s = sub.add_parser("init", parents=[common])
    s.add_argument("name")
    s.add_argument("--title", required=True)
    s.set_defaults(func=cmd_init)
    s = sub.add_parser("find", parents=[common])
    s.add_argument("--text", required=True)
    s.set_defaults(func=cmd_find)
    s = sub.add_parser("next", parents=[common])
    s.add_argument("name")
    s.set_defaults(func=cmd_next)
    s = sub.add_parser("check", parents=[common])
    s.add_argument("name")
    s.add_argument("--trim", action="store_true")
    s.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES)
    s.set_defaults(func=cmd_check)
    s = sub.add_parser("remove", parents=[common])
    s.add_argument("name")
    s.set_defaults(func=cmd_remove)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    a.root = getattr(a, "root", None)
    try:
        return a.func(a)
    except Failure as e:
        return emit_result(str(e), code=e.code)


if __name__ == "__main__":
    sys.exit(main())
