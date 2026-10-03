"""落ちたテストの本文に現れるパス（#1649 決定 2）。

全体テストで変更起因として落ちたテストの本文（JUnit の `failure` の `message` と本文、または走らせ直しのログ）と、
変更したファイルのパスを突き合わせる。ファイルも git も読まない純粋な関数だけを置く。cross-refactoring の原因の判定が
使い、CI の失敗ログと変更ファイルを突き合わせる処理（cross-review など）もそのまま使える。

パスはリポジトリからの相対パスで渡す。本文に部分文字列として現れれば一致とする（絶対パスで書かれていても、
相対パスはその末尾に含まれる）。前後が識別子の文字やパスの区切りに続くときは一致としない（`a/b.py` が
`xa/b.py` や `a/b.pyc` に当たらない）。
"""

from __future__ import annotations

import re
from typing import Iterable


def _pattern(path: str) -> "re.Pattern[str]":
    # 前は識別子の文字・`.`・`-` でない（`/` は絶対パスの区切りとして許す）。後ろは識別子の文字・`/`・`-`・拡張子の続きでない
    return re.compile(rf"(?<![\w.-]){re.escape(path)}(?![\w/-]|\.\w)")


def mentioned(text: str, paths: Iterable[str], exclude: Iterable[str] = ()) -> list[str]:
    """`paths` のうち `text` に現れるものを、渡した順で返す。`exclude` に入るパスは数えない。"""
    body = str(text or "").replace("\\", "/")
    skip = {str(p) for p in exclude}
    out: list[str] = []
    for raw in paths:
        path = str(raw or "").strip()
        if path.startswith("./"):
            path = path[2:]
        if not path or path in skip or path in out:
            continue
        if path in body and _pattern(path).search(body):
            out.append(path)
    return out
