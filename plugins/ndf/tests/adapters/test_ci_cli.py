"""サブコマンド `ndf ci watch`（`ndf.ci_cli`）の引数・GitHub への接続・標準出力のコントラクト。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from githubkit import GitHub as Client

from ndf import ci_cli
from ndf.adapters import github
from ndf.contracts import causes

from ._ci_helpers import LOG, run
from ._github_helpers import offline_client

HEAD = "2bb66a6c14a618cbac108b61737ad49cfb1ac48b"
REPO = "devbasex/ai-plugins"


PULL = {
    "number": 7,
    "state": "open",
    "merged": False,
    "body": "",
    "mergeable_state": "clean",
    "head": {"sha": HEAD, "ref": "x"},
    "base": {"ref": "develop"},
    "user": {"login": "octocat"},
    "author_association": "OWNER",
}


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    runs: list[dict],
    log: str = "",
    log_status: int = 200,
    calls: list[str] | None = None,
    job_status: int = 200,
) -> None:
    # パスの末尾 → 応答を作る関数（ジョブはどれもワークフローの実行 9、実行は pytest.yml）
    responses = {
        "/pulls/7": lambda: httpx.Response(200, json=PULL),
        "/check-runs": lambda: httpx.Response(200, json={"total_count": len(runs), "check_runs": runs}),
        "/logs": lambda: httpx.Response(log_status, text=log, headers={"content-type": "text/plain"}),
        "/actions/runs/9/artifacts": lambda: httpx.Response(200, json={"total_count": 0, "artifacts": []}),
        "/actions/runs/9": lambda: httpx.Response(200, json={"id": 9, "path": ".github/workflows/pytest.yml@refs/pull/7/merge"}),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if calls is not None:
            calls.append(path)
        if "/actions/jobs/" in path and not path.endswith("/logs"):
            return httpx.Response(job_status, json={"run_id": 9} if job_status == 200 else {"message": "no"})
        respond = next((r for tail, r in responses.items() if path.endswith(tail)), None)
        return respond() if respond else httpx.Response(404, json={"message": "not found"})

    api = offline_client(handler)
    monkeypatch.setattr(github, "client", lambda **_: api)


@pytest.fixture(autouse=True)
def _work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """設定ファイル（pytest の suite が pytest.yml に当たる）を置いた作業場所へ移る。リポジトリの設定を読まない。"""
    suite = {"name": "pytest", "runner": "pytest", "command": "pytest", "ci_jobs": [".github/workflows/pytest.yml#pytest"]}
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "project.json").write_text(json.dumps({"test": {"suites": [suite]}}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


ARGS = ["watch", "--pr", "7", "--repo", REPO, "--required", "pytest", "--required", "doc-lint", "--test", "pytest", "--budget", "60"]


def test_cli_watch_test_failure_lists_test_files(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]
) -> None:
    runs = [run("pytest (0/2)", conclusion="failure", rid=41), run("pytest", status="in_progress", rid=42), run("doc-lint", rid=43)]
    _serve(monkeypatch, runs, log=LOG)
    code = ci_cli.main(ARGS)
    out = last_result(capsys.readouterr().out)
    assert code == causes.exit_code("test_failed")
    assert (out["status"], out["cause"]["kind"], out["cause"]["instance"]) == ("stopped", "test_failed", HEAD)
    assert out["metrics"]["test_files"] == ["tests/test_other.py", "tests/test_stage_check.py"]
    assert out["items"][0] == {"kind": "check", "name": "pytest (0/2)", "result": "failure", "job": 41, "test": True}


def test_cli_watch_keeps_test_failure_when_logs_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]
) -> None:
    # ログを読めない（権限・Actions 以外のチェック）ときも、test_failed の分類と落ちたチェックの一覧は残し、理由を記録に置く
    runs = [run("pytest (0/2)", conclusion="failure", rid=41), run("pytest", rid=42), run("doc-lint", rid=43)]
    _serve(monkeypatch, runs, log="forbidden", log_status=403)
    assert ci_cli.main(ARGS) == causes.exit_code("test_failed")
    out = last_result(capsys.readouterr().out)
    assert out["cause"]["kind"] == "test_failed"
    assert (out["metrics"]["failed_tests"], out["metrics"]["test_files"]) == ([], [])
    (read,) = out["metrics"]["failed_test_reads"]
    assert (read["check"], read["run_id"], read["runner"], read["source"]) == ("pytest (0/2)", 9, "pytest", "none")
    assert "403" in read["reason"]
    assert "log_error" not in out["metrics"] and "junit_artifacts" not in out["metrics"]
    assert out["items"][0]["name"] == "pytest (0/2)"


def test_cli_watch_puts_log_error_only_when_the_job_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]
) -> None:
    runs = [run("pytest (0/2)", conclusion="failure", rid=41), run("pytest", rid=42), run("doc-lint", rid=43)]
    _serve(monkeypatch, runs, log=LOG, job_status=404)
    assert ci_cli.main(ARGS) == causes.exit_code("test_failed")
    out = last_result(capsys.readouterr().out)
    assert "404" in out["metrics"]["log_error"]
    assert out["metrics"]["failed_test_reads"][0]["source"] == "none"
    assert out["metrics"]["failed_test_reads"][0]["run_id"] is None


def test_cli_watch_carries_the_declared_junit_glob(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    last_result: Callable[[str], dict],
    _work: Path,
) -> None:
    # 設定に glob があれば結果に写す。成果物が無ければログ（pytest のパーサー）で読む
    suite = {"name": "pytest", "runner": "pytest", "command": "pytest", "ci_jobs": [".github/workflows/pytest.yml"]}
    decl = {"test": {"ci": {"junit_artifacts": "junit-*"}, "suites": [suite]}}
    (_work / ".ndf" / "project.json").write_text(json.dumps(decl), encoding="utf-8")
    runs = [run("pytest (0/2)", conclusion="failure", rid=41), run("pytest", rid=42), run("doc-lint", rid=43)]
    _serve(monkeypatch, runs, log=LOG)
    assert ci_cli.main(ARGS) == causes.exit_code("test_failed")
    out = last_result(capsys.readouterr().out)
    assert out["metrics"]["junit_artifacts"] == "junit-*"
    (read,) = out["metrics"]["failed_test_reads"]
    assert read["source"] == "log" and read["found"] == 3
    assert "junit-*" in read["reason"]


def test_cli_watch_passes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]) -> None:
    _serve(monkeypatch, [run("pytest", rid=1), run("doc-lint", rid=2)])
    assert ci_cli.main(ARGS) == 0
    assert last_result(capsys.readouterr().out)["status"] == "ok"


def test_cli_watch_after_merge_with_failed_shard(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]
) -> None:
    _serve(monkeypatch, [run("pytest", rid=1), run("doc-lint", rid=2)])
    assert ci_cli.main([*ARGS, "--after-merge", "--merge-failed", "pytest (1/2)"]) == causes.exit_code("test_failed")
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "test_failed"


def test_cli_watch_after_merge_with_failed_test_does_not_wait_for_pending(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]
) -> None:
    # マージの段階がテストの落ちを読んでいれば、まとめのチェックが終わっていなくても読み直さずに返す
    calls: list[str] = []
    _serve(monkeypatch, [run("pytest", status="in_progress", rid=1), run("doc-lint", rid=2)], calls=calls)
    assert ci_cli.main([*ARGS, "--after-merge", "--merge-failed", "pytest (1/2)"]) == causes.exit_code("test_failed")
    out = last_result(capsys.readouterr().out)
    assert (out["cause"]["kind"], out["metrics"]["rounds"]) == ("test_failed", 1)
    assert sum(p.endswith("/check-runs") for p in calls) == 1


def test_cli_watch_after_merge_without_failures_is_not_ci(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]
) -> None:
    _serve(monkeypatch, [run("pytest", rid=1), run("doc-lint", rid=2)])
    assert ci_cli.main([*ARGS, "--after-merge"]) == causes.exit_code("not_ci")
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "not_ci"


def test_cli_watch_rate_limit_is_infra_wait(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]
) -> None:
    def limited(**_) -> Client:
        headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"}
        handler = lambda request: httpx.Response(403, json={"message": "rate"}, headers=headers)  # noqa: E731
        return offline_client(handler)

    monkeypatch.setattr(github, "client", limited)
    assert ci_cli.main(ARGS) == causes.exit_code("infra_wait")
    out = last_result(capsys.readouterr().out)
    assert out["cause"]["kind"] == "infra_wait" and out["cause"]["retry_after"] > 0


def test_cli_watch_lists_missing_and_stale_checks_with_test_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]
) -> None:
    # 現れない必須のチェック（missing）と、結論の無いまま終わったチェック（stale）を、テストのチェックかどうかとともに items へ並べる
    done_at = "2026-11-12T13:33:00Z"
    runs = [
        run("CI / pytest (0/2)", status="in_progress", completed_at=done_at, rid=1),
        run("W / lint", status="in_progress", completed_at=done_at, rid=2),
    ]
    _serve(monkeypatch, runs)
    argv = ["watch", "--pr", "7", "--repo", REPO, "--test", "pytest", "--budget", "60"]
    argv += [a for name in ("pytest", "lint", "pytest (1/2)", "doc-lint") for a in ("--required", name)]
    code = ci_cli.main(argv)
    out = last_result(capsys.readouterr().out)
    assert (code, out["status"], out["cause"]["kind"]) == (causes.exit_code("precondition"), "stopped", "precondition")
    assert out["items"] == [
        {"kind": "check", "name": "doc-lint", "result": "missing", "test": False},
        {"kind": "check", "name": "pytest (1/2)", "result": "missing", "test": True},
        {"kind": "check", "name": "CI / pytest (0/2)", "result": "stale", "test": True},
        {"kind": "check", "name": "W / lint", "result": "stale", "test": False},
    ]


def test_cli_items_marks_missing_and_stale_test_checks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result: Callable[[str], dict]
) -> None:
    # 現状固定: missing・stale の行の test は、分割ジョブを含むテストのチェック（--test "pytest (0/2)"）かで決まる。
    # 以前は Reading（missing=["CI / pytest (1/2)", "doc-lint"]・stale=["pytest", "W / lint (0/2)"]）を作って
    # ci_cli._items を直接呼んでいた。公開のエントリポイントでは missing の名前は必須の検証のキー（前置きを外した名前）で、
    # missing も stale も名前の順（大文字が先）に並ぶ
    done_at = "2026-11-12T13:33:00Z"
    runs = [
        run("pytest", status="in_progress", completed_at=done_at, rid=1),
        run("W / lint (0/2)", status="in_progress", completed_at=done_at, rid=2),
    ]
    _serve(monkeypatch, runs)
    argv = ["watch", "--pr", "7", "--repo", REPO, "--test", "pytest (0/2)", "--budget", "60"]
    argv += [a for name in ("CI / pytest (1/2)", "doc-lint", "pytest", "W / lint (0/2)") for a in ("--required", name)]
    code = ci_cli.main(argv)
    out = last_result(capsys.readouterr().out)
    assert (code, out["cause"]["kind"]) == (causes.exit_code("precondition"), "precondition")
    assert out["items"] == [
        {"kind": "check", "name": "doc-lint", "result": "missing", "test": False},
        {"kind": "check", "name": "pytest (1/2)", "result": "missing", "test": True},
        {"kind": "check", "name": "W / lint (0/2)", "result": "stale", "test": False},
        {"kind": "check", "name": "pytest", "result": "stale", "test": True},
    ]


NEW = "3cc77b7d25b729dcbc219848c38a57c98bd2c59c"


class _Hub:
    """PR の HEAD を読むたびに `heads` の順で返し（尽きたら最後のまま）、コミットごとのチェックを返す偽の GitHub。"""

    def __init__(self, heads: list[str], runs: dict[str, list[dict]]) -> None:
        self.heads, self.runs, self.read_shas = heads, runs, []

    def pull(self, number: int) -> github.Pull:
        sha = self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]
        return _pull(sha)

    def check_runs(self, sha: str) -> list[dict]:
        self.read_shas.append(sha)
        return self.runs[sha]


def _pull(sha: str) -> github.Pull:
    return github.Pull(7, "open", False, sha, "x", "develop", "", "clean", "octocat", "OWNER")


def test_watch_waits_until_the_pull_reaches_the_pushed_head() -> None:
    """push の直後に PR が前のコミット（HEAD）を指す間は、前のコミットの結果を読まず、結論を出さずに読み直す。"""
    hub = _Hub([HEAD, NEW], {HEAD: [run("pytest", conclusion="failure")], NEW: [run("pytest")]})
    reader = ci_cli.HeadReader(hub, _pull(HEAD), NEW, ["pytest"], ["pytest"])
    first = reader()
    assert first.verdict == "pending" and first.running == [ci_cli.BEHIND.format(sha=NEW[:12])]
    assert reader().verdict == "passed"
    assert hub.read_shas == [NEW] and reader.pull.head_sha == NEW


def test_watch_reads_the_pull_head_outside_its_branch() -> None:
    """作業場所の HEAD が分からない（PR のブランチの外で打った）ときは、PR の HEAD をそのまま読む。"""
    hub = _Hub([NEW], {HEAD: [run("pytest", conclusion="failure")]})
    assert ci_cli.HeadReader(hub, _pull(HEAD), None, ["pytest"], ["pytest"])().verdict == "test_failed"
    assert hub.read_shas == [HEAD]


def test_local_head_only_on_the_pull_branch(tmp_path: Path) -> None:
    from ndf.adapters import git

    g = git.Git(tmp_path)
    for args in (
        ("init", "-q", "-b", "x"),
        ("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "c"),
    ):
        g.run(*args)
    assert ci_cli.local_head("x", g) == g.rev_parse("HEAD")
    assert ci_cli.local_head("y", g) is None
    assert ci_cli.local_head("x", git.Git(tmp_path / "none")) is None
