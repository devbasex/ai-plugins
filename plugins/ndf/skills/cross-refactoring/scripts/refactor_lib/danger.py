"""危険の印（D1〜D5）を git の事実から判定する（#933 の「危険の印」）。

危険の印は、項目ごとの限ったテストで覆えない変更を表す。印が 1 つでも立てば、
検証の中で全体のテストを 1 度だけ走らせる。

**判定は git だけを情報源にする。** 実装担当の申告で検証を減らさない。例外は D5 で、
担当の `risk` は印を**立てる側にだけ**使う（ここでは真偽を受け取るだけ）。

**言語ごとの参照の解決は持たない**（#933 決定 20）。`refactor.py` は標準ライブラリ
だけで、対象の言語を問わない。D3 と D4 は名前の一致で判定する。D4 は覆っていると
示せないときに立て、D3 の見落とし（動的な読み込み・文字列で組み立てた import）は
残る危険として受け入れる。見落としは最終ゲートが拾う。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
from typing import Any, Optional

from . import info
from .gitfacts import CODE_EXTENSIONS, is_test_path
from .paths import git_out
from .scope import round_test_roots

# 再 export を持ちうるパッケージの入口。中身を探さずに D3 を立てる。入口の名前で
# 参照する側は、入口のパス（`pkg/__init__`）ではなくパッケージの名前で読むため、
# 名前の一致では当たらない。
ENTRY_NAMES: frozenset[str] = frozenset({
    "__init__.py",
    "index.js", "index.ts", "index.jsx", "index.tsx", "index.mjs", "index.cjs",
    "index.d.ts",
    "mod.rs", "lib.rs",
})

# D3 で探さない文書のファイル。一般的な語幹は文書に必ず当たり、ほぼすべての項目で
# D3 が立つ。`CODE_EXTENSIONS` で数える前に git の側でも除いて、読む量を減らす。
_DOC_EXCLUDES = (
    ":(exclude)*.md", ":(exclude)*.rst", ":(exclude)*.txt",
    ":(exclude)docs/**", ":(exclude)docs",
)

# POSIX 拡張正規表現で特別な意味を持つ文字。`re.escape` は `-` や `#` も `\` 付きに
# するが、`\-` の扱いは正規表現の実装で違うため、ここでは要るものだけを逃がす。
_ERE_SPECIAL = frozenset(".[]{}()*+?^$|\\")


def _ere_escape(text: str) -> str:
    return "".join("\\" + ch if ch in _ERE_SPECIAL else ch for ch in text)


def d1(files: list[str], item_path: str, tests: list[str]) -> bool:
    """項目のコミットが、項目の `path` と `tests[]` 以外のファイルを触ったか。"""
    allowed = {os.path.normpath(item_path)} | {os.path.normpath(t) for t in tests or []}
    return any(os.path.normpath(f) not in allowed for f in files)


def d2(work: str, shas: list[str]) -> bool:
    """項目のコミットがファイルを消したか、名前を変えたか。

    `-M` で名前の変更を `R<類似度>` として読む。付けないと消去と追加に割れ、
    変更が `D` に紛れても判定は同じだが、報告で名前の変更と読めなくなる。
    """
    for sha in shas:
        out = git_out(work, ["diff-tree", "--no-commit-id", "-r", "--name-status", "-M", sha]) or ""
        if any(line[:1] in ("D", "R") for line in out.splitlines()):
            return True
    return False


def _module_patterns(path: str, symbol: str) -> list[str]:
    """触ったファイルを参照する形の正規表現（`git grep -E`）。

    拡張子を落としたリポジトリ相対パスの末尾 2 区切り（親と語幹）から作る。
    語幹だけで探すと `plan` のような一般的な名前が無関係の箇所に当たる。
    リポジトリの直下のファイル（親が無い）は、行頭の `import` / `from` の形に限る。
    `import plan` を行の途中で許すと `from x import plan` にも当たる（実測）。
    """
    pure = pathlib.PurePosixPath(path)
    stem = pure.stem
    parent = pure.parent.name
    s = _ere_escape(stem)
    if parent:
        p = _ere_escape(parent)
        patterns = [f"{p}/{s}", f"{p}\\.{s}", f"{p} import .*\\b{s}\\b"]
    else:
        patterns = [f"^[[:space:]]*import {s}\\b", f"^[[:space:]]*from {s}\\b"]
    if symbol and "." in symbol:
        patterns.append(_ere_escape(symbol))
    return patterns


def _grep(work: str, pattern: str, excludes: list[str]) -> Optional[list[str]]:
    """`git grep -lE` の当たったファイル。当たり無し（終了コード 1）は空、失敗は `None`。

    終了コード 2 以上（正規表現の誤り・リポジトリでない）を当たり無しと読むと、
    判定できないまま D3 を立てずに通す。
    """
    r = subprocess.run(
        ["git", "grep", "-lE", "-e", pattern, "--", ".", *_DOC_EXCLUDES, *excludes],
        cwd=work, capture_output=True, text=True,
    )
    if r.returncode == 1:
        return []
    if r.returncode != 0:
        return None
    return [line for line in r.stdout.splitlines() if line]


def d3(work: str, files: list[str], symbol: str, scope: list[str]) -> list[str]:
    """触った本番のファイルが `--scope` の外のコードから参照されているか。当たった語を返す。

    探す先は `--scope` の外の**コードのファイルだけ**である。触ったファイルそのものも
    除く（自分の中の文字列は外からの参照でない）。パッケージの入口は探さずに
    `entry:<path>` を返す。`git grep` が失敗した語は `grep-failed:<語>` として返し、
    判定できないので立てる側へ倒す。
    """
    hits: list[str] = []
    excludes = [f":(exclude){os.path.normpath(s)}" for s in scope or [] if str(s).strip()]
    for path in files:
        if is_test_path(path):
            continue
        name = pathlib.PurePosixPath(path).name
        if name in ENTRY_NAMES:
            hits.append(f"entry:{path}")
            continue
        own = [f":(exclude){path}"]
        for pattern in _module_patterns(path, symbol):
            found = _grep(work, pattern, excludes + own)
            if found is None:
                info(f"⚠ git grep に失敗しました（{pattern}）。参照の有無を判定できないため D3 を立てます")
                hits.append(f"grep-failed:{pattern}")
                continue
            if any(pathlib.PurePosixPath(f).suffix.lower() in CODE_EXTENSIONS for f in found):
                if pattern not in hits:
                    hits.append(pattern)
    return hits


def d4(work: str, test_files: Optional[list[str]], touched: str, symbol: str) -> bool:
    """限ったテストが触った本番のファイルを覆うと**示せない**か。

    限ったテストのファイルのどれかに、拡張子を除いたファイル名か `symbol` の名前
    （修飾名の全体と末尾）が現れれば覆うとみなす。ファイルを挙げられない（`None`・空）
    ときは示せないとして立てる。
    """
    if not test_files:
        return True
    names = {pathlib.PurePosixPath(touched).stem}
    if symbol:
        names.add(symbol)
        names.add(symbol.rsplit(".", 1)[-1])
    names.discard("")
    for rel in test_files:
        try:
            text = (pathlib.Path(work) / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(name in text for name in names):
            return False
    return True


def limited_test_files_from_targets(test_targets: list[str]) -> list[str]:
    """`test_targets` のパスの部分（`::` より前）を、重複なく現れた順に返す。"""
    files: list[str] = []
    for target in test_targets or []:
        path = str(target).split("::", 1)[0]
        if path and path not in files:
            files.append(path)
    return files


def limited_test_files_from_round_test(round_test: str, work: str) -> Optional[list[str]]:
    """`--round-test` の対象の語の配下の追跡されたファイル。対象の語が無ければ `None`（決定 21）。

    `make test` やラッパーは走ったファイルを挙げられないため、呼ぶ側は D4 を立てる。
    """
    roots = round_test_roots(round_test, work)
    if not roots:
        return None
    files: list[str] = []
    for root in roots:
        out = git_out(work, ["ls-files", "-z", "--", root], strip=False) or ""
        for path in out.split("\0"):
            if path and path not in files:
                files.append(path)
    return files


def item_flags(
    work: str,
    item: dict[str, Any],
    shas: list[str],
    files: list[str],
    scope: list[str],
    test_files: Optional[list[str]],
    d5: bool,
) -> dict[str, list[str]]:
    """項目に立った印（`D1`〜`D5`）と、D3 で当たった語を返す。

    D4 は触った本番のファイル（テストでない）ごとに見て、1 つでも示せなければ立てる。
    """
    flags: list[str] = []
    symbol = str(item.get("symbol") or "")
    if d1(files, str(item.get("path") or ""), list(item.get("tests") or [])):
        flags.append("D1")
    if d2(work, shas):
        flags.append("D2")
    hits = d3(work, files, symbol, scope)
    if hits:
        flags.append("D3")
    touched = [f for f in files if not is_test_path(f)]
    if any(d4(work, test_files, path, symbol) for path in touched):
        flags.append("D4")
    if d5:
        flags.append("D5")
    return {"flags": flags, "hits": hits}
