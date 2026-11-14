"""組み立ての判断（assemble_decide）: 状態の置き場・実行の鍵・差し込み・作業ディレクトリ（RF5a・N2b・B1）。"""

from __future__ import annotations

import pytest

from ndf.core.plan.assemble_decide import choose_run, fill, run_key, state_dir_for, workdir_for


def test_state_dir_is_absolute_for_a_relative_plan_path():
    """プランを相対のパスで渡しても {state_dir} は絶対のパスで埋まる（#2949）。"""
    assert state_dir_for("plans/impl.json", None, "/repo") == "/repo/plans/impl.state"
    assert state_dir_for("plans/impl.json", "work/s", "/repo") == "/repo/work/s"
    assert state_dir_for("plans/impl.json", "/abs/s", "/repo") == "/abs/s"


def test_run_key_differs_for_each_part():
    """実行の回ごとに鍵を分ける（B1）。head が違えば直した head を流す（#3176）。"""
    base = run_key("p", "d", None, "h1")
    assert len({base, run_key("q", "d", None, "h1"), run_key("p", "e", None, "h1"), run_key("p", "d", "s", "h1"), run_key("p", "d", None, "h2"), run_key("p", "d", None, "h1", 1)}) == 6
    assert run_key("p", "d", None, "h1") == base


def _record(**kw):
    rec = {"key": "K", "name": "p", "digest": "d", "start": None, "round": 0, "heads": ["h1"]}
    rec.update(kw)
    return rec


def test_choose_run_keeps_the_key_while_the_round_is_running():
    """承認ゲートで待つ回は、head が動いても同じ鍵で続ける（B2）。"""
    assert choose_run(_record(), "p", "d", None, "h9", finished=False) == ("K", 0)


def test_choose_run_keeps_a_finished_key_for_the_same_head():
    """終わった回を同じ head で打ち直すと同じ鍵（retired/ の結果を返す。B4）。"""
    assert choose_run(_record(heads=["h1", "h2"]), "p", "d", None, "h2", finished=True) == ("K", 0)


def test_choose_run_starts_a_new_round_for_a_fixed_head():
    choice = choose_run(_record(), "p", "d", None, "h9", finished=True)
    assert choice == (run_key("p", "d", None, "h9", 1), 1)


def test_choose_run_starts_a_new_round_for_another_start():
    choice = choose_run(_record(), "p", "d", "pr", "h1", finished=False)
    assert choice.round_no == 1
    assert choice.key != "K"


def test_fill_replaces_known_names_only():
    assert fill(["x", "{state_dir}/a", "{other}"], {"state_dir": "/s"}) == ["x", "/s/a", "{other}"]


@pytest.mark.parametrize(
    ("place", "worktree", "repo", "usable", "expected"),
    [
        ("worktree", "/wt", "/repo", True, "/wt"),
        ("worktree", "/wt", "/repo", False, "/repo"),
        ("repo", "/wt", "/repo", True, "/repo"),
        ("worktree", None, "/repo", False, "/repo"),
        ("worktree", "/wt", None, False, None),
    ],
)
def test_workdir_falls_back_to_the_repo_root(place, worktree, repo, usable, expected):
    """マージで作業場所が消えた後のステップはリポジトリの根で打つ（#2969・#3022・#3186）。"""
    assert workdir_for(place, worktree, repo, usable) == expected
