"""公開の処理: 本番チャネルのコミットへリリースタグを打ち、GitHub Release を作る（#2623 の決定 3）。

CD のワークフローと手動の経路（`release --channel prod`）がここを呼ぶ。出すのは呼び手で、ここは items を返すか
`StepError` で止まる。足りないものだけを作り、既存のタグを書き換えない（I8・I10）。
"""

from __future__ import annotations

import json
import os
import tempfile

import gh_parts
from release_lib.names import changelog_span, latest_release_tag, tag_glob, tag_name, tag_template_of
from step_result import StepError, git, plugin_dir


def wants(decl) -> tuple[bool, bool]:
    """宣言が作らせる公開物: (タグ, GitHub Release)。release.tag が null ならタグも GitHub Release も、github_release が偽なら
    GitHub Release を作らない。作る側（`release-steps.py` の `_published`）と確かめる側（`cmd_record`）が同じ値を使う。"""
    rel = decl.release or {}
    tag = rel.get("tag", "") is not None
    return tag, tag and rel.get("github_release", True) is not False


def manifest_rel(root, plugin) -> str:
    """`plugin` のマニフェストのリポジトリの根からの位置（version_at・untagged_since が読む）。"""
    return (plugin_dir(root, plugin) / ".claude-plugin" / "plugin.json").relative_to(root).as_posix()


def version_at(root, plugin, revision) -> str:
    """`revision` のコミットの plugin.json の版。読めなければ 1 で止める。"""
    rel = manifest_rel(root, plugin)
    p = git(root, "show", f"{revision}:{rel}", check=False)
    try:
        ver = json.loads(p.stdout)["version"] if p.returncode == 0 else None
    except (ValueError, KeyError, TypeError):
        ver = None
    if not isinstance(ver, str) or not ver:
        raise StepError(f"{str(revision)[:8]} の {rel} の版を読めない")
    return ver


def _version_or_none(root, plugin, revision) -> str | None:
    try:
        return version_at(root, plugin, revision)
    except StepError:
        return None


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


def _untagged_manifest(root, plugin):
    """タグを作らない宣言（release.tag が null）なら (マニフェストの位置, 本番ブランチの参照)。ほかと読めなければ None。"""
    import delivery

    decl = delivery.load_delivery(root)
    if decl.production is None or wants(decl)[0]:
        return None
    try:
        rel = manifest_rel(root, plugin)
    except StepError:  # プラグインのマニフェストが無い: 版で起点を決められない
        return None
    prod = f"origin/{decl.production}"
    if git(root, "rev-parse", "-q", "--verify", prod, check=False).returncode != 0:
        prod = decl.production
    return rel, prod


def untagged_since(root, plugin, end) -> str | None:
    """タグを作らない宣言（release.tag が null）で、他のプラグインの差分・承認資料の比較元・版の節の PR の範囲の起点にするコミット。
    本番ブランチの先端の `plugin` の版（出した最新の版）を `end` の履歴で初めて持ったコミット（前のリリースの版上げ）。
    タグを作る宣言と、本番ブランチ・版を読めなければ None。"""
    if (at := _untagged_manifest(root, plugin)) is None:
        return None
    rel, prod = at
    shipped = _version_or_none(root, plugin, prod)
    if shipped is None:
        return None
    found = None
    for c in git(root, "log", "--format=%H", end, "--", rel, check=False).stdout.split():
        if _version_or_none(root, plugin, c) == shipped:
            found = c  # 同じ版の続く並びの最も古いコミットまでさかのぼる
        elif found:
            break
    return found


def untagged_before(root, plugin, ver) -> str | None:
    """タグを作らない宣言で、出した版 `ver` の直前の正式版（リリース記録の直前の版）。本番ブランチの履歴で `plugin` の版を
    新しい順に読み、`ver` と違い接尾辞の無い最初の版。タグを作る宣言と、読めなければ None。"""
    if (at := _untagged_manifest(root, plugin)) is None:
        return None
    rel, prod = at
    for c in git(root, "log", "--format=%H", prod, "--", rel, check=False).stdout.split():
        v = _version_or_none(root, plugin, c)
        if v and v != ver and "-" not in v:
            return v
    return None


def release_tag_before(root, plugin, current=None):
    """宣言の release.tag の形（無ければ `<plugin>--v<版>`）に合い接尾辞の無いタグのうち、current を除いて最も新しいもの。無ければ None。"""
    template = tag_template_of(root)
    tags = git(root, "tag", "--list", tag_glob(plugin, template), "--sort=-v:refname").stdout.split()
    return latest_release_tag(tags, plugin, template, current)


def prev_release(root, plugin, end, current=None):
    """前のリリースの起点（他のプラグインの差分・承認資料の比較元・版の節と notes の PR の範囲）。タグを作らない宣言（release.tag が null）なら前のリリースの
    版上げのコミット（`untagged_since`。宣言をタグ無しへ変える前の古いタグが残っていても使わない）、ほかは current を
    除いて最も新しいタグ。決められなければ None。"""
    return untagged_since(root, plugin, end) or release_tag_before(root, plugin, current)
