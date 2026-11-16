"""workflow_trigger.py: workflow の定義が PR の head に検査を作るかを、`on` の絞り込みと PR の事実から決める（#3672）。

`land` が検査の 0 件を「検査なし」で通すか「始まるのを待つ」で止めるかに使う。照らすのは GitHub の絞り込みの書き方
（branches・branches-ignore・paths・paths-ignore・tags）で、読めない定義と読めない事実は動くとみなす（検査なしで通さない側へ倒す）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_HEAD_EVENTS = ("pull_request", "pull_request_target")


@dataclass(frozen=True)
class PullEvent:
    """workflow の絞り込みに照らす PR の事実。読めない値は None で、その絞り込みは動くとみなす（検査なしで通さない側へ倒す）。"""

    head_ref: str | None = None  # head のブランチ名（push の branches・branches-ignore）
    base: str | None = None  # 宛先のブランチ名（pull_request の branches・branches-ignore）
    files: Any = lambda: None  # () -> 変更ファイルの並び | None（pull_request の paths・paths-ignore）


def _filter_re(pattern: str) -> re.Pattern:
    """GitHub の絞り込みの書き方（`*` は `/` を除く・`**` はすべて・`?` は 1 文字・`+` は直前の 1 回以上・`[]` は文字の組）を正規表現へ直す。"""
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**", i):
            out, i = out + ".*", i + 2
            continue
        if c == "[" and (end := pattern.find("]", i + 1)) > i:
            out, i = out + pattern[i : end + 1], end + 1
            continue
        out += {"*": "[^/]*", "?": "[^/]", "+": "+"}.get(c, re.escape(c))
        i += 1
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


def _pull_runs(spec: Any, event: PullEvent) -> bool:
    """pull_request の絞り込みが PR に当たるか。branches は宛先、paths・paths-ignore は変更ファイルと照らす（両方が当たって動く）。"""
    if not isinstance(spec, dict):
        return True
    if _ref_runs(spec, event.base, "branches") is False:
        return False
    if "paths" not in spec and "paths-ignore" not in spec:
        return True
    files = event.files()
    if not files or ("paths" in spec and "paths-ignore" in spec):
        return True  # 読めない・空の差分・両方を書いた（GitHub が受けない）形は動くとみなす
    if "paths" in spec:
        return any(_filter_hits(spec.get("paths") or [], f) for f in files)
    return not all(_filter_hits(spec.get("paths-ignore") or [], f) for f in files)


def _push_runs(spec: Any, event: PullEvent) -> bool:
    """push の絞り込みが head のブランチへの push に当たるか。tags だけを絞った push はブランチの push で動かない。"""
    if not isinstance(spec, dict):
        return True
    branch = _ref_runs(spec, event.head_ref, "branches")
    if branch is not None:
        return branch
    return not any(k in spec for k in ("tags", "tags-ignore"))


def runs_on_pull_head(text: str | None, event: PullEvent | None = None) -> bool:
    """workflow の定義（YAML の字面）が PR の head に検査を作るか。`on` の pull_request・pull_request_target が絞り込み
    （宛先の branches・変更ファイルの paths）でこの PR に当たるか、push が絞り込み（branches・branches-ignore）で head の
    ブランチに当たれば真。読めない定義と、照らす事実（`event`）を読めない絞り込みは動くとみなす（検査なしで通さない側へ倒す）。"""
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
