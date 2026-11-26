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

import merge_hub

from ._ci_helpers import LOG, run
from ._github_helpers import offline_client

import scenes

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


# --- ndf ci merge --reviewed（#3879 構成要素 8: レビューした HEAD でだけマージする） ---

REVIEWED = "1" * 40


def _merge_with(monkeypatch: pytest.MonkeyPatch, head: str, merged: list[tuple[int, str]]) -> None:
    """作業場所の HEAD が `head` で、ブランチ x に開いた PR #7 を持つ偽の GitHub（マージの要求を `merged` へ足す）。"""

    class FakeGit:
        def rev_parse(self, _rev: str) -> str:
            return head

        def remote_repo(self) -> str:
            return REPO

    hub = merge_hub.MergeHub(
        pulls_of={7: merge_hub.pull(head=head, base="develop", ref="x")},
        files={"develop": (merge_hub.project_text(merge_hub.strategy(base="develop")), "c1")},
    )

    class FakeHub:
        def __new__(cls, _repo: str, _client: object) -> merge_hub.MergeHub:
            return hub

    def record(number: int, expect_head: str, method: object = None, **_: object) -> github.MergeOutcome:
        merged.append((number, expect_head))
        return github.MergeOutcome(True, None, f"#{number} をマージした", expect_head)

    hub.merge = record  # type: ignore[method-assign]

    monkeypatch.setattr(ci_cli.git, "Git", FakeGit)
    monkeypatch.setattr(ci_cli, "GitHub", FakeHub)
    monkeypatch.setattr(github, "client", lambda **_: None)


def _cli_merge_at_the_reviewed_head_or_without_it_merges_as_before(monkeypatch, capsys, last_result, reviewed):
    merged: list[tuple[int, str]] = []
    _merge_with(monkeypatch, REVIEWED, merged)
    assert ci_cli.main(["merge", "--branch", "x", *reviewed]) == 0
    assert last_result(capsys.readouterr().out)["status"] == "ok"
    assert merged == [(7, REVIEWED)]


def _cli_merge_refuses_a_head_other_than_the_reviewed_one(monkeypatch, capsys, last_result):
    merged: list[tuple[int, str]] = []
    _merge_with(monkeypatch, HEAD, merged)
    assert ci_cli.main(["merge", "--branch", "x", "--reviewed", REVIEWED]) == causes.exit_code("refused")
    found = last_result(capsys.readouterr().out)
    assert found["cause"]["kind"] == "refused" and REVIEWED[:12] in found["cause"]["detail"]
    assert merged == []


# --- ndf ci merge・ask・answer の本番チャネルの判定と承認の証跡（#4409） ---

from ndf.core import merge_approval  # noqa: E402


def _use(monkeypatch: pytest.MonkeyPatch, hub: merge_hub.MergeHub, head: str = merge_hub.HEAD) -> merge_hub.MergeHub:
    class FakeGit:
        def __init__(self, *_: object) -> None:
            pass

        def rev_parse(self, _rev: str) -> str:
            return head

        def remote_repo(self) -> str:
            return merge_hub.REPO

    class FakeHub:
        def __new__(cls, _repo: str, _client: object) -> merge_hub.MergeHub:
            return hub

    monkeypatch.setattr(ci_cli.git, "Git", FakeGit)
    monkeypatch.setattr(ci_cli, "GitHub", FakeHub)
    monkeypatch.setattr(github, "client", lambda **_: None)
    return hub


def _approve(
    hub: merge_hub.MergeHub,
    answer: str = "approved",
    head: str = merge_hub.HEAD,
    base: str = "main",
    production: bool = True,
    by: str = "user",
) -> None:
    target = merge_approval.MergeTarget(merge_hub.REPO, 7, base, head)
    body = merge_approval.render_merge_row(merge_approval.MergeRow(answer, by, target, production, f"r{len(hub.comments_of.get(7, []))}"))
    hub.comments_of.setdefault(7, []).append(github.Authored(hub.login, "OWNER", body, author_id=hub.account_id))


def _out(capsys, last_result) -> dict:
    return last_result(capsys.readouterr().out)


def _merge_to_a_non_production_base_merges_whatever_the_gate(monkeypatch, capsys, last_result, gate):
    """受け入れ条件 7: develop 宛て（本番チャネルは main）の PR は止まらずにマージする。"""
    hub = _use(
        monkeypatch,
        merge_hub.MergeHub(
            pulls_of={7: merge_hub.pull(base="develop")},
            files={"develop": (merge_hub.project_text(merge_hub.strategy(base="develop")), "c1")},
        ),
    )
    assert ci_cli.main(["merge", "--branch", "sprint/x", "--production-gate", gate]) == 0
    assert hub.merged == [7] and "comments" not in hub.calls
    assert _out(capsys, last_result)["metrics"]["verdict"] == "not_production"


def _merge_to_production_without_automatic_rows_merges(monkeypatch, capsys, last_result, deliveries):
    """受け入れ条件 8: 自動反映の行が無い本番チャネルへの PR はマージする。"""
    hub = _use(monkeypatch, merge_hub.MergeHub(files={"main": (merge_hub.project_text(merge_hub.strategy(deliveries=deliveries)), "c1")}))
    assert ci_cli.main(["merge", "--branch", "sprint/x"]) == 0
    assert hub.merged == [7]


def _merge_without_approval_stops_for_automatic_unset_and_unknown(monkeypatch, capsys, last_result, tmp_path, text):
    """受け入れ条件 4・9: 自動反映・設定が無い・読めないときは、証跡が無ければマージせず承認待ちで止まる。"""
    hub = _use(monkeypatch, merge_hub.MergeHub(files={"main": (text, "c1")}))
    out_file = tmp_path / "presented-merge.json"
    code = ci_cli.main(["merge", "--branch", "sprint/x", "--presented-out", str(out_file)])
    found = _out(capsys, last_result)
    assert code == 10 and found["status"] == "gate" and hub.merged == []
    held = json.loads(out_file.read_text(encoding="utf-8"))
    assert held["presented_id"] == found["metrics"]["presented_id"]
    assert (held["repo"], held["number"], held["base"], held["head"], held["production"]) == (
        merge_hub.REPO,
        7,
        "main",
        merge_hub.HEAD,
        True,
    )
    assert ci_cli.PRODUCTION_LINE in out_file.with_suffix(".md").read_text(encoding="utf-8")


def _merge_defers_and_stales_without_approval(monkeypatch, capsys, last_result):
    """受け入れ条件 1〜3: 工程の中は先送りで ok（マージしない）、design-merge・release-merge は approval_stale。"""
    hub = _use(monkeypatch, merge_hub.MergeHub())
    assert ci_cli.main(["merge", "--branch", "sprint/x", "--production-gate", "defer"]) == 0
    found = _out(capsys, last_result)
    assert found["metrics"]["merge"] == "deferred" and found["items"] == [{"kind": "pr", "number": 7}]
    assert ci_cli.main(["merge", "--branch", "sprint/x", "--production-gate", "stale"]) == causes.exit_code("approval_stale")
    found = _out(capsys, last_result)
    assert found["cause"]["kind"] == "approval_stale" and "routes" not in found
    assert hub.merged == []


def _merge_with_a_valid_trail_merges(monkeypatch, capsys, last_result):
    hub = _use(monkeypatch, merge_hub.MergeHub())
    _approve(hub)
    assert ci_cli.main(["merge", "--branch", "sprint/x"]) == 0
    assert hub.merged == [7] and _out(capsys, last_result)["metrics"]["approved_by"] == "user"


def _merge_ignores_stale_rejected_or_not_production_trails(monkeypatch, capsys, last_result, trail_kwargs):
    """受け入れ条件 5・決定 7: 承認の後の HEAD・マージ先の変更、却下、「本番へ反映される: いいえ」ではマージしない。"""
    hub = _use(monkeypatch, merge_hub.MergeHub())
    _approve(hub)
    if trail_kwargs.get("answer") == "rejected":
        _approve(hub, answer="rejected")
    else:
        hub.comments_of[7] = []
        _approve(hub, **trail_kwargs)
    assert ci_cli.main(["merge", "--branch", "sprint/x", "--production-gate", "defer"]) == 0
    assert hub.merged == [] and _out(capsys, last_result)["metrics"]["merge"] == "deferred"


def _merge_does_not_count_trails_of_another_login(monkeypatch, capsys, last_result):
    hub = _use(monkeypatch, merge_hub.MergeHub())
    _approve(hub)
    # 同じ名前でも、アカウントが違えば数えない
    hub.account_id = 2
    assert ci_cli.main(["merge", "--branch", "sprint/x", "--production-gate", "defer"]) == 0
    assert hub.merged == []


def _merge_without_a_login_stops_apart_from_approval_stale(monkeypatch, capsys, last_result):
    """r1-10: token の利用者のアカウント ID を読めなければ、approval_stale でなく precondition で止まり、キューから外す。"""
    hub = _use(monkeypatch, merge_hub.MergeHub(pulls_of={7: merge_hub.pull(queued=True)}))
    _approve(hub)
    hub.login, hub.account_id = "", 0
    assert ci_cli.main(["merge", "--branch", "sprint/x", "--production-gate", "stale"]) == causes.exit_code("precondition")
    assert hub.merged == [] and hub.dequeued == [7] and "アカウント ID" in _out(capsys, last_result)["cause"]["detail"]


def _merge_moved_right_before_sending_is_approval_stale(monkeypatch, capsys, last_result, gate, code):
    """r1-6: マージの直前の確かめで HEAD が動いたら、承認の対象を持つ呼び手は approval_stale、持たない呼び手は refused で止まる。"""
    hub = _use(monkeypatch, merge_hub.MergeHub(pull_at_merge=merge_hub.pull(head="f" * 40)))
    _approve(hub)
    approved = ["--approved-head", merge_hub.HEAD, "--approved-base", "main"] if gate == "stale" else []
    assert ci_cli.main(["merge", "--pr", "7", "--production-gate", gate, *approved]) == causes.exit_code(code)
    assert hub.merged == []


def _merge_reads_the_base_branch_of_the_pr_repository(monkeypatch, capsys, last_result):
    """決定 3: 別のリポジトリから読んだ設定（自動反映の行が無くても）は unknown として承認を求める。"""
    hub = _use(monkeypatch, merge_hub.MergeHub(files={"main": (merge_hub.project_text(merge_hub.strategy(deliveries="manual")), "c1")}))
    hub.file_repo = "o/other"
    assert ci_cli.main(["merge", "--branch", "sprint/x", "--production-gate", "defer"]) == 0
    assert hub.merged == [] and _out(capsys, last_result)["metrics"]["verdict"] == "unknown"


def _merge_rejudges_when_the_config_changed_before_sending(monkeypatch, capsys, last_result):
    """決定 3・8: 判定の後にマージ先の設定へ自動反映の行が足されたら、確かめの本文で判定し直して止まる。"""
    manual = merge_hub.project_text(merge_hub.strategy(deliveries="manual"))
    auto = merge_hub.project_text(merge_hub.strategy(deliveries="auto"))
    hub = _use(monkeypatch, merge_hub.MergeHub(files={"main": (manual, "c1")}, files_at_merge={"main": (auto, "c2")}))
    assert ci_cli.main(["merge", "--branch", "sprint/x", "--production-gate", "defer"]) == 0
    found = _out(capsys, last_result)
    assert hub.merged == [] and found["metrics"]["merge"] == "deferred" and found["metrics"]["verdict"] == "automatic"


def _merge_by_pr_does_nothing_for_a_merged_pr(monkeypatch, capsys, last_result):
    """決定 5: --pr の PR がマージ済みなら、設定の読み取りもマージもしない。"""
    hub = _use(monkeypatch, merge_hub.MergeHub(pulls_of={7: merge_hub.pull(merged=True)}))
    assert (
        ci_cli.main(["merge", "--pr", "7", "--production-gate", "stale", "--approved-head", merge_hub.HEAD, "--approved-base", "main"]) == 0
    )
    assert _out(capsys, last_result)["metrics"]["merge"] == "already"
    assert hub.calls == ["pulls"]


def _merge_by_pr_checks_the_approved_target_before_the_verdict(monkeypatch, capsys, last_result, head, base, files):
    """受け入れ条件 5・決定 5: 承認の時点と HEAD かマージ先が違えば、判定で承認が要らなくても approval_stale で止まる。"""
    hub = _use(monkeypatch, merge_hub.MergeHub(files=files) if files else merge_hub.MergeHub())
    _approve(hub)
    args = ["merge", "--pr", "7", "--production-gate", "stale", "--approved-head", head, "--approved-base", base]
    assert ci_cli.main(args) == causes.exit_code("approval_stale")
    assert hub.merged == [] and "branch_file" not in hub.calls


def _merge_by_pr_with_the_approved_target_merges(monkeypatch, capsys, last_result):
    hub = _use(monkeypatch, merge_hub.MergeHub())
    _approve(hub)
    args = ["merge", "--pr", "7", "--production-gate", "stale", "--approved-head", merge_hub.HEAD, "--approved-base", "main"]
    assert ci_cli.main(args) == 0
    assert hub.merged == [7]


def _merge_dequeues_a_queued_pr_that_now_needs_approval(monkeypatch, capsys, last_result):
    """決定 8: キューの待ちの打ち直しで承認が要るのに証跡が無ければ、キューから外す。"""
    hub = _use(monkeypatch, merge_hub.MergeHub(pulls_of={7: merge_hub.pull(queued=True)}))
    assert ci_cli.main(["merge", "--branch", "sprint/x", "--production-gate", "defer"]) == 0
    assert hub.dequeued == [7]


def _merge_dequeues_a_queued_pr_whose_approved_target_moved(monkeypatch, capsys, last_result):
    """承認した HEAD と違って approval_stale で止まるときも、キューに入っている PR は外す。"""
    hub = _use(monkeypatch, merge_hub.MergeHub(pulls_of={7: merge_hub.pull(queued=True)}))
    args = ["merge", "--pr", "7", "--production-gate", "stale", "--approved-head", merge_hub.OTHER, "--approved-base", "main"]
    assert ci_cli.main(args) == causes.exit_code("approval_stale")
    assert hub.merged == [] and hub.dequeued == [7]


def _ask_writes_the_presented_target_and_material(monkeypatch, capsys, last_result, tmp_path, text, production):
    """受け入れ条件 6: ndf ci ask の承認資料は、判定が automatic・unset・unknown のときだけ本番へ反映され得る行を持つ。"""
    _use(monkeypatch, merge_hub.MergeHub(files={"main": (text, "c1")}))
    out_file = tmp_path / "presented-merge-gate.json"
    assert ci_cli.main(["ask", "--branch", "sprint/x", "--presented-out", str(out_file)]) == 10
    found = _out(capsys, last_result)
    assert json.loads(out_file.read_text(encoding="utf-8"))["presented_id"] == found["metrics"]["presented_id"]
    assert (ci_cli.PRODUCTION_LINE in out_file.with_suffix(".md").read_text(encoding="utf-8")) is production


def _ask_with_a_md_out_keeps_the_target_apart_from_the_material(monkeypatch, capsys, last_result, tmp_path):
    """r1-6: `--presented-out` が `.md` でも、承認の対象を資料で上書きせず、資料のシンボリックリンクの参照先も書き換えない。"""
    _use(monkeypatch, merge_hub.MergeHub())
    out_file = tmp_path / "approval.md"
    kept = tmp_path / "kept.md"
    kept.write_text("残す\n", encoding="utf-8")
    ci_cli.material_beside(out_file).symlink_to(kept)
    assert ci_cli.main(["ask", "--branch", "sprint/x", "--presented-out", str(out_file)]) == 10
    found = _out(capsys, last_result)
    assert json.loads(out_file.read_text(encoding="utf-8"))["presented_id"] == found["metrics"]["presented_id"]
    assert found["presentation_path"] == str(ci_cli.material_beside(out_file)) != str(out_file)
    assert ci_cli.PRODUCTION_LINE in ci_cli.material_beside(out_file).read_text(encoding="utf-8")
    assert kept.read_text(encoding="utf-8") == "残す\n"


def _ask_stops_when_the_presented_file_cannot_be_written(monkeypatch, capsys, last_result, tmp_path):
    _use(monkeypatch, merge_hub.MergeHub())
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    assert ci_cli.main(["ask", "--branch", "sprint/x", "--presented-out", str(blocker / "p.json")]) == causes.exit_code("precondition")


def _asked(monkeypatch, capsys, last_result, tmp_path, hub: merge_hub.MergeHub) -> tuple[Path, str]:
    out_file = tmp_path / "presented-merge.json"
    assert ci_cli.main(["merge", "--branch", "sprint/x", "--presented-out", str(out_file)]) == 10
    return out_file, _out(capsys, last_result)["metrics"]["presented_id"]


def _record(tmp_path: Path, presented_id: str | None, by: str = "human", answer: str = "approved") -> Path:
    path = tmp_path / "answer-records" / "merge.json"
    path.parent.mkdir(exist_ok=True)
    body = {"step": "merge", "answer": answer, "by": by, "at": "t", "run": "run-1", "presented_id": presented_id}
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _answer_args(record: Path, presented: Path) -> list[str]:
    return ["answer", "--branch", "sprint/x", "--answer-record", str(record), "--presented", str(presented)]


def _answer_writes_the_trail_once_and_merge_follows_it(monkeypatch, capsys, last_result, tmp_path, answer):
    """受け入れ条件 4・5: 承認待ちの後の回答の記録から証跡を書き、承認なら続く merge がマージする。2 回打っても投稿は 1 回。"""
    hub = _use(monkeypatch, merge_hub.MergeHub())
    presented, presented_id = _asked(monkeypatch, capsys, last_result, tmp_path, hub)
    record = _record(tmp_path, presented_id, answer=answer)
    assert ci_cli.main(_answer_args(record, presented)) == 0
    assert ci_cli.main(_answer_args(record, presented)) == 0
    assert hub.calls.count("post_comment") == 1
    capsys.readouterr()
    code = ci_cli.main(["merge", "--branch", "sprint/x", "--presented-out", str(presented)])
    assert (hub.merged == [7]) is (answer == "approved")
    assert code == (0 if answer == "approved" else 10)


def _answer_refuses_without_writing(monkeypatch, capsys, last_result, tmp_path, case):
    """受け入れ条件 5: 回答の記録を経ない・利用者でない・提示と結び付かない・質問の後に変わった回答は、証跡を書かずに refused。"""
    hub = _use(monkeypatch, merge_hub.MergeHub())
    presented, presented_id = _asked(monkeypatch, capsys, last_result, tmp_path, hub)
    by = {"conductor": "conductor", "organizer": "organizer", "no-by": None}.get(case, "human")
    pid = {"other-id": "x" * 32, "no-id": None}.get(case, presented_id)
    record = _record(tmp_path, pid, by=by)  # type: ignore[arg-type]
    if case == "no-record":
        record.unlink()
    if case == "moved-head":
        hub.pulls_of[7] = merge_hub.pull(head=merge_hub.OTHER)
    if case == "moved-base":
        hub.pulls_of[7] = merge_hub.pull(base="release")
    if case == "moved-production":
        # r3-3: 質問の後にマージ先の設定が手で反映する形へ変わり、本番へ反映されるかが提示と違う
        hub.files["main"] = (merge_hub.project_text(merge_hub.strategy(deliveries="manual")), "c2")
    assert ci_cli.main(_answer_args(record, presented)) == causes.exit_code("refused")
    assert "post_comment" not in hub.calls


def _a_new_presentation_refuses_the_answer_to_the_old_one(monkeypatch, capsys, last_result, tmp_path):
    """受け入れ条件 5（提示と回答の結び付き）: 流し直しで提示が置き換わると、前の提示への回答では書かない。"""
    hub = _use(monkeypatch, merge_hub.MergeHub())
    presented, first = _asked(monkeypatch, capsys, last_result, tmp_path, hub)
    record = _record(tmp_path, first)
    hub.pulls_of[7] = merge_hub.pull(head=merge_hub.OTHER)
    _use(monkeypatch, hub, head=merge_hub.OTHER)
    _asked(monkeypatch, capsys, last_result, tmp_path, hub)
    assert ci_cli.main(_answer_args(record, presented)) == causes.exit_code("refused")
    assert "post_comment" not in hub.calls


@pytest.mark.parametrize(
    scenes.ARGS,
    [
        *[
            scenes.scene(
                _cli_merge_at_the_reviewed_head_or_without_it_merges_as_before,
                id=f"cli_merge_at_the_reviewed_head_or_without_it_merges_as_before-{label}",
                reviewed=v,
            )
            for label, v in zip(["without-reviewed", "at-reviewed-head"], [[], ["--reviewed", REVIEWED]], strict=True)
        ],
        scenes.scene(_cli_merge_refuses_a_head_other_than_the_reviewed_one, id="cli_merge_refuses_a_head_other_than_the_reviewed_one"),
        *[
            scenes.scene(
                _merge_to_a_non_production_base_merges_whatever_the_gate,
                id=scenes.label("merge_to_a_non_production_base_merges_whatever_the_gate", i, (v,)),
                gate=v,
            )
            for i, v in enumerate(["stop", "defer", "stale"])
        ],
        *[
            scenes.scene(
                _merge_to_production_without_automatic_rows_merges,
                id=f"merge_to_production_without_automatic_rows_merges-{label}",
                deliveries=v,
            )
            for label, v in zip(["manual-delivery", "no-delivery"], ["manual", []], strict=True)
        ],
        *[
            scenes.scene(
                _merge_without_approval_stops_for_automatic_unset_and_unknown,
                id=f"merge_without_approval_stops_for_automatic_unset_and_unknown-{label}",
                text=v,
            )
            for label, v in zip(
                ["no-config-file", "not-json", "no-strategy-section", "automatic", "unset", "broken-deliveries"],
                [
                    None,
                    "not json",
                    merge_hub.project_text(None),
                    merge_hub.project_text(merge_hub.strategy(deliveries="auto")),
                    merge_hub.project_text(merge_hub.strategy(deliveries=None, cd=None)),
                    merge_hub.project_text(merge_hub.strategy(deliveries="x")),
                ],
                strict=True,
            )
        ],
        scenes.scene(_merge_defers_and_stales_without_approval, id="merge_defers_and_stales_without_approval"),
        scenes.scene(_merge_with_a_valid_trail_merges, id="merge_with_a_valid_trail_merges"),
        *[
            scenes.scene(
                _merge_ignores_stale_rejected_or_not_production_trails,
                id=f"merge_ignores_stale_rejected_or_not_production_trails-{label}",
                trail_kwargs=v,
            )
            for label, v in zip(
                ["head-moved", "base-moved", "not-production", "rejected-later"],
                [
                    {"head": merge_hub.OTHER},
                    {"base": "release"},
                    {"production": False},
                    {"by": "user", "answer": "rejected"},
                ],
                strict=True,
            )
        ],
        scenes.scene(_merge_does_not_count_trails_of_another_login, id="merge_does_not_count_trails_of_another_login"),
        *[
            scenes.scene(
                _merge_moved_right_before_sending_is_approval_stale,
                id=scenes.label("merge_moved_right_before_sending_is_approval_stale", i, (g,)),
                gate=g,
                code=c,
            )
            for i, (g, c) in enumerate([("stale", "approval_stale"), ("defer", "refused")])
        ],
        scenes.scene(_merge_without_a_login_stops_apart_from_approval_stale, id="merge_without_a_login_stops_apart_from_approval_stale"),
        scenes.scene(_merge_reads_the_base_branch_of_the_pr_repository, id="merge_reads_the_base_branch_of_the_pr_repository"),
        scenes.scene(_merge_rejudges_when_the_config_changed_before_sending, id="merge_rejudges_when_the_config_changed_before_sending"),
        scenes.scene(_merge_by_pr_does_nothing_for_a_merged_pr, id="merge_by_pr_does_nothing_for_a_merged_pr"),
        *[
            scenes.scene(
                _merge_by_pr_checks_the_approved_target_before_the_verdict,
                id=f"merge_by_pr_checks_the_approved_target_before_the_verdict-{label}",
                **dict(zip(("head", "base", "files"), v)),
            )
            for label, v in zip(
                ["other-head", "empty-base", "other-base", "other-head-manual-base"],
                [
                    (merge_hub.OTHER, "main", None),
                    (merge_hub.HEAD, "", None),
                    (merge_hub.HEAD, "develop", None),
                    (merge_hub.OTHER, "main", {"main": (merge_hub.project_text(merge_hub.strategy(deliveries="manual")), "c1")}),
                ],
                strict=True,
            )
        ],
        scenes.scene(_merge_by_pr_with_the_approved_target_merges, id="merge_by_pr_with_the_approved_target_merges"),
        scenes.scene(_merge_dequeues_a_queued_pr_that_now_needs_approval, id="merge_dequeues_a_queued_pr_that_now_needs_approval"),
        scenes.scene(_merge_dequeues_a_queued_pr_whose_approved_target_moved, id="merge_dequeues_a_queued_pr_whose_approved_target_moved"),
        *[
            scenes.scene(
                _ask_writes_the_presented_target_and_material,
                id=f"ask_writes_the_presented_target_and_material-{label}",
                **dict(zip(("text", "production"), v)),
            )
            for label, v in zip(
                ["automatic", "unset", "no-config-file", "manual"],
                [
                    (merge_hub.project_text(merge_hub.strategy(deliveries="auto")), True),
                    (merge_hub.project_text(merge_hub.strategy(deliveries=None, cd=None)), True),
                    (None, True),
                    (merge_hub.project_text(merge_hub.strategy(deliveries="manual")), False),
                ],
                strict=True,
            )
        ],
        scenes.scene(
            _ask_with_a_md_out_keeps_the_target_apart_from_the_material,
            id="ask_with_a_md_out_keeps_the_target_apart_from_the_material",
        ),
        scenes.scene(_ask_stops_when_the_presented_file_cannot_be_written, id="ask_stops_when_the_presented_file_cannot_be_written"),
        *[
            scenes.scene(
                _answer_writes_the_trail_once_and_merge_follows_it,
                id=scenes.label("answer_writes_the_trail_once_and_merge_follows_it", i, (v,)),
                answer=v,
            )
            for i, v in enumerate(["approved", "rejected"])
        ],
        *[
            scenes.scene(_answer_refuses_without_writing, id=scenes.label("answer_refuses_without_writing", i, (v,)), case=v)
            for i, v in enumerate(
                ["no-record", "conductor", "organizer", "no-by", "other-id", "no-id", "moved-head", "moved-base", "moved-production"]
            )
        ],
        scenes.scene(_a_new_presentation_refuses_the_answer_to_the_old_one, id="a_new_presentation_refuses_the_answer_to_the_old_one"),
    ],
)
def test_production_merge_scenes(request, scene, values):
    """ndf ci merge・ask・answer の本番チャネルの判定と承認の証跡の場面（#4409）。"""
    scenes.play(request, scene, values)
