"""テストとプロダクションコードのパスの判定。git の出力だけで決める。"""
from __future__ import annotations

import pathlib
import subprocess
from typing import Optional

from .paths import git_out


# テストの置き場所。現状固定テストが先行しているかの判定に使う。
TEST_PATH_MARKERS = ("/test/", "/tests/", "/spec/", "/specs/", "__tests__/")
TEST_NAME_MARKERS = (".test.", ".spec.", "_test.", "_spec.", "test_", "spec_")

# 本番コードの拡張子。構造改善を飛ばしてよいかの判定（`assess`）に使う（#494）。
CODE_EXTENSIONS = frozenset({
    ".py", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".php",
    ".rb", ".go", ".rs", ".java", ".kt", ".swift", ".c", ".h", ".cc", ".cpp", ".cs",
})


def is_test_path(path: str) -> bool:
    """テストの置き場所か。判定は `commit_touches_tests` と同じ基準で行う。"""
    lowered = f"/{path.lower()}"
    name = lowered.rsplit("/", 1)[-1]
    return (any(m in lowered for m in TEST_PATH_MARKERS)
            or any(m in name for m in TEST_NAME_MARKERS))


def _has_shebang(work: str, rev: str, path: str) -> bool:
    """`<rev>:<path>` の 1 行目が `#!` で始まるか。その版に無ければ False。"""
    r = subprocess.run(["git", "cat-file", "blob", f"{rev}:{path}"], cwd=work,
                       capture_output=True)
    return r.returncode == 0 and r.stdout.startswith(b"#!")


def _is_code_path(work: str, base: str, path: str) -> bool:
    """本番コードのファイルか。拡張子で判定し、拡張子の無いファイルは shebang で判定する。

    拡張子の無いスクリプト（`containers/base/tmux-session` の `#!/bin/sh` など）を
    数えないと、シェルスクリプトを主に持つリポジトリで構造改善が常に飛ばされる（#1134）。
    削除したファイルは HEAD に無いので、起点の版で判定する。
    """
    suffix = pathlib.PurePosixPath(path).suffix.lower()
    if suffix:
        return suffix in CODE_EXTENSIONS
    return _has_shebang(work, "HEAD", path) or _has_shebang(work, base, path)


def production_code_changes(work: str, base: str) -> Optional[list[tuple[str, int]]]:
    """`<base>...HEAD` の差分のうち、本番コードのファイルと変更行（追加 + 削除）を返す。

    `<base>` を解けないときは `None`。**`--no-renames` を付ける。** 付けないと rename が
    `dir/{old.py => new.py}` の形になり、拡張子で判定できない。付ければ旧パスの削除と
    新パスの追加に分かれ、両方のパスで判定できる。拡張子の無いファイルは shebang で
    判定する（`_is_code_path`）。`-z` は、ASCII 以外を含むパスが
    引用符付きで出て拡張子が読めなくなるのを防ぐ。
    """
    out = git_out(work, ["diff", "--numstat", "-z", "--no-renames", f"{base}...HEAD"])
    if out is None:
        return None
    changes: list[tuple[str, int]] = []
    for line in out.split("\0"):
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        path = parts[2]
        if is_test_path(path) or not _is_code_path(work, base, path):
            continue
        # バイナリは `-` になるので数えない
        changes.append((path, sum(int(n) for n in parts[:2] if n.isdigit())))
    return changes
