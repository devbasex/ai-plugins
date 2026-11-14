"""落ちたテストだけを打ち直すコマンドを、宣言のテストの suite の `scope_command` から組む（#3473）。

- 落ちたテストの node id（`tests/a.py::t1` の形。`::` の前がファイル）を、そのファイルを `paths` に含む suite へ
  振り分ける。複数の suite の `paths` に入るなら、いちばん深い（長い）`paths` を持つ suite に入れ、同じ深さなら宣言の順で先の suite にする
- 振り分けた node id を持つ suite だけを、その `scope_command` の `{paths}` へシェルの引用を通して入れて打つ
- どの suite にも入らない node id があるか、入る suite が `{paths}` を持たなければ組まない（全体を打たない）
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from typing import Optional

PATHS_SLOT = "{paths}"
SCOPE_KEY = "scope_command"
# node id のうちファイルとテストの名前を分ける区切り
NODE_SEP = "::"
# `paths` を省いた suite の範囲（作業場所の全体）
WHOLE = "."


class RerunError(ValueError):
    """落ちたテストを打ち直すコマンドを宣言から組めない。"""


def node_file(node: str) -> str:
    """node id のファイルの部分。"""
    return node.split(NODE_SEP, 1)[0]


def _paths(suite: Mapping[str, object]) -> list[str]:
    raw = suite.get("paths")
    return [str(p) for p in raw] if isinstance(raw, list) and raw else [WHOLE]


def _depth(path: str, suite: Mapping[str, object]) -> Optional[int]:
    """`path` が suite の `paths` に入れば、入る `paths` のうち最も長いものの長さ（全体は 0）。入らなければ None。"""
    hits = []
    for loc in _paths(suite):
        base = loc.rstrip("/")
        if base in ("", WHOLE):
            hits.append(0)
        elif path == base or path.startswith(base + "/"):
            hits.append(len(base))
    return max(hits) if hits else None


def _owner(node: str, suites: Sequence[Mapping[str, object]]) -> Optional[int]:
    """node id を持つ suite の位置（いちばん深い `paths`、同じなら宣言の順で先）。どれにも入らなければ None。"""
    depths = [(d, -i) for i, s in enumerate(suites) if (d := _depth(node_file(node), s)) is not None]
    return -max(depths)[1] if depths else None


def commands(suites: Sequence[Mapping[str, object]], nodes: Sequence[str]) -> list[str]:
    """落ちたテスト `nodes` を持つ suite ごとの、`scope_command` の `{paths}` へ node id を入れたコマンド（宣言の順）。

    `suites` は宣言のテストの suite。組めなければ `RerunError`。
    """
    if not nodes:
        raise RerunError("落ちたテストの node id が無い")
    owned: dict[int, list[str]] = {}
    for node in nodes:
        at = _owner(node, suites)
        if at is None:
            raise RerunError(f"落ちたテスト {node} を持つテストの suite が宣言に無い")
        owned.setdefault(at, []).append(node)
    out = []
    for at in sorted(owned):
        template = suites[at].get(SCOPE_KEY)
        if not isinstance(template, str) or PATHS_SLOT not in template:
            raise RerunError(f"テストの suite {suites[at].get('name')} の {SCOPE_KEY} に {PATHS_SLOT} が無い")
        out.append(template.replace(PATHS_SLOT, shlex.join(owned[at])))
    return out
