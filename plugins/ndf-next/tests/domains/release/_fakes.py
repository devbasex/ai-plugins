"""リリースのテストが共有する、ネットワークへ出ない GitHub の偽物と、手元の git のリポジトリの作り方。"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ndf.adapters.github import Authored, GitHubError, MergeOutcome, Pull

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
    drop_comments: bool = False
    node_data: dict[int, dict] = field(default_factory=dict)
    runs: list[dict] = field(default_factory=list)

    def pull(self, number: int) -> Pull:
        return self.pulls[number]

    def comments(self, number: int) -> list[Authored]:
        return list(self.comments_of.get(number, []))

    def trusts(self, authored: Authored) -> bool:
        return authored.author in self.trusted

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

    def nodes(self, numbers: Any, node: str, fields: str) -> dict[int, dict]:
        return {int(n): self.node_data.get(int(n), {}) for n in numbers}

    def check_runs(self, sha: str) -> list[dict]:
        return list(self.runs)

    def merge(self, number: int, expect_head: str | None = None, method: str | None = None) -> MergeOutcome:
        self.merged.append(number)
        self.merge_methods.append(method)
        return MergeOutcome(True, None, f"#{number} をマージした", self.pulls[number].head_sha)


def not_found() -> GitHubError:
    return GitHubError("無い", 404)


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout


def configure(path: Path) -> Path:
    """コミットの作者と署名なしを、そのリポジトリだけに設定する。"""
    git(path, "config", "user.email", "t@example.invalid")
    git(path, "config", "user.name", "t")
    git(path, "config", "commit.gpgsign", "false")
    return path


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "develop")
    return configure(path)


def clone(tmp_path: Path, origin: Path, name: str, branch: str = "develop") -> Path:
    """origin のブランチを `tmp_path/<name>` へ clone し、設定する。"""
    path = tmp_path / name
    git(tmp_path, "clone", "-q", "-b", branch, str(origin), str(path))
    return configure(path)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
