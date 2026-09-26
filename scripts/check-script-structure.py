#!/usr/bin/env python3
"""NDF のスクリプトの構造チェック（#1142 の不変条件 I4・I5・I14）。

見るのは `plugins/ndf/` の下の `.py` と `.sh` のうち、テストを除くもの（`tests/`・`test/` の下と
`test_` で始まるファイル）。git の作業ツリーでは git が追跡するファイルだけを見る。

- `lines`: 500 行を超えるファイル（I5・決定 18）。例外リストに載るファイルは、載せた行数を超えたら落ちる
- `same-body`: 本体の同じ最上位の関数が 2 つ以上のファイルにある（I4）。Python は docstring・型注釈・
  関数名を除いた構文木で、シェルは空行とコメントの行を除き前後の空白を落とした行で比べる
- `same-name`: 同じ名前で本体の違う最上位の関数が 2 つ以上のファイルにある（I4）。Python とシェルは
  別々に数える

- `wrapped`: 汎用の処理の包み（`lib/` の包み。決定 19）が受け持つ標準ライブラリの部品を、包みの外の Python の
  モジュールが使う（I14）。部品と持ち主は `WRAPPED` の表で、`fcntl`・`pty`・`termios`・`urllib.request` の import、
  `/proc/` の読み取り（docstring を除く文字列）、囲み（```` ``` ```` / `~~~`）を追う正規表現と `startswith` を見る。
  例外リストの `name` は部品の名前（`fcntl` など）

副命令のハンドラー（`cmd_*`）・`main`・`build_parser`・`_build_parser`・シェルの `usage` は規則で外す。

例外リストの置き場（既定は `scripts/script-structure-allow/`）は 1 項目 1 ファイルで、各ファイルは
`{"path", "name", "kind", "reason"}` を持つ。ファイル名は `allow_file_name()` が path・name・kind から
決める（例 `plugins__ndf__scripts__lib__deps.py--find_uv--same-body.json`）。並列の計画が別の項目を
消す・足しても同じファイルを触らないため、マージで衝突しない。`lines` の項目は `name` を空にし、
載せた時点の行数を `lines` に持つ（ラチェット）。違反に当たらない項目は `unused-allow` として落とす。
直した移行ステップは、同じ PR でその項目のファイルを消す。

    python3 scripts/check-script-structure.py [--root <リポジトリ>] [--allow <ディレクトリ>]

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
MAX_LINES = 500
KINDS = ("lines", "same-body", "same-name", "wrapped")
LIB = "plugins/ndf/scripts/"
# I14: 部品 → 使ってよい包み（決定 19。擬似端末は relay_lib/terminal.py が包みを兼ねる）
WRAPPED = {
    "fcntl": (LIB + "lib/locks.py",),
    "pty": (LIB + "relay_lib/terminal.py",),
    "termios": (LIB + "relay_lib/terminal.py",),
    "urllib.request": (LIB + "lib/notify.py",),
    "proc-fs": (LIB + "lib/procs.py",),
    "fence-regex": (LIB + "lib/md.py",),
}
RE_FUNCS = {"compile", "match", "search", "fullmatch", "finditer", "findall", "sub", "subn", "split"}
FENCE_HINT = re.compile(r"```|~~~|`\{3|~\{3|\[`~\]|\[~`\]")
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


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
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
                for part in ("fcntl", "pty", "termios", "urllib.request"):
                    if a.name == part or a.name.startswith(part + "."):
                        hit(part, node, f"import {a.name}")
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = {a.name for a in node.names}
            for part in ("fcntl", "pty", "termios", "urllib.request"):
                if node.module == part or node.module.startswith(part + ".") \
                        or (part == "urllib.request" and node.module == "urllib" and "request" in names):
                    hit(part, node, f"from {node.module} import")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs \
                and "/proc/" in node.value:
            hit("proc-fs", node, repr(node.value[:40]))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            f = node.func
            regex = f.attr in RE_FUNCS and isinstance(f.value, ast.Name) and f.value.id == "re"
            if (regex or f.attr in ("startswith", "endswith")) \
                    and any(FENCE_HINT.search(s) for a in node.args for s in _strings(a)):
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
        end = next((j for j in range(start, len(lines))
                    if lines[j].rstrip() in (indent + "}", indent + "};")), None)
        if end is None:
            i += 1
            continue
        out.append((name, _norm_sh(lines[start:end])))
        i = end + 1
    return out


def allow_file_name(row: dict) -> str:
    """例外リストの 1 項目のファイル名。path の `/` を `__` に、各部を `--` でつなぐ（lines は name を省く）。"""
    parts = [row["path"].replace("/", "__"), row["name"], row["kind"]]
    return "--".join(p for p in parts if p) + ".json"


def load_allow(path: Path) -> list[dict]:
    """置き場（ディレクトリ）の `*.json` を 1 ファイル 1 項目として読む。"""
    if not path.exists():
        return []
    if not path.is_dir():
        raise UsageError(f"例外リストの置き場はディレクトリにする: {path}")
    rows = []
    for f in sorted(path.glob("*.json")):
        try:
            r = json.loads(f.read_text())
        except ValueError as e:
            raise UsageError(f"例外リストを読めない: {f}: {e}")
        keys = {"path", "name", "kind", "reason"} | ({"lines"} if isinstance(r, dict) and r.get("kind") == "lines" else set())
        if not isinstance(r, dict) or set(r) != keys:
            raise UsageError(f"例外リストの {f.name} は {'・'.join(sorted(keys))} を持つ: {r}")
        if "lines" in r and (isinstance(r["lines"], bool) or not isinstance(r["lines"], int) or r["lines"] <= MAX_LINES):
            raise UsageError(f"例外リストの {f.name} の lines は {MAX_LINES} を超える整数: {r['lines']}")
        if r["kind"] not in KINDS:
            raise UsageError(f"例外リストの {f.name} の kind は {'/'.join(KINDS)} のどれか: {r['kind']}")
        if not isinstance(r["reason"], str) or not r["reason"].strip():
            raise UsageError(f"例外リストの {f.name} に理由が無い: {r['path']}:{r['name']}")
        if not all(isinstance(r[k], str) for k in ("path", "name")) or f.name != allow_file_name(r):
            raise UsageError(f"例外リストの {f.name} は {allow_file_name(r)} という名前にする")
        rows.append(r)
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
            violations.append({"kind": "lines", "path": rel, "function": "", "detail": f"{n} 行", "lines": n})
        lang = "py" if rel.endswith(".py") else "sh"
        if lang == "py":
            for part, where in sorted(wrapped_parts(text).items()):
                if rel not in WRAPPED[part]:
                    violations.append({"kind": "wrapped", "path": rel, "function": part,
                                       "detail": f"包み {' / '.join(WRAPPED[part])} が受け持つ部品を使う（{where}）"})
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
    allowed = {(r["path"], r["name"], r["kind"]): r for r in allow}
    hit = set()
    items = []
    for v in violations:
        k = (v["path"], v["function"], v["kind"])
        row = allowed.get(k)
        if row is not None:
            hit.add(k)
            if v["kind"] == "lines" and v["lines"] > row["lines"]:
                items.append(item("lines", v["path"], "", "violation",
                                  f"{v['lines']} 行。例外リストの {row['lines']} 行を超えた"))
            continue
        items.append(item(v["kind"], v["path"], v["function"], "violation", v["detail"]))
    for r in allow:
        k = (r["path"], r["name"], r["kind"])
        if k not in hit:
            items.append(item("unused-allow", r["path"], r["name"], "violation",
                              f"{r['kind']} の例外に当たる違反が無い。例外リストの {allow_file_name(r)} を消す"))
    items.sort(key=lambda i: (i["path"], i["function"], i["kind"]))
    metrics.update(allowed=len(hit), violations=len(items))
    return items, metrics


def emit(status: str, summary: str, items: list[dict], metrics: dict) -> None:
    print(json.dumps({"tool": "check-script-structure", "status": status, "summary": summary,
                      "items": items, "metrics": metrics}, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", type=Path, default=REPO, help="リポジトリの根（既定はこのスクリプトのリポジトリ）")
    ap.add_argument("--allow", type=Path, help="例外リストの置き場（既定は <root>/scripts/script-structure-allow/）")
    a = ap.parse_args(argv)
    root = a.root.resolve()
    try:
        allow = load_allow(a.allow or root / "scripts" / "script-structure-allow")
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
