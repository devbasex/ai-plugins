#!/usr/bin/env python3
"""根の `scripts/` の構造チェック（docs/specifications/ndf-rebuild.md の決定 89・docs/specifications/ndf-rebuild-libraries.md の決定 23。11.0.0 の入れ替え #3304 の後の形）。

NDF（`plugins/ndf/`）の構造は NDF 自身の検査（`plugins/ndf/bin/ndf check`。規則は
`plugins/ndf/src/ndf/checks/README.md`）が見る。このチェックは NDF の外で、その検査が見ない次の 2 つを見る。

- `require-groups`: 根の `scripts/` のエントリポイント（最上位に `if __name__ == "__main__":` の文を持つ `.py`。
  テストと `scripts/lib/ndf_wrappers.py` を除く）から import をたどって届く外部パッケージのグループを、その import を
  覆う `require(…)`（`scripts/lib/ndf_wrappers.py` の `require`）が並べていない（docs/specifications/ndf-rebuild-libraries.md の決定 17・23）。`python3 scripts/<名前>.py`
  で打つと、`require` が根の uv の環境へ起動し直すまで外部パッケージは読めないためである。import は関数の中の
  ものも含めて、エントリポイントの置き場と `scripts/`・`scripts/lib/` のモジュールへ推移的にたどり、届いた外部
  パッケージを `ndf_wrappers.py` の `GROUPS` でグループへ引く。呼び出しの流れは追わない。`require` が覆うのは、
  モジュールの最上位で呼べばそれより後の最上位の文とすべての関数の本体、関数の中で呼べばその関数のそれより後の文
  （入れ子のブロックで呼べばそのブロックの中）だけ。例外は `main` の本体の最上位で呼ぶ `require` で、そのモジュールを
  エントリポイントとして見るときだけ、`main` のそれより後の文と、関数の本体で遅れて読む依存（最上位で import した
  モジュールの関数の本体を含む）を覆う。任意の依存は違反にしない: `except ImportError` / `ModuleNotFoundError`
  （組の中を含む）を受ける `try` の本体と、`find_spec("<名前>")` / `importable("<グループ>")` の確かめが成り立つとき
  だけ通る枝（真で入る `if` の本体と、偽で入り `return` / `raise` で終わる `if` の後の同じブロックの文）。互いに
  import し合うモジュールは、たどっている途中のものを空として扱う。例外リストの `name` はグループの名前
- `hook-deps`: プラグインの hook の定義（`plugins/*/hooks/*.json`）の command が `uv run` を挟む（docs/specifications/ndf-rebuild.md の決定 89。hook は
  用意済みの環境の python を直に起動する）。例外リストの `name` は `uv run`

例外リストの置き場（既定は `scripts/script-structure-allow/`）は 1 項目 1 ファイルで、各ファイルは
`{"path", "name", "kind", "reason"}` を持つ。ファイル名は `allow_file_name()` が path・name・kind から
決める（例 `scripts__x.py--md--require-groups.json`）。違反に当たらない項目は `unused-allow` として落とす。

    python3 scripts/check-script-structure.py [--root <リポジトリ>] [--allow <ディレクトリ>] [<ファイル>...]

ファイル（根からの相対パスか根の下の絶対パス。無いファイルやディレクトリも可）を渡すと、検査は木全体で行い、
`items` と合否を指定に関わる違反だけで決める（#1668。項目の範囲テストの静的解析の suite が使う）。関わるのは、
違反の `path` が指定にあるとき、指定に例外リストの置き場のファイルがあればその行に当たる違反と `unused-allow`、
指定に検査のスクリプト自身か `scripts/lib/` があればすべての違反である。このとき `metrics` に `targets`（指定の数）と
`outside`（指定に関わらず外した違反の数）を足す。

最後に結果 JSON を 1 行出す（`{"tool", "status", "summary", "items", "metrics"}`）。終了コードは 0 が違反なし、
1 が違反あり、2 が例外リストの読めない・形の誤り（指定に関わらず 2）。
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from allowlist import AllowError, allow_file_name, under  # noqa: E402
from allowlist import load_allow as _load_rows  # noqa: E402
from allowlist import rel_target as _rel  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SCAN = "scripts"
SELF = "scripts/check-script-structure.py"
SHARED_LIB = "scripts/lib"
WRAPPERS = SHARED_LIB + "/ndf_wrappers.py"
# エントリポイントの置き場のほかに import をたどる置き場（エントリポイントは `scripts/lib` を sys.path へ足す）
IMPORT_ROOTS = (SCAN + "/", SHARED_LIB + "/")
KINDS = ("hook-deps", "require-groups")

UsageError = AllowError


def is_test(rel: Path) -> bool:
    return any(x in ("tests", "test") for x in rel.parts) or rel.name.startswith("test_")


def list_files(root: Path) -> list[str]:
    """走査の対象（根の `scripts/` のテストを除く `.py`）を、root からの相対パス（`/` 区切り）で返す。"""
    p = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", SCAN], capture_output=True, text=True)
    if p.returncode == 0 and p.stdout:
        rels = [r for r in p.stdout.split("\0") if r]
    else:
        base = root / SCAN
        rels = [str(f.relative_to(root)) for f in base.rglob("*") if f.is_file()] if base.is_dir() else []
    out = []
    for r in rels:
        rel = Path(r)
        if rel.suffix != ".py" or ".worktrees" in rel.parts or is_test(rel):
            continue
        if (root / rel).is_file():
            out.append(rel.as_posix())
    return sorted(out)


def load_allow(path: Path) -> list[dict]:
    """置き場（ディレクトリ）の `*.json` を 1 ファイル 1 項目として読む。"""
    return _load_rows(path, KINDS)


def scan(root: Path) -> tuple[list[dict], dict]:
    files = list_files(root)
    violations = hook_deps(root) + require_groups(root, files)
    return violations, {"files": len(files)}


def hook_deps(root: Path) -> list[dict]:
    """docs/specifications/ndf-rebuild.md の決定 89: hook の command が uv run を挟まないか。"""
    out: list[dict] = []
    for f in sorted(root.glob("plugins/*/hooks/*.json")):
        if "uv run" in f.read_text(errors="ignore"):
            out.append(
                {
                    "kind": "hook-deps",
                    "path": f.relative_to(root).as_posix(),
                    "function": "uv run",
                    "detail": "hook の command が uv run を挟む（用意済みの環境の python を直に起動する）",
                }
            )
    return out


def _dep_groups(root: Path) -> dict[str, list[str]]:
    """`scripts/lib/ndf_wrappers.py` の `GROUPS`（グループ → import の名前）。無ければ空。"""
    try:
        tree = ast.parse((root / WRAPPERS).read_text(errors="ignore"))
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
    """文が `require(…)` / `ndf_wrappers.require(…)` の呼び出しなら、並べたグループ。そうでなければ None。"""
    call = stmt.value if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) else None
    if call is None:
        return None
    f = call.func
    if (isinstance(f, ast.Attribute) and f.attr == "require" and isinstance(f.value, ast.Name) and f.value.id == "ndf_wrappers") or (
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
            _ModuleWalk(self, rel, bases, tree, entry, load, late).walk(tree.body, set(), False, None)
        self.memo[key] = (load, late)
        return load, late


class _ModuleWalk:
    """1 つのモジュールの文をたどる間の状態（読み込みの時に届く側 load・遅れて読む側 late と、冒頭と main の require）。"""

    def __init__(
        self,
        scan: _DepScan,
        rel: str,
        bases: tuple[str, ...],
        tree: ast.Module,
        entry: bool,
        load: dict[str, tuple[str, frozenset]],
        late: dict[str, tuple[str, frozenset]],
    ):
        self.scan, self.rel, self.bases, self.load, self.late = scan, rel, bases, load, late
        self.top = set().union(*(_require_call(s) or set() for s in tree.body))
        self.main = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
        self.in_main = set().union(*(_require_call(s) or set() for s in self.main.body)) if entry and self.main else set()

    def walk(self, stmts: list[ast.stmt], covered: set[str], lazy: bool, fn: ast.AST | None) -> None:
        covered = set(covered)
        for n in stmts:
            listed = _require_call(n)
            if listed is not None:
                covered |= listed
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                base = self.top if fn is None else covered
                self.walk(n.body, base | (self.in_main if n is not self.main else set()), True, n)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                self.visit_import(n, self.late if lazy else self.load, covered, fn)
            elif isinstance(n, ast.If):
                self.walk(n.body, covered | self.scan._checked(n.test, True), lazy, fn)
                self.walk(n.orelse, covered | self.scan._checked(n.test, False), lazy, fn)
                if n.body and isinstance(n.body[-1], (ast.Return, ast.Raise)):
                    covered |= self.scan._checked(n.test, False)
            else:
                for block in _child_blocks(n):
                    self.walk(block, covered, lazy, fn)

    def visit_import(self, n: ast.Import | ast.ImportFrom, side: dict, covered: set[str], fn: ast.AST | None) -> None:
        """import の文が届けるグループを side（と、読まれる側の遅れて読む分を late）へ足す。"""
        for name in _import_names(n):
            local = self.scan._resolve(self.rel, self.bases, name)
            if local:
                sub_load, sub_late = self.scan.module(local, self.bases)
                for g, (where, owners) in sub_load.items():
                    _add_group(side, g, where, owners, covered)
                # 読まれる側の関数の本体は main の require の後に呼ばれる（最上位の import のとき）
                for g, (where, owners) in sub_late.items():
                    _add_group(self.late, g, where, owners, covered | (self.in_main if fn is None else set()))
            elif not name.startswith("."):
                owners = self.scan.owners(name)
                if owners:
                    _add_group(side, owners[0], f"{name}（{self.rel}）", frozenset(owners), covered)


def _entries(root: Path, files: list[str]) -> list[str]:
    """`if __name__ == "__main__":` の文を最上位に持つ `.py`（`ndf_wrappers.py` を除く）。"""
    out = []
    for rel in files:
        if rel == WRAPPERS:
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


def require_groups(root: Path, files: list[str]) -> list[dict]:
    """docs/specifications/ndf-rebuild-libraries.md の決定 23・#1455: エントリポイントから import（関数の中を含む）をたどって届く外部パッケージのグループを、覆う require が並べるか。"""
    groups = _dep_groups(root)
    if not groups:
        return []
    deps, out = _DepScan(root, groups), []
    for entry in _entries(root, files):
        bases = tuple(dict.fromkeys((Path(entry).parent.as_posix() + "/", *IMPORT_ROOTS)))
        load, late = deps.module(entry, bases, entry=True)
        missing = {**late, **load}
        for g, (where, _) in sorted(missing.items()):
            out.append(
                {
                    "kind": "require-groups",
                    "path": entry,
                    "function": g,
                    "detail": f"{where}へ届くのに require() にグループ {g} が無い",
                }
            )
    return out


def item(kind: str, path: str, function: str, result: str, detail: str) -> dict:
    return {
        "kind": kind,
        "name": f"{path}:{function}" if function else path,
        "path": path,
        "function": function,
        "result": result,
        "detail": detail,
    }


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
        if self.everything or under(v["path"], self.targets):
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
        if k in allowed:
            hit.add(k)
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
