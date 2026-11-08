"""リリースの設定から決まる名前と、CHANGELOG.md の版の節（`release-steps.py` から分けた。#1336）。

ブランチ（ベースブランチ・本番チャネル）は `.ndf/worktree.json`、配るプラグインは引数 → `.ndf/supervise.json` の
release.plugin から読む（`lib/delivery.py`）。CHANGELOG.md の置き場と見出し `## [<plugin> <基底の版>]` は形 package-plugin の約束である。

使う側は `deps.require("md")` を先に呼び、`lib/` を `sys.path` に入れてから import する。
"""

from __future__ import annotations

import re

import delivery
import md
import proc
from step_result import EXIT_PRECONDITION, StepError, base_of


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


def tagged(root, tag) -> bool:
    """手元にタグ tag があるか（呼び手が先にタグを fetch する）。"""
    return proc.git(root, "rev-parse", "-q", "--verify", f"refs/tags/{tag}", check=False).returncode == 0


def require_untagged(root, tag, version):
    """リリースタグのある版の節は書き換えない（#2555 の I3）。基底の版のリリースタグ tag があれば書き込む前に止める。"""
    if tagged(root, tag):
        raise StepError(f"{base_of(version)} はリリースタグ {tag} があるため版の節へ書かない", EXIT_PRECONDITION)


def _base_key(v):
    try:
        return tuple(int(x) for x in base_of(v).split("."))
    except ValueError:
        return None


def drop_untagged_sections(lines, plugin, prev, version, has_tag) -> list[str]:
    """前のリリースタグの版 prev より新しく出す正式版 version より古い、リリースタグの無い版（has_tag(版) が偽）の節を
    lines から消し（その場で書き換える）、消した見出しを返す（#2555 の I4）。prev が無ければ消さない。"""
    lo, hi = _base_key(prev or ""), _base_key(version)
    if lo is None or hi is None:
        return []
    head = f"## [{plugin} "
    gone = []
    for at in reversed(h2_lines(lines)):
        m = re.match(rf"{re.escape(head)}([^\]\s]+)\]", lines[at])
        key = m and _base_key(m.group(1))
        if key and lo < key < hi and not has_tag(m.group(1)):
            gone.append(lines[at])
            del lines[at : next_h2(lines, at)]
    return gone[::-1]


def plugin_of(root, a) -> str:
    """配るプラグイン: --plugin → `.ndf/supervise.json` の release.plugin。決まらなければ止める。"""
    return _plugin_from(a, delivery.load_delivery(root))


def _plugin_from(a, d):
    """--plugin → --plugins の先頭 → 宣言 `d` の release.plugin の順に決める。決まらなければ止める。"""
    arg = getattr(a, "plugin", None) or (getattr(a, "plugins", None) or "").split(",")[0].strip() or None
    plugin = arg or (d.release or {}).get("plugin")
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
    """リリースのブランチと名前（#1336 の決定 8）: (ベースブランチ, 本番チャネル, 配るプラグイン)。
    ブランチは `.ndf/worktree.json` の base_branch・production_branch（無ければ既定ブランチ）、プラグインは引数の
    --plugin / --plugins → `.ndf/supervise.json` の release.plugin の順に決める。決まらなければ止める。"""
    d = delivery.load_delivery(root)
    if d.problems:
        raise StepError("宣言を読めない: " + " / ".join(d.problems), EXIT_PRECONDITION)
    if not (d.base and d.production):
        raise StepError(
            "ベースブランチか本番チャネルを決められない（.ndf/worktree.json の base_branch・production_branch）", EXIT_PRECONDITION
        )
    return d.base, d.production, _plugin_from(a, d)
