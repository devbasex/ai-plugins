"""CI のテストの所要の読み取り（`scripts/ci_test_durations.py`、#4331）。

`gh` の代わりに、API のパスから応答（JSON・ZIP のバイト列・例外）を引く偽物を `main(argv, gh=...)` へ渡す。
成果物は `tmp_path` に XML と試行の番号のファイルを書いて作る。実際の待ちと子プロセスは使わない。
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import sys
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import ci_test_durations as ctd  # noqa: E402

NOW = datetime(2026, 11, 24, tzinfo=timezone.utc)
JOBS_PATH = "repos/{owner}/{repo}/actions/runs/%d/jobs?filter=latest&per_page=100"


class FakeGh:
    """パスの正規表現から応答を引く `Gh` の偽物。応答が例外なら上げ、関数なら呼ぶ。"""

    def __init__(self, routes: dict[str, object] | None = None) -> None:
        self.routes = dict(routes or {})
        self.calls: Counter[str] = Counter()
        self.paths: list[str] = []
        self.limits: list[int | None] = []

    def _answer(self, path: str, kind: str):
        self.calls[kind] += 1
        self.paths.append(path)
        for pattern, value in self.routes.items():
            if re.fullmatch(pattern, path):
                if callable(value) and not isinstance(value, type):
                    value = value()
                if isinstance(value, BaseException):
                    raise value
                return value
        raise ctd.GhError(f"{path}: 404")

    def get(self, path: str, kind: str):
        return self._answer(path, kind)

    def raw(self, path: str, kind: str, limit: int | None = None) -> bytes:
        self.limits.append(limit)
        data = self._answer(path, kind)
        if limit is not None and len(data) > limit:
            raise ctd.ArtifactTooLarge(limit + 1)
        return data


def zip_of(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in files.items():
            zf.writestr(name, body)
    return buf.getvalue()


def xml_of(*cases: tuple[str, str, float | None]) -> str:
    body = "".join(
        f'<testcase classname="{c}" name="{n}"' + (f' time="{t}"' if t is not None else "") + "/>" for c, n, t in cases
    )
    return f"<testsuites><testsuite>{body}</testsuite></testsuites>"


def job(i: int, attempt: int = 1, conclusion: str = "success", total: int = 6) -> dict:
    return {
        "name": f"pytest ({i}/{total})",
        "run_attempt": attempt,
        "conclusion": conclusion,
        "started_at": "2026-11-24T00:00:00Z",
        "completed_at": f"2026-11-24T00:01:{10 + i:02d}Z",
        "steps": [
            {"name": ctd.ROOT_STEP, "started_at": "2026-11-24T00:00:10Z", "completed_at": "2026-11-24T00:00:30Z"},
            {"name": ctd.NDF_STEP, "started_at": "2026-11-24T00:00:30Z", "completed_at": "2026-11-24T00:01:00Z"},
        ],
    }


def write_part(base: Path, kind: str, i: int, attempt: int | None = 1, xml: str | None = None) -> None:
    prefix = ctd.NDF_ARTIFACT if kind == "ndf" else ctd.ROOT_ARTIFACT
    d = base / f"{prefix}{i}"
    d.mkdir(parents=True, exist_ok=True)
    if xml is not False:
        (d / f"{prefix}{i}.xml").write_text(xml or xml_of((f"{kind}.test_{i}", "t", 1.0 + i)), encoding="utf-8")
    if attempt is not None:
        (d / f"shard-attempt-{i}.txt").write_text(f"{attempt}\n", encoding="utf-8")


def full_dir(base: Path, total: int = 6, attempt: int = 1) -> Path:
    for i in range(total):
        write_part(base, "root", i, attempt)
        write_part(base, "ndf", i, attempt)
    return base


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "ci-test-duration.json"

    def write(**kw) -> Path:
        body = {"runs": 3, "limit_seconds": 100, "top": 3} | kw
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    write()
    monkeypatch.setattr(ctd, "CONFIG", path)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    return write


# --- read_config ---


def test_read_config(config) -> None:
    assert ctd.read_config(config()) == {"runs": 3, "limit_seconds": 100.0, "top": 3}
    assert ctd.read_config(config(runs=1, limit_seconds=12.5))["runs"] == 1


@pytest.mark.parametrize("kw", [{"runs": 0}, {"top": None}, {"limit_seconds": 0}, {"runs": True}, {"limit_seconds": "1"}])
def test_read_config_rejects(config, kw) -> None:
    with pytest.raises(ctd.ConfigError):
        ctd.read_config(config(**kw))


def test_read_config_rejects_non_json(tmp_path) -> None:
    (tmp_path / "x.json").write_text("{", encoding="utf-8")
    with pytest.raises(ctd.ConfigError):
        ctd.read_config(tmp_path / "x.json")


def test_read_config_reads_repository_config() -> None:
    assert set(ctd.read_config()) == {"runs", "limit_seconds", "top"}


# --- shard_jobs・parse_junit・read_parts・match_attempts ---


def test_shard_jobs() -> None:
    jobs = [job(i) for i in range(6)] + [{"name": "pytest (所要)"}, {"name": "pytest (NDF の ok の出力)"}]

    total, by = ctd.shard_jobs(jobs)

    assert total == 6 and sorted(by) == list(range(6))
    assert ctd.shard_jobs([{"name": "pytest"}]) == (0, {})


def test_parse_junit() -> None:
    data = xml_of(("tests.test_a.TestX", "test_1", 0.5), ("tests.test_b", "test_2", None)).encode()

    assert ctd.parse_junit(data) == [("tests.test_a.TestX::test_1", 0.5), ("tests.test_b::test_2", 0.0)]
    with pytest.raises(ctd.ET.ParseError):
        ctd.parse_junit(b"<testsuites><testcase")


@pytest.mark.parametrize("value", ["abc", "nan", "inf", "-1"])
def test_parse_junit_rejects_broken_seconds(value) -> None:
    with pytest.raises(ValueError):
        ctd.parse_junit(xml_of(("tests.test_a", "t", value)).encode())


def test_read_parts_treats_broken_seconds_as_missing(tmp_path) -> None:
    write_part(tmp_path, "root", 0, 1)
    write_part(tmp_path, "ndf", 0, 1, xml=xml_of(("ndf.test_0", "t", "abc")))

    parts = ctd.read_parts(tmp_path)

    assert parts[("root", 0)].error is None and parts[("root", 0)].cases == [("root.test_0::t", 1.0)]
    assert parts[("ndf", 0)].error and "abc" in parts[("ndf", 0)].error


def test_read_parts(tmp_path) -> None:
    write_part(tmp_path, "root", 0, 2)
    write_part(tmp_path, "ndf", 0, None)
    write_part(tmp_path, "ndf", 1, 1, xml="<broken")
    write_part(tmp_path, "root", 1, 1, xml=False)
    (tmp_path / "ndf-ok-tools-0").mkdir()

    parts = ctd.read_parts(tmp_path)

    assert sorted(parts) == [("ndf", 0), ("ndf", 1), ("root", 0), ("root", 1)]
    assert parts[("root", 0)].attempt == 2 and parts[("root", 0)].cases == [("root.test_0::t", 1.0)]
    assert parts[("ndf", 0)].attempt is None and parts[("ndf", 0)].error is None
    assert parts[("ndf", 1)].error and parts[("root", 1)].error == "JUnit XML が無い"


def test_match_attempts(tmp_path) -> None:
    full_dir(tmp_path)
    write_part(tmp_path, "ndf", 2, attempt=2)
    jobs = {i: job(i, conclusion="failure" if i == 4 else "success") for i in range(6)}

    match = ctd.match_attempts(ctd.read_parts(tmp_path), 6, jobs)

    assert match.accepted == [0, 1, 3, 4, 5]
    assert match.stale == [2] and match.failed == [4] and match.missing == []
    assert match.shard_attempts == [1, 1, None, 1, 1, 1]


def test_match_attempts_missing(tmp_path) -> None:
    full_dir(tmp_path)
    for name in ("pytest-ndf-junit-0/pytest-ndf-junit-0.xml", "pytest-ndf-junit-1/pytest-ndf-junit-1.xml"):
        (tmp_path / name).unlink()
    shutil.rmtree(tmp_path / "pytest-ndf-junit-1")

    match = ctd.match_attempts(ctd.read_parts(tmp_path), 6, {i: job(i) for i in range(6)})

    # ジョブ 0 は試行の番号のファイルだけの部品（番号は一致する）でも missing
    assert match.missing == [0, 1] and 0 not in match.accepted


# --- current_total ---


def test_current_total_valid(tmp_path) -> None:
    gh = FakeGh({re.escape(JOBS_PATH % 7): {"jobs": [job(i) for i in range(6)]}})

    body, unreadable = ctd.current_total(full_dir(tmp_path), gh, 7, 1)

    assert body == {
        "run": 7,
        "attempt": 1,
        "shard_attempts": [1] * 6,
        "valid": True,
        "root_seconds": 21.0,
        "ndf_seconds": 21.0,
        "total_seconds": 42.0,
        "reason": None,
    }
    assert unreadable == []


@pytest.mark.parametrize(
    ("routes", "reason"),
    [
        ({}, "ジョブの一覧を取れない"),
        ({".*": ctd.GhTimeUp("x")}, "ジョブの一覧を取れない（時間の上限）"),
        ({".*": {"jobs": [{"name": "lint"}]}}, "テストのジョブが一覧に無い"),
        ({".*": {"jobs": [job(i, attempt=2 if i == 3 else 1) for i in range(6)]}}, "古い試行のジョブ: 3"),
    ],
)
def test_current_total_invalid(tmp_path, routes, reason) -> None:
    body, unreadable = ctd.current_total(full_dir(tmp_path), FakeGh(routes), 7, 1)

    assert body["valid"] is False and body["total_seconds"] is None and body["reason"] == reason
    assert len(body["shard_attempts"]) == 6
    assert unreadable and reason in unreadable[0]


def test_current_total_broken_xml(tmp_path) -> None:
    full_dir(tmp_path)
    write_part(tmp_path, "root", 5, xml="<broken")

    body, _ = ctd.current_total(tmp_path, FakeGh({".*": {"jobs": [job(i) for i in range(6)]}}), 7, 1)

    assert body["valid"] is False and "欠けたジョブ: 5" in body["reason"] and "読めない XML" in body["reason"]


# --- past_totals ---


def total_zip(run_id: int, seconds: float = 50.0, valid: bool = True, attempt: int = 1) -> bytes:
    body = {"run": run_id, "attempt": attempt, "shard_attempts": [attempt] * 6, "valid": valid, "total_seconds": seconds}
    return zip_of({"ci-test-duration-total.json": json.dumps(body)})


def history(n: int, start: int = 100, per_page: int = 100) -> dict[str, object]:
    """実行番号 start-1 から下へ n 回の、成功した main の実行と集計の成果物。"""
    runs = [
        {"id": 1000 + k, "run_number": start - 1 - k, "run_attempt": 1, "created_at": (NOW - timedelta(hours=k + 1)).isoformat()}
        for k in range(n)
    ]
    arts = [
        {"id": 5000 + r["id"], "expired": False, "workflow_run": {"id": r["id"]}, "created_at": r["created_at"]} for r in runs
    ]
    routes: dict[str, object] = {}
    for page in range(0, max(n, 1), per_page):
        no = page // per_page + 1
        routes[rf".*/workflows/pytest\.yml/runs\?.*&page={no}"] = {"workflow_runs": runs[page : page + per_page]}
        routes[rf".*/actions/artifacts\?name=ci-test-duration-total&.*&page={no}"] = {"artifacts": arts[page : page + per_page]}
    for r in runs:
        routes[rf".*/artifacts/{5000 + r['id']}/zip"] = total_zip(r["id"])
    return routes


def test_past_totals_enough_on_first_page() -> None:
    gh = FakeGh(history(20))

    search = ctd.past_totals(gh, 100, 9, NOW)

    assert search.stop == "enough" and [r for r, _ in search.totals] == list(range(1000, 1009))
    assert gh.calls == Counter({"pages": 2, "zips": 9})


def test_past_totals_skips_current_and_newer_runs() -> None:
    routes = history(5)
    newer = {"id": 1, "run_number": 100, "run_attempt": 1, "created_at": NOW.isoformat()}
    routes[r".*/workflows/pytest\.yml/runs\?.*&page=1"]["workflow_runs"].insert(0, newer)

    search = ctd.past_totals(FakeGh(routes), 100, 2, NOW)

    assert [r for r, _ in search.totals] == [1000, 1001]


def test_past_totals_orders_by_run_number_not_artifact_time() -> None:
    routes = history(3)
    arts = routes[r".*/actions/artifacts\?name=ci-test-duration-total&.*&page=1"]["artifacts"]
    arts.reverse()

    search = ctd.past_totals(FakeGh(routes), 100, 2, NOW)

    assert [r for r, _ in search.totals] == [1000, 1001]


def test_past_totals_reads_second_run_page() -> None:
    routes = history(4, per_page=100)
    runs = routes[r".*/workflows/pytest\.yml/runs\?.*&page=1"]["workflow_runs"]
    # 1 ページ目が満杯（100 件）で、候補は新しい実行（数えない）ばかり
    page1 = [{"id": 9000 + k, "run_number": 200 + k, "run_attempt": 1, "created_at": NOW.isoformat()} for k in range(100)]
    routes[r".*/workflows/pytest\.yml/runs\?.*&page=1"] = {"workflow_runs": page1}
    routes[r".*/workflows/pytest\.yml/runs\?.*&page=2"] = {"workflow_runs": runs}

    search = ctd.past_totals(FakeGh(routes), 100, 2, NOW)

    assert search.stop == "enough" and [r for r, _ in search.totals] == [1000, 1001]


def test_past_totals_zip_limit_and_broken() -> None:
    routes = history(20)
    for k in range(11):
        routes[rf".*/artifacts/{6000 + k}/zip"] = b"not a zip"

    gh = FakeGh(routes)
    search = ctd.past_totals(gh, 100, 9, NOW)

    assert search.stop == "zip_limit" and len(search.totals) == 7 and gh.calls["zips"] == 18
    assert len(search.unreadable) == 11 and "run 1000" in search.unreadable[0]


def test_past_totals_enough_on_eighteenth_zip() -> None:
    routes = history(20)
    for k in range(0, 18, 2):
        routes[rf".*/artifacts/{6000 + k}/zip"] = b"not a zip"

    gh = FakeGh(routes)
    search = ctd.past_totals(gh, 100, 9, NOW)

    assert search.stop == "enough" and len(search.totals) == 9 and gh.calls["zips"] == 18


def test_past_totals_does_not_count_invalid_or_stale_or_other_run() -> None:
    routes = history(6)
    routes[r".*/artifacts/6000/zip"] = total_zip(1000, valid=False)
    routes[r".*/artifacts/6001/zip"] = total_zip(1001, attempt=2)
    routes[r".*/artifacts/6002/zip"] = total_zip(9999)

    search = ctd.past_totals(FakeGh(routes), 100, 3, NOW)

    assert [r for r, _ in search.totals] == [1003, 1004, 1005]
    assert len(search.unreadable) == 1 and "run 1002" in search.unreadable[0]


def test_past_totals_list_error() -> None:
    search = ctd.past_totals(FakeGh({}), 100, 9, NOW)

    assert search.stop == "list_error" and search.totals == [] and "実行の一覧" in search.unreadable[0]


def test_past_totals_time_limit_keeps_collected() -> None:
    routes = history(10)
    routes[r".*/artifacts/6002/zip"] = ctd.GhTimeUp("x")

    search = ctd.past_totals(FakeGh(routes), 100, 9, NOW)

    assert search.stop == "time_limit" and len(search.totals) == 2 and search.unreadable == []


def test_past_totals_retention_and_exhausted() -> None:
    routes = history(3)
    routes[r".*/workflows/pytest\.yml/runs\?.*&page=1"]["workflow_runs"][1]["created_at"] = (NOW - timedelta(days=91)).isoformat()
    assert ctd.past_totals(FakeGh(routes), 100, 9, NOW).stop == "retention"
    assert ctd.past_totals(FakeGh(history(3)), 100, 9, NOW).stop == "exhausted"


def test_past_totals_skips_runs_without_artifact() -> None:
    routes = history(4)
    arts = routes[r".*/actions/artifacts\?name=ci-test-duration-total&.*&page=1"]["artifacts"]
    del arts[0]

    gh = FakeGh(routes)
    search = ctd.past_totals(gh, 100, 2, NOW)

    assert [r for r, _ in search.totals] == [1001, 1002] and gh.calls["zips"] == 2


# --- verdict ---


def test_verdict() -> None:
    assert ctd.verdict([90, 120, 130], {"limit_seconds": 100.0}) == {
        "count": 3,
        "median": 120,
        "limit": 100.0,
        "diff": 20.0,
        "over": True,
    }
    assert ctd.verdict([100, 100], {"limit_seconds": 100.0})["over"] is False


# --- Gh ---


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_gh_timeout_is_remaining_or_call_timeout(monkeypatch) -> None:
    seen = []

    def run(cmd, capture_output, timeout):
        seen.append(timeout)
        return subprocess.CompletedProcess(cmd, 0, b"{}", b"")

    monkeypatch.setattr(ctd.subprocess, "run", run)
    clock = FakeClock(100.0)
    gh = ctd.Gh(deadline=110.0, clock=clock)

    gh.get("a", "jobs")
    clock.now = 50.0
    gh.get("b", "pages")
    ctd.Gh().get("c", "pages")

    assert seen == [10.0, ctd.CALL_TIMEOUT, ctd.CALL_TIMEOUT]
    assert gh.calls == Counter({"jobs": 1, "pages": 1})


def test_gh_time_up_without_call(monkeypatch) -> None:
    monkeypatch.setattr(ctd.subprocess, "run", lambda *a, **k: pytest.fail("gh を起動した"))
    gh = ctd.Gh(deadline=100.0, clock=FakeClock(100.0))

    with pytest.raises(ctd.GhTimeUp):
        gh.get("a", "jobs")
    assert gh.calls == Counter()


def test_gh_errors(monkeypatch) -> None:
    monkeypatch.setattr(ctd.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, b"", b"HTTP 404\nmore"))
    with pytest.raises(ctd.GhError, match="a: HTTP 404$"):
        ctd.Gh().get("a", "jobs")
    monkeypatch.setattr(ctd.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, b"<html>", b""))
    with pytest.raises(ctd.GhError, match="JSON"):
        ctd.Gh().get("a", "jobs")

    def missing(*a, **k):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(ctd.subprocess, "run", missing)
    with pytest.raises(ctd.GhUnavailable):
        ctd.Gh().get("a", "jobs")


def test_gh_raw_stops_over_limit(monkeypatch) -> None:
    killed = []

    class Popen:
        def __init__(self, cmd, stdout, stderr) -> None:
            self.stdout = io.BytesIO(b"x" * (ctd.CHUNK * 3))

        def kill(self) -> None:
            killed.append(True)

        def wait(self) -> int:
            return -9

    monkeypatch.setattr(ctd.subprocess, "Popen", Popen)

    with pytest.raises(ctd.ArtifactTooLarge) as e:
        ctd.Gh().raw("a", "zips", limit=ctd.CHUNK + 1)
    assert killed and e.value.received == ctd.CHUNK * 2


# --- render_trend と cmd_trend ---


def trend(tmp_path: Path, gh: FakeGh, *extra: str) -> tuple[int, Path]:
    out = tmp_path / "out" / "total.json"
    out.parent.mkdir(exist_ok=True)
    code = ctd.main(
        ["trend", str(tmp_path / "junit"), "--run", "7", "--run-number", "100", "--attempt", "1", "--out", str(out), *extra],
        gh=gh,
    )
    return code, out


def trend_gh(n_past: int = 20, seconds: float = 50.0) -> FakeGh:
    routes = history(n_past)
    for r in range(1000, 1000 + n_past):
        routes[rf".*/artifacts/{5000 + r}/zip"] = total_zip(r, seconds)
    routes[re.escape(JOBS_PATH % 7)] = {"jobs": [job(i) for i in range(6)]}
    return FakeGh(routes)


def annotations(out: str, title: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith(title)]


def test_trend_over_limit_warns_and_returns_zero(tmp_path, config, capsys, monkeypatch) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    full_dir(tmp_path / "junit")
    config(limit_seconds=45)

    code, out = trend(tmp_path, trend_gh(seconds=50.0))

    stdout = capsys.readouterr().out
    assert code == 0
    (warning,) = annotations(stdout, "::warning title=テストの所要::")
    assert "中央値 50.0 秒" in warning and "上限 45.0 秒" in warning and "5.0 秒超えた" in warning
    assert json.loads(out.read_text(encoding="utf-8"))["total_seconds"] == 42.0
    assert "| run | 合計の秒 |" in summary.read_text(encoding="utf-8")
    assert "GitHub API の呼び出し: jobs 1・pages 2・zips 2" in stdout


def test_trend_under_limit_is_quiet(tmp_path, config, capsys) -> None:
    full_dir(tmp_path / "junit")

    code, _ = trend(tmp_path, trend_gh(seconds=50.0))

    stdout = capsys.readouterr().out
    assert code == 0 and "::warning" not in stdout and "::notice" not in stdout
    assert "中央値 50.0 秒・上限 100.0 秒" in stdout


def test_trend_only_limit_changes_verdict(tmp_path, config, capsys) -> None:
    full_dir(tmp_path / "junit")
    trend(tmp_path, trend_gh(seconds=50.0))
    assert "::warning" not in capsys.readouterr().out

    config(limit_seconds=49)
    trend(tmp_path, trend_gh(seconds=50.0))
    assert annotations(capsys.readouterr().out, "::warning title=テストの所要::")


def test_trend_runs_one_uses_only_this_run(tmp_path, config, capsys) -> None:
    full_dir(tmp_path / "junit")
    config(runs=1, limit_seconds=40)
    gh = trend_gh()

    assert trend(tmp_path, gh)[0] == 0

    assert gh.calls["pages"] == 0 and gh.calls["zips"] == 0
    assert annotations(capsys.readouterr().out, "::warning title=テストの所要::")


def test_trend_not_enough_runs_notices(tmp_path, config, capsys) -> None:
    full_dir(tmp_path / "junit")
    config(runs=10)

    trend(tmp_path, trend_gh(n_past=3))

    stdout = capsys.readouterr().out
    (notice,) = annotations(stdout, "::notice title=テストの所要::")
    assert "4 回" in notice and "候補が尽きた" in notice
    assert "::warning" not in stdout


def test_trend_without_gh_writes_invalid_total(tmp_path, config, capsys) -> None:
    full_dir(tmp_path / "junit")

    code, out = trend(tmp_path, FakeGh({".*": ctd.GhUnavailable("gh が無い")}))

    stdout = capsys.readouterr().out
    assert code == 0 and json.loads(out.read_text(encoding="utf-8"))["valid"] is False
    assert len(annotations(stdout, "::warning title=テストの所要の読み取り::")) == 1


def test_trend_time_up_on_this_run_jobs(tmp_path, config, capsys) -> None:
    full_dir(tmp_path / "junit")

    code, out = trend(tmp_path, FakeGh({".*": ctd.GhTimeUp("x")}))

    stdout = capsys.readouterr().out
    assert code == 0 and json.loads(out.read_text(encoding="utf-8"))["reason"] == "ジョブの一覧を取れない（時間の上限）"
    assert len(annotations(stdout, "::warning title=テストの所要の読み取り::")) == 1
    assert "::notice" not in stdout


def test_trend_time_up_on_past_runs(tmp_path, config, capsys) -> None:
    full_dir(tmp_path / "junit")
    gh = trend_gh()
    gh.routes[r".*/artifacts/6001/zip"] = ctd.GhTimeUp("x")

    trend(tmp_path, gh)

    stdout = capsys.readouterr().out
    (notice,) = annotations(stdout, "::notice title=テストの所要::")
    assert "時間の上限" in notice and "::warning" not in stdout


def test_trend_time_up_after_broken_total_warns_both(tmp_path, config, capsys) -> None:
    full_dir(tmp_path / "junit")
    gh = trend_gh()
    gh.routes[r".*/artifacts/6000/zip"] = b"broken"
    gh.routes[r".*/artifacts/6001/zip"] = ctd.GhTimeUp("x")

    trend(tmp_path, gh)

    stdout = capsys.readouterr().out
    assert len(annotations(stdout, "::notice title=テストの所要::")) == 1
    (warning,) = annotations(stdout, "::warning title=テストの所要の読み取り::")
    assert "run 1000" in warning


def test_trend_config_error_returns_two(tmp_path, config) -> None:
    config(runs=0)
    full_dir(tmp_path / "junit")

    assert trend(tmp_path, trend_gh())[0] == 2


def test_trend_unwritable_out_still_zero(tmp_path, config, capsys) -> None:
    full_dir(tmp_path / "junit")
    code = ctd.main(
        ["trend", str(tmp_path / "junit"), "--run", "7", "--run-number", "100", "--attempt", "1", "--out", str(tmp_path / "no" / "x.json")],
        gh=trend_gh(),
    )

    assert code == 0 and "集計を書けない" in capsys.readouterr().out


def test_render_trend_escapes_annotation() -> None:
    current = {"run": 7, "valid": False, "reason": "100%\n壊れた", "total_seconds": None}

    _, notes, _ = ctd.render_trend(current, ["100%\n壊れた"], None, None, Counter())

    assert notes == ["::warning title=テストの所要の読み取り::読めなかったもの: 100%25%0A壊れた"]


def test_main_requires_arguments() -> None:
    with pytest.raises(SystemExit) as e:
        ctd.main(["trend"], gh=FakeGh())
    assert e.value.code == 2


# --- fetch_run・render_run・cmd_run ---


def artifact(aid: int, name: str, size: int = 100, expired: bool = False) -> dict:
    return {"id": aid, "name": name, "size_in_bytes": size, "expired": expired}


def run_routes(total: int = 6, attempt_of=lambda i: 1, conclusion_of=lambda i: "success") -> dict[str, object]:
    routes: dict[str, object] = {re.escape(JOBS_PATH % 7): {"jobs": [job(i, 1, conclusion_of(i)) for i in range(total)]}}
    arts = []
    for i in range(total):
        for k, (prefix, kind) in enumerate(((ctd.ROOT_ARTIFACT, "root"), (ctd.NDF_ARTIFACT, "ndf"))):
            aid = 100 + i * 2 + k
            arts.append(artifact(aid, f"{prefix}{i}"))
            routes[rf".*/artifacts/{aid}/zip"] = zip_of(
                {f"{prefix}{i}.xml": xml_of((f"{kind}.test_{i}", "t", 1.0 + i)), f"shard-attempt-{i}.txt": str(attempt_of(i))}
            )
    routes[r".*/runs/7/artifacts\?per_page=100"] = {"artifacts": arts}
    return routes


def run_cmd(gh: FakeGh, capsys) -> tuple[int, str, str]:
    code = ctd.main(["run", "7"], gh=gh)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_run_prints_totals_top_and_jobs(config, capsys) -> None:
    code, out, err = run_cmd(FakeGh(run_routes()), capsys)

    assert code == 0, err
    lines = out.splitlines()
    assert lines[0] == "根: 合計 21.0 秒（6 件）" and lines[1] == "NDF: 合計 21.0 秒（6 件）"
    assert lines[2] == "合計: 42.0 秒（12 件）"
    assert "遅い順の上位 3 件:" in out and "6.00 秒  NDF  ndf.test_5::t" in out
    assert "  5  success  75  20  30" in out
    assert "最も長いジョブ: 5（75 秒）" in out


def test_run_partial_and_failed(config, capsys) -> None:
    routes = run_routes(attempt_of=lambda i: 2 if i == 1 else 1, conclusion_of=lambda i: "failure" if i == 3 else "success")
    arts = routes[r".*/runs/7/artifacts\?per_page=100"]["artifacts"]
    arts[:] = [a for a in arts if a["name"] != "pytest-ndf-junit-0"]
    # 試行の番号のファイルだけで XML の無い成果物
    routes[r".*/artifacts/111/zip"] = zip_of({"shard-attempt-5.txt": "1"})

    code, out, _ = run_cmd(FakeGh(routes), capsys)

    lines = out.splitlines()
    assert code == 0
    assert lines[0] == "部分集計（欠けたジョブ: 0、5、古い試行のジョブ: 1）"
    assert lines[1] == "失敗したジョブを含む（3）"
    assert lines[2] == "根: 合計 12.0 秒（3 件）"


def test_run_without_ndf_returns_one(config, capsys) -> None:
    routes = run_routes()
    arts = routes[r".*/runs/7/artifacts\?per_page=100"]["artifacts"]
    arts[:] = [a for a in arts if not a["name"].startswith(ctd.NDF_ARTIFACT)]

    code, _, err = run_cmd(FakeGh(routes), capsys)

    assert code == 1 and err == "NDF の JUnit XML が無い\n"


def test_run_without_any_returns_one_for_both(config, capsys) -> None:
    routes = run_routes()
    routes[r".*/runs/7/artifacts\?per_page=100"] = {"artifacts": []}

    code, _, err = run_cmd(FakeGh(routes), capsys)

    assert code == 1 and len(err.splitlines()) == 2


def test_run_without_gh_returns_two(config, capsys) -> None:
    assert run_cmd(FakeGh({".*": ctd.GhUnavailable("gh が無い")}), capsys)[0] == 2


def test_run_does_not_fetch_expired_or_misnamed(config, capsys) -> None:
    routes = run_routes()
    arts = routes[r".*/runs/7/artifacts\?per_page=100"]["artifacts"]
    arts += [artifact(900, "pytest-junit-0x"), artifact(901, "pytest-junit-6"), artifact(902, "pytest-junit-0", expired=True)]
    arts += [artifact(903, "pytest-ndf-junit-2", size=ctd.ARTIFACT_LIMIT + 1)]
    gh = FakeGh(routes)

    run_cmd(gh, capsys)

    assert not [p for p in gh.paths if re.search(r"/(900|901|902|903)/zip", p)]


def test_fetch_run_writes_only_allowed_entries(tmp_path) -> None:
    routes = run_routes(total=1)
    routes[r".*/artifacts/100/zip"] = zip_of(
        {
            "../x.xml": "<a/>",
            "/tmp/x.xml": "<a/>",
            "sub/pytest-junit-0.xml": "<a/>",
            "pytest-junit-1.xml": "<a/>",
            "pytest-junit-0.xml": xml_of(("a", "t", 1.0)),
        }
    )

    ctd.fetch_run(FakeGh(routes), 7, tmp_path / "dest")

    written = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert written == [
        "dest/pytest-junit-0/pytest-junit-0.xml",
        "dest/pytest-ndf-junit-0/pytest-ndf-junit-0.xml",
        "dest/pytest-ndf-junit-0/shard-attempt-0.txt",
    ]


def test_fetch_run_skips_response_over_limit(tmp_path, monkeypatch, capsys) -> None:
    routes = run_routes(total=1)
    big = routes[r".*/artifacts/100/zip"]
    monkeypatch.setattr(ctd, "ARTIFACT_LIMIT", len(big) - 1)
    gh = FakeGh(routes)

    ctd.fetch_run(gh, 7, tmp_path)

    assert not (tmp_path / "pytest-junit-0").exists()
    assert gh.limits[0] == len(big) - 1 and gh.limits[1] == len(big) - 1
    assert "pytest-junit-0 を読み飛ばした" in capsys.readouterr().err


def test_fetch_run_counts_received_bytes(tmp_path, monkeypatch) -> None:
    routes = run_routes(total=1)
    monkeypatch.setattr(ctd, "TOTAL_LIMIT", 10_000)
    monkeypatch.setattr(ctd, "ARTIFACT_LIMIT", 10_000)
    routes[r".*/artifacts/100/zip"] = b"x" * 20_000
    gh = FakeGh(routes)

    ctd.fetch_run(gh, 7, tmp_path)

    # 1 つ目で受け取った 10,001 バイトが累積に足され、残りは 0 以下になる
    assert gh.limits == [10_000] and not (tmp_path / "pytest-ndf-junit-0").exists()


def test_fetch_run_skips_entries_over_limit(tmp_path, monkeypatch, capsys) -> None:
    routes = run_routes(total=1)
    monkeypatch.setattr(ctd, "ARTIFACT_LIMIT", 5_000)
    routes[r".*/artifacts/100/zip"] = zip_of({"pytest-junit-0.xml": "<a>" + " " * 6_000 + "</a>"})

    ctd.fetch_run(FakeGh(routes), 7, tmp_path)

    assert not (tmp_path / "pytest-junit-0").exists()
    assert "大きさの上限" in capsys.readouterr().err


def corrupt(data: bytes, offset: int, value: int) -> bytes:
    """中央ディレクトリの見出し（PK\\x01\\x02）の `offset` の 4 バイトを `value` に書き換える。"""
    at = data.index(b"PK\x01\x02") + offset
    return data[:at] + value.to_bytes(4, "little") + data[at + 4 :]


@pytest.mark.parametrize(("offset", "value"), [(16, 0xDEADBEEF), (24, 3)])
def test_fetch_run_drops_broken_zip(tmp_path, capsys, offset, value) -> None:
    routes = run_routes(total=1)
    routes[r".*/artifacts/100/zip"] = corrupt(zip_of({"pytest-junit-0.xml": xml_of(("a", "t", 1.0))}), offset, value)

    ctd.fetch_run(FakeGh(routes), 7, tmp_path)

    assert not (tmp_path / "pytest-junit-0").exists()
    assert "pytest-junit-0 を読めない（壊れた ZIP）" in capsys.readouterr().err
