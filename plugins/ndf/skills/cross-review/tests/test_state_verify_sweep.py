"""最終スイープの結果を検証する経路のテスト。

最終スイープはループの終了後に残った未解決の指摘を片づける手順（Step 7.5）である。
結果ファイルに書かれた残件数は申告であり、GitHub 側の実数とは別のものである。
申告のまま完了報告へ進むと、未解決の指摘が残ったまま「0 件」と報告される。

| 申告 | GitHub 側 | 記録する残件数 | 終了コード |
| --- | --- | --- | --- |
| 0 件 | 0 件 | 0 | 0 |
| 0 件 | 2 件 | 2 | 6 |
| 1 件 | 1 件 | 1 | 6（件数と理由を完了報告へ入れる） |
| 0 件 | 取得できない | 0（申告のまま） | 0（確認できなかったことを残す） |
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest

PR = 3131
REPO = "o/r"


def _state(**over) -> dict:
    state = {
        "current_pr": PR,
        "repo": REPO,
        "max_rounds": 12,
        "rotate_after": 8,
        "only": None,
        "pr_history": [{"pr": PR, "opened_at": "...", "closed_at": None, "rounds": 1}],
        "rounds": [{"round": 1, "pr": PR, "started_at": "2026-08-31T00:00:00+00:00"}],
        "deferred_nits": [],
        "final": "approved",
    }
    state.update(over)
    return state


def _write(tmp_dir: pathlib.Path, state: dict) -> None:
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def _read(tmp_dir: pathlib.Path) -> dict:
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())


def _sweep(tmp_dir: pathlib.Path, **over) -> None:
    payload = {
        "resolved": 5, "fixed_in_sweep": 2, "commit": "abc1234",
        "remaining_open": 0, "items": [],
    }
    payload.update(over)
    (tmp_dir / f"sweep-pr{PR}-result.json").write_text(json.dumps(payload))


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


def _args() -> argparse.Namespace:
    return argparse.Namespace(pr=PR, file=None)


# ---------------- 検証 ----------------

def test_zero_remaining_is_confirmed_against_github(tmp_dir, state_mod, unresolved, capsys):
    _write(tmp_dir, _state())
    _sweep(tmp_dir)
    unresolved([])

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_verify_sweep(_args())

    assert e.value.code == 0
    assert "REMAINING_OPEN=0" in capsys.readouterr().out
    sweep = _read(tmp_dir)["sweep"]
    assert sweep["remaining_open"] == 0
    assert sweep["verified"] is True


def test_a_declaration_of_zero_does_not_override_the_actual_count(tmp_dir, state_mod, unresolved, capsys):
    """申告が 0 件でも、GitHub 側に残っていれば残件数として記録する。"""
    _write(tmp_dir, _state())
    _sweep(tmp_dir, remaining_open=0)
    unresolved([{"id": "PRRT_a", "path": "src/foo.py", "line": "42"},
                {"id": "PRRT_b", "path": "docs/bar.md", "line": "7"}])

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_verify_sweep(_args())

    assert e.value.code == 6
    assert "REMAINING_OPEN=2" in capsys.readouterr().out
    sweep = _read(tmp_dir)["sweep"]
    assert sweep["declared_remaining_open"] == 0
    assert sweep["remaining_open"] == 2


def test_the_reason_is_kept_when_threads_remain(tmp_dir, state_mod, unresolved):
    _write(tmp_dir, _state())
    _sweep(tmp_dir, remaining_open=1, remaining_reason="外部の担当者による指摘のため保留")
    unresolved([{"id": "PRRT_a", "path": "src/foo.py", "line": "42"}])

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_verify_sweep(_args())

    assert e.value.code == 6
    assert _read(tmp_dir)["sweep"]["remaining_reason"] == "外部の担当者による指摘のため保留"


def test_a_missing_reason_is_recorded_as_absent(tmp_dir, state_mod, unresolved):
    _write(tmp_dir, _state())
    _sweep(tmp_dir, remaining_open=1)
    unresolved([{"id": "PRRT_a", "path": "src/foo.py", "line": "42"}])

    with pytest.raises(SystemExit):
        state_mod.cmd_verify_sweep(_args())

    assert _read(tmp_dir)["sweep"]["remaining_reason"] == "理由の記載なし"


def test_unavailable_count_falls_back_to_the_declaration(tmp_dir, state_mod, unresolved, capsys):
    """取得できないときは申告を採用し、確認できなかったことを残す。"""
    _write(tmp_dir, _state())
    _sweep(tmp_dir, remaining_open=0)
    unresolved(None)

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_verify_sweep(_args())

    assert e.value.code == 0
    assert _read(tmp_dir)["sweep"]["verified"] is False
    assert "確認できません" in capsys.readouterr().err


def test_a_missing_sweep_result_fails(tmp_dir, state_mod, unresolved):
    _write(tmp_dir, _state())
    unresolved([])

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_verify_sweep(_args())

    assert e.value.code == 1


# ---------------- 完了報告 ----------------

def test_report_includes_the_remaining_count_and_the_reason(tmp_dir, state_mod, capsys):
    _write(tmp_dir, _state(sweep={
        "declared_remaining_open": 1, "remaining_open": 1,
        "remaining_reason": "外部の担当者による指摘のため保留", "verified": True,
    }))

    state_mod.cmd_report(argparse.Namespace(pr=PR))

    out = capsys.readouterr().out
    assert "最終スイープ" in out
    assert "1 件" in out
    assert "外部の担当者による指摘のため保留" in out


def test_report_states_zero_when_nothing_remains(tmp_dir, state_mod, capsys):
    _write(tmp_dir, _state(sweep={
        "declared_remaining_open": 0, "remaining_open": 0,
        "remaining_reason": None, "verified": True,
    }))

    state_mod.cmd_report(argparse.Namespace(pr=PR))

    assert "未解決の指摘: 0 件" in capsys.readouterr().out


def test_report_marks_an_unverified_sweep(tmp_dir, state_mod, capsys):
    """検証を通っていない状態ファイルでも報告が出る。"""
    _write(tmp_dir, _state())

    state_mod.cmd_report(argparse.Namespace(pr=PR))

    assert "最終スイープ: 未検証" in capsys.readouterr().out
