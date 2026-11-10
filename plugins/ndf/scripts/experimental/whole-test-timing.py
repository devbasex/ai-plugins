#!/usr/bin/env python3
"""whole-test-timing.py（試行）: 全体テストを同じ条件で測り、着手前後のテストの集合を照らす（#2291・#2309）。

    python3 whole-test-timing.py measure --root <dir> --out <JSON>
    python3 whole-test-timing.py diff --before <JSON> --after <JSON> --ledger <md> [--root <着手後の木>]
    python3 whole-test-timing.py startup --root <dir> [--runs N] <スクリプト>... [-- <引数>...]

measure は `.ndf/project.json` の `test.suites` の `command` を宣言の順に 1 本ずつ流し、最初の起動から最後の
終了までを壁時計とする。開始時の 1 分の load average が CPU の数を超えたら `reference`（判定に使わない参考の値）を
立てる。junit を宣言した suite は、junit の `time`（準備・本体・後始末の和）から件数・合計・遅いテストの上位 30 件・
ファイルごとの合計の上位 10 本を出し、`--out` へ nodeid ごとの結果と秒を書く。各 suite の pytest へ
`whole-test-timing/wtt_collect.py` を `PYTEST_PLUGINS` で読み込ませ、集められて実行から外された nodeid を
`outcome: "deselected"` で足す（宣言のコマンドは変えない）。

diff は着手前にあって着手後に無い nodeid のうち台帳に書かれていないもの（missing）、着手前に走っていて着手後に
skip・deselect になった nodeid（now_skipped・now_deselected。deselect は台帳に載っていても出す）、台帳に載る消した
テストの定義が着手後の木に残っているもの（still_defined）を出す。nodeid はパラメータの部分まで含めて文字どおりに照らす。

startup はスクリプトの最小の起動（既定は `--help`）を N 回流し、中央値・最小・最大のミリ秒を出す。

結果は lib/step_result.py の形の 1 行の JSON（`tool: "whole-test-timing"`）。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import statistics
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
COLLECT_DIR = Path(__file__).resolve().parent / "whole-test-timing"
NOT_RUN = ("skipped", "deselected")


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
    ran = {k: v for k, v in tests.items() if v["outcome"] not in NOT_RUN}
    metrics = {
        "tests": len(ran),
        "skipped": sum(1 for v in tests.values() if v["outcome"] == "skipped"),
        "deselected": sum(1 for v in tests.values() if v["outcome"] == "deselected"),
        "sum_seconds": round(sum(v["seconds"] for v in tests.values()), 1),
    }
    slow = sorted(tests.items(), key=lambda kv: (-kv[1]["seconds"], kv[0]))[:TOP_TESTS]
    files: dict[str, list] = {}
    for nid, v in tests.items():
        if v["outcome"] == "deselected":
            continue
        f = files.setdefault(nid.split("::", 1)[0], [0.0, 0])
        f[0] += v["seconds"]
        f[1] += 1
    top_files = sorted(files.items(), key=lambda kv: (-kv[1][0], kv[0]))[:TOP_FILES]
    items = [{"kind": "test", "name": nid, "seconds": v["seconds"], "result": v["outcome"]} for nid, v in slow]
    items += [{"kind": "file", "name": f, "seconds": round(s, 1), "count": n, "result": "sum"} for f, (s, n) in top_files]
    return metrics, items


def collect_env(root: Path, out: Path, base=None) -> dict:
    """suite の pytest へ wtt_collect を読み込ませる環境。プラグインが読み込んだ後に自分の項目を外す。"""
    env = dict(os.environ if base is None else base)
    env["PYTEST_PLUGINS"] = ",".join(p for p in (env.get("PYTEST_PLUGINS", ""), "wtt_collect") if p)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(COLLECT_DIR), env.get("PYTHONPATH", "")) if p)
    env["WTT_COLLECT_PYTHONPATH"] = str(COLLECT_DIR)
    env["WTT_COLLECT_DIR"] = str(out)
    env["WTT_COLLECT_ROOT"] = str(root)
    return env


def read_deselected(collect_dir: Path) -> list[str]:
    """プロセスごとの記録を合わせ、集められて残らなかった nodeid を返す。"""
    collected: set[str] = set()
    kept: set[str] = set()
    for f in sorted(collect_dir.glob("*.json")) if collect_dir.is_dir() else []:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise StepError(f"収集の記録を読めない: {f}: {e}", EXIT_UNREADABLE)
        collected.update(data.get("collected", []))
        kept.update(data.get("kept", []))
    return sorted(collected - kept)


def run_suites(root: Path, suites: list[dict], log_dir: Path) -> tuple[list[dict], float]:
    log_dir.mkdir(parents=True, exist_ok=True)
    runs = []
    first = time.monotonic()
    for s in suites:
        name = str(s.get("name") or len(runs))
        env = collect_env(root, log_dir / f"{name}.collect")
        start = time.monotonic()
        with (log_dir / f"{name}.log").open("w", encoding="utf-8") as log:
            p = subprocess.run(["bash", "-c", s["command"]], cwd=root, stdout=log, stderr=subprocess.STDOUT, env=env)
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
        name = str(s.get("name") or suites.index(s))
        for nid in read_deselected(out.with_suffix(".logs") / f"{name}.collect"):
            tests.setdefault(nid, {"outcome": "deselected", "seconds": 0.0})
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


def _defined(tree: ast.Module, names: list[str]) -> bool:
    """クラスの入れ子を辿り、最後の名前の関数（またはクラス）の定義があるか。"""
    body = tree.body
    for i, name in enumerate(names):
        last = i == len(names) - 1
        kinds = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef) if last else (ast.ClassDef,)
        node = next((n for n in body if isinstance(n, kinds) and n.name == name), None)
        if node is None:
            return False
        body = getattr(node, "body", [])
    return True


def still_defined(root: Path, nid: str, after: dict) -> bool:
    """消したテストの定義が着手後の木に残るか。パラメータのケースは、同じ関数のほかのケースが走っていれば消えたとみなす。"""
    path, *names = nid.split("::")
    if not names or not path.endswith(".py"):
        return False
    try:
        tree = ast.parse((root / path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return False
    base = names[-1].split("[", 1)[0]
    if not _defined(tree, [*names[:-1], base]):
        return False
    if "[" not in names[-1]:
        return True
    prefix = "::".join([path, *names[:-1], base]) + "["
    return not any(k.startswith(prefix) for k in after)


def compare_sets(before: dict, after: dict, ledger: set[str], root: Path | None = None) -> dict:
    ran_before = {k for k, v in before.items() if v["outcome"] not in NOT_RUN}
    missing = sorted(k for k in ran_before if k not in after and k not in ledger)
    skipped = sorted(k for k in ran_before if k in after and after[k]["outcome"] == "skipped")
    deselected = sorted(k for k in ran_before if k in after and after[k]["outcome"] == "deselected")
    removed = sorted(k for k in ran_before if k not in after and k in ledger)
    defined = [k for k in removed if root is not None and still_defined(root, k, after)]
    return {"missing": missing, "now_skipped": skipped, "now_deselected": deselected, "still_defined": defined}


def cmd_diff(a) -> None:
    before, after = _read_out(a.before), _read_out(a.after)
    try:
        ledger = ledger_nodeids(Path(a.ledger).read_text(encoding="utf-8"))
    except OSError as e:
        raise StepError(f"台帳を読めない: {a.ledger}: {e}", EXIT_UNREADABLE)
    found = compare_sets(before, after, ledger, Path(a.root).resolve())
    b, f = summarize_tests(before)[0], summarize_tests(after)[0]
    metrics = {"before": b, "after": f, **{k: len(v) for k, v in found.items()}}
    labels = {"missing": "not-in-ledger", "now_skipped": "skipped", "now_deselected": "deselected", "still_defined": "defined"}
    items = [{"kind": k, "name": n, "result": labels[k]} for k in labels for n in found[k]]
    status = "stopped" if items else "ok"
    summary = (
        f"着手前 {b['tests']} 件・{b['sum_seconds']} 秒 → 着手後 {f['tests']} 件・{f['sum_seconds']} 秒。"
        f"台帳に無い消えたテスト {len(found['missing'])} 件・skip に変わったテスト {len(found['now_skipped'])} 件・"
        f"deselect に変わったテスト {len(found['now_deselected'])} 件・定義が残る消したテスト {len(found['still_defined'])} 件"
    )
    emit(result(TOOL, status, summary, items, metrics))


def time_startup(root: Path, script: str, args: list[str], runs: int) -> dict:
    ms, codes = [], []
    for _ in range(runs):
        start = time.monotonic()
        p = subprocess.run([sys.executable, script, *args], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ms.append((time.monotonic() - start) * 1000)
        codes.append(p.returncode)
    return {
        "kind": "startup",
        "name": script,
        "result": str(codes[0]) if len(set(codes)) == 1 else "varied",
        "median_ms": round(statistics.median(ms), 1),
        "min_ms": round(min(ms), 1),
        "max_ms": round(max(ms), 1),
        "exit_codes": sorted(set(codes)),
    }


def cmd_startup(a) -> None:
    root = Path(a.root or ".").resolve()
    if a.runs < 1:
        raise StepError("--runs は 1 以上", EXIT_UNREADABLE)
    absent = [s for s in a.scripts if not (root / s).is_file()]
    if absent:
        raise StepError(f"スクリプトが無い: {', '.join(absent)}", EXIT_UNREADABLE)
    load1 = os.getloadavg()[0]
    cpus = os.cpu_count() or 1
    args = a.extra if a.extra is not None else ["--help"]
    items = [time_startup(root, s, args, a.runs) for s in a.scripts]
    metrics = {"load_average_1m": round(load1, 2), "cpus": cpus, "reference": load1 > cpus, "runs": a.runs, "args": args}
    varied = [i["name"] for i in items if i["result"] == "varied"]
    summary = "・".join(f"{Path(i['name']).name} {i['median_ms']} ms" for i in items) + f"（中央値・{a.runs} 回）"
    if varied:
        summary += f"。終了コードが回ごとに違った: {', '.join(varied)}"
    emit(result(TOOL, "stopped" if varied else "ok", summary, items, metrics), 1 if varied else 0)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    extra = None
    if "--" in argv:
        extra = argv[argv.index("--") + 1 :]
        argv = argv[: argv.index("--")]
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
    d.add_argument("--root", default=".", help="着手後の木（消したテストの定義を探す）")
    d.set_defaults(func=cmd_diff)
    st = sub.add_parser("startup")
    st.add_argument("--root", default=".")
    st.add_argument("--runs", type=int, default=10)
    st.add_argument("scripts", nargs="+")
    st.set_defaults(func=cmd_startup, extra=extra)
    return main_with(ap, lambda a: TOOL, argv)


if __name__ == "__main__":
    main()
