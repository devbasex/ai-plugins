"""公開の処理: 本番チャネルのコミットへリリースタグを打ち、GitHub Release を作る（#2623 の決定 3）。

CD のワークフローと手動の経路（`release --channel prod`）がここを呼ぶ。出すのは呼び手で、ここは items を返すか
`StepError` で止まる。足りないものだけを作り、既存のタグを書き換えない（I8・I10）。
"""

from __future__ import annotations

import os
import tempfile

import gh_parts
from release_tag import makes_tags
from release_lib.names import changelog_span, tag_name

# 前のリリースの起点と出済みの版の正本（lib/release_prev.py）を再び公開する
from release_prev import (  # noqa: F401
    manifest_rel,
    prev_release,
    release_tag_before,
    shipped_untagged,
    untagged_before,
    untagged_since,
    version_at,
)
from step_result import StepError, git


def wants(decl) -> tuple[bool, bool]:
    """宣言が作らせる公開物: (タグ, GitHub Release)。release.tag が null ならタグも GitHub Release も、github_release が偽なら
    GitHub Release を作らない。作る側（`release-steps.py` の `_published`）と確かめる側（`cmd_record`）が同じ値を使う。"""
    rel = decl.release or {}
    tag = makes_tags(rel)
    return tag, tag and rel.get("github_release", True) is not False


def _notes_at(root, plugin, ver, revision) -> str:
    """`revision` のコミットの CHANGELOG.md の版の節。無ければ題と同じ 1 行（手元の木が別のコミットでも同じ本文を作る）。"""
    p = git(root, "show", f"{revision}:CHANGELOG.md", check=False)
    lines = p.stdout.split("\n") if p.returncode == 0 else []
    at, end = changelog_span(lines, plugin, ver)
    return ("" if at is None else "\n".join(lines[at + 1 : end]).strip()) or f"{plugin} v{ver}"


def _ensure_tag(root, plugin, ver, tag, revision) -> dict:
    """origin のタグが `revision` を指すことを確かめ、無ければ作って push する。origin か手元の同名タグが別のコミットを指していれば止める。"""
    if git(root, "ls-remote", "--exit-code", "--tags", "origin", f"refs/tags/{tag}", check=False).returncode == 0:
        git(root, "fetch", "-q", "origin", f"+refs/tags/{tag}:refs/tags/{tag}")
        if (at := git(root, "rev-parse", f"refs/tags/{tag}^{{commit}}").stdout.strip()) != revision:
            raise StepError(f"origin のタグ {tag} が本番チャネルのコミット {revision[:8]} でなく {at[:8]} を指す")
        return {"kind": "tag", "name": tag, "result": "exists"}
    local = git(root, "rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}", check=False)
    if local.returncode != 0:
        git(root, "tag", "-a", tag, revision, "-m", f"{plugin} v{ver}")
    elif (at := local.stdout.strip()) != revision:
        raise StepError(f"手元のタグ {tag} が本番チャネルのコミット {revision[:8]} でなく {at[:8]} を指す")
    git(root, "push", "-q", "origin", tag)
    return {"kind": "tag", "name": tag, "result": "pushed"}


def _ensure_release(root, plugin, ver, tag, revision) -> dict:
    """GitHub Release が無ければ、変更履歴の版の節を本文に Latest で作る。"""
    if gh_parts.gh(["release", "view", tag, "--json", "tagName"], cwd=root).returncode == 0:
        return {"kind": "release", "name": tag, "result": "exists"}
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(_notes_at(root, plugin, ver, revision) + "\n")
        notes_file = f.name
    try:
        p = gh_parts.gh(["release", "create", tag, "--title", f"{plugin} v{ver}", "--notes-file", notes_file, "--latest"], cwd=root)
    finally:
        os.unlink(notes_file)
    if p.returncode != 0:
        raise StepError(f"gh release create {tag} が失敗: {p.stderr.strip()[:300]}")
    return {"kind": "release", "name": tag, "result": "created"}


def tag_and_release(root, plugin, ver, tag, revision, release: bool = True) -> list[dict]:
    """タグと GitHub Release のうち足りないものを作り、items（tag・release の 1 件ずつ）を返す。`release` が偽ならタグだけ。"""
    item = _ensure_tag(root, plugin, ver, tag, revision)
    return [item, _ensure_release(root, plugin, ver, tag, revision)] if release else [item]


def released_before(root, plugin, revision) -> dict | None:
    """`revision` の版のタグが origin にあり、`revision` の祖先を指すなら（版を上げないマージ）その item を返す。無ければ None（#3750）。
    祖先でない（別の系統の）コミットを指すときも None で、`publish` が今までどおり止める。"""
    ver = version_at(root, plugin, revision)
    tag = tag_name(root, plugin, ver)
    if git(root, "ls-remote", "--exit-code", "--tags", "origin", f"refs/tags/{tag}", check=False).returncode != 0:
        return None
    git(root, "fetch", "-q", "origin", f"+refs/tags/{tag}:refs/tags/{tag}")
    at = git(root, "rev-parse", f"refs/tags/{tag}^{{commit}}").stdout.strip()
    if at == revision or git(root, "merge-base", "--is-ancestor", at, revision, check=False).returncode != 0:
        return None
    return {"kind": "tag", "name": tag, "result": "unchanged", "at": at}


def publish(root, plugin, revision) -> tuple[str, str, list[dict]]:
    """`revision` のコミットの版で公開する。版に接尾辞があれば何も作らない。(版, タグ, items) を返す。"""
    ver = version_at(root, plugin, revision)
    if "-" in ver:
        raise StepError(f"{revision[:8]} の版 {ver} に接尾辞があるため、タグも GitHub Release も作らない")
    tag = tag_name(root, plugin, ver)
    return ver, tag, tag_and_release(root, plugin, ver, tag, revision)


def made_by(decl, tag) -> str:
    """宣言が作らせる公開物の語（承認資料の同意と戻し方が書く）。`wants` と同じ値で決め、作らなければ空文字。"""
    want_tag, want_release = wants(decl)
    return "" if not want_tag else f"タグ {tag} と GitHub Release" if want_release else f"タグ {tag}"
