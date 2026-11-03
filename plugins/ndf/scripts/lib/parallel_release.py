"""まとめた配布（#2436 の I9・I14・I15）: 範囲の Pull Request をレーンへ対応づける（`present`）・提示したコミットと
`develop` の先頭を比べる 1 つの関数（`moved`）・範囲で答えられる依頼を決める（`answerable`）・提示のプランと本番の
プランを作る（`build_plans`）。

配布の中身は依頼の集合ではなく、提示したコミットの範囲 `main..<SHA>` で決まる。内訳は承認する人が読むための表示である。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable, Iterable, Optional

import parallel_plan as pp

MAX_HELPER_SEQ = 9999  # 一時レーンの名前 `<計画名>-rel<連番>` を 32 字に収める（I1）


def moved(presented: str, head: str) -> bool:
    """承認の答えの時点の `origin/develop` の先頭が、提示したコミットから進んでいるか（I14）。"""
    return bool(head) and head.strip() != presented.strip()


def map_lanes(plan: pp.ParallelPlan, prs: list[int], closes: dict[int, list[int]], requests: Iterable[dict]) -> dict:
    """範囲の Pull Request をレーンへ対応づける。

    依頼のあるレーンは依頼の `prs` が範囲にあるかで決め（依頼済みの印）、依頼の無いレーンは範囲の Pull Request が閉じる
    課題がスプリントのレーンの `input.issues` にあるかで決める。どちらにも当たらないものは `orphans` に並べる。"""
    in_range = set(prs)
    lanes: dict[str, dict] = {}
    claimed: set[int] = set()
    for req in requests:
        hit = sorted(set(req.get("prs") or []) & in_range)
        if hit:
            row = lanes.setdefault(req["lane"], {"prs": [], "requested": True, "requests": []})
            row["requested"] = True
            row["prs"] = sorted(set(row["prs"]) | set(hit))
            row["requests"].append(req["id"])
            claimed |= set(hit)
    for ln in plan.lanes:
        if not isinstance(ln.input, pp.SprintInput):
            continue
        own = sorted(n for n in prs if n not in claimed and set(closes.get(n) or []) & set(ln.input.issues))
        if own:
            row = lanes.setdefault(ln.name, {"prs": [], "requested": False, "requests": []})
            row["prs"] = sorted(set(row["prs"]) | set(own))
            claimed |= set(own)
    return {"lanes": lanes, "orphans": sorted(in_range - claimed)}


def answerable(pending: Iterable[dict], prs: Iterable[int]) -> list[str]:
    """範囲 `prs` に Pull Request が入る依頼の ID（その配布の答えを受ける。I9・I15）。"""
    got = set(prs)
    return [r["id"] for r in pending if set(r.get("prs") or []) & got]


def breakdown_md(mapped: dict, added: Optional[dict] = None) -> str:
    """承認資料へ足すレーンの内訳の節。出し直しなら加わった範囲を載せる。"""
    lines = ["", "## レーンの内訳", "", "| レーン | 依頼 | Pull Request |", "| --- | --- | --- |"]
    for name, row in sorted(mapped["lanes"].items()):
        lines.append(f"| {name} | {'あり' if row['requested'] else 'なし'} | {', '.join(f'#{n}' for n in row['prs'])} |")
    if mapped["orphans"]:
        lines.append(f"| （レーンの無い Pull Request） | - | {', '.join(f'#{n}' for n in mapped['orphans'])} |")
    if added:
        lines += ["", "### 出し直しで加わった範囲", "", "| レーン | Pull Request |", "| --- | --- |"]
        for name, row in sorted(added["lanes"].items()):
            lines.append(f"| {name} | {', '.join(f'#{n}' for n in row['prs'])} |")
        if added["orphans"]:
            lines.append(f"| （レーンの無い Pull Request） | {', '.join(f'#{n}' for n in added['orphans'])} |")
    return "\n".join(lines) + "\n"


def closing_issues(root: Path, prs: list[int], run: Callable = subprocess.run) -> dict[int, list[int]]:
    """Pull Request が閉じる課題（`closingIssuesReferences`）。読めないものは空にする。"""
    out: dict[int, list[int]] = {}
    for n in prs:
        got = run(
            ["gh", "pr", "view", str(n), "--json", "closingIssuesReferences", "-q", "[.closingIssuesReferences[].number]"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        try:
            out[n] = [int(x) for x in json.loads(got.stdout or "[]")] if got.returncode == 0 else []
        except ValueError:
            out[n] = []
    return out


def present(
    root: Path,
    plan: pp.ParallelPlan,
    approval: Path,
    requests: list[dict],
    out_prs: Path,
    presented_json: Path,
    first: Optional[dict] = None,
    closes_of: Callable = closing_issues,
) -> dict:
    """承認資料から提示したコミットと含む Pull Request を読み、レーンへ対応づけて `presented.json` と `--out` を書く。

    `first` は出し直しのときの提示 1 の `presented.json`（加わった範囲を載せる）。承認資料に承認したコミットが無ければ ValueError。"""
    import approved_commit
    from release_lib import included

    sha = approved_commit.material_sha(approval)
    prs = included.read_included(approval)
    closes = closes_of(root, prs)
    mapped = map_lanes(plan, prs, closes, requests)
    added = None
    if first:
        extra = [n for n in prs if n not in set(first.get("prs") or [])]
        added = map_lanes(plan, extra, closes, requests)
    data = {"sha": sha, "prs": prs, "lanes": mapped["lanes"], "orphans": mapped["orphans"], "added": added}
    Path(presented_json).parent.mkdir(parents=True, exist_ok=True)
    Path(presented_json).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(out_prs).parent.mkdir(parents=True, exist_ok=True)
    Path(out_prs).write_text(" ".join(str(n) for n in prs) + "\n", encoding="utf-8")
    with open(approval, "a", encoding="utf-8") as f:
        f.write(breakdown_md(mapped, added))
    return data


def release_dir(main: Path, name: str, seq: int, k: int) -> Path:
    return pp.plan_dir(main, name) / "releases" / str(seq) / str(k)


def helper_name(name: str, seq: int) -> str:
    """一時レーンの名前（`<計画名>-rel<連番>`）。"""
    return f"{name}-rel{seq}"


def pipeline_cmd(supervise: str, d: Path) -> str:
    """一時レーンが流す 1 行（提示のプラン → 本番のプラン）。"""
    return f"{supervise} queue {d / 'present.json'} --then {d / 'release.json'} --done {d / 'done.json'}"


def build_plans(main: Path, name: str, seq: int, k: int, version: str, builder: Optional[Callable] = None) -> Path:
    """`releases/<連番>/<提示>/` に提示のプランと本番のプランと答えの記録（`{"gates": []}`）を書き、置き場を返す。

    `builder(d, version)` が (提示のプラン, 本番のプラン) を返す。省けば `supervise_lib.parallel_plans` を使う。"""
    d = release_dir(main, name, seq, k)
    d.mkdir(parents=True, exist_ok=True)
    if builder is None:
        from supervise_lib.sprint_waves import plan_parallel_release as builder  # type: ignore[no-redef]
    present_plan, release_plan = builder(main, name, d, version)
    (d / "present.json").write_text(json.dumps(present_plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (d / "release.json").write_text(json.dumps(release_plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    answer = d / "answer.json"
    if not answer.exists():
        answer.write_text(json.dumps({"gates": []}) + "\n", encoding="utf-8")
    return d
