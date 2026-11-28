"""承認の対象（PR とマージ先の設定）を読む（`ndf.progress.gate` の部品。#4520）。

1 回の呼び出しの中では、対象の PR とマージ先の設定を 1 回だけ読む。工程のステップを流した後は、ステップが PR へ
push しうるので `forget` で捨てて読み直す。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, NamedTuple, Optional

from ndf.adapters.git import GitError
from ndf.adapters.github import GitHub, GitHubError
from ndf.core import production_merge
from ndf.progress import model
from ndf.progress.gate.asking import Question, Target, text_sha256


class Reading(NamedTuple):
    """読んだ対象と、PR がマージ済みか。承認資料のハッシュは持たない（呼び手が保存したファイルから足す）。

    `head_ref` と `cross_repository` は head のブランチと、それがフォークにあるか（`ndf progress question --pr` の確かめ）。"""

    target: Target
    merged: bool
    head_ref: Optional[str] = None
    cross_repository: bool = False


class TargetReader:
    """`hub` は GitHub を返す口（`answering.shared_hub`）。読んだ PR とマージ先の設定は `forget` まで使い回す。"""

    def __init__(self, hub: Callable[[], GitHub]) -> None:
        self._hub = hub
        self._pulls: dict[int, Any] = {}
        self._configs: dict[str, Any] = {}

    def forget(self) -> None:
        self._pulls.clear()
        self._configs.clear()

    def read(self, q: Question, *, config: bool = True) -> Reading:
        """質問の対象。`config` が偽ならマージ先の設定を読まず、head・本文・題だけを返す。読めなければ `model.Refused`。"""
        if q.pr is None:
            return Reading(Target(), False)
        found = None
        try:
            hub = self._hub()
            if q.pr not in self._pulls:
                self._pulls[q.pr] = hub.pull(q.pr)
            pull = self._pulls[q.pr]
            if config:
                if pull.base_ref not in self._configs:
                    self._configs[pull.base_ref] = hub.branch_file(pull.base_ref, production_merge.PROJECT_FILE)
                found = self._configs[pull.base_ref]
        except (GitHubError, GitError) as exc:
            raise model.Refused(f"PR #{q.pr} の head を読めない（マージ先も読めない）ので、回答と対象を照らせない: {exc}") from None
        seen = Target(pull.head_sha, body=text_sha256(pull.body), title=text_sha256(pull.title))
        if found is None:
            return Reading(seen, pull.merged, pull.head_ref, pull.cross_repository)
        verdict = production_merge.judge_project_text(found.text, pull.base_ref, source_repo=found.repo, repo=hub.repo)
        production = production_merge.reaches_production(verdict)
        bound = Target(seen.head, None, hub.repo, pull.number, pull.base_ref, production, body=seen.body, title=seen.title)
        return Reading(bound, pull.merged, pull.head_ref, pull.cross_repository)
