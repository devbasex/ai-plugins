"""cross-review の状態の判定テストが共有する足場（state・round・承認の結果）。

conftest.py へ置くと、複数の Skill のテストを同時に実行したときに `conftest` という
モジュール名が衝突する。直接 import する補助はこの固有名のモジュールへ置く。
"""

from __future__ import annotations


def judge_state(pr: int, repo: str, rounds: list[dict], **over) -> dict:
    """判定が読む最小の状態。`over` で項目を差し替える。"""
    state = {
        "current_pr": pr,
        "repo": repo,
        "max_rounds": 12,
        "rotate_after": 8,
        "only": None,
        "rounds": rounds,
        "pr_history": [{"pr": pr, "rounds": len(rounds)}],
        "deferred_nits": [],
        "final": None,
    }
    state.update(over)
    return state


def judge_round(pr: int, no: int, started_at: str, **over) -> dict:
    entry: dict = {"round": no, "pr": pr, "started_at": started_at}
    entry.update(over)
    return entry


def approve() -> dict:
    return {
        "intent": "APPROVE",
        "posted_as": "APPROVE",
        "comments": 0,
        "by_severity": {"critical": 0, "major": 0},
    }
