"""strategy_rows.py: 取り込み方の表とレビューの単位の表（#3672 の D11。`lib/branch_strategy.py` の補助）。

取り込み方の表 `ROWS` の行の名前は、ブランチ戦略の宣言（`.ndf/project.json` の `branch_strategy`）の `release_strategy` の値
（`merge_base_branch`・`release_branch`・`trunk`）と同じである。範囲の決め方・組み直しの判定・スプリントブランチを
残すかを行が持ち（リリースブランチを組む Git の手順は `release_lib/strategy_route.py`）、呼ぶ側は行の属性とメソッドだけを読み、取り込み方の名前で分岐しない。

レビューの単位（セクションの `reviews`。#3163）は、PR の種類ごとに掛けるレビュー（`checks`）と対象の差分（`diff`）を持つ。
形と取り込み方との組み合わせ（R9）はスキーマが確かめ、実装の差分を覆うか（I4）と重なり（I12）だけを `review_problems` が確かめる。

特定のブランチ名（既定ブランチの候補・測る候補）は、このモジュールの定数だけに置く。標準ライブラリだけで書く。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field

# 既定ブランチを origin の HEAD から決められないときに探すローカルのブランチ（`repo.default_branch`）
DEFAULT_BRANCH_CANDIDATES = ("main", "master")
# 測定（`project-decl.py measure`）と既存の宣言の指紋が候補にするブランチ名
MEASURE_CANDIDATES = ("main", "master", "develop")
# スプリントブランチとリリースブランチの頭（名前の規則は NDF が決める）
SPRINT_PREFIX = "sprint/"
RELEASE_PREFIX = "release/v"

# リリースブランチを組むマージの件名（`release_lib/strategy_route.py` の `_build_release_branch`）。範囲の PR（`release_lib/included.py`）が単位の中の PR を拾う目印
UNIT_MERGE = re.compile(rf"^Merge (?:origin/)?{re.escape(SPRINT_PREFIX)}\S+ into {re.escape(RELEASE_PREFIX)}\S+$")


def unit_merge_subject(unit: str, branch: str) -> str:
    return f"Merge {unit} into {branch}"


def git_run(root, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)


def git_succeeds(root, *args: str) -> bool:
    return git_run(root, *args).returncode == 0


def ancestor_of(root, a: str, b: str) -> bool:
    return git_run(root, "merge-base", "--is-ancestor", a, b).returncode == 0


def commit_resolved_merge(root, subject: str) -> bool:
    """止まったマージが rerere の記録で衝突をすべて解き index へ載せていれば、`subject` でコミットして True。
    マージの途中でない・未解消のパスが残る・コミットできなければ False（呼び手が取り消す）。"""
    if not git_succeeds(root, "rev-parse", "-q", "--verify", "MERGE_HEAD"):
        return False
    unmerged = git_run(root, "diff", "--name-only", "--diff-filter=U")
    if unmerged.returncode != 0 or unmerged.stdout.strip():
        return False
    return git_succeeds(root, "commit", "-q", "-m", subject)


class ReleaseBuildError(RuntimeError):
    """リリースブランチを組めない（衝突・単位が無い）。止めて人へ戻す（E10）。"""


def _moved_units(presented: dict, now: dict) -> list[str]:
    """提示の時点から動いた単位（ブランチ）の名前。答えの時点の値が取れない単位は比べない。"""
    now_units = now.get("units") or {}
    return [
        name for name, sha in (presented.get("units") or {}).items() if (cur := now_units.get(name)) and cur.strip() != str(sha).strip()
    ]


@dataclass(frozen=True)
class Row:
    """取り込み方の表の 1 行。"""

    name: str
    keeps_sprint_branch: bool  # 本番ブランチに含まれるまでスプリントブランチを残すか
    selects_units: bool  # リリースの単位を選んで本番ブランチから組むか
    # 組み直しの判定で比べる先頭（`base` = ベースブランチ、`units` = 選んだ単位、`production` = 本番ブランチ）
    watched: tuple = field(default_factory=tuple)
    summary: str = ""

    def moved(self, presented: dict, now: dict) -> list[str]:
        """提示の時点の先頭（`presented`）から、答えの時点（`now`）で動いた先頭の名前。空なら組み直さない。

        どちらも `{"base": SHA, "production": SHA, "units": {ブランチ: SHA}}` の形。答えの時点の値が取れない先頭は比べない。"""
        out = []
        for k in self.watched:
            if k == "units":
                out += _moved_units(presented, now)
                continue
            cur = now.get(k)
            if cur and cur.strip() != str(presented.get(k) or "").strip():
                out.append(k)
        return out

    def release_range(self, production_ref: str, prev_tag: str | None, head: str) -> tuple[str, str]:
        """承認資料の範囲 `(元, 先)`。丸ごと出す行は本番ブランチ..提示したコミット、選ぶ行は前の本番のタグ..リリースブランチの先頭。"""
        if self.selects_units:
            return (prev_tag or production_ref, head)
        return (production_ref, head)


ROWS: dict[str, Row] = {
    "merge_base_branch": Row(
        name="merge_base_branch",
        keeps_sprint_branch=False,
        selects_units=False,
        watched=("base",),
        summary="ベースブランチを丸ごと本番ブランチへ出す",
    ),
    "release_branch": Row(
        name="release_branch",
        keeps_sprint_branch=True,
        selects_units=True,
        watched=("units", "production"),
        summary="本番ブランチから切ったリリースブランチへ、選んだスプリントブランチを集める",
    ),
    "trunk": Row(
        name="trunk",
        keeps_sprint_branch=False,
        selects_units=False,
        watched=("production",),
        summary="スプリントの PR を本番ブランチへ直接入れる",
    ),
}


def sprint_branch_kept(root, branch_ref: str, production_ref: str, row: Row) -> bool:
    """後片付けでスプリントブランチを残すか。残す行では、本番ブランチに含まれるまで残す。"""
    return row.keeps_sprint_branch and not ancestor_of(root, branch_ref, production_ref)


# --- レビューの単位（#3163） ---------------------------------------------------------------
REVIEWS_KEY = "reviews"
# PR の種類: 実装 PR・スプリントの PR・リリースブランチの PR・設計 PR・確定仕様化の PR
REVIEW_KINDS = ("impl", "sprint", "release", "design", "spec")
# 掛けるレビュー。並びがステップを置く順（リファクタリング → cross-review → 振る舞いの確認）の唯一の置き場
CHECKS = ("cross-refactoring", "cross-review", "behavior")
# 対象の差分: PR の差分全体・未検査の差分・衝突の解消の差分・掛けない
DIFFS = ("pr", "unchecked", "resolution", "none")
# 実装の差分を覆う種類 → その差分（I4。`release` の `resolution` は衝突の解消の差分しか見ないため数えない）
COVERING = {"impl": "pr", "sprint": "unchecked", "release": "pr"}
# 衝突の解消のレビューの PR の見せるブランチの頭と、土台のブランチの後ろ
REVIEW_PREFIX = f"review/{RELEASE_PREFIX}"
REVIEW_BASE_SUFFIX = "-base"


@dataclass(frozen=True)
class ReviewRow:
    """レビューの単位の 1 行（PR の種類・掛けるレビューの組・対象の差分）。"""

    kind: str
    checks: tuple = ()
    diff: str = "none"

    def runs(self, check: str) -> bool:
        return check in self.checks

    def covers_impl(self) -> bool:
        """実装の差分を cross-review で覆うか（I4）。"""
        return self.runs("cross-review") and COVERING.get(self.kind) == self.diff

    def as_dict(self) -> dict:
        return {"checks": list(self.checks), "diff": self.diff}


def _ordered_row(kind: str, checks, diff: str) -> ReviewRow:
    return ReviewRow(kind, tuple(c for c in CHECKS if c in checks), diff)


# 種類 → 許す checks・diff（スキーマの `reviews` と同じ値。測定の候補に使う）
REVIEW_ALLOWED: dict[str, dict] = {
    "impl": {"checks": CHECKS, "diff": ("pr", "none")},
    "sprint": {"checks": CHECKS, "diff": ("unchecked", "none")},
    "release": {"checks": ("cross-review",), "diff": ("pr", "resolution", "none")},
    "design": {"checks": ("cross-review",), "diff": ("pr", "none")},
    "spec": {"checks": ("cross-review",), "diff": ("pr", "none")},
}
# 書いていない種類の値。キーは課題のまとまりの記録が有るか（I5）
REVIEW_DEFAULTS: dict[str, dict[bool, ReviewRow]] = {
    "impl": {True: _ordered_row("impl", ("cross-review",), "pr"), False: _ordered_row("impl", (), "none")},
    "sprint": {
        True: _ordered_row("sprint", ("cross-refactoring", "behavior"), "unchecked"),
        False: _ordered_row("sprint", CHECKS, "unchecked"),
    },
    "release": {True: _ordered_row("release", (), "none"), False: _ordered_row("release", (), "none")},
    "design": {True: _ordered_row("design", ("cross-review",), "pr"), False: _ordered_row("design", ("cross-review",), "pr")},
    "spec": {True: _ordered_row("spec", (), "none"), False: _ordered_row("spec", (), "none")},
}


@dataclass(frozen=True)
class ReviewUnits:
    """宣言に書いた種類の行だけを持つ。書いていない種類は `row` が既定値の表から解く。"""

    declared: dict = field(default_factory=dict)

    def row(self, kind: str, bundled: bool) -> ReviewRow:
        return self.declared.get(kind) or REVIEW_DEFAULTS[kind][bundled]

    def as_dict(self) -> dict:
        return {k: self.declared[k].as_dict() for k in REVIEW_KINDS if k in self.declared}


def reviews_of(value) -> ReviewUnits:
    """スキーマを通ったセクションの `reviews` の値を読む。無ければ（None）すべての種類が既定値。"""
    rows = {
        k: _ordered_row(k, tuple(v.get("checks") or ()), v.get("diff") or "none") for k, v in (value or {}).items() if k in REVIEW_KINDS
    }
    return ReviewUnits(rows)


def review_problems(value) -> list[str]:
    """スキーマを通った `reviews` の値の、実装の差分の覆い（I4）と重なり（I12）と checks・diff の食い違いの理由の文。"""
    units = reviews_of(value)
    out = []
    for kind, row in units.declared.items():
        if row.diff == "none" and row.checks:
            out.append(f"{REVIEWS_KEY}.{kind}: diff が none なのに checks がある")
        elif row.diff != "none" and not row.checks:
            out.append(f"{REVIEWS_KEY}.{kind}: diff が {row.diff} なのに checks が空")
    if out:
        return out
    for b in (True, False):
        if not any(units.row(k, b).covers_impl() for k in COVERING):
            out.append(
                f"{REVIEWS_KEY}: 課題のまとまりの記録が{'有る' if b else '無い'}とき、実装の差分がどの PR でも cross-review されない"
            )
    written = [k for k, r in units.declared.items() if r.covers_impl()]
    for kind in COVERING:
        if written and kind not in units.declared and any(REVIEW_DEFAULTS[kind][b].covers_impl() for b in (True, False)):
            out.append(f"{REVIEWS_KEY}.{kind}: 既定値の {kind} が、書いた {', '.join(written)} と同じ実装の差分を cross-review する")
    return out
