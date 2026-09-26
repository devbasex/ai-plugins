#!/usr/bin/env python3
"""手順書の bash が参照する変数の出所をチェックする（#518-1）。

**繰り返しの中で使う値を、繰り返しの外の 1 回だけが返す構造は表に出ない。** `init` から
順に実行すれば定義されるため、手元では再現しない。骨組みを抜粋して写した経路と、状態
ファイルから再開する経路で未定義になり、`unbound variable` で止まる。

チェックは 1 つで、**参照する変数がその行より前のコマンドで得られるか**を見る。出所は 3 つある。

| 出所 | 例 |
| --- | --- |
| その bash の中の代入 | `NAME=x` / `for a in ...` / `export X=1` |
| 副コマンドが返す値 | `rf_eval start-round "$ID"` が `emit` するキー |
| 骨組みの外から渡る値 | `--external` が宣言するもの（利用者が引数として与える） |

副コマンドが返すキーは、そのスクリプトの構文木から読む。`cmd_<名前>` の関数が呼ぶ
`emit(...)` のキーワードを集め、`_emit_init` のようなヘルパーを経由する呼び出しも
**1 階層だけ**たどる。

bash のブロックは `lib/md.py`（CommonMark の囲み）で、bash そのものは `lib/shparse.py`（tree-sitter-bash）の
構文木で読む（#1142 の D8）。構文木を書かれた順にたどり、代入・`for`・`read`・`eval` で得た名前を、それより
後ろの参照の出所にする。引用符で囲んだヒアドキュメントの本文とコメントは参照に数えない。

使い方:

    python3 scripts/check-skill-shell-vars.py                    # 既定の対象をすべて
    python3 scripts/check-skill-shell-vars.py --skill-dir <パス> # 1 本だけ
    python3 scripts/check-skill-shell-vars.py --show-sources RUNTIMES
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys
from typing import Iterable, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
from ndf_wrappers import require  # noqa: E402  根の lock で包みの依存を解決する（#1142 の決定 19）

require("md", "shparse")
import md  # noqa: E402
import shparse  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# 既定でチェックする Skill。**骨組みを `eval` で受け取る手順書だけを対象にする。**
# 変数を渡さない手順書へ広げると、外から渡る値の宣言だけが増える。
DEFAULT_SKILLS = ("plugins/ndf/skills/cross-refactoring",)

# 骨組みの外から渡る値。利用者が引数として与えるものと、この Skill の入口が決めるもの。
DEFAULT_EXTERNAL = (
    "PR", "SCOPE", "BASELINE", "HOST", "MAX_FIX", "CI_CHECK", "WORKFLOW_STEP", "MODEL_ARGS", "SYNC_COMMAND",
    "PLAN_FILE", "ROTATE_MODE", "ONLY", "FOCUS", "EXTRA_INSTRUCTIONS_FILE",
    "INITIAL_PR", "MAX_ROUNDS", "ROTATE_AFTER",
)

# シェルと環境が持つ値。参照されても出所を問わない。
SHELL_BUILTINS = {
    "HOME", "PWD", "OLDPWD", "PATH", "USER", "SHELL", "TMPDIR", "IFS",
    "RANDOM", "LINENO", "SECONDS", "BASH_SOURCE", "FUNCNAME", "PIPESTATUS",
    "CLAUDE_PLUGIN_ROOT", "NDF_WORKTREE_BASE", "CROSS_REVIEW_TMP_DIR",
    "CROSS_REFACTORING_TMP_DIR",
}

# `rf_eval <副コマンド>` / `eval "$(... <副コマンド> ...)"` の形で値を受け取るコマンドの副コマンドの語
_EVAL_COMMANDS = {"rf_eval", "eval"}
_SUBCOMMAND = re.compile(r"\b([a-z][a-z0-9-]{2,})\b")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# 既定値を持つ参照（`${X:-...}` ほか）の演算子。出所を問わない
_DEFAULTED = {":-", ":=", ":+", "-", "+"}


def bash_blocks(text: str) -> list[str]:
    """Markdown の bash のコードブロック（情報文字列が `bash` / `sh` の囲み）を順に返す。"""
    return [t.content.rstrip("\n") for t in md.md_tokens(text)
            if t.type == "fence" and t.info.strip() in ("bash", "sh")]


def emitted_keys(scripts_dir: pathlib.Path) -> dict[str, set[str]]:
    """副コマンドの名前ごとに、`emit` が返すキーの集合を返す。

    `cmd_start_round` は副コマンド `start-round` に対応する。ヘルパーを経由する
    呼び出しは **1 階層だけ**たどる（`cmd_init` → `_emit_init`）。
    """
    per_function: dict[str, set[str]] = {}
    calls: dict[str, set[str]] = {}
    for path in sorted(scripts_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            keys, called = set(), set()
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                name = _call_name(node.func)
                if name == "emit":
                    keys |= {kw.arg for kw in node.keywords if kw.arg}
                elif name:
                    called.add(name)
            per_function.setdefault(fn.name, set()).update(keys)
            calls.setdefault(fn.name, set()).update(called)

    result: dict[str, set[str]] = {}
    for name, keys in per_function.items():
        if not name.startswith("cmd_"):
            continue
        total = set(keys)
        for callee in calls.get(name, set()):        # ヘルパーは 1 階層だけ
            total |= per_function.get(callee, set())
        result[name[4:].replace("_", "-")] = total
    return result


def _call_name(node: ast.expr) -> Optional[str]:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _reference(node) -> Optional[str]:
    """`$X` / `${X}` の名前。既定値を持つ参照と特殊な変数（`$?`・`$1`）は None。"""
    if node.type == "simple_expansion":
        names = [c for c in node.children if c.type == "variable_name"]
        return shparse.node_text(names[0]) if names else None
    kids = node.children
    for k, c in enumerate(kids):
        if c.type == "subscript":
            c = next((d for d in c.children if d.type == "variable_name"), None)
            if c is None:
                return None
        if c.type == "variable_name":
            after = kids[k + 1].type if k + 1 < len(kids) else ""
            return None if after in _DEFAULTED else shparse.node_text(c)
    return None


def _command_name(node) -> str:
    head = node.child_by_field_name("name")
    return shparse.node_text(head) if head is not None else ""


class _Flow:
    """構文木を書かれた順にたどり、出所の無い参照を集める。"""

    def __init__(self, known: set[str], emits: dict[str, set[str]]):
        self.known = known
        self.emits = emits
        self.missing: list[tuple[str, int]] = []

    def walk(self, node) -> None:
        kind = node.type
        if kind == "comment":
            return
        if kind in ("simple_expansion", "expansion"):
            name = _reference(node)
            if name and name not in self.known:
                self.missing.append((name, node.start_point[0] + 1))
                self.known.add(name)      # 同じ名前を何度も並べない
            for c in node.children:       # `${X:-$Y}` の既定値の中の参照
                if c.type not in ("variable_name", "subscript"):
                    self.walk(c)
            return
        if kind == "variable_assignment":
            for c in node.children:
                if c.type != "variable_name":
                    self.walk(c)
            name = node.child_by_field_name("name")
            if name is not None:
                self.known.add(shparse.node_text(name))
            return
        if kind == "for_statement":
            var = node.child_by_field_name("variable")
            body = node.child_by_field_name("body")
            for c in node.children:
                if c != var and c != body:  # 節は取り出すたびに作り直されるため、同一性ではなく等しさで比べる
                    self.walk(c)
            if var is not None:
                self.known.add(shparse.node_text(var))
            if body is not None:
                self.walk(body)
            return
        for c in node.children:
            self.walk(c)
        if kind == "command":
            self._command_defines(node)

    def _command_defines(self, node) -> None:
        name = _command_name(node)
        if name == "read":
            words = [shparse.node_text(c) for c in node.children if c.type == "word"]
            self.known |= {w for w in words if _NAME.fullmatch(w)}
        elif name in _EVAL_COMMANDS:
            for sub in _SUBCOMMAND.findall(shparse.node_text(node)):
                self.known |= self.emits.get(sub, set())


def check_skill(skill_dir: pathlib.Path, external: set[str]) -> list[str]:
    """出所の無い参照を報告文の並びで返す。"""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return [f"{skill_dir}: SKILL.md がありません"]
    emits = emitted_keys(skill_dir / "scripts")
    # 報告のパスはリポジトリからの相対にする。**リポジトリの外を渡されることもある**
    # ため、収まらない場合はそのまま出す。
    try:
        shown = skill_md.relative_to(REPO_ROOT)
    except ValueError:
        shown = skill_md
    problems: list[str] = []
    for block in bash_blocks(skill_md.read_text(encoding="utf-8")):
        flow = _Flow(set(external) | SHELL_BUILTINS, emits)
        flow.walk(shparse.parse_bash(block))
        for name, lineno in flow.missing:
            if name.isdigit():
                continue
            problems.append(f"{shown}: ${name} の出所がありません（bash ブロックの {lineno} 行目）")
    return problems


def sources_of(skill_dir: pathlib.Path, name: str) -> list[str]:
    """その変数を返す副コマンドの名前。"""
    emits = emitted_keys(skill_dir / "scripts")
    return sorted(sub for sub, keys in emits.items() if name in keys)


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skill-dir", action="append", default=None,
                    help="チェックする Skill のディレクトリ（複数可）")
    ap.add_argument("--external", default=None,
                    help="骨組みの外から渡る値。コンマ区切り")
    ap.add_argument("--show-sources", default=None,
                    help="その変数を返す副コマンドを出して終わる")
    args = ap.parse_args(list(argv) if argv is not None else None)

    targets = [pathlib.Path(d) for d in (args.skill_dir or [])]
    if not targets:
        targets = [REPO_ROOT / d for d in DEFAULT_SKILLS]

    if args.show_sources:
        for skill in targets:
            for sub in sources_of(skill, args.show_sources):
                print(f"{args.show_sources}: {sub}")
        return 0

    external = set(DEFAULT_EXTERNAL)
    if args.external:
        external |= {w.strip() for w in args.external.split(",") if w.strip()}

    problems: list[str] = []
    for skill in targets:
        problems += check_skill(skill, external)

    if problems:
        for line in problems:
            print(line)
        print(f"\n出所の無い参照が {len(problems)} 件あります。")
        return 1
    print(f"Shell variable sources are satisfied ({len(targets)} skills)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
