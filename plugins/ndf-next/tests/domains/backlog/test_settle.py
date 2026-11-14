"""`ndf issues settle`: 証拠を確かめてから閉じる（#3395 の「閉じる操作」・IY5・3395-6）。"""

from __future__ import annotations

import json
from pathlib import Path

from ndf.domains.backlog import cli, settle_decide

TEST = "tests/domains/backlog/test_x.py::test_reproduces_2701"
PATCH = "@@ -0,0 +1,3 @@\n+def test_reproduces_2701():\n+    assert True\n"


def _pull(merged: bool = True, merged_at: str = "2026-11-14T00:00:00Z") -> dict:
    return {"number": 7, "merged": merged, "merged_at": merged_at, "base": {"ref": "develop"}}


def _settle(capsys, last_result, tmp_path: Path, rows: list, *extra: str) -> dict:
    path = tmp_path / "settle.json"
    path.write_text(json.dumps({"settle": rows}))
    cli.main(["settle", "--repo", "acme/widgets", "--file", str(path), *extra])
    return last_result(capsys.readouterr().out)


def _improved(issue: int = 2701, pr: int = 7) -> dict:
    return {"issue": issue, "child": "M6", "evidence": {"kind": "improved", "pr": pr, "test": TEST}}


def _with_test(fake, merged: bool = True) -> None:
    fake.issue(2701)
    fake.pulls[7] = _pull(merged)
    fake.pull_files[7] = [{"filename": TEST.split("::")[0], "patch": PATCH}]
    fake.contents[(TEST.split("::")[0], "develop")] = "def test_reproduces_2701():\n    assert True\n"


def test_unmerged_evidence_does_not_close_and_stops(repo_dir, fake, capsys, last_result, tmp_path):
    # 証拠のテストがマージされていない行は閉じずに stopped を返す
    _with_test(fake, merged=False)
    got = _settle(capsys, last_result, tmp_path, [_improved()])
    assert got["status"] == "stopped" and got["cause"]["kind"] == "refused"
    assert got["items"][0]["result"] == "unverified" and "マージされていない" in got["items"][0]["reason"]
    assert fake.issues[2701]["state"] == "open" and fake.writes == []


def test_improved_evidence_comments_then_closes(repo_dir, fake, capsys, last_result, tmp_path):
    _with_test(fake)
    got = _settle(capsys, last_result, tmp_path, [_improved()])
    assert got["status"] == "ok" and got["items"][0]["result"] == "closed"
    assert [w[0] for w in fake.writes] == ["POST", "PATCH"]
    assert TEST in fake.writes[0][2]["body"]
    assert fake.issues[2701]["state"] == "closed"


def test_dry_run_verifies_without_closing(repo_dir, fake, capsys, last_result, tmp_path):
    _with_test(fake)
    got = _settle(capsys, last_result, tmp_path, [_improved()], "--dry-run")
    assert got["status"] == "ok" and got["items"][0]["result"] == "verified" and fake.writes == []


def test_improved_needs_the_pr_to_add_the_test_function(repo_dir, fake, capsys, last_result, tmp_path):
    _with_test(fake)
    fake.pull_files[7] = [{"filename": TEST.split("::")[0], "patch": "@@\n def test_reproduces_2701():\n+    pass\n"}]
    got = _settle(capsys, last_result, tmp_path, [_improved()])
    assert got["status"] == "stopped" and "定義を足していない" in got["items"][0]["reason"]


def test_improved_needs_the_test_on_the_base_branch(repo_dir, fake, capsys, last_result, tmp_path):
    _with_test(fake)
    fake.contents.clear()
    got = _settle(capsys, last_result, tmp_path, [_improved()])
    assert got["status"] == "stopped" and "基底のブランチ" in got["items"][0]["reason"]


def test_improved_needs_the_pr_to_target_the_base_branch(repo_dir, fake, capsys, last_result, tmp_path):
    _with_test(fake)
    fake.pulls[7]["base"] = {"ref": "feature/x"}
    got = _settle(capsys, last_result, tmp_path, [_improved()])
    assert got["status"] == "stopped" and "宛先" in got["items"][0]["reason"] and fake.writes == []


def test_removed_path_evidence(repo_dir, fake, capsys, last_result, tmp_path):
    for n in (5, 6, 7, 8):
        fake.issue(n)
    fake.commits.add("abc123")
    fake.off_base.add("def456")
    # gone/old.py と still/here.py は abc123 の親にあり、abc123 で gone/old.py だけが消えた
    fake.contents[("gone/old.py", "abc123p")] = "x"
    fake.contents[("still/here.py", "abc123p")] = "x"
    fake.contents[("still/here.py", "develop")] = "x"
    rows = [{"issue": 5, "child": "S2", "evidence": {"kind": "removed", "path": "gone/old.py", "commit": "abc123"}},
            {"issue": 6, "child": "S2", "evidence": {"kind": "removed", "path": "still/here.py", "commit": "abc123"}},
            {"issue": 7, "child": "S2", "evidence": {"kind": "removed", "path": "never/was.py", "commit": "abc123"}},
            {"issue": 8, "child": "S2", "evidence": {"kind": "removed", "path": "gone/old.py", "commit": "def456"}}]  # fmt: skip
    got = _settle(capsys, last_result, tmp_path, rows)
    assert {i["issue"]: i["result"] for i in got["items"]} == {5: "closed", 6: "unverified", 7: "unverified", 8: "unverified"}
    assert "親に never/was.py が無い" in got["items"][2]["reason"] and "祖先でない" in got["items"][3]["reason"]
    assert got["status"] == "stopped" and all(fake.issues[n]["state"] == "open" for n in (6, 7, 8))


def test_not_reproduced_needs_exit_zero_a_base_record_and_a_past_time_after_the_merge(repo_dir, fake, capsys, last_result, tmp_path):
    for n in (2690, 2719, 2720, 2721, 2722):
        fake.issue(n)
    fake.pulls[7] = _pull(merged_at="2026-11-10T00:00:00Z")
    fake.contents[("trial.log", "develop")] = "ok\n"
    (repo_dir / "local.log").write_text("ok\n")
    ev = {"kind": "not_reproduced", "command": "ndf ci wait", "exit": 0, "record": "trial.log", "after_pr": 7, "at": "2026-11-11T00:00:00Z"}
    rows = [{"issue": 2690, "child": "T2", "evidence": ev},
            {"issue": 2719, "child": "T2", "evidence": {**ev, "at": "2026-11-10T00:00:00+09:00"}},
            {"issue": 2720, "child": "T2", "evidence": {**ev, "exit": 1}},
            {"issue": 2721, "child": "T2", "evidence": {**ev, "record": "local.log"}},
            {"issue": 2722, "child": "T2", "evidence": {**ev, "at": "2999-01-01T00:00:00Z"}}]  # fmt: skip
    got = _settle(capsys, last_result, tmp_path, rows)
    assert {i["issue"]: i["result"] for i in got["items"]} == {2690: "closed", 2719: "unverified", 2720: "unverified",
                                                               2721: "unverified", 2722: "unverified"}  # fmt: skip


def test_already_closed_issue_is_left_alone(repo_dir, fake, capsys, last_result, tmp_path):
    _with_test(fake)
    fake.issues[2701]["state"] = "closed"
    got = _settle(capsys, last_result, tmp_path, [_improved()])
    assert got["status"] == "ok" and got["items"][0]["result"] == "already_closed" and fake.writes == []


def test_malformed_rows_are_reported_and_not_closed(repo_dir, fake, capsys, last_result, tmp_path):
    rows = [{"issue": 1, "evidence": {"kind": "improved", "pr": 7}}, {"issue": 0, "evidence": {}}]
    got = _settle(capsys, last_result, tmp_path, rows)
    assert got["status"] == "stopped" and [i["result"] for i in got["items"]] == ["unverified", "unverified"]
    assert fake.writes == []


def test_input_shape_and_helpers():
    rows, bad = settle_decide.parse({"settle": [_improved()]})
    assert rows[0].kind == "improved" and bad == []
    assert settle_decide.test_target("a/b.py::C::test_x[1]") == ("a/b.py", "test_x")
    assert settle_decide.parse_time("2026-11-14") is None
    assert settle_decide.adds_def(PATCH, "test_reproduces_2701") and not settle_decide.adds_def(PATCH, "test_other")
