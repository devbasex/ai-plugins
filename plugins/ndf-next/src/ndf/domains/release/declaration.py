"""リリースの宣言（`.ndf/release.json`）を読み、宣言の手順を流す。

置き場と名前は事実の表（`ndf-files` の `release` と `places` の `ndf`）から引き、写さない。宣言が無ければ
既定の値（手順なし・テストをリリース対象に数えない・CHANGELOG は根の `CHANGELOG.md`）で進める。

| 鍵 | 意味 | 既定 |
| --- | --- | --- |
| `steps` | リリースの段階で流すコマンド（`name`・`stage`・`command`・`writes`・`timeout_seconds`）。`command` の `{version}` は出す版に置き換える | 無し |
| `undistributed` | リリース対象に関わらないファイルの形（プラグインの根からの gitignore の形） | テスト（`changes_decide.DEFAULT_UNDISTRIBUTED`） |
| `bump_undistributed` | リリース対象に関わらない変化だけのプラグインも版を上げるか（#2217） | 偽 |
| `version_files` | 版上げで、プラグインの名前を含む行の版を書き換えるファイル | 無し |
| `changelog` | CHANGELOG のファイル（リポジトリの根から） | `CHANGELOG.md` |

トークン消費の記録のような、リポジトリ固有のリリースの作業は宣言の手順に書く（NDF は中身を知らない）。

ほかの宣言から読むもの（#3572）:

| 関数 | 宣言 | 読むもの |
| --- | --- | --- |
| `flow_of`・`require_github_flow`・`review_problems` | `.ndf/worktree.json` の `branch_flow` | 取り込み方の表（`branch-flows`）の行。ndf-next のリリースは行 `github-flow` だけを扱う |
| `verify_of` | `.ndf/project.json` の `delivery` の検証の行（`channel: verify`） | 検証の ref の名前と 2 つ目のマーケットプレイスの名前 |
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple

from ndf import identity
from ndf.adapters import git as git_mod
from ndf.contracts import facts
from ndf.domains.release import changes_decide

# 読める宣言の版
READABLE_VERSION = 1
# 宣言の手順の段階（本番の組み立て・開発版の組み立て・リリース後テスト）
STAGES = ("production", "dev", "verify")
# 手順の時間の既定（秒）
DEFAULT_TIMEOUT = 600
# 手順のコマンドで出す版に置き換える語
VERSION_PLACEHOLDER = "{version}"
# プロジェクトの宣言（迂回の宣言 `production_merge.admin` を持つ。#3506）
PROJECT_FILE = Path(identity.FIXED_WORDS["declaration_dir"]) / "project.json"


class DeclarationError(Exception):
    """宣言が読めない・版が違う・値の形が違う。"""


class StepFailed(Exception):
    """宣言の手順が失敗した（終了コードが 0 でない・時間切れ・起動できない）。"""


@dataclass(frozen=True)
class Step:
    name: str
    stage: str
    command: tuple[str, ...]
    writes: tuple[str, ...] = ()
    timeout: float = DEFAULT_TIMEOUT


@dataclass(frozen=True)
class Declaration:
    steps: tuple[Step, ...] = ()
    undistributed: tuple[str, ...] = changes_decide.DEFAULT_UNDISTRIBUTED
    bump_undistributed: bool = False
    version_files: tuple[str, ...] = ()
    changelog: str = "CHANGELOG.md"
    raw: dict = field(default_factory=dict, compare=False, repr=False)

    def for_stage(self, stage: str) -> list[Step]:
        return [s for s in self.steps if s.stage == stage]


def path_of(root: Path) -> Path:
    place = facts.value("ndf-files", "release", "place")
    return root / facts.value("places", place, "path") / facts.value("ndf-files", "release", "path")


def _value(raw: dict, key: str, default: object) -> object:
    """鍵があれば値、無ければ `default`。"""
    return raw[key] if key in raw else default


def _strings(raw: dict, key: str, default: Sequence[str]) -> tuple[str, ...]:
    value = _value(raw, key, list(default))
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise DeclarationError(f"{key} は文字列の配列")
    return tuple(value)


def _step(raw: object) -> Step:
    if not isinstance(raw, dict):
        raise DeclarationError(f"steps の行がオブジェクトでない: {raw!r}")
    name, stage, command = raw.get("name"), raw.get("stage"), raw.get("command")
    if not isinstance(name, str) or stage not in STAGES:
        raise DeclarationError(f"steps の行の name か stage（{', '.join(STAGES)}）が違う: {raw!r}")
    if not isinstance(command, list) or not command or not all(isinstance(c, str) for c in command):
        raise DeclarationError(f"steps の {name} の command は空でない文字列の配列")
    timeout = _value(raw, "timeout_seconds", DEFAULT_TIMEOUT)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise DeclarationError(f"steps の {name} の timeout_seconds は正の数")
    return Step(name, stage, tuple(command), _strings(raw, "writes", ()), float(timeout))


def _parse(raw: dict) -> Declaration:
    if raw.get("version") != READABLE_VERSION:
        raise DeclarationError(f"宣言の version が {READABLE_VERSION} でない: {raw.get('version')!r}")
    steps = _value(raw, "steps", [])
    if not isinstance(steps, list):
        raise DeclarationError("steps は配列")
    flag = _value(raw, "bump_undistributed", False)
    changelog = _value(raw, "changelog", Declaration.changelog)
    if not isinstance(flag, bool) or not isinstance(changelog, str):
        raise DeclarationError("bump_undistributed は真偽、changelog は文字列")
    return Declaration(
        tuple(_step(s) for s in steps),
        _strings(raw, "undistributed", changes_decide.DEFAULT_UNDISTRIBUTED),
        flag,
        _strings(raw, "version_files", ()),
        changelog,
        raw,
    )


def load(root: Path) -> Declaration:
    """宣言を読む。無ければ既定の宣言。"""
    path = path_of(root)
    if not path.is_file():
        return Declaration()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeclarationError(f"{path} を読めない: {exc}") from None
    if not isinstance(raw, dict):
        raise DeclarationError(f"{path} が JSON のオブジェクトでない")
    return _parse(raw)


def run_step(root: Path, step: Step, version: str) -> None:
    """宣言の手順を 1 つ流す。失敗は `StepFailed`（標準エラーの末尾を添える）。"""
    argv = [part.replace(VERSION_PLACEHOLDER, version) for part in step.command]
    try:
        done = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=step.timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StepFailed(f"手順 {step.name} を流せない: {exc}") from None
    if done.returncode != 0:
        raise StepFailed(f"手順 {step.name} が終了コード {done.returncode} で失敗: {done.stderr.strip()[-500:]}")


def _admin_declaration(root: Path) -> Path:
    """迂回の宣言を読むファイル（契約の `production_merge` の置き場は `main`）。

    メインディレクトリが解ければそこのファイルだけを読み、無くても呼ばれた木の値で補わない。PR の作業場所が
    足した宣言だけで迂回を選べないようにする（旧いプラグインの `decl.overlay_keys` と同じ扱い。#2363・#3506 の I1）。
    git の外ならメインディレクトリを持たないので呼ばれた木。
    """
    try:
        main = git_mod.Git(root).common_dir().parent
    except git_mod.GitError:  # git の外はメインディレクトリが無いので呼ばれた木の宣言を読む
        return root / PROJECT_FILE
    return main / PROJECT_FILE


def production_admin(root: Path) -> bool:
    """迂回の宣言（`.ndf/project.json` の `production_merge.admin`）が JSON の true か（#3506 の I1）。

    宣言はメインディレクトリから読む（`_admin_declaration`）。ファイルが無い・読めない・壊れている・値の型が違う・
    `unknown` は偽（迂回しない）。
    """
    try:
        raw = json.loads(_admin_declaration(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 読めない宣言は宣言なしとして迂回しない（#3506 の I1）
        return False
    got = raw.get("production_merge") if isinstance(raw, dict) else None
    return isinstance(got, dict) and got.get("admin") is True


# ブランチの運用の宣言と、本番ブランチの名前の鍵（#3572。`ndf.core.route.default_base` と同じ宣言の 1 鍵を読む）
BRANCH_FILE = Path(identity.FIXED_WORDS["declaration_dir"]) / "worktree.json"
PRODUCTION_KEY = "production_branch"


def production_branch(root: Path) -> str:
    """宣言（`.ndf/worktree.json`）の本番ブランチの名前。無い・読めない・空・前後に空白があれば `DeclarationError`。"""
    path = root / BRANCH_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeclarationError(f"{path} を読めない（{PRODUCTION_KEY} を宣言するか --production を渡す）: {exc}") from None
    got = raw[PRODUCTION_KEY] if isinstance(raw, dict) and PRODUCTION_KEY in raw else None
    if not isinstance(got, str) or not got or got != got.strip():
        raise DeclarationError(f"{path} の {PRODUCTION_KEY} が名前でない（宣言するか --production を渡す）: {got!r}")
    return got


# ndf-next のリリースが扱う取り込み方の表の行（スプリントの PR を本番ブランチへ直接マージする行。#3572 の決定 5）
GITHUB_FLOW = facts.ids("branch-flows", promotion="direct")[0]
FLOW_CHOICES = ("start", "doc_prs", "integrate")
# 実装の差分を cross-review するレビューの単位の種類（#3119 の I2）
CODE_REVIEW_KINDS = ("impl", "sprint")


class VerifyDecl(NamedTuple):
    """検証の ref の名前と、それを指す 2 つ目のマーケットプレイスの名前。"""

    ref: str
    marketplace: str


def _branch_flow(root: Path) -> dict:
    """宣言 `.ndf/worktree.json` の `branch_flow`（無ければ空）。読めなければ `DeclarationError`。"""
    path = root / BRANCH_FILE
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeclarationError(f"{path} を読めない: {exc}") from None
    got = raw.get("branch_flow") if isinstance(raw, dict) else None
    if got is not None and not isinstance(got, dict):
        raise DeclarationError(f"{path} の branch_flow がオブジェクトでない")
    return got if got is not None else {}


def _flow_row(flow: dict) -> dict:
    """宣言の `branch_flow` が選ぶ取り込み方の表の行。表に無いか、行が許さない値を選べば `DeclarationError`（#3119 の I1）。"""
    table = "branch-flows"
    rows = facts.rows(table, promotion=flow["promotion"]) if "promotion" in flow else facts.rows(table, default=True)
    if not rows:
        raise DeclarationError(f"branch_flow.promotion {flow.get('promotion')!r} が取り込み方の表 {table} に無い")
    row = rows[0]
    bad = [f"{c}={flow[c]!r}" for c in FLOW_CHOICES if c in flow and flow[c] not in row[c]]
    if bad:
        raise DeclarationError(f"取り込み方 {row['id']} が許さない branch_flow の値: {', '.join(bad)}")
    return row


def flow_of(root: Path) -> str:
    """宣言の取り込み方の表の行の id（`branch_flow` が無ければ既定の行）。"""
    return str(_flow_row(_branch_flow(root))["id"])


def require_github_flow(root: Path) -> None:
    """取り込み方の行が `github-flow` でなければ `DeclarationError` で止める（既定の行へ倒さない。決定 5）。"""
    found = flow_of(root)
    if found != GITHUB_FLOW:
        raise DeclarationError(f"取り込み方 {found} は ndf-next のリリースで扱わない（{GITHUB_FLOW} の宣言だけを扱う）")


def _reviews_cross(unit: object) -> bool:
    """レビューの単位の行が実装の差分を cross-review するか。"""
    if not isinstance(unit, dict):
        return False
    checks = unit.get("checks")
    return isinstance(checks, list) and "cross-review" in checks and unit.get("diff") != "none"


def review_problems(root: Path) -> list[dict[str, str]]:
    """取り込み方の行が `github-flow` の宣言で、`branch_flow.reviews` の破れを 1 件ずつ返す（#3119 の I2）。

    書ける種類は表の行の `review_kinds` から読む。書いていない種類は既定値に任せて数えない。
    """
    flow = _branch_flow(root)
    row = _flow_row(flow)
    if row["id"] != GITHUB_FLOW:
        return []
    reviews = flow.get("reviews")
    reviews = reviews if isinstance(reviews, dict) else {}
    out = [
        {"kind": "reviews", "name": kind, "detail": f"取り込み方 {GITHUB_FLOW} では {kind} のレビューの単位を書けない"}
        for kind in reviews
        if kind not in row["review_kinds"]
    ]
    code = [k for k in CODE_REVIEW_KINDS if k in reviews]
    if len(code) == len(CODE_REVIEW_KINDS) and not any(_reviews_cross(reviews[k]) for k in code):
        detail = f"{'・'.join(CODE_REVIEW_KINDS)} のどちらも実装の差分を cross-review しない"
        out.append({"kind": "reviews", "name": "cross-review", "detail": detail})
    return out


def verify_of(root: Path) -> VerifyDecl:
    """`.ndf/project.json` の `delivery` の検証の行（`channel: verify`）の ref とマーケットプレイスの名前。

    宣言はメインディレクトリから読む（`_admin_declaration` と同じ。PR の作業場所が足した宣言で ref を選べない）。
    行が無い・2 つ以上・値が空なら `DeclarationError`（既定値を持たない）。
    """
    path = _admin_declaration(root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DeclarationError(f"{path} を読めない: {exc}") from None
    rows = raw.get("delivery") if isinstance(raw, dict) else None
    channel = "verify"
    found = [r for r in rows if isinstance(r, dict) and r.get("channel") == channel] if isinstance(rows, list) else []
    if len(found) != 1:
        raise DeclarationError(f"{path} の delivery に channel {channel} の行が 1 つでない（{len(found)} 行）")
    verify = found[0].get("verify")
    values = [verify.get(k) if isinstance(verify, dict) else None for k in ("ref", "marketplace")]
    if not all(isinstance(v, str) and v.strip() == v and v for v in values):
        raise DeclarationError(f"{path} の検証の行の verify.ref と verify.marketplace が空でない名前でない: {verify!r}")
    return VerifyDecl(str(values[0]), str(values[1]))


def optional_verify_ref(root: Path) -> str | None:
    """宣言の検証の ref の名前（検証の行が無ければ None。検証の ref の判定を飛ばす）。"""
    try:
        return verify_of(root).ref
    except DeclarationError:  # noqa: NDF401 ファイル: 検証の行の無いリポジトリは検証の ref を持たない
        return None
