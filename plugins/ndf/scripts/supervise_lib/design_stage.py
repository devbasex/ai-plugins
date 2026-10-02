"""`supervise.py design-results`: 設計の結果を読み、実装のステージのプランを書き換える（#1485 の決定 1〜3）。

承認ゲート 1 の直後のステージが打つ。設計の課題ごとに `design/issue-<番号>` のマージした PR を探し、その PR が
変えた `issues/*.md` を `origin/<ベースブランチ>` から読んで「設計の結果」の表を集める。実装する課題のプランへ
`触るファイル` を、取り込んだ課題のプランへ `実行の条件` を書き、取り込み先のプランの `課題` と実装の指示文へ
取り込んだ課題を足す。同じ入力に対しては同じ内容を書く（打ち直しても `課題` を重ねない）。"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

import design_results as dr
import gh_call
from step_result import result
from supervise_lib.procedures import DESIGN_RESULTS, absorbed_condition, with_touched
from supervise_lib.templates import impl_prompt

TOOL = "supervise-design-results"
IMPL_STAGE = "実装"


def merged_design_pr(root: str, base: str, n: int) -> int | None:
    """`design/issue-<n>` から base へマージした PR の番号。無ければ None。"""
    p = gh_call.gh(
        ["pr", "list", "--head", f"design/issue-{n}", "--base", base, "--state", "merged", "--json", "number", "--jq", ".[0].number"],
        cwd=root,
    )
    text = p.stdout.strip() if p.returncode == 0 else ""
    return int(text) if text.isdigit() else None


def changed_design_docs(root: str, base: str, pr: int) -> list[str]:
    """PR が変えた `issues/*.md` の、`origin/<base>` での中身。"""
    p = gh_call.gh(["pr", "view", str(pr), "--json", "files", "--jq", ".files[].path"], cwd=root)
    paths = [f for f in p.stdout.split() if re.fullmatch(r"issues/[^/]+\.md", f)] if p.returncode == 0 else []
    subprocess.run(["git", "-C", root, "fetch", "-q", "origin", base], capture_output=True, text=True)
    out = []
    for f in paths:
        show = subprocess.run(["git", "-C", root, "show", f"origin/{base}:{f}"], capture_output=True, text=True)
        if show.returncode == 0:
            out.append(show.stdout)
    return out


def read_results(root: str, base: str, design: list[int]) -> dr.DesignResults:
    """設計の課題ごとに設計の結果を読んで合わせる。読めなかった課題は `unread` へ入れる。"""
    res = dr.DesignResults()
    for n in design:
        pr = merged_design_pr(root, base, n)
        rows = None
        if pr is not None:
            res.design_prs.append(pr)
            found = [r for r in (dr.parse_results(t) for t in changed_design_docs(root, base, pr)) if r is not None]
            rows = [row for rs in found for row in rs] if found else None
        if rows is None:
            res.unread.append(n)
        else:
            res.add(rows)
    return res


def impl_plans(manifest: Path) -> dict[int, Path]:
    """manifest の実装のステージのプラン（課題の番号 → パス）。ファイル名は `<番号>-impl-<課題>.json`。"""
    data = json.loads(manifest.read_text(encoding="utf-8"))
    out = {}
    for entry in data.get("ステージ") or []:
        if entry.get("name") != IMPL_STAGE:
            continue
        for p in entry.get("plans") or []:
            m = re.search(r"-impl-(\d+)\.json$", p)
            if m:
                out[int(m.group(1))] = Path(p)
    return out


def rewrite(plans: dict[int, dict], res: dr.DesignResults) -> list[dict]:
    """プランへ設計の結果を書く。取り込み先が無ければ DesignResultsError。書いた内容の一覧を返す。"""
    implemented = {r.issue for r in res.rows if r.kind == dr.IMPLEMENT}
    absorbed: dict[int, list[int]] = {}
    for r in res.rows:
        if r.kind != dr.ABSORB:
            continue
        if r.host not in plans and r.host not in implemented:
            raise dr.DesignResultsError(f"#{r.issue} の取り込み先 #{r.host} がスプリントの課題にも設計の結果の実装する行にも無い")
        absorbed.setdefault(r.host, []).append(r.issue)
    items = []
    for n, plan in plans.items():
        host = res.host_of(n)
        if host is not None and host in plans:
            plan["実行の条件"] = absorbed_condition(n, host)
            items.append({"issue": n, "absorbed_into": host})
            continue
        with_touched(plan, res.files_of(n))
        issues = [n] + [i for i in absorbed.get(n, []) if i != n]
        plan["課題"] = issues
        for step in plan.get("steps", []):
            if step.get("id") == "impl":
                ns = argparse.Namespace(issue=issues, prompt=None, prompt_file=None, files=plan.get("触るファイル"))
                step["prompt"] = impl_prompt(ns)
        items.append({"issue": n, "issues": issues, "files": plan.get("触るファイル", [])})
    return items


def cmd_design_results(a, read=read_results) -> tuple[dict, int | None]:
    manifest = Path(a.manifest)
    try:
        paths = impl_plans(manifest)
        plans = {n: json.loads(p.read_text(encoding="utf-8")) for n, p in paths.items()}
    except (OSError, ValueError) as e:
        return result(TOOL, "stopped", f"manifest か実装のプランを読めない: {e}"), 2
    try:
        res = read(a.root, a.base, list(a.design))
        items = rewrite(plans, res) if res.rows else []
    except dr.DesignResultsError as e:
        return result(TOOL, "stopped", f"設計の結果を書けない: {e}。人へ戻す"), 2
    for n, plan in plans.items():
        paths[n].write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out = manifest.parent / DESIGN_RESULTS
    out.write_text(json.dumps(res.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = f"設計の結果を {len(res.rows)} 行読み、実装のプランを {len(items)} 本書いた: {out}"
    if res.unread:
        summary += "。設計の結果を読めなかった設計の課題: " + " ".join(f"#{n}" for n in res.unread) + "（今の振る舞いで流す）"
    return result(TOOL, "ok", summary, items, {"rows": len(res.rows), "unread": res.unread, "design_prs": res.design_prs}), 0
