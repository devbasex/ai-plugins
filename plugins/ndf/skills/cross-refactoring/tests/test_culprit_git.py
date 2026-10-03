"""全体テストの変更起因の失敗から、原因の項目だけを直すか取り消す（#1649）を**実際の git**で確かめる。

| 振る舞い | 確かめること |
| --- | --- |
| パスの一致（`path`） | 落ちたテストの本文に、危険フラグの無い項目の変えたファイルが現れると、その項目だけが修正へ回る（AC1） |
| 締め切りの後 | 原因の項目だけを取り消し、危険フラグの項目は残る（AC2） |
| 外す走らせ直し（`isolate`） | 本文にパスが無ければ、項目を 1 件ずつ外して通るようになった項目が原因になる。HEAD と worktree は動かない（AC3・I4） |
| 原因が 2 件 | 別々のテストを別々の項目が落とすと、両方が原因になり、ほかの項目は触らない（AC4） |
| #1634 #1663 の形 | 危険フラグの項目は無関係で、危険フラグの無い項目が行数の上限と同名の関数の検査を落とす。危険フラグの項目は 1 件も取り消されない（AC8） |
| 報告 | 原因の項目と手がかりが状態ファイルと `refactor.py report` に出る（AC7） |
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
PAST = "2000-01-01T00:00:00+00:00"

# `total` が calc.py の中で例外を投げる。JUnit の本文の traceback に `src/calc.py` が現れる。
CALC_RAISES = CALC.replace("    return result\n", "    raise RuntimeError('calc broke')\n")
# 本文にパスを出さない壊し方（`assert 7 == 6` だけ）。
CALC_OFF_BY_ONE = CALC.replace("return result", "return result + 1")


@pytest.fixture
def flow(tmp_path, monkeypatch, refactor, patch_lib, env_tmp_dir):
    return build_git_flow(tmp_path, monkeypatch, patch_lib, env_tmp_dir)


def _item(item_id, rank):
    return {
        "id": item_id,
        "rank": rank,
        "path": "src/calc.py",
        "symbol": "add",
        "smell": "long_method",
        "technique": "extract_method",
        "severity": "major",
        "proposed_by": ["codex"],
        "tier": "high",
        "risk": False,
        "public_io": False,
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
    values = [line.split("=", 1)[1] for line in capsys.readouterr().out.splitlines() if line.startswith(key + "=")]
    return values[-1] if values else None


def _write(work, rel, text):
    path = work / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _head(work):
    return git("rev-parse", "HEAD", cwd=work).stdout.strip()


def _existing(flow, files):
    for rel, text in files.items():
        _write(flow["work"], rel, text)
    commit_with_trailers(flow["work"], "既存のテスト", {})
    state = read_state(flow["path"])
    state["baseline_test"]["head"] = _head(flow["work"])
    write_state(flow["path"], state)


def _implement(flow, cmd_setup, cmd_implement, changes, **over):
    work = flow["work"]
    state = read_state(flow["path"])
    state["items"] = [_item(item_id, rank) for rank, item_id in enumerate(changes, start=1)]
    state["plan"] = {
        "base_sha": _head(work),
        "reserve": {"danger_whole_test": 0.1, "final_whole_test": 0.1, "fix": 5.5},
        "end_at": FAR,
        "table_source": "defaults",
    }
    state["phase"] = "implement"
    state.update(over)
    write_state(flow["path"], state)
    _call(cmd_setup, "cmd_start_phase", phase="implement")
    for item_id, change in changes.items():
        change(work)
        commit_with_trailers(work, f"Refactor {item_id}", item_trailers(item_id))
    _call(cmd_implement, "cmd_merge_implement")


def _items(flow):
    return {i["id"]: i for i in read_state(flow["path"])["items"]}


def _flagged(name):
    """項目の `path` の外のファイルだけを触る（D1 が立つ。全体テストには関わらない）。"""
    return lambda w: _write(w, f"src/{name}.py", "X = 1\n")


def _calc(text):
    """`src/calc.py` だけを変える（危険フラグは立たない）。"""
    return lambda w: _write(w, "src/calc.py", text)


def _verify(flow, cmd_converge, capsys):
    capsys.readouterr()
    _call(cmd_converge, "cmd_verify")
    return _emitted(capsys, "VERIFY"), read_state(flow["path"])["whole_test"]


def test_a_path_in_the_failure_names_the_unflagged_item_and_only_it_goes_to_fix(flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    """AC1・AC9 の裏 — 危険フラグの項目でなく、本文にパスが現れた危険フラグの無い項目が修正へ回る。"""
    _existing(flow, {"tests/test_total.py": TEST_TOTAL})
    _implement(flow, cmd_setup, cmd_implement, {"I-001": _flagged("other"), "I-002": _calc(CALC_RAISES)})

    verify, record = _verify(flow, cmd_converge, capsys)

    assert verify == "fix"
    assert _items(flow)["I-001"]["danger"] and not _items(flow)["I-002"]["danger"]
    assert record["culprit"]["culprits"] == ["I-002"] and record["culprit"]["basis"] == "path"
    assert record["culprit"]["evidence"]["I-002"]["paths"] == ["src/calc.py"]
    assert record["items"] == ["I-002"]
    assert read_state(flow["path"])["fix"]["items"] == ["I-002"]
    items = _items(flow)
    assert items["I-002"]["status"] == "failing" and items["I-001"]["status"] == "verified"


def test_after_the_deadline_only_the_culprit_is_reverted(flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    """AC2 — 締め切りを過ぎたら原因の項目だけを取り消し、落ちたテストが通った時点で止める。危険フラグの項目は残る。"""
    work = flow["work"]
    _existing(flow, {"tests/test_total.py": TEST_TOTAL})
    _implement(
        flow,
        cmd_setup,
        cmd_implement,
        {"I-001": _calc(CALC_RAISES), "I-002": _flagged("other"), "I-003": _flagged("another")},
        started_at=PAST,
    )

    verify, record = _verify(flow, cmd_converge, capsys)

    assert verify == "done"
    items = _items(flow)
    assert items["I-001"]["status"] == "reverted"
    assert items["I-002"]["status"] == "verified" and items["I-003"]["status"] == "verified"
    assert record["culprit"]["basis"] == "path" and record["resolution"] == "narrowed"
    assert (work / "src" / "other.py").exists() and "calc broke" not in (work / "src" / "calc.py").read_text()


def test_without_a_path_the_item_whose_removal_makes_the_test_pass_is_the_culprit(flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    """AC3・I4 — 本文にパスが無ければ、新しい順に外して走らせ直し、通るようになった項目を原因にする。HEAD は動かない。"""
    work = flow["work"]
    _existing(flow, {"tests/test_total.py": TEST_TOTAL})
    _implement(flow, cmd_setup, cmd_implement, {"I-001": _calc(CALC_OFF_BY_ONE), "I-002": _flagged("other"), "I-003": _flagged("another")})
    head = _head(work)

    verify, record = _verify(flow, cmd_converge, capsys)

    assert verify == "fix"
    assert record["culprit"]["culprits"] == ["I-001"] and record["culprit"]["basis"] == "isolate"
    assert record["culprit"]["evidence"]["I-001"]["tests"] == ["tests/test_total.py::tests.test_total::test_total"]
    assert record["items"] == ["I-001"]
    assert _head(work) == head
    assert git("status", "--porcelain", cwd=work).stdout.strip() == ""
    assert "return result + 1" in (work / "src" / "calc.py").read_text()


OTHER_TEST = "from src import shared\n\n\ndef test_shared():\n    assert shared.X == 1\n"


def test_two_items_breaking_two_tests_are_both_culprits(flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    """AC4 — 別々のテストを別々の項目が落とすと、両方が原因になり、危険フラグの項目は修正へ回らない。"""
    _existing(flow, {"tests/test_total.py": TEST_TOTAL, "src/shared.py": "X = 1\n", "tests/test_shared.py": OTHER_TEST})
    _implement(
        flow,
        cmd_setup,
        cmd_implement,
        {"I-001": _flagged("other"), "I-002": _calc(CALC_RAISES), "I-003": lambda w: _write(w, "src/shared.py", "X = 2\n")},
    )

    verify, record = _verify(flow, cmd_converge, capsys)

    assert verify == "fix"
    assert sorted(record["culprit"]["culprits"]) == ["I-002", "I-003"]
    assert record["culprit"]["basis"] == "isolate"
    assert sorted(read_state(flow["path"])["fix"]["items"]) == ["I-002", "I-003"]
    assert _items(flow)["I-001"]["status"] == "verified"


LAYOUT_TEST = """import pathlib


def test_no_file_exceeds_12_lines():
    for path in sorted(pathlib.Path("src").glob("*.py")):
        n = len(path.read_text().splitlines())
        assert n <= 12, f"{path.as_posix()} has {n} lines"


def test_no_duplicate_function_names():
    seen = {}
    for path in sorted(pathlib.Path("src").glob("*.py")):
        for line in path.read_text().splitlines():
            if line.startswith("def "):
                name = line[4:].split("(")[0]
                assert name not in seen, f"{name} in {seen.get(name)} and {path.as_posix()}"
                seen[name] = path.as_posix()
"""


def test_the_1634_and_1663_shape_reverts_no_flagged_item(flow, cmd_setup, cmd_implement, cmd_converge, cmd_report, capsys):
    """AC8・AC7 — 危険フラグの項目は落ちたテストに関わらず、危険フラグの無い項目が行数の上限と同名の関数の検査を落とす。

    締め切りを過ぎていても、取り消されるのは原因の 2 項目だけで、危険フラグの項目は 1 件も取り消されない。
    """
    _existing(flow, {"tests/test_layout.py": LAYOUT_TEST})
    long_calc = CALC + "\n\ndef extra():\n    return 0\n"  # 13 行を超える
    _implement(
        flow,
        cmd_setup,
        cmd_implement,
        {
            "I-001": _flagged("other"),
            "I-002": _calc(long_calc),
            "I-003": _flagged("another"),
            "I-004": lambda w: _write(w, "src/participants.py", "def add(a, b):\n    return a + b\n"),
        },
        started_at=PAST,
    )
    state = read_state(flow["path"])
    state["items"][3]["path"] = "src/participants.py"  # 項目の対象の中だけを変えた（危険フラグは立たない）
    write_state(flow["path"], state)

    verify, record = _verify(flow, cmd_converge, capsys)

    assert verify == "done"
    assert _items(flow)["I-001"]["danger"] and _items(flow)["I-003"]["danger"]
    assert not _items(flow)["I-002"]["danger"] and not _items(flow)["I-004"]["danger"]
    assert sorted(record["culprit"]["culprits"]) == ["I-002", "I-004"] and record["culprit"]["basis"] == "path"
    items = _items(flow)
    assert items["I-001"]["status"] == "verified" and items["I-003"]["status"] == "verified"
    assert items["I-002"]["status"] == "reverted" and items["I-004"]["status"] == "reverted"

    _call(cmd_report, "cmd_report", metrics=False)
    out = capsys.readouterr().out
    assert "I-002" in out and "I-004" in out and "path" in out


def test_without_time_and_without_a_path_every_item_is_reverted_newest_first_until_it_passes(
    flow, cmd_setup, cmd_implement, cmd_converge, capsys
):
    """AC5 — どちらでも決まらなければ、すべての項目を新しい順に取り消し、通った時点で止める。「決まらず」と残る。"""
    _existing(flow, {"tests/test_total.py": TEST_TOTAL})
    _implement(
        flow,
        cmd_setup,
        cmd_implement,
        {"I-001": _flagged("other"), "I-002": _calc(CALC_OFF_BY_ONE), "I-003": _flagged("another")},
        started_at=PAST,
    )

    verify, record = _verify(flow, cmd_converge, capsys)

    assert verify == "done"
    assert record["culprit"]["basis"] == "undetermined" and record["culprit"]["order"] == ["I-003", "I-002", "I-001"]
    items = _items(flow)
    assert [items[i]["status"] for i in ("I-003", "I-002", "I-001")] == ["reverted", "reverted", "verified"]
