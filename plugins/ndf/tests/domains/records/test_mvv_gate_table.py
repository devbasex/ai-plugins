"""MVV 判定の表の確かめ（#2912 の決定 7・受け入れ条件 5c）。省ける判定のときだけ、差分の設計文書の head の「人へ残す判断」の表を読む。

判定の代役・偽の GitHub・設計のブランチの作業場所は `test_mvv_gate` のものを使う。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ndf.adapters import github
from ndf.core import merge_approval
from ndf.domains.records import mvv_gate

from . import test_mvv_gate as base
from .test_mvv_gate import DOCS, FOLLOW, HEAD, _TARGET, Ask, _inputs

# 設計のブランチの作業場所と、PR のコメントへ書く判定の記録（`test_mvv_gate` の fixture）
repo = base.repo
notes = base.notes


class Hub(base.Hub):
    """`test_mvv_gate` の偽の GitHub に、設計文書の head の本文の読み取り（`aliased`）を足したもの。"""

    def __init__(self, number: int, case: dict[str, Any]) -> None:
        super().__init__(number, case)
        self.selections: list[dict[str, str]] = []

    def aliased(self, selections: dict[str, str], batch: str = "github.graphql-batch") -> dict[str, Any]:
        """設計文書の head の本文（#2912 の表の確かめ）。`case["blobs"]` はパス → 本文（文字列）か応答の辞書、または投げる例外。"""
        self.selections.append(dict(selections))
        got = self.case.get("blobs", {})
        if isinstance(got, Exception):
            raise got
        out: dict[str, Any] = {}
        for key in selections:
            blob = got.get(key)
            out[key] = {"oid": "o", "text": blob, "isTruncated": False} if isinstance(blob, str) else blob
        return out


DESIGN_DOC, DETAIL_DOC = "issues/issue-7-design.md", "issues/issue-7-design-detail.md"
TABLE_C7 = "# 設計\n\n## 人へ残す判断\n\n| 判断 | 当たるレッドライン | なぜ当たるか |\n| --- | --- | --- |\n| 設定を書き換える | `C7` | .ndf/ を変える |\n"
NO_TABLE = "# 設計\n\n## 決定の記録\n\nA を採る\n"


def _design_case(blobs: Any, *files: tuple[str, str]) -> dict[str, Any]:
    rows = [{"filename": f, "status": st, "patch": "+誤字を直す"} for f, st in files] or [
        {"filename": DESIGN_DOC, "status": "modified", "patch": "+誤字を直す"}
    ]
    return {"body": "設計の誤字を直す", "files": rows, "blobs": blobs}


def _gate_with(repo: Path, case: dict[str, Any], answer: dict[str, Any] = FOLLOW) -> tuple[dict[str, Any], Hub]:
    hub = Hub(5, case)
    out = mvv_gate.gate(repo, "design", _inputs(), lambda root, remote=None: hub, Ask(answer))
    return out, hub


def test_a_numbered_table_row_keeps_the_gate_even_when_the_verdict_skips(repo: Path, notes: list[tuple[int, str]]) -> None:
    """5c: 回答が follow で boundary が空でも、表に番号を持つ行があれば省かず、target を外し、why にパスと番号を残す。"""
    out, hub = _gate_with(repo, _design_case({DESIGN_DOC: TABLE_C7}))
    assert (out["skip"], "target" in out, out["verdict"]["boundary"]) == (False, False, [])
    assert out["why"] == [mvv_gate.TABLE_KEPT + f"{DESIGN_DOC}（C7）"]
    ((selection,),) = [list(s.values()) for s in hub.selections]
    assert f'"{HEAD}:{DESIGN_DOC}"' in selection and "isTruncated" in selection
    # 省かない判定の記録には、マージの承認の証跡の行を書かない
    ((_, text),) = notes
    assert mvv_gate.TABLE_KEPT.strip() in text
    hub_rows = SimpleNamespace(comments=lambda n: [github.Authored("octocat", "OWNER", text, author_id=1)])
    assert merge_approval.merge_rows(hub_rows, 5, github.Viewer("octocat", 1)) == []  # type: ignore[arg-type]


@pytest.mark.parametrize("text", [NO_TABLE, TABLE_C7.replace("`C7`", "")], ids=["no-table", "no-number"])
def test_designs_without_numbered_rows_still_skip(repo: Path, text: str) -> None:
    out, hub = _gate_with(repo, _design_case({DESIGN_DOC: text}))
    assert (out["skip"], out["target"], out["why"]) == (True, _TARGET, [])
    assert len(hub.selections) == 1


def test_two_design_documents_are_read_in_one_request_and_removed_ones_are_not(repo: Path) -> None:
    case = _design_case(
        {DESIGN_DOC: NO_TABLE, DETAIL_DOC: TABLE_C7},
        (DESIGN_DOC, "modified"),
        (DETAIL_DOC, "added"),
        ("issues/issue-8-design.md", "removed"),
    )
    out, hub = _gate_with(repo, case)
    (selection,) = hub.selections
    assert list(selection) == [DESIGN_DOC, DETAIL_DOC]
    # マージ先の設定と 2 件の本文を、GitHub の 1 回の要求で読む
    assert hub.requests == 1
    assert out["skip"] is False and out["why"] == [mvv_gate.TABLE_KEPT + f"{DETAIL_DOC}（C7）"]


@pytest.mark.parametrize(
    "blob",
    [None, {"oid": "o", "text": None, "isTruncated": False}, {"oid": "o", "text": NO_TABLE, "isTruncated": True}],
    ids=["missing", "not-a-blob", "truncated"],
)
def test_unreadable_design_documents_keep_the_gate(repo: Path, blob: Any) -> None:
    """本文が無い・切り詰められた設計文書は表を確かめられないので省かない（切られた前半に番号が無くても）。"""
    out, _ = _gate_with(repo, _design_case({DESIGN_DOC: blob}))
    assert (out["skip"], "target" in out) == (False, False)
    assert out["why"] == [mvv_gate.TABLE_UNREADABLE + f"{DESIGN_DOC}（本文が無い）"]


def test_a_github_failure_keeps_the_gate_for_every_design_document(repo: Path) -> None:
    case = _design_case(github.GitHubError("届かない zz-fake-secret-88"), (DESIGN_DOC, "modified"), (DETAIL_DOC, "added"))
    out, _ = _gate_with(repo, case)
    # マージ先の設定と本文は同じ要求で読むので、設定の理由に続けて設計文書ごとの理由が載る
    assert out["skip"] is False and "target" not in out and len(out["why"]) == 3
    assert out["why"][0].startswith("マージ先の設定を読めない")
    assert all(w.startswith(mvv_gate.TABLE_UNREADABLE) and "診断の記録" in w for w in out["why"][1:])
    assert "zz-fake-secret-88" not in json.dumps(out, ensure_ascii=False)


def test_the_table_is_not_read_when_the_gate_is_kept_anyway(repo: Path) -> None:
    """省けない判定（レッドラインを挙げた・差分に設計文書が無い）では、表を読む要求を送らない。"""
    redline = {**FOLLOW, "boundary": ["C7: 設定を書き換える"]}
    out, hub = _gate_with(repo, _design_case({DESIGN_DOC: TABLE_C7}), redline)
    assert out["skip"] is False and hub.selections == []
    out, hub = _gate_with(repo, {**DOCS, "blobs": {}})
    assert out["skip"] is True and hub.selections == []
