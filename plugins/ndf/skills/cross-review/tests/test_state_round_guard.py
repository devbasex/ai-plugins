"""修正の記録が無いまま次のラウンドを開始させない検査のテスト。

進行側が手で修正して次のラウンドへ進めると、修正の工程（Step 5）が担う返信と
Resolve が飛ばされる。飛ばされたまま進むと、未解決の指摘が残ったまま承認まで到達する。

次の 2 つを、ラウンドの開始時に止める。

| 状態 | 扱い |
| --- | --- |
| 前のラウンドが修正必須の判定で、修正の記録が無い | 終了コード 5 で止める |
| 前のラウンドで Resolve したと申告されたスレッドが、GitHub 側で未解決のまま | 終了コード 5 で止める |
| 未解決の指摘を取得できない | 検査を行わず、確認できなかったことを残して進む |
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest

PR = 5150
REPO = "o/r"


def _round(no: int, **over) -> dict:
    entry = {
        "round": no,
        "pr": PR,
        "started_at": "2026-08-31T00:00:00+00:00",
        "codex": {"intent": "REQUEST_CHANGES", "by_severity": {"major": 2}},
        "gemini": {"intent": "APPROVE", "by_severity": {}},
        "verdict": "changes_requested",
    }
    entry.update(over)
    return entry


def _state(rounds: list[dict], **over) -> dict:
    state = {
        "current_pr": PR,
        "repo": REPO,
        "max_rounds": 12,
        "rotate_after": 8,
        "only": None,
        "rounds": rounds,
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
    def _set(threads):
        monkeypatch.setattr(
            state_mod, "_fetch_unresolved_threads",
            lambda repo, pr: threads,
        )
    return _set


# ---------------- 修正の記録が無い ----------------

def test_missing_fix_record_after_a_change_request_fails(tmp_dir, state_mod, unresolved, capsys):
    unresolved([])
    _write(tmp_dir, _state([_round(1)]))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert e.value.code == 5
    assert "修正の記録" in capsys.readouterr().err
    # ラウンドは開かれていない
    assert len(_read(tmp_dir)["rounds"]) == 1


def test_verdict_is_recomputed_when_it_is_absent(tmp_dir, state_mod, unresolved):
    """判定の結果が残っていない状態ファイルでも、保存された重要度から判定し直す。"""
    unresolved([])
    prev = _round(1)
    del prev["verdict"]
    _write(tmp_dir, _state([prev]))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert e.value.code == 5


def test_an_approved_previous_round_needs_no_fix_record(tmp_dir, state_mod, unresolved):
    """承認で終えたラウンドの後は、修正の記録が無くても進む。"""
    unresolved([])
    prev = _round(
        1,
        codex={"intent": "APPROVE", "by_severity": {}},
        verdict="approved",
    )
    _write(tmp_dir, _state([prev]))

    state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert len(_read(tmp_dir)["rounds"]) == 2


def test_the_first_round_is_not_checked(tmp_dir, state_mod, monkeypatch):
    """前のラウンドが無ければ検査しない。GitHub も見に行かない。"""
    monkeypatch.setattr(
        state_mod, "_fetch_unresolved_threads",
        lambda repo, pr: pytest.fail("前のラウンドが無いのに GitHub を呼んでいる"),
    )
    _write(tmp_dir, _state([]))

    state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert len(_read(tmp_dir)["rounds"]) == 1


# ---------------- 申告どおり Resolve されていない ----------------

def test_a_thread_claimed_resolved_but_still_open_fails(tmp_dir, state_mod, unresolved, capsys):
    unresolved([{"id": "PRRT_a", "path": "src/foo.py", "line": "42"}])
    prev = _round(1, fix={"commit": "abc1234", "resolved_thread_ids": ["PRRT_a"]})
    _write(tmp_dir, _state([prev]))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert e.value.code == 5
    err = capsys.readouterr().err
    assert "PRRT_a" in err
    assert len(_read(tmp_dir)["rounds"]) == 1


def test_resolved_claims_that_hold_let_the_round_start(tmp_dir, state_mod, unresolved):
    unresolved([{"id": "PRRT_other", "path": "docs/bar.md", "line": "7"}])
    prev = _round(1, fix={"commit": "abc1234", "resolved_thread_ids": ["PRRT_a"]})
    _write(tmp_dir, _state([prev]))

    state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert len(_read(tmp_dir)["rounds"]) == 2


def test_no_claimed_identifier_skips_the_check(tmp_dir, state_mod, monkeypatch):
    """識別子の申告が無ければ突き合わせる相手がいない。GitHub を見に行かない。"""
    monkeypatch.setattr(
        state_mod, "_fetch_unresolved_threads",
        lambda repo, pr: pytest.fail("識別子が無いのに GitHub を呼んでいる"),
    )
    prev = _round(1, fix={"commit": "abc1234", "resolved_thread_ids": []})
    _write(tmp_dir, _state([prev]))

    state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert len(_read(tmp_dir)["rounds"]) == 2


def test_unavailable_count_does_not_stop_the_round(tmp_dir, state_mod, unresolved, capsys):
    """取得できないときはループを止めず、確認できなかったことを残す。"""
    unresolved(None)
    prev = _round(1, fix={"commit": "abc1234", "resolved_thread_ids": ["PRRT_a"]})
    _write(tmp_dir, _state([prev]))

    state_mod.cmd_start_round(argparse.Namespace(pr=PR))

    assert len(_read(tmp_dir)["rounds"]) == 2
    assert "確認できません" in capsys.readouterr().err
