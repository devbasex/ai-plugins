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
前の起動が残した修正の結果ファイルを取り込む（#2770）。修正の結果は状態ファイルへ書いてから結果ファイルを
ラウンドの名前へ移し、送る（push が例外で止まっても、次の起動が記録から再開できる）。前の起動が送れなかった修正の
コミットが残っていれば、始めに送ってから進む。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
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
    ledger: FindingLedger
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


# --- 台帳 -------------------------------------------------------------------------------


def _ledger_row(fid: str, round_no: int, text: str, by: Any, key: Optional[str] = None, section: Optional[str] = None) -> dict[str, Any]:
    """台帳の 1 行（未解決で始まる）。"""
    return {"id": fid, "key": key, "text": text, "section": section, "round": round_no, "outcome": fin.UNRESOLVED, "reason": None, "by": by}


class FindingLedger:
    """指摘の台帳（id から行の辞書。状態ファイルの findings に並べる形のまま持つ）と、その規則。

    指摘は鍵で重ねず、見送り済みの指摘は出し直されても扱わず、直した指摘が戻れば未解決へ戻す。
    """

    def __init__(self, rows: Sequence[Any] = ()):
        self.rows: dict[str, dict[str, Any]] = {str(f["id"]): f for f in rows if isinstance(f, dict) and "id" in f}

    def _next_id(self, round_no: int) -> str:
        """ラウンド `round_no` で新しく出た指摘の id（`r<ラウンド>-<そのラウンドの通し番号>`）。"""
        return f"r{round_no}-{sum(1 for f in self.rows.values() if f.get('round') == round_no) + 1}"

    def enter(self, round_no: int, raised: Sequence[dict[str, Any]]) -> frozenset[str]:
        """指摘を台帳へ入れ、このラウンドで出た鍵を返す。見送り済みは扱わず、直した指摘が戻れば未解決へ戻す。"""
        by_key = {f.get("key"): f for f in self.rows.values()}
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
                fid = self._next_id(round_no)
                self.rows[fid] = _ledger_row(fid, round_no, text, item.get("by"), key, _text(item.get("section")))
            elif known.get("outcome") == fin.FIXED:
                known |= {"outcome": fin.UNRESOLVED, "reason": None, "round": round_no}
        return frozenset(keys)

    def skipped_texts(self) -> list[str]:
        """見送り済みの指摘の本文。"""
        return [f["text"] for f in self.rows.values() if f.get("outcome") == fin.SKIPPED]

    def pending(self, handed: Sequence[fin.Handed]) -> tuple[list[dict[str, Any]], list[fin.Handed]]:
        """未解決の指摘と、まだ決めていない足すべき内容。"""
        open_ = [f for f in self.rows.values() if f.get("outcome") == fin.UNRESOLVED]
        undecided = [h for h in handed if h.id not in self.rows or self.rows[h.id].get("outcome") not in fin.DECIDED]
        return open_, undecided

    def apply_fix(self, items: Sequence[Any], handed: Sequence[fin.Handed], round_no: int) -> list[dict[str, Any]]:
        """修正の結果の指摘ごとの終わり方を取り込み、決めた（直した・見送った）件を返す。足すべき内容は新しい行にする。"""
        texts = {h.id: h.text for h in handed}
        reported = [f for f in items if isinstance(f, dict) and f.get("outcome") in fin.DECIDED]
        for item in reported:
            fid = str(item.get("id"))
            reason = _text(item.get("reason"))
            if fid in self.rows:
                self.rows[fid] |= {"outcome": item["outcome"], "reason": reason}
            elif fid in texts:
                self.rows[fid] = _ledger_row(fid, round_no, texts[fid], "rebuild") | {"outcome": item["outcome"], "reason": reason}
        return reported

    def reopen(self, fids: Sequence[str]) -> None:
        """直したとした指摘を未解決へ戻す（修正が PR に届かなかった）。"""
        for fid in fids:
            if fid in self.rows:
                self.rows[fid] |= {"outcome": fin.UNRESOLVED, "reason": None}

    def restore_fixed(self, fids: Sequence[str]) -> None:
        """送れずに未解決へ戻した指摘を、送り直せた後に直したへ戻す（未解決のままの行だけ）。"""
        for fid in fids:
            if fid in self.rows and self.rows[fid].get("outcome") == fin.UNRESOLVED:
                self.rows[fid] |= {"outcome": fin.FIXED, "reason": None}

    def findings(self) -> tuple[fin.Finding, ...]:
        """要約に載せる指摘（終わり方を知っている行）。"""
        return tuple(
            fin.Finding(f["id"], f["text"] if isinstance(f.get("text"), str) else "", f["outcome"], _text(f.get("reason")))
            for f in self.rows.values()
            if f.get("outcome") in fin.OUTCOMES
        )


# --- 始め ------------------------------------------------------------------------------


def _ingest_pending(run: _Run) -> None:
    """前の起動が残した修正の結果ファイルを、修正の記録の無い最後のラウンドへ取り込む（#2770）。"""
    rounds = store.list_of(run.data, store.ROUNDS)
    last = store.last_round(run.data)
    pending = store.pending_fix_result(run.cfg.state_dir, run.ref)
    gate = fixgate_decide.require_fix_for_changes(len(rounds), last.get("verdict"), last.get("fix"), str(pending) if pending else None)
    if gate.kind == fixgate_decide.STOP:
        raise DriveStopped("work_stopped", str(gate.reason))
    if gate.kind == fixgate_decide.INGEST and pending is not None:
        try:
            last["fix"] = _take_fix(run, pending, len(rounds))
        except store.FixResultMismatch as exc:
            raise DriveStopped("invalid", f"前の起動が残した修正の結果を取り込めない: {exc}") from exc
        _save(run)
        _consume(pending, len(rounds))


def _resend(run: _Run, synced: worktree.SyncResult) -> None:
    """前の起動が送れなかった修正のコミット（載せ直した自分のコミット）があれば送る。送れなければ記録して止める。"""
    fix = store.last_round(run.data).get("fix")
    if synced.replayed == 0 or not isinstance(fix, dict) or "pushed" in fix:
        return
    round_no = len(store.list_of(run.data, store.ROUNDS))
    remote = run.g.rev_parse(f"refs/remotes/{git.ORIGIN}/{run.cfg.branch}")
    # 載せ直しでコミットの SHA が変わるので、報告されたコミットとの照合はしない
    sent = _push(run, round_no, remote, fix, check_commit=False)
    if sent:
        # 前の起動が送れずに未解決へ戻した指摘は、PR へ届いたので直したへ戻す（修正担当へ渡し直さない）
        run.ledger.restore_fixed(store.list_of(fix, "fixed"))
    _save(run)
    if not sent:
        raise DriveStopped("work_stopped", f"前の起動の round {round_no} の修正を送れない: {run.warnings[-1]}")


def _start(cfg: RunConfig) -> _Run:
    worktree.registration(cfg.worktree)
    g = git.Git(cfg.worktree)
    synced = worktree.sync_worktree(g, cfg.branch)
    if synced.conflicts:
        raise DriveStopped("precondition", worktree.conflict_detail(synced))
    ref = store.state_pr(cfg.state_dir, cfg.pr)
    data = store.read_state(ref)
    run = _Run(cfg, g, ref, data, FindingLedger(store.list_of(data, store.FINDINGS)), [], [], [])
    _ingest_pending(run)
    _resend(run, synced)
    return run


def _round_stem(run: _Run, round_no: int, suffix: str) -> Path:
    """状態の置き場のラウンドごとのファイルの名前（`round<n><suffix>`）。"""
    return run.cfg.state_dir / f"round{round_no}{suffix}"


def _note(run: _Run, round_no: int, who: str, result: str, warning: Optional[str] = None) -> None:
    """過程へ 1 行足す。`warning` があれば先に注意へも足す。"""
    if warning is not None:
        run.warnings.append(warning)
    run.process.append({"run": f"round {round_no} {who}", "result": result})


# --- 相手役 -----------------------------------------------------------------------------


def _review_prompt(run: _Run, round_no: int, scope: scope_decide.Scope, diff: Path) -> str:
    decided = run.ledger.skipped_texts()
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
    specs = [participants.review_launch(a, cfg.worktree, prompt, _round_stem(run, round_no, f"-{a}")) for a in cfg.reviewers]
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
    paths = agents.AgentPaths.at(_round_stem(run, round_no, "-fix"))
    return agents.Launch(
        run.cfg.fixer, (*claude.claude_args(call), prompt), run.cfg.worktree, paths, limits.seconds(participants.REVIEW_TIMEOUT)
    )


def _fix_record(ledger: FindingLedger, ref: store.StateRef, path: Path, handed: Sequence[fin.Handed], round_no: int) -> dict[str, Any]:
    """修正の結果ファイルを読んで台帳へ取り込み、ラウンドの修正の記録（commit・reported・fixed）を返す。
    番号が合わなければ `store.FixResultMismatch`。"""
    found = store.read_fix_result(path, ref)
    reported = ledger.apply_fix(store.list_of(found, store.FINDINGS), handed, round_no)
    fixed = [str(f.get("id")) for f in reported if f["outcome"] == fin.FIXED]
    return {"commit": found.get("commit"), "reported": len(reported), "fixed": fixed}


def _take_fix(run: _Run, path: Path, round_no: int) -> dict[str, Any]:
    """修正の結果を台帳へ取り込む。結果ファイルは呼び手が状態ファイルへ書いてから `_consume` で移す。"""
    return _fix_record(run.ledger, run.ref, path, run.cfg.handed, round_no)


def _consume(path: Path, round_no: int, label: str = "round") -> None:
    """取り込んだ（または取り込めなかった）結果ファイルを `<label><ラウンド>` の名前へ移す（次の起動が残りと読み違えない）。
    状態ファイルへ書いた後に呼ぶ。"""
    os.replace(path, path.with_name(f"{path.stem}-{label}{round_no}.json"))


def merge_fix(state_dir: Path, pr: int, path: Path, handed: Sequence[fin.Handed] = ()) -> int:
    """駆動の外で残った修正の結果ファイルを、最後のラウンドへ `run` と同じ形で取り込む（`merge-fix`。#2770）。
    台帳へ反映して状態ファイルへ書いてから結果ファイルを移し、取り込んだラウンドの番号を返す。`handed` は `run` へ
    渡した足すべき内容（`--findings`）で、その id の決定も台帳の行にする。
    番号が合わなければ `store.FixResultMismatch`（何も書かない）。"""
    ref = store.state_pr(state_dir, pr)
    data = store.read_state(ref)
    rounds = data.setdefault(store.ROUNDS, [])
    if not rounds:
        rounds.append({"round": 1})
    round_no = rounds[-1].get("round") or len(rounds)
    ledger = FindingLedger(store.list_of(data, store.FINDINGS))
    rounds[-1]["fix"] = _fix_record(ledger, ref, path, handed, round_no)
    data[store.FINDINGS] = list(ledger.rows.values())
    store.write_object(ref.path, data)
    _consume(path, round_no)
    return round_no


def _fix(
    run: _Run, round_no: int, open_: Sequence[dict[str, Any]], handed: Sequence[fin.Handed], launch: Launcher
) -> Optional[dict[str, Any]]:
    """修正担当を起動して結果を取り込む。結果ファイルが無ければ注意に載せて None。
    起動の前に残っている結果ファイルは退避する（前の起動の結果を今のラウンドの結果と読み違えない）。修正担当が
    DONE で終わらなくても（タイムアウトなど）結果ファイルを書き終えていれば取り込む（直した・見送ったを失わない）。"""
    result_path = store.fix_result_path(run.cfg.state_dir, run.ref)
    if result_path.is_file():
        _consume(result_path, round_no, "stale-round")
    status = launch(_fixer_launch(run, round_no, _fix_prompt(run, open_, handed, result_path)))
    if not result_path.is_file():
        warning = f"round {round_no} の修正担当が結果を書かなかった（{status.state}: {status.detail}）"
        _note(run, round_no, run.cfg.fixer, f"結果無し（{status.state}）", warning)
        return None
    try:
        fix = _take_fix(run, result_path, round_no)
    except (store.FixResultMismatch, store.StateBroken) as exc:
        # 合わない・書きかけ（DONE でない修正担当）のファイルを残すと、次の起動が同じファイルを取り込もうとして
        # 止まり続けるので、退避する
        _consume(result_path, round_no, "mismatch-round")
        run.warnings.append(f"round {round_no} の修正の結果を取り込めない: {exc}")
        return None
    _note(run, round_no, run.cfg.fixer, f"指摘 {fix['reported']} 件を扱った")
    return fix


def _push(run: _Run, round_no: int, reviewed_head: str, fix: dict[str, Any], check_commit: bool = True) -> bool:
    """修正のコミットを送る。コミットが無ければ送らない。送れなければ、このラウンドで直したとした指摘を未解決へ戻し
    （PR に届いていない）、注意に載せて False。"""
    if run.g.rev_parse("HEAD") == reviewed_head:
        return True
    commit = fix.get("commit") if check_commit and isinstance(fix.get("commit"), str) else None
    sent = push.push_fix(run.g, run.cfg.branch, commit)
    if sent.ok:
        fix["pushed"] = sent.head
        return True
    run.ledger.reopen(store.list_of(fix, "fixed"))
    tail = f"（衝突: {', '.join(sent.conflicts)}）" if sent.conflicts else ""
    _note(run, round_no, "push", f"送れない（{sent.reason}）", f"round {round_no} の修正を送れなかった: {sent.reason}{tail}")
    return False


# --- 記録 -------------------------------------------------------------------------------


def _save(run: _Run) -> None:
    run.data[store.FINDINGS] = list(run.ledger.rows.values())
    store.write_object(run.ref.path, run.data)


def _summary(run: _Run, final: str, rounds: int) -> Outcome:
    findings = run.ledger.findings()
    path = run.cfg.state_dir / SUMMARY_NAME
    review = fin.Review(final, rounds, findings)
    store.write_object(path, fin.summary_of(review, run.ref.origin_pr))
    return Outcome(review, path, tuple(run.warnings), tuple(run.process))


class _Prepared(NamedTuple):
    """ラウンドの前半で決めたもの（範囲・変わった節・詳細レビューを通った節・レビューする head・差分のファイル）。"""

    scope: scope_decide.Scope
    sections: list[str]
    reviewed: set[str]
    head: str
    diff: Path


def _prepare_round(run: _Run, round_no: int) -> _Prepared:
    """範囲を決めて差分のファイルを書く。"""
    cfg = run.cfg
    reviewed = scope_decide.reviewed_of(run.data)
    sections = changed_sections(run.g, cfg.base, cfg.worktree)
    scope = scope_decide.state_scope(run.data, cfg.every_section, round_no, sections)
    head = run.g.rev_parse("HEAD")
    diff = _round_stem(run, round_no, ".diff")
    diff.write_text(run.g.run("diff", f"{scope.base or cfg.base}...HEAD"), encoding="utf-8")
    return _Prepared(scope, sections, reviewed, head, diff)


def _verdict(run: _Run, open_: Sequence[dict[str, Any]], handed: Sequence[fin.Handed]) -> str:
    """ラウンドの判定（収束・振動・修正が要る）。"""
    if not open_ and not handed:
        return fin.CONVERGED
    if oscillation.returned(run.history):
        return fin.OSCILLATING
    return CHANGES_REQUIRED


def _round(run: _Run, round_no: int, launch: Launcher) -> Optional[str]:
    """1 ラウンドを回す。終わり方（`finish_decide` の FINALS）か、次へ進むなら None。"""
    rounds = run.data.setdefault(store.ROUNDS, [])
    prep = _prepare_round(run, round_no)
    raised, answered = _review(run, round_no, _review_prompt(run, round_no, prep.scope, prep.diff), launch)
    if answered == 0:
        return fin.ERROR
    run.history.append(run.ledger.enter(round_no, raised))
    if prep.scope.kind == scope_decide.FULL:
        run.data[store.REVIEWED] = sorted(prep.reviewed | set(prep.sections))
    record: dict[str, Any] = {"round": round_no, "head": prep.head, "scope": prep.scope.kind}
    rounds.append(record)
    open_, handed = run.ledger.pending(run.cfg.handed)
    record["verdict"] = _verdict(run, open_, handed)
    if record["verdict"] != CHANGES_REQUIRED:
        return record["verdict"]
    _save(run)
    fix = _fix(run, round_no, open_, handed, launch)
    if fix is None:
        # 修正必須のまま残すと、次の起動が修正の記録の欠けで止まり続ける。errored で残し、次の起動で修正担当をもう一度起動する
        record["verdict"] = fin.ERROR
        return fin.ERROR
    record["fix"] = fix
    _save(run)
    _consume(store.fix_result_path(run.cfg.state_dir, run.ref), round_no)
    return None if _push(run, round_no, prep.head, fix) else fin.ERROR


def drive(cfg: RunConfig, launch: Launcher = agents.run_agent) -> Outcome:
    """ラウンドを上限まで回し、要約を書いて返す。始められなければ `DriveStopped`・`worktree.WorktreeUnregistered`。"""
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    run = _start(cfg)
    final, done = fin.MAX_ROUNDS, 0
    first = len(store.list_of(run.data, store.ROUNDS)) + 1
    for round_no in range(first, first + cfg.max_rounds):
        ended = _round(run, round_no, launch)
        done = len(store.list_of(run.data, store.ROUNDS)) - first + 1
        _save(run)
        if ended is not None:
            final = ended
            break
    return _summary(run, final, done)
