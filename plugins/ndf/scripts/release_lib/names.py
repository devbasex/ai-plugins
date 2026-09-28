"""配布の宣言から決まる名前と、CHANGELOG.md の版の節（`release-steps.py` から分けた。#1336）。

ブランチ（ベースブランチ・本番チャネル）は `.ndf/worktree.json`、配るプラグインは引数 → `.ndf/supervise.json` の
release.plugin から読む（`lib/delivery.py`）。CHANGELOG.md の置き場と見出し `## [<plugin> <基底の版>]` は形 package-plugin の約束である。

使う側は `deps.require("md")` を先に呼び、`lib/` を `sys.path` に入れてから import する。
"""

from __future__ import annotations

import delivery
import md
from step_result import EXIT_PRECONDITION, StepError, base_of, git


def release_tag(plugin, version):
    """正式版・開発版のリリースタグ `<plugin>--v<版>`。"""
    return f"{plugin}--v{version}"


def release_tag_before(root, plugin, current=None):
    """<plugin>--v で始まり接尾辞の無いタグのうち、current を除いて最も新しいもの。無ければ None。"""
    head = release_tag(plugin, "")
    tags = git(root, "tag", "--list", f"{head}*", "--sort=-v:refname").stdout.split()
    return next((t for t in tags if t != current and "-" not in t[len(head) :]), None)


def h2_lines(lines):
    """深さ 2 の見出しの行（0 始まり）。コードの囲みの中の `##` は見出しにしない（lib/md.py）。"""
    return [h.line for h in md.headings("\n".join(lines)) if h.level == 2]


def next_h2(lines, at):
    """lines[at] の次の `## ` の見出しの行。無ければ行の数。"""
    return next((i for i in h2_lines(lines) if i > at), len(lines))


def changelog_span(lines, plugin, version):
    """CHANGELOG.md の `## [<plugin> <基底の版>]` の節の (見出しの行, 次の節の行)。無ければ (None, None)。"""
    head = f"## [{plugin} {base_of(version)}]"
    at = next((i for i in h2_lines(lines) if lines[i] == head or lines[i].startswith(head + " ")), None)
    return (None, None) if at is None else (at, next_h2(lines, at))


def plugin_of(root, a) -> str:
    """配るプラグイン: --plugin → `.ndf/supervise.json` の release.plugin。決まらなければ止める。"""
    plugin = getattr(a, "plugin", None) or (delivery.load_delivery(root).release or {}).get("plugin")
    if not plugin:
        raise StepError("配るプラグインを決められない（--plugin か .ndf/supervise.json の release.plugin）", EXIT_PRECONDITION)
    return plugin


def changelog_section(root, version, plugin):
    """CHANGELOG.md の `## [<plugin> <基底の版>]` の節の本文（見出しを除く）を返す。"""
    cl = root / "CHANGELOG.md"
    lines = cl.read_text(encoding="utf-8").split("\n") if cl.is_file() else []
    at, end = changelog_span(lines, plugin, version)
    return "" if at is None else "\n".join(lines[at + 1 : end]).strip()


def release_decl(root, a=None):
    """配布のブランチと名前（#1336 の決定 8）: (ベースブランチ, 本番チャネル, 配るプラグイン)。
    ブランチは `.ndf/worktree.json` の base_branch・production_branch（無ければ既定ブランチ）、プラグインは引数の
    --plugin / --plugins → `.ndf/supervise.json` の release.plugin の順に決める。決まらなければ止める。"""
    d = delivery.load_delivery(root)
    if d.problems:
        raise StepError("宣言を読めない: " + " / ".join(d.problems), EXIT_PRECONDITION)
    arg = getattr(a, "plugin", None) or (getattr(a, "plugins", None) or "").split(",")[0].strip() or None
    plugin = arg or (d.release or {}).get("plugin")
    if not (d.base and d.production):
        raise StepError(
            "ベースブランチか本番チャネルを決められない（.ndf/worktree.json の base_branch・production_branch）", EXIT_PRECONDITION
        )
    if not plugin:
        raise StepError("配るプラグインを決められない（--plugin か .ndf/supervise.json の release.plugin）", EXIT_PRECONDITION)
    return d.base, d.production, plugin
