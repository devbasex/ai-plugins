"""棚卸しのテストが共有する、ネットワークへ出ない GitHub（REST と GraphQL の偽物）と git のリポジトリ。"""

from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from githubkit import GitHub as Client

from ndf.adapters import github
from ndf.domains.backlog import cli

REPO = "acme/widgets"


class FakeHub:
    """1 つのリポジトリの Issue・マイルストーン・PR・ファイルを持ち、要求を記録する。"""

    def __init__(self) -> None:
        self.issues: dict[int, dict[str, Any]] = {}
        self.milestones: dict[str, int] = {}
        self.pulls: dict[int, dict[str, Any]] = {}
        self.pull_files: dict[int, list[dict[str, Any]]] = {}
        self.contents: dict[tuple[str, str], str] = {}
        self.commits: set[str] = set()
        self.writes: list[tuple[str, str, Any]] = []

    def issue(self, n: int, title: str = "", body: str = "", milestone: str | None = None, labels: tuple[str, ...] = (),
              updated: str = "2026-11-01T00:00:00Z", state: str = "open", parent: int | None = None) -> None:  # fmt: skip
        self.issues[n] = {"number": n, "title": title or f"課題 {n}", "body": body, "milestone": milestone, "labels": list(labels),
                          "updated": updated, "state": state, "parent": parent, "id": 1000 + n}  # fmt: skip

    # ---- GraphQL ----
    def _node(self, n: int) -> dict[str, Any] | None:
        i = self.issues.get(n)
        if i is None:
            return None
        return {"number": n, "title": i["title"], "body": i["body"], "createdAt": "2026-10-01T00:00:00Z", "updatedAt": i["updated"],
                "milestone": {"title": i["milestone"]} if i["milestone"] else None,
                "labels": {"nodes": [{"name": lb} for lb in i["labels"]]}}  # fmt: skip

    def graphql(self, query: str) -> dict[str, Any]:
        if "milestones(" in query:
            nodes = [{"number": v, "title": k} for k, v in self.milestones.items()]
            return {"repository": {"milestones": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}}
        if "issues(labels" in query:
            nodes = [{"number": n} for n, i in sorted(self.issues.items()) if i["state"] == "open"]
            return {
                "repository": {"issues": {"totalCount": len(nodes), "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}
            }
        return {"repository": {alias: self._node(int(n)) for alias, n in re.findall(r"(\w+): issue\(number: (\d+)\)", query)}}

    # ---- REST ----
    def rest(self, method: str, path: str, body: Any, params: dict[str, str]) -> httpx.Response:
        prefix = f"/repos/{REPO}"
        tail = path[len(prefix) :]
        if method != "GET":
            self.writes.append((method, tail, body))
        for pattern, fn in self._routes():
            m = re.fullmatch(pattern, f"{method} {tail}")
            if m:
                return fn(m, body, params)
        return httpx.Response(404, json={"message": f"no route {method} {tail}"})

    def _routes(self) -> list[tuple[str, Any]]:
        return [
            (r"GET ", lambda m, b, p: httpx.Response(200, json={"default_branch": "develop"})),
            (r"GET /issues/(\d+)/parent", self._parent),
            (r"GET /issues/(\d+)", lambda m, b, p: self._issue_rest(int(m.group(1)))),
            (r"PATCH /issues/(\d+)", self._patch),
            (r"POST /issues/(\d+)/comments", lambda m, b, p: httpx.Response(201, json={"id": 1})),
            (r"POST /issues/(\d+)/sub_issues", self._add_sub),
            (r"DELETE /issues/(\d+)/sub_issue", self._remove_sub),
            (r"GET /pulls/(\d+)/files", self._files),
            (r"GET /pulls/(\d+)", self._pull),
            (r"GET /contents/(.+)", self._content),
            (r"GET /commits/(\w+)", lambda m, b, p: httpx.Response(200 if m.group(1) in self.commits else 404, json={})),
        ]

    def _issue_rest(self, n: int) -> httpx.Response:
        i = self.issues.get(n)
        return httpx.Response(200, json={"number": n, "id": i["id"], "state": i["state"]}) if i else httpx.Response(404, json={})

    def _parent(self, m: re.Match, body: Any, params: Any) -> httpx.Response:
        parent = self.issues[int(m.group(1))]["parent"]
        if parent is None:
            return httpx.Response(404, json={"message": "No parent issue found", "status": "404"})
        return httpx.Response(200, json={"number": parent})

    def _patch(self, m: re.Match, body: dict, params: Any) -> httpx.Response:
        i = self.issues[int(m.group(1))]
        if "state" in body:
            i["state"] = body["state"]
        if "milestone" in body:
            i["milestone"] = next((t for t, v in self.milestones.items() if v == body["milestone"]), None)
        return httpx.Response(200, json={})

    def _by_id(self, sub_id: int) -> dict[str, Any]:
        return next(i for i in self.issues.values() if i["id"] == sub_id)

    def _add_sub(self, m: re.Match, body: dict, params: Any) -> httpx.Response:
        self._by_id(body["sub_issue_id"])["parent"] = int(m.group(1))
        return httpx.Response(201, json={})

    def _remove_sub(self, m: re.Match, body: dict, params: Any) -> httpx.Response:
        self._by_id(body["sub_issue_id"])["parent"] = None
        return httpx.Response(200, json={})

    def _pull(self, m: re.Match, body: Any, params: Any) -> httpx.Response:
        raw = self.pulls.get(int(m.group(1)))
        return httpx.Response(200, json=raw) if raw else httpx.Response(404, json={})

    def _files(self, m: re.Match, body: Any, params: Any) -> httpx.Response:
        return httpx.Response(200, json=self.pull_files.get(int(m.group(1)), []))

    def _content(self, m: re.Match, body: Any, params: dict) -> httpx.Response:
        text = self.contents.get((m.group(1), params.get("ref", "")))
        if text is None:
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json={"encoding": "base64", "content": base64.b64encode(text.encode()).decode()})

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/graphql"):
            return httpx.Response(200, json={"data": self.graphql(json.loads(request.content)["query"])})
        body = json.loads(request.content) if request.content else None
        return self.rest(request.method, request.url.path, body, dict(request.url.params))

    def client(self) -> github.GitHub:
        return github.GitHub(REPO, Client("t", transport=httpx.MockTransport(self.handler), http_cache=False, auto_retry=False))


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeHub:
    """`ndf issues` の GitHub を偽物へ差し替える。"""
    hub = FakeHub()
    monkeypatch.setattr(cli, "hub", lambda repo: hub.client())
    return hub


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """コミットを 1 つ持つ git のリポジトリ（作業ディレクトリをここへ移す）。"""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "develop")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "README").write_text("x\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    monkeypatch.chdir(root)
    monkeypatch.delenv("NDF_UPKEEP_STATE_DIR", raising=False)
    return root


@pytest.fixture
def git_in():
    """`git` を作業ディレクトリで打つ関数。"""
    return _git
