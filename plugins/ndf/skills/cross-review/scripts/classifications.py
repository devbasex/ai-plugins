"""cross-review の区分に関する共有定義（#156、#732）。

収束の判定（`state.py`）と効果の測定（`measure.py`）が同じ区分を数えるための
唯一の定義を置く。片方だけに区分を足すと、判定が数えた指摘を測定が採らず、
その方式の再現率が実際より低く出る（`test_measure.py` が両者の一致を固定する）。
"""
from __future__ import annotations

# 収束の判定が数える区分（#156、#732）。**残る 3 つは数えない。** 数えないのは、誤りだと
# 示された棄却と、承認を妨げない軽微な指摘だけである。棄却した指摘を数えると、そのぶん
# ラウンドが増える（#69 で同じ論点が 5 ラウンド続いた事象）。
COUNTED_CLASSIFICATIONS = ("verified_blocking", "needs_human_judgment", "unrefuted")


# --- Pull Request の分類ごとの収束の戦略（#1005） -----------------------------------
#
# 設計 PR と実装 PR は性質が逆向きなので、同じ収束の規則で回さない。
#
# | 分類 | 収束の条件 | 差分の渡し方 |
# | --- | --- | --- |
# | design | 上限 3 ラウンドで関門 1 へ渡す（収束を待たない） | 2 ラウンド目以降は前のラウンドからの変更だけ |
# | code | 新しい指摘が出なくなるまで | 全差分 |
#
# 設計の指摘の連鎖は文書の修正が生むため、回すほど増える。実装 PR は 2 ラウンドで収束するのが普通。

REVIEW_KINDS = ("design", "code")
DEFAULT_MAX_ROUNDS = {"design": 3, "code": 12}
# 1 本の設計文書の行数の上限。超える主題は設計を 2 本に分ける
DESIGN_DOC_MAX_LINES = 1000
DESIGN_BRANCH_PREFIX = "design/"
# 行数を見る設計文書（`design` が `issues/` に置く文書のうち、要求の文書を除く）
DESIGN_DOC_FILE_SUFFIXES = ("-design.md", "-design-decisions.md")


def review_kind(head_branch: str | None, categories: list[str] | tuple[str, ...]) -> str:
    """PR を design / code へ分ける。

    head のブランチが `design/` で始まるか、変更が設計文書だけ（`design` を含み `code` を含まない）
    なら design。それ以外は code。
    """
    if (head_branch or "").startswith(DESIGN_BRANCH_PREFIX):
        return "design"
    cats = set(categories or ())
    if "design" in cats and "code" not in cats:
        return "design"
    return "code"


def default_max_rounds(kind: str) -> int:
    """`--max-rounds` を渡さないときの上限。"""
    return DEFAULT_MAX_ROUNDS.get(kind, DEFAULT_MAX_ROUNDS["code"])


def diff_scope(kind: str, round_no: int) -> str:
    """レビューの担当へ渡す差分の範囲。`since_previous`（前のラウンドからの変更だけ）か `full`。"""
    return "since_previous" if kind == "design" and round_no >= 2 else "full"


def oversized_design_docs(root, paths, limit: int = DESIGN_DOC_MAX_LINES) -> list[dict]:
    """設計文書のうち行数が上限を超えるものを `{"path", "lines"}` で返す。読めないものは飛ばす。"""
    from pathlib import Path

    from_root = Path(root) if root else None
    out = []
    for p in dict.fromkeys(paths or ()):
        if not (isinstance(p, str) and p.startswith("issues/") and p.endswith(DESIGN_DOC_FILE_SUFFIXES)):
            continue
        f = from_root / p if from_root else Path(p)
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                lines = sum(1 for _ in fh)
        except OSError:
            continue
        if lines > limit:
            out.append({"path": p, "lines": lines})
    return out


# --- 設計 PR のレビューの段（#1111） -------------------------------------------------
#
# 設計 PR の 1 ラウンド目はドメインモデルの節だけを見る（モデルの段）。2 ラウンド目以降は、確定した
# モデルを前提に残りを見る（詳細の段）。設計文書にドメインモデルの節が無ければ 1 ラウンド目から詳細の段。
# モデルの段の APPROVE では抜けない。関門の数とラウンドの上限は変えない。

REVIEW_STAGES = ("model", "detail")
DOMAIN_MODEL_HEADING = "## ドメインモデル"


def review_stage(kind: str, round_no: int, has_model: bool) -> str | None:
    """そのラウンドの段。`design` の 1 ラウンド目で `has_model` なら model、ほかの design は detail、code は None。"""
    if kind != "design":
        return None
    return "model" if round_no == 1 and has_model else "detail"


def has_domain_model(root, paths) -> bool:
    """変更した設計文書のどれかに見出し `## ドメインモデル` があるか。読めないものは飛ばす。"""
    from pathlib import Path

    from_root = Path(root) if root else None
    for p in dict.fromkeys(paths or ()):
        if not (isinstance(p, str) and p.endswith(DESIGN_DOC_FILE_SUFFIXES)):
            continue
        f = from_root / p if from_root else Path(p)
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                if any(line.rstrip() == DOMAIN_MODEL_HEADING for line in fh):
                    return True
        except OSError:
            continue
    return False
