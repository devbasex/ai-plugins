"""工程「実装」を実装計画のレビューの単位ごとに流す処理（#4369 の詳細設計の「単位を流す処理」）。

ここにあるのは、開始前の確かめ（`prepare`）と、打ち直しで単位の回をどう続けるかの判定（`round_state`・`resume_of`）である。
開始前の確かめは、作業場所が今いるブランチをリモートから取得し、コミットされた実装計画に単位の見出しがあるかで経路を決める。
単位の経路なら、手元をリモートに揃え、表をすべて読み、ブランチをリモートへ送ってから並びを `units.json` に書く。
読み方と順序は設計書の「開始前の確かめ」、打ち直しの表は詳細設計書の「`resume_of` の判定表」にある。

`ndf.progress.invoke` を import しない（`invoke` がこのモジュールを呼ぶので、型は `plan_runs` が持つ）。
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import contracts
from ndf.adapters import github
from ndf.adapters.git import HEADS, Git
from ndf.contracts import facts, result
from ndf.core import branch_strategy, json_pointer
from ndf.core.issue_docs import plan_issues
from ndf.core.plan import files as plan_files
from ndf.core.plan import inputs_decide
from ndf.core.plan import runner as plan_runner
from ndf.core.plan.review_units_decide import PlanDoc, Unit, UnitsUnreadable, has_units, lineup
from ndf.progress import plan_runs, refactor_values
from ndf.progress.command import PROJECT_FILE, ndf_settings
from ndf.progress.issue_values import UNITS_FILE
from ndf.progress.plan_runs import StageRun, UnitsStopped

# 単位の回の保存先のファイル（`reviewed-head` は `impl` のプラン、`stopped-head` と `merge.json` は単位を流す処理が書く）
REVIEWED_HEAD_FILE = "reviewed-head"
STOPPED_HEAD_FILE = "stopped-head"
MERGE_FILE = "merge.json"
# 打ち直しの判定の、レビューのステップ（ほかのステップと停止理由の語は `resume_of` が持つ。定数にすると同じ語の別の事実と重なる）
REVIEW_STEPS = frozenset({"review", "review-delta", "resolved"})
# 単位の PR で待つチェックの設定のキー（`workflow` の下。書かなければ `ci.required_checks` を読む。#4369 の決定 13）
UNIT_CHECKS = "unit_required_checks"
# `git ls-remote --exit-code` が、該当する ref が無いときに返す終了コード
LS_REMOTE_ABSENT = 2
# 単位のブランチ名に付ける、単位の名前の sha256 の先頭の字数
UNIT_HASH_CHARS = 8


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
    tip = _fetched_tip(g, remote, branch) if branch is not None else None
    uncommitted = _uncommitted_plans(g, issues)
    seen = [*_plans_at(g, "HEAD", issues), *(_plans_at(g, tip, issues) if tip is not None else []), *_worktree_plans(root, uncommitted)]
    if not any(has_units(p.text) for p in seen):
        return _plain_route(work_dir)
    if uncommitted:
        detail = f"未コミットの実装計画がある: {', '.join(uncommitted)}。コミットするか取り消してから打ち直す"
        raise UnitsStopped("precondition", detail)
    project = ndf_settings(location, PROJECT_FILE)
    branch = _checked_branch(run, branch, project)
    if tip is not None:
        _catch_up(g, remote, branch, tip)
    plans = _plans_at(g, "HEAD", issues)
    if not any(has_units(p.text) for p in plans):
        return _plain_route(work_dir)
    units = _read_units(g, remote, plans, issues)
    pushed = g.attempt("push", "-q", "--", remote, f"HEAD:{HEADS}{branch}")
    if pushed.returncode != 0:
        raise UnitsStopped("precondition", f"{remote} の {branch} へ送れない: {pushed.stderr.strip()}")
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


def _fetched_tip(g: Git, remote: str, branch: str) -> Optional[str]:
    """リモートから取得したブランチの HEAD。リモートにブランチが無いと確かめられれば None、ほかの失敗は止める。"""
    fetched = g.fetch_branch(remote, branch)
    if fetched.returncode == 0:
        return g.rev_parse(f"refs/remotes/{remote}/{branch}")
    probe = g.attempt("ls-remote", "--exit-code", "--heads", remote, f"{HEADS}{branch}")
    if probe.returncode == LS_REMOTE_ABSENT:
        return None
    raise UnitsStopped("precondition", f"{remote} の {branch} を取得できない: {fetched.stderr.strip() or probe.stderr.strip()}")


def _is_sprint_plan(path: str, issues: Sequence[int]) -> bool:
    """スプリントの課題の実装計画のパスか（名前の番号のどれかがスプリントの課題）。"""
    return bool(plan_issues(path) & set(issues))


def _plans_at(g: Git, commit: str, issues: Sequence[int]) -> list[PlanDoc]:
    """コミットにあるスプリントの課題の実装計画。"""
    names = [p for p in g.run("ls-tree", "-z", "--name-only", commit, "--", f"{facts.value('locations', 'issues', 'path')}/").split("\0") if p]
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
        raise UnitsStopped("precondition", "スプリントに登録されたブランチ（文脈の branch）が無い。工程「作業場所の用意」を流してから打ち直す")
    if branch != registered:
        detail = f"作業場所のブランチ {branch or '（ブランチに居ない）'} が、スプリントに登録されたブランチ {registered} と違う。{registered} へ切り替えてから打ち直す"
        raise UnitsStopped("precondition", detail)
    try:
        released = released_branches(branch_strategy.of_project(project).section)
    except contracts.ContractError as exc:
        raise UnitsStopped("precondition", f"ブランチ戦略の設定を読めない: {exc}") from None
    if branch in released:
        detail = f"ブランチ {branch} は {PROJECT_FILE} の branch_strategy がリリース先に挙げている。単位はスプリントブランチの作業場所で流す"
        raise UnitsStopped("precondition", detail)
    return registered


def _catch_up(g: Git, remote: str, branch: str, tip: str) -> None:
    """手元が遅れていればリモートの HEAD まで進める。分かれていれば止める（強制しない）。"""
    head = g.rev_parse("HEAD")
    if head == tip or g.is_ancestor(tip, head):
        return
    if not g.is_ancestor(head, tip):
        detail = f"手元の {branch}（{head}）と {remote} の {branch}（{tip}）が分かれている。揃えてから打ち直す"
        raise UnitsStopped("precondition", detail)
    done = g.merge_ff(f"refs/remotes/{remote}/{branch}")
    if done.returncode != 0:
        raise UnitsStopped("precondition", f"{branch} を {remote} の HEAD へ進められない: {done.stderr.strip()}")


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


def round_state(unit_dir: Path) -> RoundState:
    """`run.json` を持つ最新の回の、結果・マージの結果・`reviewed-head`・`stopped-head`。記録の無い空の回は数えない。"""
    rounds = sorted(
        (n, p) for p in (unit_dir.iterdir() if unit_dir.is_dir() else []) if (n := _round_number(p)) is not None and (p / plan_runner.RUN_FILE).is_file()
    )
    if not rounds:
        return RoundState(unit_dir / "1", False, None, None, None, None)
    path = rounds[-1][1]
    _, done = plan_runs.previous(path)
    merged = plan_files.read_json(path / MERGE_FILE)
    kind = merged.get("kind") if merged is not None else None
    return RoundState(
        path, True, done, kind if isinstance(kind, str) else None, _saved_head(path / REVIEWED_HEAD_FILE), _saved_head(path / STOPPED_HEAD_FILE)
    )


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
