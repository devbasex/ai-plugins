"""見本の突き合わせのテストの、偽の記述・見本・見本の定義。

偽の記述は本物の GitHub の `issue` と同じ参照の構造にする（`$ref` で別の定義を指し、null を取る列を `type` の配列か null の枝で書き、
`additionalProperties` を書かない）。定義が名前を決めない辞書（`meta`・`tags`）と枝（`state`・`owner`）は、本物の定義のほかの形のぶんを足す。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from ndf.domains.samples import verify

URL = "https://example.test/description.json"
SAMPLE = "tests/samples/github/rest/issue.json"
POINTER = "#/components/schemas/issue"
ABSENT = ["body_html", "user/starred_at", "labels/*/archived_at"]
# 結果 JSON に出てはいけない目印
SECRET_VALUE = "SECRET-VALUE-3687"
SECRET_KEY = "SECRET-KEY-3687"

_DOCUMENT: dict[str, Any] = {
    "openapi": "3.1.0",
    "components": {
        "schemas": {
            "issue": {
                "type": "object",
                "required": ["number", "user", "labels"],
                "properties": {
                    "number": {"type": "integer", "example": 1},
                    "body": {"type": ["string", "null"]},
                    "body_html": {"type": "string"},
                    "state_reason": {"type": ["string", "null"], "enum": ["completed", "reopened", None]},
                    "user": {"anyOf": [{"type": "null"}, {"$ref": "#/components/schemas/simple-user"}]},
                    "assignee": {"anyOf": [{"type": "null"}, {"$ref": "#/components/schemas/simple-user"}]},
                    "creator": {"allOf": [{"$ref": "#/components/schemas/simple-user"}, {"properties": {"note": {"type": "string"}}}]},
                    "labels": {"type": "array", "items": {"$ref": "#/components/schemas/label"}},
                    "meta": {"type": "object", "additionalProperties": {"$ref": "#/components/schemas/label"}},
                    "tags": {"type": "object", "patternProperties": {"^S": {"type": "integer"}}},
                    "free": {"type": "object"},
                },
            },
            "simple-user": {
                "type": "object",
                "required": ["login"],
                "properties": {"login": {"type": "string"}, "id": {"type": "integer"}, "starred_at": {"type": "string"}},
            },
            "label": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "archived_at": {"type": ["string", "null"]}},
            },
        }
    },
}
_SAMPLE: dict[str, Any] = {
    "number": 1,
    "body": None,
    "state_reason": "completed",
    "user": {"login": "octocat", "id": 1},
    "assignee": None,
    "creator": {"login": "octocat", "id": 1, "starred_at": "x", "note": "n"},
    "labels": [{"name": "bug"}, {"name": "docs"}],
    "meta": {"any": {"name": "x"}},
    "tags": {"S1": 1},
    "free": {"anything": 1},
}


def document() -> dict[str, Any]:
    return copy.deepcopy(_DOCUMENT)


def sample() -> dict[str, Any]:
    return copy.deepcopy(_SAMPLE)


def properties(doc: dict[str, Any], name: str) -> dict[str, Any]:
    """偽の記述の定義 1 つの `properties`。"""
    return doc["components"]["schemas"][name]["properties"]


class World:
    """一時の保存先の見本の定義（`contracts/live-samples/`）と見本のファイル、記述を返す偽の取得（`fetch`）。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.documents: dict[str, Any] = {URL: document()}
        self.fetched: list[str] = []

    def define(self, name: str = "issue", data: Any = None, **changes: Any) -> None:
        """見本の定義と見本を 1 件置く。`data` は見本の中身（False なら置かない）、`changes` は定義の値。"""
        raw = {
            "summary": name,
            "peer": "github",
            "sample": SAMPLE.replace("issue", name),
            "source": {"schema": changes.pop("url", URL), "pointer": changes.pop("pointer", POINTER)},
            "absent_keys": ABSENT,
            **changes,
        }
        path = self.root / "contracts" / verify.DEFINITIONS / f"{name}.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        if data is False:
            return
        target = self.root / raw["sample"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data if isinstance(data, str) else json.dumps(sample() if data is None else data), encoding="utf-8")

    def fetch(self, url: str) -> Any:
        self.fetched.append(url)
        got = self.documents[url]
        if isinstance(got, Exception):
            raise got
        return got
