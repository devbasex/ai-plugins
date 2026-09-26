"""子プロセスと git の起動・自分の終わり方（#1142 の L0）。標準ライブラリだけを import する。

失敗は `StepError(msg, code)` に揃える。`code` の既定 1 は `step_result.EXIT_VIOLATION` と同じ値で、
`step_result` は同じクラスを再エクスポートする（`step_result` がこのモジュールを import し、逆は無い）。

`subprocess.run` はモジュールの属性として呼ぶ。テストが `subprocess.run` を差し替えると、ここにも効く。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import NoReturn, Sequence


class StepError(Exception):
    """手順の失敗。status=stopped と code（既定 1）で終える。"""

    def __init__(self, msg, code=1):
        super().__init__(msg)
        self.code = code


def run(cmd: Sequence, cwd=None, check: bool = True, env=None, timeout: float | None = None,
        input: str | None = None) -> subprocess.CompletedProcess:
    """`cmd` を起動し、標準出力と標準エラーを文字列で受ける。`check` なら 0 以外を `StepError` にする。"""
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, input=input)
    if check and p.returncode != 0:
        raise StepError(f"{' '.join(map(str, cmd))} が終了コード {p.returncode}: {p.stderr.strip()[:500]}")
    return p


def git(root, *args, check: bool = True) -> subprocess.CompletedProcess:
    """`git -C <root> <args>`。"""
    return run(["git", "-C", str(root), *args], check=check)


def git_out(root, *args) -> str | None:
    """`git` の標準出力（前後の空白を落とす）。失敗や git が無いときは `None`。"""
    try:
        p = git(root, *args, check=False)
    except OSError:
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def git_root(arg=None) -> Path:
    """`arg` があればそのパス、無ければカレントの作業ツリーの根。作業ツリーでなければ `StepError`（2）。"""
    if arg:
        return Path(arg).resolve()
    p = run(["git", "rev-parse", "--show-toplevel"], check=False)
    if p.returncode != 0:
        raise StepError("カレントが git の作業ツリーではない（--root を渡す）", 2)
    return Path(p.stdout.strip())


def die(msg: str, code: int = 1) -> NoReturn:
    """`❌ <msg>` を標準エラーへ出して `code` で終える。"""
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(code)


def info(msg: str) -> None:
    """標準エラーへ 1 行出す（標準出力は結果のために空けておく）。"""
    print(msg, file=sys.stderr)
