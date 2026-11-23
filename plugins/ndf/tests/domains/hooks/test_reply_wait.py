"""ルールの前置き（10 系から移した `classify_reply_wait`）の正誤（#4000 の受け入れ条件 0）。

例の組は 10 系の `plugins/ndf/scripts/tests/fixtures/wait_notice_corpus.json`（`ndf--v10.27.77`）を移したもので、
ai-plugins の対話のセッションで利用者の入力の直前に終わった実際の応答 225 件である（認証情報の保管先・ファイル名・変数名は
架空の値に置き換えた。判定に関わる文の形と語は変えていない）。モデルを呼ばず、guard `stop-wait` が
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


def test_a_question_after_a_wait_elsewhere_in_the_same_sentence_is_a_wait():
    assert _is_wait("別のレーンは承認を待っていますが、こちらの変更はマージしてよいですか。")


def test_an_answer_wait_elsewhere_is_not_a_wait_here():
    assert reply_wait.reply_kind("p9 の作業を終えました。別のレーンは回答を待っています。") == "elsewhere_wait"


@pytest.mark.parametrize("text", ["返答を待ちます。", "ご回答をお待ちしています。"])
def test_the_own_waits_of_the_11_series_rule_are_waits(text):
    """11.0.22 の `classify_reply` が拾う本人の回答待ちは、モデルを呼ばずにルールが待ちと読む。"""
    assert _is_wait(text)


@pytest.mark.parametrize(
    "text",
    [
        "URL は `https://example.com/?a=1` です。",
        "https://example.com/?a=1 を開いて確かめました。",
        "[一覧](https://example.com/list?page=2) に載せました。",
    ],
    ids=["inline-code", "bare-url", "link"],
)
def test_a_url_with_a_query_is_a_report(text):
    assert reply_wait.reply_kind(text) == "report"


def test_a_wait_here_after_a_clause_naming_another_lane_is_a_wait():
    """レーン名の句と「待」の句が別なら、待っている主体はこちら（句をまたいで別の場所の待ちと読まない）。"""
    assert _is_wait("別のレーンを止めて、こちらの変更は回答を待ちます。")


def test_a_wait_elsewhere_with_a_comma_after_the_topic_is_not_a_wait_here():
    assert reply_wait.reply_kind("p9 の作業を終えました。別のレーンは、承認を待っています。") == "elsewhere_wait"


def test_an_angle_bracket_link_with_a_query_is_a_report():
    assert reply_wait.reply_kind("[手順](<guide (v2).md?version=2>) に載せました。") == "report"


def test_a_wait_word_inside_inline_code_is_a_report():
    assert reply_wait.reply_kind("設定から `answer=回答を待つ` を削除しました。") == "report"


def test_a_conditional_contact_note_is_a_report():
    assert reply_wait.reply_kind("作業を終えました。ご質問があれば、ご連絡をお待ちしています。") == "report"


def test_prose_kind_matches_reply_kind_for_a_reply_with_prose():
    """guard は `has_prose` の結果を持ったまま `prose_kind` を呼ぶ。本文のある応答では `reply_kind` と同じ種類を返す。"""
    for text in ("返答を待ちます。", "作業を終えました。", "p9 の作業を終えました。別のレーンは承認を待っています。"):
        assert reply_wait.prose_kind(text) == reply_wait.reply_kind(text)


def test_a_question_inside_a_code_block_is_not_a_wait():
    text = "手順を書きました。\n\n```\n進めてよいですか？\n```\n\nコミットは 1 件です。"
    assert not _is_wait(text)


def test_an_operation_guide_is_not_a_wait():
    assert not _is_wait("更新を終えました。アプリを再起動してください。")


def test_an_empty_reply_is_none():
    assert reply_wait.reply_kind("") == "none"
    assert reply_wait.reply_kind("```\nprint(1)\n```") == "none"
