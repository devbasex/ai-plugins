"""`gh api -i` の出力をヘッダと本文へ分ける分岐（`_parse_rest_headers`）。

`_gh_rest` 経由の結合テスト（`test_state_rest_metadata.py`）は通っているが、各分岐の入力
（状態行だけ `\\r` を持たない・コロンの無い行・空行が無い入力）が単体で固定されていない。
現状の振る舞いを記録する（現状固定テスト。正しさを主張しない）。
"""
from __future__ import annotations


def test_headers_are_lowercased_and_body_is_split_at_the_blank_line(state_mod):
    """状態行（`\\r` なし）＋ヘッダ（`\\r\\n`）＋空行＋本文を分ける。"""
    text = (
        "HTTP/2.0 200 OK\n"
        "Content-Type: application/json\r\n"
        "X-Ratelimit-Remaining: 4999\r\n"
        "\r\n"
        '{"number": 1}'
    )
    headers, body = state_mod._parse_rest_headers(text)

    assert headers == {
        "content-type": "application/json",
        "x-ratelimit-remaining": "4999",
    }
    assert body == '{"number": 1}'


def test_the_status_line_without_a_colon_is_not_a_header(state_mod):
    """コロンの無い行（状態行など）はヘッダ辞書に入らない。"""
    text = (
        "HTTP/2.0 200 OK\n"
        "no-colon-line\n"
        "Accept: application/json\r\n"
        "\r\n"
        "body"
    )
    headers, body = state_mod._parse_rest_headers(text)

    assert headers == {"accept": "application/json"}
    assert body == "body"


def test_an_input_without_a_blank_line_yields_an_empty_body(state_mod):
    """空行を含まない入力は、本文が空文字になる（区切りが見つからない現状）。"""
    text = (
        "HTTP/2.0 200 OK\n"
        "Content-Type: application/json\r\n"
        "X-Ratelimit-Remaining: 10\r\n"
    )
    headers, body = state_mod._parse_rest_headers(text)

    assert headers == {
        "content-type": "application/json",
        "x-ratelimit-remaining": "10",
    }
    assert body == ""
