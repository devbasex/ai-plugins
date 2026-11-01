"""supervise.py の `new sprint` を起動する引数列の共通の土台。

既定の名前・課題・版数を持ち、テストごとの差分（名前・課題・設計・版数と、末尾に足すフラグ）だけを受ける。
"""

from __future__ import annotations

from pathlib import Path


def new_sprint_args(
    worktree: Path | str,
    out: Path | str,
    *extra: str,
    name: str = "m",
    issue: tuple[str, ...] = ("1",),
    design: tuple[str, ...] = (),
    version: str | None = "10.18.0-dev.1",
) -> list[str]:
    args = ["new", "sprint", "--name", name, "--worktree", str(worktree), "--issue", *issue]
    if design:
        args += ["--design", *design]
    if version is not None:
        args += ["--version", version]
    return [*args, "--out", str(out), *extra]
