#!/usr/bin/env python3
"""NDF のスクリプトの構造チェック（#1142 の不変条件 I4・I5）。

見るのは `plugins/ndf/` の下の `.py` と `.sh` のうち、テストを除くもの（`tests/`・`test/` の下と
`test_` で始まるファイル）。git の作業ツリーでは git が追跡するファイルだけを見る。

- `lines`: 1000 行を超えるファイル（I5）
- `same-body`: 本体の同じ最上位の関数が 2 つ以上のファイルにある（I4）。Python は docstring・型注釈・
  関数名を除いた構文木で、シェルは空行とコメントの行を除き前後の空白を落とした行で比べる
- `same-name`: 同じ名前で本体の違う最上位の関数が 2 つ以上のファイルにある（I4）。Python とシェルは
  別々に数える

副命令のハンドラー（`cmd_*`）・`main`・`build_parser`・`_build_parser`・シェルの `usage` は規則で外す。

例外リスト（既定は `scripts/script-structure-allow.json`）は `{"path", "name", "kind", "reason"}` の
配列である。`lines` の行は `name` を空にする。違反に当たらない行は `unused-allow` として落とす。
直した移行ステップは、同じ PR で例外リストの行を消す。

    python3 scripts/check-script-structure.py [--root <リポジトリ>] [--allow <json>]

最後に結果 JSON を 1 行出す（`plugins/ndf/scripts/lib/README.md` の形）。終了コードは 0 が違反なし、
1 が違反あり、2 が例外リストの読めない・形の誤り。
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

REPO = Path(__file__).resolve().parents[1]
SCAN = "plugins/ndf"
MAX_LINES = 1000
KINDS = ("lines", "same-body", "same-name")
RULE_NAMES = {"main", "build_parser", "_build_parser"}
SHELL_RULE_NAMES = {"usage"}
SH_FUNC_RE = re.compile(r"^(\s*)(?:function\s+([A-Za-z_][\w:.-]*)\s*(?:\(\))?|([A-Za-z_][\w:.-]*)\s*\(\))\s*(\{.*)?$")


class UsageError(Exception):
    pass


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
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
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
        end = next((j for j in range(start, len(lines))
                    if lines[j].rstrip() in (indent + "}", indent + "};")), None)
        if end is None:
            i += 1
            continue
        out.append((name, _norm_sh(lines[start:end])))
        i = end + 1
    return out


def load_allow(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        rows = json.loads(path.read_text())
    except ValueError as e:
        raise UsageError(f"例外リストを読めない: {path}: {e}")
    if not isinstance(rows, list):
        raise UsageError(f"例外リストは配列にする: {path}")
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or set(r) != {"path", "name", "kind", "reason"}:
            raise UsageError(f"例外リストの {i + 1} 行目は path・name・kind・reason の 4 つを持つ: {r}")
        if r["kind"] not in KINDS:
            raise UsageError(f"例外リストの {i + 1} 行目の kind は {'/'.join(KINDS)} のどれか: {r['kind']}")
        if not isinstance(r["reason"], str) or not r["reason"].strip():
            raise UsageError(f"例外リストの {i + 1} 行目に理由が無い: {r['path']}:{r['name']}")
    return rows


def scan(root: Path) -> tuple[list[dict], dict]:
    files = list_files(root)
    violations: list[dict] = []
    defs: dict[str, list[tuple[str, str, str]]] = {"py": [], "sh": []}  # lang -> [(path, name, key)]
    unparsed = 0
    for rel in files:
        text = (root / rel).read_text(errors="ignore")
        n = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
        if n > MAX_LINES:
            violations.append({"kind": "lines", "path": rel, "function": "", "detail": f"{n} 行"})
        lang = "py" if rel.endswith(".py") else "sh"
        found = py_functions(text) if lang == "py" else sh_functions(text)
        if found is None:
            unparsed += 1
            continue
        for name, key in found:
            if not excluded(name, lang):
                defs[lang].append((rel, name, key))
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
                violations.append({"kind": "same-body", "path": path, "function": name,
                                   "detail": "本体が同じ: " + ", ".join(sorted(others))})
        for name, per_path in by_name.items():
            if len(per_path) < 2:
                continue
            for path, keys in per_path.items():
                diff = sorted(p for p, ks in per_path.items() if p != path and ks != keys)
                if diff:
                    violations.append({"kind": "same-name", "path": path, "function": name,
                                       "detail": "同じ名前で本体が違う: " + ", ".join(diff)})
    metrics = {"files": len(files), "functions": sum(len(v) for v in defs.values()), "unparsed": unparsed}
    return violations, metrics


def item(kind: str, path: str, function: str, result: str, detail: str) -> dict:
    return {"kind": kind, "name": f"{path}:{function}" if function else path, "path": path,
            "function": function, "result": result, "detail": detail}


def check(root: Path, allow: list[dict]) -> tuple[list[dict], dict]:
    violations, metrics = scan(root)
    allowed = {(r["path"], r["name"], r["kind"]) for r in allow}
    hit = set()
    items = []
    for v in violations:
        k = (v["path"], v["function"], v["kind"])
        if k in allowed:
            hit.add(k)
            continue
        items.append(item(v["kind"], v["path"], v["function"], "violation", v["detail"]))
    for r in allow:
        k = (r["path"], r["name"], r["kind"])
        if k not in hit:
            items.append(item("unused-allow", r["path"], r["name"], "violation",
                              f"{r['kind']} の例外に当たる違反が無い。例外リストから消す"))
    items.sort(key=lambda i: (i["path"], i["function"], i["kind"]))
    metrics.update(allowed=len(hit), violations=len(items))
    return items, metrics


def emit(status: str, summary: str, items: list[dict], metrics: dict) -> None:
    print(json.dumps({"tool": "check-script-structure", "status": status, "summary": summary,
                      "items": items, "metrics": metrics}, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=REPO, help="リポジトリの根（既定はこのスクリプトのリポジトリ）")
    ap.add_argument("--allow", type=Path, help="例外リスト（既定は <root>/scripts/script-structure-allow.json）")
    a = ap.parse_args(argv)
    root = a.root.resolve()
    try:
        allow = load_allow(a.allow or root / "scripts" / "script-structure-allow.json")
    except UsageError as e:
        emit("stopped", str(e), [], {})
        return 2
    items, metrics = check(root, allow)
    if items:
        for i in items:
            print(f"{i['kind']}: {i['name']}: {i['detail']}", file=sys.stderr)
        emit("stopped", f"構造の違反 {len(items)} 件（例外リストで許した {metrics['allowed']} 件を除く）", items, metrics)
        return 1
    emit("ok", f"違反なし（{metrics['files']} ファイル・例外リストで許した {metrics['allowed']} 件）", items, metrics)
    return 0


if __name__ == "__main__":
    sys.exit(main())
