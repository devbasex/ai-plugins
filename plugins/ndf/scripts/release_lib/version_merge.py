"""release/v<版> を起点のブランチへ取り込むときの版数の衝突を解く（#2436 の R5。`release-steps.py` の release から分けた）。

承認の後に他のデプロイが起点のブランチの版数を上げていると、release/v<版> の取り込みが版数を持つ箇所で衝突する。
release/v<版> そのものへは起点のブランチを取り込まず（取り込むと承認の外のコミットが本番チャネルへ出る）、
release/v<版> の先頭と起点のブランチの先頭を親に持つマージのコミットを作業ツリーに触れずに作る。解くのは、
release/v<版> が分岐点から変えたパス（版上げが書いたファイル）の、release/v<版> が書いた版数の欄の違いだけで、
その塊を release/v<版> の側（本番の版）で取る。それ以外のパス・版数の外・依存ライブラリなど他の版数が 1 つでも違えば
解かずに止める（起点のブランチの更新を本番の側の古い値へ戻さない）。

選んで集める取り込み方の戻すマージ（`sections=True`。#3121 の決定 17）は、CHANGELOG.md の衝突も解く。開発版のデプロイが起点の
ブランチへ、リリースブランチと同じ見出し（`## [<plugin> <基底の版>]`）の節を別の中身で書くためで、起点のブランチの本文に
release/v<版> が分岐点から変えた節だけを写す（`resolve_sections`。節の切り分けは `carry.split_h2`）。
"""

from __future__ import annotations

import os
import re
import tempfile

from release_lib.carry import split_h2
from step_result import StepError, git, run

VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?")
_OURS, _SEP, _THEIRS = "<<<<<<< ", "=======", ">>>>>>> "
CHANGELOG = "CHANGELOG.md"


class VersionConflict(StepError):
    """版数の外の箇所が衝突した（解かずに止める。終了コード 1）。paths は解けなかったパス。"""

    def __init__(self, paths: list[str]):
        super().__init__(f"取り込みで版数の外の箇所が衝突した（解かずに止めた）: {', '.join(paths)}", 1)
        self.paths = paths


def _same_but_versions(a: list[str], b: list[str], allowed: set[str]) -> bool:
    """行ごとに版数の外が同じで、違う版数はどれもこちらの側が allowed（release/v<版> が書いた版数）のもの。"""
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if VERSION_RE.sub("<v>", x) != VERSION_RE.sub("<v>", y):
            return False
        if any(v != w and v not in allowed for v, w in zip(VERSION_RE.findall(x), VERSION_RE.findall(y))):
            return False
    return True


def resolve_text(text: str, allowed: set[str]) -> str | None:
    """衝突の印を持つ本文の塊がすべて allowed の版数の違いだけなら、こちら（release/v<版>）の側を取った本文。違えば None。"""
    out: list[str] = []
    ours: list[str] | None = None
    theirs: list[str] | None = None
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        if ours is None:
            if bare.startswith(_OURS):
                ours = []
            else:
                out.append(line)
        elif theirs is None:
            if bare == _SEP:
                theirs = []
            elif bare.startswith("||||||| "):
                return None  # diff3 の形は扱わない（merge.conflictStyle を merge にして作る）
            else:
                ours.append(line)
        elif bare.startswith(_THEIRS):
            if not _same_but_versions(ours, theirs, allowed):
                return None
            out.extend(ours)
            ours = theirs = None
        else:
            theirs.append(line)
    return "".join(out) if ours is None else None


def resolve_sections(base: str, ours: str, theirs: str) -> str:
    """CHANGELOG.md の本文を、theirs（起点のブランチ）に ours（release/v<版>）が分岐点 base から変えた節だけを写して作る。見出しが
    theirs にあれば置き換え、無ければ theirs の最初の節の前へ ours の並びで足す。ours が消した節と節の外の変更は写さない
    （起点のブランチの更新を本番の側の古い値へ戻さない）。"""
    was = dict(split_h2(base.split("\n"))[1])
    changed = {h: body for h, body in split_h2(ours.split("\n"))[1] if was.get(h) != body}
    top, kept = split_h2(theirs.split("\n"))
    have = {h for h, _ in kept}
    added = [line for h, body in changed.items() if h not in have for line in (h, *body)]
    return "\n".join(top + added + [line for h, body in kept for line in (h, *changed.get(h, body))])


def _blob(root, rev: str, path: str) -> str:
    p = git(root, "show", f"{rev}:{path}", check=False)
    return p.stdout if p.returncode == 0 else ""


def _merge_tree(root, ours: str, theirs: str) -> tuple[str, dict[str, dict[int, tuple[str, str]]]]:
    """(結果の木, {パス: {段: (mode, oid)}})。衝突が無ければ段の一覧は空。"""
    p = git(root, "-c", "merge.conflictStyle=merge", "merge-tree", "--write-tree", "-z", "--no-messages", ours, theirs, check=False)
    if p.returncode not in (0, 1):
        raise StepError(f"git merge-tree が終了コード {p.returncode}: {p.stderr.strip()[:300]}")
    parts = [x for x in p.stdout.split("\0") if x]
    stages: dict[str, dict[int, tuple[str, str]]] = {}
    for entry in parts[1:]:
        meta, _, path = entry.partition("\t")
        mode, oid, stage = meta.split(" ")
        stages.setdefault(path, {})[int(stage)] = (mode, oid)
    return parts[0], stages


def _release_side(root, ours: str, theirs: str) -> tuple[set[str], set[str]]:
    """(release/v<版> が分岐点から変えたパス, そこで書いた版数)。版上げ（bump）が書いた箇所だけを解く対象にする。"""
    base = git(root, "merge-base", ours, theirs).stdout.strip()
    paths = set(git(root, "diff", "--name-only", "-z", base, ours).stdout.split("\0")) - {""}
    added = [
        line for line in git(root, "diff", "-U0", base, ours).stdout.splitlines() if line.startswith("+") and not line.startswith("+++")
    ]
    return paths, {v for line in added for v in VERSION_RE.findall(line)}


def sync_commit(root, ours: str, theirs: str, message: str, sections: bool = False) -> tuple[str, list[str]]:
    """ours（release/v<版> の先頭）に theirs（起点のブランチの先頭）を取り込んだマージのコミットを作り、
    (コミット, 解いたパス) を返す。作業ツリーと index には触れない。release/v<版> が書いた版数の外が衝突すれば
    VersionConflict。`sections` なら CHANGELOG.md の衝突は節の単位で解く（`resolve_sections`）。"""
    tree, stages = _merge_tree(root, ours, theirs)
    paths, allowed = _release_side(root, ours, theirs) if stages else (set(), set())
    resolved: dict[str, tuple[str, str]] = {}
    bad: list[str] = []
    for path, by in sorted(stages.items()):
        ok = 2 in by and 3 in by and path in paths
        text = git(root, "cat-file", "blob", f"{tree}:{path}", check=False) if ok else None
        fixed = resolve_text(text.stdout, allowed) if text is not None and text.returncode == 0 else None
        if fixed is None and sections and ok and path == CHANGELOG:
            fork = git(root, "merge-base", ours, theirs).stdout.strip()
            fixed = resolve_sections(_blob(root, fork, path), _blob(root, ours, path), _blob(root, theirs, path))
        if fixed is None:
            bad.append(path)
            continue
        oid = run(["git", "-C", str(root), "hash-object", "-w", "--stdin"], input=fixed).stdout.strip()
        resolved[path] = (by[2][0], oid)
    if bad:
        raise VersionConflict(bad)
    if resolved:
        tree = _rewrite_tree(root, tree, resolved)
    sha = git(root, "commit-tree", tree, "-p", ours, "-p", theirs, "-m", message).stdout.strip()
    return sha, sorted(resolved)


def _rewrite_tree(root, tree: str, blobs: dict[str, tuple[str, str]]) -> str:
    """tree のパスを blobs の (mode, oid) へ置き換えた木。作業場所の index は使わない。"""
    fd, index = tempfile.mkstemp(prefix="ndf-version-merge-")
    os.close(fd)
    os.unlink(index)  # 空のファイルは壊れた index とみなされるため、無い状態から read-tree で作らせる
    env = {**os.environ, "GIT_INDEX_FILE": index}
    try:
        run(["git", "-C", str(root), "read-tree", tree], env=env)
        for path, (mode, oid) in blobs.items():
            run(["git", "-C", str(root), "update-index", "--cacheinfo", f"{mode},{oid},{path}"], env=env)
        return run(["git", "-C", str(root), "write-tree"], env=env).stdout.strip()
    finally:
        if os.path.exists(index):
            os.unlink(index)
