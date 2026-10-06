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

from crossref_helpers import build_git_flow, commit_with_trailers, git, item_trailers, read_state, write_result, write_state

FAR = "2099-01-01T00:00:00+00:00"
PAST = "2000-01-01T00:00:00+00:00"
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


def test_an_item_the_fixer_did_not_commit_is_dropped_on_the_next_verify(
    flow, cmd_setup, cmd_implement, cmd_converge, cmd_merge_fix, capsys
):
    """R3 AC1・AC2: コミットしなかった I-002 は次の修正へ回らず取り消す。コミットした I-001 は範囲テストで通る。"""
    _implement(flow, cmd_setup, cmd_implement, ["a", "b"], {"I-001": ("a", _module("a", 2)), "I-002": ("b", _module("b", 2))})
    capsys.readouterr()
    _call(cmd_converge, "cmd_verify")
    assert _emitted(capsys, "VERIFY") == "fix"
    assert read_state(flow["path"])["fix"]["items"] == ["I-001", "I-002"]

    _fix(flow, cmd_setup, cmd_merge_fix, {"I-001": ("a", _module("a"))})
    items = _items(flow)
    assert items["I-002"]["unfixed"] == 1 and "unfixed" not in items["I-001"]
    assert [items[i]["fix_count"] for i in ("I-001", "I-002")] == [1, 1]

    _call(cmd_converge, "cmd_verify")
    assert _emitted(capsys, "VERIFY") == "done", "締め切りの前でも、直さなかった項目を修正へ回さない"
    items = _items(flow)
    assert items["I-002"]["status"] == "reverted"
    assert items["I-002"]["failure_reason"].startswith("修正担当が直さなかった")
    assert items["I-001"]["status"] == "verified"
    assert int(items["I-001"]["verify_runs"]) == 2
    assert _module("b") == (flow["work"] / "src" / "b.py").read_text(encoding="utf-8")


def test_a_fix_launch_without_a_result_does_not_mark_unfixed_items(flow, cmd_setup, cmd_implement, cmd_converge, cmd_merge_fix, capsys):
    """R3 AC4（I6）: 結果を残さずに終わった修正の起動は数えず、締め切りの前なら同じ項目を修正へ回す。"""
    _implement(flow, cmd_setup, cmd_implement, ["a"], {"I-001": ("a", _module("a", 2))})
    _call(cmd_converge, "cmd_verify")
    _fix(flow, cmd_setup, cmd_merge_fix, {}, result=False)
    assert "unfixed" not in _items(flow)["I-001"]

    capsys.readouterr()
    _call(cmd_converge, "cmd_verify")
    assert _emitted(capsys, "VERIFY") == "fix"
    assert read_state(flow["path"])["fix"]["items"] == ["I-001"]


def test_an_item_whose_fix_was_discarded_for_breaking_the_rules_is_not_unfixed(flow, cmd_setup, cmd_implement, cmd_converge, cmd_merge_fix):
    """手順を外れた修正: 範囲ごと捨てても、コミットした I-001 は直さなかったと扱わない。コミットしなかった I-002 は扱う。"""
    _implement(flow, cmd_setup, cmd_implement, ["a", "b"], {"I-001": ("a", _module("a", 2)), "I-002": ("b", _module("b", 2))})
    _call(cmd_converge, "cmd_verify")
    _call(cmd_setup, "cmd_start_phase", phase="fix")
    # 範囲の外の既存のファイルを、項目が変えた名前と関係なく変える（新しいファイルは範囲の中として扱うため使わない。#1814）
    ignore = flow["work"] / ".gitignore"
    _write(flow["work"], ".gitignore", ignore.read_text(encoding="utf-8") + "build/\n")
    commit_with_trailers(flow["work"], "Fix I-001", item_trailers("I-001"))
    write_result(flow["path"], FIX_STEM, {"items": []})
    _call(cmd_merge_fix, "cmd_merge_fix")

    items = _items(flow)
    assert "build/" not in ignore.read_text(encoding="utf-8"), "手順を外れた修正は範囲ごと捨てる"
    assert "unfixed" not in items["I-001"]
    assert items["I-002"]["unfixed"] == 1


def test_items_failing_the_same_structure_check_are_dropped_after_one_fix(
    flow, cmd_setup, cmd_implement, cmd_converge, cmd_merge_fix, cmd_gate, ledger, capsys
):
    """R3 AC3（#1733 の形）: 18 件のうち 8 件が同じ構造検査で落ち、修正担当が 8 件ともコミットしない。

    8 件は 1 回目の修正の後に取り消され（`fix_count` 1）、取り消しで残りを巻き込まず、残りは検証を通る。"""
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
    _call(cmd_converge, "cmd_verify")
    assert _emitted(capsys, "VERIFY") == "fix"
    assert sorted(read_state(flow["path"])["fix"]["items"]) == sorted(big_ids)
    _fix(flow, cmd_setup, cmd_merge_fix, {})

    _call(cmd_converge, "cmd_verify")
    assert _emitted(capsys, "VERIFY") == "done"
    items = _items(flow)
    assert {i for i in big_ids if items[i]["status"] == "reverted"} == set(big_ids)
    assert all(items[i]["fix_count"] == 1 for i in big_ids)
    kept = [i for i in items if i not in big_ids]
    assert all(items[i]["status"] == "verified" for i in kept)
    assert read_state(flow["path"])["fix_stats"]["launches"] == 1

    _call(cmd_gate, "cmd_final_gate")
    tally = ledger.tally(read_state(flow["path"]))
    assert (tally.adopted, tally.reverted) == (10, 8)


def test_items_left_after_a_drop_are_judged_again_on_the_new_head(flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    """R2 AC3（I3）: 検証の中の取り消しの後、残った項目を新しい HEAD の範囲テストで判定し直す。

    I-002 は I-001 が足した関数を使う。I-001 を締め切りで取り消すと、確かめ直しで I-002 が落ちて取り消される。
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
    state = read_state(flow["path"])
    state["started_at"] = PAST
    write_state(flow["path"], state)

    capsys.readouterr()
    _call(cmd_converge, "cmd_verify")

    assert _emitted(capsys, "VERIFY") == "done"
    saved = read_state(flow["path"])
    items = {i["id"]: i for i in saved["items"]}
    assert items["I-001"]["status"] == "reverted"
    assert items["I-002"]["status"] == "reverted", "確かめ直しで落ちた項目は範囲テストの失敗として扱う"
    assert items["I-003"]["status"] == "verified"
    assert int(items["I-003"]["verify_runs"]) >= 2
    assert "I-003" in saved["drops"][0]["recheck"]
