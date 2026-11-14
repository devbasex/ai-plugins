"""止まった後の流し直し（#2639 の再現の手順）: 開始点（`--from`）を組まずに続きから流れ、承認ゲートでは答えごとの行き先を返す。

旧い NDF は、止まったキューを `queue --resume --from <プラン>` で流し直し、承認ゲートの終了コード 10 が `queue && wait && loop lap`
の後段を切った。ndf-next はどちらも持たない: 流し直しは同じ `ndf progress next <進行表>` で、承認ゲートの結果は続きの打ち方
（答えごとの `routes`）を持つ。
"""

from __future__ import annotations

from dataclasses import replace

from ndf.contracts import result
from ndf.progress import gate, invoke, run


def test_a_stopped_table_resumes_with_the_same_next_and_the_gate_carries_the_routes(st, env, new_table, flow):
    tid = new_table()
    calls: list[str] = []

    def stop_once(r, cwd, work_dir, answer):
        calls.append(r.step)
        if len(calls) == 1:
            return invoke.finished(r, result.stopped("plan", "interrupted", "GitHub の一時障害で push が止まった"))
        return env.invoker(r, cwd, work_dir, answer)

    failing = replace(env, invoker=stop_once)
    first = flow.turn(tid, failing)
    assert first["status"] != "gate" and len(calls) == 1
    resumed = first
    for _ in range(10):
        if resumed["status"] == "gate":
            break
        # 流し直しの打ち方は、開始点を組まない同じ next（結果の next は --from を持たない）
        assert "--from" not in (resumed.get("next") or {}).get("argv", [])
        resumed = flow.turn(tid, failing)
    assert resumed["status"] == "gate" and len(calls) >= 2
    q = gate.waiting(run.read(st, tid))
    assert q is not None
    routes = gate.gate_result(q)["routes"]
    assert all(r["argv"][:3] == ["ndf", "answer", tid] for r in routes.values())
