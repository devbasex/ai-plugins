"""PR と時刻を「載った版」（PR が載った正式版）へ寄せる（#1316 の決定 1・2・11）。

計測のスクリプト（`scripts/token-usage.py` の軸 `release` と `scripts/measure/claude-p-usage.py`）が呼ぶ
唯一の寄せ方。寄せる順は次のとおり:

1. CHANGELOG の版の節（`## [<plugin> <版>]`）に PR の番号が載っていれば、その版（`changelog`）
2. PR がマージ済みなら、マージの時刻の後に最初に打たれた正式版のタグ（`merged_at`）
3. どの PR も寄せられなければ、行の時刻の後に最初に打たれた正式版のタグ（`time_only`）

正式版は、タグ `<plugin>--v<版>` のうち版が接尾辞（`-dev.N`・`-rc.N`）を持たないもの。版の系列には縛らない。
版の PR 数（`prs_of`）は PR の一覧と寄せ方だけで決め、観測した会話・計画の有無に依らない（I5）。
数えるのはマージ済みで、ブランチが `release/` で始まらない PR。
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_PLUGIN = "ndf"
PR_LIST_LIMIT = 2000
# 正式版の版数の形（タグと CHANGELOG の見出しで同じ定義を使う）
RELEASE_VERSION = r"\d+(?:\.\d+)*"


def ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


@dataclass(frozen=True)
class Release:
    version: str
    tagged_at: float


@dataclass(frozen=True)
class Placement:
    pr: int | None
    version: str | None
    by: str  # changelog / merged_at / time_only
    reason: str = ""  # time_only のとき、PR に寄せられなかった理由（PR 無し / pr_unmerged / 版が無い）


def load_releases(repo: Path, plugin: str = DEFAULT_PLUGIN) -> list[Release]:
    """タグ `<plugin>--v*` のうち正式版を、打った時刻の順に返す。"""
    prefix = f"{plugin}--v"
    out = subprocess.run(
        ["git", "tag", "-l", f"{prefix}*", "--format=%(refname:short) %(creatordate:unix)"],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout
    rel = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[0].startswith(prefix):
            continue
        v = parts[0][len(prefix) :]
        if "-" in v or not re.fullmatch(RELEASE_VERSION, v):
            continue
        rel.append(Release(v, float(parts[1])))
    return sorted(rel, key=lambda r: r.tagged_at)


def changelog_prs(text: str, plugin: str = DEFAULT_PLUGIN) -> dict[int, str]:
    """CHANGELOG の `## [<plugin> <正式版>]` の節に現れる `#<番号>` → 版。先に現れた（新しい）版を採る。"""
    head = re.compile(r"## \[" + re.escape(plugin) + " (" + RELEASE_VERSION + r")\]")
    sec, m = None, {}
    for line in text.split("\n"):
        h = head.match(line)
        if h:
            sec = h.group(1)
            continue
        if line.startswith("## ["):
            sec = None
        if sec:
            for n in re.findall(r"#(\d+)", line):
                m.setdefault(int(n), sec)
    return m


def load_prs(repo: Path, cache: Path | None = None, limit: int = PR_LIST_LIMIT) -> list[dict]:
    """PR の一覧（`gh pr list --state all`）。`cache` があれば読み、無ければ 1 回打って書く。"""
    if cache is not None and cache.exists():
        return json.loads(cache.read_text())
    data = subprocess.run(
        ["gh", "pr", "list", "--state", "all", "--limit", str(limit), "--json", "number,title,headRefName,mergedAt,createdAt,state"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(data)
    return json.loads(data)


class ReleaseMap:
    def __init__(self, releases: list[Release], changelog: dict[int, str], prs: list[dict]):
        self.versions = sorted(releases, key=lambda r: r.tagged_at)
        self.changelog = changelog
        self.prs = {p["number"]: p for p in prs}
        self.by_branch: dict[str, list[dict]] = {}
        self.by_issue: dict[int, list[dict]] = {}
        for p in prs:
            self.by_branch.setdefault(p.get("headRefName") or "", []).append(p)
            for n in re.findall(r"#(\d+)", p.get("title") or ""):
                self.by_issue.setdefault(int(n), []).append(p)
        self._counts: dict[str | None, set[int]] | None = None

    @classmethod
    def from_repo(cls, repo: Path, prs: list[dict], plugin: str = DEFAULT_PLUGIN) -> "ReleaseMap":
        cl = repo / "CHANGELOG.md"
        text = cl.read_text(encoding="utf-8") if cl.exists() else ""
        return cls(load_releases(repo, plugin), changelog_prs(text, plugin), prs)

    def version_at(self, t: float | None) -> str | None:
        """時刻 t の後に最初に打たれた正式版（その作業が載る版）。"""
        if t is None:
            return None
        for r in self.versions:
            if t <= r.tagged_at:
                return r.version
        return None

    def version_of_pr(self, n: int) -> tuple[str | None, str]:
        if n in self.changelog:
            return self.changelog[n], "changelog"
        p = self.prs.get(n)
        if p and p.get("mergedAt"):
            v = self.version_at(ts(p["mergedAt"]))
            return v, "merged_at" if v else "版が無い"
        return None, "pr_unmerged"

    def place(self, prs: list[int], t: float | None) -> Placement:
        """PR の候補（先頭から）を載った版へ寄せる。どれも寄せられなければ時刻 t で版だけを決める。"""
        reasons = []
        for n in prs:
            v, how = self.version_of_pr(n)
            if v:
                return Placement(n, v, how)
            reasons.append(how)
        return Placement(prs[0] if prs else None, self.version_at(t), "time_only", reasons[0] if reasons else "PR 無し")

    def prs_of(self, version: str | None) -> set[int]:
        """版に載った PR（マージ済み・`release/` 以外のブランチ）の番号。"""
        if self._counts is None:
            self._counts = {}
            for n, p in self.prs.items():
                if not p.get("mergedAt") or (p.get("headRefName") or "").startswith("release/"):
                    continue
                self._counts.setdefault(self.version_of_pr(n)[0], set()).add(n)
        return set(self._counts.get(version, set()))

    def merged_pr_of_issues(self, issues: list[int]) -> list[int]:
        """課題を題に持つマージ済みの PR のうち、番号の最も小さい 1 件（無ければ空）。"""
        found = [p["number"] for i in issues for p in self.by_issue.get(i, []) if p.get("mergedAt")]
        return sorted(set(found))[:1]

    def window(self, version: str) -> tuple[float | None, float]:
        """版の期間（前の正式版のタグの時刻, この版のタグの時刻]。"""
        for i, r in enumerate(self.versions):
            if r.version == version:
                return (self.versions[i - 1].tagged_at if i else None), r.tagged_at
        raise KeyError(version)
