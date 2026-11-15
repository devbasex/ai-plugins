"""本番ブランチへ入ったコミットの版で、タグと GitHub Release のうち足りないものを作る（workflow が本番ブランチへのマージの後に打つ）。

- 対象はマーケットプレイスに登録されたプラグインのうち、そのコミットでプラグインの定義が変わり（版を上げた）、版に
  接尾辞が無い（本番の版の）もの
- タグ（`<プラグイン名>--v<版>`）が無ければ、そのコミットを指す参照として作る。あれば作らず、別のコミットを指していても
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
from ndf.domains.release import bump_decide, changelog_decide, gather, included_decide, version_decide
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release import plugins as plugins_mod

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


def _released(root: Path, revision: str) -> Iterator[tuple[plugins_mod.Plugin, str]]:
    """そのコミットで定義が変わり（版を上げた）、版に接尾辞が無いプラグインと版（登録の順。1 件ずつ読む）。"""
    changed = set(git_mod.Git(root).changed_files(f"{revision}^1", revision))
    for plugin in plugins_mod.load(root):
        ver = plugins_mod.version(root, plugin)
        bumped = any(m.relative_to(root).as_posix() in changed for m in plugin.manifests(root))
        if bumped and not version_decide.is_dev(ver):
            yield plugin, ver


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


def publish(root: Path, hub: GitHub, revision: str) -> list[Made]:
    """足りないタグと GitHub Release を作り、1 件ずつの結果を返す。"""
    decl = decl_mod.load(root)
    log_path = root / decl.changelog
    log_text = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    out: list[Made] = []
    for plugin, ver in _released(root, revision):
        tag = version_decide.tag_of(plugin.name, ver)
        title = f"{plugin.name} v{ver}"
        notes = changelog_decide.notes(log_text, plugin.name, ver)
        body = notes if notes else title
        out.append(_tag(hub, tag, revision))
        out.append(_release(hub, tag, title, _with_late(body, _late(root, revision, body))))
    return out
