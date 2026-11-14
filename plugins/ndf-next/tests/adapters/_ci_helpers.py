"""CI のテストが共有する見本（check run の行と落ちたテストを含むログ）。"""

from __future__ import annotations


def run(name: str, status: str = "completed", conclusion: str | None = None, rid: int = 1, **extra) -> dict:
    """REST の check run 1 件。終わったものは既定で success と completed_at を持つ。"""
    done = status == "completed"
    row = {
        "id": rid, "name": name, "status": status, "conclusion": "success" if done and conclusion is None else conclusion,
        "started_at": "2026-11-12T13:30:00Z", "completed_at": "2026-11-12T13:33:00Z" if done else None,
    }  # fmt: skip
    return {**row, **extra}


# 落ちたテストの拾い出し（#3188）の見本のログ
LOG = """2026-11-12T13:33:01.1Z ============ short test summary info ============
2026-11-12T13:33:01.2Z FAILED tests/test_stage_check.py::test_order[standard] - AssertionError: x
2026-11-12T13:33:01.3Z FAILED tests/test_stage_check.py::test_names - AssertionError
2026-11-12T13:33:01.4Z ERROR tests/test_other.py - ImportError
2026-11-12T13:33:01.5Z FAILED tests/test_stage_check.py::test_names - AssertionError
"""
