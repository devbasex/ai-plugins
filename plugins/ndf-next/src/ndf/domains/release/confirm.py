"""本番の PR のマージの後に、CD の版上げ（版上げの PR のマージ・タグ・その GitHub Release）がそろうのを待って確かめ、
作った版を本番の PR へ記録する（本番のプランの `confirm`。#3572 の決定 7）。

| 読み直しの結果 | 動き |
| --- | --- |
| 本番の PR のマージコミットを含む版上げの PR のマージ・タグ・GitHub Release の 3 つがある | 作った版の記録を 1 回だけ書いて返す |
| どれかが無く、締め切りの前 | `CONFIRM_POLL_SECONDS` 秒待って読み直す |
| どれかが無く、締め切りを過ぎた | 足りないもの（`bump` か `publish`）を持つ止まった結果を返す。記録を書かない |

読むだけで、コードもブランチも変えない。書くのは本番の PR の作った版の記録 1 件である。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from ndf.adapters import git as git_mod
from ndf.adapters.github import PER_PAGE, GitHub, text_of
from ndf.domains.release import gather, land, records_trail, version_decide

# 読み直しの間隔（秒。マージキューの読み直しと同じ値）
CONFIRM_POLL_SECONDS = land.QUEUE_POLL_SECONDS
# 足りないもの（版上げの PR のマージ・タグか GitHub Release）
MISSING_BUMP, MISSING_PUBLISH = "bump", "publish"
# 止まった理由の種類
DEPLOY_FAILED = "deploy_failed"


# 版上げの PR のマージコミットと版 → タグを確かめるプラグインの名前
PluginOf = Callable[[str, str], str]


class ConfirmError(Exception):
    """確かめの前提が無い（本番の PR がマージされていない・閉じた版上げの PR を読み切れない）。"""


class Watch(NamedTuple):
    """待ちの予算（秒）と、時刻と眠りの関数（テストが差し替える）。"""

    budget: float
    clock: Callable[[], float]
    sleep: Callable[[float], None]


class Confirmed(NamedTuple):
    """確かめの結果。そろわなければ `kind` が `deploy_failed`、`missing` が `bump` か `publish`。"""

    version: str | None
    tag: str | None
    bump_pr: int | None
    kind: str | None = None
    missing: str | None = None
    detail: str = ""
    bump_revision: str | None = None


def _bump_containing(hub: GitHub, repo_git: git_mod.Git, merge_sha: str, production: str) -> tuple[int, str, str] | None:
    """マージ済みの版上げの PR のうち、マージコミットが `merge_sha` を含む最も小さい版のもの（PR の番号, 版, マージコミット）。

    閉じた PR を更新の新しい順に 1 ページ読む。1 ページが埋まり、含むものが見つからなければ確かめられないとして `ConfirmError`。
    """
    rows = hub.rest(
        "GET",
        f"/repos/{hub.repo}/pulls",
        params={"state": "closed", "base": production, "sort": "updated", "direction": "desc", "per_page": PER_PAGE},
    )
    found = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    hits = []
    for r in found:
        ref, sha = text_of(r["head"]["ref"]), text_of(r.get("merge_commit_sha"))
        if not ref.startswith(version_decide.BRANCH_PREFIX) or not r.get("merged_at") or not sha:
            continue
        if repo_git.is_ancestor(merge_sha, sha):
            version = version_decide.version_of_branch(ref)
            hits.append((version_decide.parse(version), int(r["number"]), version, sha))
    if not hits:
        if len(found) >= PER_PAGE:
            raise ConfirmError(f"{production} 宛ての閉じた PR が {PER_PAGE} 件以上あり、版上げの PR を確かめられない")
        return None
    _v, number, version, sha = min(hits)
    return number, version, sha


def _tagged(hub: GitHub, tag: str) -> bool:
    """タグが origin にあるか。"""
    return gather.resource_exists(hub, f"/repos/{hub.repo}/git/ref/tags/{tag}")


def _has_release(hub: GitHub, tag: str) -> bool:
    """タグの GitHub Release があるか。"""
    return gather.resource_exists(hub, f"/repos/{hub.repo}/releases/tags/{tag}")


def _read(hub: GitHub, repo_git: git_mod.Git, merge_sha: str, production: str, plugin_of: PluginOf) -> Confirmed:
    """1 回の読み直し。そろっていれば `kind` が None の結果、そろわなければ足りないものを持つ結果。

    確かめるタグのプラグインは、版上げの PR のマージコミットと版から `plugin_of` が決める（マーケットプレイスの先頭に限らない）。"""
    gather.fetch(repo_git, production)
    found = _bump_containing(hub, repo_git, merge_sha, production)
    if found is None:
        return Confirmed(None, None, None, DEPLOY_FAILED, MISSING_BUMP, "版上げの PR がまだマージされていない")
    number, version, sha = found
    tag = version_decide.tag_of(plugin_of(sha, version), version)
    if not _tagged(hub, tag):
        return Confirmed(version, tag, number, DEPLOY_FAILED, MISSING_PUBLISH, f"タグ {tag} がまだ無い", sha)
    if not _has_release(hub, tag):
        return Confirmed(version, tag, number, DEPLOY_FAILED, MISSING_PUBLISH, f"タグ {tag} の GitHub Release がまだ無い", sha)
    return Confirmed(version, tag, number, bump_revision=sha)


def confirm(
    hub: GitHub,
    repo_git: git_mod.Git,
    number: int,
    production: str,
    plugin_of: PluginOf,
    watch: Watch,
) -> Confirmed:
    """本番の PR `number` のマージを含む版上げの PR のマージ・タグ・GitHub Release を `watch.budget` 秒まで待ち、作った版を記録する。"""
    pull = hub.pull(number)
    if not pull.merged or not pull.merge_sha:
        raise ConfirmError(f"#{number} はまだマージされていない（release-steps merge の後に打つ）")
    deadline = watch.clock() + watch.budget
    while True:
        got = _read(hub, repo_git, pull.merge_sha, production, plugin_of)
        if got.kind is None and got.version is not None and got.tag is not None and got.bump_pr is not None:
            records_trail.write_version_record(hub, number, got.version, got.tag, got.bump_pr)
            return got._replace(detail=f"#{number} の版 v{got.version}（タグ {got.tag}・版上げの PR #{got.bump_pr}）を確かめた")
        left = deadline - watch.clock()
        if left <= 0:
            return got._replace(detail=f"本番はマージ済み。CD の {got.missing} がそろわない: {got.detail}")
        watch.sleep(min(CONFIRM_POLL_SECONDS, left))
