"""通過工程の控えと報告のテスト（#221）。

判定は `scripts/lib/workflow-common.sh` に集約されている。テストはこの層と入口の
スクリプトに対して書き、GitHub への通信は行わない。
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from workflow_helpers import (
    SLUG, base_env, init_repo, path_with, run_stage_check, state_file,
)

# #161 の実測の並び（issue #221 の本文）。レビューと後片付けが 2 回ずつ現れる。
MEASURED_161 = [
    "作業場所の用意", "要求と受け入れ条件", "設計", "設計レビュー", "後片付け",
    "計画", "完了判定", "レビュー", "後片付け",
]
# 上の並びに、実装・構造改善・Pull Request を足した完全版。抜けは確定仕様化だけになる。
FULL_161 = [
    "作業場所の用意", "要求と受け入れ条件", "設計", "設計レビュー", "後片付け",
    "計画", "実装", "構造改善", "完了判定", "レビュー", "Pull Request", "後片付け",
]


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return init_repo(tmp_path / "main")


@pytest.fixture()
def state(tmp_path: Path) -> Path:
    return tmp_path / "state"


def record(repo: Path, state: Path, issue: int, key: str, value: str) -> subprocess.CompletedProcess:
    return run_stage_check("record", str(issue), key, value, cwd=repo, env=base_env(state))


def report(repo: Path, state: Path, issue: int) -> subprocess.CompletedProcess:
    return run_stage_check("report", str(issue), cwd=repo, env=base_env(state))


def seed(repo: Path, state: Path, issue: int, mode: str, stages: list[str]) -> None:
    record(repo, state, issue, "mode", mode)
    for stage in stages:
        record(repo, state, issue, "stage", stage)


def test_a_record_writes_the_state_file(repo: Path, state: Path) -> None:
    result = record(repo, state, 221, "stage", "設計")

    assert result.returncode == 0, result.stderr
    saved = json.loads(state_file(state, 221).read_text(encoding="utf-8"))
    assert saved["version"] == 1
    assert saved["repo"] == SLUG
    assert saved["issue"] == 221
    assert saved["stages"] == ["設計"]


def test_the_same_stage_twice_is_kept_twice(repo: Path, state: Path) -> None:
    """事象の記録を採る。既にある値を書き換えない。"""
    record(repo, state, 221, "stage", "レビュー")
    record(repo, state, 221, "stage", "レビュー")

    saved = json.loads(state_file(state, 221).read_text(encoding="utf-8"))
    assert saved["stages"] == ["レビュー", "レビュー"]


def test_a_report_without_any_record_says_so(repo: Path, state: Path) -> None:
    """#221-4: すべての工程を欠落として並べない。"""
    result = report(repo, state, 999)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "#999 の進行の記録がありません。"
    assert "記録なし" not in result.stdout


def test_a_report_lists_the_recorded_stages(repo: Path, state: Path) -> None:
    """#221-2: 報告はスクリプトの出力として出る。"""
    seed(repo, state, 161, "architecture", FULL_161)

    out = report(repo, state, 161).stdout

    assert out.splitlines()[0] == "#161 の通過工程（architecture）"
    assert "  記録あり: 作業場所の用意 / 要求と受け入れ条件 / 設計 / 設計レビュー / 計画 /" in out


def test_the_measured_order_reports_only_the_missing_stage(repo: Path, state: Path) -> None:
    """#221-3: 工程が前後しても誤検知しない。#161 で抜けたのは確定仕様化だけである。"""
    seed(repo, state, 161, "architecture", FULL_161)

    out = report(repo, state, 161).stdout

    assert "  記録なし: 確定仕様化" in out
    assert "レビュー" not in out.split("記録なし:")[1]


def test_the_report_shows_how_to_record_the_missing_stage(repo: Path, state: Path) -> None:
    seed(repo, state, 161, "architecture", FULL_161)

    out = report(repo, state, 161).stdout

    assert 'bash "$SCRIPTS/projects-sync.sh" 161 stage "確定仕様化"' in out


def test_a_report_without_a_gap_says_so(repo: Path, state: Path) -> None:
    seed(repo, state, 161, "architecture", MEASURED_161[:4])

    out = report(repo, state, 161).stdout

    assert "記録の無い必須の工程はありません。" in out
    assert "記録なし" not in out


def test_a_conditional_stage_is_listed_apart(repo: Path, state: Path) -> None:
    """条件付きの工程は必須と分けて出す。`standard` の確定仕様化がこれにあたる。"""
    seed(repo, state, 161, "standard", FULL_161)

    out = report(repo, state, 161).stdout

    assert "  条件付き: 確定仕様化" in out
    assert "記録なし" not in out


def test_stages_after_the_furthest_record_are_not_listed(repo: Path, state: Path) -> None:
    """まだ来ていない工程は欠落ではない。**いちばん先の記録までを見る。**"""
    seed(repo, state, 161, "architecture", ["作業場所の用意", "要求と受け入れ条件", "設計"])

    out = report(repo, state, 161).stdout

    assert "配布" not in out
    assert "振り返り" not in out


def test_a_report_without_a_mode_does_not_judge(repo: Path, state: Path) -> None:
    """モードが取れないときは必須の工程を判定しない。"""
    record(repo, state, 161, "stage", "設計")

    out = report(repo, state, 161).stdout

    assert "モードの記録が無いため、必須の工程は判定しません。" in out
    assert "記録なし" not in out


def test_a_broken_state_file_is_read_as_no_record(repo: Path, state: Path) -> None:
    record(repo, state, 221, "stage", "設計")
    state_file(state, 221).write_text("{壊れている", encoding="utf-8")

    out = report(repo, state, 221).stdout

    assert out.strip() == "#221 の進行の記録がありません。"


def test_a_state_file_of_another_version_is_ignored(repo: Path, state: Path) -> None:
    record(repo, state, 221, "stage", "設計")
    path = state_file(state, 221)
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["version"] = 2
    path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")

    out = report(repo, state, 221).stdout

    assert out.strip() == "#221 の進行の記録がありません。"


def test_a_repository_without_the_projects_declaration_still_records(repo: Path, state: Path) -> None:
    """#221-7: 盤面に載っていない課題でも働く。宣言ファイルは読まない。"""
    assert not (repo / ".ndf" / "projects.json").exists()

    seed(repo, state, 266, "architecture", ["作業場所の用意", "設計"])

    assert state_file(state, 266).is_file()
    assert "#266 の通過工程（architecture）" in report(repo, state, 266).stdout


def test_two_records_at_once_keep_both_stages(repo: Path, state: Path) -> None:
    """#221-8: 同じ課題へ同時に記録しても控えは壊れない。"""
    env = base_env(state, {"NDF_STAGE_LOCK_TIMEOUT": "20"})
    procs = [
        subprocess.Popen(
            ["bash", str(Path(__file__).resolve().parents[1] / "scripts/stage-check.sh"),
             "record", "221", "stage", stage],
            cwd=str(repo), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        for stage in ("設計", "計画", "実装", "レビュー")
    ]
    for proc in procs:
        proc.communicate()

    saved = json.loads(state_file(state, 221).read_text(encoding="utf-8"))
    assert sorted(saved["stages"]) == sorted(["設計", "計画", "実装", "レビュー"])


def test_a_record_that_cannot_take_the_lock_changes_nothing(repo: Path, state: Path) -> None:
    """排他を取れないときは書き込みそのものを行わない。"""
    record(repo, state, 221, "stage", "設計")
    path = state_file(state, 221)
    before = path.read_bytes()
    lock = Path(str(path) + ".lockdir")
    lock.mkdir()
    # **持ち主はこのテスト自身にする。** PID 1 を持ち主にすると、その利用者が signal を
    # 送れない環境で「持ち主が消えた」と判定され、ロックが捨てられて記録が通る。
    (lock / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")

    result = record(repo, state, 221, "stage", "計画")

    assert result.returncode == 0
    assert path.read_bytes() == before
    assert len(result.stderr.strip().splitlines()) == 1


def test_a_wrong_argument_returns_two(repo: Path, state: Path) -> None:
    for args in (("record",), ("record", "221", "stage"), ("report",), ("知らない", "221")):
        result = run_stage_check(*args, cwd=repo, env=base_env(state))
        assert result.returncode == 2, args
        assert result.stderr.strip()


def test_an_unknown_key_returns_two(repo: Path, state: Path) -> None:
    result = run_stage_check("record", "221", "worktree", ".worktrees/x", cwd=repo, env=base_env(state))

    assert result.returncode == 2


def test_a_stage_outside_the_workflow_table_returns_two(repo: Path, state: Path) -> None:
    result = run_stage_check("record", "221", "stage", "存在しない工程", cwd=repo, env=base_env(state))

    assert result.returncode == 2


def test_a_record_without_jq_does_not_fail(repo: Path, state: Path, tmp_path: Path) -> None:
    """#221 の経路は通す側へ倒す。判定に要るコマンドが無くても工程を止めない。"""
    env = base_env(state, {"PATH": path_with(tmp_path / "bin", without=("jq",))})

    result = run_stage_check("record", "221", "stage", "設計", cwd=repo, env=env)

    assert result.returncode == 0
