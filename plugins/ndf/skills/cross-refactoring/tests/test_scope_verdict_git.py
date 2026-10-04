"""他の項目の違反で落ちた範囲テストが、触っていない項目を取り消さない（#1688 の AC7〜AC10・AC12）を**実際の git**で確かめる。

項目 X（I-001）が行数の上限ちょうどのファイルへ行を足し、項目 Y（I-002）が別のファイルを変えて、リポジトリ全体の
行数を見るテストを範囲テストに持つ（実行 rf1673 の I-014 と I-001 の形）。

| 振る舞い | 確かめること |
| --- | --- |
| 巻き込まれた項目 | Y は取り消されず `implemented` のまま待ち、`blocked_by` に X が残る。修正へ回るのは X だけ（AC7・I4） |
| 原因の項目の理由 | 締め切りで X を取り消す理由に suite とファイルが入り、Y は走らせ直されて `verified` になる（AC8・I5・I6） |
| 自分の失敗 | Y が自分の変えたファイルで落ちたら、従来どおり `failing` になる（AC9） |
| テストの名前 | 全体を見るテストの名前とパスを替えても同じ（AC10） |
| 静的解析の suite が無い宣言 | Y は従来どおり `failing` になり、`blocked_by` を持たず、範囲テストは 1 回だけ（AC12・I7） |
"""

from __future__ import annotations

import argparse
import sys

import pytest

from crossref_helpers import SCOPE_COMMAND, build_git_flow, commit_with_trailers, git, item_trailers, read_state, write_state

FAR = "2099-01-01T00:00:00+00:00"
PAST = "2000-01-01T00:00:00+00:00"
LIMIT = 12

SIZE_CHECK = f"""import sys

bad = [p for p in sys.argv[1:] if len(open(p).read().splitlines()) > {LIMIT}]
for p in bad:
    print(f"{{p}}: too many lines")
sys.exit(1 if bad else 0)
"""


def _whole_test(name):
    """`src/` の全ファイルの行数を見るテスト（リポジトリ全体を見る形。名前は呼ぶ側が決める）。"""
    return f"""import pathlib


def {name}():
    for path in sorted(pathlib.Path("src").glob("*.py")):
        n = len(path.read_text().splitlines())
        assert n <= {LIMIT}, f"{{path.as_posix()}}: {{n}} lines"
"""


AT_LIMIT = "".join(f"V{i} = {i}\n" for i in range(LIMIT))
OVER = AT_LIMIT + "".join(f"W{i} = {i}\n" for i in range(9))


@pytest.fixture
def flow(tmp_path, monkeypatch, refactor, patch_lib, env_tmp_dir):
    return build_git_flow(tmp_path, monkeypatch, patch_lib, env_tmp_dir)


def _write(work, rel, text):
    path = work / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _head(work):
    return git("rev-parse", "HEAD", cwd=work).stdout.strip()


def _call(module, name, **kwargs):
    return getattr(module, name)(argparse.Namespace(id=130, **kwargs))


def _item(item_id, rank, path, test_target):
    return {
        "id": item_id,
        "rank": rank,
        "path": path,
        "symbol": "V0",
        "smell": "long_method",
        "technique": "extract_method",
        "severity": "major",
        "proposed_by": ["codex"],
        "tier": "high",
        "risk": False,
        "public_io": False,
        "tests": [],
        "test_targets": [test_target],
        "scope_commands": [{"suite": "pytest", "kind": "test", "command": SCOPE_COMMAND.replace("{paths}", test_target)}],
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


def _setup(flow, cmd_setup, cmd_implement, *, whole="tests/test_layout.py", name="test_layout", x_text=OVER, y_text="Y = 1\n", lint=True, **over):
    work = flow["work"]
    _write(work, "size_check.py", SIZE_CHECK)
    _write(work, "src/big.py", AT_LIMIT)
    _write(work, "src/structure.py", "Y = 0\n")
    _write(work, whole, _whole_test(name))
    commit_with_trailers(work, "既存", {})
    state = read_state(flow["path"])
    state["baseline_test"]["head"] = _head(work)
    if lint:
        state["strategy"]["suites"].append(
            {
                "name": "size",
                "kind": "lint",
                "command": f"{sys.executable} size_check.py src",
                "scope_command": f"{sys.executable} size_check.py {{paths}}",
                "junit": None,
                "paths": ["src"],
            }
        )
    state["items"] = [_item("I-001", 1, "src/big.py", "tests/test_calc.py"), _item("I-002", 2, "src/structure.py", whole)]
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
    _write(work, "src/big.py", x_text)
    commit_with_trailers(work, "Refactor I-001", item_trailers("I-001"))
    _write(work, "src/structure.py", y_text)
    commit_with_trailers(work, "Refactor I-002", item_trailers("I-002"))
    _call(cmd_implement, "cmd_merge_implement")


def _verify(flow, cmd_converge, capsys):
    capsys.readouterr()
    _call(cmd_converge, "cmd_verify")
    out = capsys.readouterr().out
    values = [line.split("=", 1)[1] for line in out.splitlines() if line.startswith("VERIFY=")]
    state = read_state(flow["path"])
    return (values[-1] if values else None), {i["id"]: i for i in state["items"]}, state


WHOLE_TESTS = [
    pytest.param("tests/test_layout.py", "test_layout", id="layout"),
    pytest.param("tests/checks/test_every_module_is_short.py", "test_every_module_is_short", id="other-name"),
]


@pytest.mark.parametrize("whole,name", WHOLE_TESTS)
def test_an_item_broken_by_another_items_violation_waits(flow, cmd_setup, cmd_implement, cmd_converge, capsys, whole, name):
    """AC7・AC10・I4 — Y は取り消されず `implemented` で待ち、修正へ回るのは X だけ。"""
    _setup(flow, cmd_setup, cmd_implement, whole=whole, name=name)

    verify, items, state = _verify(flow, cmd_converge, capsys)

    assert verify == "fix"
    assert state["fix"]["items"] == ["I-001"]
    x, y = items["I-001"], items["I-002"]
    assert x["status"] == "failing"
    assert x["failed_check"]["kind"] == "lint" and x["failed_check"]["suite"] == "size"
    assert x["failed_check"]["files"] == ["src/big.py"]
    assert y["status"] == "implemented"
    assert y["blocked_by"]["items"] == ["I-001"] and y["blocked_by"]["basis"] == "path"


@pytest.mark.parametrize("whole,name", WHOLE_TESTS)
def test_after_the_deadline_only_the_culprit_is_reverted_with_its_check(flow, cmd_setup, cmd_implement, cmd_converge, capsys, whole, name):
    """AC7・AC8・I5・I6 — 締め切りで X を取り消す理由に suite とファイルが入り、Y は走らせ直されて残る。"""
    _setup(flow, cmd_setup, cmd_implement, whole=whole, name=name, started_at=PAST)

    verify, items, _ = _verify(flow, cmd_converge, capsys)

    assert verify == "done"
    x, y = items["I-001"], items["I-002"]
    assert x["status"] == "reverted"
    assert "size" in x["failure_reason"] and "src/big.py" in x["failure_reason"]
    assert x["failure_reason"] != "範囲テストが修正に使える時間の内に通らなかった"
    assert y["status"] == "verified" and "blocked_by" not in y
    assert y["verify_runs"] == 2


def test_an_item_failing_on_its_own_file_still_fails(flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    """AC9 — Y の変えたファイルが全体を見るテストを落とすと、Y が原因に入り `failing` になる。"""
    _setup(flow, cmd_setup, cmd_implement, x_text=AT_LIMIT.replace("V0 = 0", "V0 = 1"), y_text=OVER)

    verify, items, state = _verify(flow, cmd_converge, capsys)

    assert verify == "fix"
    assert items["I-002"]["status"] == "failing" and "blocked_by" not in items["I-002"]
    assert "I-002" in state["fix"]["items"]


def test_without_a_lint_suite_the_verdict_is_unchanged(flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    """AC12・I7 — 静的解析の suite が無い宣言では原因を判定せず、Y は従来どおり `failing` になる。"""
    _setup(flow, cmd_setup, cmd_implement, lint=False)

    verify, items, state = _verify(flow, cmd_converge, capsys)

    assert verify == "fix"
    y = items["I-002"]
    assert y["status"] == "failing" and "blocked_by" not in y and y["verify_runs"] == 1
    assert items["I-001"]["status"] == "verified"
    assert state["fix"]["items"] == ["I-002"]
