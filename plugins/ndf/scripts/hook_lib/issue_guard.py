"""AI が直接打つ `gh issue create` を、起票の前に記載項目の定義で判定する PreToolUse の guard（#2737）。Claude Code の Bash だけ。

コマンドの位置で起動する `gh issue create`（`env`・`command`・`nohup` などの包みと変数の代入を挟んでよい）を構文木
（`lib/shparse.py`）から探し、本文とラベルを読んで `lib/issue_items.py` の `judge_body(本文, ラベル, {"ai": True})` に渡す。

| 本文の渡し方 | 読み方 |
| --- | --- |
| `--body` / `-b` の文字列 | 引用を外した字面（展開を含む語は読めない形） |
| `--body-file` / `-F` のファイル | コマンドの実行ディレクトリからの相対で読む（`-` の標準入力は読めない形） |
| `--body "$(cat <<'EOF' … EOF)"` | ヒアドキュメントの本文（区切りを引用しないときは展開を含まない本文だけ） |

ラベルは `--label` / `-l`（繰り返しとカンマ区切り）。`-R` / `--repo` が今のリポジトリ（`git remote get-url origin`）と
違うときは判定しない。欠けがある・本文を上の形で読めない（標準入力・変数・`--web`）ときは拒否し、理由に欠けた項目名・
定義のファイル・正しい手段を書く。GitHub は呼ばない。構文木を読み切れない・定義を読めないときは判定せずに通す。
`bash -c` の中や `gh api` での起票は見ない（起票の後の検査 `issue-items.py audit` が拾う）。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import issue_items
import limits
import shparse as sp

from . import payload as pl
from .token_guard import deny

SCRIPTS = Path(__file__).resolve().parents[1]
WRAPPERS = frozenset({"env", "command", "builtin", "nohup", "exec", "time"})
ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
REPO_IN_URL = re.compile(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$")
BODY_OPTS = ("--body", "-b")
FILE_OPTS = ("--body-file", "-F")
LABEL_OPTS = ("--label", "-l")
REPO_OPTS = ("--repo", "-R")
WEB_OPTS = ("--web", "-w")
CONTEXT = {"ai": True}


def _call_words(n: sp.Node) -> list[sp.Node]:
    return [c for c in n.children if sp.field_of(n, c) in ("name", "argument")]


def _start(words: list[str]) -> int:
    """包みのコマンドと変数の代入を飛ばした、本体の語の位置。"""
    i = 0
    while i < len(words) and (words[i] in WRAPPERS or ASSIGN.match(words[i]) or (i and words[i].startswith("-"))):
        i += 1
    return i


def creates(root: sp.Node) -> list[sp.Node]:
    """コマンドの位置で起動する `gh issue create` の `command` のノード。ヒアドキュメントの本文は見ない。"""
    out, stack = [], [root]
    while stack:
        n = stack.pop()
        if n.type == "heredoc_body":
            continue
        if n.type == "command":
            words = [sp.unquote(w) for w in _call_words(n)]
            i = _start(words)
            if words[i : i + 3] == ["gh", "issue", "create"]:
                out.append(n)
        stack.extend(c for c in n.children if c.child_count)
    return out


def _lone_substitution(node: sp.Node) -> sp.Node | None:
    """語がコマンド置換 1 つだけならその置換のノード。違えば `None`。"""
    subs = [node] if node.type == "command_substitution" else [c for c in node.children if c.type == "command_substitution"]
    if len(subs) != 1 or (node.type == "string" and any(c.type not in ('"', "command_substitution") for c in node.children)):
        return None
    return subs[0]


def _cat_heredoc(sub: sp.Node) -> tuple[sp.Node, sp.Node] | None:
    """置換の中身が `cat <<区切り` 1 つなら `(ヒアドキュメントの redirect, 本文)` のノード。違えば `None`。"""
    inner = [c for c in sub.children if c.is_named]
    if len(inner) != 1 or inner[0].type != "redirected_statement":
        return None
    body = inner[0].child_by_field_name("body")
    redirects = [c for c in inner[0].children if c.type == "heredoc_redirect"]
    if body is None or len(redirects) != 1 or [sp.unquote(w) for w in _call_words(body)] != ["cat"]:
        return None
    r = redirects[0]
    text = next((c for c in r.children if c.type == "heredoc_body"), None)
    if text is None:
        return None
    return r, text


def _heredoc_text(node: sp.Node) -> str | None:
    """`"$(cat <<'EOF' … EOF)"` の形ならヒアドキュメントの本文。違えば `None`。"""
    sub = _lone_substitution(node)
    found = _cat_heredoc(sub) if sub is not None else None
    if found is None:
        return None
    r, text = found
    if not sp.heredoc_quoted(r) and (text.child_count or any(ch in sp.node_text(text) for ch in "$`")):
        return None
    return sp.node_text(text)


def _arg_value(node: sp.Node) -> str | None:
    """引数の値。展開を含む語は読めない形（`None`）。"""
    here = _heredoc_text(node)
    if here is not None:
        return here
    if node.type in ("command_substitution", "simple_expansion", "expansion") or any(
        c.type in ("command_substitution", "simple_expansion", "expansion", "process_substitution") for c in node.children
    ):
        return None
    return sp.unquote(node)


def create_args(nodes: list[sp.Node], cwd: str) -> tuple[str | None, list[str], str | None]:
    """`gh issue create` の引数から `(本文, ラベル, --repo)`。本文を読めない形なら本文は `None`。"""
    acc: dict = {"body": None, "labels": [], "repo": None}
    unreadable = False
    i = 0
    while i < len(nodes):
        word = sp.unquote(nodes[i])
        name, eq, inline = word.partition("=") if word.startswith("--") else (word, "", "")
        unreadable |= name in WEB_OPTS
        value, i = _opt_value(nodes, i, name, eq, inline)
        unreadable |= _apply_arg(acc, name, value, cwd)
        i += 1
    return (None if unreadable else acc["body"]), acc["labels"], acc["repo"]


def _opt_value(nodes: list[sp.Node], i: int, name: str, eq: str, inline: str) -> tuple[str | None, int]:
    """オプションの値と、値を読んだ位置。`=` が無い値付きオプションは次の語から読む。"""
    if name in BODY_OPTS + FILE_OPTS + LABEL_OPTS + REPO_OPTS and not eq:
        i += 1
        return (_arg_value(nodes[i]) if i < len(nodes) else None), i
    return inline, i


def _apply_arg(acc: dict, name: str, value: str | None, cwd: str) -> bool:
    """オプションを `acc`（body・labels・repo）へ反映する。本文を読めなかったら真。"""
    if name in BODY_OPTS:
        acc["body"] = value
        return value is None
    if name in FILE_OPTS:
        acc["body"] = _read_file(value, cwd)
        return acc["body"] is None
    if name in LABEL_OPTS and value:
        acc["labels"] += [x.strip() for x in value.split(",") if x.strip()]
    elif name in REPO_OPTS:
        acc["repo"] = value
    return False


def _read_file(value: str | None, cwd: str) -> str | None:
    if value in (None, "", "-"):
        return None
    p = Path(value) if Path(value).is_absolute() else Path(cwd) / value
    try:
        return p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def origin_repo(cwd: str) -> str | None:
    try:
        p = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"], capture_output=True, text=True, timeout=limits.seconds("hook.git-remote")
        )
    except (OSError, subprocess.SubprocessError):
        return None
    m = REPO_IN_URL.search(p.stdout.strip()) if p.returncode == 0 else None
    return m.group(1) if m else None


def _other_repo(repo: str | None, cwd: str) -> bool:
    if not repo:
        return False
    here = origin_repo(cwd)
    want = repo.split("github.com/")[-1].removesuffix(".git").strip("/")
    return here is None or here.lower() != want.lower()


def _means(definition: issue_items.Definition) -> str:
    labels = "・".join(f"`{lab}`（{kind}）" for lab, kind in definition.kind_labels().items())
    return (
        f"範囲外の課題は python3 {SCRIPTS}/issue-file.py create で起票する（out-of-scope の手順）。それ以外は本文をファイルに書き、"
        f"gh issue create --label <種類のラベル> --body-file <ファイル> で打つ。種類のラベルは {labels} のどれか 1 つ"
    )


def _unreadable_text(definition: issue_items.Definition) -> str:
    return (
        "gh issue create の本文を読めない形で渡している（標準入力・変数・--web）。起票の前に本文が記載項目の定義を満たすかを"
        f"確かめられないため止めた。定義: {definition.path}。{_means(definition)}"
    )


def _gap_text(gaps, definition: issue_items.Definition) -> str:
    return (
        f"gh issue create の本文が記載項目の定義を満たさない: {issue_items.describe_gaps(gaps)}。Issue は作られていない。"
        f"欠けた項目を項目名の見出し（## <項目名>）で書き足して打ち直す。定義: {definition.path}。{_means(definition)}"
    )


def issue_decision(ev: pl.Event) -> dict | None:
    cmd = ev.command_text()
    if "gh" not in cmd or "create" not in cmd or "issue" not in cmd:
        return None
    root = sp.parse_bash(cmd)
    if sp.has_unreadable_error(root):
        return None
    found = creates(root)
    if not found:
        return None
    cwd = ev.command_cwd() or ev.cwd
    try:
        definition = issue_items.load_definition(cwd)
    except issue_items.DefinitionError:
        return None
    for node in found:
        words = _call_words(node)
        start = _start([sp.unquote(w) for w in words]) + 3
        body, labels, repo = create_args(words[start:], cwd)
        if _other_repo(repo, cwd):
            continue
        if body is None:
            return deny(_unreadable_text(definition))
        _kind, gaps = issue_items.judge_body(body, labels, CONTEXT, definition)
        if gaps:
            return deny(_gap_text(gaps, definition))
    return None
