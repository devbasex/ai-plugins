"""引き継いだ指摘を収束判定へ入れるテスト。

引き継いだ指摘は、レビューを再開した時点で Pull Request に残っていた未解決の指摘を指す。
中断の前に受けた修正必須の指摘が、修正の工程を 1 度も通らないまま収束する経路を塞ぐ。

判定へ入れる対象は 2 つに分かれる。

| 対象 | 判定への入れ方 |
| --- | --- |
| そのラウンドで新しく投稿された指摘 | 外部の AI が返した判定と重要度で見る（変更なし） |
| 引き継いだ指摘 | 修正の工程を 1 度通すまで収束させない |

すべてのラウンドで未解決の指摘が 0 件になるまで収束させる形にはしない。承認された
ラウンドに軽微な指摘が乗るのは通常の経路であり、そこを条件にするとラウンドが増え続ける。
収束を止めるのは修正の工程を 1 度通すまでで、増えるラウンドは最大 1 回に収まる。
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest

PR = 7788
REPO = "o/r"


def _threads(*ids: str) -> list[dict]:
    return [{"id": i, "path": "src/foo.py", "line": "42"} for i in ids]


def _state(**over) -> dict:
    state = {
        "current_pr": PR,
        "repo": REPO,
        "max_rounds": 12,
        "rotate_after": 8,
        "only": None,
        "rounds": [{
            "round": 1,
            "pr": PR,
            "started_at": "2026-08-31T00:00:00+00:00",
            "codex": {"intent": "APPROVE", "by_severity": {}},
            "agy": {"intent": "APPROVE", "by_severity": {}},
        }],
        "deferred_nits": [],
        "final": None,
    }
    state.update(over)
    return state


def _write(tmp_dir: pathlib.Path, state: dict) -> None:
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def _read(tmp_dir: pathlib.Path) -> dict:
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def unresolved(monkeypatch, state_mod):
    """GitHub 側の未解決の指摘を差し替える。`None` は取得できなかったことを表す。"""
    def _set(threads):
        monkeypatch.setattr(
            state_mod, "_fetch_unresolved_threads",
            lambda repo, pr: threads,
        )
    return _set


# ---------------- 再開の時点で記録する ----------------

def test_resume_records_the_carried_over_threads(state_mod, unresolved):
    unresolved(_threads("PRRT_a", "PRRT_b"))
    st = _state()

    state_mod._record_carried_over(st, REPO, PR)

    assert st["carried_over"]["count"] == 2
    assert st["carried_over"]["thread_ids"] == ["PRRT_a", "PRRT_b"]
    assert st["carried_over"]["fixed_in_round"] is None


def test_resume_without_unresolved_threads_records_nothing(state_mod, unresolved):
    """未解決の指摘が 0 件なら引き継ぎは無い。収束の振る舞いは現行のまま。"""
    unresolved([])
    st = _state(carried_over={"count": 3, "thread_ids": ["PRRT_x"], "fixed_in_round": None})

    state_mod._record_carried_over(st, REPO, PR)

    assert st.get("carried_over") is None


def test_unavailable_count_keeps_the_previous_record(state_mod, unresolved):
    """取得できないときは記録を書き換えない。0 件として扱わない。"""
    unresolved(None)
    kept = {"count": 3, "thread_ids": ["PRRT_x"], "fixed_in_round": None}
    st = _state(carried_over=dict(kept))

    state_mod._record_carried_over(st, REPO, PR)

    assert st["carried_over"]["count"] == 3
    assert st["carried_over"]["thread_ids"] == ["PRRT_x"]


def test_init_resume_reports_the_carried_over_count(tmp_dir, state_mod, unresolved, monkeypatch, capsys):
    """再開の出力に引き継いだ指摘の件数が出て、状態ファイルへ残る。"""
    unresolved(_threads("PRRT_a", "PRRT_b"))
    monkeypatch.setattr(state_mod, "_sh", lambda cmd, check=True: REPO)
    _write(tmp_dir, _state(
        auto_review_instructions="",
        review_instructions="",
        worktree_path=str(tmp_dir),
    ))
    args = argparse.Namespace(
        pr=PR, max_rounds=12, rotate_after=8, only=None,
        worktree=str(tmp_dir), focus=None, extra_instructions_file=None,
    )

    state_mod.cmd_init(args)

    assert "CARRIED_OVER_THREADS=2" in capsys.readouterr().out
    assert _read(tmp_dir)["carried_over"]["count"] == 2


# ---------------- 収束の判定 ----------------

def test_approval_does_not_converge_while_carried_over_threads_wait(tmp_dir, state_mod, capsys):
    """両者が承認しても、引き継いだ指摘が修正の工程を通るまで収束させない。"""
    _write(tmp_dir, _state(carried_over={
        "count": 2, "thread_ids": ["PRRT_a", "PRRT_b"], "fixed_in_round": None,
    }))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 2
    assert "CARRIED_OVER_THREADS=2" in capsys.readouterr().out
    assert _read(tmp_dir)["final"] is None


def test_approval_converges_after_the_fix_step_ran_once(tmp_dir, state_mod):
    """修正の工程を 1 度通した後は、承認が揃ったラウンドで収束する。"""
    _write(tmp_dir, _state(carried_over={
        "count": 2, "thread_ids": ["PRRT_a", "PRRT_b"], "fixed_in_round": 1,
    }))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 0
    assert _read(tmp_dir)["final"] == "approved"


def test_without_carried_over_threads_the_verdict_is_unchanged(tmp_dir, state_mod):
    """引き継いだ指摘が無いときの収束は現行と変わらない。"""
    _write(tmp_dir, _state())

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 0
    assert _read(tmp_dir)["final"] == "approved"


def test_judge_records_the_verdict_on_the_round(tmp_dir, state_mod):
    """次のラウンドの検査が読めるよう、判定の結果をラウンドへ残す。"""
    _write(tmp_dir, _state(carried_over={
        "count": 1, "thread_ids": ["PRRT_a"], "fixed_in_round": None,
    }))

    with pytest.raises(SystemExit):
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert _read(tmp_dir)["rounds"][-1]["verdict"] == "changes_requested"


# ---------------- 修正の工程を通した記録 ----------------

def test_merge_fix_marks_the_round_that_handled_the_carried_over(tmp_dir, state_mod):
    _write(tmp_dir, _state(carried_over={
        "count": 2, "thread_ids": ["PRRT_a", "PRRT_b"], "fixed_in_round": None,
    }))
    (tmp_dir / f"fix-pr{PR}-result.json").write_text(json.dumps({
        "pr": PR, "fix_commit": "abc1234", "ci_status": "SUCCESS",
        "fixed_count": 2, "resolved_threads": [{"thread_id": "PRRT_a"}],
        "deferred": [], "rejected": [],
    }))

    state_mod.cmd_merge_fix(argparse.Namespace(pr=PR, file=None))

    st = _read(tmp_dir)
    assert st["carried_over"]["fixed_in_round"] == 1
    assert st["rounds"][-1]["fix"]["resolved_thread_ids"] == ["PRRT_a"]


def test_merge_fix_keeps_the_first_round_that_handled_it(tmp_dir, state_mod):
    """既に記録があるラウンド番号は書き換えない。"""
    _write(tmp_dir, _state(carried_over={
        "count": 2, "thread_ids": ["PRRT_a"], "fixed_in_round": 1,
    }))
    (tmp_dir / f"fix-pr{PR}-result.json").write_text(json.dumps({
        "pr": PR, "fix_commit": "def5678", "ci_status": "SUCCESS",
        "fixed_count": 1, "resolved_threads": [], "deferred": [], "rejected": [],
    }))

    state_mod.cmd_merge_fix(argparse.Namespace(pr=PR, file=None))

    assert _read(tmp_dir)["carried_over"]["fixed_in_round"] == 1


# ---------------- 通した後の再開 ----------------

def test_resume_after_the_fix_step_keeps_the_record(state_mod, unresolved):
    """通した後に残る指摘だけなら、通したラウンドの記録を残す。

    deferred / rejected と最終スイープ待ちの指摘は Resolve されないまま残る。
    再開のたびに未処理として数え直すと、収束が再開のたびに 1 ラウンド先送りされる。
    """
    unresolved(_threads("PRRT_a", "PRRT_b"))
    st = _state(carried_over={
        "count": 2, "thread_ids": ["PRRT_a", "PRRT_b"], "fixed_in_round": 1,
    })

    changed = state_mod._record_carried_over(st, REPO, PR)

    assert changed is False
    assert st["carried_over"]["fixed_in_round"] == 1
    assert state_mod._carried_over_pending(st) is None


def test_resume_after_the_fix_step_with_a_new_thread_forces_one_more_round(state_mod, unresolved):
    """通した後に新しい指摘が出たときは、それを含めてもう 1 度通す。"""
    unresolved(_threads("PRRT_a", "PRRT_new"))
    st = _state(carried_over={
        "count": 1, "thread_ids": ["PRRT_a"], "fixed_in_round": 1,
    })

    changed = state_mod._record_carried_over(st, REPO, PR)

    assert changed is True
    assert st["carried_over"]["thread_ids"] == ["PRRT_a", "PRRT_new"]
    assert st["carried_over"]["fixed_in_round"] is None
    assert state_mod._carried_over_pending(st) is not None


def test_resume_before_the_fix_step_still_counts_again(state_mod, unresolved):
    """まだ通していないあいだは、これまでどおり数え直して収束を抑止する。"""
    unresolved(_threads("PRRT_a"))
    st = _state(carried_over={
        "count": 1, "thread_ids": ["PRRT_a"], "fixed_in_round": None,
    })

    changed = state_mod._record_carried_over(st, REPO, PR)

    assert changed is True
    assert st["carried_over"]["fixed_in_round"] is None
