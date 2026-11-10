"""失敗した項目の扱い（#1793 の R3 と確かめ直し）を**実際の git と pytest** で確かめる。

| 受け入れ条件 | 確かめること |
| --- | --- |
| R3 AC1・AC2（I5） | 結果を残してコミットしなかった項目は次の `fix.items` に入らず、次の検証の最初に取り消す。コミットした項目は範囲テストを走らせる |
| R3 AC3（#1733 の形） | 同じ構造検査で落ちた 8 件を 1 回目の修正の後に取り消し、残りは検証を通る |
| R3 AC4（I6） | 結果を残さずに終わった修正の起動は数えない |
| 手順を外れた修正 | 範囲ごと捨てた修正でも、コミットした項目は直さなかったと扱わない |
| R2 AC3（I3） | 検証の中の取り消しの後、残った項目を新しい HEAD の範囲テストで判定し直す |
"""

from __future__ import annotations

import argparse

import pytest

from crossref_helpers import verify_judged, build_git_flow, commit_with_trailers, git, item_trailers, read_state, write_result, write_state

FAR = "2099-01-01T00:00:00+00:00"
FIX_STEM = "claude-fix-rf130"


@pytest.fixture
def flow(tmp_path, monkeypatch, refactor, patch_lib, env_tmp_dir):
    return build_git_flow(tmp_path, monkeypatch, patch_lib, env_tmp_dir)


def _write(work, rel, text):
    path = work / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _module(name, value=1):
    return f"def {name}():\n    return {value}\n"


def _test(name, value=1):
    return f"from src.{name} import {name}\n\n\ndef test_{name}():\n    assert {name}() == {value}\n"


def _item(item_id, rank, name):
    return {
        "id": item_id,
        "rank": rank,
        "path": f"src/{name}.py",
        "symbol": name,
        "smell": "long_method",
        "technique": "extract_method",
        "severity": "major",
        "proposed_by": ["codex"],
        "tier": "high",
        "risk": False,
        "tests": [],
        "test_targets": [f"tests/test_{name}.py::test_{name}"],
        "command": ["pytest", "-q", f"tests/test_{name}.py"],
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


def _items(flow):
    return {i["id"]: i for i in read_state(flow["path"])["items"]}


def _implement(flow, cmd_setup, cmd_implement, names, changes, extra=None):
    """`names` のモジュールとテストを起点に置き、`changes`（項目 ID → (モジュール名, 中身)）の順に 1 項目 1 コミットで積む。"""
    work = flow["work"]
    for name in names:
        _write(work, f"src/{name}.py", _module(name))
        _write(work, f"tests/test_{name}.py", _test(name))
    for rel, text in (extra or {}).items():
        _write(work, rel, text)
    commit_with_trailers(work, "既存のモジュール", {})
    items = [_item(item_id, n, name) for n, (item_id, (name, _)) in enumerate(changes.items(), start=1)]
    state = read_state(flow["path"])
    state["items"] = items
    state["plan"] = {
        "base_sha": git("rev-parse", "HEAD", cwd=work).stdout.strip(),
        "reserve": {"danger_whole_test": 0.1, "final_whole_test": 0.1, "fix": 5.5},
        "end_at": FAR,
        "table_source": "defaults",
    }
    state["phase"] = "implement"
    write_state(flow["path"], state)
    _call(cmd_setup, "cmd_start_phase", phase="implement")
    for item_id, (name, text) in changes.items():
        _write(work, f"src/{name}.py", text)
        commit_with_trailers(work, f"Refactor {item_id}", item_trailers(item_id))
    _call(cmd_implement, "cmd_merge_implement")


def _fix(flow, cmd_setup, cmd_merge_fix, commits, *, result=True):
    """偽の修正担当。`commits`（項目 ID → (モジュール名, 中身)）だけをコミットし、`result` なら結果ファイルを残す。"""
    _call(cmd_setup, "cmd_start_phase", phase="fix")
    for item_id, (name, text) in commits.items():
        _write(flow["work"], f"src/{name}.py", text)
        commit_with_trailers(flow["work"], f"Fix {item_id}", item_trailers(item_id))
    if result:
        fix_items = read_state(flow["path"])["fix"]["items"]
        write_result(flow["path"], FIX_STEM, {"items": [{"id": i, "status": "not_fixed"} for i in fix_items]})
    _call(cmd_merge_fix, "cmd_merge_fix")


def test_an_item_the_fixer_did_not_commit_stalls_and_gets_another_fixer(
    flow, cmd_setup, cmd_implement, cmd_converge, cmd_merge_fix, capsys
):
    """#2457 の棚卸し #29: コミットしなかった I-002 は取り消さず停滞として数え、判定し直して修正担当を替える。"""
    _implement(flow, cmd_setup, cmd_implement, ["a", "b"], {"I-001": ("a", _module("a", 2)), "I-002": ("b", _module("b", 2))})
    capsys.readouterr()
    verify_judged(cmd_converge, flow["path"], capsys)
    assert _emitted(capsys, "VERIFY") == "fix"
    assert read_state(flow["path"])["fix"]["items"] == ["I-001", "I-002"]

    _fix(flow, cmd_setup, cmd_merge_fix, {"I-001": ("a", _module("a"))})
    items = _items(flow)
    assert items["I-002"]["unfixed"] == 1 and "unfixed" not in items["I-001"]
    assert [items[i]["fix_count"] for i in ("I-001", "I-002")] == [1, 1]

    verify_judged(cmd_converge, flow["path"], capsys)
    assert _emitted(capsys, "VERIFY") == "fix"
    items = _items(flow)
    assert items["I-002"]["status"] == "failing" and items["I-002"]["fixer_switched"] is True
    assert items["I-002"]["fix_history"][-1]["progress"] == "stalled"
    assert items["I-001"]["status"] == "verified"
    assert int(items["I-001"]["verify_runs"]) == 2
    assert read_state(flow["path"])["fix"]["items"] == ["I-002"]

    # 替えた修正担当がコミットしたら、前の起動の `unfixed` を外して再検証へ戻す
    _fix(flow, cmd_setup, cmd_merge_fix, {"I-002": ("b", _module("b"))})
    items = _items(flow)
    assert "unfixed" not in items["I-002"] and items["I-002"]["status"] == "implemented"


def test_a_fix_launch_without_a_result_does_not_mark_unfixed_items(flow, cmd_setup, cmd_implement, cmd_converge, cmd_merge_fix, capsys):
    """R3 AC4（I6）: 結果を残さずに終わった修正の起動は数えず、締め切りの前なら同じ項目を修正へ回す。"""
    _implement(flow, cmd_setup, cmd_implement, ["a"], {"I-001": ("a", _module("a", 2))})
    verify_judged(cmd_converge, flow["path"], capsys)
    _fix(flow, cmd_setup, cmd_merge_fix, {}, result=False)
    assert "unfixed" not in _items(flow)["I-001"]

    capsys.readouterr()
    verify_judged(cmd_converge, flow["path"], capsys)
    assert _emitted(capsys, "VERIFY") == "fix"
    assert read_state(flow["path"])["fix"]["items"] == ["I-001"]


def test_a_fix_outside_the_scope_is_kept_and_reported(flow, cmd_setup, cmd_implement, cmd_converge, cmd_merge_fix, capsys):
    """#2457 受け入れ条件 8: 範囲の外を変えた修正を捨てず、`outside_scope` に残す。コミットしなかった I-002 は直さなかったと扱う。"""
    _implement(flow, cmd_setup, cmd_implement, ["a", "b"], {"I-001": ("a", _module("a", 2)), "I-002": ("b", _module("b", 2))})
    verify_judged(cmd_converge, flow["path"], capsys)
    _call(cmd_setup, "cmd_start_phase", phase="fix")
    # 範囲の外の既存のファイルを、項目が変えた名前と関係なく変える（新しいファイルは範囲の中として扱うため使わない。#1814）
    ignore = flow["work"] / ".gitignore"
    _write(flow["work"], ".gitignore", ignore.read_text(encoding="utf-8") + "build/\n")
    commit_with_trailers(flow["work"], "Fix I-001", item_trailers("I-001"))
    write_result(flow["path"], FIX_STEM, {"items": []})
    _call(cmd_merge_fix, "cmd_merge_fix")

    items = _items(flow)
    assert "build/" in ignore.read_text(encoding="utf-8")
    assert [r["path"] for r in items["I-001"]["outside_scope"]] == [".gitignore"]
    assert "unfixed" not in items["I-001"] and len(items["I-001"]["commits"]["fix"]) == 1
    assert items["I-002"]["unfixed"] == 1


def test_items_failing_the_same_structure_check_are_dropped_after_one_fix(
    flow, cmd_setup, cmd_implement, cmd_converge, cmd_merge_fix, cmd_gate, ledger, capsys
):
    """#1733 の形・#2457 受け入れ条件 17: 18 件のうち 8 件が同じ構造検査で落ち、修正担当が 8 件ともコミットしない。

    1 回目の修正の後は停滞として修正担当を替え、替えた後も停滞したら 8 件を打ち消す。残りは巻き込まれず検証を通る。"""
    limit = "import pathlib\n\n\ndef test_big_stays_small():\n    assert len(pathlib.Path('src/big.py').read_text().splitlines()) <= 3\n"
    names = [f"m{n:02d}" for n in range(1, 11)]
    changes = {}
    big = "VALUE = 0\n"
    for n in range(1, 19):
        item_id = f"I-{n:03d}"
        if n in (2, 3, 5, 8, 11, 13, 16, 17):  # 構造検査で落ちる項目を他の項目の間に挟む
            big += f"\n\ndef f{n}():\n    return {n}\n"
            changes[item_id] = ("big", big)
        else:
            name = names.pop(0)
            changes[item_id] = (name, _module(name, 1) + f"\n\nX = {n}\n")
    big_ids = [i for i, (name, _) in changes.items() if name == "big"]
    assert len(big_ids) == 8
    _implement(
        flow,
        cmd_setup,
        cmd_implement,
        [f"m{n:02d}" for n in range(1, 11)],
        changes,
        extra={"src/big.py": "VALUE = 0\n", "tests/test_big.py": limit},
    )
    state = read_state(flow["path"])
    for item in state["items"]:
        if item["id"] in big_ids:
            item["test_targets"] = ["tests/test_big.py::test_big_stays_small"]
    write_state(flow["path"], state)

    capsys.readouterr()
    verify_judged(cmd_converge, flow["path"], capsys)
    assert _emitted(capsys, "VERIFY") == "fix"
    assert sorted(read_state(flow["path"])["fix"]["items"]) == sorted(big_ids)
    _fix(flow, cmd_setup, cmd_merge_fix, {})

    verify_judged(cmd_converge, flow["path"], capsys)
    assert _emitted(capsys, "VERIFY") == "fix", "1 回目の停滞では修正担当を替えて続ける"
    _fix(flow, cmd_setup, cmd_merge_fix, {})

    verify_judged(cmd_converge, flow["path"], capsys)
    assert _emitted(capsys, "VERIFY") == "done"
    items = _items(flow)
    assert {i for i in big_ids if items[i]["status"] == "reverted"} == set(big_ids)
    assert all(items[i]["revert_reason"] == "stalled_after_switch" for i in big_ids)
    assert all(items[i]["fix_count"] == 2 for i in big_ids)
    kept = [i for i in items if i not in big_ids]
    assert all(items[i]["status"] == "verified" for i in kept)
    assert read_state(flow["path"])["fix_stats"]["launches"] == 2

    _call(cmd_gate, "cmd_final_gate")
    tally = ledger.tally(read_state(flow["path"]))
    assert (tally.adopted, tally.reverted) == (10, 8)


def test_items_left_after_a_drop_are_judged_again_on_the_new_head(flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    """R2 AC3（I3）: 検証の中の打ち消しの後、残った項目を新しい HEAD の範囲テストで判定し直す。

    I-002 は I-001 が足した関数を使う。I-001 を成立しないと判定して打ち消すと、確かめ直しで I-002 が落ちて修正へ回る。
    I-003 は確かめ直しを通って `verified` のまま残る。"""
    helper = _module("a") + "\n\ndef helper():\n    return 5\n"
    uses = "from src.a import helper\n\n\ndef b():\n    return helper() - 4\n"
    _implement(
        flow,
        cmd_setup,
        cmd_implement,
        ["a", "b", "c"],
        {"I-001": ("a", helper.replace("return 1", "return 9")), "I-002": ("b", uses), "I-003": ("c", _module("c") + "\n\nY = 1\n")},
    )
    capsys.readouterr()
    assert verify_judged(cmd_converge, flow["path"], capsys, feasible={"I-001": False}) == "fix"

    saved = read_state(flow["path"])
    items = {i["id"]: i for i in saved["items"]}
    assert items["I-001"]["status"] == "reverted"
    assert items["I-002"]["status"] == "failing", "確かめ直しで落ちた項目は範囲テストの失敗として修正へ回す"
    assert items["I-003"]["status"] == "verified"
    assert int(items["I-003"]["verify_runs"]) >= 2
    assert "I-003" in saved["drops"][0]["recheck"]


# ---------- `fix_intake._apply_fix_result` の現状固定（実際の git） ----------


def _repo(tmp_path):
    work = tmp_path / "apply"
    work.mkdir()
    git("init", "-q", str(work), cwd=tmp_path)
    git("config", "user.email", "t@e.st", cwd=work)
    git("config", "user.name", "test", cwd=work)
    _write(work, "a.txt", "a0\n")
    _write(work, "b.txt", "b0\n")
    commit_with_trailers(work, "init", {})
    return work


def _bare_item(item_id):
    return {"id": item_id, "commits": {"test": None, "implement": None, "fix": []}}


def _said(capsys):
    """`info` の出力（標準出力・標準エラーのどちらに出ても拾う）。"""
    got = capsys.readouterr()
    return got.out + got.err


def _newest_first(*shas):
    return list(reversed(shas))


def test_apply_fix_result_with_an_unknown_range_only_reports(tmp_path, fix_intake, capsys):
    """範囲を確定できなかったときは問題を出すだけで、状態も履歴も変えない。"""
    work = _repo(tmp_path)
    head = git("rev-parse", "HEAD", cwd=work).stdout.strip()
    state = {"items": [_bare_item("I-001")]}
    result = {"head": head, "ordered": [], "known": False, "problems": ["範囲が不明"]}
    fix_intake._apply_fix_result(tmp_path / "s.json", state, str(work), result)
    assert "❌ 範囲が不明" in _said(capsys)
    assert state == {"items": [_bare_item("I-001")]}
    assert git("rev-parse", "HEAD", cwd=work).stdout.strip() == head


def test_apply_fix_result_links_good_commits_oldest_first_and_rewrites(tmp_path, fix_intake):
    """問題の無いコミットは古い順に項目の `commits.fix` へ足し、計画に無い項目のコミットは捨て置く。書き換えの判定を項目へ足す。"""
    work = _repo(tmp_path)
    _write(work, "a.txt", "a1\n")
    first = commit_with_trailers(work, "Fix I-001", item_trailers("I-001"))
    _write(work, "b.txt", "b1\n")
    second = commit_with_trailers(work, "Fix I-002", item_trailers("I-002"))
    _write(work, "a.txt", "a2\n")
    third = commit_with_trailers(work, "Fix I-001 again", item_trailers("I-001"))
    _write(work, "b.txt", "b2\n")
    commit_with_trailers(work, "Fix I-009", item_trailers("I-009"))
    unknown = git("rev-parse", "HEAD", cwd=work).stdout.strip()
    head = unknown
    state = {"items": [_bare_item("I-001"), _bare_item("I-002")]}
    state["items"][1]["review_scope_judgements"] = [{"path": "old"}]
    result = {
        "head": head,
        "ordered": _newest_first(first, second, third, unknown),
        "known": True,
        "problems": [],
        "rewrites": {"I-001": [{"path": "a.txt"}], "I-002": [{"path": "b.txt"}], "I-009": [{"path": "x"}]},
    }
    fix_intake._apply_fix_result(tmp_path / "s.json", state, str(work), result)
    items = {i["id"]: i for i in state["items"]}
    assert items["I-001"]["commits"]["fix"] == [first, third]
    assert items["I-002"]["commits"]["fix"] == [second]
    assert items["I-001"]["review_scope_judgements"] == [{"path": "a.txt"}]
    assert items["I-002"]["review_scope_judgements"] == [{"path": "old"}, {"path": "b.txt"}]
    assert "sendbacks" not in state and "ledger" not in state
    assert git("rev-parse", "HEAD", cwd=work).stdout.strip() == head


def test_apply_fix_result_reverts_only_the_bad_commits_and_records_sendbacks(tmp_path, fix_intake, capsys):
    """手順を外れたコミットだけを新しい順に打ち消し、元のコミットと打ち消しのコミットを台帳へ古い順に載せる。

    差し戻しは項目があればその項目へ、無ければ状態の `sendbacks` へ残す。残りのコミットは項目へ関連付ける。"""
    work = _repo(tmp_path)
    _write(work, "a.txt", "a1\n")
    bad_a = commit_with_trailers(work, "Fix I-001 bad", item_trailers("I-001"))
    _write(work, "b.txt", "b1\n")
    good = commit_with_trailers(work, "Fix I-002", item_trailers("I-002"))
    _write(work, "c.txt", "c1\n")
    bad_x = commit_with_trailers(work, "stray", {})
    state = {"items": [_bare_item("I-001"), _bare_item("I-002")]}
    problems = [
        {"sha": bad_a, "item": "I-001", "problem": "外れた A"},
        {"sha": bad_x, "item": "", "problem": "外れた X"},
    ]
    result = {"head": bad_x, "ordered": _newest_first(bad_a, good, bad_x), "known": True, "problems": problems}
    fix_intake._apply_fix_result(tmp_path / "s.json", state, str(work), result)

    out = _said(capsys)
    assert "↪ 外れた A（差し戻し）" in out and "↪ 外れた X（差し戻し）" in out
    assert (work / "a.txt").read_text(encoding="utf-8") == "a0\n"
    assert (work / "b.txt").read_text(encoding="utf-8") == "b1\n"
    assert not (work / "c.txt").exists()
    made = git("rev-list", "-2", "HEAD", cwd=work).stdout.split()
    assert state["ledger"]["orchestrator_commits"] == [bad_a, bad_x, *reversed(made)], "元のコミットも公開の照合で止めない"
    subjects = git("log", "-2", "--format=%s", cwd=work).stdout.splitlines()
    assert subjects == ['Revert "Fix I-001 bad"', 'Revert "stray"']
    items = {i["id"]: i for i in state["items"]}
    assert items["I-001"]["commits"]["fix"] == []
    assert items["I-002"]["commits"]["fix"] == [good]
    [row_a] = items["I-001"]["sendbacks"]
    assert (row_a["kind"], row_a["reason"], row_a["commit"]) == ("fix_procedure", "外れた A", bad_a)
    assert isinstance(row_a["at"], str) and row_a["at"]
    [row_x] = state["sendbacks"]
    assert (row_x["kind"], row_x["reason"], row_x["commit"]) == ("fix_procedure", "外れた X", bad_x)


def test_apply_fix_result_reverts_the_whole_range_when_the_bad_commit_alone_conflicts(tmp_path, fix_intake, capsys):
    """違反のコミットだけを戻すと衝突するとき（後のコミットが同じ行を直した）は、修正の範囲全体を打ち消して
    修正を採らない。違反のコミットを項目へ結ばず、元のコミットと打ち消しのコミットを台帳へ載せる。"""
    work = _repo(tmp_path)
    _write(work, "a.txt", "a1\n")
    bad = commit_with_trailers(work, "Fix I-001 bad", item_trailers("I-001"))
    _write(work, "a.txt", "a2\n")
    good = commit_with_trailers(work, "Fix I-001", item_trailers("I-001"))
    state = {"items": [_bare_item("I-001")]}
    result = {
        "head": good,
        "ordered": _newest_first(bad, good),
        "known": True,
        "problems": [{"sha": bad, "item": "I-001", "problem": "外れた"}],
    }
    fix_intake._apply_fix_result(tmp_path / "s.json", state, str(work), result)

    out = _said(capsys)
    assert "修正の範囲 2 件を打ち消しました（修正を採りません）" in out
    assert (work / "a.txt").read_text(encoding="utf-8") == "a0\n"
    assert git("status", "--porcelain", cwd=work).stdout == ""
    made = git("rev-list", "-2", "HEAD", cwd=work).stdout.split()
    assert state["ledger"]["orchestrator_commits"] == [bad, good, *reversed(made)]
    item = state["items"][0]
    assert item["commits"]["fix"] == [], "違反のコミットを項目へ結ばない"
    assert [r["commit"] for r in item["sendbacks"]] == [bad]
