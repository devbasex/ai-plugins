"""本番ブランチへ入ったコミットのバージョンで、タグと GitHub Release のうち足りないものを作る（workflow が本番ブランチへのマージの後に打つ）。

- 設定のリリースの形を確かめる（扱わない形は止まる。I1）。タグの書式（設定の `release.tag`）が `null` なら何も作らない
- 出したバージョンは保存先のアダプターから求める。ファイルを持つ保存先（`paths()` が空でない）は、そのコミットと第 1 親で
  バージョンが変わった単位（第 1 親との差分でバージョンを持つパスが変わった単位だけを読む）。ファイルを持たない保存先（`tag`）は、
  そのコミットをマージのコミットとして本番ブランチへ入ったマージ済みの PR か、`strict` の squash で閉じて入れた PR（正規の squash。
  `record.landed_squash` と共有）の head の名前をリリースブランチの書式で読む。接尾辞の付いたバージョン（開発版）は出さない
- 1 回の push で本番へ入ったコミット（`since` から first-parent で古い順）を 1 回の起動で順に公開する。出すバージョンがある
  コミットだけ、そのコミットの中身（保存先・CHANGELOG）を読む。作業場所の HEAD と違うコミットは一時の作業場所へ取り出して読み、
  抜けるときに外す。単位のバージョンを持つ定義どうしがそろわなければ（Claude Code と Codex の定義の食い違い）タグを作らず止まる
- タグ（設定の書式の名前）が無ければ、そのコミットを指す参照として作る。あれば、コミットまでたどった指す先を比べ、同じなら作らず、
  別のコミットなら動かさずに `TagMoved` で止まる（I7。#3615 の決定 12）
- GitHub Release は設定の `release.github_release` が真のときだけ、無ければ CHANGELOG のそのバージョンのセクションを本文にして作る。
  セクションが無ければ `<単位の名前> v<バージョン>` の 1 行。今の Latest が同じ単位のより新しいバージョンなら Latest にしない
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf.adapters import git as git_mod
from ndf.adapters.github import GitHub, GitHubError, text_of
from ndf.core.branch_strategy import Strategy
from ndf.domains.release import changelog_decide, forms_decide, names_decide, record, version_decide
from ndf.domains.release import ndf_settings as decl_mod
from ndf.domains.release import version_store as store_mod

# GET で対象が無いときの状態
NOT_FOUND = HTTPStatus.NOT_FOUND
# 結果の語と種類の語（`Made.result`・`Made.kind`）
CREATED, EXISTS = "created", "exists"
TAG, RELEASE = "tag", "release"
# 注釈付きタグの ref の `object.type`
ANNOTATED = "tag"
# 作業場所の HEAD と違うコミットを取り出す一時の作業場所の接頭辞
SCRATCH_PREFIX = "ndf-release-publish-"


class TagMoved(Exception):
    """同じ名前のタグが、出したコミットと別のコミットを指している（動かさない）。"""


class Made(NamedTuple):
    """1 件の結果（`kind` は `tag`・`release`、`result` は `created`・`exists`）。"""

    kind: str
    name: str
    result: str

    def item(self) -> dict:
        return {"kind": self.kind, "name": self.name, "result": self.result}


def _get(hub: GitHub, path: str) -> Optional[Any]:
    """GET の結果。404 なら None。"""
    try:
        return hub.rest("GET", path)
    except GitHubError as exc:
        if exc.status != NOT_FOUND:
            raise
        return None


def _target_commit(hub: GitHub, ref: Any) -> str:
    """タグの ref が指すコミット（注釈付きタグはタグオブジェクトをたどる）。"""
    obj = ref["object"] if isinstance(ref, dict) and isinstance(ref.get("object"), dict) else {}
    sha = text_of(obj.get("sha"))
    if obj.get("type") == ANNOTATED:
        tag_obj = hub.rest("GET", f"/repos/{hub.repo}/git/tags/{sha}")
        sha = text_of(tag_obj["object"]["sha"]) if isinstance(tag_obj, dict) else sha
    return sha


def _tag(hub: GitHub, tag: str, revision: str) -> Made:
    found = _get(hub, f"/repos/{hub.repo}/git/ref/tags/{tag}")
    if found is None:
        hub.rest("POST", f"/repos/{hub.repo}/git/refs", {"ref": f"refs/tags/{tag}", "sha": revision})
        return Made(TAG, tag, CREATED)
    pointed = _target_commit(hub, found)
    if pointed != revision:
        raise TagMoved(
            f"タグ {tag} は {pointed} を指していて、出したコミット {revision} と違う（タグを動かさない。人が直してから打ち直す）"
        )
    return Made(TAG, tag, EXISTS)


def _makes_latest(hub: GitHub, formats: names_decide.NameFormats, unit: str, version: str) -> str:
    """GitHub Release の `make_latest` の値。今の Latest が同じ単位のより新しいバージョンなら `"false"`（古いバージョンを後から公開・
    打ち直しても Latest を逆戻りさせない）。Latest が無い・別の単位のタグ・より古いバージョンなら `"true"`。"""
    latest = _get(hub, f"/repos/{hub.repo}/releases/latest")
    name = text_of(latest.get("tag_name")) if isinstance(latest, dict) else ""
    current = formats.version_of_tag(unit, name) if name else None
    if current is not None and version_decide.parse(current) > version_decide.parse(version):
        return "false"
    return "true"


def _release(hub: GitHub, formats: names_decide.NameFormats, unit: str, version: str, notes: str) -> Made:
    tag = formats.tag_of(unit, version)
    title = f"{unit} v{version}"
    if _get(hub, f"/repos/{hub.repo}/releases/tags/{tag}") is not None:
        return Made(RELEASE, tag, EXISTS)
    latest = _makes_latest(hub, formats, unit, version)
    hub.rest("POST", f"/repos/{hub.repo}/releases", {"tag_name": tag, "name": title, "body": notes or title, "make_latest": latest})
    return Made(RELEASE, tag, CREATED)


def _landed_here(row: Any, production: str, revision: str) -> bool:
    """PR の行が、そのコミット `revision` で本番ブランチ `production` へ入ったマージ済みの PR か（マージのコミットが `revision`）。"""
    if not isinstance(row, dict) or row.get("merged_at") is None or text_of(row.get("merge_commit_sha")) != revision:
        return False
    return isinstance(row.get("base"), dict) and text_of(row["base"].get("ref")) == production


def _released_by_branch(hub: GitHub, formats: names_decide.NameFormats, revision: str, production: str) -> Optional[str]:
    """そのコミットで本番ブランチへ入ったマージ済みの PR（無ければ、そのコミットで `strict` の squash で閉じて入れた PR）の head の
    名前を、リリースブランチの書式で読んだバージョン。無ければ None。

    コミットを含むだけの PR（別のブランチへマージした PR・前に入った PR）のバージョンでは作らない。"""
    rows = hub.rest("GET", f"/repos/{hub.repo}/commits/{revision}/pulls")
    merged = [row for row in (rows if isinstance(rows, list) else []) if _landed_here(row, production, revision)]
    for row in merged:
        name = text_of(row["head"]["ref"]) if isinstance(row.get("head"), dict) else ""
        version = formats.branch_version(name)
        if version is not None:
            return version
    if merged:  # マージで入ったコミットは squash の早送りでない（コミットを読み足さない）
        return None
    landed = record.landed_squash(hub, production, revision)  # strict の squash は PR を閉じる（マージ済みにならない）
    return formats.branch_version(landed.head_ref) if landed is not None else None


def _released_by_files(
    repo_git: git_mod.Git, store: store_mod.VersionStore, found: list[store_mod.Unit], revision: str
) -> list[tuple[store_mod.Unit, str]]:
    """ファイルを持つ保存先で、そのコミットと第 1 親でバージョンが変わった単位（単位の順）。

    第 1 親との差分を 1 回読み、バージョンを持つパス（`version_paths`）が変わった単位だけを読む（第 1 親が無ければ全単位）。"""
    diff = repo_git.attempt("diff", "--name-only", "-z", git_mod.END_OF_OPTIONS, f"{revision}^1", revision)
    changed = set(diff.stdout.split("\0")) if diff.returncode == 0 else None
    out = []
    for unit in found:
        if changed is not None and not changed & store.version_paths(unit):
            continue
        now = store.read(repo_git, revision, unit)
        if now is not None and now != store.read(repo_git, f"{revision}^1", unit):
            out.append((unit, now))
    return out


def _released(
    repo_git: git_mod.Git, store: store_mod.VersionStore, hub: GitHub, formats: names_decide.NameFormats, revision: str, production: str
) -> list[tuple[store_mod.Unit, str]]:
    """そのコミットで出したバージョン（単位の順。接尾辞の付いたバージョンを除く）。"""
    found = store.units()
    formats.require_unit_names(u.name for u in found)
    if store.paths():
        out = _released_by_files(repo_git, store, found, revision)
    else:
        version = _released_by_branch(hub, formats, revision, production)
        out = [(unit, version) for unit in found] if version is not None else []
    return [(u, v) for u, v in out if not version_decide.is_dev(v)]


def makes_tags(strategy: Strategy) -> bool:
    """設定のリリースの形を確かめ（扱わない形は止まる。I1）、タグの書式（`release.tag`）が `null` でなければ真。"""
    release = strategy.section["release"]
    forms_decide.form_of(release["form"])
    return names_decide.formats_of(release).tag is not None


def revisions(repo_git: git_mod.Git, revision: str, since: Optional[str]) -> list[str]:
    """公開するコミット（古い順）。`since` が `revision` の祖先なら `since..revision` の first-parent のすべて、そうでなければ
    （省いた・新しいブランチ・履歴の書き換えで前の先頭が読めない）`revision` だけ。"""
    if since and repo_git.attempt("merge-base", "--is-ancestor", since, revision).returncode == 0:
        return repo_git.run("rev-list", "--first-parent", "--reverse", git_mod.END_OF_OPTIONS, f"{since}..{revision}").split()
    return [revision]


@contextmanager
def _checkout(repo_git: git_mod.Git, root: Path, head: str, revision: str) -> Iterator[Path]:
    """`revision` の中身を読む根。作業場所の HEAD `head` が `revision` ならその場所、違えば一時の作業場所（抜けるときに外す）。"""
    if revision == head:
        yield root
        return
    parent = Path(tempfile.mkdtemp(prefix=SCRATCH_PREFIX))
    path = parent / "work"
    repo_git.run("worktree", "add", "--quiet", "--detach", "--", str(path), revision)
    try:
        yield path
    finally:
        if repo_git.attempt("worktree", "remove", "--force", "--", str(path)).returncode != 0:
            repo_git.attempt("worktree", "prune")
        shutil.rmtree(parent, ignore_errors=True)


def _publish_at(here: Path, hub: GitHub, strategy: Strategy, revision: str, released: list[tuple[store_mod.Unit, str]]) -> list[Made]:
    """`revision` の中身を取り出した `here` の保存先と CHANGELOG で、出したバージョンのタグと GitHub Release を作る。

    単位のバージョンを持つ定義どうしがそろわなければ、1 件も作らずに `StoreError`。"""
    release = strategy.section["release"]
    formats = names_decide.formats_of(release)
    store = store_mod.load(here, strategy, hub.repo)
    for unit, _ in released:
        store.require_consistent(unit)
    log_path = decl_mod.load(here).changelog_path(here)
    log_text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    out: list[Made] = []
    for unit, ver in released:
        tag = formats.tag_of(unit.name, ver)
        if tag is None:
            continue
        out.append(_tag(hub, tag, revision))
        if release["github_release"] is True:
            notes = changelog_decide.notes(log_text, formats.heading_pattern(unit.name, ver))
            out.append(_release(hub, formats, unit.name, ver, notes))
    return out


def publish(root: Path, hub: GitHub, revision: str, strategy: Strategy, since: Optional[str] = None) -> list[Made]:
    """`revision`（`since` を渡せば `since` から `revision` までの first-parent のコミットを古い順に）で足りないタグと
    GitHub Release を作り、1 件ずつの結果を返す。タグの書式が `null` なら空。出すバージョンの無いコミットは中身を取り出さない。"""
    if not makes_tags(strategy):
        return []
    formats = names_decide.formats_of(strategy.section["release"])
    repo_git = git_mod.Git(root)
    store = store_mod.load(root, strategy, hub.repo)
    head = repo_git.rev_parse("HEAD")
    out: list[Made] = []
    for rev in revisions(repo_git, revision, since):
        released = _released(repo_git, store, hub, formats, rev, strategy.production)
        if not released:
            continue
        with _checkout(repo_git, root, head, rev) as here:
            out += _publish_at(here, hub, strategy, rev, released)
    return out
