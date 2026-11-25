"""版上げのスプリント（モード `release-bump`）のステップのコマンド（`ndf.progress.gate` の部品。#4446 の決定 9）。

ndf progress bump-assemble   workflow の assemble を起動して実行の終わりを待ち、版上げの PR の本文（承認資料）をファイルへ写す
ndf progress bump-present    読み手役が確かめた承認資料を版上げの PR に提示する（承認は続く承認ゲートが受ける）
ndf progress bump-record     まとめたリリースの回答を計画へ記録し、まとめた各スプリントのレーンの行へ回答を載せる

どれも工程の `ndf.script` のステップが打ち、値は引数でなく工程の入力（環境変数 `NDF_STAGE_INPUT`）で受ける。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ndf import cli
from ndf.adapters import git
from ndf.adapters.github import GitHub, GitHubError, client
from ndf.contracts import limits, result
from ndf.core import branch_strategy
from ndf.progress import command, invoke, lane, model, plan, run, store
from ndf.progress.gate.asking import RELEASE_KIND

TOOL = cli.tool_of(__package__)
# 版上げの PR を作らない設定の承認の対象（`.ndf/project.json` の branch_strategy.approval.target。本番の PR を承認する）
NO_BUMP_TARGET = "production_pr"
# スプリントブランチを集めるリリース方式
UNITS_STRATEGY = "release_branch"
# workflow の組み立てを待つ上限と間隔
WAIT = "release-bump.assemble"
# 保存先（チェックアウトの保存先の下）と、承認資料・起動した実行と PR を持つマーカーのファイル
BUMP_DIR = "bump"
MATERIAL_FILE, DISPATCHED_FILE = "material.md", "dispatched"


class Stop(Exception):
    """打てない理由。`kind` は停止理由の種類。"""

    def __init__(self, message: str, kind: str = "precondition") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Io:
    """外との接点（テストが差し替える）。`ndf` はサブコマンドを打って結果 JSON を返す。"""

    hub: Callable[[Path], GitHub]
    ndf: Callable[[Sequence[str], Path], dict[str, Any]]
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic


def _hub(cwd: Path) -> GitHub:
    g = git.Git(cwd)
    return GitHub(g.remote_repo(git.remote_of(g.common_dir().parent)), client())


def _ndf(argv: Sequence[str], cwd: Path) -> dict[str, Any]:
    done = subprocess.run(invoke.ndf_command(*argv), cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False)
    got = result.last_line(done.stdout)
    return (
        got if got is not None else result.stopped(TOOL, "bad_report", f"ndf {argv[0]} の結果 JSON が無い（終了コード {done.returncode}）")
    )


def default_io() -> Io:
    return Io(_hub, _ndf)


def _stage_values(environ: Mapping[str, str], *keys: str) -> dict[str, Any]:
    got = invoke.stage_inputs(environ)
    missing = [k for k in keys if got is None or k not in got]
    if got is None or missing:
        raise Stop(f"工程の入力（環境変数 {invoke.INPUT_ENV}）に {', '.join(missing)} が無い", "invalid")
    return got


def _plan_of(conn: Any, st: store.Store, cwd: Path, name: str) -> tuple[str, lane.PlanView]:
    pid = plan.plan_id(command.repo_id(cwd, st.root), name)
    view, _ = lane.read_plan(conn, pid)
    return pid, view


def _release_requests(view: lane.PlanView, keys: Sequence[str]) -> list[lane.Request]:
    """まとめるリリースのリクエスト。計画に無いか、リリースのリクエストでなければ `Stop`。"""
    bad = [k for k in keys if k not in view.requests or view.requests[k].body.get("kind") != RELEASE_KIND]
    if bad:
        raise Stop(f"計画 {view.name} にリリースのリクエスト {', '.join(bad)} が無い")
    return [view.requests[k] for k in keys]


def _open_releases(io: Io, cwd: Path, branches: Mapping[str, str]) -> list[int]:
    got = io.ndf(["release-steps", "open-releases", "--base", branches["base"], "--production", branches["production"]], cwd)
    if got["status"] != result.OK:
        raise Stop(f"開いている版上げの PR を読めない: {got['summary']}")
    return [int(i["number"]) for i in got["items"]]


def _units(io: Io, cwd: Path, strategy: branch_strategy.Strategy, requests: Sequence[lane.Request]) -> dict[str, str]:
    """workflow の assemble の範囲の入力。スプリントブランチを集める方式だけが、リクエストの PR のブランチを `units` に渡す。"""
    if strategy.release_strategy != UNITS_STRATEGY:
        return {}
    hub = io.hub(cwd)
    numbers = sorted({int(n) for r in requests for n in r.body["prs"]})
    heads = [hub.pull(n).head_ref for n in numbers]
    if not heads:
        raise Stop("まとめるリクエストに PR が無く、集めるスプリントブランチを決められない")
    return {"units": " ".join(heads)}


def bump_assemble(a: argparse.Namespace, cwd: Path, st: store.Store, io: Io, environ: Mapping[str, str]) -> dict[str, Any]:
    """workflow の assemble を起動し、その実行が作った版上げの PR の本文を承認資料にする。"""
    inputs = _stage_values(environ, "release_requests", "release_plan")
    with store.transaction(st) as conn:
        _pid, view = _plan_of(conn, st, cwd, inputs["release_plan"])
        requests = _release_requests(view, inputs["release_requests"])
    strategy = branch_strategy.load(git.Git(cwd).common_dir().parent)
    if strategy.section["approval"]["target"] == NO_BUMP_TARGET:
        raise Stop(f"承認の対象が {NO_BUMP_TARGET} の設定は版上げの PR を作らず、版上げのスプリントを流せない")
    ident = hashlib.sha256(json.dumps([inputs["release_plan"], inputs["release_requests"]]).encode()).hexdigest()
    work = st.root / run.WORK_DIR / BUMP_DIR / ident
    work.mkdir(parents=True, exist_ok=True)
    number = _release_pr(io, cwd, strategy, requests, work / DISPATCHED_FILE)
    path = work / MATERIAL_FILE
    path.write_text(io.hub(cwd).pull(number).body, encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    material = {"path": str(path.resolve()), "sha256": digest, "file_sha256": digest}
    outputs = {"pr": number, model.MATERIAL_KEY: material}
    return result.result(TOOL, result.OK, f"版上げの PR #{number} の承認資料を {path} へ写した", outputs=outputs)


def _release_pr(io: Io, cwd: Path, strategy: branch_strategy.Strategy, requests: Sequence[lane.Request], mark: Path) -> int:
    """このスプリントが起動した assemble の版上げの PR の番号。

    マーカーは起動した実行の id（`run`）と、その実行が作った PR（`pr`）を持つ。打ち直しでは、記録した PR が開いていれば使い、
    PR を記録する前に止まっていれば起動し直さずに同じ実行を待つ。"""
    branches = strategy.section["branches"]
    held = json.loads(mark.read_text(encoding="utf-8")) if mark.exists() else None
    if held is not None and "pr" in held:
        if held["pr"] in _open_releases(io, cwd, branches):
            return held["pr"]
        held = None  # 記録した PR は閉じたので組み直す
    if held is None:
        opened = _open_releases(io, cwd, branches)
        if opened:
            listed = ", ".join(f"#{n}" for n in opened)
            raise Stop(f"{branches['production']} 宛ての版上げの PR {listed} が既に開いている（閉じた後に打ち直す）")
        held = {"run": _dispatch(io.hub(cwd), cwd, io, strategy, requests)}
        mark.write_text(json.dumps(held) + "\n", encoding="utf-8")
    _await_run(io, io.hub(cwd), held["run"], mark)
    opened = _open_releases(io, cwd, branches)
    if len(opened) != 1:
        mark.unlink()
        raise Stop(f"assemble の実行 {held['run']} は成功したが、開いている版上げの PR が {len(opened)} 件で、作った PR を決められない")
    mark.write_text(json.dumps({**held, "pr": opened[0]}) + "\n", encoding="utf-8")
    return opened[0]


def _dispatch(hub: GitHub, cwd: Path, io: Io, strategy: branch_strategy.Strategy, requests: Sequence[lane.Request]) -> int:
    """workflow の assemble を起動し、起動した実行の id を返す（`return_run_details`）。"""
    section = strategy.section
    workflow = section["release"]["cd"]["workflow"]
    inputs = {"step": "assemble", **_units(io, cwd, strategy, requests)}
    body = {"ref": section["branches"]["production"], "inputs": inputs, "return_run_details": True}
    got = hub.rest("POST", f"/repos/{hub.repo}/actions/workflows/{workflow}/dispatches", body)
    if not isinstance(got, dict) or "workflow_run_id" not in got:
        raise Stop(f"workflow {workflow} を起動したが実行の id が返らない（実行の結果を確かめる）")
    return int(got["workflow_run_id"])


def _await_run(io: Io, hub: GitHub, run_id: int, mark: Path) -> None:
    """assemble の実行が終わるまで待つ。成功で終わらなければマーカーを消して止まる（打ち直しで起動し直す）。"""
    seconds, interval = limits.seconds(WAIT), limits.value(WAIT, "interval")
    deadline = io.clock() + seconds
    while io.clock() < deadline:
        io.sleep(interval)
        got = hub.workflow_run(run_id)
        if got["status"] != "completed":
            continue
        if got["conclusion"] != "success":
            mark.unlink()
            raise Stop(f"assemble の実行が {got['conclusion']} で終わった: {got['html_url']}", "check_failed")
        return
    raise Stop(f"assemble の実行 {run_id} が終わらない（{seconds:g} 秒待った。打ち直すと同じ実行を待つ）", "wait_timeout")


def bump_present(a: argparse.Namespace, cwd: Path, st: store.Store, io: Io, environ: Mapping[str, str]) -> dict[str, Any]:
    """承認資料を版上げの PR に提示する。提示の「回答待ち」は、続く承認ゲートが受けるので ok にする。"""
    inputs = _stage_values(environ, "pr", model.MATERIAL_KEY)
    got = io.ndf(["release-steps", "present", "--pr", str(inputs["pr"]), f"--material={inputs[model.MATERIAL_KEY]['path']}"], cwd)
    if got["status"] == result.STOPPED:
        return got
    summary = f"版上げの PR #{inputs['pr']} に承認資料を提示した: {got['summary']}"
    return result.result(TOOL, result.OK, summary)


def bump_record(a: argparse.Namespace, cwd: Path, st: store.Store, io: Io, environ: Mapping[str, str]) -> dict[str, Any]:
    """リリースのステップがバージョンを返していれば released、打たずに済んでいれば（承認されなかった）rejected を記録する。

    まとめたリクエストを閉じる記録と、各スプリントのレーンの行の回答は 1 回のトランザクションで書く。"""
    inputs = _stage_values(environ, "release_requests", "release_plan", "pr")
    version = inputs.get("product_version")
    value = plan.RELEASED if version else plan.REJECTED
    reason = f"版上げのスプリント: #{inputs['pr']} を{'リリースした' if version else '承認されなかった'}"
    release = str(inputs["pr"]) if version else None
    warnings = []
    with store.transaction(st) as conn:
        pid, view = _plan_of(conn, st, cwd, inputs["release_plan"])
        requests = _release_requests(view, inputs["release_requests"])
        pending = [r for r in requests if r.closed is None]
        if pending:
            plan.record_release(conn, pid, [r.key for r in pending], plan.ReleaseAnswer(value, reason, version, release))
        for r in pending:
            sprint = r.body.get("sprint_state")
            if sprint and lane.record_answer(conn, sprint, {"answer": value}) is None:
                warnings.append(f"スプリント {sprint} を持つレーンの行が無く、回答を載せていない")
    summary = f"まとめたリリースの回答（{value}）をリクエスト {len(pending)} 件へ記録した"
    return result.result(TOOL, result.OK, summary, warnings=warnings)


COMMANDS = {"bump-assemble": bump_assemble, "bump-present": bump_present, "bump-record": bump_record}


def main(argv: list[str], io: Optional[Io] = None, environ: Optional[Mapping[str, str]] = None) -> int:
    ap = argparse.ArgumentParser(prog=f"ndf {TOOL}", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)
    for name in COMMANDS:
        sub.add_parser(name)
    command.add_cwd(sub.choices.values())
    env = environ if environ is not None else os.environ
    held = io if io is not None else default_io()

    def act(a: argparse.Namespace, cwd: Path, st: store.Store) -> dict[str, Any]:
        try:
            return COMMANDS[a.action](a, cwd, st, held, env)
        except Stop as exc:
            return result.stopped(TOOL, exc.kind, str(exc))
        except GitHubError as exc:
            return result.stopped(TOOL, "transient" if exc.passing else "precondition", f"GitHub を読み書きできない: {exc}")
        except (git.GitError, branch_strategy.StrategyError, OSError) as exc:
            return result.stopped(TOOL, "precondition", str(exc))

    return command.run_main(ap, lambda a, cwd: command.with_store(cwd, act, a), argv)
