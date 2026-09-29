"""PR と課題の単発の読み書き（#1142 の L0・決定 16・不足 g）。

単発の読み書きは REST で行い、GraphQL の枠を代われない操作のために残す。REST が上限なら、同じ操作を
`gh pr` / `gh issue`（GraphQL）で行う（`gh_quota.with_fallback`）。読み取りは GraphQL の `--json` と同じ形で返す。
新しい関数は `gh_quota.Attempt`（`value`・`error`・`via`）を返す。

`view_json` は L0 の時点の呼び出し側のため、GraphQL を先に読み、上限のときだけ REST で読む形のまま残す。
REST を先に読むのは `view` で、呼び出し側は C1〜C7 で `view` へ移る。
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Callable

import gh_call
import gh_fields
import gh_quota

Attempt = gh_quota.Attempt
PER_PAGE = 100
_NUMBER_IN_URL = re.compile(r"/(?:pull|issues)/(\d+)")
# `view_json` が REST で読み替えるフィールド（#1211 の範囲のまま。広い対応表を使うのは `view`）
_VIEW_JSON_FIELDS = {"pr": {"number", "title", "body", "state", "url", "mergeCommit"}, "issue": {"number", "title", "body", "state", "url"}}


def view_json(kind: str, number: int, fields: str, repo: str | None = None, cwd: str | None = None) -> gh_call.GhResult:
    """`gh <pr|issue> view <n> --json <fields>` を呼ぶ。GraphQL が上限のときだけ REST で読み直し、
    GraphQL の `--json` と同じ形の JSON を stdout に入れて返す。

    上限以外の失敗と、対応表（`gh_fields`）に無いフィールドを頼まれたときは、元の結果をそのまま返す。
    `repo` を省くと `gh` が `cwd` のリポジトリを使う（REST は `{owner}/{repo}` の置き換え）。
    """
    args = [kind, "view", str(int(number))] + (["--repo", repo] if repo else []) + ["--json", fields]
    r = gh_call.gh(args, cwd=cwd)
    if r.returncode == 0 or not gh_quota.is_rate_limited(r.stderr + r.stdout):
        return r
    names = gh_fields.field_names(fields)
    if not names or any(f not in _VIEW_JSON_FIELDS[kind] for f in names):
        return r
    path = f"repos/{repo or '{owner}/{repo}'}/{gh_fields.VIEW_REST_PATH[kind]}/{int(number)}"
    rr = gh_call.gh(["api", path], cwd=cwd)
    try:
        d = json.loads(rr.stdout) if rr.returncode == 0 else None
    except ValueError:
        d = None
    if not isinstance(d, dict):
        why = rr.stderr.strip() or "REST の応答を読めない"
        return gh_call.GhResult(rr.returncode or 1, "", f"{r.stderr.strip()}\nREST でも読めない: {why}")
    return gh_call.GhResult(0, json.dumps(gh_fields.to_json_shape(kind, d, fields), ensure_ascii=False), "")


def pr_files(number: int, repo: str | None = None, cwd: str | None = None) -> gh_call.GhResult:
    """PR の変更したファイルを全件読む（REST の `pulls/<n>/files` を全ページ）。

    `gh pr view --json files` は先頭 100 件で切れ、rename の前のパスも返さない。stdout は
    `[{"path", "previous_path", "status", "additions", "deletions"}]` の JSON（`previous_path` は rename のときだけ値を持つ）。
    """
    path = f"repos/{repo or '{owner}/{repo}'}/pulls/{int(number)}/files?per_page={PER_PAGE}"
    r = gh_call.gh(["api", "--paginate", path], cwd=cwd)
    if r.returncode != 0:
        return r
    rows: list = []
    text, dec, i = r.stdout, json.JSONDecoder(), 0
    try:
        while True:  # --paginate はページごとの配列を続けて書く（`[...][...]`）
            while i < len(text) and text[i].isspace():
                i += 1
            if i >= len(text):
                break
            page, i = dec.raw_decode(text, i)
            if not isinstance(page, list):
                raise ValueError(page)
            rows += page
        files = [
            {
                "path": f["filename"],
                "previous_path": f.get("previous_filename"),
                "status": f.get("status", ""),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
            }
            for f in rows
        ]
    except (ValueError, TypeError, KeyError):
        return gh_call.GhResult(1, "", f"PR #{int(number)} の変更したファイルを読めない")
    return gh_call.GhResult(0, json.dumps(files, ensure_ascii=False), "")


def pr_head_branches(numbers: list[int], cwd: str | None = None) -> tuple[dict[int, str] | None, str]:
    """PR の番号 → head のブランチ名（`cwd` のリポジトリ）。GraphQL の別名で 100 件ずつまとめて読む（PR 1 本ごとに呼ばない）。
    番号が PR でないもの（squash の件名の `(#N)` が課題を指すなど）は空文字。読めなければ (None, 理由)。"""
    out: dict[int, str] = {}
    for i in range(0, len(numbers), PER_PAGE):
        chunk = [int(n) for n in numbers[i : i + PER_PAGE]]
        fields = " ".join(f"p{n}: pullRequest(number: {n}) {{ headRefName }}" for n in chunk)
        query = f"query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{ {fields} }} }}"
        r = gh_call.gh(["api", "graphql", "-F", "owner={owner}", "-F", "name={repo}", "-f", f"query={query}"], cwd=cwd)
        try:
            data = json.loads(r.stdout)["data"]["repository"]
        except (ValueError, KeyError, TypeError):
            data = None
        if not isinstance(data, dict):  # 一部の番号が PR でないだけなら data は残る
            return None, ("gh が無い" if r.returncode == 127 else (r.stderr or r.stdout).strip()[:300] or "応答を読めない")
        for n in chunk:
            out[n] = (data.get(f"p{n}") or {}).get("headRefName") or ""
    return out, ""


def _rest(path: str, method: str = "GET", payload: Any = None) -> Attempt:
    resp = gh_call.request(path, method, payload)
    if not resp.ok or resp.error:
        return Attempt(None, resp.error or f"HTTP {resp.status}", "rest")
    return Attempt(resp.body, "", "rest")


def _graphql_cli(args: list[str], stdin: str | None = None, parse: bool = True) -> Attempt:
    r = gh_call.gh(args, stdin)
    if r.returncode != 0:
        return Attempt(None, (r.stderr or r.stdout).strip() or f"gh {args[0]} が失敗", "graphql")
    if not parse:
        return Attempt(r.stdout.strip(), "", "graphql")
    try:
        return Attempt(json.loads(r.stdout or "null"), "", "graphql")
    except ValueError:
        return Attempt(None, f"gh {' '.join(args[:2])} の出力を読めない", "graphql")


def _slug(repo: str | None) -> str:
    slug = gh_call.resolve_repo(repo)
    if not slug:
        raise ValueError("リポジトリを決められない（repo を渡す）")
    return slug


def view(kind: str, number: int, fields: str, repo: str | None = None) -> Attempt:
    """PR / 課題を 1 件読む。対応表で作れるフィールドは REST で、作れなければ GraphQL で読む。"""
    slug = _slug(repo)
    graphql = lambda: _graphql_cli([kind, "view", str(int(number)), "--repo", slug, "--json", fields])  # noqa: E731
    if not gh_fields.covers(kind, fields):
        return graphql()

    def by_rest() -> Attempt:
        a = _rest(f"repos/{slug}/{gh_fields.VIEW_REST_PATH[kind]}/{int(number)}")
        return a if not a.ok else a._replace(value=gh_fields.to_json_shape(kind, a.value or {}, fields))

    return gh_quota.with_fallback(by_rest, graphql)


def _keep_list_item(kind: str, d: dict, state: str, labels: list[str]) -> bool:
    """REST の一覧の 1 件を残すか。issue の一覧に混ざる PR・merged でない PR・ラベルの足りない PR を捨てる。"""
    if kind == "issue" and "pull_request" in d:
        return False
    if state == "merged" and not d.get("merged_at"):
        return False
    return not (kind == "pr" and labels and not set(labels) <= {x.get("name") for x in d.get("labels") or []})


def _rest_pages(base: str, keep: Callable[[Any], bool], shape: Callable[[Any], Any], limit: int) -> Attempt:
    """REST の一覧をページを進めて読み、`keep` を通った項目を `shape` で整えて `limit` 件まで返す。
    短いページが来たら終わりとみなす。"""
    out: list[Any] = []
    page = 1
    while len(out) < limit:
        a = _rest(f"{base}&page={page}")
        if not a.ok or not isinstance(a.value, list):
            return a if not a.ok else Attempt(None, "REST の一覧を読めない", "rest")
        for d in a.value:
            if keep(d):
                out.append(shape(d))
        if len(a.value) < PER_PAGE:
            break
        page += 1
    return Attempt(out[:limit], "", "rest")


def _list(kind: str, repo: str | None, fields: str, state: str, labels: list[str] | None, limit: int) -> Attempt:
    slug = _slug(repo)
    labels = list(labels or [])

    def by_rest() -> Attempt:
        rest_state = "closed" if state == "merged" else state
        base = f"repos/{slug}/{gh_fields.VIEW_REST_PATH[kind]}?state={rest_state}&per_page={PER_PAGE}"
        if kind == "issue" and labels:
            base += "&labels=" + ",".join(labels)
        return _rest_pages(
            base, lambda d: _keep_list_item(kind, d, state, labels), lambda d: gh_fields.to_json_shape(kind, d, fields), limit
        )

    args = [kind, "list", "--repo", slug, "--state", state, "--limit", str(limit), "--json", fields]
    for name in labels:
        args += ["--label", name]
    graphql = lambda: _graphql_cli(args)  # noqa: E731
    return gh_quota.with_fallback(by_rest, graphql) if gh_fields.covers(kind, fields) else graphql()


def pr_list(repo: str | None, fields: str, state: str = "open", labels: list[str] | None = None, limit: int = 30) -> Attempt:
    """`gh pr list --json` と同じ形の一覧。`state` は open / closed / merged / all。"""
    return _list("pr", repo, fields, state, labels, limit)


def issue_list(repo: str | None, fields: str, state: str = "open", labels: list[str] | None = None, limit: int = 30) -> Attempt:
    """`gh issue list --json` と同じ形の一覧（PR を含めない）。`state` は open / closed / all。"""
    return _list("issue", repo, fields, state, labels, limit)


def _created(a: Attempt) -> Attempt:
    """作成の結果を `{"number", "url"}` に揃える（REST は本文、GraphQL の CLI は URL の 1 行）。"""
    if not a.ok:
        return a
    if isinstance(a.value, dict):
        return a._replace(value={"number": a.value.get("number"), "url": a.value.get("html_url") or ""})
    url = str(a.value or "").strip().splitlines()[-1] if a.value else ""
    m = _NUMBER_IN_URL.search(url)
    return a._replace(value={"number": int(m.group(1)) if m else None, "url": url})


def pr_create(repo: str | None, title: str, body: str, head: str, base: str, draft: bool = False) -> Attempt:
    slug = _slug(repo)
    payload = {"title": title, "body": body, "head": head, "base": base, "draft": draft}
    args = ["pr", "create", "--repo", slug, "--title", title, "--body-file", "-", "--head", head, "--base", base]
    return _created(
        gh_quota.with_fallback(
            lambda: _rest(f"repos/{slug}/pulls", "POST", payload), lambda: _graphql_cli(args + (["--draft"] if draft else []), body, False)
        )
    )


def issue_create(repo: str | None, title: str, body: str, labels: list[str] | None = None) -> Attempt:
    slug = _slug(repo)
    payload = {"title": title, "body": body, **({"labels": list(labels)} if labels else {})}
    args = ["issue", "create", "--repo", slug, "--title", title, "--body-file", "-"]
    for name in labels or []:
        args += ["--label", name]
    return _created(gh_quota.with_fallback(lambda: _rest(f"repos/{slug}/issues", "POST", payload), lambda: _graphql_cli(args, body, False)))


def _edit(
    kind: str,
    repo: str | None,
    number: int,
    title: str | None,
    body: str | None,
    add_labels: list[str] | None,
    remove_labels: list[str] | None,
) -> Attempt:
    slug = _slug(repo)
    n = int(number)

    def by_rest() -> Attempt:
        fields = {k: v for k, v in (("title", title), ("body", body)) if v is not None}
        if fields:
            a = _rest(f"repos/{slug}/{gh_fields.VIEW_REST_PATH[kind]}/{n}", "PATCH", fields)
            if not a.ok:
                return a
        if add_labels:
            a = _rest(f"repos/{slug}/issues/{n}/labels", "POST", {"labels": list(add_labels)})
            if not a.ok:
                return a
        for name in remove_labels or []:
            a = _rest(f"repos/{slug}/issues/{n}/labels/{urllib.parse.quote(name, safe='')}", "DELETE")
            if not a.ok and "404" not in a.error:
                return a
        return Attempt(True, "", "rest")

    args = [kind, "edit", str(n), "--repo", slug]
    args += ["--title", title] if title is not None else []
    args += ["--body-file", "-"] if body is not None else []
    for name in add_labels or []:
        args += ["--add-label", name]
    for name in remove_labels or []:
        args += ["--remove-label", name]

    def by_graphql() -> Attempt:
        a = _graphql_cli(args, body, False)
        return a._replace(value=True) if a.ok else a

    return gh_quota.with_fallback(by_rest, by_graphql)


def pr_edit(
    repo: str | None,
    number: int,
    title: str | None = None,
    body: str | None = None,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
) -> Attempt:
    return _edit("pr", repo, number, title, body, add_labels, remove_labels)


def issue_edit(
    repo: str | None,
    number: int,
    title: str | None = None,
    body: str | None = None,
    add_labels: list[str] | None = None,
    remove_labels: list[str] | None = None,
) -> Attempt:
    return _edit("issue", repo, number, title, body, add_labels, remove_labels)


def comment(repo: str | None, number: int, body: str) -> Attempt:
    """PR / 課題へコメントを 1 件足す。値は `{"url"}`。"""
    slug = _slug(repo)
    a = gh_quota.with_fallback(
        lambda: _rest(f"repos/{slug}/issues/{int(number)}/comments", "POST", {"body": body}),
        lambda: _graphql_cli(["issue", "comment", str(int(number)), "--repo", slug, "--body-file", "-"], body, False),
    )
    if not a.ok:
        return a
    url = a.value.get("html_url") if isinstance(a.value, dict) else str(a.value or "").strip()
    return a._replace(value={"url": url or ""})


MERGE_METHODS = ("merge", "squash", "rebase")


def pr_merge(repo: str | None, number: int, method: str = "merge", sha: str | None = None) -> Attempt:
    """PR をマージする。`sha` を渡すと head がその commit のときだけマージする。値は `{"merged", "sha"}`。"""
    if method not in MERGE_METHODS:
        raise ValueError(f"マージの方法は {'/'.join(MERGE_METHODS)} のどれか: {method}")
    slug = _slug(repo)
    payload = {"merge_method": method, **({"sha": sha} if sha else {})}
    args = ["pr", "merge", str(int(number)), "--repo", slug, f"--{method}"]
    args += ["--match-head-commit", sha] if sha else []
    a = gh_quota.with_fallback(
        lambda: _rest(f"repos/{slug}/pulls/{int(number)}/merge", "PUT", payload), lambda: _graphql_cli(args, None, False)
    )
    if not a.ok:
        return a
    if isinstance(a.value, dict):
        return a._replace(value={"merged": bool(a.value.get("merged")), "sha": a.value.get("sha")})
    return a._replace(value={"merged": True, "sha": None})


def fetch_body(repo: str, number: int) -> str | None:
    """issue / PR の本文を REST で取る（PR も issues の端点で読める）。取得できなければ `None`。"""
    resp = gh_call.rest(f"repos/{repo}/issues/{int(number)}")
    if resp is None or not isinstance(resp.body, dict) or "body" not in resp.body:
        return None
    return str(resp.body.get("body") or "")


def update_body(repo: str, number: int, body: str) -> bool:
    return gh_call.rest(f"repos/{repo}/issues/{int(number)}", method="PATCH", payload={"body": body}) is not None


def viewer_login() -> str | None:
    r = gh_call.gh(["api", "user", "--jq", ".login"])
    return r.stdout.strip() or None if r.returncode == 0 else None
