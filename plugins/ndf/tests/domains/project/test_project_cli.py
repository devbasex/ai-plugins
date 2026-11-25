"""`ndf project check`・`schema` の判定と終了コード（#3569）。"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from ndf import contracts
from ndf.domains.project import analysis_record, check, cli, measure_ci, write
from ndf.domains.project.items import unknown, value_digest
from ndf.domains.records import files


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (path / "README.md").write_text("x\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "init")
    return path


def _declare(repo: Path, record: dict | None, doc: dict | None = None) -> dict:
    """設定（既定は解析の書いた値の無い新しい形）と、`record` があれば解析の記録を書く。書いた設定を返す。"""
    data = doc if doc is not None else {"version": 1, "analysis": {"written": {}}}
    (repo / ".ndf").mkdir(exist_ok=True)
    (repo / ".ndf" / "project.json").write_text(json.dumps(data), encoding="utf-8")
    if record is not None:
        _put_record(repo, record)
    return data


def _put_record(repo: Path, record: dict) -> None:
    path = files.path(repo, "project-analysis")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")


def _record(repo: Path) -> dict:
    return json.loads(files.path(repo, "project-analysis").read_text(encoding="utf-8"))


def _fresh_record(repo: Path, doc: dict | None = None) -> dict:
    g = check.git.Git(repo)
    return {
        "version": analysis_record.RECORD_VERSION,
        "analyzer": check.ANALYZER,
        "inputs": check.inputs(g),
        "branches": check.branch_state(g),
        "written_digest": analysis_record.written_digest(doc if doc is not None else {"analysis": {"written": {}}}),
    }


def test_inputs_pick_the_input_table():
    assert check.is_input("pyproject.toml")
    assert check.is_input("sub/package.json")
    assert check.is_input(".github/workflows/ci.yml")
    assert not check.is_input("a/b/package.json")
    assert not check.is_input("README.md")


def test_missing_fresh_stale_and_force(repo, capsys, last_result):
    assert cli.main(["check", "--root", str(repo)]) == 3
    assert last_result(capsys.readouterr().out)["items"][0]["kind"] == "missing"
    _declare(repo, _fresh_record(repo))
    assert cli.main(["check", "--root", str(repo)]) == 0
    assert last_result(capsys.readouterr().out)["metrics"] == {"inputs": 1}
    assert cli.main(["check", "--force", "--root", str(repo)]) == 3
    (repo / "pyproject.toml").write_text("[project]\nname = 'y'\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "change")
    assert cli.main(["check", "--root", str(repo)]) == 3
    got = last_result(capsys.readouterr().out)
    assert got["cause"]["kind"] == "not_applicable"
    assert [i["kind"] for i in got["items"]] == ["stale"]


def test_old_analyzer_and_hand_written_are_stale(repo, capsys, last_result):
    _declare(repo, None)
    assert cli.main(["check", "--root", str(repo)]) == 3
    _declare(repo, {**_fresh_record(repo), "analyzer": check.ANALYZER - 1})
    assert cli.main(["check", "--root", str(repo)]) == 3
    assert len(last_result(capsys.readouterr().out)["items"]) == 1


def test_broken_ndf_settings_are_2(repo, capsys, last_result):
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "project.json").write_text("{", encoding="utf-8")
    assert cli.main(["check", "--root", str(repo)]) == 2
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "invalid"
    (repo / ".ndf" / "project.json").write_text(json.dumps({"version": 9}), encoding="utf-8")
    assert cli.main(["check", "--root", str(repo)]) == 2


def test_outside_git_cannot_judge(tmp_path, capsys, last_result):
    assert cli.main(["check", "--root", str(tmp_path)]) == 1
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "unknown"


def test_check_reads_the_main_directory_from_a_linked_worktree(repo, tmp_path):
    _declare(repo, _fresh_record(repo))
    _git(repo, "add", ".ndf/project.json")
    _git(repo, "commit", "-qm", "decl")
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-q", str(linked), "-b", "side")
    (repo / ".ndf" / "project.json").write_text("{", encoding="utf-8")
    assert check.judge(linked, False).kind == check.BROKEN


def test_schema_prints_or_writes(tmp_path, capsys, last_result):
    assert cli.main(["schema"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert json.loads("\n".join(out[:-1]))["$id"] == "ndf-settings/project.schema.json"
    target = tmp_path / "s" / "project.schema.json"
    assert cli.main(["schema", "--out", str(target)]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["type"] == "object"


def test_unknown_subcommand_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        cli.main(["measure-all"])
    assert exc.value.code == 2


def _measure_file(tmp_path: Path, items: dict, repo: Path | None = None) -> Path:
    """測定の結果。`repo` を渡せば、入力のファイルのハッシュをそのリポジトリの今の HEAD から入れる。"""
    inputs = check.inputs(check.git.Git(repo)) if repo is not None else {}
    path = tmp_path / "measure.json"
    path.write_text(json.dumps({"analyzer": check.ANALYZER, "items": items, "inputs": inputs, "notes": ["注記"]}), encoding="utf-8")
    return path


def _answers(tmp_path: Path, answers: dict) -> Path:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(answers), encoding="utf-8")
    return path


def test_the_measure_deadline_comes_from_the_settings_unless_given(tmp_path, monkeypatch):
    seen: list[float] = []

    def fake(root, budget):
        seen.append(budget)
        raise check.CheckError("測らない")

    monkeypatch.setattr(cli, "measure_project", fake)
    monkeypatch.setattr(cli.limits, "seconds", lambda name: {"project.measure": 7.5}[name])
    args = ["measure", "--root", str(tmp_path / "repo"), "--out", str(tmp_path / "m.json")]
    assert cli.main(args) != 0
    assert cli.main([*args, "--budget", "30"]) != 0
    assert seen == [7.5, 30.0]


def test_measure_writes_outside_the_repo_only(repo, tmp_path, capsys, last_result):
    assert cli.main(["measure", "--root", str(repo), "--out", str(repo / "m.json")]) == 1
    out = tmp_path / "out" / "m.json"
    assert cli.main(["measure", "--root", str(repo), "--out", str(out), "--budget", "30"]) == 0
    got = last_result(capsys.readouterr().out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["items"]["ci"]["status"] == "unknown"  # origin が GitHub でない
    assert data["items"]["languages"]["status"] == "measured"
    assert got["metrics"]["out"] == str(out)


def test_measure_masks_secrets_before_writing(repo, tmp_path, monkeypatch, capsys):
    token = "ghp_" + "A" * 24
    data = {"elapsed_seconds": 0.1, "items": {"materials": {"status": "measured", "value": {"readme": f"key {token}"}}}}
    monkeypatch.setattr(cli, "measure_project", lambda root, budget: data)
    out = tmp_path / "out" / "m.json"
    assert cli.main(["measure", "--root", str(repo), "--out", str(out)]) == 0
    assert token not in out.read_text(encoding="utf-8") and token not in capsys.readouterr().out


def test_write_creates_then_keeps_hand_values(repo, tmp_path, capsys, last_result):
    measured = _measure_file(
        tmp_path,
        {"checks": {"status": "measured", "value": {"tools": []}}, "test": {"status": "question", "candidates": {}, "evidence": []}},
    )
    answers = _answers(tmp_path, {"test": {"value": {"suites": []}}, "nope": {"value": 1}})
    assert cli.main(["write", "--root", str(repo), "--measure", str(measured), "--answers", str(answers), "--dry-run"]) == 0
    assert not (repo / ".ndf").exists()
    out = capsys.readouterr().out
    assert f"書く（--dry-run）: {files.path(repo, 'project-analysis-ignore')}（git から外す設定）" in out
    assert cli.main(["write", "--root", str(repo), "--measure", str(measured), "--answers", str(answers)]) == 0
    got = last_result(capsys.readouterr().out)
    kinds = {r["key"]: r["kind"] for r in got["items"] if "key" in r}
    assert kinds["checks"] == "written" and kinds["test"] == "written" and kinds["nope"] == "ignored"
    decl = json.loads((repo / ".ndf" / "project.json").read_text(encoding="utf-8"))
    assert decl["checks"] == {"tools": []} and set(decl["analysis"]) == {"written"}
    assert _record(repo)["analyzer"] == check.ANALYZER
    decl["checks"] = {"tools": [{"name": "ruff", "config": "ruff.toml"}]}  # 人が書き換えた値
    (repo / ".ndf" / "project.json").write_text(json.dumps(decl), encoding="utf-8")
    assert cli.main(["write", "--root", str(repo), "--measure", str(measured), "--answers", str(answers)]) == 0
    rows = {r["key"]: r for r in last_result(capsys.readouterr().out)["items"] if "key" in r}
    assert rows["checks"]["kind"] == "mismatch"
    assert json.loads((repo / ".ndf" / "project.json").read_text(encoding="utf-8"))["checks"] == decl["checks"]


def test_write_refuses_broken_ndf_settings(repo, tmp_path):
    measured = _measure_file(tmp_path, {})
    (repo / ".ndf").mkdir()
    (repo / ".ndf" / "project.json").write_text("{", encoding="utf-8")
    assert cli.main(["write", "--root", str(repo), "--measure", str(measured)]) == 2


SECRET = "ghp_abcdefghijklmnopqrstuvwxyz"


def test_merge_item_branches():
    """現状固定（I-008）: 秘密の形・キーが無い・手の値・食い違い・前の write の値の置き換えの各経路。"""

    old = {"x": 1}
    ours = {"languages": value_digest(old)}
    unknown = {"unknown": "測れない"}
    assert write.merge_item("languages", (True, {"t": SECRET}), {"x": 2}, "測った", ours) == (
        {"unknown": write.SECRET_REASON},
        [{"kind": "secret", "key": "languages"}],
        True,
    )
    assert write.merge_item("languages", (False, None), {"t": SECRET}, "測った", {}) == (
        {"unknown": write.SECRET_REASON},
        [{"kind": "secret", "key": "languages"}, {"kind": "unknown", "key": "languages", "reason": write.SECRET_REASON}],
        True,
    )
    assert write.merge_item("languages", (False, None), {"x": 2}, "測った", {}) == (
        {"x": 2},
        [{"kind": "written", "key": "languages", "origin": "測った"}],
        True,
    )
    assert write.merge_item("languages", (True, old), {"x": 2}, "測った", {}) == (
        old,
        [{"kind": "mismatch", "key": "languages", "declared": old, "analyzed": {"x": 2}}],
        False,
    )
    assert write.merge_item("languages", (True, old), {"x": 1}, "測った", {}) == (old, [{"kind": "kept", "key": "languages"}], False)
    assert write.merge_item("languages", (True, old), unknown, "不明", {}) == (old, [{"kind": "kept", "key": "languages"}], False)
    assert write.merge_item("languages", (True, old), {"t": SECRET}, "測った", {}) == (
        old,
        [{"kind": "secret", "key": "languages"}, {"kind": "kept", "key": "languages"}],
        False,
    )
    assert write.merge_item("languages", (True, old), {"x": 2}, "判断した", ours) == (
        {"x": 2},
        [{"kind": "written", "key": "languages", "origin": "判断した"}],
        True,
    )
    assert write.merge_item("languages", (True, old), unknown, "不明", ours) == (
        old,
        [{"kind": "unknown", "key": "languages", "reason": "測れない", "kept_previous": True}],
        True,
    )


def test_row_line_formats_each_kind():
    """現状固定（I-015）: 出力の行の種類ごとの 1 行と、P 番号の当て方。"""

    lines = [
        write.row_line({"kind": "written", "key": "languages", "origin": "測った"}),
        write.row_line({"kind": "kept", "key": "test"}),
        write.row_line({"kind": "secret", "key": "ci"}),
        write.row_line({"kind": "unknown", "key": "ci", "reason": "時間切れ", "kept_previous": True}),
        write.row_line({"kind": "unknown", "key": "issues"}),
        write.row_line({"kind": "mismatch", "key": "checks", "declared": {"a": "値"}, "analyzed": [1]}),
        write.row_line({"kind": "note", "key": "notes"}),
    ]
    assert lines == [
        "  P1 languages: 測った",
        "  P2 test: 手の値を保った",
        "  P4 ci: 秘密の形の値を捨てて不明にした",
        "  P4 ci: 不明（時間切れ）（前の値を残した）",
        "  P8 issues: 不明（None）",
        '  P9 checks: 食い違い 設定 {"a": "値"} / 解析 [1]',
        "   notes: note",
    ]


# --- 現状固定: cli._ci_items（I-022。CI の部分・課題の SSOT の項目の入れ方と注記） ---

_REPO_ISSUES = {"markdown_files": 2, "templates": [], "hosts": []}


def _ci_ctx(**over) -> cli.MeasurementContext:
    ctx = {
        "gh_repo": "o/r",
        "head": "abc",
        "repo_issues": _REPO_ISSUES,
    }
    return cli.MeasurementContext(**{**ctx, **over})


def _patch_ci(monkeypatch, ci, calls: list) -> None:
    def fake_ci(tree, gh_repo, head, deadline, environ):
        calls.append(("ci", tree, gh_repo, head))
        if isinstance(ci, Exception):
            raise ci
        return ci

    monkeypatch.setattr(measure_ci, "measure", fake_ci)


def test_ci_items_current_behavior_with_ci_part(monkeypatch):
    calls: list = []
    ci_part = {
        "ci": {"status": "measured", "value": "c"},
        "notes": ["ci の注記"],
        "merges": {"develop": 3, "main": 7},
        "github_issues": 4,
        "hosts": ["redmine.example.com"],
    }
    _patch_ci(monkeypatch, ci_part, calls)
    step = cli._Steps(time.monotonic() + 60)
    step.items["instructions"] = {"status": "measured", "value": {"notes": ["指示の注記"]}}
    tree = object()
    notes = cli._ci_items(tree, step, _ci_ctx())
    assert notes == ["指示の注記", "ci の注記"]
    assert calls == [("ci", tree, "o/r", "abc")]
    assert step.items["ci"] == ci_part["ci"]
    assert "test_duration" not in step.items
    assert step.items["issues"] == measure_ci.measure_issues(_REPO_ISSUES, ci_part)


def test_ci_items_current_behavior_without_ci_part(monkeypatch):
    calls: list = []
    _patch_ci(monkeypatch, None, calls)
    step = cli._Steps(time.monotonic() + 60)
    notes = cli._ci_items(object(), step, _ci_ctx())
    assert notes == []
    assert "ci" not in step.items
    assert "test_duration" not in step.items
    assert step.items["issues"] == measure_ci.measure_issues(_REPO_ISSUES, {})


def test_ci_items_current_behavior_keeps_existing_issues(monkeypatch):
    _patch_ci(monkeypatch, None, [])
    step = cli._Steps(time.monotonic() + 60)
    step.items["issues"] = {"status": "measured", "value": "keep"}
    cli._ci_items(object(), step, _ci_ctx())
    assert step.items["issues"] == {"status": "measured", "value": "keep"}


def test_ci_items_current_behavior_past_deadline_marks_unknown(monkeypatch):
    calls: list = []
    _patch_ci(monkeypatch, {"never": True}, calls)
    step = cli._Steps(time.monotonic() - 1)
    notes = cli._ci_items(object(), step, _ci_ctx())
    assert notes == []
    assert calls == []
    assert step.items["ci"] == unknown("時間切れ")
    assert "test_duration" not in step.items
    assert step.items["issues"] == measure_ci.measure_issues(_REPO_ISSUES, {})


SUITE_SCHEMA = contracts.schema_path("ndf-settings/project") + "#/$defs/Suite"
BASE_SUITE = {"name": "pytest", "runner": "pytest", "command": "pytest"}


@pytest.mark.parametrize("codes", [["5"], 5, [1.5], [True], []])
def test_collect_exit_codes_must_be_a_list_of_integers(codes):
    # 受け入れ条件 14: 整数の並び以外は設定ファイルのスキーマの検査で落ちる
    assert contracts.schema_errors({**BASE_SUITE, "collect_command": "pytest --collect-only -q", "collect_exit_codes": codes}, SUITE_SCHEMA)


def test_collect_exit_codes_need_a_collect_command():
    assert contracts.schema_errors({**BASE_SUITE, "collect_exit_codes": [0, 5]}, SUITE_SCHEMA)
    assert (
        contracts.schema_errors({**BASE_SUITE, "collect_command": "pytest --collect-only -q", "collect_exit_codes": [0, 5]}, SUITE_SCHEMA)
        == []
    )


# --- 解析の記録（#4516）: 判定は .ndf/analysis/project.json を読み、書き出しは記録を分けて書く ---


def _measured(value) -> dict:
    return {"status": "measured", "value": value}


def _write_cli(repo: Path, measure: Path, answers: Path | None = None) -> int:
    return cli.main(["write", "--root", str(repo), "--measure", str(measure), *(["--answers", str(answers)] if answers else [])])


def _settings(repo: Path) -> dict:
    return json.loads((repo / ".ndf" / "project.json").read_text(encoding="utf-8"))


def _judged(capsys, last_result, repo: Path) -> tuple[int, list[str]]:
    code = cli.main(["check", "--root", str(repo)])
    return code, [i["reason"] for i in last_result(capsys.readouterr().out)["items"]]


def test_check_reads_the_record_of_the_main_directory_from_a_linked_worktree(repo, tmp_path):
    _declare(repo, _fresh_record(repo))
    _git(repo, "add", ".ndf/project.json")
    _git(repo, "commit", "-qm", "decl")
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-q", str(linked), "-b", "side")
    assert check.judge(linked, False).kind == check.FRESH
    files.path(repo, "project-analysis").unlink()
    assert check.judge(linked, False).kind == check.STALE


@pytest.mark.parametrize(
    "text,head",
    [
        (None, "解析の記録が無い"),
        ("{", "解析の記録が読めない: "),
        (b'{"version": "\xff"}', "解析の記録が読めない: UTF-8 として読めない"),
        ("AT", "解析の記録が形に合わない: "),
    ],
)
def test_a_missing_or_unreadable_record_is_stale(repo, capsys, last_result, text, head):
    _declare(repo, None)
    path = files.path(repo, "project-analysis")
    if text is not None:
        path.parent.mkdir(parents=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    elif text == "AT":
        path.write_text(json.dumps({**_fresh_record(repo), "at": "2026-11-15T00:00:00+00:00"}), encoding="utf-8")
    elif text is not None:
        path.write_text(text, encoding="utf-8")
    code, reasons = _judged(capsys, last_result, repo)
    assert code == 3 and len(reasons) == 1 and reasons[0].startswith(head)


def test_a_record_with_the_pending_mark_is_stale(repo, capsys, last_result):
    _declare(repo, {**_fresh_record(repo), "pending": True})
    assert _judged(capsys, last_result, repo) == (3, ["前の書き出しが終わっていない"])


OLD_ANALYSIS = {"analyzer": 3, "at": "2026-11-19T01:44:55+00:00", "inputs": {}, "branches": {"head": None, "present": []}}


@pytest.mark.parametrize(
    "doc",
    [
        {
            "version": 1,
            "languages": [{"name": "python", "frameworks": [], "files": 3}],
            "test_duration": {"measured": [{"seconds": 1.0}]},
            "ci": {"provider": "github-actions", "workflows": [], "required_checks": ["pytest"]},
            "analysis": {**OLD_ANALYSIS, "written": {}},
        },
        {"version": 1, "ci": {"provider": "github-actions", "workflows": []}, "analysis": OLD_ANALYSIS},
    ],
)
def test_an_old_settings_is_stale_not_broken(repo, capsys, last_result, doc):
    _declare(repo, _fresh_record(repo, doc), doc)
    code, reasons = _judged(capsys, last_result, repo)
    assert code == 3 and len(reasons) == 1 and reasons[0].startswith("旧い形の値が設定にある: ")


def test_the_second_write_writes_nothing_even_when_the_inputs_change(repo, tmp_path, capsys, last_result):
    measure = _measure_file(tmp_path, {"checks": _measured({"tools": []})}, repo)
    assert _write_cli(repo, measure) == 0
    lines = capsys.readouterr().out.splitlines()
    record, decl = _record(repo), _settings(repo)
    assert f"書いた: {files.path(repo, 'project-analysis')}（解析の記録。入力 1 件）" in lines
    assert not any("pyproject.toml" in line for line in lines[:-1])  # 記録は差分でなく要約で出す
    assert set(record) == {"version", "analyzer", "inputs", "branches", "written_digest"}
    assert record["written_digest"] == analysis_record.written_digest(decl)
    assert set(decl["analysis"]) == {"written"}
    assert files.path(repo, "project-analysis-ignore").read_text(encoding="utf-8") == analysis_record.IGNORE_TEXT
    assert _judged(capsys, last_result, repo)[0] == 0
    assert _write_cli(repo, measure) == 0
    assert last_result(capsys.readouterr().out)["metrics"]["written"] == 0
    (repo / "pyproject.toml").write_text("[project]\nname = 'y'\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "change")
    assert _write_cli(repo, _measure_file(tmp_path, {"checks": _measured({"tools": []})}, repo)) == 0
    written = last_result(capsys.readouterr().out)["metrics"]["files"]
    assert str(repo / ".ndf" / "project.json") not in written and str(files.path(repo, "project-analysis")) in written
    assert _judged(capsys, last_result, repo)[0] == 0


def test_restoring_only_the_settings_is_stale_but_hand_values_are_not(repo, tmp_path, capsys, last_result):
    assert _write_cli(repo, _measure_file(tmp_path, {"checks": _measured({"tools": []})}, repo)) == 0
    first = (repo / ".ndf" / "project.json").read_text(encoding="utf-8")
    assert _write_cli(repo, _measure_file(tmp_path, {"checks": _measured({"tools": [{"name": "ruff", "config": "ruff.toml"}]})}, repo)) == 0
    capsys.readouterr()
    decl = _settings(repo)
    decl["services"] = {"unknown": "人が直した"}  # analysis.written は変えない
    (repo / ".ndf" / "project.json").write_text(json.dumps(decl), encoding="utf-8")
    assert _judged(capsys, last_result, repo)[0] == 0
    (repo / ".ndf" / "project.json").write_text(first, encoding="utf-8")
    assert _judged(capsys, last_result, repo) == (3, ["設定が解析の記録と対応しない"])


def test_write_moves_an_old_settings_keeping_who_wrote_each_item(repo, tmp_path, capsys, last_result):
    languages = [{"name": "python", "frameworks": [], "files": 3}]
    doc = {
        "version": 1,
        "languages": languages,
        "test_duration": {"measured": [{"seconds": 1.0}]},
        "ci": {"provider": "github-actions", "workflows": [{"path": "a.yml", "jobs": 1}], "required_checks": ["pytest"]},
        "analysis": {**OLD_ANALYSIS, "written": {"languages": value_digest(languages)}},
    }
    _declare(repo, None, doc)
    items = {"languages": _measured([{"name": "python", "frameworks": ["django"]}]), "ci": _measured({"required_checks": ["lint"]})}
    assert _write_cli(repo, _measure_file(tmp_path, items, repo)) == 0
    rows = {r["key"]: r["kind"] for r in last_result(capsys.readouterr().out)["items"] if "key" in r}
    decl = _settings(repo)
    assert decl["languages"] == [{"name": "python", "frameworks": ["django"]}]  # 解析が書いた値は新しい値へ
    assert decl["ci"] == {"required_checks": ["pytest"]} and rows["ci"] == "mismatch"  # 人の値は旧い形のキーだけを外して残す
    assert "test_duration" not in decl and set(decl["analysis"]) == {"written"} and "ci" not in decl["analysis"]["written"]
    assert _judged(capsys, last_result, repo)[0] == 0


def test_write_moves_an_old_settings_without_written_and_required_checks(repo, tmp_path, capsys, last_result):
    _declare(repo, None, {"version": 1, "ci": {"provider": "github-actions", "workflows": []}, "analysis": OLD_ANALYSIS})
    assert _write_cli(repo, _measure_file(tmp_path, {"ci": _measured({"required_checks": ["lint"]})}, repo)) == 0
    decl = _settings(repo)
    assert decl["ci"] == {} and set(decl["analysis"]) == {"written"}


def test_write_without_a_record_replaces_only_the_values_it_wrote(repo, tmp_path, capsys, last_result):
    ours, hand = {"tools": []}, {"unknown": "人が書いた"}
    doc = {"version": 1, "checks": ours, "services": hand, "analysis": {"written": {"checks": value_digest(ours)}}}
    _declare(repo, None, doc)
    items = {"checks": _measured({"tools": [{"name": "ruff", "config": "ruff.toml"}]}), "services": _measured({"containers": []})}
    assert _write_cli(repo, _measure_file(tmp_path, items, repo)) == 0
    decl = _settings(repo)
    assert decl["checks"] == {"tools": [{"name": "ruff", "config": "ruff.toml"}]} and decl["services"] == hand


@pytest.mark.parametrize("target", ["project", "record"])
def test_a_write_that_fails_after_the_pending_mark_leaves_it_stale(repo, tmp_path, monkeypatch, capsys, last_result, target):
    assert _write_cli(repo, _measure_file(tmp_path, {"checks": _measured({"tools": []})}, repo)) == 0
    changed = _measure_file(tmp_path, {"checks": _measured({"tools": [{"name": "ruff", "config": "ruff.toml"}]})}, repo)
    real = cli.signals.replace_text

    def failing(path, text):
        final_record = path == files.path(repo, "project-analysis") and '"pending"' not in text
        if (target == "project" and path.name == "project.json" and path.parent.name == ".ndf") or (target == "record" and final_record):
            raise OSError("書けない")
        return real(path, text)

    monkeypatch.setattr(cli.signals, "replace_text", failing)
    assert _write_cli(repo, changed) == 1
    monkeypatch.setattr(cli.signals, "replace_text", real)
    capsys.readouterr()
    assert _judged(capsys, last_result, repo) == (3, ["前の書き出しが終わっていない"])
    assert _write_cli(repo, changed) == 0
    assert "pending" not in _record(repo)
    capsys.readouterr()
    assert _judged(capsys, last_result, repo)[0] == 0


def test_changing_only_the_answers_leaves_no_pending_mark(repo, tmp_path, capsys, last_result):
    measure = _measure_file(tmp_path, {"test": {"status": "question", "candidates": {}, "evidence": []}}, repo)
    assert _write_cli(repo, measure, _answers(tmp_path, {"test": {"value": {"suites": []}}})) == 0
    record = _record(repo)
    suites = {"suites": [{"name": "pytest", "runner": "pytest", "command": "pytest"}]}
    assert _write_cli(repo, measure, _answers(tmp_path, {"test": {"value": suites}})) == 0
    capsys.readouterr()
    assert _settings(repo)["test"] == suites
    assert {k: v for k, v in _record(repo).items() if k != "written_digest"} == {k: v for k, v in record.items() if k != "written_digest"}
    assert "pending" not in _record(repo)
    assert _judged(capsys, last_result, repo)[0] == 0
