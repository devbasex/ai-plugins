"""cross-refactoring の 1 回の実行の駆動（提案 → 計画 → 項目ごとのテストの追加・実装・検証と修正 → 最終ゲート）。

- 完了の記録が残っていて PR の head と結果を変える引数が同じなら、前回の結果を返す。案内には drive が持つ手段だけを
  書く（`finished_decide`。drive は打ち直しの引数を持たないので、状態ファイルを消す手順を案内する。#2057）
- 作業場所は `workspace` が用意し、再開では作り直さずに登録を確かめる（#3106）
- 課題が触る関数（#622）を init で決め、提案と計画の項目はその関数を指すものだけを採る
- 担当の結果ファイルの置き場は `launch` が決める（#750）
- コードを書く工程は、担当を起動する前に起点を工程の記録へ書く。担当が結果を出さずに終われば、起点まで取り消して
  項目を見送る（#3313。`phase_decide`）
- テストを足した項目は、足したテストが全体の実行に収集されるかと、直す前のコードで通るかを確かめ、どちらかが
  成り立たなければ項目を戻す（#620。`collect`・`verify.tests_failing`）
- 検証は項目が触るモジュールに対応するテストと検査だけを打ち（#3049）、項目の起点から在った失敗をその項目の失敗に
  数えない（#3339。`verify_decide.attribute`）
- 最終ゲートは `final_gate` が回す（衝突は待たずに止める #2680・対象範囲の外の直しは取り込まない #3038）
- 計画の項目は実装の予算で選び、コーディングの工程の起動の秒の累計が安全網に当たれば項目を取り消さずに止める
  （`budget_decide`）
- 修正が停滞か振動した項目は、修正担当と別のランタイムに成立を判定させる（`judge_decide`）
- 打ち直すと通る失敗はフレーキーとして項目の失敗に数えず、実行の中で 1 度だけ直しを依頼する（`flaky_decide`）
- 結果の出口（完了・止まった）で、PR のリファクタリング計画のコメントを書き、logs へ完了報告を置く（`report_decide`）

止まった実行は状態ファイルを残し、同じ引数で打ち直すと終わった工程を流し直さずに続ける。
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional, cast

from ndf.adapters import agents, git, github, publish
from ndf.contracts import limits
from ndf.core.converge import participants, worktree
from ndf.domains.crossrefactor import (
    budget_decide,
    collect,
    final_gate,
    finished_decide,
    flaky_decide,
    items_decide,
    judge_decide,
    launch,
    launch_decide,
    phase_decide,
    prompt_decide,
    report_decide,
    scope,
    store,
    symbols_decide,
    tests_decide,
    verify,
    verify_decide,
    workspace,
)
from ndf.testing import limits as test_limits
from ndf.testing import slots

Status = items_decide.Status


class DriveStopped(Exception):
    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


@dataclass(frozen=True)
class RunConfig:
    """1 回の実行の引数。`args` は結果を変える引数の並び（完了の記録と比べる）。"""

    repo: Path
    pr: int
    branch: str
    base: str
    scope: tuple[str, ...]
    required: tuple[str, ...]
    tests: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    participants: tuple[str, ...] = ()
    max_fix: int = 2
    budget_minutes: Optional[int] = None
    safety_net_minutes: Optional[float] = None

    def args(self) -> list[str]:
        """結果を変える引数の並び。安全網の上限は入れない（大きくして打ち直すと、止まった所から続ける）。"""
        return [
            self.base, *self.scope, "--required", *self.required, "--ci-test", *self.tests, "--symbols", *self.symbols,
            "--participants", *self.participants, "--max-fix", str(self.max_fix), "--budget-minutes", str(self.budget),
        ]  # fmt: skip

    @property
    def budget(self) -> int:
        return self.budget_minutes if self.budget_minutes is not None else budget_decide.default_minutes()


@dataclass
class Ports:
    """外との接続（テストで差し替える）。"""

    launcher: launch.Launcher = agents.run_agent
    hub: Optional[Callable[[git.Git], github.GitHub]] = None
    sleep: Callable[[float], None] = time.sleep
    comment: Optional[Callable[[git.Git, int, str], str]] = None
    clock: Callable[[], float] = time.monotonic
    ci: final_gate.CiPort = field(default_factory=final_gate.CiPort)

    def github(self, work: git.Git) -> github.GitHub:
        """作業場所の origin の GitHub。"""
        return self.hub(work) if self.hub is not None else github.GitHub(work.remote_repo(), github.client())

    def post(self, work: git.Git, pr: int, body: str) -> str:
        """PR へコメントを書き、その URL を返す（書く操作は `publish.Publisher` を通す）。"""
        if self.comment is not None:
            return self.comment(work, pr, body)
        return publish.Publisher(self.github(work)).comment(pr, body).url


@dataclass(frozen=True)
class Finish:
    """実行の終わり。`kind` は `ok` か止まった理由の種類。"""

    kind: str
    detail: str
    items: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "detail": self.detail, "items": self.items, "metrics": self.metrics}

    @classmethod
    def from_json(cls, data: Mapping[str, Any], notes: Sequence[str] = ()) -> Finish:
        return cls(str(data["kind"]), str(data["detail"]), list(data["items"]), dict(data["metrics"]), tuple(notes))


@dataclass
class _Run:
    cfg: RunConfig
    ports: Ports
    ws: workspace.Workspace
    state: dict[str, Any]
    path: Path

    @property
    def work(self) -> git.Git:
        return git.Git(self.ws.work)

    def state_get(self, key: str, default: Any) -> Any:
        """状態の `key` の値。無ければ `default`。"""
        return self.state[key] if key in self.state else default

    def save(self) -> None:
        store.save(self.path, self.state)

    def dirs(self) -> launch.Dirs:
        return launch.Dirs(self.ws.read_root, self.ws.work, self.ws.logs)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _reused(cfg: RunConfig, state: Mapping[str, Any], path: Path) -> Optional[Finish]:
    """完了の記録から前回の結果を返すか決める（#2057）。打ち直すなら None。"""
    if "done" not in state:
        return None
    done = state["done"]
    head = worktree.fetch_head(git.Git(cfg.repo), cfg.branch)
    recorded = (done["head"] if "head" in done else None, done["args"] if "args" in done else None)
    hint = f"状態ファイル {path} を消してから同じコマンドを打ち直す"
    got = finished_decide.finished(recorded, (head, cfg.args()), reopen_flag=None, discard_hint=hint)
    return Finish.from_json(done["result"], got.notes) if got.keep else None


def _init(run: _Run) -> None:
    """起点・対象範囲・課題が触る関数を記録する（済んでいれば何もしない）。"""
    if "touched" in run.state:
        return
    cfg = run.cfg
    touched = scope.touched_or_explicit(run.ws.work, cfg.base, "HEAD", cfg.scope, cfg.symbols)
    run.state.update(
        pr=cfg.pr, branch=cfg.branch, base=cfg.base, args=cfg.args(), target_scope=list(cfg.scope), touched=touched.as_json(), phases={}
    )
    run.save()


def _touched(run: _Run) -> symbols_decide.Touched:
    return symbols_decide.Touched.from_json(run.state["touched"])


def _record_phase(run: _Run, phase: str) -> str:
    """担当を起動する前に工程の起点を記録して返す（#3313）。"""
    head = run.work.rev_parse("HEAD")
    run.state["phases"] = phase_decide.start_phase(run.state["phases"], phase, head, _now())
    run.save()
    return head


def _net(run: _Run) -> budget_decide.SafetyNet:
    """安全網。上限は今の引数から毎回組む（--safety-net-minutes を大きくして打ち直すと続けられる）。"""
    budget = run.state_get("budget", None)
    effective = budget_decide.Budget(budget["minutes"], budget["extended_to"]).effective if budget is not None else run.cfg.budget
    used = float(run.state_get("coding_seconds", 0.0))
    return budget_decide.SafetyNet.of(effective, run.cfg.safety_net_minutes, used)


def _launch(run: _Run, req: launch.Request) -> Optional[dict[str, Any]]:
    """担当を 1 回起動し、工程別の所要と安全網の累計へ秒を足す。コーディングの工程は安全網に当たっていれば起動しない。

    安全網の残りで監視の上限を縮めた起動が上限に達したら、所要を保存してから項目を取り消さずに止める（安全網を
    大きくして打ち直すと、起点の記録からその項目をやり直す）。
    """
    coding = req.phase in launch_decide.coding_phases()
    capped = False
    if coding:
        net = _net(run)
        if net.halted:
            raise DriveStopped("fix_limit", net.reason())
        capped = net.remaining_seconds < launch.phase_limit(req.phase)
        req = replace(req, timeout=min(launch.phase_limit(req.phase), net.remaining_seconds))
    began = run.ports.clock()
    got, status = launch.run(req, run.dirs(), run.ports.launcher)
    spent = run.ports.clock() - began
    seconds = dict(run.state_get("phase_seconds", {}))
    seconds[req.phase] = round((seconds[req.phase] if req.phase in seconds else 0.0) + spent, 1)
    run.state["phase_seconds"] = seconds
    if coding:
        run.state["coding_seconds"] = _net(run).add(req.phase, spent).used_seconds
    run.save()
    if capped and status.state == agents.AgentState.TIMEOUT:
        raise DriveStopped("fix_limit", _net(run).reason())
    return got


def _propose(run: _Run) -> list[dict[str, Any]]:
    """提案の担当を順に起動し、提案をまとめる。結果を出さない担当は数えずに続ける。"""
    workspace.refresh_readers(run.ws)
    prompt = prompt_decide.propose(run.cfg.pr, _touched(run))
    by_runtime: dict[str, list[dict[str, Any]]] = {}
    for runtime in run.ws.runtimes:
        got = _launch(run, launch.Request("propose", runtime, prompt, f"pr{run.cfg.pr}", "proposals"))
        if got is not None:
            by_runtime[runtime] = got["proposals"]
    merged, broken = items_decide.merge_proposals(by_runtime)
    run.state["proposal_counts"] = {"runtimes": len(by_runtime), "broken": broken}
    return merged


def _plan(run: _Run) -> None:
    """提案から項目を組む（済んでいれば何もしない）。計画の担当が結果を出さなければ提案をそのまま項目にする。"""
    if "items" in run.state:
        return
    proposals = _propose(run)
    _record_phase(run, "plan")
    planned = None
    if proposals:
        req = launch.Request("plan", participants.FIXER, prompt_decide.plan(run.cfg.pr, proposals), f"pr{run.cfg.pr}", "items")
        got = _launch(run, req)
        planned = got["items"] if got is not None else None
    items, skipped = items_decide.plan_items(planned if planned is not None else proposals, _touched(run))
    budget = None
    if planned is not None:
        # 見積りは計画の担当だけが返す。提案をそのまま項目にしたときは予算で選ばない
        picked = budget_decide.select(items, run.cfg.budget)
        items, skipped, budget = picked.adopted, skipped + picked.skipped, picked.budget.as_json()
    run.state.update(items=items, skipped=skipped, plan_fallback=planned is None and bool(proposals), budget=budget)
    run.save()


def _reset(run: _Run, sha: str) -> None:
    run.work.run("reset", "-q", "--hard", sha)
    run.work.run("clean", "-q", "-fd")


def _close(run: _Run, item: dict[str, Any], status: Status, reason: str) -> None:
    item.update(status=status.value, reason=reason)
    run.save()


def _undo_item(run: _Run, item: dict[str, Any], status: Status, reason: str) -> None:
    """項目の起点まで取り消してから、状態と理由を書いて閉じる。"""
    _reset(run, item["start"])
    _close(run, item, status, reason)


def _code(run: _Run, item: dict[str, Any], phase: str, prompt: str) -> Optional[dict[str, Any]]:
    """コードを書く工程を 1 回流す。結果が無ければ起点まで取り消して項目を見送り、None を返す（#3313）。"""
    _record_phase(run, phase)
    got = _launch(run, launch.Request(phase, participants.FIXER, prompt, item["id"]))
    if got is not None:
        return got
    # 振り替える担当は無い（コードを書くのは修正担当だけ）。項目は採らないので、起点の記録の有無に依らず項目の起点まで戻す
    after = phase_decide.on_no_result(run.state["phases"][phase], run.work.rev_parse("HEAD"), spare=False)
    _undo_item(run, item, Status.DEFERRED, f"{phase}: {after.reason}")
    return None


def _add_tests(run: _Run, item: dict[str, Any]) -> bool:
    """振る舞いを固定するテストを足す。足したテストが全体の実行に収集されなければ項目を戻す（#620）。"""
    got = _code(run, item, "add-tests", prompt_decide.add_tests(item))
    if got is None:
        return False
    tests = [str(t) for t in got["tests"]] if isinstance(got.get("tests"), list) else []
    if not tests:
        return True
    got_collect = collect.collected(run.ws.work, tests, test_limits.load_suites(run.ws.work), limits.seconds(collect.COLLECT_POLICY))
    if got_collect.uncollected:
        _undo_item(run, item, Status.REVERTED, f"足したテストが全体の実行に収集されない: {', '.join(got_collect.uncollected)}")
        return False
    # 実装の前（直す前のコード）で足したテストが通ることを確かめる。通らないテストは今の振る舞いを固定していない。
    # 鍵はファイル単位なので、起点で元から落ちていた鍵と打ち直すと通る鍵は除く（_own_failures と同じ帰属）
    found = verify.suites(run.ws.work)
    broken = _attribute(run, item, found, verify.tests_failing(run.ws.work, found, tests)).own
    if broken:
        _undo_item(run, item, Status.REVERTED, f"足したテストが直す前のコードで通らない: {', '.join(sorted(broken))}")
        return False
    return True


def _implement(run: _Run, item: dict[str, Any]) -> bool:
    return _code(run, item, "implement", prompt_decide.implement(item)) is not None


def _own_failures(run: _Run, item: dict[str, Any], found: Sequence[verify.Suite]) -> verify_decide.Attributed:
    """項目の起点から今までに増えた失敗（#3339）。打ち直して通った鍵はフレーキーとして除き、直しの依頼へ積む。"""
    changed = list(run.work.changed_files(item["start"], "HEAD"))
    now = verify.failing(run.ws.work, found, verify.targets(run.ws.work, found, changed))
    return _attribute(run, item, found, now)


def _attribute(run: _Run, item: dict[str, Any], found: Sequence[verify.Suite], now: frozenset[str]) -> verify_decide.Attributed:
    """落ちた鍵 `now` から、項目の起点で元から落ちていた鍵を除き、打ち直して通った鍵をフレーキーとして直しの依頼へ積む。"""
    before = verify.at_commit(run.ws.reader(participants.FIXER), item["start"], found, now)
    got = verify_decide.attribute(before, now)
    if not got.own:
        return got
    again = verify.failing(run.ws.work, found, verify.regroup(got.own))
    stable, flaky = flaky_decide.split_flaky(got.own, again)
    _ask_flaky(run, flaky)
    return verify_decide.Attributed(stable, got.inherited)


def _judges(run: _Run) -> list[str]:
    return [r for r in run.ws.runtimes if r != participants.FIXER]


def _judge(run: _Run, item: dict[str, Any], own: frozenset[str]) -> judge_decide.Move:
    """修正が停滞か振動した項目の成立を、修正担当と別のランタイムに判定させる（1 項目に 1 回）。"""
    judges = _judges(run)
    move = judge_decide.after_stall(item, judges)
    if move.kind != judge_decide.JUDGE:
        return move
    # 判定の担当は読み取り用ディレクトリで読む。検証中の HEAD（足したテスト・実装・修正）へ合わせてから起動する
    workspace.refresh_readers(run.ws)
    got = _launch(run, launch.Request("judge", judges[0], prompt_decide.judge(item, own), item["id"], "items"))
    row = judge_decide.answer_of(got, item["id"])
    item["feasibility"] = [*(item["feasibility"] if "feasibility" in item else []), judge_decide.record(judges[0], row, _now())]
    run.save()
    return judge_decide.on_answer(row)


def _stop_reason(
    run: _Run, item: dict[str, Any], progress: verify_decide.Progress, own: frozenset[str], fixes: int
) -> tuple[Optional[str], bool]:
    """修正をやめる理由（続けるなら None）と、履歴を区切るか。停滞か振動したら成立を判定させる。"""
    if progress.gave_up:
        move = _judge(run, item, own)
        if move.kind != judge_decide.CONTINUE:
            return f"{move.detail}: {progress.detail}", False
        return None, True
    if fixes >= run.cfg.max_fix:
        return f"修正の回数の上限 {run.cfg.max_fix} 回に達した: {progress.detail}", False
    return None, False


def _verify(run: _Run, item: dict[str, Any]) -> None:
    """検証と修正を回し、項目を採るか戻す。停滞か振動したら成立を判定させ、成立するならもう一巡だけ続ける。"""
    found = verify.suites(run.ws.work)
    history: list[frozenset[str]] = []
    rounds: list[list[str]] = []
    fixes = 0
    reason = ""
    for _ in range(run.cfg.max_fix + 3):
        got = _own_failures(run, item, found)
        history.append(got.own)
        rounds.append(sorted(got.own))
        item.update(history=rounds, inherited=sorted(got.inherited))
        progress = verify_decide.fix_progress(history)
        if progress.kind == verify_decide.Kind.PASSED:
            _close(run, item, Status.ADOPTED, progress.detail)
            return
        stop, regroup = _stop_reason(run, item, progress, got.own, fixes)
        reason = stop if stop is not None else progress.detail
        if stop is not None:
            break
        if regroup:
            history = [got.own]
        if _code(run, item, "fix", prompt_decide.fix(item, got.own)) is None:
            return
        fixes += 1
    _undo_item(run, item, Status.REVERTED, f"検証が通らない: {reason}")


def _ask_flaky(run: _Run, flaky: frozenset[str]) -> None:
    """フレーキーな鍵のうち、この実行でまだ依頼していないものを直しの依頼へ積む。"""
    asked = run.state_get("flaky_requested", [])
    fresh = flaky_decide.fresh(asked, flaky)
    if not fresh:
        return
    held = run.state_get("flaky_request", None)
    pending = held if held else {"keys": [], "attempts": 0, "sendback": []}
    pending["keys"] = sorted({*pending["keys"], *fresh})
    run.state.update(flaky_requested=sorted({*asked, *fresh}), flaky_request=pending)
    run.save()


def _attempt_flaky_fix(run: _Run, pending: dict[str, Any]) -> tuple[list[flaky_decide.Commit], str]:
    """直しを起動し、違反があれば起点まで取り消して差し戻す（上限まで）。最後の起動のコミットと結果の種類を返す。"""
    commits: list[flaky_decide.Commit] = []
    kind = flaky_decide.NONE
    for _ in range(flaky_decide.MAX_ATTEMPTS):
        base = _record_phase(run, "flaky-fix")
        try:
            _launch(run, launch.Request("flaky-fix", participants.FIXER, prompt_decide.flaky(pending), "flaky"))
        except DriveStopped:
            # 止まった起動の途中のコミットを検査せずに残さない（打ち直すと起点から直しをやり直す）
            _reset(run, base)
            raise
        commits = flaky_decide.parse_log(run.work.run("log", "--reverse", "--name-only", flaky_decide.LOG_FORMAT, f"{base}..HEAD"))
        found = flaky_decide.violations(commits, pending["keys"], tests_decide.is_test_file)
        pending["attempts"] += 1
        kind = flaky_decide.outcome(commits, found, pending["attempts"])
        if kind in (flaky_decide.SENDBACK, flaky_decide.REVERTED):
            _reset(run, base)
        if kind != flaky_decide.SENDBACK:
            break
        pending["sendback"] = found
        run.save()
    return commits, kind


def _flaky_fix(run: _Run) -> None:
    """積んだフレーキーなテストの直しを依頼して取り込む。違反は起点まで取り消して 1 度だけ差し戻す。項目には触れない。"""
    if "flaky_request" not in run.state or not run.state["flaky_request"]:
        return
    pending = run.state["flaky_request"]
    commits, kind = _attempt_flaky_fix(run, pending)
    done = flaky_decide.results(pending["keys"], commits, kind)
    run.state.update(flaky_request=None, flaky_fixes=[*run.state_get("flaky_fixes", []), *done])
    run.save()


def _items(run: _Run) -> None:
    """未着手の項目を順に流す。止まった実行の途中の項目（起点の記録がある）は、起点まで戻してからやり直す。

    途中の項目は、積まれていたフレーキーの直しを取り込む前に起点へ戻す。直しの後に戻すと直しのコミットが消える。
    起点は直しを取り込んだ後の HEAD へ置き直す（項目を取り消しても直しのコミットは残す）。
    """
    resumed = items_decide.next_open(run.state["items"])
    if resumed is not None and "start" in resumed:
        _reset(run, resumed["start"])
    _flaky_fix(run)
    while (found := items_decide.next_open(run.state["items"])) is not None:
        item = cast(dict[str, Any], found)
        item["start"] = run.work.rev_parse("HEAD")
        run.save()
        if _add_tests(run, item) and _implement(run, item):
            _verify(run, item)
        _flaky_fix(run)


def _final(run: _Run) -> None:
    """最終ゲートを回して記録する（済んでいれば何もしない）。"""
    if "final" in run.state and run.state["final"]["status"] == "passed":
        return
    cfg = run.cfg
    gate = final_gate.GateConfig(cfg.pr, cfg.branch, cfg.required, cfg.tests, cfg.scope, cfg.max_fix)

    def fix(failed: Sequence[str], round_no: int) -> bool:
        _record_phase(run, "final-fix")
        prompt = prompt_decide.final_fix(cfg.pr, cfg.scope, failed)
        return _launch(run, launch.Request("final-fix", participants.FIXER, prompt, f"r{round_no}")) is not None

    hub = run.ports.github(run.work)
    try:
        run.state["final"] = final_gate.run_gate(run.work, hub, gate, fix, run.ports.sleep, run.ports.ci)
    except final_gate.GateStopped as exc:
        run.state["final"] = exc.record
        run.save()
        raise DriveStopped(exc.kind, exc.detail) from None
    run.save()


def _step_metrics(run: _Run) -> dict[str, Any]:
    """予算・安全網・成立の判定・フレーキーの直しの数。"""
    items = run.state_get("items", [])
    net = _net(run)
    flaky = run.state_get("flaky_fixes", [])
    return {
        "budget_minutes": run.cfg.budget,
        "safety_net_minutes": round(net.limit_minutes, 1),
        "coding_minutes": round(net.used_seconds / 60, 1),
        "judged": sum(len(i["feasibility"]) for i in items if "feasibility" in i),
        "flaky_fixed": sum(1 for f in flaky if f["result"] == flaky_decide.KEPT),
    }


def _summary(run: _Run) -> Finish:
    items = run.state["items"]
    counts = items_decide.counts(items)
    metrics = {
        **counts,
        "items": len(items),
        "skipped": len(run.state["skipped"]),
        "fix_rounds": sum(max(len(i["history"]) - 1, 0) for i in items if "history" in i),
    }
    detail = f"項目 {len(items)} 件のうち採った {counts[Status.ADOPTED.value]} 件・戻した {counts[Status.REVERTED.value]} 件・見送った {counts[Status.DEFERRED.value]} 件。最終ゲートを通った"
    shown = [
        {
            "kind": "item",
            **{k: i[k] for k in ("id", "path", "symbol", "title", "status") if k in i},
            "reason": i["reason"] if "reason" in i else "",
        }
        for i in items
    ]
    return Finish("ok", detail, shown + [{"kind": "final", **run.state["final"]}], {**metrics, **_step_metrics(run)})


def _plan_comment(run: _Run, outcome: str) -> str:
    """リファクタリング計画のコメントを書く。前に書いた本文と同じなら書かない。書けなくても結果は変えない。"""
    if "items" not in run.state:
        return "skipped"
    body = report_decide.plan_comment(run.cfg.pr, run.state, outcome)
    sent = run.state_get("plan_comment", None)
    if sent is not None and sent["digest"] == report_decide.digest(body):
        return "unchanged"
    try:
        url = run.ports.post(run.work, run.cfg.pr, body)
    except github.GitHubError as exc:
        print(f"⚠ リファクタリング計画のコメントを書けなかった（結果は変えない）: {exc}", file=sys.stderr)
        return "failed"
    run.state["plan_comment"] = {"digest": report_decide.digest(body), "url": url}
    run.save()
    return "written"


def _publish(run: _Run, finish: Finish) -> Finish:
    """結果の出口（完了・止まった）で、計画のコメントを書き、完了報告を logs へ置く。"""
    outcome = "完了" if finish.kind == "ok" else f"止まった（{finish.kind}: {finish.detail}）"
    report = run.ws.logs / "report.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(report_decide.report(run.cfg.pr, run.state, outcome), encoding="utf-8")
    metrics = {**finish.metrics, "report": str(report), "plan_comment": _plan_comment(run, outcome)}
    return Finish(finish.kind, finish.detail, finish.items, metrics, finish.notes)


def _resume_mismatch(cfg: RunConfig, state: Mapping[str, Any], path: Path) -> Optional[Finish]:
    """未完了の状態を続ける前に、記録の対象ブランチと結果を変える引数を今と比べる。違えば止まる（続けない）。

    違う引数で続けると、前回の範囲で選んだ項目と予算をそのまま処理し、今回の範囲の外まで公開してしまう。
    """
    if "args" not in state:
        return None
    if state["branch"] == cfg.branch and list(state["args"]) == cfg.args():
        return None
    detail = f"未完了の状態の対象ブランチか結果を変える引数が今回と違う。前と同じ引数で打ち直すか、状態ファイル {path} を消してから打ち直す"
    return Finish("invalid", detail)


def _open(cfg: RunConfig, ports: Ports) -> tuple[Optional[_Run], Optional[Finish]]:
    root = workspace.root_of(git.Git(cfg.repo), cfg.pr)
    path = store.path_of(root)
    state = store.load(path)
    reused = _reused(cfg, state, path)
    if reused is not None:
        return None, reused
    if "done" in state:
        state = {}
    mismatch = _resume_mismatch(cfg, state, path)
    if mismatch is not None:
        return None, mismatch
    runtimes = (*participants.assign(cfg.participants), participants.FIXER)
    ws = workspace.prepare(git.Git(cfg.repo), cfg.pr, cfg.branch, runtimes)
    return _Run(cfg, ports, ws, state, path), None


def drive(cfg: RunConfig, ports: Optional[Ports] = None) -> Finish:
    """1 回の実行を進める。止まれば `Finish` の `kind` に止まった理由の種類を入れて返す（状態ファイルは残す）。"""
    run, reused = _open(cfg, ports if ports is not None else Ports())
    if reused is not None:
        for note in reused.notes:
            print(note, file=sys.stderr)
        return reused
    assert run is not None
    try:
        _init(run)
        _plan(run)
        _items(run)
        _final(run)
    except (DriveStopped, slots.Stopped) as exc:
        # テストの枠の待ちの諦めとメモリの停止は、落ちたテストに数えずに止まった理由にする（状態ファイルは残す）
        kind, detail = (exc.kind, exc.detail) if isinstance(exc, DriveStopped) else (exc.cause, exc.summary())
        counts = items_decide.counts(run.state["items"]) if "items" in run.state else {}
        return _publish(run, Finish(kind, detail, metrics={**counts, **_step_metrics(run)}))
    finish = _publish(run, _summary(run))
    run.state["done"] = {"head": run.work.rev_parse("HEAD"), "args": cfg.args(), "result": finish.as_json()}
    run.save()
    workspace.release(git.Git(cfg.repo), run.ws)
    return finish
