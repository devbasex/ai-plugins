"""指摘の取り込みが、指摘のファイルからレビューを組み立てて送る（#730 #583）。

**書き込みはレビューを回す側だけが行う。** 担当は指摘のファイルと結果ファイルを書いて
終わり、取り込みがそこから投稿を組み立て、待ち行列を通して送り、送信の応答を記録に
する。担当の申告と GitHub の実数を突き合わせる処理は無くなる。

| 何を確かめるか | 受け入れ条件 |
| --- | --- |
| 指摘のファイルから投稿が組み立ち、1 回の呼び出しで送られる | AC5 |
| 応答に本文が出ない | AC6 |
| 投稿の後・記録の前で止めた実行をやり直しても増えない | AC12 |
| 記録の URL と件数が送信の応答から来る | AC15 |
| インラインが 0 件でも結果なしにならない | AC17 |
| 指摘のファイルが無ければ投稿を 0 件にする | AC3 |
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest
import review_lib.commands.read_result

PR = 730
AGENT = "codex"
ROUND = 2
REPO = "o/r"
ACTOR = "takemi"
SHA = "f" * 40
INLINE_TEXT = "[major / 正確性] 戻り値を確かめる"
SUMMARY_TEXT = "層の分け方をそろえると読みやすい"


def _seed(tmp_dir: pathlib.Path, **over) -> None:
    state = {
        "current_pr": PR,
        "repo": REPO,
        "viewer_login": ACTOR,
        "is_own_pr": False,
        "event_downgrade": False,
        "rounds": [{"round": ROUND, "pr": PR, "head_sha": SHA,
                    "started_at": "2026-09-22T00:00:00+00:00"}],
        "final": None,
    }
    state.update(over)
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(
        json.dumps(state), encoding="utf-8")


def _note(tmp_dir: pathlib.Path, comments: list[dict] | None = None) -> pathlib.Path:
    if comments is None:
        comments = [{"path": "a.py", "line": 12, "body": INLINE_TEXT,
                     "severity": "major"}]
    path = tmp_dir / f"{AGENT}-review-pr{PR}-round{ROUND}-payload.json"
    path.write_text(json.dumps({"summary": SUMMARY_TEXT, "comments": comments},
                               ensure_ascii=False), encoding="utf-8")
    return path


def _result(tmp_dir: pathlib.Path, **over) -> pathlib.Path:
    data = {"event": "REQUEST_CHANGES", "by_severity": {"major": 1}}
    data.update(over)
    path = tmp_dir / f"{AGENT}-review-pr{PR}-result.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _args() -> argparse.Namespace:
    return argparse.Namespace(pr=PR, agent=AGENT, file=None)


def _state(tmp_dir: pathlib.Path) -> dict:
    return json.loads(
        (tmp_dir / f"cross-review-pr{PR}-state.json").read_text(encoding="utf-8"))


def _entry(tmp_dir: pathlib.Path) -> dict:
    return _state(tmp_dir)["rounds"][-1][AGENT]


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


_ACCEPT = {"match": f"pulls/{PR}/reviews", "stdout": json.dumps(
    {"id": 555, "html_url": f"https://github.com/o/r/pull/{PR}#pullrequestreview-555"})}
_NO_PRIOR = {"match": f"pulls/{PR}/reviews?", "stdout": "[]"}
_URL = f"https://github.com/o/r/pull/{PR}#pullrequestreview-555"


def test_the_take_in_posts_the_review_and_records_the_response(
        tmp_dir, state_mod, fake_gh, capsys) -> None:
    _seed(tmp_dir)
    _note(tmp_dir)
    _result(tmp_dir)
    fake_gh.set_rules([_NO_PRIOR, _ACCEPT])

    review_lib.commands.read_result.cmd_read_result(_args())

    entry = _entry(tmp_dir)
    assert entry["review_url"] == _URL         # 送信の応答から取る（AC15）
    assert entry["comments"] == 1              # 送れたインラインの数
    assert entry["intent"] == "REQUEST_CHANGES"
    assert entry["queued"] is False
    out = capsys.readouterr().out
    assert f"POSTED review_url={_URL}" in out
    assert "INLINE=1 BODY=0 QUEUED=0" in out
    assert "FINDINGS=1" in out


def test_no_body_reaches_the_answer(tmp_dir, state_mod, fake_gh, capsys) -> None:
    """取り込みの出力に、レビューの本文もインラインの本文も出ない（AC6）。"""
    _seed(tmp_dir)
    _note(tmp_dir)
    _result(tmp_dir)
    fake_gh.set_rules([_NO_PRIOR, _ACCEPT])

    review_lib.commands.read_result.cmd_read_result(_args())

    captured = capsys.readouterr()
    assert INLINE_TEXT not in captured.out and INLINE_TEXT not in captured.err
    assert SUMMARY_TEXT not in captured.out and SUMMARY_TEXT not in captured.err
    assert len(captured.out.splitlines()) <= 20


def test_the_body_is_sent_from_the_note(tmp_dir, state_mod, fake_gh) -> None:
    """本文は指摘のファイルから組み立てて送る。先頭行がラウンドと席を持つ（AC5・AC10）。"""
    _seed(tmp_dir)
    _note(tmp_dir)
    _result(tmp_dir)
    fake_gh.set_rules([_NO_PRIOR, _ACCEPT])

    review_lib.commands.read_result.cmd_read_result(_args())

    sent = [c for c in fake_gh.calls() if "--method POST" in " ".join(c["argv"])]
    body = json.loads(sent[-1]["stdin"])
    assert body["body"].splitlines()[0] == \
        f"## 🤖 cross-review | round {ROUND} | {AGENT} | REQUEST_CHANGES"
    assert body["commit_id"] == SHA
    assert body["comments"][0]["path"] == "a.py"


def test_a_second_take_in_does_not_add_a_second_review(
        tmp_dir, state_mod, fake_gh) -> None:
    """投稿の後・記録の前で止めた実行をやり直しても、レビューは増えない（AC12・AC20）。"""
    _seed(tmp_dir)
    _note(tmp_dir)
    _result(tmp_dir)
    fake_gh.set_rules([_NO_PRIOR, _ACCEPT])
    review_lib.commands.read_result.cmd_read_result(_args())
    posted_once = len([c for c in fake_gh.joined() if "--method POST" in c])

    # 記録を消して、投稿だけが残った状態を作る。
    st = _state(tmp_dir)
    st["rounds"][-1].pop(AGENT, None)
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(
        json.dumps(st), encoding="utf-8")
    fake_gh.set_rules([
        {"match": f"pulls/{PR}/reviews?", "stdout": json.dumps([{
            "user": {"login": ACTOR}, "state": "CHANGES_REQUESTED", "id": 555,
            "body": f"## 🤖 cross-review | round {ROUND} | {AGENT} | REQUEST_CHANGES\n",
            "html_url": _URL}])},
    ])

    review_lib.commands.read_result.cmd_read_result(_args())

    assert len([c for c in fake_gh.joined() if "--method POST" in c]) == posted_once
    assert _entry(tmp_dir)["review_url"] == _URL


def test_findings_without_an_inline_are_still_a_result(
        tmp_dir, state_mod, fake_gh, capsys) -> None:
    """指摘があってインラインが 0 件でも、その担当は結果なしにならない（AC17）。"""
    _seed(tmp_dir)
    _note(tmp_dir, comments=[{"body": SUMMARY_TEXT, "severity": "major"}])
    _result(tmp_dir)
    fake_gh.set_rules([_NO_PRIOR, _ACCEPT])

    review_lib.commands.read_result.cmd_read_result(_args())

    entry = _entry(tmp_dir)
    assert entry["intent"] == "REQUEST_CHANGES"
    assert entry["comments"] == 0
    assert "INLINE=0 BODY=1" in capsys.readouterr().out
    assert len(_state(tmp_dir)["review_findings"]) == 1


def test_a_note_alone_is_treated_as_no_result(tmp_dir, state_mod, fake_gh) -> None:
    """指摘のファイルだけがあって結果ファイルが無ければ、投稿を 0 件にする（AC3・AC4）。"""
    _seed(tmp_dir)
    _note(tmp_dir)
    fake_gh.set_rules([_NO_PRIOR, _ACCEPT])

    with pytest.raises(SystemExit):
        review_lib.commands.read_result.cmd_read_result(_args())

    assert [c for c in fake_gh.joined() if "--method POST" in c] == []


def test_only_what_is_sent_is_downgraded_on_ones_own_pull_request(
        tmp_dir, state_mod, fake_gh) -> None:
    """自分の Pull Request では送った形だけを落とす（AC32）。"""
    _seed(tmp_dir, is_own_pr=True, event_downgrade=True)
    _note(tmp_dir)
    _result(tmp_dir)
    fake_gh.set_rules([_NO_PRIOR, _ACCEPT])

    review_lib.commands.read_result.cmd_read_result(_args())

    sent = [c for c in fake_gh.calls() if "--method POST" in " ".join(c["argv"])]
    assert json.loads(sent[-1]["stdin"])["event"] == "COMMENT"
    entry = _entry(tmp_dir)
    assert entry["intent"] == "REQUEST_CHANGES" and entry["posted_as"] == "COMMENT"


def test_a_reviewer_that_wrote_nothing_adds_no_review(
        tmp_dir, state_mod, fake_gh) -> None:
    """指摘のファイルも結果も書かずに終わった担当では、レビューが 1 件も増えない（AC4）。"""
    _seed(tmp_dir)
    # 書きかけの一時の名前だけが残った状態も、正式の名前が無ければ結果なしである。
    (tmp_dir / f"{AGENT}-review-pr{PR}-round{ROUND}-payload.json.tmp").write_text("{")
    fake_gh.set_rules([_NO_PRIOR, _ACCEPT])

    with pytest.raises(SystemExit):
        review_lib.commands.read_result.cmd_read_result(_args())

    assert [c for c in fake_gh.joined() if "--method POST" in c] == []
    assert _entry(tmp_dir)["intent"] == "NO_RESULT"
