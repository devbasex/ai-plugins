"""リリースタグの形（宣言の `release.tag`。`{plugin}`・`{version}` を含む形）から、タグの名前・検索のパターン・版を解く。

前版の検索・公開・記録（`release_lib`）、計画の版の観測（`release_version`）、導入の確かめ（`release-verification-steps.py`）、
検査の範囲（`check-trigger.py`）が同じ形を使う。形の解き方の正本はここ 1 か所で、`release_lib.names` はここを再び公開する。
"""

from __future__ import annotations

from step_result import base_of

DEFAULT_TAG = "{plugin}--v{version}"  # 宣言に release.tag の形が無い（null を含む）ときのタグの形


def tag_template(release) -> str:
    """宣言の `release.tag`（`{version}` を含む形）。無い・null・`{version}` を含まないなら既定の形。"""
    t = (release or {}).get("tag") if isinstance(release, dict) else None
    return t if isinstance(t, str) and "{version}" in t else DEFAULT_TAG


def tag_template_of(root) -> str:
    """`root` のリポジトリの宣言から引くタグの形（`tag_template`）。前版の検索・公開・記録が同じ形を使う。"""
    import delivery

    return tag_template(delivery.load_delivery(root).release)


def release_tag(plugin, version, template=DEFAULT_TAG) -> str:
    """基底の版のリリースタグ（形 `template` の `{plugin}`・`{version}` を埋める）。"""
    return template.replace("{version}", base_of(version)).replace("{plugin}", plugin)


def tag_glob(plugin, template=DEFAULT_TAG) -> str:
    """形 `template` のリリースタグを `git tag --list` で探すパターン（版の位置を `*` にする）。"""
    return template.replace("{plugin}", plugin).replace("{version}", "*")


def tag_version(tag, plugin, template=DEFAULT_TAG) -> str | None:
    """形 `template` のリリースタグ `tag` の版（数字で始まる部分）。形に合わなければ None。"""
    head, _, tail = template.replace("{plugin}", plugin).partition("{version}")
    if not (tag.startswith(head) and tag.endswith(tail) and len(tag) > len(head) + len(tail)):
        return None
    v = tag[len(head) : len(tag) - len(tail)]
    return v if v[0].isdigit() else None


def latest_release_tag(tags, plugin, template=DEFAULT_TAG, current=None) -> str | None:
    """新しい順の `tags` から、`current` を除いた最初の正式版（接尾辞の無い版）のタグ。無ければ None。"""
    return next((t for t in tags if t != current and (v := tag_version(t, plugin, template)) and "-" not in v), None)
