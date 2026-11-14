"""apply の判定の反映・起票・記録（ledger）と、マイルストーンの説明の `### 順位` の節・見積もりの Size の重ね。"""

from __future__ import annotations

import json
from pathlib import Path

from ndf.domains.backlog import cli, model, table_decide

UPDATED = "2026-11-01T00:00:00Z"


def _run(capsys, last_result, *argv: str) -> dict:
    cli.main(list(argv))
    return last_result(capsys.readouterr().out)


def _act(fake, n: int, verdict: str, changes: dict | None = None, **extra) -> dict:
    i = fake.issues[n]
    out = {"number": n, "verdict": verdict, "updated_at": i["updated"], "digest": model.snapshot_digest(i), **extra}
    return {**out, "changes": changes} if changes is not None else out


def _apply(capsys, last_result, tmp_path: Path, plan: dict) -> dict:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False))
    return _run(capsys, last_result, "apply", "--repo", "acme/widgets", "--plan", str(path), "--run-id", "r1")


def test_verdicts_are_reflected_and_not_written_twice(repo_dir, fake, capsys, last_result, tmp_path):
    for n in (1, 2, 3):
        fake.issue(n, body="元の本文", labels=("old",))
    plan = {"actions": [_act(fake, 1, "追記が要る", {"body": "元の本文\n\n追記", "add_labels": ["bug"], "remove_labels": ["old"]}),
                        _act(fake, 2, "閉じてよい", {"state": "closed", "state_reason": "completed"}),
                        _act(fake, 3, "要判断", {"state": "closed"})]}  # fmt: skip
    got = _apply(capsys, last_result, tmp_path, plan)
    assert got["status"] == "ok"
    assert got["metrics"]["applied"] == [1, 2] and got["metrics"]["closed"] == [2] and got["metrics"]["returned"] == [3]
    assert fake.issues[1]["body"].endswith("追記") and fake.issues[1]["labels"] == ["bug"]
    assert fake.issues[2]["state"] == "closed" and fake.issues[3]["state"] == "open"
    writes = len(fake.writes)
    again = _apply(capsys, last_result, tmp_path, plan)
    assert again["metrics"]["already"] == [1, 2] and len(fake.writes) == writes


def test_wont_do_waits_for_approval_as_a_gate(repo_dir, fake, capsys, last_result, tmp_path):
    fake.issue(4)
    plan = {"actions": [_act(fake, 4, "やらない", {"state": "closed", "state_reason": "not_planned"})]}
    got = _apply(capsys, last_result, tmp_path, plan)
    assert got["status"] == "gate" and got["metrics"]["needs_approval"] == [4]
    assert got["routes"]["approved"]["argv"][:3] == ["ndf", "issues", "apply"] and fake.issues[4]["state"] == "open"
    plan["actions"][0]["approved"] = True
    assert _apply(capsys, last_result, tmp_path, plan)["metrics"]["applied"] == [4]
    assert fake.issues[4]["state"] == "closed"


def test_issue_changed_after_the_plan_is_skipped(repo_dir, fake, capsys, last_result, tmp_path):
    fake.issue(5, body="前")
    act = _act(fake, 5, "書き直しが要る", {"body": "後"})
    fake.issue(5, body="誰かが書き換えた", updated="2026-11-02T00:00:00Z")
    got = _apply(capsys, last_result, tmp_path, {"actions": [act]})
    assert got["status"] == "gate" and got["metrics"]["skipped_changed"] == [5]
    assert fake.issues[5]["body"] == "誰かが書き換えた"


def test_wrong_actions_write_nothing(repo_dir, fake, capsys, last_result, tmp_path):
    fake.issue(1)
    plan = {"actions": [{"number": 1, "verdict": "閉じる", "updated_at": UPDATED, "digest": "x", "changes": {"milestone": "M"}}]}
    got = _apply(capsys, last_result, tmp_path, plan)
    assert got["status"] == "stopped" and got["cause"]["kind"] == "invalid" and fake.writes == []


def test_create_marks_decisions_and_is_recorded(repo_dir, fake, capsys, last_result, tmp_path):
    base = {"title": "新しい課題", "body": "本文", "sources": [{"kind": "issue", "value": "#1"}], "mvv_basis": "Value 3"}
    plan = {"create": [{**base, "kind": "needs", "mvv_verdict": "follow"},
                       {**base, "kind": "seeds", "title": "迷う課題", "mvv_verdict": "unsure"},
                       {"kind": "needs", "title": "出典なし"}]}  # fmt: skip
    got = _apply(capsys, last_result, tmp_path, plan)
    created = {c["title"]: c for c in got["metrics"]["created"]}
    assert created["新しい課題"]["labels"] == ["needs"]
    assert created["迷う課題"]["labels"] == ["seeds", "needs-decision"]
    assert [r["title"] for r in got["metrics"]["create_rejected"]] == ["出典なし"]
    made = fake.issues[created["新しい課題"]["number"]]
    assert "## 出典" in made["body"] and "Value 3" in made["body"]
    count = len(fake.issues)
    again = _apply(capsys, last_result, tmp_path, plan)
    assert all(c["already"] for c in again["metrics"]["created"]) and len(fake.issues) == count


def _rank(capsys, last_result, tmp_path: Path, *extra: str) -> dict:
    rows = [{"number": n, "ubv": 1 + n % 3, "tc": 1, "rr_oe": 1, "size": 1, "digest": f"d{n}"} for n in range(1, 9)]
    scores = tmp_path / "s.json"
    scores.write_text(json.dumps({"issues": rows}))
    return _run(capsys, last_result, "rank", "--repo", "acme/widgets", "--scores", str(scores), *extra)


def test_apply_writes_the_rank_section_and_keeps_the_rest(repo_dir, fake, capsys, last_result, tmp_path):
    for n in range(1, 9):
        fake.issue(n, milestone="01")
    fake.milestones = {"01": 1}
    fake.descriptions = {1: "目的の文\n\n### 順位\n\n古い表\n\n## 後ろの節\n\n残す"}
    ranked = _rank(capsys, last_result, tmp_path)
    got = _apply(capsys, last_result, tmp_path, {"rank": ranked["metrics"]["digest"]})
    assert got["status"] == "ok" and got["metrics"]["tables"]["written"] == ["01"]
    desc = fake.descriptions[1]
    assert desc.startswith("目的の文\n\n### 順位\n\n| 順位 |") and desc.endswith("## 後ろの節\n\n残す") and "古い表" not in desc


def test_rank_section_halves_rows_when_the_description_is_too_long(repo_dir, fake, capsys, last_result, tmp_path):
    for n in range(1, 9):
        fake.issue(n, milestone="01")
    fake.milestones = {"01": 1}
    ranked = _rank(capsys, last_result, tmp_path)
    full = len(table_decide.replace_section("", table_decide.render(ranked["items"][0])))
    fake.max_description = full - 200
    got = _apply(capsys, last_result, tmp_path, {"rank": ranked["metrics"]["digest"]})
    limit = got["metrics"]["tables"]["truncated"]["01"]
    assert limit in (4, 2, 1, 0) and "件は表に載せていない" in fake.descriptions[1]


def test_unwritable_rank_section_stops_with_gaps(repo_dir, fake, capsys, last_result, tmp_path):
    for n in range(1, 9):
        fake.issue(n, milestone="01")
    fake.milestones = {"01": 1}
    ranked = _rank(capsys, last_result, tmp_path)
    fake.max_description = 5
    got = _apply(capsys, last_result, tmp_path, {"rank": ranked["metrics"]["digest"]})
    assert got["status"] == "stopped" and got["cause"]["kind"] == "incomplete"
    assert got["gaps"] == [{"kind": "write", "name": "01", "reason": "順位の表を書けない"}]


def test_rank_section_steps_down_to_half():
    assert [table_decide.next_limit(8, x) for x in (None, 4, 2, 1, 0)] == [4, 2, 1, 0, None]


def test_sizes_from_estimate_records_replace_the_size(repo_dir, fake, capsys, last_result, tmp_path):
    for n in range(1, 9):
        fake.issue(n, milestone="01")
    sizes = tmp_path / "sizes.json"
    sizes.write_text(json.dumps({"issues": [{"number": 3, "size": 8}, {"number": 4, "size": "over"}]}))
    got = _rank(capsys, last_result, tmp_path, "--sizes", str(sizes))
    rows = {r["number"]: r for r in got["items"][0]["rows"]}
    assert rows[3]["size"] == 8 and rows[3]["sources"]["size"] == "records" and rows[4]["size"] == "over"
    assert rows[1]["sources"]["size"] == "llm" and got["metrics"]["sized"] == [3, 4]
    sizes.write_text(json.dumps({"issues": [{"number": 3, "size": 7}]}))
    assert _rank(capsys, last_result, tmp_path, "--sizes", str(sizes))["cause"]["kind"] == "invalid"
