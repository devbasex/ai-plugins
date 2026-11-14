"""apply の判定の反映・起票・記録（ledger）と、マイルストーンの説明の `### 順位` の節・見積もりの Size の重ね。"""

from __future__ import annotations

import json
from pathlib import Path

from ndf.adapters.github import GitHubError
from ndf.domains.backlog import actions, model, table_decide

UPDATED = "2026-11-01T00:00:00Z"


def _act(fake, n: int, verdict: str, changes: dict | None = None, **extra) -> dict:
    i = fake.issues[n]
    out = {"number": n, "verdict": verdict, "updated_at": i["updated"], "digest": model.snapshot_digest(i), **extra}
    return {**out, "changes": changes} if changes is not None else out


def _apply(run_issues, tmp_path: Path, plan: dict) -> dict:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False))
    return run_issues("apply", "--repo", "acme/widgets", "--plan", str(path), "--run-id", "r1")


def test_verdicts_are_reflected_and_not_written_twice(repo_dir, fake, run_issues, tmp_path):
    for n in (1, 2, 3):
        fake.issue(n, body="元の本文", labels=("old",))
    plan = {"actions": [_act(fake, 1, "追記が要る", {"body": "元の本文\n\n追記", "add_labels": ["bug"], "remove_labels": ["old"]}),
                        _act(fake, 2, "閉じてよい", {"state": "closed", "state_reason": "completed"}),
                        _act(fake, 3, "要判断", {"state": "closed"})]}  # fmt: skip
    got = _apply(run_issues, tmp_path, plan)
    assert got["status"] == "ok"
    assert got["metrics"]["applied"] == [1, 2] and got["metrics"]["closed"] == [2] and got["metrics"]["returned"] == [3]
    assert fake.issues[1]["body"].endswith("追記") and fake.issues[1]["labels"] == ["bug"]
    assert fake.issues[2]["state"] == "closed" and fake.issues[3]["state"] == "open"
    writes = len(fake.writes)
    again = _apply(run_issues, tmp_path, plan)
    assert again["metrics"]["already"] == [1, 2] and len(fake.writes) == writes


def test_wont_do_waits_for_approval_as_a_gate(repo_dir, fake, run_issues, tmp_path):
    fake.issue(4)
    plan = {"actions": [_act(fake, 4, "やらない", {"state": "closed", "state_reason": "not_planned"})]}
    got = _apply(run_issues, tmp_path, plan)
    assert got["status"] == "gate" and got["metrics"]["needs_approval"] == [4]
    assert got["routes"]["approved"]["argv"][:3] == ["ndf", "issues", "apply"] and fake.issues[4]["state"] == "open"
    plan["actions"][0]["approved"] = True
    assert _apply(run_issues, tmp_path, plan)["metrics"]["applied"] == [4]
    assert fake.issues[4]["state"] == "closed"


def test_issue_changed_after_the_plan_is_skipped(repo_dir, fake, run_issues, tmp_path):
    fake.issue(5, body="前")
    act = _act(fake, 5, "書き直しが要る", {"body": "後"})
    fake.issue(5, body="誰かが書き換えた", updated="2026-11-02T00:00:00Z")
    got = _apply(run_issues, tmp_path, {"actions": [act]})
    assert got["status"] == "gate" and got["metrics"]["skipped_changed"] == [5]
    assert fake.issues[5]["body"] == "誰かが書き換えた"


def test_wrong_actions_write_nothing(repo_dir, fake, run_issues, tmp_path):
    fake.issue(1)
    plan = {"actions": [{"number": 1, "verdict": "閉じる", "updated_at": UPDATED, "digest": "x", "changes": {"milestone": "M"}}]}
    got = _apply(run_issues, tmp_path, plan)
    assert got["status"] == "stopped" and got["cause"]["kind"] == "invalid" and fake.writes == []


def test_create_marks_decisions_and_is_recorded(repo_dir, fake, run_issues, tmp_path):
    base = {"title": "新しい課題", "body": "本文", "sources": [{"kind": "issue", "value": "#1"}], "mvv_basis": "Value 3"}
    plan = {"create": [{**base, "kind": "needs", "mvv_verdict": "follow"},
                       {**base, "kind": "seeds", "title": "迷う課題", "mvv_verdict": "unsure"},
                       {"kind": "needs", "title": "出典なし"}]}  # fmt: skip
    got = _apply(run_issues, tmp_path, plan)
    created = {c["title"]: c for c in got["metrics"]["created"]}
    assert created["新しい課題"]["labels"] == ["needs"]
    assert created["迷う課題"]["labels"] == ["seeds", "needs-decision"]
    assert [r["title"] for r in got["metrics"]["create_rejected"]] == ["出典なし"]
    made = fake.issues[created["新しい課題"]["number"]]
    assert "## 出典" in made["body"] and "Value 3" in made["body"]
    count = len(fake.issues)
    again = _apply(run_issues, tmp_path, plan)
    assert all(c["already"] for c in again["metrics"]["created"]) and len(fake.issues) == count


def _rank(run_issues, tmp_path: Path, *extra: str) -> dict:
    rows = [{"number": n, "ubv": 1 + n % 3, "tc": 1, "rr_oe": 1, "size": 1, "digest": f"d{n}"} for n in range(1, 9)]
    scores = tmp_path / "s.json"
    scores.write_text(json.dumps({"issues": rows}))
    return run_issues("rank", "--repo", "acme/widgets", "--scores", str(scores), *extra)


def test_apply_writes_the_rank_section_and_keeps_the_rest(repo_dir, fake, run_issues, tmp_path):
    for n in range(1, 9):
        fake.issue(n, milestone="01")
    fake.milestones = {"01": 1}
    fake.descriptions = {1: "目的の文\n\n### 順位\n\n古い表\n\n## 後ろの節\n\n残す"}
    ranked = _rank(run_issues, tmp_path)
    got = _apply(run_issues, tmp_path, {"rank": ranked["metrics"]["digest"]})
    assert got["status"] == "ok" and got["metrics"]["tables"]["written"] == ["01"]
    desc = fake.descriptions[1]
    assert desc.startswith("目的の文\n\n### 順位\n\n| 順位 |") and desc.endswith("## 後ろの節\n\n残す") and "古い表" not in desc


def test_rank_section_halves_rows_when_the_description_is_too_long(repo_dir, fake, run_issues, tmp_path):
    for n in range(1, 9):
        fake.issue(n, milestone="01")
    fake.milestones = {"01": 1}
    ranked = _rank(run_issues, tmp_path)
    full = len(table_decide.replace_section("", table_decide.render(ranked["items"][0])))
    fake.max_description = full - 200
    got = _apply(run_issues, tmp_path, {"rank": ranked["metrics"]["digest"]})
    limit = got["metrics"]["tables"]["truncated"]["01"]
    assert limit in (4, 2, 1, 0) and "件は表に載せていない" in fake.descriptions[1]


def test_unwritable_rank_section_stops_with_gaps(repo_dir, fake, run_issues, tmp_path):
    for n in range(1, 9):
        fake.issue(n, milestone="01")
    fake.milestones = {"01": 1}
    ranked = _rank(run_issues, tmp_path)
    fake.max_description = 5
    got = _apply(run_issues, tmp_path, {"rank": ranked["metrics"]["digest"]})
    assert got["status"] == "stopped" and got["cause"]["kind"] == "incomplete"
    assert got["gaps"] == [{"kind": "write", "name": "01", "reason": "順位の表を書けない"}]


def test_rank_section_steps_down_to_half():
    assert [table_decide.next_limit(8, x) for x in (None, 4, 2, 1, 0)] == [4, 2, 1, 0, None]


def test_sizes_from_estimate_records_replace_the_size(repo_dir, fake, run_issues, tmp_path):
    for n in range(1, 9):
        fake.issue(n, milestone="01")
    sizes = tmp_path / "sizes.json"
    sizes.write_text(json.dumps({"issues": [{"number": 3, "size": 8}, {"number": 4, "size": "over"}]}))
    got = _rank(run_issues, tmp_path, "--sizes", str(sizes))
    rows = {r["number"]: r for r in got["items"][0]["rows"]}
    assert rows[3]["size"] == 8 and rows[3]["sources"]["size"] == "records" and rows[4]["size"] == "over"
    assert rows[1]["sources"]["size"] == "llm" and got["metrics"]["sized"] == [3, 4]
    sizes.write_text(json.dumps({"issues": [{"number": 3, "size": 7}]}))
    assert _rank(run_issues, tmp_path, "--sizes", str(sizes))["cause"]["kind"] == "invalid"


# --- 現状固定: actions._write（書き込みの順と、途中で落ちたときに書けた分だけを記録へ残す振る舞い） ---


class StubRest:
    """`repo` と `rest` だけを持つ偽物。1 件の課題を持ち、書き込みを順に記録する。`fail_at` 番目の書き込みで落ちる。"""

    repo = "acme/widgets"

    def __init__(self, labels: list[str], fail_at: int | None = None) -> None:
        self.cur = {"title": "題", "body": "元", "state": "open", "milestone": None, "labels": list(labels), "updated_at": UPDATED}
        self.fail_at = fail_at
        self.writes: list[tuple[str, str, object]] = []

    def rest(self, method: str, path: str, body: object = None) -> dict:
        if method == "GET":
            return {**self.cur, "labels": [{"name": lb} for lb in self.cur["labels"]]}
        if self.fail_at is not None and len(self.writes) + 1 == self.fail_at:
            self.fail_at = None
            raise GitHubError(f"落ちた: {method} {path}", 500)
        self.writes.append((method, path.removeprefix("/repos/acme/widgets/issues/9"), body))
        if method == "PATCH":
            self.cur.update({k: v for k, v in body.items() if k in ("title", "body", "state")})
        elif method == "POST":
            self.cur["labels"] += [lb for lb in body["labels"] if lb not in self.cur["labels"]]
        else:
            self.cur["labels"].remove(path.rsplit("/", 1)[1])
        return {}


def _stub_plan(gh: StubRest) -> dict:
    changes = {"body": "新", "add_labels": ["bug"], "remove_labels": ["old", "stale"]}
    act = {"number": 9, "verdict": "追記が要る", "updated_at": UPDATED, "digest": model.snapshot_digest(gh.cur), "changes": changes}
    return {"actions": [act]}


def _written(labels: list[str]) -> str:
    """StubRest の課題へ本文「新」と `labels` を書いた後の要約値。"""
    return model.snapshot_digest({"title": "題", "body": "新", "state": "open", "milestone": None, "labels": labels})


def _ledger_rows(tmp_path: Path) -> list[dict]:
    return list(actions.Ledger(tmp_path).rows.values())


def test_write_order_and_applied_record(tmp_path):
    gh = StubRest(["old", "stale", "keep"])
    got = actions.apply_actions(gh, actions.Ledger(tmp_path), _stub_plan(gh))
    assert gh.writes == [("PATCH", "", {"body": "新"}), ("POST", "/labels", {"labels": ["bug"]}),
                         ("DELETE", "/labels/old", None), ("DELETE", "/labels/stale", None)]  # fmt: skip
    assert got["buckets"]["applied"] == [9] and got["items"][0]["reason"] == "body, +bug, -old, -stale"
    digest = _written(["keep", "bug"])
    assert _ledger_rows(tmp_path) == [
        {"result": "applied", "fields": ["body"], "add_labels": ["bug"], "remove_labels": ["old", "stale"], "digest": digest}
    ]


def test_write_failing_first_leaves_no_record(tmp_path):
    gh = StubRest(["old", "stale"], fail_at=1)
    got = actions.apply_actions(gh, actions.Ledger(tmp_path), _stub_plan(gh))
    assert got["buckets"]["failed"] == [9] and got["failed"] == [{"number": 9, "error": "落ちた: PATCH /repos/acme/widgets/issues/9"}]
    assert gh.writes == [] and _ledger_rows(tmp_path) == []


def test_write_failing_midway_records_partial_digest_and_resumes(tmp_path):
    gh = StubRest(["old", "stale"], fail_at=4)
    plan = _stub_plan(gh)
    got = actions.apply_actions(gh, actions.Ledger(tmp_path), plan)
    # 本文・ラベルの追加・1 つ目の外しまで書けて、2 つ目の外しで落ちる
    assert got["buckets"]["failed"] == [9] and [w[0] for w in gh.writes] == ["PATCH", "POST", "DELETE"]
    assert _ledger_rows(tmp_path) == [{"result": "partial", "digest": _written(["stale", "bug"])}]
    # 打ち直しは自分の書き込みを並行の更新と取り違えず、残りだけを書く
    again = actions.apply_actions(gh, actions.Ledger(tmp_path), plan)
    assert again["buckets"]["applied"] == [9] and again["items"][0]["reason"] == "-stale"
    assert gh.writes[3:] == [("DELETE", "/labels/stale", None)] and gh.cur["labels"] == ["bug"]
    assert _ledger_rows(tmp_path)[0]["result"] == "applied" and _ledger_rows(tmp_path)[0]["remove_labels"] == ["stale"]


def test_write_failing_after_patch_records_partial(tmp_path):
    gh = StubRest(["old"], fail_at=2)
    got = actions.apply_actions(gh, actions.Ledger(tmp_path), _stub_plan(gh))
    assert got["buckets"]["failed"] == [9] and gh.writes == [("PATCH", "", {"body": "新"})]
    assert _ledger_rows(tmp_path) == [{"result": "partial", "digest": _written(["old"])}]


def test_apply_adds_the_rank_section_to_a_description_without_one(repo_dir, fake, run_issues, tmp_path):
    for n in range(1, 9):
        fake.issue(n, milestone="01")
    fake.milestones = {"01": 1}
    fake.descriptions = {1: "目的"}
    ranked = _rank(run_issues, tmp_path)
    got = _apply(run_issues, tmp_path, {"rank": ranked["metrics"]["digest"]})
    assert got["status"] == "ok" and got["metrics"]["tables"]["written"] == ["01"]
    assert fake.descriptions[1].startswith("目的\n\n### 順位\n\n| 順位 |")
