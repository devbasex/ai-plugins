"""この変更より前のスプリントの、実装計画の単位の表を単位のファイルへ変換する（#3702 の決定 10。
issues/issue-3702-design-detail.md の「この変更より前のスプリントの変換」）。

工程「実装」の開始前の確かめ（`units_invoke.prepare`）が、`progress.run` の持たせた `convert` を呼ぶ。変換の担当は
プラン `implementation-plan-convert` で、送り先の HEAD から作る一時の作業場所にコミットする。コミットは旧い表との突き合わせか再承認が
済むまでローカルの ref（`refs/ndf/convert/<ステートマシン>`）だけに置き、スプリントの作業場所は push が通るまで変えない。
打ち直しは、手元に持つコミット・送り先の単位のファイル・`convert.json` から続きを決める。
"""

from __future__ import annotations

import functools
import hashlib
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf.adapters.git import HEADS, Git, github_target, mask_urls
from ndf.contracts import result
from ndf.core import route
from ndf.core.plan import files as plan_files
from ndf.core.plan import runner as plan_runner
from ndf.core.plan import units_files
from ndf.core.plan import units_decide as ud
from ndf.core.plan.review_units_decide import (
    PlanDoc,
    Unit,
    UnitsUnreadable,
    has_units,
    lineup_with_waits,
    unit_key,
    without_sections,
)
from ndf.progress import design_record, folding, invoke, run_view, stages, store, units_flow, units_guard, units_invoke, units_sync
from ndf.progress.plan_runs import ConvertRequest, SprintPlan, StageResult, UnitsStopped

# スプリントの作業の保存先に置く、送ったコミットと確かめに使った承認の記録（`{"commit", "approval": {"kind", "head"}}`）
CONVERT_FILE = units_invoke.CONVERT_FILE
# 違反が 2 回続いたときの違反の一覧
VIOLATIONS_FILE = "convert-violations.json"
# 再承認の資料の名前に付ける、中身の sha256 の先頭の字数（提示ごとに別のファイルで、書いた後は書き換えない）
MATERIAL_HASH_CHARS = 16
# 担当のプランの実行の保存先（スプリントの作業の保存先の下。起動ごとに番号を分ける）
PLAN_DIR = "convert-plan"
TEMPLATE = "implementation-plan-convert"
REF_PREFIX = f"refs/{result.ENTRY}/convert/"
# 送り先の HEAD を受ける一時の ref。同じリポジトリの別の fetch が書き換える `FETCH_HEAD` は読まない
TIP_PREFIX = f"refs/{result.ENTRY}/convert-tip/"
# 一時の作業場所の親（メインディレクトリの下。`.git` の下は担当が書けない）
TREE_PARENT = Path(".worktrees") / "ndf-convert"
# 1 回の `convert` で担当を起動する上限（最初の変換と、やり直し 1 回。競合のやり直しも数える）
MAX_LAUNCHES = 2
REJECTED = folding.answer_word("rejected")
_COMMIT_MESSAGE = "Docs: 実装計画の単位を単位のファイルへ変換する"


class Job(NamedTuple):
    """変換が読むスプリントの保存した状態。`machine_repo` はステートマシンの `repo`（作成の時点のリモートの URL）を読む。"""

    view: run_view.View
    context: Mapping[str, Any]
    machine_repo: Callable[[], str]
    state_root: Path
    work_root: Path


# --- スプリントの保存した状態 --------------------------------------------------------------------


def machine_repo(st: store.Store, machine_id: str) -> str:
    """ステートマシンの `repo`（作成の時点のリモートの URL）。"""
    with store.transaction(st) as conn:
        meta = store.read_machine(conn, machine_id)
    assert meta is not None, "流しているステートマシンはある"
    return str(meta["repo"])


def sprint_plan(v: run_view.View, s: stages.StageDef, root: Path, repo: Callable[[], str] = str) -> Optional[SprintPlan]:
    """単位ごとに流す工程の呼び出しに持たせる、承認の要否・承認の記録・省いた課題（#3702 の決定 11）。ほかの工程は None。

    `root` は状態の保存先。比較の関数は、記録と今の文脈を持たせて渡す（単位を流す処理は `design_record` を import できない）。
    この変更より前の定義の工程「実装」（`per_unit` の無いものを含む）には、単位のファイルへの変換と、その送り先の読み取り、
    保存した並びを使い直せるかの確かめを持たせる（決定 10）。`repo` はステートマシンの `repo` を読む。
    """
    if not (s.per_unit or s.id == design_record.IMPLEMENT_DEFINITION.stem):
        return None
    context = v.context()
    omitted = tuple(context["plan_omitted"]) if "plan_omitted" in context else ()
    required = design_record.required(v.snap)
    record = design_record.approved_of(v) if required else None
    compared = {"issues": context["issues"] if "issues" in context else [], "plan_omitted": list(omitted)}
    work = run_view.work_root(root, v.machine_id)
    new_definition = "plan_omitted" in s.optional_inputs
    job = Job(v, context, repo, root, work)
    return SprintPlan(
        convert=None if new_definition else functools.partial(convert, job),
        destination=None if new_definition else functools.partial(destination, job),
        verify_fetch=None if new_definition else functools.partial(check_fetch, job),
        reusable=None if new_definition else functools.partial(still_converted, job),
        branch=context["branch"] if "branch" in context else None,
        required=required,
        record=record,
        differences=None if record is None else lambda tree: design_record.mismatches(record, tree, compared),
        omitted=omitted,
        new_definition=new_definition,
        work_root=work,
        launched=None
        if record is None
        else functools.partial(units_guard.record_launch, work, approval=units_guard.Approval(record, design_record.approval_seq_of(v))),
    )


# --- 旧い表との比較 ------------------------------------------------------------------------------

_Key = tuple[str, str]  # (実装計画のパス, 単位の名前)


def _all_waits(edges: Mapping[_Key, set[_Key]]) -> dict[_Key, set[_Key]]:
    """単位ごとの、間接を含めて先に要る単位。"""
    out: dict[_Key, set[_Key]] = {}
    for key in edges:
        seen: set[_Key] = set()
        stack = list(edges[key])
        while stack:
            found = stack.pop()
            if found not in seen:
                seen.add(found)
                stack += edges[found] if found in edges else ()
        out[key] = seen
    return out


def compare_tables(old: Sequence[Unit], new: Sequence[ud.View]) -> list[str]:
    """旧い表（`lineup_with_waits`）と比べた違い。単位の名前と含むタスク、消えた待ち、根拠の無い待ち。

    根拠のある待ちは、旧い表の先に要る単位の辺と、触るファイルが重なる 2 単位の間の新しい依存と同じ向きの辺をつないで、
    間接を含めてたどれる組である。実行順の番号は比べない。
    """
    old_of = {(u.plan, u.name): u for u in old}
    new_of = {(units_files.paired_plan(v.file), v.name): v for v in new if v.file}
    found = [f"単位 {name}（{plan}）が変換の後に無い" for plan, name in sorted(old_of.keys() - new_of.keys())]
    found += [f"単位 {name}（{plan}）は承認した表に無い" for plan, name in sorted(new_of.keys() - old_of.keys())]
    common = sorted(old_of.keys() & new_of.keys())
    for key in common:
        was, now = sorted(old_of[key].tasks), sorted(int(t[1:]) for t in new_of[key].tasks)
        if was != now:
            found.append(f"単位 {key[1]} の含むタスクが違う（承認 {was}、今 {now}）")
    names = {unit_key(u.issue, u.name): key for key, u in old_of.items()}
    old_edges = {key: {names[a] for a in u.after if a in names} for key, u in old_of.items()}
    plan_of_file = {v.file: units_files.paired_plan(v.file) for v in new if v.file}
    new_edges = {key: {(plan_of_file[f], n) for f, n, _ in v.after if f in plan_of_file} for key, v in new_of.items()}
    was_waits, waits = _all_waits(old_edges), _all_waits(new_edges)
    for waiting in common:
        lost = was_waits[waiting] - waits[waiting]
        found += [f"単位 {waiting[1]} が {name} を待たなくなる" for plan, name in sorted(lost) if (plan, name) in new_of]
    overlap = {a: {b for b in waits[a] if set(new_of[a].touches) & set(new_of[b].touches)} for a in new_of}
    justified = _all_waits({a: (old_edges[a] if a in old_edges else set()) | overlap[a] for a in new_of})
    for waiting in common:
        extra = sorted(waits[waiting] - justified[waiting])
        found += [f"単位 {waiting[1]} が根拠無く {name} を待つ" for plan, name in extra if (plan, name) in old_of]
    return found


# --- 変換 -----------------------------------------------------------------------------------------


class _Run(NamedTuple):
    """1 回の `convert` で変わらない値。`git` はスプリントの作業場所、`tip` は送り先の URL から読んだブランチの HEAD。"""

    job: Job
    request: ConvertRequest
    ports: units_invoke.Ports
    git: Git
    root: Path
    counts: dict[int, int]

    @property
    def ref(self) -> str:
        return REF_PREFIX + _ref_name(self.job)

    @property
    def issues(self) -> tuple[int, ...]:
        return tuple(self.request.issues)

    @property
    def tree_path(self) -> Path:
        main = self.git.worktrees()[0].path
        return main / TREE_PARENT / self.ref.removeprefix(REF_PREFIX)


class _Checked(NamedTuple):
    """読み元 1 つの確かめ。`bad` があれば `report` は使わない。"""

    report: Optional[units_files.Report]
    bad: list[str]


def _ref_name(job: Job) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", job.view.machine_id).strip("-")


def convert(job: Job, request: ConvertRequest, ports: units_invoke.Ports, answer: Optional[str]) -> Optional[StageResult]:
    """変換の続きから進める。実装へ進めるなら None、再承認が要れば `gate` の結果。止めるときは `UnitsStopped`。

    判定には、手元の HEAD でなく送り先の HEAD を使う。手元が遅れていても、再承認を待つ間と却下の後は手元を動かさない。
    None を返すときは、手元を確かめた送り先の HEAD まで進めてあるので、呼び手は送らない。
    """
    g = Git(request.sprint_dir)
    root = Path(g.run("rev-parse", "--show-toplevel").strip())
    r = _Run(job, request, ports, Git(root), root, dict(request.count(request.issues)))
    held = r.git.commit_or_none(r.ref)
    if answer == REJECTED:
        _drop_held(r)
        raise UnitsStopped("refused", "単位の分け直しの再承認が却下されたので、実装を始めない")
    url, tip = request.destination if request.destination is not None else destination(job, r.git, request.branch)
    if tip is None:
        raise UnitsStopped("precondition", f"送り先に {request.branch} が無いので、変換しない")
    local = r.git.rev_parse("HEAD")
    if held is not None and r.git.is_ancestor(held, tip):
        _drop_held(r)  # push の後に中断した打ち直し
        held = None
    # 送り先に無いコミットは、再承認の資料の対象にも変換の送信にも含めない
    if not r.git.is_ancestor(local, tip):
        raise UnitsStopped("refused", f"手元の {request.branch}（{local}）に送り先に無いコミットがあるので、送らない")
    if held is None:
        # 変換の記録と合えば先に進む（実装の後に表の追記が単位のファイルとずれても、変換し直さない）
        if _matches_conversion(r.git, r.root, job.work_root, tip, r.issues):
            _fast_forward(r, local, tip)
            return None
        checked = _check_units(r, tip)
        if checked.report is not None and not checked.bad:
            asked = _published(r, url, tip, checked.report)
            if asked is None:
                _fast_forward(r, local, tip)
            return asked
    return _reflect(r, held, url, tip)


def _fast_forward(r: _Run, local: str, commit: str) -> None:
    """手元（`local`）を、送り先に届いているコミット `commit` まで fast-forward で進める。"""
    if local == commit:
        return
    moved = r.git.merge_ff(commit)
    if moved.returncode != 0:
        raise UnitsStopped("precondition", f"作業場所を {commit} へ進められない: {mask_urls(moved.stderr.strip())}")


def _plans(r: _Run, commit: str) -> list[PlanDoc]:
    return units_flow.plans_at(r.git, commit, r.issues)[0]


def _check_units(r: _Run, commit: Optional[str], root: Optional[Path] = None) -> _Checked:
    """コミット `commit`（None なら作業場所 `root` のファイル）の単位のファイルと表の確かめ（`ndf plan units check --docs`）。"""
    tree = units_files.Tree(root or r.root, commit)
    try:
        if commit is None:
            paths = [n for n in tree.names() if units_flow.is_sprint_plan(n, r.issues)]
            plans = units_flow.worktree_plans(tree.root, paths)
        else:
            plans = _plans(r, commit)
        targets = units_files.supplemented(tree, r.issues, r.request.omitted, plans)
        limits = units_files.limits_of(units_files.project_of(tree))
        report = units_files.check_collected(tree, targets, limits, r.counts, True)
    except units_files.UnitsFileError as exc:
        return _Checked(None, [str(exc)])
    return _Checked(report, [f"{f.rule} {f.unit}: {f.reason}" for f in units_files.findings(report)])


def _drop_held(r: _Run) -> None:
    r.git.run("update-ref", "-d", r.ref)
    _remove_tree(r)


def _remove_tree(r: _Run) -> None:
    path = r.tree_path
    if path.exists():
        r.git.attempt("worktree", "remove", "--force", str(path))
        shutil.rmtree(path, ignore_errors=True)
    r.git.attempt("worktree", "prune")


def _new_tree(r: _Run, commit: str) -> Path:
    _remove_tree(r)
    r.tree_path.parent.mkdir(parents=True, exist_ok=True)
    r.git.run("worktree", "add", "-q", str(r.tree_path), commit)  # コミットを指すので detached HEAD になる
    return r.tree_path


def _sprint_repo(job: Job) -> str:
    """ステートマシンの `repo` を解析した `<所有者>/<リポジトリ>`。"""
    machine = github_target(job.machine_repo())
    if machine is None:
        raise UnitsStopped("refused", "スプリントのリポジトリを解析できないので、変換しない")
    return machine.repo


def check_fetch(job: Job, git: Git, repo: Optional[str] = None) -> None:
    """取得の URL がスプリントのリポジトリ（`repo` を省けば解析する）を指すことを確かめる。通信しない。違えば `refused`。"""
    repo = _sprint_repo(job) if repo is None else repo
    fetch = github_target(git.remote_url())
    if fetch is None or fetch.repo.casefold() != repo.casefold():
        now = "解析できない" if fetch is None else fetch.repo
        raise UnitsStopped("refused", f"取得の URL のリポジトリがスプリントと違う（スプリント {repo}、今 {now}）ので、変換しない")


def checked_url(job: Job, git: Git) -> str:
    """確かめた送り先の URL。通信しない。

    push の URL と取得の URL の両方が、スプリントのリポジトリであることを求める。単位を流す処理は、受け入れ条件の取得と
    単位の起動で使うリポジトリを取得の URL から決めるためである。違えば `UnitsStopped`（`refused`）。
    """
    repo = _sprint_repo(job)
    url, errors = units_sync.push_url(git, repo)
    if url is None:
        raise UnitsStopped("refused", "送り先を確かめられないので、変換しない: " + "; ".join(errors))
    check_fetch(job, git, repo)
    return url


def destination(job: Job, git: Git, branch: str) -> tuple[str, Optional[str]]:
    """確かめた送り先の URL と、そこから読んだスプリントブランチの HEAD（送り先にブランチが無ければ None）。

    取得は 1 回で、読むための一時の ref と、リモート追跡の ref の両方を更新する。
    """
    url = checked_url(job, git)
    ref = TIP_PREFIX + _ref_name(job)
    source = f"{HEADS}{branch}"
    tracking = f"refs/remotes/{git.remote()}/{branch}"
    done = git.fetch(url, f"+{source}:{ref}", f"+{source}:{tracking}")
    if done.returncode != 0:
        if units_flow.probe_branch(git, url, branch).returncode == units_flow.LS_REMOTE_ABSENT:
            return url, None
        raise UnitsStopped("precondition", f"送り先の {branch} を読めない: {mask_urls(done.stderr.strip())}")
    tip = git.rev_parse(ref)
    git.run("update-ref", "-d", ref)
    return url, tip


# --- 担当の起動 -----------------------------------------------------------------------------------


def _commit_leftover(r: _Run, tree: Path) -> None:
    """担当がコミットせずに残した文書の変更をコミットする。"""
    g = Git(tree)
    if g.status_paths():
        g.run("add", "--all", "--", "issues")
        g.attempt("commit", "-q", "-m", _COMMIT_MESSAGE)


def _adopt(r: _Run, tip: str) -> Optional[str]:
    """中断で残った一時の作業場所の、確かめを通る中身をコミットして返す。無ければ None。"""
    tree = r.tree_path
    if not (tree / ".git").exists():
        return None
    if _check_units(r, None, tree).bad:
        return None
    _commit_leftover(r, tree)
    head = Git(tree).rev_parse("HEAD")
    return None if r.git.is_ancestor(head, tip) else head


def _launch_worker(r: _Run, tip: str) -> str:
    """変換の担当を送り先の HEAD から作る一時の作業場所で流し、できたコミット（変えていなければ `tip`）を返す。"""
    tree = _new_tree(r, tip)
    run = r.request.run
    plans = r.job.work_root / PLAN_DIR
    number = len(list(plans.glob("*"))) + 1 if plans.is_dir() else 1
    template = route.templates()[TEMPLATE]
    planned = replace(
        run.plain(),
        name=template.name,
        template=template,
        outputs=(),
        inputs={**run.inputs, "worktree": str(tree), "branch": r.request.branch},
        overrides={"convert": "convert", "branch": r.request.branch, "worktree": str(tree)},
        env_extra={},
        start=None,
        notes=None,
        sprint=None,
    )
    got = r.ports.call_plan(planned, plans / str(number))
    if got.status != result.OK:
        _remove_tree(r)
        raise units_invoke._unit_stop_of(got.report, "単位のファイルへの変換が止まった")
    _commit_leftover(r, tree)
    return Git(tree).rev_parse("HEAD")


def _hold(r: _Run, commit: str) -> None:
    r.git.run("update-ref", r.ref, commit)
    _remove_tree(r)


def _onto(r: _Run, commit: str, tip: str) -> Optional[str]:
    """`commit` の変更だけを `tip` に載せたコミット。競合すれば None。"""
    if r.git.commit_or_none(f"{commit}^") == tip:
        return commit
    tree = _new_tree(r, tip)
    g = Git(tree)
    done = g.attempt("cherry-pick", "--allow-empty", commit)
    made = g.rev_parse("HEAD") if done.returncode == 0 else None
    _remove_tree(r)
    return made


# --- 反映 -----------------------------------------------------------------------------------------


def _changed_outside(r: _Run, tip: str, commit: str) -> list[str]:
    """実装計画の 2 つのセクションの外を変えた点。"""
    before = {p.path: p.text for p in _plans(r, tip)}
    after = {p.path: p.text for p in _plans(r, commit)}
    titles = (units_files.PARALLEL_TITLE,)
    changed = [
        path
        for path in sorted(before.keys() | after.keys())
        if before.get(path) != after.get(path)
        and (path not in before or path not in after or without_sections(before[path], titles) != without_sections(after[path], titles))
    ]
    return [f"{path}: 「レビューの単位」と「並列と直列」の外を変えた" for path in changed]


def _violations(r: _Run, tip: str, commit: str) -> _Checked:
    if commit == tip:
        return _Checked(None, ["担当がコミットを作らなかった"])
    checked = _check_units(r, commit)
    bad = [*_changed_outside(r, tip, commit), *checked.bad]
    return _Checked(checked.report, bad)


def _candidate(r: _Run, held: Optional[str], tip: str) -> tuple[str, units_files.Report]:
    """送り先の HEAD に載る、確かめを通ったコミットとその確かめ。担当の起動は `MAX_LAUNCHES` 回まで。"""
    launches = 0
    commit = held if held is not None else _adopt(r, tip)
    while True:
        if commit is None:
            commit = _launch_worker(r, tip)
            launches += 1
        send = _onto(r, commit, tip)
        found = _Checked(None, ["送り先の HEAD に載せると競合する"]) if send is None else _violations(r, tip, send)
        if send is not None and not found.bad:
            assert found.report is not None
            return send, found.report
        if launches >= MAX_LAUNCHES:
            _hold(r, send or commit)
            path = r.job.work_root / VIOLATIONS_FILE
            plan_files.write_json(path, {"commit": send or commit, "violations": found.bad})
            raise UnitsStopped("invalid", f"変換が {launches} 回続けて通らない（{path}）: {'; '.join(found.bad)}")
        commit = None


def _current_targets(r: _Run, report: units_files.Report) -> dict[str, Any]:
    """記録との比較（`design_record.mismatches`）に渡す、今の実行対象。課題全体を 1 単位にした課題は省いた課題に数える。"""
    return {"issues": list(r.issues), "plan_omitted": sorted({*r.request.omitted, *(f.issues[0] for f in report.files if f.whole)})}


def _plan_texts(r: _Run, commit: str, sections: bool = True) -> dict[str, str]:
    titles = (units_files.PARALLEL_TITLE,)
    return {p.path: p.text if sections else without_sections(p.text, titles) for p in _plans(r, commit)}


def _differences_from_record(r: _Run, record: Mapping[str, Any], commit: str, report: units_files.Report) -> list[str]:
    """再承認の記録と送るコミットの違い（単位のファイル・実行対象と、実装計画の全文）。"""
    found = list(design_record.mismatches(record, units_files.Tree(r.root, commit), _current_targets(r, report)).differences)
    head = str(record["review"]["head"])
    if not found and _plan_texts(r, head) != _plan_texts(r, commit):
        found.append("実装計画の中身が承認した内容と違う")
    return found


def _old_approved_head(r: _Run, url: str) -> Optional[str]:
    """旧い設計承認の HEAD。回答が無ければ None。手元にも送り先にも無ければ止める。"""
    head = run_view.approved_head(r.job.view, design_record.GATE, r.job.state_root)
    if head is None:
        return None
    if r.git.commit_or_none(head) is None and (r.git.fetch(url, head).returncode != 0 or r.git.commit_or_none(head) is None):
        raise UnitsStopped("precondition", f"旧い設計承認の HEAD {head} を読めない")
    return head


def _old_table(r: _Run, head: str) -> Optional[list[PlanDoc]]:
    """旧い設計承認の HEAD の実装計画。単位の表が無ければ None（承認した分け方が無い）。"""
    plans = _plans(r, head)
    return plans if any(has_units(p.text) for p in plans) else None


def _differences_from_table(r: _Run, head: str, commit: str, report: units_files.Report) -> list[str]:
    plans = _old_table(r, head)
    if plans is None:
        return []
    try:
        found = compare_tables(lineup_with_waits(plans, r.counts, r.issues), report.judgement.views)
    except UnitsUnreadable as exc:
        found = [f"承認した表を読めない: {reason}" for reason in exc.reasons]
    if _plan_texts(r, head, sections=False) != _plan_texts(r, commit, sections=False):
        found.append("実装計画の本文が、設計承認の後に書き換わった")
    return found


def _reflect(r: _Run, held: Optional[str], url: str, tip: str) -> Optional[StageResult]:
    """旧い表との比較か再承認を確かめてから、送るコミットを push して作業場所を進める。"""
    send, report = _candidate(r, held, tip)
    _hold(r, send)
    record = design_record.reapproved_of(r.job.view)
    old = None
    if record is not None:
        differences = _differences_from_record(r, record, send, report)
    else:
        old = _old_approved_head(r, url)
        differences = [] if old is None else _differences_from_table(r, old, send, report)
    if differences:
        return _ask_again(r, send, report, differences, record, old if record is None else _old_approved_head(r, url))
    approval = {"kind": "reapproval", "head": record["review"]["head"]} if record is not None else {"kind": "design", "head": old}
    _push_conversion(r, url, tip, send, report, approval)
    _fast_forward(r, r.git.rev_parse("HEAD"), send)
    _drop_held(r)
    return None


def _record_conversion(r: _Run, commit: str, approval: Mapping[str, Any]) -> None:
    plan_files.write_json(r.job.work_root / CONVERT_FILE, dict(commit=commit, approval=dict(approval)))


def _converted_record(r: _Run, commit: str, report: units_files.Report) -> dict[str, Any]:
    machine = github_target(r.job.machine_repo())
    assert machine is not None, "送り先の確かめ（`checked_url`）か保存したステートマシンが先に確かめる"
    limits = units_files.limits_of(units_files.project_of(units_files.Tree(r.root, commit)))
    return design_record.converted(machine.repo, commit, r.request.branch, {"issues": list(r.issues)}, limits, report)


def _push_conversion(r: _Run, url: str, tip: str, send: str, report: units_files.Report, approval: Mapping[str, Any]) -> None:
    """承認した文書の反映と同じ送り先と送るコミットの確かめを通してから push する。強制はしない。"""
    record = _converted_record(r, send, report)
    _, errors = units_sync.destination_errors(r.git, record, r.job.context, r.job.machine_repo(), url)
    names = units_files.Tree(r.root, send).names()
    allowed = [*units_files.paths_naming(names, r.issues), *(p.path for p in _plans(r, send))]
    errors += units_sync.outgoing_errors(r.git, tip, send, allowed)
    if errors:
        raise UnitsStopped("refused", "変換のコミットを送らない: " + "; ".join(errors))
    # push の後に中断しても、再開で何を送ったかを読めるように、先に記録する
    _record_conversion(r, send, approval)
    refspec = f"{send}:{HEADS}{r.request.branch}"
    done = r.git.attempt("push", "--no-follow-tags", "--recurse-submodules=no", "--", url, refspec)
    if done.returncode != 0:
        raise UnitsStopped("precondition", f"{r.request.branch} への push が拒否された: {mask_urls(done.stderr.strip())}")


# --- 反映済み -------------------------------------------------------------------------------------


def _same_unit_files(root: Path, issues: Sequence[int], commit: str, head: str) -> bool:
    """スプリントの課題の単位のファイルが、実際の行数を除いて同じか。"""
    old, new = units_files.Tree(root, commit), units_files.Tree(root, head)
    try:
        before = units_files.paths_naming(old.names(), issues)
        after = units_files.paths_naming(new.names(), issues)
        return before == after and not any(units_files.differs(units_files.load(old, p), units_files.load(new, p)) for p in before)
    except units_files.UnitsFileError:  # noqa: NDF401 利用者の入力: 読めないファイルは同じでないとみなし、再承認で理由を示す
        return False


def _matches_conversion(git: Git, root: Path, work_root: Path, head: str, issues: Sequence[int]) -> bool:
    """変換の記録のコミットが `head` の祖先で、単位のファイルがそのコミットと同じか。"""
    saved = plan_files.read_json(work_root / CONVERT_FILE)
    if saved is None or git.commit_or_none(str(saved["commit"])) is None:
        return False
    commit = str(saved["commit"])
    return git.is_ancestor(commit, head) and _same_unit_files(root, issues, commit, head)


def still_converted(job: Job, location: Path, source: str) -> bool:
    """保存した単位の並び（本文を読むコミットが `source`）を使い直せるか。通信しない。

    並びが変換の記録のコミットから作ったもので、手元の HEAD の単位のファイルがそのコミットと同じときだけ使い直す。
    使い直せるときも、リモートの URL がスプリントのリポジトリでなくなっていれば `checked_url` が止める。
    """
    saved = plan_files.read_json(job.work_root / CONVERT_FILE)
    if saved is None or str(saved["commit"]) != source:
        return False
    root = Path(Git(location).run("rev-parse", "--show-toplevel").strip())
    git = Git(root)
    issues = tuple(sorted(int(n) for n in job.context["issues"]))
    if not _matches_conversion(git, root, job.work_root, git.rev_parse("HEAD"), issues):
        return False
    checked_url(job, git)
    return True


def _published(r: _Run, url: str, head: str, report: units_files.Report) -> Optional[StageResult]:
    """送り先に確かめを通る単位のファイルがあり、変換の記録と合わない打ち直し。再承認の記録と合えば進み、ほかは再承認を求める。"""
    record = design_record.reapproved_of(r.job.view)
    if record is not None:
        approved = str(record["review"]["head"])
        if (
            r.git.commit_or_none(approved) is not None
            and r.git.is_ancestor(approved, head)
            and _same_unit_files(r.root, r.issues, approved, head)
            and not _differences_from_record(r, record, head, report)
        ):
            _record_conversion(r, approved, {"kind": "reapproval", "head": approved})
            return None
    why = ["変換の記録の無い単位のファイルがあるか、変換の後に単位のファイルか実装計画が書き換わった"]
    if record is not None:
        why += _differences_from_record(r, record, head, report)
    return _ask_again(r, head, report, why, record, _old_approved_head(r, url))


# --- 再承認 ---------------------------------------------------------------------------------------


def _old_rows(r: _Run, head: Optional[str]) -> list[str]:
    plans = None if head is None else _old_table(r, head)
    if plans is None:
        return ["（承認した表は無い）"]
    try:
        units = lineup_with_waits(plans, r.counts, r.issues)
    except UnitsUnreadable as exc:
        return [f"（承認した表を読めない: {'; '.join(exc.reasons)}）"]
    return [
        "| 単位 | 含むタスク | 先に要る単位 |",
        "| --- | --- | --- |",
        *(f"| {u.name} | {'・'.join(map(str, u.tasks))} | {'・'.join(a.split(':', 1)[1] for a in u.after) or '無し'} |" for u in units),
    ]


def _reapproval_material(
    r: _Run, commit: str, report: units_files.Report, differences: Sequence[str], record: Optional[Mapping[str, Any]], old: Optional[str]
) -> str:
    previous = str(record["review"]["head"]) if record is not None else old
    paths = [p.path for p in _plans(r, commit)]
    diff = "（前回の承認は無い）"
    if previous is not None and r.git.commit_or_none(previous) is not None:
        changed = r.git.run("diff", previous, commit, "--", *paths) if paths else ""
        diff = f"```diff\n{changed}```"
    lines = [
        "# 単位の分け直しの再承認",
        "",
        "## 再承認が必要になった理由",
        "",
        *(f"- {d}" for d in differences),
        "",
        "## 承認した表",
        "",
        *_old_rows(r, old),
        "",
        "## 新しい分け方",
        "",
        *units_files.item_table(units_files.unit_items(report)),
        "",
        f"## 実装計画の差分（{previous or '無し'} → {commit}）",
        "",
        diff,
        "",
    ]
    return "\n".join(lines)


def _ask_again(
    r: _Run,
    commit: str,
    report: units_files.Report,
    differences: Sequence[str],
    record: Optional[Mapping[str, Any]],
    old: Optional[str],
) -> StageResult:
    """再承認の資料を書き、記録を持つ `gate` の結果を返す。回答は `ndf answer` が工程「実装」の事象に書く。"""
    body = _reapproval_material(r, commit, report, differences, record, old).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    path = r.job.work_root / f"reapproval-{digest[:MATERIAL_HASH_CHARS]}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    summary = f"単位の分け方か実装計画が承認した内容と違うので、再承認を待つ（{path}）: {'; '.join(differences)}"
    got = result.result(plan_runner.TOOL, result.GATE, summary, routes={invoke.APPROVED: None, REJECTED: None}, presentation_path=str(path))
    got[design_record.TARGET_KEY] = _converted_record(r, commit, report)
    got[design_record.MATERIAL_KEY] = {"path": str(path), "sha256": digest}
    return StageResult(got, {})
