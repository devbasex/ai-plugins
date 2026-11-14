"""6 本のレーンが `ndf progress next` で同じ進行表の DB へ同時に書くテストの、レーン 1 本の中身（pytest は集めない）。

レーンは自分のスプリントの進行表を作り、`next` を打っては起こした作業を同じプロセスで流し（`run.work`）、承認ゲートに
答えて完了まで進める。これを決めた回数だけ繰り返し、`database is locked`（止まった理由 store_busy）に当たった回数を返す。
作業（プラン）は偽物で、進行表の DB への書き込みだけが本物である。
"""

from __future__ import annotations

from pathlib import Path

from ndf.contracts import result
from ndf.progress import command, invoke, model, run, store


def values(cwd: Path, branch: str) -> dict:
    """偽の作業が出す文脈の値の組（工程の定義の outputs の鍵の値）。"""
    return {"worktree": str(cwd), "branch": branch, "design_pr": 7, "pr": 8, "delivery": {"kind": "plugin"}, "version": "11.0.0"}


def ok_result(r: invoke.StageRun, values: dict) -> invoke.StageResult:
    """宣言した出力を `values` から持つ ok の結果。"""
    outputs = {k: values[k] for k in r.outputs}
    return invoke.finished(r, result.result("plan", "ok", f"{r.step} を終えた", outputs=outputs or None))


def turn(st: store.Store, tid: str, cwd: Path, env: run.Env) -> dict:
    """`next` を打ち、作業を起こしたらその作業を同じプロセスで流す。"""
    out = run.advance(st, tid, cwd, env)
    if out["status"] == "ok" and "next" in out and out["next"]["argv"][2] == "wait":
        out = run.work(st, tid, cwd, env)
    return out


class _NoRecords:
    def read(self, number: int) -> str:
        return ""

    def write(self, number: int, body: str) -> None:
        return None


def _env(cwd: Path) -> run.Env:
    given = values(cwd, "sprint/s")
    return run.Env(
        facts=lambda words, ctx, c: {w: True for w in words},
        launch=lambda st, tid, c: None,
        invoker=lambda r, c, w, a: ok_result(r, given),
        records=lambda c: _NoRecords(),
        sleep=lambda s: None,
    )


def _sprint(st: store.Store, tid: str, cwd: Path, env: run.Env) -> int:
    """1 スプリントを完了まで流す。store_busy に当たった回数を返す（当たれば同じ手を打ち直す）。"""
    busy = 0
    for _ in range(200):
        try:
            out = turn(st, tid, cwd, env)
            if out["status"] == "gate":
                run.answer(st, tid, out["metrics"]["stage"], "approved", "レーンの試験", None)
            elif out["metrics"].get("state") == "completed" or out["metrics"].get("move") == "done":
                return busy
        except store.StoreBusy:
            busy += 1
    raise AssertionError(f"{tid} が完了しない")


def lane(url: str, root: str, cwd: str, name: str, rounds: int, barrier, out) -> None:
    """全員がそろってから、`rounds` 回のスプリントを流し、(名前, busy の回数, 完了した数) を `out` へ置く。"""
    here = Path(cwd)
    env = _env(here)
    repo = command._repo(here, Path(root))
    barrier.wait()
    busy = done = 0
    for i in range(rounds):
        sprint = f"{name}-{i}"
        assert command.main(["new", sprint, "--mode", "light", "--issues", "1"]) == 0
        with store.open_store(url, Path(root)) as st:
            busy += _sprint(st, model.table_id("sprint", repo, sprint), here, env)
            done += 1
    out.put((name, busy, done))
