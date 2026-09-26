"""パスのパターン照合の包み（#1142 の決定 19・種類 10）。pathspec を呼ぶのはこのモジュールだけである。

パターンは git の wildmatch の書き方で、**リポジトリの根から見たパス全体**に当てる（`.gitignore` と違い、
`/` を含まないパターンも深い階層のファイル名には当たらない）。

| パターン | 当たる | 当たらない |
| --- | --- | --- |
| `*.md` | `README.md` | `docs/a.md`（`*` は `/` をまたがない） |
| `docs/*.md` | `docs/a.md` | `docs/x/a.md` |
| `docs/**` | `docs/a.md`・`docs/x/a.md` | `docs` |
| `**/*.md` | `README.md`・`docs/x/a.md` | |
| `docs/` | `docs/a.md`（ディレクトリの中身） | `docsx/a.md` |

`!` で始まるパターンは、前のパターンが当てたものを外す（`.gitignore` と同じ）。

今までの照合との違い: `glossary.declared_path_matches` と `collect._matches_file_pattern` は fnmatch で、`*` が
`/` をまたいだ（`*.md` が `docs/a.md` に当たった）。`lib/pace.py:glob_re` は今と同じく `/` をまたがない。

使う側は `deps.require("pathmatch")` を先に呼ぶ。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Iterable

import pathspec


def _anchored(pattern: str) -> str:
    """根に錨を下ろしたパターンにする（`!` は保つ。`**/` で始まるものは錨が無くても同じ）。"""
    neg = pattern.startswith("!")
    body = pattern[1:] if neg else pattern
    if not body.startswith("/"):
        body = "/" + body
    return ("!" if neg else "") + body


@lru_cache(maxsize=256)
def _spec(patterns: tuple[str, ...]) -> pathspec.PathSpec:
    lines = [_anchored(p.strip()) for p in patterns if p.strip() and not p.strip().startswith("#")]
    return pathspec.PathSpec.from_lines("gitwildmatch", lines)


def path_matches(path: str, patterns: Iterable[str]) -> bool:
    """`path`（根からの相対。`/` 区切り）がパターンのどれかに当たるか。先頭の `./` は外す。"""
    rel = path[2:] if path.startswith("./") else path
    return _spec(tuple(patterns)).match_file(rel)


def filter_paths(paths: Iterable[str], patterns: Iterable[str]) -> list[str]:
    """パターンに当たるパスだけを、渡した順のまま返す。"""
    spec = tuple(patterns)
    return [p for p in paths if path_matches(p, spec)]
