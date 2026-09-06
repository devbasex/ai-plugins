"""レビュー結果の妥当性と、収束の判定。

結果ファイルの形式・投稿が GitHub に届いているか・変更要求の有無を見て、次に
何をするかを返す。
"""
from __future__ import annotations

import subprocess
from typing import Any, Optional

from . import info


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
