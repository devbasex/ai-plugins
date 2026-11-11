"""実行の核に関わる契約の検査（#2337 の決定 5・6・12、不変条件 I4・I5・I7・I13）。標準ライブラリだけで書く。

- `core-entry`: 核の対象外の印を持たないエントリポイントが核を通る（Python は `__main__` の中身が
  `run_core.main("<id>", <本体>)` の 1 文、シェルは先頭の実行文が `rc_enter "<id>" "$@"`。id は契約の path から
  `plugins/ndf/` を除いたプラグインの根からのパス）
- `core-exempt`: `hook` の印は hook の定義か hook のエントリポイントからの呼び出しを根拠に持つ。`runtime` の印は
  ベースブランチより増えない
- `exit-meaning`: 契約の `exit` の値は終了コードの表の見出しで始まり、エントリポイントとその部品は `EXIT_` で始まる
  整数の定数を持たない（持ち主の `lib/exit_codes.py` を除く）
- `result-read`: `lib/step_read.py` の外で、名前に `last_json` を含む関数の定義と `json.loads(<式>.splitlines()[-1])` を書かない
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

from .model import CONTRACTS, core_exempt

_LIB = Path(__file__).resolve().parents[2] / "plugins" / "ndf" / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import exit_codes  # noqa: E402

EXIT_OWNERS = ("plugins/ndf/scripts/lib/exit_codes.py",)
READER = "plugins/ndf/scripts/lib/step_read.py"
KINDS = ("core-entry", "core-exempt", "exit-meaning", "result-read")
PLUGIN_HEAD = "plugins/ndf/"


def entry_id(rel: str) -> str:
    """契約の path から核の id（プラグインの根からのパス）を作る。"""
    return rel.removeprefix(PLUGIN_HEAD)


def _main_block(tree: ast.Module) -> ast.If | None:
    for n in tree.body:
        if isinstance(n, ast.If) and isinstance(n.test, ast.Compare):
            t = n.test
            if isinstance(t.left, ast.Name) and t.left.id == "__name__" and isinstance(t.comparators[0], ast.Constant):
                if t.comparators[0].value == "__main__":
                    return n
    return None


def py_core_entry(text: str, rel: str) -> str | None:
    """Python のエントリポイントが核を通るか。通らなければ理由を返す。"""
    rel = entry_id(rel)
    try:
        block = _main_block(ast.parse(text))
    except SyntaxError as e:
        return f"構文を読めない: {e}"
    if block is None:
        return 'if __name__ == "__main__": が無い'
    body = block.body
    ok = (
        len(body) == 1
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Call)
        and isinstance(body[0].value.func, ast.Attribute)
        and isinstance(body[0].value.func.value, ast.Name)
        and body[0].value.func.value.id == "run_core"
        and body[0].value.func.attr == "main"
        and len(body[0].value.args) == 2
    )
    if not ok:
        return f'__main__ の中身を run_core.main("{rel}", main) の 1 文にする'
    first = body[0].value.args[0]
    if not (isinstance(first, ast.Constant) and first.value == rel):
        return f"run_core.main の id をプラグインの根からのパス（{rel}）にする"
    return None


def sh_core_entry(text: str, rel: str) -> str | None:
    """シェルのエントリポイントの先頭の実行文が `rc_enter "<id>" "$@"` か。通らなければ理由を返す。"""
    rel = entry_id(rel)
    for line in text.splitlines()[1:]:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"""^\.\s+.*run-core\.sh"?\s*;\s*rc_enter\s+(["']?)([^"'\s]+)\1\s+"\$@"\s*$""", s)
        if not m:
            return f'先頭の実行文を . "<lib>/run-core.sh"; rc_enter "{rel}" "$@" にする'
        if m.group(2) != rel:
            return f"rc_enter の id をプラグインの根からのパス（{rel}）にする"
        return None
    return "実行文が無い"


def _frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[: end if end > 0 else 0]


def hook_texts(root: Path) -> str:
    """hook の定義の文（`plugins/ndf/hooks/*.json` と Skill の frontmatter）。"""
    parts = [f.read_text(errors="ignore") for f in sorted((root / "plugins/ndf/hooks").glob("*.json"))]
    parts += [_frontmatter(f.read_text(errors="ignore")) for f in sorted((root / "plugins/ndf/skills").glob("*/SKILL.md"))]
    return "\n".join(parts)


def exempt_violations(root: Path, entries: dict, reached) -> list[tuple[str, str, str]]:
    """(path, name, 理由) の並び。`reached(rel)` は rel が import でたどるファイルの集合。"""
    out = []
    hooks_text = hook_texts(root)
    hook_entries = {r for r, e in entries.items() if core_exempt(e) == "hook"}
    hook_mods = set(hook_entries)
    for r in hook_entries:
        hook_mods |= reached(r)
    for rel, e in sorted(entries.items()):
        if core_exempt(e) != "hook":
            continue
        if Path(rel).name in hooks_text:
            continue
        if any(c in hook_mods and c != rel for c in e.get("callers", [])):
            continue
        out.append((rel, "hook", "hook の印の根拠が無い（hook の定義にパスが無く、hook のエントリポイントから呼ばれない）"))
    return out


def base_runtime_count(root: Path, sha: str | None) -> int | None:
    """ベースブランチの契約の一覧の runtime の印の数（決められなければ None）。
    git は一覧の取得と中身の一括読み（`cat-file --batch`）の 2 回だけ起こす。"""
    if not sha:
        return None
    ls = subprocess.run(
        ["git", "-C", str(root), "ls-tree", "-r", "--name-only", sha, "--", CONTRACTS + "/entrypoints/"], capture_output=True, text=True
    )
    if ls.returncode != 0:
        return None
    names = ls.stdout.splitlines()
    if not names:
        return 0
    cat = subprocess.run(
        ["git", "-C", str(root), "cat-file", "--batch"],
        input="".join(f"{sha}:{p}\n" for p in names).encode(),
        capture_output=True,
    )
    if cat.returncode != 0:
        return None
    n, buf, i = 0, cat.stdout, 0
    while i < len(buf):
        j = buf.index(b"\n", i)
        head = buf[i:j].split()
        i = j + 1
        if len(head) != 3:  # "<名前> missing" など中身の無い行
            continue
        size = int(head[2])
        body, i = buf[i : i + size], i + size + 1
        try:
            d = json.loads(body)
        except ValueError:
            continue
        if isinstance(d, dict) and core_exempt(d) == "runtime":
            n += 1
    return n


def exit_label_violations(entries: dict) -> list[tuple[str, str, str]]:
    out = []
    for rel, e in sorted(entries.items()):
        if core_exempt(e):
            continue
        for k, text in sorted(e["exit"].items()):
            if not k.lstrip("-").isdigit():
                continue
            label = exit_codes.label_of(int(k))
            if not label:
                out.append((rel, f"exit:{k}", f"終了コード {k} は終了コードの表に無い"))
            elif not text.startswith(label):
                out.append((rel, f"exit:{k}", f"契約の exit の {k} を見出し「{label}」で始める"))
    return out


def _exit_consts(tree: ast.Module) -> list[str]:
    out = []
    for n in tree.body:
        targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, ast.AnnAssign) and n.value else []
        value = getattr(n, "value", None)
        if not (isinstance(value, ast.Constant) and type(value.value) is int):
            continue
        out += [t.id for t in targets if isinstance(t, ast.Name) and t.id.startswith("EXIT_")]
    return out


def exit_const_violations(root: Path, entries: dict, reached) -> list[tuple[str, str, str]]:
    mods: set[str] = set()
    for rel, e in entries.items():
        if not core_exempt(e) and rel.endswith(".py"):
            mods |= reached(rel)
    out = []
    for m in sorted(mods - set(EXIT_OWNERS)):
        try:
            tree = ast.parse((root / m).read_text(errors="ignore"))
        except (OSError, SyntaxError):
            continue
        for name in _exit_consts(tree):
            out.append((m, name, f"独自の終了コードの定数 {name}。step_result の終了コードの表の定数を使う"))
    return out


def _is_last_line_loads(n: ast.AST) -> bool:
    """`json.loads(<式>.splitlines()[-1])` の形か。"""
    if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "loads" and n.args):
        return False
    a = n.args[0]
    if not (isinstance(a, ast.Subscript) and isinstance(a.value, ast.Call) and isinstance(a.value.func, ast.Attribute)):
        return False
    idx = a.slice
    neg_one = isinstance(idx, ast.UnaryOp) and isinstance(idx.op, ast.USub) and getattr(idx.operand, "value", None) == 1
    return a.value.func.attr == "splitlines" and neg_one


class _ReadScan(ast.NodeVisitor):
    def __init__(self):
        self.stack: list[str] = []
        self.found: list[tuple[str, str]] = []

    def _fn(self, n):
        if "last_json" in n.name:
            self.found.append((f"def:{n.name}", f"結果の読み手 {n.name} を定義する。lib/step_read.py の read_completed を使う"))
        self.stack.append(n.name)
        self.generic_visit(n)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = _fn

    def visit_Call(self, n):
        if _is_last_line_loads(n):
            where = self.stack[-1] if self.stack else "<module>"
            self.found.append((f"{where}:last-line", "最後の行を json.loads で読む。lib/step_read.py の read_completed を使う"))
        self.generic_visit(n)


def plugin_py_files(root: Path) -> list[str]:
    out = []
    for base in [root / "plugins/ndf/scripts", *sorted((root / "plugins/ndf/skills").glob("*/scripts"))]:
        for f in sorted(base.rglob("*.py")):
            rel = f.relative_to(root).as_posix()
            if "/tests/" in rel or "/experimental/" in rel or f.name.startswith("test_") or rel == READER:
                continue
            out.append(rel)
    return out


def result_read_violations(root: Path) -> list[tuple[str, str, str]]:
    out = []
    for rel in plugin_py_files(root):
        try:
            tree = ast.parse((root / rel).read_text(errors="ignore"))
        except (OSError, SyntaxError):
            continue
        s = _ReadScan()
        s.visit(tree)
        seen = set()
        for name, why in s.found:
            if name not in seen:
                seen.add(name)
                out.append((rel, name, why))
    return out
