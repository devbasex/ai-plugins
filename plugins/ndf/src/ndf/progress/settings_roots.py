"""設定を読むリポジトリの根を、1 回の判定の中で作業場所ごとに 1 回だけ求める（#4513）。

工程の条件の判定（`ndf.progress.command`）は、ブランチ戦略の設定と `docs`・`test` の設定を同じ作業場所の根から読む。
"""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator
from pathlib import Path
from typing import Optional

from ndf.adapters.git import Git

# `roots_once` の中で求めた根: 作業場所 → 根。範囲の外では None で、覚えない
_ROOTS: contextvars.ContextVar[Optional[dict[Path, Path]]] = contextvars.ContextVar("roots", default=None)


@contextlib.contextmanager
def roots_once() -> Iterator[None]:
    """この中の `settings_root` は、同じ作業場所の根を 1 回だけ求める（外の範囲の中なら、外で求めた根を引き継ぐ。#4513）。

    工程の条件の判定は、ブランチ戦略の設定と `docs`・`test` の設定を同じ作業場所の根から読む。1 回の判定の中で
    `git rev-parse --show-toplevel` を読む設定の数だけ打たない。
    """
    if _ROOTS.get() is not None:
        yield
        return
    token = _ROOTS.set({})
    try:
        yield
    finally:
        _ROOTS.reset(token)


def settings_root(cwd: Path) -> Path:
    """設定を読むリポジトリの根（`Git.toplevel`）。`GitError` はそのまま上げる。

    `cwd` に `.ndf/project.json` があっても根とはみなさない。サブディレクトリに別の設定を置いたリポジトリで、そこから打っても
    リポジトリの根の設定を読む（#4513）。`roots_once` の中では、同じ作業場所で先に求めた根を使う（git を打ち直さない）。
    """
    held = _ROOTS.get()
    if held is None:
        return Git(cwd).toplevel()
    key = cwd.resolve()  # パスの解決は使い回す範囲の中だけで打つ（範囲の外では git の起動のほかに増やさない）
    if key not in held:
        held[key] = Git(cwd).toplevel()
    return held[key]
