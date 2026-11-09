"""上限の宣言（`plugins/ndf/contracts/limits.json`）とコードの方針の名前の対応（#2343 の決定 13・不変条件 I1・I4）。

- 形（I1）は配布物の読み手（`plugins/ndf/scripts/lib/limits.py` の `validate`）で確かめ、検査の規則を 2 つに分けない
- 方針の名前を引く呼び出し（`import limits` で束ねた名前の `.policy("<名前>")`・`.seconds("<名前>")`・`.value("<名前>", …)`）を
  構文木で集める。名前はモジュールの最上位で文字列を代入した定数でもよい。宣言に無い名前・文字列で書いていない名前・
  どこからも引かれない方針を落とす。読み手が接頭辞で数え上げる `monitor.` の方針は引かれたものとする
- 契約の一覧の逆引き（`contracts/index.md`）へ「上限の宣言」の表（方針・値・何の上限か・引く関数）を描く
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

DECL = "plugins/ndf/contracts/limits.json"
READER = "plugins/ndf/scripts/lib/limits.py"
CALLS = ("policy", "seconds", "value")
ENUMERATED = "monitor."
VALUE_KEYS = ("seconds", "interval", "factor", "max_interval", "per_call", "attempts", "same_cause")


def _reader(root: Path):
    spec = importlib.util.spec_from_file_location("ndf_limits_reader_for_contracts", root / READER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def code_files(root: Path) -> list[str]:
    """方針の名前を引くコード: `plugins/ndf/` の `.py` のうち、テスト（`tests/` の下）と実験版を除くもの。
    名前が `test_` で始まるライブラリ（`lib/test_triage.py`）も含める。"""
    out = []
    for f in sorted((root / "plugins/ndf").rglob("*.py")):
        rel = f.relative_to(root).as_posix()
        if "/tests/" not in rel and "/experimental/" not in rel and "/.worktrees/" not in rel:
            out.append(rel)
    return out


def load_policies(root: Path) -> tuple[dict, str | None]:
    """宣言の方針の表と、読めない・形が違うときの理由。"""
    path = root / DECL
    if not path.is_file():
        return {}, f"上限の宣言 {DECL} が無い"
    try:
        reader = _reader(root)
        return reader.validate(json.loads(path.read_text(encoding="utf-8")), DECL), None
    except Exception as exc:  # noqa: BLE001  読み手の LimitsError と JSON の誤りを同じ違反にする
        return {}, str(exc)


def _aliases(tree: ast.Module) -> set[str]:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.asname or a.name for a in n.names if a.name == "limits"}
    return out


def _str_consts(tree: ast.Module) -> dict[str, str]:
    out = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            out.update({t.id: n.value.value for t in n.targets if isinstance(t, ast.Name)})
    return out


class _Calls(ast.NodeVisitor):
    def __init__(self, aliases: set[str], consts: dict[str, str]):
        self.aliases, self.consts = aliases, consts
        self.stack: list[str] = []
        self.found: list[tuple[str | None, str, int]] = []  # (名前か None, 関数, 行)

    def _scope(self, n):
        self.stack.append(n.name)
        self.generic_visit(n)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

    def visit_Call(self, n: ast.Call) -> None:
        f = n.func
        if isinstance(f, ast.Attribute) and f.attr in CALLS and isinstance(f.value, ast.Name) and f.value.id in self.aliases and n.args:
            a = n.args[0]
            name = a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else None
            if name is None and isinstance(a, ast.Name):
                name = self.consts.get(a.id)
            self.found.append((name, ".".join(self.stack) or "<module>", n.lineno))
        self.generic_visit(n)


def references(root: Path, files: list[str]) -> dict[str, list[str]]:
    """方針の名前 → 引く関数（`<パス>:<関数>`）。名前を決められない呼び出しは `None` の鍵に入る。"""
    out: dict = {}
    for rel in files:
        if rel == READER:
            continue
        try:
            tree = ast.parse((root / rel).read_text(errors="ignore"))
        except (OSError, SyntaxError):
            continue
        aliases = _aliases(tree)
        if not aliases:
            continue
        c = _Calls(aliases, _str_consts(tree))
        c.visit(tree)
        for name, func, line in c.found:
            where = f"{rel}:{func}" if name is not None else f"{rel}:{line}"
            if where not in out.setdefault(name, []):
                out[name].append(where)
    return out


def violations(root: Path, files: list[str]) -> list[tuple[str, str, str, str]]:
    """`(種類, パス, 名前, 理由)` の並び。種類は limits-shape・limits-unknown・limits-unused。"""
    policies, why = load_policies(root)
    if why:
        return [("limits-shape", DECL, "", why)]
    refs = references(root, files)
    out = []
    for where in refs.pop(None, []):
        out.append(("limits-unknown", where.rsplit(":", 1)[0], where, "方針の名前を文字列（か最上位の文字列の定数）で書く"))
    for name, wheres in sorted(refs.items()):
        if name not in policies:
            for w in wheres:
                out.append(("limits-unknown", w.split(":", 1)[0], name, f"上限の宣言 {DECL} に無い方針を引く（{w}）"))
    for name in sorted(set(policies) - set(refs)):
        if not name.startswith(ENUMERATED):
            out.append(("limits-unused", DECL, name, "どこからも引かれない方針。消すか、引く部品を直す"))
    return out


def _values(p) -> str:
    return " / ".join(f"{k} {getattr(p, k):g}" for k in VALUE_KEYS if getattr(p, k) is not None)


def render_section(root: Path, files: list[str]) -> list[str]:
    """逆引きの「上限の宣言」の表の行。"""
    policies, why = load_policies(root)
    lines = ["", "## 上限の宣言", "", f"`{DECL}` の方針。部品は方針の名前で `lib/limits.py` から引く。", ""]
    if why:
        return lines + [f"宣言を読めない: {why}"]
    refs = references(root, files)
    lines += ["| 方針 | 値 | 何の上限か | 引く関数 |", "| --- | --- | --- | --- |"]
    for name, p in sorted(policies.items()):
        users = refs.get(name) or ([f"{READER}（接頭辞 {ENUMERATED} で数え上げる）"] if name.startswith(ENUMERATED) else [])
        cell = "<br>".join(f"`{u}`" for u in users) if users else "—"
        lines.append(f"| `{name}` | {_values(p)} | {p.summary.replace('|', '／')} | {cell} |")
    return lines
