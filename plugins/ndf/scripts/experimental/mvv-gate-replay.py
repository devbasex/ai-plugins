#!/usr/bin/env python3
"""mvv-gate-replay.py: MVV 判定の記録から設計の集合を選び、変更の前と後の判定の指示文で判定し直して数える（#2912 の F4）。

    python3 mvv-gate-replay.py run --before <版> --out <結果のファイル> [--log <mvv-gate.jsonl>] [--until <時刻>]
                                   [--root DIR] [--repo OWNER/NAME] [--select-only]

- 集合は MVV 判定の記録（`--log`、省くと mvv-gate.py の既定）の設計承認ゲートの行から、PR ごとに最後の行で選ぶ。
  期間は `--until`（既定 2026-11-10T00:00Z）より前に限る
  - 対象: 判定が `unknown` か `not_follow`・理由が人へ委ねた判断を挙げる（design-survey.py の委ねる語 `DELEGATE`）・
    boundary が空。変更の前と後の 2 回判定する
  - 対照: boundary が空でない行。変更の後の 1 回だけ判定する（レッドラインで戻ることが変わらないかを見る）
- 材料は mvv-gate.py の部品で組む（PR の題名・本文・変更したファイル・PR の先頭のコミットの設計文書）。MVV の節は
  `--root` の今の承認済みのプロジェクト MVV で、スプリント MVV は渡さない
- 変更の前の指示文は `git show <版>:plugins/ndf/scripts/mvv-gate.py` の `SYSTEM`、変更の後は今の `SYSTEM`
- 割合は「従う」で boundary が空の件数 ÷ 集合の PR の数。判定を読めない件は分母に残し、件数を別に出す
- MVV 判定の記録とスプリントの状態へは書かない（I6）。結果は `--out` のファイルにだけ書き、標準出力へ要約を出す
- `--select-only` は判定せず、選んだ集合だけを書く（費用の見積もりに使う）

結果は `lib/step_result.py` の形の 1 行の JSON。`items` は PR ごとの
`{kind: target|control, pr, recorded, before, after}`（判定は `{verdict, passed, boundary}`、読めなければ `{verdict: null, error}`）、
`metrics` は `{target: {prs, before: {passed, rate, unreadable}, after: {...}, recorded_mismatch}, control: {prs, after: {...}}, cost_usd}`。
終了コード: 0 = 数えた / 2 = 記録か版を読めない / 3 = 集合が 0 件。
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS / "lib"))
from step_result import EXIT_PRECONDITION, EXIT_UNREADABLE, emit, result  # noqa: E402

TOOL = "mvv-gate-replay"
UNTIL = "2026-11-10T00:00:00+00:00"
GATE_PATH = "plugins/ndf/scripts/mvv-gate.py"


def _load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load_script("mvv_gate_for_replay", SCRIPTS / "mvv-gate.py")
survey = _load_script("design_survey_for_replay", SCRIPTS / "design-survey.py")


def _at(s: str) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def read_gate_log(path: Path) -> list[dict]:
    """記録の行。読めない行は飛ばす（記録の 1 行の誤りで測りを止めない）。"""
    rows = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def select_replay(rows: list[dict], until: dt.datetime) -> tuple[list[dict], list[dict]]:
    """（対象, 対照）。どちらも PR ごとに最後の行（期間の中の設計承認ゲートの行）。"""
    last: dict[tuple, dict] = {}
    for r in rows:
        at = _at(r.get("at", ""))
        prs = tuple(r.get("pr") or ())
        if r.get("gate") != "design" or not prs or at is None or at >= until:
            continue
        last[prs] = r
    target, control = [], []
    for r in last.values():
        boundary = r.get("boundary") or []
        reasons = " ".join(str(x) for x in r.get("reasons") or [])
        if boundary:
            control.append(r)
        elif r.get("verdict") in ("unknown", "not_follow") and survey.DELEGATE.search(reasons):
            target.append(r)
    return target, control


def system_at(rev: str, root: Path) -> str:
    """版 `rev` の mvv-gate.py の `SYSTEM`。読めなければ ValueError。"""
    p = subprocess.run(["git", "show", f"{rev}:{GATE_PATH}"], cwd=root, capture_output=True, text=True)
    if p.returncode != 0:
        raise ValueError(f"git show {rev}:{GATE_PATH}: {p.stderr.strip()[:200]}")
    for node in ast.parse(p.stdout).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SYSTEM" for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError(f"{rev} の mvv-gate.py に SYSTEM が無い")


def pr_materials(prs: list[int], repo: str | None, cwd: Path) -> list[str]:
    """mvv-gate.py と同じ材料（PR ごとの事実と設計文書）。取れなければ gate.Back。"""
    out = []
    for n in prs:
        info = gate.pr_facts(n, repo, cwd)
        out.append(gate.pr_material(n, info))
        out += [gate.design_material(n, p, info, repo, cwd) for p in gate.design_docs(info)]
    return out


def judge_once(system: str, prompt: str) -> tuple[dict, float]:
    """指示文 `system` で 1 回判定する。MVV 判定の記録へは書かない。"""
    gate.SYSTEM = system
    verdict, raw, usage = gate.ask(prompt)
    cost = float(usage.get("cost_usd") or 0)
    if verdict is None:
        return {"verdict": None, "error": raw[-200:]}, cost
    passed = verdict["verdict"] == "follow" and not verdict["boundary"]
    return {"verdict": verdict["verdict"], "passed": passed, "boundary": verdict["boundary"]}, cost


def tally_verdicts(judged: list[dict], prs: int) -> dict:
    passed = sum(1 for j in judged if j.get("passed"))
    return {
        "passed": passed,
        "rate": round(passed / prs, 4) if prs else 0.0,
        "unreadable": sum(1 for j in judged if j.get("verdict") is None),
    }


def write_out(path: str, obj: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False) + "\n", encoding="utf-8")


def _replay_item(kind: str, r: dict) -> dict:
    return {"kind": kind, "pr": list(r.get("pr") or []), "recorded": r.get("verdict"), "at": r.get("at")}


def cmd_run(a):
    root = Path(a.root or ".").resolve()
    until = _at(a.until)
    if until is None:
        emit(result(TOOL, "stopped", f"--until を時刻として読めない: {a.until}"), EXIT_UNREADABLE)
    log = Path(a.log or gate.pm.gate_log_path())
    try:
        rows = read_gate_log(log)
        before = system_at(a.before, root)
    except (OSError, ValueError) as e:
        emit(result(TOOL, "stopped", f"記録か変更の前の指示文を読めない: {e}"), EXIT_UNREADABLE)
    target, control = select_replay(rows, until)
    if not target and not control:
        emit(result(TOOL, "stopped", f"{log} に対象も対照も無い（{a.until} より前の設計承認ゲート）"), EXIT_PRECONDITION)
    items = [_replay_item("target", r) for r in target] + [_replay_item("control", r) for r in control]
    metrics: dict = {"target": {"prs": len(target)}, "control": {"prs": len(control)}, "cost_usd": 0.0}
    if a.select_only:
        res = result(TOOL, "ok", f"対象 {len(target)} 件・対照 {len(control)} 件を選んだ（判定しない）", items, metrics)
        write_out(a.out, res)
        emit(res)
    after = gate.SYSTEM
    project = gate.pm.load_mvv(root)
    cost = 0.0
    for it in items:
        try:
            prompt = gate.build_prompt(project, None, "design", pr_materials(it["pr"], a.repo, root))
        except gate.Back as e:
            it["error"] = str(e)[:200]
            it["after"] = {"verdict": None, "error": it["error"]}
            if it["kind"] == "target":
                it["before"] = dict(it["after"])
            continue
        if it["kind"] == "target":
            it["before"], c = judge_once(before, prompt)
            cost += c
        it["after"], c = judge_once(after, prompt)
        cost += c
    gate.SYSTEM = after
    t_items = [it for it in items if it["kind"] == "target"]
    c_items = [it for it in items if it["kind"] == "control"]
    metrics["target"].update(
        before=tally_verdicts([it["before"] for it in t_items], len(t_items)),
        after=tally_verdicts([it["after"] for it in t_items], len(t_items)),
        recorded_mismatch=sum(1 for it in t_items if it["before"].get("verdict") not in (None, it["recorded"])),
    )
    metrics["control"]["after"] = tally_verdicts([it["after"] for it in c_items], len(c_items))
    metrics["cost_usd"] = round(cost, 4)
    tb, ta = metrics["target"]["before"], metrics["target"]["after"]
    summary = (
        f"対象 {len(t_items)} 件: 従う {tb['passed']} → {ta['passed']} 件（{tb['rate']:.0%} → {ta['rate']:.0%}）。"
        f"対照 {len(c_items)} 件: 変更の後に従うで省けた {metrics['control']['after']['passed']} 件。費用 {cost:.2f} USD"
    )
    res = result(TOOL, "ok", summary, items, metrics)
    write_out(a.out, res)
    emit(res)


def build_parser():
    ap = argparse.ArgumentParser(prog="mvv-gate-replay.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="集合を選び、変更の前と後の指示文で判定し直して数える")
    p.add_argument("--before", required=True, help="変更の前の mvv-gate.py を読む版（git のコミットか参照）")
    p.add_argument("--out", required=True, help="結果を書くファイル")
    p.add_argument("--log", default="", help="MVV 判定の記録（省くと mvv-gate.py の既定）")
    p.add_argument("--until", default=UNTIL, help="この時刻より前の記録だけを使う（ISO 8601）")
    p.add_argument("--root", default="", help="リポジトリの根（プロジェクト MVV と git show に使う）")
    p.add_argument("--repo", default=None, help="PR のリポジトリ（OWNER/NAME）")
    p.add_argument("--select-only", action="store_true", help="判定せず、選んだ集合だけを書く")
    p.set_defaults(func=cmd_run)
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
