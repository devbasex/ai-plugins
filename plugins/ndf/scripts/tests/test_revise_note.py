"""出し直しの指摘の部品（#2510）: 項目の数え方・回の集め方・表の突き合わせ・履歴の追記・プランの分岐・コマンド。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import supervise_lib  # noqa: E402,F401  lib/ を sys.path へ足す
import revise_note as rn  # noqa: E402
from supervise_lib import sprint_waves  # noqa: E402


def row(note="- 絞る", by=None, count=1, at="2026-11-05T00:00:00Z", missing=None):
    w = {"number": 7, "answer": "revise", "note": note, "revise_count": count, "answered_at": at, "pr": 50}
    return {**w, **({"by": by} if by else {}), **({"missing": missing} if missing else {})}


def data_of(*notes):
    rec = {"name": "l1", "waiting": [row(n, count=i) for i, n in enumerate(notes, 1)]}
    return {"issue": 7, "rounds": rn.rounds_of(rec, 7)}


@pytest.mark.parametrize(
    "note, want",
    [
        ("- 一\n* 二\n1. 三\n2) 四\n\n段落", ["一", "二", "三", "四"]),
        ("段落だけ\n続き", ["段落だけ\n続き"]),
        ("```\n- 中\n```\n- 外", ["外"]),
        ("", []),
    ],
)
def test_items_are_bullets_and_numbers_or_the_whole_text(note, want):
    assert rn.split_items(note) == want


def test_a_user_round_counts_items_and_an_auto_round_only_names_missing():
    rec = {"name": "l1", "waiting": [row("- 一\n- 二"), row("", by="auto", count=2, missing=["l1:1-2"]), {"number": 8, "answer": "revise"}]}
    user, auto = rn.rounds_of(rec, 7)
    assert [i["id"] for i in user["items"]] == ["l1:1-1", "l1:1-2"] and user["by"] == "user"
    assert auto["items"] == [] and auto["by"] == "auto" and auto["missing"] == ["l1:1-2"] and auto["note"] == ""


def test_rounds_come_from_every_loop_in_time_order_and_ids_do_not_clash(tmp_path):
    other = tmp_path / ".ndf/loop/old/loop.json"
    other.parent.mkdir(parents=True)
    other.write_text(json.dumps({"waiting": [row("- 古い", at="2026-11-01T00:00:00Z")]}))
    broken = tmp_path / ".ndf/loop/bad/loop.json"
    broken.parent.mkdir()
    broken.write_text("{")
    rec = {"name": "l1", "waiting": [row("- 新しい")]}
    data, skipped = rn.collect_rounds(tmp_path, rec, 7)
    assert [(r["loop"], r["round"]) for r in data["rounds"]] == [("old", 1), ("l1", 1)]
    assert rn.note_item_ids(data) == ["old:1-1", "l1:1-1"] and skipped == ["bad"]


def test_an_issue_without_rounds_has_no_input(tmp_path):
    assert rn.collect_rounds(tmp_path, {"name": "l1", "waiting": []}, 7) == (None, [])


TABLE = """## 出し直しの指摘への対応

| 項目 | 指摘 | 対応 | 直した箇所・直せない理由 |
| --- | --- | --- | --- |
| l1:1-1 | 絞る | 直した | 対象範囲 |
| `l1:1-2` | 分ける | 直せない |  |
"""


def fake_gh(body):
    box = {"body": body, "calls": []}

    def run(cmd, cwd=None, stdin=None):
        box["calls"].append(cmd)
        if cmd[:3] == ["gh", "pr", "view"]:
            return (1, "", "切れた") if box.get("broken") else (0, box["body"], "")
        box["body"] = stdin
        return 0, "", ""

    box["run"] = run
    return box


def test_an_empty_cell_and_a_missing_row_are_reported_and_the_doc_table_is_copied():
    gh = fake_gh("## Summary\n\n別の表\n\n## 出し直しの指摘への対応\n\n古い表\n")
    got = rn.check_table(gh["run"], ".", 50, TABLE, data_of("- 絞る\n- 分ける\n- 足す"))
    assert got["code"] == rn.CHECK_MISSING and got["missing"] == ["l1:1-2", "l1:1-3"]
    assert "古い表" not in gh["body"] and "| l1:1-1 | 絞る | 直した | 対象範囲 |" in gh["body"] and "別の表" in gh["body"]


def test_a_complete_table_passes_and_an_equal_body_is_not_written():
    gh = fake_gh(TABLE)
    data = data_of("- 絞る")
    assert rn.check_table(gh["run"], ".", 50, TABLE, data) == {"code": 0, "missing": [], "reason": ""}
    assert not any(c[:3] == ["gh", "pr", "edit"] for c in gh["calls"])


def test_no_table_misses_every_item_and_an_unreadable_pr_is_2():
    gh = fake_gh("")
    assert rn.check_table(gh["run"], ".", 50, "# 設計\n", data_of("- 一"))["missing"] == ["l1:1-1"]
    gh["broken"] = True
    assert rn.check_table(gh["run"], ".", 50, TABLE, data_of("- 一"))["code"] == rn.CHECK_UNREADABLE


def test_auto_return_is_allowed_once_per_human_round():
    rec = {"waiting": [row()]}
    assert rn.may_return(rec, 7)
    rec["waiting"].append(row(by="auto", count=2))
    assert not rn.may_return(rec, 7)
    rec["waiting"].append(row(count=3))
    assert rn.may_return(rec, 7)


def test_history_appends_keeps_the_note_and_skips_the_same_round():
    first = rn.with_history("本文\n", "l1", row("## 見出し\n- 絞る"))
    assert rn.with_history(first, "l1", row()) == first  # 同じループの同じ回は足さない
    other = rn.with_history(first, "l2", row("- 別のループ"))
    both = rn.with_history(other, "l1", row("", by="auto", count=2, missing=["l1:1-1"]))
    text = both[both.index(rn.HISTORY) :]
    assert "> ## 見出し" in text and "> - 別のループ" in text and "届いていない項目: l1:1-1" in text
    assert "第 1 回（2026-11-05・設計 PR #50・開発ループ l1・人）" in text and "開発ループ l2" in text and "・自動）" in text
    assert both.count(rn.HISTORY) == 1


def plan_args(tmp_path, notes=None):
    return SimpleNamespace(
        base="main", mode="standard", name="m1", worktree=str(tmp_path), state=str(tmp_path / "state"), revise_notes=notes
    )


def steps_of(plan):
    return {s["id"]: s for s in plan["steps"]}


def input_file(tmp_path, *notes):
    p = tmp_path / "revise-42.json"
    p.write_text(json.dumps(data_of(*notes), ensure_ascii=False))
    return str(p)


def test_a_plan_with_notes_always_runs_requirements_and_carries_every_round(tmp_path):
    path = input_file(tmp_path, "- 第一の指摘", "- 第二の指摘")
    ds = steps_of(sprint_waves.plan_mvv_design(plan_args(tmp_path, {42: path}), 42, str(tmp_path)))
    assert "requirements-check" not in ds and ds["glossary"]["next"] == "requirements"
    for sid in ("requirements", "design"):
        assert "第一の指摘" in ds[sid]["prompt"] and "第二の指摘" in ds[sid]["prompt"] and rn.ASK in ds[sid]["prompt"]
    assert rn.TABLE in ds["design"]["prompt"] and rn.TABLE not in ds["requirements"]["prompt"]
    assert ds["push-glossary"]["next"] == "revise-check" and ds["revise-check"]["next"] == "mvv"
    assert ds["revise-check"]["gate_next"] == "end" and f"--notes {path}" in ds["revise-check"]["cmd"]
    assert f"--revise-note {path}" in ds["mvv"]["cmd"] and ds["push-glossary-gate"]["next"] == "ready"


def test_the_base_plan_with_notes_goes_on_to_ready_after_the_check(tmp_path):
    path = input_file(tmp_path, "- 指摘")
    ds = steps_of(sprint_waves.plan_sprint_design(plan_args(tmp_path, {42: path}), 42, str(tmp_path)))
    assert ds["push-glossary"]["next"] == "revise-check" and ds["revise-check"]["next"] == "ready"


def test_a_plan_without_notes_is_unchanged(tmp_path):
    plain = sprint_waves.plan_mvv_design(plan_args(tmp_path), 42, str(tmp_path))
    other = input_file(tmp_path, "- 別の課題の指摘")
    assert plain == sprint_waves.plan_mvv_design(plan_args(tmp_path, {43: other}), 42, str(tmp_path))
    ds = steps_of(plain)
    assert ds["glossary"]["next"] == "requirements-check" and ds["requirements-check"]["next"] == "design" and "revise-check" not in ds
    assert "--revise-note" not in ds["mvv"]["cmd"] and "指摘の全回" not in ds["design"]["prompt"]


def run_check(tmp_path, doc: str, body: str = "") -> tuple[int, dict]:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (tmp_path / "body.md").write_text(body)
    gh = bindir / "gh"
    gh.write_text(f'#!/bin/sh\nif [ "$2" = view ]; then cat {tmp_path / "body.md"}; else cat > {tmp_path / "body.md"}; fi\n')
    gh.chmod(0o755)
    (tmp_path / "doc.md").write_text(doc)
    args = ["check", "--pr", "50", "--doc", str(tmp_path / "doc.md"), "--notes", input_file(tmp_path, "- 絞る")]
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    p = subprocess.run([sys.executable, str(SCRIPTS / "revise-note.py"), *args], capture_output=True, text=True, env=env, cwd=tmp_path)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def test_the_command_exits_0_when_complete_and_copies_the_table(tmp_path):
    code, res = run_check(tmp_path, f"# 設計\n\n{TABLE}", "## Summary\n\nx\n")
    assert code == 0 and res["status"] == "ok" and "| l1:1-1 |" in (tmp_path / "body.md").read_text()


def test_the_command_exits_10_on_a_gap_and_2_when_it_cannot_read(tmp_path):
    code, res = run_check(tmp_path, "# 設計\n")
    assert code == 10 and res["status"] == "gate" and res["items"] == [{"item": "l1:1-1", "result": "missing"}]
    (tmp_path / "doc.md").unlink()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    args = ["check", "--pr", "50", "--doc", str(tmp_path / "doc.md"), "--notes", str(tmp_path / "revise-42.json")]
    p = subprocess.run([sys.executable, str(SCRIPTS / "revise-note.py"), *args], capture_output=True, text=True, env=env)
    assert p.returncode == 2
