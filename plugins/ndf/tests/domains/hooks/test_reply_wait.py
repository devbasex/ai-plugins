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


@pytest.mark.parametrize(
    "text",
    [
        "返答を待ちます。",
        "ご回答をお待ちしています。",
        "ご指示をお待ちしています。",
        "ご確認をお待ちしています。",
        "承認をお待ちしています。",
    ],
)
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


def test_a_wait_elsewhere_after_an_action_of_the_same_lane_is_not_a_wait_here():
    """主体を書かない句は前の句の主体（別のレーン）を引き継ぐ。"""
    assert reply_wait.reply_kind("p9 の作業を終えました。別のレーンはレビューを終え、承認を待っています。") == "elsewhere_wait"


def test_a_wait_elsewhere_with_a_comma_after_the_topic_is_not_a_wait_here():
    assert reply_wait.reply_kind("p9 の作業を終えました。別のレーンは、承認を待っています。") == "elsewhere_wait"


def test_an_angle_bracket_link_with_a_query_is_a_report():
    assert reply_wait.reply_kind("[手順](<guide (v2).md?version=2>) に載せました。") == "report"


def test_a_wait_word_inside_inline_code_is_a_report():
    assert reply_wait.reply_kind("設定から `answer=回答を待つ` を削除しました。") == "report"


def test_a_conditional_contact_note_is_a_report():
    assert reply_wait.reply_kind("作業を終えました。ご質問があれば、ご連絡をお待ちしています。") == "report"


def test_an_approval_request_here_after_a_lane_subject_is_a_wait():
    """主体を書かない句でも、こちらの操作への承認を求める句は別のレーンの主体を引き継がない。"""
    assert _is_wait("別のレーンはレビューを終え、こちらの変更をマージしてよいかご承認をお待ちしています。")


def test_a_confirmation_wait_elsewhere_is_not_a_wait_here():
    assert reply_wait.reply_kind("別のレーンはレビューを終え、担当者の確認をお待ちしています。") == "elsewhere_wait"


def test_waiting_for_ci_results_is_a_report():
    assert reply_wait.reply_kind("CI の結果をお待ちしています。") == "report"


def test_a_conditional_contact_note_with_a_specific_word_is_a_report():
    assert reply_wait.reply_kind("修正は完了しました。問題があれば、ご指示をお待ちしています。") == "report"


def test_read_prose_matches_reply_kind_for_a_reply_with_prose():
    """guard は `prose_lines` の返りを持ったまま `read_prose` を呼ぶ。本文のある応答では `reply_kind` と同じ種類を返す。"""
    for text in ("返答を待ちます。", "作業を終えました。", "p9 の作業を終えました。別のレーンは承認を待っています。"):
        assert reply_wait.read_prose(reply_wait.prose_lines(text)).kind == reply_wait.reply_kind(text)


@pytest.mark.parametrize(
    "text",
    [
        "対応が完了しました。引き続きよろしくお願いします。",
        "修正を push しました。今後ともよろしくお願い申し上げます。",
        "PR を作りました。テストも通りました。以上です。\n引き続きよろしくお願いします。",
    ],
)
def test_a_completion_report_with_a_set_closing_is_settled_by_the_rule(text):
    """定型の結びの前が完了の報告だけなら、ルールで `closing_report`（guard は軽いモデルを呼ばずに通す）。"""
    assert reply_wait.reply_kind(text) == "closing_report"


@pytest.mark.parametrize(
    "text, kind",
    [
        # 結びだけで報告の文が無い・結びの前が言い切らない → ルールで決めず、軽いモデルへ渡す
        ("以上です。よろしくお願いいたします。", "report"),
        ("作業を終えました。どうしましょう。\n引き続きよろしくお願いします。", "report"),
        ("作業を終えました。判断が必要です。引き続きよろしくお願いします。", "report"),
        # 結びの前の承認のリクエスト・質問はルールの待ち
        ("作業を終えました。マージしてよいですか。\n引き続きよろしくお願いします。", "prose_wait"),
        ("作業を終えました。どちらにしますか？\n引き続きよろしくお願いします。", "prose_wait"),
    ],
)
def test_a_set_closing_does_not_hide_what_comes_before_it(text, kind):
    assert reply_wait.reply_kind(text) == kind


def test_a_quote_over_several_lines_is_not_read_as_a_question():
    """かぎ括弧の引用が行をまたいでも中身を判定に入れない。閉じない開きかっこは引用に数えない。"""
    assert reply_wait.reply_kind("ログの抜粋です。\n『処理を止めました。\n続けてもよいですか？』") == "report"
    assert reply_wait.reply_kind("ログの抜粋です。\n「処理を止めました。\n「続けてもよいですか？」と出ました」") == "report"
    assert reply_wait.reply_kind("ログの抜粋です。\n『処理を止めました。\n続けてもよいですか？") == "prose_wait"


def test_a_quote_over_several_lines_keeps_the_question_after_it():
    text = "ログの抜粋です。\n『処理を止めました。\n続けてもよいですか？』\nこちらは再開してよいですか？"
    assert reply_wait.reply_kind(text) == "prose_wait"


@pytest.mark.parametrize("text", ["修正を終えました。ご確認をよろしくお願いします。", "差分を載せました。承認をお願いします。"])
def test_an_explicit_request_with_a_closing_is_still_a_wait(text):
    assert _is_wait(text)


def test_a_question_inside_a_code_block_is_not_a_wait():
    text = "手順を書きました。\n\n```\n進めてよいですか？\n```\n\nコミットは 1 件です。"
    assert not _is_wait(text)


def test_an_operation_guide_is_not_a_wait():
    assert not _is_wait("更新を終えました。アプリを再起動してください。")


def test_an_empty_reply_is_none():
    assert reply_wait.reply_kind("") == "none"
    assert reply_wait.reply_kind("```\nprint(1)\n```") == "none"


@pytest.mark.parametrize(
    "text",
    [
        "例を載せました。\n\n    続けてもよいですか？\n\nコミットは 1 件です。",
        "例を載せました。\n\n````markdown\n```\n続けてもよいですか？\n```\n````\n\nコミットは 1 件です。",
    ],
)
def test_a_question_in_indented_or_long_fenced_code_is_not_a_wait(text):
    """コードのブロックは CommonMark の解析で決める（インデントのコード・四連の囲みの中の質問を本文と読まない）。"""
    assert reply_wait.reply_kind(text) == "report"


def test_a_question_in_a_heading_is_a_wait():
    assert reply_wait.reply_kind("## このまま進めてよいですか？") == "prose_wait"
    assert reply_wait.reply_kind("作業を終えました。\n\n## このまま進めてよいですか？") == "prose_wait"


@pytest.mark.parametrize(
    "text, kind",
    [
        # 章題が最後の 3 行を使い、前の質問を押し出さない
        ("このまま進めてよいですか？\n\n## 検証結果\nテストはすべて通りました。\n差分は確認済みです。", "prose_wait"),
        # 章題は完了の報告の文に数えない
        ("## 実装結果\n対応が完了しました。\n引き続きよろしくお願いします。", "closing_report"),
        # 直下の本文が見出しの事柄を言い切って回答した質問の見出しは章題（定型の結びで通す）
        ("## 再起動は必要ですか？\n再起動は不要だと確認しました。\n以上です。", "closing_report"),
        # 別の場所の待ちを報告する見出しは、本人への質問でない
        ("## 別のレーンは承認を待っています\nこちらの作業は完了しました。\n以上です。", "closing_report"),
    ],
)
def test_a_heading_without_a_question_is_a_title(text, kind):
    assert reply_wait.reply_kind(text) == kind


@pytest.mark.parametrize(
    "text",
    [
        # FAQ の質問の見出しに後の本文が回答している
        "## よくある質問\n### どうやって設定しますか？\n環境変数で指定できます。",
        "## マージの承認は必要ですか？\nこのリポジトリでは必須です。",
        "## 設定ファイルを変更してもよいですか？\nはい、変更できます。値は環境変数でも上書きできます。",
        "## この設定は再起動後も残りますか？\n設定はディスクに保存されるため、再起動後も保持されます。",
        # 手段の質問の下の手順
        "## どうやって設定しますか？\n- 設定ファイルを開く\n- 値を書く",
        "## どうやって移行を進めますか？\n- 設定を書き出す\n- 新しい環境へ読み込む",
        # 見出しと別の事柄の報告と結び（回答と読まず、定型の結びでも通さない）
        "## 既存の設定を使いますか？\nテストはすべて通りました。\n引き続きよろしくお願いします。",
        # 判断できない見出しを残したまま、別の場所の待ちだけとして通さない
        "## 既存の設定を使いますか？\n別のレーンは承認を待っています。",
        # 見出しの語を含む報告でも、調べた結論でなければ判断の材料で、回答と読まない
        "## 既存の設定を使いますか？\n既存の設定でテストはすべて通りました。\n以上です。",
        # 事実を尋ねる見出しの下のファイルの一覧は、選択肢でなく報告
        "## どのファイルが変更されましたか？\n次のファイルを変更しました。\n- a.py\n- b.py",
    ],
)
def test_a_question_heading_followed_by_prose_goes_to_the_model(text):
    # 後に本文が続く質問の見出しは、利用者への質問か章題かを語の形で決めず、ルールで待ちにも定型の結びにもしない
    # （`report` は stop-wait が軽いモデルへ回す）
    assert reply_wait.reply_kind(text) == "report"


@pytest.mark.parametrize(
    "text",
    [
        # 見出しで選択・承認・確認を明示的に求め、下に選択肢・判断材料・結び（定型の結びでも通さない）
        "## どちらの案で進めますか？\n- 案 A\n- 案 B",
        # 前置きの文の後に選択肢の箇条書き
        "## どちらの案で進めますか？\nどちらもテスト済みです。\n- 案 A\n- 案 B",
        "## どう進めますか？\n- 案 A: 先に直す\n- 案 B: 後で直す",
        "## このままマージしてよいですか？\nテストはすべて通りました。\n引き続きよろしくお願いします。",
        "## このままマージしますか？\nテストはすべて通りました。\n引き続きよろしくお願いします。",
        "## 確認してください\n修正を反映しました。\n引き続きよろしくお願いします。",
        "## このままマージしてよいですか？\nCI はすべて通過し、マージできます。",
        # 本人への承認の質問に別の場所の待ちを併記しても、別の場所の待ちだけとして通さない
        "## このままマージしてよいですか？\n別のレーンは承認を待っています。",
        # 承認の質問の下で判断の材料を確認したと述べても、質問への回答と読まない
        "## このままマージしてよいですか？\nマージ前のテストがすべて通ったことを確認しました。\n以上です。",
        "## どの案を採用しますか？\n- A. 先に直す\n- B. 後で直す",
        # 小文字の選択肢のラベル
        "## どちらがよいですか？\n- a. 先に修正する\n- b. 後で修正する",
        # 許可の質問の下の本文は、回答に見えても判断の材料に見えてもルールで章題にしない（モデルへ回す）
        "## 管理者権限なしで実行してもよいですか？\n管理者権限なしで実行できることを確認しました。\n以上です。",
        "## 本番へデプロイしてよいですか？\nステージングへデプロイできることを確認しました。\n以上です。",
        "## このままマージしてよいですか？\nマージできるかどうかは未確認だと分かりました。\n以上です。",
    ],
)
def test_a_heading_that_requests_the_user_is_a_heading_wait(text):
    # stop-wait はモデルの判定に従い、モデルを使えない Stop では変更を要求する
    assert reply_wait.reply_kind(text) == "heading_wait"


def test_read_prose_lists_the_headings_left_to_the_model():
    lines = reply_wait.prose_lines("## 実装結果\n## このままマージしてよいですか？\nテストはすべて通りました。")
    assert reply_wait.read_prose(lines).delegated == ("このままマージしてよいですか？",)


def test_a_wait_in_the_parentheses_of_a_set_closing_is_not_a_closing_report():
    text = "対応が完了しました。引き続きよろしくお願いします（このままマージしてよいですか）。"
    assert reply_wait.reply_kind(text) != "closing_report"
