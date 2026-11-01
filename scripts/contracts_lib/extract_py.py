"""Python のエントリポイントから、引数・環境変数・設定のキー・ファイルの書き・終了コードを静的に拾う。

拾う規則は設計の決定 5（`issues/issue-2336-design-decisions.md`）。拾った事実は、エントリポイントから
import でたどれるモジュールの読み書き（呼ばれるかは追わない）として扱う。構文木はファイルごとに 1 回だけ作る。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from py_imports import IMPORT_ROOTS, LIB, imported_tree, module_file

FILE_NAME = re.compile(r"^[\w*.-]*\w[\w*-]*\.(json|jsonl|md|txt|log|yml|yaml|toml|sh|csv|html|db|lock|pid|rc|out)$")
JSONIO_WRITERS = {"write_atomic", "append_jsonl"}


@dataclass
class ModFacts:
    """1 モジュールから拾った事実。"""

    rel: str
    runnable: bool = False
    imports: set[str] = field(default_factory=set)
    options: set[tuple[str, str]] = field(default_factory=set)  # (副命令, 引数)
    positionals: set[tuple[str, str]] = field(default_factory=set)
    has_parser: bool = False
    argv: bool = False
    env: dict[str, int] = field(default_factory=dict)  # 名前 → 行
    env_dynamic: set[str] = field(default_factory=set)  # 関数名
    writes: dict[str, int] = field(default_factory=dict)  # ファイル名か glob → 行
    setting_keys: dict[tuple[str, str], int] = field(default_factory=dict)  # (設定ファイル, キー) → 行
    setting_opens: dict[tuple[str, str], int] = field(default_factory=dict)  # (設定ファイル, 関数) → 行
    exits: dict[str, str] = field(default_factory=dict)  # 終了コード → 定数名（数値なら空）
    stdin: bool = False
    stdout: str = "none"
    summary: str = ""
    constants: dict[str, object] = field(default_factory=dict)
    unparsed: bool = False


def _const_str(node: ast.AST, consts: dict) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and isinstance(consts.get(node.id), str):
        return consts[node.id]
    return None


def _glob_of(node: ast.AST, consts: dict) -> list[str]:
    """パスの式に現れるファイル名（f 文字列は差し込みを `*` にした型）。"""
    out, skip = [], set()
    for n in ast.walk(node):
        if id(n) in skip:
            continue
        if isinstance(n, ast.JoinedStr):
            skip |= {id(x) for x in ast.walk(n)}
            s = "".join(v.value if isinstance(v, ast.Constant) else "*" for v in n.values)
            out.append(s.rsplit("/", 1)[-1])
        else:
            s = _const_str(n, consts)
            if s is not None:
                out.append(s.rsplit("/", 1)[-1])
    return [s for s in out if FILE_NAME.match(s)]


def _func_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_environ(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "environ") or (isinstance(node, ast.Name) and node.id == "environ")


class _Scan(ast.NodeVisitor):
    def __init__(self, facts: ModFacts, settings: dict[str, dict], readers: dict[str, str]):
        self.f = facts
        self.settings = settings
        self.readers = readers  # 修飾名 → 設定ファイルの並び
        self.by_func: dict[str, list[str]] = {}
        for qual, files in readers.items():
            if ":" in qual:
                self.by_func.setdefault(qual.rsplit(":", 1)[1], []).extend(files)
        self.func = "<module>"
        self.sub_of: dict[str, str] = {}
        self.decl_vars: dict[str, str] = {}

    def visit_FunctionDef(self, node):
        prev, prev_vars = self.func, self.decl_vars
        self.func, self.decl_vars = node.name, {}
        self._opens(node)
        self.generic_visit(node)
        self.func, self.decl_vars = prev, prev_vars

    visit_AsyncFunctionDef = visit_FunctionDef

    def _opens(self, node):
        """関数の中に `".ndf"` と設定ファイルの名前の両方の文字列があれば、その設定を開くとみなす。"""
        strs, skip = set(), set()
        for n in ast.walk(node):
            if isinstance(n, ast.JoinedStr) or (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)):
                skip |= {id(x) for x in ast.walk(n)}
            if id(n) in skip:
                continue
            s = _const_str(n, self.f.constants)
            if s is not None:
                strs.add(s)
        for name in self.settings:
            if (".ndf" in strs and name in strs) or f".ndf/{name}" in strs:
                if name not in self.readers.get(self.func_qual(node.name), ()):
                    self.f.setting_opens.setdefault((name, node.name), node.lineno)

    def func_qual(self, name: str) -> str:
        return f"{self.f.rel}:{name}"

    def visit_Assign(self, node):
        reader = self._reader_call(node.value)
        if reader:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Tuple) and tgt.elts and isinstance(tgt.elts[0], ast.Name):
                self.decl_vars[tgt.elts[0].id] = reader
            elif isinstance(tgt, ast.Name):
                self.decl_vars[tgt.id] = reader
        if isinstance(node.value, ast.Call) and _func_name(node.value.func) == "add_parser":
            name = _const_str(node.value.args[0], self.f.constants) if node.value.args else None
            if name and isinstance(node.targets[0], ast.Name):
                self.sub_of[node.targets[0].id] = name
        self.generic_visit(node)

    def _reader_call(self, node) -> str | None:
        if isinstance(node, ast.Subscript):
            node = node.value
        if not isinstance(node, ast.Call):
            return None
        files = self.by_func.get(_func_name(node.func) or "", [])
        if len(files) == 1:
            return files[0]
        for a in node.args:
            for n in ast.walk(a):
                s = _const_str(n, self.f.constants)
                if s and s.rsplit("/", 1)[-1] in files:
                    return s.rsplit("/", 1)[-1]
                if isinstance(n, ast.Attribute) and n.attr in ("SUPERVISE_DECL", "WORKTREE_DECL"):
                    return {"SUPERVISE_DECL": "supervise.json", "WORKTREE_DECL": "worktree.json"}[n.attr]
        return None

    def _key_read(self, recv, key_node, line):
        file = self.decl_vars.get(recv.id) if isinstance(recv, ast.Name) else self._reader_call(recv)
        key = _const_str(key_node, self.f.constants)
        if file and key:
            self.f.setting_keys.setdefault((file, key), line)

    def visit_Subscript(self, node):
        if _is_environ(node.value) and isinstance(node.ctx, ast.Load):
            self._env(node.slice, node.lineno)
        elif isinstance(node.ctx, ast.Load):
            self._key_read(node.value, node.slice, node.lineno)
        self.generic_visit(node)

    def visit_Compare(self, node):
        if len(node.ops) == 1 and isinstance(node.ops[0], (ast.In, ast.NotIn)) and _is_environ(node.comparators[0]):
            self._env(node.left, node.lineno)
        self.generic_visit(node)

    def visit_For(self, node):
        if _is_environ(node.iter) or (isinstance(node.iter, ast.Call) and _is_environ(getattr(node.iter.func, "value", None))):
            self.f.env_dynamic.add(self.func)
        self.generic_visit(node)

    def _env(self, arg, line):
        name = _const_str(arg, self.f.constants)
        if name is None:
            self.f.env_dynamic.add(self.func)
        else:
            self.f.env.setdefault(name, line)

    def visit_Call(self, node):
        fn = _func_name(node.func)
        recv = getattr(node.func, "value", None)
        self._env_or_key_call(node, fn, recv)
        self._argparse_call(node, fn, recv)
        self._write_call(node, fn, recv)
        self._exit_call(node, fn, recv)
        self._stdout_call(fn, recv)
        self.generic_visit(node)

    def _env_or_key_call(self, node, fn, recv):
        """環境変数の読み（`os.environ.get`・`os.getenv`）か、設定のキーの読み（`.get`）。"""
        if fn == "get" and recv is not None and _is_environ(recv) and node.args:
            self._env(node.args[0], node.lineno)
        elif fn == "getenv" and node.args:
            self._env(node.args[0], node.lineno)
        elif fn == "get" and node.args and recv is not None:
            self._key_read(recv, node.args[0], node.lineno)

    def _argparse_call(self, node, fn, recv):
        """`add_argument` の引数（副命令ごとのオプションか位置引数）。"""
        if fn != "add_argument":
            return
        self.f.has_parser = True
        sub = self.sub_of.get(recv.id, "") if isinstance(recv, ast.Name) else ""
        strs = [s for s in (_const_str(a, self.f.constants) for a in node.args) if s]
        opts = [s for s in strs if s.startswith("-")]
        if opts:
            self.f.options |= {(sub, o) for o in opts}
        elif strs:
            self.f.positionals.add((sub, strs[0]))

    def _write_call(self, node, fn, recv):
        """ファイルの書き（書きモードの `open`・`write_text`・`write_bytes`・JSON の書き手）。"""
        if fn == "open" and isinstance(node.func, ast.Name) and node.args:
            mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
            m = _const_str(mode, {}) if mode is not None else None
            if m and any(c in m for c in "wax"):
                self._write(node.args[0], node.lineno)
        elif fn in ("write_text", "write_bytes") and recv is not None:
            self._write(recv, node.lineno)
        elif fn in JSONIO_WRITERS and node.args:
            self._write(node.args[0], node.lineno)

    def _exit_call(self, node, fn, recv):
        """終了コード（`sys.exit`・`SystemExit`・`StepError` の第 2 引数）。"""
        if fn in ("exit", "SystemExit") and node.args and (fn == "SystemExit" or getattr(recv, "id", "") == "sys"):
            self._exit(node.args[0])
        if fn == "StepError" and len(node.args) > 1:
            self._exit(node.args[1])

    def _stdout_call(self, fn, recv):
        """標準出力の形（text → json → step-result の順に強くなる）。"""
        if fn == "print" and self.f.stdout == "none":
            self.f.stdout = "text"
        if fn == "dumps" and getattr(recv, "id", "") == "json":
            self.f.stdout = "json" if self.f.stdout in ("none", "text") else self.f.stdout
        if fn in ("emit", "emit_result") and self.f.stdout != "step-result":
            self.f.stdout = "step-result"

    def visit_Return(self, node):
        if self.func == "main" or self.func.startswith("cmd_"):
            if node.value is not None:
                self._exit(node.value)
        self.generic_visit(node)

    def _exit(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
            self.f.exits.setdefault(str(node.value), "")
        elif isinstance(node, ast.Name) and isinstance(self.f.constants.get(node.id), int):
            self.f.exits.setdefault(str(self.f.constants[node.id]), node.id)

    def _write(self, node, line):
        names = _glob_of(node, self.f.constants)
        for n in names:
            if n not in self.settings:
                self.f.writes.setdefault(n, line)

    def visit_Attribute(self, node):
        if isinstance(node.value, ast.Name) and node.value.id == "sys":
            if node.attr == "argv":
                self.f.argv = True
            elif node.attr == "stdin":
                self.f.stdin = True
        self.generic_visit(node)


def _path_const(v: ast.AST, consts: dict) -> str | None:
    """`Path(".ndf") / "x.json"`・`Path(".ndf", "x.json")` のような定数のパス（`/` でつなぐ）。"""
    if isinstance(v, ast.Call) and _func_name(v.func) in ("Path", "PurePath"):
        parts = [_const_str(a, consts) for a in v.args]
        return "/".join(parts) if parts and all(parts) else None
    if isinstance(v, ast.BinOp) and isinstance(v.op, ast.Div):
        left = _const_str(v.left, consts) or _path_const(v.left, consts)
        right = _const_str(v.right, consts)
        return f"{left}/{right}" if left and right else None
    return None


def _module_constants(tree: ast.Module) -> dict[str, object]:
    out: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            v = node.value
            if isinstance(v, ast.Constant) and isinstance(v.value, (str, int)) and not isinstance(v.value, bool):
                out[node.targets[0].id] = v.value
            else:
                s = _path_const(v, out)
                if s:
                    out[node.targets[0].id] = s
    return out


def is_runnable(tree: ast.Module) -> bool:
    """最上位に `if __name__ == "__main__":` を持つか（前提 1）。"""
    for n in tree.body:
        if isinstance(n, ast.If) and isinstance(n.test, ast.Compare):
            t = n.test
            if (
                isinstance(t.left, ast.Name)
                and t.left.id == "__name__"
                and len(t.comparators) == 1
                and isinstance(t.comparators[0], ast.Constant)
                and t.comparators[0].value == "__main__"
            ):
                return True
    return False


class PyExtractor:
    """モジュールの事実をファイルごとに 1 回だけ拾い、エントリポイントから import でたどって合わせる。"""

    def __init__(self, root: Path, settings: dict[str, dict]):
        self.root = root
        self.settings = settings
        self.readers: dict[str, list[str]] = {}
        for name, s in settings.items():
            for q in s.get("readers", []):
                self.readers.setdefault(q, []).append(name)
        self.cache: dict[str, ModFacts] = {}

    def module(self, rel: str) -> ModFacts:
        if rel in self.cache:
            return self.cache[rel]
        text = (self.root / rel).read_text(errors="ignore")
        f = ModFacts(rel)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            f.unparsed = True
            self.cache[rel] = f
            return f
        f.runnable = is_runnable(tree)
        f.constants = _module_constants(tree)
        roots = (Path(rel).parent.as_posix() + "/", *IMPORT_ROOTS)
        f.imports = {m for m in (module_file(self.root, n, roots) for n in imported_tree(rel, tree, roots)) if m}
        doc = ast.get_docstring(tree) or ""
        f.summary = doc.strip().split("\n")[0] if doc else ""
        _Scan(f, self.settings, self.readers).visit(tree)
        self.cache[rel] = f
        return f

    def reach(self, entry: str) -> list[ModFacts]:
        todo, seen, out = [entry], set(), []
        while todo:
            rel = todo.pop()
            if rel in seen:
                continue
            seen.add(rel)
            m = self.module(rel)
            out.append(m)
            todo += sorted(m.imports - seen)
        return out

    def facts(self, entry: str) -> dict:
        """エントリポイントの事実（契約の項目と同じ語で）。"""
        mods = self.reach(entry)
        own = [m for m in mods if m.rel == entry or not m.runnable]
        args_mods = [m for m in own if m.rel == entry or not m.rel.startswith(LIB + "lib/") or m.has_parser]
        top = mods[0]
        opts = set().union(*(m.options for m in args_mods))
        pos = set().union(*(m.positionals for m in args_mods))
        out = {
            "summary": top.summary,
            "argv_manual": top.argv and not opts and not pos,
            "options": opts,
            "positionals": pos,
            "env": {},
            "env_dynamic": {},
            "writes": {},
            "setting_keys": {},
            "setting_opens": {},
            "exits": dict(top.exits),
            "stdin": top.stdin,
            "stdout": top.stdout,
            "unparsed": [m.rel for m in mods if m.unparsed],
        }
        for m in mods:
            for k, line in m.env.items():
                out["env"].setdefault(k, f"{m.rel}:{line}")
            for fn in m.env_dynamic:
                out["env_dynamic"].setdefault((m.rel, fn), m.rel)
            for k, line in m.writes.items():
                out["writes"].setdefault(k, f"{m.rel}:{line}")
            for k, line in m.setting_keys.items():
                out["setting_keys"].setdefault(k, f"{m.rel}:{line}")
            for k, line in m.setting_opens.items():
                out["setting_opens"].setdefault((m.rel, *k), f"{m.rel}:{line}")
        for m in own[1:]:
            if m.rel.startswith(str(Path(entry).with_suffix("")).replace("-", "_") + "_lib/"):
                for code, name in m.exits.items():
                    out["exits"].setdefault(code, name)
        if top.has_parser or opts or pos:
            out["exits"].setdefault("2", "")
        out["exits"].setdefault("0", "")
        return out
