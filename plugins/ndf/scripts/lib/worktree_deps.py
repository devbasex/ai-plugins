"""依存の用意（`worktree-deps.sh prepare`）を Python の worktree の作成箇所から呼ぶ包み（#1337）。

宣言の中身の判定（壊れているか・何を用意するか）は `worktree-deps.sh` だけが持つ。ここが持つのは、
共有の宣言（メインディレクトリの `.ndf/worktree.json`）に `deps` があるかを自分で読むことと、
終了コードと報告の受け渡しだけである。

**宣言が無ければプロセスを 1 つも起こさない。** メインディレクトリも、worktree の `.git` ファイルと
git の管理ディレクトリの `commondir` を読んで決める（git を起動しない）。

    res = worktree_deps.prepare(worktree, if_unprepared=True)
    if not res.ok:
        die(res.message)

CLI（シェルの作成箇所から呼ぶ）:

    python3 worktree_deps.py prepare <worktree> [--if-unprepared]

終了コードは `worktree-deps.sh` と同じ（0 済み・宣言なし / 1 失敗 / 3 宣言が壊れている）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DECLARATION_FILE = ".ndf/worktree.json"
SCRIPT = Path(__file__).resolve().parent.parent / "worktree-deps.sh"


@dataclass(frozen=True)
class Result:
    """用意の結果。`code` は `worktree-deps.sh` の終了コード、`message` はその標準エラー。"""

    code: int
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.code == 0


def main_dir_of(worktree: str | Path) -> Path | None:
    """worktree のメインディレクトリを、git を起動せずに返す。分からなければ None。

    リンクされた worktree の `.git` はファイルで、`gitdir: <管理ディレクトリ>` を持つ。
    管理ディレクトリの `commondir` が共通の git ディレクトリを指し、その親がメインディレクトリである。
    `.git` がディレクトリなら、そのディレクトリ自身がメインディレクトリである。
    """
    wt = Path(worktree)
    dot = wt / ".git"
    if dot.is_dir():
        return wt.resolve()
    try:
        line = dot.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = (wt / line[len("gitdir:") :].strip()).resolve()
    try:
        common = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve()
    except OSError:
        return None
    return common.parent


def declared(main_dir: str | Path) -> bool:
    """共有の宣言に `deps` があるか。JSON として読めないときも True（判定と報告は `worktree-deps.sh`）。

    `lib/worktree-declaration.sh` の `wt_deps_declared` と同じ基準である。
    """
    path = Path(main_dir) / DECLARATION_FILE
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    if not isinstance(data, dict):
        return False
    return data.get("deps") not in (None, {})


def prepare(worktree: str | Path, *, main_dir: str | Path | None = None, if_unprepared: bool = False) -> Result:
    """宣言があれば `worktree-deps.sh prepare` を打つ。宣言が無ければ何も起こさず `Result(0)` を返す。

    `if_unprepared` は使い回す worktree で使う（用意の印があれば手順を 1 つも走らせない）。
    """
    main = Path(main_dir) if main_dir else main_dir_of(worktree)
    # メインディレクトリそのものは用意の対象にしない（`run` をメインディレクトリで走らせない）
    if main is None or main.resolve() == Path(worktree).resolve() or not declared(main):
        return Result(0)
    cmd = ["bash", str(SCRIPT), "prepare", str(worktree)] + (["--if-unprepared"] if if_unprepared else [])
    p = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return Result(p.returncode, p.stderr.strip())


def prepare_reporting(worktree: str | Path, *, main_dir: str | Path | None = None, if_unprepared: bool = False) -> str | None:
    """`prepare` して報告（済み・失敗の行と末尾）を標準エラーへ出す。失敗なら先頭の 1 行を返す（ほかは None）。

    worktree は消さない。呼び出し側は返った行を誤りの文に入れて止まる。
    """
    res = prepare(worktree, main_dir=main_dir, if_unprepared=if_unprepared)
    if res.message:
        print(res.message, file=sys.stderr)
    if res.ok:
        return None
    return res.message.splitlines()[0] if res.message else f"依存の用意: 終了コード {res.code}"


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--if-unprepared"]
    if len(args) != 2 or args[0] != "prepare":
        print("使い方: worktree_deps.py prepare <worktree> [--if-unprepared]", file=sys.stderr)
        return 1
    res = prepare(args[1], if_unprepared="--if-unprepared" in argv)
    if res.message:
        print(res.message, file=sys.stderr)
    return res.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
