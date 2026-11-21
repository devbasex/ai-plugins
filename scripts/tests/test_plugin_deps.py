"""依存の定義と lock の一致の検査（#3370 #3691）。

`.github/workflows/script-structure.yml` の `uv lock --check` の手順を縛る（#3370 の受け入れ条件 10、不変条件 IG5）。
`scope` の手順の run は、一時ディレクトリに作った git の木の上で bash で実際に打ち、出力を読む。
4 つの uv のプロジェクトの 7 日の規則（`exclude-newer`）を縛る（#3370 の受け入れ条件 5、不変条件 I6 の uv の部分）。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import tomllib

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "script-structure.yml"
JOB = "script-structure-check"
# uv のプロジェクトのディレクトリ（リポジトリの根から）と、その lock の検査を起動する scope の出力
PROJECTS = {
    ".": "lock_root",
    "plugins/ndf": "lock_ndf",
    "plugins/playwright-kit/skills/playwright-kit-ops": "lock_playwright",
    "plugins/mcp/mcp-serena": "lock_serena",
}
# 7 日の規則の正（不変条件 I6）
SOURCE_OF_TRUTH = "plugins/ndf"


def load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def triggers(wf: dict) -> dict:
    # PyYAML は YAML 1.1 の規則で `on` を真偽値の True と読む
    return wf.get("on", wf.get(True)) or {}


def steps(wf: dict) -> list[dict]:
    return wf["jobs"][JOB]["steps"]


def scope_step(wf: dict) -> dict:
    found = [s for s in steps(wf) if s.get("id") == "scope"]
    assert len(found) == 1, "scope の手順が 1 つだけある"
    return found[0]


def lock_check_steps(wf: dict) -> dict[str, dict]:
    """`uv lock --check --project <ディレクトリ>` を打つ手順を、ディレクトリから引く。"""
    out: dict[str, dict] = {}
    for s in steps(wf):
        for m in re.finditer(r"uv lock --check --project (\S+)", s.get("run", "")):
            out[m.group(1)] = s
    return out


def scope_output_of(step: dict) -> str:
    """手順の `if` が見る scope の出力の名前。"""
    m = re.fullmatch(r"\s*steps\.scope\.outputs\.(\w+) == 'true'\s*", step.get("if", ""))
    assert m, f"手順の if が scope の出力 1 つで決まる: {step.get('if')!r}"
    return m.group(1)


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def run_scope(wf: dict, cwd: Path, event: str) -> dict[str, str]:
    """scope の手順の run を、ワークフローの env と同じ値で打ち、GITHUB_OUTPUT に書かれた値を返す。"""
    out = cwd / "github-output"
    out.write_text("", encoding="utf-8")
    env = {**os.environ, **{k: str(v) for k, v in (wf.get("env") or {}).items()}}
    env.update(GITHUB_EVENT_NAME=event, GITHUB_OUTPUT=str(out))
    subprocess.run(["bash", "-e", "-c", scope_step(wf)["run"]], cwd=cwd, env=env, check=True, capture_output=True)
    pairs = [line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if line]
    return dict(pairs)


@pytest.fixture()
def pr_tree(tmp_path: Path):
    """宛先の先頭（HEAD^1）と、指定したファイルを書き換えたマージのコミット（HEAD）を持つ木を作る。"""

    def make(changed: list[str]) -> Path:
        git(tmp_path, "init", "-q")
        git(tmp_path, "config", "user.email", "t@example.invalid")
        git(tmp_path, "config", "user.name", "t")
        (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
        git(tmp_path, "add", "-A")
        git(tmp_path, "commit", "-q", "-m", "base")
        for rel in changed:
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("changed\n", encoding="utf-8")
        git(tmp_path, "add", "-A")
        git(tmp_path, "commit", "-q", "-m", "change")
        return tmp_path

    return make


def test_ig5_ndf_lock_check_is_kept():
    """IG5: `uv lock --check --project plugins/ndf` の手順が残る（#3370 の受け入れ条件 10）。"""
    wf = load_workflow()
    assert "plugins/ndf" in lock_check_steps(wf)


def test_ig5_lock_check_runs_on_merge_queue_and_push(tmp_path):
    """IG5: merge queue と push では plugins/ndf の lock の検査を打つ。"""
    wf = load_workflow()
    on = triggers(wf)
    assert "merge_group" in on
    assert "push" in on
    assert "plugins/ndf/**" in on["push"]["paths"]
    step = lock_check_steps(wf)["plugins/ndf"]
    assert "continue-on-error" not in step
    name = scope_output_of(step)
    for event in ("merge_group", "push"):
        assert run_scope(wf, tmp_path, event)[name] == "true", event


def test_ig5_ndf_pyproject_only_pr_runs_ndf_lock_check(pr_tree):
    """IG5: plugins/ndf/pyproject.toml だけを書き換えた PR で plugins/ndf の lock の検査を打つ（受け入れ条件 10）。"""
    wf = load_workflow()
    name = scope_output_of(lock_check_steps(wf)["plugins/ndf"])
    assert run_scope(wf, pr_tree(["plugins/ndf/pyproject.toml"]), "pull_request")[name] == "true"


def rel(directory: str, name: str) -> str:
    return name if directory == "." else f"{directory}/{name}"


def read_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def test_i6_ndf_exclude_newer_is_seven_days():
    """I6: 7 日の規則の正は plugins/ndf/pyproject.toml の `[tool.uv] exclude-newer = "7 days"`（受け入れ条件 5）。"""
    assert read_toml(REPO / SOURCE_OF_TRUTH / "pyproject.toml")["tool"]["uv"]["exclude-newer"] == "7 days"


@pytest.mark.parametrize("directory", sorted(PROJECTS))
def test_i6_every_uv_project_has_the_same_exclude_newer(directory):
    """I6: 4 つの uv のプロジェクトの exclude-newer は plugins/ndf と同じで、lock に `exclude-newer-span = "P7D"` が残る。"""
    want = read_toml(REPO / SOURCE_OF_TRUTH / "pyproject.toml")["tool"]["uv"]["exclude-newer"]
    assert read_toml(REPO / rel(directory, "pyproject.toml"))["tool"]["uv"]["exclude-newer"] == want
    assert read_toml(REPO / rel(directory, "uv.lock"))["options"]["exclude-newer-span"] == "P7D"


def test_ig5_every_uv_project_has_its_own_lock_check():
    """IG5: 4 つの uv のプロジェクトごとに lock の検査があり、それぞれ自分の scope の出力で起動する。"""
    wf = load_workflow()
    checks = lock_check_steps(wf)
    assert set(checks) == set(PROJECTS)
    for directory, output in PROJECTS.items():
        assert "continue-on-error" not in checks[directory]
        assert scope_output_of(checks[directory]) == output, directory


def test_ig5_all_lock_checks_run_on_merge_queue_and_push(tmp_path):
    """IG5: merge queue と push では 4 つの lock の検査を常に打つ。push の paths は 4 つの定義と lock を含む。"""
    wf = load_workflow()
    paths = triggers(wf)["push"]["paths"]
    for directory in PROJECTS:
        for name in ("pyproject.toml", "uv.lock"):
            target = rel(directory, name)
            assert target in paths or any(p.endswith("/**") and target.startswith(p[:-2]) for p in paths), target
    for event in ("merge_group", "push"):
        outputs = run_scope(wf, tmp_path, event)
        assert {outputs.get(o) for o in PROJECTS.values()} == {"true"}, event


@pytest.mark.parametrize("directory", sorted(PROJECTS))
@pytest.mark.parametrize("name", ["pyproject.toml", "uv.lock"])
def test_ig5_pr_touching_one_project_runs_its_lock_check(pr_tree, directory, name):
    """IG5: 1 つのプロジェクトの定義か lock だけを書き換えた PR で、そのプロジェクトの検査を打つ。

    playwright-kit の入力が変わったら根も打つ（根が playwright-kit をパスの依存で持つ）。ほかのプロジェクトの検査は打たない。
    """
    outputs = run_scope(load_workflow(), pr_tree([rel(directory, name)]), "pull_request")
    want = {directory}
    if directory == "plugins/playwright-kit/skills/playwright-kit-ops":
        want.add(".")
    assert {d for d, o in PROJECTS.items() if outputs.get(o) == "true"} == want


def test_ig5_pr_touching_no_definition_skips_lock_checks(pr_tree):
    """IG5: 定義も lock も触らない PR では、4 つの lock の検査を打たない。"""
    outputs = run_scope(load_workflow(), pr_tree(["docs/x.md"]), "pull_request")
    assert {outputs.get(o) for o in PROJECTS.values()} == {"false"}
