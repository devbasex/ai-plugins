"""REST の応答を GraphQL の `--json` の形へ変える対応表（#1142 の L0・決定 16）。GitHub を呼ばない。

REST の関数（`gh_rest`）は、`gh pr view --json` / `gh issue list --json` と同じ形で返す。呼び出し側は枠を知らない。
対応表に無いフィールドは REST では作れないため、`to_json_shape` は `None` を返し、呼び出し側が GraphQL で読む。
"""
from __future__ import annotations

from typing import Any, Callable


def _rest_state(d: dict[str, Any]) -> str:
    """REST の state を GraphQL の形（OPEN / CLOSED / MERGED）へ揃える。MERGED は merged_at で見分ける。"""
    if d.get("merged_at"):
        return "MERGED"
    return str(d.get("state") or "").upper()


def _rest_merge_commit(d: dict[str, Any]) -> dict[str, str] | None:
    """GraphQL の mergeCommit は `{"oid": ...}` か null。REST の merge_commit_sha は未マージでも試しの
    マージを指すので、merged_at があるときだけ載せる。"""
    sha = d.get("merge_commit_sha")
    return {"oid": sha} if d.get("merged_at") and sha else None


def _login(d: dict[str, Any]) -> dict[str, str] | None:
    user = d.get("user")
    return {"login": str(user.get("login") or "")} if isinstance(user, dict) else None


def _labels(d: dict[str, Any]) -> list[dict[str, str]]:
    return [{"name": str(x.get("name") or "")} for x in d.get("labels") or [] if isinstance(x, dict)]


_COMMON: dict[str, Callable[[dict[str, Any]], Any]] = {
    "number": lambda d: d.get("number"),
    "title": lambda d: d.get("title") or "",
    "body": lambda d: d.get("body") or "",
    "url": lambda d: d.get("html_url") or "",
    "author": _login,
    "labels": _labels,
    "createdAt": lambda d: d.get("created_at"),
    "updatedAt": lambda d: d.get("updated_at"),
    "closedAt": lambda d: d.get("closed_at"),
}

# REST の応答から GraphQL の `--json` のフィールドを作る対応表
_VIEW_FIELDS: dict[str, dict[str, Callable[[dict[str, Any]], Any]]] = {
    "pr": {
        **_COMMON,
        "state": _rest_state,
        "mergeCommit": _rest_merge_commit,
        "isDraft": lambda d: bool(d.get("draft")),
        "headRefName": lambda d: (d.get("head") or {}).get("ref") or "",
        "headRefOid": lambda d: (d.get("head") or {}).get("sha") or "",
        "baseRefName": lambda d: (d.get("base") or {}).get("ref") or "",
        "mergedAt": lambda d: d.get("merged_at"),
    },
    "issue": {
        **_COMMON,
        "state": lambda d: str(d.get("state") or "").upper(),
    },
}
_VIEW_REST_PATH = {"pr": "pulls", "issue": "issues"}


def field_names(fields: str | list[str]) -> list[str]:
    """`"a,b"` か一覧を、空を除いた名前の一覧にする。"""
    items = fields.split(",") if isinstance(fields, str) else list(fields)
    return [f.strip() for f in items if f and f.strip()]


def covers(kind: str, fields: str | list[str]) -> bool:
    """対応表だけで `fields` を作れるか。"""
    names = field_names(fields)
    return bool(names) and all(f in _VIEW_FIELDS[kind] for f in names)


def to_json_shape(kind: str, d: dict[str, Any], fields: str | list[str]) -> dict[str, Any] | None:
    """REST の 1 件を `--json <fields>` の形へ変える。対応表に無いフィールドがあれば `None`。"""
    if not covers(kind, fields):
        return None
    table = _VIEW_FIELDS[kind]
    return {f: table[f](d) for f in field_names(fields)}
