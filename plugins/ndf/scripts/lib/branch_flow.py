"""ブランチ運用の宣言（`.ndf/worktree.json` の `branch_flow`。#3121）の読み分け・既定値・取り込み方の表・スナップショット。

宣言は 4 項目（スプリントの起点 `start`・ベースブランチへ入れる時点 `integrate`・文書の Pull Request の宛先 `doc_prs`・
本番への取り込み方 `promotion`）を持つ。ブランチ名は写さず、`base_branch`・`production_branch` をチャネル（`base` /
`production`）で指す（決定 2）。キーが無ければ既定値の行（`DEFAULT`）で、変更前の振る舞いと同じ値を出す（I2）。

取り込み方は表 `PROMOTIONS` の行で、範囲の決め方・組み直しの判定・リリースブランチの組み方・スプリントブランチを残すかを
行が持つ（決定 3）。呼ぶ側は行の属性とメソッドだけを読み、取り込み方の名前で分岐しない。取り込み方を足すときは、この表に
行を 1 つ足す。

特定のブランチ名（既定ブランチの候補・測る候補）は、このモジュールの定数だけに置く（受け入れ条件 7）。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

DECL_KEY = "branch_flow"
SNAPSHOT_KEY = "ブランチ運用"
# 既定ブランチを origin の HEAD から決められないときに探すローカルのブランチ（`repo.default_branch`）
DEFAULT_BRANCH_CANDIDATES = ("main", "master")
# 測定（`project-decl.py measure`）と既存の宣言の指紋が候補にするブランチ名
MEASURE_CANDIDATES = ("main", "master", "develop")
# スプリントブランチとリリースブランチの頭（名前の規則は NDF が決める）
SPRINT_PREFIX = "sprint/"
RELEASE_PREFIX = "release/v"

CHANNELS = ("base", "production")
DOC_TARGETS = ("base", "sprint")
# ベースブランチへ入れる時点 → その時点の検査の Pull Request の宛先のチャネル
INTEGRATE_POINTS = {"check": "base"}


class FlowError(ValueError):
    """宣言の形の誤り・取り込み方の表に合わない組（I1）。`items` は直すキーと許す値の並び。"""

    def __init__(self, items: list[dict]):
        self.items = items
        super().__init__(
            "・".join(f"{DECL_KEY}.{i['key']} = {i['value']!r}（許す値: {', '.join(i['allowed'])}）" for i in items)
            + f" — .ndf/worktree.json の {DECL_KEY} を直す"
        )


def _git(root, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)


def _is_ancestor(root, a: str, b: str) -> bool:
    return _git(root, "merge-base", "--is-ancestor", a, b).returncode == 0


class ReleaseBuildError(RuntimeError):
    """リリースブランチを組めない（衝突・単位が無い）。止めて人へ戻す（E10）。"""


@dataclass(frozen=True)
class Promotion:
    """取り込み方の表の 1 行。"""

    name: str
    allowed_start: frozenset
    allowed_doc_prs: frozenset
    allowed_integrate: frozenset
    keeps_sprint_branch: bool  # 本番チャネルに含まれるまでスプリントブランチを残すか（決定 7）
    selects_units: bool  # リリースの単位を選んで本番チャネルから組むか
    # 組み直しの判定で比べる先頭（`base` = ベースブランチ、`units` = 選んだ単位、`production` = 本番チャネル）
    watched: tuple = field(default_factory=tuple)
    summary: str = ""

    def moved(self, presented: dict, now: dict) -> list[str]:
        """提示の時点の先頭（`presented`）から、答えの時点（`now`）で動いた先頭の名前。空なら組み直さない（I7）。

        どちらも `{"base": SHA, "production": SHA, "units": {ブランチ: SHA}}` の形。答えの時点の値が取れない先頭は比べない。"""
        out = []
        for k in self.watched:
            if k == "units":
                for name, sha in (presented.get("units") or {}).items():
                    cur = (now.get("units") or {}).get(name)
                    if cur and cur.strip() != str(sha).strip():
                        out.append(name)
            else:
                cur = now.get(k)
                if cur and cur.strip() != str(presented.get(k) or "").strip():
                    out.append(k)
        return out

    def release_range(self, production_ref: str, prev_tag: str | None, head: str) -> tuple[str, str]:
        """承認資料の範囲 `(元, 先)`。丸ごと出す行は本番チャネル..提示したコミット、選ぶ行は前の本番のタグ..リリースブランチの先頭。"""
        if self.selects_units:
            return (prev_tag or production_ref, head)
        return (production_ref, head)

    def release_from(self, flow: "ResolvedFlow") -> str:
        """リリースブランチを切る元のブランチ。"""
        return flow.production_branch if self.selects_units else flow.base_branch

    def build_release_branch(self, root, branch: str, production_ref: str, units: list[str]) -> str:
        """`production_ref` から `branch` を切り、`units` を `--no-ff` で順にマージして先頭の SHA を返す（I5）。

        丸ごと出す行では使わない（ValueError）。衝突すれば取り消して ReleaseBuildError（E10）。同じ衝突の解き方は rerere で再利用する。"""
        if not self.selects_units:
            raise ValueError(f"取り込み方 {self.name} はリリースの単位を選ばない")
        if not units:
            raise ReleaseBuildError("選んだリリースの単位が無い")
        p = _git(root, "checkout", "-q", "-B", branch, production_ref)
        if p.returncode != 0:
            raise ReleaseBuildError(f"{production_ref} から {branch} を切れない: {p.stderr.strip()}")
        for u in units:
            if _is_ancestor(root, u, "HEAD"):
                continue
            p = _git(root, "-c", "rerere.enabled=true", "-c", "rerere.autoupdate=true", "merge", "--no-ff", "--no-edit", "-m", f"Merge {u} into {branch}", u)
            if p.returncode != 0:
                _git(root, "merge", "--abort")
                raise ReleaseBuildError(f"{u} を {branch} へマージできない（衝突は人が解く）: {(p.stdout + p.stderr).strip()[-400:]}")
        return _git(root, "rev-parse", "HEAD").stdout.strip()


PROMOTIONS: dict[str, Promotion] = {
    "whole": Promotion(
        name="whole",
        allowed_start=frozenset({"base"}),
        allowed_doc_prs=frozenset({"base"}),
        allowed_integrate=frozenset({"check"}),
        keeps_sprint_branch=False,
        selects_units=False,
        watched=("base",),
        summary="ベースブランチを丸ごと本番チャネルへ出す",
    ),
    "selected": Promotion(
        name="selected",
        allowed_start=frozenset({"production"}),
        allowed_doc_prs=frozenset({"sprint"}),
        allowed_integrate=frozenset({"check"}),
        keeps_sprint_branch=True,
        selects_units=True,
        watched=("units", "production"),
        summary="本番チャネルから切ったリリースブランチへ、選んだスプリントブランチを集める",
    ),
}

DEFAULT = {"start": "base", "integrate": "check", "doc_prs": "base", "promotion": "whole"}
KEYS = tuple(DEFAULT)


def allowed_values() -> dict[str, tuple]:
    """キー → 許す値（スキーマの enum と測定の候補はこれから作る）。"""
    return {"start": CHANNELS, "integrate": tuple(INTEGRATE_POINTS), "doc_prs": DOC_TARGETS, "promotion": tuple(PROMOTIONS)}


@dataclass(frozen=True)
class BranchFlow:
    start: str = DEFAULT["start"]
    integrate: str = DEFAULT["integrate"]
    doc_prs: str = DEFAULT["doc_prs"]
    promotion: str = DEFAULT["promotion"]
    declared: bool = False  # 宣言に branch_flow があったか

    @property
    def row(self) -> Promotion:
        return PROMOTIONS[self.promotion]

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in KEYS}

    def resolve(self, base: str | None, production: str | None) -> "ResolvedFlow":
        start_branch = production if self.start == "production" else base
        return ResolvedFlow(self, start_branch or base, base, production)


def parse(value) -> BranchFlow:
    """`branch_flow` の値を読む。`None` は既定値の行。形の誤り・表に合わない組は FlowError（既定値へ倒さない。I1）。"""
    if value is None:
        return BranchFlow()
    allowed = allowed_values()
    if not isinstance(value, dict):
        raise FlowError([{"key": "(全体)", "value": value, "allowed": ["4 項目のオブジェクト"]}])
    items = [{"key": k, "value": v, "allowed": ["(このキーは無い)", *KEYS]} for k, v in value.items() if k not in allowed]
    vals = {**DEFAULT}
    for k, opts in allowed.items():
        if k in value:
            v = value[k]
            if not isinstance(v, str) or v not in opts:
                items.append({"key": k, "value": v, "allowed": list(opts)})
            else:
                vals[k] = v
    if not items:
        row = PROMOTIONS[vals["promotion"]]
        for k, ok in (("start", row.allowed_start), ("doc_prs", row.allowed_doc_prs), ("integrate", row.allowed_integrate)):
            if vals[k] not in ok:
                items.append({"key": k, "value": vals[k], "allowed": sorted(ok), "because": f"promotion = {row.name}"})
    if items:
        raise FlowError(items)
    return BranchFlow(**vals, declared=True)


def of_decl(wt: dict) -> BranchFlow:
    """`.ndf/worktree.json` の中身から読む。"""
    return parse((wt or {}).get(DECL_KEY))


def read(root) -> BranchFlow:
    """メインディレクトリの `.ndf/worktree.json`（契約の置き場 `main`）の `branch_flow`。読めない宣言は FlowError。"""
    import decl

    wt, why = decl.read_json_setting(root, "worktree.json")
    if why:
        raise FlowError([{"key": "(全体)", "value": why, "allowed": ["JSON のオブジェクト"]}])
    return of_decl(wt)


@dataclass(frozen=True)
class ResolvedFlow:
    """ブランチ名まで解いた宣言。スプリントの開始時にスナップショットとして目録へ書く（決定 4）。"""

    flow: BranchFlow
    start_branch: str | None
    base_branch: str | None
    production_branch: str | None

    @property
    def row(self) -> Promotion:
        return self.flow.row

    def doc_target(self, sprint_branch: str) -> str | None:
        """設計 PR と確定仕様化の PR の宛先。"""
        return sprint_branch if self.flow.doc_prs == "sprint" else self.base_branch

    def check_target(self) -> str | None:
        """検査の Pull Request の宛先（ベースブランチへ入れる時点の行）。"""
        return self.production_branch if INTEGRATE_POINTS[self.flow.integrate] == "production" else self.base_branch

    def as_snapshot(self, source: str) -> dict:
        return {
            **self.flow.as_dict(),
            "start_branch": self.start_branch,
            "base_branch": self.base_branch,
            "production_branch": self.production_branch,
            "source": source,
        }


def from_snapshot(snap: dict | None, base: str | None, production: str | None) -> ResolvedFlow:
    """目録のスナップショットから読む。無ければ（変更前の目録）宣言の無いリポジトリとして読む（I3）。"""
    if not snap:
        return BranchFlow().resolve(base, production)
    flow = parse({k: snap[k] for k in KEYS if k in snap})
    return ResolvedFlow(flow, snap.get("start_branch") or base, snap.get("base_branch") or base, snap.get("production_branch") or production)


def sprint_branch_kept(root, branch_ref: str, production_ref: str, row: Promotion) -> bool:
    """後片付けでスプリントブランチを残すか。残す行では、本番チャネルに含まれるまで残す（決定 7）。"""
    return row.keeps_sprint_branch and not _is_ancestor(root, branch_ref, production_ref)


def schema_fragment() -> dict:
    """`worktree.schema.json` の `branch_flow` の定義（表から作る）。"""
    allowed = allowed_values()
    return {
        "type": "object",
        "description": "ブランチ運用の宣言（#3121）。無ければ既定値の行（ベースブランチから切り、ベースブランチを丸ごと出す）",
        "additionalProperties": False,
        "properties": {k: {"type": "string", "enum": list(v), "default": DEFAULT[k]} for k, v in allowed.items()},
    }

