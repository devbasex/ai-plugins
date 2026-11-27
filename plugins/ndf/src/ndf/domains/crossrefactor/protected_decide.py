"""テストの定義のファイルの判定（#4566 の決定 6）。ファイルもプロセスも触らない。git を読むのは `local_gate` である。

手元の経路では、この実行の変更が自分を合格させる基準にならないよう、次のファイルを対象範囲から外し、push の前に
変えていないかを確かめる。パスはリポジトリのルートからの相対で、PR のブランチの先頭のツリーで解決する。

- どこかの階層が `.ndf`
- 名前が決まった名前の一覧（`PROTECTED_NAMES`）に当たる（どの階層でも）
- テストのコマンドの語をパスにしたもの。親のディレクトリをファイルかリンクに置き換える変更（親のパスそのもの）も当たる。
  ツリーにまだ無いパスも当たる。シンボリックリンクはリンクとリンク先（ツリーの中だけ）の両方。途中のディレクトリが
  リンクのパスも、要素ごとに解決して実際に使われるファイルを保護する
- `/` を含まない語について、`search`（`PATH` の相対の要素。空と `.` はルート）の下の同じ名前のパス
- 設定 `test.protected_files` の指定の中（`scoped.in_scope`）
"""

from __future__ import annotations

import fnmatch
import posixpath
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from ndf import identity
from ndf.domains.crossrefactor import symbols_decide
from ndf.testing import scoped, weight

NDF_DIR = identity.FIXED_WORDS["ndf_settings_dir"]
# 依存とテストの定義の決まった名前と、テストランナーが自動で読むファイル（どの階層でも。設計文書の表）
PROTECTED_NAMES = tuple(
    """
    package.json composer.json pyproject.toml setup.cfg pytest.ini tox.ini phpunit.xml* vitest.config.* jest.config.*
    Makefile Gemfile Rakefile go.mod Cargo.toml pom.xml build.gradle* settings.gradle* compose*.y*ml docker-compose*.y*ml
    conftest.py noxfile.py .coveragerc jest.setup.* vitest.setup.* vitest.workspace.* .mocharc* karma.conf.*
    playwright.config.* cypress.config.* .rspec spec_helper.rb rails_helper.rb
    """.split()
)
# リンクをたどる回数の上限（循環するリンクで止まらないため）
LINK_HOPS = 40


def normalize(word: str) -> str | None:
    """語をルートからの相対パスへ正規化する。絶対パス・ルートの外・ルートそのものは None。"""
    if not word or word.startswith("/"):
        return None
    path = posixpath.normpath(word)
    if path == "." or path == ".." or path.startswith("../"):
        return None
    return path


def _ancestors(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


@dataclass(frozen=True)
class Protected:
    """保護するファイルの判定。`exact` は名指しのパス（とその親・リンク先）、`declared` は `test.protected_files`。"""

    exact: frozenset[str]
    declared: tuple[str, ...] = ()

    def covers(self, path: str) -> bool:
        p = normalize(path)
        if p is None:
            return False
        parts = p.split("/")
        if NDF_DIR in parts:
            return True
        if any(fnmatch.fnmatchcase(parts[-1], pat) for pat in PROTECTED_NAMES):
            return True
        return p in self.exact or (bool(self.declared) and scoped.in_scope(p, self.declared))


def _word_parts(word: str) -> tuple[str, ...]:
    """語と、`=` を含む語の `=` より後ろ。"""
    return (word, word.split("=", 1)[1]) if "=" in word else (word,)


def _words(commands: Iterable[str]) -> list[str]:
    """コマンドの語と、`=` を含む語の `=` より後ろ。"""
    return [part for text in commands for words in weight.split_commands(text) for w in words for part in _word_parts(w)]


def _named(word: str, search: Sequence[str]) -> set[str]:
    """語が名指すパス。`/` を含まない語は `search` の要素の下の同じ名前も。"""
    candidates = [word]
    if word and "/" not in word:
        candidates += [posixpath.join(e, word) if e not in ("", ".") else word for e in search]
    return {p for p in (normalize(c) for c in candidates) if p is not None}


def _closure(path: str, tree: Mapping[str, str | None]) -> set[str]:
    """パスと親と、ツリーの中のリンク先とその親。"""
    out = {path, *_ancestors(path)}
    for target in _link_targets(path, tree):
        out.update({target, *_ancestors(target)})
    return out


def _link_targets(path: str, tree: Mapping[str, str | None]) -> list[str]:
    """`path` の要素（途中のディレクトリと末尾）にツリーのリンクがあれば、先頭のリンクから 1 つずつ解決した並び。

    途中のディレクトリがリンクでも、実際に使われるファイルのパスまでたどる。ツリーの外を指せば止める。
    """
    found: list[str] = []
    current = path
    for _ in range(LINK_HOPS):
        nxt = _resolve_first(current, tree)
        if nxt is None or nxt in found or nxt == path:
            break
        found.append(nxt)
        current = nxt
    return found


def _resolve_first(path: str, tree: Mapping[str, str | None]) -> str | None:
    """`path` の要素を先頭から見て、最初のリンクをリンク先へ置き換えたパス。リンクが無いか外を指せば None。"""
    parts = path.split("/")
    for i in range(1, len(parts) + 1):
        prefix = "/".join(parts[:i])
        target = tree[prefix] if prefix in tree else None
        if target is None:
            continue
        if target.startswith("/"):
            return None
        base = normalize(posixpath.join(posixpath.dirname(prefix), target))
        return None if base is None else "/".join([base, *parts[i:]])
    return None


def protected_of(tree: Mapping[str, str | None], commands: Sequence[str], declared: Sequence[str], search: Sequence[str] = ()) -> Protected:
    """ツリー（パス → リンク先。リンクでなければ None）から、保護するファイルの判定を組む。"""
    named = {p for word in _words(commands) for p in _named(word, search)}
    exact = {p for path in named for p in _closure(path, tree)}
    clean = tuple(p for p in (normalize(d.rstrip("/")) for d in declared) if p is not None)
    return Protected(frozenset(exact), clean)


def inside_work(commands: Sequence[str], roots: Sequence[str]) -> tuple[str, ...]:
    """コマンドの語（リダイレクト先を含む）のうち、`roots` の配下（ルートそのものを含む）を指す絶対パスの語。"""
    bases = [posixpath.normpath(r) for r in roots if r.startswith("/")]

    def under(part: str) -> bool:
        path = posixpath.normpath(part)
        return part.startswith("/") and any(path == b or path.startswith(b.rstrip("/") + "/") for b in bases)

    hits = [word for text in commands for word in weight.all_words(text) if any(under(p) for p in _word_parts(word))]
    return tuple(dict.fromkeys(hits))


def narrow(scope: Sequence[str], tracked: Sequence[str], protected: Protected) -> tuple[str, ...]:
    """対象範囲の指定のうち、当たる追跡ファイルがすべて保護するファイルであるものを外す。

    当たるファイルが無い指定は、指定そのものが保護に当たるときだけ外す。
    """
    kept: list[str] = []
    for entry in scope:
        matched = [f for f in tracked if scoped.in_scope(f, (entry,))]
        drop = all(protected.covers(f) for f in matched) if matched else protected.covers(entry)
        if not drop:
            kept.append(entry)
    return tuple(kept)


def unprotected(touched: symbols_decide.Touched, protected: Protected | None) -> symbols_decide.Touched:
    """触る関数とファイルから保護するファイルを外す（`protected` が None ならそのまま）。"""
    if protected is None:
        return touched
    symbols = {p: s for p, s in touched.symbols.items() if not protected.covers(p)}
    return symbols_decide.Touched(symbols, tuple(p for p in touched.whole_files if not protected.covers(p)))
