"""手で終えるデプロイの工程の確かめに要る PR と CD の実行を読み、`ndf.progress.delivery_decide` に渡す（#4520）。

読むのは、記録の PR の本文とコメント（信用する書き手のものだけ）、本番ブランチのブランチ戦略の設定、リリースの PR の
マージのコミット、その CD の実行の一覧である。読めなければ終了を書かない。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf.adapters import ci
from ndf.adapters.git import GitError
from ndf.adapters.github import Authored, GitHub, GitHubError, Pull
from ndf.contracts.worker_kinds import DELIVERY, PRODUCT_VERSION
from ndf.core import branch_strategy, production_merge, release_record
from ndf.progress import command, delivery_decide, model, run
from ndf.progress.run_view import NO_RELEASE, RECORD_PR

# PR を読む口（承認の対象の読み取りと同じ応答を使い回すときに渡す）
ReadPull = Callable[[int], Pull]


def declared(hub: GitHub, cwd: Path) -> Mapping[str, Any]:
    """本番ブランチの `.ndf/project.json` の `branch_strategy`。本番ブランチの名前は手元の設定から引き、無ければ空。"""
    local = command.ndf_settings(cwd, production_merge.PROJECT_FILE).get(branch_strategy.SECTION)
    branches = local.get("branches") if isinstance(local, Mapping) else None
    production = branches.get("production") if isinstance(branches, Mapping) else None
    if not isinstance(production, str):
        return {}
    text = hub.branch_file(production, production_merge.PROJECT_FILE).text
    data = json.loads(text) if text is not None else None
    section = data.get(branch_strategy.SECTION) if isinstance(data, Mapping) else None
    return section if isinstance(section, Mapping) else {}


def _workflow(strategy: Mapping[str, Any], target: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """CD の結果を読む workflow（読まなければ None）と、断る理由。"""
    release = strategy.get("release")
    if not isinstance(release, Mapping):
        return None, None
    holder = release.get("cd")
    if production_merge.DELIVERIES in release:
        listed = release[production_merge.DELIVERIES]
        rows = [r for r in listed if isinstance(r, Mapping) and r.get("target") == target] if isinstance(listed, list) else []
        if not rows:
            return None, f"設定の deliveries に出力の届け先 {target} の行が無く、CD の結果を確かめられない。出力の delivery を直す"
        holder = next((r["result"] for r in rows if isinstance(r.get("result"), Mapping) and r["result"].get("workflow")), None)
    workflow = holder.get("workflow") if isinstance(holder, Mapping) else None
    return (workflow if isinstance(workflow, str) and workflow else None), None


def _bodies(hub: GitHub, pull: Pull, comments: list[Authored]) -> list[str]:
    """PR の本文とコメントのうち、信用する書き手のリリース記録の本文（古い順）。"""
    return [a.body for a in (pull.authored, *comments) if _looks_recorded(a.body) and hub.trusts(a)]


def _looks_recorded(body: str) -> bool:
    """リリース記録でありうる本文か（権限の読み取りを、記録の書き手だけにする）。"""
    return "## リリース記録" in body or release_record.RECORD_MARK in body


def _conclusion(hub: GitHub, pull: Pull, workflow: str, branch: Optional[str]) -> Optional[str]:
    """リリースの PR のマージコミットに対する、本番ブランチの実行の結論。マージコミットか実行が無いか、終わっていなければ
    None。"""
    if not (pull.merged and pull.merge_sha):
        return None
    listed = hub.workflow_runs(workflow, branch=branch if branch is not None else pull.base_ref, head_sha=pull.merge_sha)
    picked = delivery_decide.pick_run(listed)
    return ci.ended_with(dict(picked)) if picked is not None else None


class Wanted(NamedTuple):
    """確かめる対象。`record_pr` は記録を読む PR、`version` と `target` は出力の出したバージョンと届け先。"""

    record_pr: int
    sprint_pr: int
    version: Optional[str] = None
    target: Optional[str] = None
    no_release: bool = False


def confirm(hub: GitHub, wanted: Wanted, strategy: Mapping[str, Any], pull: Optional[ReadPull] = None) -> delivery_decide.Verdict:
    """記録の PR と CD の実行を読み、デプロイの工程を終えてよいかを返す。読めなければ `delivery_decide.Unreadable`。"""
    read_pull = pull if pull is not None else hub.pull
    number = wanted.record_pr
    try:
        bodies = _bodies(hub, read_pull(number), hub.comments(number))
        if wanted.no_release:
            return delivery_decide.judge_no_release(bodies, sprint_pr=wanted.sprint_pr)
        found = delivery_decide.pick_records(bodies, sprint_pr=wanted.sprint_pr, version=str(wanted.version))
        workflow, refused = _workflow(strategy, wanted.target)
        if refused is not None:
            return delivery_decide.Verdict(False, refused)
        if workflow is None or not found:
            return delivery_decide.judge_delivery(found, None)
        branches = strategy.get("branches")
        branch = branches.get("production") if isinstance(branches, Mapping) else None
        # 手で置いた記録のリリースの PR は、読んだ PR である
        releases = dict.fromkeys(record.release for record in found)
        runs = {r: _conclusion(hub, read_pull(number if r is None else r), workflow, branch) for r in releases}
        return delivery_decide.judge_delivery(found, runs)
    except release_record.UnreadableRecord as exc:
        raise delivery_decide.Unreadable("invalid", f"PR #{number} のリリース記録を読めない。記録を直して打ち直す: {exc}") from None
    except (GitHubError, GitError) as exc:
        raise delivery_decide.Unreadable("transient", f"PR #{number} のリリース記録か CD の実行を読めない。打ち直す: {exc}") from None


def pull_number(value: Any) -> Optional[int]:
    """PR の番号（整数か、末尾が番号の文字列 `release_pr`）。読めなければ None。"""
    if isinstance(value, int):
        return value
    found = re.search(r"(\d+)/?$", value) if isinstance(value, str) else None
    return int(found[1]) if found else None


def default_record_pr(context: Mapping[str, Any]) -> Optional[int]:
    """記録を持つ PR の既定。文脈の `release_pr`、無ければ `pr`。"""
    released_by = pull_number(context.get("release_pr"))
    return released_by if released_by is not None else pull_number(context.get("pr"))


def check(
    hub: Callable[[], GitHub],
    cwd: Path,
    context: Mapping[str, Any],
    record_pr: Optional[int],
    no_release: bool,
    pull: Optional[ReadPull] = None,
) -> delivery_decide.Verdict:
    """文脈（手で終える出力を重ねたもの）の PR・バージョン・届け先で `confirm` を打つ。読めなければ `delivery_decide.Unreadable`。"""
    sprint_pr = pull_number(context.get("pr"))
    if sprint_pr is None or record_pr is None:
        return delivery_decide.Verdict(False, "スプリントの PR（文脈の pr）が無く、リリース記録を確かめられない")
    delivery = context.get(DELIVERY)
    try:
        made = hub()
        strategy = {} if no_release else declared(made, cwd)
    except (GitHubError, GitError, ValueError) as exc:
        raise delivery_decide.Unreadable("transient", f"本番ブランチの設定を読めない。打ち直す: {exc}") from None
    target = delivery.get("target") if isinstance(delivery, Mapping) else None
    wanted = Wanted(record_pr, sprint_pr, context.get(PRODUCT_VERSION), target, no_release)
    return confirm(made, wanted, strategy, pull)


def held_check(hub: Callable[[], GitHub], cwd: Path, pull: ReadPull) -> Callable[[run.View, Mapping[str, Any]], delivery_decide.Verdict]:
    """回答待ちの後の確かめ直し（`run.Env.confirm`）。`finish` が回答待ちの記録に残した PR を読む。"""

    def of(v: run.View, held: Mapping[str, Any]) -> delivery_decide.Verdict:
        return check(hub, cwd, v.context(), held.get(RECORD_PR), held.get(NO_RELEASE) is True, pull)

    return of


def released(hub: Callable[[], GitHub]) -> Callable[[run.View, str], bool]:
    """デプロイの工程 `stage` に、直近の開き直しより後のリリース実行の証拠があるかを読む口（質問と回答の経路）。

    読めなければ `model.Refused`（質問も回答も記録しない）。"""

    def of(v: run.View, stage: str) -> bool:
        context = v.context()
        sprint_pr, record_pr = pull_number(context.get("pr")), default_record_pr(context)
        if sprint_pr is None or record_pr is None:
            return False
        try:
            made = hub()
            listed = made.comments(record_pr)
            found = [(c.body, c.created) for c in listed if _looks_recorded(c.body) and made.trusts(c)]
            return delivery_decide.executed(found, sprint_pr=sprint_pr, since=_reopened_at(v, stage))
        except (GitHubError, GitError, release_record.UnreadableRecord) as exc:
            raise model.Refused(f"PR #{record_pr} のリリース記録を読めないので、リリースが済んでいるかを確かめられない: {exc}") from None

    return of


def _reopened_at(v: run.View, stage: str) -> Optional[datetime]:
    """工程の直近の開き直しの時刻。開き直していなければ None。"""
    cut = v.last_reopened(stage)
    return next((ev.at for ev in v.events if ev.seq == cut), None) if cut else None
