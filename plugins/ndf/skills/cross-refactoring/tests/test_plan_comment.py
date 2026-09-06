"""改修計画を Pull Request のコメント 1 件へ残す（#436 決定 6 / D3・D4）。

**改修計画は実行の記録であって、リポジトリの知識ではない。** 既定はコメントで、
ラウンドが進むたびに**同じコメントを編集する**。URL は永続で、マージの後も開ける。

`gh` は呼ばない。`_sh` を差し替え、渡した引数と返した JSON だけを見る。
"""
from __future__ import annotations

import json

import pytest

from crossref_helpers import make_state, read_state


COMMENT_URL = "https://github.com/devbasex/ai-plugins/pull/130#issuecomment-999"


def _item(**over):
    base = {
        "item_id": "R1-001", "round": 1, "path": "src/foo.py",
        "symbol": "Foo.handle", "smell": "long_method",
        "technique": "extract_method", "severity": "major",
        "rationale": "1 関数が 6 段の処理を通しで行っている",
        "plan": "1. 範囲の確定を切り出す", "test_gap": False,
        "estimated_diff_lines": 40, "proposed_by": ["codex", "agy"],
        "status": "done", "commits": ["abc1234"],
    }
    base.update(over)
    return base


def _state(tmp_path, **over):
    over.setdefault("rounds", [{
        "round": 1, "impl": "codex", "reviewers": ["agy", "kiro"],
        "items": ["R1-001"], "reviews": [], "fix_rounds": 0,
    }])
    over.setdefault("items", [_item()])
    over.setdefault("plan_mode", "comment")
    over.setdefault("plan_file", "")
    path = make_state(tmp_path, **over)
    return path, read_state(path)


@pytest.fixture
def gh(refactor, monkeypatch):
    """`gh api` の呼び出しを記録し、決めた応答を返す。"""
    calls: list[list[str]] = []
    responses: dict[str, str] = {}

    def fake_sh(cmd, cwd=None, check=True):
        calls.append(list(cmd))
        for key, value in responses.items():
            if key in " ".join(cmd):
                return value
        return ""

    monkeypatch.setattr(refactor, "_sh", fake_sh)
    return calls, responses


# ---------- 置き場所の決まり方 ----------

def test_the_default_mode_is_a_comment(refactor, tmp_path):
    _, state = _state(tmp_path)
    assert refactor.plan_mode(state) == refactor.PLAN_COMMENT


def test_a_state_file_from_an_older_version_keeps_using_the_file(refactor, tmp_path):
    """宣言の無い状態ファイルは書き出し先から読む。再開でコメントへ移さない。"""
    _, state = _state(tmp_path, plan_mode=None, plan_file="issues/plan.md")
    assert refactor.plan_mode(state) == refactor.PLAN_FILE


def test_a_state_file_without_any_plan_records_nothing(refactor, tmp_path):
    _, state = _state(tmp_path, plan_mode=None, plan_file="")
    assert refactor.plan_mode(state) == refactor.PLAN_NONE


# ---------- 投稿と編集 ----------

def test_the_first_publish_creates_a_comment(refactor, tmp_path, gh):
    calls, responses = gh
    responses["issues/130/comments"] = json.dumps(
        {"id": 999, "html_url": COMMENT_URL})
    _, state = _state(tmp_path)

    url = refactor.publish_plan_comment(state)

    assert url == COMMENT_URL
    assert state["plan_comment"] == {"id": 999, "url": COMMENT_URL}
    posted = [c for c in calls if "POST" in c]
    assert len(posted) == 1


def test_the_second_publish_edits_the_same_comment(refactor, tmp_path, gh):
    """**同じコメントを編集する。** ラウンドごとに積み増さない。"""
    calls, responses = gh
    responses["issues/comments/999"] = json.dumps(
        {"id": 999, "html_url": COMMENT_URL})
    _, state = _state(tmp_path, plan_comment={"id": 999, "url": COMMENT_URL})

    refactor.publish_plan_comment(state)

    assert not [c for c in calls if "POST" in c]
    patched = [c for c in calls if "PATCH" in c]
    assert len(patched) == 1
    assert "repos/devbasex/ai-plugins/issues/comments/999" in patched[0]


def test_a_lost_record_is_recovered_from_the_marker(refactor, tmp_path, gh):
    """控えが失われても、印で引き当て直す。引き当てないとコメントが積まれる。"""
    calls, responses = gh
    _, state = _state(tmp_path)
    marker = refactor.plan_comment_marker(state)
    responses["--paginate"] = json.dumps(
        [{"id": 12, "body": "別のコメント"},
         {"id": 999, "body": f"{marker}\n\n# 改修計画"}])
    responses["issues/comments/999"] = json.dumps(
        {"id": 999, "html_url": COMMENT_URL})

    refactor.publish_plan_comment(state)

    assert not [c for c in calls if "POST" in c]
    assert [c for c in calls if "PATCH" in c]


def test_the_body_carries_the_marker_and_the_plan(refactor, tmp_path):
    _, state = _state(tmp_path)
    body = refactor.plan_comment_body(state)
    assert body.startswith(refactor.plan_comment_marker(state))
    assert "R1-001" in body and "src/foo.py" in body


def test_a_failed_post_does_not_stop_the_run(refactor, tmp_path, gh):
    """記録が残らないことと、変更が検証を通っていないことは別である。"""
    _, state = _state(tmp_path)
    assert refactor.publish_plan_comment(state) is None
    assert state.get("plan_comment") in (None, {})


def test_the_file_mode_does_not_post_a_comment(refactor, tmp_path, gh):
    calls, _ = gh
    _, state = _state(tmp_path, plan_mode="file", plan_file="issues/plan.md")
    assert refactor.publish_plan_comment(state) is None
    assert calls == []


# ---------- 見送りの内訳は改修計画が持つ ----------

def test_the_plan_lists_the_deferred_items(refactor, tmp_path):
    """**内訳を持つのは改修計画だけである**（決定 6-b）。"""
    _, state = _state(tmp_path, deferred_items=[{
        "item_id": "R1-002", "round": 1, "path": "src/bar.py",
        "symbol": "Bar.run", "smell": "duplication",
        "defer_reason": "差分予算を超えた",
    }])
    text = refactor.format_plan(state)
    assert "見送った項目" in text
    assert "src/bar.py#Bar.run" in text and "差分予算を超えた" in text


def test_the_plan_says_none_when_nothing_was_deferred(refactor, tmp_path):
    _, state = _state(tmp_path)
    assert "見送った項目" in refactor.format_plan(state)
