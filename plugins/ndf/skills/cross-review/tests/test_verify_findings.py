"""実行検証（#156 の 3 本目 Task 2）。

**`suggested_check` はレビュワーが書いた文字列である。** そのまま実行すると、誤りや
誘導がそのまま実行になる。実行してよいのは、起動した側が `--verify-command` で渡した
コマンドに当たるものだけである。
"""
from __future__ import annotations

import os
import pathlib

import pytest


def _finding(fid, check, **over):
    f = {
        "finding_id": fid, "agent": "codex", "path": "a.py", "line": 1,
        "body": "x", "severity": "major", "pr": 1, "round": 1,
        "suggested_check": check, "origin_runtimes": ["codex"],
    }
    f.update(over)
    return f


def verify(state_mod, findings, allowed, work, **kw):
    st = {"review_findings": list(findings)}
    state_mod._verify_findings(st, round_no=1, allowed=allowed, work=str(work), **kw)
    return st["review_findings"]


@pytest.fixture()
def work(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text("def test_x(): assert True\n")
    return tmp_path


# ---------- 渡されなければ実行しない ----------

def test_nothing_runs_without_allowed_commands(state_mod, work):
    out = verify(state_mod, [_finding("f0", "pytest tests/t.py")], allowed=[], work=work)
    assert out[0]["verification"]["result"] == "not_run"


# ---------- トークン照合 ----------

def test_a_prefix_of_the_name_is_not_a_match(state_mod, work):
    """`pytest` の宣言に `pytest-danger` が一致してはならない。"""
    out = verify(state_mod, [_finding("f0", "pytest-danger tests/t.py")],
                 allowed=["pytest"], work=work)
    assert out[0]["verification"]["result"] == "not_run"


def test_a_metacharacter_stays_in_the_token(state_mod, work):
    """`pytest; rm -rf /` の先頭トークンは `pytest;` である。"""
    out = verify(state_mod, [_finding("f0", "pytest; rm -rf /")],
                 allowed=["pytest"], work=work)
    assert out[0]["verification"]["result"] == "not_run"


def test_an_option_after_the_command_is_refused(state_mod, work):
    """`-c` や `-p` は任意の設定とプラグインを読み込ませる。"""
    out = verify(state_mod, [_finding("f0", "pytest -p evil tests/t.py")],
                 allowed=["pytest"], work=work)
    assert out[0]["verification"]["result"] == "not_run"


def test_a_multi_token_command_matches(state_mod, work):
    out = verify(state_mod, [_finding("f0", "uv run pytest tests/t.py")],
                 allowed=["uv run pytest"], work=work, runner=lambda *a, **k: 1)
    assert out[0]["verification"]["result"] == "reproduced"


# ---------- 位置指定 ----------

def test_a_path_outside_the_worktree_is_refused(state_mod, work):
    out = verify(state_mod, [_finding("f0", "pytest /tmp/evil.py")],
                 allowed=["pytest"], work=work)
    assert out[0]["verification"]["result"] == "not_run"


def test_a_relative_path_escaping_the_worktree_is_refused(state_mod, work):
    out = verify(state_mod, [_finding("f0", "pytest ../outside/t.py")],
                 allowed=["pytest"], work=work)
    assert out[0]["verification"]["result"] == "not_run"


def test_a_symlink_pointing_outside_is_refused(state_mod, work, tmp_path):
    """**`realpath` で解決する。** `abspath` は symlink をたどらず見逃す。"""
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "evil.py").write_text("")
    os.symlink(outside / "evil.py", work / "link.py")

    out = verify(state_mod, [_finding("f0", "pytest link.py")],
                 allowed=["pytest"], work=work)
    assert out[0]["verification"]["result"] == "not_run"


def test_a_test_id_after_the_path_is_resolved_by_the_file_part(state_mod, work):
    """`tests/t.py::test_x` はファイル名の部分だけを解決する。"""
    out = verify(state_mod, [_finding("f0", "pytest tests/t.py::test_x")],
                 allowed=["pytest"], work=work, runner=lambda *a, **k: 1)
    assert out[0]["verification"]["result"] == "reproduced"


def test_a_command_without_any_path_is_refused(state_mod, work):
    """位置指定が無い実行は、指摘とは無関係な失敗を拾う。"""
    out = verify(state_mod, [_finding("f0", "pytest")], allowed=["pytest"], work=work)
    assert out[0]["verification"]["result"] == "not_run"


# ---------- 再現の向き ----------

def test_a_failing_run_is_reproduced(state_mod, work):
    """指摘は「壊れている」という主張である。**失敗が再現である。**"""
    out = verify(state_mod, [_finding("f0", "pytest tests/t.py")],
                 allowed=["pytest"], work=work, runner=lambda *a, **k: 1)
    assert out[0]["verification"]["result"] == "reproduced"
    assert out[0]["verification"]["exit_code"] == 1


def test_a_passing_run_is_not_reproduced(state_mod, work):
    out = verify(state_mod, [_finding("f0", "pytest tests/t.py")],
                 allowed=["pytest"], work=work, runner=lambda *a, **k: 0)
    assert out[0]["verification"]["result"] == "not_reproduced"


def test_another_exit_code_is_not_run(state_mod, work):
    """対象が無い（4）や収集 0 件（5）は、再現でも棄却でもない。"""
    out = verify(state_mod, [_finding("f0", "pytest tests/t.py")],
                 allowed=["pytest"], work=work, runner=lambda *a, **k: 4)
    assert out[0]["verification"]["result"] == "not_run"


def test_the_reproduced_exit_codes_can_be_given(state_mod, work):
    out = verify(state_mod, [_finding("f0", "pytest tests/t.py")],
                 allowed=["pytest"], work=work, reproduced_codes=[2],
                 runner=lambda *a, **k: 2)
    assert out[0]["verification"]["result"] == "reproduced"


# ---------- 記録 ----------

def test_the_record_carries_the_command_and_source(state_mod, work):
    out = verify(state_mod, [_finding("f0", "pytest tests/t.py")],
                 allowed=["pytest"], work=work, runner=lambda *a, **k: 1)
    v = out[0]["verification"]
    assert v["command"] == "pytest tests/t.py"
    assert v["finding_id"] == "f0"
    assert v["ran_at"]


def test_a_finding_without_a_check_is_not_run(state_mod, work):
    out = verify(state_mod, [_finding("f0", "")], allowed=["pytest"], work=work)
    assert out[0]["verification"]["result"] == "not_run"


# ---------- 実行時刻 ----------

def test_a_record_that_was_not_run_has_no_time(state_mod, work):
    """**実行していない記録へ時刻を入れない。** 実行済みに見える。"""
    out = verify(state_mod, [_finding("f0", "pytest tests/t.py")], allowed=[], work=work)
    v = out[0]["verification"]
    assert v["result"] == "not_run"
    assert v["exit_code"] is None
    assert v["ran_at"] is None


def test_a_refused_command_has_no_time(state_mod, work):
    """渡されていないコマンドは実行されないため、時刻も残らない。"""
    out = verify(state_mod, [_finding("f0", "pytest-danger tests/t.py")],
                 allowed=["pytest"], work=work)
    assert out[0]["verification"]["ran_at"] is None


def test_a_finding_without_a_check_has_no_time(state_mod, work):
    out = verify(state_mod, [_finding("f0", "")], allowed=["pytest"], work=work)
    assert out[0]["verification"]["ran_at"] is None


def test_the_time_is_written_whenever_the_exit_code_is_known(state_mod, work):
    """実行した枝は、結果が `not_run`（対象外の終了コード）でも時刻を持つ。

    **時刻が対になるのは `exit_code` である。** 実行して 4 を受け取った記録は、
    一度も実行していない記録と区別できる。
    """
    out = verify(state_mod, [_finding("f0", "pytest tests/t.py")],
                 allowed=["pytest"], work=work, runner=lambda *a, **k: 4)
    v = out[0]["verification"]
    assert v["result"] == "not_run"
    assert v["exit_code"] == 4
    assert v["ran_at"]


def test_a_reused_result_carries_a_time_too(state_mod, work):
    """同じコマンドは 1 度しか実行しないが、どちらの記録も実行済みである。"""
    out = verify(state_mod, [
        _finding("f0", "pytest tests/t.py"),
        _finding("f1", "pytest tests/t.py"),
    ], allowed=["pytest"], work=work, runner=lambda *a, **k: 1)
    assert out[0]["verification"]["ran_at"]
    assert out[1]["verification"]["ran_at"]


# ---------- 束ねた組 ----------

def test_a_merged_side_is_verified_too(state_mod, work):
    """**束ねた組の全員を対象にする**（`issue-156-pr3-contracts.md`）。"""
    out = verify(state_mod, [
        _finding("f0", "pytest tests/t.py"),
        _finding("f1", "pytest tests/t.py", merged_into="f0"),
    ], allowed=["pytest"], work=work, runner=lambda *a, **k: 1)
    assert out[0]["verification"]["result"] == "reproduced"
    assert out[1]["verification"]["result"] == "reproduced"
    assert out[1]["verification"]["finding_id"] == "f1"


def test_the_group_supplies_the_check_when_the_representative_has_none(state_mod, work):
    """**代表の手順が空でも、組の誰かが書いていれば検証できる。**

    代表の値だけを読むと、取り込みの順序で採否が変わる（実行回数 0・`not_run`）。
    """
    out = verify(state_mod, [
        _finding("f0", ""),
        _finding("f1", "pytest tests/t.py", merged_into="f0"),
    ], allowed=["pytest"], work=work, runner=lambda *a, **k: 1)

    assert out[0]["verification"]["result"] == "reproduced"
    # **出所を残す。** 代表ではなく、その手順を書いた指摘の `finding_id` である。
    assert out[0]["verification"]["finding_id"] == "f1"
    assert out[0]["verification"]["command"] == "pytest tests/t.py"


def test_the_order_of_the_group_does_not_change_the_outcome(state_mod, work):
    """代表と束ねられた側を入れ替えても同じ結果になる。"""
    reversed_group = verify(state_mod, [
        _finding("f0", "pytest tests/t.py"),
        _finding("f1", "", merged_into="f0"),
    ], allowed=["pytest"], work=work, runner=lambda *a, **k: 1)
    assert reversed_group[0]["verification"]["result"] == "reproduced"
    assert reversed_group[0]["verification"]["finding_id"] == "f0"


def test_the_strongest_result_wins_in_the_group(state_mod, work):
    """`reproduced` > `not_reproduced` > `not_run` の順で選び直す。"""
    def runner(argv, _work):
        return 0 if "t.py" in argv[-1] else 1

    out = verify(state_mod, [
        _finding("f0", "pytest tests/t.py"),
        _finding("f1", "pytest tests/u.py", merged_into="f0"),
    ], allowed=["pytest"], work=work, runner=runner)

    assert out[0]["verification"]["result"] == "reproduced"
    assert out[0]["verification"]["finding_id"] == "f1"
    # 束ねられた側は自分の結果を持ち続ける。
    assert out[1]["verification"]["result"] == "reproduced"


def test_the_same_command_runs_only_once(state_mod, work):
    """組の全員が同じ手順を書くのは普通に起こる。**2 度走らせない。**"""
    calls = []

    def runner(argv, _work):
        calls.append(tuple(argv))
        return 1

    verify(state_mod, [
        _finding("f0", "pytest tests/t.py"),
        _finding("f1", "pytest tests/t.py", merged_into="f0"),
    ], allowed=["pytest"], work=work, runner=runner)

    assert len(calls) == 1
