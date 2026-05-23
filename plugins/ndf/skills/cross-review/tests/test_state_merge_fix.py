"""state.py cmd_merge_fix の堅牢化テスト (PLAN21).

カバー範囲:
  1. 正規パス + 正規 key (`fix_commit` / `fixed_count`)
  2. 正規パス不在 → `/tmp/` fallback で拾える
  3. 戻り値 key 別名 (`commit_sha` / `fixed`) も受理
  4. 全候補不在 → exit 3 で die + 探索 path 一覧が stderr に出る
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest


PR = 9919


def _seed_state(tmp_dir: pathlib.Path) -> None:
    state = {
        "current_pr": PR,
        "rounds": [
            {"round": 1, "pr": PR, "started_at": "2026-05-23T00:00:00+00:00"}
        ],
        "deferred_nits": [],
        "final": None,
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def _make_args(file_path: pathlib.Path | None = None) -> argparse.Namespace:
    return argparse.Namespace(pr=PR, file=str(file_path) if file_path else None)


def _read_state(tmp_dir: pathlib.Path) -> dict:
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())


def _canonical_fix() -> dict:
    return {
        "pr": PR,
        "fix_commit": "abc1234",
        "ci_status": "SUCCESS",
        "ci_failed_checks": [],
        "fixed_count": 5,
        "by_severity": {"critical": 0, "major": 3, "minor": 2, "nit": 0},
        "resolved_threads": [{"thread_id": "T1"}],
        "deferred": [],
        "rejected": [],
    }


@pytest.fixture()
def patched_tmp_dir(monkeypatch, tmp_path, state_mod):
    """`CROSS_REVIEW_TMP_DIR` を tmp_path に向けて `_tmp_dir()` を決定的にする。"""
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


def test_canonical_path_and_key(patched_tmp_dir, state_mod):
    """正規パス ($TMP_DIR/fix-prN-result.json) + 正規 key で state にマージされる。"""
    tmp_dir = patched_tmp_dir
    _seed_state(tmp_dir)
    fix = _canonical_fix()
    (tmp_dir / f"fix-pr{PR}-result.json").write_text(json.dumps(fix))

    state_mod.cmd_merge_fix(_make_args())

    st = _read_state(tmp_dir)
    merged = st["rounds"][-1]["fix"]
    assert merged["commit"] == "abc1234"
    assert merged["fixed"] == 5
    assert merged["ci"] == "SUCCESS"
    assert merged["by_severity"]["major"] == 3


def test_tmp_fallback_path(patched_tmp_dir, state_mod, tmp_path):
    """正規パス不在で /tmp/fix-prN-result.json にある場合は fallback で拾う。

    旧プロンプトでサブエージェントが `/tmp/` を指定したケースの救済。
    """
    tmp_dir = patched_tmp_dir
    _seed_state(tmp_dir)
    # 正規パスには書かず、/tmp/ のみに置く
    fix = _canonical_fix()
    fix["fix_commit"] = "tmp_path_commit"
    legacy = pathlib.Path(f"/tmp/fix-pr{PR}-result.json")
    legacy.write_text(json.dumps(fix))
    try:
        state_mod.cmd_merge_fix(_make_args())
        st = _read_state(tmp_dir)
        assert st["rounds"][-1]["fix"]["commit"] == "tmp_path_commit"
    finally:
        legacy.unlink(missing_ok=True)


def test_key_alias_commit_sha_and_fixed(patched_tmp_dir, state_mod):
    """サブエージェントが別名 (`commit_sha` / `fixed`) で書いても受理する。"""
    tmp_dir = patched_tmp_dir
    _seed_state(tmp_dir)
    fix = {
        "pr": PR,
        "commit_sha": "alias_sha",
        "fixed": 3,
        "ci_status": "SUCCESS",
        "deferred": [],
        "rejected": [],
        "resolved_threads": [],
    }
    (tmp_dir / f"fix-pr{PR}-result.json").write_text(json.dumps(fix))

    state_mod.cmd_merge_fix(_make_args())

    st = _read_state(tmp_dir)
    merged = st["rounds"][-1]["fix"]
    assert merged["commit"] == "alias_sha"
    assert merged["fixed"] == 3


def test_missing_all_candidates_dies_with_paths(patched_tmp_dir, state_mod, capsys):
    """どの候補にもファイルが無い場合は exit 3 + 探索 path 一覧が stderr に出る。"""
    tmp_dir = patched_tmp_dir
    _seed_state(tmp_dir)
    # /tmp/ にも置かない (clean up to be safe)
    legacy = pathlib.Path(f"/tmp/fix-pr{PR}-result.json")
    legacy.unlink(missing_ok=True)

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_merge_fix(_make_args())
    assert e.value.code == 3

    captured = capsys.readouterr()
    # 探索 path 一覧 (どこを見たか) がメッセージに含まれる
    assert str(tmp_dir / f"fix-pr{PR}-result.json") in captured.err
    assert f"/tmp/fix-pr{PR}-result.json" in captured.err


def test_explicit_file_arg_wins(patched_tmp_dir, state_mod, tmp_path):
    """`--file` 明示時はそれを最優先で読む (正規パスや /tmp/ より優先)。"""
    tmp_dir = patched_tmp_dir
    _seed_state(tmp_dir)
    # 正規パスにも置くが、--file で別ファイルを指定する
    canonical = _canonical_fix()
    canonical["fix_commit"] = "canonical_should_not_be_used"
    (tmp_dir / f"fix-pr{PR}-result.json").write_text(json.dumps(canonical))

    explicit_fix = _canonical_fix()
    explicit_fix["fix_commit"] = "explicit_wins"
    explicit_path = tmp_path / "custom-fix.json"
    explicit_path.write_text(json.dumps(explicit_fix))

    state_mod.cmd_merge_fix(_make_args(explicit_path))

    st = _read_state(tmp_dir)
    assert st["rounds"][-1]["fix"]["commit"] == "explicit_wins"
