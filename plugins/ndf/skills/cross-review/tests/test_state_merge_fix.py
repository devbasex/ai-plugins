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
  6. (PLAN21 round 3) 正規パス JSON parse 失敗 → 即時 die(code=3)
  7. (PLAN21 round 3) `pr` フィールドが数値として解釈できない → skip
  8. (PLAN21 round 3) `_is_fresh_fix_result` 戻り値のタプル化追従
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


# ---------------- PLAN21 round 3: 正規パス parse 失敗 / pr 型不正 / 戻り値タプル化 ----------------


def test_canonical_path_json_parse_failure_dies_immediately(
    patched_tmp_dir, state_mod, capsys
):
    """正規パス ($TMP_DIR/fix-prN-result.json) の JSON parse 失敗は即時 die(code=3)。

    codex round 2 指摘: 壊れた正規パスをスキップして /tmp/ fallback に流れると
    別 PR の戻り値を誤マージする事故が起こるため、正規パスの parse 失敗は致命扱い。
    """
    tmp_dir = patched_tmp_dir
    # round 開始を十分過去にして mtime チェックでは弾かれないようにする
    past = _dt.datetime(2000, 1, 1, tzinfo=_dt.timezone.utc)
    _seed_state_with_started_at(tmp_dir, past.isoformat(timespec="seconds"))

    # 正規パスに壊れた JSON を置く (size>0 だが parse 不能)
    canonical = tmp_dir / f"fix-pr{PR}-result.json"
    canonical.write_text("{ this is not valid json")

    # /tmp/ 側には別 PR を装う偽の正規 JSON を置く (これに流れ込んだら事故)
    legacy = pathlib.Path(f"/tmp/fix-pr{PR}-result.json")
    legacy.write_text(json.dumps(_canonical_fix()))
    try:
        with pytest.raises(SystemExit) as e:
            state_mod.cmd_merge_fix(_make_args())
        assert e.value.code == 3
        captured = capsys.readouterr()
        # 正規パスの読み取り失敗である旨が stderr に出る
        assert "正規パス" in captured.err
        # /tmp 側の fix がマージされていないこと (= state.rounds[-1].fix が無い)
        st = _read_state(tmp_dir)
        assert "fix" not in st["rounds"][-1]
    finally:
        legacy.unlink(missing_ok=True)


def test_fallback_pr_field_non_numeric_is_skipped(patched_tmp_dir, state_mod, capsys):
    """fallback ファイルの `pr` フィールドが int 変換不能なら skip (ValueError 防止)。

    gemini round 2 指摘: `int(file_pr)` で ValueError が裸で上がるとプロセスごと落ちる。
    """
    tmp_dir = patched_tmp_dir
    past = _dt.datetime(2000, 1, 1, tzinfo=_dt.timezone.utc)
    _seed_state_with_started_at(tmp_dir, past.isoformat(timespec="seconds"))

    # 正規パスに `pr` が int 化できない値の JSON を置く
    canonical = tmp_dir / f"fix-pr{PR}-result.json"
    bad = _canonical_fix()
    bad["pr"] = "not-a-number"
    canonical.write_text(json.dumps(bad))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_merge_fix(_make_args())
    # /tmp/ 側にも候補が無いので最終的には "戻り値ファイル無し" で die(3)
    assert e.value.code == 3
    captured = capsys.readouterr()
    # 数値として解釈できない旨が stderr に出る
    assert "数値として解釈できない" in captured.err
    # state にマージされていないこと
    st = _read_state(tmp_dir)
    assert "fix" not in st["rounds"][-1]


def test_is_fresh_fix_result_returns_tuple_with_parsed_payload(
    patched_tmp_dir, state_mod
):
    """`_is_fresh_fix_result` は (is_fresh, parsed_payload) を返す。

    gemini round 2 指摘の性能改善: cmd_merge_fix 側で再パースしないよう、
    fresh な場合は parse 済み dict を返す。
    """
    tmp_dir = patched_tmp_dir
    past_dt = _dt.datetime.now(_dt.timezone.utc).astimezone() - _dt.timedelta(hours=1)
    past_ts = past_dt.timestamp()

    fix = _canonical_fix()
    fix["fix_commit"] = "tuple_return_ok"
    p = tmp_dir / f"fix-pr{PR}-result.json"
    p.write_text(json.dumps(fix))

    is_fresh, parsed = state_mod._is_fresh_fix_result(p, PR, past_ts, is_canonical=True)
    assert is_fresh is True
    assert isinstance(parsed, dict)
    assert parsed["fix_commit"] == "tuple_return_ok"


def test_is_fresh_fix_result_returns_none_when_stale(patched_tmp_dir, state_mod):
    """stale な場合は (False, None) を返す。"""
    tmp_dir = patched_tmp_dir
    now_dt = _dt.datetime.now(_dt.timezone.utc).astimezone()

    p = tmp_dir / f"fix-pr{PR}-result.json"
    p.write_text(json.dumps(_canonical_fix()))
    stale_ts = now_dt.timestamp() - 3600
    os.utime(p, (stale_ts, stale_ts))

    is_fresh, parsed = state_mod._is_fresh_fix_result(
        p, PR, now_dt.timestamp(), is_canonical=False
    )
    assert is_fresh is False
    assert parsed is None


# ---------------- PLAN21 round 4: non-dict JSON 防御 ----------------


def test_is_fresh_fix_result_non_dict_json_is_skipped(patched_tmp_dir, state_mod, capsys):
    """`_is_fresh_fix_result` は dict 以外 (list 等) の JSON を読んだら (False, None) を返す。

    gemini round 3 指摘: `json.loads` は list / int / str も返しうるため、
    `payload.get(...)` 呼び出し前に `isinstance(payload, dict)` で防御する必要がある。
    """
    tmp_dir = patched_tmp_dir
    # round 開始は十分過去にして mtime チェックを通す
    past_dt = _dt.datetime.now(_dt.timezone.utc).astimezone() - _dt.timedelta(hours=1)
    past_ts = past_dt.timestamp()

    p = tmp_dir / f"fix-pr{PR}-result.json"
    # JSON として valid だが dict ではない (list)
    p.write_text(json.dumps([{"fix_commit": "should_be_ignored"}]))

    is_fresh, parsed = state_mod._is_fresh_fix_result(
        p, PR, past_ts, is_canonical=False
    )
    assert is_fresh is False
    assert parsed is None
    captured = capsys.readouterr()
    # dict ではない旨が stderr に出る
    assert "dict ではない" in captured.err


def test_is_fresh_fix_result_non_dict_json_canonical_dies_round5(
    patched_tmp_dir, state_mod, capsys
):
    """PLAN21 round 5 で挙動変更: 正規パス (is_canonical=True) で非 dict なら die(code=3)。

    旧 round 4 では skip (False, None) だったが、codex round 4 指摘により
    `/tmp/` fallback への流れ込みを防ぐため parse 失敗と同様に即時中断する。
    """
    tmp_dir = patched_tmp_dir
    past_dt = _dt.datetime.now(_dt.timezone.utc).astimezone() - _dt.timedelta(hours=1)
    past_ts = past_dt.timestamp()

    p = tmp_dir / f"fix-pr{PR}-result.json"
    p.write_text(json.dumps(["not", "a", "dict"]))

    with pytest.raises(SystemExit) as e:
        state_mod._is_fresh_fix_result(p, PR, past_ts, is_canonical=True)
    assert e.value.code == 3
    captured = capsys.readouterr()
    assert "dict ではない" in captured.err
    assert "正規パス" in captured.err


def test_explicit_file_non_dict_json_dies(patched_tmp_dir, state_mod, capsys, tmp_path):
    """`--file` 明示時に non-dict JSON が渡されたら die(code=3) で即時中断。

    gemini round 3 指摘: list 等が渡されると後続 `fix.get(...)` でクラッシュするため、
    明示指定の場合も dict 検証を行ってから fix に代入する。
    """
    tmp_dir = patched_tmp_dir
    _seed_state(tmp_dir)

    explicit_path = tmp_path / "bad-explicit.json"
    # JSON として valid だが dict ではない (list)
    explicit_path.write_text(json.dumps([_canonical_fix()]))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_merge_fix(_make_args(explicit_path))
    assert e.value.code == 3
    captured = capsys.readouterr()
    assert "dict ではない" in captured.err
    # state は更新されていない
    st = _read_state(tmp_dir)
    assert "fix" not in st["rounds"][-1]


# ---------------- PLAN21 round 5: 正規パス non-dict 即時 die / --file 厳格化 ----------------


def test_canonical_path_non_dict_json_dies_immediately(
    patched_tmp_dir, state_mod, capsys
):
    """正規パス ($TMP_DIR/fix-prN-result.json) の JSON が dict 以外なら即時 die(code=3)。

    codex round 4 指摘: parse 失敗と同様、非 dict も「壊れた正規出力」として扱う。
    skip で /tmp/ fallback に流れると別実行の戻り値を誤マージする経路が残るため、
    list / str / int 等が返ったら fallback には進まず即時中断する。
    """
    tmp_dir = patched_tmp_dir
    past = _dt.datetime(2000, 1, 1, tzinfo=_dt.timezone.utc)
    _seed_state_with_started_at(tmp_dir, past.isoformat(timespec="seconds"))

    # 正規パスに list (非 dict) を置く
    canonical = tmp_dir / f"fix-pr{PR}-result.json"
    canonical.write_text(json.dumps([{"fix_commit": "should_not_be_read"}]))

    # /tmp/ 側には別 PR を装う正規 JSON を置く (これに流れ込んだら事故)
    legacy = pathlib.Path(f"/tmp/fix-pr{PR}-result.json")
    legacy.write_text(json.dumps(_canonical_fix()))
    try:
        with pytest.raises(SystemExit) as e:
            state_mod.cmd_merge_fix(_make_args())
        assert e.value.code == 3
        captured = capsys.readouterr()
        # 正規パスで非 dict であった旨が stderr に出る
        assert "正規パス" in captured.err
        assert "dict ではない" in captured.err
        # /tmp 側の fix がマージされていないこと
        st = _read_state(tmp_dir)
        assert "fix" not in st["rounds"][-1]
    finally:
        legacy.unlink(missing_ok=True)


def test_explicit_file_nonexistent_dies(patched_tmp_dir, state_mod, capsys, tmp_path):
    """`--file` 指定パスが存在しなければ fallback に進まず die(code=3)。

    codex round 4 指摘: ユーザー明示指定なのに無言で fallback に流れて
    別実行の戻り値を誤マージするのを防ぐ。
    """
    tmp_dir = patched_tmp_dir
    _seed_state(tmp_dir)
    # 正規パスにも置いて、fallback に流れたら事故と判別できるようにする
    (tmp_dir / f"fix-pr{PR}-result.json").write_text(json.dumps(_canonical_fix()))

    nonexistent = tmp_path / "does-not-exist.json"
    assert not nonexistent.exists()

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_merge_fix(_make_args(nonexistent))
    assert e.value.code == 3
    captured = capsys.readouterr()
    assert "存在しません" in captured.err
    # fallback が読まれていない (state にマージされていない)
    st = _read_state(tmp_dir)
    assert "fix" not in st["rounds"][-1]


def test_explicit_file_empty_dies(patched_tmp_dir, state_mod, capsys, tmp_path):
    """`--file` 指定パスが空ファイルなら fallback に進まず die(code=3)。"""
    tmp_dir = patched_tmp_dir
    _seed_state(tmp_dir)
    (tmp_dir / f"fix-pr{PR}-result.json").write_text(json.dumps(_canonical_fix()))

    empty = tmp_path / "empty.json"
    empty.write_text("")

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_merge_fix(_make_args(empty))
    assert e.value.code == 3
    captured = capsys.readouterr()
    assert "空です" in captured.err
    st = _read_state(tmp_dir)
    assert "fix" not in st["rounds"][-1]


def test_explicit_file_invalid_json_dies(patched_tmp_dir, state_mod, capsys, tmp_path):
    """`--file` 指定パスが JSON 不正なら fallback に進まず die(code=3)。"""
    tmp_dir = patched_tmp_dir
    _seed_state(tmp_dir)
    (tmp_dir / f"fix-pr{PR}-result.json").write_text(json.dumps(_canonical_fix()))

    bad = tmp_path / "bad.json"
    bad.write_text("{ this is not valid json")

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_merge_fix(_make_args(bad))
    assert e.value.code == 3
    captured = capsys.readouterr()
    assert "parse に失敗" in captured.err or "読み取り" in captured.err
    st = _read_state(tmp_dir)
    assert "fix" not in st["rounds"][-1]


# ---------------- PLAN25: _count() 正規化 / int 混入時の堅牢化 ----------------


@pytest.mark.parametrize(
    "value, expected",
    [
        (3, 3),  # int(件数) はそのまま件数として扱う
        (0, 0),  # int の 0
        ([{"a": 1}, {"b": 2}], 2),  # list は len()
        ([], 0),  # 空 list
        ((1, 2, 3), 3),  # tuple も len()
        (None, 0),  # None は 0
        (False, 0),  # bool は int サブクラスだが件数扱いしない
        (True, 0),  # bool=True も 0
        ("xyz", 0),  # 想定外の型 (str) は 0
    ],
)
def test_count_normalizes_int_list_tuple_none_bool(state_mod, value, expected):
    """`_count()` が int / list / tuple / None / bool / 想定外型を件数(int)に正規化する。

    codex review 指摘 (PLAN25): `_count()` の挙動がテストで固定されておらず、
    同じ TypeError 経路 (`len(int)`) が再発しても検知できない。
    """
    assert state_mod._count(value) == expected


def test_merge_fix_int_counts_do_not_crash_and_persist(patched_tmp_dir, state_mod):
    """`resolved_threads` / `deferred` / `rejected` が int でも TypeError を出さず件数(int)が保存される。

    PLAN25 の本丸: fix サブエージェントが list の代わりに int(件数) を書いても
    `len(int)` で落ちず、state.json に件数がそのまま保存されること。
    また `deferred` が int でも deferred ループでクラッシュしないこと。
    """
    tmp_dir = patched_tmp_dir
    _seed_state(tmp_dir)
    fix = _canonical_fix()
    # list ではなく int(件数) を書く (再発防止対象のケース)
    fix["resolved_threads"] = 4
    fix["deferred"] = 2
    fix["rejected"] = 1
    (tmp_dir / f"fix-pr{PR}-result.json").write_text(json.dumps(fix))

    # TypeError を出さずに完走すること
    state_mod.cmd_merge_fix(_make_args())

    st = _read_state(tmp_dir)
    merged = st["rounds"][-1]["fix"]
    assert merged["resolved_threads"] == 4
    assert merged["deferred"] == 2
    assert merged["rejected"] == 1
    # deferred が int の場合は deferred ループをスキップするので deferred_nits は空のまま
    assert st["deferred_nits"] == []


def test_merge_fix_list_deferred_expands_nits(patched_tmp_dir, state_mod):
    """`deferred` が list の場合は従来どおり件数が保存され deferred_nits が展開される。

    int 対応で list の既存挙動 (件数集計 + deferred_nits 展開) が壊れていないことの regression guard。
    """
    tmp_dir = patched_tmp_dir
    _seed_state(tmp_dir)
    fix = _canonical_fix()
    fix["resolved_threads"] = [{"thread_id": "T1"}, {"thread_id": "T2"}]
    fix["deferred"] = [
        {"comment_id": 1, "summary": "nit-1"},
        {"comment_id": 2, "summary": "nit-2"},
    ]
    fix["rejected"] = [{"comment_id": 3, "summary": "rej-1"}]
    (tmp_dir / f"fix-pr{PR}-result.json").write_text(json.dumps(fix))

    state_mod.cmd_merge_fix(_make_args())

    st = _read_state(tmp_dir)
    merged = st["rounds"][-1]["fix"]
    # list は len() で件数化
    assert merged["resolved_threads"] == 2
    assert merged["deferred"] == 2
    assert merged["rejected"] == 1
    # deferred_nits が展開され、pr / round が付与される
    assert len(st["deferred_nits"]) == 2
    summaries = {n["summary"] for n in st["deferred_nits"]}
    assert summaries == {"nit-1", "nit-2"}
    for n in st["deferred_nits"]:
        assert n["pr"] == PR
        assert n["round"] == 1
