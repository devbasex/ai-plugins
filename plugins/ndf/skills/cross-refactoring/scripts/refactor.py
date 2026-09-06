#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""cross-refactoring の状態管理 CLI。

`<work>/.cross_refactoring/cross-refactoring-rf<ID>-state.json` の初期化・読み書きと、
二段の収束判定（提案ラウンドの繰り返しの中にレビュー収束の繰り返しが入る）を
1 つの CLI に集約する。

サブコマンドの一覧と役割は `--help` が持つ。ここには写さない（片方だけが古くなるため）。

終了コードは呼び出し側の bash が分岐に使う。各サブコマンドの docstring を参照。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import time
import types
from typing import Any, Callable, Iterable, Optional

# 共通層はプラグインルート直下にある。**`.resolve()` を通す。** Kiro CLI は
# `.kiro/skills/<名前>` を symlink にするため、解かずに `parents[]` を数えると
# `.kiro` で止まってプラグインルートへ届かない。
sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parents[3] / "scripts" / "lib"),
)

import assignment  # noqa: E402
import auth  # noqa: E402
import metrics as metrics_lib  # noqa: E402
import models as models_lib  # noqa: E402
import statefile  # noqa: E402

# 分割したモジュールは同じディレクトリの `refactor_lib/` にある。**自身の
# ディレクトリを探索先へ入れる。** `uv run --script` で起動したときの現在地は、
# スクリプトの位置と揃わない。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from refactor_lib import ABORT, die, info  # noqa: E402,F401
from refactor_lib import vocabulary as _vocabulary  # noqa: E402
from refactor_lib import paths as _paths  # noqa: E402
from refactor_lib import plan as _plan  # noqa: E402
from refactor_lib import gitfacts as _gitfacts  # noqa: E402

# **入口は全モジュールの名前を自分の名前空間へ取り込む。** 呼び出し側と手順書は
# `refactor.py` を指し、テストは `refactor.<名前>` を参照する。分割してもその形を
# 変えない。
_LIB_MODULES: tuple[types.ModuleType, ...] = (
    _vocabulary,
    _paths,
    _plan,
    _gitfacts,
)


def _reexport() -> None:
    """各モジュールの名前を入口へ取り込む。モジュール自身は取り込まない。"""
    for mod in _LIB_MODULES:
        for name, value in vars(mod).items():
            if name.startswith("__") or isinstance(value, types.ModuleType):
                continue
            globals()[name] = value


_reexport()


class _Entry(types.ModuleType):
    """`refactor.<名前>` の差し替えを、定義元のモジュールへも伝える。

    再エクスポートは値の写しであるため、入口だけを差し替えても、定義元を見て
    いる呼び出し側は元の値を使い続ける。**差し替えが片側にしか効かない状態を
    作らない。**
    """

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        for mod in _LIB_MODULES:
            if name in vars(mod):
                setattr(mod, name, value)


sys.modules[__name__].__class__ = _Entry


# ---------------- 提案のマージ ----------------

def _normalize_proposal(raw: dict[str, Any], source: str) -> Optional[dict[str, Any]]:
    """1 件の提案を正規化する。必須項目を欠くものは捨てる。

    語彙外の `smell` / `technique` は `unknown` として警告し、**最低の重要度へ
    降格**させる。しきい値で自動的に落ちるため、語彙を守らない提案が
    重複排除をすり抜けて残ることがない。
    """
    path = str(raw.get("path") or "").strip()
    symbol = str(raw.get("symbol") or "").strip()
    if not path or not symbol:
        info(f"⚠ {source}: path / symbol の無い提案を無視しました: {raw!r:.120}")
        return None

    smell = str(raw.get("smell") or "").strip()
    technique = str(raw.get("technique") or "").strip()
    severity = str(raw.get("severity") or "").strip().lower()
    degraded = False
    if smell not in SMELLS:
        info(f"⚠ {source}: 語彙外の兆候 `{smell}` — unknown へ降格 ({path}#{symbol})")
        smell = "unknown"
        degraded = True
    if technique not in TECHNIQUES:
        info(f"⚠ {source}: 語彙外の手法 `{technique}` — unknown へ降格 ({path}#{symbol})")
        technique = "unknown"
        degraded = True
    if severity not in SEVERITY_ORDER:
        info(f"⚠ {source}: 語彙外の重要度 `{severity}` — unknown へ降格 ({path}#{symbol})")
        severity = "unknown"
        degraded = True
    if degraded:
        severity = "unknown"

    estimated = _safe_int(raw.get("estimated_diff_lines"))

    return {
        "path": path,
        "symbol": symbol,
        "smell": smell,
        "technique": technique,
        "severity": severity,
        "rationale": str(raw.get("rationale") or "").strip(),
        "plan": str(raw.get("plan") or "").strip(),
        "test_gap": bool(raw.get("test_gap")),
        "estimated_diff_lines": max(estimated, 0),
        "proposed_by": [source],
    }


def _dedupe_key(item: dict[str, Any]) -> tuple[str, str, str]:
    """重複排除の鍵。同じ箇所への同じ兆候の指摘を 1 件へまとめる。"""
    return (item["path"], item["symbol"], item["smell"])


def _merge_one(existing: dict[str, Any], incoming: dict[str, Any]) -> None:
    """同一の鍵を持つ提案を統合する。

    `rationale` と `plan` は**最も具体的なもの**（長い方）を採る。重要度は高い方、
    推定差分行数は大きい方を採り、見積りを楽観側へ倒さない。
    """
    for source in incoming["proposed_by"]:
        if source not in existing["proposed_by"]:
            existing["proposed_by"].append(source)
    if len(incoming["rationale"]) > len(existing["rationale"]):
        existing["rationale"] = incoming["rationale"]
    if len(incoming["plan"]) > len(existing["plan"]):
        existing["plan"] = incoming["plan"]
    if SEVERITY_ORDER[incoming["severity"]] > SEVERITY_ORDER[existing["severity"]]:
        existing["severity"] = incoming["severity"]
        existing["technique"] = incoming["technique"]
    existing["test_gap"] = existing["test_gap"] or incoming["test_gap"]
    existing["estimated_diff_lines"] = max(
        existing["estimated_diff_lines"], incoming["estimated_diff_lines"]
    )


def merge_proposals(
    proposals: dict[str, list[dict[str, Any]]],
    threshold: str = DEFAULT_SEVERITY_THRESHOLD,
    max_items: int = 5,
    excluded_keys: Iterable[tuple[str, str, str]] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """提案をマージして `(採用, 見送り)` を返す。

    優先度は「**合意したランタイム数 → 重要度 → 推定差分行数の昇順**」。
    小さく合意の多いものから直す。合意が多い提案は誤検知の確率が低く、
    小さい提案は失敗したときの取り消し範囲も小さい。

    `excluded_keys` には過去に見送った項目の鍵を渡す。見送った項目を毎ラウンド
    再提案されると収束しないため、対象外として落とす。
    """
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for source, items in proposals.items():
        for raw in items:
            norm = _normalize_proposal(raw, source)
            if norm is None:
                continue
            key = _dedupe_key(norm)
            if key in merged:
                _merge_one(merged[key], norm)
            else:
                merged[key] = norm

    excluded = set(excluded_keys)
    adopted: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    min_severity = SEVERITY_ORDER.get(threshold, SEVERITY_ORDER[DEFAULT_SEVERITY_THRESHOLD])

    for key, item in merged.items():
        if key in excluded:
            item["defer_reason"] = "過去のラウンドで見送った項目のため対象外"
            deferred.append(item)
        elif SEVERITY_ORDER[item["severity"]] < min_severity:
            item["defer_reason"] = f"重要度 {item['severity']} がしきい値 {threshold} 未満"
            deferred.append(item)
        else:
            adopted.append(item)

    adopted.sort(
        key=lambda i: (
            -len(i["proposed_by"]),
            -SEVERITY_ORDER[i["severity"]],
            i["estimated_diff_lines"],
            i["path"],
            i["symbol"],
        )
    )
    if len(adopted) > max_items:
        for item in adopted[max_items:]:
            item["defer_reason"] = f"1 ラウンドの採用上限 {max_items} 件を超えた"
        deferred.extend(adopted[max_items:])
        adopted = adopted[:max_items]
    return adopted, deferred


def duplicate_rate(
    current: Iterable[tuple[str, str, str]], previous: Iterable[tuple[str, str, str]]
) -> float:
    """前ラウンドの提案とどれだけ重なっているか。前ラウンドが空なら 0 を返す。"""
    prev = set(previous)
    cur = set(current)
    if not prev or not cur:
        return 0.0
    return len(cur & prev) / len(cur)


# ---------------- 適用結果の検証 ----------------
#
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


# ---------------- レビュー判定 ----------------

REVIEW_URL_MARKER = "#pullrequestreview-"


def _review_id_from_url(url: Optional[str]) -> str:
    """レビュー URL から識別子を取り出す。取れなければ空文字。"""
    _, sep, tail = (url or "").partition(REVIEW_URL_MARKER)
    return tail if sep and tail.isdigit() else ""


def _posted_review_state(repo: str, pr: int, review_id: str) -> Optional[bool]:
    """投稿されたレビューが GitHub 側にあるか。

    `True` = ある / `False` = 無い / `None` = 確かめられない。

    **「無い」と「確かめられない」を区別する。** 取得の失敗で止めると、
    GitHub 側の一時的な不調で進行が進まなくなる。
    """
    r = subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{pr}/reviews/{review_id}", "--jq", ".id"],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        return True
    if "404" in r.stderr or "Not Found" in r.stderr:
        return False
    return None


def _unposted_reviewers(
    state: dict[str, Any], reviews: dict[str, Any], reviewers: list[str]
) -> tuple[list[str], list[str]]:
    """投稿できていないレビュー担当と、その理由を返す。

    申告された URL の識別子を GitHub 側へ問い合わせて存在を確かめる。
    取得できないときは申告を採り、確かめられなかったことを出力へ残す。
    """
    names: list[str] = []
    problems: list[str] = []
    for name in reviewers:
        review = reviews.get(name)
        if not isinstance(review, dict):
            continue
        url = review.get("review_url")
        if review.get("post_error") or not url:
            # 理由は `judge()` が問題として出す。ここでは担当だけ拾う。
            names.append(name)
            continue
        review_id = _review_id_from_url(url)
        if not review_id:
            names.append(name)
            problems.append(f"{name} のレビュー URL の形が違います: {url}")
            continue
        posted = _posted_review_state(state["repo"], state["current_pr"], review_id)
        if posted is False:
            names.append(name)
            problems.append(f"{name} のレビューが GitHub 側にありません: {url}")
        elif posted is None:
            info(f"⚠ {name} のレビューの存在を確かめられませんでした（申告を採ります）: {url}")
    return names, problems


def _validate_review_payload(name: str, review: Any) -> Optional[str]:
    """review の存在と dict 型を確認する。

    出力の形が崩れていても落ちないようにする。相手は LLM なので、
    期待した型で返ってこないことがある。崩れていたら差し戻す。
    """
    if not review:
        return f"{name} のレビュー結果がありません"
    if not isinstance(review, dict):
        return f"{name} のレビュー結果が JSON オブジェクトではありません"
    return None


def _validate_review_verdict_and_posting(name: str, review: dict[str, Any]) -> list[str]:
    """verdict と review_url/post_error を検証する。

    **投稿できていない判定は採らない。** 投稿が失敗しても結果ファイルの
    判定だけは残るため、そのまま採ると実装担当が読むべき指摘が
    Pull Request に無いまま収束する。
    """
    problems: list[str] = []
    verdict = review.get("verdict")
    if verdict not in {"APPROVE", "REQUEST_CHANGES"}:
        problems.append(
            f"{name} の判定 `{verdict}` は APPROVE / REQUEST_CHANGES のいずれかで"
            "なければなりません（判定に COMMENT は使いません。"
            "投稿の event とは別物です）"
        )
    post_error = review.get("post_error")
    if post_error:
        problems.append(f"{name} がレビューを投稿できませんでした: {post_error}")
    elif not review.get("review_url"):
        problems.append(
            f"{name} のレビュー URL がありません（投稿できていない可能性があります）"
        )
    return problems


def _validate_review_findings(
    name: str, findings: Any, round_items: list[str]
) -> list[str]:
    """findings 配列と各要素の item_id を検証する。"""
    problems: list[str] = []
    if not isinstance(findings, list):
        return [f"{name} の findings が配列ではありません"]
    for i, finding in enumerate(findings):
        if not isinstance(finding, dict):
            problems.append(
                f"{name} の指摘 {i + 1} が JSON オブジェクトではありません"
            )
            continue
        if "item_id" not in finding:
            problems.append(f"{name} の指摘 {i + 1} に item_id がありません")
            continue
        item_id = finding["item_id"]
        if item_id is not None and item_id not in round_items:
            problems.append(
                f"{name} の指摘 {i + 1} の item_id `{item_id}` は"
                "このラウンドの改善項目ではありません"
            )
    return problems


def _requested_changes(
    reviewers: list[str], reviews: dict[str, Any]
) -> list[str]:
    """変更要求を出したレビュー担当を返す。修正の後の再レビューの対象になる。

    承認した担当は、指摘への対応だけの差分をもう一度読むことになる。範囲外を触った
    修正は進行側の機械検証（`--scope` の逸脱・差分予算・テスト）が拾うため、起動を
    重ねない。
    """
    return [
        name for name in reviewers
        if (reviews.get(name) or {}).get("verdict") != "APPROVE"
    ]


def judge(
    reviews: dict[str, dict[str, Any]], reviewers: list[str], round_items: list[str]
) -> tuple[str, list[str]]:
    """レビュー結果を判定し `(判定, 問題の一覧)` を返す。

    判定は `approved` / `changes` / `invalid` の 3 つ。
    `invalid` は差し戻して**再レビューさせる**もので、承認にも変更要求にもしない。

    指摘には改善項目 ID を必須とする。取り消しを項目単位で行うために必要で、
    そのラウンドに無い ID や欠落は判定に使えない。ラウンド全体に対する指摘は
    `null` を明示させ、取り消し時はラウンド全件の対象とする。
    """
    problems: list[str] = []
    for name in reviewers:
        review = reviews.get(name)
        payload_problem = _validate_review_payload(name, review)
        if payload_problem:
            problems.append(payload_problem)
            continue
        problems.extend(_validate_review_verdict_and_posting(name, review))
        problems.extend(
            _validate_review_findings(name, review.get("findings") or [], round_items)
        )
    if problems:
        return "invalid", problems
    if not _requested_changes(reviewers, reviews):
        return "approved", []
    return "changes", []


def unresolved_item_ids(
    review_history: list[dict[str, Any]], round_items: list[str]
) -> tuple[list[str], bool]:
    """未解決の指摘から `(取り消す項目 ID, ラウンド全件が対象か)` を求める。

    ID が `null` の未解決指摘（ラウンド全体に対する指摘）が 1 件でもあれば、
    そのラウンドで適用した項目を全件取り消す。どの項目に紐づくか決められない
    以上、一部だけ残すと Pull Request に中途半端な状態が残るためである。
    """
    targets: list[str] = []
    whole_round = False
    for review in review_history:
        for finding in review.get("findings") or []:
            if finding.get("resolved"):
                continue
            item_id = finding.get("item_id")
            if item_id is None:
                whole_round = True
            elif item_id in round_items and item_id not in targets:
                targets.append(item_id)
    if whole_round:
        return list(round_items), True
    return targets, False


# ---------------- サブコマンド ----------------

def check_auth(runtimes: Iterable[str]) -> dict[str, dict[str, Any]]:
    """参加する CLI の認証状態を確かめる。1 つでも欠けたら初期化を中断する。

    実装は共通層（`lib/auth.py`）にある。**この工程の中断は終了コード 4 である**ため、
    出力と中断の手段をここから渡す。
    """
    return auth.check_auth(runtimes, info=info, die=die)


def _review_post_note(is_own_pr: bool) -> str:
    """レビュープロンプトへ渡す投稿の event の指示を組み立てる。

    定義を検証側（この CLI）に置き、状態ファイル経由で起動側へ渡す。
    語彙の受け渡しと同じ形にして、文面の分岐が起動シェルへ散らないようにする。
    """
    if is_own_pr:
        return (
            "この Pull Request の作成者はあなたを動かしている利用者本人です。"
            "GitHub は自分の Pull Request への `APPROVE` と `REQUEST_CHANGES` を "
            "`HTTP 422` で拒むため、**投稿は必ず `-f event=COMMENT` で行ってください**。"
            "判定そのものは本文の先頭行と結果ファイルへ `APPROVE` / `REQUEST_CHANGES` "
            "のまま残します。収束判定は結果ファイルの判定を見るので、"
            "投稿を倒しても評価は変わりません。"
        )
    return (
        "投稿の `-f event=` には判定をそのまま渡してください"
        "（`APPROVE` または `REQUEST_CHANGES`）。"
    )


def _apply_post_event(state: dict[str, Any], is_own_pr: bool) -> None:
    """投稿の event に関する項目を状態へ入れる。

    初期化と再開の**両方**から呼ぶ。この指示が入る前の版で作った状態ファイルには
    項目そのものが無く、無いまま再開すると自分の Pull Request で `HTTP 422` を
    踏み続ける。値は GitHub 側の照合結果だけで決まるので、再開のたびに入れ直しても
    判定は変わらない。
    """
    state["is_own_pr"] = is_own_pr
    state["event_downgrade"] = is_own_pr
    state["review_post_note"] = _review_post_note(is_own_pr)


def _warn_unmeasurable_models(
    model_spec: dict[str, Optional[str]], participants: Iterable[str]
) -> None:
    """実際に動いたモデルを取得できない指定を、**着手前に**知らせる。

    分離の対象は 2 つある。kiro の既定 `auto` はラウンドごとに違うモデルが動きうる。
    実測モデル名を取れないランタイム（claude 以外）で `--model` を渡さないラウンドも、
    何が動いたかを後から確かめる手段が無い。報告まで分からないと、比較のために
    回した実行が丸ごと無駄になる。止めはしない（比較が目的でない実行もある）。
    """
    for runtime in sorted(participants):
        if models_lib.is_measurable(runtime, model_spec.get(runtime)):
            continue
        info(
            f"⚠ {runtime} のモデルが "
            f"{models_lib.label(model_spec.get(runtime))} です — "
            "実際に動いたモデルを取得できないため、そのラウンドは集計から分離されます。"
            f"比較するなら --model {runtime}=<モデル名> を指定してください"
        )


_REPO_URL = re.compile(
    r"(?:github\.com[:/])(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$"
)


def _repo_from_git() -> Optional[str]:
    """git の設定から `owner/repo` を求める。求まらなければ `None`。

    **求めた名前はそのまま使わない。** `repos/{owner}/{repo}/pulls/{PR}` の応答が
    そのまま検証になるため、誤った名前は失敗として現れる（`_fetch_pr_context`）。
    """
    m = _REPO_URL.search(_sh(["git", "remote", "get-url", "origin"], check=False))
    return f"{m.group('owner')}/{m.group('name')}" if m else None


def _pr_payload(repo: str, pr: int) -> Optional[dict[str, Any]]:
    """`repos/{repo}/pulls/{pr}` の応答を返す。読めなければ `None`。"""
    out = _sh(["gh", "api", f"repos/{repo}/pulls/{int(pr)}"], check=False)
    if not out:
        return None
    try:
        body = json.loads(out)
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) and body.get("number") else None


def _fetch_pr_context(pr: int, repo: Optional[str] = None) -> tuple[str, str, str, bool, str]:
    """GitHub から Pull Request のメタデータを取り、自分の Pull Request かを判定する。

    返すのは `(repo, base_branch, head_branch, is_own_pr, author)`。

    **作成者・head・base は REST の 1 回でまとめて取る**（#271）。項目ごとに
    `gh pr view` を投げると、同じ Pull Request へ GraphQL を 3 点使う。尽きるのは
    GraphQL 側であり、REST 側は上限 5,000 のうち大半が残ったまま進行が止まる。
    """
    tried: list[str] = []
    body: Optional[dict[str, Any]] = None
    resolved = ""
    for candidate in (repo, _repo_from_git()):
        if not candidate or candidate in tried:
            continue
        tried.append(candidate)
        body = _pr_payload(candidate, pr)
        if body is not None:
            resolved = candidate
            break
    if body is None:
        # 求めた名前が誤っていたときだけ、GraphQL で解決し直す。
        fallback = _sh(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
        body = _pr_payload(fallback, pr)
        if body is None:
            die(f"Pull Request #{pr} のメタデータを取得できません（リポジトリ名: {fallback}）")
            raise SystemExit(ABORT)
        resolved = fallback

    # **取得に失敗しても止めない。** bot トークン（Actions の `GITHUB_TOKEN` など）は
    # `/user` を読めず `HTTP 403` を返す。この値は自分の Pull Request かどうかの
    # 判定にしか使わないので、読めなければ他者の Pull Request として扱えばよい。
    viewer = _sh(["gh", "api", "user", "--jq", ".login"], check=False)
    author = str((body.get("user") or {}).get("login") or "")
    is_own_pr = bool(viewer) and viewer == author
    head_branch = str((body.get("head") or {}).get("ref") or "")
    base_branch = str((body.get("base") or {}).get("ref") or "")
    return resolved, base_branch, head_branch, is_own_pr, author


def cmd_init(args: argparse.Namespace) -> None:
    """Step 0 — ホストと母集合を確定し、作業ディレクトリ root と状態を用意する。

    **提案・レビューの母集合（全 − ホスト）と適用の母集合（全 − agy）を
    別々に確定する。** 両者は重なるが一致しない。
    """
    try:
        host, detection = assignment.detect_host(args.host)
    except assignment.AssignmentError as e:
        die(str(e))
        return
    try:
        model_spec = models_lib.parse_model_args(args.model)
    except models_lib.ModelSpecError as e:
        die(str(e))
        return

    runtimes = assignment.review_pool(host)
    impl_capable = assignment.impl_pool()
    if host in runtimes:
        die(f"提案・レビューの母集合にホスト {host} が含まれています（判定の誤り）")
    _warn_unmeasurable_models(model_spec, set(runtimes) | set(impl_capable))

    # **認証は作業ディレクトリを作る前に確かめる。** 未認証のまま進むと、
    # 参加者が欠けた構成のまま最後まで走り切ってしまう。
    auth = check_auth(sorted(set(runtimes) | set(impl_capable)))

    # リポジトリ名は git の設定から求め、Pull Request の応答で確かめる（#271）。
    repo, base_branch, head_branch, is_own_pr, author = _fetch_pr_context(args.pr)
    if is_own_pr:
        info(f"⚠ 自分の Pull Request です（作成者 {author}）— 投稿は COMMENT へ倒します")

    root = (
        pathlib.Path(args.worktree_root).resolve() if args.worktree_root
        else _default_worktree_base() / _repo_slug(repo) / f"rf{args.pr}"
    )
    work = root / "work"
    _ensure_work_worktree(work, head_branch)

    tmp_dir = _tmp_dir_for(work)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    state_file = _state_path(tmp_dir, args.pr)

    if state_file.exists():
        state = statefile.load(state_file)
        if state.get("final") is None:
            info(f"↻ 前回中断した状態から再開します（提案ラウンド {state.get('outer_round', 0)}）")
            _apply_post_event(state, is_own_pr)
            statefile.save(state_file, state)
            _emit_init(state)
            return

    baseline = _run_baseline_test(args.baseline_test, work, args.test_timeout)

    state: dict[str, Any] = {
        "id": args.pr,
        "started_at": statefile.now(),
        "repo": repo,
        "current_pr": args.pr,
        "base_branch": base_branch,
        "head_branch": head_branch,
        "worktree_root": str(root),
        "worktrees": {"work": str(work), **{r: str(root / r) for r in runtimes}},
        "tmp_dir": str(tmp_dir),
        "target_scope": list(args.scope),
        "host": host,
        "host_detection": detection,
        "runtimes": runtimes,
        "impl_capable": impl_capable,
        "models": model_spec,
        "auth": auth,
        # 提案プロンプトへ許容値をそのまま列挙するために持たせる。
        # 定義は検証側（この CLI）にあり、状態ファイル経由で起動側へ渡す。
        "vocabulary": vocabulary(),
        "skills": {"required": list(REQUIRED_SKILLS)},
        "max_outer_rounds": args.max_outer_rounds,
        "max_fix_rounds": args.max_fix_rounds,
        "max_items_per_round": args.max_items_per_round,
        "severity_threshold": args.severity_threshold,
        "baseline_test": baseline,
        # 生成物の同期は**進行側の責務**。push の直前に実行する。
        "sync_command": args.sync_command,
        # 改修計画の書き出し先も同じ経路に乗せる。指定が無ければ既定のパスを使い、
        # 空文字なら記録しない。
        "plan_file": normalize_plan_file(
            default_plan_file(args.pr) if args.plan_file is None else args.plan_file
        ),
        "test_timeout": args.test_timeout,
        "outer_round": 0,
        "phase": "init",
        "rounds": [],
        "items": [],
        "deferred_items": [],
        "final": None,
    }
    # GitHub は自分の Pull Request への `APPROVE` と `REQUEST_CHANGES` を
    # `HTTP 422` で拒む。判定はそのまま結果ファイルへ残し、**投稿の event だけ**
    # を倒す。収束判定は結果ファイルの判定を見るので、倒しても進行は変わらない。
    _apply_post_event(state, is_own_pr)
    statefile.save(state_file, state)
    info(f"✅ 状態を初期化しました: {state_file}")
    info(f"   ホスト: {host}（{detection}）")
    info(f"   提案・レビュー: {' / '.join(runtimes)}")
    info(f"   適用の母集合: {' / '.join(impl_capable)}")
    _emit_init(state)


def _emit_init(state: dict[str, Any]) -> None:
    statefile.emit(
        ID=state["id"],
        REPO=state["repo"],
        HOST=state["host"],
        RUNTIMES=" ".join(state["runtimes"]),
        RUNTIMES_CSV=",".join(state["runtimes"]),
        IMPL_POOL=" ".join(state["impl_capable"]),
        WORKTREE_ROOT=state["worktree_root"],
        WORK=state["worktrees"]["work"],
        TMP_DIR=state["tmp_dir"],
        HEAD_BRANCH=state["head_branch"],
        BASE_BRANCH=state["base_branch"],
        SCOPE=" ".join(state["target_scope"]),
    )


def _ensure_work_worktree(work: pathlib.Path, head_branch: str) -> None:
    """書き込み用の作業ディレクトリを冪等に用意する。

    ここだけが**唯一の非 detach**（Pull Request の head ブランチを checkout する）。
    読み取り用は `prepare-worktrees.sh` が `--detach` で作る。同一ブランチを
    2 つの作業ディレクトリへ checkout できないという git の制約があるためである。
    """
    if work.exists():
        if _is_registered_worktree(work):
            _sync_work_worktree(work, head_branch)
            return
        stale = work.with_name(f"work.stale-{time.strftime('%Y%m%d%H%M%S')}")
        work.rename(stale)
        info(f"⚠ 現リポジトリの作業ディレクトリではないため退避しました: {stale}")
    work.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "worktree", "prune"], capture_output=True, text=True)
    _sh(["git", "fetch", "origin", head_branch])
    # ローカルに head ブランチがあるかどうかで作り方が変わる。無い状態で
    # `worktree add <path> <branch>` を叩くと「そんなブランチは無い」で失敗する。
    exists = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{head_branch}"],
        capture_output=True, text=True,
    ).returncode == 0
    if exists:
        _sh(["git", "worktree", "add", str(work), head_branch])
    else:
        _sh(["git", "worktree", "add", "-b", head_branch, str(work),
             f"origin/{head_branch}"])
    info(f"✅ 書き込み用の作業ディレクトリを作成しました: {work}")


def _sync_work_worktree(work: pathlib.Path, head_branch: str) -> None:
    """既存の書き込み用作業ディレクトリを origin の head へ追いつかせる。

    再開までに Pull Request の head が進んでいることがある。同期せずに使うと、
    **古い HEAD に対して提案・適用**してしまう。早送りできない（履歴が分かれた）
    ときは、どちらが正しいかを機械が決められないので中断する。
    """
    fetched = subprocess.run(
        ["git", "fetch", "origin", head_branch],
        cwd=str(work), capture_output=True, text=True,
    )
    if fetched.returncode != 0:
        # 取得できないまま古い `origin/<head>` へ早送りすると、同期したつもりで
        # **古い HEAD のまま**進んでしまう。通信・認証の失敗はここで止める。
        die(
            f"origin/{head_branch} を取得できませんでした: "
            f"{fetched.stderr.strip()[:300]}。"
            "古い HEAD のまま進めないため中断します"
        )
    r = subprocess.run(
        ["git", "merge", "--ff-only", f"origin/{head_branch}"],
        cwd=str(work), capture_output=True, text=True,
    )
    if r.returncode != 0:
        die(
            f"作業ディレクトリを origin/{head_branch} へ早送りできませんでした: "
            f"{r.stderr.strip()[:300]}。"
            "履歴が分かれています。内容を確認してから再実行してください"
        )
    info(f"↻ 作業ディレクトリを origin/{head_branch} へ同期しました: {work}")


def _is_registered_worktree(path: pathlib.Path) -> bool:
    out = _sh(["git", "worktree", "list", "--porcelain"], check=False)
    target = str(path.resolve())
    return any(line == f"worktree {target}" for line in out.splitlines())


def _run_baseline_test(
    command: str, work: pathlib.Path, timeout: int = DEFAULT_TEST_TIMEOUT
) -> dict[str, Any]:
    """着手前のテストを実行して記録する。

    失敗している状態で構造改善に入ると、**壊したのか元から壊れていたのか**
    区別できない。そもそも振る舞いが変わっていないことを示す手段が無い書き換えは
    構造改善ではないため、テストコマンドは必須にしている。
    """
    code, timed_out = _run_with_timeout(command, str(work), timeout)
    if timed_out:
        die(
            f"着手前のテストが {timeout} 秒で終わりませんでした（{command}）。"
            "打ち切りました"
        )
        raise SystemExit(1)
    status = "green" if code == 0 else "red"
    if status == "red":
        die(
            f"着手前のテストが失敗しています（{command}）。"
            "先に直してから開始してください"
        )
    info(f"✅ 着手前のテスト成功: {command}")
    return {"command": command, "status": status, "checked_at": statefile.now()}


def cmd_start_round(args: argparse.Namespace) -> None:
    """Step 2 — 提案ラウンドを開き、実装担当とレビュー担当を返す。

    終了コード: 0 = ラウンドを開いた / 1 = 提案ラウンドの繰り返しが終了済み。

    **再開しても担当は変わらない。** 同じラウンド番号を開き直したときは記録済みの
    割り当てをそのまま返す。
    """
    path, state = _load(args.id)
    if state.get("final"):
        info(f"提案ラウンドの繰り返しは終了しています（{state['final']}）")
        sys.exit(1)

    rounds = state["rounds"]
    if len(rounds) >= state["max_outer_rounds"]:
        _finish(path, state, "max_outer_rounds")
        sys.exit(1)

    round_no = len(rounds) + 1
    existing = next((r for r in rounds if r["round"] == round_no), None)
    if existing is None:
        impl, reviewers = assignment.assign(round_no, state["host"])
        models = state["models"]
        existing = {
            "round": round_no,
            "started_at": statefile.now(),
            "impl": impl,
            "impl_model": {"requested": models.get(impl), "observed": None},
            "reviewers": reviewers,
            "reviewer_models": {
                r: {"requested": models.get(r), "observed": None} for r in reviewers
            },
            "proposed": {},
            "merged": 0, "adopted": 0, "deferred": 0,
            "items": [],
            "apply": {"applied": [], "failed": [], "base_sha": None, "head_sha": None},
            "fix_rounds": 0,
            "durations": {},
            "reviews": [],
        }
        rounds.append(existing)
        state["outer_round"] = round_no
        state["phase"] = "propose"
        statefile.save(path, state)

    info(
        f"=== 提案ラウンド {round_no} / {state['max_outer_rounds']} "
        f"（実装 {existing['impl']} / レビュー {' + '.join(existing['reviewers'])}）==="
    )
    statefile.emit(
        ROUND=round_no,
        IMPL=existing["impl"],
        IMPL_MODEL=existing["impl_model"]["requested"],
        REVIEWERS=" ".join(existing["reviewers"]),
        REVIEWERS_CSV=",".join(existing["reviewers"]),
        MAX_FIX_ROUNDS=state["max_fix_rounds"],
    )


def _load_runtime_proposals(
    state: dict[str, Any], entry: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    """各ランタイムの提案結果ファイルを読み込み、JSON を解析する。

    1 者が結果ファイルを欠かした・壊れた JSON を返した場合も、その者の提案を
    無かったものとして扱い、全体の統合は続ける。
    """
    proposals: dict[str, list[dict[str, Any]]] = {}
    for runtime in state["runtimes"]:
        result = _result_path(
            state, runtime,
            stem_for(runtime, "propose", state["id"], entry["round"]),
        )
        if not result.exists():
            info(f"⚠ {runtime} の提案結果がありません: {result}")
            continue
        try:
            payload = json.loads(result.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            info(f"⚠ {runtime} の提案結果が JSON として読めません: {e}")
            continue
        if not isinstance(payload, dict):
            # 配列や数値のまま `payload.get(...)` を呼ぶと落ちる。
            # 提案は無かったものとして続ける（1 者の不調で全体を止めない）。
            info(
                f"⚠ {runtime} の提案結果が JSON オブジェクトではありません"
                f"（{type(payload).__name__}）。提案なしとして扱います"
            )
            proposals[runtime] = []
            entry["proposed"][runtime] = 0
            continue
        items = payload.get("items")
        proposals[runtime] = [i for i in items if isinstance(i, dict)] \
            if isinstance(items, list) else []
        entry["proposed"][runtime] = len(proposals[runtime])
    return proposals


def _update_state_from_merged_proposals(
    path: pathlib.Path,
    state: dict[str, Any],
    entry: dict[str, Any],
    adopted: list[dict[str, Any]],
    deferred: list[dict[str, Any]],
) -> None:
    """統合済みの提案から状態オブジェクトを更新し、次回ラウンドの起点を準備する。"""
    # 収束判定に使う「前ラウンドとの重複率」。見送りも含めた提案全体で測る。
    current_keys = [(i["path"], i["symbol"], i["smell"]) for i in adopted + deferred]
    entry["proposal_keys"] = [list(k) for k in current_keys]
    entry["merged"] = len(current_keys)
    entry["adopted"] = len(adopted)
    entry["deferred"] = len(deferred)

    round_no = entry["round"]
    for n, item in enumerate(adopted, start=1):
        item_id = f"R{round_no}-{n:03d}"
        state["items"].append({
            "item_id": item_id,
            "round": round_no,
            **item,
            "status": "pending",
            "commits": [],
        })
        entry["items"].append(item_id)
    for item in deferred:
        state["deferred_items"].append({**item, "round": round_no})

    # 適用の起点は**オーケストレータ側で**確定させる。実装担当の申告に委ねると、
    # 欠落・不正時に範囲検査が無効になり、過去の任意のコミットが実在扱いになる。
    # 提案は読むだけなので、この時点の HEAD が着手前の状態である。
    entry["apply_base_sha"] = _git_out(state["worktrees"]["work"], ["rev-parse", "HEAD"])

    state["phase"] = "apply" if adopted else "converged"
    if not adopted:
        # 呼び出し側は終了コード 2 で繰り返しを抜けるため、`advance` を通らない。
        # 終了理由をここで確定させないと、報告が「未終了」のままになる。
        state["final"] = "no_more_proposals"
        state["ended_at"] = statefile.now()
    statefile.save(path, state)


def cmd_merge_proposals(args: argparse.Namespace) -> None:
    """Step 3 — 提案をマージして改善項目を作る。

    終了コード: 0 = 採用あり / 2 = 採用 0 件（提案ラウンドの繰り返しを終える）。

    **同じラウンドで叩き直しても二重に項目を作らない。** 進行を止めても再開できる
    ことが前提なので、統合済みなら前回と同じ結果をそのまま返す。
    """
    path, state = _load(args.id)
    entry = _current_round(state)

    if entry.get("proposal_keys") is not None:
        info(
            f"↻ 提案ラウンド {entry['round']} は統合済みです"
            f"（採用 {entry.get('adopted', 0)} 件 / 見送り {entry.get('deferred', 0)} 件）"
        )
        for item_id in entry.get("items", []):
            item = _find_item(state, item_id, required=False)
            if item is not None:
                info(f"  {item_id} [{item['severity']}] {item['path']}#{item['symbol']}")
        if not entry.get("adopted"):
            sys.exit(2)
        return

    proposals = _load_runtime_proposals(state, entry)

    excluded = {
        (d["path"], d["symbol"], d["smell"]) for d in state["deferred_items"]
    }
    adopted, deferred = merge_proposals(
        proposals,
        threshold=state["severity_threshold"],
        max_items=state["max_items_per_round"],
        excluded_keys=excluded,
    )

    _update_state_from_merged_proposals(path, state, entry, adopted, deferred)
    info(
        f"提案 {sum(entry['proposed'].values())} 件 → 統合 {entry['merged']} 件 → "
        f"採用 {entry['adopted']} 件 / 見送り {entry['deferred']} 件"
    )
    for item_id in entry["items"]:
        item = _find_item(state, item_id)
        info(
            f"  {item_id} [{item['severity']}] {item['path']}#{item['symbol']} "
            f"{item['smell']} → {item['technique']} "
            f"(合意 {len(item['proposed_by'])} / 見積 {item['estimated_diff_lines']} 行)"
        )
    if not adopted:
        info("採用 0 件のため、提案ラウンドの繰り返しを終えます")
        sys.exit(2)


def cmd_merge_apply(args: argparse.Namespace) -> None:
    """Step 4 — 適用結果を検証して取り込む。

    終了コード: 0 = 1 件以上成功 / 2 = 全件失敗（次の提案ラウンドへ進む）。

    **1 件の失敗でラウンドを止めない。** 失敗した項目だけを見送りにして、
    残りは採用する。
    """
    path, state = _load(args.id)
    entry = _round(state, args.round)
    if not args.dry_run:
        _discard_impl_leftovers(state, state["worktrees"]["work"])
        _resume_incomplete_apply(path, state, entry)

    # **叩き直しても同じ判定を返す。** 取り込み済みで再実行すると、前回作った
    # 取り消しコミットが「未割当」と判定され、成功した項目まで巻き込んで
    # ラウンド全体を取り消してしまう。
    if (entry.get("apply") or {}).get("merged_at"):
        applied_before = entry["apply"].get("applied") or []
        info(
            f"↻ ラウンド {args.round} の適用は取り込み済みです"
            f"（採用 {len(applied_before)} 件 / 失敗 "
            f"{len(entry['apply'].get('failed') or [])} 件）"
        )
        if not applied_before:
            sys.exit(2)
        return

    payload, work, head_branch, test_command, head_sha, ordered_range, in_range = (
        _load_apply_context(path, state, entry, args)
    )

    reported, unknown_ids = _collect_apply_reports(payload, entry)

    _validate_apply_commit_ownership(
        path, state, entry, args, reported, unknown_ids, work,
        ordered_range, in_range, head_sha,
    )

    applied, failed = _verify_apply_items(
        path, state, entry, args, reported, work, in_range, test_command, head_branch,
    )

    entry["apply"] = {
        "applied": applied,
        "failed": failed,
        # 起点はオーケストレータが記録したもの。申告は記録にも残さない。
        "base_sha": entry.get("apply_base_sha"),
        "head_sha": head_sha,
        # **取り込み済みの印は最後に立てる。** 取り消しより先に立てると、取り消しに
        # 失敗して中断したときに、次の実行が処理済みガードで素通りしてしまい、
        # 検証を通っていない変更が Pull Request に残り続ける。
        "merged_at": None,
    }
    entry.setdefault("durations", {})["apply"] = _safe_int(
        payload.get("elapsed_seconds")
    )
    state["phase"] = "review" if applied else "propose"

    # `--dry-run` では git も状態ファイルも触らない。片方だけ進むと、確認の
    # つもりで実行した利用者の進行が壊れる。
    if args.dry_run:
        if failed:
            _drop_items(state, entry, failed, dry_run=True)
        info("（dry-run）状態ファイルは更新していません")
        applied = list(entry["apply"]["applied"])
    elif failed:
        # `merged_at` は `_apply_drop` が取り消しの完了時点で立てる。
        applied = _apply_drop(path, state, entry, failed)
    else:
        # **全項目が通ったときも進行側が公開する。** 実装担当は push しないため、
        # ここで公開しないとレビュー担当が Pull Request 上の差分へ指摘を書けない。
        entry["apply"]["merged_at"] = statefile.now()
        entry["pending_push"] = True
        statefile.save(path, state)
        _push_head(state)
        entry["pending_push"] = False
        statefile.save(path, state)

    if not applied:
        info("全項目が失敗したため、このラウンドのレビューは行いません")
        sys.exit(2)


def _load_apply_context(
    path: pathlib.Path,
    state: dict[str, Any],
    entry: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[dict[str, Any], pathlib.Path, str, str, str, list[str], set[str]]:
    impl = entry["impl"]
    result = _result_path(state, impl, stem_for(impl, "apply", state["id"], args.round))
    payload = _read_result(result, impl)

    _record_observed_model(entry, "impl", impl, state, "apply", args.round)

    # 着手前のテストが**成功と確認できていない限り**適用結果を採らない。
    # `red` だけでなく `unknown`（確認していない）も拒否する。確認していない状態を
    # 通すと、「壊したのか元から壊れていたのか」を判別する手段が無いまま進む。
    baseline = state.get("baseline_test") or {}
    if baseline.get("status") != "green":
        for item_id in entry["items"]:
            _find_item(state, item_id)["status"] = "blocked"
        if not args.dry_run:
            statefile.save(path, state)
        die(
            f"着手前のテストが成功と確認できていません（status={baseline.get('status')}）。"
            "適用へ着手しません（全項目を blocked）",
            code=2,
        )

    # 検証の材料は git から取る。結果ファイルから使うのは
    # 「どのコミットがどの項目のものか」という対応付けだけ。
    work = state["worktrees"]["work"]
    head_branch = state["head_branch"]
    test_command = baseline["command"]
    head_sha = _git_out(work, ["rev-parse", "HEAD"]) or ""
    # 起点は `merge-proposals` が記録したもの。**実装担当の申告は使わない。**
    ordered_range = commits_in_range(work, entry.get("apply_base_sha"), head_sha)
    in_range = set(ordered_range or [])
    if ordered_range is None:
        # 範囲を確定できないなら、何も検証できない。素通しにせず失敗させる。
        for item_id in entry["items"]:
            _find_item(state, item_id)["status"] = "blocked"
        if not args.dry_run:
            statefile.save(path, state)
        die(
            "適用の範囲を確定できませんでした"
            f"（起点 {entry.get('apply_base_sha')} / HEAD {head_sha}）。"
            "検証できない適用は採りません",
            code=2,
        )
    return payload, work, head_branch, test_command, head_sha, ordered_range, in_range


def _collect_apply_reports(
    payload: dict[str, Any],
    entry: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    # 申告は**このラウンドの改善項目のものだけ**を採る。架空の項目 ID へ割り当てられた
    # コミットを数に入れると、割り当て済みに見えるのに項目別の検証にも入らず、
    # そのまま Pull Request に残せてしまう。
    round_items = set(entry["items"])
    reported: dict[str, dict[str, Any]] = {}
    unknown_ids: list[str] = []
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        info(f"⚠ 適用結果の items が配列ではありません（{type(raw_items).__name__}）")
        raw_items = []
    for r in raw_items:
        if not isinstance(r, dict):
            continue
        item_id = r.get("item_id")
        if item_id in round_items:
            reported[item_id] = r
        elif item_id is not None:
            unknown_ids.append(str(item_id))
    return reported, unknown_ids


def _detect_commit_owners(
    work: pathlib.Path, reported: dict[str, dict[str, Any]]
) -> tuple[dict[str, str], list[str]]:
    """申告コミットを完全 SHA へ正規化し、所有項目と重複申告を特定する。

    **1 コミットの所有項目は 1 つだけ。** 同じコミットを 2 つの項目が申告すると、
    片方が失敗して取り消したときに、もう片方は成功のまま残る。状態ファイルと
    実際の差分が食い違い、どちらが正しいか決められなくなる。

    判定は**完全な SHA へ正規化してから**行う。申告の文字列をそのまま鍵にすると、
    一方が完全 SHA、他方が短縮 SHA で同じコミットを指したときに重複を見逃す。
    """
    owner_of: dict[str, str] = {}
    duplicated: list[str] = []
    for item_id, r in reported.items():
        for sha in _reported_shas(r):
            full = _git_out(work, ["rev-parse", "--verify", f"{sha}^{{commit}}"])
            if full is None:
                continue          # 実在しない申告は項目ごとの検証で落ちる
            if full in owner_of and owner_of[full] != item_id:
                duplicated.append(full)
            owner_of.setdefault(full, item_id)
    return owner_of, duplicated


def _build_ownership_error_reason(
    unassigned: list[str], unknown_ids: list[str], duplicated: list[str]
) -> str:
    """所有権検査の失敗理由を組み立てる。"""
    causes = []
    if unassigned:
        causes.append(
            f"どの改善項目にも割り当てられていないコミットが {len(unassigned)} 件"
            f"（{', '.join(s[:7] for s in unassigned[:5])}）"
        )
    if unknown_ids:
        causes.append(
            f"このラウンドに無い改善項目 ID の申告"
            f"（{', '.join(unknown_ids[:5])}）"
        )
    if duplicated:
        causes.append(
            f"複数の項目が同じコミットを申告しています"
            f"（{', '.join(s[:7] for s in duplicated[:5])}）"
        )
    return (
        "、".join(causes)
        + "。検証を回避した変更や、状態と実差分の食い違いを Pull Request に"
          "残さないため、ラウンドごと取り消します"
    )


def _revert_unverified_apply_round(
    path: pathlib.Path,
    state: dict[str, Any],
    entry: dict[str, Any],
    args: argparse.Namespace,
    work: pathlib.Path,
    ordered_range: list[str],
    unassigned: list[str],
    unknown_ids: list[str],
    duplicated: list[str],
    head_sha: str,
) -> None:
    """検証を通らない適用ラウンドの範囲を取り消し、状態と公開を反映する。"""
    # 範囲全体を取り消す。どのコミットが安全かを決められない以上、
    # 起点まで戻すのが最も確実である。順序は `_revert_item_commits` が
    # git の履歴から決め直す。
    whole_round = {
        "item_id": f"R{entry['round']}-range",
        "commits": list(ordered_range),
    }
    if not args.dry_run:
        # **取り消しへ着手する前に印を立てる。** 取り消しは済んだのに push
        # できずに終わると、未検証の変更が Pull Request に残ったままになる。
        entry["pending_push"] = True
        statefile.save(path, state)
    _revert_item_commits(state, whole_round, args.dry_run)
    if not args.dry_run:
        # 取り消し後の状態を新しい起点にする。叩き直しても範囲が空になり、
        # 取り消しコミット自体を「未割当」として再び戻すことがない。
        entry["apply_base_sha"] = _git_out(work, ["rev-parse", "HEAD"])
    entry["apply"] = {
        "applied": [], "failed": list(entry["items"]),
        "base_sha": entry.get("apply_base_sha"), "head_sha": head_sha,
        "unassigned_commits": unassigned,
        "unknown_item_ids": unknown_ids,
        "duplicated_commits": duplicated,
        "merged_at": statefile.now(),
    }
    state["phase"] = "propose"
    if args.dry_run:
        info("（dry-run）状態ファイルは更新していません")
    else:
        # 項目別の失敗と同じく、**ここで取り消した項目も「対象外」に残す**。
        # 残さないと同じ提案が次のラウンドで再び採用される。
        _defer_abandoned_items(state, entry)
        statefile.save(path, state)
        _push_head(state)
        entry["pending_push"] = False
        statefile.save(path, state)


def _validate_apply_commit_ownership(
    path: pathlib.Path,
    state: dict[str, Any],
    entry: dict[str, Any],
    args: argparse.Namespace,
    reported: dict[str, dict[str, Any]],
    unknown_ids: list[str],
    work: pathlib.Path,
    ordered_range: list[str],
    in_range: set[str],
    head_sha: str,
) -> None:
    # **範囲のコミットは全て、いずれかの改善項目に割り当てられていること。**
    # 申告から漏れたコミットはテストもトレーラーも差分予算も検査されず、そのまま
    # Pull Request に残る。都合の悪い変更を申告しないだけで検査を回避できてしまう。
    owner_of, duplicated = _detect_commit_owners(work, reported)

    unassigned = sorted(in_range - set(owner_of))
    if not (unassigned or unknown_ids or duplicated):
        return

    reason = _build_ownership_error_reason(unassigned, unknown_ids, duplicated)
    info(f"❌ {reason}")
    for item_id in entry["items"]:
        it = _find_item(state, item_id)
        it["status"] = "abandoned"
        it["failure_reason"] = reason
    _revert_unverified_apply_round(
        path, state, entry, args, work, ordered_range,
        unassigned, unknown_ids, duplicated, head_sha,
    )
    sys.exit(2)


def _verify_apply_items(
    path: pathlib.Path,
    state: dict[str, Any],
    entry: dict[str, Any],
    args: argparse.Namespace,
    reported: dict[str, dict[str, Any]],
    work: pathlib.Path,
    in_range: set[str],
    test_command: str,
    head_branch: str,
) -> tuple[list[str], list[str]]:
    applied: list[str] = []
    failed: list[str] = []
    scope = state.get("target_scope") or []
    # **判定はその都度残す。** まとめて最後に保存すると、取り消しの途中で中断した
    # ときに適用の記録が一切残らず、どのコミットが検証を通ったのかを状態から
    # 復元できなくなる。再開可能性は収束ループの前提なので、ここが崩れると
    # 中断からの復帰手段が無くなる。
    progress: list[dict[str, Any]] = []
    entry["apply_progress"] = progress
    for item_id in entry["items"]:
        item = _find_item(state, item_id)
        got = reported.get(item_id)
        if got is None:
            problem = "適用結果に項目がありません"
            facts: list[dict[str, Any]] = []
        else:
            facts = collect_commit_facts(
                work, _reported_shas(got), in_range, test_command, head_branch,
                _safe_int(state.get("test_timeout"), DEFAULT_TEST_TIMEOUT),
            )
            problem = verify_apply_item(item, facts, scope)
        if problem:
            item["status"] = "abandoned"
            item["failure_reason"] = problem
            item["test_failed"] = bool(got and "テストが成功していません" in problem)
            item["budget_exceeded"] = bool(got and "差分予算" in problem)
            item["out_of_scope"] = bool(got and "対象範囲の外" in problem)
            # 取り消しは全項目の判定が出そろってから**まとめて**行う。項目ごとに
            # その場で戻すと、まだ判定していない項目のコミットと競合する。
            item["commits"] = _reported_shas(got)
            failed.append(item_id)
            info(f"❌ {item_id}: {problem}")
        else:
            item["status"] = "reviewing"
            item["commits"] = _reported_shas(got)
            item["diff_lines"] = sum(_safe_int(c.get("diff_lines")) for c in facts)
            applied.append(item_id)
            info(f"✅ {item_id}: {len(item['commits'])} コミット / {item['diff_lines']} 行")
        progress.append({
            "item_id": item_id, "at": statefile.now(),
            "result": "failed" if problem else "ok",
            "reason": problem, "commits": list(item.get("commits") or []),
        })
        if not args.dry_run:
            statefile.save(path, state)
    return applied, failed


def _defer_abandoned_items(state: dict[str, Any], entry: dict[str, Any]) -> None:
    """このラウンドで取り消した項目を「対象外」として記録する。

    記録しないと、**同じ提案が次のラウンドで再び採用され、同じ理由で失敗する**。
    実測では適用で失敗した項目が 3 ランタイム全員から再提案され、合意数が最大に
    なって最優先で採用された。手順書が「同じ提案が毎ラウンド出続けて収束しない」
    として禁じている状態そのものである。

    除外の鍵は `path` + `symbol` + `smell` なので、その 3 つを必ず残す。
    """
    already = {d.get("item_id") for d in state["deferred_items"]}
    for item_id in entry["items"]:
        item = _find_item(state, item_id, required=False)
        if item is None or item.get("status") != "abandoned" or item_id in already:
            continue
        state["deferred_items"].append({
            "item_id": item_id,
            "path": item["path"], "symbol": item["symbol"], "smell": item["smell"],
            "round": entry["round"],
            "defer_reason": item.get("failure_reason") or "適用結果の検証を通らなかった",
        })


def _run_drop(
    path: pathlib.Path, state: dict[str, Any], entry: dict[str, Any],
    targets: list[str],
) -> dict[str, Any]:
    """取り消しを、中断しても再開できる形で実行する。

    `pending_drop` と `pending_push` を立ててから入り、**戻ったらすぐ保存する**。
    保存しないまま落ちると、積み直しで変わった SHA と取り消し済みの印が失われ、
    次の実行は**履歴に無い SHA を相手に**取り消しをやり直すことになる。

    印はここでは消さない。**呼び出し側が完了の記録と同じ保存で消す。** 先に消すと、
    完了を記録する前に落ちたときに、次の実行が「取り消し済みだが未完了」の状態を
    見分けられなくなる。
    """
    entry["pending_drop"] = list(targets)
    entry["pending_push"] = True
    statefile.save(path, state)
    result = _drop_items(state, entry, list(targets))
    statefile.save(path, state)
    return result


def _apply_drop(
    path: pathlib.Path, state: dict[str, Any], entry: dict[str, Any],
    failed: list[str],
) -> list[str]:
    """検証に失敗した項目を取り消し、採用として残る項目 ID を返す。

    **中断しても再開できる形で記録する。** 失敗の位置で必要な再開が変わるため、
    印は次の順で切り替える。

    | 中断した位置 | 残る印 | 次の実行がすること |
    | --- | --- | --- |
    | 取り消しの途中 | `pending_drop` あり / `merged_at` なし | 取り消しをやり直す |
    | 取り消し後・push 前 | `pending_drop` なし / `merged_at` あり / `pending_push` あり | **push の再送だけ** |

    取り消しより先に `merged_at` を立てると、取り消しに失敗したときに次の実行が
    処理済みガードで素通りし、**再試行できない**。逆に push まで終えるまで
    `merged_at` を立てないと、push だけ失敗したときに次の実行が適用の検証をやり直し、
    取り消しと積み直しのコミットを「未割当」と判定してラウンドごと巻き込む。
    """
    work = state["worktrees"]["work"]
    result = _run_drop(path, state, entry, failed)
    applied = list(entry["apply"].get("applied") or [])
    if result["mode"] == "round":
        # 積み直せなかった。合意済みの項目も含めて全件捨てる。
        for item_id in entry["items"]:
            it = _find_item(state, item_id)
            it["status"] = "abandoned"
            it.setdefault(
                "failure_reason",
                "残す項目を積み直せなかったため、ラウンドごと取り消した",
            )
        applied = []
        entry["apply"]["applied"] = []
        entry["apply"]["failed"] = list(entry["items"])
        # 取り消し後の状態を新しい起点にする（叩き直しでの二重取り消しを防ぐ）。
        entry["apply_base_sha"] = _git_out(work, ["rev-parse", "HEAD"])
        state["phase"] = "propose"

    # 取り消した項目は「対象外」として残す。次のラウンドで同じ提案が採用され、
    # 同じ理由で失敗するのを防ぐ。
    _defer_abandoned_items(state, entry)
    # **取り消しが済んだことを push より先に、印の解除と同じ保存で永続化する。**
    # 保存せずに push して失敗すると、次の実行が適用の検証をやり直し、取り消しと
    # 積み直しのコミットを「未割当」と判定してラウンドごと巻き込んでしまう。
    # `pending_push` は残るので、次の実行は push の再送だけを行う。
    entry["pending_drop"] = []
    entry["apply"]["merged_at"] = statefile.now()
    statefile.save(path, state)
    _push_head(state)
    entry["pending_push"] = False
    statefile.save(path, state)
    return applied


def _resume_incomplete_apply(
    path: pathlib.Path, state: dict[str, Any], entry: dict[str, Any]
) -> None:
    """前回終わらなかった取り消しと push を、処理済みの判定より**先に**片づける。

    取り消しをやり残したまま push だけ先に流すと、検証を通っていない HEAD が
    Pull Request へ反映されてしまう。**取り消しの再実行を先に行う。**
    """
    if entry.get("pending_drop"):
        info("↻ 前回終わらなかった取り消しを再実行します")
        _apply_drop(path, state, entry, list(entry["pending_drop"]))
        return
    _flush_pending_push(path, state, entry)


def _prepare_fix_phase(state: dict[str, Any], entry: dict[str, Any]) -> None:
    """変更要求を返す前に、修正フェーズが要る記録を残す。

    **変更要求の出口は 2 つある**（通常の判定と、差し戻し上限からの落ちこみ）。
    どちらも同じ記録が要るため、書き漏らしが起きないよう 1 箇所へ集める。

    - `fix_base_sha`: 修正の範囲の起点。無いと `merge-fix` が範囲を確定できず、
      `fix_rounds` が進まないまま修正と再レビューを往復し続ける
    - `fix_attempts`: 試行番号。`merge-fix` が「叩き直し」と「次のラウンド」を
      区別するのに使う
    """
    entry["fix_base_sha"] = _git_out(state["worktrees"]["work"], ["rev-parse", "HEAD"])
    entry["fix_attempts"] = entry.get("fix_attempts", 0) + 1


def cmd_review_targets(args: argparse.Namespace) -> None:
    """Step 5 — 次に起動するレビュー担当を返す。

    **初回と再レビューの区別は状態が持つ。** 呼び出し側は同じコマンドを 2 回呼ぶだけで、
    どちらかを引数で伝えない。ラウンドの記録に `fix_reviewers` があれば再レビュー、
    無ければ初回である。**このキーを持たない既存の状態ファイルは初回として読む。**

    差し戻し（`invalid`）はこのキーを書かないため、2 者へ戻る。結果の形が判定に使えない
    状態は修正の成否とは別で、承認した担当の結果も読めていない可能性がある。

    終了コード: 0 = 対象を返した / 4（`ABORT`）= ラウンドが無い、または対象が 0 人。
    """
    _, state = _load(args.id)
    entry = _round(state, args.round)
    targets = entry.get("fix_reviewers")
    if targets is None:
        targets = entry["reviewers"]
    if not targets:
        die(
            f"ラウンド {args.round} の再レビューの対象が 0 人です。"
            "判定できない状態のまま進めません"
        )
    print(f"REVIEW_TARGETS='{' '.join(targets)}'")
    print(f"REVIEW_TARGETS_CSV={','.join(targets)}")


def cmd_judge_review(args: argparse.Namespace) -> None:
    """Step 5 — レビュー担当の判定を取り込む。

    終了コード: 0 = 2 者とも承認 / 2 = 修正へ / 3 = 差し戻して再レビュー。
    """
    path, state = _load(args.id)
    entry = _round(state, args.round)
    reviewers = entry["reviewers"]

    reviews: dict[str, dict[str, Any]] = {}
    # 鍵には**修正の世代**を含める。1 回修正したあとに同じ指摘文が返ってくることは
    # 普通にあり、内容だけで見ると「叩き直し」と区別できず、起点も試行番号も
    # 更新されないまま止まってしまう。
    digest = hashlib.sha256(f"fix{entry.get('fix_rounds', 0)}:".encode("ascii"))
    for name in reviewers:
        result = _result_path(state, name, stem_for(name, "review", state["id"], args.round))
        digest.update(name.encode("utf-8"))
        if not result.exists():
            info(f"⚠ {name} のレビュー結果がありません: {result}")
            continue
        digest.update(result.read_bytes())
        try:
            reviews[name] = json.loads(result.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            info(f"⚠ {name} のレビュー結果が JSON として読めません: {e}")
        _record_observed_model(entry, "reviewer", name, state, "review", args.round)

    # **投稿の確認は結果ファイルの内容では決まらない。** GitHub 側の状態なので、
    # 鍵に入れずに判定を再生すると、投稿が見えるようになった後で叩き直しても
    # 差し戻しを返し続け、進行が止まる。確認の結果まで同じときだけ再生する。
    unposted, post_problems = _unposted_reviewers(state, reviews, reviewers)
    digest.update(("unposted:" + ",".join(sorted(unposted))).encode("utf-8"))

    # **同じレビュー結果で叩き直しても、記録も起点も試行番号も動かさない。**
    # 動かすと、同じ修正結果を別の試行として再処理したり、修正コミットを検証範囲の
    # 外へ追い出したりできてしまう。前回の終了コードだけを再現する。
    review_key = digest.hexdigest()
    for seen in entry.get("review_merged", []):
        if seen.get("key") == review_key:
            info(f"↻ このレビュー結果は判定済みです（前回の終了コード {seen['exit']}）")
            if seen["exit"]:
                sys.exit(seen["exit"])
            return

    verdict, problems, record = _aggregate_review_results(
        entry, reviewers, reviews, post_problems
    )
    statefile.save(path, state)

    def _remember(exit_code: int) -> None:
        entry.setdefault("review_merged", []).append(
            {"key": review_key, "exit": exit_code}
        )

    _handle_review_verdict(
        path, state, entry, reviewers, reviews, unposted,
        verdict, problems, record, _remember,
    )


def _handle_review_verdict(
    path: pathlib.Path,
    state: dict[str, Any],
    entry: dict[str, Any],
    reviewers: list[str],
    reviews: dict[str, dict[str, Any]],
    unposted: list[str],
    verdict: str,
    problems: list[str],
    record: dict[str, Any],
    remember: Callable[[int], None],
) -> None:
    if verdict == "invalid":
        for p in problems:
            info(f"❌ {p}")
        # **差し戻しは絞り込みを解く。** 変更要求で絞った後に形式の誤りが出た場合、
        # 絞ったままだと差し戻しの再レビューが 1 者だけで行われる。結果の形が判定に
        # 使えない状態は修正の成否とは別である。
        entry.pop("fix_reviewers", None)
        entry["invalid_reviews"] = entry.get("invalid_reviews", 0) + 1
        if entry["invalid_reviews"] > MAX_INVALID_REVIEWS:
            # **結果が無いことと、形が違うことを分ける。** 結果を残さなかったのは
            # レビュー担当のプロセスが仕事をしなかったということで、実装担当が
            # 直せる指摘ではない。変更要求へ落とすと、直しようのない指摘を渡された
            # 実装担当が空回りし、承認済みの項目まで見送りへ進む。
            missing = [name for name in reviewers if name not in reviews]
            # **投稿できなかった担当も同じ扱いにする。** 判定は残っていても
            # Pull Request に指摘が無い以上、実装担当が読めるものは存在しない。
            blocked = missing + [name for name in unposted if name not in missing]
            if blocked:
                remember(ABORT)
                statefile.save(path, state)
                die(
                    f"レビュー担当 {' / '.join(blocked)} が結果を残せませんでした"
                    "（結果ファイルの欠落、または投稿の失敗）。"
                    "実装担当への指摘ではないため、進行を中断します。"
                    "原因を直して同じコマンド列を叩き直せば再開できます"
                )
            # 差し戻しを無限に繰り返さない。形式を満たせないレビューが続く以上、
            # このラウンドの成果は検証されていないものとして扱い、変更要求へ落とす。
            # 紐づけ先が決まらないので、取り消しはラウンド全件が対象になる。
            record["findings"].append({
                "reviewer": "cross-refactoring",
                "item_id": None,
                "thread_id": None,
                "summary": (
                    f"レビュー結果の形式が {MAX_INVALID_REVIEWS + 1} 回続けて不正だった: "
                    + " / ".join(problems)
                ),
                "resolved": False,
            })
            # 絞り込みは上で解いたままにする。合成した指摘は誰が出したものでもなく、
            # 再レビューの対象が決まらない。
            # **この出口も修正フェーズの起点を記録する。** 記録せずに変更要求を
            # 返すと `merge-fix` が範囲を確定できずに弾かれ、`fix_rounds` が
            # 進まない。`should-abandon` は `fix_rounds` で見送りを決めるため、
            # 上限へ永久に到達せず修正と再レビューを往復し続ける。
            _prepare_fix_phase(state, entry)
            remember(2)
            statefile.save(path, state)
            info("差し戻しの上限に達したため、変更要求として扱います")
            sys.exit(2)
        remember(3)
        statefile.save(path, state)
        info("レビュー結果を差し戻します。指摘には必ず改善項目 ID を付けてください")
        sys.exit(3)
    if verdict == "approved":
        for item_id in entry["apply"]["applied"]:
            _find_item(state, item_id)["status"] = "done"
        state["phase"] = "propose"
        remember(0)
        statefile.save(path, state)
        info("✅ レビュー担当 2 者とも承認しました")
        return
    # **再レビューの対象を、変更要求を出した担当だけに絞る。** 差し戻し上限からの
    # 落ちこみでは書かない。合成した指摘は誰が出したものでもなく、対象が決まらない。
    entry["fix_reviewers"] = _requested_changes(reviewers, reviews)
    _prepare_fix_phase(state, entry)
    remember(2)
    statefile.save(path, state)
    open_findings = sum(1 for f in record["findings"] if not f["resolved"])
    info(f"変更要求があります（未解決の指摘 {open_findings} 件）")
    sys.exit(2)


def _aggregate_review_results(
    entry: dict[str, Any],
    reviewers: list[str],
    reviews: dict[str, dict[str, Any]],
    post_problems: list[str],
) -> tuple[str, list[str], dict[str, Any]]:
    verdict, problems = judge(reviews, reviewers, entry["items"])
    if post_problems:
        verdict = "invalid"
        problems = problems + post_problems

    # 記録も**型検査済みの値だけ**で作る。`judge()` が invalid と判定した入力でも
    # ここを通るため、無条件に `.get()` を呼ぶと差し戻す前に落ちる。
    record: dict[str, Any] = {"round": len(entry["reviews"]) + 1, "findings": []}
    for name in reviewers:
        review = reviews.get(name)
        review = review if isinstance(review, dict) else {}
        record[name] = review.get("verdict")
        findings = review.get("findings")
        for finding in findings if isinstance(findings, list) else []:
            if not isinstance(finding, dict):
                continue
            record["findings"].append({
                "reviewer": name,
                "item_id": finding.get("item_id"),
                "thread_id": finding.get("thread_id"),
                "summary": finding.get("summary"),
                "resolved": bool(finding.get("resolved")),
            })
    entry["reviews"].append(record)
    # レビュー担当ごとの所要時間は**別々に**持つ。ラウンドの合計を各担当へ配ると、
    # 2 者分を両方に数えることになり、担当同士の比較が成り立たない。
    per_reviewer = entry.setdefault("reviewer_seconds", {})
    for name in reviewers:
        review = reviews.get(name)
        elapsed = review.get("elapsed_seconds") if isinstance(review, dict) else 0
        per_reviewer[name] = per_reviewer.get(name, 0) + _safe_int(elapsed)
    entry.setdefault("durations", {})["review"] = sum(per_reviewer.values())
    return verdict, problems, record


def cmd_should_abandon(args: argparse.Namespace) -> None:
    """Step 6 — 修正ラウンドの上限に達したか。

    終了コード: 0 = 見送りへ移る / 2 = まだ修正できる。
    """
    _, state = _load(args.id)
    entry = _round(state, args.round)
    limit = state["max_fix_rounds"]
    if entry["fix_rounds"] >= limit:
        info(f"修正ラウンドが上限 {limit} に達しました。未解決の項目を見送ります")
        return
    info(f"修正ラウンド {entry['fix_rounds']} / {limit} — まだ修正します")
    sys.exit(2)


def cmd_abandon_items(args: argparse.Namespace) -> None:
    """Step 6 — 未解決の指摘に紐づく改善項目だけを取り消す。

    **合意済みの項目は Pull Request に残す。** これを可能にするために、適用は
    項目ごとに 1 コミットへまとめ、状態ファイルへコミットを記録している。
    """
    path, state = _load(args.id)
    entry = _round(state, args.round)
    if not args.dry_run:
        # **やり残した取り消しを push の再送より先に片づける。** 先に push すると、
        # 取り消しが途中の HEAD をそのまま Pull Request へ反映してしまう。
        if entry.get("pending_drop"):
            info("↻ 前回終わらなかった取り消しを再実行します")
            _run_drop(path, state, entry, list(entry["pending_drop"]))
        else:
            _flush_pending_push(path, state, entry)

    # 取り消し自体は `reverted` で冪等だが、見送りの記録は重複しうる。
    if entry.get("abandoned") is not None:
        info(f"↻ ラウンド {args.round} の見送りは処理済みです"
             f"（{len(entry['abandoned'])} 件）")
        return

    targets, whole_round = unresolved_item_ids(entry["reviews"], entry["apply"]["applied"])
    if whole_round:
        info(
            "どの項目にも紐づかない未解決の指摘があるため、"
            "このラウンドで適用した項目を全件取り消します"
        )
    if not targets:
        info("取り消す項目はありません")
        if not args.dry_run:
            entry["abandoned"] = []
            statefile.save(path, state)
        return

    if args.dry_run:
        _drop_items(state, entry, targets, dry_run=True)
        info("（dry-run）状態ファイルは更新していません")
        return

    result = _run_drop(path, state, entry, targets)
    if result["mode"] == "round":
        info("積み直せなかったため、このラウンドで適用した項目を全件見送ります")
        targets = list(entry["apply"].get("applied") or targets)

    already = {d.get("item_id") for d in state["deferred_items"]}
    for item_id in targets:
        item = _find_item(state, item_id)
        item["status"] = "abandoned"
        item.setdefault("failure_reason", "修正ラウンドの上限に達しても指摘が解決しなかった")
        if item_id in already:
            continue
        state["deferred_items"].append({
            "item_id": item_id,
            "path": item["path"], "symbol": item["symbol"], "smell": item["smell"],
            "round": entry["round"],
            "defer_reason": item["failure_reason"],
        })
        info(f"↩ {item_id} を見送りました")

    # 見送りの記録と印の解除を**同じ保存で**行う。保存してから push するので、
    # push が失敗しても記録とローカルの git が食い違わない。
    entry["abandoned"] = targets
    entry["pending_drop"] = []
    state["phase"] = "propose"
    statefile.save(path, state)
    _push_head(state)
    entry["pending_push"] = False
    statefile.save(path, state)


def _fix_merge_key(entry: dict[str, Any], result: pathlib.Path) -> str:
    """修正結果の取り込み済み判定に使う鍵を作る。

    **叩き直しても二重に取り込まない。** 修正は同じラウンドで何度も回るため、
    「このラウンドで処理済みか」では判定できない。**入力が前回と同じか**で見る。
    次の修正ラウンドでは結果ファイルが上書きされ、HEAD も進むので鍵が変わる。
    鍵は**試行番号と結果ファイルの内容**から作る。

    - HEAD は混ぜない。検証に失敗して取り消すと HEAD が変わるため、鍵が一致せず
      同じ申告を再処理してしまう。
    - 内容だけでも足りない。次の修正ラウンドが同じ JSON（コミットなし・同じ
      未解決 ID など）を返すと過去のラウンドと衝突し、`fix_rounds` が進まないまま
      同じ修正を起動し続ける。
    - ファイルの更新時刻も使わない。粒度が環境によって違い、書き直しても同じ値に
      なりうる。

    修正の前には必ず `judge-review` が走るので、そこで進めた試行番号が
    **実行単位の識別子**になる。叩き直しただけなら番号は変わらない。
    """
    attempt = entry.get("fix_attempts", 0)
    return f"{attempt}:" + hashlib.sha256(result.read_bytes()).hexdigest()


def _already_merged_fix_result(
    entry: dict[str, Any], merge_key: str
) -> bool:
    """この修正結果を取り込み済みなら真を返し、鍵の一覧を更新する。"""
    merged_keys = entry.setdefault("fix_merged_keys", [])
    if merge_key in merged_keys:
        info(
            f"↻ この修正結果は取り込み済みです"
            f"（修正ラウンド {entry['fix_rounds']}）"
        )
        return True
    return False


def _resolved_fix_thread_ids(payload: dict[str, Any], repo: str, pr: int) -> set[str]:
    """自己申告と GitHub 側の解決状態を突き合わせ、両方が解決と言う ID だけ返す。

    自己申告をそのまま信じない。解決 API に失敗・未実行でも「解決済み」と
    書けてしまい、未解決の指摘が取り消し対象から外れる。GitHub 側の
    `isResolved` と突き合わせ、**両方が解決と言っているものだけ**を反映する。
    """
    raw_claimed = payload.get("resolved_thread_ids")
    # 文字列は 1 文字ずつに分解され、数値や真偽値は反復できずに落ちる。
    # **配列であることを先に確かめる。**
    claimed = {
        t for t in (raw_claimed if isinstance(raw_claimed, list) else [])
        if isinstance(t, str) and t.strip()
    }
    if raw_claimed is not None and not isinstance(raw_claimed, list):
        info(f"⚠ resolved_thread_ids が配列ではありません（{type(raw_claimed).__name__}）。"
             "解決の申告は無かったものとして扱います")
    actual = resolved_threads_on_github(repo, pr)
    if actual is None:
        info("⚠ レビュースレッドの解決状態を取得できませんでした。"
             "自己申告は採用せず、未解決のまま扱います")
        return set()
    resolved = claimed & actual
    for thread_id in sorted(claimed - actual):
        info(f"⚠ {thread_id} は解決済みと申告されましたが、GitHub では未解決です")
    return resolved


def _unassigned_fix_commits(
    work: str, reported_shas: list[str], ordered_range: list[str]
) -> list[str]:
    """範囲内のコミットのうち、どの申告にも含まれていないものを返す。

    適用と同じく、**範囲のコミットは全て申告されていること**を求める。
    申告から漏れた修正コミットは検証を受けないまま Pull Request に残る。
    """
    reported_full = {
        full for full in (
            _git_out(work, ["rev-parse", "--verify", f"{s}^{{commit}}"])
            for s in reported_shas
        ) if full
    }
    return sorted(set(ordered_range) - reported_full)


def _verify_fix_commits(
    facts: list[dict[str, Any]], scope: list[str]
) -> tuple[list[str], list[tuple[str, str]]]:
    """修正コミットを検証し、問題点の一覧と受理した (item_id, sha) を返す。

    **不正なコミットが 1 件でもあれば、修正ラウンドの範囲ごと取り消す。**
    状態を記録しないだけでは、未検証の変更が Pull Request に残り続ける
    （見送りの対象にもならない）。どのコミットが安全かは決められないので、
    適用フェーズの未割当コミットと同じ扱いにする。
    """
    problems: list[str] = []
    accepted: list[tuple[str, str]] = []      # (item_id, sha)
    seen: dict[str, set[str]] = {}            # item_id -> 実在するコミットの集合
    for commit in facts:
        item_id = (commit.get("trailers") or {}).get("Item-Id")
        problem = verify_fix_commit(commit, scope)
        if problem:
            problems.append(problem)
            info(f"❌ 修正コミットが手順を満たしていません: {problem}")
            continue
        seen.setdefault(item_id, set()).add(commit["sha"])
        accepted.append((item_id, commit["sha"]))

    # 粒度は 1 件ずつの検証が済んでから見る。壊れたコミットの理由を
    # 粒度の失敗で覆い隠さない。
    for item_id, shas in seen.items():
        problem = verify_commit_granularity({"item_id": item_id}, len(shas))
        if problem:
            problems.append(problem)
            info(f"❌ 修正コミットが手順を満たしていません: {problem}")
    return problems, accepted


def _mark_resolved_fix_findings(entry: dict[str, Any], resolved: set[str]) -> None:
    """GitHub 側で解決済みになった thread に対応する指摘へ、解決の印を付ける。"""
    for review in entry["reviews"]:
        for finding in review["findings"]:
            if finding.get("thread_id") not in resolved:
                continue
            finding["resolved"] = True


def _record_accepted_fix_commits(
    state: dict[str, Any], accepted: list[tuple[str, str]]
) -> None:
    """検証を通った修正コミットを、対応する改善項目へ紐づける。

    見送り済みなどで項目が見つからないコミットは、紐づけ先が無いので飛ばす。
    """
    for item_id, sha in accepted:
        item = _find_item(state, item_id, required=False)
        if item is not None:
            item.setdefault("commits", []).append(sha)


def _revert_invalid_fix_round(
    path: pathlib.Path,
    state: dict[str, Any],
    entry: dict[str, Any],
    ordered_range: list[str],
) -> set[str]:
    """検証を通らない修正ラウンドの範囲を取り消し、採用する解決スレッドを返す。

    取り消した以上、解決の申告も採らないので**常に空集合を返す**。
    """
    work = state["worktrees"]["work"]
    # **状態へ記録する前に取り消す。** 先に記録すると、取り消し済みのコミットが
    # 状態ファイルに残り、後の見送り処理が同じコミットをもう一度取り消そうとする。
    info("検証を通らない変更を残さないため、この修正ラウンドの範囲を取り消します")
    # **取り消しへ着手する前に印を立てる。** 取り消しは済んだのに push できずに
    # 終わると、未検証の変更が Pull Request に残ったままになる。
    entry["pending_push"] = True
    statefile.save(path, state)
    _revert_item_commits(
        state,
        {"item_id": f"R{entry['round']}-fix{entry['fix_rounds'] + 1}",
         "commits": list(ordered_range)},
        dry_run=False,
    )
    # 取り消し後の状態を新しい起点にし、**その場で保存する**。ここで保存せずに
    # 落ちると、次の実行は古い起点から範囲を取り直して取り消しコミット自体を
    # 「未申告」と判定し、**取り消しを取り消して**しまう。
    entry["fix_base_sha"] = _git_out(work, ["rev-parse", "HEAD"])
    statefile.save(path, state)
    # **push は保存のあと。** ここで push して失敗すると、取り消しコミットは
    # ローカルに残るのに起点の更新が保存されず、叩き直しで二重に取り消してしまう。
    info("⚠ 修正を取り消したため、解決の申告は採用しません")
    return set()


def cmd_merge_fix(args: argparse.Namespace) -> None:
    """Step 6 — 修正結果を取り込み、修正ラウンドを 1 つ進める。"""
    path, state = _load(args.id)
    entry = _round(state, args.round)
    _discard_impl_leftovers(state, state["worktrees"]["work"])
    _flush_pending_push(path, state, entry)
    impl = entry["impl"]
    result = _result_path(state, impl, stem_for(impl, "fix", state["id"], args.round))
    payload = _read_result(result, impl)

    work = state["worktrees"]["work"]
    head_now = _git_out(work, ["rev-parse", "HEAD"]) or ""
    merge_key = _fix_merge_key(entry, result)
    if _already_merged_fix_result(entry, merge_key):
        return
    merged_keys = entry["fix_merged_keys"]

    resolved = _resolved_fix_thread_ids(payload, state["repo"], state["current_pr"])

    # 修正コミットも適用と同じ基準で、**git と実際のテスト実行から**検証する。
    # 結果ファイルの申告で済ませると、手順を満たさない変更が収束済みになれてしまう。
    baseline = state.get("baseline_test") or {}
    # 修正の範囲も**オーケストレータが記録した起点**から取る。起点は
    # `judge-review` が変更要求を返したときの HEAD である。
    ordered_range = commits_in_range(work, entry.get("fix_base_sha"), head_now)
    if ordered_range is None:
        # **修正ラウンドは進める。** 進めないと `should-abandon` が見送りへ移る
        # 条件（`fix_rounds` が上限に達する）を永久に満たさず、修正フェーズと
        # 再レビューを無限に往復する。この修正は採らないので、範囲外の記録は
        # 何も足さない。
        entry["fix_rounds"] += 1
        statefile.save(path, state)
        die(
            "修正の範囲を確定できませんでした"
            f"（起点 {entry.get('fix_base_sha')} / HEAD {head_now}）。"
            "検証できない修正は採りません",
            code=2,
        )
    reported_shas = _reported_shas(payload)
    unassigned = _unassigned_fix_commits(work, reported_shas, ordered_range)

    facts = collect_commit_facts(
        work, reported_shas, set(ordered_range),
        baseline.get("command") or "true", state["head_branch"],
        _safe_int(state.get("test_timeout"), DEFAULT_TEST_TIMEOUT),
    )

    problems, accepted = _verify_fix_commits(facts, state.get("target_scope") or [])

    if unassigned:
        info(
            f"❌ どの申告にも含まれていない修正コミットが {len(unassigned)} 件あります"
            f"（{', '.join(s[:7] for s in unassigned[:5])}）"
        )

    if unassigned or problems:
        resolved = _revert_invalid_fix_round(path, state, entry, ordered_range)
    else:
        _record_accepted_fix_commits(state, accepted)

    _mark_resolved_fix_findings(entry, resolved)

    merged_keys.append(merge_key)
    entry["fix_rounds"] += 1

    entry.setdefault("durations", {})["fix"] = (
        entry.get("durations", {}).get("fix", 0)
        + _safe_int(payload.get("elapsed_seconds"))
    )
    statefile.save(path, state)
    # **取り消したかどうかに関わらず公開する。** 実装担当は push しないため、
    # ここで公開しないと再レビューが Pull Request 上の差分を見られない。
    _push_with_retry_marker(path, state, entry)
    info(f"修正を取り込みました（解決 {len(resolved)} スレッド / 修正ラウンド {entry['fix_rounds']}）")


def cmd_advance(args: argparse.Namespace) -> None:
    """Step 7 — 提案ラウンドの繰り返しを続けるか判定する。

    終了コード: 0 = 続ける / 1 = 終了。

    終了条件は 3 つ。採用 0 件 / 上限到達 / 前ラウンドとの提案重複率が
    しきい値以上。**同じ提案が毎ラウンド出続けて終わらない**ことを防ぐ。
    """
    path, state = _load(args.id)
    rounds = state["rounds"]
    if state.get("final"):
        info(f"終了済みです（{state['final']}）")
        sys.exit(1)
    if not rounds:
        return
    if len(rounds) >= state["max_outer_rounds"]:
        _finish(path, state, "max_outer_rounds")
        sys.exit(1)
    last = rounds[-1]
    if last.get("adopted") == 0:
        _finish(path, state, "no_more_proposals")
        sys.exit(1)
    if len(rounds) >= 2:
        rate = duplicate_rate(
            [tuple(k) for k in last.get("proposal_keys") or []],
            [tuple(k) for k in rounds[-2].get("proposal_keys") or []],
        )
        if rate >= DUPLICATE_RATE_THRESHOLD:
            info(f"提案の重複率が {rate:.0%} で、前ラウンドとほぼ同じです")
            _finish(path, state, "duplicate_proposals")
            sys.exit(1)


def _finish(path: pathlib.Path, state: dict[str, Any], reason: str) -> None:
    state["final"] = reason
    state["ended_at"] = statefile.now()
    state["phase"] = "final"
    statefile.save(path, state)
    info(f"提案ラウンドの繰り返しを終了します（理由: {reason}）")


def cmd_status(args: argparse.Namespace) -> None:
    """現在の状態を人が読む形で出す。"""
    _, state = _load(args.id)
    print(f"# cross-refactoring rf{state['id']}（{state['repo']} #{state['current_pr']}）")
    print(f"ホスト: {state['host']}（{state['host_detection']}）")
    print(f"提案・レビュー: {' / '.join(state['runtimes'])}")
    print(f"適用の母集合: {' / '.join(state['impl_capable'])}")
    print(f"局面: {state['phase']} / 提案ラウンド {state['outer_round']} "
          f"/ {state['max_outer_rounds']}")
    print(f"終了理由: {state.get('final') or '（未終了）'}")
    print()
    print(_round_table(state))


def cmd_report(args: argparse.Namespace) -> None:
    """Step 8 — ラウンド表・項目表・見送り項目・指標を出す。"""
    _, state = _load(args.id)
    print(f"# cross-refactoring 実行報告 — {state['repo']} #{state['current_pr']}")
    print()
    print(f"- ホスト: {state['host']}（{state['host_detection']}）")
    print(f"- 対象範囲: {', '.join(state['target_scope']) or '（未指定）'}")
    print(f"- 終了理由: {state.get('final') or '（未終了）'}")
    baseline = state.get("baseline_test") or {}
    print(f"- 着手前のテスト: {baseline.get('command') or '（未指定）'}"
          f"（{baseline.get('status')}）")
    print()
    print("## ラウンド")
    print()
    print(_round_table(state))
    print()
    print("## 改善項目")
    print()
    print(_item_table(state))
    if state["deferred_items"]:
        print()
        print("## 見送った提案")
        print()
        print("| ラウンド | 対象 | 兆候 | 理由 |")
        print("| --- | --- | --- | --- |")
        for d in state["deferred_items"]:
            print(f"| {d.get('round', '—')} | {d['path']}#{d['symbol']} | "
                  f"{d['smell']} | {d.get('defer_reason', '—')} |")
    if args.metrics:
        print()
        print("# 指標")
        print()
        print(metrics_lib.format_report(metrics_lib.aggregate(state)))


def _round_table(state: dict[str, Any]) -> str:
    lines = [
        "| R | 実装担当 | モデル | レビュー担当 | モデル | 採用 | 適用 | 見送り | 修正 | 初回承認 |",
        "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for entry in state["rounds"]:
        reviewers = entry.get("reviewers", [])
        reviewer_models = entry.get("reviewer_models") or {}
        reviews = entry.get("reviews") or []
        first_approved = "—"
        if reviews:
            first_approved = (
                "はい" if all(reviews[0].get(r) == "APPROVE" for r in reviewers) else "いいえ"
            )
        lines.append(
            f"| {entry['round']} | {entry.get('impl', '—')} | "
            f"{models_lib.label((entry.get('impl_model') or {}).get('requested'))} | "
            f"{' / '.join(reviewers) or '—'} | "
            f"{' / '.join(models_lib.label((reviewer_models.get(r) or {}).get('requested')) for r in reviewers) or '—'} | "
            f"{entry.get('adopted', 0)} | {len(entry.get('apply', {}).get('applied', []))} | "
            f"{len(entry.get('apply', {}).get('failed', []))} | {entry.get('fix_rounds', 0)} | "
            f"{first_approved} |"
        )
    return "\n".join(lines) if state["rounds"] else "（ラウンドなし）"


def _item_table(state: dict[str, Any]) -> str:
    if not state["items"]:
        return "（改善項目なし）"
    lines = [
        "| ID | 対象 | 兆候 | 手法 | 重要度 | 提案元 | 状態 | コミット |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: |",
    ]
    for item in state["items"]:
        lines.append(
            f"| {item['item_id']} | {item['path']}#{item['symbol']} | "
            f"{item['smell']} | {item['technique']} | {item['severity']} | "
            f"{'/'.join(item.get('proposed_by', []))} | {item['status']} | "
            f"{len(item.get('commits') or [])} |"
        )
    return "\n".join(lines)


# ---------------- main ----------------

def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser(
        "init",
        help="Step 0 — ホスト確定 / 母集合の確定 / 作業ディレクトリ root / 状態初期化")
    init.add_argument("pr", type=int)
    init.add_argument("--scope", nargs="+", required=True,
                      help="対象範囲。提案が無制限に広がらないよう必須にしている")
    init.add_argument("--host", choices=list(assignment.HOST_RUNTIMES), default=None,
                      help="ホストの明示指定。未指定時は環境変数から推定する")
    # 既定は輪番の 1 周（適用の母集合の大きさ）。3 のままだと `ALL_RUNTIMES` の
    # 先頭にある claude の順番（ラウンド 4）へ届かない。
    init.add_argument("--max-outer-rounds", type=int,
                      default=len(assignment.ALL_RUNTIMES))
    init.add_argument("--max-fix-rounds", type=int, default=3)
    init.add_argument("--max-items-per-round", type=int, default=5)
    init.add_argument("--severity-threshold", default=DEFAULT_SEVERITY_THRESHOLD,
                      choices=[s for s in SEVERITY_ORDER if s != "unknown"])
    init.add_argument("--model", action="append", metavar="RUNTIME=MODEL",
                      help="ランタイムごとのモデル指定。繰り返し指定できる")
    init.add_argument("--test-timeout", type=int, default=DEFAULT_TEST_TIMEOUT,
                      help="テスト 1 回あたりの上限秒数。超えたら失敗として扱う "
                           f"(default: {DEFAULT_TEST_TIMEOUT})")
    init.add_argument("--sync-command", default=None,
                      help="生成物を同期するコマンド。**push の直前**に進行側が実行し、"
                           "差分があれば進行側のコミットとして積む。"
                           "同期を実装担当にさせると範囲外の変更になるため分離している")
    init.add_argument("--plan-file", default=None,
                      help="改修計画を書き出すパス（対象リポジトリからの相対）。"
                           "提案の理由と手順は状態ファイルにしか残らず、差分から"
                           "除外されるため、公開の直前に進行側が書き出す。"
                           f"既定は {DEFAULT_PLAN_DIR}/refactoring-plan-rf<PR>.md。"
                           "空文字を渡すと記録しない")
    init.add_argument("--baseline-test", required=True,
                      help="着手前と各コミットで実行するテストコマンド。"
                           "振る舞い不変を示す手段が無い書き換えは構造改善ではないため必須")
    init.add_argument("--worktree-root", default=None)
    init.set_defaults(func=cmd_init)

    for name, func, help_ in (
        ("start-round", cmd_start_round,
         "Step 2 — 提案ラウンドを開く。実装担当とレビュー担当を返す"),
        ("merge-proposals", cmd_merge_proposals,
         "Step 3 — 提案の語彙検証・重複排除・優先度付け・採否"),
        ("advance", cmd_advance, "Step 7 — 提案ラウンドの収束判定"),
        ("status", cmd_status, "現在の状態を人が読む形で出す"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("id", type=int)
        sp.set_defaults(func=func)

    for name, func, help_ in (
        ("review-targets", cmd_review_targets,
         "Step 5 — 次に起動するレビュー担当（初回は 2 者、再レビューは変更要求を出した担当）"),
        ("judge-review", cmd_judge_review, "Step 5 — レビュー担当の判定"),
        ("should-abandon", cmd_should_abandon, "Step 6 — 修正ラウンド上限の到達判定"),
        ("merge-fix", cmd_merge_fix, "Step 6 — 修正結果の取り込み"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("id", type=int)
        sp.add_argument("round", type=int)
        sp.set_defaults(func=func)

    # コミットを取り消しうる 2 つは、実行前に何が消えるかを確かめられるようにする。
    for name, func, help_ in (
        ("merge-apply", cmd_merge_apply,
         "Step 4 — 適用結果の検証（差分予算 / テスト / トレーラー / 固定テスト先行）"),
        ("abandon-items", cmd_abandon_items,
         "Step 6 — 未解決の指摘に紐づく項目だけを取り消す"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("id", type=int)
        sp.add_argument("round", type=int)
        sp.add_argument("--dry-run", action="store_true",
                        help="取り消すコミットを表示するだけで実行しない")
        sp.set_defaults(func=func)

    rp = sub.add_parser(
        "report", help="Step 8 — ラウンド表・項目表・見送り・指標")
    rp.add_argument("id", type=int)
    rp.add_argument("--metrics", action="store_true",
                    help="ランタイムとモデルの組で指標を集計する")
    rp.set_defaults(func=cmd_report)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
