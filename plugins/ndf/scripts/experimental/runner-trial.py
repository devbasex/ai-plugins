#!/usr/bin/env python3
"""runner-trial.py: #1142 の不足 i の設計の前に、プランの実行とキューを置き換えるライブラリを試す（実験版）。

    python3 runner-trial.py check <候補> [--root DIR]   # 表せるか・キュー・再開・進捗ログと wait を確かめる
    python3 runner-trial.py cost [<候補>...] [--work DIR]  # uv での解決と導入・大きさ・import の秒数・依存の数

候補は langgraph（SqliteSaver）・burr（SQLitePersister）・dbos（DBOS Transact の SQLite）。プランは
supervise.py new impl の雛形（rt_common.PLAN_STEPS）を、ステップを偽物（sleep と決めた終了コード）にして流す。
依存は lib/deps.py と同じ形（uv の環境へ起動し直す）で解決し、宣言と lock は隣の runner-trial/ にある。
環境は ~/.cache/ndf/venv/runner-trial-<候補> に置く（NDF_DEPS_VENV を接頭辞に変えられる）。
結果は lib/step_result.py の形の 1 行の JSON。終了コード 0 = ok / 1 = 確かめたことが成り立たない / 3 = 前提が無い。

内部の副命令（check が起動する）:
    python3 runner-trial.py run <候補> --state DIR --plan NAME --scenario S [--approve] [--slots DIR]
    python3 runner-trial.py dbos-queue --root DIR --stages JSON
    python3 runner-trial.py noop <候補>                 # 起動し直しと import だけ（cost が秒数を測る）
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT = HERE.parent / "runner-trial"
sys.path.insert(0, str(HERE.parents[1] / "lib"))
sys.path.insert(0, str(PROJECT))
import deps as dt  # noqa: E402  uv を探す・入れる手は安定版の lib/deps.py のものを使う
from step_result import emit, result  # noqa: E402

import rt_common as rc  # noqa: E402

TOOL = "runner-trial"
CANDS = {"langgraph": "rt_langgraph", "burr": "rt_burr", "dbos": "rt_dbos"}
IMPORTS = {"langgraph": "from langgraph.graph import StateGraph; from langgraph.checkpoint.sqlite import SqliteSaver",
           "burr": "from burr.core import ApplicationBuilder; from burr.core.persistence import SQLitePersister",
           "dbos": "from dbos import DBOS, Queue"}
TOP = {"langgraph": "langgraph", "burr": "burr", "dbos": "dbos"}
SUPERVISE = HERE.parents[1] / "supervise.py"
EXIT = {"done": 0, "gate": 10, "stopped": 1, "limit": 1}


def venv_of(cand: str) -> str:
    return (os.environ.get("NDF_DEPS_VENV") or str(Path.home() / ".cache/ndf/venv/runner-trial")) + f"-{cand}"


def reexec_into(cand: str) -> None:
    """候補のパッケージが import できる環境で動いていることを保証する。無ければ uv の環境へ起動し直す。"""
    if importlib.util.find_spec(TOP[cand]):
        return
    if os.environ.get("NDF_DEPS_REEXEC"):
        emit(result(TOOL, "stopped", f"uv の環境へ起動し直したが {cand} を import できない"), 3)
    uv = dt.find_uv() or dt.install_uv()
    if not uv:
        emit(result(TOOL, "stopped", f"uv を入れられない（{dt.UV_VERSION}）"), 3)
    env = dict(os.environ, NDF_DEPS_REEXEC="1", UV_PROJECT_ENVIRONMENT=venv_of(cand))
    argv = [uv, "run", "--quiet", "--frozen", "--project", str(PROJECT), "--extra", cand,
            "python", str(HERE), *sys.argv[1:]]
    os.execve(uv, argv, env)


def adapter(cand: str):
    reexec_into(cand)
    return importlib.import_module(CANDS[cand])


# --- 内部の副命令 ---

def cmd_run(a) -> None:
    mod = adapter(a.cand)
    slots = rc.Slots(Path(a.slots), {"graphql": 1}) if a.slots else None
    ctx = rc.Ctx(Path(a.state), a.plan, a.scenario, slots)
    out = getattr(mod, f"run_{a.cand}")(ctx, a.approve)  # 候補ごとの run_<候補>
    out["listen_ports"] = rc.own_listen_ports()
    exit_now = out.pop("exit_now", False)
    print(json.dumps(out, ensure_ascii=False), flush=True)
    code = EXIT.get(out.get("status"), 2)
    if exit_now or a.cand == "dbos":
        mod.hard_exit(code)
    sys.exit(code)


def cmd_dbos_queue(a) -> None:
    mod = adapter("dbos")
    out = mod.run_queue(Path(a.root), json.loads(a.stages))
    print(json.dumps(out, ensure_ascii=False), flush=True)
    mod.hard_exit(0)


def cmd_noop(a) -> None:
    reexec_into(a.cand)
    exec(IMPORTS[a.cand], {})


# --- check: 表せるか ---

def run_sub(cand: str, root: Path, plan: str, scenario: str, *extra: str, slots: Path | None = None,
            timeout: float = 120) -> tuple[int, dict]:
    argv = [sys.executable, str(HERE), "run", cand, "--state", str(root / f"{plan}-state"), "--plan", plan,
            "--scenario", scenario, *extra] + (["--slots", str(slots)] if slots else [])
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    last = (p.stdout.strip().splitlines() or ["{}"])[-1]
    try:
        return p.returncode, json.loads(last)
    except json.JSONDecodeError:
        return p.returncode, {"error": (p.stderr or p.stdout)[-600:]}


def steps_started(root: Path, plan: str | None = None) -> list[dict]:
    ev = root / "events.jsonl"
    if not ev.is_file():
        return []
    rows = [json.loads(x) for x in ev.read_text().splitlines() if x.strip()]
    return [r for r in rows if r["what"] == "start" and (plan is None or r["plan"] == plan)]


def check_express(cand: str, root: Path) -> dict:
    items = []
    for sc in ("pass", "branch", "loop", "stop"):
        d = root / f"express-{sc}"
        t0 = time.time()
        code, out = run_sub(cand, d, sc, sc)
        want_status, want_path = rc.reference_path(sc)
        items.append({"scenario": sc, "ok": out.get("status") == want_status and out.get("path") == want_path,
                      "status": out.get("status"), "steps": len(out.get("path") or []), "exit": code,
                      "seconds": round(time.time() - t0, 2), "error": out.get("error"),
                      "listen_ports": out.get("listen_ports")})
    d = root / "express-gate"
    c1, o1 = run_sub(cand, d, "gate", "gate")
    n1 = len(steps_started(d))
    c2, o2 = run_sub(cand, d, "gate", "gate")  # 承認の無い打ち直しは、同じ承認ゲートで止まったまま
    n2 = len(steps_started(d))
    c3, o3 = run_sub(cand, d, "gate", "gate", "--approve")
    started = [r["step"] for r in steps_started(d)]
    want_status, want_path = rc.reference_path("gate")
    items.append({"scenario": "gate", "ok": (o1.get("status"), o2.get("status"), o3.get("status")) == (
        "gate", "gate", "done") and n1 == n2 and started == want_path, "exits": [c1, c2, c3],
        "rerun_without_approve_ran_steps": n2 - n1, "after_approve": started[n1:],
        "error": o1.get("error") or o2.get("error") or o3.get("error")})
    return {"check": "express", "ok": all(i["ok"] for i in items), "items": items}


# --- check: キュー ---

def overlap(intervals: list[tuple[float, float]]) -> int:
    pts = sorted([(s, 1) for s, _ in intervals] + [(e, -1) for _, e in intervals], key=lambda x: (x[0], x[1]))
    cur = best = 0
    for _, d in pts:
        cur += d
        best = max(best, cur)
    return best


def queue_stats(root: Path) -> dict:
    rows = [json.loads(x) for x in (root / "events.jsonl").read_text().splitlines() if x.strip()]
    plans, gql, opened, last_end, waited = {}, [], {}, {}, 0
    for r in rows:
        if r["what"] not in ("start", "end"):
            continue
        if r["what"] == "end":
            last_end[r["plan"]] = r["t"]
        elif "graphql" in r.get("resources", []) and r["t"] - last_end.get(r["plan"], r["t"]) > 0.1:
            waited += 1  # 同じプランの前のステップの終わりから、枠が空くのを待った
        s, e = plans.get(r["plan"], (r["t"], r["t"]))
        plans[r["plan"]] = (min(s, r["t"]), max(e, r["t"]))
        if "graphql" in r.get("resources", []):
            key = (r["plan"], r["step"], r["visit"])
            if r["what"] == "start":
                opened[key] = r["t"]
            elif key in opened:
                gql.append((opened.pop(key), r["t"]))
    return {"plans": plans, "max_plans": overlap(list(plans.values())), "max_graphql": overlap(gql),
            "graphql_waited": waited}


def self_built_queue(cand: str, root: Path, stages: list[list[dict]], width: int = 2) -> list:
    """LangGraph と Burr はキューを持たないため、supervise.py queue と同じくプランごとに子プロセスを起こす。"""
    results = []
    for i, stage in enumerate(stages):
        pending, running, outs = list(stage), {}, []
        while pending or running:
            while pending and len(running) < width:
                p = pending.pop(0)
                argv = [sys.executable, str(HERE), "run", cand, "--state", str(root / f"{p['plan']}-state"),
                        "--plan", p["plan"], "--scenario", p["scenario"], "--slots", str(root / "slots")]
                running[p["plan"]] = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                                      text=True)
            for name, proc in list(running.items()):
                if proc.poll() is not None:
                    outs.append(json.loads(proc.stdout.read().strip().splitlines()[-1]))
                    del running[name]
            time.sleep(0.05)
        results.append({"stage": i, "outcomes": outs})
        if any(o.get("status") != "done" for o in outs):
            results.append({"stage": i + 1, "skipped": [p["plan"] for s in stages[i + 1:] for p in s]})
            break
    return results


def write_listing(root: Path, stages: list[list[dict]]) -> Path:
    """supervise.py queue と同じ置き場: プラン <root>/<名>.json・<名>-state/・done の隣の <done>.plans.json。"""
    names = [p["plan"] for s in stages for p in s]
    for n in names:
        (root / f"{n}.json").write_text(json.dumps({"steps": rc.PLAN_STEPS}, ensure_ascii=False))
        (root / f"{n}-state").mkdir(parents=True, exist_ok=True)
    done = root / "queue-done.json"
    done.with_suffix(".plans.json").write_text(json.dumps(
        {"started": rc.iso_now(), "plans": [str(root / f"{n}.json") for n in names], "offsets": {}}))
    return done


def sv_wait(done: Path) -> tuple[int, dict]:
    p = subprocess.run([sys.executable, str(SUPERVISE), "wait", str(done), "--timeout", "3", "--poll", "0.2"],
                       capture_output=True, text=True, timeout=60)
    try:
        return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return p.returncode, {"error": p.stderr[-400:]}


def check_queue(cand: str, root: Path) -> dict:
    items = []
    for label, stop_plan in (("all-done", None), ("stage1-stops", "q2")):
        d = root / f"queue-{label}"
        d.mkdir(parents=True, exist_ok=True)
        stages = [[{"plan": f"q{i}", "scenario": "stop" if f"q{i}" == stop_plan else "pass"} for i in (1, 2, 3, 4)],
                  [{"plan": f"q{i}", "scenario": "pass"} for i in (5, 6)]]
        done = write_listing(d, stages)
        t0 = time.time()
        if cand == "dbos":
            p = subprocess.run([sys.executable, str(HERE), "dbos-queue", "--root", str(d), "--stages",
                                json.dumps(stages)], capture_output=True, text=True, timeout=300)
            try:
                res = json.loads(p.stdout.strip().splitlines()[-1])
            except (json.JSONDecodeError, IndexError):
                res = [{"error": p.stderr[-600:]}]
        else:
            res = self_built_queue(cand, d, stages)
        secs = round(time.time() - t0, 2)
        st = queue_stats(d)
        s1 = [st["plans"][p["plan"]] for p in stages[0] if p["plan"] in st["plans"]]
        s2 = [st["plans"][p["plan"]] for p in stages[1] if p["plan"] in st["plans"]]
        order_ok = not s2 or min(s for s, _ in s2) >= max(e for _, e in s1)
        ran = sorted(st["plans"])
        want = [f"q{i}" for i in range(1, 7)] if stop_plan is None else ["q1", "q2", "q3", "q4"]
        w1 = sv_wait(done)  # done を書く前: queue の流すプランの attention で返る（stage1-stops のとき）
        (done).write_text(json.dumps({"status": "ok" if stop_plan is None else "stopped",
                                      "summary": f"{len(ran)} 本を流した"}, ensure_ascii=False))
        w2 = sv_wait(done)
        items.append({"case": label, "seconds": secs, "ran": ran, "max_plans": st["max_plans"],
                      "max_graphql": st["max_graphql"], "graphql_waited": st["graphql_waited"], "stage_order_ok": order_ok,
                      "wait_before_done": [w1[0], w1[1].get("status"), w1[1].get("metrics", {}).get("attention")],
                      "wait_after_done": [w2[0], w2[1].get("status")],
                      "ok": ran == want and st["max_plans"] <= 2 and st["max_graphql"] <= 1 and order_ok
                      and w1[0] == (20 if stop_plan else 3) and w2[0] == 0,
                      "raw": res if not isinstance(res, list) or any("error" in r for r in res) else None})
    return {"check": "queue", "ok": all(i["ok"] for i in items), "items": items}


# --- check: 再開 ---

def check_resume(cand: str, root: Path) -> dict:
    d = root / "resume"
    state = d / "k-state"
    argv = [sys.executable, str(HERE), "run", cand, "--state", str(state), "--plan", "k", "--scenario", "slow"]
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.time() + 120

    def reached() -> bool:
        return any(r["step"] == "test-limited" for r in steps_started(d))
    while time.time() < deadline and proc.poll() is None and not reached():
        time.sleep(0.05)
    if not reached():  # 落とす前に子が終わった、または時間切れ。再開を確かめる前提が成り立たない
        exited = proc.poll()
        if exited is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        reason = "120 秒で test-limited に届かない" if exited is None else f"test-limited の前に終了した（{exited}）"
        return {"check": "resume", "items": [{"before_kill": [r["step"] for r in steps_started(d)],
                                              "error": reason}], "ok": False}
    time.sleep(0.5)
    os.killpg(proc.pid, signal.SIGKILL)  # プランのプロセスと、流れているステップの子プロセスをまとめて落とす
    proc.wait()
    first = [r["step"] for r in steps_started(d)]
    code, out = run_sub(cand, d, "k", "slow")
    rows = steps_started(d)
    second = [r["step"] for r in rows[len(first):]]
    completed_before = first[:-1]  # 最後の 1 つは落ちたときに流れていた
    return {"check": "resume", "items": [{"before_kill": first, "after_restart": second,
                                          "restarted_at": second[0] if second else None,
                                          "rerun_completed_steps": [s for s in second if s in completed_before],
                                          "status": out.get("status"), "error": out.get("error")}],
            "ok": out.get("status") == "done" and bool(second) and second[0] == first[-1]
            and first + second[1:] == rc.reference_path("slow")[1]}


def cmd_check(a) -> None:
    root = Path(a.root or Path.home() / f".cache/ndf/runner-trial/check-{a.cand}-{int(time.time())}")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    checks = [check_express(a.cand, root), check_queue(a.cand, root), check_resume(a.cand, root)]
    ok = all(c["ok"] for c in checks)
    summary = f"{a.cand}: " + "・".join(f"{c['check']} {'ok' if c['ok'] else 'NG'}" for c in checks)
    emit(result(TOOL, "ok" if ok else "stopped", summary, checks, {"root": str(root)}), 0 if ok else 1)


# --- cost ---

def timed(argv: list[str], env: dict | None = None) -> float:
    t0 = time.time()
    subprocess.run(argv, env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return round(time.time() - t0, 3)


def dir_mb(p: Path) -> float:
    return round(sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink()) / 2**20, 1)


def cost_of(cand: str, uv: str, work: Path) -> dict:
    cache, venv, lockdir = work / f"cache-{cand}", work / f"venv-{cand}", work / f"lock-{cand}"
    for p in (cache, venv, lockdir):
        shutil.rmtree(p, ignore_errors=True)
    lockdir.mkdir(parents=True)
    text = (PROJECT / "pyproject.toml").read_text()
    pins = [ln.split("=", 1)[1].strip() for ln in text.splitlines() if ln.startswith(f"{cand} = ")][0]
    (lockdir / "pyproject.toml").write_text(
        f'[project]\nname = "rt-{cand}"\nversion = "0"\nrequires-python = ">=3.10"\ndependencies = {pins}\n'
        "[tool.uv]\npackage = false\n")
    env = dict(os.environ, UV_CACHE_DIR=str(cache), UV_PROJECT_ENVIRONMENT=str(venv))
    resolve = timed([uv, "lock", "--project", str(lockdir)], env)
    shutil.rmtree(cache)
    sync = [uv, "sync", "--frozen", "--project", str(PROJECT), "--extra", cand]
    install_cold = timed(sync, env)
    shutil.rmtree(venv)
    install_warm = timed(sync, env)
    py = str(venv / "bin/python")
    imp = IMPORTS[cand]
    import_cold = timed([py, "-c", imp])
    import_warm = statistics.median(timed([py, "-c", imp]) for _ in range(3))
    bare = statistics.median(timed([py, "-c", "pass"]) for _ in range(3))
    site = next(venv.glob("lib/python*/site-packages"))
    lock = (lockdir / "uv.lock").read_text()
    renv = dict(os.environ, NDF_DEPS_VENV=str(work / "reexec"))
    reexec_cold = timed([sys.executable, str(HERE), "noop", cand], renv)
    reexec_warm = statistics.median(timed([sys.executable, str(HERE), "noop", cand], renv) for _ in range(3))
    return {"candidate": cand, "resolve_s": resolve, "install_cold_s": install_cold, "install_warm_s": install_warm,
            "venv_mb": dir_mb(venv), "site_packages_mb": dir_mb(site),
            "packages": sum(1 for _ in site.glob("*.dist-info")), "locked_packages": lock.count("[[package]]") - 1,
            "import_cold_s": import_cold, "import_warm_s": import_warm, "python_bare_s": bare,
            "entry_with_reexec_cold_s": reexec_cold, "entry_with_reexec_warm_s": reexec_warm}


def cmd_cost(a) -> None:
    uv = dt.find_uv() or dt.install_uv()
    if not uv:
        emit(result(TOOL, "stopped", "uv を入れられない"), 3)
    work = Path(a.work or Path.home() / ".cache/ndf/runner-trial/cost")
    work.mkdir(parents=True, exist_ok=True)
    items = [cost_of(c, uv, work) for c in (a.cands or list(CANDS))]
    summary = "・".join(f"{i['candidate']} {i['site_packages_mb']}MB/{i['packages']}件/import {i['import_warm_s']}s"
                       for i in items)
    emit(result(TOOL, "ok", summary, items, {"uv": subprocess.run([uv, "--version"], capture_output=True,
                                                                  text=True).stdout.strip()}))


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("cand", choices=list(CANDS))
    c.add_argument("--root")
    k = sub.add_parser("cost")
    k.add_argument("cands", nargs="*", choices=list(CANDS))
    k.add_argument("--work")
    r = sub.add_parser("run")
    r.add_argument("cand", choices=list(CANDS))
    r.add_argument("--state", required=True)
    r.add_argument("--plan", required=True)
    r.add_argument("--scenario", required=True, choices=list(rc.SCENARIOS))
    r.add_argument("--approve", action="store_true")
    r.add_argument("--slots")
    q = sub.add_parser("dbos-queue")
    q.add_argument("--root", required=True)
    q.add_argument("--stages", required=True)
    n = sub.add_parser("noop")
    n.add_argument("cand", choices=list(CANDS))
    a = p.parse_args()
    {"check": cmd_check, "cost": cmd_cost, "run": cmd_run, "dbos-queue": cmd_dbos_queue, "noop": cmd_noop}[a.cmd](a)


if __name__ == "__main__":
    main()
