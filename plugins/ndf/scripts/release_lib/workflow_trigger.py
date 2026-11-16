"""workflow_trigger.py: workflow の定義が PR の head に検査を作るかを、`on` の絞り込みと PR の事実から決める（#3672）。

`land` が検査の 0 件を「検査なし」で通すか「始まるのを待つ」で止めるかに使う。照らすのは GitHub の絞り込みの書き方
（branches・branches-ignore・paths・paths-ignore・tags）で、読めない定義と読めない事実は動くとみなす（検査なしで通さない側へ倒す）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# PR の head に check run を作るのは pull_request だけ。pull_request_target は宛先のコミットで動き、結果は head に付かない
_HEAD_EVENTS = ("pull_request",)
# PR の head への push で起動する種類。`types` を書かなければこの 3 つで動く（GitHub の既定）
_HEAD_TYPES = frozenset({"opened", "synchronize", "reopened"})


@dataclass(frozen=True)
class PullEvent:
    """workflow の絞り込みに照らす PR の事実。読めない値は None で、その絞り込みは動くとみなす（検査なしで通さない側へ倒す）。"""

    head_ref: str | None = None  # head のブランチ名（push の branches・branches-ignore）
    base: str | None = None  # 宛先のブランチ名（pull_request の branches・branches-ignore）
    files: Any = lambda: None  # () -> PR の変更ファイル（三点の差分）の並び | None（pull_request の paths・paths-ignore）
    push_files: Any = lambda: None  # () -> 直近の push の変更ファイル（二点の差分）| None（push の paths）


def _filter_re(pattern: str) -> re.Pattern:
    """GitHub の絞り込みの書き方を正規表現へ直す。`*` は `/` を除く 0 文字以上・`**` はすべて（`**/` はディレクトリ 0 個にも当たる）・
    `?` は直前の 1 文字の 0〜1 回・`+` は直前の 1 文字の 1 回以上・`[]` は文字の組（GitHub の Filter pattern cheat sheet）。
    直前が 1 文字（字面の文字か `[]`）でない `?`・`+` は字面の文字として扱う。"""
    out, i, atom = "", 0, False  # atom: 直前に置いたのが 1 文字（`?`・`+` を掛けられる）か
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out, i, atom = out + "(?:.*/)?", i + 3, False
        elif pattern.startswith("**", i):
            out, i, atom = out + ".*", i + 2, False
        elif c == "[" and (end := pattern.find("]", i + 1)) > i:
            out, i, atom = out + pattern[i : end + 1], end + 1, True
        elif c in "?+" and atom:
            out, i, atom = out + c, i + 1, False
        else:
            out, i, atom = out + ("[^/]*" if c == "*" else re.escape(c)), i + 1, c != "*"
    return re.compile(out + r"\Z")


def _filter_hits(patterns: Any, value: str) -> bool:
    """`value` がパターンの並びに当たるか。`!` で始まるパターンは前のパターンが当てたものを外す（後のものが勝つ）。"""
    hit = False
    for p in patterns if isinstance(patterns, list) else [patterns]:
        p = str(p)
        neg = p.startswith("!")
        if _filter_re(p[1:] if neg else p).match(value):
            hit = not neg
    return hit


def _ref_runs(spec: dict, name: str | None, key: str) -> bool | None:
    """`key`（branches）と `key-ignore` の絞り込みが `name` に当たるか。絞り込みが無ければ None、名前を読めなければ真。"""
    if key in spec:
        return name is None or _filter_hits(spec.get(key) or [], name)
    if f"{key}-ignore" in spec:
        return name is None or not _filter_hits(spec.get(f"{key}-ignore") or [], name)
    return None


def _paths_run(spec: dict, read_files) -> bool:
    """paths・paths-ignore の絞り込みが変更ファイル（`read_files()`）に当たるか。照らす差分はイベントで違う
    （pull_request は PR の三点の差分、push はその push の二点の差分）。読めない・空の差分・両方を書いた（GitHub が受けない）形は動くとみなす。"""
    if "paths" not in spec and "paths-ignore" not in spec:
        return True
    files = read_files()
    if not files or ("paths" in spec and "paths-ignore" in spec):
        return True
    if "paths" in spec:
        return any(_filter_hits(spec.get("paths") or [], f) for f in files)
    return not all(_filter_hits(spec.get("paths-ignore") or [], f) for f in files)


def _pull_runs(spec: Any, event: PullEvent) -> bool:
    """pull_request の絞り込みが PR の head への push に当たるか。types は opened・synchronize・reopened のどれかを含むとき
    （書かなければ既定でこの 3 つ）だけ動く。branches は宛先、paths・paths-ignore は PR の変更ファイルと照らす（すべてが当たって動く）。"""
    if not isinstance(spec, dict):
        return True
    types = spec.get("types")
    if types is not None and not _HEAD_TYPES & {str(t) for t in (types if isinstance(types, list) else [types])}:
        return False
    if _ref_runs(spec, event.base, "branches") is False:
        return False
    return _paths_run(spec, event.files)


def _push_runs(spec: Any, event: PullEvent) -> bool:
    """push の絞り込みが head のブランチへの push に当たるか。branches は head のブランチ、paths・paths-ignore はその push の
    変更ファイル（before..after の二点の差分。PR の三点の差分に無いファイルも含みうる）と照らす（両方が当たって動く）。
    tags だけを絞った push はブランチの push で動かない。"""
    if not isinstance(spec, dict):
        return True
    branch = _ref_runs(spec, event.head_ref, "branches")
    if branch is False or (branch is None and any(k in spec for k in ("tags", "tags-ignore"))):
        return False
    return _paths_run(spec, event.push_files)


def runs_on_pull_head(text: str | None, event: PullEvent | None = None) -> bool:
    """workflow の定義（YAML の字面）が PR の head に検査を作るか（pull_request_target は宛先のコミットで動くため数えない）。`on` の pull_request が絞り込み
    （起動の種類の types・宛先の branches・PR の変更ファイルの paths）でこの PR に当たるか、push が絞り込み（head のブランチの
    branches・直近の push の変更ファイルの paths）に当たれば真。読めない定義と、照らす事実（`event`）を読めない絞り込みは動くとみなす（検査なしで通さない側へ倒す）。"""
    import yamlio

    event = event or PullEvent()
    try:
        doc = yamlio.load_yaml(text, "workflow") if text is not None else None
    except yamlio.YamlError:
        doc = None
    if not isinstance(doc, dict):
        return True
    on = doc.get("on", doc.get(True))  # YAML 1.1 の読み手では `on` が真偽値のキーになる
    if isinstance(on, str):
        on = {on: None}
    elif isinstance(on, list):
        on = {str(e): None for e in on}
    if not isinstance(on, dict):
        return True
    if any(_pull_runs(on[e], event) for e in _HEAD_EVENTS if e in on):
        return True
    return "push" in on and _push_runs(on["push"], event)
