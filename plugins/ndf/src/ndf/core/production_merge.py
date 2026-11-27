"""マージ先が自動反映の本番チャネルかの判定の部品（#4409 の決定 2）。

hook（`domains.hooks.strict`）と `ndf ci merge` が同じ規則で判定するために、規則をここだけに置く。
hook は環境が無いときシステムの python3 で動くため、標準ライブラリと標準ライブラリだけで書いた `ndf.identity` だけを読む。
値の意味と呼ぶ側の扱いは #4409 の設計の表「判定の部品が返す値」にある。
"""

from __future__ import annotations

import json
from enum import Enum
from collections.abc import Mapping
from typing import Any, Optional

from ndf import contracts, identity
from ndf.contracts import internal
from ndf.contracts.worker_kinds import DELIVERY

# 判定に読むマージ先の設定のファイル（リポジトリの根からのパス）
PROJECT_FILE = identity.FIXED_WORDS["ndf_settings_dir"] + "/project.json"


class Verdict(str, Enum):
    """マージ先と設定から決まる判定の値。"""

    # マージ先が本番チャネル（`branches.production`）でない
    NOT_PRODUCTION = "not_production"
    # 本番チャネルで、マージした時点で利用者へ届く行がある
    AUTOMATIC = "automatic"
    # 本番チャネルで、届け先の行が手動だけか空（別のリリースの手順で届ける）
    MANUAL = "manual"
    # 本番チャネルで、`deliveries` が無く `cd` が null（ブランチ戦略のプリセットの形）
    UNSET = "unset"
    # 設定を読めない・形が違う
    UNKNOWN = "unknown"


# 承認の証跡が無いときの `ndf ci merge --production-gate` の終え方。stop は承認待ち、defer はマージせずに ok、stale は
# approval_stale（工程が組むプランの値 `progress.invoke.PRODUCTION_GATE` も同じ語を使う）
STOP, DEFER, STALE = "stop", "defer", "stale"
GATES = (STOP, DEFER, STALE)

# 承認が要る値（`ndf ci merge` の扱い。承認資料の「本番へ反映され得る」も同じ集合）
_NEEDS_APPROVAL = frozenset({Verdict.AUTOMATIC, Verdict.UNSET, Verdict.UNKNOWN})


def _production_row(row: object) -> bool:
    """本番に届く行か。`channel` の省略・`null` はどのチャネルにも当たる（`forms_decide.deliveries_of` と同じ）。"""
    return isinstance(row, dict) and row.get("channel") in (None, "production")


def _automatic_row(row: object) -> bool:
    """本番に適用される自動反映の行か。"""
    return _production_row(row) and isinstance(row, dict) and row.get("kind") != "manual"


def production_delivery(section: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """ブランチ戦略の設定 `section` の `release.deliveries` のうち、本番に届く行が 1 つで工程の文脈の `delivery` の形に合えば
    その行（#4513）。ほかは None。

    文脈の形は文脈のスキーマ全体へ `{"delivery": 行}` を渡して確かめる（`$defs/delivery` だけを引くと、`branch` の参照が
    ルートの `properties` を指すので有効な行が参照の誤りで落ちる）。
    """
    release = section.get("release")
    rows = release.get("deliveries") if isinstance(release, Mapping) else None
    found = [r for r in rows if _production_row(r)] if isinstance(rows, list) else []
    if len(found) != 1:
        return None
    row = dict(found[0])
    return None if contracts.schema_errors({DELIVERY: row}, internal.CONTEXT) else row


def automatic_rows(section: object) -> Verdict:
    """`branch_strategy` のセクションの `release` だけを見た値（マージ先を見ない。hook が使う）。例外を投げない。"""
    release = section.get("release") if isinstance(section, dict) else None
    if not isinstance(release, dict):
        return Verdict.UNKNOWN
    if "deliveries" in release:
        rows = release["deliveries"]
        if not isinstance(rows, list):
            return Verdict.UNKNOWN
        return Verdict.AUTOMATIC if any(_automatic_row(r) for r in rows) else Verdict.MANUAL
    if "cd" not in release:
        return Verdict.UNKNOWN
    cd = release["cd"]
    if cd is None:
        return Verdict.UNSET
    return Verdict.AUTOMATIC if isinstance(cd, dict) else Verdict.UNKNOWN


def judge(section: object, base: str) -> Verdict:
    """マージ先 `base` の判定。`branches.production` と違えば `NOT_PRODUCTION`、同じなら `automatic_rows` の値。"""
    branches = section.get("branches") if isinstance(section, dict) else None
    production = branches.get("production") if isinstance(branches, dict) else None
    if not isinstance(production, str) or not production:
        return Verdict.UNKNOWN
    if base != production:
        return Verdict.NOT_PRODUCTION
    return automatic_rows(section)


def judge_project_text(text: Optional[str], base: str, *, source_repo: str, repo: str) -> Verdict:
    """マージ先のブランチの設定のファイルの本文 `text`（無ければ None）での `judge`。読めなければ `UNKNOWN`。

    本文を読んだリポジトリ `source_repo` が対象のリポジトリ `repo` と違うときも `UNKNOWN`（大文字小文字は区別しない）。
    """
    if source_repo.lower() != repo.lower() or text is None:
        return Verdict.UNKNOWN
    try:
        data = json.loads(text)
    except ValueError:
        return Verdict.UNKNOWN
    return judge(data.get("branch_strategy") if isinstance(data, dict) else None, base)


def needs_approval(v: Verdict) -> bool:
    """`ndf ci merge` がマージの前に承認の証跡を求めるか。"""
    return v in _NEEDS_APPROVAL


def reaches_production(v: Verdict) -> bool:
    """承認資料の行と証跡の「本番へ反映されるか」の値（`needs_approval` と同じ集合）。"""
    return needs_approval(v)
