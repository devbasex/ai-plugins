"""git の操作（外部との接続の層）。

git を起動するのはこのモジュールの `Git` だけで、呼ぶ側は git の出力の形を知らない。起動の関数は差し替えられ、
テストは git を起動せずに出力を渡せる。失敗は `GitError`（終了コードと標準エラー）で返し、黙って空を返さない。
"""

from __future__ import annotations

import os
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
# ブランチの参照の接頭辞（`git worktree list --porcelain` の branch の行）
HEADS = "refs/heads/"
# 取得と照合に使うリモートの名前
ORIGIN = "origin"
# git が認証の入力を端末へ求めないようにする環境変数（取得と送信が人の入力を待って止まらない）
NO_PROMPT = {"GIT_TERMINAL_PROMPT": "0"}


class GitError(Exception):
    """git が失敗した・出力が読めない・引数が誤り。"""


class Completed(NamedTuple):
    """git の 1 回の起動の結果。"""

    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str], Optional[Path]], Completed]


def run_git(args: list[str], cwd: Optional[Path]) -> Completed:
    """git を 1 回起動する（既定の起動の関数）。認証の入力を端末へ求めない（`NO_PROMPT`）。"""
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False, env={**os.environ, **NO_PROMPT})
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


class WorktreeEntry(NamedTuple):
    """`git worktree list` の 1 件。`branch` は detached HEAD なら `None`。"""

    path: Path
    head: str
    branch: Optional[str]
    prunable: bool = False
    locked: bool = False


def _short_branch(ref: Optional[str]) -> Optional[str]:
    """branch の行の参照（`refs/heads/<名前>`）の名前。ブランチの参照でなければ `None`。"""
    return ref[len(HEADS) :] if ref is not None and ref.startswith(HEADS) else None


def _entry(record: str) -> Optional[WorktreeEntry]:
    """worktree 1 件の項目（NUL 区切り）を読む。`worktree` の行が無ければ `None`。"""
    fields = dict(f.partition(" ")[::2] for f in record.split("\0") if f)
    if "worktree" not in fields:
        return None
    head = fields["HEAD"] if "HEAD" in fields else ""
    return WorktreeEntry(Path(fields["worktree"]), head, _short_branch(fields.get("branch")), "prunable" in fields, "locked" in fields)


def parse_worktree_list(text: str) -> list[WorktreeEntry]:
    """`git worktree list --porcelain -z` の出力を読む（項目は NUL、worktree は空の項目で区切られる）。先頭はメインディレクトリ。"""
    return [e for e in (_entry(record) for record in text.split("\0\0")) if e is not None]


def parse_status_z(text: str) -> list[str]:
    """`git status --porcelain -z` の出力のパス。改名と複製（状態の先頭が `R`・`C`）は続く項目の変更前のパスも並べる。"""
    fields = text.split("\0")
    out, i = [], 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        out.append(entry[3:])
        if entry[0] in "RC" and i < len(fields):
            out.append(fields[i])
            i += 1
    return out


@dataclass(frozen=True)
class Git:
    """1 つの作業場所での git の操作。"""

    cwd: Optional[Path] = None
    runner: Runner = run_git

    def attempt(self, *args: str) -> Completed:
        """git を起動して結果をそのまま返す（失敗でも例外にしない。成否を読むのは呼び手）。"""
        return self.runner(list(args), self.cwd)

    def run(self, *args: str) -> str:
        """git を起動して標準出力を返す。終了コードが 0 でなければ `GitError`。"""
        done = self.attempt(*args)
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

    def _probe(self, what: str, *args: str) -> bool:
        """真偽を終了コードで返す探り（0 は真・1 は偽）。それ以外は `GitError`（`what` を確かめられない）。"""
        done = self.attempt(*args)
        if done.returncode not in (0, 1):
            raise GitError(f"{what}を確かめられない（終了コード {done.returncode}）: {done.stderr.strip()}")
        return done.returncode == 0

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """`ancestor` が `descendant` の祖先か。git の失敗（終了コード 1 以外）は `GitError`。"""
        return self._probe("祖先", "merge-base", "--is-ancestor", ancestor, descendant)

    def has_ref(self, ref: str) -> bool:
        """完全な参照の名前（`refs/heads/<名前>` など）があるか。git の失敗（終了コード 1 以外）は `GitError`。"""
        return self._probe("参照", "show-ref", "--verify", "--quiet", ref)

    def changed_files(self, base: str, head: str) -> list[str]:
        """`base...head`（分岐点から head まで）で変わったファイルのパス。

        `-z` で NUL 区切りに読む。改行区切りでは、既定の `core.quotePath` が日本語・タブ・引用符を含むパスを
        引用・エスケープした文字列のまま返し、呼び手が実際のファイルを開けない。
        """
        return [p for p in self.run("diff", "--name-only", "-z", f"{base}...{head}").split("\0") if p]

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

    def common_dir(self) -> Path:
        """git の共通ディレクトリ（linked worktree からでもメインの `.git`）の実体の絶対パス。"""
        common = self.run("rev-parse", "--path-format=absolute", "--git-common-dir").strip()
        return Path(os.path.realpath(common))

    def remote_repo(self, remote: str = ORIGIN) -> str:
        """リモートの `<所有者>/<リポジトリ>`。GitHub の URL でなければ `GitError`。"""
        url = self.run("remote", "get-url", remote).strip()
        repo = repo_of_url(url)
        if repo is None:
            raise GitError(f"リモート {remote} は GitHub のリポジトリでない: {url}")
        return repo

    def status_paths(self) -> list[str]:
        """未コミットの変更のパス（追跡していないファイルは中まで含める）。改名と複製は変更前のパスも含める。"""
        return parse_status_z(self.run("status", "--porcelain", "-z", "--untracked-files=all"))

    def worktrees(self) -> list[WorktreeEntry]:
        """このリポジトリの worktree の一覧（先頭はメインディレクトリ）。"""
        return parse_worktree_list(self.run("worktree", "list", "--porcelain", "-z"))

    def fetch(self, remote: str, *refspecs: str) -> Completed:
        """`git fetch -q --no-tags <remote> <refspec>...` を打ち、結果をそのまま返す（失敗を止めるかは呼び手が決める）。

        既定の起動の関数は認証の入力を端末へ求めない。`FETCH_HEAD` を読まずに済むよう、取り込む側は
        `refs/remotes/<remote>/<ブランチ>` を読む（ほかのセッションの fetch と `FETCH_HEAD` を取り合わない。#3317）。
        """
        return self.attempt("fetch", "-q", "--no-tags", remote, *refspecs)

    def fetch_branch(self, remote: str, name: str) -> Completed:
        """ブランチ 1 本を `refs/remotes/<remote>/<name>` へ取り込む（`FETCH_HEAD` を読まない。#3317）。結果はそのまま返す。"""
        return self.fetch(remote, f"+refs/heads/{name}:refs/remotes/{remote}/{name}")
