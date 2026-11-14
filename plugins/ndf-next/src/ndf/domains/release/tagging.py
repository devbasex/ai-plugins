"""本番ブランチへ入ったコミットの版で、タグと GitHub Release のうち足りないものを作る（workflow が push で打つ）。

- 対象はマーケットプレイスに登録されたプラグインのうち、そのコミットでプラグインの定義が変わり（版を上げた）、版に
  接尾辞が無い（本番の版の）もの
- タグ（`<プラグイン名>--v<版>`）が無ければ、そのコミットを指す参照として作る。あれば作らず、別のコミットを指していても
  動かさない（前のリリースのタグ）
- GitHub Release が無ければ、CHANGELOG のその版の節を本文にして作る。節が無ければ `<プラグイン名> v<版>` の 1 行
"""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import NamedTuple

from ndf.adapters import git as git_mod
from ndf.adapters.github import GitHub, GitHubError
from ndf.domains.release import changelog_decide, version_decide
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release import plugins as plugins_mod

# GET で対象が無いときの状態
NOT_FOUND = HTTPStatus.NOT_FOUND


class Made(NamedTuple):
    """1 件の結果（`kind` は `tag`・`release`、`result` は `created`・`exists`）。"""

    kind: str
    name: str
    result: str

    def item(self) -> dict:
        return {"kind": self.kind, "name": self.name, "result": self.result}


def _missing(hub: GitHub, path: str) -> bool:
    """GET が 404 なら真。"""
    try:
        hub.rest("GET", path)
    except GitHubError as exc:
        if exc.status != NOT_FOUND:
            raise
        return True
    return False


def _tag(hub: GitHub, tag: str, revision: str) -> Made:
    if not _missing(hub, f"/repos/{hub.repo}/git/ref/tags/{tag}"):
        return Made("tag", tag, "exists")
    hub.rest("POST", f"/repos/{hub.repo}/git/refs", {"ref": f"refs/tags/{tag}", "sha": revision})
    return Made("tag", tag, "created")


def _release(hub: GitHub, tag: str, title: str, notes: str) -> Made:
    if not _missing(hub, f"/repos/{hub.repo}/releases/tags/{tag}"):
        return Made("release", tag, "exists")
    hub.rest("POST", f"/repos/{hub.repo}/releases", {"tag_name": tag, "name": title, "body": notes, "make_latest": "true"})
    return Made("release", tag, "created")


def publish(root: Path, hub: GitHub, revision: str) -> list[Made]:
    """足りないタグと GitHub Release を作り、1 件ずつの結果を返す。"""
    decl = decl_mod.load(root)
    log_path = root / decl.changelog
    log_text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    changed = set(git_mod.Git(root).changed_files(f"{revision}^1", revision))
    out: list[Made] = []
    for plugin in plugins_mod.load(root):
        ver = plugins_mod.version(root, plugin)
        bumped = any(m.relative_to(root).as_posix() in changed for m in plugin.manifests(root))
        if not bumped or version_decide.is_dev(ver):
            continue
        tag = version_decide.tag_of(plugin.name, ver)
        title = f"{plugin.name} v{ver}"
        notes = changelog_decide.notes(log_text, plugin.name, ver)
        out.append(_tag(hub, tag, revision))
        out.append(_release(hub, tag, title, notes if notes else title))
    return out
