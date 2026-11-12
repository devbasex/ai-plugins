#!/usr/bin/env python3
"""selection-replay.py: スプリントの記録で、今の選び方と新しい選び方の「同時に動く本数」の平均を出す模擬（#2997 の決定 7・8）。

    python3 selection-replay.py run --sprint <スプリントの記録の置き場> --order <課題,課題,...> --out <結果のファイル>
                                    [--queue-files design|changed] [--estimate body|design|changed]
                                    [--issues 3] [--max 3] [--width 2] [--root DIR]

- 課題は `--order`（順位の順）。所要は各課題の `6-impl-<番号>-state/progress.jsonl` のステップの `seconds` の和
- 実装のキューの触るファイルは `--queue-files`: `design` は記録の `design-results.json`、`changed` は実装の PR
  （`pr` のステップの要約の URL）が変えたファイルを `gh pr view --json files` で読む
- 選ぶ側の見込みは `--estimate`: `body` は課題の本文（`gh issue view`）と `--root` の `git ls-files`、`design`・`changed` は
  実装のキューの触るファイルと同じ出し方（見込みが完全に当たる上限）
- 選び方: 残りの課題から `--issues` 件ずつスプリントを組む。今の選び方は順位の順、新しい選び方は `touch_estimate.spread`
  （逆転の幅 `--width`）。共有の一覧は `--root` の `.ndf/supervise.json` の `queue.shared`
- 平均は「所要の和 ÷ 経過」。経過はスプリントごとに、重なりの組（`admission.groups`）を組の中で直列にし、`--max` 本の枠へ
  長い組から割り当てた終わりの時刻で、スプリントの経過を足す
- 所要か触るファイルの記録が欠けた課題は模擬から外し、`skipped` に並べる。記録へは書かない。結果は `--out` にだけ書く

結果は `lib/step_result.py` の形の 1 行の JSON。`metrics` は
`{current: {average, elapsed, busy, sprints}, new: {...}, skipped: [{issue, reason}], width, estimate, queue_files}`。
終了コード: 0 = 出した / 2 = 記録を読めない / 3 = 模擬できる課題が 0 件。
"""

from __future__ import annotations

import argparse
import heapq
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import deps  # noqa: E402

deps.require("md", "schema")

import touch_estimate as te  # noqa: E402
from step_result import EXIT_PRECONDITION, EXIT_UNREADABLE, emit, result  # noqa: E402
from supervise_lib import admission  # noqa: E402

TOOL = "selection-replay"
PR_URL = re.compile(r"/pull/(\d+)")


def _gh_stdout(args: list[str], cwd: Path) -> str | None:
    got = subprocess.run(["gh", *args], cwd=str(cwd), capture_output=True, text=True)
    return got.stdout if got.returncode == 0 else None


def durations(sprint: Path, issue: int) -> tuple[float | None, int | None]:
    """課題の実装のプランの所要（ステップの秒の和）と、実装の PR の番号。記録が無ければ (None, None)。"""
    p = sprint / f"6-impl-{issue}-state" / "progress.jsonl"
    if not p.is_file():
        return None, None
    total, pr = 0.0, None
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("kind") != "step":
            continue
        total += float(row.get("seconds") or 0)
        if row.get("step") == "pr" and (m := PR_URL.search(str(row.get("summary") or ""))):
            pr = int(m.group(1))
    return total, pr


def design_result_files(sprint: Path) -> dict[int, list[str]]:
    p = sprint / "design-results.json"
    if not p.is_file():
        return {}
    rows = json.loads(p.read_text(encoding="utf-8")).get("rows") or []
    return {r["issue"]: list(r.get("files") or []) for r in rows if r.get("kind") == "実装する"}


def pr_changed_paths(pr: int | None, root: Path) -> list[str]:
    out = _gh_stdout(["pr", "view", str(pr), "--json", "files", "--jq", "[.files[].path]"], root) if pr else None
    return json.loads(out) if out else []


def body_files(issue: int, root: Path, files: list[str]) -> list[str]:
    body = _gh_stdout(["issue", "view", str(issue), "--json", "body", "--jq", ".body"], root)
    return te.from_body(body or "", files)


def sprint_elapsed(batch: list[int], seconds: dict[int, float], files: dict[int, list[str]], shared: list, slots: int) -> float:
    """1 スプリントの経過: 重なりの組を組の中で直列にし、`slots` 本の枠へ長い組から割り当てた終わりの時刻。"""
    groups = admission.groups({str(n): files[n] for n in batch}, shared)
    lengths = sorted((sum(seconds[int(n)] for n in g) for g in groups), reverse=True)
    ends = [0.0] * max(slots, 1)
    for length in lengths:
        heapq.heapreplace(ends, ends[0] + length)
    return max(ends)


def replay(order, seconds, files, estimates, shared, issues: int, slots: int, width: int) -> dict:
    """今の選び方（width 0）と新しい選び方（width）で、スプリントを順に組んで平均を出す。"""
    out = {}
    for name, w in (("current", 0), ("new", width)):
        left, sprints, total = list(order), [], 0.0
        while left:
            batch = te.spread(left, set(), estimates, issues, w, shared).take
            sprints.append(batch)
            total += sprint_elapsed(batch, seconds, files, shared, slots)
            left = [n for n in left if n not in batch]
        busy = sum(seconds[n] for n in order)
        out[name] = {
            "average": round(busy / total, 3) if total else 0.0,
            "elapsed": round(total, 1),
            "busy": round(busy, 1),
            "sprints": sprints,
        }
    return out


def cmd_run(a) -> int:
    sprint, root = Path(a.sprint), Path(a.root)
    order = [int(x) for x in re.split(r"[\s,]+", a.order) if x.isdigit()]
    try:
        design = design_result_files(sprint)
    except (OSError, ValueError) as e:
        emit(result(TOOL, "stopped", f"記録を読めない: {e}"), EXIT_UNREADABLE)
    tracked = subprocess.run(["git", "ls-files"], cwd=str(root), capture_output=True, text=True).stdout.splitlines()
    seconds, files, est, skipped = {}, {}, {}, []
    for n in order:
        sec, pr = durations(sprint, n)
        queue = design.get(n, []) if a.queue_files == "design" else pr_changed_paths(pr, root)
        if sec is None or not queue:
            skipped.append({"issue": n, "reason": "所要の記録が無い" if sec is None else "触るファイルの記録が無い"})
            continue
        seconds[n], files[n] = sec, queue
        if a.estimate == "body":
            paths = body_files(n, root, tracked)
        elif a.estimate == a.queue_files:
            paths = queue
        else:
            paths = design.get(n, []) if a.estimate == "design" else pr_changed_paths(pr, root)
        est[n] = te.Estimate(paths, te.BODY if a.estimate == "body" else te.DESIGN)
    kept = [n for n in order if n in seconds]
    if not kept:
        emit(result(TOOL, "stopped", "模擬できる課題が無い", skipped), EXIT_PRECONDITION)
    got = replay(kept, seconds, files, est, te.shared_list(root), a.issues, a.max, a.width)
    metrics = {**got, "skipped": skipped, "width": a.width, "estimate": a.estimate, "queue_files": a.queue_files}
    summary = f"同時に動く本数の平均: 今の選び方 {got['current']['average']} 本・新しい選び方 {got['new']['average']} 本（課題 {len(kept)} 件・外した {len(skipped)} 件）"
    line = json.dumps(result(TOOL, "ok", summary, skipped, metrics), ensure_ascii=False)
    Path(a.out).write_text(line + "\n", encoding="utf-8")
    print(summary)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--sprint", required=True)
    run.add_argument("--order", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--queue-files", choices=("design", "changed"), default="design")
    run.add_argument("--estimate", choices=("body", "design", "changed"), default="body")
    run.add_argument("--issues", type=int, default=3)
    run.add_argument("--max", type=int, default=3)
    run.add_argument("--width", type=int, default=2)
    run.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    return cmd_run(a)


if __name__ == "__main__":
    sys.exit(main())
