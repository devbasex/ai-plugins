"""ブランチ運用の宣言（`.ndf/worktree.json` の `branch_flow`。#3121）の読み分け・既定値・取り込み方の表・スナップショット。

宣言は 4 項目（スプリントの起点 `start`・ベースブランチへ入れる時点 `integrate`・文書の Pull Request の宛先 `doc_prs`・
本番への取り込み方 `promotion`）を持つ。ブランチ名は写さず、`base_branch`・`production_branch` をチャネル（`base` /
`production`）で指す（決定 2）。キーが無ければ既定値の行（`DEFAULT`）で、変更前の振る舞いと同じ値を出す（I2）。

取り込み方は表 `PROMOTIONS` の行で、範囲の決め方・組み直しの判定・リリースブランチの組み方・スプリントブランチを残すかを
行が持つ（決定 3）。呼ぶ側は行の属性とメソッドだけを読み、取り込み方の名前で分岐しない。取り込み方を足すときは、この表に
行を 1 つ足す。

特定のブランチ名（既定ブランチの候補・測る候補）は、このモジュールの定数だけに置く（受け入れ条件 7）。

レビューの単位（`branch_flow.reviews`。#3163）は、PR の種類ごとに掛けるレビュー（`checks`）と対象の差分（`diff`）を持つ。
許す値は `REVIEW_ALLOWED`、書いていない種類の値は `REVIEW_DEFAULTS`（課題のまとまりの記録の有無で 2 列）の 1 か所に置き、
スキーマ・読み分け・測定はそこから作る。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field

DECL_KEY = "branch_flow"
SNAPSHOT_KEY = "ブランチ運用"
# 既定ブランチを origin の HEAD から決められないときに探すローカルのブランチ（`repo.default_branch`）
DEFAULT_BRANCH_CANDIDATES = ("main", "master")
# 測定（`project-decl.py measure`）と既存の宣言の指紋が候補にするブランチ名
MEASURE_CANDIDATES = ("main", "master", "develop")
# スプリントブランチとリリースブランチの頭（名前の規則は NDF が決める）
SPRINT_PREFIX = "sprint/"
RELEASE_PREFIX = "release/v"

# リリースブランチを組むマージの件名（`build_release_branch`）。範囲の PR（`release_lib/included.py`）が単位の中の PR を拾う目印
UNIT_MERGE = re.compile(rf"^Merge (?:origin/)?{re.escape(SPRINT_PREFIX)}\S+ into {re.escape(RELEASE_PREFIX)}\S+$")


def unit_merge_subject(unit: str, branch: str) -> str:
    return f"Merge {unit} into {branch}"


CHANNELS = ("base", "production")
DOC_TARGETS = ("base", "sprint")
# ベースブランチへ入れる時点 → その時点の検査の Pull Request の宛先のチャネル
INTEGRATE_POINTS = {"check": "base"}


class FlowError(ValueError):
    """宣言の形の誤り・取り込み方の表に合わない組（I1）。`items` は直すキーと許す値の並び。理由（`because`）があれば添える。"""

    def __init__(self, items: list[dict]):
        self.items = items
        super().__init__(
            "\n".join(
                f"{DECL_KEY}.{i['key']} = {i['value']!r}（許す値: {', '.join(i['allowed'])}）"
                + (f"（理由: {i['because']}）" if i.get("because") else "")
                for i in items
            )
            + f"\n— .ndf/worktree.json の {DECL_KEY} を直す"
        )


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

        丸ごと出す行では使わない（ValueError）。衝突すれば取り消して ReleaseBuildError（E10）。同じ衝突の解き方は rerere で再利用し、
        rerere が衝突をすべて解いたマージはそのままコミットして進む。組むのは detached HEAD で、ローカルの `branch` を作らず
        動かさない（本番プランの worktree が `branch` を使っていても組み直せる）。送るのは呼び手が HEAD を送る。"""
        if not self.selects_units:
            raise ValueError(f"取り込み方 {self.name} はリリースの単位を選ばない")
        if not units:
            raise ReleaseBuildError("選んだリリースの単位が無い")
        p = git_run(root, "checkout", "-q", "--detach", production_ref)
        if p.returncode != 0:
            raise ReleaseBuildError(f"{production_ref} から {branch} を切れない: {p.stderr.strip()}")
        for u in units:
            if ancestor_of(root, u, "HEAD"):
                continue
            p = git_run(
                root,
                "-c",
                "rerere.enabled=true",
                "-c",
                "rerere.autoupdate=true",
                "merge",
                "--no-ff",
                "--no-edit",
                "-m",
                unit_merge_subject(u, branch),
                u,
            )
            if p.returncode != 0 and not commit_resolved_merge(root, unit_merge_subject(u, branch)):
                git_run(root, "merge", "--abort")
                raise ReleaseBuildError(f"{u} を {branch} へマージできない（衝突は人が解く）: {(p.stdout + p.stderr).strip()[-400:]}")
        return git_run(root, "rev-parse", "HEAD").stdout.strip()


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


def _ordered_row(kind: str, checks: tuple, diff: str) -> ReviewRow:
    return ReviewRow(kind, tuple(c for c in CHECKS if c in checks), diff)


# 種類 → 許す checks・diff と、取り込み方がリリースの単位を選ぶときだけ書けるか（I1・I3）
REVIEW_ALLOWED: dict[str, dict] = {
    "impl": {"checks": CHECKS, "diff": ("pr", "none"), "needs_units": False},
    "sprint": {"checks": CHECKS, "diff": ("unchecked", "none"), "needs_units": False},
    "release": {"checks": ("cross-review",), "diff": ("pr", "resolution", "none"), "needs_units": True},
    "design": {"checks": ("cross-review",), "diff": ("pr", "none"), "needs_units": False},
    "spec": {"checks": ("cross-review",), "diff": ("pr", "none"), "needs_units": False},
}
# 書いていない種類の値。キーは課題のまとまりの記録が有るか（I5。ndf v10.27.61 の決め打ちと同じ）
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


def review_allowed() -> dict[str, dict]:
    """種類 → 許す `checks`・`diff` と書ける条件（呼ぶ側が書き換えても表は変わらない）。"""
    return {k: dict(v) for k, v in REVIEW_ALLOWED.items()}


@dataclass(frozen=True)
class ReviewUnits:
    """宣言に書いた種類の行だけを持つ。書いていない種類は `row` が既定値の表から解く（決定 3）。"""

    declared: dict = field(default_factory=dict)

    def row(self, kind: str, bundled: bool) -> ReviewRow:
        return self.declared.get(kind) or REVIEW_DEFAULTS[kind][bundled]

    def uncovered(self) -> list[dict]:
        """まとまりの記録の有無の各場合で、実装の差分を cross-review で覆う行が無ければ指摘の行（I4）。"""
        want = [f"{k} に cross-review / {d}" for k, d in COVERING.items()]
        return [
            _flow_item(
                REVIEWS_KEY,
                self.as_dict(),
                want,
                f"課題のまとまりの記録が{'有る' if b else '無い'}とき、実装の差分がどの PR でも cross-review されない",
            )
            for b in (True, False)
            if not any(self.row(k, b).covers_impl() for k in COVERING)
        ]

    def overlaps(self) -> list[dict]:
        """書いた行と書いていない種類の既定値の行が、同じ場合に実装の差分を覆えば指摘の行（I12）。"""
        written = [k for k, r in self.declared.items() if r.covers_impl()]
        return [
            _flow_item(
                f"{REVIEWS_KEY}.{kind}",
                None,
                [f"{kind} の行を書き、cross-review を掛けるかを決める"],
                f"既定値の {kind} が、書いた {', '.join(written)} と同じ実装の差分を cross-review する",
            )
            for kind in COVERING
            if written and kind not in self.declared and any(REVIEW_DEFAULTS[kind][b].covers_impl() for b in (True, False))
        ]

    def as_dict(self) -> dict:
        return {k: self.declared[k].as_dict() for k in REVIEW_KINDS if k in self.declared}


def _flow_item(key: str, value, allowed: list, because: str | None = None) -> dict:
    """直すキー・値・許す値（・理由）の指摘の行（`FlowError.items` の形）。"""
    return {"key": key, "value": value, "allowed": allowed, **({"because": because} if because else {})}


def _review_row_items(kind: str, value) -> tuple[ReviewRow | None, list[dict]]:
    """種類 1 行の形を読む（I1）。"""
    key, allowed = f"{REVIEWS_KEY}.{kind}", REVIEW_ALLOWED[kind]
    if not isinstance(value, dict):
        return None, [_flow_item(key, value, ["checks と diff のオブジェクト"])]
    items = [_flow_item(f"{key}.{k}", v, ["(このキーは無い)", "checks", "diff"]) for k, v in value.items() if k not in ("checks", "diff")]
    checks, diff = value.get("checks"), value.get("diff")
    if not (
        isinstance(checks, list) and all(isinstance(c, str) and c in allowed["checks"] for c in checks) and len(set(checks)) == len(checks)
    ):
        items.append(_flow_item(f"{key}.checks", checks, list(allowed["checks"])))
    if not isinstance(diff, str) or diff not in allowed["diff"]:
        items.append(_flow_item(f"{key}.diff", diff, list(allowed["diff"])))
    return (None, items) if items else (_ordered_row(kind, tuple(checks), diff), [])


def _combination_items(kind: str, row: ReviewRow, promotion: Promotion | None) -> list[dict]:
    """形を通った行の組み合わせの誤り（I2・I3）。"""
    key, items = f"{REVIEWS_KEY}.{kind}", []
    if row.diff == "none" and row.checks:
        items.append(_flow_item(f"{key}.checks", list(row.checks), ["[]"], "diff = none"))
    elif row.diff != "none" and not row.checks:
        items.append(_flow_item(f"{key}.checks", [], list(REVIEW_ALLOWED[kind]["checks"]), f"diff = {row.diff}"))
    if promotion is not None and REVIEW_ALLOWED[kind]["needs_units"] and not promotion.selects_units:
        items.append(_flow_item(key, row.as_dict(), ["(書かない)"], f"promotion = {promotion.name}"))
    return items


def parse_reviews(value, promotion: Promotion | None) -> tuple[ReviewUnits, list[dict]]:
    """`reviews` の値を読み、形と組み合わせ（I1〜I4・I12）の指摘の行と一緒に返す。指摘があれば `ReviewUnits()`。

    `promotion` が None（取り込み方の値が誤り）なら、取り込み方との組み合わせ（I3）を見ない。形の誤りがあれば組み合わせを、
    組み合わせの誤りがあれば I4・I12 を見ない（誤った行で覆いを数えない）。"""
    value = {} if value is None else value
    if not isinstance(value, dict):
        return ReviewUnits(), [_flow_item(REVIEWS_KEY, value, ["PR の種類をキーにしたオブジェクト"])]
    items = [_flow_item(f"{REVIEWS_KEY}.{k}", v, ["(この種類は無い)", *REVIEW_KINDS]) for k, v in value.items() if k not in REVIEW_KINDS]
    rows = {}
    for kind in (k for k in REVIEW_KINDS if k in value):
        row, bad = _review_row_items(kind, value[kind])
        items += bad
        rows.update({kind: row} if row else {})
    items = items or [i for kind, row in rows.items() for i in _combination_items(kind, row, promotion)]
    units = ReviewUnits(rows)
    items = items or units.uncovered() + units.overlaps()
    return (ReviewUnits(), items) if items else (units, [])


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
    reviews: ReviewUnits = field(default_factory=ReviewUnits)  # レビューの単位（#3163）。KEYS とは別に持つ

    @property
    def row(self) -> Promotion:
        return PROMOTIONS[self.promotion]

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in KEYS}

    def resolve(self, base: str | None, production: str | None) -> "ResolvedFlow":
        start_branch = production if self.start == "production" else base
        return ResolvedFlow(self, start_branch or base, base, production)


def parse_flow(value) -> BranchFlow:
    """`branch_flow` の値を読む。`None` は既定値の行。形の誤り・表に合わない組は FlowError（既定値へ倒さない。I1）。"""
    if value is None:
        return BranchFlow()
    allowed = allowed_values()
    if not isinstance(value, dict):
        raise FlowError([{"key": "(全体)", "value": value, "allowed": ["4 項目のオブジェクト"]}])
    items = [
        {"key": k, "value": v, "allowed": ["(このキーは無い)", *KEYS, REVIEWS_KEY]}
        for k, v in value.items()
        if k not in allowed and k != REVIEWS_KEY
    ]
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
    promotion_ok = not any(i["key"] == "promotion" for i in items)
    units, bad = parse_reviews(value.get(REVIEWS_KEY), PROMOTIONS[vals["promotion"]] if promotion_ok else None)
    items += bad
    if items:
        raise FlowError(items)
    return BranchFlow(**vals, declared=True, reviews=units)


def of_decl(wt: dict) -> BranchFlow:
    """`.ndf/worktree.json` の中身から読む。"""
    return parse_flow((wt or {}).get(DECL_KEY))


def read_flow(root) -> BranchFlow:
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
            REVIEWS_KEY: self.flow.reviews.as_dict(),
        }


def from_snapshot(snap: dict | None, base: str | None, production: str | None) -> ResolvedFlow:
    """目録のスナップショットから読む。無ければ（変更前の目録）宣言の無いリポジトリとして読む（I3）。
    `reviews` の無いスナップショット（#3163 より前に始めたスプリント）は `{}`（すべての種類が既定値）として読む（I6）。"""
    if not snap:
        return BranchFlow().resolve(base, production)
    flow = parse_flow({**{k: snap[k] for k in KEYS if k in snap}, REVIEWS_KEY: snap.get(REVIEWS_KEY) or {}})
    return ResolvedFlow(
        flow, snap.get("start_branch") or base, snap.get("base_branch") or base, snap.get("production_branch") or production
    )


def sprint_branch_kept(root, branch_ref: str, production_ref: str, row: Promotion) -> bool:
    """後片付けでスプリントブランチを残すか。残す行では、本番チャネルに含まれるまで残す（決定 7）。"""
    return row.keeps_sprint_branch and not ancestor_of(root, branch_ref, production_ref)


def schema_fragment() -> dict:
    """`worktree.schema.json` の `branch_flow` の定義（表から作る）。"""
    allowed = allowed_values()
    reviews = {
        "type": "object",
        "description": "レビューの単位（#3163）。PR の種類ごとに掛けるレビューと対象の差分。書いていない種類は既定値",
        "additionalProperties": False,
        "properties": {
            kind: {
                "type": "object",
                "additionalProperties": False,
                "required": ["checks", "diff"],
                "properties": {
                    "checks": {"type": "array", "uniqueItems": True, "items": {"type": "string", "enum": list(row["checks"])}},
                    "diff": {"type": "string", "enum": list(row["diff"])},
                },
            }
            for kind, row in REVIEW_ALLOWED.items()
        },
    }
    return {
        "type": "object",
        "description": "ブランチ運用の宣言（#3121）。無ければ既定値の行（ベースブランチから切り、ベースブランチを丸ごと出す）",
        "additionalProperties": False,
        "properties": {
            **{k: {"type": "string", "enum": list(v), "default": DEFAULT[k]} for k, v in allowed.items()},
            REVIEWS_KEY: reviews,
        },
    }
