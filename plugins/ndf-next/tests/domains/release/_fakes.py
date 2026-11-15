"""リリースのテストが共有する、ネットワークへ出ない GitHub の偽物と、手元の git のリポジトリの作り方。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import git_sandbox

from ndf.adapters import ci
from ndf.adapters.github import Authored, GitHubError, MergeOutcome, Pull
from ndf.domains.release import approval_decide, land, trail

HEAD = "a" * 40
OTHER = "b" * 40


def pull(number: int = 7, head: str = HEAD, base: str = "main", ref: str = "release/v1.2.4", merged: bool = False, body: str = "") -> Pull:
    return Pull(number, "open", merged, head, ref, base, body, "clean", "octocat", "OWNER")


@dataclass
class FakeHub:
    """`GitHub` の代わり。書いたコメントは `comments_of` に足し、REST の要求は `requests` に残す。"""

    repo: str = "o/r"
    pulls: dict[int, Pull] = field(default_factory=dict)
    comments_of: dict[int, list[Authored]] = field(default_factory=dict)
    trusted: set[str] = field(default_factory=lambda: {"octocat"})
    requests: list[tuple[str, str, Any]] = field(default_factory=list)
    responses: dict[tuple[str, str], Any] = field(default_factory=dict)
    merged: list[int] = field(default_factory=list)
    merge_methods: list[str | None] = field(default_factory=list)
    merge_required: list[bool] = field(default_factory=list)
    drop_comments: bool = False
    node_data: dict[int, dict] = field(default_factory=dict)
    runs: list[dict] = field(default_factory=list)
    status_list: list[dict] = field(default_factory=list)
    bot_apps: dict[str, int] = field(default_factory=dict)
    # compare で読むコミット（先端の SHA ごと）と、読んだ (base, head)
    commits_of: dict[str, list[dict]] = field(default_factory=dict)
    compared: list[tuple[str, str]] = field(default_factory=list)
    # マージキューに入っている PR と、キューから外した PR
    queued: set[int] = field(default_factory=set)
    dequeued: list[int] = field(default_factory=list)
    # head のブランチごとの PR（`pulls_by_head` が返す）
    heads: dict[str, list[Pull]] = field(default_factory=dict)

    def pull(self, number: int) -> Pull:
        return self.pulls[number]

    def pulls_by_head(self, branches) -> dict[str, list[Pull]]:
        return {b: list(self.heads.get(b, [])) for b in branches}

    def comments(self, number: int) -> list[Authored]:
        return list(self.comments_of.get(number, []))

    def trusts(self, authored: Authored) -> bool:
        return authored.author in self.trusted

    def token_trusted(self) -> bool:
        # 書いたコメントの書き手は octocat（`rest` の POST）
        return "octocat" in self.trusted

    def visibilities(self, repos: Any) -> dict[str, str]:
        return {r: "PRIVATE" for r in repos}

    def rest(self, method: str, path: str, payload: Any = None, params: Any = None) -> Any:
        self.requests.append((method, path, payload))
        if method == "POST" and path.endswith("/comments"):
            number = int(path.split("/")[-2])
            if not self.drop_comments:
                self.comments_of.setdefault(number, []).append(Authored("octocat", "OWNER", payload["body"]))
            return {"html_url": f"https://example.invalid/{number}"}
        got = self.responses.get((method, path))
        if isinstance(got, Exception):
            raise got
        return got if got is not None else {}

    def branch_rules(self, branch: str) -> list[dict]:
        got = self.rest("GET", f"/repos/{self.repo}/rules/branches/{branch}")
        return got if isinstance(got, list) else []

    def nodes(self, numbers: Any, node: str, fields: str) -> dict[int, dict]:
        return {int(n): self.node_data.get(int(n), {}) for n in numbers}

    def commit_parents(self, sha: str) -> list[str]:
        return [f"parent-of-{sha}"]

    def compare_commits(self, base: str, head: str) -> list[dict]:
        self.compared.append((base, head))
        return list(self.commits_of.get(head, []))

    def check_runs(self, sha: str) -> list[dict]:
        return list(self.runs)

    def statuses(self, sha: str) -> list[dict]:
        return list(self.status_list)

    def bot_app(self, login: str) -> int | None:
        return self.bot_apps.get(login)

    def merge(self, number: int, expect_head: str | None = None, method: str | None = None, require_method: bool = False) -> MergeOutcome:
        self.merged.append(number)
        self.merge_methods.append(method)
        self.merge_required.append(require_method)
        return MergeOutcome(True, None, f"#{number} をマージした", self.pulls[number].head_sha)

    def in_merge_queue(self, number: int) -> bool:
        return number in self.queued

    def dequeue(self, number: int) -> None:
        self.queued.discard(number)
        self.dequeued.append(number)


def not_found() -> GitHubError:
    return GitHubError("無い", 404)


def trail_comment(answer: str, sha: str, author: str = "octocat") -> Authored:
    """利用者が担い手の証跡のコメント。"""
    return Authored(author, "OWNER", approval_decide.render_trail(answer, sha, trail.USER_BY))


def ci_reading(failed: bool) -> ci.Reading:
    """必須のチェックの読み（`failed` なら pytest が落ちている）。"""
    bad = [ci.Check("pytest", "done", "failure", 1)] if failed else []
    return ci.Reading(bad, [], [], [], [], [], True)


def _fixed_wait(failed: bool, calls: list[int]) -> Callable[[Callable[[], ci.Reading], float], ci.WatchResult]:
    def wait(reader: Callable[[], ci.Reading], budget: float) -> ci.WatchResult:
        calls.append(1)
        return ci.WatchResult(ci_reading(failed), 1, 0.0, 0.0)

    return wait


def release_lander(hub: FakeHub, failed: bool, calls: list[int]) -> land.Lander:
    """本番ブランチ main（必須のチェック pytest）の Lander。待ちは 1 回で `failed` の読みを返す。"""
    hub.responses[("GET", "/repos/o/r/rules/branches/main")] = [
        {
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": "pytest"}, {"context": approval_decide.STATUS_CONTEXT}]},
        }
    ]
    hub.responses[("GET", "/repos/o/r/rules/branches/develop")] = []
    return land.Lander(hub, "main", 60.0, wait=_fixed_wait(failed, calls))


def configure(path: Path) -> Path:
    """コミットの作者と署名なしを、そのリポジトリだけに設定する。"""
    git_sandbox.sh(path, "config", "user.email", "t@example.invalid")
    git_sandbox.sh(path, "config", "user.name", "t")
    git_sandbox.sh(path, "config", "commit.gpgsign", "false")
    return path


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git_sandbox.sh(path, "init", "-q", "-b", "develop")
    return configure(path)


def clone(tmp_path: Path, origin: Path, name: str, branch: str = "develop") -> Path:
    """origin のブランチを `tmp_path/<name>` へ clone し、設定する。"""
    path = tmp_path / name
    git_sandbox.sh(tmp_path, "clone", "-q", "-b", branch, str(origin), str(path))
    return configure(path)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
