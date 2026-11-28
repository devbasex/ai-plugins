"""要求仕様のコピーのうち、直してよい範囲を決める（#4543 の決定 7）。

要求の正は課題の本文なので、本文から写した範囲は直させない。直してよいのは、現在の本文にも、本文の編集履歴のどの版にも
無かった `## ` の見出しのセクション（コピーにだけ足した実装計画など）の中だけである。確かめられなければ、直してよいものは無い。
承認の前の確認（`ndf document-rebuild gate`・`editable`）と cross-review の修正担当への指示が、ここの判定だけを使う。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Optional

from ndf.adapters import markdown
from ndf.adapters.github import GitHub, GitHubError
from ndf.core import issue_docs

# 要求の範囲は、本文のこの見出しより前（無ければ全文）
PROGRESS = "進行"
_LEVEL = 2
_PAGE = 100
_SPACE = re.compile(r"\s+")
HISTORY_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){issue(number:$number)"
    f"{{userContentEdits(first:{_PAGE}){{totalCount nodes{{deletedAt diff}}}}}}}}}}"
)


def sections(text: str) -> list[tuple[str, str]]:
    """`## ` の見出しごとの（見出し, 中身）。最初の見出しの前と、`# ` の見出しから次の `## ` までの見出しは空文字列。"""
    lines = text.splitlines(keepends=True)
    heads = [(h.line, h.title.strip() if h.level == _LEVEL else "") for h in markdown.atx_headings(text) if h.level <= _LEVEL]
    bounds = [(0, ""), *heads]
    ends = [line for line, _ in heads] + [len(lines)]
    return [(title, "".join(lines[start:end])) for (start, title), end in zip(bounds, ends, strict=True)]


def requirement_titles(body: str) -> set[str]:
    """本文の要求の範囲にある `## ` の見出し。"""
    titles = [title for title, _ in sections(body)]
    if PROGRESS in titles:
        titles = titles[: titles.index(PROGRESS)]
    return {t for t in titles if t}


def unchanged_outside(before: str, after: str, editable: frozenset[str]) -> bool:
    """変わったのが直してよいセクションの中だけか。見出しの並びが同じで、ほかのセクションの中身が同じなら真
    （見出しの追加・削除・移動・改名と、同じ名前のセクションの新設は偽）。"""
    old, new = sections(before), sections(after)
    if [title for title, _ in old] != [title for title, _ in new]:
        return False
    return all(was == now for (title, was), (_, now) in zip(old, new, strict=True) if title not in editable)


def all_outside(quotes: Sequence[str], text: str, editable: frozenset[str]) -> bool:
    """残った文の引用が、すべて直してよいセクションの外にあると確かめられるか。"""
    if not editable:
        return True
    parts = sections(text)
    inside = [_SPACE.sub(" ", chunk) for title, chunk in parts if title in editable]
    outside = [_SPACE.sub(" ", chunk) for title, chunk in parts if title not in editable]

    def kept(quote: str) -> bool:
        found = _SPACE.sub(" ", quote).strip()
        return any(found in c for c in outside) and not any(found in c for c in inside)

    return all(kept(q) for q in quotes)


def _same(body: str) -> str:
    return body.replace("\r\n", "\n").strip()


def _past_bodies(hub: GitHub, number: int, current: str) -> Optional[list[str]]:
    """本文の編集履歴の版ごとの全文。読み切れないか、版の全文だと確かめられなければ None。

    GitHub の説明では `diff` は編集の要約である。実際の応答は版の全文で、現在の本文と同じ版がある（2026-11-28 に確かめた）。
    その版が無ければ、要約などの形と扱い、要求の範囲を復元できないとする。
    """
    edits: Any = hub.repository(HISTORY_QUERY, {"number": number})["issue"]["userContentEdits"]
    nodes = edits["nodes"]
    if edits["totalCount"] > len(nodes) or any(n["deletedAt"] or not n["diff"] for n in nodes):
        return None
    bodies = [n["diff"] for n in nodes]
    # 一度も編集していない本文は、版が 1 つも返らない
    if bodies and _same(current) not in {_same(body) for body in bodies}:
        return None
    return bodies


def editable_sections(hub: GitHub, number: int, copy: str) -> frozenset[str]:
    """コピーにだけあると確かめた見出し。編集履歴は、現在の本文に無い見出しが残ったときだけ読む。"""
    try:
        body = hub.issue_body(number).body
        current = requirement_titles(body)
        in_copy = {title for title, _ in sections(copy) if title}
        candidates = in_copy - current - {PROGRESS}
        # 本文の見出しがコピーにそろっていなければ、写した範囲を見分けられない
        if not candidates or not current <= in_copy:
            return frozenset()
        past = _past_bodies(hub, number, body)
    except (GitHubError, KeyError, TypeError):  # noqa: NDF401 GitHub: 読めなければ「見分けられない」として何も直させない
        return frozenset()
    if past is None:
        return frozenset()
    return frozenset(candidates.difference(*(requirement_titles(version) for version in past)))


def editable_of(hub_of: Callable[[], GitHub], cwd: Path, path: str) -> frozenset[str]:
    """作業場所の要求仕様のコピー `path` の直してよい見出し。コピーが読めなければ空で、GitHub へは問い合わせない。"""
    numbers = issue_docs.requirements_issues(path)
    file = cwd / path
    if not numbers or file.is_symlink():
        return frozenset()
    try:
        text = file.read_bytes().decode("utf-8", "replace")
        hub = hub_of()
    except (OSError, GitHubError):  # noqa: NDF401 ファイル: 読めないコピーと認証できない GitHub は、何も直させない
        return frozenset()
    return editable_sections(hub, min(numbers), text)
