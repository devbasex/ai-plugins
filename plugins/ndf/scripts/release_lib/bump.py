"""版を上げる箇所を集め、bump-my-version で書き換える（`release-steps.py bump` から分けた。#1142 の D7）。

版数を持つ箇所（プラグインのマニフェスト・marketplace の説明・README の一覧表と更新案内の見出し・NDF の説明文書）を
行の単位で集め、`lib/versions.py` の `bump_replace`（bump-my-version の `replace`）の 1 回で書き換える。
見つからない箇所と、同じ字面の行がほかにもあって 1 行に絞れない箇所は、書き換えずに manual として返す。

使う側は `deps.require("versions", "bump")` を先に呼び、`lib/` を `sys.path` に入れてから import する。
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import versions
from step_result import StepError, base_of


def ver_pat(v):
    """版の文字列を、前後に版の続きが無いときだけ当てる正規表現にする。"""
    return r"(?:(?<=v)|(?<![0-9A-Za-z.\-]))" + re.escape(v) + r"(?![0-9A-Za-z\-]|\.[0-9A-Za-z])"


CUR, NEW = "{current_version}", "{new_version}"
CUR_BASE, NEW_BASE = "{current_major}.{current_minor}.{current_patch}", "{new_major}.{new_minor}.{new_patch}"
# bump-my-version が版を読む形（このリポジトリの版の形。lib/versions.py と同じ）
BUMP_PARSE = r"(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-(?P<pre_l>dev|rc)\.(?P<pre_n>\d+))?"
BUMP_SERIALIZE = ["{major}.{minor}.{patch}-{pre_l}.{pre_n}", "{major}.{minor}.{patch}"]


def _braces(text):
    return text.replace("{", "{{").replace("}", "}}")


class BumpPlan:
    """版数を持つ行を集め、bump-my-version の `replace` の 1 回で書き換える。見つからなかった箇所は manual へ集める。

    集めるのは行の全体で、行の中の旧版を `{current_version}` にした字面を search にする（行の外の同じ版は変えない）。
    同じ字面の行が集めていない場所にもあれば、書き換えずに manual へ回す。
    """

    def __init__(self, root, old, new):
        self.root, self.old, self.new = root, old, new
        self.places, self.files, self.manual = [], [], []
        self._taken = set()

    def lines(self, path):
        return path.read_text(encoding="utf-8").split("\n")

    def rel(self, path):
        return path.relative_to(self.root).as_posix()

    def place(self, path, line_nos, search, replace, what):
        """同じ字面の行（`line_nos`）を 1 つの書き換えとして足す。同じ字面が `line_nos` の外にもあれば足さずに
        manual へ回す（bump-my-version はファイルの中の同じ字面をすべて書き換える）。"""
        lines = self.lines(path)
        line = lines[line_nos[0]]
        same = [i for i, l in enumerate(lines) if line in l]
        if sorted(same) != sorted(line_nos) or "\n".join(lines).count(line) != len(same):
            self.manual.append(f"{self.rel(path)}: {what} の行と同じ字面がほかにもある（手で直す）")
            return False
        self._taken.update((self.rel(path), i) for i in line_nos)
        self.places.append({"filename": self.rel(path), "search": search, "replace": replace})
        if self.rel(path) not in self.files:
            self.files.append(self.rel(path))
        return True

    def sub(self, path, line_re, what, count=1, start=0, stop=None, required=True):
        """line_re に合う行の中の旧版を新版へ直す行として集める。集めた行数を返す。"""
        if not path.is_file():
            if required:
                self.manual.append(f"{self.rel(path)} が無い（{what}）")
            return 0
        lines = self.lines(path)
        stop = len(lines) if stop is None else stop
        done, already = 0, 0
        rx = re.compile(line_re)
        for i in range(start, stop):
            if done >= count:
                break
            if not rx.search(lines[i]) or (self.rel(path), i) in self._taken:
                continue
            if re.search(ver_pat(self.old), lines[i]):
                twins = [j for j in range(i, stop) if lines[j] == lines[i] and rx.search(lines[j])][:count - done]
                esc = _braces(lines[i])
                if self.place(path, twins, re.sub(ver_pat(self.old), CUR, esc), re.sub(ver_pat(self.old), NEW, esc),
                              what):
                    done += len(twins)
                else:
                    return done
            elif re.search(ver_pat(self.new), lines[i]):
                already += 1
        if required and done + already < count:
            self.manual.append(
                f"{self.rel(path)}: {what} の旧版 {self.old} が"
                f" {count} 箇所見つからず {done + already} 箇所だけ（手で直す）")
        return done

    def config(self):
        head = ["[tool.bumpversion]", f"current_version = {json.dumps(self.old)}",
                f"parse = {json.dumps(BUMP_PARSE)}", f"serialize = {json.dumps(BUMP_SERIALIZE)}",
                "commit = false", "tag = false"]
        body = []
        for pl in self.places:
            body += ["", "[[tool.bumpversion.files]]"] + [f"{k} = {json.dumps(v, ensure_ascii=False)}"
                                                         for k, v in pl.items()]
        return "\n".join(head + body) + "\n"

    def apply(self):
        """集めた行を bump-my-version で書き換える。落ちたら何も書かずに止める（書き換えは 1 回の呼び出し）。"""
        if not self.places:
            return
        with tempfile.TemporaryDirectory(prefix="ndf-bump-") as d:
            cfg = Path(d) / "bumpversion.toml"
            cfg.write_text(self.config(), encoding="utf-8")
            res = versions.bump_replace(cfg, self.old, self.new, self.root)
        if not res.ok:
            raise StepError(f"bump-my-version が {self.old} → {self.new} を書き換えられない: {res.output.strip()[-300:]}")


def bump_update_heading(ed, readme):
    """README の更新案内の見出しを新しい版へ書き換える（チェックは見出しを現行の版の 1 つだけに求める）。"""
    rel = readme.relative_to(ed.root).as_posix()
    if not readme.is_file():
        ed.manual.append(f"{rel} が無い（更新案内の見出し）")
        return
    lines = ed.lines(readme)
    new_h = f"## v{ed.new} へ更新するとき"
    if new_h in lines:
        return
    rx = re.compile(r"^## v\S+ へ更新するとき$")
    at = next((i for i, l in enumerate(lines) if rx.match(l)), None)
    if at is None:
        ed.manual.append(f"{rel}: 更新案内の見出しが無い（「{new_h}」を手で足す）")
        return
    if not ed.place(readme, [at], _braces(lines[at]), f"## v{NEW} へ更新するとき", "更新案内の見出し"):
        return
    if base_of(ed.old) != base_of(ed.new):
        ed.manual.append(f"{rel}: 更新案内の本文を v{ed.new} の変更へ書き直す（changelog が PR の一覧へ差し替える）")


def bump_versioning_doc(ed):
    """docs/versioning-and-distribution.md の「版の付け方と開発版の配布」章の正式版の版数。"""
    doc = ed.root / "docs" / "versioning-and-distribution.md"
    rel = "docs/versioning-and-distribution.md"
    ob, nb = base_of(ed.old), base_of(ed.new)
    if ob == nb:
        return
    if not doc.is_file():
        ed.manual.append(f"{rel} が無い（この章は手で直す）")
        return
    lines = ed.lines(doc)
    targets = [
        (re.compile(r"^\| 正式版 \| `([^`]+)` \|"), "正式版の表の行"),
        (re.compile(r"`([^`]+)` の次を開発するなら"), "接尾辞の例"),
    ]
    for rx, what in targets:
        hits = [i for i, l in enumerate(lines) if rx.search(l)]
        if len(hits) != 1:
            ed.manual.append(f"{rel}: 「版の付け方と開発版の配布」章の{what}が特定できない（この章は手で直す）")
            continue
        i = hits[0]
        cur = rx.search(lines[i]).group(1)
        if cur == nb:
            continue
        if cur != ob:
            ed.manual.append(f"{rel}: {what}の版が {cur} で旧版 {ob} と違う（この章は手で直す）")
            continue
        esc = _braces(lines[i])
        ed.place(doc, [i], esc.replace(f"`{ob}`", f"`{CUR_BASE}`", 1), esc.replace(f"`{ob}`", f"`{NEW_BASE}`", 1), what)


def marketplace_range(lines, name):
    """marketplace.json の中で、その plugin の項目の行の範囲（name の行から次の name の行まで）。"""
    rx = re.compile(r'^\s*"name"\s*:\s*"([^"]+)"')
    start = None
    for i, l in enumerate(lines):
        m = rx.match(l)
        if not m:
            continue
        if start is not None:
            return start, i
        if m.group(1) == name:
            start = i
    return (start, len(lines)) if start is not None else (None, None)


def plan_bump(root, pdir, plugin, old, new):
    """`plugin` の版を `old` から `new` へ上げる箇所を集めた `BumpPlan` を返す（まだ書き換えない）。"""
    ed = BumpPlan(root, old, new)
    desc_re = r'^\s*"description"\s*:.*\(v' + re.escape(old) + r"\)"

    for rel, _ in ((".claude-plugin/plugin.json", True), (".codex-plugin/plugin.json", False),
                   ("dev.agy/plugin.json", False), ("plugin.json", False)):
        f = pdir / rel
        if not f.is_file():
            continue
        ed.sub(f, r'^\s*"version"\s*:', f"{rel} の version")
        if f"(v{old})" in f.read_text(encoding="utf-8"):
            ed.sub(f, desc_re, f"{rel} の description")

    mp = root / ".claude-plugin" / "marketplace.json"
    if mp.is_file():
        s, e = marketplace_range(ed.lines(mp), plugin)
        if s is None:
            ed.manual.append(f".claude-plugin/marketplace.json に {plugin} の項目が無い")
        elif any(f"(v{old})" in l for l in ed.lines(mp)[s:e]):
            ed.sub(mp, desc_re, "marketplace.json の description", start=s, stop=e)

    readme = root / "README.md"
    rows = [l for l in (ed.lines(readme) if readme.is_file() else []) if l.startswith(f"| **{plugin}** |")]
    if rows:
        ed.sub(readme, r"^\| \*\*" + re.escape(plugin) + r"\*\* \|", "README.md のプラグイン一覧表")
    elif plugin in ("ndf", "playwright-kit"):
        ed.manual.append(f"README.md のプラグイン一覧表に {plugin} の行が無い")
    if plugin == "ndf":
        ed.sub(readme, r"\*\*NDFプラグイン v", "README.md の概要の版")
        ed.sub(root / "AGENTS.md", r"主要プラグインです（v", "AGENTS.md の版")
        nr = pdir / "README.md"
        ed.sub(nr, r"（Kiro CLI用 / v", "plugins/ndf/README.md の Kiro の確認例")
        ed.sub(nr, r"/plugins/cache/ai-plugins/ndf/", "plugins/ndf/README.md の Codex のパス例", count=2)
        ed.sub(nr, r"ndf@ai-plugins\s+installed", "plugins/ndf/README.md の codex plugin list の出力例")
        bump_versioning_doc(ed)

    bump_update_heading(ed, pdir / "README.md")
    return ed
