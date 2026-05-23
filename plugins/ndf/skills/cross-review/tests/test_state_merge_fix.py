"""state.py cmd_merge_fix の堅牢化テスト (PLAN21).

カバー範囲:
  1. 正規パス + 正規 key (`fix_commit` / `fixed_count`)
  2. 正規パス不在 → `/tmp/` fallback で拾える
  3. 戻り値 key 別名 (`commit_sha` / `fixed`) も受理
  4. 全候補不在 → exit 3 で die + 探索 path 一覧が stderr に出る
  5. (PLAN21 round 2) fallback の stale 検証
     - mtime が round 開始前 → skip
     - JSON 内 `pr` 不一致 → skip
     - 明示 `--file` は stale 検証スキップ
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
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


# ---------------- PLAN21 round 2: fallback stale 検証 ----------------


def _seed_state_with_started_at(tmp_dir: pathlib.Path, started_at: str) -> None:
    """`started_at` を任意のタイムスタンプで seed する。"""
    state = {
        "current_pr": PR,
        "rounds": [{"round": 1, "pr": PR, "started_at": started_at}],
        "deferred_nits": [],
        "final": None,
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def test_fallback_stale_mtime_is_ignored(patched_tmp_dir, state_mod, capsys):
    """正規 fallback ファイルの mtime が round 開始前なら skip。

    別 round / 別実行の残骸ファイルを誤マージしないことを確認する。
    """
    tmp_dir = patched_tmp_dir
    # round 開始時刻 = 現在 (これより古いファイルは skip 対象)
    now_dt = _dt.datetime.now(_dt.timezone.utc).astimezone()
    _seed_state_with_started_at(tmp_dir, now_dt.isoformat(timespec="seconds"))

    stale = tmp_dir / f"fix-pr{PR}-result.json"
    stale.write_text(json.dumps(_canonical_fix()))
    # mtime を 1 時間前に巻き戻す (round 開始より明確に古い)
    stale_ts = now_dt.timestamp() - 3600
    os.utime(stale, (stale_ts, stale_ts))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_merge_fix(_make_args())
    assert e.value.code == 3

    captured = capsys.readouterr()
    # 古いファイルとして skip された旨が stderr に出る
    assert "round 開始前" in captured.err or "古いファイル" in captured.err


def test_fallback_tmp_stale_is_ignored(patched_tmp_dir, state_mod, capsys):
    """`/tmp/fix-prN-result.json` の古い残骸 (別リポジトリの同番号 PR 想定) は skip。

    codex review 指摘の本丸: PR 番号だけで命名された共有 namespace の
    古いファイルを無条件に拾わないこと。
    """
    tmp_dir = patched_tmp_dir
    now_dt = _dt.datetime.now(_dt.timezone.utc).astimezone()
    _seed_state_with_started_at(tmp_dir, now_dt.isoformat(timespec="seconds"))

    legacy = pathlib.Path(f"/tmp/fix-pr{PR}-result.json")
    legacy.write_text(json.dumps(_canonical_fix()))
    # mtime を 2 時間前に巻き戻す (= 過去の別実行で残った想定)
    stale_ts = now_dt.timestamp() - 7200
    os.utime(legacy, (stale_ts, stale_ts))
    try:
        with pytest.raises(SystemExit) as e:
            state_mod.cmd_merge_fix(_make_args())
        assert e.value.code == 3
        captured = capsys.readouterr()
        assert "round 開始前" in captured.err or "古いファイル" in captured.err
    finally:
        legacy.unlink(missing_ok=True)


def test_fallback_pr_mismatch_is_ignored(patched_tmp_dir, state_mod, capsys):
    """fallback ファイル内の `pr` が対象 PR と一致しない場合は skip。

    別リポジトリの同番号 PR の戻り値が `/tmp` 共有 namespace に
    残っていたケースを想定する。
    """
    tmp_dir = patched_tmp_dir
    # round 開始は十分過去にして mtime チェックでは弾かれないようにする
    past = _dt.datetime(2000, 1, 1, tzinfo=_dt.timezone.utc)
    _seed_state_with_started_at(tmp_dir, past.isoformat(timespec="seconds"))

    fix = _canonical_fix()
    fix["pr"] = PR + 1  # 別 PR の戻り値
    (tmp_dir / f"fix-pr{PR}-result.json").write_text(json.dumps(fix))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_merge_fix(_make_args())
    assert e.value.code == 3
    captured = capsys.readouterr()
    assert "pr 不一致" in captured.err or "別 PR" in captured.err


def test_fallback_fresh_mtime_and_matching_pr_is_accepted(patched_tmp_dir, state_mod):
    """mtime が round 開始後 & `pr` 一致なら採用される (regression guard)。"""
    tmp_dir = patched_tmp_dir
    # round 開始を 1 時間前に置く → 直後に書いたファイルは "fresh"
    past_dt = _dt.datetime.now(_dt.timezone.utc).astimezone() - _dt.timedelta(hours=1)
    _seed_state_with_started_at(tmp_dir, past_dt.isoformat(timespec="seconds"))

    fix = _canonical_fix()
    fix["fix_commit"] = "fresh_ok"
    (tmp_dir / f"fix-pr{PR}-result.json").write_text(json.dumps(fix))

    state_mod.cmd_merge_fix(_make_args())

    st = _read_state(tmp_dir)
    assert st["rounds"][-1]["fix"]["commit"] == "fresh_ok"


def test_explicit_file_bypasses_stale_check(patched_tmp_dir, state_mod, tmp_path):
    """`--file` 明示時は stale 検証をスキップする (ユーザー指定優先)。"""
    tmp_dir = patched_tmp_dir
    now_dt = _dt.datetime.now(_dt.timezone.utc).astimezone()
    _seed_state_with_started_at(tmp_dir, now_dt.isoformat(timespec="seconds"))

    explicit_fix = _canonical_fix()
    explicit_fix["fix_commit"] = "explicit_stale_ok"
    explicit_path = tmp_path / "explicit.json"
    explicit_path.write_text(json.dumps(explicit_fix))
    # mtime を round 開始前に巻き戻しても、--file 指定なら採用される
    stale_ts = now_dt.timestamp() - 3600
    os.utime(explicit_path, (stale_ts, stale_ts))

    state_mod.cmd_merge_fix(_make_args(explicit_path))

    st = _read_state(tmp_dir)
    assert st["rounds"][-1]["fix"]["commit"] == "explicit_stale_ok"
