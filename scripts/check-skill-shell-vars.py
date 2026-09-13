#!/usr/bin/env python3
"""手順書の bash が参照する変数の出所を検査する（#518-1）。

**繰り返しの中で使う値を、繰り返しの外の 1 回だけが返す構造は表に出ない。** `init` から
順に実行すれば定義されるため、手元では再現しない。骨組みを抜粋して写した経路と、状態
ファイルから再開する経路で未定義になり、`unbound variable` で止まる。

検査は 1 つで、**参照する変数がその行より前のコマンドで得られるか**を見る。出所は 3 つある。

| 出所 | 例 |
| --- | --- |
| その bash の中の代入 | `NAME=x` / `for a in ...` / `export X=1` |
| 副コマンドが返す値 | `rf_eval start-round "$ID"` が `emit` するキー |
| 骨組みの外から渡る値 | `--external` が宣言するもの（利用者が引数として与える） |

副コマンドが返すキーは、そのスクリプトの構文木から読む。`cmd_<名前>` の関数が呼ぶ
`emit(...)` のキーワードを集め、`_emit_init` のようなヘルパーを経由する呼び出しも
**1 段だけ**たどる。

**シェルの構文解析器は使わない。** 骨組みは代入・`for`・コマンド置換に限られており、
この範囲は後ろ向きの状態だけで読める（#201 で同じ判断をしている）。

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

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# 既定で検査する Skill。**骨組みを `eval` で受け取る手順書だけを対象にする。**
# 変数を渡さない手順書へ広げると、外から渡る値の宣言だけが増える。
DEFAULT_SKILLS = ("plugins/ndf/skills/cross-refactoring",)

# 骨組みの外から渡る値。利用者が引数として与えるものと、この Skill の入口が決めるもの。
DEFAULT_EXTERNAL = (
    "PR", "SCOPE", "BASELINE", "HOST", "MAX_TEST", "MAX_OUTER", "MAX_FIX",
    "MAX_ITEMS", "CI_CHECK", "WORKFLOW_STEP", "MODEL_ARGS", "SYNC_COMMAND",
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

_VAR_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)([^}]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
_ASSIGN = re.compile(r"^\s*(?:export\s+|local\s+|declare\s+)?([A-Za-z_][A-Za-z0-9_]*)=")
_FOR = re.compile(r"\bfor\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\b")
_READ = re.compile(r"\bread\b[^|;]*?\s([A-Za-z_][A-Za-z0-9_]*)\s*$")
# `rf_eval <副コマンド>` / `eval "$(... <副コマンド> ...)"` の形で値を受け取る行
_EVAL_CALL = re.compile(r"\b(?:rf_eval|eval)\b[^\n]*?\b([a-z][a-z0-9-]{2,})\b")
# 1 行の中の区切り。**引用符の中の区切りも割れるが、判定は「定義が早く効く」
# 側へ倒れるだけである。** 出所の無い参照を見落とす向きには働かない。
_SEPARATOR = re.compile(r"[;&|]+")


def bash_blocks(text: str) -> list[str]:
    """Markdown の bash のコードブロックを順に返す。"""
    blocks, current, inside = [], [], False
    for line in text.split("\n"):
        if line.startswith("```"):
            if inside:
                blocks.append("\n".join(current))
                current, inside = [], False
            elif line.strip() in ("```bash", "```sh"):
                inside = True
            continue
        if inside:
            current.append(line)
    return blocks


def emitted_keys(scripts_dir: pathlib.Path) -> dict[str, set[str]]:
    """副コマンドの名前ごとに、`emit` が返すキーの集合を返す。

    `cmd_start_round` は副コマンド `start-round` に対応する。ヘルパーを経由する
    呼び出しは **1 段だけ**たどる（`cmd_init` → `_emit_init`）。
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
        for callee in calls.get(name, set()):        # ヘルパーは 1 段だけ
            total |= per_function.get(callee, set())
        result[name[4:].replace("_", "-")] = total
    return result


def _call_name(node: ast.expr) -> Optional[str]:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def referenced(line: str) -> list[str]:
    """その行が参照する変数。既定値を持つ参照（`${X:-...}`）は除く。"""
    names = []
    for braced, modifier, plain in _VAR_REF.findall(line):
        if braced:
            if modifier.startswith((":-", ":=", ":+", "-", "+")):
                continue
            names.append(braced)
        elif plain:
            names.append(plain)
    return names


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
        known = set(external) | SHELL_BUILTINS
        for lineno, line in enumerate(block.split("\n"), 1):
            code = line.split("#", 1)[0]
            # **1 行に複数のコマンドが並ぶ。** `out=$(...); rc=$?` や
            # `for a in $X; do echo "$a"; done` は、同じ行の左で定義した値を
            # 右で読む。行をそのまま見ると、定義より先に参照を判定してしまう。
            for part in _SEPARATOR.split(code):
                for name in referenced(part):
                    if name in known or name.isdigit():
                        continue
                    problems.append(
                        f"{shown}: ${name} の出所がありません"
                        f"（bash ブロックの {lineno} 行目）"
                    )
                    known.add(name)      # 同じ名前を何度も並べない
                known |= set(_ASSIGN.findall(part))
                known |= set(_FOR.findall(part))
                known |= set(_READ.findall(part))
                for subcommand in _EVAL_CALL.findall(part):
                    known |= emits.get(subcommand, set())
    return problems


def sources_of(skill_dir: pathlib.Path, name: str) -> list[str]:
    """その変数を返す副コマンドの名前。"""
    emits = emitted_keys(skill_dir / "scripts")
    return sorted(sub for sub, keys in emits.items() if name in keys)


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skill-dir", action="append", default=None,
                    help="検査する Skill のディレクトリ（複数可）")
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
