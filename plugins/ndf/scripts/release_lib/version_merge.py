"""release/v<版> を起点のブランチへ取り込むときの版数の衝突を解く（#2436 の R5。`release-steps.py` の release から分けた）。

承認の後に他の配布が起点のブランチの版数を上げていると、release/v<版> の取り込みが版数を持つ箇所で衝突する。
release/v<版> そのものへは起点のブランチを取り込まず（取り込むと承認の外のコミットが本番チャネルへ出る）、
release/v<版> の先頭と起点のブランチの先頭を親に持つマージのコミットを作業ツリーに触れずに作る。衝突した塊が
版数の違いだけなら release/v<版> の側（本番の版）を取り、1 つでも版数の外が違えば解かずに止める。
"""

from __future__ import annotations

import os
import re
import tempfile

from step_result import StepError, git, run

VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?")
_OURS, _SEP, _THEIRS = "<<<<<<< ", "=======", ">>>>>>> "


class VersionConflict(StepError):
    """版数の外の箇所が衝突した（解かずに止める。終了コード 1）。paths は解けなかったパス。"""

    def __init__(self, paths: list[str]):
        super().__init__(f"取り込みで版数の外の箇所が衝突した（解かずに止めた）: {', '.join(paths)}", 1)
        self.paths = paths


def _same_but_versions(a: list[str], b: list[str]) -> bool:
    return [VERSION_RE.sub("<v>", line) for line in a] == [VERSION_RE.sub("<v>", line) for line in b]


def resolve_text(text: str) -> str | None:
    """衝突の印を持つ本文の塊がすべて版数の違いだけなら、こちら（release/v<版>）の側を取った本文。違えば None。"""
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
            if not _same_but_versions(ours, theirs):
                return None
            out.extend(ours)
            ours = theirs = None
        else:
            theirs.append(line)
    return "".join(out) if ours is None else None


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


def sync_commit(root, ours: str, theirs: str, message: str) -> tuple[str, list[str]]:
    """ours（release/v<版> の先頭）に theirs（起点のブランチの先頭）を取り込んだマージのコミットを作り、
    (コミット, 版数で解いたパス) を返す。作業ツリーと index には触れない。版数の外が衝突すれば VersionConflict。"""
    tree, stages = _merge_tree(root, ours, theirs)
    resolved: dict[str, tuple[str, str]] = {}
    bad: list[str] = []
    for path, by in sorted(stages.items()):
        text = git(root, "cat-file", "blob", f"{tree}:{path}", check=False) if 2 in by and 3 in by else None
        fixed = resolve_text(text.stdout) if text is not None and text.returncode == 0 else None
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
