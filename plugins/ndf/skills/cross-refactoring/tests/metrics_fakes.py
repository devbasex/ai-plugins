"""指標の測定のテストで使う偽の測定ツール（#1319）。

実物のツール（Ruff・complexipy・lizard・symilar・jscpd）の取得（通信）には頼らない。出力の形は
2026-09-26（lizard）と 2026-09-27（Ruff・complexipy・symilar・jscpd）に実物で確かめた形に揃える。
1 つの Python の台本を、ツールの名前（と uvx / npx）で置く。起動のたびに `FAKE_LOG` へ
`{tool, argv, cwd}` を 1 行足す。`FAKE_<TOOL>` で振る舞いを変える（`fail` / `garbage` / `slow`）。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any, Iterable

FAKE = r'''#!__PYTHON__
import ast, json, os, re, subprocess, sys, time
name, args = os.path.basename(sys.argv[0]), sys.argv[1:]
if name == "uvx":
    assert args[0] == "--from"
    name, args = args[2], args[3:]
elif name == "npx":
    assert args[0] == "-y"
    name, args = "jscpd", args[2:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"tool": name, "argv": sys.argv[1:], "cwd": os.getcwd()}) + "\n")
mode = os.environ.get("FAKE_" + name.upper(), "")
if "--version" in args:
    print(f"{name} 9.9.9")
    sys.exit(0)
if mode == "fail":
    sys.stderr.write("boom\n")
    sys.exit(3)
if mode == "garbage":
    print("not json,")
    sys.exit(0)
if mode == "slow":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(os.environ["FAKE_PIDS"], "a") as f:
        f.write(f"{os.getpid()}\n{child.pid}\n")
    time.sleep(60)
files = [a for a in args if re.search(r"\.(py|ts|go|sh|js)$", a)]

def functions(tree):
    out = []
    def walk(node, stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, stack + [child.name])
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append((stack, child))
                walk(child, stack + [child.name])
    walk(tree, [])
    return out

if name == "ruff":
    diags = []
    for path in files:
        full = os.path.abspath(path)
        try:
            tree = ast.parse(open(path).read())
        except SyntaxError:
            diags.append({"code": "invalid-syntax", "name": "invalid-syntax", "filename": full,
                          "location": {"row": 1, "column": 1}, "message": "Expected `)`"})
            continue
        for stack, fn in functions(tree):
            row = {"row": fn.lineno, "column": 5}
            diags.append({"code": "C901", "filename": full, "location": row,
                          "message": f"`{fn.name}` is too complex ({3 + len(fn.body)} > 0)"})
            diags.append({"code": "PLR0912", "filename": full, "location": row,
                          "message": "Too many branches (2 > 0)"})
    print(json.dumps(diags))
elif name == "complexipy":
    output = args[args.index("--output") + 1]
    entries, rc = [], 0
    for path in files:
        try:
            tree = ast.parse(open(path).read())
        except SyntaxError:
            print(f"error: Failed to process {path}")
            rc = 1
            continue
        # 入れ子の関数と、入れ子のクラスのメソッドは出さない（実物と同じ）
        top = [([], n) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        top += [([c.name], n) for c in tree.body if isinstance(c, ast.ClassDef)
                for n in c.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for stack, fn in top:
            label = "::".join(stack + [fn.name])
            entries.append({"complexity": 10 * len(fn.body), "file_name": os.path.basename(path),
                            "function_name": label, "path": path, "refactor_plans": []})
    with open(output, "w") as f:
        json.dump(entries, f)
    sys.exit(rc)
elif name == "lizard":
    for path in files:
        for i, line in enumerate(open(path).read().splitlines(), start=1):
            m = re.search(r"(?:function|func)\s+(\w+)", line)
            if m:
                n = m.group(1)
                print(f'5,{i + 1},30,1,6,"{n}@{i}-{i + 5}@{path}","{path}","{n}","{n}()",{i},{i + 5}')
elif name == "symilar":
    if os.environ.get("FAKE_SYMILAR_CLONE") and len(files) >= 2:
        print(f"9 similar lines in 2 files\n=={files[0]}:[0:9]\n=={files[1]}:[4:13]\n   x = 1\n")
    print("TOTAL lines=100 duplicates=9 percent=9.00")
elif name == "jscpd":
    output = args[args.index("--output") + 1]
    dups = []
    if len(files) >= 2:
        dups.append({"format": "typescript", "lines": 9,
                     "firstFile": {"name": files[0], "start": 1, "end": 9},
                     "secondFile": {"name": files[1], "start": 3, "end": 11}})
    total = {"lines": 40, "sources": len(files), "clones": len(dups), "duplicatedLines": 9 * len(dups)}
    with open(os.path.join(output, "jscpd-report.json"), "w") as f:
        json.dump({"statistics": {"total": total}, "duplicates": dups}, f)
'''

ALL_TOOLS = ("ruff", "complexipy", "lizard", "symilar", "jscpd")


def install(bin_dir: pathlib.Path, names: Iterable[str] = ALL_TOOLS) -> pathlib.Path:
    """偽のツールを `bin_dir` に置いて返す。"""
    bin_dir.mkdir(parents=True, exist_ok=True)
    body = FAKE.replace("__PYTHON__", sys.executable)
    for name in names:
        target = bin_dir / name
        target.write_text(body, encoding="utf-8")
        target.chmod(0o755)
    return bin_dir


_WHICH = shutil.which


def which_in(bin_dir: pathlib.Path):
    """`bin_dir` だけを探す `which`。利用者の環境の uvx / npx を拾わない。"""
    return lambda name, *a, **k: _WHICH(name, path=str(bin_dir))


def calls(log: pathlib.Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


def git(*args: str, cwd: Any) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout


def make_repo(work: pathlib.Path, files: dict[str, str], untracked: dict[str, str] = None) -> None:
    """ファイルを置いて 1 回コミットする。`untracked` は追跡しないまま置く。"""
    work.mkdir(parents=True, exist_ok=True)
    git("init", "-q", cwd=work)
    git("config", "user.email", "t@example.com", cwd=work)
    git("config", "user.name", "t", cwd=work)
    for rel, text in files.items():
        p = work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (work / ".gitignore").write_text(".cross_refactoring/\n", encoding="utf-8")
    git("add", "-A", cwd=work)
    git("commit", "-qm", "init", cwd=work)
    for rel, text in (untracked or {}).items():
        p = work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def env_for(monkeypatch: Any, tmp_path: pathlib.Path, bin_dir: pathlib.Path) -> pathlib.Path:
    """偽のツールが起動されるよう PATH の先頭へ置き、起動の記録の置き場を返す。"""
    log = tmp_path / "fake-log.jsonl"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_PIDS", str(tmp_path / "fake-pids"))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return log
