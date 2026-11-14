"""プランの実行の負荷の測定（B3・B6）。pytest は集めない（手で流し、出た値を PR に貼る）。

20 本のプランの実行と 14 本のラッパー（`next` と `wait` を打ち続けるプロセス）を同時に流し、次を JSON で出す:

- `database is locked` の回数（すべての子の標準出力・標準エラー・ステップのログ）。B3 の合格は 0 回
- 1 ステップあたりの上乗せ（`ndf plan next` の 1 回の所要からステップの本体の秒を引き、ステップの数で割る）の
  中央値・95 パーセンタイル・最大。B3 の合格は 1 秒以内
- 鍵のファイルの `quick_check` と片付けの走査（`sweep`・`sweep_url`）の 1 回あたりの所要と、働いた回数（`count`）・
  呼ばれた回数（`called`）。呼び出し（`ndf plan` の起動。`calls`）の回数と並べ、目印が効いて働いた回数が呼び出しに
  比例しないかを見る（B6）。`quick_check` は新しい鍵のファイルごとに 1 回働く（回の数に比例し、呼び出しの数には比例しない）

使い方（plugins/ndf-next で）:

    uv run --frozen --project . python tests/load/plan_load.py --minutes 30 --out <結果.json> [--work <置き場>] [--db-url <URL>]

`--db-url` を渡すと `NDF_PROGRESS_DB` に入れ、PostgreSQL の殻で測る。子は `--child` でこのファイル自身を起動し、
`ndf.core.durable` の `check_file`・`sweep`・`sweep_url` に時間の計測を差し込んでから `ndf` のエントリポイントを呼ぶ。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

SRC = Path(__file__).resolve().parents[2] / "src"
LOCKED = "database is locked"
STEPS_PER_PLAN = 5
STEP_SECONDS = 0.2
WAIT_TIMEOUT = "1"
ENV_DB = "NDF_PROGRESS_DB"


# --- 子: 計測を差し込んで ndf を打つ ----------------------------------------------------------------


def _timed(module: Any, name: str, sink: Path) -> None:
    """`module.name` を、所要の秒を `sink` へ 1 行ずつ足す版に置き換える。"""
    original = getattr(module, name)

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        value = original(*args, **kwargs)
        # 片付けの走査は、目印が新しく走査を飛ばした回（`skipped`）を分けて数える
        skipped = bool(getattr(value, "skipped", False))
        line = json.dumps({"fn": name, "seconds": time.perf_counter() - started, "skipped": skipped, "pid": os.getpid()})
        with sink.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return value

    setattr(module, name, wrapper)


def child(sink: Path, argv: list[str]) -> int:
    sys.path.insert(0, str(SRC))
    from ndf import cli
    from ndf.core import durable

    for name in ("check_file", "sweep", "sweep_url"):
        _timed(durable, name, sink)
    return cli.main(argv)


# --- 親: リポジトリとプランを作り、ループを並べて流す -----------------------------------------------


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
        # 回ごとに `--set round=<n>` で別の鍵にする。差し込みの値をプランが使わなくても、値が違えば別の回になる
        body = {"name": f"p{i}", "repo": str(repo), "steps": steps, "touches": [f"f{i}.py"]}
        (repo / "plans" / f"p{i}.json").write_text(json.dumps(body), encoding="utf-8")
        out.append(f"plans/p{i}.json")
    return out


class Recorder:
    """ループが打った `ndf plan` の 1 回ずつの記録（スレッドから足す）。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []
        # プランごとの今の回の番号（ラッパーが同じ回へ `next` を打つ）
        self.rounds: dict[str, int] = {}

    def add(self, row: dict[str, Any]) -> None:
        with self.lock:
            self.calls.append(row)


def _call(repo: Path, env: dict[str, str], sink: Path, args: list[str], rec: Recorder, role: str) -> None:
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, __file__, "--child", str(sink), "--", "plan", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    seconds = time.monotonic() - started
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    try:
        found = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError:  # noqa: NDF401 外部コマンド: 結果 JSON でない最後の行は status が無いとして数える
        found = {}
    cause = found.get("cause") if isinstance(found.get("cause"), dict) else {}
    status = found.get("status") if found.get("status") != "stopped" else f"stopped/{cause.get('kind')}"
    locked = (done.stdout + done.stderr).count(LOCKED)
    summary = str(found.get("summary")) if status != "ok" else None
    rec.add({"role": role, "cmd": args[0], "seconds": seconds, "status": status, "summary": summary, "locked": locked})


def _plan_loop(repo: Path, env: dict[str, str], sink: Path, plan: str, deadline: float, rec: Recorder) -> None:
    """1 本のプランの実行: 回を変えて `next` を打ち続ける。"""
    n = 0
    while time.monotonic() < deadline:
        n += 1
        rec.rounds[plan] = n
        _call(repo, env, sink, ["next", plan, "--set", f"round={n}"], rec, "plan")


def _wrapper_loop(repo: Path, env: dict[str, str], sink: Path, plan: str, deadline: float, rec: Recorder) -> None:
    """1 本のラッパー: プランの今の回へ `wait` と `next` を打ち続ける。`next` は流れている回の終わりを待つか、
    終わった回の控えを返す。"""
    while time.monotonic() < deadline:
        _call(repo, env, sink, ["wait", plan, "--timeout", WAIT_TIMEOUT], rec, "wrapper")
        _call(repo, env, sink, ["next", plan, "--set", f"round={rec.rounds.get(plan, 1)}"], rec, "wrapper")


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def _timing(rows: list[dict[str, Any]], names: tuple[str, ...]) -> dict[str, Any]:
    """打った回数と、実際に働いた回（走査を飛ばさなかった回）の所要。"""
    called = [r for r in rows if r["fn"] in names]
    secs = [r["seconds"] for r in called if not r["skipped"]]
    return {
        "called": len(called),
        "count": len(secs),
        "mean_seconds": statistics.fmean(secs) if secs else None,
        "p95_seconds": _pct(secs, 0.95),
        "max_seconds": max(secs) if secs else None,
    }


def _locked_in_logs(repo: Path) -> int:
    """ステップのログ（状態の置き場の下）に出た `database is locked` の回数。"""
    return sum(p.read_text(encoding="utf-8", errors="replace").count(LOCKED) for p in (repo / "plans").rglob("*.log"))


def summarize(rec: Recorder, sink: Path, repo: Path, args: argparse.Namespace, elapsed: float) -> dict[str, Any]:
    timings = [json.loads(line) for line in sink.read_text(encoding="utf-8").splitlines() if line.strip()] if sink.exists() else []
    runs = [c for c in rec.calls if c["role"] == "plan" and c["cmd"] == "next" and c["status"] == "ok"]
    overhead = [(c["seconds"] - STEPS_PER_PLAN * STEP_SECONDS) / STEPS_PER_PLAN for c in runs]
    return {
        "plans": args.plans,
        "wrappers": args.wrappers,
        "minutes": round(elapsed / 60, 2),
        "store": "postgresql" if args.db_url else "sqlite",
        "calls": len(rec.calls),
        "calls_by_status": dict(sorted(Counter(f"{c['cmd']}:{c['status']}" for c in rec.calls).items())),
        "database_is_locked": sum(c["locked"] for c in rec.calls) + _locked_in_logs(repo),
        "plan_rounds_ok": len(runs),
        "overhead_per_step_seconds": {"p50": _pct(overhead, 0.5), "p95": _pct(overhead, 0.95), "max": max(overhead) if overhead else None},
        "quick_check": _timing(timings, ("check_file",)),
        "sweep": _timing(timings, ("sweep", "sweep_url")),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--plans", type=int, default=20)
    p.add_argument("--wrappers", type=int, default=14)
    p.add_argument("--minutes", type=float, default=30.0)
    p.add_argument("--work")
    p.add_argument("--db-url")
    p.add_argument("--out")
    args = p.parse_args(argv)
    work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="ndf-plan-load-", dir=Path.cwd()))
    repo = work / "repo"
    repo.mkdir(parents=True)
    plans = _plans(repo, args.plans)
    sink = work / "timings.jsonl"
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    env.pop(ENV_DB, None)
    if args.db_url:
        env[ENV_DB] = args.db_url
    rec = Recorder()
    deadline = time.monotonic() + args.minutes * 60
    started = time.monotonic()
    threads = [threading.Thread(target=_plan_loop, args=(repo, env, sink, plans[i], deadline, rec)) for i in range(args.plans)]
    threads += [
        threading.Thread(target=_wrapper_loop, args=(repo, env, sink, plans[i % args.plans], deadline, rec)) for i in range(args.wrappers)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    out = summarize(rec, sink, repo, args, time.monotonic() - started)
    (work / "calls.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in rec.calls), encoding="utf-8")
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
