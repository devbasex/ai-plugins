"""実行の要約と集計（#662 の AC10 / AC12 / AC15 / AC17 / AC19〜AC22）。

要約は状態ファイルの辞書と、一時ディレクトリの監視の記録だけから組み立てる。
ここでは共通層の `run_metrics.py` を単体で確かめ、状態の保存からの呼び出しは
各 Skill のテストが確かめる。
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
RUN_METRICS = LIB / "run_metrics.py"

COMMON_KEYS = {
    "schema", "kind", "repo", "id", "ndf_version", "host", "started_at",
    "ended_at", "last_saved_at", "final", "wall_clock_seconds", "rounds", "launches",
}


@pytest.fixture(scope="module")
def rm():
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    spec = importlib.util.spec_from_file_location("ndf_lib_run_metrics", RUN_METRICS)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ndf_lib_run_metrics"] = mod
    spec.loader.exec_module(mod)
    return mod


def _review_state(tmp_dir: pathlib.Path, **over) -> dict:
    state = {
        "started_at": "2026-09-15T10:00:00+09:00",
        "ended_at": "2026-09-15T10:30:00+09:00",
        "host": "claude",
        "repo": "devbasex/ai-plugins",
        "current_pr": 665,
        "pr_history": [{"pr": 665, "opened_at": "2026-09-15T10:00:00+09:00"}],
        "tmp_dir": str(tmp_dir),
        "review_instructions": "秘密のレビュー観点",
        "rounds": [
            {"round": 1, "started_at": "2026-09-15T10:00:00+09:00"},
            {"round": 2, "started_at": "2026-09-15T10:12:00+09:00",
             "ended_at": "2026-09-15T10:25:00+09:00"},
        ],
        "final": "approved",
    }
    state.update(over)
    return state


def _journal(tmp_dir: pathlib.Path, *rows: dict) -> None:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    with (tmp_dir / "monitor-outcomes.jsonl").open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _launch(agent: str, stem: str, reason: str, started: str, ended: str,
            elapsed: float = 60.0) -> dict:
    status = {"ok": "OK", "timeout": "TIMEOUT", "missing": "NO_RESULT",
              "stalled": "STALLED", "early_error": "EARLY_ERROR"}[reason]
    return {
        "agent": agent, "stem": stem, "status": status, "exit_code": 0,
        "reason": reason, "detail": "Authorization: Bearer sk-secret-xyz",
        "launched_at": started, "started_at": started, "ended_at": ended,
        "elapsed": elapsed, "idle_seconds": 1.0, "progress_tail": "",
        "result_exists": reason == "ok", "pid": 123,
    }


# ---------- AC10: 置き場所 ----------

@pytest.mark.parametrize(("env", "expected"), [
    ({"NDF_METRICS_DIR": "D", "XDG_STATE_HOME": "X", "HOME": "H"}, "D"),
    ({"XDG_STATE_HOME": "X", "HOME": "H"}, "X/ndf/metrics"),
    ({"HOME": "H"}, "H/.local/state/ndf/metrics"),
    ({"NDF_METRICS_DIR": "", "XDG_STATE_HOME": "", "HOME": "H"}, "H/.local/state/ndf/metrics"),
])
def test_metrics_dir_resolution_order(rm, tmp_path, env, expected):
    env = {k: (str(tmp_path / v) if v else v) for k, v in env.items()}
    assert rm.metrics_dir(env) == tmp_path / expected


def test_metrics_disabled_writes_nothing(rm, tmp_path):
    state_path = tmp_path / "tmp" / "cross-review-pr665-state.json"
    base = tmp_path / "metrics"
    env = {"NDF_METRICS": "0", "NDF_METRICS_DIR": str(base)}
    path, reason = rm.write_summary(state_path, _review_state(tmp_path / "tmp"),
                                    "cross-review", env=env)
    assert path is None
    assert "NDF_METRICS=0" in reason
    assert not base.exists()


# ---------- AC8 のパス / AC12 / AC15 / AC17 ----------

def test_summary_path_uses_repo_kind_id_and_utc_start(rm, tmp_path):
    state = _review_state(tmp_path)
    path = rm.summary_path(state, "cross-review", base=tmp_path / "m")
    assert path == tmp_path / "m" / "devbasex--ai-plugins" / "cross-review-pr665-20260915T010000Z.json"


def test_summary_has_common_keys_and_launches_without_detail(rm, tmp_path):
    tmp_dir = tmp_path / "tmp"
    _journal(
        tmp_dir,
        # 前の実行の起動（開始より前）は数えない
        _launch("agy", "agy-review-pr665", "ok", "2026-09-14T09:00:00+09:00", "2026-09-14T09:10:00+09:00"),
        _launch("agy", "agy-review-pr665", "timeout", "2026-09-15T10:01:00+09:00", "2026-09-15T10:08:00+09:00"),
        _launch("kiro", "kiro-critique-pr665", "ok", "2026-09-15T10:09:00+09:00", "2026-09-15T10:11:00+09:00"),
        # 別の Pull Request の起動は数えない
        _launch("kiro", "kiro-review-pr999", "ok", "2026-09-15T10:02:00+09:00", "2026-09-15T10:03:00+09:00"),
    )
    state = _review_state(tmp_dir)
    summary = rm.build_summary(tmp_dir / "cross-review-pr665-state.json", state, "cross-review")

    assert set(summary) == COMMON_KEYS
    assert summary["schema"] == 1
    assert summary["kind"] == "cross-review"
    assert (summary["repo"], summary["id"], summary["host"]) == ("devbasex/ai-plugins", 665, "claude")
    assert summary["final"] == "approved"
    assert summary["wall_clock_seconds"] == 1800
    assert summary["ndf_version"] == json.loads(
        (LIB.parents[1] / ".claude-plugin" / "plugin.json").read_text())["version"]
    assert summary["rounds"] == [
        {"round": 1, "started_at": "2026-09-15T10:00:00+09:00", "ended_at": "2026-09-15T10:12:00+09:00"},
        {"round": 2, "started_at": "2026-09-15T10:12:00+09:00", "ended_at": "2026-09-15T10:25:00+09:00"},
    ]
    assert [(l["agent"], l["reason"]) for l in summary["launches"]] == [("agy", "timeout"), ("kiro", "ok")]
    assert all("detail" not in l for l in summary["launches"])
    # AC17: 本文・detail を含まない
    text = json.dumps(summary, ensure_ascii=False)
    assert "sk-secret" not in text
    assert "秘密のレビュー観点" not in text


def test_summary_ignores_malformed_and_non_object_journal_rows(rm, tmp_path):
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    launch = _launch(
        "agy", "agy-review-pr665", "ok",
        "2026-09-15T10:01:00+09:00", "2026-09-15T10:02:00+09:00",
    )
    (tmp_dir / "monitor-outcomes.jsonl").write_text(
        "{not json\n" + json.dumps(["not", "an", "object"]) + "\n"
        + json.dumps(launch) + "\n",
        encoding="utf-8",
    )

    summary = rm.build_summary(
        tmp_dir / "cross-review-pr665-state.json",
        _review_state(tmp_dir),
        "cross-review",
    )

    assert summary["launches"] == [
        {key: value for key, value in launch.items() if key != "detail"}
    ]


def test_unfinished_run_has_null_end_and_last_saved_at(rm, tmp_path):
    state = _review_state(tmp_path, final=None)
    state.pop("ended_at")
    summary = rm.build_summary(tmp_path / "s.json", state, "cross-review")
    assert summary["final"] is None
    assert summary["ended_at"] is None
    assert summary["wall_clock_seconds"] is None
    assert summary["last_saved_at"]
    assert summary["rounds"][0]["ended_at"] == "2026-09-15T10:12:00+09:00"


def test_naive_times_get_the_local_timezone(rm, tmp_path):
    """cross-refactoring の `statefile.now` はタイムゾーンを持たない。"""
    state = {"id": 130, "repo": "o/r", "host": "codex", "started_at": "2026-08-15T00:00:00",
             "final": None, "rounds": []}
    summary = rm.build_summary(tmp_path / "s.json", state, "cross-refactoring")
    import datetime as dt
    assert dt.datetime.fromisoformat(summary["started_at"]).tzinfo is not None


def test_extra_keys_are_merged(rm, tmp_path):
    summary = rm.build_summary(tmp_path / "s.json", _review_state(tmp_path), "cross-review",
                               extra=lambda path, st, launches: {"measure": {"rounds": 2}})
    assert summary["measure"] == {"rounds": 2}


def test_write_summary_overwrites_the_same_file(rm, tmp_path):
    base = tmp_path / "m"
    env = {"NDF_METRICS_DIR": str(base)}
    state = _review_state(tmp_path, final=None)
    first, _ = rm.write_summary(tmp_path / "s.json", state, "cross-review", env=env)
    state["final"] = "approved"
    second, _ = rm.write_summary(tmp_path / "s.json", state, "cross-review", env=env)
    assert first == second
    assert json.loads(second.read_text())["final"] == "approved"
    assert len(list(base.rglob("*.json"))) == 1


def test_after_save_swallows_errors(rm, tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("壊れた")
    monkeypatch.setattr(rm, "build_summary", boom)
    rm.after_save(tmp_path / "s.json", _review_state(tmp_path), "cross-review")
    assert "壊れた" in capsys.readouterr().err


# ---------- AC19〜AC22: 集計 ----------

def _summary(kind, id_, started, minutes, *, final="approved", repo="o/r", version="10.12.0",
             rounds=1, launches=()):
    wall = None if final is None else minutes * 60
    return {
        "schema": 1, "kind": kind, "repo": repo, "id": id_, "ndf_version": version,
        "host": "claude", "started_at": started, "ended_at": None, "last_saved_at": started,
        "final": final, "wall_clock_seconds": wall,
        "rounds": [{"round": i + 1} for i in range(rounds)],
        "launches": list(launches),
    }


@pytest.fixture()
def metrics_tree(tmp_path):
    base = tmp_path / "metrics"
    (base / "o--r").mkdir(parents=True)
    (base / "x--y").mkdir(parents=True)
    rows = [
        _summary("cross-review", 1, "2026-09-01T10:00:00+09:00", 10, rounds=1,
                 launches=[{"agent": "agy", "reason": "timeout"}, {"agent": "kiro", "reason": "ok"}]),
        _summary("cross-review", 2, "2026-09-02T10:00:00+09:00", 20, rounds=2,
                 launches=[{"agent": "agy", "reason": "timeout"}]),
        _summary("cross-review", 3, "2026-09-03T10:00:00+09:00", 40, rounds=3),
        _summary("cross-review", 4, "2026-09-04T10:00:00+09:00", 0, final=None),
        _summary("cross-refactoring", 5, "2026-09-05T10:00:00+09:00", 90, repo="x/y",
                 version="10.13.0"),
    ]
    for row in rows:
        folder = row["repo"].replace("/", "--")
        (base / folder / f"{row['kind']}-{row['id']}.json").write_text(json.dumps(row))
    (base / "o--r" / "broken.json").write_text("{not json")
    return base


def _aggregate(base, *args):
    return subprocess.run(
        [sys.executable, str(RUN_METRICS), "aggregate", "--dir", str(base), *args],
        capture_output=True, text=True, timeout=30,
    )


def _row(out: str, label: str) -> list[str]:
    for line in out.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == label:
            return cells
    raise AssertionError(f"{label} の行がありません:\n{out}")


def test_aggregate_total_by_kind_with_unfinished_row(metrics_tree):
    proc = _aggregate(metrics_tree)
    assert proc.returncode == 0, proc.stderr
    # 件数 / 中央値 / p75 / p90 / 最大 / 合計（分）
    assert _row(proc.stdout, "cross-review")[1:] == ["3", "20.0", "30.0", "36.0", "40.0", "70.0"]
    assert _row(proc.stdout, "cross-refactoring")[1:] == ["1", "90.0", "90.0", "90.0", "90.0", "90.0"]
    assert _row(proc.stdout, "終わっていない（cross-review）")[1] == "1"
    # AC21: 壊れた要約は 1 行で報告して飛ばす
    assert "読めない要約: 1 件" in proc.stdout + proc.stderr


@pytest.mark.parametrize(("args", "counts"), [
    (("--since", "2026-09-02"), {"cross-review": "2", "cross-refactoring": "1"}),
    (("--until", "2026-09-02"), {"cross-review": "2"}),
    (("--repo", "x/y"), {"cross-refactoring": "1"}),
    (("--kind", "cross-refactoring"), {"cross-refactoring": "1"}),
    (("--version", "10.12.0"), {"cross-review": "3"}),
])
def test_aggregate_filters(metrics_tree, args, counts):
    proc = _aggregate(metrics_tree, *args)
    assert proc.returncode == 0, proc.stderr
    for label, count in counts.items():
        assert _row(proc.stdout, label)[1] == count
    for label in {"cross-review", "cross-refactoring"} - set(counts):
        with pytest.raises(AssertionError):
            _row(proc.stdout, label)


def test_aggregate_by_round_count(metrics_tree):
    proc = _aggregate(metrics_tree, "--by", "round-count")
    assert proc.returncode == 0, proc.stderr
    assert _row(proc.stdout, "1")[1:] == ["1", "10.0"]
    assert _row(proc.stdout, "2")[1:] == ["1", "20.0"]
    assert _row(proc.stdout, "3 以上")[1:] == ["1", "40.0"]


def test_aggregate_by_reason(metrics_tree):
    proc = _aggregate(metrics_tree, "--by", "reason")
    assert proc.returncode == 0, proc.stderr
    lines = [l for l in proc.stdout.splitlines() if l.startswith("| cross-review")]
    cells = sorted(tuple(c.strip() for c in l.strip().strip("|").split("|")) for l in lines)
    assert cells == [("cross-review", "agy", "timeout", "2"), ("cross-review", "kiro", "ok", "1")]


def test_aggregate_with_no_summaries(tmp_path):
    proc = _aggregate(tmp_path / "empty")
    assert proc.returncode == 0, proc.stderr


# ---------- _select の等値の絞り込み（現状固定） ----------

_SELECT_ROWS = [
    {"id": "a", "repo": "o/x", "kind": "cross-review", "ndf_version": "1.0.0"},
    {"id": "b", "repo": "o/y", "kind": "cross-review", "ndf_version": "1.0.0"},
    {"id": "c", "repo": "o/x", "kind": "cross-refactoring", "ndf_version": "2.0.0"},
    {"id": "d", "repo": "o/x", "kind": "cross-review", "ndf_version": "2.0.0"},
]


def _select_args(**over):
    import argparse
    base = {"since": None, "until": None, "repo": None, "kind": None, "version": None}
    base.update(over)
    return argparse.Namespace(**base)


@pytest.mark.parametrize("over, expected", [
    ({}, ["a", "b", "c", "d"]),
    ({"repo": "o/x"}, ["a", "c", "d"]),
    ({"kind": "cross-refactoring"}, ["c"]),
    ({"version": "1.0.0"}, ["a", "b"]),
    ({"repo": "o/x", "kind": "cross-review", "version": "2.0.0"}, ["d"]),
    ({"repo": "o/z"}, []),
    ({"repo": "", "kind": "", "version": ""}, ["a", "b", "c", "d"]),
])
def test_select_equality_filters(rm, over, expected):
    picked = rm._select(_SELECT_ROWS, _select_args(**over))
    assert [row["id"] for row in picked] == expected
