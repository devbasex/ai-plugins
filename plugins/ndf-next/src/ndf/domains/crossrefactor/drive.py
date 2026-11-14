"""cross-refactoring の 1 回の実行の駆動（提案 → 計画 → 項目ごとのテストの追加・実装・検証と修正 → 最終ゲート）。

- 完了の記録が残っていて PR の head と結果を変える引数が同じなら、前回の結果を返す。案内には drive が持つ手段だけを
  書く（`finished_decide`。drive は打ち直しの引数を持たないので、状態ファイルを消す手順を案内する。#2057）
- 作業場所は `workspace` が用意し、再開では作り直さずに登録を確かめる（#3106）
- 課題が触る関数（#622）を init で決め、提案と計画の項目はその関数を指すものだけを採る
- 担当の結果ファイルの置き場は `launch` が決める（#750）
- コードを書く工程は、担当を起動する前に起点を工程の記録へ書く。担当が結果を出さずに終われば、起点まで取り消して
  項目を見送る（#3313。`phase_decide`）
- テストを足した項目は、足したテストが全体の実行に収集されるかを確かめ、されなければ項目を戻す（#620。`collect`）
- 検証は項目が触るモジュールに対応するテストと検査だけを打ち（#3049）、項目の起点から在った失敗をその項目の失敗に
  数えない（#3339。`verify_decide.attribute`）
- 最終ゲートは `final_gate` が回す（衝突は待たずに止める #2680・対象範囲の外の直しは取り込まない #3038）

止まった実行は状態ファイルを残し、同じ引数で打ち直すと終わった工程を流し直さずに続ける。
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional, cast

from ndf.adapters import agents, git, github
from ndf.contracts import limits
from ndf.core.converge import participants, place, worktree
from ndf.domains.crossrefactor import (
    collect,
    final_gate,
    finished_decide,
    items_decide,
    launch,
    phase_decide,
    scope,
    store,
    symbols_decide,
    verify,
    verify_decide,
    workspace,
)
from ndf.testing import limits as test_limits

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

    def args(self) -> list[str]:
        return [self.base, *self.scope, "--symbols", *self.symbols, "--participants", *self.participants, "--max-fix", str(self.max_fix)]


@dataclass
class Ports:
    """外との接続（テストで差し替える）。"""

    launcher: launch.Launcher = agents.run_agent
    hub: Optional[Callable[[git.Git], github.GitHub]] = None
    sleep: Callable[[float], None] = time.sleep

    def github(self, work: git.Git) -> github.GitHub:
        """作業場所の origin の GitHub。"""
        return self.hub(work) if self.hub is not None else github.GitHub(work.remote_repo(), github.client())


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
    if cfg.symbols:
        touched = symbols_decide.explicit(cfg.symbols)
    else:
        touched = scope.touched(run.ws.work, cfg.base, "HEAD", cfg.scope)
    run.state.update(
        pr=cfg.pr, branch=cfg.branch, base=cfg.base, args=cfg.args(), target_scope=list(cfg.scope), touched=touched.as_json(), phases={}
    )
    run.save()


def _touched(run: _Run) -> symbols_decide.Touched:
    t = run.state["touched"]
    return symbols_decide.Touched({p: frozenset(n) for p, n in t["symbols"].items()}, tuple(t["whole_files"]))


def _record_phase(run: _Run, phase: str) -> str:
    """担当を起動する前に工程の起点を記録して返す（#3313）。"""
    head = run.work.rev_parse("HEAD")
    run.state["phases"] = phase_decide.start_phase(run.state["phases"], phase, head, _now())
    run.save()
    return head


def _propose_prompt(run: _Run) -> str:
    symbols = "\n".join(f"- {p}: {', '.join(sorted(n))}" for p, n in _touched(run).symbols.items())
    return (
        f"PR #{run.cfg.pr} が触る次の関数・クラスについて、振る舞いを変えないリファクタリングを提案する。ほかの関数は提案しない。\n"
        f"{symbols}\n"
        '最後の行に JSON {"proposals": [{"path": "<ファイル>", "symbol": "<関数名>", "title": "<要約>", "reason": "<理由>"}]} を出す。'
    )


def _propose(run: _Run) -> list[dict[str, Any]]:
    """提案の担当を順に起動し、提案をまとめる。結果を出さない担当は数えずに続ける。"""
    workspace.refresh_readers(run.ws)
    prompt = _propose_prompt(run)
    by_runtime: dict[str, list[dict[str, Any]]] = {}
    for runtime in run.ws.runtimes:  # noqa: NDF201 上限: 担当の数（ランタイムの表の既定の席と修正担当）
        got, _ = launch.run(launch.Request("propose", runtime, prompt, f"pr{run.cfg.pr}", "proposals"), run.dirs(), run.ports.launcher)
        if got is not None:
            by_runtime[runtime] = got["proposals"]
    merged, broken = items_decide.merge_proposals(by_runtime)
    run.state["proposal_counts"] = {"runtimes": len(by_runtime), "broken": broken}
    return merged


def _plan_prompt(run: _Run, proposals: Sequence[Mapping[str, Any]]) -> str:
    lines = [f"- {p['path']} {p['symbol']}: {p['title']}（{p['reason']}）" for p in proposals]
    return (
        f"PR #{run.cfg.pr} のリファクタリングの提案から、採る項目を選んで並べる（重なるものは 1 つにする）。\n"
        + "\n".join(lines)
        + '\n最後の行に JSON {"items": [{"path": "<ファイル>", "symbol": "<関数名>", "title": "<要約>", "reason": "<理由>"}]} を出す。'
    )


def _plan(run: _Run) -> None:
    """提案から項目を組む（済んでいれば何もしない）。計画の担当が結果を出さなければ提案をそのまま項目にする。"""
    if "items" in run.state:
        return
    proposals = _propose(run)
    _record_phase(run, "plan")
    planned = None
    if proposals:
        req = launch.Request("plan", participants.FIXER, _plan_prompt(run, proposals), f"pr{run.cfg.pr}", "items")
        got, _ = launch.run(req, run.dirs(), run.ports.launcher)
        planned = got["items"] if got is not None else None
    items, skipped = items_decide.plan_items(planned if planned is not None else proposals, _touched(run))
    run.state.update(items=items, skipped=skipped, plan_fallback=planned is None and bool(proposals))
    run.save()


def _reset(run: _Run, sha: str) -> None:
    run.work.run("reset", "-q", "--hard", sha)
    run.work.run("clean", "-q", "-fd")


def _close(run: _Run, item: dict[str, Any], status: Status, reason: str) -> None:
    item.update(status=status.value, reason=reason)
    run.save()


def _code(run: _Run, item: dict[str, Any], phase: str, prompt: str) -> Optional[dict[str, Any]]:
    """コードを書く工程を 1 回流す。結果が無ければ起点まで取り消して項目を見送り、None を返す（#3313）。"""
    _record_phase(run, phase)
    got, _ = launch.run(launch.Request(phase, participants.FIXER, prompt, item["id"]), run.dirs(), run.ports.launcher)
    if got is not None:
        return got
    # 振り替える担当は無い（コードを書くのは修正担当だけ）。項目は採らないので、起点の記録の有無に依らず項目の起点まで戻す
    after = phase_decide.on_no_result(run.state["phases"][phase], run.work.rev_parse("HEAD"), spare=False)
    _reset(run, item["start"])
    _close(run, item, Status.DEFERRED, f"{phase}: {after.reason}")
    return None


def _item_text(item: Mapping[str, Any]) -> str:
    return f"{item['id']}（{item['path']} の {item['symbol']}）: {item['title']}"


def _add_tests(run: _Run, item: dict[str, Any]) -> bool:
    """振る舞いを固定するテストを足す。足したテストが全体の実行に収集されなければ項目を戻す（#620）。"""
    prompt = (
        f"{_item_text(item)} を直す前に、今の振る舞いを固定するテストを足してコミットする（push しない）。"
        ' 結果は {"tests": ["<足したテストのファイル>"]}。'
    )
    got = _code(run, item, "add-tests", prompt)
    if got is None:
        return False
    tests = [str(t) for t in got["tests"]] if isinstance(got.get("tests"), list) else []
    if not tests:
        return True
    found = collect.collected(run.ws.work, tests, test_limits.load_suites(run.ws.work), limits.seconds(collect.COLLECT_POLICY))
    if found.uncollected:
        _reset(run, item["start"])
        _close(run, item, Status.REVERTED, f"足したテストが全体の実行に収集されない: {', '.join(found.uncollected)}")
        return False
    return True


def _implement(run: _Run, item: dict[str, Any]) -> bool:
    prompt = f"{_item_text(item)} を、振る舞いを変えずに直してコミットする（push しない）。ほかの関数は変えない。" ' 結果は {"commit": "<最後のコミット>"}。'
    return _code(run, item, "implement", prompt) is not None


def _own_failures(run: _Run, item: dict[str, Any], found: Sequence[verify.Suite]) -> verify_decide.Attributed:
    """項目の起点から今までに増えた失敗（#3339）。"""
    changed = list(run.work.changed_files(item["start"], "HEAD"))
    now = verify.failing(run.ws.work, found, verify.targets(run.ws.work, found, changed))
    before = verify.at_commit(run.ws.reader(participants.FIXER), item["start"], found, now)
    return verify_decide.attribute(before, now)


def _fix_prompt(item: Mapping[str, Any], own: frozenset[str]) -> str:
    return (
        f"{_item_text(item)} の変更で、次の検査かテストが落ちた（<suite>:<ファイル>）。この項目の変更の中で直してコミットする。\n"
        + "\n".join(f"- {k}" for k in sorted(own))
        + '\n結果は {"commit": "<最後のコミット>"}。'
    )


def _verify(run: _Run, item: dict[str, Any]) -> None:
    """検証と修正を回し、項目を採るか戻す。"""
    found = verify.suites(run.ws.work)
    history: list[frozenset[str]] = []
    for _ in range(run.cfg.max_fix + 1):
        got = _own_failures(run, item, found)
        history.append(got.own)
        item.update(history=[sorted(h) for h in history], inherited=sorted(got.inherited))
        progress = verify_decide.fix_progress(history)
        if progress.kind == verify_decide.Kind.PASSED:
            _close(run, item, Status.ADOPTED, progress.detail)
            return
        if progress.gave_up or len(history) > run.cfg.max_fix:
            break
        if _code(run, item, "fix", _fix_prompt(item, got.own)) is None:
            return
    _reset(run, item["start"])
    _close(run, item, Status.REVERTED, f"検証が通らない: {progress.detail}")


def _items(run: _Run) -> None:
    while (found := items_decide.next_open(run.state["items"])) is not None:
        item = cast(dict[str, Any], found)
        item["start"] = run.work.rev_parse("HEAD")
        run.save()
        if _add_tests(run, item) and _implement(run, item):
            _verify(run, item)


def _final(run: _Run) -> None:
    """最終ゲートを回して記録する（済んでいれば何もしない）。"""
    if "final" in run.state and run.state["final"]["status"] == "passed":
        return
    cfg = run.cfg
    gate = final_gate.GateConfig(cfg.pr, cfg.branch, cfg.required, cfg.tests, cfg.scope, cfg.max_fix)

    def fix(failed: Sequence[str], round_no: int) -> bool:
        _record_phase(run, "final-fix")
        prompt = (
            f"PR #{cfg.pr} の CI で次のテストが落ちた。リファクタリング計画の対象範囲（{', '.join(cfg.scope)}）の中だけを変えて直し、"
            "コミットする（push しない）。\n" + "\n".join(f"- {f}" for f in failed) + '\n結果は {"commit": "<最後のコミット>"}。'
        )
        got, _ = launch.run(launch.Request("final-fix", participants.FIXER, prompt, f"r{round_no}"), run.dirs(), run.ports.launcher)
        return got is not None

    hub = run.ports.github(run.work)
    try:
        run.state["final"] = final_gate.run_gate(run.work, hub, gate, fix, run.ports.sleep)
    except final_gate.GateStopped as exc:
        run.state["final"] = exc.record
        run.save()
        raise DriveStopped(exc.kind, exc.detail) from None
    run.save()


def _summary(run: _Run) -> Finish:
    items = run.state["items"]
    counts = items_decide.counts(items)
    metrics = {**counts, "items": len(items), "skipped": len(run.state["skipped"]), "fix_rounds": sum(max(len(i["history"]) - 1, 0) for i in items if "history" in i)}
    detail = f"項目 {len(items)} 件のうち採った {counts[Status.ADOPTED.value]} 件・戻した {counts[Status.REVERTED.value]} 件・見送った {counts[Status.DEFERRED.value]} 件。最終ゲートを通った"
    shown = [{"kind": "item", **{k: i[k] for k in ("id", "path", "symbol", "title", "status") if k in i}, "reason": i["reason"] if "reason" in i else ""} for i in items]
    return Finish("ok", detail, shown + [{"kind": "final", **run.state["final"]}], metrics)


def _open(cfg: RunConfig, ports: Ports) -> tuple[Optional[_Run], Optional[Finish]]:
    root = place.work_root(git.Git(cfg.repo), f"rf{cfg.pr}")
    path = store.path_of(root)
    state = store.load(path)
    reused = _reused(cfg, state, path)
    if reused is not None:
        return None, reused
    if "done" in state:
        state = {}
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
    except DriveStopped as exc:
        return Finish(exc.kind, exc.detail, metrics=items_decide.counts(run.state["items"]) if "items" in run.state else {})
    finish = _summary(run)
    run.state["done"] = {"head": run.work.rev_parse("HEAD"), "args": cfg.args(), "result": finish.as_json()}
    run.save()
    workspace.release(git.Git(cfg.repo), run.ws)
    return finish
