"""最終ゲート修正を push の前に静的解析で判定する（#1693 の AC1〜AC6・AC12）を**実際の git**で確かめる。

| 振る舞い | 確かめること |
| --- | --- |
| 違反を入れた修正 | `merge-final-fix` が 2 で終わり、push せず、HEAD が `fix_base_sha` へ戻る（AC1・I1） |
| 差し戻しの記録 | `lint_rejections` に suite とファイルが残り、次の `final-gate` の `fix_request` に載る（AC2） |
| 通る修正 | 取り込んで push する（AC3） |
| 置き場と起動のされ方 | 手元・`local-scoped-ci-whole`・`--ci-check`、単独・工程の 1 つで同じ（AC4） |
| 触っていないファイル | 修正が触っていないファイルの既存の違反では落ちない（AC5・I2） |
| 差し戻しの後の最終ゲート | 全体テストを走らせず前回の判定を使い回し、入口で何も公開しない（AC6・F3） |
| 静的解析の suite が無い宣言 | 違反を入れた修正も従来どおり取り込んで push する（AC12・I7） |
"""

from __future__ import annotations

import argparse
import sys

import pytest

from crossref_helpers import build_git_flow, commit_with_trailers, git, read_state, strategy_state, write_result, write_state

# 12 行を超える `src/` のファイルを違反にする静的解析（行数の上限の検査と同じ形）
SIZE_CHECK = """import sys

bad = [p for p in sys.argv[1:] if len(open(p).read().splitlines()) > 12]
for p in bad:
    print(f"{p}: too many lines")
sys.exit(1 if bad else 0)
"""
LONG = "".join(f"X{i} = {i}\n" for i in range(20))


def _lint_suite():
    return {
        "name": "size",
        "kind": "lint",
        "command": f"{sys.executable} size_check.py src",
        "scope_command": f"{sys.executable} size_check.py {{paths}}",
        "junit": None,
        "paths": ["src"],
    }


def _call(module, name):
    return getattr(module, name)(argparse.Namespace(id=130))


def _head(work):
    return git("rev-parse", "HEAD", cwd=work).stdout.strip()


def _write(work, rel, text):
    path = work / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def flow(tmp_path, monkeypatch, refactor, patch_lib, env_tmp_dir):
    return build_git_flow(tmp_path, monkeypatch, patch_lib, env_tmp_dir)


def _setup(flow, *, lint=True, name="local-full", ci_check=None, workflow_step=True, existing=None):
    """最終ゲートが落ちて修正を依頼した状態（公開済みの地点 = `fix_base_sha` = HEAD）を作る。"""
    work = flow["work"]
    _write(work, "size_check.py", SIZE_CHECK)
    for rel, text in (existing or {}).items():
        _write(work, rel, text)
    commit_with_trailers(work, "base", {})
    base = _head(work)
    state = read_state(flow["path"])
    strategy = strategy_state(name)
    if lint:
        strategy["suites"].append(_lint_suite())
    state.update(
        strategy=strategy,
        ci_check=ci_check,
        workflow_step=workflow_step,
        phase="final",
        plan={"base_sha": base, "reserve": {"fix": 1.0, "final_fix": 1.0}, "end_at": "2099-01-01T00:00:00+00:00"},
        final_gate={
            "fix_rounds": 1,
            "checks": [],
            "status": "failing",
            "impl": "claude",
            "fix_base_sha": base,
            "last_failing": {"head": base, "detail": "pytest / 終了コード 1", "fix_commits": 0},
        },
    )
    write_state(flow["path"], state)
    return base


def _final_fix(flow, files):
    work = flow["work"]
    for rel, text in files.items():
        _write(work, rel, text)
    sha = commit_with_trailers(work, "Fix the final gate", {"Impl-Runtime": "claude", "Impl-Model": "default"})
    write_result(flow["path"], "claude-final-fix", {"elapsed_seconds": 5, "commits": [{"sha": sha}]})
    return sha


VARIANTS = [
    pytest.param({"name": "local-full"}, id="local"),
    pytest.param({"name": "local-scoped-ci-whole"}, id="scoped-ci-whole"),
    pytest.param({"name": "local-full", "ci_check": "tests"}, id="ci-check"),
    pytest.param({"name": "local-full", "workflow_step": False}, id="standalone"),
]


@pytest.mark.parametrize("variant", VARIANTS)
def test_a_final_fix_with_a_lint_violation_is_reverted_without_a_push(flow, cmd_final_fix, capsys, variant):
    """AC1・AC2・AC4 — 違反を入れた修正は push されず、HEAD から外れ、違反が記録に残る。"""
    base = _setup(flow, **variant)
    _final_fix(flow, {"src/calc.py": LONG})

    with pytest.raises(SystemExit) as e:
        _call(cmd_final_fix, "cmd_merge_final_fix")

    assert e.value.code == 2
    assert "FINAL_FIX=lint_rejected" in capsys.readouterr().out
    assert flow["pushed"] == []
    assert _head(flow["work"]) == base
    gate = read_state(flow["path"])["final_gate"]
    assert not gate.get("fix_commits")
    rejection = gate["lint_rejections"][-1]
    assert rejection["round"] == 1 and len(rejection["commits"]) == 1
    assert [(r["suite"], r["files"]) for r in rejection["rejections"]] == [("size", ["src/calc.py"])]


@pytest.mark.parametrize("variant", VARIANTS)
def test_a_clean_final_fix_is_taken_in_and_pushed(flow, cmd_final_fix, variant):
    """AC3・AC4 — 静的解析を通る修正は従来どおり取り込み、push する。"""
    _setup(flow, **variant)
    sha = _final_fix(flow, {"src/calc.py": "def add(a, b):\n    return a + b\n"})

    _call(cmd_final_fix, "cmd_merge_final_fix")

    gate = read_state(flow["path"])["final_gate"]
    assert gate["fix_commits"] == [sha]
    assert flow["pushed"] == [sha]
    assert "lint_rejections" not in gate


def test_an_existing_violation_in_an_untouched_file_does_not_reject(flow, cmd_final_fix):
    """AC5・I2 — 修正が触っていないファイルの既存の違反では落ちない。"""
    _setup(flow, existing={"src/legacy.py": LONG})
    sha = _final_fix(flow, {"src/calc.py": "def add(a, b):\n    return a + b\n"})

    _call(cmd_final_fix, "cmd_merge_final_fix")

    assert read_state(flow["path"])["final_gate"]["fix_commits"] == [sha]
    assert flow["pushed"] == [sha]


def test_without_a_lint_suite_the_take_in_is_unchanged(flow, cmd_final_fix):
    """AC12・I7 — 静的解析の suite が無い宣言では、違反を入れた修正も取り込んで push する。"""
    _setup(flow, lint=False)
    sha = _final_fix(flow, {"src/calc.py": LONG})

    _call(cmd_final_fix, "cmd_merge_final_fix")

    gate = read_state(flow["path"])["final_gate"]
    assert gate["fix_commits"] == [sha] and flow["pushed"] == [sha]
    assert "lint_rejections" not in gate


def test_the_next_gate_reuses_the_verdict_and_hands_the_rejection_on(flow, cmd_final_fix, cmd_gate, patch_lib, capsys):
    """AC2・AC6・F3 — 差し戻しの後の最終ゲートは全体テストを走らせず、何も公開せず、依頼に違反を載せる。"""
    base = _setup(flow)
    _final_fix(flow, {"src/calc.py": LONG})
    with pytest.raises(SystemExit):
        _call(cmd_final_fix, "cmd_merge_final_fix")
    ran: list = []
    patch_lib("run_with_timeout", lambda *a, **k: ran.append(a) or (0, False))

    with pytest.raises(SystemExit) as e:
        _call(cmd_gate, "cmd_final_gate")

    assert e.value.code == 2 and "FINAL_GATE=failing" in capsys.readouterr().out
    assert ran == [], "同じ HEAD では全体テストも静的解析も走らせない"
    assert flow["pushed"] == [] and _head(flow["work"]) == base
    gate = read_state(flow["path"])["final_gate"]
    assert gate["checks"][-1]["mode"] == "reused"
    assert gate["fix_rounds"] == 2
    assert "size" in gate["fix_request"] and "src/calc.py" in gate["fix_request"]


def test_the_gate_runs_again_once_the_head_moves(flow, cmd_final_fix, cmd_gate, patch_lib):
    """F3 — 差し戻しの後でも、HEAD が前回の判定の地点から進んでいれば使い回さない。"""
    _setup(flow)
    _final_fix(flow, {"src/calc.py": LONG})
    with pytest.raises(SystemExit):
        _call(cmd_final_fix, "cmd_merge_final_fix")
    _write(flow["work"], "src/extra.py", "Y = 1\n")
    commit_with_trailers(flow["work"], "moved", {})
    ran: list = []
    patch_lib("run_with_timeout", lambda command, *a, **k: ran.append(command) or (0, False))

    _call(cmd_gate, "cmd_final_gate")

    assert ran, "HEAD が進めば走らせる"
    assert read_state(flow["path"])["final_gate"]["checks"][-1]["mode"] != "reused"
