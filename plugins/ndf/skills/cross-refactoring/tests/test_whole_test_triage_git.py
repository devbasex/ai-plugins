"""危険フラグで走らせた全体のテストが落ちたときの扱い（#933 決定 22 / 実装計画 I14）を**実際の git**で確かめる。

| 振る舞い | 確かめること |
| --- | --- |
| 揺れ | 落ちたテストだけを今の HEAD で走らせ直して通れば、取り消さない |
| 元からの失敗 | 着手前の HEAD（一時の作業ツリー）でも落ちれば、取り消さない。一時の作業ツリーは残さない |
| 直しを試みる | 変更が原因なら修正へ回し、落ちたテストだけを走らせ直して通れば残す |
| 締め切りで絞る | 修正に使える時間が無ければ、新しい順に 1 件ずつ取り消し、通った時点で止める |
| 見分けられない | JUnit が無ければ全体を 1 度走らせ直し、落としたことと理由を残す。着手前が green なら変更起因、red ならまとめて取り消す |
| CI に任せる | `local-scoped-ci-whole` は検証の中で全体テストを走らせず、最終ゲートへ寄せる |
| 基準を示す | 報告と改修計画に着手前の全体のテストの結果と HEAD が出る |
"""

from __future__ import annotations

import argparse
import sys

import pytest

from crossref_helpers import (
    verify_judged,
    CALC,
    SCOPE_COMMAND,
    TEST_TOTAL,
    build_git_flow,
    commit_existing_tests,
    commit_with_trailers,
    git,
    implement_items,
    item_trailers,
    read_state,
    strategy_state,
    write_state,
)

FAR = "2099-01-01T00:00:00+00:00"


@pytest.fixture
def flow(tmp_path, monkeypatch, refactor, patch_lib, env_tmp_dir):
    return build_git_flow(tmp_path, monkeypatch, patch_lib, env_tmp_dir)


def _item(item_id, rank, symbol="add"):
    return {
        "id": item_id,
        "rank": rank,
        "path": "src/calc.py",
        "symbol": symbol,
        "smell": "long_method",
        "technique": "extract_method",
        "severity": "major",
        "proposed_by": ["codex"],
        "tier": "high",
        "risk": False,
        "tests": [],
        "test_targets": ["tests/test_calc.py"],
        "command": ["pytest", "-q", "tests/test_calc.py"],
        "command_source": "targets",
        "estimate": {"test": 0.0, "implement": 1.3, "verify": 0.2},
        "start_deadline": FAR,
        "test_start_deadline": None,
        "status": "planned",
        "commits": {"test": None, "implement": None, "fix": []},
        "seconds": {},
        "fix_count": 0,
        "danger": [],
        "estimated_diff_lines": 20,
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


def _implement(flow, cmd_setup, cmd_phases, cmd_implement, changes, late=False, **state_overrides):
    """項目ごとに `changes[id](work)` を当てて 1 コミットずつ積み、取り込む。"""
    items = [_item(item_id, rank) for rank, item_id in enumerate(changes, start=1)]
    implement_items(flow, cmd_phases, cmd_implement, items, changes, late=late, **state_overrides)


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


def test_a_flaky_failure_is_not_reverted(flow, cmd_setup, cmd_phases, cmd_implement, cmd_converge, tmp_path, capsys):
    mark = tmp_path / "flaky-mark"
    commit_existing_tests(
        flow,
        {
            "tests/test_flaky.py": (
                "import pathlib\n\n\ndef test_once():\n"
                f"    mark = pathlib.Path({str(mark)!r})\n"
                "    if not mark.exists():\n        mark.write_text('x')\n        assert False\n"
            )
        },
    )
    _implement(flow, cmd_setup, cmd_phases, cmd_implement, {"I-001": _touch_other("other")})
    capsys.readouterr()

    _call(cmd_converge, "cmd_verify")

    assert _emitted(capsys, "VERIFY") == "done"
    record = read_state(flow["path"])["whole_test"]
    assert record["status"] == "fail"
    # ID は JUnit（xunit1）の `file::classname::name`（#1334 決定 5）。
    assert record["flaky"] == ["tests/test_flaky.py::tests.test_flaky::test_once"]
    assert record["caused"] == [] and record["reverted"] is False and "culprit" not in record
    assert _items(flow)["I-001"]["status"] == "verified"
    # 受け入れ条件 11: 見分けたフレーキーは、その実行の中での直しの依頼に積まれる（項目の合否には数えない）
    request = read_state(flow["path"])["flaky_request"]
    assert request["tests"] == record["flaky"] and request["commands"]


def test_a_failure_already_present_at_the_start_is_not_reverted(flow, cmd_setup, cmd_phases, cmd_implement, cmd_converge, tmp_path, capsys):
    mark = tmp_path / "env-mark"
    commit_existing_tests(
        flow, {"tests/test_env.py": (f"import pathlib\n\n\ndef test_env():\n    assert not pathlib.Path({str(mark)!r}).exists()\n")}
    )
    _implement(flow, cmd_setup, cmd_phases, cmd_implement, {"I-001": _touch_other("other")})
    mark.write_text("x")  # 着手の後に環境が変わり、どの HEAD でも落ちる
    capsys.readouterr()

    _call(cmd_converge, "cmd_verify")

    assert _emitted(capsys, "VERIFY") == "done"
    record = read_state(flow["path"])["whole_test"]
    assert record["preexisting"] == ["tests/test_env.py::tests.test_env::test_env"]
    assert record["caused"] == [] and record["reverted"] is False and "culprit" not in record
    assert _items(flow)["I-001"]["status"] == "verified"
    # 着手前の HEAD は一時の作業ツリーで走らせ、作業ディレクトリは動かさず、後で消す
    assert len(_worktrees(flow["work"])) == 1
    assert git("status", "--porcelain", cwd=flow["work"]).stdout.strip() == ""


def test_a_failure_from_the_merged_base_branch_is_unrelated_and_keeps_the_item(flow, cmd_setup, cmd_phases, cmd_implement, cmd_converge, capsys):
    """#2457 受け入れ条件 10（PR 2452 の形）: 着手の後に取り込んだベースブランチで落ちたテストは、比較の木
    （項目のコミットを外した木）でも落ちるため無関係と記録され、通っていた項目は取り消されない。"""
    work = flow["work"]
    commit_existing_tests(
        flow,
        {
            "src/threshold.txt": "10\n",
            "tests/test_threshold.py": (
                "import pathlib\n\n\ndef test_threshold():\n    assert int(pathlib.Path('src/threshold.txt').read_text()) >= 10\n"
            ),
        },
    )
    start = _head(work)
    _implement(flow, cmd_setup, cmd_phases, cmd_implement, {"I-001": _touch_other("other")})
    git("checkout", "-q", "-b", "base-update", start, cwd=work)
    _write(work, "src/threshold.txt", "5\n")
    commit_with_trailers(work, "ベースブランチでしきい値を下げた", {})
    git("checkout", "-q", "-", cwd=work)
    git("merge", "-q", "--no-edit", "base-update", cwd=work)
    capsys.readouterr()

    _call(cmd_converge, "cmd_verify")

    assert _emitted(capsys, "VERIFY") == "done"
    record = read_state(flow["path"])["whole_test"]
    assert record["preexisting"] == ["tests/test_threshold.py::tests.test_threshold::test_threshold"]
    assert record["caused"] == [] and not record.get("reverted")
    assert record["comparison_tree"] and record["comparison_tree"] != start
    assert record["comparison_fallback"] is None
    assert _items(flow)["I-001"]["status"] == "verified"
    assert (work / "src" / "other.py").exists()
    assert len(_worktrees(work)) == 1, "比較の木の一時の worktree を残さない"


def test_a_failure_caused_by_the_change_goes_to_fix_and_is_kept_when_fixed(
    flow, cmd_setup, cmd_phases, cmd_implement, cmd_converge, cmd_merge_fix, capsys
):
    work = flow["work"]
    commit_existing_tests(flow, {"tests/test_total.py": TEST_TOTAL})
    _implement(flow, cmd_setup, cmd_phases, cmd_implement, {"I-001": _break_total})

    assert verify_judged(cmd_converge, flow["path"], capsys) == "fix"
    state = read_state(flow["path"])
    assert state["whole_test"]["caused"] == ["tests/test_total.py::tests.test_total::test_total"]
    assert state["fix"]["items"] == ["I-001"]
    item = _items(flow)["I-001"]
    assert item["status"] == "failing"
    # 修正担当へは、落ちたテストのファイルだけを走らせ直すコマンド（雛形の {paths} の置き換え）とその出力を渡す
    assert item["whole_test_command"] == [" ".join(SCOPE_COMMAND.split()[:-1] + ["tests/test_total.py"])]
    with open(item["last_log"], encoding="utf-8") as fh:
        assert "test_total" in fh.read()

    _call(cmd_phases, "cmd_start_phase", phase="fix")
    _write(work, "src/calc.py", CALC)
    commit_with_trailers(work, "Fix", item_trailers("I-001"))
    _call(cmd_merge_fix, "cmd_merge_fix")
    capsys.readouterr()
    _call(cmd_converge, "cmd_verify")

    assert _emitted(capsys, "VERIFY") == "done"
    record = read_state(flow["path"])["whole_test"]
    assert record["resolution"] == "fixed" and record["reverted"] is False
    item = _items(flow)["I-001"]
    assert item["status"] == "verified" and "whole_test_command" not in item


def test_after_the_deadline_the_whole_test_failure_still_goes_to_fix(flow, cmd_setup, cmd_phases, cmd_implement, cmd_converge, capsys):
    """#2457 受け入れ条件 5 — 始まりが過去でも、全体テストの変更起因で取り消さず、原因の項目を修正へ回す。"""
    work = flow["work"]
    commit_existing_tests(flow, {"tests/test_total.py": TEST_TOTAL})
    _implement(
        flow,
        cmd_setup, cmd_phases,
        cmd_implement,
        {"I-001": _break_total, "I-002": _touch_other("other"), "I-003": _touch_other("another")},
        late=True,
    )

    assert verify_judged(cmd_converge, flow["path"], capsys) == "fix"
    items = _items(flow)
    assert items["I-001"]["status"] == "failing"
    assert items["I-002"]["status"] == "verified" and items["I-003"]["status"] == "verified"
    record = read_state(flow["path"])["whole_test"]
    assert record["resolution"] == "fixing" and not record.get("reverted")
    assert (work / "src" / "other.py").exists()


def test_without_junit_the_whole_test_is_rerun_and_a_green_baseline_sends_it_to_fix(flow, cmd_setup, cmd_phases, cmd_implement, cmd_converge, capsys):
    """AC6 — JUnit が無いときは見分けを全体の走らせ直しに落とし、落としたことが結果に出る。着手前が green なら変更起因。

    原因の項目は走らせ直しのログに現れるパスで決め、その項目だけを修正へ回す（#1649）。
    """
    commit_existing_tests(flow, {"tests/test_total.py": TEST_TOTAL})
    raises = lambda w: _write(w, "src/calc.py", CALC.replace("    return result\n", "    raise RuntimeError('broke')\n"))  # noqa: E731
    _implement(flow, cmd_setup, cmd_phases, cmd_implement, {"I-001": _touch_other("other"), "I-002": raises}, strategy=strategy_state(junit=False))

    assert verify_judged(cmd_converge, flow["path"], capsys) == "fix"
    record = read_state(flow["path"])["whole_test"]
    assert record["fallback_reason"] and record["fallback_rerun"] == "fail"
    assert record["resolution"] == "fixing" and record["failed_tests"] is None
    assert record["culprit"]["culprits"] == ["I-002"] and record["culprit"]["basis"] == "path"
    items = _items(flow)
    assert items["I-001"]["status"] == "verified" and items["I-002"]["status"] == "failing"


def test_without_junit_and_a_red_baseline_nothing_is_reverted_and_the_material_is_kept(
    flow, cmd_setup, cmd_phases, cmd_implement, cmd_converge, capsys
):
    """#2457 の棚卸し #35: JUnit が無く着手前も red なら、まとめて取り消さず、落ちたテストの材料を残して最終ゲートへ寄せる。"""
    commit_existing_tests(flow, {"tests/test_total.py": TEST_TOTAL})
    state = read_state(flow["path"])
    state["baseline_test"]["status"] = "red"
    write_state(flow["path"], state)
    _implement(
        flow, cmd_setup, cmd_phases, cmd_implement, {"I-001": _touch_other("other"), "I-002": _break_total}, strategy=strategy_state(junit=False)
    )
    capsys.readouterr()

    _call(cmd_converge, "cmd_verify")

    assert _emitted(capsys, "VERIFY") == "done"
    items = _items(flow)
    assert items["I-001"]["status"] == "verified" and items["I-002"]["status"] == "verified"
    record = read_state(flow["path"])["whole_test"]
    assert record["resolution"] == "deferred" and not record.get("reverted")
    assert record["fallback_reason"]
    material = record["material"]
    assert material["log"].endswith(".log") and material["commands"]


def test_a_ci_whole_strategy_defers_the_whole_test_to_the_final_gate(flow, cmd_setup, cmd_phases, cmd_implement, cmd_converge, capsys, patch_lib):
    """AC3・AC8 — 全体テストを CI に任せる戦略では、危険フラグが立っても検証の中で全体テストを走らせず、最終ゲートへ寄せる。"""
    whole_runs: list = []
    original = sys.modules["refactor_lib.targets"].run_with_timeout

    def spy_run(command, cwd, timeout, grace=5.0, output=None):
        if command == strategy_state("local-scoped-ci-whole")["suites"][0]["command"]:
            whole_runs.append(command)
        return original(command, cwd, timeout, grace, output)

    patch_lib("run_with_timeout", spy_run)
    commit_existing_tests(flow, {"tests/test_total.py": TEST_TOTAL})
    _implement(flow, cmd_setup, cmd_phases, cmd_implement, {"I-001": _touch_other("other")}, strategy=strategy_state("local-scoped-ci-whole"))
    capsys.readouterr()

    _call(cmd_converge, "cmd_verify")

    assert _emitted(capsys, "VERIFY") == "done"
    record = read_state(flow["path"])["whole_test"]
    assert record.get("ran") is False and record["resolution"] == "deferred"
    assert record["deferred"] == {"flags": ["D1"], "items": ["I-001"]}
    assert whole_runs == [], "全体テストのコマンドを手元で起動しない（I4）"
    assert _items(flow)["I-001"]["status"] == "verified"


def test_the_report_and_the_plan_show_the_baseline(flow, cmd_report, plan, capsys):
    state = read_state(flow["path"])
    state["baseline_test"].update({"head": "0123456789abcdef", "seconds": 12.5})
    write_state(flow["path"], state)

    _call(cmd_report, "cmd_report", metrics=False)
    out = capsys.readouterr().out
    body = plan.format_plan(read_state(flow["path"]))

    for text in (out, body):
        assert "0123456" in text and "12.5" in text


def test_the_fix_command_keeps_the_suite_boundaries(refactor):
    """変更起因の suite が複数なら、launch-cli.sh が空白で連結しても壊れない 1 本にして渡す。"""
    culprit = sys.modules["refactor_lib.culprit"]
    assert culprit.one_command(["pytest a -q"]) == "pytest a -q"
    assert culprit.one_command(["pytest a -q", "pytest b -q"]) == "( pytest a -q ) && ( pytest b -q )"
