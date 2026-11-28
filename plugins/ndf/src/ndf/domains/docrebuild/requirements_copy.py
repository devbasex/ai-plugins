"""要求仕様のコピーのうち、承認の前の確認が直してよいセクションを決める（#4543 の決定 7）。

要求の正は課題の本文なので、本文から写した範囲は直させない。直してよいのは、現在の本文にも、本文の編集履歴のどの版にも
無かった `## ` の見出しのセクション（コピーにだけ足した実装計画など）だけである。確かめられなければ、直してよいものは無い。
"""

from __future__ import annotations

from typing import Any, Optional

from ndf.adapters import markdown
from ndf.adapters.github import GitHub, GitHubError

# 要求の範囲は、本文のこの見出しより前（無ければ全文）
PROGRESS = "進行"
_LEVEL = 2
_PAGE = 100
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


def protected(text: str, editable: frozenset[str]) -> str:
    """直してよいセクションを除いた残り（直しの前後でここが同じであることを確かめる）。"""
    return "".join(chunk for title, chunk in sections(text) if title not in editable)


def _past_bodies(hub: GitHub, number: int) -> Optional[list[str]]:
    """本文の編集履歴の版ごとの全文。読み切れなければ None。"""
    edits: Any = hub.repository(HISTORY_QUERY, {"number": number})["issue"]["userContentEdits"]
    nodes = edits["nodes"]
    if edits["totalCount"] > len(nodes) or any(n["deletedAt"] or not n["diff"] for n in nodes):
        return None
    return [n["diff"] for n in nodes]


def editable_sections(hub: GitHub, number: int, copy: str) -> frozenset[str]:
    """コピーにだけあると確かめた見出し。編集履歴は、現在の本文に無い見出しが残ったときだけ読む。"""
    try:
        current = requirement_titles(hub.issue_body(number).body)
        in_copy = {title for title, _ in sections(copy) if title}
        candidates = in_copy - current - {PROGRESS}
        # 本文の見出しがコピーにそろっていなければ、写した範囲を見分けられない
        if not candidates or not current <= in_copy:
            return frozenset()
        past = _past_bodies(hub, number)
    except (GitHubError, KeyError, TypeError):  # noqa: NDF401 GitHub: 読めなければ「見分けられない」として何も直させない
        return frozenset()
    if past is None:
        return frozenset()
    return frozenset(candidates.difference(*(requirement_titles(body) for body in past)))
