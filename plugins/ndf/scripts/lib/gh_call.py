"""GitHub の呼び出しの最下層（#1142 の L0・決定 16 と 17）。GitHub を呼ぶのはこのモジュールだけである。

- `gh(args)`: `gh` の CLI を 1 回呼ぶ。テストは `RUNNER` を見本の応答へ差し替える
- `rest(path)`: REST の 1 回の要求。githubkit が import できれば githubkit で、できなければ `gh api -i` で送る
- `rest_cached(path)`: 読み直し用。プロセスの中で ETag を覚え、`If-None-Match` を付けて送る。
  変わっていなければ 304 が返り、上限に数えられない（2026-09-26 の試行で確かめた）

githubkit は `deps.require("github")` を呼んだエントリポイントの中でだけ import できる。呼んでいない
エントリポイント（L0 の時点の呼び出し側）は、同じ関数で `gh api` を使う。`RUNNER` を差し替えたとき
（テスト）も `gh api` を使い、GitHub へ届かない。

`gh api` は 304 のとき終了コード 1 を返す（stdout にヘッダー、stderr に `gh: HTTP 304`）。ここでは終了コードでなく
状態行の状態コードで 304 を見分け、失敗として扱わない。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
from typing import Any, Callable, NamedTuple


class GhResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


def _subprocess_gh(args: list[str], stdin: str | None = None, cwd: str | None = None) -> GhResult:
    try:
        p = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, cwd=cwd)
    except OSError as exc:
        return GhResult(127, "", f"gh を実行できない: {exc}")
    return GhResult(p.returncode, p.stdout, p.stderr)


# テストはここを見本の応答へ差し替える。引数は `gh` の後ろの argv と標準入力（`cwd` を渡すときだけ `cwd=`）。
RUNNER: Callable[..., GhResult] = _subprocess_gh


def gh(args: list[str], stdin: str | None = None, cwd: str | None = None) -> GhResult:
    if cwd is None:
        return RUNNER(list(args), stdin)
    return RUNNER(list(args), stdin, cwd=str(cwd))


class RestResponse(NamedTuple):
    """REST の 1 回の応答。残量は通常の要求の応答ヘッダからしか読めない。

    `status` は状態コード（読めなければ 0）。`not_modified` は ETag が一致して 304 が返った（上限に数えられない）。
    `error` は失敗の文（成功は空）。
    """

    headers: dict[str, str]
    body: Any
    rate_remaining: int | None
    rate_reset: str | None
    status: int = 200
    not_modified: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 or self.status == 304


_STATUS_LINE = re.compile(r"^HTTP/\S+\s+(\d{3})")


def parse_rest_headers(text: str) -> tuple[dict[str, str], str]:
    """`gh api -i` の出力を、ヘッダの辞書と本文へ分ける。

    状態行だけが `\\r` を持たず、以降のヘッダは `\\r\\n` で終わる（実測）。行末の
    違いで分けられなくなるため、空行そのものを区切りとして読む。
    """
    headers: dict[str, str] = {}
    lines = text.splitlines(keepends=True)
    body_at = len(lines)
    for i, line in enumerate(lines):
        if not line.strip():
            body_at = i + 1
            break
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    return headers, "".join(lines[body_at:])


def _response(status: int, headers: dict[str, str], body: Any, error: str = "") -> RestResponse:
    remaining = headers.get("x-ratelimit-remaining")
    try:
        rate_remaining = int(remaining) if remaining is not None else None
    except ValueError:
        rate_remaining = None
    return RestResponse(headers, body, rate_remaining, headers.get("x-ratelimit-reset"), status,
                        status == 304, error)


def _via_gh(path: str, method: str, payload: Any, etag: str | None) -> RestResponse:
    args = ["api", "-i", path]
    stdin = None
    if method != "GET":
        args[1:1] = ["-X", method]
    if etag:
        args[1:1] = ["-H", f"If-None-Match: {etag}"]
    if payload is not None:
        args += ["--input", "-"]
        stdin = json.dumps(payload, ensure_ascii=False)
    r = gh(args, stdin)
    m = _STATUS_LINE.match(r.stdout)
    status = int(m.group(1)) if m else (200 if r.returncode == 0 else 0)
    headers, raw = parse_rest_headers(r.stdout) if m or r.returncode == 0 else ({}, "")
    if status == 304:
        return _response(304, headers, None)
    if r.returncode != 0:
        return _response(status, headers, None, (r.stderr or r.stdout).strip() or f"gh api が終了コード {r.returncode}")
    try:
        body = json.loads(raw) if raw.strip() else None
    except json.JSONDecodeError:
        return _response(0, headers, None, "REST の応答を読めない")
    return _response(status, headers, body)


_CLIENT: list[Any] = []


def gh_token() -> str | None:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    r = gh(["auth", "token"])
    return r.stdout.strip() or None if r.returncode == 0 else None


def client() -> Any:
    """githubkit のクライアント（`http_cache`・`auto_retry` 付き）。使えなければ `None`（`gh api` を使う）。

    githubkit の待ち直しは既定で 1 回、ETag のキャッシュはプロセスの中だけである。上限の回数・回復の時刻までの
    待ち・間隔の伸長は `gh_quota` が持つ。
    """
    if RUNNER is not _subprocess_gh or importlib.util.find_spec("githubkit") is None:
        return None
    if not _CLIENT:
        token = gh_token()
        if not token:
            return None
        from githubkit import GitHub
        _CLIENT.append(GitHub(token, http_cache=True, auto_retry=True))
    return _CLIENT[0]


def _via_client(c: Any, path: str, method: str, payload: Any, etag: str | None) -> RestResponse:
    headers = {"If-None-Match": etag} if etag else None
    try:
        resp = c.request(method, "/" + path.lstrip("/"), json=payload, headers=headers)
    except Exception as exc:  # noqa: BLE001  githubkit の失敗（上限・4xx・通信）を 1 つの形へ揃える
        raw = getattr(getattr(exc, "response", None), "raw_response", None) or getattr(exc, "response", None)
        status = int(getattr(raw, "status_code", 0) or 0)
        hdrs = {k.lower(): v for k, v in dict(getattr(raw, "headers", {}) or {}).items()}
        text = str(getattr(raw, "text", "") or "") if raw is not None else ""
        return _response(status, hdrs, None, f"{type(exc).__name__}: {text or exc}"[:500])
    hdrs = {k.lower(): v for k, v in dict(resp.headers).items()}
    ext = getattr(resp.raw_response, "extensions", {}) or {}
    if resp.status_code == 304 or ext.get("hishel_revalidated"):
        body = None if resp.status_code == 304 else _json_or_none(resp)
        return RestResponse(*_response(200, hdrs, body)[:4], resp.status_code, True, "")
    return _response(resp.status_code, hdrs, _json_or_none(resp))


def _json_or_none(resp: Any) -> Any:
    try:
        return resp.json() if resp.content else None
    except ValueError:
        return None


def request(path: str, method: str = "GET", payload: Any = None, etag: str | None = None) -> RestResponse:
    """REST の 1 回の要求。失敗も `RestResponse`（`error` と `status`）で返す。"""
    c = client()
    return _via_client(c, path, method, payload, etag) if c is not None else _via_gh(path, method, payload, etag)


def rest(path: str, method: str = "GET", payload: Any = None) -> RestResponse | None:
    """REST の 1 回の要求。失敗は `None`（例外を投げず、呼び出し側が「確かめられなかった」と読む）。"""
    resp = request(path, method, payload)
    return resp if resp.ok and not resp.error else None


# 読み直し用の ETag と、その時の本文（プロセスの中だけ）。merge-when-green は 1 本のプロセスで読み直すので足りる
_ETAGS: dict[str, tuple[str, Any]] = {}


def rest_cached(path: str) -> RestResponse:
    """GET を ETag 付きで読み直す。304 なら前の本文を `not_modified=True` で返す（上限に数えられない）。"""
    known = _ETAGS.get(path)
    resp = request(path, etag=known[0] if known else None)
    if resp.status == 304 and known:
        return resp._replace(body=known[1], not_modified=True)
    if resp.ok and not resp.error and resp.headers.get("etag"):
        _ETAGS[path] = (resp.headers["etag"], resp.body)
    return resp


_REPO_URL = re.compile(r"(?:github\.com[:/])(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$")


def resolve_repo(repo: str | None = None) -> str | None:
    """`owner/repo` を決める。渡された値 → `origin` の URL → `gh repo view` の順。"""
    if repo:
        return repo
    try:
        p = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True)
        m = _REPO_URL.search(p.stdout.strip()) if p.returncode == 0 else None
    except OSError:
        m = None
    if m:
        return f"{m.group('owner')}/{m.group('name')}"
    r = gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    return r.stdout.strip() or None if r.returncode == 0 else None


def _output_via_runner(cmd: list[str]) -> str | None:
    """`gh ...` の argv を受け、標準出力を返す。失敗は `None`。"""
    r = gh(cmd[1:] if cmd and cmd[0] == "gh" else cmd)
    return r.stdout if r.returncode == 0 else None
