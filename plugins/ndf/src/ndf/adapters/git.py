"""git の操作（外部との接続の層）。

git を起動するのはこのモジュールの `Git` だけで、呼ぶ側は git の出力の形を知らない。起動の関数は差し替えられ、
テストは git を起動せずに出力を渡せる。失敗は `GitError`（終了コードと標準エラー）で返し、黙って空を返さない。
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple, Optional
from urllib.parse import urlsplit

from ndf import identity

# 完全な SHA と、検証に使える短い SHA（git の既定の短縮の長さ以上）
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHORT_SHA = re.compile(r"^[0-9a-f]{7,40}$")
# `git diff --shortstat` の 1 行（`N files changed, A insertions(+), D deletions(-)`）
_SHORTSTAT = re.compile(r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?")
# scp の形のリモートの URL（`[<利用者>@]<ホスト>:<パス>`）
_SCP_URL = re.compile(r"^(?:[^@/:]+@)?(?P<host>[^/:]+):(?P<path>.+)$")
# GitHub のリモートの URL のスキームと、そのスキームで GitHub を指すホスト（`ssh.github.com` は SSH を 443 番で使う接続先）
_SSH_HOSTS = frozenset({"github.com", "ssh.github.com"})
_GITHUB_HOSTS = {"https": frozenset({"github.com"}), "http": frozenset({"github.com"}), "git": frozenset({"github.com"}), "ssh": _SSH_HOSTS}
# `git cat-file --batch` がオブジェクトごとに置く見出しの行（無い名前の行は `<名前> missing`）
_BATCH_HEAD = re.compile(rb"^[0-9a-f]{40,64} (\S+) ([0-9]+)$")
# git の誤りの文に出る URL の、伏せる部分（利用者の情報と、`?`・`#` から後ろ）。トークンを URL に埋めたリモートの誤りに出る
_URL_USERINFO = re.compile(r"(?<=://)[^\s/?#]*@")
_URL_QUERY = re.compile(r"(://[^\s?#]*[?#])\S+")
# `git log --format` で各コミットの行の先頭に置くマーカー（差分の統計の行と見分ける）
_COMMIT_MARK = "@"
# ブランチの参照の接頭辞（`git worktree list --porcelain` の branch の行）
# Git のツリーで通常のファイルを表すモード（実行できるファイルを含む）
REGULAR_MODES = ("100644", "100755")
HEADS = "refs/heads/"
# タグの参照の接頭辞
TAGS = "refs/tags/"
# リモート名の既定を持つ唯一の保存先（スキーマの `default`）。値はここに書かない（#3617 の I7a）
REMOTE_SCHEMA = ("contracts/schemas/ndf-settings/worktree.schema.json", ("properties", "remote", "default"))
# リモート名を書く設定（メインディレクトリからの相対）
WORKTREE_NDF_SETTINGS = identity.FIXED_WORDS["ndf_settings_dir"] + "/worktree.json"
# git が認証の入力を端末へ求めないようにする環境変数（取得と送信が人の入力を待って止まらない）
NO_PROMPT = {"GIT_TERMINAL_PROMPT": "0"}
# 部分クローン（`--filter`）で手元に無いオブジェクトを、読み取りの途中でリモートから取得させない環境変数。
# `GIT_NO_LAZY_FETCH` を読まないバージョン（2.43.0・2.44.0 など）も、通信の手段を 1 つも許さなければ取得が通信の前に失敗する
NO_LAZY_FETCH = {"GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": ""}
# ref・リモートの名前を option として読ませない区切り（3364-7）。`--` が pathspec の区切りになるサブコマンド
# （diff・log・rev-parse）と、`--` を受けないもの（merge）は `END_OF_OPTIONS`（git 2.24 以降）を ref の
# 前に置き、checkout は SHA へ解決してから渡し、`--` の後を ref として読むもの（merge-base・show-ref・remote・fetch）は `--` の後に置く。`-` で始まる値は option にならず、ref として読めずに失敗する
END_OF_OPTIONS = "--end-of-options"


class GitError(Exception):
    """git が失敗した・出力が読めない・引数が誤り。"""


class RemoteError(GitError):
    """リモート名を決められない（設定の値の誤り・スキーマの既定が読めない）。"""


@functools.lru_cache(maxsize=1)
def default_remote() -> str:
    """リモート名の既定（`worktree.schema.json` の `/properties/remote/default`）。1 回だけ読む。"""
    rel, pointer = REMOTE_SCHEMA
    path = identity.ROOT / rel
    try:
        node = json.loads(path.read_text(encoding="utf-8"))
        for key in pointer:
            node = node[key]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RemoteError(f"{path} の /{'/'.join(pointer)} を読めない: {exc}") from None
    if not isinstance(node, str) or not node:
        raise RemoteError(f"{path} の /{'/'.join(pointer)} が空でない文字列でない: {node!r}")
    return node


def remote_of(main_dir: Path) -> str:
    """`<main_dir>/.ndf/worktree.json` の `remote`。設定かキーが無ければ `default_remote()`。誤りは `RemoteError`。"""
    path = Path(main_dir) / WORKTREE_NDF_SETTINGS
    if not path.is_file():
        return default_remote()
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RemoteError(f"{path} を読めない: {exc}") from None
    if not isinstance(doc, dict) or "remote" not in doc:
        return default_remote()
    value = doc["remote"]
    if not isinstance(value, str) or not value or value.startswith("-"):
        raise RemoteError(f"{WORKTREE_NDF_SETTINGS} の remote を直す: {value!r}")
    return value


class Completed(NamedTuple):
    """git の 1 回の起動の結果。"""

    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str], Optional[Path]], Completed]


def _start(
    args: list[str], cwd: Optional[Path], text: bool, stdin: Optional[bytes] = None, env: Optional[Mapping[str, str]] = None
) -> subprocess.CompletedProcess[Any]:
    """git を 1 回起動する。認証の入力を端末へ求めない（`NO_PROMPT`）。`stdin` があれば標準入力へ渡し、`env` があれば環境変数に足す。"""
    full = {**os.environ, **NO_PROMPT, **(env if env is not None else {})}
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=text, check=False, env=full, input=stdin)
    except OSError as exc:
        raise GitError(f"git を起動できない: {exc}") from None


def run_git(args: list[str], cwd: Optional[Path], env: Optional[Mapping[str, str]] = None) -> Completed:
    """git を 1 回起動する（既定の起動の関数）。認証の入力を端末へ求めない（`NO_PROMPT`）。"""
    proc = _start(args, cwd, text=True, env=env)
    return Completed(proc.returncode, proc.stdout, proc.stderr)


def mask_urls(text: str) -> str:
    """git の誤りの文から、URL の認証情報とクエリを伏せる（結果 JSON・ログに出すため）。"""
    return _URL_QUERY.sub(r"\1***", _URL_USERINFO.sub("***@", text))


def _failed(args: tuple[str, ...], returncode: int, stderr: str) -> GitError:
    return GitError(mask_urls(f"git {' '.join(args)} が失敗（終了コード {returncode}）: {stderr.strip()}"))


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
    """リモートの URL の `<所有者>/<リポジトリ>`。GitHub の URL でなければ None。ホストとパスを分けて読むので、パスの中に
    `github.com` を持つほかのホストの URL は GitHub とみなさない。"""
    url = url.strip()
    if "://" in url:
        try:
            parts = urlsplit(url)
        except ValueError:  # noqa: NDF401 利用者の入力: 形の壊れた URL は GitHub のリポジトリと確かめられない
            return None
        scheme, host, path = parts.scheme, parts.hostname, parts.path
    else:
        m = _SCP_URL.match(url)
        if m is None:
            return None
        scheme, host, path = "ssh", m.group("host").lower(), m.group("path")
    if scheme not in _GITHUB_HOSTS or host not in _GITHUB_HOSTS[scheme]:
        return None
    owner, _, name = path.strip("/").partition("/")
    name = name.removesuffix(".git")
    return f"{owner}/{name}" if owner and name and "/" not in name else None


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
    # git の環境変数に足す値（`run_bytes` の起動。`attempt` の起動は `runner` が足す）
    env: Optional[Mapping[str, str]] = None

    @classmethod
    def offline(cls, cwd: Optional[Path]) -> Git:
        """手元に無いオブジェクトをリモートから取得しない `Git`（`NO_LAZY_FETCH`）。欠けたオブジェクトは、git のバージョンと操作により、無いものとして読むか `GitError`。"""
        return cls(cwd, functools.partial(run_git, env=NO_LAZY_FETCH), NO_LAZY_FETCH)

    def attempt(self, *args: str) -> Completed:
        """git を起動して結果をそのまま返す（失敗でも例外にしない。成否を読むのは呼び手）。"""
        return self.runner(list(args), self.cwd)

    def run(self, *args: str) -> str:
        """git を起動して標準出力を返す。終了コードが 0 でなければ `GitError`。"""
        done = self.attempt(*args)
        if done.returncode != 0:
            raise _failed(args, done.returncode, done.stderr)
        return done.stdout

    def run_bytes(self, *args: str, stdin: Optional[bytes] = None, codes: Sequence[int] = (0,)) -> bytes:
        """git を起動して標準出力を元のバイト列のまま返す（改行を変えない）。終了コードが `codes` に無ければ `GitError`。"""
        done = _start(list(args), self.cwd, text=False, stdin=stdin, env=self.env)
        if done.returncode not in codes:
            raise _failed(args, done.returncode, done.stderr.decode("utf-8", "replace"))
        return done.stdout

    def toplevel(self) -> Path:
        """作業場所の worktree の根（`git rev-parse --show-toplevel`。サブモジュールならそのworktree）。失敗は `GitError`。

        設定を読むリポジトリの根の解き方はここだけに置く（`route.repo_root`・`progress.settings_roots.settings_root` が使う。#4513）。
        """
        return Path(self.run("rev-parse", "--show-toplevel").strip())

    def rev_parse(self, ref: str) -> str:
        """参照（短い SHA・ブランチ・タグ）をコミットの完全な SHA へ解決する（#2041）。"""
        sha = self.run("rev-parse", "--verify", "--quiet", END_OF_OPTIONS, f"{ref}^{{commit}}").strip()
        if not FULL_SHA.match(sha):
            raise GitError(f"{ref} をコミットへ解決できない: {sha!r}")
        return sha

    def commit_or_none(self, ref: str) -> Optional[str]:
        """参照のコミットの完全な SHA。参照を読めなければ None（`rev-parse` の終了コード 1）、git の失敗は `GitError`（#4337）。"""
        args = ("rev-parse", "--verify", "--quiet", END_OF_OPTIONS, f"{ref}^{{commit}}")
        done = self.attempt(*args)
        if done.returncode == 1:
            return None
        if done.returncode != 0:
            raise _failed(args, done.returncode, done.stderr)
        sha = done.stdout.strip()
        if not FULL_SHA.match(sha):
            raise GitError(f"{ref} をコミットへ解決できない: {sha!r}")
        return sha

    def blobs(self, *names: str) -> list[Optional[bytes]]:
        """blob の元のバイト列を、何件でも git を 1 回だけ起動して `names`（`<コミット>:<パス>` か id）の順に返す。無い・blob でないものは None。"""
        rest = self.run_bytes("cat-file", "--batch", stdin="".join(f"{n}\n" for n in names).encode("utf-8"))
        out: list[Optional[bytes]] = []
        for _ in names:
            line, _, rest = rest.partition(b"\n")
            head = _BATCH_HEAD.match(line)
            if head is None:
                out.append(None)
                continue
            size = int(head.group(2))
            out.append(rest[:size] if head.group(1) == b"blob" else None)
            rest = rest[size + 1 :]
        return out

    def commits_of(self, *refs: str) -> list[str]:
        """参照（`HEAD` か `refs/` から始まる全体の名前）のコミットの完全な SHA を、何件でも git を 1 回だけ起動して返す。

        `--verify` は参照を 1 つしか受けず、`--verify` の無い `rev-parse` は `--end-of-options` を出力に写すので、
        `-` で始まる参照は option として読ませないよう起動の前に断る。
        """
        if any(r.startswith("-") for r in refs):
            raise GitError(f"参照の名前が - で始まる: {refs!r}")
        got = self.run("rev-parse", *(f"{r}^{{commit}}" for r in refs)).split()
        if len(got) != len(refs) or not all(FULL_SHA.match(sha) for sha in got):
            raise GitError(f"{', '.join(refs)} をコミットへ解決できない: {got!r}")
        return got

    def commit_named(self, name: str) -> Optional[str]:
        """`name` がブランチでない手元のコミット（短縮した SHA・タグ・`HEAD~3` など）を指せば完全な SHA。ブランチか、解決できなければ None。"""
        try:
            ref = self.run("rev-parse", "--verify", "--quiet", "--symbolic-full-name", END_OF_OPTIONS, name).strip()
            return None if ref and not ref.startswith(TAGS) else self.rev_parse(name)
        except GitError:  # noqa: NDF401 外部コマンド: コミットを指さない名前は None で呼び手へ返す（呼び手は元の失敗を出す）
            return None

    def merge_base(self, a: str, b: str) -> str:
        return self.run("merge-base", "--", a, b).strip()

    def _probe(self, what: str, *args: str) -> bool:
        """真偽を終了コードで返す探り（0 は真・1 は偽）。それ以外は `GitError`（`what` を確かめられない）。"""
        done = self.attempt(*args)
        if done.returncode not in (0, 1):
            raise GitError(f"{what}を確かめられない（終了コード {done.returncode}）: {done.stderr.strip()}")
        return done.returncode == 0

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """`ancestor` が `descendant` の祖先か。git の失敗（終了コード 1 以外）は `GitError`。"""
        return self._probe("祖先", "merge-base", "--is-ancestor", "--", ancestor, descendant)

    def has_ref(self, ref: str) -> bool:
        """完全な参照の名前（`refs/heads/<名前>` など）があるか。git の失敗（終了コード 1 以外）は `GitError`。"""
        return self._probe("参照", "show-ref", "--verify", "--quiet", "--", ref)

    def log_of(self, rev: str, *options: str) -> str:
        """`git log <options> <rev>` の標準出力。`rev` は ref か範囲（`a..b`）で、option として読ませない（3364-7）。"""
        return self.run("log", *options, END_OF_OPTIONS, rev)

    def diff_between(self, a: str, b: str, *options: str) -> str:
        """`git diff <options> <a> <b>` の標準出力。`a`・`b` を option として読ませない（3364-7）。"""
        return self.run("diff", *options, END_OF_OPTIONS, a, b)

    def checkout_detached(self, ref: str) -> Completed:
        """`ref` へ detached HEAD で移る。結果はそのまま返す。`ref` を option として読ませない（3364-7）。

        checkout はバージョンによって `END_OF_OPTIONS` を ref の側へ残して正しい ref でも失敗する（git 2.43 の
        parse-options）。先に `rev_parse` で完全な SHA へ解決し、16 進の SHA だけを渡す。SHA は `-` で始まらない。
        """
        try:
            sha = self.rev_parse(ref)
        except GitError as e:
            return Completed(128, "", str(e))
        return self.attempt("checkout", "-q", "--detach", sha)

    def merge_ff(self, ref: str, *options: str) -> Completed:
        """`ref` を fast-forward だけでマージする。結果はそのまま返す。`ref` を option として読ませない（3364-7）。

        `options` は `git merge` に足す引数（`ref` の前に置く）。
        """
        return self.attempt("merge", "--ff-only", "-q", *options, END_OF_OPTIONS, ref)

    def changed_files(self, base: str, head: str, *options: str) -> list[str]:
        """`base...head`（分岐点から head まで）で変わったファイルのパス。`options` は `git diff` に足す引数。

        `-z` で NUL 区切りに読む。改行区切りでは、既定の `core.quotePath` が日本語・タブ・引用符を含むパスを
        引用・エスケープした文字列のまま返し、呼び手が実際のファイルを開けない。
        """
        return [p for p in self.run("diff", "--name-only", "-z", *options, END_OF_OPTIONS, f"{base}...{head}").split("\0") if p]

    def changed_lines(self, base: str, head: str, exclude: Iterable[str] = ()) -> int:
        """`base..head` の first-parent の各コミットの変更行数の和。`exclude` のコミットの分は数えない（#3087）。

        範囲全体の差分から除くコミットの差分を引くと、同じファイルを順に書き換えるコミット（リリースの PR）の
        重なりを二重に引く。コミットごとに数えて足すので、除くコミットの分だけが抜ける。
        `exclude` は完全か短い SHA で、1 回の `git log` で数える。
        """
        log_text = self.run(
            "log",
            "--first-parent",
            "--diff-merges=first-parent",
            "--shortstat",
            f"--format={_COMMIT_MARK}%H",
            END_OF_OPTIONS,
            f"{base}..{head}",
        )
        drop = [e.strip().lower() for e in exclude]
        per_commit = first_parent_lines(log_text)
        return sum(n for sha, n in per_commit.items() if not any(sha_matches(d, sha) for d in drop))

    def common_dir(self) -> Path:
        """git の共通ディレクトリ（linked worktree からでもメインの `.git`）の実体の絶対パス。"""
        common = self.run("rev-parse", "--path-format=absolute", "--git-common-dir").strip()
        return Path(os.path.realpath(common))

    def remote(self) -> str:
        """この作業場所のリモート名（メインディレクトリの設定の `remote`、無ければ既定）。"""
        entries = self.worktrees()
        if not entries:
            raise GitError("メインディレクトリを求められない（git worktree list が空）")
        return remote_of(entries[0].path)

    def remote_url(self, remote: Optional[str] = None) -> str:
        """リモートの URL。`remote` を省けばこの作業場所のリモート名。引けなければ `GitError`。"""
        remote = remote if remote is not None else self.remote()
        return self.run("remote", "get-url", "--", remote).strip()

    def remote_repo(self, remote: Optional[str] = None) -> str:
        """リモートの `<所有者>/<リポジトリ>`。`remote` を省けばこの作業場所のリモート名。GitHub の URL でなければ `GitError`。

        URL は認証情報を含み得るので、例外の文に入れない。
        """
        remote = remote if remote is not None else self.remote()
        repo = repo_of_url(self.remote_url(remote))
        if repo is None:
            raise GitError(f"リモート {remote} の URL は GitHub のリポジトリでない")
        return repo

    def push_repos(self, remote: str) -> list[Optional[str]]:
        """`git push <remote>` の送り先ごとの `<所有者>/<リポジトリ>`（`pushurl`・`pushInsteadOf` を反映する。GitHub の URL で
        なければ None）。"""
        return [repo_of_url(url) for url in self.run("remote", "get-url", "--push", "--all", "--", remote).splitlines()]

    def remote_default_branch(self, remote: Optional[str] = None) -> Optional[str]:
        """リモートの既定ブランチ（取得済みの `refs/remotes/<remote>/HEAD` の指す先から `<remote>/` を外した名前）。

        `remote` を省けばこの作業場所のリモート名。HEAD が無い・`<remote>/` で始まらなければ `None`。
        """
        remote = remote if remote is not None else self.remote()
        done = self.attempt("symbolic-ref", "--quiet", "--short", f"refs/remotes/{remote}/HEAD")
        name = done.stdout.strip() if done.returncode == 0 else ""
        prefix = remote + "/"
        if not name.startswith(prefix) or name == prefix:
            return None
        return name[len(prefix) :]

    def status_paths(self) -> list[str]:
        """未コミットの変更のパス（追跡していないファイルは中まで含める）。改名と複製は変更前のパスも含める。"""
        return parse_status_z(self.run("status", "--porcelain", "-z", "--untracked-files=all"))

    def worktrees(self) -> list[WorktreeEntry]:
        """このリポジトリの worktree の一覧（先頭はメインディレクトリ）。"""
        return parse_worktree_list(self.run("worktree", "list", "--porcelain", "-z"))

    def fetch(self, remote: str, *refspecs: str) -> Completed:
        """`git fetch -q --no-tags <remote> <refspec>...` を打ち、結果をそのまま返す（失敗を止めるかは呼び手が決める）。

        既定の起動の関数は認証の入力を端末へ求めない。`FETCH_HEAD` を読まずに済むよう、マージする側は
        `refs/remotes/<remote>/<ブランチ>` を読む（ほかのセッションの fetch と `FETCH_HEAD` を取り合わない。#3317）。
        """
        return self.attempt("fetch", "-q", "--no-tags", "--", remote, *refspecs)

    def fetch_branch(self, remote: str, name: str) -> Completed:
        """ブランチ 1 本を `refs/remotes/<remote>/<name>` へマージする（`FETCH_HEAD` を読まない。#3317）。結果はそのまま返す。"""
        return self.fetch(remote, f"+refs/heads/{name}:refs/remotes/{remote}/{name}")

    def fetch_branch_or_raise(self, remote: str, name: str) -> None:
        """`fetch_branch` を打ち、マージできなければ `GitError`（`<remote> の <name> をマージできない`）。"""
        done = self.fetch_branch(remote, name)
        if done.returncode != 0:
            raise GitError(f"{remote} の {name} をマージできない: {mask_urls(done.stderr.strip())}")
