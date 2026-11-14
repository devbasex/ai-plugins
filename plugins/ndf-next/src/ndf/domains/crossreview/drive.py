"""収束ループの駆動（副命令 `ndf cross-review run`）。1 回の起動でラウンドを上限まで回す。

1 ラウンドは次の順に進む:

1. 範囲を決める（`scope_decide`。設計の PR は、詳細レビューを通らない節が残る間は全差分。#3181）
2. 相手役（`ndf.core.converge.participants`）を並べて起動し、最後の 1 行の `{"findings": [...]}` を読む
3. 指摘を鍵（`oscillation.finding_key`）で重ねず台帳へ入れる。見送り済みの指摘は出し直されても扱わない
4. 未解決の指摘と、まだ決めていない足すべき内容（`--findings`。#2444）が無ければ収束。前々ラウンドの指摘が
   戻っていれば振動で止める
5. 修正担当（claude）を起動し、修正の結果ファイル（`store.fix_result_path`）の指摘ごとの「直した」「見送った（理由）」を
   台帳へ取り込む。修正のコミットを `push.push_fix` で送る（拒まれたら 1 回だけ取り込む。#2440）

状態ファイルへ rounds・reviewed_sections・台帳（findings）をラウンドごとに書き、終わったら `summary.json`
（`finish_decide` が読む形）を書く。始めに worktree の管理情報を確かめ（#3109）、origin の先頭へ合わせ（#2440）、
前の起動が残した修正の結果ファイルを取り込む（#2770）。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf.adapters import agents, claude, git
from ndf.contracts import limits
from ndf.core.converge import oscillation, participants, worktree
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview import fixgate_decide, push, scope_decide, store

# 終わったときの要約（`finish_decide` が読む）
SUMMARY_NAME = "summary.json"
# ラウンドの上限の既定
DEFAULT_MAX_ROUNDS = 3
# ラウンドの判定（指摘が無くなったラウンドは finish_decide.CONVERGED）
CHANGES_REQUIRED = fixgate_decide.CHANGES_REQUIRED
# 修正担当に許す Tool
FIXER_TOOLS = "Read,Edit,Write,Bash,Grep,Glob"

Launcher = Callable[[agents.Launch], agents.AgentStatus]


class DriveStopped(Exception):
    """駆動を始められない（同期の衝突・修正の記録の欠け）。`kind` は止まった理由の表の種類。"""

    def __init__(self, kind: str, detail: str):
        self.kind = kind
        super().__init__(detail)


@dataclass(frozen=True)
class RunConfig:
    """1 回の起動の引数。`purpose` は PR の目的（設計のプランの雛形の purpose なら節を必ず詳細レビューへ通す）。"""

    worktree: Path
    branch: str
    base: str
    pr: int
    state_dir: Path
    every_section: bool
    reviewers: tuple[str, ...]
    max_rounds: int = DEFAULT_MAX_ROUNDS
    handed: tuple[fin.Handed, ...] = ()
    fixer: str = participants.FIXER


class Outcome(NamedTuple):
    """駆動の結果。`review` は `finish_decide.finish` へ渡す形、`process` は起動ごとの過程。"""

    review: fin.Review
    summary_path: Path
    warnings: tuple[str, ...]
    process: tuple[dict[str, str], ...]


@dataclass
class _Run:
    """1 回の起動の間に重ねる値（状態ファイルの中身・台帳・注意・過程）。"""

    cfg: RunConfig
    g: git.Git
    ref: store.StateRef
    data: dict[str, Any]
    ledger: dict[str, dict[str, Any]]
    warnings: list[str]
    process: list[dict[str, str]]
    history: list[frozenset[str]]


# --- 範囲 -------------------------------------------------------------------------------


def changed_sections(g: git.Git, base: str, root: Path) -> list[str]:
    """`base...HEAD` で変わった Markdown の節（`<パス>#<見出し>`）。"""
    out: list[str] = []
    for path in g.changed_files(base, "HEAD"):
        target = root / path
        if path.endswith(".md") and target.is_file():
            out += scope_decide.sections_of(path, target.read_text(encoding="utf-8"))
    return out


def _text(value: object) -> Optional[str]:
    """空でない文字列ならそのまま、ほかは None（理由・節の無い指摘）。"""
    return value if isinstance(value, str) and value.strip() else None


def _list(data: Mapping[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    return value if isinstance(value, list) else []


# --- 始め ------------------------------------------------------------------------------


def _ingest_pending(run: _Run) -> None:
    """前の起動が残した修正の結果ファイルを、修正の記録の無い最後のラウンドへ取り込む（#2770）。"""
    rounds = _list(run.data, store.ROUNDS)
    last = store.last_round(run.data)
    pending = store.pending_fix_result(run.cfg.state_dir, run.ref)
    gate = fixgate_decide.require_fix_for_changes(len(rounds), last.get("verdict"), last.get("fix"), str(pending) if pending else None)
    if gate.kind == fixgate_decide.STOP:
        raise DriveStopped("work_stopped", str(gate.reason))
    if gate.kind == fixgate_decide.INGEST and pending is not None:
        last["fix"] = _take_fix(run, pending, len(rounds))


def _start(cfg: RunConfig) -> _Run:
    worktree.registration(cfg.worktree)
    g = git.Git(cfg.worktree)
    synced = worktree.sync_worktree(g, cfg.branch)
    if synced.conflicts:
        raise DriveStopped("precondition", worktree.conflict_detail(synced))
    ref = store.state_pr(cfg.state_dir, cfg.pr)
    data = store.read_state(ref)
    ledger = {str(f["id"]): f for f in _list(data, store.FINDINGS) if isinstance(f, dict) and "id" in f}
    run = _Run(cfg, g, ref, data, ledger, [], [], [])
    _ingest_pending(run)
    return run


def _note(run: _Run, round_no: int, who: str, result: str, warning: Optional[str] = None) -> None:
    """過程へ 1 行足す。`warning` があれば先に注意へも足す。"""
    if warning is not None:
        run.warnings.append(warning)
    run.process.append({"run": f"round {round_no} {who}", "result": result})


# --- 相手役 -----------------------------------------------------------------------------


def _review_prompt(run: _Run, round_no: int, scope: scope_decide.Scope, diff: Path) -> str:
    decided = [f["text"] for f in run.ledger.values() if f.get("outcome") == fin.SKIPPED]
    handed = [f"- {h.id}: {h.text}" for h in run.cfg.handed]
    lines = [
        f"PR #{run.ref.current_pr} のレビューの round {round_no} である。読むだけで、ファイルを変えない。",
        f"差分は {diff}（範囲: {scope.kind}{'・' + scope.base if scope.base else ''}）にある。worktree は {run.cfg.worktree}。",
    ]
    if scope.unreviewed:
        lines.append("次の節はまだ詳細レビューを通っていない。1 節ずつ読む: " + "・".join(scope.unreviewed))
    if handed:
        lines += ["組み直しが残した足すべき内容（足されたかを確かめる）:", *handed]
    if decided:
        lines += ["理由を添えて見送り済みの指摘（出し直さない）:", *(f"- {t}" for t in decided)]
    lines.append(
        '最後の行に 1 行の JSON {"findings": [{"text": "<指摘>", "section": "<パス#見出し か 空>"}]} だけを出す。指摘が無ければ空の配列。'
    )
    return "\n".join(lines)


def _reviewer_findings(status: agents.AgentStatus, out: Path) -> Optional[list[dict[str, Any]]]:
    if status.state != agents.AgentState.DONE:
        return None
    try:
        text = out.read_text(encoding="utf-8", errors="replace")
    except OSError:  # noqa: NDF401 ファイル: 出力の無い相手役は答えなかった扱い（呼び手が注意に載せる）
        return None
    return participants.findings_of(text)


def _review(run: _Run, round_no: int, prompt: str, launch: Launcher) -> tuple[list[dict[str, Any]], int]:
    """相手役を並べて起動し、指摘と、答えた相手役の数を返す。答えなかった相手役は注意と過程に載せる。"""
    cfg = run.cfg
    specs = [participants.review_launch(a, cfg.worktree, prompt, cfg.state_dir / f"round{round_no}-{a}") for a in cfg.reviewers]
    with ThreadPoolExecutor(max_workers=max(1, len(specs))) as pool:
        statuses = list(pool.map(launch, specs))
    raised: list[dict[str, Any]] = []
    answered = 0
    for spec, status in zip(specs, statuses):
        found = _reviewer_findings(status, spec.paths.out)
        if found is None:
            warning = f"round {round_no} の相手役 {spec.agent} が答えなかった（{status.state}: {status.detail}）"
            _note(run, round_no, spec.agent, f"答え無し（{status.state}）", warning)
            continue
        answered += 1
        _note(run, round_no, spec.agent, f"指摘 {len(found)} 件")
        raised += [{**f, "by": spec.agent} for f in found]
    return raised, answered


def _ledger_row(fid: str, round_no: int, text: str, by: Any, key: Optional[str] = None, section: Optional[str] = None) -> dict[str, Any]:
    """台帳の 1 行（未解決で始まる）。"""
    return {"id": fid, "key": key, "text": text, "section": section, "round": round_no, "outcome": fin.UNRESOLVED, "reason": None, "by": by}


def _next_id(run: _Run, round_no: int) -> str:
    """ラウンド `round_no` で新しく出た指摘の id（`r<ラウンド>-<そのラウンドの通し番号>`）。"""
    return f"r{round_no}-{sum(1 for f in run.ledger.values() if f.get('round') == round_no) + 1}"


def _enter(run: _Run, round_no: int, raised: Sequence[dict[str, Any]]) -> frozenset[str]:
    """指摘を台帳へ入れ、このラウンドで出た鍵を返す。見送り済みは扱わず、直した指摘が戻れば未解決へ戻す。"""
    by_key = {f.get("key"): f for f in run.ledger.values()}
    keys: set[str] = set()
    for item in raised:
        text = item.get("text")
        text = text.strip() if isinstance(text, str) else ""
        if not text:
            continue
        key = oscillation.finding_key(text)
        if key in keys:
            continue
        keys.add(key)
        known = by_key.get(key)
        if known is None:
            fid = _next_id(run, round_no)
            run.ledger[fid] = _ledger_row(fid, round_no, text, item.get("by"), key, _text(item.get("section")))
        elif known.get("outcome") == fin.FIXED:
            known |= {"outcome": fin.UNRESOLVED, "reason": None, "round": round_no}
    return frozenset(keys)


def _pending(run: _Run) -> tuple[list[dict[str, Any]], list[fin.Handed]]:
    """未解決の指摘と、まだ決めていない足すべき内容。"""
    open_ = [f for f in run.ledger.values() if f.get("outcome") == fin.UNRESOLVED]
    handed = [h for h in run.cfg.handed if h.id not in run.ledger or run.ledger[h.id].get("outcome") not in fin.DECIDED]
    return open_, handed


# --- 修正担当 ---------------------------------------------------------------------------


def _fix_prompt(run: _Run, open_: Sequence[dict[str, Any]], handed: Sequence[fin.Handed], result_path: Path) -> str:
    lines = [
        f"PR #{run.ref.current_pr} の worktree {run.cfg.worktree} で、次を 1 件ずつ直すか、理由を添えて見送る。直したらコミットする（push しない）。"
    ]
    lines += [f"- {f['id']}: {f['text']}" + (f"（{f['section']}）" if f.get("section") else "") for f in open_]
    lines += [f"- {h.id}: 足すべき内容 {h.text}" for h in handed]
    shape = (
        '{"pr": '
        + str(run.ref.origin_pr)
        + ', "commit": "<最後のコミット>", "findings": [{"id": "<上の id>", "outcome": "fixed|deferred", "reason": "<見送った理由>"}]}'
    )
    lines.append(f"終えたら {result_path} へ JSON {shape} を書く。すべての id を載せる。")
    return "\n".join(lines)


def _fixer_launch(run: _Run, round_no: int, prompt: str) -> agents.Launch:
    call = claude.ClaudeCall(system="cross-review の修正担当", permission=claude.Permission.TOOLS, tools=FIXER_TOOLS)
    paths = agents.AgentPaths.at(run.cfg.state_dir / f"round{round_no}-fix")
    return agents.Launch(
        run.cfg.fixer, (*claude.claude_args(call), prompt), run.cfg.worktree, paths, limits.seconds(participants.REVIEW_TIMEOUT)
    )


def _take_fix(run: _Run, path: Path, round_no: int) -> dict[str, Any]:
    """修正の結果を台帳へ取り込み、結果ファイルをラウンドの名前へ移す（次の起動が残りと読み違えない）。"""
    found = store.read_fix_result(path, run.ref)
    handed = {h.id: h.text for h in run.cfg.handed}
    reported = [f for f in _list(found, store.FINDINGS) if isinstance(f, dict) and f.get("outcome") in fin.DECIDED]
    for item in reported:
        fid = str(item.get("id"))
        reason = _text(item.get("reason"))
        if fid in run.ledger:
            run.ledger[fid] |= {"outcome": item["outcome"], "reason": reason}
        elif fid in handed:
            run.ledger[fid] = _ledger_row(fid, round_no, handed[fid], "rebuild") | {"outcome": item["outcome"], "reason": reason}
    os.replace(path, path.with_name(f"{path.stem}-round{round_no}.json"))
    fixed = [str(f.get("id")) for f in reported if f["outcome"] == fin.FIXED]
    return {"commit": found.get("commit"), "reported": len(reported), "fixed": fixed}


def _fix(
    run: _Run, round_no: int, open_: Sequence[dict[str, Any]], handed: Sequence[fin.Handed], launch: Launcher
) -> Optional[dict[str, Any]]:
    """修正担当を起動して結果を取り込む。答えが無ければ注意に載せて None。"""
    result_path = store.fix_result_path(run.cfg.state_dir, run.ref)
    status = launch(_fixer_launch(run, round_no, _fix_prompt(run, open_, handed, result_path)))
    if status.state != agents.AgentState.DONE or not result_path.is_file():
        warning = f"round {round_no} の修正担当が結果を書かなかった（{status.state}: {status.detail}）"
        _note(run, round_no, run.cfg.fixer, f"結果無し（{status.state}）", warning)
        return None
    try:
        fix = _take_fix(run, result_path, round_no)
    except store.FixResultMismatch as exc:
        run.warnings.append(f"round {round_no} の修正の結果を取り込めない: {exc}")
        return None
    _note(run, round_no, run.cfg.fixer, f"指摘 {fix['reported']} 件を扱った")
    return fix


def _push(run: _Run, round_no: int, reviewed_head: str, fix: dict[str, Any]) -> bool:
    """修正のコミットを送る。コミットが無ければ送らない。送れなければ、このラウンドで直したとした指摘を未解決へ戻し
    （PR に届いていない）、注意に載せて False。"""
    if run.g.rev_parse("HEAD") == reviewed_head:
        return True
    commit = fix.get("commit") if isinstance(fix.get("commit"), str) else None
    sent = push.push_fix(run.g, run.cfg.branch, commit)
    if sent.ok:
        fix["pushed"] = sent.head
        return True
    for fid in fix["fixed"]:
        if fid in run.ledger:
            run.ledger[fid] |= {"outcome": fin.UNRESOLVED, "reason": None}
    tail = f"（衝突: {', '.join(sent.conflicts)}）" if sent.conflicts else ""
    _note(run, round_no, "push", f"送れない（{sent.reason}）", f"round {round_no} の修正を送れなかった: {sent.reason}{tail}")
    return False


# --- 記録 -------------------------------------------------------------------------------


def _save(run: _Run) -> None:
    run.data[store.FINDINGS] = list(run.ledger.values())
    store.write_object(run.ref.path, run.data)


def _summary(run: _Run, final: str, rounds: int) -> Outcome:
    findings = tuple(
        fin.Finding(f["id"], f["text"] if isinstance(f.get("text"), str) else "", f["outcome"], _text(f.get("reason")))
        for f in run.ledger.values()
        if f.get("outcome") in fin.OUTCOMES
    )
    path = run.cfg.state_dir / SUMMARY_NAME
    rows = [dict(f._asdict()) for f in findings]
    store.write_object(path, {"final": final, store.ROUNDS: rounds, store.FINDINGS: rows, "pr": run.ref.origin_pr})
    return Outcome(fin.Review(final, rounds, findings), path, tuple(run.warnings), tuple(run.process))


def _round(run: _Run, round_no: int, launch: Launcher) -> Optional[str]:
    """1 ラウンドを回す。終わり方（`finish_decide` の FINALS）か、次へ進むなら None。"""
    cfg = run.cfg
    rounds = run.data.setdefault(store.ROUNDS, [])
    reviewed = scope_decide.reviewed_of(run.data)
    sections = changed_sections(run.g, cfg.base, cfg.worktree)
    scope = scope_decide.state_scope(run.data, cfg.every_section, round_no, sections)
    head = run.g.rev_parse("HEAD")
    diff = cfg.state_dir / f"round{round_no}.diff"
    diff.write_text(run.g.run("diff", f"{scope.base or cfg.base}...HEAD"), encoding="utf-8")
    raised, answered = _review(run, round_no, _review_prompt(run, round_no, scope, diff), launch)
    if answered == 0:
        return fin.ERROR
    run.history.append(_enter(run, round_no, raised))
    if scope.kind == scope_decide.FULL:
        run.data[store.REVIEWED] = sorted(reviewed | set(sections))
    record: dict[str, Any] = {"round": round_no, "head": head, "scope": scope.kind}
    rounds.append(record)
    open_, handed = _pending(run)
    if not open_ and not handed:
        record["verdict"] = fin.CONVERGED
        return fin.CONVERGED
    if oscillation.returned(run.history):
        record["verdict"] = fin.OSCILLATING
        return fin.OSCILLATING
    record["verdict"] = CHANGES_REQUIRED
    _save(run)
    fix = _fix(run, round_no, open_, handed, launch)
    if fix is None:
        return fin.ERROR
    record["fix"] = fix
    return None if _push(run, round_no, head, fix) else fin.ERROR


def drive(cfg: RunConfig, launch: Launcher = agents.run_agent) -> Outcome:
    """ラウンドを上限まで回し、要約を書いて返す。始められなければ `DriveStopped`・`worktree.WorktreeUnregistered`。"""
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    run = _start(cfg)
    final, done = fin.MAX_ROUNDS, 0
    first = len(_list(run.data, store.ROUNDS)) + 1
    for round_no in range(first, first + cfg.max_rounds):
        ended = _round(run, round_no, launch)
        done = len(_list(run.data, store.ROUNDS)) - first + 1
        _save(run)
        if ended is not None:
            final = ended
            break
    return _summary(run, final, done)
