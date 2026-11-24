"""見本のファイルだけから応答を組み立てる偽の GitHub（REST）。

見本（`tests/samples/github/rest/`）は `ndf samples verify` が GitHub の公開の記述と突き合わせるので、応答の形が本物とずれれば
毎日のジョブが落ちる。応答の値をテストに書き足さない。
"""

# テストは `from sample_hub import SampleHub` で読む（pytest の pythonpath が tests/support をパスへ足す）

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "github" / "rest"


class SampleHub:
    """見本の `url` への GET に、その見本をそのまま返す。ほかの要求は 404。`sent` は受けた要求の（メソッド, パス）。"""

    def __init__(self) -> None:
        self.issue: dict[str, Any] = json.loads((SAMPLES / "issue.json").read_text(encoding="utf-8"))
        self.repo = urlsplit(self.issue["repository_url"]).path.removeprefix("/repos/")
        self.sent: list[tuple[str, str]] = []
        self.transport = httpx.MockTransport(self._answer)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.sent.append((request.method, request.url.path))
        if (request.method, request.url.path) == ("GET", urlsplit(self.issue["url"]).path):
            return httpx.Response(200, json=self.issue)
        return httpx.Response(404, json={"message": "Not Found"})
