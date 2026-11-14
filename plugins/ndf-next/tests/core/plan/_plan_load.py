"""プランの実行の負荷の測定（B3・B6）。テストのエントリポイントは `test_load.py`（CI の pytest が拾う）。手で流すときは次のとおり:

    uv run --frozen --project . python tests/core/plan/_plan_load.py --rounds 3 [--plans 20] [--wrappers 14] [--out <結果.json>] [--db-url <URL>]

20 本のプランの実行と 14 本のラッパー（`next` と `wait` を打ち続けるプロセス）を、全員そろえてから（barrier）同時に
走らせる。プランの実行は回（`--set round=<n>`。値が違えば別の鍵になる）を 1 から `rounds` まで打ち、各回で 5 ステップを
流す。ラッパーは受け持つプランの今の回へ `wait` と `next` を、そのプランが最後の回を終えるまで打つ。

子は `--child` でこのファイル自身を起動し、`ndf.core.durable` と `ndf.core.plan.runner`・`steps` に数える手を差し込んでから
`ndf` のエントリポイントを呼ぶ。数える値（所要の秒は数えない。所要はマシンの性能と本数で決まり、合否に使わない）:

- `database is locked` の回数（すべての子の標準出力・標準エラー・ステップのログ）。B3 の合格は 0 回
- 流したステップの回数と、2 回流れたステップの数
- 回し終えた後の WAL（`*-wal`）の大きさの最大（B6）
- 鍵のファイルの `quick_check`（`check_file`）と片付けの走査（`sweep`・`sweep_url`）の働いた回数（`count`）と
  呼ばれた回数（`called`）。目印が効けば、働いた回数は呼び出しの回数（`calls`）に比例しない（B6）。
  `quick_check` は鍵のファイル（`key_files`）ごとに高々 1 回、片付けの走査は置き場ごとに 24 時間に 1 回働く
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[3] / "src"
LOCKED = "database is locked"
STEPS_PER_PLAN = 5
STEP_SECONDS = 0.2
WAIT_TIMEOUT = "1"
ENV_DB = "NDF_PROGRESS_DB"
# 合格の線（B6）。WAL の上限は SQLite の既定の自動チェックポイント（1000 ページ × 4096 バイト）
WAL_LIMIT_BYTES = 1000 * 4096
# B6 で回数を数える関数
PROBE_FNS = ("_check_key_file", "check_file", "sweep", "sweep_url")


# --- 子: 計測を差し込んで ndf を打つ ----------------------------------------------------------------


class Probe:
    """1 つの子プロセスの回数の記録。終わるときに 1 行の JSON にして `sink` へ足す。"""

    def __init__(self) -> None:
        self.local = threading.local()
        self.lock = threading.Lock()
        self.probes: list[dict[str, Any]] = []
        self.steps: list[str | None] = []

    def counted(self, module: Any, name: str) -> None:
        original = getattr(module, name)

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            value = original(*args, **kwargs)
            # 片付けの走査は、目印が走査を飛ばした回（`skipped`）を働いていないとして分ける
            with self.lock:
                self.probes.append({"fn": name, "skipped": bool(getattr(value, "skipped", False))})
            return value

        setattr(module, name, wrapper)

    def counted_steps(self, runner: Any, steps: Any) -> None:
        """ステップを打つ手（`_perform` の `RunStep`）ごとに、流したステップ（プラン・回・ステップ）を 1 件足す。"""
        perform, body = runner._perform, steps.run

        def body_wrapper(ctx: Any, step: Any) -> Any:
            self.local.key = f"{ctx.plan.name}:{ctx.values.get('round')}:{step.id}"
            return body(ctx, step)

        def perform_wrapper(spec: Any, act: Any) -> Any:
            if not isinstance(act, runner.RunStep):
                return perform(spec, act)
            self.local.key = None
            try:
                return perform(spec, act)
            finally:
                with self.lock:
                    self.steps.append(self.local.key)

        runner._perform, steps.run = perform_wrapper, body_wrapper

    def row(self) -> dict[str, Any]:
        return {"probes": self.probes, "steps": self.steps}


def child(sink: Path, argv: list[str]) -> int:
    sys.path.insert(0, str(SRC))
    from ndf import cli
    from ndf.core import durable
    from ndf.core.plan import runner, steps

    probe = Probe()
    for name in PROBE_FNS:
        probe.counted(durable, name)
    probe.counted_steps(runner, steps)
    try:
        return cli.main(argv)
    finally:
        with sink.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"cmd": argv[1] if len(argv) > 1 else None, **probe.row()}) + "\n")


# --- 親: リポジトリとプランを作り、そろえてから流す -------------------------------------------------


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, check=True, capture_output=True)


def _plans(repo: Path, count: int) -> list[str]:
    _git(repo, "init", "-q")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "init")
    (repo / "plans").mkdir()
    sleep = [sys.executable, "-c", f"import time; time.sleep({STEP_SECONDS})"]
    out = []
    for i in range(count):
        steps = [{"id": f"s{n}", "run": sleep} for n in range(STEPS_PER_PLAN)]
        body = {"name": f"p{i}", "repo": str(repo), "steps": steps, "touches": [f"f{i}.py"]}
        (repo / "plans" / f"p{i}.json").write_text(json.dumps(body), encoding="utf-8")
        out.append(f"plans/p{i}.json")
    return out


class Recorder:
    """ループが打った `ndf plan` の 1 回ずつの記録（スレッドから足す）。"""

    def __init__(self, plans: list[str]) -> None:
        self.lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []
        # プランごとの今の回の番号（ラッパーが同じ回へ `next` を打つ）と、最後の回を終えた印
        self.rounds: dict[str, int] = {p: 1 for p in plans}
        self.done: dict[str, threading.Event] = {p: threading.Event() for p in plans}

    def add(self, row: dict[str, Any]) -> None:
        with self.lock:
            self.calls.append(row)


def _call(repo: Path, env: dict[str, str], sink: Path, args: list[str], rec: Recorder, role: str) -> None:
    done = subprocess.run(
        [sys.executable, __file__, "--child", str(sink), "--", "plan", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    try:
        found = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError:  # noqa: NDF401 外部コマンド: 結果 JSON でない最後の行は status が無いとして数える
        found = {}
    cause = found.get("cause") if isinstance(found.get("cause"), dict) else {}
    status = found.get("status") if found.get("status") != "stopped" else f"stopped/{cause.get('kind')}"
    locked = (done.stdout + done.stderr).count(LOCKED)
    rec.add({"role": role, "cmd": args[0], "status": status, "locked": locked, "stderr": done.stderr[-400:] if status is None else None})


def _plan_loop(repo: Path, env: dict[str, str], sink: Path, plan: str, rounds: int, rec: Recorder, start: threading.Barrier) -> None:
    """1 本のプランの実行: 回を 1 から `rounds` まで `next` で流す。"""
    start.wait()
    for n in range(1, rounds + 1):
        rec.rounds[plan] = n
        _call(repo, env, sink, ["next", plan, "--set", f"round={n}"], rec, "plan")
    rec.done[plan].set()


def _wrapper_loop(repo: Path, env: dict[str, str], sink: Path, plan: str, rec: Recorder, start: threading.Barrier) -> None:
    """1 本のラッパー: プランが最後の回を終えるまで、今の回へ `wait` と `next` を打つ。`next` は流れている回の終わりを
    待つか、終わった回の控えを返す。"""
    start.wait()
    while not rec.done[plan].is_set():
        _call(repo, env, sink, ["wait", plan, "--timeout", WAIT_TIMEOUT], rec, "wrapper")
        _call(repo, env, sink, ["next", plan, "--set", f"round={rec.rounds[plan]}"], rec, "wrapper")


def _counts(rows: list[dict[str, Any]], names: tuple[str, ...]) -> dict[str, int]:
    """呼ばれた回数と、実際に働いた回（走査を飛ばさなかった回）の回数。"""
    called = [r for r in rows if r["fn"] in names]
    return {"called": len(called), "count": sum(1 for r in called if not r["skipped"])}


def _store_files(repo: Path) -> list[Path]:
    return [p for p in (repo / ".git").rglob("*") if p.is_file()]


def _children(sink: Path) -> list[dict[str, Any]]:
    """子のプロセスが 1 行ずつ書いた回数の記録。"""
    return [json.loads(line) for line in sink.read_text(encoding="utf-8").splitlines() if line.strip()] if sink.exists() else []


def _locked_in_logs(files: list[Path], repo: Path) -> int:
    """置き場とプランの置き場のログに出た `database is locked` の数。"""
    logs = [p for p in files + list((repo / "plans").rglob("*")) if p.suffix == ".log"]
    return sum(p.read_text(encoding="utf-8", errors="replace").count(LOCKED) for p in logs)


def _call_counts(rec: Recorder) -> dict[str, Any]:
    """呼び出しの数・副命令と status ごとの数・失敗の標準エラーの見本。"""
    return {
        "calls": len(rec.calls),
        "calls_by_status": dict(sorted(Counter(f"{c['cmd']}:{c['status']}" for c in rec.calls).items())),
        "failed_samples": [c["stderr"] for c in rec.calls if c["stderr"]][:3],
    }


def _file_counts(files: list[Path]) -> dict[str, int]:
    """WAL の最大の大きさと数、鍵ごとのファイルの数。"""
    wal = [p.stat().st_size for p in files if p.name.endswith("-wal")]
    return {
        "wal_max_bytes": max(wal, default=0),
        "wal_files": len(wal),
        "key_files": sum(1 for p in files if p.name.endswith(".sqlite")),
    }


def summarize(rec: Recorder, sink: Path, repo: Path, args: argparse.Namespace) -> dict[str, Any]:
    children = _children(sink)
    probes = [p for c in children for p in c["probes"]]
    runs = Counter(key for c in children for key in c["steps"])
    files = _store_files(repo)
    calls = _call_counts(rec)
    return {
        "plans": args.plans,
        "wrappers": args.wrappers,
        "rounds": args.rounds,
        "steps_per_plan": STEPS_PER_PLAN,
        "store": "postgresql" if args.db_url else "sqlite",
        **calls,
        "database_is_locked": sum(c["locked"] for c in rec.calls) + _locked_in_logs(files, repo),
        "steps_run": sum(runs.values()),
        "steps_run_twice": sum(1 for n in runs.values() if n > 1),
        **_file_counts(files),
        "quick_check": {**_counts(probes, ("check_file",)), "asked": sum(1 for p in probes if p["fn"] == "_check_key_file")},
        "sweep": _counts(probes, ("sweep", "sweep_url")),
    }


def run(work: Path, plans: int, wrappers: int, rounds: int, db_url: str | None = None) -> dict[str, Any]:
    """負荷を 1 回流し、数えた値を返す。`work` の下にリポジトリ・回数の記録（`counts.jsonl`・`calls.jsonl`）を置く。"""
    repo = work / "repo"
    repo.mkdir(parents=True)
    paths = _plans(repo, plans)
    sink = work / "counts.jsonl"
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    env.pop(ENV_DB, None)
    if db_url:
        env[ENV_DB] = db_url
    rec = Recorder(paths)
    start = threading.Barrier(plans + wrappers)
    jobs: list[tuple[Callable[..., None], tuple[Any, ...]]] = [
        (_plan_loop, (repo, env, sink, paths[i], rounds, rec, start)) for i in range(plans)
    ]
    jobs += [(_wrapper_loop, (repo, env, sink, paths[i % plans], rec, start)) for i in range(wrappers)]
    threads = [threading.Thread(target=fn, args=a) for fn, a in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ns = argparse.Namespace(plans=plans, wrappers=wrappers, rounds=rounds, db_url=db_url)
    out = summarize(rec, sink, repo, ns)
    (work / "calls.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in rec.calls), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--plans", type=int, default=20)
    p.add_argument("--wrappers", type=int, default=14)
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--work")
    p.add_argument("--db-url")
    p.add_argument("--out")
    args = p.parse_args(argv)
    work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="ndf-plan-load-", dir=Path.cwd()))
    out = run(work, args.plans, args.wrappers, args.rounds, args.db_url)
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--child":
        rest = sys.argv[3:]
        sys.exit(child(Path(sys.argv[2]), rest[1:] if rest[:1] == ["--"] else rest))
    sys.exit(main())
