"""前のリリースの起点と出済みの版を、宣言のタグの有無に合わせて決める（#3672 の round 11〜15）。

タグを作る宣言は宣言の形（`release_tag`）のリリースタグから、タグを作らない宣言（`release.tag` が null）は本番ブランチの
版（プラグインのマニフェストの version）から引く。宣言をタグ無しへ変える前の古いタグが残っていても、版を読める限りそのタグを使わない。
`release_lib.publish` が再び公開し、`release-steps.py`・`release-verification-steps.py`・`check-trigger.py`・
`release_lib.names` が同じ値を使う（重い依存を持たないよう lib に置く）。

- `prev_release`: 前のリリースの起点（他のプラグインの差分・承認資料の比較元・版の節の PR の範囲・導入の確かめの差分）
- `untagged_before`: タグを作らない宣言で、出した版の直前の正式版（リリース記録の直前の版）
- `shipped_untagged`: タグを作らない宣言で、その版がもう本番ブランチへ出ているか（タグの有無の確かめの代わり）
"""

from __future__ import annotations

import json

from release_tag import latest_release_tag, makes_tags, tag_glob, tag_template_of
from step_result import StepError, base_of, git, plugin_dir


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


def version_or_none(root, plugin, revision) -> str | None:
    """`version_at` の読めなければ None の形。"""
    try:
        return version_at(root, plugin, revision)
    except StepError:
        return None


def _untagged_manifest(root, plugin, remote_only=False):
    """タグを作らない宣言（release.tag が null）なら (マニフェストの位置, 本番ブランチの参照)。ほかと読めなければ None。
    `remote_only` なら origin の本番ブランチだけを読む（手元の本番ブランチは版上げのコミットを先に持ちうる）。"""
    import delivery

    decl = delivery.load_delivery(root)
    if decl.production is None or makes_tags(decl.release):
        return None
    try:
        rel = manifest_rel(root, plugin)
    except StepError:  # プラグインのマニフェストが無い: 版で起点を決められない
        return None
    prod = f"origin/{decl.production}"
    if git(root, "rev-parse", "-q", "--verify", prod, check=False).returncode != 0:
        if remote_only:
            return None
        prod = decl.production
    return rel, prod


def untagged_since(root, plugin, end) -> str | None:
    """タグを作らない宣言（release.tag が null）で、他のプラグインの差分・承認資料の比較元・版の節の PR の範囲の起点にするコミット。
    本番ブランチの先端の `plugin` の版（出した最新の版）を `end` の履歴で初めて持ったコミット（前のリリースの版上げ）。
    タグを作る宣言と、本番ブランチ・版を読めなければ None。"""
    if (at := _untagged_manifest(root, plugin)) is None:
        return None
    rel, prod = at
    shipped = version_or_none(root, plugin, prod)
    if shipped is None:
        return None
    found = None
    for c in git(root, "log", "--format=%H", end, "--", rel, check=False).stdout.split():
        if version_or_none(root, plugin, c) == shipped:
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
        v = version_or_none(root, plugin, c)
        if v and v != ver and "-" not in v:
            return v
    return None


def base_key(v):
    """基底の版の数の組（版の大小を比べる鍵）。読めなければ None。"""
    try:
        return tuple(int(x) for x in base_of(v).split("."))
    except ValueError:
        return None


def shipped_untagged(root, plugin, ver) -> str | None:
    """タグを作らない宣言で、`ver` の基底の版がもう本番ブランチへ出ていれば、origin の本番ブランチの版（出した最新の正式版）。
    出した版は本番ブランチの版以下である。タグを作る宣言・まだ出ていない・読めなければ None（タグの有無はリリースタグで確かめる）。"""
    if (at := _untagged_manifest(root, plugin, remote_only=True)) is None:
        return None
    shipped = version_or_none(root, plugin, at[1])
    if not shipped or "-" in shipped:
        return None
    lo, hi = base_key(ver), base_key(shipped)
    return shipped if lo is not None and hi is not None and lo <= hi else None


def release_tag_before(root, plugin, current=None):
    """宣言の release.tag の形（無ければ既定の形 `release_tag.DEFAULT_TAG`）に合い接尾辞の無いタグのうち、current を除いて最も新しいもの。無ければ None。"""
    template = tag_template_of(root)
    tags = git(root, "tag", "--list", tag_glob(plugin, template), "--sort=-v:refname").stdout.split()
    return latest_release_tag(tags, plugin, template, current)


def prev_release(root, plugin, end, current=None):
    """前のリリースの起点（他のプラグインの差分・承認資料の比較元・版の節と notes の PR の範囲・導入の確かめの差分）。タグを作らない宣言
    （release.tag が null）なら前のリリースの版上げのコミット（`untagged_since`。宣言をタグ無しへ変える前の古いタグが残っていても
    使わない）、ほかは current を除いて最も新しいタグ。版を読めない（マニフェストが無い・本番ブランチが無い）ときもタグへ戻る。
    決められなければ None。"""
    return untagged_since(root, plugin, end) or release_tag_before(root, plugin, current)
