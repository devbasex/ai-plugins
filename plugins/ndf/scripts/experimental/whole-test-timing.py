#!/usr/bin/env python3
"""whole-test-timing.py（試行）: 全体テストを同じ条件で測り、着手前後のテストの集合を照らす（#2291）。

    python3 whole-test-timing.py measure --root <dir> --out <JSON>
    python3 whole-test-timing.py diff --before <JSON> --after <JSON> --ledger <md>

measure は `.ndf/project.json` の `test.suites` の `command` を宣言の順に 1 本ずつ流し、最初の起動から最後の
終了までを壁時計とする。開始時の 1 分の load average が CPU の数を超えたら `reference`（判定に使わない参考の値）を
立てる。junit を宣言した suite は、junit の `time`（準備・本体・後始末の和）から件数・合計・遅いテストの上位 30 件・
ファイルごとの合計の上位 10 本を出し、`--out` へ nodeid ごとの結果と秒を書く。

diff は着手前にあって着手後に無い nodeid のうち台帳に書かれていないものと、着手前に走っていて着手後に skip に
なった nodeid を出す。nodeid はパラメータの部分まで含めて文字どおりに照らす。

結果は lib/step_result.py の形の 1 行の JSON（`tool: "whole-test-timing"`）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from step_result import EXIT_UNREADABLE, StepError, emit, main_with, result  # noqa: E402

TOOL = "whole-test-timing"
TOP_TESTS = 30
TOP_FILES = 10


def load_suites(root: Path) -> list[dict]:
    try:
        data = json.loads((root / ".ndf" / "project.json").read_text(encoding="utf-8"))
        suites = data["test"]["suites"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise StepError(f".ndf/project.json の test.suites を読めない: {e}", EXIT_UNREADABLE)
    if not isinstance(suites, list) or not all(isinstance(s, dict) and s.get("command") for s in suites):
        raise StepError("test.suites の各要素に command が要る", EXIT_UNREADABLE)
    return suites


def _nodeid(case: ET.Element) -> str:
    """xunit1 の testcase から pytest の nodeid を組む（file と classname の差がクラスの部分）。"""
    file = case.get("file") or ""
    name = case.get("name") or ""
    classname = case.get("classname") or ""
    module = file[:-3].replace("/", ".") if file.endswith(".py") else ""
    rest = classname[len(module) :].lstrip(".") if module and classname.startswith(module) else ""
    parts = [file or classname, *(p for p in rest.split(".") if p), name]
    return "::".join(parts)


def _outcome(case: ET.Element) -> str:
    for tag in ("failure", "error", "skipped"):
        if case.find(tag) is not None:
            return {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
    return "passed"


def read_junit(path: Path) -> dict[str, dict]:
    """nodeid → {"outcome", "seconds"}。同じ nodeid が 2 度出たら（準備と後始末の誤り）秒を足し、悪い方の結果を残す。"""
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError) as e:
        raise StepError(f"junit を読めない: {path}: {e}", EXIT_UNREADABLE)
    tests: dict[str, dict] = {}
    for case in tree.iter("testcase"):
        nid = _nodeid(case)
        sec = float(case.get("time") or 0.0)
        out = _outcome(case)
        if nid in tests:
            prev = tests[nid]
            prev["seconds"] = round(prev["seconds"] + sec, 3)
            if out in ("failed", "error"):
                prev["outcome"] = out
            continue
        tests[nid] = {"outcome": out, "seconds": round(sec, 3)}
    return tests


def summarize_tests(tests: dict[str, dict]) -> tuple[dict, list[dict]]:
    ran = {k: v for k, v in tests.items() if v["outcome"] != "skipped"}
    metrics = {
        "tests": len(ran),
        "skipped": len(tests) - len(ran),
        "sum_seconds": round(sum(v["seconds"] for v in tests.values()), 1),
    }
    slow = sorted(tests.items(), key=lambda kv: (-kv[1]["seconds"], kv[0]))[:TOP_TESTS]
    files: dict[str, list] = {}
    for nid, v in tests.items():
        f = files.setdefault(nid.split("::", 1)[0], [0.0, 0])
        f[0] += v["seconds"]
        f[1] += 1
    top_files = sorted(files.items(), key=lambda kv: (-kv[1][0], kv[0]))[:TOP_FILES]
    items = [{"kind": "test", "name": nid, "seconds": v["seconds"], "result": v["outcome"]} for nid, v in slow]
    items += [{"kind": "file", "name": f, "seconds": round(s, 1), "count": n, "result": "sum"} for f, (s, n) in top_files]
    return metrics, items


def run_suites(root: Path, suites: list[dict], log_dir: Path) -> tuple[list[dict], float]:
    log_dir.mkdir(parents=True, exist_ok=True)
    runs = []
    first = time.monotonic()
    for s in suites:
        name = str(s.get("name") or len(runs))
        start = time.monotonic()
        with (log_dir / f"{name}.log").open("w", encoding="utf-8") as log:
            p = subprocess.run(["bash", "-c", s["command"]], cwd=root, stdout=log, stderr=subprocess.STDOUT)
        runs.append({"name": name, "seconds": round(time.monotonic() - start, 1), "exit": p.returncode})
    return runs, round(time.monotonic() - first, 1)


def cmd_measure(a) -> None:
    root = Path(a.root or ".").resolve()
    suites = load_suites(root)
    load1 = os.getloadavg()[0]
    cpus = os.cpu_count() or 1
    out = Path(a.out).resolve()
    runs, wall = run_suites(root, suites, out.with_suffix(".logs"))
    tests: dict[str, dict] = {}
    for s in suites:
        if s.get("junit"):
            tests.update(read_junit(root / s["junit"]))
    counted, items = summarize_tests(tests)
    metrics = {
        "load_average_1m": round(load1, 2),
        "cpus": cpus,
        "reference": load1 > cpus,
        "wall_seconds": wall,
        "suites": runs,
        **counted,
    }
    out.write_text(json.dumps({"metrics": metrics, "tests": tests}, ensure_ascii=False, indent=1), encoding="utf-8")
    failed = [r["name"] for r in runs if r["exit"] != 0]
    status = "stopped" if failed else "ok"
    summary = f"壁時計 {wall} 秒・{counted['tests']} 件・合計 {counted['sum_seconds']} 秒・load average {load1:.2f}"
    if failed:
        summary += f"。落ちた suite: {', '.join(failed)}"
    emit(result(TOOL, status, summary, items, metrics))


def ledger_nodeids(text: str) -> set[str]:
    """台帳の表の行にバッククォートで書かれた nodeid（`::` を含むもの）。"""
    ids = set()
    for line in text.splitlines():
        if line.lstrip().startswith("|"):
            ids.update(m for m in re.findall(r"`([^`]+)`", line) if "::" in m)
    return ids


def _read_out(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data["tests"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise StepError(f"計測の記録を読めない: {path}: {e}", EXIT_UNREADABLE)


def compare_sets(before: dict, after: dict, ledger: set[str]) -> dict:
    ran_before = {k for k, v in before.items() if v["outcome"] != "skipped"}
    missing = sorted(k for k in ran_before if k not in after and k not in ledger)
    skipped = sorted(k for k in ran_before if k in after and after[k]["outcome"] == "skipped")
    return {"missing": missing, "now_skipped": skipped}


def cmd_diff(a) -> None:
    before, after = _read_out(a.before), _read_out(a.after)
    try:
        ledger = ledger_nodeids(Path(a.ledger).read_text(encoding="utf-8"))
    except OSError as e:
        raise StepError(f"台帳を読めない: {a.ledger}: {e}", EXIT_UNREADABLE)
    found = compare_sets(before, after, ledger)
    b, f = summarize_tests(before)[0], summarize_tests(after)[0]
    metrics = {"before": b, "after": f, "missing": len(found["missing"]), "now_skipped": len(found["now_skipped"])}
    items = [{"kind": "missing", "name": n, "result": "not-in-ledger"} for n in found["missing"]]
    items += [{"kind": "now_skipped", "name": n, "result": "skipped"} for n in found["now_skipped"]]
    status = "stopped" if items else "ok"
    summary = (
        f"着手前 {b['tests']} 件・{b['sum_seconds']} 秒 → 着手後 {f['tests']} 件・{f['sum_seconds']} 秒。"
        f"台帳に無い消えたテスト {len(found['missing'])} 件・skip に変わったテスト {len(found['now_skipped'])} 件"
    )
    emit(result(TOOL, status, summary, items, metrics))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--root", default=".")
    m.add_argument("--out", required=True)
    m.set_defaults(func=cmd_measure)
    d = sub.add_parser("diff")
    d.add_argument("--before", required=True)
    d.add_argument("--after", required=True)
    d.add_argument("--ledger", required=True)
    d.set_defaults(func=cmd_diff)
    return main_with(ap, lambda a: TOOL, argv)


if __name__ == "__main__":
    main()
