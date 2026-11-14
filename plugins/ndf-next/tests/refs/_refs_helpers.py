"""参照の部品のテストが共有する、読み手の偽物・状態の置き場の記録の書き方・GitHub の GraphQL の答え方。"""

from __future__ import annotations

import importlib.util
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx
from githubkit import GitHub as Client

from ndf.adapters import github
from ndf.core import durable, refs
from ndf.core.plan.records import PROGRESS_FILE
from ndf.core.plan.runner import RUN_FILE, result_path

REPO = "devbasex/ai-plugins"

# テストのパッケージ同士は import で届かない（根に `__init__.py` が無い）ため、GraphQL の PR の節点は
# GitHub のアダプターのテストと同じ作り方をファイルから読む。
_GITHUB_HELPERS = Path(__file__).resolve().parents[1] / "adapters" / "_github_helpers.py"
_spec = importlib.util.spec_from_file_location("_refs_github_helpers", _GITHUB_HELPERS)
assert _spec is not None and _spec.loader is not None
_github_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_github_helpers)

_ALIAS = re.compile(r"\b(a\d+): (\w+)\(([^)]*)\)")


@dataclass
class FakeSources:
    """状態の置き場ごとの記録と、GitHub の答えを辞書で持つ読み手。`calls` は GitHub を読んだ回数。"""

    records: dict[Path, refs.PlanRecord] = field(default_factory=dict)
    rows: dict[Path, list[dict[str, Any]]] = field(default_factory=dict)
    closing: dict[int, list[int]] = field(default_factory=dict)
    designs: dict[str, refs.DesignPr] = field(default_factory=dict)
    unreadable: Optional[refs.Unreadable] = None
    calls: list[str] = field(default_factory=list)

    def plan_record(self, state_dir: Path) -> Optional[refs.PlanRecord]:
        return self.records[state_dir] if state_dir in self.records else None

    def progress(self, state_dir: Path) -> list[dict[str, Any]]:
        return self.rows[state_dir] if state_dir in self.rows else []

    def closing_prs(self, issues: Sequence[int]) -> list[int]:
        self.calls.append("closing")
        if self.unreadable is not None:
            raise self.unreadable
        return sorted({n for i in issues for n in (self.closing[i] if i in self.closing else [])})

    def design_pr(self, branch: str) -> refs.DesignPr:
        self.calls.append("design")
        return self.designs[branch] if branch in self.designs else refs.DesignPr(None)


def write_result(state_dir: Path, status: str, outputs: Mapping[str, Any], key: str = "k-1") -> None:
    """プランの状態の置き場へ、最後の回の鍵（run.json）と結果 JSON を書く（runner が書く形）。"""
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / RUN_FILE).write_text(json.dumps({"key": key, "name": "p", "round": 0, "heads": []}), encoding="utf-8")
    path = result_path(state_dir, durable.run_name(key))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tool": "plan", "status": status, "summary": "s", "outputs": dict(outputs)}), encoding="utf-8")


def write_progress(state_dir: Path, rows: Sequence[Mapping[str, Any]], tail: str = "") -> None:
    """進捗記録を書く。`tail` は末尾に足す生の行（書きかけの行の再現）。"""
    state_dir.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows) + tail
    (state_dir / PROGRESS_FILE).write_text(text, encoding="utf-8")


def graphql_pull(number: int, merged: bool, state: str, head: str, ref: str) -> dict[str, Any]:
    """GraphQL の PR の節点（`github.PULL_FIELDS` の形。本文は空）。"""
    return _github_helpers.graphql_pull_node(number, state.upper(), merged, head, ref, "")


def hub(handler: Callable[[httpx.Request], httpx.Response]) -> github.GitHub:
    """要求を `handler` が答える、ネットワークへ出ない `GitHub`（キャッシュと再試行なし）。"""
    return github.GitHub(REPO, Client("t", transport=httpx.MockTransport(handler), http_cache=False, auto_retry=False))


def graphql_hub(answer: Callable[[str, str], Any], seen: Optional[list[str]] = None) -> github.GitHub:
    """GraphQL のエイリアスごとの選択（`a0: <節点>(<引数>)`）を `answer(節点, 引数)` が答える `GitHub`。"""

    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        if seen is not None:
            seen.append(query)
        data = {m.group(1): answer(m.group(2), m.group(3)) for m in _ALIAS.finditer(query)}
        return httpx.Response(200, json={"data": {"repository": data}})

    return hub(handler)


def failing_hub(status: int) -> github.GitHub:
    """どの要求にも `status` を返す `GitHub`。"""
    return hub(lambda _request: httpx.Response(status, json={"message": "no"}))
