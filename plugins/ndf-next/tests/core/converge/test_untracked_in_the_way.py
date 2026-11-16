"""現状固定: 同期の前に、origin が追跡するパスと重なる未追跡のファイルを見つける（`worktree._untracked_in_the_way`）。

重なるのは、同じパスのファイル・origin のディレクトリと同じパス・origin のファイルを親に持つパスの 3 つ。
"""

from __future__ import annotations

from ndf.core.converge import worktree


class _Git:
    """`ls-files --others` と `ls-tree` の出力だけを返す git の代わり。"""

    def __init__(self, untracked: list[str], tracked: list[str]) -> None:
        self.untracked = untracked
        self.tracked = tracked
        self.calls: list[tuple[str, ...]] = []

    def run(self, *args: str) -> str:
        self.calls.append(args)
        return "\0".join(self.untracked if args[0] == "ls-files" else self.tracked) + "\0"


def _in_the_way(untracked: list[str], tracked: list[str]) -> list[str]:
    return worktree._untracked_in_the_way(_Git(untracked, tracked), "origin/x")  # type: ignore[arg-type]


def test_untracked_file_on_a_tracked_file_clashes() -> None:
    assert _in_the_way(["a/b.txt", "c.txt"], ["a/b.txt"]) == ["a/b.txt"]


def test_untracked_file_on_a_tracked_directory_clashes() -> None:
    assert _in_the_way(["a", "a/x/y", "z"], ["a/x/y/f.py"]) == ["a", "a/x/y"]


def test_untracked_file_under_a_tracked_file_clashes() -> None:
    assert _in_the_way(["a/b.txt/inner", "a/c/inner"], ["a/b.txt"]) == ["a/b.txt/inner"]


def test_no_untracked_files_does_not_read_the_tree() -> None:
    g = _Git([], ["a"])
    assert worktree._untracked_in_the_way(g, "origin/x") == []  # type: ignore[arg-type]
    assert [c[0] for c in g.calls] == ["ls-files"]


def test_keeps_the_order_of_untracked_files() -> None:
    assert _in_the_way(["b", "a"], ["a", "b"]) == ["b", "a"]
