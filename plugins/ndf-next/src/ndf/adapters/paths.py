"""パスのパターン照合のアダプター。照合は pathspec だけが行い、glob を正規表現へ自分で変換しない。

パターンは gitignore の書き方で、**リポジトリの根から見たパス全体**に当てる（`.gitignore` と違い、`/` を
含まないパターンも深い階層のファイル名には当たらない）。

| パターン | 当たる | 当たらない |
| --- | --- | --- |
| `*.md` | `README.md` | `docs/a.md`（`*` は `/` をまたがない） |
| `docs/*.md` | `docs/a.md` | `docs/x/a.md` |
| `docs/**` | `docs/a.md`・`docs/x/a.md` | `docs` |
| `**/*.md` | `README.md`・`docs/x/a.md` | |
| `docs/` | `docs/a.md`（ディレクトリの中身） | `docsx/a.md` |

`!` で始まるパターンは、前のパターンが当てたものを外す（`.gitignore` と同じ）。
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache

import pathspec

# pathspec のパターンの種類（gitignore の書き方）
PATTERN_KIND = "gitignore"


def _anchored(pattern: str) -> str:
    """根に錨を下ろしたパターンにする（`!` は保つ。先頭の `./` は外す）。"""
    negated = pattern.startswith("!")
    body = pattern[1:] if negated else pattern
    body = body.removeprefix("./")
    if not body.startswith("/"):
        body = "/" + body
    return ("!" if negated else "") + body


@lru_cache(maxsize=256)
def _spec(patterns: tuple[str, ...]) -> pathspec.PathSpec:
    return pathspec.PathSpec.from_lines(PATTERN_KIND, [_anchored(p) for p in patterns if p.strip()])


def path_matches(path: str, patterns: Iterable[str]) -> bool:
    """パス（根からの相対。`./` で始まってもよい）にパターンのどれかが当たり、後の `!` で外されないかを返す。"""
    return _spec(tuple(patterns)).match_file(path.removeprefix("./"))


def matching(paths: Iterable[str], patterns: Iterable[str]) -> list[str]:
    """パスのうち、パターンに当たるものを元の順で返す。"""
    spec = _spec(tuple(patterns))
    return [p for p in paths if spec.match_file(p.removeprefix("./"))]
