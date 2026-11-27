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
# リモートの URL から `<所有者>/<リポジトリ>` を取る（https と ssh の両方）
# リモートの URL の書き方 2 つ。scp の形（`git@github.com:<所有者>/<名前>.git`）と、それ以外の `<スキーム>://` の形（ホストは urlsplit で読む）
_SCP_REMOTE = re.compile(r"^(?:[^@/\s]+@)?github\.com:(?P<path>[^?#\s]+)$")
# GitHub のリポジトリのパス。所有者と名前に使える文字だけを受け、クエリ・フラグメント・余分な階層を含むパスは受けない
_REPO_PATH = re.compile(r"^/?(?P<owner>[A-Za-z0-9-]+)/(?P<name>[A-Za-z0-9._-]+?)(?:\.git)?/?$")
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


class GitDirs(NamedTuple):
    """作業場所の git のディレクトリ 2 つ。メインの作業場所では同じパスになる。"""

    own: Path  # この作業場所の git のディレクトリ（linked worktree では `.git/worktrees/<名前>`）
    common: Path  # 設定ファイル `config` を持つ共通のディレクトリ


def git_dirs(here: Path) -> Optional[GitDirs]:
    """git を起動せずに、`here` かその親の `.git` から、作業場所の git のディレクトリと共通のディレクトリを求める。無ければ None。

    linked worktree の `.git` ファイルは、`gitdir:` の行と、その先の `commondir` をたどる。
    """
    dot = next((c / ".git" for c in (here, *here.parents) if (c / ".git").exists()), None)
    if dot is None:
        return None
    if dot.is_dir():
        return GitDirs(dot, dot)
    try:
        line = dot.read_text(encoding="utf-8").strip()
    except OSError:  # noqa: NDF401 ファイル: .git を読めなければ git のディレクトリは無い
        return None
    if not line.startswith("gitdir:"):
        return None
    own = Path(os.path.abspath(dot.parent / line[len("gitdir:") :].strip()))
    try:
        common = (own / "commondir").read_text(encoding="utf-8").strip()
    except OSError:  # commondir が無ければ、その git のディレクトリが共通のディレクトリ
        return GitDirs(own, own)
    return GitDirs(own, Path(os.path.abspath(own / common)))


def configured_remote_url(dirs: GitDirs) -> Optional[str]:
    """git を起動せずに、共通のディレクトリの `config` から、この作業場所のリモート名（`remote_of`）の最初の URL を読む。

    リモート名はメインディレクトリ（共通のディレクトリの親）の設定から決める（`Git.remote()` と同じ設定）。設定かリモートを読めなければ None。
    `config` から、`include.path` と、`gitdir:`・`gitdir/i:` の条件の `includeIf` が読み込む設定ファイルもたどる（リモートを別の
    設定ファイルに分けた構成でも、git が解決する URL を得る）。条件は、git と同じく、この作業場所の git のディレクトリ（`dirs.own`）に照らす。
    ほかの条件（`onbranch:`・`hasconfig:`）の `includeIf` は読まない。`insteadOf` などの書き換えは当てない（`git remote get-url` と違いうる）。
    """
    try:
        name = remote_of(dirs.common.parent)
    except RemoteError:  # noqa: NDF401 ファイル: リモート名を決められなければリモートは不明
        return None
    return _config_value_of(dirs.common / "config", dirs.own, ("remote", name), "url", set())


# 設定ファイルの見出し（`[name]`・`[name "sub"]`・古い形の `[name.sub]`）と、`key = value` の行
_CONFIG_SECTION = re.compile(r'^\[\s*([A-Za-z0-9.-]+)(?:\s+"((?:[^"\\]|\\.)*)")?\s*\]')
_CONFIG_ENTRY = re.compile(r"^([A-Za-z][A-Za-z0-9-]*)\s*(?:=\s*(.*))?$")


def _config_value_of(path: Path, own: Path, want: tuple[str, str], key: str, seen: set[Path]) -> Optional[str]:
    """設定ファイル `path` と、そこから読み込む設定ファイルを git と同じ順に読み、見出し `want`（小文字の名前と、下位の名前）の
    `key` の最初の値を返す。見つけた時点で読むのをやめる。無ければ None。

    読めないファイルは空として扱う。同じファイルを 2 度読まない（読み込みの循環で止まらない）。
    """
    real = Path(os.path.realpath(path))
    if real in seen:
        return None
    seen.add(real)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:  # noqa: NDF401 ファイル: git も読めない読み込み先は飛ばす
        return None
    for heading, name, value in _config_entries(text):
        if heading == want and name == key:
            return value
        if name == "path" and _includes(heading, path, own):
            target = Path(os.path.expanduser(value))
            found = _config_value_of(target if target.is_absolute() else path.parent / target, own, want, key, seen)
            if found is not None:
                return found
    return None


def _config_entries(text: str) -> Iterable[tuple[tuple[str, str], str, str]]:
    """設定ファイルの本文の、値の行ごとの（見出し、小文字のキー、値）。見出しは小文字の名前と、下位の名前の組。"""
    heading = ("", "")
    for raw in text.splitlines():
        line = raw.strip()
        head = _CONFIG_SECTION.match(line)
        if head is not None:
            heading = _heading(head.group(1), head.group(2))
            line = line[head.end() :].strip()
        entry = _CONFIG_ENTRY.match(line)
        if entry is not None:
            yield heading, entry.group(1).lower(), _config_value(entry.group(2) if entry.group(2) is not None else "true")


def _heading(name: str, sub: Optional[str]) -> tuple[str, str]:
    """見出しの名前と下位の名前を、比べる形（名前は小文字、下位の名前はエスケープを外した文字）にする。"""
    if sub is None:
        name, _, sub = name.partition(".")
    return name.lower(), re.sub(r"\\(.)", r"\1", sub)


def _config_value(text: str) -> str:
    """設定の値の右辺。引用符を外し、引用符の外の `#`・`;` から後ろ（注釈）を除く。"""
    out, quoted, i = [], False, 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            out.append(text[i + 1])  # URL とパスの値に要るのは `\"` と `\\` だけ（`\n` などの制御文字は扱わない）
            i += 2
            continue
        if ch == '"':
            quoted = not quoted
        elif ch in "#;" and not quoted:
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out).strip()


def _includes(section: tuple[str, str], path: Path, own: Path) -> bool:
    """見出し `section` の `path` が読み込む対象か（`include`、または条件が作業場所の git のディレクトリ `own` に当たる `includeIf`）。"""
    name, sub = section
    if name == "include":
        return True
    if name != "includeif":
        return False
    for prefix, fold in (("gitdir:", False), ("gitdir/i:", True)):
        if sub.startswith(prefix):
            return _gitdir_matches(sub[len(prefix) :], path, own, fold)
    return False


def _gitdir_matches(pattern: str, path: Path, own: Path, fold: bool) -> bool:
    """`includeIf "gitdir:<pattern>"` の条件が作業場所の git のディレクトリ `own` に当たるか（git の `include_by_gitdir` と同じ）。

    `~` を展開し、`./` で始まれば、設定ファイル `path` の実体があるディレクトリに置き換えて、その部分は文字どおりに比べる。
    ほかの相対の形は先頭に `**/` を足し、`/` で終われば `**` を足す。残りは wildmatch（`_wildmatch`）で比べる。
    `own` は実体のパスで比べ、当たらなければ、そのままの絶対パスでもう一度比べる（2 つが同じなら 1 回だけ比べる）。
    文字どおりに比べる部分の長さは、エンコードした後のバイト数で数える（日本語などの多バイトの字を含むパスでも、
    置き換えた部分の `[`・`*` をパターンとして評価しない）。
    """
    if pattern.startswith("~"):
        pattern = os.path.expanduser(pattern)
    prefix = 0
    if pattern.startswith("./"):
        base = os.path.dirname(os.path.realpath(path))
        pattern, prefix = base + pattern[1:], len(os.fsencode(base + "/"))
    elif not os.path.isabs(pattern):
        pattern = "**/" + pattern
    if pattern.endswith("/"):
        pattern += "**"
    pat = os.fsencode(pattern)
    real, absolute = os.fsencode(os.path.realpath(own)), os.fsencode(os.path.abspath(own))
    for text in (real,) if real == absolute else (real, absolute):
        head, got = pat[:prefix], text[:prefix]
        if len(text) < prefix or (head.lower() != got.lower() if fold else head != got):
            return False
        if _wildmatch(pat[prefix:], text[prefix:], fold):
            return True
    return False


# git の wildmatch の結果（`wildmatch.c` の `WM_*`）。打ち切りは、外側の `*` に戻らずに当たらないと決める
_WM_MATCH, _WM_NOMATCH, _WM_ABORT_ALL, _WM_ABORT_TO_STARSTAR = 0, 1, -1, -2
_SLASH, _STAR, _BACKSLASH, _LBRACKET, _RBRACKET = (ord(c) for c in "/*\\[]")
_GLOB_SPECIAL = frozenset(b"*?[\\")
# 角括弧の中の POSIX 文字クラス（git の sane_ctype と同じく ASCII だけ）
_POSIX_CLASSES: dict[bytes, Callable[[int, bool], bool]] = {
    b"alnum": lambda c, _f: chr(c).isascii() and chr(c).isalnum(),
    b"alpha": lambda c, _f: chr(c).isascii() and chr(c).isalpha(),
    b"blank": lambda c, _f: c in b" \t",
    b"cntrl": lambda c, _f: c < 32 or c == 127,
    b"digit": lambda c, _f: 48 <= c <= 57,
    b"graph": lambda c, _f: 33 <= c <= 126,
    b"lower": lambda c, _f: 97 <= c <= 122,
    b"print": lambda c, _f: 32 <= c <= 126,
    b"punct": lambda c, _f: 33 <= c <= 126 and not chr(c).isalnum(),
    b"space": lambda c, _f: c in b" \t\n\r\f\v",
    b"upper": lambda c, fold: 65 <= c <= 90 or (fold and 97 <= c <= 122),
    b"xdigit": lambda c, _f: c in b"0123456789abcdefABCDEF",
}


def _wildmatch(pattern: bytes, text: bytes, fold: bool) -> bool:
    """git の `wildmatch(pattern, text, WM_PATHNAME | (fold なら WM_CASEFOLD))` が当たりを返すか。`wildmatch.c` の `dowild` を移したもの。"""
    return _dowild(pattern, 0, text, 0, fold) == _WM_MATCH


def _at(s: bytes, i: int) -> int:
    """`s` の `i` 番目のバイト。末尾より後ろは 0（C の文字列の終わり）。"""
    return s[i] if i < len(s) else 0


def _fold(c: int, fold: bool) -> int:
    return c + 32 if fold and 65 <= c <= 90 else c


def _dowild(p: bytes, pi: int, t: bytes, ti: int, fold: bool) -> int:
    while pi < len(p):
        p_ch, t_ch = _fold(p[pi], fold), _fold(_at(t, ti), fold)
        if ti >= len(t) and p_ch != _STAR:
            return _WM_ABORT_ALL
        if p_ch == _STAR:
            star = _star(p, pi, t, ti, fold)
            if isinstance(star, int):
                return star
            pi, ti = star
        else:
            found, pi = _bracket(p, pi, t_ch, fold) if p_ch == _LBRACKET else _one(p, pi, p_ch, t_ch)
            if found != _WM_MATCH:
                return found
        pi, ti = pi + 1, ti + 1
    return _WM_NOMATCH if ti < len(t) else _WM_MATCH


def _one(p: bytes, pi: int, p_ch: int, t_ch: int) -> tuple[int, int]:
    """`dowild` の `?`・`\\` のエスケープ・ふつうの字の場合。結果と、読んだ最後の位置。"""
    if p_ch == ord("?"):
        return (_WM_NOMATCH if t_ch == _SLASH else _WM_MATCH), pi
    if p_ch == _BACKSLASH:
        pi += 1
        p_ch = _at(p, pi)
    return (_WM_MATCH if t_ch == p_ch else _WM_NOMATCH), pi


def _star(p: bytes, pi: int, t: bytes, ti: int, fold: bool) -> int | tuple[int, int]:
    """`dowild` の `*` の場合。結果が決まればその値、`*/` で次の階層へ進むなら、続きを読む位置（`/` の位置の組）。"""
    head = _star_head(p, pi, t, ti, fold)
    if isinstance(head, int):
        return head
    pi, match_slash = head
    if pi >= len(p):
        return _WM_NOMATCH if not match_slash and _SLASH in t[ti:] else _WM_MATCH
    if not match_slash and p[pi] == _SLASH:
        slash = t.find(b"/", ti)
        return (pi, slash) if slash >= 0 else _WM_NOMATCH
    return _star_scan(p, pi, t, ti, fold, match_slash)


def _star_head(p: bytes, pi: int, t: bytes, ti: int, fold: bool) -> int | tuple[int, bool]:
    """`*` の並びを読み、次の位置と、`/` をまたげるか（階層の全体を表す `**` か）。`**/` が 0 個の階層で当たれば `_WM_MATCH`。"""
    pi += 1
    if _at(p, pi) != _STAR:
        return pi, False
    prev = pi - 2
    while _at(p, pi) == _STAR:
        pi += 1
    nxt = _at(p, pi)
    whole = prev < 0 or p[prev] == _SLASH
    if not whole or not (pi >= len(p) or nxt == _SLASH or (nxt == _BACKSLASH and _at(p, pi + 1) == _SLASH)):
        return pi, False
    if nxt == _SLASH and _dowild(p, pi + 1, t, ti, fold) == _WM_MATCH:
        return _WM_MATCH
    return pi, True


def _star_scan(p: bytes, pi: int, t: bytes, ti: int, fold: bool, match_slash: bool) -> int:
    """`*` の後ろを、`*` が当たる長さを 1 字ずつ延ばしながら比べる。"""
    t_ch = _fold(_at(t, ti), fold)
    while ti < len(t):
        if p[pi] not in _GLOB_SPECIAL:
            want = _fold(p[pi], fold)
            while ti < len(t) and (match_slash or t[ti] != _SLASH) and _fold(t[ti], fold) != want:
                ti += 1
            t_ch = _fold(_at(t, ti), fold)
            if ti >= len(t) or t_ch != want:
                return _WM_ABORT_ALL if match_slash else _WM_ABORT_TO_STARSTAR
        found = _dowild(p, pi, t, ti, fold)
        if found != _WM_NOMATCH and (not match_slash or found != _WM_ABORT_TO_STARSTAR):
            return found
        if found == _WM_NOMATCH and not match_slash and t_ch == _SLASH:
            return _WM_ABORT_TO_STARSTAR
        ti += 1
        t_ch = _at(t, ti)
    return _WM_ABORT_ALL


def _bracket(p: bytes, pi: int, t_ch: int, fold: bool) -> tuple[int, int]:
    """`dowild` の `[...]` の場合。結果と、閉じる `]` の位置。逆順の範囲（`[z-a]`）は何にも当たらず、閉じない括弧と
    名前の誤った POSIX 文字クラスは `_WM_ABORT_ALL`（git と同じ）。"""
    pi += 1
    negated = _at(p, pi) in b"!^"
    pi += 1 if negated else 0
    prev, matched = 0, False
    while True:
        if pi >= len(p):
            return _WM_ABORT_ALL, pi
        found, pi, p_ch = _bracket_item(p, pi, t_ch, fold, prev)
        if found is None:
            return _WM_ABORT_ALL, pi
        matched |= found
        prev = p_ch
        pi += 1
        if _at(p, pi) == _RBRACKET:
            break
    return (_WM_NOMATCH if matched == negated or t_ch == _SLASH else _WM_MATCH), pi


def _bracket_item(p: bytes, pi: int, t_ch: int, fold: bool, prev: int) -> tuple[Optional[bool], int, int]:
    """角括弧の中の 1 つ（字・エスケープ・範囲・POSIX 文字クラス）が当たるか、読んだ最後の位置、次の範囲の始まりに使う字。
    打ち切るなら当たるかは None。"""
    p_ch = p[pi]
    if p_ch == _BACKSLASH:
        pi += 1
        return (None, pi, 0) if pi >= len(p) else (t_ch == p[pi], pi, p[pi])
    if p_ch == ord("-") and prev and pi + 1 < len(p) and p[pi + 1] != _RBRACKET:
        pi, end = _range_end(p, pi)
        if end < 0:
            return None, pi, 0
        upper = t_ch - 32 if fold and 97 <= t_ch <= 122 else t_ch
        return prev <= t_ch <= end or prev <= upper <= end, pi, 0
    if p_ch == _LBRACKET and _at(p, pi + 1) == ord(":"):
        return _posix_class(p, pi, t_ch, fold)
    return t_ch == p_ch, pi, p_ch


def _range_end(p: bytes, pi: int) -> tuple[int, int]:
    """範囲の `-` の後ろの文字（`\\` のエスケープを外す）と、その位置。エスケープで終われば文字は -1。"""
    pi += 1
    if p[pi] == _BACKSLASH:
        pi += 1
        return (pi, p[pi]) if pi < len(p) else (pi, -1)
    return pi, p[pi]


def _posix_class(p: bytes, pi: int, t_ch: int, fold: bool) -> tuple[Optional[bool], int, int]:
    """`[:name:]` の評価（当たったか、最後に読んだ位置、続きの比べに使う直前の文字）。`:]` で閉じていなければ `[` を
    ふつうの文字として比べる。閉じない・名前が誤りなら当たったかは None。"""
    start = pi + 2
    end = p.find(b"]", start)
    if end < 0:
        return None, len(p), 0
    if end - start - 1 < 0 or p[end - 1] != ord(":"):
        return t_ch == _LBRACKET, pi, _LBRACKET
    check = _POSIX_CLASSES.get(p[start : end - 1])
    return (None if check is None else check(t_ch, fold)), end, 0


def slug_of_url(url: str) -> Optional[str]:
    """git の URL（https・ssh・scp の形）の `owner/repo`。ホストは問わない。"""
    tail = url.rstrip("/")
    if tail.endswith(".git"):
        tail = tail[: -len(".git")]
    parts = tail.replace(":", "/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 and all(parts[-2:]) else None


def configured_remote_slug(here: Path) -> Optional[str]:
    """読み手役の確認の記録と、hook の guard `reading-check` が照らし合わせに使うリポジトリの識別子。

    `here` を含む作業場所（`git_dirs`）で `configured_remote_url` が読んだ URL の `owner/repo`（`slug_of_url`）で、読めなければ None。
    記録する側・照らす側・質問を出せるかを決める側が、この 1 つの関数で同じ値を得る（`insteadOf` を当てた後の URL とは違いうるため）。
    git を起動しない。
    """
    dirs = git_dirs(here)
    url = configured_remote_url(dirs) if dirs is not None else None
    return slug_of_url(url) if url is not None else None


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


def _mask_urls(text: str) -> str:
    """git の誤りの文から、URL の認証情報とクエリを伏せる（結果 JSON・ログに出すため）。"""
    return _URL_QUERY.sub(r"\1***", _URL_USERINFO.sub("***@", text))


def _failed(args: tuple[str, ...], returncode: int, stderr: str) -> GitError:
    return GitError(_mask_urls(f"git {' '.join(args)} が失敗（終了コード {returncode}）: {stderr.strip()}"))


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
    """リモートの URL の `<所有者>/<リポジトリ>`。GitHub の URL でなければ None。

    ホストが github.com であることを確かめ、認証情報・クエリ・フラグメントを除いたパスから所有者と名前だけを取る
    （URL に埋めたトークンを使用量ログなどへ写さない。#1959）。パスがリポジトリの形でなければ None。
    """
    text = url.strip()
    if "://" in text:
        parts = urlsplit(text)
        if parts.hostname is None or parts.hostname.lower() != "github.com":
            return None
        path = parts.path
    else:
        scp = _SCP_REMOTE.match(text)
        if scp is None:
            return None
        path = scp.group("path")
    m = _REPO_PATH.match(path)
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
        """参照（`refs/` から始まる全体の名前）のコミットの完全な SHA を、何件でも git を 1 回だけ起動して返す。"""
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

    def merge_ff(self, ref: str) -> Completed:
        """`ref` を fast-forward だけでマージする。結果はそのまま返す。`ref` を option として読ませない（3364-7）。"""
        return self.attempt("merge", "--ff-only", "-q", END_OF_OPTIONS, ref)

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
            raise GitError(f"{remote} の {name} をマージできない: {_mask_urls(done.stderr.strip())}")
