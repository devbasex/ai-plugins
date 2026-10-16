"""走っているプランの登録簿（#1674）。queue のプロセスに 1 つあり、永続しない。

登録と抹消は `flow.plan_workflow` だけが呼ぶ（始めに `begin`、`finally` で `end`）。実装のステップは、担当を
起動する時点で `others` を読み、自分以外の走っているプランの `触るファイル` を除外の行にする。

    begin("/q/a.json", ["lib/a.py"])
    others("/q/b.json")   # ["lib/a.py"]
    end("/q/a.json")
"""

from __future__ import annotations

import threading

_LOCK = threading.Lock()
_RUNNING: dict[str, list[str]] = {}  # プランの絶対パス → 触るファイル（登録の順）


def begin(plan: str, files: list[str]) -> None:
    with _LOCK:
        _RUNNING.pop(plan, None)
        _RUNNING[plan] = list(files)


def end(plan: str) -> None:
    with _LOCK:
        _RUNNING.pop(plan, None)


def others(plan: str) -> list[str]:
    """自分以外の走っているプランの `触るファイル`（登録の順に、重ねずに）。"""
    with _LOCK:
        return list(dict.fromkeys(f for p, files in _RUNNING.items() if p != plan for f in files))
