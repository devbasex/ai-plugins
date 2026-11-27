"""テストの定義のファイルの判定（#4566 の決定 6）。ファイルもプロセスも触らない。git を読むのは `local_gate` である。

手元の経路では、この実行の変更が自分を合格させる基準にならないよう、次のファイルを対象範囲から外し、push の前に
変えていないかを確かめる。パスはリポジトリのルートからの相対で、PR のブランチの先頭のツリーで解決する。

- どこかの階層が `.ndf`
- 名前が決まった名前の一覧（`PROTECTED_NAMES`）に当たる（どの階層でも）
- テストのコマンドの語をパスにしたもの。親のディレクトリをファイルかリンクに置き換える変更（親のパスそのもの）も当たる。
  ツリーにまだ無いパスも当たる。シンボリックリンクはリンクとリンク先（ツリーの中だけ）の両方
- `/` を含まない語について、`search`（`PATH` の相対の要素。空と `.` はルート）の下の同じ名前のパス
- 設定 `test.protected_files` の指定の中（`scoped.in_scope`）
"""

from __future__ import annotations

import fnmatch
import posixpath
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from ndf.testing import scoped, weight

NDF_DIR = ".ndf"
# 依存とテストの定義の決まった名前と、テストランナーが自動で読むファイル（どの階層でも。設計文書の表）
PROTECTED_NAMES = (
    "package.json", "composer.json", "pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini", "phpunit.xml*",
    "vitest.config.*", "jest.config.*", "Makefile", "Gemfile", "Rakefile", "go.mod", "Cargo.toml", "pom.xml",
    "build.gradle*", "settings.gradle*", "compose*.y*ml", "docker-compose*.y*ml",
    "conftest.py", "noxfile.py", ".coveragerc", "jest.setup.*", "vitest.setup.*", "vitest.workspace.*", ".mocharc*",
    "karma.conf.*", "playwright.config.*", "cypress.config.*", ".rspec", "spec_helper.rb", "rails_helper.rb",
)  # fmt: skip
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


def _parents(path: str) -> list[str]:
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


def _words(commands: Iterable[str]) -> list[str]:
    """コマンドの語と、`=` を含む語の `=` より後ろ。"""
    out: list[str] = []
    for text in commands:
        for words in weight.split_commands(text):
            for w in words:
                out.append(w)
                if "=" in w:
                    out.append(w.split("=", 1)[1])
    return out


def _link_targets(path: str, tree: Mapping[str, str | None]) -> list[str]:
    """`path` がツリーのリンクなら、ツリーの中のリンク先を順にたどった並び（外を指せば止める）。"""
    found: list[str] = []
    current = path
    for _ in range(LINK_HOPS):
        target = tree.get(current)
        if target is None:
            break
        nxt = normalize(posixpath.join(posixpath.dirname(current), target)) if not target.startswith("/") else None
        if nxt is None or nxt in found or nxt == path:
            break
        found.append(nxt)
        current = nxt
    return found


def protected_of(
    tree: Mapping[str, str | None], commands: Sequence[str], declared: Sequence[str], search: Sequence[str] = ()
) -> Protected:
    """ツリー（パス → リンク先。リンクでなければ None）から、保護するファイルの判定を組む。"""
    named: set[str] = set()
    for word in _words(commands):
        path = normalize(word)
        if path is not None:
            named.add(path)
        if "/" not in word and word:
            for element in search:
                joined = normalize(posixpath.join(element, word) if element not in ("", ".") else word)
                if joined is not None:
                    named.add(joined)
    exact: set[str] = set()
    for path in named:
        exact.add(path)
        exact.update(_parents(path))
        for target in _link_targets(path, tree):
            exact.add(target)
            exact.update(_parents(target))
    clean = tuple(p for p in (normalize(d.rstrip("/")) for d in declared) if p is not None)
    return Protected(frozenset(exact), clean)


def inside_work(commands: Sequence[str], roots: Sequence[str]) -> tuple[str, ...]:
    """コマンドの語（リダイレクト先を含む）のうち、`roots` の配下（ルートそのものを含む）を指す絶対パスの語。"""
    bases = [posixpath.normpath(r) for r in roots if r.startswith("/")]
    hits: list[str] = []
    for text in commands:
        for word in weight.all_words(text):
            for part in (word, *([word.split("=", 1)[1]] if "=" in word else [])):
                if not part.startswith("/"):
                    continue
                path = posixpath.normpath(part)
                if any(path == b or path.startswith(b.rstrip("/") + "/") for b in bases):
                    hits.append(word)
                    break
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
