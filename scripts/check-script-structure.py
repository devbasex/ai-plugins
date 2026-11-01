#!/usr/bin/env python3
"""NDF のスクリプトの構造チェック（#1142 の不変条件 I4・I5・I13・I14・I15・I16 と決定 23）。

見るのは `plugins/ndf/` の下の `.py` と `.sh` のうち、テストを除くもの（`tests/`・`test/` の下と
`test_` で始まるファイル）。git の作業ツリーでは git が追跡するファイルだけを見る。

- `lines`: 500 行を超えるファイル（I5・決定 18）。例外リストに載るファイルは、載せた行数を超えたら落ちる
- `same-body`: 本体の同じ最上位の関数が 2 つ以上のファイルにある（I4）。Python は docstring・型注釈・
  関数名を除いた構文木で、シェルは空行とコメントの行を除き前後の空白を落とした行で比べる
- `same-name`: 同じ名前で本体の違う最上位の関数が 2 つ以上のファイルにある（I4）。Python とシェルは
  別々に数える

- `wrapped`: 汎用の処理の包み（`lib/` の包み。決定 19）が受け持つ標準ライブラリの部品を、包みの外の Python の
  モジュールが使う（I14）。部品と持ち主は `WRAPPED` の表で、`fcntl`・`pty`・`termios`・`urllib.request`・`dbos`
  （耐久の記録の包み `lib/durable.py`。I16）の import、
  `/proc/` の読み取り（docstring を除く文字列）、囲み（```` ``` ```` / `~~~`）を追う正規表現と `startswith` を見る。
  例外リストの `name` は部品の名前（`fcntl` など）
- `hook-deps`: hook の経路が `deps.require()` を呼ぶ（I13・決定 20）。hook の経路は、hook のエントリポイント
  （`HOOK_ENTRIES`）から import でたどれる `scripts/` の中のモジュールと、`plugins/*/hooks/*.json` の command である。
  モジュールは `require` を呼んだら（`deps.require(…)`・`from deps import require`）落ち、command は `uv run` を
  挟んだら落ちる（hook は SessionStart が用意した環境の python を直に起動する）。例外リストの `name` は
  `deps.require` か `uv run`
- `durable-boundary`: プランの実行・投稿キュー・収束ループの置き場（`DURABLE_BOUNDARY`）に、ステップの遷移の
  ループ・再開の位置の読み書き・子プロセスの数え上げによる同時の本数の制御が残る（I15・C-1）。見るのは
  `Engine.run` と 2 本の drive の `Drive.run`（cross-refactoring は `Drive.phases`・`Drive.final_gate` も）の
  `for` / `while` の文、`RunState.record` が `state.json` へ書く辞書の `log`・`llm`・`project_mvv` 以外の鍵、
  `supervise_lib/queue.py` の `Popen`・`.poll()`・関数 `run_batch`、`post_queue.Queue` の `os.open`・`glob` の
  呼び出し（`_import_legacy` を除く）、drive の `save_ds`・`ds_path`。あわせて `state.py` を除く置き場が
  `durable.workflow` の関数を持つか、それを持つモジュールを import することを見る。置き場を 1 つも持たない木は見ない
- `require-groups`: エントリポイント（最上位に `if __name__ == "__main__":` の文を持つ `.py`。hook の経路と
  `lib/deps.py` を除く）から import をたどって届く外部パッケージのグループを、その import を覆う `deps.require(…)`
  が並べていない（決定 17・23）。import は関数の中のものも含めて、エントリポイントの置き場と `scripts/`・
  `scripts/lib/` のモジュールへ推移的にたどり、届いた外部パッケージを `lib/deps.py` の `GROUPS` でグループへ引く。
  呼び出しの流れは追わない。`require` が覆うのは、モジュールの最上位で呼べばそれより後の最上位の文とすべての
  関数の本体、関数の中で呼べばその関数のそれより後の文（入れ子のブロックで呼べばそのブロックの中）だけ。
  例外は `main` の本体の最上位で呼ぶ `require` で、そのモジュールをエントリポイントとして見るときだけ、`main` の
  それより後の文と、関数の本体で遅れて読む依存（最上位で import したモジュールの関数の本体を含む）を覆う。
  任意の依存は違反にしない: `except ImportError` / `ModuleNotFoundError`（組の中を含む）を受ける `try` の本体と、
  `find_spec("<名前>")` / `deps.importable("<グループ>")` の確かめが成り立つときだけ通る枝（真で入る `if` の本体と、
  偽で入り `return` / `raise` で終わる `if` の後の同じブロックの文）。互いに import し合うモジュールは、
  たどっている途中のものを空として扱う。例外リストの `name` はグループの名前

副命令のハンドラー（`cmd_*`）・`main`・`build_parser`・`_build_parser`・シェルの `usage` は規則で外す。

例外リストの置き場（既定は `scripts/script-structure-allow/`）は 1 項目 1 ファイルで、各ファイルは
`{"path", "name", "kind", "reason"}` を持つ。ファイル名は `allow_file_name()` が path・name・kind から
決める（例 `plugins__ndf__scripts__lib__deps.py--find_uv--same-body.json`）。並列の計画が別の項目を
消す・足しても同じファイルを触らないため、マージで衝突しない。`lines` の項目は `name` を空にし、
載せた時点の行数を `lines` に持つ（ラチェット）。違反に当たらない項目は `unused-allow` として落とす。
直した移行ステップは、同じ PR でその項目のファイルを消す。

    python3 scripts/check-script-structure.py [--root <リポジトリ>] [--allow <ディレクトリ>] [<ファイル>...]

ファイル（根からの相対パスか根の下の絶対パス。無いファイルやディレクトリも可）を渡すと、検査は木全体で行い、
`items` と合否を指定に関わる違反だけで決める（#1668。項目の範囲テストの静的解析の suite が使う）。関わるのは、
違反の `path` が指定にあるとき、`same-name` / `same-body` では同じ名前・同じ本体を持つほかのファイルのどれかが
指定にあるとき、指定に例外リストの置き場のファイルがあればその行に当たる違反と `unused-allow`、指定に検査の
スクリプト自身があればすべての違反である。このとき `metrics` に `targets`（指定の数）と `outside`（指定に
関わらず外した違反の数）を足す。

最後に結果 JSON を 1 行出す（`plugins/ndf/scripts/lib/README.md` の形）。終了コードは 0 が違反なし、
1 が違反あり、2 が例外リストの読めない・形の誤り（指定に関わらず 2）。
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from allowlist import AllowError, allow_file_name, under  # noqa: E402
from allowlist import load_allow as _load_rows  # noqa: E402
from py_imports import IMPORT_ROOTS  # noqa: E402
from py_imports import imported as _imported  # noqa: E402
from py_imports import module_file as _module_file  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SCAN = "plugins/ndf"
SELF = "scripts/check-script-structure.py"
SHARED_LIB = "scripts/lib"
MAX_LINES = 500
KINDS = ("lines", "same-body", "same-name", "wrapped", "hook-deps", "durable-boundary", "require-groups")
LIB = "plugins/ndf/scripts/"
# I14: 部品 → 使ってよい包み（決定 19。擬似端末は relay_lib/terminal.py が包みを兼ねる）
WRAPPED = {
    "fcntl": (LIB + "lib/locks.py",),
    "pty": (LIB + "relay_lib/terminal.py",),
    "termios": (LIB + "relay_lib/terminal.py",),
    "urllib.request": (LIB + "lib/notify.py",),
    "proc-fs": (LIB + "lib/procs.py",),
    "fence-regex": (LIB + "lib/md.py",),
    "dbos": (LIB + "lib/durable.py",),
}
# I14 のうち import で見る部品（I16 の dbos を含む）
IMPORT_PARTS = ("fcntl", "pty", "termios", "urllib.request", "dbos")
# I13: hook のエントリポイント（決定 20）。ここから import でたどれるモジュールは deps を import しない
HOOK_ENTRIES = (LIB + "hook.py",)
RE_FUNCS = {"compile", "match", "search", "fullmatch", "finditer", "findall", "sub", "subn", "split"}
FENCE_HINT = re.compile(r"```|~~~|`\{3|~\{3|\[`~\]|\[~`\]")
RULE_NAMES = {"main", "build_parser", "_build_parser"}
SHELL_RULE_NAMES = {"usage"}
SH_FUNC_RE = re.compile(r"^(\s*)(?:function\s+([A-Za-z_][\w:.-]*)\s*(?:\(\))?|([A-Za-z_][\w:.-]*)\s*\(\))\s*(\{.*)?$")


UsageError = AllowError


def is_test(rel: Path) -> bool:
    return any(x in ("tests", "test") for x in rel.parts) or rel.name.startswith("test_")


def list_files(root: Path) -> list[str]:
    """走査の対象を、root からの相対パス（`/` 区切り）で返す。"""
    p = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", SCAN], capture_output=True, text=True)
    if p.returncode == 0 and p.stdout:
        rels = [r for r in p.stdout.split("\0") if r]
    else:
        base = root / SCAN
        rels = [str(f.relative_to(root)) for f in base.rglob("*") if f.is_file()] if base.is_dir() else []
    out = []
    for r in rels:
        rel = Path(r)
        if rel.suffix not in (".py", ".sh") or ".worktrees" in rel.parts or is_test(rel):
            continue
        if (root / rel).is_file():
            out.append(rel.as_posix())
    return sorted(out)


def excluded(name: str, lang: str) -> bool:
    return name.startswith("cmd_") or name in RULE_NAMES or (lang == "sh" and name in SHELL_RULE_NAMES)


def _strip_annotations(fn: ast.AST) -> None:
    for node in ast.walk(fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node.returns = None
            a = node.args
            for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
                if arg is not None:
                    arg.annotation = None
        elif isinstance(node, ast.AnnAssign):
            node.annotation = ast.Constant(value=None)


def py_body_key(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """docstring・型注釈・関数名を除いた本体の鍵。"""
    fn = copy.deepcopy(fn)
    fn.name = ""
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:] or [ast.Pass()]
    fn.body = body
    _strip_annotations(fn)
    return hashlib.sha1(ast.dump(fn).encode()).hexdigest()


def py_functions(text: str) -> list[tuple[str, str]] | None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    return [(n.name, py_body_key(n)) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                ids.add(id(first.value))
    return ids


def _strings(node: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def wrapped_parts(text: str) -> dict[str, str]:
    """I14: 包みが受け持つ部品の使用を `{部品: 最初の行と形}` で返す（読めない Python は空）。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    found: dict[str, str] = {}

    def hit(part: str, node: ast.AST, how: str) -> None:
        found.setdefault(part, f"{node.lineno} 行: {how}")

    docs = _docstring_ids(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                for part in IMPORT_PARTS:
                    if a.name == part or a.name.startswith(part + "."):
                        hit(part, node, f"import {a.name}")
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = {a.name for a in node.names}
            for part in IMPORT_PARTS:
                if (
                    node.module == part
                    or node.module.startswith(part + ".")
                    or (part == "urllib.request" and node.module == "urllib" and "request" in names)
                ):
                    hit(part, node, f"from {node.module} import")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs and "/proc/" in node.value:
            hit("proc-fs", node, repr(node.value[:40]))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            f = node.func
            regex = f.attr in RE_FUNCS and isinstance(f.value, ast.Name) and f.value.id == "re"
            if (regex or f.attr in ("startswith", "endswith")) and any(FENCE_HINT.search(s) for a in node.args for s in _strings(a)):
                hit("fence-regex", node, f"{'re.' if regex else '.'}{f.attr}")
    return found


def _norm_sh(lines: list[str]) -> str:
    kept = [ln.strip() for ln in lines]
    kept = [ln for ln in kept if ln and not ln.startswith("#")]
    return hashlib.sha1("\n".join(kept).encode()).hexdigest()


def sh_functions(text: str) -> list[tuple[str, str]]:
    """シェルの関数の名前と本体の鍵。閉じ括弧は定義の行と同じ字下げの `}` とする。"""
    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        m = SH_FUNC_RE.match(lines[i])
        if not m:
            i += 1
            continue
        indent, name, rest = m.group(1), m.group(2) or m.group(3), (m.group(4) or "").strip()
        if rest.startswith("{") and rest.rstrip(";").endswith("}") and len(rest) > 1:
            out.append((name, _norm_sh([rest[1:-1].strip().rstrip(";").strip()])))
            i += 1
            continue
        start = i + 1
        if not rest:  # `{` が次の行にある形
            if start < len(lines) and lines[start].strip() == "{":
                start += 1
            else:
                i += 1
                continue
        end = next((j for j in range(start, len(lines)) if lines[j].rstrip() in (indent + "}", indent + "};")), None)
        if end is None:
            i += 1
            continue
        out.append((name, _norm_sh(lines[start:end])))
        i = end + 1
    return out


def _check_lines(f: Path, r: dict) -> None:
    if "lines" in r and (isinstance(r["lines"], bool) or not isinstance(r["lines"], int) or r["lines"] <= MAX_LINES):
        raise UsageError(f"例外リストの {f.name} の lines は {MAX_LINES} を超える整数: {r['lines']}")


def load_allow(path: Path) -> list[dict]:
    """置き場（ディレクトリ）の `*.json` を 1 ファイル 1 項目として読む（`lines` は載せた時点の行数を持つ）。"""
    return _load_rows(path, KINDS, lambda r: {"lines"} if r.get("kind") == "lines" else set(), _check_lines)


def _scan_file(root: Path, rel: str, defs: dict[str, list[tuple[str, str, str]]], violations: list[dict]) -> bool:
    """1 ファイルの行数と包みを検査し、関数を defs へ集める。関数を読めなければ False を返す。"""
    text = (root / rel).read_text(errors="ignore")
    n = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    if n > MAX_LINES:
        violations.append({"kind": "lines", "path": rel, "function": "", "detail": f"{n} 行", "lines": n})
    lang = "py" if rel.endswith(".py") else "sh"
    if lang == "py":
        for part, where in sorted(wrapped_parts(text).items()):
            if rel not in WRAPPED[part]:
                violations.append(
                    {
                        "kind": "wrapped",
                        "path": rel,
                        "function": part,
                        "detail": f"包み {' / '.join(WRAPPED[part])} が受け持つ部品を使う（{where}）",
                    }
                )
    found = py_functions(text) if lang == "py" else sh_functions(text)
    if found is None:
        return False
    for name, key in found:
        if not excluded(name, lang):
            defs[lang].append((rel, name, key))
    return True


def _duplicate_violations(defs: dict[str, list[tuple[str, str, str]]]) -> list[dict]:
    violations: list[dict] = []
    for lang, items in defs.items():
        by_key, by_name = defaultdict(set), defaultdict(dict)
        for path, name, key in items:
            by_key[(lang, key)].add(path)
            by_name[name].setdefault(path, set()).add(key)
        seen = set()
        for path, name, key in items:
            others = by_key[(lang, key)] - {path}
            if others and (path, name, "same-body") not in seen:
                seen.add((path, name, "same-body"))
                violations.append(
                    {
                        "kind": "same-body",
                        "path": path,
                        "function": name,
                        "detail": "本体が同じ: " + ", ".join(sorted(others)),
                        "others": sorted(others),
                    }
                )
        for name, per_path in by_name.items():
            if len(per_path) < 2:
                continue
            for path, keys in per_path.items():
                diff = sorted(p for p, ks in per_path.items() if p != path and ks != keys)
                if diff:
                    violations.append(
                        {
                            "kind": "same-name",
                            "path": path,
                            "function": name,
                            "detail": "同じ名前で本体が違う: " + ", ".join(diff),
                            "others": diff,
                        }
                    )
    return violations


def scan(root: Path) -> tuple[list[dict], dict]:
    files = list_files(root)
    violations: list[dict] = []
    defs: dict[str, list[tuple[str, str, str]]] = {"py": [], "sh": []}  # lang -> [(path, name, key)]
    unparsed = sum(not _scan_file(root, rel, defs, violations) for rel in files)
    violations += _duplicate_violations(defs)
    violations += hook_deps(root)
    violations += durable_boundary(root)
    violations += require_groups(root)
    metrics = {"files": len(files), "functions": sum(len(v) for v in defs.values()), "unparsed": unparsed}
    return violations, metrics


def _calls_require(tree: ast.AST) -> bool:
    """`deps.require(…)` を呼ぶか、`from deps import require` するか。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "deps" and any(a.name == "require" for a in node.names):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "require"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "deps"
        ):
            return True
    return False


def hook_deps(root: Path) -> list[dict]:
    """I13: hook の経路のモジュールと hook の command が、deps と uv run を使わないか。"""
    out: list[dict] = []
    todo, seen = [e for e in HOOK_ENTRIES if (root / e).is_file()], set()
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        seen.add(rel)
        text = (root / rel).read_text(errors="ignore")
        names = _imported(rel, text)
        try:
            calls = _calls_require(ast.parse(text))
        except SyntaxError:
            calls = False
        if calls:
            out.append(
                {
                    "kind": "hook-deps",
                    "path": rel,
                    "function": "deps.require",
                    "detail": "hook の経路のモジュールが deps.require() を呼ぶ（hook は用意済みの環境の python で動く）",
                }
            )
        todo += [f for f in (_module_file(root, n) for n in names) if f and f not in seen]
    for f in sorted(root.glob("plugins/*/hooks/*.json")):
        rel = f.relative_to(root).as_posix()
        if "uv run" in f.read_text(errors="ignore"):
            out.append(
                {
                    "kind": "hook-deps",
                    "path": rel,
                    "function": "uv run",
                    "detail": "hook の command が uv run を挟む（用意済みの環境の python を直に起動する）",
                }
            )
    return out


def _dep_groups(root: Path) -> dict[str, list[str]]:
    """`lib/deps.py` の `GROUPS`（グループ → import の名前）。無ければ空。"""
    f = root / LIB / "lib" / "deps.py"
    try:
        tree = ast.parse(f.read_text(errors="ignore"))
    except (OSError, SyntaxError):
        return {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "GROUPS" for t in node.targets):
            try:
                return ast.literal_eval(node.value)
            except ValueError:
                return {}
    return {}


_IMPORT_ERRORS = ("ImportError", "ModuleNotFoundError")


def _guards_import(t: ast.Try) -> bool:
    """`except ImportError` / `ModuleNotFoundError`（組の中を含む）を受ける `try` か。"""
    for h in t.handlers:
        types = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
        if any(isinstance(x, ast.Name) and x.id in _IMPORT_ERRORS for x in types):
            return True
    return False


def _require_call(stmt: ast.stmt) -> set[str] | None:
    """文が `deps.require(…)` / `require(…)` の呼び出しなら、並べたグループ。そうでなければ None。"""
    call = stmt.value if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) else None
    if call is None:
        return None
    f = call.func
    if (isinstance(f, ast.Attribute) and f.attr == "require" and isinstance(f.value, ast.Name) and f.value.id == "deps") or (
        isinstance(f, ast.Name) and f.id == "require"
    ):
        return {a.value for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str)}
    return None


def _import_names(n: ast.Import | ast.ImportFrom) -> list[str]:
    """import の文が読む名前（相対の import は `.` から始まる）。"""
    if isinstance(n, ast.Import):
        return [a.name for a in n.names]
    if n.level:
        mod = f".{n.module}" if n.module else ""
        return ([mod] if mod else []) + [f"{mod}.{a.name}" if mod else "." + a.name for a in n.names]
    return ([n.module] if n.module else []) + [f"{n.module}.{a.name}" for a in n.names]


def _child_blocks(n: ast.stmt) -> list[list[ast.stmt]]:
    """複文の子の文の並び（import を受ける `try` の本体は除く）。"""
    if isinstance(n, ast.Try):
        return ([] if _guards_import(n) else [n.body]) + [h.body for h in n.handlers] + [n.orelse, n.finalbody]
    blocks: list[list[ast.stmt]] = []
    for field in ("body", "orelse", "finalbody", "handlers", "cases"):
        sub = getattr(n, field, None)
        if isinstance(sub, list):
            blocks += [c.body for c in sub] if field in ("handlers", "cases") else [sub]
    return blocks


def _add_group(side: dict, group: str, where: str, owners: frozenset, covered: set[str]) -> None:
    if not owners & covered:
        side.setdefault(group, (where, owners))


class _DepScan:
    """I1〜I4: モジュールごとの未宣言のグループ（読み込みの時に届く側・遅れて読む側）を求めて覚える。"""

    def __init__(self, root: Path, groups: dict[str, list[str]]):
        self.root, self.groups = root, groups
        self.memo: dict[tuple, tuple[dict, dict]] = {}
        self.trees: dict[str, ast.Module | None] = {}

    def owners(self, name: str) -> list[str]:
        return [g for g, mods in self.groups.items() if any(name == m or name.startswith(m + ".") for m in mods)]

    def tree(self, rel: str) -> ast.Module | None:
        if rel not in self.trees:
            try:
                self.trees[rel] = ast.parse((self.root / rel).read_text(errors="ignore"))
            except (OSError, SyntaxError):
                self.trees[rel] = None
        return self.trees[rel]

    def _checked(self, e: ast.expr, truthy: bool) -> set[str]:
        """I2: 条件 e が真（truthy）/ 偽のときに import できると確かめたグループ。"""
        if isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.Not):
            return self._checked(e.operand, not truthy)
        if isinstance(e, ast.BoolOp):
            if isinstance(e.op, ast.And) == truthy:
                return set().union(*(self._checked(v, truthy) for v in e.values))
            return set()
        if isinstance(e, ast.Compare) and len(e.ops) == 1 and isinstance(e.comparators[0], ast.Constant):
            if e.comparators[0].value is None and isinstance(e.ops[0], (ast.Is, ast.IsNot)):
                return self._checked(e.left, truthy == isinstance(e.ops[0], ast.IsNot))
            return set()
        if not truthy or not isinstance(e, ast.Call) or len(e.args) != 1:
            return set()
        arg, f = e.args[0], e.func
        if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
            return set()
        fname = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
        if fname == "find_spec":
            return set(self.owners(arg.value))
        if fname == "importable":
            return {arg.value}
        return set()

    def _resolve(self, rel: str, bases: tuple[str, ...], name: str) -> str | None:
        if name.startswith("."):
            here = Path(rel).parent.as_posix()
            cands = [f"{here}/{name[1:].replace('.', '/')}{s}" for s in (".py", "/__init__.py")]
        else:
            cands = [b + name.replace(".", "/") + s for b in bases for s in (".py", "/__init__.py")]
        return next((c for c in cands if (self.root / c).is_file()), None)

    def module(self, rel: str, bases: tuple[str, ...], entry: bool = False) -> tuple[dict, dict]:
        """(読み込みの時に届く未宣言のグループ, 遅れて読む未宣言のグループ)。値は (届いた名前（書いたモジュール）, 持つグループ)。"""
        key = (rel, bases, entry)
        if key in self.memo:
            return self.memo[key]
        self.memo[key] = ({}, {})  # たどっている途中は空として扱う
        load: dict[str, tuple[str, frozenset]] = {}
        late: dict[str, tuple[str, frozenset]] = {}
        tree = self.tree(rel)
        if tree is not None:
            top = set().union(*(_require_call(s) or set() for s in tree.body))
            main = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
            in_main = set().union(*(_require_call(s) or set() for s in main.body)) if entry and main else set()

            def walk(stmts: list[ast.stmt], covered: set[str], lazy: bool, fn: ast.AST | None) -> None:
                covered = set(covered)
                for n in stmts:
                    listed = _require_call(n)
                    if listed is not None:
                        covered |= listed
                    elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        base = top if fn is None else covered
                        walk(n.body, base | (in_main if n is not main else set()), True, n)
                    elif isinstance(n, (ast.Import, ast.ImportFrom)):
                        self._visit_import(rel, bases, n, late if lazy else load, late, covered, in_main, fn)
                    elif isinstance(n, ast.If):
                        walk(n.body, covered | self._checked(n.test, True), lazy, fn)
                        walk(n.orelse, covered | self._checked(n.test, False), lazy, fn)
                        if n.body and isinstance(n.body[-1], (ast.Return, ast.Raise)):
                            covered |= self._checked(n.test, False)
                    else:
                        for block in _child_blocks(n):
                            walk(block, covered, lazy, fn)

            walk(tree.body, set(), False, None)
        self.memo[key] = (load, late)
        return load, late

    def _visit_import(
        self,
        rel: str,
        bases: tuple[str, ...],
        n: ast.Import | ast.ImportFrom,
        side: dict,
        late: dict,
        covered: set[str],
        in_main: set[str],
        fn: ast.AST | None,
    ) -> None:
        """import の文が届けるグループを side（と、読まれる側の遅れて読む分を late）へ足す。"""
        for name in _import_names(n):
            local = self._resolve(rel, bases, name)
            if local:
                sub_load, sub_late = self.module(local, bases)
                for g, (where, owners) in sub_load.items():
                    _add_group(side, g, where, owners, covered)
                # 読まれる側の関数の本体は main の require の後に呼ばれる（最上位の import のとき）
                for g, (where, owners) in sub_late.items():
                    _add_group(late, g, where, owners, covered | (in_main if fn is None else set()))
            elif not name.startswith("."):
                owners = self.owners(name)
                if owners:
                    _add_group(side, owners[0], f"{name}（{rel}）", frozenset(owners), covered)


def _entries(root: Path, files: list[str]) -> list[str]:
    """`if __name__ == "__main__":` の文を最上位に持つ `.py`（hook の経路と `lib/deps.py` を除く。I7）。"""
    hook, todo = set(), [e for e in HOOK_ENTRIES if (root / e).is_file()]
    while todo:
        rel = todo.pop()
        if rel in hook:
            continue
        hook.add(rel)
        todo += [f for f in (_module_file(root, n) for n in _imported(rel, (root / rel).read_text(errors="ignore"))) if f]
    out = []
    for rel in files:
        if not rel.endswith(".py") or rel.endswith("lib/deps.py") or rel in hook:
            continue
        text = (root / rel).read_text(errors="ignore")
        if "__main__" not in text:
            continue
        try:
            body = ast.parse(text).body
        except SyntaxError:
            continue
        if any(isinstance(n, ast.If) and "__name__" in ast.unparse(n.test) and "__main__" in ast.unparse(n.test) for n in body):
            out.append(rel)
    return out


def require_groups(root: Path) -> list[dict]:
    """決定 23・#1455: エントリポイントから import（関数の中を含む）をたどって届く外部パッケージのグループを、覆う require が並べるか。"""
    groups = _dep_groups(root)
    if not groups:
        return []
    scan, out = _DepScan(root, groups), []
    for entry in _entries(root, list_files(root)):
        bases = (Path(entry).parent.as_posix() + "/", *IMPORT_ROOTS)
        load, late = scan.module(entry, bases, entry=True)
        missing = {**late, **load}
        for g, (where, _) in sorted(missing.items()):
            out.append(
                {
                    "kind": "require-groups",
                    "path": entry,
                    "function": g,
                    "detail": f"{where}へ届くのに deps.require() にグループ {g} が無い",
                }
            )
    return out


def _walk_no_nested(node: ast.AST):
    """node の下を、入れ子の関数とクラスの中に入らずにたどる。"""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield child
        yield from _walk_no_nested(child)


def _find_def(tree: ast.Module, dotted: str) -> ast.AST | None:
    """`Class.method` か `func` の定義（無ければ None）。"""
    scope: ast.AST = tree
    for part in dotted.split("."):
        scope = next(
            (
                n
                for n in getattr(scope, "body", [])
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == part
            ),
            None,
        )
        if scope is None:
            return None
    return scope


def _is_durable_workflow(node: ast.AST) -> bool:
    f = node.func if isinstance(node, ast.Call) else node
    return isinstance(f, ast.Attribute) and f.attr == "workflow" and isinstance(f.value, ast.Name) and f.value.id == "durable"


def _has_workflow(tree: ast.AST) -> bool:
    """`@durable.workflow(...)` の関数か、`durable.workflow(...)(関数)` の呼び出しを持つか。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(_is_durable_workflow(d) for d in node.decorator_list):
            return True
        if isinstance(node, ast.Call) and _is_durable_workflow(node):
            return True
    return False


def _no_loops(fn: ast.AST) -> str | None:
    for node in _walk_no_nested(fn):
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            return f"{node.lineno} 行: {type(node).__name__.lower().replace('async', '')} の文"
    return None


def _state_keys(fn: ast.AST) -> str | None:
    """`json.dumps(<辞書>)` に渡す辞書の鍵が STATE_KEYS に収まるか。"""
    literal: dict[str, list[ast.AST]] = defaultdict(list)
    for node in _walk_no_nested(fn):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and isinstance(node.value, ast.Dict):
                    literal[t.id] += node.value.keys
                elif isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name):
                    literal[t.value.id].append(t.slice)
    for node in _walk_no_nested(fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "dumps" and node.args):
            continue
        arg = node.args[0]
        keys = arg.keys if isinstance(arg, ast.Dict) else literal.get(arg.id, []) if isinstance(arg, ast.Name) else [arg]
        for k in keys:
            if not (isinstance(k, ast.Constant) and k.value in STATE_KEYS):
                return f"{node.lineno} 行: state.json へ書く辞書に {ast.unparse(k) if k is not None else '**'} の鍵"
    return None


def _no_process_pool(tree: ast.AST) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run_batch":
            return f"{node.lineno} 行: 関数 run_batch"
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            if name == "Popen" or (name == "poll" and isinstance(f, ast.Attribute)):
                return f"{node.lineno} 行: {name} の呼び出し"
    return None


def _no_file_queue(cls: ast.AST) -> str | None:
    for method in getattr(cls, "body", []):
        if getattr(method, "name", "") == "_import_legacy":
            continue
        for sub in ast.walk(method):
            if isinstance(sub, ast.Call) and isinstance(sub.func, (ast.Attribute, ast.Name)):
                f = sub.func
                name = f.attr if isinstance(f, ast.Attribute) else f.id
                owner = f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
                if (name == "open" and owner == "os") or name in ("glob", "rglob", "iglob"):
                    return f"{sub.lineno} 行: {owner + '.' if owner else ''}{name} の呼び出し（{getattr(method, 'name', '')}）"
    return None


def _no_drive_file(tree: ast.AST) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in ("save_ds", "ds_path"):
            return f"{node.lineno} 行: {node.name} の定義（drive の状態ファイル）"
    return None


def durable_boundary(root: Path) -> list[dict]:
    """I15: 置き場の関数に、遷移のループ・再開の位置の読み書き・子プロセスの数え上げが無く、耐久ワークフローがあるか。"""
    present = [rel for rel in DURABLE_BOUNDARY if (root / rel).is_file()]
    if not present:
        return []  # 置き場を持たない木（テストの一時の木など）は見ない
    out: list[dict] = []

    def bad(rel: str, function: str, detail: str) -> None:
        out.append({"kind": "durable-boundary", "path": rel, "function": function, "detail": detail})

    for rel, (targets, rule, needs_workflow) in DURABLE_BOUNDARY.items():
        if rel not in present:
            bad(rel, "", "I15 の置き場のファイルが無い")
            continue
        text = (root / rel).read_text(errors="ignore")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            bad(rel, "", "構文木を読めない")
            continue
        for dotted in targets or ("",):
            node = _find_def(tree, dotted) if dotted else tree
            if node is None:
                bad(rel, dotted, "I15 の表の関数・クラスが無い")
                continue
            why = rule(node)
            if why:
                bad(rel, dotted, why)
        if rel in DRIVES and (why := _no_drive_file(tree)):
            bad(rel, "Drive", why)
        if needs_workflow and not _has_workflow(tree):
            mods = [f for f in (_module_file(root, n) for n in _imported(rel, text)) if f]
            if not any(_has_workflow(ast.parse((root / m).read_text(errors="ignore"))) for m in mods):
                bad(rel, "durable.workflow", "@durable.workflow の関数も、それを持つモジュールの呼び出しも無い")
    return out


STATE_KEYS = ("log", "llm", "project_mvv")
# I15 の置き場: ファイル → (見る関数・クラス（空はモジュール全体）, 規則, 耐久ワークフローが要るか)
DURABLE_BOUNDARY = {
    LIB + "supervise_lib/engine.py": (("Engine.run",), _no_loops, True),
    LIB + "supervise_lib/state.py": (("RunState.record",), _state_keys, False),
    LIB + "supervise_lib/queue.py": ((), _no_process_pool, True),
    LIB + "lib/post_queue.py": (("Queue",), _no_file_queue, True),
    "plugins/ndf/skills/cross-review/scripts/drive.py": (("Drive.run",), _no_loops, True),
    "plugins/ndf/skills/cross-refactoring/scripts/drive.py": (("Drive.run", "Drive.phases", "Drive.final_gate"), _no_loops, True),
}
DRIVES = ("plugins/ndf/skills/cross-review/scripts/drive.py", "plugins/ndf/skills/cross-refactoring/scripts/drive.py")


def item(kind: str, path: str, function: str, result: str, detail: str) -> dict:
    return {
        "kind": kind,
        "name": f"{path}:{function}" if function else path,
        "path": path,
        "function": function,
        "result": result,
        "detail": detail,
    }


def _rel(root: Path, target: str) -> str:
    """指定のパスを根からの相対パス（`/` 区切り・末尾の `/` なし）にする。根の外の絶対パスはそのまま返す。"""
    p = Path(target)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(root)
        except ValueError:
            return p.as_posix()
    parts = [x for x in p.as_posix().split("/") if x not in ("", ".")]
    return "/".join(parts) or "."


class Scope:
    """位置引数のファイルの指定。違反と例外の行が指定に関わるかを決める（#1668）。"""

    def __init__(self, root: Path, allow_dir: Path, targets: list[str]):
        self.targets = {_rel(root, t) for t in targets}
        allow_rel = _rel(root, str(allow_dir.resolve()))
        # 例外リストの置き場と、チェッカ自身とその部品（scripts/lib/）の変更は木全体の結果を変える
        self.everything = under(SELF, self.targets) or under(allow_rel, self.targets) or any(under(t, (SHARED_LIB,)) for t in self.targets)
        self.allow_files = {Path(t).name for t in self.targets if t.startswith(allow_rel + "/")}

    def row(self, r: dict) -> bool:
        """例外の行が指定に関わるか（行の `path` か、行のファイルが指定にある）。"""
        return self.everything or under(r["path"], self.targets) or allow_file_name(r) in self.allow_files

    def violation(self, v: dict, row: dict | None) -> bool:
        if self.everything or under(v["path"], self.targets) or any(under(o, self.targets) for o in v.get("others", ())):
            return True
        return row is not None and allow_file_name(row) in self.allow_files


def check(root: Path, allow: list[dict], scope: Scope | None = None) -> tuple[list[dict], dict]:
    violations, metrics = scan(root)
    allowed = {(r["path"], r["name"], r["kind"]): r for r in allow}
    hit = set()
    items = []
    outside = 0
    for v in violations:
        k = (v["path"], v["function"], v["kind"])
        row = allowed.get(k)
        if row is not None:
            hit.add(k)
            if v["kind"] == "lines" and v["lines"] > row["lines"]:
                if scope is None or scope.violation(v, row):
                    items.append(item("lines", v["path"], "", "violation", f"{v['lines']} 行。例外リストの {row['lines']} 行を超えた"))
                else:
                    outside += 1
            continue
        if scope is not None and not scope.violation(v, None):
            outside += 1
            continue
        items.append(item(v["kind"], v["path"], v["function"], "violation", v["detail"]))
    for r in allow:
        k = (r["path"], r["name"], r["kind"])
        if k not in hit:
            if scope is not None and not scope.row(r):
                outside += 1
                continue
            items.append(
                item(
                    "unused-allow",
                    r["path"],
                    r["name"],
                    "violation",
                    f"{r['kind']} の例外に当たる違反が無い。例外リストの {allow_file_name(r)} を消す",
                )
            )
    items.sort(key=lambda i: (i["path"], i["function"], i["kind"]))
    metrics.update(allowed=len(hit), violations=len(items))
    if scope is not None:
        metrics.update(targets=len(scope.targets), outside=outside)
    return items, metrics


def emit(status: str, summary: str, items: list[dict], metrics: dict) -> None:
    print(
        json.dumps(
            {"tool": "check-script-structure", "status": status, "summary": summary, "items": items, "metrics": metrics}, ensure_ascii=False
        )
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=REPO, help="リポジトリの根（既定はこのスクリプトのリポジトリ）")
    ap.add_argument("--allow", type=Path, help="例外リストの置き場（既定は <root>/scripts/script-structure-allow/）")
    ap.add_argument("files", nargs="*", help="合否を決めるファイル（省くと木全体の違反で決める）")
    a = ap.parse_args(argv)
    root = a.root.resolve()
    allow_dir = a.allow or root / "scripts" / "script-structure-allow"
    try:
        allow = load_allow(allow_dir)
    except UsageError as e:
        emit("stopped", str(e), [], {})
        return 2
    items, metrics = check(root, allow, Scope(root, allow_dir, a.files) if a.files else None)
    if items:
        for i in items:
            print(f"{i['kind']}: {i['name']}: {i['detail']}", file=sys.stderr)
        emit("stopped", f"構造の違反 {len(items)} 件（例外リストで許した {metrics['allowed']} 件を除く）", items, metrics)
        return 1
    emit("ok", f"違反なし（{metrics['files']} ファイル・例外リストで許した {metrics['allowed']} 件）", items, metrics)
    return 0


if __name__ == "__main__":
    sys.exit(main())
