"""席の名前を受け口が通すか（#727 の AC21）。

担当の単位は「席の名前」になった（設計の決定 10）。形は `assignment.SEAT_PATTERN`
（ランタイム名か、その名前に `-2`〜`-9` を付けたもの）。使える者が 2 者に満たない
ラウンドでは、同じランタイムの 2 つ目（`claude-2`）が席に入る。**受け口がこの形を
弾くと、結果を残した担当が「結果なし」として扱われる。**

見るのは結果の受け口・起動スクリプト・監視の位置引数・計測の 4 つである。綴りのチェックは
argparse の型が行い、通らなければ終了コード 2 になる。シェル側は席の形に合わない名前を
終了コード 1 で弾く。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import pytest

PR = 4243
SEAT = "claude-2"


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod) -> pathlib.Path:
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def review_posted(monkeypatch, state_mod):
    """投稿の実在確認は届いた前提にする。ここで見るのは席の名前である。"""
    monkeypatch.setattr(state_mod, "_review_exists", lambda repo, pr, url: True)


def _seed_state(tmp_dir: pathlib.Path) -> None:
    state = {
        "current_pr": PR,
        "rounds": [{"round": 1, "pr": PR, "started_at": "2026-09-19T00:00:00+00:00",
                    "reviewers": ["codex", SEAT]}],
        "final": None,
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


# ---------------- 引数のチェック ----------------

def test_the_parser_accepts_a_second_seat(state_mod):
    args = state_mod.build_parser().parse_args(["read-result", "1", SEAT])
    assert args.agent == SEAT


def test_the_parser_still_accepts_every_runtime(state_mod):
    parser = state_mod.build_parser()
    for runtime in state_mod.assignment.ALL_RUNTIMES:
        assert parser.parse_args(["read-result", "1", runtime]).agent == runtime


@pytest.mark.parametrize("seat", ["gemini", "claude-1", "claude-10", "claude_2", ""])
def test_a_name_outside_the_seat_pattern_exits_with_two(state_mod, seat):
    with pytest.raises(SystemExit) as e:
        state_mod.build_parser().parse_args(["read-result", "1", seat])
    assert e.value.code == 2


# ---------------- 記録の鍵 ----------------

def test_the_result_of_a_second_seat_is_recorded_under_its_seat_name(tmp_dir, state_mod):
    """AC21: `read-result <pr> claude-2` の結果は `rounds[-1]["claude-2"]` に入る。"""
    _seed_state(tmp_dir)
    rfile = tmp_dir / "result.json"
    rfile.write_text(json.dumps({
        "event": "APPROVE", "posted_as": "APPROVE", "comments_count": 0,
        "review_url": "https://example/pr/1#1", "by_severity": {},
    }))

    state_mod.cmd_read_result(argparse.Namespace(pr=PR, agent=SEAT, file=str(rfile)))

    st = json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())
    assert st["rounds"][-1][SEAT]["intent"] == "APPROVE"


# ---------------- 起動スクリプト ----------------
#
# 起動スクリプトは席の名前を受け、CLI は `${SEAT%%-*}` で選ぶ（設計の決定 10）。
# **渡した先を差し替えて確かめる。** 実物の共通の起動スクリプトを呼ぶと CLI を起動する。
# 差し替えのために、起動スクリプトの隣に置いた符号のリンクから、相対で解決される
# 共通層の位置（`../../../scripts/lib`）へ記録を置く。

LAUNCH_SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
LIB = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "lib"


def _stub_tree(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """起動スクリプトの符号のリンクと、差し替えた共通の起動スクリプトを置く。

    返すのは `(起動スクリプトのパス, 渡された引数を書き出す記録のパス)`。
    """
    fake_scripts = tmp_path / "plugin" / "skills" / "cross-review" / "scripts"
    fake_scripts.mkdir(parents=True)
    for name in ("launch-reviewer.sh", "_tmpdir.sh"):
        (fake_scripts / name).symlink_to(LAUNCH_SCRIPTS / name)
    fake_lib = tmp_path / "plugin" / "scripts" / "lib"
    fake_lib.mkdir(parents=True)
    (fake_lib / "_tmpdir.sh").symlink_to(LIB / "_tmpdir.sh")
    record = tmp_path / "launch-args.txt"
    stub = fake_lib / "launch-cli.sh"
    stub.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "{record}"\n')
    stub.chmod(0o755)
    return fake_scripts / "launch-reviewer.sh", record


def _run_launch(script: pathlib.Path, seat: str, tmp_dir: pathlib.Path):
    import os
    import subprocess

    state = {
        "current_pr": PR, "repo": "o/r", "worktree_path": str(tmp_dir),
        "rounds": [{"round": 1, "head_sha": "a" * 40}],
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))
    return subprocess.run(
        ["bash", str(script), seat, str(PR), "1"],
        env={**os.environ, "CROSS_REVIEW_TMP_DIR": str(tmp_dir)},
        capture_output=True, text=True, timeout=30,
    )


def test_the_launcher_passes_the_runtime_of_the_seat_to_the_shared_launcher(tmp_path):
    """AC21: `claude-2` で起動すると、共通の起動スクリプトへ渡るのは `claude` である。"""
    script, record = _stub_tree(tmp_path)
    tmp_dir = tmp_path / "work"
    tmp_dir.mkdir()

    result = _run_launch(script, SEAT, tmp_dir)

    assert result.returncode == 0, result.stderr
    assert record.read_text().splitlines()[0] == "claude"


def test_the_launcher_builds_the_stem_from_the_seat_name(tmp_path):
    """AC21: 結果ファイルの stem は席の名前で組む（`claude-2-review-pr<N>`）。"""
    script, record = _stub_tree(tmp_path)
    tmp_dir = tmp_path / "work"
    tmp_dir.mkdir()

    result = _run_launch(script, SEAT, tmp_dir)

    assert result.returncode == 0, result.stderr
    assert record.read_text().splitlines()[3] == str(tmp_dir / f"{SEAT}-review-pr{PR}")
    assert (tmp_dir / f"{SEAT}-review-pr{PR}-prompt.md").is_file()


@pytest.mark.parametrize("script_name", ["launch-reviewer.sh", "critique.sh"])
def test_the_launch_scripts_reject_a_name_outside_the_seat_pattern(tmp_path, script_name):
    import os
    import subprocess

    result = subprocess.run(
        ["bash", str(LAUNCH_SCRIPTS / script_name), "bogus", "1", "1"],
        env={**os.environ, "CROSS_REVIEW_TMP_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=30,
    )

    assert result.returncode == 1
    assert "受け付けられない席の名前です" in result.stderr
    assert list(tmp_path.iterdir()) == []


# ---------------- 監視の位置引数 ----------------

def test_the_monitor_accepts_a_second_seat_as_its_target(monitor_mod):
    """AC21: 監視の位置引数は席の名前を受ける。"""
    assert monitor_mod._seat_or_both("kiro-2") == "kiro-2"
    assert monitor_mod._seat_or_both("both") == "both"
    for runtime in ("claude", "codex", "agy", "kiro"):
        assert monitor_mod._seat_or_both(runtime) == runtime


def _run_monitor(tmp_path: pathlib.Path, *argv: str):
    import os
    import subprocess

    monitor = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "monitor.py"
    return subprocess.run(
        [sys.executable, str(monitor), *argv],
        env={**os.environ, "CROSS_REVIEW_TMP_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=120,
    )


def test_the_monitor_takes_a_second_seat_as_its_positional_argument(tmp_path):
    """AC21: 位置引数に `kiro-2` を渡しても argparse は弾かない。"""
    # 起動待ちを使い切らないよう、終了済みの pid を先に置く。
    (tmp_path / "kiro-2-review-pr1.pid").write_text("2147483646\n", encoding="utf-8")

    result = _run_monitor(tmp_path, "1", "kiro-2", "--tmp-dir", str(tmp_path),
                          "--timeout", "1", "--poll", "1")

    assert result.returncode != 2, result.stderr
    assert "席の名前の形が違います" not in result.stderr


def test_the_monitor_rejects_a_name_outside_the_seat_pattern(tmp_path):
    result = _run_monitor(tmp_path, "1", "gemini")

    assert result.returncode == 2, result.stderr
    assert "席の名前の形が違います" in result.stderr


def test_the_runtime_of_a_seat_is_used_for_the_cli_specific_checks(monitor_mod):
    """席の形に合わない名前はそのまま返す（cross-refactoring の任意の骨格のため）。"""
    assert monitor_mod._agent_runtime("codex-2") == "codex"
    assert monitor_mod._agent_runtime("impl") == "impl"


# ---------------- 監視の上限と無進捗の許容 ----------------

@pytest.fixture()
def no_limit_env(monkeypatch):
    """上限の表を上書きする環境変数を外す。手元の設定でこの節が揺れないようにする。"""
    for name in ("MONITOR_TIMEOUT", "MONITOR_STALL"):
        monkeypatch.delenv(name, raising=False)
        for runtime in ("CLAUDE", "CODEX", "AGY", "KIRO"):
            monkeypatch.delenv(f"{name}_{runtime}", raising=False)


@pytest.mark.parametrize("seat,expected", [
    ("claude-2", 900), ("agy-2", 480), ("kiro-2", 480), ("codex-2", 180),
])
def test_a_second_seat_gets_the_allowance_of_its_runtime(
    monitor_mod, no_limit_env, seat, expected
):
    """2 席目の無進捗の許容は、そのランタイムの値になる。

    席の名前のまま上限の表を引くと表に無い担当として既定（180 秒）へ落ち、1 席目より
    早く無進捗と判定される。
    """
    assert monitor_mod._agent_stall_default(seat) == expected


def test_a_second_seat_reads_the_environment_variable_of_its_runtime(
    monkeypatch, monitor_mod, no_limit_env
):
    """担当別の環境変数もランタイム名で引く（`MONITOR_STALL_CLAUDE-2` は書けない）。"""
    monkeypatch.setenv("MONITOR_STALL_CLAUDE", "777")
    assert monitor_mod._agent_stall_default("claude-2") == 777


def test_both_seats_of_a_runtime_are_monitored_with_the_same_limits(
    monkeypatch, monitor_mod, no_limit_env
):
    """並列監視の入口（`_run_all`）でも、2 席目が 1 席目と同じ上限で監視される。"""
    seen: dict[str, object] = {}

    def fake_monitor_agent(agent, pr, config):
        seen[agent] = config
        return monitor_mod.AgentStatus(agent=agent)

    monkeypatch.setattr(monitor_mod, "monitor_agent", fake_monitor_agent)
    monkeypatch.setattr(monitor_mod, "_record_outcome", lambda *a, **k: None)

    args = argparse.Namespace(
        timeout=None, stall_timeout=None, poll=1, no_require_result=False,
        no_early_error=False, stem_template=monitor_mod.DEFAULT_STEM_TEMPLATE,
        pr=1, phase="review",
    )
    monitor_mod._run_all(["claude", "claude-2"], args, "review")

    assert seen["claude-2"].stall_timeout == seen["claude"].stall_timeout == 900
    assert seen["claude-2"].timeout == seen["claude"].timeout


# ---------------- 計測 ----------------

def test_the_measure_counts_a_second_seat(measure_mod):
    """AC21: 席の名前で残った結果も、そのラウンドの担当の数に入る。"""
    assert measure_mod._reviewer_count(
        {"round": 1, "pr": 1, "codex": {"intent": "APPROVE"}, SEAT: {"intent": "APPROVE"}}
    ) == 2


def test_the_measure_ignores_keys_outside_the_seat_pattern(measure_mod):
    assert measure_mod._reviewer_count(
        {"round": 1, "pr": 1, "ci": {"state": "SUCCESS"}, "claude-1": {"intent": "APPROVE"}}
    ) == 0
