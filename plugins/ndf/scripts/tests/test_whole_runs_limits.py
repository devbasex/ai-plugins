"""予算なしのテストの上限を、所要の記録と先に走った全体テストの実測から決める（#1631）。

- `limits`: 上限 = ceil(3·max(w, m))。実測が無い・記録以下なら今の値（受け入れ条件 2・5・7・9、I1〜I4）
- `whole_runs`: git の共通ディレクトリへ追記し、同じ鍵の最新 5 行の最大を直近とする（決定 2・I6・I7）
- `test-run.py`: `--limit` で上限を受け、手元の全体検証が終わったら実測を残す（受け入れ条件 3・8）
- `steps.fill_test_limit`: ステップの開始で上限を決め直し、`--limit` と `timeout` を同じ値から作る（受け入れ条件 1・3、決定 3）
"""

from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
for p in (SCRIPTS / "lib", SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import test_strategy as ts  # noqa: E402
import whole_runs  # noqa: E402
from supervise_lib import steps as steps_mod  # noqa: E402
from supervise_lib.verify_steps import with_margin  # noqa: E402

LOCAL = ts.Strategy(ts.LOCAL_FULL, "args")
KEYS = ("init_test_timeout", "test_timeout", "whole_timeout")


def _limits(**kw):
    r = ts.limits(LOCAL, None, **kw)
    return {k: r[k] for k in KEYS}, r["basis"]


# ---------- limits ----------


def test_the_reported_case_is_unchanged_without_a_measurement():
    """受け入れ条件 5 — 実測が無ければ本文の再現と同じ 9 秒で、根拠は記録。"""
    got, basis = _limits(whole_seconds_value=2.7, whole_source="ndf-record")
    assert got == {k: 9 for k in KEYS}
    assert (basis["recent"], basis["recent_source"], basis["limit_from"]) == (None, None, "record")


def test_a_measurement_at_or_below_the_record_keeps_the_limit():
    """受け入れ条件 5・前提 5 — 記録より速い実測で上限を詰めない。"""
    got, basis = _limits(whole_seconds_value=2.7, whole_source="ndf-record", recent_seconds=2.5, recent_source="test-run")
    assert got == {k: 9 for k in KEYS}
    assert basis["limit_from"] == "record" and basis["recent"] == 2.5


@pytest.mark.parametrize(("m", "want"), [(5.0, 15), (12.6, 38)])
def test_a_longer_measurement_widens_the_limit_to_three_times_it(m, want):
    """受け入れ条件 2・7 — 3 倍の切り上げそのもので、余裕の係数も定数も足さない。"""
    got, basis = _limits(whole_seconds_value=2.7, whole_source="ndf-record", recent_seconds=m, recent_source="cross-refactoring")
    assert got == {k: want for k in KEYS}
    assert (basis["recent"], basis["recent_source"], basis["limit_from"]) == (m, "cross-refactoring", "recent")


def test_an_unknown_duration_is_not_narrowed_by_a_measurement():
    """I2・前提 6 — 所要が不明なら 900 / 1800 秒を下回らず、3·m が上回るときだけ広げる。"""
    small, basis = _limits(recent_seconds=10.0, recent_source="test-run")
    assert (small["test_timeout"], small["whole_timeout"]) == (900, 1800) and basis["limit_from"] == "unknown"
    big, basis = _limits(recent_seconds=1000.0, recent_source="test-run")
    assert (big["test_timeout"], big["whole_timeout"]) == (3000, 3000) and basis["limit_from"] == "recent"
    assert basis["unknown_duration"] is True


def test_a_budgeted_call_ignores_the_measurement():
    """受け入れ条件 9・I3。"""
    kw = {"whole_seconds_value": 2.7, "whole_source": "ndf-record", "ci_seconds": 40.0}
    assert ts.limits(LOCAL, 30, **kw) == ts.limits(LOCAL, 30, **kw, recent_seconds=100.0, recent_source="test-run")


# ---------- whole_runs ----------


def test_recent_is_the_largest_of_the_last_five_rows_of_the_same_key(tmp_path):
    for s in (50.0, 1.0, 2.0, 3.0, 4.0, 5.0):
        assert whole_runs.append(tmp_path, "k", s, "test-run")
    whole_runs.append(tmp_path, "other", 99.0, "test-run")
    assert whole_runs.recent(tmp_path, "k") == (5.0, "test-run"), "6 行前の 50 秒は直近に入れない"
    whole_runs.append(tmp_path, "k", 1.0, "cross-refactoring")
    assert whole_runs.recent(tmp_path, "k") == (5.0, "test-run"), "落ちて早く止まった短い行で戻らない"
    assert whole_runs.recent(tmp_path, "missing") is None


def test_broken_rows_are_skipped(tmp_path):
    f = whole_runs.path(tmp_path)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('not json\n{"schema": 2, "key": "k", "seconds": 80}\n{"schema": 1, "key": "k", "seconds": "x"}\n', encoding="utf-8")
    assert whole_runs.recent(tmp_path, "k") is None
    whole_runs.append(tmp_path, "k", 7.0, "test-run")
    assert whole_runs.recent(tmp_path, "k") == (7.0, "test-run")


def test_an_unwritable_place_returns_false_without_raising(tmp_path, monkeypatch, capsys):
    """I7 — 置き場がファイルでふさがれていても例外にしない。"""
    blocker = tmp_path / "blocked"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("NDF_WHOLE_RUNS_DIR", str(blocker))
    assert whole_runs.append(tmp_path, "k", 3.0, "test-run") is False
    assert whole_runs.recent(tmp_path, "k") is None
    assert "残せませんでした" in capsys.readouterr().err


def _git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=str(cwd), check=True, capture_output=True)


def test_another_worktree_reads_the_same_record(tmp_path, monkeypatch):
    """決定 2 — 置き場は git の共通ディレクトリで、別の worktree から読める。"""
    monkeypatch.delenv("NDF_WHOLE_RUNS_DIR", raising=False)
    main = tmp_path / "main"
    main.mkdir()
    _git("init", "-q", cwd=main)
    _git("commit", "-q", "--allow-empty", "-m", "i", cwd=main)
    _git("worktree", "add", "-q", "-b", "other", str(tmp_path / "wt"), cwd=main)
    assert whole_runs.append(tmp_path / "wt", "k", 12.0, "test-run")
    assert whole_runs.path(tmp_path / "wt") == main / ".git" / "ndf" / "test-run" / "whole.jsonl"
    assert whole_runs.recent(main, "k") == (12.0, "test-run")
    assert not (tmp_path / "wt" / ".ndf").exists()


# ---------- test-run.py と steps.fill_test_limit（一時リポジトリで実際に走らせる） ----------

TEST_RUN = SCRIPTS / "test-run.py"
DECL = {
    "test": {"suites": [{"name": "t", "runner": "sh", "command": "sleep 1.5", "paths": ["."]}]},
    "test_duration": {"measured": [{"source": "ndf-record", "seconds": 0.1}]},
}


def _repo(tmp_path):
    root = tmp_path / "repo"
    (root / ".ndf").mkdir(parents=True)
    _git("init", "-q", str(root), cwd=tmp_path)
    _git("commit", "-q", "--allow-empty", "-m", "i", cwd=root)
    (root / ".ndf" / "project.json").write_text(json.dumps(DECL), encoding="utf-8")
    return root


def _run(root, *args, timeout=None):
    p = subprocess.run([sys.executable, str(TEST_RUN), *args, "--root", str(root)], capture_output=True, text=True, timeout=timeout)
    return p.returncode, json.loads(p.stdout.strip().splitlines()[-1])


def _key(root):
    return ts.whole_key(ts.resolve(json.loads((root / ".ndf" / "project.json").read_text(encoding="utf-8"))))


def test_test_run_records_a_finished_whole_and_the_next_run_widens(tmp_path):
    """記録 0.1 秒の上限 1 秒では止まり（打ち切りは残さない）、`--limit` で通った秒が次の上限を広げる。"""
    root = _repo(tmp_path)
    code, out = _run(root, "whole")
    assert code != 0 and "1 秒で終わらなかった" in out["summary"]
    assert whole_runs.recent(root, _key(root)) is None, "I6 — 打ち切りは残さない"
    code, out = _run(root, "whole", "--limit", "10")
    assert code == 0 and out["items"][0]["limits"]["whole_timeout"] == 10
    seconds, source = whole_runs.recent(root, _key(root))
    assert source == "test-run" and 1.5 <= seconds < 10
    code, out = _run(root, "whole")
    assert code == 0 and out["items"][0]["limits"]["whole_timeout"] >= 5


def test_test_run_keeps_its_result_when_the_record_cannot_be_written(tmp_path, monkeypatch):
    """受け入れ条件 8。"""
    root = _repo(tmp_path)
    blocker = tmp_path / "blocked"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("NDF_WHOLE_RUNS_DIR", str(blocker))
    code, out = _run(root, "whole", "--limit", "10")
    assert code == 0 and out["status"] == "ok"


def _ctx(plan, cwd):
    return types.SimpleNamespace(plan=plan, cwd=str(cwd), state=types.SimpleNamespace(cur={}))


def test_a_check_plan_made_before_the_impl_run_does_not_stop(tmp_path):
    """受け入れ条件 1・3 — 実装のフェーズの全体テストが残した実測を、先に作った検査のプランの `test-all` が開始の時点で読む。
    `--limit` と `timeout` は同じ上限から作り、`.ndf/project.json` は変わらない。"""
    root = _repo(tmp_path)
    before = (root / ".ndf" / "project.json").read_bytes()
    strategy = ts.resolve(DECL)
    planned = ts.unbudgeted_limits(strategy, DECL, root)  # 検査のプランの作成（実測が無い時点）
    assert planned["whole_timeout"] == 1 and planned["basis"]["limit_from"] == "record"
    plan = {"テストの時間": planned}
    cmd = f"{sys.executable} {TEST_RUN} whole --root {root} --limit {{test_limit:whole_timeout:1}}"
    step = {"id": "test-all", "timeout": with_margin(1)}

    impl = subprocess.run(
        [sys.executable, str(TEST_RUN), "whole", "--root", str(root), "--limit", "36"], capture_output=True, text=True
    )
    assert impl.returncode == 0  # 実装のフェーズの test-all（報告では上限 36 秒）

    ctx = _ctx(plan, root)
    filled, timeout = steps_mod.fill_test_limit(ctx, step, cmd)
    limit = ctx.state.cur["test_limit"]["seconds"]
    assert limit >= 5 and f"--limit {limit}" in filled and timeout == with_margin(limit)
    assert ctx.state.cur["test_limit"]["basis"]["limit_from"] == "recent"
    p = subprocess.run(filled, shell=True, capture_output=True, text=True, timeout=timeout)
    assert p.returncode == 0, p.stdout
    assert (root / ".ndf" / "project.json").read_bytes() == before


def test_the_step_limit_never_drops_below_the_planned_one(tmp_path):
    """決定 3 — 開始の時点の値が作成の時点を下回らない。basis が読めなければ作成の時点の値で流す。"""
    planned = ts.unbudgeted_limits(LOCAL, {"test_duration": {"measured": [{"source": "ndf-record", "seconds": 0.1}]}}, tmp_path)
    ctx = _ctx({"テストの時間": planned}, tmp_path)
    cmd, timeout = steps_mod.fill_test_limit(ctx, {"timeout": 77}, "x --limit {test_limit:test_timeout:50}")
    assert (cmd, timeout) == ("x --limit 50", 77)
    ctx = _ctx({}, tmp_path)
    cmd, timeout = steps_mod.fill_test_limit(ctx, {"timeout": 77}, "x --limit {test_limit:whole_timeout:9}")
    assert (cmd, timeout) == ("x --limit 9", 77) and ctx.state.cur["test_limit"]["basis"] is None
    assert steps_mod.fill_test_limit(ctx, {"timeout": 5}, "true") == ("true", 5)


# ---------- cross-refactoring の着手前のテスト ----------


def test_only_a_finished_whole_baseline_is_recorded(tmp_path, monkeypatch):
    """決定 4・I6 — `mode: whole` で打ち切られなかった着手前のテストだけを残す。"""
    monkeypatch.syspath_prepend(str(SCRIPTS.parent / "skills" / "cross-refactoring" / "scripts"))
    from refactor_lib import init_test

    strategy = ts.resolve(DECL)
    prep = types.SimpleNamespace(work=tmp_path, strategy=strategy)
    for baseline in (
        {"mode": "scope", "timed_out": False, "seconds": 30.0},
        {"mode": "whole", "timed_out": True, "seconds": 40.0},
        {"mode": "round", "timed_out": False, "seconds": 50.0},
    ):
        init_test.record_whole_run(prep, baseline)
    assert whole_runs.recent(tmp_path, ts.whole_key(strategy)) is None
    init_test.record_whole_run(prep, {"mode": "whole", "timed_out": False, "seconds": 12.6})
    assert whole_runs.recent(tmp_path, ts.whole_key(strategy)) == (12.6, "cross-refactoring")
