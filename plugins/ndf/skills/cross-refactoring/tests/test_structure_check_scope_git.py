"""構造チェックを項目の範囲テストで走らせる（#1668）を**実際の git と構造チェック**で確かめる。

| 振る舞い | 確かめること |
| --- | --- |
| 宣言 | このリポジトリの宣言に静的解析の suite `script-structure` があり、`plugins/ndf/` を変えたときだけ組まれる |
| 範囲テスト | 行数の上限を超える項目と同名の関数を足す項目が、その項目の範囲テストで落ちて修正へ回り、ほかの項目は `verified` のまま残る |
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil

import pytest

from crossref_helpers import build_git_flow, commit_with_trailers, git, item_trailers, read_state, strategy_state, write_state

REPO = pathlib.Path(__file__).resolve().parents[5]
CHECK = REPO / "scripts" / "check-script-structure.py"
FAR = "2099-01-01T00:00:00+00:00"


def _declared():
    import test_strategy as ts

    return ts, ts.resolve(json.loads((REPO / ".ndf" / "project.json").read_text(encoding="utf-8")))


def _structure_suite(strategy):
    return next(s for s in strategy.suites if s.name == "script-structure")


def test_the_declaration_has_the_structure_check_as_a_lint_suite():
    ts, strategy = _declared()
    suite = _structure_suite(strategy)
    assert suite.kind == ts.LINT and ts.has_paths(suite.scope_command)
    assert "scripts/check-script-structure.py" in suite.scope_command
    assert suite.paths == ["plugins/ndf", "scripts/script-structure-allow", "scripts/check-script-structure.py"]


def test_the_structure_check_runs_only_when_its_paths_change():
    ts, strategy = _declared()
    changed = ["plugins/ndf/scripts/lib/test_strategy.py", "scripts/script-structure-allow/x.json", "README.md"]
    lint = [r for r in ts.scope_runs(strategy, [], changed) if r.suite == "script-structure"]
    assert len(lint) == 1
    assert lint[0].command.endswith("plugins/ndf/scripts/lib/test_strategy.py scripts/script-structure-allow/x.json")
    assert [r for r in ts.scope_runs(strategy, [], ["README.md", "issues/x.md"]) if r.suite == "script-structure"] == []


# ---------- 項目の範囲テスト ----------


@pytest.fixture
def flow(tmp_path, monkeypatch, refactor, patch_lib, env_tmp_dir):
    return build_git_flow(tmp_path, monkeypatch, patch_lib, env_tmp_dir)


def _item(item_id, rank, path):
    return {
        "id": item_id,
        "rank": rank,
        "path": path,
        "symbol": "f",
        "smell": "long_method",
        "technique": "extract_method",
        "severity": "major",
        "proposed_by": ["codex"],
        "tier": "high",
        "risk": False,
        "tests": [],
        "test_targets": ["tests/test_calc.py"],
        "scope_commands": [{"suite": "pytest", "kind": "test", "command": "pytest -q tests/test_calc.py"}],
        "command_source": "targets",
        "estimate": {"test": 0.0, "implement": 1.3, "verify": 0.2},
        "start_deadline": FAR,
        "test_start_deadline": None,
        "status": "planned",
        "commits": {"test": None, "implement": None, "fix": []},
        "seconds": {},
        "fix_count": 0,
        "danger": [],
        "estimated_diff_lines": 200,
    }


def _write(work, rel, text):
    p = work / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _call(module, name, **kwargs):
    return getattr(module, name)(argparse.Namespace(id=130, **kwargs))


def _emitted(capsys, key):
    values = [line.split("=", 1)[1] for line in capsys.readouterr().out.splitlines() if line.startswith(key + "=")]
    return values[-1] if values else None


def test_items_breaking_the_structure_fail_their_own_scope_test(flow, cmd_setup, cmd_implement, cmd_converge, capsys):
    """#1668 の受け入れ条件 6 — PR 1663 の 2 つの形（行数の上限を超える・同名の関数を足す）を、ほかの項目と並べて検証する。"""
    work = flow["work"]
    (work / "scripts").mkdir(exist_ok=True)
    shutil.copy(CHECK, work / "scripts" / "check-script-structure.py")
    _write(work, "plugins/ndf/scripts/lib/accounts.py", "def remove(p):\n    return p\n")
    _write(work, "plugins/ndf/scripts/big.py", "x = 1\n" * 400)
    _write(work, "plugins/ndf/scripts/clean.py", "def clean():\n    return 1\n")
    commit_with_trailers(work, "構造チェックの木", {})

    _, declared = _declared()
    state = read_state(flow["path"])
    state["strategy"] = strategy_state(suites=[*strategy_state()["suites"], _structure_suite(declared).as_state()])
    changes = {
        "I-001": ("plugins/ndf/scripts/big.py", lambda w: _write(w, "plugins/ndf/scripts/big.py", "x = 1\n" * 501)),
        "I-002": ("plugins/ndf/scripts/trash.py", lambda w: _write(w, "plugins/ndf/scripts/trash.py", "def remove(p):\n    return None\n")),
        "I-003": ("plugins/ndf/scripts/clean.py", lambda w: _write(w, "plugins/ndf/scripts/clean.py", "def clean():\n    return 2\n")),
        "I-004": ("src/calc.py", lambda w: _write(w, "src/calc.py", (w / "src" / "calc.py").read_text() + "\nX = 1\n")),
    }
    state["items"] = [_item(i, rank, path) for rank, (i, (path, _)) in enumerate(changes.items(), start=1)]
    state["plan"] = {
        "base_sha": git("rev-parse", "HEAD", cwd=work).stdout.strip(),
        "reserve": {"danger_whole_test": 0.1, "final_whole_test": 0.1, "fix": 5.5},
        "end_at": FAR,
        "table_source": "defaults",
    }
    state["target_scope"] = ["src", "tests", "plugins/ndf"]
    state["phase"] = "implement"
    write_state(flow["path"], state)
    _call(cmd_setup, "cmd_start_phase", phase="implement")
    for item_id, (_, change) in changes.items():
        change(work)
        commit_with_trailers(work, f"Refactor {item_id}", item_trailers(item_id))
    _call(cmd_implement, "cmd_merge_implement")
    capsys.readouterr()

    _call(cmd_converge, "cmd_verify")

    assert _emitted(capsys, "VERIFY") == "fix"
    # I-003 も plugins/ndf/ を変えて構造チェックを走らせるが、木に残る I-001・I-002 の違反は I-003 に関わらないので通る
    status = {i["id"]: i["status"] for i in read_state(flow["path"])["items"]}
    assert status == {"I-001": "failing", "I-002": "failing", "I-003": "verified", "I-004": "verified"}
