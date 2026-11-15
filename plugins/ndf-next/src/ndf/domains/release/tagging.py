"""本番ブランチへ入ったコミットの版で、タグと GitHub Release のうち足りないものを作る（workflow が本番ブランチへのマージの後に打つ）。

- 対象はバージョンの置き場（`version_store`）の単位のうち、そのコミットでバージョンが変わり、バージョンに接尾辞が無い
  （本番の版の）もの。今の行（リリースブランチの PR のマージコミット）も `github-flow` の行（バージョン上げの PR のマージ
  コミット）も同じ規則で作る（#3572 の決定 16）
- バージョン上げのマージコミットが merge queue で前に並んだ PR に追い越されていれば（`overtaken`）、タグも GitHub Release も
  作らない（そのコミットのバージョンの範囲に、前に並んだ PR が載っていない。`bump` が上げ直す。決定 20）
- タグ（`<単位の名前>--v<版>`）が無ければ、そのコミットを指す参照として作る。あれば作らず、別のコミットを指していても
  動かさない（前のリリースのタグ）
- GitHub Release が無ければ、CHANGELOG のその版の節を本文にして作る。節が無ければ `<プラグイン名> v<版>` の 1 行
- `revision` が版上げの PR のマージコミットなら、タグを打つコミットに含まれ版の節に載らない PR（版上げの PR を組んだ後に
  main へ入った PR。次の版の節に載る）を本文の末尾の節 `LATE_HEADING` に並べる（#3572。`_late`）
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

from ndf.adapters import git as git_mod
from ndf.adapters.github import GitHub
from ndf.domains.release import bump_decide, changelog_decide, gather, included_decide, version_decide, version_store
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release.version_store import Unit, VersionStore

# 結果の語と種類の語（`Made.result`・`Made.kind`）
CREATED, EXISTS = "created", "exists"
TAG, RELEASE = "tag", "release"
# 次の版の節に載る PR を並べる本文の節の見出し
LATE_HEADING = "## 次の版の節に載る PR"
_NUMBER = re.compile(r"#(\d+)")


class Made(NamedTuple):
    """1 件の結果（`kind` は `tag`・`release`、`result` は `created`・`exists`）。"""

    kind: str
    name: str
    result: str

    def item(self) -> dict:
        return {"kind": self.kind, "name": self.name, "result": self.result}


def _tag(hub: GitHub, tag: str, revision: str) -> Made:
    if gather.resource_exists(hub, f"/repos/{hub.repo}/git/ref/tags/{tag}"):
        return Made(TAG, tag, EXISTS)
    hub.rest("POST", f"/repos/{hub.repo}/git/refs", {"ref": f"refs/tags/{tag}", "sha": revision})
    return Made(TAG, tag, CREATED)


def _release(hub: GitHub, tag: str, title: str, notes: str) -> Made:
    if gather.resource_exists(hub, f"/repos/{hub.repo}/releases/tags/{tag}"):
        return Made(RELEASE, tag, EXISTS)
    hub.rest("POST", f"/repos/{hub.repo}/releases", {"tag_name": tag, "name": title, "body": notes, "make_latest": "true"})
    return Made(RELEASE, tag, CREATED)


def overtaken(repo_git: git_mod.Git, revision: str) -> bool:
    """バージョン上げのマージコミット `revision` が、キューで前に並んだ PR に追い越されたか（#3572 の決定 20）。

    親が 2 つで、第 2 親がバージョン上げのコミット（起点の行を持つ）で、第 1 親（マージの直前の本番ブランチの先端）が
    第 2 親の祖先でなければ真。親が 1 つ・第 2 親がバージョン上げのコミットでない（今の行のリリースブランチ）なら偽。"""
    parents = repo_git.run("rev-list", "--parents", "-n", "1", git_mod.END_OF_OPTIONS, revision).split()[1:]
    if len(parents) != 2:
        return False
    if bump_decide.origin_of(repo_git.run("log", "-1", "--format=%B", git_mod.END_OF_OPTIONS, parents[1])) is None:
        return False
    return not repo_git.is_ancestor(parents[0], parents[1])


def bumped(store: VersionStore, root: Path, revision: str) -> list[Unit]:
    """そのコミット（第 1 親と比べて）でバージョンが変わった単位（置き場の順）。追い越されたマージコミットなら空。

    タグを作る対象（`publish`）と、バージョン上げがタグを待つ対象（`bump.Bumper` の先端の判定）の正本。"""
    repo_git = git_mod.Git(root)
    if overtaken(repo_git, revision):
        return []
    return version_store.bumped(store, repo_git, revision)


def _released(store: VersionStore, root: Path, revision: str) -> Iterator[tuple[Unit, str]]:
    """そのコミットでバージョンが変わり、バージョンに接尾辞が無い単位とバージョン（置き場の順。1 件ずつ読む）。"""
    repo_git = git_mod.Git(root)
    for unit in bumped(store, root, revision):
        ver = store.read(repo_git, revision, unit)
        if ver is not None and not version_decide.is_dev(ver):
            yield unit, ver


def _late(root: Path, revision: str, notes: str) -> list[int]:
    """タグを打つコミット `revision`（版上げの PR のマージ）に含まれ、版の節 `notes` に載らない PR の番号。

    範囲は第 2 親（版上げのコミット）の起点から第 1 親（マージ前の main）まで。起点が読めなければ空。
    """
    repo_git = git_mod.Git(root)
    done = repo_git.attempt("log", "-1", "--format=%B", git_mod.END_OF_OPTIONS, f"{revision}^2")
    origin = bump_decide.origin_of(done.stdout) if done.returncode == 0 else None
    if origin is None:
        return []
    subjects = repo_git.run("log", "--first-parent", "--format=%s", git_mod.END_OF_OPTIONS, f"{origin}..{revision}^1")
    listed = [int(n) for n in _NUMBER.findall(notes)]
    return bump_decide.late(included_decide.numbers(subjects.splitlines()), listed)


def _with_late(notes: str, late: list[int]) -> str:
    if not late:
        return notes
    return f"{notes.rstrip()}\n\n{LATE_HEADING}\n\n" + "".join(f"- #{n}\n" for n in late)


def publish(root: Path, hub: GitHub, revision: str, store: VersionStore) -> list[Made]:
    """足りないタグと GitHub Release を作り、1 件ずつの結果を返す。"""
    decl = decl_mod.load(root)
    log_path = root / decl.changelog
    log_text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    out: list[Made] = []
    for unit, ver in _released(store, root, revision):
        tag = version_decide.tag_of(unit.name, ver)
        title = f"{unit.name} v{ver}"
        notes = changelog_decide.notes(log_text, unit.name, ver)
        body = notes if notes else title
        out.append(_tag(hub, tag, revision))
        out.append(_release(hub, tag, title, _with_late(body, _late(root, revision, body))))
    return out
