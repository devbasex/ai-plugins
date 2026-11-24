"""`ndf ci merge`・`ask`・`answer` のテストが共有する、ネットワークへ出ない GitHub の偽物（#4409）。

PR・マージ先のブランチごとの設定（`.ndf/project.json` の本文と blob の oid）・コメントを持ち、マージとコメントの投稿を
記録する。`GitHub.merge` の照合（`expect`）は、PR の今のマージ先・HEAD と設定の oid を照らすだけの単純な形で真似る。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from ndf.adapters.github import Authored, FileAt, MergeExpect, MergeOutcome, Pull

REPO = "o/r"
HEAD = "a" * 40
OTHER = "b" * 40


def strategy(base: str = "main", production: str = "main", deliveries: Any = "auto", cd: Any = None) -> dict:
    """`branch_strategy` のセクション。`deliveries` は `auto`・`manual`・`[]`・None（キーを置かない）か任意の値。"""
    release: dict[str, Any] = {"cd": cd}
    if deliveries == "auto":
        release["deliveries"] = [{"target": "対象", "kind": "auto", "channel": "production"}]
    elif deliveries == "manual":
        release["deliveries"] = [{"target": "対象", "kind": "manual", "channel": "production"}]
    elif deliveries is not None:
        release["deliveries"] = deliveries
    return {"branches": {"base": base, "production": production}, "release": release}


def project_text(section: Optional[dict]) -> str:
    return json.dumps({"version": 1, "branch_strategy": section} if section is not None else {"version": 1})


def pull(number: int = 7, head: str = HEAD, base: str = "main", ref: str = "sprint/x", merged: bool = False, queued: bool = False) -> Pull:
    return Pull(number, "closed" if merged else "open", merged, head, ref, base, "", "clean", "octocat", "OWNER", "", queued)


@dataclass
class MergeHub:
    """`GitHub` の代わり。`files` はブランチ → (本文, oid)、`merged` はマージした PR の番号、`calls` は呼んだ口の名前。"""

    repo: str = REPO
    pulls_of: dict[int, Pull] = field(default_factory=lambda: {7: pull()})
    files: dict[str, tuple[Optional[str], Optional[str]]] = field(default_factory=lambda: {"main": (project_text(strategy()), "c1")})
    comments_of: dict[int, list[Authored]] = field(default_factory=dict)
    login: str = "octocat"
    file_repo: Optional[str] = None
    merged: list[int] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    dequeued: list[int] = field(default_factory=list)
    # マージの直前の照合で返す設定（ブランチ → (本文, oid)）。無ければ `files`
    files_at_merge: Optional[dict[str, tuple[Optional[str], Optional[str]]]] = None
    # 照合で見せる PR（判定の後に変わった PR）。無ければ `pulls_of`
    pull_at_merge: Optional[Pull] = None

    def pulls_by_head(self, branches: Any) -> dict[str, list[Pull]]:
        self.calls.append("pulls_by_head")
        return {b: [p for p in self.pulls_of.values() if p.head_ref == b] for b in branches}

    def pulls(self, numbers: Any) -> dict[int, Optional[Pull]]:
        self.calls.append("pulls")
        return {int(n): self.pulls_of.get(int(n)) for n in numbers}

    def pull(self, number: int) -> Pull:
        self.calls.append("pull")
        return self.pulls_of[number]

    def branch_file(self, base: str, path: str) -> FileAt:
        self.calls.append("branch_file")
        text, oid = self.files.get(base, (None, None))
        return FileAt(self.file_repo or self.repo, text, oid)

    def viewer_login(self) -> str:
        return self.login

    def comments(self, number: int) -> list[Authored]:
        self.calls.append("comments")
        return list(self.comments_of.get(number, []))

    def visibilities(self, repos: Any) -> dict[str, str]:
        return {r: "PRIVATE" for r in repos}

    def rest(self, method: str, path: str, payload: Any = None, params: Any = None) -> Any:
        if method == "POST" and path.endswith("/comments"):
            self.calls.append("post_comment")
            number = int(path.split("/")[-2])
            self.comments_of.setdefault(number, []).append(Authored(self.login, "OWNER", payload["body"]))
            return {"html_url": f"https://example.invalid/{number}"}
        return {}

    def dequeue(self, number: int) -> None:
        self.dequeued.append(number)

    def merge(
        self,
        number: int,
        expect_head: Optional[str] = None,
        method: Optional[str] = None,
        require_method: bool = False,
        jump: bool = False,
        expect: Optional[MergeExpect] = None,
    ) -> MergeOutcome:
        self.calls.append("merge")
        now = self.pull_at_merge or self.pulls_of[number]
        if expect is not None:
            if now.base_ref != expect.base or now.head_sha != expect.head:
                return MergeOutcome(False, "refused", "照合で変わった", now.head_sha)
            text, oid = (self.files_at_merge or self.files).get(expect.base, (None, None))
            if oid != expect.config_oid:
                stop = expect.rejudge(text)
                if stop is not None:
                    return stop
        self.merged.append(number)
        return MergeOutcome(True, None, f"#{number} をマージした", now.head_sha)
