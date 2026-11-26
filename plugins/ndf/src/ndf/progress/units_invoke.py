"""工程「実装」を実装計画のレビューの単位ごとに流す処理（#4369 の詳細設計の「単位を流す処理」）。

ここにあるのは、開始前の確かめ（`prepare`）、打ち直しで単位の回をどう続けるかの判定（`round_state`・`resume_of`）と、
単位を順に流す処理（`run_stage`。作業場所の用意・`impl`・マージ・スプリントの PR）である。
開始前の確かめは、作業場所が今いるブランチをリモートから取得し、コミットされた実装計画に単位の見出しがあるかで経路を決める。
単位の経路なら、手元をリモートに揃え、表をすべて読み、ブランチをリモートへ送ってから並びを `units.json` に書く。
読み方と順序は設計書の「開始前の確かめ」、打ち直しの表は詳細設計書の「`resume_of` の判定表」にある。

`ndf.progress.invoke` を import しない（`invoke` がこのモジュールを呼ぶので、型は `plan_runs` が持つ）。プランを流すのと
コマンドを打つのは `invoke` が渡す `Ports` を通す。`domains/` を import できないので、作業場所の用意は `ndf worktree create`、
マージは `ndf ci merge` を子プロセスで打つ。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import contracts, identity
from ndf.adapters import github, github_pulls
from ndf.adapters.git import HEADS, Git, mask_urls
from ndf.cli import ndf_command
from ndf.contracts import facts, result, worker_paths
from ndf.core import branch_strategy, json_pointer, route
from ndf.core.issue_docs import plan_issues
from ndf.core.plan import files as plan_files
from ndf.core.plan import inputs_decide
from ndf.core.plan import runner as plan_runner
from ndf.core.plan.review_units_decide import PlanDoc, Unit, UnitsUnreadable, acceptance_args, has_units, lineup
from ndf.progress import issue_values, plan_runs, refactor_values
from ndf.progress.command import PROJECT_FILE, ndf_settings_in
from ndf.progress.issue_values import UNITS_DIR, UNITS_FILE
from ndf.progress.plan_runs import StageResult, StageRun, UnitsStopped
from ndf.testing import scoped

# 単位の回の保存先のファイル（`reviewed-head` は `impl` のプラン、`stopped-head`・`base-head`・`merge.json` は単位を流す処理が書く）。
# `base-head` は、その回の実装の前に単位の作業場所が取り込んだスプリントブランチの HEAD（スプリントの PR の前の照合に使う）
REVIEWED_HEAD_FILE = "reviewed-head"
STOPPED_HEAD_FILE = "stopped-head"
BASE_HEAD_FILE = "base-head"
MERGE_FILE = "merge.json"
# 打ち直しの判定の、レビューのステップ（ほかのステップと停止理由の語は `resume_of` が持つ。定数にすると同じ語の別の事実と重なる）
REVIEW_STEPS = frozenset({"review", "review-delta", "resolved"})
# 単位の PR で待つチェックの設定のキー（`workflow` の下。書かなければ `ci.required_checks` を読む。#4369 の決定 13）
UNIT_CHECKS = "unit_required_checks"
# `git ls-remote --exit-code` が、該当する ref が無いときに返す終了コード
LS_REMOTE_ABSENT = 2
# 単位のブランチ名に付ける、単位の名前の sha256 の先頭の字数
UNIT_HASH_CHARS = 8
# 単位の回の保存先の、PR の番号（`impl` の `pr` のステップが書く）と単位の範囲の指示（`unit_run` が書く）のファイル
PR_NUMBER_FILE = Path(route.PR_NUMBER_FILE).name
UNIT_FILE = "unit.md"
# 単位の PR で待つチェックが空のとき、`impl` から外すステップ（`ci` と、その失敗からしか行かないもの。#4369 の決定 13）
CI_STEPS = ("ci", "repeat-check", "ci-fix", "ci-local", "push-fixed")
# スプリントの PR を開くプランの保存先（工程の保存先の下）と、そのプランから外すステップ（単位の変更はマージで送り済み）
SPRINT_PR_DIR = "sprint-pr"
SPRINT_PR_DROP = ("push", "fix-push")
# 単位のプランの子へ渡す、`gh` の対象のリポジトリと単位の範囲の指示のファイル（`contracts/domains/env.json`）
GH_REPO_ENV = identity.env_name("gh-repo")
UNIT_FILE_ENV = identity.env_name("unit-file")


class Lineup(NamedTuple):
    """単位の経路の並び。`unit_ci` は単位の PR のマージ先（`base`）・待つチェックの名前（`required`）・その名前を読んだ設定のキー（`key`）。"""

    units: tuple[Unit, ...]
    unit_ci: dict[str, Any]


class RoundState(NamedTuple):
    """単位の、プランの実行の記録（`run.json`）を持つ最新の回。`ran` が偽なら回が無く、`round_dir` は 1 回目の保存先。

    `done` はその回に保存した結果（終わっていなければ None）、`merge` は `merge.json` の結果の種類、`reviewed` は
    `reviewed-head`、`stopped_head` は `stopped-head` の値（無ければどれも None）。
    """

    round_dir: Path
    ran: bool
    done: Optional[dict[str, Any]]
    merge: Optional[str]
    reviewed: Optional[str]
    stopped_head: Optional[str]


class Resume(NamedTuple):
    """打ち直しの扱い。`action` は `run`（impl を流す）か `merge`（マージだけを打ち直す）、`new_round` は次の回の保存先を使うか、
    `start` は新しい回の `--from`（None なら最初からか、プランの打ち直しに任せる）。"""

    action: str
    new_round: bool
    start: Optional[str]


class Ports(NamedTuple):
    """単位を流す処理が外へ出す呼び出し（`invoke` が渡し、テストが差し替える）。

    `call_plan` は呼び出しを保存先でプランとして流し、`command` は argv を作業ディレクトリと環境で打つ。
    """

    call_plan: Callable[[StageRun, Path], StageResult]
    command: Callable[[list[str], Path, dict[str, str]], "subprocess.CompletedProcess[str]"]


class Context(NamedTuple):
    """単位を流す間に変わらない値。`sprint` はスプリントの作業場所、`branch` はスプリントブランチ、`repo` は選んだリモートの
    `<所有者>/<リポジトリ>`、`review` は単位の PR のレビューの起動（`--pr` を持たない）、`local_tests` は手元のテストを打つか。"""

    run: StageRun
    work_dir: Path
    sprint: Path
    branch: str
    remote: str
    repo: str
    unit_ci: dict[str, Any]
    review: tuple[str, ...]
    local_tests: bool


# --- 開始前の確かめ -----------------------------------------------------------------------------


def prepare(run: StageRun, work_dir: Path, cwd: Path) -> Optional[Lineup]:
    """設計書の「開始前の確かめ」を行い、単位の経路なら並びを `work_dir/units.json` に書いて返す。これまでの経路なら None。

    これまでの経路では `units.json` に経路だけを書き、作業場所は変えない。止めるときは `UnitsStopped`（`units.json` を書かない）。
    git と GitHub の失敗のうちここで分けないものは、そのまま上げる（呼び手の `invoke.plan_failure` が分ける）。
    """
    location = _work_location(run, cwd)
    g = Git(location)
    root = Path(g.run("rev-parse", "--show-toplevel").strip())
    issues = sorted({int(n) for n in run.inputs["issues"]})
    branch = _current_branch(g)
    remote = g.remote()
    fetched = _fetched_tip(g, remote, branch) if branch is not None else None
    uncommitted = _uncommitted_plans(g, issues)
    # 各コミットの実装計画は 1 度だけ読む（手元と取得先が同じコミットなら取得先を読まず、進めた後も読み直さない）
    head, tip = fetched if fetched is not None else ("HEAD", None)
    at_head = _plans_at(g, head, issues)
    at_tip = at_head if tip is None or tip == head else _plans_at(g, tip, issues)
    seen = [*at_head, *(at_tip if at_tip is not at_head else []), *_worktree_plans(root, uncommitted)]
    if not any(has_units(p.text) for p in seen):
        return _plain_route(work_dir)
    if uncommitted:
        detail = f"未コミットの実装計画がある: {', '.join(uncommitted)}。コミットするか取り消してから打ち直す"
        raise UnitsStopped("precondition", detail)
    project = ndf_settings_in(root, PROJECT_FILE)
    branch = _checked_branch(run, branch, project)
    moved = tip is not None and _catch_up(g, remote, branch, head, tip)
    plans = at_tip if moved else at_head
    if not any(has_units(p.text) for p in plans):
        return _plain_route(work_dir)
    if moved:
        # 単位の経路を続けるときだけ、進めた先のコミットの設定でリリース先（I4）と単位の PR で待つチェックを決め直す
        project = ndf_settings_in(root, PROJECT_FILE)
        branch = _checked_branch(run, branch, project)
    units = _read_units(g, remote, plans, issues)
    pushed = g.attempt("push", "-q", "--no-follow-tags", "--", remote, f"HEAD:{HEADS}{branch}")
    if pushed.returncode != 0:
        raise UnitsStopped("precondition", f"{remote} の {branch} へ送れない: {mask_urls(pushed.stderr.strip())}")
    found = Lineup(units, _unit_ci(project, branch))
    print(
        f"ndf progress: 単位の PR のマージ先 {branch}、待つチェック {', '.join(found.unit_ci['required']) or '無し'}"
        f"（{found.unit_ci['key']}）",
        file=sys.stderr,
    )
    plan_files.write_json(work_dir / UNITS_FILE, {"route": "units", "units": [_unit_json(u) for u in units], "unit_ci": found.unit_ci})
    return found


def _work_location(run: StageRun, cwd: Path) -> Path:
    """入力の作業場所（`worktree`）。無ければ打った場所。"""
    held = run.inputs["worktree"] if "worktree" in run.inputs else None
    return Path(held) if isinstance(held, str) and held else cwd


def _current_branch(g: Git) -> Optional[str]:
    """作業場所が今いるブランチ。ブランチに居なければ None。"""
    done = g.attempt("branch", "--show-current")
    name = done.stdout.strip()
    return name if done.returncode == 0 and name != "" else None


def _fetched_tip(g: Git, remote: str, branch: str) -> Optional[tuple[str, str]]:
    """手元の HEAD と、リモートから取得したブランチの HEAD の SHA（1 回の起動で解決する）。

    リモートにブランチが無いと確かめられれば None、ほかの失敗は止める。
    """
    fetched = g.fetch_branch(remote, branch)
    if fetched.returncode == 0:
        head, tip = g.commits_of("HEAD", f"refs/remotes/{remote}/{branch}")
        return head, tip
    probe = g.attempt("ls-remote", "--exit-code", "--heads", remote, f"{HEADS}{branch}")
    if probe.returncode == LS_REMOTE_ABSENT:
        return None
    raise UnitsStopped("precondition", f"{remote} の {branch} を取得できない: {mask_urls(fetched.stderr.strip() or probe.stderr.strip())}")


def _is_sprint_plan(path: str, issues: Sequence[int]) -> bool:
    """スプリントの課題の実装計画のパスか（名前の番号のどれかがスプリントの課題）。"""
    return bool(plan_issues(path) & set(issues))


def _plans_at(g: Git, commit: str, issues: Sequence[int]) -> list[PlanDoc]:
    """コミットにあるスプリントの課題の実装計画。"""
    names = [
        p for p in g.run("ls-tree", "-z", "--name-only", commit, "--", f"{facts.value('locations', 'issues', 'path')}/").split("\0") if p
    ]
    paths = [p for p in names if _is_sprint_plan(p, issues)]
    blobs = g.blobs(*(f"{commit}:{p}" for p in paths)) if paths else []
    return [PlanDoc(p, plan_issues(p), b.decode("utf-8")) for p, b in zip(paths, blobs) if b is not None]


def _uncommitted_plans(g: Git, issues: Sequence[int]) -> list[str]:
    """変更・削除がコミットされていないか、追跡されていない、スプリントの課題の実装計画のパス。"""
    return sorted({p for p in g.status_paths() if _is_sprint_plan(p, issues)})


def _worktree_plans(root: Path, paths: Sequence[str]) -> list[PlanDoc]:
    """worktree にある未コミットの実装計画（削除されたものは読まない）。"""
    return [PlanDoc(p, plan_issues(p), (root / p).read_text(encoding="utf-8")) for p in paths if (root / p).is_file()]


def _plain_route(work_dir: Path) -> None:
    plan_files.write_json(work_dir / UNITS_FILE, {"route": "plain", "units": []})
    return None


def _pointed(section: Mapping[str, Any], pointer: str) -> Any:
    """設定の JSON Pointer の値。無いキーの並びは空の並び（`branch_strategy.get` と違い止めない。スキーマが任意とするキーである）。"""
    found, value = json_pointer.resolve(section, pointer)
    return value if found else []


def released_branches(section: Mapping[str, Any]) -> frozenset[str]:
    """ブランチ戦略の設定がリリース先に挙げたブランチ（`branches.base`・`branches.production`・`channels[].ref`・
    `release.deliveries[].branch`）。単位をマージしてはいけない先である（I4）。"""
    strategy = branch_strategy.Strategy(dict(section))
    found = {strategy.base, strategy.production}
    found |= {c["ref"] for c in _pointed(section, "/channels")}
    found |= {d["branch"] for d in _pointed(section, "/release/deliveries") if "branch" in d and isinstance(d["branch"], str)}
    return frozenset(found)


def _checked_branch(run: StageRun, branch: Optional[str], project: Mapping[str, Any]) -> str:
    """作業場所が今いるブランチが、登録されたスプリントブランチで、リリース先でないことを確かめて返す（I4）。"""
    registered = run.inputs["branch"] if "branch" in run.inputs else None
    if not isinstance(registered, str) or not registered:
        raise UnitsStopped(
            "precondition", "スプリントに登録されたブランチ（文脈の branch）が無い。工程「作業場所の用意」を流してから打ち直す"
        )
    if branch != registered:
        detail = f"作業場所のブランチ {branch or '（ブランチに居ない）'} が、スプリントに登録されたブランチ {registered} と違う。{registered} へ切り替えてから打ち直す"
        raise UnitsStopped("precondition", detail)
    try:
        released = released_branches(branch_strategy.of_project(project).section)
    except contracts.ContractError as exc:
        raise UnitsStopped("precondition", f"ブランチ戦略の設定を読めない: {exc}") from None
    if branch in released:
        detail = (
            f"ブランチ {branch} は {PROJECT_FILE} の branch_strategy がリリース先に挙げている。単位はスプリントブランチの作業場所で流す"
        )
        raise UnitsStopped("precondition", detail)
    return registered


def _catch_up(g: Git, remote: str, branch: str, head: str, tip: str) -> bool:
    """手元（`head`）が遅れていればリモートの HEAD（`tip`）まで進め、進めたかを返す。分かれていれば止める（強制しない）。

    進める先は確かめた `tip` の SHA である（追跡の ref は別の作業場所の fetch で動くので、検査と違うコミットへ進めない）。
    """
    if head == tip or g.is_ancestor(tip, head):
        return False
    if not g.is_ancestor(head, tip):
        detail = f"手元の {branch}（{head}）と {remote} の {branch}（{tip}）が分かれている。揃えてから打ち直す"
        raise UnitsStopped("precondition", detail)
    done = g.merge_ff(tip)
    if done.returncode != 0:
        raise UnitsStopped("precondition", f"{branch} を {remote} の HEAD へ進められない: {mask_urls(done.stderr.strip())}")
    return True


def _read_units(g: Git, remote: str, plans: Sequence[PlanDoc], issues: Sequence[int]) -> tuple[Unit, ...]:
    """課題の本文から受け入れ条件の数を数え、すべての実装計画の表を読む。本文は数えるためだけに使い、ほかへ渡さない。"""
    hub = github.GitHub(g.remote_repo(remote), github.client())
    bodies = hub.nodes(issues, "issueOrPullRequest", github.BODY_FIELDS)
    counts = {n: _criteria_count(bodies[n]) for n in issues}
    try:
        return lineup(plans, counts, issues)
    except UnitsUnreadable as exc:
        raise UnitsStopped("invalid", "実装計画のレビューの単位を読めない: " + "; ".join(exc.reasons)) from None


def _criteria_count(node: Mapping[str, Any]) -> int:
    """課題の本文の受け入れ条件の数（`ndf loop acceptance check` と同じ数え方）。本文が無ければ 0。"""
    body = node["body"] if "body" in node else None
    return len(inputs_decide.criteria(body)) if isinstance(body, str) else 0


def _unit_ci(project: Mapping[str, Any], branch: str) -> dict[str, Any]:
    """単位の PR で待つチェックと、それを読んだ設定のキー（決定 13）。`workflow.unit_required_checks` が無ければ `ci.required_checks`。"""
    workflow = project["workflow"] if isinstance(project.get("workflow"), Mapping) else {}
    if UNIT_CHECKS in workflow:
        return {"base": branch, "required": list(workflow[UNIT_CHECKS]), "key": f"workflow.{UNIT_CHECKS}"}
    section, key = refactor_values.REQUIRED_CHECKS
    ci = project[section] if isinstance(project.get(section), Mapping) else {}
    held = ci[key] if key in ci else []
    return {"base": branch, "required": list(held) if isinstance(held, list) else [], "key": ".".join(refactor_values.REQUIRED_CHECKS)}


def _unit_json(unit: Unit) -> dict[str, Any]:
    return {**unit._asdict(), "tasks": list(unit.tasks), "criteria": [list(c) for c in unit.criteria]}


def unit_branch(sprint: str, unit: Unit) -> str:
    """単位のブランチ名 `<スプリントブランチ>-u<代表の課題>-<単位の名前の sha256 の先頭 8 字>`。"""
    digest = hashlib.sha256(unit.name.encode("utf-8")).hexdigest()[:UNIT_HASH_CHARS]
    return f"{sprint}-u{unit.issue}-{digest}"


# --- 打ち直しの判定 -----------------------------------------------------------------------------


def _round_number(path: Path) -> Optional[int]:
    return int(path.name) if path.is_dir() and path.name.isdigit() else None


def _saved_head(path: Path) -> Optional[str]:
    return path.read_text(encoding="utf-8").strip() if path.is_file() else None


def _own_records(round_dir: Path) -> tuple[Optional[str], Optional[str]]:
    """この処理が書いた `merge.json` の結果の種類と `stopped-head` の値。呼んだ利用者が持ち、ほかが書き込めないものだけを読む。"""
    with worker_paths.open_or_create(round_dir) as wd:
        merged, stopped = (worker_paths.read_file(wd, n, owned=True) for n in (MERGE_FILE, STOPPED_HEAD_FILE))
    try:
        kind = json.loads(merged).get("kind") if merged is not None else None
    except (ValueError, AttributeError):
        kind = None
    return (kind if isinstance(kind, str) else None), (stopped.strip() if stopped is not None else None)


def _saved_base(round_dir: Path) -> Optional[str]:
    """この処理が書いた `base-head` の値（呼んだ利用者が持ち、ほかが書き込めないものだけを読む）。無ければ None。"""
    if not round_dir.is_dir():
        return None
    with worker_paths.open_or_create(round_dir) as wd:
        held = worker_paths.read_file(wd, BASE_HEAD_FILE, owned=True)
    return held.strip() if held is not None else None


def _write_record(round_dir: Path, name: str, text: str) -> None:
    """回の保存先へ記録を書く。既存のファイルを開いて切り詰めず、ほかの利用者が名前を差し替えられない保存先にだけ書く。"""
    with worker_paths.open_or_create(round_dir) as wd:
        worker_paths.check_unshared(wd)
        worker_paths.write_file(wd, name, text, private=True)


def round_state(unit_dir: Path) -> RoundState:
    """`run.json` を持つ最新の回の、結果・マージの結果・`reviewed-head`・`stopped-head`。記録の無い空の回は数えない。"""
    rounds = sorted(
        (n, p)
        for p in (unit_dir.iterdir() if unit_dir.is_dir() else [])
        if (n := _round_number(p)) is not None and (p / plan_runner.RUN_FILE).is_file()
    )
    if not rounds:
        return RoundState(unit_dir / "1", False, None, None, None, None)
    path = rounds[-1][1]
    _, done = plan_runs.previous(path)
    kind, stopped = _own_records(path)
    return RoundState(path, True, done, kind, _saved_head(path / REVIEWED_HEAD_FILE), stopped)


def _stopped_step(done: Optional[Mapping[str, Any]]) -> Optional[str]:
    """止まった回の、結果の `items` の先頭（`kind` が `plan`）の `step`。止まっていないか無ければ None。"""
    if done is None or done.get("status") != result.STOPPED:
        return None
    items = done.get("items")
    first = items[0] if isinstance(items, list) and items and isinstance(items[0], Mapping) else {}
    step = first.get("step") if first.get("kind") == plan_runner.TOOL else None
    return step if isinstance(step, str) else None


def resume_of(state: RoundState, head: str, local_tests: bool) -> Resume:
    """打ち直しの扱い（詳細設計書の判定表。上の行を先に当てる）。`head` は、スプリントブランチをマージした後の単位の作業場所の HEAD。

    マージだけを打ち直すのは、マージが CI の待ち（マージキューの待ちを含む）か基盤の待ちで止まった回である。HEAD が変わった単位を
    手元のテストから流し直すのは、テストより後のステップで止まった回で、作業・受け入れ条件・テスト・修正で止まった回は含めない。
    """
    merge_only = {"ci_pending", "infra_wait"}
    before_test = {"work", "acceptance", "test", "test-repeat", "fix"}
    if not state.ran:
        return Resume("run", False, None)
    finished = state.done is not None and state.done.get("status") == result.OK
    if finished and state.merge in merge_only and state.reviewed == head:
        return Resume("merge", False, None)
    step = _stopped_step(state.done)
    moved = state.stopped_head != head
    retest = "test" if local_tests else "push"
    if finished or step in REVIEW_STEPS:
        return Resume("run", True, retest if moved else "push")
    if step is not None and step not in before_test and moved:
        return Resume("run", True, retest)
    return Resume("run", False, None)


# --- 単位を流す -----------------------------------------------------------------------------------


def run_stage(run: StageRun, work_dir: Path, cwd: Path, ports: Ports) -> Optional[StageResult]:
    """単位を表の順に流し、すべてマージしたらスプリントの PR を開く。これまでの経路なら None。

    並びは `units.json` から読み、無ければ `prepare` で作る。マージ済みの単位は開始時に 1 回読んだ一覧で飛ばす（`already_merged`）。
    単位が止まれば後の単位を始めず、止まった単位の名前・課題・PR の番号・理由を結果に持たせる（受け入れ条件 5）。
    スプリントの作業場所は、スプリントの PR を開く直前に、単位のマージ結果だけでできたリモートの履歴まで進める（`sprint_pr`）。
    前の実行でマージされた単位（マージキューの待ちで止まった後にマージされたものなど）も、打ち直しで同じように照合して進む。
    マージコミットは、開始時にマージ済みの単位は開始時の一覧から、この実行でマージした単位は `ndf ci merge` の結果から得る。
    """
    held = plan_files.read_json(work_dir / UNITS_FILE)
    found = _held_lineup(held) if held is not None else prepare(run, work_dir, cwd)
    if found is None:
        return None
    ctx = _unit_context(run, work_dir, cwd, found)
    # 単位の名前は課題をまたいで重なりうるので、ブランチは単位ごとに求める（`lineup` は代表の課題と名前の組で重複を見る）
    pairs = [(u, unit_branch(ctx.branch, u)) for u in found.units]
    hub = github.GitHub(ctx.repo, github.client())
    merged = github_pulls.merged_pulls(hub, [b for _, b in pairs], ctx.branch)
    items: list[dict[str, Any]] = []
    landed: dict[str, str] = {}
    for unit, branch in pairs:
        if branch in merged:
            items.append(_unit_item(unit, merged[branch].number, "already_merged"))
            landed[branch] = merged[branch].merge_sha
            continue
        try:
            item, landed[branch] = _one_unit(ctx, unit, branch, ports)
        except UnitsStopped as exc:
            return _unit_stopped(ctx, items, unit, branch, exc)
        items.append(item)
    unknown = [b for _, b in pairs if not landed[b]]
    if unknown:
        # GitHub がマージの応答でマージコミットを返さなかったときだけ読み直す（通常の経路では問い合わせを増やさない）
        again = github_pulls.merged_pulls(hub, unknown, ctx.branch)
        landed.update({b: again[b].merge_sha for b in unknown if b in again})
    return _after_units(ctx, items, ports, [(b, landed[b]) for _, b in pairs])


def _held_lineup(held: Mapping[str, Any]) -> Optional[Lineup]:
    """`prepare` が書いた並び。これまでの経路なら None。"""
    if held["route"] != "units":
        return None
    units = tuple(
        Unit(**{**u, "tasks": tuple(u["tasks"]), "criteria": tuple((int(i), int(n)) for i, n in u["criteria"])}) for u in held["units"]
    )
    return Lineup(units, dict(held["unit_ci"]))


def _unit_context(run: StageRun, work_dir: Path, cwd: Path, found: Lineup) -> Context:
    """単位を流す間に変わらない値。作業場所が今いるブランチが、登録されたスプリントブランチで、並びのマージ先と同じことを確かめる
    （`units.json` を読む打ち直しは `prepare` の確かめを経ないので、ここで毎回確かめる）。"""
    g = Git(_work_location(run, cwd))
    root = Path(g.run("rev-parse", "--show-toplevel").strip())
    project = ndf_settings_in(root, PROJECT_FILE)
    branch = _checked_branch(run, _current_branch(g), project)
    if branch != found.unit_ci["base"]:
        detail = f"スプリントに登録されたブランチ {branch} が、単位の PR のマージ先 {found.unit_ci['base']}（{UNITS_FILE}）と違う"
        raise UnitsStopped("precondition", detail)
    remote = g.remote()
    test = project["test"] if isinstance(project.get("test"), Mapping) else {}
    review = _review_command(project, f"{remote}/{branch}")
    return Context(run, work_dir, root, branch, remote, g.remote_repo(remote), found.unit_ci, review, not scoped.ci_only(test))


def _review_command(project: Mapping[str, Any], base: str) -> tuple[str, ...]:
    """単位の PR のレビューの起動。設定（`workflow.launch.review`）があればそれ、無ければ `--pr` を持たない既定の起動。"""
    workflow = project["workflow"] if isinstance(project.get("workflow"), Mapping) else {}
    launch = workflow["launch"] if isinstance(workflow.get("launch"), Mapping) else {}
    held = launch["review"] if "review" in launch else None
    if isinstance(held, list) and held and all(isinstance(a, str) and a for a in held):
        return tuple(held)
    return tuple(route.review_command("impl", base))


def _unit_item(unit: Unit, pr: Optional[int], status: str) -> dict[str, Any]:
    return {"kind": "unit", "name": unit.name, "issue": unit.issue, "pr": pr, "status": status}


def _one_unit(ctx: Context, unit: Unit, branch: str, ports: Ports) -> tuple[dict[str, Any], str]:
    """単位 1 つを、作業場所の用意 → 打ち直しの判定 → スプリントブランチの HEAD のマージ → `impl` → マージの順に流す。

    マージだけを打ち直す判定はスプリントブランチをマージする前の HEAD で行う（マージするとレビューした HEAD と合わなくなる）。
    返すのは単位の項目と、マージで入ったコミット（`ndf ci merge` が返さなければ空）。
    """
    worktree = unit_worktree(ctx, branch, ports)
    state = round_state(ctx.work_dir / UNITS_DIR / branch)
    g = Git(worktree)
    round_dir = state.round_dir
    if resume_of(state, g.rev_parse("HEAD"), ctx.local_tests).action != "merge":
        base = include_sprint(ctx, worktree)
        resume = resume_of(state, g.rev_parse("HEAD"), ctx.local_tests)
        round_dir = _next_round(state) if resume.new_round else state.round_dir
        _write_record(round_dir, BASE_HEAD_FILE, base + "\n")
        got = ports.call_plan(unit_run(ctx, unit, worktree, round_dir, resume.start), round_dir)
        _write_record(round_dir, STOPPED_HEAD_FILE, g.rev_parse("HEAD") + "\n")
        if got.status != result.OK:
            raise _unit_stop_of(got.report, "impl が止まった", _ci_note(ctx, got.report))
    landed = merge_unit(ctx, branch, worktree, round_dir, ports)
    return _unit_item(unit, _pr_number(round_dir), "merged"), landed


def _next_round(state: RoundState) -> Path:
    return state.round_dir.parent / str(int(state.round_dir.name) + 1)


def _pr_number(round_dir: Path) -> Optional[int]:
    held = _saved_head(round_dir / PR_NUMBER_FILE)
    return int(held) if held is not None and held.isdigit() else None


def _ci_note(ctx: Context, report: Mapping[str, Any]) -> str:
    """CI のステップで止まった単位の理由に足す、待つチェックの名前を読んだ設定のキー。"""
    if _stopped_step(report) != "ci":
        return ""
    return f"（待つチェック {', '.join(ctx.unit_ci['required'])} は {ctx.unit_ci['key']} の設定）"


def _unit_stop_of(report: Mapping[str, Any], what: str, note: str = "") -> UnitsStopped:
    """作業の止まった結果 JSON を、同じ停止理由の種類と `retry_after` のまま単位の停止にする。"""
    cause = report["cause"] if isinstance(report.get("cause"), Mapping) else {}
    kind = str(cause["kind"]) if "kind" in cause else "unknown"
    detail = cause["detail"] if isinstance(cause.get("detail"), str) else str(report["summary"])
    wait = cause.get("retry_after")
    return UnitsStopped(kind, f"{what}: {detail}{note}", retry_after=float(wait) if isinstance(wait, (int, float)) else None)


def _unit_stopped(ctx: Context, items: list[dict[str, Any]], unit: Unit, branch: str, exc: UnitsStopped) -> StageResult:
    """単位で止まった結果。工程のステートマシンは `cause` だけを残すので、単位の名前と理由は説明の文に書く。"""
    pr = _pr_number(round_state(ctx.work_dir / UNITS_DIR / branch).round_dir)
    where = f"単位 {unit.name}（課題 #{unit.issue}{f'、PR #{pr}' if pr is not None else ''}）"
    row = {**_unit_item(unit, pr, "stopped"), "reason": exc.detail}
    report = result.stopped(
        plan_runner.TOOL,
        exc.kind,
        f"{where}で止まった: {exc.detail}",
        retry_after=exc.retry_after,
        items=[*items, row],
        metrics={"unit_ci": ctx.unit_ci},
    )
    return StageResult(report, {})


def _after_units(ctx: Context, items: list[dict[str, Any]], ports: Ports, landed: Sequence[tuple[str, str]]) -> StageResult:
    """すべての単位がマージ済みになった後に、スプリントの PR を開いた結果。開けなければ止まった単位を持たない `stopped`。"""
    metrics = {"unit_ci": ctx.unit_ci}
    try:
        opened = sprint_pr(ctx, ports, landed)
    except UnitsStopped as exc:
        return StageResult(result.stopped(plan_runner.TOOL, exc.kind, exc.detail, items=items, metrics=metrics), {})
    if opened.status != result.OK:
        stop = _unit_stop_of(opened.report, "スプリントの PR を開けない")
        gaps = opened.report["gaps"] if isinstance(opened.report.get("gaps"), list) else None
        report = result.stopped(
            plan_runner.TOOL, stop.kind, stop.detail, retry_after=stop.retry_after, items=items, metrics=metrics, gaps=gaps
        )
        return StageResult(report, {})
    summary = f"単位 {len(items)} 個をスプリントブランチ {ctx.branch} へマージし、スプリントの PR を開いた"
    return StageResult(result.result(plan_runner.TOOL, result.OK, summary, items=items, metrics=metrics), {})


def _fetch_sprint(g: Git, ctx: Context) -> str:
    """スプリントブランチを取得し、リモートの HEAD の SHA を返す。取得できなければ止める。"""
    done = g.fetch_branch(ctx.remote, ctx.branch)
    if done.returncode != 0:
        raise UnitsStopped("precondition", f"{ctx.remote} の {ctx.branch} を取得できない: {mask_urls(done.stderr.strip())}")
    return g.rev_parse(f"refs/remotes/{ctx.remote}/{ctx.branch}")


def unit_worktree(ctx: Context, branch: str, ports: Ports) -> Path:
    """単位の作業場所。スプリントブランチと単位のブランチを取得し直し、設計書の「単位のブランチと保存先」の表で用意する。

    既にある作業場所に未コミットの変更か進行中のマージがあれば、スプリントブランチのマージも `impl` もせずに止める（利用者の変更に触れない）。
    """
    g = Git(ctx.sprint)
    tracking = f"refs/remotes/{ctx.remote}/{ctx.branch}"
    refspecs = (f"+{HEADS}{ctx.branch}:{tracking}", f"+{HEADS}{ctx.branch}-u*:{tracking}-u*")
    fetched = g.attempt("fetch", "-q", "--no-tags", "--prune", "--", ctx.remote, *refspecs)
    if fetched.returncode != 0:
        raise UnitsStopped(
            "precondition", f"{ctx.remote} の {ctx.branch} と単位のブランチを取得できない: {mask_urls(fetched.stderr.strip())}"
        )
    local, remote = g.commit_or_none(f"{HEADS}{branch}"), g.commit_or_none(f"refs/remotes/{ctx.remote}/{branch}")
    behind = local is not None and remote is not None and local != remote and not g.is_ancestor(remote, local)
    if behind and not g.is_ancestor(str(local), str(remote)):
        raise UnitsStopped(
            "precondition", f"手元の {branch}（{local}）と {ctx.remote} の {branch}（{remote}）が分かれている。揃えてから打ち直す"
        )
    path = next((e.path for e in g.worktrees() if e.branch == branch and not e.prunable), None)
    if path is None:
        path = _created(ctx, branch, ports)
    else:
        _check_clean(path)
    if behind:
        moved = Git(path).merge_ff(str(remote))
        if moved.returncode != 0:
            raise UnitsStopped("precondition", f"{branch} を {ctx.remote} の HEAD へ進められない: {mask_urls(moved.stderr.strip())}")
    return path


def _created(ctx: Context, branch: str, ports: Ports) -> Path:
    """`ndf worktree create <単位のブランチ> --from <スプリントブランチ>` で作った作業場所。"""
    done = ports.command(ndf_command("worktree", "create", branch, "--from", ctx.branch), ctx.sprint, dict(os.environ))
    report = result.last_line(done.stdout)
    if report is None or report["status"] != result.OK:
        reason = (
            _unit_stop_of(report, "作業場所を作れない").detail
            if report is not None
            else f"作業場所を作れない（終了コード {done.returncode}）"
        )
        raise UnitsStopped("precondition", reason)
    return Path(str(report["outputs"]["worktree"]))


def _check_clean(path: Path) -> None:
    """作業場所に未コミットの変更（追跡していないファイルを含む）か進行中のマージがあれば止める。"""
    g = Git(path)
    changed = g.status_paths()
    merging = g.commit_or_none("MERGE_HEAD") is not None
    if changed or merging:
        what = f"未コミットの変更（{', '.join(changed[:5])}）" if changed else "進行中のマージ"
        raise UnitsStopped("precondition", f"単位の作業場所 {path} に{what}がある。コミットするか取り消してから打ち直す")


def include_sprint(ctx: Context, worktree: Path) -> str:
    """単位の作業場所がスプリントブランチの HEAD（取得済み）を含まなければ `git merge --no-edit` でマージし、その HEAD を返す（I3）。

    衝突すれば、この関数が始めたマージだけを取り消して止める（呼ぶ前からあるマージは `unit_worktree` が先に止める）。
    """
    g = Git(worktree)
    tip = g.rev_parse(f"refs/remotes/{ctx.remote}/{ctx.branch}")
    if g.is_ancestor(tip, "HEAD"):
        return tip
    done = g.attempt("merge", "--no-edit", "-q", tip)
    if done.returncode != 0:
        g.attempt("merge", "--abort")
        detail = f"{ctx.branch} の HEAD をマージすると衝突するので、そのマージを取り消した: {mask_urls(done.stderr.strip() or done.stdout.strip())}"
        raise UnitsStopped("base_conflict", detail)
    return tip


def unit_run(ctx: Context, unit: Unit, worktree: Path, round_dir: Path, start: Optional[str]) -> StageRun:
    """単位の範囲の指示を書き、単位の `impl` の呼び出し（上書きの値・テンプレート・環境変数）を組む。

    代表の課題の題と本文は、選んだリポジトリから工程の保存先へ写す。作成者を信用しなければ止める。補った単位は指示を書かない。
    """
    issued = issue_values.issue_values(ctx.run.inputs, ctx.work_dir, worktree, number=unit.issue, repo=ctx.repo)
    if issued is None:
        raise UnitsStopped("precondition", f"課題 #{unit.issue} の作成者に書き込み権限が無く、題と本文を作業へ渡さない")
    unit_file = "" if unit.supplemented else str(_write_unit_file(round_dir, unit))
    overrides: dict[str, route.Value] = {
        **issued,
        "branch": unit_branch(ctx.branch, unit),
        "base": ctx.branch,
        "remote": ctx.remote,
        "acceptance": acceptance_args(unit),
        "review": route.unit_review_launch(ctx.repo, ctx.review),
        "unit_file": unit_file,
    }
    drop = ["done-check"]
    if ctx.unit_ci["required"]:
        overrides["required"] = [str(c) for c in ctx.unit_ci["required"]]
    else:
        drop += CI_STEPS
    env = {GH_REPO_ENV: ctx.repo, **({UNIT_FILE_ENV: unit_file} if unit_file else {})}
    plain = ctx.run.plain()
    template = route.unit_template(route.templates()["impl"], ctx.repo, drop)
    inputs = {**plain.inputs, "worktree": str(worktree)}
    return replace(plain, inputs=inputs, overrides=overrides, template=template, env_extra=env, start=start)


def _write_unit_file(round_dir: Path, unit: Unit) -> Path:
    """単位の範囲の指示（決定 8）を回の保存先へ書き直す。条件は番号だけを書き、課題の本文は写さない。"""
    criteria = "、".join(f"課題 #{i} の {n}" for i, n in unit.criteria) if unit.criteria else "無し"
    text = f"""# レビューの単位: {unit.name}

- 実装計画: {unit.plan}
- 含むタスク: {"、".join(f"Task {t}" for t in unit.tasks)}
- 満たす受け入れ条件: {criteria}

この PR は、上の単位のタスクだけを実装する。後の単位のタスクは実装しない。

- 単位の外を変えてよいのは、今の単位の変更が原因でテスト・CI が失敗するか、レビューで指摘を受け、直すのに単位の外の変更が要る場合だけである。変更は最小にし、コミットメッセージに理由を書く
- 今の単位の変更が原因でない失敗（変更の前からある失敗、原因を確かめられない失敗）は直さない。直さない失敗が残る場合と、後の単位のタスクを実装しないと通らない場合は、理由を返して止まる
- 例外: 今の差分に含まれる秘密・認証認可・利用者のデータ・戻せない操作の問題は、担当の単位にかかわらず指摘し、直す。後の単位を理由に見送らない
"""
    round_dir.mkdir(parents=True, exist_ok=True)
    with worker_paths.open_or_create(round_dir) as wd:
        worker_paths.write_file(wd, UNIT_FILE, text, private=True)
        return wd.path / UNIT_FILE


def merge_unit(ctx: Context, branch: str, worktree: Path, round_dir: Path, ports: Ports) -> str:
    """`ndf ci merge` で単位の PR をマージし、結果の種類を `merge.json` に残して、マージで入ったコミット（返されなければ空）を返す。

    PR の番号とレビューした HEAD は、同じ回の保存先から読む。どちらかが無ければマージを打たずに止める。
    スプリントの作業場所はここでは進めない（次の単位の作業場所はリモートのスプリントブランチから切る。進めるのは `sprint_pr`）。
    """
    number, reviewed = _pr_number(round_dir), _saved_head(round_dir / REVIEWED_HEAD_FILE)
    if number is None or not reviewed:
        raise UnitsStopped("precondition", f"回の保存先 {round_dir} に PR の番号かレビューした HEAD の記録が無いので、マージしない")
    argv = ndf_command(
        "ci", "merge", "--repo", ctx.repo, "--pr", str(number), "--branch", branch, "--base", ctx.branch, f"--reviewed={reviewed}"
    )
    done = ports.command(argv, worktree, dict(os.environ))
    report = result.last_line(done.stdout)
    ok = report is not None and report["status"] == result.OK
    kind = "merged" if ok else ("bad_report" if report is None else _unit_stop_of(report, "").kind)
    _write_record(round_dir, MERGE_FILE, json.dumps({"kind": kind}, ensure_ascii=False) + "\n")
    if report is None:
        raise UnitsStopped("bad_report", f"ndf ci merge が結果 JSON を返さない（終了コード {done.returncode}）")
    if not ok:
        raise _unit_stop_of(report, f"PR #{number} をマージできない")
    rows = report["items"] if isinstance(report.get("items"), list) else []
    sha = next((r.get("merge_sha") for r in rows if isinstance(r, Mapping) and r.get("kind") == "pr"), None)
    return sha if isinstance(sha, str) else ""


def sprint_pr(ctx: Context, ports: Ports, landed: Sequence[tuple[str, str]]) -> StageResult:
    """スプリントの作業場所をリモートのスプリントブランチの HEAD へ揃えてから、テンプレート `pr` を push のステップなしで流す。

    `landed` は表の順の（単位のブランチ, マージで入ったコミット）。手元から進めてよいのは、リモートの履歴が単位のレビューを経た
    マージ結果だけでできていると確かめられたときである（`_take_unit_merges`）。確かめられなければ、手元を動かさずに止める。
    """
    g = Git(ctx.sprint)
    _take_unit_merges(g, ctx, _fetch_sprint(g, ctx), landed)
    t = route.templates()["pr"]
    template = replace(t, plan=route.without_steps(route.expanded(t), SPRINT_PR_DROP))
    run = replace(ctx.run.plain(), name=t.name, template=template, overrides={"remote": ctx.remote}, env_extra={GH_REPO_ENV: ctx.repo})
    return ports.call_plan(run, ctx.work_dir / SPRINT_PR_DIR)


def _take_unit_merges(g: Git, ctx: Context, tip: str, landed: Sequence[tuple[str, str]]) -> None:
    """手元の HEAD からリモートの HEAD（`tip`）までが、単位のレビューを経たマージ結果だけでできていれば、手元を `tip` へ進める。

    手元の HEAD から始め、手元にまだ無い単位のマージを表の順にたどる。単位ごとに、実装の前に取り込んだスプリントブランチの HEAD
    （`base-head`）が直前の点と同じで、マージで入ったコミットのツリーがレビューした HEAD のツリーと同じことを確かめる。こう確かめると、
    マージの方式（squash・merge commit・rebase）によらず、単位のマージの間に入った別の変更を見つけられる。最後の点が `tip` と
    違うか、どれかの単位を確かめられなければ、手元を動かさずに止める。手元の HEAD に入っているコミットは確かめ済みとして扱う
    （利用者が確かめて取り込んだものを含む）。
    """
    head = g.rev_parse("HEAD")
    if head == tip:
        return
    if not g.is_ancestor(head, tip):
        detail = (
            f"{ctx.remote} の {ctx.branch} の HEAD（{tip}）がスプリントの作業場所の HEAD（{head}）と違う。"
            f"リモートにだけあるコミットは {head}..{tip}、手元にだけあるコミットは {tip}..{head} である。"
            "手元とリモートを揃え、その変更をスプリントの PR に含めてよいと確かめてから ndf progress next を打ち直す"
        )
        raise UnitsStopped("precondition", detail)
    point = head
    for branch, sha in landed:
        if sha and g.commit_or_none(sha) is not None and g.is_ancestor(sha, point):
            continue
        if not _reviewed_merge(g, ctx, branch, sha, point):
            raise _unreviewed(ctx, point, tip)
        point = sha
    if point != tip:
        raise _unreviewed(ctx, point, tip)
    current = _current_branch(g)
    if current != ctx.branch:
        detail = f"スプリントの作業場所のブランチ {current or '（ブランチに居ない）'} が {ctx.branch} と違う。{ctx.branch} へ切り替えてから打ち直す"
        raise UnitsStopped("precondition", detail)
    moved = g.merge_ff(tip)
    if moved.returncode != 0:
        raise UnitsStopped("precondition", f"スプリントの作業場所を {ctx.branch} の HEAD へ進められない: {mask_urls(moved.stderr.strip())}")


def _reviewed_merge(g: Git, ctx: Context, branch: str, sha: str, point: str) -> bool:
    """単位のマージで入ったコミット `sha` が、`point` の上に単位のレビューを経た変更だけを足したものか。"""
    state = round_state(ctx.work_dir / UNITS_DIR / branch)
    reviewed, base = state.reviewed, _saved_base(state.round_dir)
    if not sha or not reviewed or base != point:
        return False
    if g.commit_or_none(sha) is None or g.commit_or_none(reviewed) is None or not g.is_ancestor(point, sha):
        return False
    return g.attempt("diff", "--quiet", "--no-ext-diff", sha, reviewed, "--").returncode == 0


def _unreviewed(ctx: Context, point: str, tip: str) -> UnitsStopped:
    """単位のレビューを経たと確かめられないコミットがリモートにあるときの停止（手元は動かさない）。"""
    detail = (
        f"{ctx.remote} の {ctx.branch} に、単位の PR のレビューを経たと確かめられないコミット（{point}..{tip}）がある。"
        f"その変更をスプリントの PR に含めてよければ、スプリントの作業場所 {ctx.sprint} で git merge --ff-only {tip} を打って"
        "手元へ取り込んでから ndf progress next を打ち直す（手元へ取り込んだコミットは確かめ済みとして扱う）"
    )
    return UnitsStopped("precondition", detail)
