"""ルールの前置き（10 系から移した `classify_reply_wait`）の正誤（#4000 の受け入れ条件 0）。

例の組は 10 系の `plugins/ndf/scripts/tests/fixtures/wait_notice_corpus.json`（`ndf--v10.27.77`）を移したもので、
ai-plugins の対話のセッションで利用者の入力の直前に終わった実際の応答 225 件である。モデルを呼ばず、guard `stop-wait` が
使う `reply_kind`（移した判定に、引用と別の場所の待ちの報告を外す前処理を足したもの）だけで測る。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.domains.hooks import reply_wait

CORPUS = Path(__file__).parent / "fixtures" / "wait-corpus.json"
# 10 系の判定が例の組で出した正誤（#4000 の「10 系との比較」）。移した判定はこれを下回らない
LEAST_CAUGHT = 74
MOST_FALSE = 5


def _examples() -> list[dict]:
    return json.loads(CORPUS.read_text(encoding="utf-8"))["examples"]


def _is_wait(text: str) -> bool:
    return reply_wait.reply_kind(text) == "prose_wait"


def test_the_rule_catches_no_fewer_waits_than_the_10_series():
    waits = [e for e in _examples() if e["label"] != reply_wait.NONE]
    caught = sum(_is_wait(e["text"]) for e in waits)
    assert len(waits) == 79
    assert caught >= LEAST_CAUGHT


def test_the_rule_misreads_no_more_reports_than_the_10_series():
    reports = [e for e in _examples() if e["label"] == reply_wait.NONE]
    misread = sum(_is_wait(e["text"]) for e in reports)
    assert len(reports) == 146
    assert misread <= MOST_FALSE


_BULLETS_3 = "\n".join(f"- 報告の項目 {i}" for i in range(3))
_BULLETS_9 = "\n".join(f"- レーン p{i} の状態" for i in range(9))


@pytest.mark.parametrize(
    "text",
    [
        "どちらにするか決めていただければ進めます。",
        f"どちらにするかを決めてください。\n\n{_BULLETS_3}",
        f"どちらにするかの指示をください。\n\n{_BULLETS_9}",
        "計画のレーンを 1 本増やして進めてよいですか。\n\np12 の知らせは引き続き待っています。",
        "この段落を CLAUDE.md へ載せてよいか、引き続きお返事を待っています。",
        "言い回しの列に無いためだと調べました。これは不具合だと見ています。起票しましょうか。",
    ],
    ids=["1-condition", "2-bullets-after", "3-nine-lines-after", "4-may-i", "4-awaiting-reply", "5-shall-i"],
)
def test_the_misses_in_the_issue_are_caught_by_the_rule_alone(text):
    assert _is_wait(text)


def test_a_quoted_question_is_a_report():
    """引用の誤検出の例（#4000 の「何が起きたか」）は、ルールが変更要求を出さずに通す。"""
    text = "レーンの様子を報告します。このレーンは「どちらにするかの指示をください」と書いた後に応答を終えていて、こちらでは何も待っていません。"
    assert reply_wait.reply_kind(text) == "report"


def test_a_question_outside_the_quote_is_still_a_wait():
    assert _is_wait("このレーンは「どちらにするか」を書いていません。どちらにするか決めてください。")


def test_a_wait_elsewhere_is_not_a_wait_here():
    """別のレーンの承認待ちを伝えただけの応答は、ルールの待ちにしない（11 系の `elsewhere_wait` と同じ）。"""
    assert reply_wait.reply_kind("p9 の作業を終えました。別のレーンは承認を待っています。") == "elsewhere_wait"


def test_a_wait_elsewhere_with_a_question_here_is_a_wait():
    assert _is_wait("別のレーンは承認を待っています。こちらは #4217 を載せてよいですか。")


def test_a_question_inside_a_code_block_is_not_a_wait():
    text = "手順を書きました。\n\n```\n進めてよいですか？\n```\n\nコミットは 1 件です。"
    assert not _is_wait(text)


def test_an_operation_guide_is_not_a_wait():
    assert not _is_wait("更新を終えました。アプリを再起動してください。")


def test_an_empty_reply_is_none():
    assert reply_wait.reply_kind("") == "none"
    assert reply_wait.reply_kind("```\nprint(1)\n```") == "none"
