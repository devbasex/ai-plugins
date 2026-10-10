"""版を上げる箇所を集め、bump-my-version で書き換える（`release-steps.py bump` から分けた。#1142 の D7）。

版数を持つ行を集め（どの箇所を集めるかは `release-steps.py` の `plan_bump` が決める）、`lib/versions.py` の
`bump_replace`（bump-my-version の `replace`）の 1 回で書き換える。見つからない箇所と、同じ字面の行がほかにも
あって 1 行に絞れない箇所は、書き換えずに manual として返す。

使う側は `deps.require("versions", "bump")` を先に呼び、`lib/` を `sys.path` に入れてから import する。
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

import versions
from step_result import EXIT_PRECONDITION, StepError, plugin_dir


def ver_pat(v):
    """版の文字列を、前後に版の続きが無いときだけ当てる正規表現にする。"""
    return r"(?:(?<=v)|(?<![0-9A-Za-z.\-]))" + re.escape(v) + r"(?![0-9A-Za-z\-]|\.[0-9A-Za-z])"


CUR, NEW = "{current_version}", "{new_version}"
CUR_BASE, NEW_BASE = "{current_major}.{current_minor}.{current_patch}", "{new_major}.{new_minor}.{new_patch}"
# bump-my-version が版を読む形（このリポジトリの版の形。lib/versions.py と同じ）
BUMP_PARSE = r"(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)(?:-(?P<pre_l>dev|rc)\.(?P<pre_n>\d+))?"
BUMP_SERIALIZE = ["{major}.{minor}.{patch}-{pre_l}.{pre_n}", "{major}.{minor}.{patch}"]


def braces(text):
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
                placed = self._place_twins(path, lines, i, stop, rx, count - done, what)
                if placed is None:
                    return done
                done += placed
            elif re.search(ver_pat(self.new), lines[i]):
                already += 1
        if required and done + already < count:
            self._record_shortfall(path, what, count, done + already)
        return done

    def _place_twins(self, path, lines, i, stop, rx, limit, what):
        """i 行目と同じ字面の行（limit 件まで）を 1 つの書き換えとして足す。足した行数を返し、足せなければ None。"""
        twins = [j for j in range(i, stop) if lines[j] == lines[i] and rx.search(lines[j])][:limit]
        esc = braces(lines[i])
        if not self.place(path, twins, re.sub(ver_pat(self.old), CUR, esc), re.sub(ver_pat(self.old), NEW, esc), what):
            return None
        return len(twins)

    def _record_shortfall(self, path, what, count, found):
        self.manual.append(f"{self.rel(path)}: {what} の旧版 {self.old} が {count} 箇所見つからず {found} 箇所だけ（手で直す）")

    def config(self):
        head = [
            "[tool.bumpversion]",
            f"current_version = {json.dumps(self.old)}",
            f"parse = {json.dumps(BUMP_PARSE)}",
            f"serialize = {json.dumps(BUMP_SERIALIZE)}",
            "commit = false",
            "tag = false",
        ]
        body = []
        for pl in self.places:
            body += ["", "[[tool.bumpversion.files]]"] + [f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in pl.items()]
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


def require_bumped(root, plugin, ver):
    """配る plugin の plugin.json の版が今回の版でなければ止める（bump が通らないまま版を上げない PR を出さない。#1315）。"""
    pdir = plugin_dir(root, plugin)
    try:
        got = json.loads((pdir / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise StepError(f"plugin.json の版を読めない: {e}", EXIT_PRECONDITION)
    if got != ver:
        raise StepError(f"{plugin} の plugin.json の版が {got} で、今回の版 {ver} でない（bump が通っていない）", EXIT_PRECONDITION)
