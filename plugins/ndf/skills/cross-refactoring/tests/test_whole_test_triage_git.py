"""危険フラグで走らせた全体のテストが落ちたときの扱い（#933 決定 22 / 実装計画 I14）を**実際の git**で確かめる。

| 振る舞い | 確かめること |
| --- | --- |
| 揺れ | 落ちたテストだけを今の HEAD で走らせ直して通れば、取り消さない |
| 元からの失敗 | 着手前の HEAD（一時の作業ツリー）でも落ちれば、取り消さない。一時の作業ツリーは残さない |
| 直しを試みる | 変更が原因なら修正へ回し、落ちたテストだけを走らせ直して通れば残す |
| 締め切りで絞る | 修正に使える時間が無ければ、新しい順に 1 件ずつ取り消し、通った時点で止める |
| 取り出せない | 実行器が pytest でなければ、今と同じく危険フラグの項目をまとめて取り消し、理由を残す |
| 基準を示す | 報告と改修計画に着手前の全体のテストの結果と HEAD が出る |
"""
from __future__ import annotations

import argparse

import pytest

from crossref_helpers import (
    CALC,
    TEST_TOTAL,
    build_git_flow,
    commit_with_trailers,
    git,
    item_trailers,
    read_state,
    write_state,
)

FAR = "2099-01-01T00:00:00+00:00"


@pytest.fixture
def flow(tmp_path, monkeypatch, refactor, patch_lib, env_tmp_dir):
    return build_git_flow(tmp_path, monkeypatch, patch_lib, env_tmp_dir)


def _item(item_id, rank, symbol="add"):
    return {
        "id": item_id, "rank": rank, "path": "src/calc.py", "symbol": symbol,
        "smell": "long_method", "technique": "extract_method", "severity": "major",
        "proposed_by": ["codex"], "tier": "high", "risk": False,
        "tests": [], "test_targets": ["tests/test_calc.py"],
        "command": ["pytest", "-q", "tests/test_calc.py"], "command_source": "targets",
        "estimate": {"test": 0.0, "implement": 1.3, "verify": 0.2},
        "start_deadline": FAR, "test_start_deadline": None,
        "status": "planned", "commits": {"test": None, "implement": None, "fix": []},
        "seconds": {}, "fix_count": 0, "danger": [], "estimated_diff_lines": 20,
    }


def _call(module, name, **kwargs):
    return getattr(module, name)(argparse.Namespace(id=130, **kwargs))


def _emitted(capsys, key):
    out = capsys.readouterr().out
    values = [line.split("=", 1)[1] for line in out.splitlines() if line.startswith(key + "=")]
    return values[-1] if values else None


def _write(work, rel, text):
    path = work / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _head(work):
    return git("rev-parse", "HEAD", cwd=work).stdout.strip()


def _existing_tests(flow, files):
    """着手前からあるテストを足し、その HEAD を `init` の基準の SHA として記録する。"""
    for rel, text in files.items():
        _write(flow["work"], rel, text)
    commit_with_trailers(flow["work"], "既存のテスト", {})
    state = read_state(flow["path"])
    state["baseline_test"]["head"] = _head(flow["work"])
    write_state(flow["path"], state)


def _implement(flow, cmd_setup, cmd_implement, changes, **state_overrides):
    """項目ごとに `changes[id](work)` を当てて 1 コミットずつ積み、取り込む。"""
    work = flow["work"]
    state = read_state(flow["path"])
    state["items"] = [_item(item_id, rank) for rank, item_id in enumerate(changes, start=1)]
    state["plan"] = {"base_sha": _head(work),
                     "reserve": {"danger_whole_test": 0.1, "final_whole_test": 0.1, "fix": 5.5},
                     "end_at": FAR, "table_source": "defaults"}
    state["phase"] = "implement"
    state.update(state_overrides)
    write_state(flow["path"], state)
    _call(cmd_setup, "cmd_start_phase", phase="implement")
    for item_id, change in changes.items():
        change(work)
        commit_with_trailers(work, f"Refactor {item_id}", item_trailers(item_id))
    _call(cmd_implement, "cmd_merge_implement")


def _items(flow):
    return {i["id"]: i for i in read_state(flow["path"])["items"]}


def _touch_other(name):
    """項目の `path` の外のファイルを触る（D1 が立つ）。"""
    return lambda w: _write(w, f"src/{name}.py", "X = 1\n")


def _break_total(w):
    _write(w, "src/extra.py", "X = 1\n")
    _write(w, "src/calc.py", CALC.replace("return result", "return result + 1"))


def _worktrees(work):
    return [line for line in git("worktree", "list", cwd=work).stdout.splitlines() if line.strip()]


def test_a_flaky_failure_is_not_reverted(flow, cmd_setup, cmd_implement, cmd_converge,
                                         tmp_path, capsys):
    mark = tmp_path / "flaky-mark"
    _existing_tests(flow, {"tests/test_flaky.py": (
        "import pathlib\n\n\ndef test_once():\n"
        f"    mark = pathlib.Path({str(mark)!r})\n"
        "    if not mark.exists():\n        mark.write_text('x')\n        assert False\n")})
    _implement(flow, cmd_setup, cmd_implement, {"I-001": _touch_other("other")})
    capsys.readouterr()

    _call(cmd_converge, "cmd_verify")

    assert _emitted(capsys, "VERIFY") == "done"
    record = read_state(flow["path"])["whole_test"]
    assert record["status"] == "fail"
    assert record["flaky"] == ["tests/test_flaky.py::test_once"]
    assert record["caused"] == [] and record["reverted"] is False
    assert _items(flow)["I-001"]["status"] == "verified"


def test_a_failure_already_present_at_the_start_is_not_reverted(
        flow, cmd_setup, cmd_implement, cmd_converge, tmp_path, capsys):
    mark = tmp_path / "env-mark"
    _existing_tests(flow, {"tests/test_env.py": (
        "import pathlib\n\n\ndef test_env():\n"
        f"    assert not pathlib.Path({str(mark)!r}).exists()\n")})
    _implement(flow, cmd_setup, cmd_implement, {"I-001": _touch_other("other")})
    mark.write_text("x")  # 着手の後に環境が変わり、どの HEAD でも落ちる
    capsys.readouterr()

    _call(cmd_converge, "cmd_verify")

    assert _emitted(capsys, "VERIFY") == "done"
    record = read_state(flow["path"])["whole_test"]
    assert record["preexisting"] == ["tests/test_env.py::test_env"]
    assert record["caused"] == [] and record["reverted"] is False
    assert _items(flow)["I-001"]["status"] == "verified"
    # 着手前の HEAD は一時の作業ツリーで走らせ、作業ディレクトリは動かさず、後で消す
    assert len(_worktrees(flow["work"])) == 1
    assert git("status", "--porcelain", cwd=flow["work"]).stdout.strip() == ""


def test_a_failure_caused_by_the_change_goes_to_fix_and_is_kept_when_fixed(
        flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    work = flow["work"]
    _existing_tests(flow, {"tests/test_total.py": TEST_TOTAL})
    _implement(flow, cmd_setup, cmd_implement, {"I-001": _break_total})
    capsys.readouterr()

    _call(cmd_converge, "cmd_verify")

    assert _emitted(capsys, "VERIFY") == "fix"
    state = read_state(flow["path"])
    assert state["whole_test"]["caused"] == ["tests/test_total.py::test_total"]
    assert state["fix"]["items"] == ["I-001"]
    item = _items(flow)["I-001"]
    assert item["status"] == "failing"
    # 修正担当へは、落ちたテストだけを走らせ直すコマンドとその出力を渡す
    assert item["whole_test_command"] == ["pytest", "-q", "tests/test_total.py::test_total"]
    with open(item["last_log"], encoding="utf-8") as fh:
        assert "test_total" in fh.read()

    _call(cmd_setup, "cmd_start_phase", phase="fix")
    _write(work, "src/calc.py", CALC)
    commit_with_trailers(work, "Fix", item_trailers("I-001"))
    _call(cmd_converge, "cmd_merge_fix")
    capsys.readouterr()
    _call(cmd_converge, "cmd_verify")

    assert _emitted(capsys, "VERIFY") == "done"
    record = read_state(flow["path"])["whole_test"]
    assert record["resolution"] == "fixed" and record["reverted"] is False
    item = _items(flow)["I-001"]
    assert item["status"] == "verified" and "whole_test_command" not in item


def test_without_time_to_fix_the_newest_flagged_items_are_reverted_until_it_passes(
        flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    work = flow["work"]
    _existing_tests(flow, {"tests/test_total.py": TEST_TOTAL})
    # 始まりを過去へ置き、修正に使える時間を残さない
    _implement(flow, cmd_setup, cmd_implement,
               {"I-001": _break_total, "I-002": _touch_other("other"),
                "I-003": _touch_other("another")},
               started_at="2000-01-01T00:00:00+00:00")
    capsys.readouterr()

    _call(cmd_converge, "cmd_verify")

    assert _emitted(capsys, "VERIFY") == "done"
    items = _items(flow)
    # 新しい順に取り消し、I-001（原因）を取り消した時点で通るまで 1 件ずつ
    assert [items[i]["status"] for i in ("I-003", "I-002", "I-001")] == \
        ["reverted", "reverted", "reverted"]
    record = read_state(flow["path"])["whole_test"]
    assert record["resolution"] == "narrowed" and record["reverted"] is True
    assert "return result + 1" not in (work / "src" / "calc.py").read_text()


def test_narrowing_stops_as_soon_as_the_failed_tests_pass(
        flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    work = flow["work"]
    _existing_tests(flow, {"tests/test_total.py": TEST_TOTAL})
    _implement(flow, cmd_setup, cmd_implement,
               {"I-001": _touch_other("other"), "I-002": _break_total},
               started_at="2000-01-01T00:00:00+00:00")
    capsys.readouterr()

    _call(cmd_converge, "cmd_verify")

    assert _emitted(capsys, "VERIFY") == "done"
    items = _items(flow)
    assert items["I-002"]["status"] == "reverted"
    assert items["I-001"]["status"] == "verified"
    assert (work / "src" / "other.py").exists()
    assert read_state(flow["path"])["whole_test"]["resolution"] == "narrowed"


def test_without_failed_test_ids_the_flagged_items_are_reverted_together(
        flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    _existing_tests(flow, {"tests/test_total.py": TEST_TOTAL})
    state = read_state(flow["path"])
    state["baseline_test"]["command"] = "env pytest -q tests"  # 実行器が pytest と読めない
    write_state(flow["path"], state)
    _implement(flow, cmd_setup, cmd_implement,
               {"I-001": _touch_other("other"), "I-002": _break_total})
    capsys.readouterr()

    _call(cmd_converge, "cmd_verify")

    assert _emitted(capsys, "VERIFY") == "done"
    items = _items(flow)
    assert items["I-001"]["status"] == "reverted" and items["I-002"]["status"] == "reverted"
    record = read_state(flow["path"])["whole_test"]
    assert record["resolution"] == "reverted_all" and record["reverted"] is True
    assert record["unparsed_reason"]


def test_the_report_and_the_plan_show_the_baseline(flow, cmd_report, plan, capsys):
    state = read_state(flow["path"])
    state["baseline_test"].update({"head": "0123456789abcdef", "seconds": 12.5})
    write_state(flow["path"], state)

    _call(cmd_report, "cmd_report", metrics=False)
    out = capsys.readouterr().out
    body = plan.format_plan(read_state(flow["path"]))

    for text in (out, body):
        assert "0123456" in text and "12.5" in text
