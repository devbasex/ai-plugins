"""1 スプリントのレーンの設計承認の依頼（#3603）: 区分 `ndf-gate:1` の問いから設計承認の依頼の中身を組み、その答えをスプリント状態へ書く。

- `design_gate`: `parallel.py request-ask` の送り分け（`parallel_ask.route_ask`）が呼ぶ。問いの最初の問いの文の承認の行（`承認: #<番号>`）がちょうど 1 行で、その番号が
  レーンのスプリントで承認ゲートで終わった設計のプランの設計 PR で、PR が開いていて先頭を読めたときだけ中身を返す（I1・決定 2・3）。
  ほかは None（今どおり `kind: ask` で送る）
- `answer_design`: `parallel.py design-answer` が呼ぶ。起き直したレーンが、答えを読み、利用者が設計承認ゲートで答えたときと同じ記録を
  書き、続きを返す（決定 4）

依頼の id と中身は開発ループのレーンと同じ `parallel_lane.request_gate1` で組む（I2）。開発ループのレーンの経路（`lane_gate1`）は通らない（I3）。
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import design_pr_merge
import gate1_requests as g1

GATE1_SOURCE = "ndf-gate:1"  # 設計承認の区分
RETURNED_SUFFIX = "-r"  # MVV 判定で承認した後に開いたまま問い直された依頼の id の接尾辞の頭（I4）
REVISE_NOTE = "design-revise-{pr}.md"  # 直しの指摘ファイルの名前（プランの状態ディレクトリの work/ に置く）
REJECTED_MARK = "design-rejected-{pr}.json"  # 差し戻しを記録した印（同じ依頼で 2 回打っても 1 件にする）
APPROVAL_PREFIX = "承認: #"  # 承認の行の頭
DESIGN_STAGE = "設計"
SPRINT_STATE = Path(__file__).resolve().parents[1] / "sprint-state.py"
SUPERVISE = Path(__file__).resolve().parents[1] / "supervise.py"


@dataclass(frozen=True)
class DesignGate:
    """設計承認の依頼の中身（`request_gate1` へ渡す）。`state` はスプリント状態のパス。"""

    issue: int
    pr: int
    stamp: str
    report: str
    state: str
    verdict: str
    boundary: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    suffix: str = ""
    returned: bool = False


def approval_prs(tool_input: dict) -> list[int]:
    """最初の問いの文の承認の行の番号を、行ごとに出現順で返す。問いの文のほかの場所と選択肢の PR の URL・`#<番号>` は読まない。"""
    from parallel_question import first_question

    out = []
    for line in first_question(tool_input).splitlines():
        s = line.strip()
        if s.startswith(APPROVAL_PREFIX) and re.fullmatch(r"\d+", s[len(APPROVAL_PREFIX) :].strip()):
            out += [n for n in g1.pr_numbers(s[len(APPROVAL_PREFIX) :].strip()) if n > 0]
    return out


def _plans_of(sprint: Path, stage_name: str) -> list[Path]:
    try:
        stages = json.loads((Path(sprint) / "sprint.json").read_text(encoding="utf-8")).get("ステージ") or []
    except (OSError, ValueError, AttributeError):
        return []
    out = []
    for stage in stages:
        if isinstance(stage, dict) and stage.get("name") == stage_name:
            out += [Path(p) if Path(p).is_absolute() else Path(sprint) / p for p in stage.get("plans") or []]
    return out


def _issue_of(plan: Path) -> int | None:
    try:
        got = json.loads(plan.read_text(encoding="utf-8")).get("課題") or []
        return int(got[0]) if got else None
    except (OSError, ValueError, AttributeError, TypeError, IndexError):
        return None


def gated_design_prs(sprint: Path) -> dict[int, dict]:
    """スプリントの設計のステージのプランのうち、報告の結果が承認ゲート（`関門`）のものの設計 PR の番号から
    `{"report": <報告のパス>, "issue": <課題>, "plan": <プランのパス>}` を引く表。報告を読めない・PR の番号の無いプランは入らない。"""
    from supervise_lib.paths import report_result, state_dir_of

    out = {}
    for plan in _plans_of(sprint, DESIGN_STAGE):
        report = state_dir_of(str(plan)) / "report.md"
        try:
            if report_result(report.read_text(encoding="utf-8")) != "関門":
                continue
        except OSError:
            continue
        pr = g1.report_pr({"report": str(report)})
        if pr is not None:
            out[pr] = {"report": str(report), "issue": _issue_of(plan), "plan": str(plan)}
    return out


def sprint_state_of(sprint: Path) -> str:
    """スプリント状態のパス（`sprint.json` の `状態`、無ければスプリントの `state.json`）。読めなければ空。"""
    try:
        got = json.loads((Path(sprint) / "sprint.json").read_text(encoding="utf-8")).get("状態")
    except (OSError, ValueError, AttributeError):
        got = None
    if got:
        return str(got)
    p = Path(sprint) / "state.json"
    return str(p) if p.is_file() else ""


def _mvv_approved(rows: list[dict], rid: str) -> dict | None:
    """依頼 `rid` への MVV 判定の承認の答えの行の中身。無ければ None。"""
    for r in rows:
        msg = r.get("msg") or {}
        if r.get("kind") == "answer" and r.get("id") == rid and msg.get("result") == "approved" and msg.get("by") == "mvv":
            return msg
    return None


def next_gate1_id(rows: list[dict], base: str) -> tuple[str, bool, str]:
    """同じ先頭の `gate1` の行の並びから (今の問いに当てる id, 印 `returned` の要否, マージの結果の理由)。
    `base` か `base-r<n>` の最後の行に MVV 判定の承認の答えがあれば `base-r<n+1>`（I4）。ほかは最後の行の id（無ければ `base`）。"""
    pat = re.compile(re.escape(base) + rf"(?:{re.escape(RETURNED_SUFFIX)}(\d+))?")
    last, n = None, 0
    for r in rows:
        m = pat.fullmatch(str(r.get("id") or "")) if r.get("kind") == "gate1" else None
        if m:
            last, n = r.get("id"), int(m.group(1) or 0)
    if last is None:
        return base, False, ""
    got = _mvv_approved(rows, last)
    if got is None:
        return str(last), last != base, ""
    return f"{base}{RETURNED_SUFFIX}{n + 1}", True, str((got.get("merge") or {}).get("reason") or "")


def pr_state_of(root: Path, pr: int, run: Callable[..., tuple[int, str, str]] = design_pr_merge.run_cmd) -> dict | None:
    """設計 PR の状態と先頭（`gh pr view --json state,headRefOid`）。読めなければ None。"""
    code, so, _ = run(["gh", "pr", "view", str(pr), "--json", "state,headRefOid"], cwd=str(root))
    try:
        got = json.loads(so) if code == 0 else None
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def design_gate(
    main: Path, name: str, lane: str, tool_input: dict, rows: list[dict], view: Callable[[Path, int], dict | None] = pr_state_of
) -> DesignGate | None:
    """I1 を満たす問いなら設計承認の依頼の中身、満たさなければ None。送り手は計画 `name` の 1 スプリントのレーン `lane`。"""
    import parallel_answer as pa
    import parallel_hold as ph
    import parallel_hold_sprint as phs

    prs = approval_prs(tool_input)
    if len(prs) != 1:
        return None
    pr = prs[0]
    repo = pa.lane_repo(main, name, lane)
    sprint = phs.find_sprint(repo, (ph.lane_input(main, name, lane) or {}).get("issues") or [])
    hit = gated_design_prs(sprint).get(pr) if sprint is not None else None
    if hit is None or hit.get("issue") is None:
        return None
    got = view(repo, pr) or {}
    stamp = str(got.get("headRefOid") or "")
    if got.get("state") != "OPEN" or not re.fullmatch(r"[0-9a-f]{40}", stamp):
        return None  # 開いていない・先頭を読めない設計 PR へ承認を当てない（決定 3）
    state = sprint_state_of(sprint)
    row = g1.last_verdict(state, "design", pr) if state else {}
    base = f"{lane}-g{pr}-{stamp[: g1.HEAD_LEN]}"
    rid, returned, why = next_gate1_id(rows, base)
    reasons = [x for x in ("／".join(row.get("reasons") or []) or row.get("verdict") or "判定を読めない", why) if x]
    return DesignGate(
        issue=int(hit["issue"]), pr=pr, stamp=stamp, report=hit["report"], state=state, verdict=row.get("verdict") or "unknown",
        boundary=list(row.get("boundary") or []), reasons=reasons, suffix=rid[len(base) :], returned=returned,
    )  # fmt: skip


# ---- 答え（parallel.py design-answer） ----


def _gate1_row(rows: list[dict], rid: str) -> dict | None:
    return next((r for r in rows if r.get("kind") == "gate1" and r.get("id") == rid), None)


def _answer_row(rows: list[dict], rid: str) -> dict | None:
    return next((r.get("msg") or {} for r in rows if r.get("kind") == "answer" and r.get("id") == rid), None)


def _merge_resume(sprint: Path | None) -> str:
    """設計のステージの後で最初に `resume` を持つステージ（設計 PR のマージのステージ）の `resume`。無ければ設計のステージの `resume`。
    読めなければ空。"""
    if sprint is None:
        return ""
    try:
        stages = json.loads((Path(sprint) / "sprint.json").read_text(encoding="utf-8")).get("ステージ") or []
    except (OSError, ValueError, AttributeError):
        return ""
    stages = [s for s in stages if isinstance(s, dict)]
    at = next((i for i, s in enumerate(stages) if s.get("name") == DESIGN_STAGE), None)
    if at is None:
        return ""
    later = next((s.get("resume") for s in stages[at + 1 :] if s.get("resume")), None)
    return str(later or stages[at].get("resume") or "")


def _has_gate(state: Path, pr: int, by: str, outcome: str) -> bool:
    import gate_answer

    return any(
        gate_answer.canonical(g.get("name")) == gate_answer.DESIGN_GATE and g.get("pr") == pr and g.get("by") == by
        and g.get("outcome") == outcome
        for g in gate_answer.gates_of(state)
    )  # fmt: skip


def _write_gate(run, state: Path, pr: int, by: str, outcome: str, what: str, repo: Path, msg: dict) -> str | None:
    """`sprint-state.py gate` で設計承認ゲートの記録を書く。書けなければ子の要約を返す。"""
    import gate_answer
    import project_mvv as pm

    cmd = [sys.executable, str(SPRINT_STATE), "gate", str(state), gate_answer.DESIGN_GATE, "--what", what, "--pr", str(pr)]
    cmd += ["--by", by, "--outcome", outcome]
    if by == "mvv":
        row = g1.last_verdict(str(state), "design", pr)
        reasons = row.get("reasons") if row else msg.get("reasons")
        cmd += [
            "--verdict",
            str(row.get("verdict") or msg.get("verdict") or "unknown"),
            "--reasons",
            json.dumps(list(reasons or []), ensure_ascii=False),
        ]
        cmd += ["--log", str(pm.gate_log_path())]
    else:
        cmd += ["--root", str(repo)]
    code, so, se = run(cmd, cwd=str(repo))
    if code == 0:
        return None
    import step_read

    got = step_read.last_json(so) or {}
    return str(got.get("summary") or (so + se).strip()[-300:] or f"終了コード {code}")


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _plan_work(msg: dict) -> Path:
    """依頼の報告（設計のプランの状態ディレクトリの report.md）の隣の work/。"""
    return Path(str(msg.get("report") or "")).parent / "work"


def _revise_step(sprint: Path | None, pr: int) -> str:
    """直した後の続き: 設計のプランが `reject` のステップを持てば `run <プラン> --from reject`。"""
    hit = gated_design_prs(sprint).get(pr) if sprint else None
    if hit:
        try:
            steps = json.loads(Path(hit["plan"]).read_text(encoding="utf-8")).get("steps") or []
        except (OSError, ValueError, AttributeError):
            steps = []
        if any(isinstance(s, dict) and s.get("id") == "reject" for s in steps):
            return f"python3 {SUPERVISE} run {hit['plan']} --from reject"
    return "設計のプランを直して流し直す"


def answer_design(
    main: Path, name: str, rid: str, state: Path, run: Callable[..., tuple[int, str, str]] = design_pr_merge.run_cmd
) -> tuple[str, str, int, dict]:
    """設計承認の依頼 `rid` の答えを読み、答えの種類ごとの記録を書いて (状態, 要約, 終了コード, metrics) を返す。"""
    import jsonio
    import parallel_answer as pa
    import parallel_hold as ph
    import parallel_hold_sprint as phs
    import parallel_ledger as plg

    try:
        rows = plg.rows_since_opened(plg.read_ledger_rows(main, name).rows)
    except OSError as exc:
        return "stopped", f"計画の台帳を読めない: {exc}", 2, {}
    row = _gate1_row(rows, rid)
    if row is None:
        return "stopped", f"依頼 {rid} は設計承認の依頼（gate1）でない", 1, {}
    msg = row.get("msg") or {}
    pr, lane = int(msg.get("pr") or 0), str(msg.get("lane") or "")
    ans = _answer_row(rows, rid)
    if ans is None:
        return "stopped", f"依頼 {rid} の答えがまだ無い（耐久ワークフローが答えの後に起こす）", 3, {"pr": pr}
    result, by = ans.get("result"), str(ans.get("by") or "user")
    metrics = {"result": result, "by": by, "merge": ans.get("merge"), "pr": pr}
    if result not in ("approved", "revise", "rejected"):
        return "stopped", f"依頼 {rid} の答え（{result}）は承認・直し・差し戻しのどれでもない", 1, metrics
    try:
        jsonio.read(state, want=dict)
    except (OSError, jsonio.JsonReadError) as exc:
        return "stopped", f"スプリント状態を読めない: {exc}", 2, metrics
    repo = pa.lane_repo(main, name, lane)
    sprint = phs.find_sprint(repo, (ph.lane_input(main, name, lane) or {}).get("issues") or [])  # design_gate と同じ探し方
    what = f"設計 PR #{pr} の承認（並列の計画 {name} の依頼 {rid}）"
    if result == "approved":
        if not _has_gate(state, pr, by, "approved") and (err := _write_gate(run, state, pr, by, "approved", what, repo, msg)):
            return "stopped", f"設計承認ゲートの記録を書けない: {err}", 2, metrics
        nxt = _merge_resume(sprint)
        return "ok", f"設計 PR #{pr} の承認（{by}）を記録した。続きを流す", 0, {**metrics, "next": nxt}
    work = _plan_work(msg)
    if result == "revise":
        note = work / REVISE_NOTE.format(pr=pr)
        try:
            _write_atomic(note, str(ans.get("note") or ""))
        except OSError as exc:
            return "stopped", f"指摘ファイルを書けない: {exc}", 2, metrics
        nxt = f"指摘 {note} を読んで設計を直し、{_revise_step(sprint, pr)}"
        return "gate", f"設計 PR #{pr} は直して出し直す（指摘 {note}）", 10, {**metrics, "note": str(note), "next": nxt}
    mark = work / REJECTED_MARK.format(pr=pr)
    if jsonio.read(mark, missing={}, broken={}, want=dict).get("id") != rid:
        if err := _write_gate(run, state, pr, "user", "rejected", what, repo, msg):
            return "stopped", f"設計承認ゲートの差し戻しを書けない: {err}", 2, metrics
        try:
            _write_atomic(mark, json.dumps({"id": rid, "pr": pr}, ensure_ascii=False) + "\n")
        except OSError as exc:
            return "stopped", f"差し戻しの印を書けない: {exc}", 2, metrics
    return "gate", f"設計 PR #{pr} は差し戻された（課題 #{msg.get('issue')} を進めない）", 10, {**metrics, "next": "課題を進めない"}
