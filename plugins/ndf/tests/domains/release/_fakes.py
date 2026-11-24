"""リリースのテストが共有する、ネットワークへ出ない GitHub の偽物と、手元の git のリポジトリの作り方。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import git_sandbox
from strategy_fixture import KINDS, section  # noqa: F401 代表の 5 種の設定（10 系のテストも同じファイルを読む）

from ndf.adapters.github import Authored, GitHubError, MergeOutcome, Pull
from ndf.adapters.github_rules import RuleFacts, rule_facts_of
from ndf.core.branch_strategy import Strategy

HEAD = "a" * 40
OTHER = "b" * 40


def strategy(kind: str = "web-develop", **merge: Any) -> Strategy:
    """種類 `kind` の設定。`merge` は `merge` の値を差し替える（`admin=True` など）。"""
    got = section(kind)
    got["merge"].update(merge)
    return Strategy(got)


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
    # compare で読むコミット（HEAD の SHA ごと）と、読んだ (base, head)
    commits_of: dict[str, list[dict]] = field(default_factory=dict)
    compared: list[tuple[str, str]] = field(default_factory=list)
    # マージキューに入っている PR と、キューから外した PR
    queued: set[int] = field(default_factory=set)
    dequeued: list[int] = field(default_factory=list)
    # キューへ入れるときの先頭への指定（`merge` の jump）
    merge_jumps: list[bool] = field(default_factory=list)
    # 当たった ruleset の迂回の可否（規則があるときだけ使う）と、ruleset を読めないことにするフラグ
    bypass: str | None = "always"
    rules_unreadable: bool = False
    # GraphQL の応答（クエリの先頭の語で引く）
    graphql_data: dict[str, Any] = field(default_factory=dict)
    # PR ごとのレビュー（REST の形）と、本番ブランチ宛てで開いている PR の (番号, head)
    reviews_of: dict[int, list[dict]] = field(default_factory=dict)
    open_at: list[tuple[int, str]] = field(default_factory=list)

    def pull(self, number: int) -> Pull:
        return self.pulls[number]

    def comments(self, number: int) -> list[Authored]:
        return list(self.comments_of.get(number, []))

    def trusts(self, authored: Authored) -> bool:
        return authored.author in self.trusted

    def permission(self, login: str) -> str:
        return "write" if login in self.trusted else "read"

    def reviews(self, number: int) -> list[dict]:
        return list(self.reviews_of.get(number, []))

    def open_heads(self, base: str) -> list[tuple[int, str]]:
        return list(self.open_at)

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

    def rule_facts(self, branch: str) -> RuleFacts | None:
        """`GitHub.rule_facts` の代わり。規則（`branch_rules`）だけから組み、従来の保護は無いとみなす。"""
        if self.rules_unreadable:
            return None
        rules = self.branch_rules(branch)
        return rule_facts_of(rules, [self.bypass] if rules else [])

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(("GRAPHQL", query.split("{", 1)[0].strip(), variables))
        return self.graphql_data

    def nodes(self, numbers: Any, node: str, fields: str, batch: str = "github.graphql-batch") -> dict[int, dict]:
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

    def merge(
        self, number: int, expect_head: str | None = None, method: str | None = None, require_method: bool = False, jump: bool = False
    ) -> MergeOutcome:
        self.merged.append(number)
        self.merge_methods.append(method)
        self.merge_required.append(require_method)
        self.merge_jumps.append(jump)
        return MergeOutcome(True, None, f"#{number} をマージした", self.pulls[number].head_sha)

    def in_merge_queue(self, number: int) -> bool:
        return number in self.queued

    def dequeue(self, number: int) -> None:
        self.queued.discard(number)
        self.dequeued.append(number)


def not_found() -> GitHubError:
    return GitHubError("無い", 404)


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


def commit_files(repo: Path, files: dict[str, str], message: str) -> str:
    """`files`（リポジトリの根からのパス → 中身）を書き出してすべてをコミットし、そのコミットの SHA を返す。"""
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    git_sandbox.sh(repo, "add", "-A")
    git_sandbox.sh(repo, "commit", "-q", "-m", message)
    return git_sandbox.sh(repo, "rev-parse", "HEAD").strip()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
