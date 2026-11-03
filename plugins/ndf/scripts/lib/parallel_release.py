"""まとめた配布（#2436 の I9・I14・I15）: 範囲の Pull Request をレーンへ対応づける（`present`）・提示したコミットと
`develop` の先頭を比べる 1 つの関数（`moved`）・範囲で答えられる依頼を決める（`answerable`）・提示のプランと本番の
プランを作る（`build_plans`）。

配布の中身は依頼の集合ではなく、提示したコミットの範囲 `main..<SHA>` で決まる。内訳は承認する人が読むための表示である。
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path
from typing import Callable, Iterable, Optional

import parallel_plan as pp

MAX_HELPER_SEQ = 9999  # 一時レーンの名前 `<計画名>-rel<連番>` を 32 字に収める（I1）


def moved(presented: str, head: str) -> bool:
    """承認の答えの時点のベースブランチの先頭が、提示したコミットから進んでいるか（I14）。"""
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


def closes_by_pr(root: Path, prs: list[int], run: Callable = subprocess.run) -> dict[int, list[int]]:
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
    d: Path,
    first: Optional[dict] = None,
    closes_of: Callable = closes_by_pr,
) -> dict:
    """承認資料から提示したコミットと含む Pull Request を読み、レーンへ対応づける。

    書くのは提示の置き場 `d`（`releases/<連番>/<提示>/`）の決まった名前だけである: `presented.json`、承認資料へ足す
    内訳 `breakdown.md`、提示のプランの状態ディレクトリの `present/work/prs.txt`・`present/work/approved.txt`
    （本番のプランが `{queue_prs:present}`・`{queue_approved:present}` で読む）。承認資料へ内訳を足すのはプランのコマンドである。
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
    d = Path(d)
    (d / "present" / "work").mkdir(parents=True, exist_ok=True)
    (d / "presented.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (d / "breakdown.md").write_text(breakdown_md(mapped, added), encoding="utf-8")
    (d / "present" / "work" / "prs.txt").write_text(" ".join(str(n) for n in prs) + "\n", encoding="utf-8")
    (d / "present" / "work" / "approved.txt").write_text(sha + "\n", encoding="utf-8")
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
        builder = plan_parallel_release
    present_plan, release_plan = builder(main, name, d, version)
    (d / "present.json").write_text(json.dumps(present_plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (d / "release.json").write_text(json.dumps(release_plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not (d / "answer.json").exists():
        (d / "answer.json").write_text(json.dumps({"gates": []}) + "\n", encoding="utf-8")
    return d


# ---- プランの組み立て（supervise_lib の部品を使う。supervise_lib は呼ぶときに読む） ----


def present_gate_steps(state: str, repo: str | None, approval: str, prs: str, mode: str, facts_from: str, answer_from: str) -> list[dict]:
    """まとめた配布（#2436）の提示のプランの承認ゲート 2: MVV 判定（mvv・note）→ approve の順に置く。

    今の本番のプランでは MVV 判定と approve が排他だが、提示のプランは両方を並べる。MVV 判定が通せば承認の記録が
    既にあるため approve は答えの記録を読まずに通り、関門（10）なら提示のプランは承認ゲートで終わる。確定の後は
    一時レーンが答えを `answer_from` へ書いて `--from approve` で続ける。"""
    from gate_answer import GATE_2
    from supervise_lib.paths import MVV_PY, STEPS_PY
    from supervise_lib.release_templates import MVV_NOTE, material_path, mvv_gate_steps
    from supervise_lib.verify_steps import handoff_step, reject_step

    material = material_path(repo, approval)
    st = shlex.quote(str(Path(state).resolve()))
    steps = mvv_gate_steps(
        f"{MVV_PY} check --sprint {st} --gate release --material {shlex.quote(material)} --pr {prs} --mode {mode}"
        + (f" --root {shlex.quote(repo)}" if repo else "")
        + f" --note {MVV_NOTE}",
        f"sh -c 'for p in {prs}; do gh pr comment \"$p\" --body-file {MVV_NOTE} || exit 1; done'",
        "approve",
    )
    steps.append(
        {
            "id": "approve",
            "type": "run",
            "stage": "配布",
            "timeout": 120,
            **({"cwd": repo} if repo else {}),
            "cmd": f"{STEPS_PY} approve --approval {approval} --facts-from {shlex.quote(facts_from)}"
            f" --answer-from {shlex.quote(answer_from)} --by user",
            "next": "end",
            "gate_next": "end",
        }
    )
    steps.append(handoff_step(str(Path(state).resolve()), GATE_2, "判定のコメント"))
    steps.append(reject_step(str(Path(state).resolve()), GATE_2, "mvv", root=repo or "."))
    return steps


RELEASE_REQUEST_PHASE = "配布の依頼"


def plan_release_request(a, repo: str, plan_name: str, lane: str, prs: list[str] | None = None) -> dict:
    """並列の計画のレーンの本番のステージ（#2436 の F6）。本番を流さず、オーガナイザーへ配布の依頼を送る。

    ステップは `parallel-request` の 1 つで、依頼を送れば 10（承認ゲート）で終わり、自分の Pull Request が
    すでに `main` にあれば送らずに 0 で終わる（I15）。`prs` は出す版の Pull Request（`{queue_pr:<名>}` でもよい）。"""
    from supervise_lib.paths import PARALLEL_PY
    from supervise_lib.procedures import with_record

    issues = " ".join(str(n) for n in (a.issue or []))
    pr_args = " ".join(prs or [])
    cmd = (
        f"{PARALLEL_PY} request {shlex.quote(plan_name)} --lane {shlex.quote(lane)}"
        + (f" --prs {pr_args}" if pr_args else "")
        + (f" --issues {issues}" if issues else "")
        + " --state {state_dir}"
    )
    return with_record(
        {
            "フェーズ": RELEASE_REQUEST_PHASE,
            "課題": a.issue,
            "モード": a.mode,
            "作業場所": repo,
            "規則": "",
            "並列の計画": plan_name,
            "steps": [{"id": "parallel-request", "type": "run", "stage": "配布", "timeout": 300, "cmd": cmd, "next": "end"}],
        }
    )


def plan_parallel_release(main, name: str, d, version: str) -> tuple[dict, dict]:
    """まとめた配布（#2436）の 1 つの提示の (提示のプラン, 本番のプラン)。`d` は `releases/<連番>/<提示>/`。

    提示のプランは開発版の承認資料のステップ（approval-facts・承認資料の説明の欄・他のプラグインの上げ幅）を版上げ無しで
    流し、`parallel.py present` でレーンの内訳を足し、最後に承認ゲート 2（MVV 判定 → approve）を置く。本番のプランは
    承認ゲート 2 を持たず、確定した提示のプランの承認したコミット（`{queue_approved:present}`）から `release/v<版>` を切る。"""
    from supervise_lib.decl import apply_decls
    from supervise_lib.paths import PARALLEL_PY
    from supervise_lib.procedures import with_record
    from supervise_lib.release_templates import _dev_approval_steps
    from supervise_lib.sprint_waves import plan_mvv_release

    repo = str(main)
    d = Path(d)
    seq, k = d.parent.name, d.name
    ns = argparse.Namespace(
        worktree=repo,
        repo=repo,
        base=None,
        production_branch=None,
        release=None,
        issue=[],
        mode="standard",
        version=version,
        prev_tag=None,
        mvv=None,
        advise=None,
        channel="dev",
        prs=[],
        prs_from_queue=False,
        test_cmd=None,
        test_kind=None,
        test_paths=None,
        test_all=None,
        no_reports=None,
        sync_checks=None,
        format=None,
        glossary_ready=None,
        strategy=None,
        test_limits=None,
        template="sprint",
        level=None,
        series=None,
        branch=None,
    )
    apply_decls(ns)
    plugin = (ns.release or {}).get("plugin", "ndf")
    approval = f"issues/approval-{plugin}-v{version}-parallel-{seq}-{k}.md"
    state = d / "answer.json"
    present_state = d / "present"  # 提示のプランの状態ディレクトリ（`state_dir_of` の置き場と同じ）
    facts = _dev_approval_steps(ns, repo, approval, "0", ",".join((ns.release or {}).get("runtimes") or []))
    facts[-1]["next"] = "present"
    present = {
        "id": "present",
        "type": "run",
        "stage": "配布",
        "timeout": 300,
        "cwd": repo,
        "cmd": f"{PARALLEL_PY} present {shlex.quote(name)} {seq} {k} --approval {approval}"
        + f" && cat {shlex.quote(str(d / 'breakdown.md'))} >> {approval}",
        "next": "mvv",
    }
    gate = present_gate_steps(str(state), repo, approval, "$(cat {state_dir}/work/prs.txt)", "standard", str(present_state), str(state))
    present_plan = with_record(
        {
            "フェーズ": "配布（提示）",
            "課題": [],
            "モード": "standard",
            "作業場所": repo,
            "規則": "",
            "並列の計画": name,
            "steps": [*facts, present, *gate],
        }
    )
    release_plan = plan_mvv_release(ns, repo, version, "prod", prs=["{queue_prs:present}"])
    release_plan["起点"] = "{queue_approved:present}"
    release_plan["並列の計画"] = name
    return present_plan, release_plan
