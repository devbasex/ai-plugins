"""MVV 判定のテストが共有する、診断の記録の読み取りと伏せる対象の値。"""

from __future__ import annotations

from typing import Any

from ndf.domains.records import jsonl, mvv

# 結果の文へ出してはいけない、架空の社内のホスト名
HOST = "git.corp-internal.example.jp"


def failures() -> list[dict[str, Any]]:
    """手元の診断の記録（mvv-gate-failures）の行。壊れた行があれば落とす。"""
    rows, broken = jsonl.read_rows(mvv.record_path("mvv-gate-failures"), kind="mvv-gate-failure")
    assert not broken
    return rows
