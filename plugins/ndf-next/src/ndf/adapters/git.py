"""git の操作（外部との接続の層）。

git を起動するのはこのモジュールの `Git` だけで、呼ぶ側は git の出力の形を知らない。起動の関数は差し替えられ、
テストは git を起動せずに出力を渡せる。失敗は `GitError`（終了コードと標準エラー）で返し、黙って空を返さない。
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Optional

# 完全な SHA と、照合に使える短い SHA（git の既定の短縮の長さ以上）
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHORT_SHA = re.compile(r"^[0-9a-f]{7,40}$")
# `git diff --shortstat` の 1 行（`N files changed, A insertions(+), D deletions(-)`）
_SHORTSTAT = re.compile(r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?")
# リモートの URL から `<所有者>/<リポジトリ>` を取る（https と ssh の両方）
_REMOTE = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$")
# `git log --format` で各コミットの行の先頭に置く印（差分の統計の行と見分ける）
_COMMIT_MARK = "@"


class GitError(Exception):
    """git が失敗した・出力が読めない・引数が誤り。"""


class Completed(NamedTuple):
    """git の 1 回の起動の結果。"""

    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str], Optional[Path]], Completed]


def run_git(args: list[str], cwd: Optional[Path]) -> Completed:
    """git を 1 回起動する（既定の起動の関数）。"""
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise GitError(f"git を起動できない: {exc}") from None
    return Completed(proc.returncode, proc.stdout, proc.stderr)


def sha_matches(expected: str, actual: str) -> bool:
    """`expected`（完全か短い SHA）が完全な SHA `actual` を指すか（#2041）。

    短い SHA は先頭の一致で比べる。形が SHA でない値は `GitError`（黙って不一致にしない）。
    """
    want, got = expected.strip().lower(), actual.strip().lower()
    if not SHORT_SHA.match(want):
        raise GitError(f"SHA として読めない（7〜40 桁の 16 進）: {expected!r}")
    if not FULL_SHA.match(got):
        raise GitError(f"比べる相手が完全な SHA でない: {actual!r}")
    return got.startswith(want)


def parse_shortstat(text: str) -> int:
    """`--shortstat` の 1 行から、足した行と消した行の和を返す。行が無ければ 0。"""
    m = _SHORTSTAT.search(text)
    if m is None:
        return 0
    return sum(int(g) for g in m.groups()[1:] if g is not None)


def first_parent_lines(log_text: str) -> dict[str, int]:
    """`git log --first-parent --diff-merges=first-parent --shortstat --format=@%H` の出力を、コミットごとの変更行数にする。"""
    out: dict[str, int] = {}
    current: Optional[str] = None
    for line in log_text.splitlines():
        if line.startswith(_COMMIT_MARK):
            current = line[len(_COMMIT_MARK) :].strip()
            out[current] = 0
        elif current is not None and line.strip():
            out[current] += parse_shortstat(line)
    return out


def repo_of_url(url: str) -> Optional[str]:
    """リモートの URL の `<所有者>/<リポジトリ>`。GitHub の URL でなければ None。"""
    m = _REMOTE.search(url.strip())
    return f"{m.group('owner')}/{m.group('name')}" if m else None


@dataclass(frozen=True)
class Git:
    """1 つの作業場所での git の操作。"""

    cwd: Optional[Path] = None
    runner: Runner = run_git

    def run(self, *args: str) -> str:
        """git を起動して標準出力を返す。終了コードが 0 でなければ `GitError`。"""
        done = self.runner(list(args), self.cwd)
        if done.returncode != 0:
            raise GitError(f"git {' '.join(args)} が失敗（終了コード {done.returncode}）: {done.stderr.strip()}")
        return done.stdout

    def rev_parse(self, ref: str) -> str:
        """参照（短い SHA・ブランチ・タグ）をコミットの完全な SHA へ解決する（#2041）。"""
        sha = self.run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()
        if not FULL_SHA.match(sha):
            raise GitError(f"{ref} をコミットへ解決できない: {sha!r}")
        return sha

    def merge_base(self, a: str, b: str) -> str:
        return self.run("merge-base", a, b).strip()

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """`ancestor` が `descendant` の祖先か。git の失敗（終了コード 1 以外）は `GitError`。"""
        done = self.runner(["merge-base", "--is-ancestor", ancestor, descendant], self.cwd)
        if done.returncode not in (0, 1):
            raise GitError(f"祖先を確かめられない（終了コード {done.returncode}）: {done.stderr.strip()}")
        return done.returncode == 0

    def changed_files(self, base: str, head: str) -> list[str]:
        """`base...head`（分岐点から head まで）で変わったファイルのパス。"""
        return [p for p in self.run("diff", "--name-only", f"{base}...{head}").splitlines() if p]

    def changed_lines(self, base: str, head: str, exclude: Iterable[str] = ()) -> int:
        """`base..head` の first-parent の各コミットの変更行数の和。`exclude` のコミットの分は数えない（#3087）。

        範囲全体の差分から除くコミットの差分を引くと、同じファイルを順に書き換えるコミット（リリースの PR）の
        重なりを二重に引く。コミットごとに数えて足すので、除くコミットの分だけが抜ける。
        `exclude` は完全か短い SHA で、1 回の `git log` で数える。
        """
        log_text = self.run(
            "log", "--first-parent", "--diff-merges=first-parent", "--shortstat", f"--format={_COMMIT_MARK}%H", f"{base}..{head}"
        )
        drop = [e.strip().lower() for e in exclude]
        per_commit = first_parent_lines(log_text)
        return sum(n for sha, n in per_commit.items() if not any(sha_matches(d, sha) for d in drop))

    def remote_repo(self, remote: str = "origin") -> str:
        """リモートの `<所有者>/<リポジトリ>`。GitHub の URL でなければ `GitError`。"""
        url = self.run("remote", "get-url", remote).strip()
        repo = repo_of_url(url)
        if repo is None:
            raise GitError(f"リモート {remote} は GitHub のリポジトリでない: {url}")
        return repo
