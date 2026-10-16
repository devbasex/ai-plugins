"""宣言の `test.suites[].ci_jobs` と `test.ci_exempt` の形（#464 の I1・I2）。

`project-decl.py check` は宣言を `model.validate_decl` で読み、形に合わなければ落とす。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))

import schema  # noqa: E402
from project_lib import model  # noqa: E402

SUITE = {"name": "lint", "runner": "check-lint", "kind": "lint", "command": "bash scripts/check-lint.sh"}


def decl_test(**extra) -> dict:
    return {"suites": [SUITE], **extra}


@pytest.mark.parametrize("job", [".github/workflows/lint.yml", ".github/workflows/lint.yml#lint", "ci/a.yaml#build-1"])
def test_job_ids_of_both_forms_are_accepted(job):
    model.validate_item("test", decl_test(suites=[{**SUITE, "ci_jobs": [job]}], ci_exempt=[{"job": job, "reason": "理由"}]))


@pytest.mark.parametrize("job", ["lint", ".github/workflows/lint.yml#", ".github/workflows/lint.yml#a#b", "lint.yml#a b"])
def test_job_ids_outside_the_two_forms_are_rejected(job):
    with pytest.raises(schema.ShapeError):
        model.validate_item("test", decl_test(suites=[{**SUITE, "ci_jobs": [job]}]))
    with pytest.raises(schema.ShapeError):
        model.validate_item("test", decl_test(ci_exempt=[{"job": job, "reason": "理由"}]))


@pytest.mark.parametrize("exempt", [{"job": "a.yml"}, {"job": "a.yml", "reason": ""}, {"job": "a.yml", "reason": "  "}])
def test_exempt_needs_a_reason(exempt):
    with pytest.raises(schema.ShapeError):
        model.validate_item("test", decl_test(ci_exempt=[exempt]))


def test_declarations_without_the_keys_still_read():
    """`ci_jobs` と `ci_exempt` は省いてよい。"""
    model.validate_item("test", decl_test())


def test_this_repository_declaration_is_valid():
    model.validate_decl(json.loads((SCRIPTS.parents[2] / ".ndf" / "project.json").read_text(encoding="utf-8")))


class FakeGh:
    """`_measure_runs` が読む `Gh` の面（`repo` と `get`）だけを持つ。"""

    repo = "o/r"

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[str] = []

    def get(self, path: str):
        self.calls.append(path)
        return self.responses.get(path)


def test_measure_runs_keeps_the_current_shape(monkeypatch):
    """現状固定: workflow ごとの最新の run・job 数・step の合計・必須チェックの形。"""
    from project_lib import measure_ci

    monkeypatch.setattr(measure_ci, "_junit_of_run", lambda gh, run: None)
    run = {"id": 7, "path": ".github/workflows/ci.yml", "run_started_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:02:00Z"}
    older = {**run, "id": 6}
    step = {"name": "pytest", "started_at": "2026-01-01T00:00:10Z", "completed_at": "2026-01-01T00:01:10Z"}
    gh = FakeGh(
        {
            "repos/o/r/actions/runs?status=success&per_page=50": {"workflow_runs": [run, older]},
            "repos/o/r/actions/runs/7/jobs?per_page=100": {"jobs": [{"steps": [step]}, {"steps": []}, {"steps": []}]},
            "repos/o/r/rules/branches/main": [
                {
                    "type": "required_status_checks",
                    "parameters": {"required_status_checks": [{"context": "b"}, {"context": "a"}, {"context": "a"}]},
                }
            ],
        }
    )
    ci, found, notes = measure_ci._measure_runs(gh, {".github/workflows/ci.yml": 2, ".github/workflows/lint.yml": 1}, "main")
    assert ci == {
        "provider": "github-actions",
        "workflows": [
            {"path": ".github/workflows/ci.yml", "jobs": 3, "wall_seconds": 120.0},
            {"path": ".github/workflows/lint.yml", "jobs": 1},
        ],
        "required_checks": ["a", "b"],
    }
    assert [d["source"] for d in found] == ["ci-steps"]
    assert found[0]["seconds"] == 60.0
    assert "repos/o/r/actions/runs/6/jobs?per_page=100" not in gh.calls
    assert notes == []


def test_measure_runs_without_runs_or_workflows_reports_none():
    """現状固定: run も workflow も無ければ provider は none で、head が無ければ必須チェックを読まない。"""
    from project_lib import measure_ci

    gh = FakeGh({})
    ci, found, notes = measure_ci._measure_runs(gh, {}, None)
    assert ci == {"provider": "none", "workflows": [], "required_checks": []}
    assert found == [] and notes == []
    assert gh.calls == ["repos/o/r/actions/runs?status=success&per_page=50"]


RUNS = "repos/o/r/actions/runs?status=success&per_page=50"
STEP = {"name": "pytest", "started_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-01T00:08:00Z"}


def _run(rid: int, path: str, minutes: int) -> dict:
    return {"id": rid, "path": path, "run_started_at": "2026-01-01T00:00:00Z", "updated_at": f"2026-01-01T00:{minutes:02d}:00Z"}


def _jobs(rid: int, *conclusions) -> tuple[str, dict]:
    jobs = [{"conclusion": c, "steps": [] if c == "skipped" else [STEP]} if c else {"steps": [STEP]} for c in conclusions]
    return f"repos/o/r/actions/runs/{rid}/jobs?per_page=100", {"jobs": jobs}


def _junit_by_run(gh, run):
    return {"seconds": float(run["id"]), "source": "ci-junit", "detail": f"run {run['id']}"}


@pytest.mark.parametrize(
    "path,newer,older",
    [
        (".github/workflows/pytest.yml", ("success", "skipped"), ("success", "success")),
        ("ci/other.yaml", ("skipped",), (None, "success")),
    ],
)
def test_representative_is_the_newest_run_without_skipped_jobs(monkeypatch, path, newer, older):
    """#1664 の AC1・AC2・AC5・I1〜I3: 飛ばした新しい run を外し、古い run の壁時計・step・JUnit を採る。名前に依らない。"""
    from project_lib import measure_ci

    monkeypatch.setattr(measure_ci, "_junit_of_run", _junit_by_run)
    gh = FakeGh({RUNS: {"workflow_runs": [_run(9, path, 1), _run(8, path, 10)]}, **dict([_jobs(9, *newer), _jobs(8, *older)])})
    ci, found, notes = measure_ci._measure_runs(gh, {path: 2}, None)
    assert ci["workflows"] == [{"path": path, "jobs": 2, "wall_seconds": 600.0}]
    by = {d["source"]: d for d in found}
    assert by["ci-junit"]["seconds"] == 8.0
    assert by["ci-steps"]["detail"].startswith("run 8（") and by["ci-steps"]["seconds"] == 960.0
    assert notes == []


def test_workflow_with_only_skipped_runs_has_no_wall_and_a_note(monkeypatch):
    """#1664 の AC3・AC7・I4: 候補がすべて飛ばした run なら壁時計も所要も書かず注記を出す。run の無いワークフローには出さない。"""
    from project_lib import measure_ci

    monkeypatch.setattr(measure_ci, "_junit_of_run", _junit_by_run)
    a = "a.yml"
    gh = FakeGh({RUNS: {"workflow_runs": [_run(5, a, 1), _run(4, a, 2)]}, **dict([_jobs(5, "skipped"), _jobs(4, "success", "skipped")])})
    ci, found, notes = measure_ci._measure_runs(gh, {a: 1, "b.yml": 1}, None)
    assert ci["workflows"] == [{"path": a, "jobs": 1}, {"path": "b.yml", "jobs": 1}]
    assert found == []
    assert notes == [f"飛ばしていない run が無い: {a}（候補 2 件）"]


def test_candidates_stop_at_the_limit(monkeypatch):
    """#1664 の I5: ワークフローごとのジョブの取得は `CANDIDATE_RUNS` 回までで、それより古い run は探さない。"""
    from project_lib import measure_ci

    monkeypatch.setattr(measure_ci, "_junit_of_run", _junit_by_run)
    runs = [_run(i, "a.yml", 1) for i in range(10, 4, -1)]
    gh = FakeGh(
        {RUNS: {"workflow_runs": runs}, **dict(_jobs(i, "skipped") for i in range(10, 6, -1)), **dict(_jobs(i, "success") for i in (6, 5))}
    )
    ci, found, notes = measure_ci._measure_runs(gh, {}, None)
    assert sum("/jobs?" in c for c in gh.calls) == measure_ci.CANDIDATE_RUNS == 5
    assert ci["workflows"][0]["wall_seconds"] == 60.0 and found[0]["detail"] == "run 6"
    gh = FakeGh({RUNS: {"workflow_runs": runs}, **dict(_jobs(i, "skipped") for i in range(10, 4, -1))})
    ci, found, notes = measure_ci._measure_runs(gh, {}, None)
    assert sum("/jobs?" in c for c in gh.calls) == 5
    assert notes == ["飛ばしていない run が無い: a.yml（候補 5 件）"]


def test_timeout_while_fetching_candidates_keeps_what_was_measured(monkeypatch):
    """#1664 の I5: 候補のジョブの取得が時間切れでも ci は不明にせず、先の行と必須のチェックを残し、後は注記にする。"""
    from project_lib import measure_ci

    monkeypatch.setattr(measure_ci, "_junit_of_run", lambda gh, run: None)

    class SlowGh(FakeGh):
        def get(self, path):
            if path.startswith("repos/o/r/actions/runs/2/"):
                raise measure_ci.GhUnavailable("時間切れ")
            return super().get(path)

    rules = [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "x"}]}}]
    gh = SlowGh(
        {
            RUNS: {"workflow_runs": [_run(1, "a.yml", 3), _run(2, "b.yml", 4), _run(3, "c.yml", 5)]},
            "repos/o/r/rules/branches/main": rules,
            **dict([_jobs(1, "success")]),
        }
    )
    ci, found, notes = measure_ci._measure_runs(gh, {}, "main")
    assert ci["workflows"] == [
        {"path": "a.yml", "jobs": 1, "wall_seconds": 180.0},
        {"path": "b.yml", "jobs": 0},
        {"path": "c.yml", "jobs": 0},
    ]
    assert ci["required_checks"] == ["x"]
    assert notes == ["候補のジョブを取れない（時間切れ）: b.yml", "候補のジョブを取れない（時間切れ）: c.yml"]
    assert not any(c.startswith("repos/o/r/actions/runs/3/") for c in gh.calls)


def test_runs_list_after_the_deadline_is_a_timeout_and_starts_no_gh(tmp_path):
    """#1664 の非機能（I10）: 締め切りを過ぎた `Gh` は `gh` を起動せず時間切れを返す。"""
    import time

    from project_lib import measure_ci

    gh = measure_ci.Gh(tmp_path, "o/r", time.monotonic() - 1)
    with pytest.raises(measure_ci.GhUnavailable, match="時間切れ"):
        measure_ci._measure_runs(gh, {"a.yml": 1}, "main")
    assert gh.calls == 0
