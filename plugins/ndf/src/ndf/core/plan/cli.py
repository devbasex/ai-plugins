"""サブコマンド `plan`: プランの実行の回を流す・待つ・待ちの一覧を出す。

ndf plan next <plan.json> [--from <ステップ>] [--state-dir <保存先>] [--answer <回答> --by <回答した者> [--waiting <待ち>] [--presented <提示>]] [--set <名前>=<値>]... [--fresh]
    回を始めるか続け、終わるか承認の回答を待つ所まで進める。回答を待つ所では承認ゲートの結果（routes）を出して抜ける。
    `--fresh` は前の回の耐久の記録に戻らず、新しい回で先頭のステップから流す
ndf plan queue <plan.json>... [--then <plan.json>...]... [--from <プラン>=<ステップ>]... [--resume] [--fresh] [--queue-dir <保存先>]
    ステップ（並びと `--then` の区切り）ごとに、触るファイルの重ならない組でプランを並べて流す。`--fresh` は子の next へ渡す
ndf plan wait <plan.json>... [--state-dir <保存先>] [--timeout <秒>]
    DBOS を開かずに回のバックアップと待ちの目印を見回り、止まり・承認ゲート・終わりを種類つきで返す（next は続きの待ち）。
    `--state-dir` は `next` に渡した保存先（プラン 1 本のときだけ）
ndf plan pending
    チェックアウトの保存先から、待ちを抱えた（PENDING・ENQUEUED・DELAYED）実行を並べる（B5）
ndf plan check <plan.json>...
    プランの形と、キューの組（触るファイルの重なり・前提）を確かめる
ndf plan units check [--issue N]... [--omitted N]... [<単位のファイル>...] [--docs]
    単位のファイルを 1 つのグラフにして確かめる。`--issue` が無ければ環境変数 `NDF_STAGE_INPUT` の `issues`・`plan_omitted`・
    `worktree` を使う。`NDF_DESIGN_RECORD_DIR` があれば、レビューした HEAD のコミットを読み、通れば `checked.json` を書く
ndf plan units render [--issue N]... [--omitted N]...
    実装計画の「レビューの単位」と「並列と直列」の表を書き直し、続けて `check --docs` をする

結果 JSON を標準出力へ 1 行で出す。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple, NoReturn, Optional

from ndf import identity
from ndf.adapters import github
from ndf.adapters.git import Git, GitError
from ndf.contracts import causes, exit_codes, internal, limits, result
from ndf.contracts.exit_codes import GATE, OK, STOPPED
from ndf.core import actors, durable
from ndf.core.plan import files, inputs_decide, model, queue_decide, queue_run, runner, units_files, wait_decide

TOOL = runner.TOOL
# 待ちの上限の既定と、見回りの間隔の方針（contracts/limits/）
WAIT_DEFAULT = "supervise.wait-default"
WAIT_POLL = "durable.wait-from-poll"
# `units` が読む、工程の入力と承認の記録の保存先（`contracts/domains/env.json`）
STAGE_INPUT_ENV = "NDF_STAGE_INPUT"
RECORD_DIR_ENV = identity.env_name("design-record-dir")
# 保存先のファイル。前の 2 つ（と `units_files.REVIEWED_HEAD`）は設計の Pull Request のレビューが書き、後の 1 つは `units check` が書く
REVIEWED_PR = "reviewed-pr"
REVIEWED_BASE = "reviewed-base"
CHECKED_FILE = "checked.json"
_REVIEWED_PR_FORM = re.compile(r"[^/\s]+/[^#\s]+#([1-9][0-9]*)")
_REVIEW_LAUNCH_HINT = (
    "。workflow.launch.review でレビューの起動を差し替えているときは、起動の後ろの --reviewed-out・--reviewed-pr-out・"
    "--reviewed-base-out・--findings を受けて 3 つのファイルを書き、--reviewed-base-out を受けたら PR の今のマージ先を比較元にする。"
    "または、この設定を削って既定の起動に戻す"
)


def wait_default_seconds() -> float:
    """待ちの上限の既定の秒。"""
    return float(limits.seconds(WAIT_DEFAULT))


def _root() -> Path:
    return durable.store_root(identity.load().name, Path.cwd())


def _url() -> Optional[str]:
    """ステートマシンと同じ DB を使うときの URL（`NDF_PROGRESS_DB`。無ければチェックアウトの保存先の SQLite）。値は秘密として伏せる。"""
    value = os.environ.get("NDF_PROGRESS_DB")
    return value if value else None  # noqa: NDF501 利用者の入力: 空の NDF_PROGRESS_DB は未設定と同じ


def _exit_with(out: dict[str, Any]) -> NoReturn:
    """結果を出し、DBOS を閉じずにプロセスを抜ける（承認の回答を待つ耐久ワークフローをキーのファイルに残す）。"""
    code = result.emit(out)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def _cmd_next(a: argparse.Namespace) -> int:
    if a.by is not None and not actors.ACTORS.match(a.by):
        return result.emit(result.stopped(TOOL, "invalid", actors.invalid_message(a.by)))
    root = _root()
    cwd = Path.cwd()
    try:
        with runner.plan_lock(runner.state_dir_of(Path(a.plan), a.state_dir, cwd)):
            inv = runner.prepare(root, Path(a.plan), a.start, a.state_dir, cwd, runner.RunOptions(_pairs(a.sets, "--set"), a.fresh))
            reply = runner.Reply(a.answer, a.by, a.waiting, a.presented) if a.answer is not None else None
            return result.emit(runner.advance(root, inv, reply, _exit_with, _url()))
    except model.PlanError as exc:
        return result.emit(result.stopped(TOOL, "invalid", str(exc)))
    except durable.StoreStopped as exc:
        return result.emit(exc.stopped(TOOL, kind="path"))


def _pairs(values: Optional[list[str]], flag: str) -> dict[str, str]:
    """`<名前>=<値>` の並びを辞書にする。形が違えば `model.PlanError`。"""
    out: dict[str, str] = {}
    for value in values if values is not None else []:
        name, sep, rest = value.partition("=")
        if not sep or not name or not rest:
            raise model.PlanError(f"{flag} は <名前>=<値> の形にする: {value}")
        out[name] = rest
    return out


def _stages(a: argparse.Namespace, cwd: Path) -> list[list[queue_run.Entry]]:
    """引数からキューのステップを作る。プランは絶対のパスにし、`--from` の開始点を付ける。"""
    starts = _pairs(a.starts, "--from")
    groups = [a.plans, *(a.then if a.then is not None else [])]
    stages = [[queue_run.Entry((cwd / p).resolve(), model.load(cwd / p)) for p in group] for group in groups]
    names = [e.plan.name for stage in stages for e in stage]
    unknown = sorted(set(starts) - set(names))
    if unknown:
        raise model.PlanError(f"--from のプランがキューに無い: {', '.join(unknown)}")
    if len(set(names)) != len(names):
        raise model.PlanError("キューに同じ名前のプランが 2 本ある")
    return [[queue_run.Entry(e.path, e.plan, starts.get(e.plan.name)) for e in stage] for stage in stages]


def _cmd_queue(a: argparse.Namespace) -> int:
    cwd = Path.cwd()
    try:
        stages = _stages(a, cwd)
        qdir = queue_run.queue_dir(stages[0][0].path, a.queue_dir, cwd)
        stable = queue_run.move_to_stable(cwd)
    except model.PlanError as exc:
        return result.emit(result.stopped(TOOL, "invalid", str(exc)))
    except subprocess.CalledProcessError as exc:
        return result.emit(result.stopped(TOOL, "precondition", f"チェックアウトの根を引けない: {exc}"))
    if a.max is not None and a.max < 1:
        return result.emit(result.stopped(TOOL, "invalid", f"--max は 1 以上（入力 {a.max}）"))
    outcomes = queue_run.run(stages, qdir, stable, queue_run.QueueOptions(a.resume, a.fresh, a.max))
    return result.emit(queue_run.queue_result(TOOL, outcomes, stages, qdir, a.max))


def _watched(path: Path, state_dir: Optional[str] = None) -> wait_decide.Watched:
    """1 本のプランの回の今を、DBOS を開かずにバックアップと目印から読む。"""
    name = path.stem
    try:
        plan = model.load(path)
    except model.PlanError as exc:
        return wait_decide.Watched(name, STOPPED, "invalid", str(exc))
    sdir = runner.resolve_state_dir(path, state_dir, plan, Path.cwd())
    record = files.read_json(sdir / runner.RUN_FILE)
    if record is None:
        return wait_decide.Watched(plan.name, None)
    run = durable.run_name(record["key"])
    try:
        done = runner.stored_result(sdir, run)
    except internal.InternalError as exc:
        return wait_decide.Watched(plan.name, STOPPED, exc.kind, str(exc))
    if done is not None:
        cause = done.get("cause") if isinstance(done.get("cause"), dict) else {}
        return wait_decide.Watched(plan.name, done["status"], cause.get("kind"), cause.get("detail"), done.get("routes"))
    marker = runner.read_marker(sdir, run)
    if marker is not None:
        # 資料と回答コマンドは、目印に入れた同じ提示の一組から作る（別々に読んだ写しを混ぜない）
        shown = runner.presentation_fields(marker)
        return wait_decide.Watched(plan.name, GATE, routes=runner.presented_routes(marker), shown=shown)
    return wait_decide.Watched(plan.name, None)


class Poll(NamedTuple):
    """待ちの読み直しの時計・待ち方・間隔（秒）。"""

    monotonic: Callable[[], float]
    sleep: Callable[[float], None]
    interval: float


def _poll(read: Callable[[], list[wait_decide.Watched]], timeout: float, again: dict[str, Any], poll: Poll) -> dict[str, Any]:
    """`read` で状態を読み、確定するか `timeout` 秒が過ぎるまで `poll.interval` 秒ごとに読み直して、結果を返す。

    確定の判定を期限の比較より先に行う（期限を過ぎた後の読みでも、確定していれば確定の結果を返す）。
    """
    began = poll.monotonic()
    while True:
        found = read()
        if wait_decide.settled(found):
            return wait_decide.attention(TOOL, found, again)
        waited = poll.monotonic() - began
        if waited >= timeout:
            return wait_decide.timed_out(TOOL, found, waited, again)
        poll.sleep(poll.interval)


def _cmd_wait(a: argparse.Namespace) -> int:
    paths = [Path(p) for p in a.plans]
    if a.state_dir is not None and len(paths) != 1:
        return result.emit(result.stopped(TOOL, "invalid", "--state-dir はプラン 1 本のときだけ渡せる"))
    given = ["--state-dir", a.state_dir] if a.state_dir is not None else []
    again = result.command(TOOL, "wait", *a.plans, *given, "--timeout", str(a.timeout))
    interval = limits.value(WAIT_POLL, "interval")
    poll = Poll(monotonic=time.monotonic, sleep=time.sleep, interval=interval)
    return result.emit(_poll(lambda: [_watched(p, a.state_dir) for p in paths], a.timeout, again, poll))


def _cmd_pending(_: argparse.Namespace) -> int:
    url = _url()
    try:
        found = durable.pending(url if url is not None else _root())
    except durable.StoreStopped as exc:
        return result.emit(exc.stopped(TOOL, kind="path"))
    items = [
        {
            "kind": "run",
            "run": r.run_name,
            "workflow": r.workflow_id,
            "status": r.status,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in found
    ]
    return result.emit(result.result(TOOL, OK, f"待ちを抱えた実行が {len(items)} 件ある", items, {"pending": len(items)}))


def _cmd_check(a: argparse.Namespace) -> int:
    plans, errs = [], []
    for p in a.plans:
        try:
            plans.append(model.load(Path(p)))
        except model.PlanError as exc:
            errs.append({"kind": "violation", "reason": str(exc)})
    if errs:
        return result.emit(result.stopped(TOOL, "invalid", f"プランの誤りが {len(errs)} 件ある", items=errs))
    waves = queue_decide.waves([queue_decide.Queued(p.name, p.touches, p.needs) for p in plans])
    items = [{"kind": "wave", "plans": w} for w in waves]
    return result.emit(
        result.result(TOOL, OK, f"{len(plans)} 本のプランを {len(waves)} 組で流せる", items, {"plans": len(plans), "waves": len(waves)})
    )


class _UnitsStopped(Exception):
    """`units` を止める理由。`kind` は停止理由の語。"""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind


class _StageInput(NamedTuple):
    """`NDF_STAGE_INPUT` のうち `units` が使うキー。工程の外で打てばすべて空。"""

    issues: tuple[int, ...] = ()
    worktree: str = ""
    plan_omitted: tuple[int, ...] = ()
    branch: Optional[str] = None
    design_pr: Optional[int] = None


class _Targets(NamedTuple):
    """確かめる対象。`paths`（直に指したファイル。根からの相対）か `issues` のどちらか。`inputs` は工程の入力。"""

    root: Path
    issues: tuple[int, ...]
    omitted: tuple[int, ...]
    paths: tuple[str, ...]
    inputs: _StageInput = _StageInput()


class _Record(NamedTuple):
    """承認の記録の保存先と、そこから読んだレビューした HEAD と PR の番号。"""

    path: Path
    head: str
    pr: int


def _hub(root: Path) -> github.GitHub:
    """作業場所のリポジトリの GitHub。テストが差し替える。"""
    return github.GitHub(Git(root).remote_repo(), github.client())


def _criteria_counts(root: Path, issues: tuple[int, ...]) -> dict[int, int]:
    """課題ごとの受け入れ条件の数（本文を 1 回の一括読み取りで読み、数えるためだけに使う）。"""
    if not issues:
        return {}
    bodies = _hub(root).nodes(issues, "issue", "body")
    unread = [n for n in issues if n not in bodies or not isinstance(bodies[n].get("body"), str)]
    if unread:
        raise _UnitsStopped("precondition", f"課題 {', '.join(f'#{n}' for n in unread)} の本文を読めない")
    return {n: len(inputs_decide.criteria(bodies[n]["body"])) for n in issues}


def _repo_root(cwd: Path) -> Path:
    return Path(Git(cwd).run("rev-parse", "--show-toplevel").strip())


def _positive_ints(held: Any) -> bool:
    return isinstance(held, list) and all(type(n) is int and n > 0 for n in held)


def _stage_input(raw: str) -> _StageInput:
    """`NDF_STAGE_INPUT` の値。`units` が使うキーの型を確かめる。"""
    try:
        held = json.loads(raw)
    except json.JSONDecodeError:
        raise _UnitsStopped("invalid", f"環境変数 {STAGE_INPUT_ENV} を JSON として読めない") from None
    forms = dict(
        issues=lambda v: _positive_ints(v) and bool(v),
        worktree=lambda v: isinstance(v, str) and Path(v).is_absolute() and Path(v).is_dir(),
        plan_omitted=_positive_ints,
        branch=lambda v: isinstance(v, str) and bool(v),
        design_pr=lambda v: type(v) is int and v > 0,
    )
    if not isinstance(held, dict):
        raise _UnitsStopped("invalid", f"環境変数 {STAGE_INPUT_ENV} は JSON の object にする")
    bad = [key for key, fits in forms.items() if key in held and not fits(held[key])]
    bad += [key for key in ("issues", "worktree") if key not in held]
    if bad:
        raise _UnitsStopped("invalid", f"環境変数 {STAGE_INPUT_ENV} のキーが無いか形が違う: {', '.join(bad)}")
    return _StageInput(**{key: tuple(held[key]) if isinstance(held[key], list) else held[key] for key in forms if key in held})


def _relative(root: Path, cwd: Path, given: str) -> str:
    """引数のファイルを根からの相対パスにする。ファイル自体のリンクは解かない（読み手が断る）。"""
    full = cwd / given
    try:
        return (Path(os.path.realpath(full.parent)) / full.name).relative_to(root.resolve()).as_posix()
    except ValueError:
        raise _UnitsStopped("invalid", f"{given}: リポジトリの中のファイルでない") from None


def _targets(a: argparse.Namespace, cwd: Path) -> _Targets:
    issues = tuple(sorted(set(a.issues))) if a.issues is not None else ()
    omitted = tuple(a.omitted) if a.omitted is not None else ()
    given = a.files if a.units_cmd == "check" else []
    if given:
        if issues or omitted:
            raise _UnitsStopped("invalid", "単位のファイルを直に指すときは --issue・--omitted を渡さない")
        root = _repo_root(cwd)
        return _Targets(root, (), (), tuple(_relative(root, cwd, f) for f in given))
    if issues:
        return _Targets(_repo_root(cwd), issues, omitted, ())
    raw = os.environ.get(STAGE_INPUT_ENV) if a.units_cmd == "check" else None
    if raw is None or omitted:
        raise _UnitsStopped("invalid", "確かめる課題が無い（--issue を渡す）")
    held = _stage_input(raw)
    return _Targets(Path(held.worktree), tuple(sorted(set(held.issues))), held.plan_omitted, (), held)


def _held(path: Path) -> str:
    """保存先のファイルの中身。無いか読めなければ空。"""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):  # noqa: NDF401 ファイル: 無い・読めないものは、足りないファイルとして名前を挙げる
        return ""


def _record(path: Path, targets: _Targets) -> _Record:
    """保存先を読み、レビューの後に対象が変わっていないことを確かめる。前の `checked.json` は始めに消す。"""
    (path / CHECKED_FILE).unlink(missing_ok=True)
    if targets.paths:
        raise _UnitsStopped("invalid", f"{RECORD_DIR_ENV} があるときは、単位のファイルを直に指さず課題で確かめる")
    held = {name: _held(path / name) for name in (units_files.REVIEWED_HEAD, REVIEWED_PR, REVIEWED_BASE)}
    pr = _REVIEWED_PR_FORM.fullmatch(held[REVIEWED_PR])
    lack = [name for name, value in held.items() if not value or (name == REVIEWED_PR and pr is None)]
    if lack:
        launch = units_files.project_of(units_files.Tree(targets.root)).get("workflow")
        launch = launch.get("launch") if isinstance(launch, dict) else None
        hint = _REVIEW_LAUNCH_HINT if isinstance(launch, dict) and "review" in launch else ""
        raise _UnitsStopped("precondition", f"{path} に読めるレビューの記録が無い: {', '.join(lack)}{hint}")
    now = Git(targets.root).run("rev-parse", "HEAD").strip()
    head = held[units_files.REVIEWED_HEAD]
    if now != head:
        raise _UnitsStopped(runner.APPROVAL_STALE, f"作業場所の HEAD（{now}）がレビューした HEAD（{head}）と違う")
    assert pr is not None
    number = int(pr.group(1))
    given = targets.inputs.design_pr
    if given is not None and given != number:
        raise _UnitsStopped("refused", f"入力の設計の PR（#{given}）がレビューした PR（#{number}）と違う")
    if targets.inputs.branch is None:
        raise _UnitsStopped("precondition", f"入力にスプリントブランチ（{STAGE_INPUT_ENV} の branch）が無い")
    return _Record(path, head, number)


def _checked_units(a: argparse.Namespace, targets: _Targets, record: Optional[_Record], docs: bool) -> dict[str, Any]:
    """確かめの結果。`record` があればレビューした HEAD のコミットを読み、通れば `checked.json` を書く。"""
    tree = units_files.Tree(targets.root, None if record is None else record.head)
    limits_held = units_files.limits_of(units_files.project_of(tree))
    if targets.paths:
        loaded = [units_files.load(tree, path) for path in targets.paths]
        counts = _criteria_counts(targets.root, units_files.issues_referred(loaded))
        report = units_files.check_files(tree, loaded, limits_held, counts, docs)
    else:
        counts = _criteria_counts(targets.root, targets.issues)
        report = units_files.check(tree, targets.issues, targets.omitted, limits_held, counts, docs)
    bad = [{"kind": "violation", **found._asdict()} for found in units_files.findings(report)]
    warnings = [f"{w.rule} {w.unit}: {w.reason}" for w in report.judgement.warnings]
    metrics = {"units": len(report.judgement.views), "violations": len(bad), "warnings": len(warnings)}
    splits = units_files.split_items(report)
    if bad:
        summary = f"単位の確かめの違反が {len(bad)} 件ある（最初: {bad[0]['rule']} {bad[0]['reason']}）"
        return result.stopped(TOOL, "invalid", summary, items=bad + splits, metrics=metrics, warnings=warnings)
    units = units_files.unit_items(report)
    summary = f"{len(units)} 個の単位の確かめを通った（警告 {len(warnings)} 件）"
    outputs = None
    if record is not None:
        handled = units_files.handlings(tree, targets.issues)
        files.write_json(
            record.path / CHECKED_FILE,
            dict(
                head=record.head,
                branch=targets.inputs.branch,
                issues=list(targets.issues),
                handling={str(n): dict(handling=handling, into=into) for n, (handling, into) in handled.items()},
                omitted=[f.issues[0] for f in report.files if f.whole],
                limits=limits_held._asdict(),
                criteria={str(n): count for n, count in counts.items()},
                units=units,
            ),
        )
        outputs = dict(design_pr=record.pr)
    return result.result(TOOL, OK, summary, units + splits, metrics, warnings=warnings, outputs=outputs)


def _render(targets: _Targets) -> None:
    """実装計画の 2 つの表を書き直す。"""
    tree = units_files.Tree(targets.root)
    limits_held = units_files.limits_of(units_files.project_of(tree))
    for plan, text in units_files.rendered(tree, targets.issues, targets.omitted, limits_held).items():
        (targets.root / plan).write_text(text, encoding="utf-8", newline="\n")


def _units(a: argparse.Namespace) -> dict[str, Any]:
    targets = _targets(a, Path.cwd())
    if a.units_cmd == "render":
        _render(targets)
        return _checked_units(a, targets, None, docs=True)
    held = os.environ.get(RECORD_DIR_ENV)
    record = None if held is None else _record(Path(held), targets)
    return _checked_units(a, targets, record, a.docs)


def _cmd_units(a: argparse.Namespace) -> int:
    try:
        return result.emit(_units(a))
    except _UnitsStopped as exc:
        return result.emit(result.stopped(TOOL, exc.kind, str(exc)))
    except units_files.UnitsFileError as exc:
        return result.emit(result.stopped(TOOL, "invalid", str(exc)))
    except github.GitHubError as exc:
        return result.emit(result.stopped(TOOL, "transient" if exc.passing else "precondition", f"GitHub を読めない: {exc}"))
    except (GitError, OSError) as exc:
        return result.emit(result.stopped(TOOL, "precondition", str(exc)))


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=TOOL, description="プランの実行の回を流す・待つ")
    sub = p.add_subparsers(dest="cmd", required=True)
    nx = sub.add_parser("next", help="回を始めるか続ける")
    nx.add_argument("plan")
    nx.add_argument("--from", dest="start")
    nx.add_argument("--state-dir")
    nx.add_argument("--answer")
    nx.add_argument("--by", help="回答した者（human・conductor・organizer か service:<主体>）。--answer と組で渡す")
    nx.add_argument(runner.WAITING_FLAG, help="回答する待ち（<ステップ>:<待ちの番号>）。返された routes が持つ。今の待ちと違えば記録しない")
    nx.add_argument(runner.PRESENTED_FLAG, help="回答する提示の識別子。返された routes が持つ。今の提示と違えば記録しない")
    nx.add_argument("--set", dest="sets", action="append")
    nx.add_argument("--fresh", action="store_true")
    nx.set_defaults(fn=_cmd_next)
    qu = sub.add_parser("queue", help="ステップごとにプランを並べて流す")
    qu.add_argument("plans", nargs="+")
    qu.add_argument("--then", action="append", nargs="+")
    qu.add_argument("--from", dest="starts", action="append")
    qu.add_argument("--resume", action="store_true")
    qu.add_argument("--fresh", action="store_true")
    qu.add_argument("--max", type=int, help="同時に流すプランの本数の上限（無ければ触るファイルの重ならない組の本数まで）")
    qu.add_argument("--queue-dir")
    qu.set_defaults(fn=_cmd_queue)
    wt = sub.add_parser("wait", help="流れている回を見回る")
    wt.add_argument("plans", nargs="+")
    wt.add_argument("--state-dir")
    wt.add_argument("--timeout", type=float, default=wait_default_seconds())
    wt.set_defaults(fn=_cmd_wait)
    pd = sub.add_parser("pending", help="待ちを抱えた実行を並べる")
    pd.set_defaults(fn=_cmd_pending)
    ck = sub.add_parser("check", help="プランの形とキューの組を確かめる")
    ck.add_argument("plans", nargs="+")
    ck.set_defaults(fn=_cmd_check)
    us = sub.add_parser("units", help="レビューの単位を確かめ、実装計画の表を書く").add_subparsers(dest="units_cmd", required=True)
    uc = us.add_parser("check", help="単位のファイルを確かめる")
    ur = us.add_parser("render", help="実装計画の表を書き直して確かめる")
    for one in (uc, ur):
        one.add_argument("--issue", dest="issues", type=int, action="append", help="スプリントの課題の番号")
        one.add_argument("--omitted", type=int, action="append", help="実装計画を省いた課題の番号")
        one.set_defaults(fn=_cmd_units)
    uc.add_argument("files", nargs="*", help="単位のファイルを直に指す（--issue と併せて渡さない）")
    uc.add_argument("--docs", action="store_true", help="実装計画のタスクの集合と 2 つの表も突き合わせる")
    return p


def main(argv: list[str]) -> int:
    try:
        a = _parser().parse_args(argv)
    except SystemExit as exc:
        return causes.exit_code("invalid") if exc.code else exit_codes.code_of("ok")
    return a.fn(a)
