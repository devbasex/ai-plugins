"""公開の処理: 本番チャネルのコミットへリリースタグを打ち、GitHub Release を作る（#2623 の決定 3）。

CD のワークフローと手動の経路（`release --channel prod`）がここを呼ぶ。出すのは呼び手で、ここは items を返すか
`StepError` で止まる。足りないものだけを作り、既存のタグを書き換えない（I8・I10）。
"""

from __future__ import annotations

import json
import os
import tempfile

import gh_parts
from release_lib.names import changelog_span
from step_result import StepError, git, plugin_dir


def version_at(root, plugin, revision) -> str:
    """`revision` のコミットの plugin.json の版。読めなければ 1 で止める。"""
    rel = (plugin_dir(root, plugin) / ".claude-plugin" / "plugin.json").relative_to(root).as_posix()
    p = git(root, "show", f"{revision}:{rel}", check=False)
    try:
        ver = json.loads(p.stdout)["version"] if p.returncode == 0 else None
    except (ValueError, KeyError, TypeError):
        ver = None
    if not isinstance(ver, str) or not ver:
        raise StepError(f"{str(revision)[:8]} の {rel} の版を読めない")
    return ver


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


def tag_and_release(root, plugin, ver, tag, revision) -> list[dict]:
    """タグと GitHub Release のうち足りないものを作り、items（tag・release の 1 件ずつ）を返す。"""
    item = _ensure_tag(root, plugin, ver, tag, revision)
    return [item, _ensure_release(root, plugin, ver, tag, revision)]


def publish(root, plugin, revision) -> tuple[str, str, list[dict]]:
    """`revision` のコミットの版で公開する。版に接尾辞があれば何も作らない。(版, タグ, items) を返す。"""
    ver = version_at(root, plugin, revision)
    if "-" in ver:
        raise StepError(f"{revision[:8]} の版 {ver} に接尾辞があるため、タグも GitHub Release も作らない")
    tag = f"{plugin}--v{ver}"
    return ver, tag, tag_and_release(root, plugin, ver, tag, revision)
