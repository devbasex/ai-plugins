#!/usr/bin/env python3
"""queue-overlap-replay.py: 過去のスプリントの実装のプランで重なりの組を判定し直し、偽の重なりを数える（#1811 の F5）。

    python3 queue-overlap-replay.py [--ndf DIR] [--root DIR] [--sprint 名前 ...] [--before 版] [--out 結果のファイル]

- スプリントは `--ndf`（省くと `--root/.ndf`）の `sprint-*` と `loop/*/lap-*/sprint` のうち、数えられるプランが 2 本以上の
  もの。`--sprint`（表の名前。例 `lcost-1`・`lcore lap-1`）を渡すとそれだけに絞る
- 数えられるプランは `<番号>-impl-<課題>.json` のうち、実装のステップの記録（`<プラン>-state/01-impl.out`）と、
  報告（`<プラン>-state/report.md`）の `Pull Request:` の行を持つもの。同じ番号のプランを同じステージとして組を比べる
- 触るファイルは、設計の結果のステージと同じく、末尾が `/` の行と `--root` の下の既存のディレクトリの行を末尾 `/` に揃える
- 重なりの組は `supervise_lib/admission.py` の `overlaps` で決める（規則を自分で持たない）。共有の一覧は `--root` の
  `.ndf/supervise.json` の `queue.shared`。`--before` を渡すと、その版の `admission.py` と `queue.shared` で判定した数を並べる
- 偽の重なりは、両方の PR が変えたファイル（`gh api --paginate .../pulls/<番号>/files`）に共通が 1 つも無い組。
  組の理由は「同じファイル」（両方が等しいファイルを宣言した）・「共有の一覧」・「ディレクトリ」（`--before` の規則だけ）
- スプリントの状態へは書かない。標準出力へ Markdown の表を出し、`--out` に `lib/step_result.py` の形の 1 行の JSON を書く

`metrics.false_by_dir_or_shared` は新しい規則で、ディレクトリの宣言と共有の一覧による偽の重なりの組の数
（#1811 の受け入れ条件 6 は 0 を求める）。終了コード: 0 = 数えた / 2 = PR の変更を読めない / 3 = スプリントが 0 本。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import types
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS / "lib")]
from step_result import EXIT_PRECONDITION, EXIT_UNREADABLE, result  # noqa: E402
from supervise_lib import admission, decl  # noqa: E402
from supervise_lib.paths import is_dir_entry  # noqa: E402
from supervise_lib.queue import touched_files  # noqa: E402

TOOL = "queue-overlap-replay"
PLAN = re.compile(r"^(\d+)-impl-(\d+)\.json$")
PR = re.compile(r"Pull Request:\s*\S*/pull/(\d+)")


def sprint_name(d: Path, ndf: Path) -> str:
    rel = d.relative_to(ndf).parts
    return rel[0].removeprefix("sprint-") if len(rel) == 1 else f"{rel[1]} {rel[2]}"


def sprint_dirs(ndf: Path) -> list[Path]:
    return sorted([*ndf.glob("sprint-*"), *ndf.glob("loop/*/lap-*/sprint")], key=lambda d: sprint_name(d, ndf))


def countable(d: Path, root: str) -> list[dict]:
    """数えられるプラン `{stage, issue, pr, files}`（ステージと課題の順）。"""
    out = []
    for p in d.glob("*-impl-*.json"):
        m = PLAN.match(p.name)
        state = p.with_name(p.stem + "-state")
        report = state / "report.md"
        pr = PR.search(report.read_text(encoding="utf-8")) if m and (state / "01-impl.out").exists() and report.exists() else None
        if not pr:
            continue
        files = touched_files(p.read_text(encoding="utf-8"))
        files = [f if f.endswith("/") or not is_dir_entry(f, root) else f + "/" for f in files]
        out.append({"stage": int(m[1]), "issue": int(m[2]), "pr": int(pr[1]), "files": files})
    return sorted(out, key=lambda x: (x["stage"], x["issue"]))


def pr_files(pr: int, root: str, cache: dict[int, set[str]]) -> set[str]:
    if pr not in cache:
        cmd = ["gh", "api", "--paginate", f"repos/{{owner}}/{{repo}}/pulls/{pr}/files", "--jq", ".[].filename"]
        p = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        if p.returncode != 0:
            raise RuntimeError(f"PR #{pr} の変更を読めない: {p.stderr.strip()[-200:]}")
        cache[pr] = set(p.stdout.split())
    return cache[pr]


def old_rule(rev: str, root: str) -> tuple[types.ModuleType, list[dict]]:
    """`rev` の版の admission.py と queue.shared。"""
    show = lambda path: subprocess.run(["git", "show", f"{rev}:{path}"], cwd=root, capture_output=True, text=True, check=True).stdout  # noqa: E731
    spec = importlib.util.spec_from_loader(f"admission_{rev}", loader=None)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(show("plugins/ndf/scripts/supervise_lib/admission.py"), f"{rev}:admission.py", "exec"), mod.__dict__)
    try:
        shared = decl.queue_decl(json.loads(show(".ndf/supervise.json")))["shared"]
    except subprocess.CalledProcessError:
        shared = []
    return mod, shared


def cause(o: dict, plans: dict[str, list[str]]) -> str:
    a, b = o["paths"]
    key = lambda f: PurePosixPath(f).parts  # noqa: E731
    if a.endswith("/") or b.endswith("/"):
        return "ディレクトリ"
    both = {key(f) for f in plans[o["a"]]} & {key(f) for f in plans[o["b"]]}
    return "同じファイル" if key(a) in both and key(b) in both else "共有の一覧"


def judge(rule, shared: list[dict], plans: list[dict], changed) -> dict:
    """ステージごとに組を判定し、`{pairs, overlaps, false: [{a, b, path, cause}]}` を返す。"""
    pairs, found, false = 0, 0, []
    for stage in sorted({p["stage"] for p in plans}):
        mine = {f"#{p['issue']}": p for p in plans if p["stage"] == stage}
        files = {k: p["files"] for k, p in mine.items()}
        pairs += len(mine) * (len(mine) - 1) // 2
        for o in rule.overlaps(files, shared):
            found += 1
            if not changed(mine[o["a"]]["pr"]) & changed(mine[o["b"]]["pr"]):
                false.append({"a": o["a"], "b": o["b"], "path": o["paths"][0], "cause": cause(o, files)})
    return {"pairs": pairs, "overlaps": found, "false": false}


def table(rows: list[dict], before: bool) -> str:
    head = ["スプリント", "数えたプラン", "比べた組"]
    head += ["今の規則: 重なり", "今の規則: 偽の重なり"] if before else []
    head += ["新しい規則: 重なり", "新しい規則: 偽の重なり"]
    lines = ["| " + " | ".join(head) + " |", "| --- |" + " ---: |" * (len(head) - 1)]
    total = {"plans": 0, "pairs": 0, "bo": 0, "bf": 0, "no": 0, "nf": 0}
    for r in rows:
        cells = [r["sprint"], r["plans"], r["after"]["pairs"]]
        cells += [r["before"]["overlaps"], len(r["before"]["false"])] if before else []
        cells += [r["after"]["overlaps"], len(r["after"]["false"])]
        lines.append("| " + " | ".join(map(str, cells)) + " |")
        for k, v in (("plans", r["plans"]), ("pairs", r["after"]["pairs"]), ("no", r["after"]["overlaps"]), ("nf", len(r["after"]["false"]))):
            total[k] += v
        if before:
            total["bo"] += r["before"]["overlaps"]
            total["bf"] += len(r["before"]["false"])
    cells = ["合計", total["plans"], total["pairs"]] + ([total["bo"], total["bf"]] if before else []) + [total["no"], total["nf"]]
    lines.append("| " + " | ".join(map(str, cells)) + " |")
    falses = [(r["sprint"], f) for r in rows for f in r["after"]["false"]]
    lines += ["", "新しい規則で残る偽の重なり:", ""]
    if falses:
        lines += ["| スプリント | 組 | 重なったパス | 理由 |", "| --- | --- | --- | --- |"]
        lines += [f"| {s} | {f['a']} と {f['b']} | `{f['path']}` | {f['cause']} |" for s, f in falses]
    else:
        lines.append("無し")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="リポジトリの根（ディレクトリの判定・共有の一覧・gh と git の作業場所）")
    ap.add_argument("--ndf", help="スプリントの記録の置き場（省くと <root>/.ndf）")
    ap.add_argument("--sprint", nargs="+", default=[], help="数えるスプリントの名前（表の名前）")
    ap.add_argument("--before", help="比べる前の版（git のリビジョン）")
    ap.add_argument("--out", help="結果の JSON を書くファイル")
    a = ap.parse_args(argv)
    root = str(Path(a.root).resolve())
    ndf = Path(a.ndf or Path(root) / ".ndf").resolve()
    cfg = Path(root) / ".ndf" / "supervise.json"
    shared = decl.queue_decl(json.loads(cfg.read_text(encoding="utf-8")) if cfg.exists() else {})["shared"]
    before = old_rule(a.before, root) if a.before else None
    cache: dict[int, set[str]] = {}
    rows = []
    try:
        for d in sprint_dirs(ndf):
            name = sprint_name(d, ndf)
            plans = countable(d, root)
            if len(plans) < 2 or (a.sprint and name not in a.sprint):
                continue
            changed = lambda pr: pr_files(pr, root, cache)  # noqa: E731
            row = {"sprint": name, "plans": len(plans), "after": judge(admission, shared, plans, changed)}
            if before:
                row["before"] = judge(before[0], before[1], plans, changed)
            rows.append(row)
    except RuntimeError as e:
        out = result(TOOL, "stopped", f"{e}。数えられなかったスプリント: {name}")
        code = EXIT_UNREADABLE
    else:
        bad = sum(1 for r in rows for f in r["after"]["false"] if f["cause"] != "同じファイル")
        same = [{"sprint": r["sprint"], **f} for r in rows for f in r["after"]["false"] if f["cause"] == "同じファイル"]
        metrics = {"sprints": len(rows), "false_by_dir_or_shared": bad, "false_by_same_file": same}
        summary = f"{len(rows)} スプリントを判定し直した。ディレクトリの宣言と共有の一覧による偽の重なり {bad} 組"
        out = result(TOOL, "ok" if rows else "stopped", summary if rows else "数えられるスプリントが無い", rows, metrics)
        code = 0 if rows else EXIT_PRECONDITION
        if rows:
            sys.stdout.write(table(rows, bool(before)))
    if a.out:
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False) + "\n", encoding="utf-8")
    print(out["summary"], file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
