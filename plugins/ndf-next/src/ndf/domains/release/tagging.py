"""本番ブランチへ入ったコミットのバージョンで、タグと GitHub Release のうち足りないものを作る（workflow が本番ブランチへのマージの後に打つ）。

- 設定のリリースの形を確かめる（扱わない形は止まる。I1）。タグの書式（設定の `release.tag`）が `null` なら何も作らない
- 出したバージョンは保存先のアダプターから求める。ファイルを持つ保存先（`paths()` が空でない）は、そのコミットと第 1 親で
  バージョンが変わった単位（第 1 親との差分でバージョンを持つパスが変わった単位だけを読む）。ファイルを持たない保存先（`tag`）は、
  そのコミットをマージのコミットとして本番ブランチへ入ったマージ済みの PR の head の名前をリリースブランチの書式で読む。
  接尾辞の付いたバージョン（開発版）は出さない
- タグ（設定の書式の名前）が無ければ、そのコミットを指す参照として作る。あれば、コミットまでたどった指す先を比べ、同じなら作らず、
  別のコミットなら動かさずに `TagMoved` で止まる（I7。#3615 の決定 12）
- GitHub Release は設定の `release.github_release` が真のときだけ、無ければ CHANGELOG のそのバージョンのセクションを本文にして作る。
  セクションが無ければ `<単位の名前> v<バージョン>` の 1 行。今の Latest が同じ単位のより新しいバージョンなら Latest にしない
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf.adapters import git as git_mod
from ndf.adapters.github import GitHub, GitHubError, text_of
from ndf.core.branch_strategy import Strategy
from ndf.domains.release import changelog_decide, forms_decide, names_decide, version_decide
from ndf.domains.release import ndf_settings as decl_mod
from ndf.domains.release import version_store as store_mod

# GET で対象が無いときの状態
NOT_FOUND = HTTPStatus.NOT_FOUND
# 結果の語と種類の語（`Made.result`・`Made.kind`）
CREATED, EXISTS = "created", "exists"
TAG, RELEASE = "tag", "release"
# 注釈付きタグの ref の `object.type`
ANNOTATED = "tag"


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
    """GitHub Release の `make_latest` の値。今の Latest が同じ単位のより新しいバージョンなら `"false"`（古い版を後から公開・
    打ち直しても Latest を逆戻りさせない）。Latest が無い・別の単位のタグ・より古い版なら `"true"`。"""
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
    """そのコミットで本番ブランチへ入ったマージ済みの PR の head の名前を、リリースブランチの書式で読んだバージョン。無ければ None。

    コミットを含むだけの PR（別のブランチへマージした PR・前に入った PR）の版では作らない。"""
    rows = hub.rest("GET", f"/repos/{hub.repo}/commits/{revision}/pulls")
    for row in rows if isinstance(rows, list) else []:
        if not _landed_here(row, production, revision):
            continue
        name = text_of(row["head"]["ref"]) if isinstance(row.get("head"), dict) else ""
        version = formats.branch_version(name)
        if version is not None:
            return version
    return None


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


def publish(root: Path, hub: GitHub, revision: str, strategy: Strategy) -> list[Made]:
    """足りないタグと GitHub Release を作り、1 件ずつの結果を返す。タグの書式が `null` なら空。"""
    if not makes_tags(strategy):
        return []
    release = strategy.section["release"]
    formats = names_decide.formats_of(release)
    store = store_mod.load(root, strategy, hub.repo)
    decl = decl_mod.load(root)
    log_path = decl.changelog_path(root)
    log_text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    out: list[Made] = []
    for unit, ver in _released(git_mod.Git(root), store, hub, formats, revision, strategy.production):
        tag = formats.tag_of(unit.name, ver)
        if tag is None:
            continue
        out.append(_tag(hub, tag, revision))
        if release["github_release"] is True:
            notes = changelog_decide.notes(log_text, formats.heading_pattern(unit.name, ver))
            out.append(_release(hub, formats, unit.name, ver, notes))
    return out
