"""スクリプトの `main(argv)` を、テストのプロセスの中で呼ぶ（#2291 の決定 3 の (2)）。

スクリプトを子のプロセスで起動すると、起動と import だけで 1 回 0.2〜0.4 秒かかる。CLI の配線（引数・終了コード・
結果 JSON）は代表の数件の起動で確かめ、分岐はこの関数で確かめる。返す値は `subprocess.run` と同じ形
（`returncode`・`stdout`・`stderr`）で、起動を置き換えても読み手を変えずに済む。

作業ディレクトリ・環境変数・`sys.argv`・標準入出力は呼ぶ間だけ差し替え、戻す。モジュールは 1 度だけ読み込む。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
from pathlib import Path

_modules: dict[str, object] = {}


def load(script: Path | str):
    key = str(Path(script).resolve())
    if key not in _modules:
        name = "inproc_" + Path(key).stem.replace("-", "_")
        spec = importlib.util.spec_from_file_location(name, key)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _modules[key] = mod
    return _modules[key]


def _code(e: SystemExit, err: io.StringIO) -> int:
    if e.code is None:
        return 0
    if isinstance(e.code, int):
        return e.code
    err.write(f"{e.code}\n")
    return 1


def run(script: Path | str, *args: str, cwd: Path | str | None = None, env: dict | None = None, input: str | None = None):
    """`[python, script, *args]` を子のプロセスで起動したのと同じ形の結果を返す。"""
    mod = load(script)
    argv = [str(a) for a in args]
    out, err = io.StringIO(), io.StringIO()
    saved = (dict(os.environ), sys.argv, sys.stdin)
    try:
        if env is not None:
            os.environ.clear()
            os.environ.update({k: str(v) for k, v in env.items()})
        sys.argv = [str(script), *argv]
        sys.stdin = io.StringIO(input or "")
        with contextlib.chdir(cwd or os.getcwd()), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                r = mod.main(argv)
                code = r if isinstance(r, int) else 0
            except SystemExit as e:
                code = _code(e, err)
    finally:
        os.environ.clear()
        os.environ.update(saved[0])
        sys.argv, sys.stdin = saved[1], saved[2]
    return subprocess.CompletedProcess([sys.executable, str(script), *argv], code, out.getvalue(), err.getvalue())
