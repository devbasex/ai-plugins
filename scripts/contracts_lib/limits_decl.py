"""上限の設定（`plugins/ndf/contracts/limits/`）とコードが引く方針の名前の対応（#2343 の決定 13）。

- 設定は 1 方針 1 ファイルで、`_` で始まらない `<方針の名前>.json` が方針 1 つである。形はプラグインの読み手
  （`ndf.contracts.limits.validate`）と `ndf contracts check` が確かめる。ここではどのファイルもオブジェクトとして
  読めるかだけを見る（規則を 2 つに分けない）
- 上限の読み手（`from ndf.contracts import limits` か `import limits` で import した名前）の `.policy("<名前>")`・
  `.seconds("<名前>")`・`.value("<名前>", …)` を構文木で集め、文字列（かモジュールの最上位で文字列を代入した定数）で
  書いた名前のファイルが無ければ落とす。新しいプラグインは名前を引数や組み立てで渡す箇所を持つため、名前を静的に
  決められない呼び出しと、どこからも引かれない方針は数えない
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

DECL = "plugins/ndf/contracts/limits"
CODE = "plugins/ndf/src"
CALLS = ("policy", "seconds", "value")
READER_NAMES = ("limits", "ndf.contracts.limits")


def code_files(root: Path) -> list[str]:
    """方針の名前を引くコード: `plugins/ndf/src/` の `.py`。"""
    return [f.relative_to(root).as_posix() for f in sorted((root / CODE).rglob("*.py")) if "__pycache__" not in f.parts]


def policy_path(name: str) -> str:
    """方針 `name` のファイルの、リポジトリの根からの相対パス。"""
    return f"{DECL}/{name}.json"


def load_policies(root: Path) -> tuple[set[str], list[tuple[str, str]]]:
    """方針の名前と、読めないもの（パス・理由）。"""
    if not (root / DECL).is_dir():
        return set(), [(DECL, f"上限の設定のディレクトリ {DECL} が無い")]
    names, broken = set(), []
    for path in sorted((root / DECL).iterdir()):
        if path.name.startswith("_"):
            continue
        why = None if path.suffix == ".json" else "拡張子が .json でない"
        try:
            if why is None and not isinstance(json.loads(path.read_text(encoding="utf-8")), dict):
                why = "オブジェクトでない"
        except (OSError, ValueError) as exc:
            why = str(exc)
        if why is not None:
            rel = f"{DECL}/{path.name}"
            broken.append((rel, f"上限の設定 {rel} を読めない: {why}"))
        names.add(path.stem)
    if not names:
        return set(), [(DECL, f"上限の設定 {DECL} に方針が 1 つも無い")]
    return names, broken


def _aliases(tree: ast.Module) -> set[str]:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            out |= {a.asname or a.name for a in n.names if a.name in READER_NAMES}
    return out


def _str_consts(tree: ast.Module) -> dict[str, str]:
    out = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            out.update({t.id: n.value.value for t in n.targets if isinstance(t, ast.Name)})
    return out


def _name_of(a: ast.expr, consts: dict[str, str]) -> str | None:
    if isinstance(a, ast.Constant) and isinstance(a.value, str):
        return a.value
    return consts.get(a.id) if isinstance(a, ast.Name) else None


def _called_names(tree: ast.Module) -> list[tuple[str, int]]:
    """上限の読み手の呼び出しのうち、名前を静的に決められるものの (名前, 行)。"""
    aliases = _aliases(tree)
    if not aliases:
        return []
    consts = _str_consts(tree)
    out = []
    for n in ast.walk(tree):
        f = getattr(n, "func", None)
        if not (isinstance(n, ast.Call) and isinstance(f, ast.Attribute) and f.attr in CALLS and n.args):
            continue
        if isinstance(f.value, ast.Name) and f.value.id in aliases:
            name = _name_of(n.args[0], consts)
            if name is not None:
                out.append((name, n.lineno))
    return out


def references(root: Path, files: list[str]) -> dict[str, list[str]]:
    """方針の名前 → 引く箇所（`<パス>:<行>`）。"""
    out: dict[str, list[str]] = {}
    for rel in files:
        try:
            tree = ast.parse((root / rel).read_text(errors="ignore"))
        except (OSError, SyntaxError):
            continue
        for name, line in _called_names(tree):
            out.setdefault(name, []).append(f"{rel}:{line}")
    return out


def violations(root: Path, files: list[str]) -> list[tuple[str, str, str, str]]:
    """`(種類, パス, 名前, 理由)` の並び。種類は limits-shape・limits-unknown。"""
    policies, broken = load_policies(root)
    if broken:
        return [("limits-shape", path, "", why) for path, why in broken]
    out = []
    for name, wheres in sorted(references(root, files).items()):
        if name not in policies:
            out += [("limits-unknown", w.rsplit(":", 1)[0], name, f"上限の設定 {DECL}/ に無い方針を引く（{w}）") for w in wheres]
    return out
