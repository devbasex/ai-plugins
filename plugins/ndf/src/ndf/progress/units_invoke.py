"""工程「実装」を実装計画のレビューの単位ごとに流す処理（docs/specifications/ndf-implement-stage-review-units.md）。

ここにあるのは、開始前の確かめ（`prepare`）、打ち直しで単位の回をどう続けるかの判定（`units_flow.round_state`・`units_flow.resume_of`）と、
単位を流す処理（`run_stage`。作業場所の用意・`impl`・マージ・スプリントの PR。流す順と並列は `units_flow`、キューの待ちの状態と記録は
`units_queue`）である。
開始前の確かめは、作業場所が今いるブランチをリモートから取得し、コミットされた単位のファイルか実装計画の単位の見出しがあるかで
経路を決める。分け方の承認が要るスプリントは常に単位の経路にする。単位の経路なら、手元をリモートに揃え、並びを読んで `units.json` に書く。
単位のファイルがあるか承認が要れば、承認の記録と照らし、単位のファイルを読んで確かめてから、確かめたコミットをリモートへ送る
（issues/issue-3702-design-detail.md の「始める前」）。どちらでもなければ、実装計画の表を読んでから送る。
表の読み方と順序は docs/specifications/ndf-implement-stage-review-units.md の「開始前の確かめ」、打ち直しの表は同じ文書の「打ち直し」にある。

`ndf.progress.invoke` を import しない（`invoke` がこのモジュールを呼ぶので、型は `plan_runs` が持つ）。プランを流すのと
コマンドを打つのは `invoke` が渡す `Ports` を通す。`domains/` を import できないので、作業場所の用意は `ndf worktree create`、
マージは `ndf ci merge` を子プロセスで打つ。
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple, Optional, Union

from ndf import contracts, identity
from ndf.adapters import github, github_merge, github_pulls
from ndf.adapters.git import HEADS, Git, mask_urls
from ndf.cli import ndf_command
from ndf.contracts import named_cap, result, worker_paths
from ndf.core import branch_strategy, json_pointer, route
from ndf.core.plan import files as plan_files
from ndf.core.plan import inputs_decide, units_files
from ndf.core.plan import units_decide as ud
from ndf.core.plan import runner as plan_runner
from ndf.core.plan.review_units_decide import PlanDoc, Unit, UnitsUnreadable, acceptance_args, lineup, unit_key
from ndf.core.plan.transition_decide import USAGE_LIMIT
from ndf.progress import issue_values, refactor_values, units_flow, units_queue
from ndf.progress.command import PROJECT_FILE, ndf_settings_in
from ndf.progress.issue_values import UNITS_DIR, UNITS_FILE
from ndf.progress.plan_runs import ConvertRequest, SprintPlan, StageResult, StageRun, UnitsStopped
from ndf.testing import scoped

# 単位の PR で待つチェックの設定のキー（`workflow` の下。書かなければ `ci.required_checks` を読む。#4369 の決定 13）
UNIT_CHECKS = "unit_required_checks"
# 単位のブランチ名に付ける、単位の名前の sha256 の先頭の字数
UNIT_HASH_CHARS = 8
# 承認した文書を送るコミットの記録（ステートマシンの作業の保存先。`{"record_head", "commit"}`）
DESIGN_SYNC_FILE = "design-sync.json"
# 単位の回の保存先の、PR の番号（`impl` の `pr` のステップが書く）と単位の範囲の指示（`unit_run` が書く）のファイル
PR_NUMBER_FILE = Path(route.PR_NUMBER_FILE).name
UNIT_FILE = "unit.md"
# 単位の PR で待つチェックが空のとき、`impl` から外すステップ（`ci` と、その失敗からしか行かないもの。#4369 の決定 13）
CI_STEPS = ("ci", "repeat-check", "ci-fix", "ci-local", "push-fixed")
# スプリントの PR を開くプランの保存先（工程の保存先の下）と、そのプランから外すステップ（単位の変更はマージで送り済み）
SPRINT_PR_DIR = "sprint-pr"
SPRINT_PR_DROP = ("push", "fix-push")
# 変換（`units_convert`）が送ったコミットの記録（ステートマシンの作業の保存先）
CONVERT_FILE = "convert.json"
# 工程の保存先の `units/` の下の、反映待ちの記録
INGEST_FILE = "ingest.json"
# 単位のプランの子へ渡す、`gh` の対象のリポジトリと単位の範囲の指示のファイル（`contracts/domains/env.json`）
GH_REPO_ENV = identity.env_name("gh-repo")
UNIT_FILE_ENV = identity.env_name("unit-file")


class Lineup(NamedTuple):
    """単位の経路の並び。`unit_ci` は単位の PR のマージ先（`base`）・待つチェックの名前（`required`）・その名前を読んだ設定のキー（`key`）。

    `source` は担当タスクの本文を読むコミット（承認の記録の HEAD か、開始の HEAD）。実装計画の表から流すときは空。
    `bodies` は開始前の確かめが `source` から読んだ本文（単位のキーごと）。`units.json` には書かず、読み直した並びでは None。
    """

    units: tuple[Unit, ...]
    unit_ci: dict[str, Any]
    source: str = ""
    bodies: Optional[Mapping[str, Sequence[str]]] = None


class Ports(NamedTuple):
    """単位を流す処理が外へ出す呼び出し（`invoke` が渡し、テストが差し替える）。

    `call_plan` は呼び出しを保存先でプランとして流し、`command` は argv を作業ディレクトリと環境で打つ。`failure` は
    単位を流す間の例外を停止の結果 JSON にする（分けられない例外は None）。`call_plan` だけが並列に呼ばれ、`workers` は
    それを流すプールと待ちである。`clock` と `sleep` はキューと反映の待ちの時計と眠りである。
    """

    call_plan: Callable[[StageRun, Path], StageResult]
    command: Callable[[list[str], Path, dict[str, str]], "subprocess.CompletedProcess[str]"]
    failure: Callable[[Exception], Optional[dict[str, Any]]] = lambda exc: None
    workers: units_flow.Workers = units_flow.Workers()
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    # 工程のステップが待っていた回答（変換の再承認）
    answer: Optional[str] = None


class Context(NamedTuple):
    """単位を流す間に変わらない値。`sprint` はスプリントの作業場所、`branch` はスプリントブランチ、`repo` は選んだリモートの
    `<所有者>/<リポジトリ>`、`review` は単位の PR のレビューの起動（`--pr` を持たない）、`local_tests` は手元のテストを打つか、
    `source` は担当タスクの本文を読むコミット（`Lineup.source`）、`bodies` はそのコミットの単位のキーごとの本文。"""

    run: StageRun
    work_dir: Path
    sprint: Path
    branch: str
    remote: str
    repo: str
    unit_ci: dict[str, Any]
    review: tuple[str, ...]
    local_tests: bool
    source: str = ""
    bodies: Mapping[str, Sequence[str]] = MappingProxyType({})


# --- 開始前の確かめ -----------------------------------------------------------------------------


def prepare(run: StageRun, work_dir: Path, cwd: Path, ports: Optional[Ports] = None) -> Union[Lineup, StageResult, None]:
    """docs/specifications/ndf-implement-stage-review-units.md の「開始前の確かめ」を行い、単位の経路なら並びを `work_dir/units.json` に書いて返す。これまでの経路なら None。

    これまでの経路では `units.json` に経路だけを書き、作業場所は変えない。止めるときは `UnitsStopped`（`units.json` を書かない）。
    git と GitHub の失敗のうちここで分けないものは、そのまま上げる（呼び手の `invoke.plan_failure` が分ける）。
    この変更より前のスプリントで実装計画に単位の欄があれば変換（`_convert`）を通し、再承認が要ればその `gate` の結果を返す。
    """
    sprint = run.sprint if run.sprint is not None else SprintPlan()
    if sprint.required and (sprint.record is None or sprint.differences is None):
        raise UnitsStopped(
            "precondition",
            "設計承認ゲートの承認の記録が無いので、実装を始めない。ndf progress restore で状態を戻すか、利用者に判断を求める",
        )
    location = _work_location(run, cwd)
    g = Git(location)
    root = Path(g.run("rev-parse", "--show-toplevel").strip())
    issues = sorted({int(n) for n in run.inputs["issues"]})
    branch = _current_branch(g)
    remote = g.remote()
    uncommitted = _uncommitted(g, issues)
    head, tip, at_head, at_tip, checked_to = units_flow.read_start(g, remote, branch, issues, sprint.destination)
    left = units_flow.Seen(units_flow.worktree_plans(root, uncommitted), bool(units_files.paths_naming(uncommitted, issues)))
    if not (sprint.required or at_head.has_units() or at_tip.has_units() or left.has_units()):
        return _plain_route(work_dir)
    if uncommitted:
        detail = f"未コミットの実装計画か単位のファイルがある: {', '.join(uncommitted)}。コミットするか取り消してから打ち直す"
        raise UnitsStopped("precondition", detail)
    project = ndf_settings_in(root, PROJECT_FILE)
    branch = _checked_branch(run, branch, project)
    # 変換するスプリントは、再承認の回答と却下の前に手元を動かさない。実装へ進むと決めた変換が進める
    move = not (sprint.convert is not None and not sprint.new_definition and at_tip.has_headings())
    behind = tip is not None and _catch_up(g, remote, branch, head, tip, move)
    seen = at_tip if behind else at_head
    if not (sprint.required or seen.has_units()):
        return _plain_route(work_dir)
    source, bodies = "", None
    count = _counted_once(g, remote)
    converting = sprint.convert is not None and not sprint.new_definition and seen.has_headings()
    request = ConvertRequest(run, root, branch, tuple(issues), sprint.omitted, count, checked_to)
    asked, seen = _converted(g, sprint, seen, request, ports)
    if asked is not None:
        return asked
    if behind:
        # 単位の経路を続けるときだけ、進めた先のコミットの設定でリリース先（I4）と単位の PR で待つチェックを決め直す
        project = ndf_settings_in(root, PROJECT_FILE)
        branch = _checked_branch(run, branch, project)
    if sprint.required or seen.unit_files:
        units, source, checked, bodies = _units_from_files(g, root, sprint, issues, seen.plans, count)
        # 送るのは記録との比較と確かめを通ったコミットだけにする（止まる実装の手元のコミットを、共有のブランチに残さない）。
        # 変換を通ったスプリントは、手元の HEAD が確かめた送り先に届いているので送らない
        _push_sprint(g, remote, branch, None if converting else checked)
    else:
        if sprint.new_definition:
            detail = "実装計画にレビューの単位の欄があるのに、単位のファイル（issues/issue-<番号>-plan.units.json）が無い"
            raise UnitsStopped("invalid", detail)
        units = _read_units(g, remote, seen.plans, issues)
        _push_sprint(g, remote, branch, "HEAD")
    return _announced(work_dir, branch, Lineup(units, _unit_ci(project, branch), source, bodies))


def _counted_once(g: Git, remote: str) -> Callable[[Sequence[int]], dict[int, int]]:
    """課題の受け入れ条件の数を数える。同じ課題の並びは 1 回だけ読み、変換と開始前の確かめで使い回す。"""
    held = functools.cache(functools.partial(_count_sprint_criteria, g, remote))
    return lambda issues: held(tuple(issues))


def _converted(
    g: Git, sprint: SprintPlan, seen: units_flow.Seen, request: ConvertRequest, ports: Optional[Ports]
) -> tuple[Union[Lineup, StageResult, None], units_flow.Seen]:
    """古い定義のスプリントで実装計画に単位の欄があれば変換を通し（#3702 の決定 10）、（ここで返して終える値, 変換の後の読み取り）を返す。
    実装へ進むなら前者は None。担当を起動できない呼び出し（`ports` が無い）は並びを持たない `Lineup` を返し、単位を流すときに変換する。"""
    if sprint.convert is None or sprint.new_definition or not seen.has_headings():
        return None, seen
    if ports is None:
        return Lineup((), {}), seen
    asked = sprint.convert(request, ports, ports.answer)
    # 変換がコミットを足すので読み直す
    return asked, seen if asked is not None else units_flow.seen_at(g, "HEAD", request.issues)


def _converted_commit(sprint: SprintPlan) -> Optional[str]:
    """変換の記録のコミット。担当タスクの本文はここから読む。変換していないスプリントは None。"""
    held = None if sprint.work_root is None else plan_files.read_json(sprint.work_root / CONVERT_FILE)
    return None if held is None else str(held["commit"])


def _announced(work_dir: Path, branch: str, found: Lineup) -> Lineup:
    """単位の経路の並びを知らせ、`work_dir/units.json` に書いて返す。"""
    print(
        f"ndf progress: 単位の PR のマージ先 {branch}、待つチェック {', '.join(found.unit_ci['required']) or '無し'}"
        f"（{found.unit_ci['key']}）",
        file=sys.stderr,
    )
    units = [_unit_json(u) for u in found.units]
    held = {"route": "units", "units": units, "unit_ci": found.unit_ci, "source": found.source}
    plan_files.write_json(work_dir / UNITS_FILE, held)
    return found


def _push_sprint(g: Git, remote: str, branch: str, commit: Optional[str]) -> None:
    """手元のコミット `commit` をスプリントブランチへ送る（単位の作業場所はリモートのスプリントブランチから切る）。None なら送らない。"""
    if commit is None:
        return
    pushed = g.attempt("push", "-q", "--no-follow-tags", "--", remote, f"{commit}:{HEADS}{branch}")
    if pushed.returncode != 0:
        raise UnitsStopped("precondition", f"{remote} の {branch} へ送れない: {mask_urls(pushed.stderr.strip())}")


def _units_from_files(
    g: Git,
    root: Path,
    sprint: SprintPlan,
    issues: Sequence[int],
    plans: Sequence[PlanDoc],
    counted: Callable[[Sequence[int]], dict[int, int]],
) -> tuple[tuple[Unit, ...], str, str, dict[str, list[str]]]:
    """単位のファイルから読んだ並び・担当タスクの本文を読むコミット・確かめたコミット（開始の HEAD）・読んだ本文。

    読み元は開始の HEAD で、承認が要れば記録の値で確かめる。

    `counted` は課題の受け入れ条件の数を数える（承認が要れば呼ばず、記録の数を使う）。変換したスプリントの担当タスクの本文は、
    変換の記録のコミットから読む。
    """
    start_head = g.rev_parse("HEAD")
    tree = units_files.Tree(root, start_head)
    try:
        if sprint.required:
            record, source, targets = _approved(g, tree, sprint, start_head)
            limits = ud.Limits(**record["limits"])
            criteria = {int(n): count for n, count in record[ud.CRITERIA_KEY].items()}
            report = units_files.check_collected(tree, targets, limits, criteria, False)
        else:
            source = _converted_commit(sprint) or start_head
            collected = units_files.supplemented(tree, issues, sprint.omitted, plans)
            limits = units_files.limits_of(units_files.project_of(tree))
            criteria = counted(issues)
            report = units_files.check_collected(tree, collected, limits, criteria, False)
        bad = units_files.findings(report)
        if bad:
            reasons = "; ".join(f"{f.rule} {f.unit}: {f.reason}" for f in bad)
            raise UnitsStopped("invalid", f"単位のファイルが確かめを通らない: {reasons}")
        units = units_files.lineup_from_files(report.judgement.views, report.files)
        bodies = units_files.task_texts(units_files.Tree(root, source), units)
    except units_files.UnitsFileError as exc:
        raise UnitsStopped("invalid", f"単位のファイルを読めない: {exc}") from None
    return units, source, start_head, bodies


def _approved(g: Git, tree: units_files.Tree, sprint: SprintPlan, start_head: str) -> tuple[Mapping[str, Any], str, units_files.Targets]:
    """承認の記録・その HEAD・比較が読んだ対象。承認した文書がスプリントブランチに届き、中身が記録と同じことを確かめる。"""
    record, differences = sprint.record, sprint.differences
    assert record is not None and differences is not None, "prepare が先に確かめる"
    head = str(record["review"]["head"])
    _check_delivered(g, sprint.work_root, head, start_head)
    compared = differences(tree)
    if compared.differences:
        raise UnitsStopped("refused", f"承認した分け方と今の中身が違うので、実装を始めない: {'; '.join(compared.differences)}")
    assert compared.targets is not None, "違いが無い比較は、読んだ対象を返す"
    return record, head, compared.targets


def _check_delivered(g: Git, work_root: Optional[Path], record_head: str, start_head: str) -> None:
    """記録の HEAD か、その文書を送ったコミット（`design-sync.json`）が、開始の HEAD と同じか祖先であることを確かめる。"""
    candidates = [record_head]
    synced = None if work_root is None else plan_files.read_json(work_root / DESIGN_SYNC_FILE)
    if synced is not None and "record_head" in synced and synced["record_head"] == record_head and "commit" in synced:
        candidates.append(str(synced["commit"]))
    for commit in candidates:
        if g.commit_or_none(commit) is not None and g.is_ancestor(commit, start_head):
            return
    detail = f"承認した文書（コミット {record_head}）がスプリントブランチの HEAD（{start_head}）に届いていない"
    raise UnitsStopped("precondition", detail)


def _work_location(run: StageRun, cwd: Path) -> Path:
    """入力の作業場所（`worktree`）。無ければ打った場所。"""
    held = run.inputs["worktree"] if "worktree" in run.inputs else None
    return Path(held) if isinstance(held, str) and held else cwd


def _current_branch(g: Git) -> Optional[str]:
    """作業場所が今いるブランチ。ブランチに居なければ None。"""
    done = g.attempt("branch", "--show-current")
    name = done.stdout.strip()
    return name if done.returncode == 0 and name != "" else None


def _uncommitted(g: Git, issues: Sequence[int]) -> list[str]:
    """変更・削除がコミットされていないか、追跡されていない、スプリントの課題の実装計画と単位のファイルのパス。"""
    changed = g.status_paths()
    return sorted({p for p in changed if units_flow.is_sprint_plan(p, issues)} | units_files.paths_naming(changed, issues))


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


def _catch_up(g: Git, remote: str, branch: str, head: str, tip: str, move: bool = True) -> bool:
    """手元（`head`）が遅れていればリモートの HEAD（`tip`）まで進め、遅れていたかを返す。分かれていれば止める（強制しない）。

    `move` が偽なら、確かめるだけで進めない。

    進める先は確かめた `tip` の SHA である（追跡の ref は別の作業場所の fetch で動くので、検査と違うコミットへ進めない）。
    """
    if head == tip or g.is_ancestor(tip, head):
        return False
    if not g.is_ancestor(head, tip):
        detail = f"手元の {branch}（{head}）と {remote} の {branch}（{tip}）が分かれている。揃えてから打ち直す"
        raise UnitsStopped("precondition", detail)
    if not move:
        return True
    done = g.merge_ff(tip)
    if done.returncode != 0:
        raise UnitsStopped("precondition", f"{branch} を {remote} の HEAD へ進められない: {mask_urls(done.stderr.strip())}")
    return True


def _read_units(g: Git, remote: str, plans: Sequence[PlanDoc], issues: Sequence[int]) -> tuple[Unit, ...]:
    """すべての実装計画の表を読む。"""
    counts = _count_sprint_criteria(g, remote, issues)
    try:
        return lineup(plans, counts, issues)
    except UnitsUnreadable as exc:
        raise UnitsStopped("invalid", "実装計画のレビューの単位を読めない: " + "; ".join(exc.reasons)) from None


def _count_sprint_criteria(g: Git, remote: str, issues: Sequence[int]) -> dict[int, int]:
    """課題の本文の受け入れ条件の数。本文は数えるためだけに使い、ほかへ渡さない。"""
    hub = github.GitHub(g.remote_repo(remote), github.client())
    bodies = hub.nodes(issues, "issueOrPullRequest", github.BODY_FIELDS)
    return {n: _criteria_count(bodies[n]) for n in issues}


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
    return {
        **unit._asdict(),
        "tasks": list(unit.tasks),
        "criteria": [list(c) for c in unit.criteria],
        "after": list(unit.after),
        "task_ids": [list(t) for t in unit.task_ids],
    }


def unit_branch(sprint: str, unit: Unit) -> str:
    """単位のブランチ名 `<スプリントブランチ>-u<代表の課題>-<単位の名前の sha256 の先頭 8 字>`。"""
    digest = hashlib.sha256(unit.name.encode("utf-8")).hexdigest()[:UNIT_HASH_CHARS]
    return f"{sprint}-u{unit.issue}-{digest}"


# --- 単位を流す -----------------------------------------------------------------------------------


def run_stage(run: StageRun, work_dir: Path, cwd: Path, ports: Ports) -> Optional[StageResult]:
    """単位を依存の順に並列で流し、すべてマージしたらスプリントの PR を開く。これまでの経路なら None。

    並びは `units.json` から読み、無いか使い直せなければ（`_lineup_reusable`）`prepare` で作る。マージ済みの単位は開始時に 1 回読んだ一覧で飛ばす（`already_merged`）。
    止まった単位に依存する単位は始めず、止まった単位の名前・課題・PR の番号・理由を結果に持たせる（受け入れ条件 5）。同時に流す
    本数の上限は `named_cap` が読む値である。担当タスクの本文を読むコミットを持たない並び（実装計画の表）は、順に 1 つずつ流す。
    スプリントの作業場所は、マージが分かるたびにそのマージコミットまで進め（`advance`）、進めた単位の後の単位を始める。前の実行で
    マージされた単位（マージキューの待ちで止まった後にマージされたもの、マージの後の取得に失敗したもの）も、打ち直しで同じように進む。
    """
    held = plan_files.read_json(work_dir / UNITS_FILE)
    if held is not None and not _lineup_reusable(run, cwd, held):
        held = None
    found = _held_lineup(held) if held is not None else prepare(run, work_dir, cwd, ports)
    if found is None or isinstance(found, StageResult):
        return found
    ctx = _unit_context(run, work_dir, cwd, found)
    try:
        limit = named_cap.read(os.environ, identity.load().state_dir).value
    except named_cap.NamedCapError as exc:
        raise UnitsStopped("invalid", f"同時に流す単位の本数の上限を読めない: {exc}") from None
    # 単位の名前は課題をまたいで重なりうるので、ブランチは単位ごとに求める（`lineup` は代表の課題と名前の組で重複を見る）
    branches = {units_flow.key_of(u): unit_branch(ctx.branch, u) for u in found.units}
    hub = github.GitHub(ctx.repo, github.client())
    merged = github_pulls.merged_pulls(hub, list(branches.values()), ctx.branch)
    done = {k: merged[b] for k, b in branches.items() if b in merged}

    def failed(unit: Unit, exc: Exception) -> UnitsStopped:
        if isinstance(exc, UnitsStopped):
            return exc
        report = ports.failure(exc)
        if report is None:
            raise exc
        return _unit_stop_of(report, "単位を流せない")

    watching = _watching(ctx, ports, hub)
    landing = _to_take_in(ctx, done, watching)
    order = units_flow.Order(found.units, frozenset(done) - landing.keys(), chained=not found.source)
    ops = units_flow.Ops(
        lambda u: _start_unit(ctx, u, branches[units_flow.key_of(u)], ports, watching.read), failed, _usage_limited, watching
    )
    flowed = units_flow.flow(order, ops, limit, ports.workers, landing)
    items = dict(flowed.items)
    for unit in found.units:
        key = units_flow.key_of(unit)
        if key in done:
            items[key] = units_flow.unit_item(unit, done[key].number, "already_merged")
    if flowed.failures or flowed.broken:

        def pr_of(unit: Unit) -> Optional[int]:
            return _pr_number(units_flow.round_state(ctx.work_dir / UNITS_DIR / branches[units_flow.key_of(unit)]).round_dir)

        return units_flow.stopped_stage(found.units, flowed, items, pr_of, {"unit_ci": ctx.unit_ci})
    ordered = [items[units_flow.key_of(u)] for u in found.units]
    landed = [(branches[k], pull) for k, pull in done.items()]
    landed += [(branches[k], pull) for k, pull in zip(flowed.items, flowed.pulls)]
    return _after_units(ctx, ordered, ports, lambda tip: units_flow.merged_pulls_of(hub, ctx.branch, landed, tip))


def _to_take_in(
    ctx: Context, done: Mapping[str, github_pulls.MergedPull], watching: units_queue.Watching
) -> dict[str, github_pulls.MergedPull]:
    """マージ済みの単位のうち、マージのコミットを手元の HEAD が含まず、作業場所へ反映してから後の単位を始めるもの。

    含む単位は反映を通らないので、反映待ちの記録（前の起動の後に手元へ入ったもの）をここで消す。
    """
    held = Git(ctx.sprint).contained((p.merge_sha for p in done.values()), "HEAD")
    units_queue.Board(watching).taken(*(k for k, p in done.items() if p.merge_sha in held))
    return {k: p for k, p in done.items() if p.merge_sha and p.merge_sha not in held}


def _held_lineup(held: Mapping[str, Any]) -> Optional[Lineup]:
    """`prepare` が書いた並び。これまでの経路なら None。"""
    if held["route"] != "units":
        return None
    units = tuple(
        Unit(
            **{
                **u,
                "tasks": tuple(u["tasks"]),
                "criteria": tuple((int(i), int(n)) for i, n in u["criteria"]),
                "after": tuple(u["after"] if "after" in u else ()),
                "task_ids": tuple((str(t), int(i)) for t, i in (u["task_ids"] if "task_ids" in u else ())),
            }
        )
        for u in held["units"]
    )
    return Lineup(units, dict(held["unit_ci"]), str(held["source"]) if "source" in held else "")


def _lineup_reusable(run: StageRun, cwd: Path, held: Mapping[str, Any]) -> bool:
    """保存した並びを使い直せるか。変換するスプリントの単位の並びは、変換の記録と今の単位のファイルが合うときだけ使う。"""
    sprint = run.sprint
    if sprint is None or sprint.reusable is None or held["route"] != "units":
        return True
    return sprint.reusable(_work_location(run, cwd), str(held["source"]) if "source" in held else "")


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
    repo = g.remote_repo(remote)
    bodies = found.bodies
    if bodies is None:
        # `units.json` から読み直した並びは本文を持たないので、保存したコミットから読み直す
        bodies = _task_texts(root, found.source, found.units) if found.source else {}
    return Context(run, work_dir, root, branch, remote, repo, found.unit_ci, review, not scoped.ci_only(test), found.source, bodies)


def _review_command(project: Mapping[str, Any], base: str) -> tuple[str, ...]:
    """単位の PR のレビューの起動。設定（`workflow.launch.review`）があればそれ、無ければ `--pr` を持たない既定の起動。"""
    workflow = project["workflow"] if isinstance(project.get("workflow"), Mapping) else {}
    launch = workflow["launch"] if isinstance(workflow.get("launch"), Mapping) else {}
    held = launch["review"] if "review" in launch else None
    if isinstance(held, list) and held and all(isinstance(a, str) and a for a in held):
        return tuple(held)
    return tuple(route.review_command("impl", base))


def _watching(ctx: Context, ports: Ports, hub: github.GitHub) -> units_queue.Watching:
    """待ちの扱いの接点。待ちの上限は CI の待ちと同じ `ci_budget` である。"""
    overrides = ctx.run.overrides
    budget = overrides["ci_budget"] if "ci_budget" in overrides else route.params()["ci_budget"].default
    return units_queue.Watching(
        read=lambda numbers: units_queue.read(hub, numbers, ctx.branch),
        advance=lambda pulls, overdue: advance(ctx, pulls, overdue),
        ingest=ctx.work_dir / UNITS_DIR / INGEST_FILE,
        budget=float(str(budget)),
        clock=ports.clock,
        sleep=ports.sleep,
    )


def advance(
    ctx: Context,
    pulls: Mapping[str, github_pulls.MergedPull],
    overdue: Mapping[str, github_pulls.MergedPull] = MappingProxyType({}),
) -> frozenset[str]:
    """スプリントブランチを 1 回取得して作業場所をマージのコミットまで進め、反映できた単位のキーを返す。

    マージのコミットが分からない単位は確かめられないので、反映できたとみなす（`sprint_pr` が読み直して進める）。取得した
    リモートにまだ見えないコミットは返さない。見えるのに作業場所を進められなければ止める。`overdue`（反映の上限を超えた単位）は
    反映先の選定と止める判定に使わず、進めた後の HEAD が含むかだけを `pulls` とまとめて確かめる（`pulls` のどれも
    マージのコミットを持たなければ、取得せずに今の HEAD で確かめる）。
    """
    known = [p for p in pulls.values() if p.merge_sha]
    if not known:
        # 反映先が無いので取得せず、期限切れは今の HEAD が含むかを確かめる
        held = Git(ctx.sprint).contained((p.merge_sha for p in overdue.values()), "HEAD")
        return frozenset(pulls) | {k for k, p in overdue.items() if p.merge_sha in held}
    g = Git(ctx.sprint)
    tip = _fetch_sprint(g, ctx)
    head = _catch_up_to(g, ctx, known, tip)
    reached = {head} | g.contained((p.merge_sha for p in (*known, *overdue.values()) if p.merge_sha != head), head)
    taken = frozenset(k for k, p in {**pulls, **overdue}.items() if not p.merge_sha or p.merge_sha in reached)
    if g.contained((p.merge_sha for k, p in pulls.items() if k not in taken), tip):
        _check_sprint_branch(g, ctx)
        raise UnitsStopped("precondition", _tip_mismatch(g, ctx, tip, head))
    return taken


def _start_unit(
    ctx: Context, unit: Unit, branch: str, ports: Ports, read: Callable[[Sequence[int]], Mapping[int, units_queue.State]]
) -> units_flow.Started:
    """単位 1 つの、作業場所の用意 → 打ち直しの判定 → スプリントブランチの HEAD のマージまでを行い、`impl` の呼び出しと、
    その後のマージを返す。

    マージだけを打ち直す判定はスプリントブランチをマージする前の HEAD で行う（マージするとレビューした HEAD と合わなくなる）。
    """
    worktree = unit_worktree(ctx, branch, ports)
    state = units_flow.round_state(ctx.work_dir / UNITS_DIR / branch)
    g = Git(worktree)
    call = None
    round_dir = state.round_dir
    if units_flow.resume_of(state, g.rev_parse("HEAD"), ctx.local_tests).action != "merge":
        include_sprint(ctx, worktree)
        resume = units_flow.resume_of(state, g.rev_parse("HEAD"), ctx.local_tests)
        round_dir = _next_round(state) if resume.new_round else state.round_dir
        planned = unit_run(ctx, unit, worktree, round_dir, resume.start)

        def call() -> StageResult:
            return ports.call_plan(planned, round_dir)

    waiting_file = units_queue.queue_file(ctx.work_dir / UNITS_DIR / branch)

    def merge() -> tuple[dict[str, Any], github_pulls.MergedPull]:
        """マージした PR（マージの応答にマージコミットが無ければ `merge_sha` は空）を返す。"""
        try:
            report = merge_unit(ctx, branch, worktree, round_dir, ports)
        except units_flow.Queued as exc:
            number = _pr_number(round_dir)
            assert number is not None, "merge_unit は PR の番号が無ければマージせずに止まる"
            exc.ticket = units_queue.Ticket(number, waiting_file, merge)
            raise
        number = _pr_number(round_dir)
        assert number is not None, "merge_unit は PR の番号が無ければマージせずに止まる"
        return units_flow.unit_item(unit, number, "merged"), github_pulls.MergedPull(number, _merge_sha(report))

    def land(got: Optional[StageResult]) -> tuple[dict[str, Any], github_pulls.MergedPull]:
        if got is None:
            number = _pr_number(round_dir)
            if number is None:
                return merge()
            return units_flow.remerge(unit, units_queue.Ticket(number, waiting_file, merge), read, ports.clock)
        units_flow.write_round_record(round_dir, units_flow.STOPPED_HEAD_FILE, g.rev_parse("HEAD") + "\n")
        if got.status != result.OK:
            raise _unit_stop_of(got.report, "impl が止まった", _ci_note(ctx, got.report))
        return merge()

    return units_flow.Started(call, land)


def _usage_limited(got: StageResult) -> bool:
    """`impl` の結果が利用上限の停止か。"""
    return got.status != result.OK and _unit_stop_of(got.report, "").kind == USAGE_LIMIT


def _merge_sha(report: Mapping[str, Any]) -> str:
    """`ndf ci merge` の結果の PR の項目が持つマージコミット。無ければ空。"""
    rows = report["items"] if isinstance(report.get("items"), list) else []
    sha = next((r.get("merge_sha") for r in rows if isinstance(r, Mapping) and r.get("kind") == "pr"), None)
    return sha if isinstance(sha, str) else ""


def _next_round(state: units_flow.RoundState) -> Path:
    return state.round_dir.parent / str(int(state.round_dir.name) + 1)


def _pr_number(round_dir: Path) -> Optional[int]:
    held = units_flow.saved_head(round_dir / PR_NUMBER_FILE)
    return int(held) if held is not None and held.isdigit() else None


def _ci_note(ctx: Context, report: Mapping[str, Any]) -> str:
    """CI のステップで止まった単位の理由に足す、待つチェックの名前を読んだ設定のキー。"""
    if units_flow.stopped_step(report) != "ci":
        return ""
    return f"（待つチェック {', '.join(ctx.unit_ci['required'])} は {ctx.unit_ci['key']} の設定）"


def _unit_stop_of(report: Mapping[str, Any], what: str, note: str = "") -> UnitsStopped:
    """作業の止まった結果 JSON を、同じ停止理由の種類と `retry_after` のまま単位の停止にする。"""
    cause = report["cause"] if isinstance(report.get("cause"), Mapping) else {}
    kind = str(cause["kind"]) if "kind" in cause else "unknown"
    detail = cause["detail"] if isinstance(cause.get("detail"), str) else str(report["summary"])
    wait = cause.get("retry_after")
    return UnitsStopped(kind, f"{what}: {detail}{note}", retry_after=float(wait) if isinstance(wait, (int, float)) else None)


def _after_units(
    ctx: Context, items: list[dict[str, Any]], ports: Ports, landed: Callable[[str], Sequence[github_pulls.MergedPull]]
) -> StageResult:
    """すべての単位がマージ済みになった後に、スプリントの PR を開いた結果。開けなければ止まった単位を持たない `stopped`。

    最後の単位の PR を GitHub から読み直せないときも、PR を開けなかった停止として、すべての単位の項目と `metrics.unit_ci` を持たせる。
    """
    metrics = {"unit_ci": ctx.unit_ci}
    try:
        opened = sprint_pr(ctx, ports, landed)
    except UnitsStopped as exc:
        report = result.stopped(plan_runner.TOOL, exc.kind, exc.detail, retry_after=exc.retry_after, items=items, metrics=metrics)
        return StageResult(report, {})
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
    """単位の作業場所。スプリントブランチと単位のブランチを取得し直し、docs/specifications/ndf-implement-stage-review-units.md の「単位のブランチと作業場所」の表で用意する。

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


def include_sprint(ctx: Context, worktree: Path) -> None:
    """単位の作業場所がスプリントブランチの HEAD（取得済み）を含まなければ `git merge --no-edit` でマージする（I3）。

    衝突すれば、この関数が始めたマージだけを取り消して止める（呼ぶ前からあるマージは `unit_worktree` が先に止める）。
    """
    g = Git(worktree)
    tip = g.rev_parse(f"refs/remotes/{ctx.remote}/{ctx.branch}")
    if g.is_ancestor(tip, "HEAD"):
        return
    done = g.attempt("merge", "--no-edit", "-q", tip)
    if done.returncode != 0:
        g.attempt("merge", "--abort")
        detail = f"{ctx.branch} の HEAD をマージすると衝突するので、そのマージを取り消した: {mask_urls(done.stderr.strip() or done.stdout.strip())}"
        raise UnitsStopped("base_conflict", detail)


def unit_run(ctx: Context, unit: Unit, worktree: Path, round_dir: Path, start: Optional[str]) -> StageRun:
    """単位の範囲の指示を書き、単位の `impl` の呼び出し（上書きの値・テンプレート・環境変数）を組む。

    代表の課題の題と本文は、選んだリポジトリから工程の保存先へ写す。作成者を信用しなければ止める。補った単位は指示を書かない。
    """
    issued = issue_values.issue_values(ctx.run.inputs, ctx.work_dir, worktree, number=unit.issue, repo=ctx.repo)
    if issued is None:
        raise UnitsStopped("precondition", f"課題 #{unit.issue} の作成者に書き込み権限が無く、題と本文を作業へ渡さない")
    bodies = ctx.bodies[unit_key(unit.issue, unit.name)] if ctx.source and not unit.supplemented else []
    unit_file = "" if unit.supplemented else str(_write_unit_file(round_dir, unit, ctx.source, bodies))
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
    if ctx.run.sprint is not None and ctx.run.sprint.launched is not None:
        # 実装のプランの確かめ（guard）が照らす記録を、起動の前に残す（#3702 の決定 12）
        issues = [unit.issue, *map(int, route.acceptance_issues(overrides["acceptance"]))]
        ctx.run.sprint.launched(overrides["branch"], ctx.branch, issues=issues, unit_file=Path(unit_file) if unit_file else None)
    plain = ctx.run.plain()
    template = route.unit_template(route.templates()["impl"], ctx.repo, drop)
    inputs = {**plain.inputs, "worktree": str(worktree)}
    return replace(plain, inputs=inputs, overrides=overrides, template=template, env_extra=env, start=start)


def _task_texts(root: Path, source: str, units: Sequence[Unit]) -> dict[str, list[str]]:
    """全単位の担当タスクの本文を、コミット `source` の実装計画からまとめて読む（`units_files.task_texts`）。"""
    try:
        return units_files.task_texts(units_files.Tree(root, source), units)
    except units_files.UnitsFileError as exc:
        raise UnitsStopped("invalid", str(exc)) from None


def _write_unit_file(round_dir: Path, unit: Unit, source: str, bodies: Sequence[str]) -> Path:
    """単位の範囲の指示（決定 8）を回の保存先へ書き直す。条件は番号だけを書き、課題の本文は写さない。

    単位のファイルから流す単位は、担当タスクの所属の課題と、コミット `source` の実装計画の本文（依存の順）を持つ（#3702）。
    """
    criteria = "、".join(f"課題 #{i} の {n}" for i, n in unit.criteria) if unit.criteria else "無し"
    owners = dict(unit.task_ids)
    tasks = "、".join(f"Task {t}（課題 #{owners[f'T{t}']}）" if owners else f"Task {t}" for t in unit.tasks)
    text = f"""# レビューの単位: {unit.name}

- 実装計画: {unit.plan}
- 含むタスク: {tasks}
- 満たす受け入れ条件: {criteria}

この PR は、上の単位のタスクだけを実装する。後の単位のタスクは実装しない。

- 単位の外を変えてよいのは、今の単位の変更が原因でテスト・CI が失敗するか、レビューで指摘を受け、直すのに単位の外の変更が要る場合だけである。変更は最小にし、コミットメッセージに理由を書く
- 今の単位の変更が原因でない失敗（変更の前からある失敗、原因を確かめられない失敗）は直さない。直さない失敗が残る場合と、後の単位のタスクを実装しないと通らない場合は、理由を返して止まる
- 例外: 今の差分に含まれる秘密・認証認可・利用者のデータ・戻せない操作の問題は、担当の単位にかかわらず指摘し、直す。後の単位を理由に見送らない
"""
    if bodies:
        text += (
            f"\n## 担当タスクの作業内容\n\nコミット {source} の {unit.plan} の本文を、依存の順に写した。この本文を作業内容の正とし、"
            "作業場所の実装計画の本文には従わない。\n\n" + "\n\n".join(bodies) + "\n"
        )
    round_dir.mkdir(parents=True, exist_ok=True)
    with worker_paths.open_or_create(round_dir) as wd:
        worker_paths.write_file(wd, UNIT_FILE, text, private=True)
        return wd.path / UNIT_FILE


def merge_unit(ctx: Context, branch: str, worktree: Path, round_dir: Path, ports: Ports) -> dict[str, Any]:
    """`ndf ci merge` で単位の PR をマージし、結果の種類を `merge.json` に残す。

    PR の番号とレビューした HEAD は、同じ回の保存先から読む。どちらかが無ければマージを打たずに止める。
    PR がマージキューの待ちになれば `units_flow.Queued` で返す。
    head が最新でないので断られ、単位を始め直せば進むときは `units_flow.Outdated` で止める。
    スプリントの作業場所はここでは進めない（進めるのは `advance` と `sprint_pr`）。
    """
    number, reviewed = _pr_number(round_dir), units_flow.saved_head(round_dir / units_flow.REVIEWED_HEAD_FILE)
    if number is None or not reviewed:
        raise UnitsStopped("precondition", f"回の保存先 {round_dir} に PR の番号かレビューした HEAD の記録が無いので、マージしない")
    argv = ndf_command(
        "ci", "merge", "--repo", ctx.repo, "--pr", str(number), "--branch", branch, "--base", ctx.branch, f"--reviewed={reviewed}"
    )
    done = ports.command(argv, worktree, dict(os.environ))
    report = result.last_line(done.stdout)
    ok = report is not None and report["status"] == result.OK
    kind = "merged" if ok else ("bad_report" if report is None else _unit_stop_of(report, "").kind)
    units_flow.write_round_record(round_dir, units_flow.MERGE_FILE, json.dumps({"kind": kind}, ensure_ascii=False) + "\n")
    if report is None:
        raise UnitsStopped("bad_report", f"ndf ci merge が結果 JSON を返さない（終了コード {done.returncode}）")
    if not ok:
        stop = _unit_stop_of(report, f"PR #{number} をマージできない")
        if stop.kind == github_merge.MERGE_QUEUE_PENDING:
            raise units_flow.Queued(stop.kind, stop.detail, retry_after=stop.retry_after)
        g = Git(worktree)
        # 最新化を必須とするスプリントブランチは、別の単位のマージで古くなった HEAD を断る。HEAD がスプリントブランチの HEAD を
        # 含むのに `behind` なら、始め直しても変わらないので失敗にする
        if report["metrics"].get("behind") is True and not g.is_ancestor(_fetch_sprint(g, ctx), "HEAD"):
            raise units_flow.Outdated(stop.kind, stop.detail, retry_after=stop.retry_after)
        raise stop
    return report


def sprint_pr(ctx: Context, ports: Ports, landed: Callable[[str], Sequence[github_pulls.MergedPull]]) -> StageResult:
    """スプリントの作業場所を最後にマージした単位のマージコミット（`landed` のうち最も先のもの）まで進め、リモートのスプリントブランチの HEAD と同じことを
    確かめてから、テンプレート `pr` を push のステップなしで流す。`landed` はリモートの HEAD を受けて、マージした PR を返す。

    進めるのは、自分でマージした単位の結果までである。リモートの HEAD がそれと違えば、それ以上は進めず、push もせずに止める。
    違うコミットは単位のレビューを経ていないので、利用者が確かめてから揃えて打ち直す。打ち直しは `prepare` を通らない
    （`units.json` を読む）ので、手元を進めるのは利用者の操作だけである。
    """
    g = Git(ctx.sprint)
    tip = _fetch_sprint(g, ctx)
    head = _catch_up_to(g, ctx, landed(tip), tip)
    if tip != head:
        _check_sprint_branch(g, ctx)
        raise UnitsStopped("precondition", _tip_mismatch(g, ctx, tip, head))
    t = route.templates()["pr"]
    template = replace(t, plan=route.without_steps(route.expanded(t), SPRINT_PR_DROP))
    run = replace(ctx.run.plain(), name=t.name, template=template, overrides={"remote": ctx.remote}, env_extra={GH_REPO_ENV: ctx.repo})
    return ports.call_plan(run, ctx.work_dir / SPRINT_PR_DIR)


def _check_sprint_branch(g: Git, ctx: Context) -> None:
    """スプリントの作業場所が今もスプリントブランチにいなければ、切り替えだけを示して止める。

    別のブランチの HEAD で差分の範囲を示すと、利用者が確かめた範囲と、切り替えた後に進める範囲が食い違う。打ち直しで
    スプリントブランチの HEAD から範囲を取り直す。
    """
    current = _current_branch(g)
    if current != ctx.branch:
        command = shlex.join(["git", "-C", str(ctx.sprint), "switch", ctx.branch])
        detail = (
            f"スプリントの作業場所のブランチ {current or '（ブランチに居ない）'} が {ctx.branch} と違う。"
            f"{ctx.branch} へ切り替えてから ndf progress next を打ち直す。コマンド: {command}"
        )
        raise UnitsStopped("precondition", detail)


def _tip_mismatch(g: Git, ctx: Context, tip: str, head: str) -> str:
    """リモートの HEAD（`tip`）がスプリントブランチの HEAD（`head`）と違うときの停止の理由。両方の SHA、片方にだけあるコミットの
    範囲と、確かめた後の手順を書く。

    コマンドを示すのは、リモートだけが進んでいる（`head` が `tip` の祖先）ときの、確かめた `tip` までの fast-forward だけである。
    手元にだけあるコミットは、NDF から送る案内をしない（送ると単位のレビューもコードレビューも経ずに `gathered` の前に入る）。
    """
    found = (
        f"{ctx.remote} の {ctx.branch} の HEAD（{tip}）がスプリントの作業場所 {ctx.sprint} の HEAD（{head}）と違う。"
        f"リモートにだけあるコミットは {head}..{tip}、手元にだけあるコミットは {tip}..{head} である。"
        "これらは単位のレビューを経ていない。"
    )
    if g.is_ancestor(head, tip):
        command = shlex.join(["git", "-C", str(ctx.sprint), "merge", "--ff-only", tip])
        return (
            found
            + f"その変更をスプリントの PR に含めてよいと確かめたら、作業場所を進めてから ndf progress next を打ち直す。コマンド: {command}"
        )
    return found + (
        "手元にだけあるコミットは、NDF からは送らず、履歴も付け替えない。利用者本人が差分を確かめ、要る変更は別のブランチに"
        "コミットして、別の PR でレビューを経てマージする。未コミットの変更は git status で確かめ、ブランチを作るだけでは残らないので"
        f" git stash などで別に保全する。そのうえで作業場所を {ctx.remote} の {ctx.branch} の HEAD に揃えて、ndf progress next を打ち直す"
    )


def _catch_up_to(g: Git, ctx: Context, landed: Sequence[github_pulls.MergedPull], tip: str) -> str:
    """スプリントの作業場所を、`tip` の履歴にある単位のマージコミットのうち手元の HEAD から最も先のものまで fast-forward で
    進め、進めた後の HEAD を返す。並列に流すとマージの順は並びと違いうるので、祖先の関係で選ぶ。取得済みのリモートの HEAD（`tip`）が
    マージコミットの 1 つで、手元から進められれば、ほかの候補は調べない（Git の起動を単位の数に比例させない）。

    手元で読めるマージコミットが無いか、手元がその祖先でなければ進めない（違いは呼び手が利用者に知らせる）。進める直前に、
    作業場所が今もスプリントブランチにいることを確かめる（別のブランチをスプリントのマージ結果まで進めない）。
    """
    head = g.rev_parse("HEAD")
    target, number = head, 0
    newest = next((p for p in landed if p.merge_sha == tip), None)
    if newest is not None and (tip == head or g.is_ancestor(head, tip)):
        target, number = tip, newest.number
    else:
        in_tip = g.contained((p.merge_sha for p in landed if p.merge_sha != head), tip)
        for pull in landed:
            sha = pull.merge_sha
            if sha in in_tip and sha != target and g.is_ancestor(target, sha):
                target, number = sha, pull.number
    if target == head:
        return head
    _check_sprint_branch(g, ctx)
    moved = g.merge_ff(target)
    if moved.returncode != 0:
        raise UnitsStopped(
            "precondition", f"スプリントの作業場所を PR #{number} のマージ結果へ進められない: {mask_urls(moved.stderr.strip())}"
        )
    return g.rev_parse("HEAD")
