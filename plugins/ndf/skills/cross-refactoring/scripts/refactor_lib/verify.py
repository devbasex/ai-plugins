"""適用結果の検証。

対象範囲の逸脱・コミットのトレーラー・差分予算・コミット粒度を判定する。判定に
使う事実は git から取った値で、結果ファイルの申告は使わない。
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from .gitfacts import _safe_int
from .vocabulary import (
    DIFF_BUDGET_FACTOR,
    EXTRACTION_DIFF_BUDGET_FACTOR,
    EXTRACTION_TECHNIQUES,
    MAX_COMMITS_PER_ITEM,
    MAX_COMMITS_PER_ITEM_WITH_TEST_GAP,
    REQUIRED_TRAILERS,
)


# **結果ファイルの申告は検証の材料にしない。** 実装担当は自分の成果を報告する側なので、
# トレーラーもテスト結果も差分行数も、JSON の値を書き換えるだけで検査を通せてしまう。
# ここで使う事実（コミットの実在 / トレーラー / 差分行数 / テストの成否）は、すべて
# **git と実際のテスト実行**から取る。結果ファイルから使うのは「どのコミットが
# どの項目のものか」という対応付けの手がかりだけである。

def path_in_scope(path: str, scope: Iterable[str]) -> bool:
    """`path` が対象範囲の中にあるか。判定は**前方一致だけ**で行う。

    除外規則を足さない。規則を書けるようにすると、規則を 1 行足すだけで
    範囲の検査を骨抜きにできてしまう。

    突き合わせる前に `./` を落とす。シェルの補完で `--scope ./src` の形になることが
    多い一方、git が出すのは `src/foo.py` なので、**そのまま比べると全てのコミットが
    範囲外**になり、適用が必ず失敗する。`.` と `./` はリポジトリ全体を指す。
    """
    for entry in scope:
        raw = str(entry).strip()
        if not raw:
            continue
        prefix = raw
        while prefix.startswith("./"):
            prefix = prefix[2:]
        prefix = prefix.rstrip("/")
        if prefix in {"", "."}:
            return True
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def out_of_scope_files(commit: dict[str, Any], scope: Iterable[str]) -> list[str]:
    """コミットが触った**対象範囲の外**のファイル。範囲が空なら検査しない。"""
    paths = list(scope)
    if not paths:
        return []
    return sorted(
        p for p in (commit.get("files") or []) if not path_in_scope(p, paths)
    )


def verify_scope(commit: dict[str, Any], scope: Iterable[str]) -> Optional[str]:
    """対象範囲の外を触っていれば理由を返す。

    範囲を必須にした目的は**提案の発散と変更の肥大を防ぐ**ことなので、指定を
    検証に反映しないと目的を果たせない。実測では、生成物を同期する規約に従った
    結果として範囲外が 3 系統変更され、差分が 4 倍に膨らんで差分予算を超えた。
    生成物の同期が要る構成では、**同期は進行側の責務**として分離する。
    """
    outside = out_of_scope_files(commit, scope)
    if not outside:
        return None
    shown = ", ".join(outside[:5])
    more = f" ほか {len(outside) - 5} 件" if len(outside) > 5 else ""
    return (
        f"コミット {commit.get('sha', '?')} が対象範囲の外を変更しています"
        f"（{shown}{more}）。生成物の同期は進行側が公開の直前に行います。"
        "現状固定テストの置き場所が範囲外なら、`--scope` に含めてから実行してください"
    )


def verify_commit_trailers(commit: dict[str, Any]) -> Optional[str]:
    """コミットのトレーラーが 4 つ揃っているか。欠けていれば理由を返す。

    `commit` は **git から取った事実**（`collect_commit_facts()` の戻り値）である。
    結果ファイルの `trailers` を渡してはならない。
    """
    trailers = commit.get("trailers") or {}
    missing = [k for k in REQUIRED_TRAILERS if not str(trailers.get(k) or "").strip()]
    if missing:
        return f"コミット {commit.get('sha', '?')} にトレーラーが欠けています: {', '.join(missing)}"
    return None


def _verify_commit_basics(
    commit: dict[str, Any],
    scope: Optional[Iterable[str]],
    missing_reason: str,
) -> Optional[str]:
    """コミット 1 件が手順を満たしているかを検証する。問題があれば理由を返す。

    適用（`verify_apply_item`）と修正（`verify_fix_commit`）で**同じ基準**を使う。
    片方だけ直されると基準が食い違い、緩い側から手順を外れた変更が入る。

    実体が無いときの理由文だけは呼び出し側から渡す。範囲の呼び方が適用
    （base..head）と修正（修正ラウンドの範囲）で違うためである。
    """
    if not commit.get("exists", True):
        return missing_reason
    problem = verify_commit_trailers(commit)
    if problem:
        return problem
    problem = verify_scope(commit, scope or [])
    if problem:
        return problem
    if commit.get("test_status") != "pass":
        return (
            f"コミット {commit.get('sha', '?')} でテストが成功していません "
            f"({commit.get('test_status')})"
        )
    return None


def verify_fix_commit(
    commit: dict[str, Any], scope: Optional[Iterable[str]] = None
) -> Optional[str]:
    """修正コミットを適用と同じ基準で検証する。問題があれば理由を返す。

    適用側だけ厳しくして修正側を素通しにすると、**レビュー指摘への対応という
    名目で手順を外れた変更が入り、そのまま収束済みになる**。
    """
    return _verify_commit_basics(
        commit,
        scope,
        f"コミット {commit.get('sha', '?')} が対象の範囲に存在しません",
    )


def diff_budget_factor(technique: Optional[str]) -> int:
    """その手法に許す差分予算の倍率。

    抽出系だけ広げる。全体を広げると、範囲外を触った変更まで通ってしまう。
    """
    if technique in EXTRACTION_TECHNIQUES:
        return EXTRACTION_DIFF_BUDGET_FACTOR
    return DIFF_BUDGET_FACTOR


def verify_apply_item(
    item: dict[str, Any], facts: list[dict[str, Any]],
    scope: Optional[Iterable[str]] = None,
) -> Optional[str]:
    """1 項目の適用結果を検証する。問題があれば失敗理由を返す。

    `facts` は `collect_commit_facts()` が git と実際のテスト実行から作る。
    振る舞い不変そのものは機械的に確かめられないが、**手順が守られたかは結果から
    確かめられる**。読ませ方の不確実性に対する最後の砦としてここを厚くする。
    """
    if not facts:
        return "コミットが 1 件もありません（1 改善項目 = 1 コミットの前提を満たしていません）"

    for commit in facts:
        problem = _verify_commit_basics(
            commit,
            scope,
            f"コミット {commit.get('sha', '?')} が base..head の範囲にありません"
            "（申告だけで実体がありません）",
        )
        if problem:
            return problem
        item_id = (commit.get("trailers") or {}).get("Item-Id")
        if item_id != item["item_id"]:
            return (
                f"コミット {commit.get('sha', '?')} の Item-Id が {item_id} で、"
                f"項目 {item['item_id']} と一致しません"
                "（複数の項目を 1 コミットにまとめると取り消し範囲が決まりません）"
            )

    if item.get("test_gap"):
        # テストが乏しいと申告された項目は、現状固定テストの追加が先行していること。
        # 実測では同じ課題で固定テストの追加数が 17 本 / 1 メソッド / 0 本と揃わなかった。
        # 「テストを足した」かどうかは、そのコミットがテストの置き場所を触ったかで見る。
        if not facts[0].get("touches_tests"):
            return (
                "テストが乏しい項目なのに、現状固定テストの追加コミットが先行していません"
                f"（先頭コミット {facts[0].get('sha', '?')} がテストを触っていません）"
            )

    estimated = _safe_int(item.get("estimated_diff_lines"))
    factor = diff_budget_factor(item.get("technique"))
    budget = estimated * factor
    actual = sum(int(c.get("diff_lines") or 0) for c in facts)
    if budget and actual > budget:
        return (
            f"実差分 {actual} 行が差分予算 {budget} 行"
            f"（見積 {estimated} 行 × {factor}）を超えました（範囲の逸脱）"
        )

    # 粒度は最後に見る。トレーラーやテストの問題を粒度の失敗で覆い隠さない。
    # 数えるのは**実在するコミットの数**である。同じコミットを重ねて申告しただけの
    # ときに落とすと、食い違いの無い申告を刻みすぎとして扱ってしまう。
    problem = verify_commit_granularity(item, len({c.get("sha") for c in facts}))
    if problem:
        return problem
    return None


def commit_limit_for(item: dict[str, Any]) -> int:
    """その項目が履歴に残せるコミット数。"""
    if item.get("test_gap"):
        return MAX_COMMITS_PER_ITEM_WITH_TEST_GAP
    return MAX_COMMITS_PER_ITEM


def verify_commit_granularity(item: dict[str, Any], count: int) -> Optional[str]:
    """項目のコミット数が上限に収まっているか。超えていれば理由を返す。

    適用（`verify_apply_item`）と修正（`_verify_fix_commits`）で**同じ基準**を使う。
    適用側だけ揃えると、レビュー指摘への対応という名目で刻んだ履歴が戻ってくる。
    """
    limit = commit_limit_for(item)
    if count <= limit:
        return None
    return (
        f"項目 {item['item_id']} のコミットが {count} 件あります"
        f"（残すのは 1 項目 = 1 コミット。現状固定テストが要る項目だけ 2 コミットまで）"
    )
