"""`scripts/check-required-checks.py`（突き合わせのチェック。#653）のテスト。

ワークフロー・宣言・必須の一覧を一時ディレクトリに作り、`gh` は差し替えて呼ぶ。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts" / "check-required-checks.py"

spec = importlib.util.spec_from_file_location("check_required_checks", CHECK)
crc = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(crc)

# 2026-10-05 の develop の必須のチェック 12 個
REQUIRED_12 = [
    "guard",
    "pytest",
    "runtime-smoke (claude)",
    "runtime-smoke (codex)",
    "runtime-smoke (kiro)",
    "runtime-smoke (agy)",
    "runtime-plugin-build-check",
    "skill-frontmatter-check",
    "skill-repo-assumptions-check",
    "runtime-plugin-validate",
    "markdown-link-check",
    "instruction-files-check",
]


def rules(*contexts: str) -> list[dict]:
    return [
        {"type": "deletion"},
        {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": c} for c in contexts]}},
    ]


class Repo:
    """一時ディレクトリの木。ワークフロー・宣言・必須の一覧を置いて突き合わせを走らせる。"""

    def __init__(self, root: Path):
        self.root = root
        (root / "wf").mkdir()
        self.allow({})
        self.required([])

    def workflow(self, name: str, text: str) -> None:
        (self.root / "wf" / name).write_text(text, encoding="utf-8")

    def allow(self, data: dict) -> None:
        (self.root / "allow.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def required(self, contexts: list[str] | None = None, raw: object = None) -> None:
        body = raw if raw is not None else rules(*(contexts or []))
        (self.root / "rules.json").write_text(json.dumps(body), encoding="utf-8")

    def run(self, capsys, *extra: str, rules_file: bool = True) -> tuple[int, list[str], str]:
        argv = ["--root", str(self.root), "--repo", "o/r", "--branch", "develop", "--workflows", "wf", "--allow", "allow.json"]
        if rules_file:
            argv += ["--rules-file", "rules.json"]
        code = crc.main(argv + list(extra))
        out = capsys.readouterr()
        return code, out.out.splitlines(), out.err


@pytest.fixture()
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


@pytest.fixture()
def gh_calls(monkeypatch):
    """`gh` を差し替える。返す値は `responses` に endpoint の接頭辞 → (終了コード, 標準出力, 標準エラー) で置く。"""
    calls: list[list[str]] = []
    responses: dict[str, object] = {}

    def fake_run(cmd, **kw):
        calls.append(cmd)
        endpoint = cmd[-1]
        for prefix, got in responses.items():
            if endpoint.startswith(prefix):
                if isinstance(got, BaseException):
                    raise got
                code, out, err = got
                return subprocess.CompletedProcess(cmd, code, out, err)
        raise AssertionError(f"想定しない呼び出し: {cmd}")

    monkeypatch.setattr(crc.subprocess, "run", fake_run)
    return calls, responses


SIMPLE = "on:\n  pull_request:\njobs:\n  a:\n    runs-on: x\n"


# --- 名前の決め方（AC6 / I2） -----------------------------------------------


def test_this_repository_has_only_the_awaiting_six_and_itself(tmp_path, capsys):
    """AC6: 12 個の必須と必須にしないジョブの宣言だけで、差は 6 つと突き合わせのチェック自身だけ。"""
    allow = json.loads((REPO / "scripts" / "required-checks-allow.json").read_text(encoding="utf-8"))
    (tmp_path / "allow.json").write_text(json.dumps({"version": 1, "not_required": allow["not_required"]}), encoding="utf-8")
    (tmp_path / "rules.json").write_text(json.dumps(rules(*REQUIRED_12)), encoding="utf-8")
    code = crc.main(
        [
            "--root",
            str(REPO),
            "--repo",
            "o/r",
            "--branch",
            "develop",
            "--allow",
            str(tmp_path / "allow.json"),
            "--rules-file",
            str(tmp_path / "rules.json"),
        ]
    )
    lines = capsys.readouterr().out.splitlines()
    unregistered = sorted(line.split("（")[0].removeprefix("未登録: ") for line in lines if line.startswith("未登録: "))
    assert code == 1
    assert unregistered == sorted(
        [
            "doc-line-limit-check",
            "skill-shell-vars-check",
            "lint",
            "pr-body-decisions-check",
            "glossary-check",
            "script-structure-check",
            "required-checks-check",
        ]
    )
    assert [line for line in lines if not line.startswith("未登録: ")] == ["失敗 7 件・知らせ 0 件"]


def test_matrix_expression_in_name_is_substituted():
    job = {"name": "pytest (${{ matrix.shard }}/2)", "strategy": {"matrix": {"shard": [0, 1]}}}
    assert crc.job_check_names("pytest-shard", job) == ["pytest (0/2)", "pytest (1/2)"]


def test_matrix_values_are_appended_when_name_has_no_expression():
    job = {"strategy": {"matrix": {"runtime": ["claude", "codex"]}}}
    assert crc.job_check_names("runtime-smoke", job) == ["runtime-smoke (claude)", "runtime-smoke (codex)"]


def test_matrix_exclude_and_include_follow_github_rules():
    matrix = {"os": ["a", "b"], "v": [1, 2], "exclude": [{"os": "b", "v": 2}], "include": [{"os": "a", "extra": "x"}, {"os": "c", "v": 9}]}
    combos = crc.matrix_combos(matrix)
    assert combos == [
        {"os": "a", "v": "1", "extra": "x"},
        {"os": "a", "v": "2", "extra": "x"},
        {"os": "b", "v": "1"},
        {"os": "c", "v": "9"},
    ]
    job = {"name": "t ${{ matrix.os }}-${{ matrix.v }}", "strategy": {"matrix": matrix}}
    assert crc.job_check_names("t", job) == ["t a-1", "t a-2", "t b-1", "t c-9"]


@pytest.mark.parametrize(
    "job",
    [
        {"name": "x ${{ github.ref }}"},
        {"uses": "./.github/workflows/reuse.yml"},
        {"strategy": {"matrix": "${{ fromJSON(needs.a.outputs.m) }}"}},
        {"strategy": {"matrix": {"k": "${{ fromJSON(x) }}"}}},
        {"strategy": {"matrix": {"k": [{"a": 1}]}}},
        {"strategy": {"matrix": {"a": [1], "b": [2]}}},
        {"name": "n ${{ matrix.missing }}", "strategy": {"matrix": {"k": [1]}}},
    ],
)
def test_undeterminable_names_fail(repo, capsys, job):
    repo.workflow("w.yml", json.dumps({"on": {"pull_request": None}, "jobs": {"j": {"runs-on": "x", **job}}}))
    code, lines, _ = repo.run(capsys)
    assert code == 1
    assert any(line.startswith("名前を決められない: ") and "wf/w.yml#j" in line for line in lines)


def test_undeterminable_job_declared_not_required_is_not_counted(repo, capsys):
    repo.workflow("w.yml", json.dumps({"on": ["pull_request"], "jobs": {"j": {"uses": "./x.yml"}}}))
    repo.allow({"not_required": [{"name": "j", "reason": "再利用のワークフローで名前を決められない"}]})
    assert repo.run(capsys)[0] == 0


# --- 差の種類 ---------------------------------------------------------------


def test_unregistered_name_reports_path_and_job_id(repo, capsys):
    """AC4 / I3"""
    repo.workflow("a.yml", SIMPLE)
    code, lines, _ = repo.run(capsys)
    assert code == 1
    assert lines[0].startswith("未登録: a（wf/a.yml#a）→ ")


def test_required_without_job_is_vanished(repo, capsys):
    """AC5 / I4"""
    repo.workflow("a.yml", SIMPLE)
    repo.required(["a", "gone"])
    code, lines, _ = repo.run(capsys)
    assert code == 1
    assert any(line.startswith("消えた必須: gone（") for line in lines)


def test_duplicate_names_across_workflows_fail(repo, capsys):
    """AC1 / I1"""
    repo.workflow("a.yml", SIMPLE)
    repo.workflow("b.yml", SIMPLE)
    repo.required(["a"])
    code, lines, _ = repo.run(capsys)
    assert code == 1
    assert any(line.startswith("名前の重なり: a（wf/a.yml#a・wf/b.yml#a）") for line in lines)


def test_this_repository_has_no_duplicate_names(capsys):
    names, undetermined = crc.read_workflows(REPO, REPO / ".github" / "workflows", "develop")
    assert not undetermined
    seen = [n.name for n in names]
    assert len(seen) == len(set(seen))
    assert "check" not in seen and "ci-scope" not in seen


def test_workflows_not_run_on_pull_request_are_ignored(repo, capsys):
    repo.workflow("a.yml", "on:\n  workflow_dispatch:\njobs:\n  a:\n    runs-on: x\n")
    repo.workflow("b.yml", "on:\n  pull_request:\n    branches: [main]\njobs:\n  b:\n    runs-on: x\n")
    assert repo.run(capsys)[0] == 0


def test_reason_is_required(repo, capsys):
    """I5"""
    repo.workflow("a.yml", SIMPLE)
    repo.allow({"not_required": [{"name": "a", "reason": " "}]})
    code, lines, _ = repo.run(capsys)
    assert code == 1
    assert any(line.startswith("理由の無い宣言: a（allow.json の not_required）") for line in lines)


def test_stale_declaration_fails(repo, capsys):
    """I6"""
    repo.allow({"awaiting": [{"name": "nothing", "reason": "r"}]})
    code, lines, _ = repo.run(capsys)
    assert code == 1
    assert any(line.startswith("古い宣言: nothing（") for line in lines)


def test_not_required_and_required_conflict(repo, capsys):
    """I7"""
    repo.workflow("a.yml", SIMPLE)
    repo.allow({"not_required": [{"name": "a", "reason": "r"}]})
    repo.required(["a"])
    code, lines, _ = repo.run(capsys)
    assert code == 1
    assert any(line.startswith("宣言と ruleset の食い違い: a（") for line in lines)


def test_awaiting_is_a_notice_and_becomes_added(repo, capsys):
    """I8"""
    repo.workflow("a.yml", SIMPLE)
    repo.allow({"awaiting": [{"name": "a", "reason": "r"}]})
    code, lines, _ = repo.run(capsys)
    assert code == 0 and lines[0].startswith("追加待ち: a（")
    repo.required(["a"])
    code, lines, _ = repo.run(capsys)
    assert code == 0 and lines[0].startswith("追加済み: a（")


@pytest.mark.parametrize("decl", ["required", "awaiting"])
def test_path_filtered_jobs_cannot_be_required(repo, capsys, decl):
    """I9"""
    repo.workflow("a.yml", "on:\n  pull_request:\n    paths: ['x/**']\njobs:\n  a:\n    runs-on: x\n")
    if decl == "required":
        repo.required(["a"])
    else:
        repo.allow({"awaiting": [{"name": "a", "reason": "r"}]})
    code, lines, _ = repo.run(capsys)
    assert code == 1
    assert any(line.startswith("絞り込みのあるジョブ: a（wf/a.yml#a）") for line in lines)


def test_path_filtered_jobs_may_be_not_required(repo, capsys):
    repo.workflow("a.yml", "on:\n  pull_request:\n    paths-ignore: ['x/**']\njobs:\n  a:\n    runs-on: x\n")
    repo.allow({"not_required": [{"name": "a", "reason": "r"}]})
    assert repo.run(capsys)[0] == 0


# --- 読めないとき（AC7 / I10） ----------------------------------------------


@pytest.mark.parametrize(
    "decl, needle",
    [
        ("{", "宣言を読めない"),
        ('{"extra": []}', "未知のキー"),
        ('{"awaiting": [{"name": "a", "reason": "r", "x": 1}]}', "未知のキー"),
    ],
)
def test_unreadable_declaration_exits_2(repo, capsys, decl, needle):
    (repo.root / "allow.json").write_text(decl, encoding="utf-8")
    code, lines, err = repo.run(capsys)
    assert code == 2 and lines == [] and needle in err


def test_unreadable_workflow_exits_2(repo, capsys):
    repo.workflow("a.yml", "on: [\n")
    code, lines, err = repo.run(capsys)
    assert code == 2 and lines == [] and "ワークフローを読めない" in err


def test_empty_branch_exits_2(repo, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    code = crc.main(["--root", str(repo.root), "--repo", "o/r", "--branch", "", "--workflows", "wf", "--allow", "allow.json"])
    out = capsys.readouterr()
    assert code == 2 and out.out == "" and "宛先のブランチが空" in out.err


def test_non_array_response_exits_2(repo, capsys):
    repo.required(raw={"message": "Not Found"})
    code, lines, err = repo.run(capsys)
    assert code == 2 and lines == [] and "配列でない" in err


@pytest.mark.parametrize(
    "got, needle",
    [
        ((1, "", "gh: Resource not accessible by integration (HTTP 403)"), "終了コード 1"),
        ((4, "", "To get started with GitHub CLI, please run:  gh auth login"), "終了コード 4"),
        (subprocess.TimeoutExpired("gh", 30), "時間切れ"),
        (FileNotFoundError("gh"), "gh が無い"),
    ],
)
def test_api_failures_exit_2(repo, capsys, gh_calls, got, needle):
    calls, responses = gh_calls
    responses["repos/o/r/rules/branches/"] = got
    repo.workflow("a.yml", SIMPLE)
    code, lines, err = repo.run(capsys, rules_file=False)
    assert code == 2 and lines == [] and needle in err


@pytest.mark.parametrize(
    "branch_resp, needle",
    [((1, "", "gh: Not Found (HTTP 404)"), "宛先のブランチが無い"), ((1, "", "gh: Server Error (HTTP 502)"), "確かめられない")],
)
def test_no_rules_and_missing_branch_exits_2(repo, capsys, gh_calls, branch_resp, needle):
    calls, responses = gh_calls
    responses["repos/o/r/rules/branches/"] = (0, "[]", "")
    responses["repos/o/r/branches/"] = branch_resp
    repo.workflow("a.yml", SIMPLE)
    code, lines, err = repo.run(capsys, rules_file=False)
    assert code == 2 and lines == [] and needle in err


def test_no_rules_on_an_existing_branch_is_out_of_scope(repo, capsys, gh_calls):
    calls, responses = gh_calls
    responses["repos/o/r/rules/branches/"] = (0, json.dumps([{"type": "deletion"}]), "")
    responses["repos/o/r/branches/"] = (0, "{}", "")
    repo.workflow("a.yml", SIMPLE)
    code, lines, _ = repo.run(capsys, rules_file=False)
    assert code == 0
    assert lines[0].startswith("対象外: develop（") and lines[-1] == "失敗 0 件・知らせ 1 件"


def test_gh_is_called_with_get_only(repo, capsys, gh_calls):
    """セキュリティ: 書き込みのメソッドを打たない。"""
    calls, responses = gh_calls
    responses["repos/o/r/rules/branches/"] = (0, "[]", "")
    responses["repos/o/r/branches/"] = (0, "{}", "")
    repo.run(capsys, rules_file=False)
    assert calls and all(c[:4] == ["gh", "api", "--method", "GET"] and len(c) == 5 for c in calls)


# --- 継続的統合への組み込み（決定 11） --------------------------------------


def test_the_job_runs_only_on_pull_requests():
    import yamlio

    wf = yamlio.load_yaml((REPO / ".github" / "workflows" / "runtime-plugin-validate.yml").read_text(encoding="utf-8"))
    job = wf["jobs"]["required-checks-check"]
    assert job["if"] == "github.event_name == 'pull_request'"
    assert dict(job["permissions"]) == {"contents": "read"}


def test_cli_runs_as_a_script(tmp_path):
    rules_file = tmp_path / "rules.json"
    rules_file.write_text(json.dumps(rules(*REQUIRED_12)), encoding="utf-8")
    p = subprocess.run(
        [sys.executable, str(CHECK), "--root", str(REPO), "--repo", "o/r", "--branch", "develop", "--rules-file", str(rules_file)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert p.stdout.splitlines()[-1] == "失敗 0 件・知らせ 7 件"
