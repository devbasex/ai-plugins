"""ループの 1 回の締めの値: 開発ループの 1 回を締めるときに、スプリント状態と版の記録からループの 1 回へ写す値を組む（#2354）。

ループの 1 回の締めそのものは `sprint_loop_gate.close_lap` が持つ。"""

from __future__ import annotations


def copy_state_gates(lap: dict, state: dict) -> None:
    """スプリント状態の承認ゲートをループの 1 回へ写す（写してあるものは重ねない）。"""
    for g in state.get("gates", []):
        copy = {
            "name": g.get("name"),
            "by": g.get("by", "user"),
            "verdict": g.get("verdict", g.get("outcome", "approved")),
            "basis": g.get("basis") or [],
            "pr": g.get("pr"),
            "at": g.get("at", ""),
        }
        if copy not in lap.setdefault("gates", []):
            lap["gates"].append(copy)


def closed_versions(lap: dict, state: dict) -> dict:
    """締めたループの 1 回の版。開発版を渡したループの 1 回はスプリント状態の版の欄（無ければループの 1 回の版）。渡さなかったループの 1 回は版の記録からコピーし、
    読めない側はループの 1 回が持つ値のまま残す。本番の版はリリース承認ゲートの前にも記録があるため、本番のプランが完了したループの 1 回でだけ読む。

    渡したかは締め直しでも変わらないスプリント状態の欄で見る（ループの 1 回の開発版は 1 回目の締めで埋まる）。"""
    from supervise_lib.queue_fill import plan_finished, recorded_version  # 版の記録の置き場はキューの層が持つ

    kept = lap.get("versions") or {}
    versions = state.get("versions") or kept
    if versions.get("dev"):
        return versions
    copied = {"dev": recorded_version(lap.get("dev_plan"))}
    if plan_finished(lap.get("prod_plan")):
        copied["prod"] = recorded_version(lap.get("prod_plan"))
    return {**kept, **{k: v for k, v in copied.items() if v}}
