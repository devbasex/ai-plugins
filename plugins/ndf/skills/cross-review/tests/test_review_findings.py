"""指摘を構造化して取り込む（#156 の 2 本目）。

**投稿の写しでは、総評だけへ書いた指摘が進行側から見えない。** `payload.json` は
投稿したインラインコメントの写しであるため、差分の外を指す指摘と `HTTP 422` で
総評へ移った指摘が入らない。#156 が実測した「レビュー body だけの指摘が進行側から
見えない」（PR #157 の round 4）がこの経路である。

`comments[]` が持つのは投稿の写しではなく、**その担当が出した指摘の全件**とする。
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest

PR = 6002
REPO = "o/r"
AGENT = "agy"


def _state(**over) -> dict:
    state = {
        "current_pr": PR,
        "repo": REPO,
        "max_rounds": 12,
        "rotate_after": 8,
        "only": None,
        "host": "claude",
        "rounds": [{
            "round": 1,
            "pr": PR,
            "started_at": "2026-09-09T00:00:00+00:00",
        }],
        "deferred_nits": [],
        "rejected_findings": [],
        "pr_history": [{"pr": PR, "opened_at": "2026-09-09T00:00:00+00:00",
                        "closed_at": None, "rounds": 1}],
        "final": None,
    }
    state.update(over)
    return state


def _write(tmp_dir: pathlib.Path, state: dict) -> None:
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def _read(tmp_dir: pathlib.Path) -> dict:
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())


def _result(tmp_dir: pathlib.Path, **over) -> None:
    payload = {
        "event": "REQUEST_CHANGES", "posted_as": "COMMENT",
        "comments_count": 0, "review_url": "https://example.invalid/r/1",
        "by_severity": {"critical": 0, "major": 1, "minor": 0, "nit": 0},
    }
    payload.update(over)
    (tmp_dir / f"{AGENT}-review-pr{PR}-result.json").write_text(json.dumps(payload))


def _payload(tmp_dir: pathlib.Path, comments: list[dict]) -> None:
    (tmp_dir / f"{AGENT}-review-pr{PR}-round1-payload.json").write_text(
        json.dumps({"comments": comments}))


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _no_github_checks(monkeypatch, state_mod):
    """GitHub 側の突き合わせを通す。取り込みの形だけを見る。"""
    monkeypatch.setattr(state_mod, "_posted_comment_count", lambda *a, **k: None)
    monkeypatch.setattr(state_mod, "_review_exists", lambda *a, **k: True)


FULL = {
    "path": "src/foo.py", "line": 42, "severity": "major",
    "body": "null 入力で例外になる",
    "evidence": "handle() が None を渡す経路が 1 本ある",
    "falsification": "test_null_input が通れば棄却できる",
    "suggested_check": "pytest tests/test_foo.py::test_null_input",
    "posted_to": "inline",
}


def _read_result(state_mod) -> None:
    state_mod.cmd_read_result(argparse.Namespace(pr=PR, agent=AGENT, file=None))


# ---------- 取り込み ----------

def test_a_structured_finding_is_recorded(tmp_dir, state_mod):
    _write(tmp_dir, _state()); _result(tmp_dir); _payload(tmp_dir, [FULL])

    _read_result(state_mod)

    found = _read(tmp_dir)["review_findings"]
    assert len(found) == 1
    for key in ("path", "line", "severity", "body", "evidence", "falsification",
                "suggested_check", "posted_to"):
        assert found[0][key] == FULL[key], key


def test_the_record_carries_the_pr_round_and_agent(tmp_dir, state_mod):
    _write(tmp_dir, _state()); _result(tmp_dir); _payload(tmp_dir, [FULL])

    _read_result(state_mod)

    found = _read(tmp_dir)["review_findings"][0]
    assert found["pr"] == PR
    assert found["round"] == 1
    assert found["agent"] == AGENT


def test_a_finding_posted_to_the_body_is_recorded(tmp_dir, state_mod):
    """総評だけへ投稿した指摘も載る。**インラインの有無と件数を切り離す。**"""
    body_only = {**FULL, "posted_to": "body"}
    _write(tmp_dir, _state()); _result(tmp_dir, comments_count=0)
    _payload(tmp_dir, [body_only])

    _read_result(state_mod)

    found = _read(tmp_dir)["review_findings"]
    assert len(found) == 1
    assert found[0]["posted_to"] == "body"


def test_records_accumulate_across_agents(tmp_dir, state_mod):
    _write(tmp_dir, _state()); _result(tmp_dir); _payload(tmp_dir, [FULL])
    _read_result(state_mod)

    (tmp_dir / f"kiro-review-pr{PR}-result.json").write_text(json.dumps({
        "event": "APPROVE", "posted_as": "COMMENT", "comments_count": 0,
        "review_url": "https://example.invalid/r/2", "by_severity": {},
    }))
    (tmp_dir / f"kiro-review-pr{PR}-round1-payload.json").write_text(json.dumps({
        "comments": [{**FULL, "line": 99}]}))
    state_mod.cmd_read_result(argparse.Namespace(pr=PR, agent="kiro", file=None))

    found = _read(tmp_dir)["review_findings"]
    assert [f["agent"] for f in found] == [AGENT, "kiro"]


# ---------- 根拠の有無 ----------

def test_has_evidence_is_true_only_with_both(tmp_dir, state_mod):
    _write(tmp_dir, _state()); _result(tmp_dir); _payload(tmp_dir, [FULL])

    _read_result(state_mod)

    assert _read(tmp_dir)["review_findings"][0]["has_evidence"] is True


@pytest.mark.parametrize("missing", ["evidence", "falsification"])
def test_has_evidence_is_false_when_one_is_missing(tmp_dir, state_mod, missing):
    partial = {k: v for k, v in FULL.items() if k != missing}
    _write(tmp_dir, _state()); _result(tmp_dir); _payload(tmp_dir, [partial])

    _read_result(state_mod)

    found = _read(tmp_dir)["review_findings"][0]
    assert found["has_evidence"] is False


def test_an_empty_string_does_not_count_as_evidence(tmp_dir, state_mod):
    _write(tmp_dir, _state()); _result(tmp_dir)
    _payload(tmp_dir, [{**FULL, "evidence": "   "}])

    _read_result(state_mod)

    assert _read(tmp_dir)["review_findings"][0]["has_evidence"] is False


def test_a_finding_without_the_new_items_is_still_recorded(tmp_dir, state_mod):
    """4 項目を持たない指摘も捨てない。**捨てると対応していない担当の指摘が消える。**"""
    old_shape = {"path": "src/foo.py", "line": 42, "severity": "minor",
                 "body": "..."}
    _write(tmp_dir, _state()); _result(tmp_dir); _payload(tmp_dir, [old_shape])

    _read_result(state_mod)

    found = _read(tmp_dir)["review_findings"]
    assert len(found) == 1
    assert found[0]["has_evidence"] is False
    assert found[0]["posted_to"] == "inline"      # 既定


# ---------- 既存の値を変えない ----------

def test_a_state_file_without_the_key_is_readable(tmp_dir, state_mod):
    st = _state()
    assert "review_findings" not in st
    _write(tmp_dir, st); _result(tmp_dir); _payload(tmp_dir, [FULL])

    _read_result(state_mod)

    assert len(_read(tmp_dir)["review_findings"]) == 1


def test_the_comment_count_is_unchanged(tmp_dir, state_mod):
    """`comments_count` は投稿したインラインの数のままにする。"""
    _write(tmp_dir, _state()); _result(tmp_dir, comments_count=3)
    _payload(tmp_dir, [FULL, {**FULL, "posted_to": "body"}])

    _read_result(state_mod)

    assert _read(tmp_dir)["rounds"][-1][AGENT]["comments"] == 3


def test_a_missing_payload_does_not_break_the_import(tmp_dir, state_mod):
    _write(tmp_dir, _state()); _result(tmp_dir)

    _read_result(state_mod)

    assert _read(tmp_dir)["review_findings"] == []


# ---------- プロンプトの規約 ----------

SKILL = pathlib.Path(__file__).resolve().parents[1]


def test_the_prompt_asks_for_the_four_items():
    text = (SKILL / "scripts/launch-reviewer.sh").read_text(encoding="utf-8")
    for key in ("evidence", "falsification", "suggested_check", "posted_to"):
        assert key in text, key


def test_the_prompt_asks_for_body_only_findings():
    """総評だけへ書いた指摘も payload へ載せることを求める。"""
    text = (SKILL / "scripts/launch-reviewer.sh").read_text(encoding="utf-8")
    assert "出した指摘の全件" in text


def test_the_prompt_limits_what_may_be_read_before_finishing():
    """発見を終えるまで参照してよい既存コメントを限る。"""
    text = (SKILL / "scripts/launch-reviewer.sh").read_text(encoding="utf-8")
    assert "スナップショット" in text
    assert "同じラウンドの他の担当" in text


def test_the_snapshot_is_still_passed():
    """スナップショットは渡し続ける。**渡さないと同じ指摘が毎ラウンド出る。**"""
    text = (SKILL / "scripts/launch-reviewer.sh").read_text(encoding="utf-8")
    assert "existing-comments" in text
