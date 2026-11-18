"""GitHub Actions のワークフローの読み取り（#464 決定 5）。

11.0.0 の入れ替え（#3304）で 10 系のプラグインから根へ移した。根の検査（`check-required-checks.py`）が使う
必須のチェックの一覧の取り出し（`required_contexts`）だけを持つ。標準ライブラリだけで書く。
"""

from __future__ import annotations


# ワークフローの置き場（根からの相対）と、ファイルのパス（根からの相対）
WORKFLOW_DIR = ".github/workflows"


def required_contexts(rules: object) -> list[str] | None:
    """`rules/branches/<branch>` の応答（規則の配列）→ 必須のチェックの名前（重複なく整列）。

    `required_status_checks` の規則が 1 つも無ければ None（規則を持たないブランチと、存在しないブランチの
    どちらでもあり得る。見分けは呼ぶ側が持つ）。配列でなければ ValueError。突き合わせのチェック
    （`scripts/check-required-checks.py`）と解析（`project_lib/measure_ci.py`）が使う（#653 の決定 6）。
    """
    if not isinstance(rules, list):
        raise ValueError(f"規則の配列でない: {type(rules).__name__}")
    found, out = False, set()
    for r in rules:
        if not isinstance(r, dict) or r.get("type") != "required_status_checks":
            continue
        found = True
        for c in (r.get("parameters") or {}).get("required_status_checks") or []:
            if isinstance(c, dict) and c.get("context"):
                out.add(c["context"])
    return sorted(out) if found else None
