"""通過記録と報告のテスト（#221）。

判定は `scripts/lib/workflow-common.sh` に集約されている。テストはこの層と入口の
スクリプトに対して書き、GitHub への通信は行わない。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from workflow_helpers import (
    SLUG,
    base_env,
    init_repo,
    path_with,
    run_lib,
    run_stage_check,
    state_file,
)

# #161 の実測の並び（issue #221 の本文）。実装レビューと後片付けが 2 回ずつ現れる。
MEASURED_161 = [
    "作業場所の用意",
    "要求と受け入れ条件",
    "設計",
    "ドキュメント再構成",
    "ドキュメントレビュー",
    "後片付け",
    "計画",
    "完了判定",
    "実装レビュー",
    "後片付け",
]
# 上の並びに、詳細設計・実装・構造改善・Pull Request を足した完全版。抜けは確定仕様化だけになる。
FULL_161 = [
    "作業場所の用意",
    "要求と受け入れ条件",
    "設計",
    "詳細設計",
    "ドキュメント再構成",
    "ドキュメントレビュー",
    "後片付け",
    "計画",
    "実装",
    "構造改善",
    "完了判定",
    "実装レビュー",
    "Pull Request",
    "後片付け",
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
    record(repo, state, 221, "stage", "実装レビュー")
    record(repo, state, 221, "stage", "実装レビュー")

    saved = json.loads(state_file(state, 221).read_text(encoding="utf-8"))
    assert saved["stages"] == ["実装レビュー", "実装レビュー"]


def test_a_report_without_any_record_says_so(repo: Path, state: Path) -> None:
    """#221-4: すべての工程を欠落として並べない。"""
    result = report(repo, state, 999)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "#999 の進行の記録がありません。"
    assert "記録なし" not in result.stdout


def test_a_report_lists_the_recorded_stages(repo: Path, state: Path) -> None:
    """#221-2: 報告はスクリプトの出力として出る。"""
    seed(repo, state, 161, "standard", FULL_161)

    out = report(repo, state, 161).stdout

    assert out.splitlines()[0] == "#161 の通過工程（standard）"
    assert "  記録あり: 要求と受け入れ条件 / 作業場所の用意 / 設計 / ドキュメント再構成 /" in out


def test_the_measured_order_reports_only_the_missing_stage(repo: Path, state: Path) -> None:
    """#221-3: 工程が前後しても誤検知しない。#161 で抜けたのは確定仕様化だけである。"""
    seed(repo, state, 161, "standard", FULL_161)

    out = report(repo, state, 161).stdout

    assert "  記録なし: 確定仕様化" in out
    assert "実装レビュー" not in out.split("記録なし:")[1]


def test_the_report_shows_how_to_record_the_missing_stage(repo: Path, state: Path) -> None:
    seed(repo, state, 161, "standard", FULL_161)

    out = report(repo, state, 161).stdout

    assert 'bash "$SCRIPTS/projects-sync.sh" 161 stage "確定仕様化"' in out


def test_a_report_without_a_gap_says_so(repo: Path, state: Path) -> None:
    seed(repo, state, 161, "standard", [*MEASURED_161[:3], "詳細設計", MEASURED_161[3]])

    out = report(repo, state, 161).stdout

    assert "記録の無い必須の工程はありません。" in out
    assert "記録なし" not in out


def test_a_conditional_stage_is_listed_apart(repo: Path, state: Path) -> None:
    """条件付きの工程は必須と分けて出す。`legacy-refactor` のドキュメントレビューがこれにあたる。"""
    seed(
        repo,
        state,
        161,
        "legacy-refactor",
        [
            "作業場所の用意",
            "設計",
            "詳細設計",
            "計画",
            "実装",
            "構造改善",
            "実装レビュー",
            "完了判定",
            "Pull Request",
            "後片付け",
        ],
    )

    out = report(repo, state, 161).stdout

    assert "  条件付き: ドキュメント再構成 / ドキュメントレビュー" in out
    assert "記録なし" not in out


def test_stages_after_the_furthest_record_are_not_listed(repo: Path, state: Path) -> None:
    """まだ来ていない工程は欠落ではない。**いちばん先の記録までを見る。**"""
    seed(repo, state, 161, "standard", ["作業場所の用意", "要求と受け入れ条件", "設計"])

    out = report(repo, state, 161).stdout

    assert "デプロイ" not in out
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


@pytest.mark.parametrize(
    "content",
    [
        "{壊れている",
        json.dumps(
            {
                "version": 2,
                "repo": SLUG,
                "issue": 221,
                "mode": "standard",
                "stages": ["設計"],
            },
            ensure_ascii=False,
        ),
    ],
    ids=["broken-json", "unsupported-version"],
)
def test_record_replaces_an_unreadable_state(repo: Path, state: Path, content: str) -> None:
    """現状固定: 読めない通過記録への再記録は旧モードと旧工程を引き継がない。"""
    path = state_file(state, 221)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")

    result = run_lib(f"wf_record {SLUG} 221 stage 計画", cwd=repo, env=base_env(state))

    assert result.returncode == 0, result.stderr
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["version"] == 1
    assert saved["repo"] == SLUG
    assert saved["issue"] == 221
    assert saved["stages"] == ["計画"]
    assert "mode" not in saved


def test_a_repository_without_the_projects_declaration_still_records(repo: Path, state: Path) -> None:
    """#221-7: ボードに載っていない課題でも働く。宣言ファイルは読まない。"""
    assert not (repo / ".ndf" / "projects.json").exists()

    seed(repo, state, 266, "standard", ["作業場所の用意", "設計"])

    assert state_file(state, 266).is_file()
    assert "#266 の通過工程（standard）" in report(repo, state, 266).stdout


def test_two_records_at_once_keep_both_stages(repo: Path, state: Path) -> None:
    """#221-8: 同じ課題へ同時に記録しても通過記録は壊れない。"""
    env = base_env(state, {"NDF_STAGE_LOCK_TIMEOUT": "20"})
    procs = [
        subprocess.Popen(
            ["bash", str(Path(__file__).resolve().parents[1] / "scripts/stage-check.sh"), "record", "221", "stage", stage],
            cwd=str(repo),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for stage in ("設計", "計画", "実装", "実装レビュー")
    ]
    for proc in procs:
        proc.communicate()

    saved = json.loads(state_file(state, 221).read_text(encoding="utf-8"))
    assert sorted(saved["stages"]) == sorted(["設計", "計画", "実装", "実装レビュー"])


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


def test_a_record_keeps_a_live_lock_of_another_container(repo: Path, state: Path) -> None:
    """通過記録は複数のコンテナが共有する置き場にある。ロックは PID 名前空間を書き、別のコンテナの生きたロックを
    PID で消えたと読んで捨てない。"""
    record(repo, state, 221, "stage", "設計")
    path = state_file(state, 221)
    lock = Path(str(path) + ".lockdir")
    assert run_lib(f'wf_lock_acquire "{lock}" 1').returncode == 0
    scope, until = (lock / "owner_scope").read_text(encoding="utf-8").splitlines()
    assert scope == run_lib("_ndf_lock_scope").stdout.strip() and int(until) > time.time()

    # 別のコンテナの持ち主: その PID はこちらに無い
    (lock / "owner_scope").write_text(f"pid:[4026500000]\n{until}\n", encoding="utf-8")
    (lock / "pid").write_text("4194000\n", encoding="utf-8")
    before = path.read_bytes()
    result = record(repo, state, 221, "stage", "計画")

    assert result.returncode == 0 and path.read_bytes() == before and lock.exists()


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
    # 通したのが jq の欠如によることを見る。jq があれば通過記録が書かれる。
    assert not state_file(state, 221).exists()


def test_records_at_once_never_skip_a_stage(repo: Path, state: Path) -> None:
    """#308-2 / 3: 同時の記録を繰り返しても工程が欠けず、飛ばした記録も出ない。

    1 回の実行では取りこぼしがあっても通ることがあるため、繰り返して件数で見る。
    受け入れ条件が求める 60 回は完了判定として手元で回す。ここでの試行は 4 回にする
    （#884。8 回から減らした。4 つの記録が同時に走る試行を繰り返す目的は変わらない）。
    """
    stages = ("設計", "計画", "実装", "実装レビュー")
    env = base_env(state, {"NDF_STAGE_LOCK_TIMEOUT": "20"})
    script = str(Path(__file__).resolve().parents[1] / "scripts/stage-check.sh")

    short = []
    skipped = 0
    for trial in range(4):
        issue = 3080 + trial
        procs = [
            subprocess.Popen(
                ["bash", script, "record", str(issue), "stage", stage],
                cwd=str(repo),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for stage in stages
        ]
        for proc in procs:
            skipped += proc.communicate()[1].count("通過記録が使用中")

        path = state_file(state, issue)
        saved = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"stages": []}
        if sorted(saved["stages"]) != sorted(stages):
            short.append(saved["stages"])

    assert short == [], f"工程がそろわなかった試行 {len(short)} 件: {short}"
    assert skipped == 0, f"通過記録が使用中で飛ばした記録 {skipped} 件"


# --- #1078: 進め方 fast -------------------------------------------------------

# fast でデプロイまで進んだ課題。構造改善・実装レビュー・確定仕様化・振り返りは記録していない。
FAST_THROUGH_RELEASE = [
    "要求と受け入れ条件",
    "作業場所の用意",
    "設計",
    "詳細設計",
    "ドキュメント再構成",
    "ドキュメントレビュー",
    "計画",
    "実装",
    "完了判定",
    "Pull Request",
    "後片付け",
    "デプロイ",
]


def test_fast_lists_trigger_and_deferred_stages_apart_from_missing(repo: Path, state: Path) -> None:
    seed(repo, state, 1078, "standard", FAST_THROUGH_RELEASE)
    record(repo, state, 1078, "pace", "fast")

    out = report(repo, state, 1078).stdout

    assert out.splitlines()[0] == "#1078 の通過工程（standard・進め方 fast）"
    assert "記録なし" not in out
    assert "  トリガー: 構造改善 / 実装レビュー" in out
    assert "  まとめる: 確定仕様化 / 振り返り" in out
    assert 'stage "確定仕様化"' not in out and 'stage "構造改善"' not in out


def test_without_a_pace_record_the_report_is_unchanged(repo: Path, state: Path) -> None:
    seed(repo, state, 1078, "standard", FAST_THROUGH_RELEASE)

    out = report(repo, state, 1078).stdout

    assert "  記録なし: 構造改善 / 実装レビュー / 確定仕様化" in out
    assert "トリガー" not in out and "まとめる" not in out


def test_a_deferred_stage_moves_to_recorded_once_it_is_recorded(repo: Path, state: Path) -> None:
    seed(repo, state, 1078, "standard", [*FAST_THROUGH_RELEASE, "確定仕様化", "振り返り", "棚卸し"])
    record(repo, state, 1078, "pace", "fast")

    out = report(repo, state, 1078).stdout

    assert "まとめる" not in out and "確定仕様化" in out.split("記録あり:")[1].splitlines()[0]


def test_an_unknown_pace_returns_two(repo: Path, state: Path) -> None:
    assert record(repo, state, 1078, "pace", "slow").returncode == 2


def test_the_fast_stage_names_are_rows_of_the_workflow_table() -> None:
    """SKILL.md の進め方の表に載せる工程の名前は、工程表の行名と一致する。"""
    for stage in (
        "構造改善",
        "実装レビュー",
        "確定仕様化",
        "振り返り",
        "作業場所の用意",
        "計画",
        "実装",
        "完了判定",
        "Pull Request",
        "後片付け",
        "デプロイ",
        "リリース後テスト",
        "要求と受け入れ条件",
        "設計",
        "ドキュメント再構成",
        "ドキュメントレビュー",
    ):
        result = run_lib(f'wf_is_stage "{stage}"')
        assert result.returncode == 0, stage


# --- 工程表と対応表の行名（#3144 の I9） -------------------------------------

_SKILL_ROOT = Path(__file__).resolve().parents[1]


def _matrix_stages() -> list[str]:
    result = run_lib("wf_stages")
    assert result.returncode == 0, result.stderr
    return [line for line in result.stdout.splitlines() if line]


def _table_rows(path: Path, header: tuple[str, ...]) -> list[list[str]]:
    """見出しの先頭の列が `header` の Markdown の表を読み、本文の行のセルを返す（記号 ` を外す）。"""
    rows: list[list[str]] = []
    inside = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            if inside:
                break
            continue
        cells = [c.replace("`", "").strip() for c in line.strip().strip("|").split("|")]
        if not inside:
            inside = tuple(cells[: len(header)]) == header
            continue
        if set(cells[0]) <= {"-", " "}:
            continue
        rows.append(cells)
    return rows


def test_the_mode_table_lists_the_matrix_stages_in_order() -> None:
    """SKILL.md の「モードごとに起動する Skill」の行名の並びが、工程の分類の行名の並びと一致する。"""
    rows = _table_rows(_SKILL_ROOT / "SKILL.md", ("工程", "light", "operation"))
    assert [r[0] for r in rows] == _matrix_stages()


def test_the_board_table_uses_the_matrix_stage_names() -> None:
    """projects-tracking.md の対応表の 1 列目と `stage` の値が、工程の分類の行名にある。"""
    rows = _table_rows(
        _SKILL_ROOT / "references" / "projects-tracking.md",
        ("工程表の行", "記録する Skill", "stage の値"),
    )
    stages = set(_matrix_stages())
    assert rows
    for row in rows:
        assert row[0] in stages, row
        assert row[2] in stages, row


def test_the_detailed_design_stage_follows_the_design_stage() -> None:
    """「詳細設計」は「設計」の直後で、legacy-refactor・standard だけが必須である（#3144 の I1）。"""
    stages = _matrix_stages()
    assert stages[stages.index("設計") + 1] == "詳細設計"
    for mode, want in (
        ("light", "-"),
        ("operation", "-"),
        ("legacy-refactor", "R"),
        ("standard", "R"),
        ("documentation", "-"),
    ):
        result = run_lib(f"wf_stage_class {mode} 詳細設計")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == want, mode
