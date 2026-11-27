"""サブコマンド `ndf records` の結果 JSON（表の 1 行・閉じる条件・引継ぎ文書）。"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import git_sandbox
import pytest

from ndf import cli
from ndf.contracts import result
from ndf.domains.records import cli as records_cli
from ndf.domains.records import mvv


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    root = tmp_path / "r"
    git_sandbox.sh(tmp_path, "init", "-q", str(root))
    return root


def _run(capsys: pytest.CaptureFixture[str], last_result, *argv: str) -> tuple[int, dict]:
    code = cli.main(["records", *argv])
    got = last_result(capsys.readouterr().out)
    assert result.errors(got, code) == []
    return code, got


def test_records_is_in_the_command_table() -> None:
    assert cli.tool_of(records_cli.__name__) == "records"


def test_close_check_refuses_an_issue_with_a_failed_verification(repo: Path, capsys, last_result) -> None:
    rows = repo / "verification.json"
    record = {"version": "1.2.3", "issues": [{"number": 7, "result": "failed", "evidence": "落ちた"}]}
    rows.write_text(json.dumps(record), encoding="utf-8")
    code, got = _run(capsys, last_result, "--cwd", str(repo), "close-check", "7", "--verification", str(rows))
    assert code != 0 and got["status"] == "stopped" and got["items"][0]["closable"] is False
    code, got = _run(capsys, last_result, "--cwd", str(repo), "close-check", "8", "--verification", str(rows))
    assert code == 0 and got["items"][0]["closable"] is True
    rows.write_text(json.dumps([{"issue": 7, "result": "failed"}]), encoding="utf-8")
    code, got = _run(capsys, last_result, "--cwd", str(repo), "close-check", "7", "--verification", str(rows))
    assert code == 3 and got["cause"]["kind"] == "precondition"


def test_close_check_leaves_a_not_run_issue_to_a_person(repo: Path, capsys, last_result) -> None:
    rows = repo / "verification.json"
    issues = [
        {"number": 7, "result": "not_run", "evidence": "次のリリースの後"},
        {"number": 8, "result": "not_run", "evidence": "次のリリースの後"},
        {"number": 8, "result": "failed", "evidence": "落ちた"},
        {"number": 9, "result": "passed", "evidence": "通った"},
    ]
    rows.write_text(json.dumps({"version": "1.2.3", "issues": issues}), encoding="utf-8")
    verification = ("--verification", str(rows))

    def close_check(issue: str, *args: str):
        return _run(capsys, last_result, "--cwd", str(repo), "close-check", issue, *args)

    code, got = close_check("7", *verification, "--require")
    assert code == 10
    assert got["status"] == "gate" and got["routes"] == {"approved": None, "rejected": None}
    assert got["items"] == [{"issue": 7, "closable": False, "reason": "保留（not_run）の行が 1 件ある。閉じるかを人が決める"}]
    assert got["summary"].startswith("#7 を閉じるかは人が決める: ")
    code, got = close_check("8", *verification)
    assert code == 1
    assert got["cause"]["kind"] == "refused" and "routes" not in got
    code, got = close_check("9", *verification)
    assert code == 0
    assert got["status"] == "ok" and got["items"][0]["closable"] is True
    code, got = close_check("9", "--require")
    assert code == 1
    assert got["cause"]["kind"] == "refused"


# 現状固定（I-010 の前）: `ndf records close-check`（cli._close_check）の記録の読み込みと検査
def _close_check(capsys, last_result, repo: Path, *argv: str) -> tuple[int, dict]:
    return _run(capsys, last_result, "--cwd", str(repo), "close-check", *argv)


def test_close_check_without_a_record_closes_and_names_the_skipped_condition(repo: Path, capsys, last_result) -> None:
    code, got = _close_check(capsys, last_result, repo, "5")
    assert code == 0 and got["status"] == "ok"
    assert got["summary"] == "#5 を閉じてよい: リリース後テストの記録が無い（条件 2 を見ない）"
    assert got["items"] == [{"issue": 5, "closable": True, "reason": "リリース後テストの記録が無い（条件 2 を見ない）"}]
    code, got = _close_check(capsys, last_result, repo, "5", "--require")
    assert code == 1 and got["cause"]["kind"] == "refused"
    assert got["summary"] == "#5 を閉じない: リリース後テストの記録が無い"
    assert got["items"] == [{"issue": 5, "closable": False, "reason": "リリース後テストの記録が無い"}]


@pytest.mark.parametrize("content", [None, "{壊れた", ""])
def test_close_check_stops_when_the_record_cannot_be_read(repo: Path, capsys, last_result, content) -> None:
    path = repo / "verification.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    code, got = _close_check(capsys, last_result, repo, "7", "--verification", str(path))
    assert code == 3 and got["status"] == "stopped" and got["cause"]["kind"] == "precondition"
    assert got["summary"].startswith("リリース後テストの記録を読めない: ")
    assert got["items"] == []


def test_close_check_reads_the_record_path_as_given(
    repo: Path, capsys, last_result, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 相対パスはカレントディレクトリから解く（--cwd の根からではない）
    record = {"version": "1.2.3", "issues": [{"number": 7, "result": "failed", "evidence": "落ちた"}]}
    (repo / "verification.json").write_text(json.dumps(record), encoding="utf-8")
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    code, got = _close_check(capsys, last_result, repo, "7", "--verification", "verification.json")
    assert code == 3 and got["summary"].startswith("リリース後テストの記録を読めない: ")
    monkeypatch.chdir(repo)
    code, got = _close_check(capsys, last_result, repo, "7", "--verification", "verification.json")
    assert code == 1 and got["cause"]["kind"] == "refused"


@pytest.mark.parametrize(
    "record",
    [
        [{"number": 7, "result": "failed", "evidence": "落ちた"}],
        {"version": "1.2.3", "issues": []},
        # version は 1 行のリリースの識別子（#4513。バージョン番号の形でない値は受け、改行を含む値は断る）
        {"version": "1.2.3\n1.2.4", "issues": [{"number": 7, "result": "passed", "evidence": "通った"}]},
        {"version": "1.2.3", "issues": [{"number": 7, "result": "skipped", "evidence": "x"}]},
        {"version": "1.2.3"},
        "null",
    ],
)
def test_close_check_stops_on_a_record_of_the_wrong_shape(repo: Path, capsys, last_result, record) -> None:
    path = repo / "verification.json"
    path.write_text(record if isinstance(record, str) else json.dumps(record), encoding="utf-8")
    code, got = _close_check(capsys, last_result, repo, "7", "--verification", str(path))
    assert code == 3 and got["status"] == "stopped" and got["cause"]["kind"] == "precondition"
    assert got["summary"].startswith("リリース後テストの記録の形が違う: ")
    assert got["items"] == []


def test_close_check_judges_only_the_rows_of_the_issue(repo: Path, capsys, last_result) -> None:
    path = repo / "verification.json"
    issues = [
        {"number": 7, "result": "failed", "evidence": "落ちた"},
        {"number": 7, "result": "failed", "evidence": "また落ちた"},
        {"number": 9, "result": "passed", "evidence": "通った"},
    ]
    path.write_text(json.dumps({"version": "1.2.3", "issues": issues}), encoding="utf-8")
    code, got = _close_check(capsys, last_result, repo, "7", "--verification", str(path), "--require")
    assert code == 1 and got["summary"] == "#7 を閉じない: リリース後テストに不合格の行が 2 件ある"
    code, got = _close_check(capsys, last_result, repo, "9", "--verification", str(path), "--require")
    assert code == 0 and got["summary"] == "#9 を閉じてよい: リリース後テストに不合格と保留の行が無い"
    code, got = _close_check(capsys, last_result, repo, "8", "--verification", str(path), "--require")
    assert code == 0 and got["items"] == [{"issue": 8, "closable": True, "reason": "リリース後テストに不合格と保留の行が無い"}]


def _handoff_body(now: str) -> str:
    """必須の 5 セクションを持つ引継ぎ文書の本体。`now` は「現在地」の本文。"""
    return (
        "## 進め方（利用者の指示）\n\n- pace: auto\n\n## 課題の順\n\n- #1\n\n"
        f"## 現在地\n\n{now}\n\n## 次にやること\n\n1. 続ける\n\n## 次に実行するコマンド\n\n- 続きから\n"
    )


def test_handoff_check_reports_missing_paths(repo: Path, capsys, last_result) -> None:
    doc = repo / "handoff.md"
    text = _handoff_body("`docs/none.md` を作成済み。")
    doc.write_text(text, encoding="utf-8")
    code, got = _run(capsys, last_result, "--cwd", str(repo), "handoff", "check", str(doc))
    assert code != 0 and {"missing_path": "docs/none.md"} in got["items"]
    (repo / "docs").mkdir()
    (repo / "docs" / "none.md").write_text("x", encoding="utf-8")
    code, got = _run(capsys, last_result, "--cwd", str(repo), "handoff", "check", str(doc))
    assert code == 0 and got["metrics"]["lines"] == len(text.splitlines())


def test_handoff_check_keeps_a_dated_heading_and_reports_missing_and_reversed_sections(repo: Path, capsys, last_result) -> None:
    doc = repo / "handoff.md"
    doc.write_text(_handoff_body("- 本番").replace("## 現在地", "## 現在地（2026-11-19）"), encoding="utf-8")
    code, got = _run(capsys, last_result, "--cwd", str(repo), "handoff", "check", "handoff.md", "--trim", "--history", "h.md")
    assert code == 0 and got["items"] == [] and not (repo / "h.md").exists()
    doc.write_text("## 次にやること\n\n1. 続ける\n\n## 現在地\n\n- 本番\n", encoding="utf-8")
    code, got = _run(capsys, last_result, "--cwd", str(repo), "handoff", "check", "handoff.md")
    assert code == 1 and got["cause"]["kind"] == "check_failed"
    assert got["items"] == [
        {"violation": "必須のセクションが無い: 進め方（利用者の指示）"},
        {"violation": "必須のセクションが無い: 課題の順"},
        {"violation": "必須のセクションが無い: 次に実行するコマンド"},
        {"violation": "セクションの順が逆: 現在地 が 次にやること より前にある"},
    ]


def test_handoff_check_reads_relative_paths_from_the_target_repo(
    repo: Path, capsys, last_result, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / "handoff.md").write_text(_handoff_body("- 対象") + "\n## 古いセクション\n\n- 移す\n", encoding="utf-8")
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / "handoff.md").write_text("## 現在地\n\n- 呼び出し元\n", encoding="utf-8")
    monkeypatch.chdir(caller)
    code, got = _run(capsys, last_result, "--cwd", str(repo), "handoff", "check", "handoff.md", "--trim", "--history", "h.md")
    assert code == 0 and {"moved": "## 古いセクション"} in got["items"]
    assert (repo / "h.md").is_file() and not (caller / "h.md").exists()
    assert (caller / "handoff.md").read_text(encoding="utf-8") == "## 現在地\n\n- 呼び出し元\n"


def test_usage_stop_never_fails_the_hook(repo: Path, capsys, last_result, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("壊れた入力"))
    code, got = _run(capsys, last_result, "--cwd", str(repo), "usage", "stop")
    assert code == 0 and got["items"] == []


# 現状固定（I-017 の前）: `ndf records mvv signals / answer`（cli._mvv）の結果 JSON
@pytest.fixture
def mvv_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))
    origin, main = tmp_path / "origin.git", tmp_path / "main"
    git_sandbox.sh(tmp_path, "init", "-q", "--bare", str(origin))
    git_sandbox.sh(tmp_path, "init", "-q", "-b", "main", str(main))
    (main / ".ndf").mkdir()
    decl = {"body": "mvv.md", "versions": [{"version": 1, "sha256": "s"}], "settings": {"revise_after": {"overrides": 1}}}
    (main / ".ndf" / "mvv.json").write_text(json.dumps(decl), encoding="utf-8")
    git_sandbox.sh(main, "add", "-A")
    git_sandbox.sh(main, "commit", "-q", "-m", "main")
    git_sandbox.sh(main, "remote", "add", "origin", str(origin))
    git_sandbox.sh(main, "push", "-q", "origin", "main")
    git_sandbox.sh(main, "fetch", "-q", "origin")
    return main


def _answer(monkeypatch: pytest.MonkeyPatch, capsys, last_result, root: Path, stdin: str, *argv: str) -> tuple[int, dict]:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return _run(capsys, last_result, "--cwd", str(root), "mvv", "answer", *argv)


def test_mvv_answer_refuses_a_non_object_input(mvv_repo: Path, capsys, last_result, monkeypatch: pytest.MonkeyPatch) -> None:
    for stdin in ("壊れた", "[1]", ""):
        code, got = _answer(monkeypatch, capsys, last_result, mvv_repo, stdin)
        assert code != 0 and got["cause"]["kind"] == "invalid" and got["items"] == []
        assert got["summary"] == "承認ゲートの回答（標準入力の JSON object）が無い"


def test_mvv_answer_writes_only_an_override_and_signals_count_it(
    mvv_repo: Path, capsys, last_result, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, got = _answer(monkeypatch, capsys, last_result, mvv_repo, json.dumps({"verdict": "follow", "approved": True}))
    assert code == 0 and got["status"] == "ok" and got["summary"] == "覆しではないので書かなかった" and got["items"] == []
    code, got = _run(capsys, last_result, "--cwd", str(mvv_repo), "mvv", "signals", "--base", "main")
    assert code == 0 and got["summary"] == "MVV の改訂の兆候は閾値に届かない" and got["metrics"] == {"revision_due": False}
    assert got["items"] == [{"mvv_version": 1, "overrides": 0, "unknown_pass": 0, "unknown": 0}]
    answer = {"verdict": "not_follow", "approved": True, "state": "s", "gate": "g", "pr": 3, "extra": "x"}
    code, got = _answer(monkeypatch, capsys, last_result, mvv_repo, json.dumps(answer), "--version", "1")
    row = got["items"][0]
    assert code == 0 and got["summary"] == f"覆しの記録: {row['kind']}"
    assert {k: row[k] for k in ("version", "mvv_version", "repo", "state", "gate", "pr")} == {
        "version": 1,
        "mvv_version": 1,
        "repo": str(mvv_repo.resolve()),
        "state": "s",
        "gate": "g",
        "pr": 3,
    }
    assert "extra" not in row
    code, got = _run(capsys, last_result, "--cwd", str(mvv_repo), "mvv", "signals", "--base", "main")
    assert code == 0 and got["summary"] == "MVV の改訂を提案する兆候がある" and got["metrics"] == {"revision_due": True}
    assert got["items"] == [{"mvv_version": 1, "overrides": 1, "unknown_pass": 0, "unknown": 0}]


def test_mvv_signals_stops_when_the_base_branch_has_no_ndf_settings(mvv_repo: Path, capsys, last_result) -> None:
    code, got = _run(capsys, last_result, "--cwd", str(mvv_repo), "mvv", "signals", "--base", "none")
    assert code == 3 and got["status"] == "stopped" and got["cause"]["kind"] == "precondition"
    assert got["summary"].startswith("origin/none の MVV の設定を読めない: ")


# `ndf records mvv current`: リモートのベースブランチを取得して、承認済みの MVV を読む
NDF_DIR = ".ndf"
BODY_AT = f"{NDF_DIR}/mvv.md"
BODY = "# 見本の MVV\n\n## Mission\n\n使命\n\n## Value\n\n1. **一つ目**\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _push_mvv(repo: Path, versions: object, body: str | None = BODY) -> None:
    """`versions` を承認の記録とする MVV を、リモートの `main` へ push する。"""
    (repo / NDF_DIR / "mvv.json").write_text(json.dumps({"versions": versions}), encoding="utf-8")
    if body is not None:
        (repo / BODY_AT).write_text(body, encoding="utf-8")
    git_sandbox.sh(repo, "add", "-A")
    git_sandbox.sh(repo, "commit", "-q", "-m", "MVV")
    git_sandbox.sh(repo, "push", "-q", "origin", "main")


def _current(capsys, last_result, root: Path, *argv: str) -> tuple[int, dict]:
    return _run(capsys, last_result, "--cwd", str(root), "mvv", "current", "--base", "main", *argv)


def _assert_refused(code: int, got: dict, text: str) -> None:
    assert code == 3 and got["status"] == "stopped" and got["cause"]["kind"] == "precondition" and "outputs" not in got
    assert text in got["summary"]


def test_mvv_current_fetches_the_base_even_in_a_single_branch_clone(mvv_repo: Path, capsys, last_result) -> None:
    """別のブランチだけを clone した作業場所でも、リモートの `main` で後から承認されたバージョンを返す（#4090）。"""
    git_sandbox.sh(mvv_repo, "push", "-q", "origin", "main:work")
    _push_mvv(mvv_repo, [{"version": 1, "sha256": _sha(BODY)}])
    origin, clone = mvv_repo.parent / "origin.git", mvv_repo.parent / "clone"
    git_sandbox.sh(mvv_repo.parent, "clone", "-q", "--single-branch", "--branch", "work", origin.as_uri(), str(clone))
    for version, body in ((1, BODY), (2, BODY + "2. **二つ目**\n")):
        if version > 1:
            _push_mvv(mvv_repo, [{"version": version, "sha256": _sha(body)}], body)
        code, got = _current(capsys, last_result, clone)
        assert code == 0 and got["status"] == "ok"
        assert (got["outputs"]["mvv"]["version"], got["outputs"]["mvv"]["sha256"]) == (version, _sha(body))


def test_mvv_current_outputs_the_approved_snapshot(mvv_repo: Path, capsys, last_result) -> None:
    _push_mvv(mvv_repo, [{"version": 3, "sha256": _sha(BODY)}])
    (mvv_repo / BODY_AT).write_text("作業場所で変えた本文", encoding="utf-8")
    code, got = _current(capsys, last_result, mvv_repo)
    out = got["outputs"]["mvv"]
    assert code == 0 and got["status"] == "ok" and "warnings" not in got
    assert set(out) == {"state", "version", "sha256", "path", "principles_path", "items"}
    assert (out["state"], out["version"], out["sha256"]) == ("approved", 3, _sha(BODY))
    assert Path(out["path"]).is_absolute() and Path(out["path"]).read_text(encoding="utf-8") == BODY
    assert Path(out["principles_path"]).read_bytes() == mvv.principles()
    assert out["items"][:2] == ["Mission", "Value 1"] and "C1" in out["items"]


@pytest.mark.parametrize("state", ["absent", "unapproved"])
def test_mvv_current_of_a_base_without_an_approved_mvv_outputs_only_the_state(
    mvv_repo: Path, capsys, last_result, state: str, tmp_path: Path
) -> None:
    if state == "absent":
        git_sandbox.sh(mvv_repo, "rm", "-q", f"{NDF_DIR}/mvv.json")
        git_sandbox.sh(mvv_repo, "commit", "-q", "-m", "MVV を消す")
        git_sandbox.sh(mvv_repo, "push", "-q", "origin", "main")
    else:
        _push_mvv(mvv_repo, [], body=None)
    code, got = _current(capsys, last_result, mvv_repo)
    assert code == 0 and got["status"] == "ok" and got["outputs"] == {"mvv": {"state": state}} and "warnings" not in got
    assert not (tmp_path / "mvv-state").exists()


def test_mvv_current_refuses_a_body_changed_after_approval_unless_allowed(mvv_repo: Path, capsys, last_result, tmp_path: Path) -> None:
    """承認の後に変わった本文では止まり、`--allow-mismatch` なら本文が合わないことを警告して状態だけを返す（#4099）。"""
    _push_mvv(mvv_repo, [{"version": 1, "sha256": _sha("承認した本文")}])
    text = f"ベースの MVV の本文が承認の記録と合わない: {BODY_AT}"
    _assert_refused(*_current(capsys, last_result, mvv_repo), text)
    code, got = _current(capsys, last_result, mvv_repo, "--allow-mismatch")
    assert code == 0 and got["status"] == "ok" and got["outputs"] == {"mvv": {"state": "mismatch"}} and got["warnings"] == [text]
    assert not (tmp_path / "mvv-state").exists()


def test_mvv_current_does_not_read_the_old_base_when_the_fetch_fails(mvv_repo: Path, capsys, last_result) -> None:
    _push_mvv(mvv_repo, [{"version": 1, "sha256": _sha(BODY)}])
    git_sandbox.sh(mvv_repo, "remote", "set-url", "origin", str(mvv_repo.parent / "none.git"))
    _assert_refused(*_current(capsys, last_result, mvv_repo), "MVV を読むベースブランチを取得できない: origin の main ")


def test_mvv_current_refuses_unreadable_settings(mvv_repo: Path, capsys, last_result) -> None:
    """設定のファイルを読めないベースを、MVV の無いベースとして扱わない（#4099）。"""
    (mvv_repo / NDF_DIR / "mvv.json").write_text("{", encoding="utf-8")
    git_sandbox.sh(mvv_repo, "commit", "-qam", "壊れた設定")
    git_sandbox.sh(mvv_repo, "push", "-q", "origin", "main")
    _assert_refused(*_current(capsys, last_result, mvv_repo, "--allow-mismatch"), "の MVV の設定を読めない")
    oid = git_sandbox.sh(mvv_repo, "rev-parse", f"HEAD:{NDF_DIR}/mvv.json", strip=True)
    (mvv_repo / ".git" / "objects" / oid[:2] / oid[2:]).unlink()
    _assert_refused(*_current(capsys, last_result, mvv_repo), "")


def test_mvv_current_refuses_when_the_snapshot_cannot_be_written(mvv_repo: Path, capsys, last_result, tmp_path: Path) -> None:
    _push_mvv(mvv_repo, [{"version": 1, "sha256": _sha(BODY)}])
    (tmp_path / "mvv-state").write_text("ディレクトリでない", encoding="utf-8")
    _assert_refused(*_current(capsys, last_result, mvv_repo), str(tmp_path / "mvv-state"))
