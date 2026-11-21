"""依存の定義と lock の一致の検査（#3370 #3691）。

`.github/workflows/script-structure.yml` の `uv lock --check` の手順を縛る（#3370 の受け入れ条件 10、不変条件 IG5）。
`scope` の手順の run は、一時ディレクトリに作った git の木の上で bash で実際に打ち、出力を読む。
4 つの uv のプロジェクトの 7 日の規則（`exclude-newer`）を縛る（#3370 の受け入れ条件 5、不変条件 I6 の uv の部分）。
Renovate の設定（`.github/renovate.json`）と `scripts/plugin-deps.py` の `targets`・`read_holds`・`uv_pin` を縛る
（#3370 の受け入れ条件 6・8・12、#3691 の受け入れ条件 1・6 の固定の欄、不変条件 I3・I5 の設定の部分・I6・I7・I9）。
"""

from __future__ import annotations

import ast
import gzip
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date
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


# --- Renovate の設定と scripts/plugin-deps.py（単位 2） ---

SCRIPT = REPO / "scripts" / "plugin-deps.py"
RENOVATE = REPO / ".github" / "renovate.json"
DEPENDABOT = REPO / ".github" / "dependabot.yml"
RENOVATE_VERSION = REPO / ".github" / "renovate-version"
SLIDES = "plugins/ndf/skills/release/assets/slides"
INSTALLER = Path(__file__).resolve().parent / "fixtures" / "uv-installer-0.13.7.sh.gz"

_spec = importlib.util.spec_from_file_location("plugin_deps_mod", SCRIPT)
deps = importlib.util.module_from_spec(_spec)
sys.modules["plugin_deps_mod"] = deps
_spec.loader.exec_module(deps)


def renovate() -> dict:
    return json.loads(RENOVATE.read_text(encoding="utf-8"))


def days(value: str) -> int:
    m = re.fullmatch(r"(\d+) days?", value)
    assert m, value
    return int(m.group(1))


def seven_days() -> int:
    return days(read_toml(REPO / SOURCE_OF_TRUTH / "pyproject.toml")["tool"]["uv"]["exclude-newer"])


def walk(node, path=()):
    """設定の木の (キーの並び, 値) をすべて返す。"""
    if isinstance(node, dict):
        for k, v in node.items():
            yield path + (k,), v
            yield from walk(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, path + (i,))


def js_regex(pattern: str) -> re.Pattern[str]:
    """Renovate の正規表現（JavaScript の `(?<名前>`・`/…/` の形）を Python で読む。"""
    if len(pattern) > 1 and pattern.startswith("/") and pattern.endswith("/"):
        pattern = pattern[1:-1]
    return re.compile(pattern.replace("(?<", "(?P<"), re.M)


def manifest(target) -> str:
    return rel(target.directory, "pyproject.toml" if target.kind == "uv" else "package.json")


# Task 4: plugins/ndf/pyproject.toml の頭の注記


def head_comment(path: Path) -> str:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("#"):
            break
        lines.append(line)
    return "\n".join(lines)


def test_3370_12_ndf_pyproject_note_points_at_existing_files():
    """#3370 の受け入れ条件 12: 頭の注記が指すファイルがすべて在り、Renovate の設定を指す（文言は照らさない）。"""
    note = head_comment(REPO / SOURCE_OF_TRUTH / "pyproject.toml")
    paths = set(re.findall(r"(?<![\w./-])((?:\.github|issues|plugins|scripts|docs)/[\w./-]*\w)", note))
    assert ".github/renovate.json" in paths
    for p in paths:
        assert (REPO / p).exists(), p


# Task 5: 7 日の一致（I6）


def test_i6_renovate_and_dependabot_wait_the_same_days_as_uv():
    """I6: Renovate の minimumReleaseAge・Dependabot の cooldown・スライドの .npmrc が plugins/ndf の exclude-newer と同じ日数。"""
    want = seven_days()
    assert days(renovate()["minimumReleaseAge"]) == want
    assert renovate()["minimumReleaseAgeBehaviour"] == "timestamp-required"
    actions = [u for u in yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))["updates"] if u["package-ecosystem"] == "github-actions"]
    assert len(actions) == 1
    assert actions[0]["cooldown"]["default-days"] == want
    npmrc = dict(
        line.split("=", 1)
        for line in (REPO / SLIDES / ".npmrc").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    assert int(npmrc["min-release-age"]) == want


def test_i6_only_lock_maintenance_drops_the_wait():
    """I6: 待つ日数を null にするのは pep621 と npm の lock の作り直しだけで、どちらも有効（#3691 の受け入れ条件 1）。"""
    config = renovate()
    nulls = {path for path, v in walk(config) if path[-1] == "minimumReleaseAge" and v is None}
    assert nulls == {("pep621", "lockFileMaintenance", "minimumReleaseAge"), ("npm", "lockFileMaintenance", "minimumReleaseAge")}
    for manager in ("pep621", "npm"):
        assert config[manager]["lockFileMaintenance"]["enabled"] is True
    assert config.get("lockFileMaintenance", {}).get("enabled", False) is False


# Task 5: 承認と閉じる設定（I5 の設定の部分・#3370 の受け入れ条件 6）


def test_i5_dashboard_requires_approval_and_closes_itself():
    """I5: 更新は依存の一覧の Issue のチェックでだけ作り、起動の時刻は設定に置かない（ワークフローが持つ）。"""
    config = renovate()
    assert config["dependencyDashboardApproval"] is True
    assert config["dependencyDashboard"]["enabled"] is True
    assert config["dependencyDashboard"]["autoclose"] is True
    assert not [p for p, _ in walk(config) if p[-1] == "schedule"]
    assert not [p for p, v in walk(config) if p[-1] == "automerge" and v is not False]
    assert config["branchConcurrentLimit"] == 1 and config["prConcurrentLimit"] == 1
    assert config["statusCheckWhen"]["artifactError"] == "always"
    assert config["vulnerabilityAlerts"]["enabled"] is False and config["osvVulnerabilityAlerts"] is False


def test_renovate_version_is_one_line_of_44():
    """`.github/renovate-version` は 44 系の版の 1 行だけで、2 つ目の customManagers が拾う（決定 2）。"""
    text = RENOVATE_VERSION.read_text(encoding="utf-8")
    assert re.fullmatch(r"44\.[0-9]+\.[0-9]+\n", text)
    managers = [m for m in renovate()["customManagers"] if m["depNameTemplate"] == "renovatebot/renovate"]
    assert len(managers) == 1
    assert any(js_regex(p).search(".github/renovate-version") for p in managers[0]["managerFilePatterns"])
    m = js_regex(managers[0]["matchStrings"][0]).search(text)
    assert m and m.group("currentValue") == text.strip()


# Task 5: 対象の lock（I9・#3691 の受け入れ条件 1）


def test_i9_every_tracked_lock_is_a_renovate_target():
    """I9: 追跡している lock から ignorePaths を除いたものが、それぞれ自分の規則（規則 1〜5）と有効な仕組みを持つ。"""
    found = deps.targets(REPO)
    assert {t.directory for t in found} == set(PROJECTS) | {SLIDES}
    assert not [t for t in found if t.lock.startswith("docs/presentations/")]
    config = renovate()
    groups = {tuple(r["matchFileNames"]): r for r in config["packageRules"] if "groupName" in r and "matchFileNames" in r}
    kinds = {"uv": "pep621", "npm": "npm"}
    for t in found:
        assert kinds[t.kind] in config["enabledManagers"], t
        rule = groups.get((manifest(t),))
        assert rule, f"{t.lock} を 1 本にまとめる規則が無い"
        assert rule["prPriority"] == 2
        assert "lockFileMaintenance" not in rule["matchUpdateTypes"]
    assert len({r["groupName"] for r in groups.values() if len(r["matchFileNames"]) == 1}) == len(found)


def init_repo(path: Path, files: dict[str, str]) -> Path:
    git(path, "init", "-q")
    for name, text in files.items():
        p = path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    git(path, "add", "-A")
    return path


LOCK_TREE = {
    "uv.lock": "",
    "a/uv.lock": "",
    "web/package-lock.json": "{}",
    "docs/presentations/x/slides/package-lock.json": "{}",
}


def test_i9_targets_drops_only_ignored_paths(tmp_path):
    """I9: targets は ignorePaths に当たる lock だけを外す。"""
    root = init_repo(tmp_path, {**LOCK_TREE, ".github/renovate.json": json.dumps({"ignorePaths": ["docs/presentations/**"]})})
    assert [(t.kind, t.directory) for t in deps.targets(root)] == [("uv", "."), ("uv", "a"), ("npm", "web")]


def test_i9_targets_without_ignore_paths_lists_every_lock(tmp_path):
    """I9 の境界: ignorePaths が空なら追跡しているすべての lock。"""
    root = init_repo(tmp_path, {**LOCK_TREE, ".github/renovate.json": "{}"})
    assert len(deps.targets(root)) == 4


def test_i9_targets_stops_without_renovate_config(tmp_path):
    """I9 の異常: renovate.json が無ければ止まる（結果 JSON は stopped・config_broken、終了コード 1）。"""
    root = init_repo(tmp_path, LOCK_TREE)
    with pytest.raises(deps.Stop) as e:
        deps.targets(root)
    assert e.value.cause == "config_broken"


def test_main_reports_stopped_as_one_json_line(tmp_path, capsys):
    """main は結果 JSON を 1 行出し、stopped で終了コード 1 を返す。"""
    root = init_repo(tmp_path, LOCK_TREE)
    assert deps.main(["targets", "--root", str(root)]) == 1
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1
    result = json.loads(out[0])
    assert (result["tool"], result["status"], result["cause"]) == ("plugin-deps", "stopped", "config_broken")


# Task 5: 固定の欄（I3・I7・#3370 の受け入れ条件 8・#3691 の受け入れ条件 6 の固定の欄）


def holds_by_package() -> dict[str, object]:
    return {name: h for h in deps.read_holds(renovate()) for name in h.packages}


def test_i3_httpx_and_dbos_rules_have_the_five_fields():
    """I3: httpx と dbos の規則は種類・相手・制約・見直す日・理由の 5 つの欄を持つ。"""
    holds = holds_by_package()
    assert holds["httpx"].kind == "相手の制約"
    assert holds["httpx"].partner == "githubkit 0.17.1"
    assert holds["dbos"].kind == "対象の外"
    for h in holds.values():
        assert isinstance(h.review, date) and h.reason and h.constraint


def broken(rule_index: int, edit) -> dict:
    config = renovate()
    edit(config["packageRules"][rule_index]["description"])
    return config


def httpx_rule_index() -> int:
    return next(i for i, r in enumerate(renovate()["packageRules"]) if r.get("matchPackageNames") == ["httpx"])


@pytest.mark.parametrize("field", range(5))
def test_i3_a_missing_field_stops(field):
    """I3 の異常: 欄を 1 つ消すと ValueError（holds のサブコマンドでは config_broken）。"""
    with pytest.raises(ValueError):
        deps.read_holds(broken(httpx_rule_index(), lambda d: d.pop(field)))


def test_i3_fields_out_of_order_stop():
    """I3 の境界: 行の順が違うと欠けとして止まる。"""
    with pytest.raises(ValueError):
        deps.read_holds(broken(httpx_rule_index(), lambda d: d.insert(0, d.pop(1))))


def test_i3_a_rule_that_holds_without_fields_stops():
    """I3: 版を制限する規則は、description を外しても固定として読まれ、欄の欠けで止まる。"""
    config = {"packageRules": [{"matchPackageNames": ["x"], "allowedVersions": "==1.0.0", "description": "自由文"}]}
    with pytest.raises(ValueError):
        deps.read_holds(config)


def test_i3_holds_command_reports_config_broken(tmp_path, capsys):
    """I3 の異常: 欄の欠けた設定で holds は stopped・config_broken と規則の番号を返す。"""
    config = broken(httpx_rule_index(), lambda d: d.pop(2))
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "renovate.json").write_text(json.dumps(config), encoding="utf-8")
    assert deps.main(["holds", "--root", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["cause"] == "config_broken"
    assert f"packageRules[{httpx_rule_index()}]" in result["summary"]


def pins_in_manifests(name: str) -> set[str]:
    """定義（4 つの pyproject.toml）で name を固定している版。"""
    out = set()
    for directory in PROJECTS:
        text = (REPO / rel(directory, "pyproject.toml")).read_text(encoding="utf-8")
        out |= set(re.findall(rf'"{re.escape(name)}==([^";\s]+)', text))
    return out


def test_i7_holds_do_not_widen_beyond_the_pinned_version():
    """I7: 固定した依存は固定した版だけを許す（httpx は 0.28.1 だけ）。定義の `==` と規則の版が同じ。dbos は上げない。"""
    holds = holds_by_package()
    assert holds["httpx"].version == "0.28.1"
    for name, h in holds.items():
        if h.version is not None:
            assert pins_in_manifests(name) == {h.version}, name
    dbos = next(r for r in renovate()["packageRules"] if r.get("matchPackageNames") == ["dbos"])
    assert dbos["enabled"] is False


def test_i7_widening_allowed_versions_loses_the_hold():
    """I7: 許す版を広げると固定の版が読めなくなり、上のテストが落ちる形になる。"""
    config = renovate()
    config["packageRules"][httpx_rule_index()]["allowedVersions"] = ">=0.28.1"
    holds = {n: h for h in deps.read_holds(config) for n in h.packages}
    assert holds["httpx"].version is None


def test_3370_8_dashboard_header_lists_the_same_holds():
    """#3370 の受け入れ条件 8: 依存の一覧の Issue の頭の固定の一覧が、規則から作る一覧と同じ（httpx は固定として出る）。"""
    header = renovate()["dependencyDashboard"]["header"]
    listed = [line for line in header.splitlines() if line.startswith("- 固定:")]
    assert listed == deps.dashboard_lines(deps.read_holds(renovate()))
    assert any(line.startswith("- 固定: httpx 0.28.1") for line in listed)


def vulnerability_exceptions() -> list[tuple[str, str, str | None, str | None]]:
    """脆弱性の直しの例外（決定 9）: uv の exclude-newer-package と npm の overrides の (パッケージ, 版, 勧告の ID, 外す日)。"""
    out = []
    for directory in PROJECTS:
        for line in (REPO / rel(directory, "pyproject.toml")).read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("exclude-newer-package"):
                m = re.search(r'"([^"]+)"\s*=\s*"[^"]+"\s*}\s*#\s*(\S+)\s+外す日\s+(\S+)\s+版\s+(\S+)', line)
                assert m, f"{directory}: 例外の形が読めない: {line}"
                out.append((m.group(1), m.group(4), m.group(2), m.group(3)))
    package = json.loads((REPO / SLIDES / "package.json").read_text(encoding="utf-8"))
    out += [(name, version, None, None) for name, version in package.get("overrides", {}).items()]
    return out


def test_i3_vulnerability_exceptions_pair_with_rules():
    """I3: 脆弱性の例外は、同じ勧告の ID・版・外す日を持つ規則 12（種類 脆弱性の例外）と組になり、組の無い規則も無い。"""
    rules = [h for h in deps.read_holds(renovate()) if h.kind == "脆弱性の例外"]
    exceptions = vulnerability_exceptions()
    for name, version, advisory, until in exceptions:
        match = [h for h in rules if h.constraint == f"{name}=={version}"]
        assert match, f"{name}=={version} の例外に規則が無い"
        if advisory:
            assert match[0].partner == advisory and match[0].review.isoformat() == until
    assert len(rules) == len(exceptions)


# Task 6: UV_VERSION の 2 か所と uv-pin（#3691 の受け入れ条件 1 のコードの中の固定の行）


def test_3691_1_custom_manager_picks_both_uv_versions():
    """#3691 の受け入れ条件 1: 1 つ目の customManagers が 2 か所の UV_VERSION を同じ版として拾う。"""
    manager = next(m for m in renovate()["customManagers"] if m["depNameTemplate"] == "astral-sh/uv")
    assert manager["datasourceTemplate"] == "github-releases"
    pins = deps.uv_pins(REPO)
    for path in deps.UV_PIN_FILES:
        assert any(js_regex(p).search(path) for p in manager["managerFilePatterns"]), path
        found = js_regex(manager["matchStrings"][0]).findall((REPO / path).read_text(encoding="utf-8"))
        assert found == [pins[path][0]], path


def test_rule_10_runs_uv_pin_once_per_branch():
    """規則 10: uv の 2 か所を 1 本にまとめ、ブランチごとに 1 回、登録されたサブコマンドの uv-pin を打つ（決定 12）。"""
    rule = next(r for r in renovate()["packageRules"] if r.get("matchPackageNames") == ["astral-sh/uv"])
    tasks = rule["postUpgradeTasks"]
    assert tasks["executionMode"] == "branch"
    assert tasks["commands"] == ["python3 scripts/plugin-deps.py uv-pin"]
    assert "uv-pin" in deps.COMMANDS
    assert sorted(tasks["fileFilters"]) == sorted(deps.UV_PIN_FILES)


def assert_uv_pins_agree(root: Path) -> None:
    pins = deps.uv_pins(root)
    assert len(set(pins.values())) == 1, pins


def test_uv_pins_agree_in_both_files():
    """設定の検査: 2 つのファイルの UV_VERSION と UV_INSTALLER_SHA256 の組が同じ。"""
    assert_uv_pins_agree(REPO)


@pytest.fixture()
def uv_tree(tmp_path: Path) -> Path:
    for path in deps.UV_PIN_FILES:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / path, tmp_path / path)
    return tmp_path


def recorded_installer() -> bytes:
    return gzip.decompress(INSTALLER.read_bytes())


class Recorder:
    def __init__(self, body: bytes | None):
        self.body = body
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        if self.body is None:
            raise deps.FetchError(f"{url}: unreachable")
        return self.body


def edit(path: Path, pattern: re.Pattern[str], value: str) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(pattern.sub(lambda m: m.group(1) + value + m.group(3), text), encoding="utf-8")


@pytest.mark.parametrize("which", ["version", "sha"])
@pytest.mark.parametrize("path", deps.UV_PIN_FILES)
def test_changing_only_one_file_breaks_the_pair(uv_tree, path, which):
    """設定の検査の異常: 片方の版かハッシュだけを変えると落ちる。"""
    if which == "version":
        edit(uv_tree / path, deps.UV_VERSION_RE, "9.9.9")
    else:
        edit(uv_tree / path, deps.UV_SHA_RE, "0" * 64)
    with pytest.raises(AssertionError):
        assert_uv_pins_agree(uv_tree)


def test_recorded_installer_matches_the_committed_hash(uv_tree):
    """uv_pin の境界: 記録したインストーラ（0.13.7）の sha256 は今の値と同じで、取得は 1 回、どちらも書き換えない。"""
    before = {p: (uv_tree / p).read_bytes() for p in deps.UV_PIN_FILES}
    fetch = Recorder(recorded_installer())
    result = deps.uv_pin(uv_tree, fetch)
    assert result.status == "ok"
    assert fetch.urls == ["https://astral.sh/uv/0.13.7/install.sh"]
    assert result.items[0]["sha256"] == hashlib.sha256(recorded_installer()).hexdigest()
    assert {p: (uv_tree / p).read_bytes() for p in deps.UV_PIN_FILES} == before


def test_uv_pin_writes_the_same_hash_to_both_files(uv_tree):
    """uv_pin の正常: Renovate が 2 か所の版を上げた後、取得 1 回の本文の sha256 を 2 か所へ書く。"""
    for path in deps.UV_PIN_FILES:
        edit(uv_tree / path, deps.UV_VERSION_RE, "0.13.8")
    body = recorded_installer() + b"\n# 0.13.8\n"
    fetch = Recorder(body)
    result = deps.uv_pin(uv_tree, fetch)
    assert result.status == "ok"
    assert fetch.urls == ["https://astral.sh/uv/0.13.8/install.sh"]
    want = hashlib.sha256(body).hexdigest()
    assert set(deps.uv_pins(uv_tree).values()) == {("0.13.8", want)}
    assert "releases/download/0.13.8/" in (uv_tree / "plugins/ndf/bin/ndf").read_text(encoding="utf-8")


def test_uv_pin_does_not_write_when_fetch_fails(uv_tree):
    """uv_pin の異常: 取得の失敗でどちらのファイルも書き換えず、transient で止まる。"""
    for path in deps.UV_PIN_FILES:
        edit(uv_tree / path, deps.UV_VERSION_RE, "0.13.8")
    before = {p: (uv_tree / p).read_bytes() for p in deps.UV_PIN_FILES}
    with pytest.raises(deps.Stop) as e:
        deps.uv_pin(uv_tree, Recorder(None))
    assert e.value.cause == "transient"
    assert {p: (uv_tree / p).read_bytes() for p in deps.UV_PIN_FILES} == before


def test_uv_pin_stops_on_different_versions_without_fetching(uv_tree):
    """uv_pin の異常: 2 か所の版が違えば取得せずに config_broken で止まる。"""
    edit(uv_tree / deps.UV_PIN_FILES[0], deps.UV_VERSION_RE, "0.13.8")
    fetch = Recorder(recorded_installer())
    with pytest.raises(deps.Stop) as e:
        deps.uv_pin(uv_tree, fetch)
    assert e.value.cause == "config_broken"
    assert fetch.urls == []


def test_main_uv_pin_uses_the_module_fetch(uv_tree, monkeypatch, capsys):
    """main は外部の取得をモジュールの fetch_url で行う（差し替えられる）。"""
    fetch = Recorder(recorded_installer())
    monkeypatch.setattr(deps, "fetch_url", fetch)
    assert deps.main(["uv-pin", "--root", str(uv_tree)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    assert len(fetch.urls) == 1


# --- 確かめるテストの一覧（単位 6。#3691 の受け入れ条件 2・E6、決定 10） ---

AFFECTED_WORKFLOW = REPO / ".github" / "workflows" / "deps-affected-comment.yml"
SHA_A = "a" * 40
SHA_B = "b" * 40


def uv_lock(packages: dict[str, tuple[str, list[str]]], root_deps: list[str]) -> str:
    """記録した uv.lock の形（根の virtual のパッケージと registry のパッケージ）。"""
    out = [
        "version = 1",
        "revision = 3",
        'requires-python = ">=3.11"',
        "",
        "[[package]]",
        'name = "proj"',
        'version = "0"',
        'source = { virtual = "." }',
        "dependencies = [" + ", ".join(f'{{ name = "{d}" }}' for d in root_deps) + "]",
    ]
    for name, (version, deps_) in packages.items():
        out += ["", "[[package]]", f'name = "{name}"', f'version = "{version}"', 'source = { registry = "https://pypi.org/simple" }']
        if deps_:
            out.append("dependencies = [" + ", ".join(f'{{ name = "{d}" }}' for d in deps_) + "]")
    return "\n".join(out) + "\n"


def npm_lock(packages: dict[str, tuple[str, list[str]]], root_deps: list[str]) -> str:
    pkgs = {"": {"name": "slide-deck", "dependencies": {d: "*" for d in root_deps}}}
    for name, (version, deps_) in packages.items():
        pkgs[f"node_modules/{name}"] = {"version": version, **({"dependencies": {d: "*" for d in deps_}} if deps_ else {})}
    return json.dumps({"name": "slide-deck", "lockfileVersion": 3, "packages": pkgs}, indent=2) + "\n"


UV_BASE = {"python-dotenv": ("1.2.4", []), "httpx": ("0.28.1", ["idna"]), "idna": ("3.10", [])}
NPM_BASE = {"pptxgenjs": ("4.0.1", ["jszip"]), "jszip": ("3.10.2", ["pako"]), "pako": ("1.0.11", [])}


def test_lock_diff_uv_records_changes_and_parents():
    """lock_diff の正常: 記録した uv.lock の差分で、変わったパッケージと前後の版。直接の依存の親は空。"""
    new = {**UV_BASE, "python-dotenv": ("1.2.5", [])}
    changes = deps.lock_diff(uv_lock(UV_BASE, ["python-dotenv", "httpx"]), uv_lock(new, ["python-dotenv", "httpx"]))
    assert changes == [deps.Change("python-dotenv", "1.2.4", "1.2.5", ())]


def test_lock_diff_npm_transitive_has_parent():
    """lock_diff の正常: 記録した package-lock.json の推移的な依存は、直接の依存まで辿った親を持つ。"""
    new = {**NPM_BASE, "pako": ("1.0.12", [])}
    changes = deps.lock_diff(npm_lock(NPM_BASE, ["pptxgenjs"]), npm_lock(new, ["pptxgenjs"]))
    assert changes == [deps.Change("pako", "1.0.11", "1.0.12", ("pptxgenjs",))]


def test_lock_diff_added_and_removed_packages():
    """lock_diff の境界: 足しただけ・消しただけのパッケージ。入ったものは head、消えたものは base の関係から親を引く。"""
    old = uv_lock(UV_BASE, ["python-dotenv", "httpx"])
    new = uv_lock({"python-dotenv": ("1.2.4", []), "httpx": ("0.28.1", ["anyio"]), "anyio": ("4.0.0", [])}, ["python-dotenv", "httpx"])
    assert deps.lock_diff(old, new) == [
        deps.Change("anyio", None, "4.0.0", ("httpx",)),
        deps.Change("idna", "3.10", None, ("httpx",)),
    ]
    assert deps.lock_diff("", old)[0].before is None


@pytest.mark.parametrize("broken", ["<<<<<<< HEAD\n", "version = [\n"])
def test_lock_diff_rejects_unreadable_lock(broken):
    """lock_diff の異常: 衝突のマーカーや形の違う lock は ValueError（理由を返す）。"""
    with pytest.raises(ValueError):
        deps.lock_diff(uv_lock(UV_BASE, ["httpx"]), broken + uv_lock(UV_BASE, ["httpx"]))


def commit_affected(path: Path, message: str) -> None:
    git(path, "add", "-A")
    git(path, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", message)


@pytest.fixture()
def deps_repo(tmp_path: Path) -> Path:
    """uv のプロジェクト（app）とスライドの npm のディレクトリを持つ木。base のタグを付ける。"""
    files = {
        ".github/renovate.json": json.dumps({"ignorePaths": ["docs/presentations/**"]}),
        "app/uv.lock": uv_lock(UV_BASE, ["python-dotenv", "httpx"]),
        "app/src/pkg/__init__.py": "",
        "app/src/pkg/notify.py": "from dotenv import dotenv_values\n",
        "app/src/pkg/net.py": "import httpx\n",
        "app/tests/test_notify.py": "from pkg import notify\n",
        "slides/package-lock.json": npm_lock(NPM_BASE, ["pptxgenjs"]),
        "slides/lib/deck-frame.js": 'const P = require(require.resolve("pptxgenjs", { paths: [__dirname] }));\n',
        "app/tests/_slides.py": 'DECK = "slides/lib/deck-frame.js"\n',
        "app/tests/test_deck.py": "from ._slides import DECK\n",
        "app/tests/__init__.py": "",
    }
    init_repo(tmp_path, files)
    commit_affected(tmp_path, "base")
    git(tmp_path, "tag", "base")
    return tmp_path


def change(root: Path, rel_path: str, text: str) -> None:
    (root / rel_path).write_text(text, encoding="utf-8")
    commit_affected(root, "change")


class CommentGh:
    """記録した gh の応答。呼び出しの引数を残す。"""

    def __init__(self, head: str = SHA_A, comments: list[int] | None = None, fail: bool = False):
        self.head, self.comments, self.fail = head, comments or [], fail
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if self.fail:
            raise subprocess.CalledProcessError(1, ["gh", *args], stderr="HTTP 502")
        if args[:2] == ["api", "--paginate"]:
            return "".join(f"{i}\n" for i in self.comments)
        if args[1].endswith("/pulls/7"):
            return self.head + "\n"
        return ""

    def writes(self) -> list[list[str]]:
        return [c for c in self.calls if "-X" in c]


def run_affected(root: Path, gh=None, out: Path | None = None):
    out = out or root / "tmp" / "deps-affected.json"
    result = deps.affected(root, "base", "HEAD", pr=7, head_sha=SHA_A, repo="o/r", gh=gh or CommentGh(), out=out)
    return result, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else None)


def test_affected_maps_python_dotenv_to_its_module_and_test(deps_repo):
    """affected の正常: python-dotenv の差分は対応表の dotenv で import するモジュールと、それを import するテストへ届く。"""
    change(deps_repo, "app/uv.lock", uv_lock({**UV_BASE, "python-dotenv": ("1.2.5", [])}, ["python-dotenv", "httpx"]))
    result, data = run_affected(deps_repo)
    assert result.status == "ok" and result.cause is None
    assert (data["pr"], data["head_sha"], data["error"]) == (7, SHA_A, None)
    [lock] = data["locks"]
    assert lock["lock"] == "app/uv.lock"
    [row] = lock["changes"]
    assert (row["name"], row["before"], row["after"], row["parents"]) == ("python-dotenv", "1.2.4", "1.2.5", [])
    assert row["modules"] == [{"path": "app/src/pkg/notify.py", "tests": ["app/tests/test_notify.py"]}]


def test_affected_npm_pptxgenjs_reaches_python_tests_through_a_helper(deps_repo):
    """affected の正常: npm の pptxgenjs は require.resolve で使うモジュールと、補助を挟んで名指すテストへ届く。"""
    change(deps_repo, "slides/package-lock.json", npm_lock({**NPM_BASE, "pptxgenjs": ("4.0.2", ["jszip"])}, ["pptxgenjs"]))
    _, data = run_affected(deps_repo)
    [row] = data["locks"][0]["changes"]
    assert row["name"] == "pptxgenjs"
    assert row["modules"] == [{"path": "slides/lib/deck-frame.js", "tests": ["app/tests/test_deck.py"]}]


def test_affected_transitive_only_lists_parents(deps_repo):
    """affected の境界: 推移的な依存だけが変わった差分は、使うモジュールが無く、親（直接の依存）を添える。"""
    change(deps_repo, "app/uv.lock", uv_lock({**UV_BASE, "idna": ("3.11", [])}, ["python-dotenv", "httpx"]))
    _, data = run_affected(deps_repo)
    [row] = data["locks"][0]["changes"]
    assert (row["name"], row["parents"], row["modules"]) == ("idna", ["httpx"], [])
    assert "親: httpx" in deps.affected_body(data)


def test_affected_module_without_test_says_so(deps_repo):
    """affected の境界: 使うモジュールにテストが無ければ行を落とさず「テストが無い」と出す。"""
    change(deps_repo, "app/uv.lock", uv_lock({**UV_BASE, "httpx": ("0.28.2", ["idna"])}, ["python-dotenv", "httpx"]))
    _, data = run_affected(deps_repo)
    [row] = data["locks"][0]["changes"]
    assert row["modules"] == [{"path": "app/src/pkg/net.py", "tests": []}]
    assert "テストが無い" in deps.affected_body(data)


def test_affected_without_lock_diff_or_comment_writes_nothing(deps_repo):
    """affected の異常: lock が変わらず目印のコメントも無い差分では artifact のデータを置かない（#3691 の受け入れ条件 2）。"""
    change(deps_repo, "app/src/pkg/net.py", "import httpx  # 変更\n")
    gh = CommentGh(comments=[])
    result, data = run_affected(deps_repo, gh)
    assert result.status == "ok" and data is None
    assert gh.writes() == []


def test_affected_without_lock_diff_keeps_data_when_a_comment_exists(deps_repo):
    """affected: lock の差分が無くても目印のコメントがあれば、消すために空のデータを置く。"""
    change(deps_repo, "app/src/pkg/net.py", "import httpx  # 変更\n")
    _, data = run_affected(deps_repo, CommentGh(comments=[11]))
    assert data["locks"] == [] and data["error"] is None


def test_affected_unreadable_lock_writes_the_reason_and_exits_zero(deps_repo, capsys):
    """affected の異常（E6）: 読めない lock は理由と head の SHA のデータを置き、cause invalid・終了コード 0（合否を変えない）。"""
    change(deps_repo, "app/uv.lock", "<<<<<<< HEAD\n" + uv_lock(UV_BASE, ["httpx"]))
    out = deps_repo / "tmp" / "x.json"
    argv = ["affected", "--root", str(deps_repo), "--base", "base", "--head", "HEAD", "--pr", "7", "--head-sha", SHA_A, "--out", str(out)]
    assert deps.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["cause"] == "invalid"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["error"]["cause"] == "invalid" and "app/uv.lock" in data["error"]["reason"]
    assert data["head_sha"] == SHA_A
    assert "一覧を作れない" in deps.affected_body(data)


def direct_dependencies(directory: str) -> set[str]:
    data = read_toml(REPO / rel(directory, "pyproject.toml"))
    reqs = list(data.get("project", {}).get("dependencies", []))
    for group in data.get("project", {}).get("optional-dependencies", {}).values():
        reqs += group
    for group in data.get("dependency-groups", {}).values():
        reqs += [r for r in group if isinstance(r, str)]
    return {deps.norm(re.match(r"[A-Za-z0-9._-]+", r).group(0)) for r in reqs}


@pytest.mark.parametrize("directory", sorted(PROJECTS))
def test_every_direct_dependency_is_in_the_import_table(directory):
    """設定の検査: 4 つの pyproject.toml の直接の依存は、import の名前がリポジトリのどこかで import されるか「import しない」に載る。"""
    files = [f for f in deps.tracked(REPO) if f.endswith(".py")]
    index = deps.SourceIndex(REPO, files)
    missing = [name for name in sorted(direct_dependencies(directory)) if index.modules(files, "uv", name) == []]
    assert missing == [], f"{directory}: IMPORT_NAMES か NO_IMPORT へ足す"


def data_file(tmp_path: Path, **over) -> Path:
    data = {
        "version": 1,
        "pr": 7,
        "head_sha": SHA_A,
        "error": None,
        "locks": [
            {
                "lock": "app/uv.lock",
                "kind": "uv",
                "changes": [
                    {
                        "name": "python-dotenv",
                        "before": "1.2.4",
                        "after": "1.2.5",
                        "parents": [],
                        "no_import": None,
                        "modules": [{"path": "app/src/pkg/notify.py", "tests": ["app/tests/test_notify.py"]}],
                    }
                ],
            }
        ],
    }
    data.update(over)
    path = tmp_path / "deps-affected.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


RUN = deps.WorkflowRun("o/r", 7, SHA_A)


def test_post_affected_creates_the_comment(tmp_path):
    """post_affected の正常: 目印のコメントが無ければ 1 件作る。本文は目印の行と head の SHA から始まる。"""
    gh = CommentGh()
    assert deps.post_affected(data_file(tmp_path), RUN, gh).status == "ok"
    [call] = gh.writes()
    assert call[:4] == ["api", "-X", "POST", "repos/o/r/issues/7/comments"]
    body = call[-1].removeprefix("body=")
    assert body.splitlines()[0] == deps.AFFECTED_MARKER and SHA_A in body and "python-dotenv" in body


def test_post_affected_rewrites_the_one_comment(tmp_path):
    """post_affected の正常: 目印のコメントがあれば書き直し、2 件目以降は消してちょうど 1 件にする。"""
    gh = CommentGh(comments=[11, 12])
    deps.post_affected(data_file(tmp_path), RUN, gh)
    assert [c[:4] for c in gh.writes()] == [
        ["api", "-X", "DELETE", "repos/o/r/issues/comments/12"],
        ["api", "-X", "PATCH", "repos/o/r/issues/comments/11"],
    ]


def test_post_affected_deletes_the_comment_when_the_lock_diff_is_gone(tmp_path):
    """post_affected の境界: lock の差分が無くなった PR では、目印のコメントを消す（前の一覧を残さない）。"""
    gh = CommentGh(comments=[11])
    assert deps.post_affected(data_file(tmp_path, locks=[]), RUN, gh).status == "ok"
    assert [c[:4] for c in gh.writes()] == [["api", "-X", "DELETE", "repos/o/r/issues/comments/11"]]


@pytest.mark.parametrize(
    "run, head",
    [
        (deps.WorkflowRun("o/r", 7, SHA_A), SHA_B),  # PR の今の head が進んだ（古い実行）
        (deps.WorkflowRun("o/r", 7, SHA_B), SHA_A),  # 実行の head とデータの SHA が違う
        (deps.WorkflowRun("o/r", 8, SHA_A), SHA_A),  # 別の PR のデータ
    ],
)
def test_post_affected_skips_a_different_sha_or_pr(tmp_path, run, head):
    """post_affected の異常: 今の head と違う SHA・別の PR のデータでは書かずに skipped（終了コード 0）。"""
    gh = CommentGh(head=head, comments=[11])
    assert deps.post_affected(data_file(tmp_path), run, gh).status == "skipped"
    assert gh.writes() == []


def test_post_affected_fork_without_pr_number_checks_the_head(tmp_path):
    """post_affected: fork の PR（workflow_run の pull_requests が空）でも、PR の今の head が実行の head と同じときだけ書く。"""
    gh = CommentGh()
    assert deps.post_affected(data_file(tmp_path), deps.WorkflowRun("o/r", None, SHA_A), gh).status == "ok"
    assert len(gh.writes()) == 1


def test_post_affected_writes_the_reason_when_the_list_failed(tmp_path):
    """post_affected（E6）: 一覧を作れなかったデータでは、目印のコメントを理由と head の SHA で書き直す。"""
    gh = CommentGh(comments=[11])
    deps.post_affected(data_file(tmp_path, locks=[], error={"cause": "invalid", "reason": "app/uv.lock: 衝突のマーカーがある"}), RUN, gh)
    body = gh.writes()[0][-1]
    assert "一覧を作れない" in body and SHA_A in body and "衝突のマーカー" in body


@pytest.mark.parametrize("over", [{"head_sha": "x"}, {"pr": "7"}, {"locks": "x"}])
def test_post_affected_rejects_a_malformed_artifact(tmp_path, over):
    """post_affected の異常: 形の違う artifact は読まずに止まり、何も書かない。"""
    gh = CommentGh()
    with pytest.raises(deps.Stop) as e:
        deps.post_affected(data_file(tmp_path, **over), RUN, gh)
    assert e.value.cause == "invalid" and gh.calls == []


def test_affected_body_keeps_artifact_text_inside_the_code_block():
    """本文: artifact の文字列はコードのブロックの中に置き、中のバッククォートで囲みを抜けられない。"""
    data = {
        "pr": 7,
        "head_sha": SHA_A,
        "error": None,
        "locks": [
            {
                "lock": "x/uv.lock",
                "changes": [{"name": "evil```\n@someone", "before": "1", "after": "2", "parents": [], "no_import": None, "modules": []}],
            }
        ],
    }
    body = deps.affected_body(data)
    fence_line = body.splitlines()[3]
    assert fence_line.startswith("````") and body.rstrip().endswith(fence_line.removesuffix("text"))


def test_affected_workflows_wire_the_list_and_the_comment():
    """ワークフロー: PR の実行が artifact deps-affected を置き、workflow_run の実行が既定のブランチのコードで投稿する。"""
    wf = load_workflow()
    [step] = [s for s in steps(wf) if "plugin-deps.py affected" in s.get("run", "")]
    assert step["if"] == "github.event_name == 'pull_request'"
    assert "--base HEAD^1 --head HEAD" in step["run"] and "continue-on-error" not in step
    [upload] = [s for s in steps(wf) if str(s.get("uses", "")).startswith("actions/upload-artifact")]
    assert upload["with"]["name"] == "deps-affected" and upload["with"]["if-no-files-found"] == "ignore"
    comment = yaml.safe_load(AFFECTED_WORKFLOW.read_text(encoding="utf-8"))
    run_on = triggers(comment)["workflow_run"]
    assert run_on["workflows"] == [wf["name"]] and run_on["types"] == ["completed"]
    [job] = comment["jobs"].values()
    assert job["permissions"]["pull-requests"] == "write"
    assert comment["concurrency"]["cancel-in-progress"] is False
    for s in job["steps"]:
        assert "ref" not in (s.get("with") or {}), "PR のコードを checkout しない"
    assert any("post-affected" in s.get("run", "") for s in job["steps"])
    exempt = json.loads((REPO / ".ndf" / "project.json").read_text(encoding="utf-8"))["test"]["ci_exempt"]
    assert ".github/workflows/deps-affected-comment.yml" in {e["job"] for e in exempt}


# --- 脆弱性と解いてよい固定の知らせ（report。単位 5・Task 10・#3691 の受け入れ条件 6・不変条件 I2・I5・I11） ---
#
# 外部（uv audit・npm audit・PyPI の JSON API・gh）は差し替える。uv audit と npm audit は 2026-11-21 に打った出力を
# 切り詰めて fixtures/ に置いた（uv 0.13.7 の requests 2.19.0 の lock・npm 11.20.0 のスライドの lock）。PyPI の応答は
# 同じ日に `https://pypi.org/pypi/githubkit/json` で確かめた形（`releases` の各版のファイルの `upload_time_iso_8601`・
# `yanked`、版ごとの `info.requires_dist`）で組み立てる。

from datetime import timedelta  # noqa: E402

DEPS_WEEKLY = REPO / ".github" / "workflows" / "deps-weekly.yml"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
UV_AUDIT_VULNS = (FIXTURES / "uv-audit-requests-2.19.0.json").read_text(encoding="utf-8")
NPM_AUDIT_VULNS = (FIXTURES / "npm-audit-slides.json").read_text(encoding="utf-8")
UV_AUDIT_NONE = json.dumps(
    {
        "schema": {"version": "preview"},
        "summary": {"audited_packages": 3, "vulnerabilities": 0, "adverse_statuses": 0},
        "vulnerabilities": [],
        "adverse_statuses": [],
    }
)
NPM_AUDIT_NONE = json.dumps({"auditReportVersion": 2, "vulnerabilities": {}, "metadata": {"vulnerabilities": {"total": 0}}})
NPM_AUDIT_ERROR = json.dumps({"message": "request to http://127.0.0.1:9/-/npm/v1/security/advisories/bulk failed", "error": {}})
TODAY = date(2026, 11, 21)
BOT = {"login": "app/github-actions", "is_bot": True}
HUMAN = {"login": "someone", "is_bot": False}


def ago(n: int) -> date:
    return TODAY - timedelta(days=n)


def hold_rule(kind: str, partner: str, constraint: str, review: date, **rule) -> dict:
    desc = [f"種類: {kind}", f"相手: {partner}", f"制約: {constraint}", f"見直す日: {review.isoformat()}", "理由: テスト"]
    return {"description": desc, **rule}


def report_config(review: date = date(2027, 2, 19), listed: list[str] | None = None, extra: list[dict] | None = None) -> dict:
    return {
        "ignorePaths": ["docs/presentations/**"],
        "packageRules": [
            hold_rule(
                "プレリリースを選ぶ",
                "決定 106",
                "7 日たった最新の安定版が 365 日より古い",
                date(2027, 2, 19),
                matchPackageNames=listed or [],
                ignoreUnstable=False,
            ),
            hold_rule(
                "相手の制約",
                "githubkit 0.17.1",
                "httpx<1.0.0,>=0.23.0",
                date(2027, 2, 19),
                matchPackageNames=["httpx"],
                allowedVersions="==0.28.1",
            ),
            hold_rule("対象の外", "C1+ の続き", "3.4.0 のまま", review, matchPackageNames=["dbos"], enabled=False),
            *(extra or []),
        ],
    }


ROOT_PYPROJECT = """\
[project]
name = "t"
version = "0"
requires-python = ">=3.11"
dependencies = ["httpx==0.28.1"]

[project.optional-dependencies]
github = ["githubkit[auth]==0.17.1"]

[tool.uv.sources]
local_pkg = { path = "local", editable = true }
"""

SLIDES_LOCK = json.dumps(
    {
        "lockfileVersion": 3,
        "packages": {
            "": {"dependencies": {"pptxgenjs": "4.0.1"}},
            "node_modules/image-size": {"version": "1.2.1"},
            "node_modules/pptxgenjs": {"version": "4.0.1"},
        },
    }
)


def report_tree(tmp_path: Path, config: dict | None = None) -> Path:
    files = {
        ".github/renovate.json": json.dumps(config or report_config()),
        "pyproject.toml": ROOT_PYPROJECT,
        "uv.lock": "",
        "web/package.json": "{}",
        "web/package-lock.json": SLIDES_LOCK,
        "docs/presentations/x/package-lock.json": "{}",
    }
    return init_repo(tmp_path, files)


class FakeRun:
    """uv audit と npm audit の差し替え。結果は (道具, 対象のディレクトリ) から引く。"""

    def __init__(self, root: Path, uv: tuple[int, str] = (1, UV_AUDIT_VULNS), npm: tuple[int, str] = (1, NPM_AUDIT_VULNS)):
        self.results = {("uv", str(root)): uv, ("npm", str(root / "web")): npm}
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        flag = "--project" if args[0] == "uv" else "--prefix"
        rc, out = self.results[(args[0], args[args.index(flag) + 1])]
        return subprocess.CompletedProcess(args, rc, out, "")


def pypi_page(releases: dict[str, date], requires: list[str] | None = None, yanked: tuple[str, ...] = ()) -> bytes:
    files = {v: [{"upload_time_iso_8601": f"{d.isoformat()}T06:09:15.860267Z", "yanked": v in yanked}] for v, d in releases.items()}
    return json.dumps({"info": {"requires_dist": requires}, "releases": files}).encode()


def version_page(requires: list[str]) -> bytes:
    return json.dumps({"info": {"requires_dist": requires}, "urls": []}).encode()


PARTNER_REQUIRES = ["anyio<5.0.0,>=3.6.1", "httpx<1.0.0,>=0.23.0", 'pyjwt[crypto]<3.0.0,>=2.4.0; extra == "auth"']


def pages(requires: list[str] | None = None, httpx: dict[str, date] | None = None, **more: bytes) -> dict[str, bytes | None]:
    """既定: githubkit の採用できる最新は 0.18.0（10 日前。0.19.0 は 3 日前で待つ日数に満たない）で、httpx の制約は同じ。"""
    out = {
        "https://pypi.org/pypi/githubkit/json": pypi_page({"0.17.1": ago(15), "0.18.0": ago(10), "0.19.0": ago(3)}),
        "https://pypi.org/pypi/githubkit/0.18.0/json": version_page(PARTNER_REQUIRES if requires is None else requires),
        "https://pypi.org/pypi/httpx/json": pypi_page(httpx or {"0.28.1": ago(100), "1.0.0": ago(20)}),
    }
    out.update({f"https://pypi.org/pypi/{k}/json": v for k, v in more.items()})
    return out


class FakeFetch:
    def __init__(self, table: dict[str, bytes | None]):
        self.table = table
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        body = self.table.get(url)
        if body is None:
            raise deps.FetchError(f"{url}: 404")
        return body


class FakeGh:
    """gh の差し替え。開いた Issue の一覧を返し、書き込みの呼び出しと渡した本文を記録する。"""

    def __init__(self, issues: list[dict] | None = None, fail_list: bool = False):
        self.issues = issues or []
        self.fail_list = fail_list
        self.calls: list[list[str]] = []
        self.bodies: list[str] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if "--body-file" in args:
            self.bodies.append(Path(args[args.index("--body-file") + 1]).read_text(encoding="utf-8"))
        if args[:2] == ["issue", "list"]:
            if self.fail_list:
                raise subprocess.CalledProcessError(1, ["gh", *args], "", "HTTP 502")
            return json.dumps(self.issues)
        if args[:2] == ["issue", "create"]:
            return "https://github.com/o/r/issues/99\n"
        return ""

    def writes(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] != ["issue", "list"]]


def managed(number: int = 5, body: str = "古い本文", author: dict = BOT) -> dict:
    return {"number": number, "author": author, "body": f"{deps.REPORT_MARK}\n{body}"}


def run_report(root: Path, run=None, fetch=None, gh=None):
    run = run or FakeRun(root)
    fetch = fetch or FakeFetch(pages())
    gh = gh if gh is not None else FakeGh()
    return deps.report(root, fetch, gh, run, TODAY), run, fetch, gh


def items_of(result, section: str) -> list[dict]:
    return [i for i in result.items if i.get("section") == section]


# Task 10: 脆弱性の取得と Issue の作成・書き直し・閉じる（I2・#3691 の受け入れ条件 6）


def test_3691_6_report_creates_one_issue_with_the_three_tables(tmp_path):
    """3691:6: 記録した uv audit と npm audit の脆弱性から、目印つきの 1 件の Issue を作る。本文は 3 つの表を持つ。"""
    root = report_tree(tmp_path)
    result, run, _, gh = run_report(root)
    assert result.status == "ok"
    creates = [c for c in gh.writes() if c[:2] == ["issue", "create"]]
    assert len(creates) == 1 and "supply-chain" in creates[0]
    body = gh.bodies[-1]
    assert body.splitlines()[0] == deps.REPORT_MARK
    for heading in ("## 脆弱性", "## 解いてよい固定", "## プレリリースを選べる依存"):
        assert heading in body
    for advisory in ("GHSA-jjg7-2v4v-x38h", "GHSA-x84v-xcm2-53pg", "GHSA-5p2g-fcmc-qvqq", "GHSA-w3rx-r6r6-pgpr"):
        assert advisory in body
    # npm の版は lock から引く
    assert "| image-size | 1.2.1 |" in body
    assert {(c[0], c[1]) for c in run.calls} == {("uv", "audit"), ("npm", "audit")}
    assert not [c for c in run.calls if "docs/presentations" in " ".join(c)], "ignorePaths の lock は検査しない（I9）"
    assert {"section": "issue", "number": 99, "action": "created"} in result.items


def test_i2_report_rewrites_the_managed_issue(tmp_path):
    """I2: 目印と作成者で引いた 1 件を書き直し、2 件目を作らない。"""
    root = report_tree(tmp_path)
    result, _, _, gh = run_report(root, gh=FakeGh([managed()]))
    assert [c[:3] for c in gh.writes()] == [["issue", "edit", "5"]]
    assert gh.bodies[-1].splitlines()[0] == deps.REPORT_MARK
    assert {"section": "issue", "number": 5, "action": "edited"} in result.items


def test_i2_report_does_not_rewrite_the_same_body(tmp_path):
    """I2: 同じ入力なら同じ本文で、書き直さない（並行と排他の冪等性）。"""
    root = report_tree(tmp_path)
    first = FakeGh()
    run_report(root, gh=first)
    body = first.bodies[-1]
    again = FakeGh([{"number": 5, "author": BOT, "body": body}])
    result, _, _, _ = run_report(root, gh=again)
    assert again.writes() == []
    assert {"section": "issue", "number": 5, "action": "unchanged"} in result.items


def test_3691_6_report_closes_the_issue_when_nothing_is_left(tmp_path):
    """3691:6: 3 つの表がどれも 0 行なら、管理の Issue を書き直して閉じる。"""
    root = report_tree(tmp_path)
    run = FakeRun(root, uv=(0, UV_AUDIT_NONE), npm=(0, NPM_AUDIT_NONE))
    result, _, _, gh = run_report(root, run=run, gh=FakeGh([managed()]))
    assert [c[:3] for c in gh.writes()] == [["issue", "edit", "5"], ["issue", "close", "5"]]
    assert {"section": "issue", "number": 5, "action": "closed"} in result.items
    assert result.status == "ok"


def test_report_with_nothing_to_tell_and_no_issue_writes_nothing(tmp_path):
    """0 行で管理の Issue も無ければ、何も書かない。"""
    root = report_tree(tmp_path)
    run = FakeRun(root, uv=(0, UV_AUDIT_NONE), npm=(0, NPM_AUDIT_NONE))
    result, _, _, gh = run_report(root, run=run)
    assert gh.writes() == []
    assert result.status == "ok"


def test_i2_a_labelled_issue_without_the_mark_is_not_touched(tmp_path):
    """I2: 同じラベルで目印の無い Issue（人の報告）には触れず、自分の 1 件を作る。"""
    root = report_tree(tmp_path)
    human = {"number": 7, "author": HUMAN, "body": "脆弱性を見つけた", "labels": [{"name": "supply-chain"}]}
    _, _, _, gh = run_report(root, gh=FakeGh([human]))
    assert not [c for c in gh.writes() if "7" in c]
    assert [c[:2] for c in gh.writes() if c[0] == "issue"] == [["issue", "create"]]


@pytest.mark.parametrize(
    "issues",
    [[managed(5), managed(6)], [managed(5, author=HUMAN)]],
    ids=["two-marked", "marked-by-someone-else"],
)
def test_i2_ambiguous_managed_issue_stops_without_touching(tmp_path, issues):
    """I2 の異常: 目印つきが 2 件以上か、目印を持つのに作成者が違えば、どれにも触れずに config_broken で止まる。"""
    root = report_tree(tmp_path)
    gh = FakeGh(issues)
    with pytest.raises(deps.Stop) as e:
        run_report(root, gh=gh)
    assert e.value.cause == "config_broken"
    assert gh.writes() == []


# Task 10: 取得の失敗（I11）


def failing_inputs(root: Path) -> dict[str, tuple]:
    changed_schema = json.loads(UV_AUDIT_VULNS)
    changed_schema["schema"]["version"] = "1"
    return {
        "uv-audit-exit-2": (FakeRun(root, uv=(2, "")), FakeFetch(pages()), FakeGh([managed()])),
        "uv-audit-schema-changed": (FakeRun(root, uv=(1, json.dumps(changed_schema))), FakeFetch(pages()), FakeGh([managed()])),
        "uv-audit-exit-0-with-vulns": (FakeRun(root, uv=(0, UV_AUDIT_VULNS)), FakeFetch(pages()), FakeGh([managed()])),
        "npm-audit-error": (FakeRun(root, npm=(1, NPM_AUDIT_ERROR)), FakeFetch(pages()), FakeGh([managed()])),
        "npm-audit-not-json": (FakeRun(root, npm=(1, "npm error")), FakeFetch(pages()), FakeGh([managed()])),
        "pypi-partner-404": (
            FakeRun(root),
            FakeFetch({**pages(), "https://pypi.org/pypi/githubkit/0.18.0/json": None}),
            FakeGh([managed()]),
        ),
        "pypi-direct-dep-404": (FakeRun(root), FakeFetch({**pages(), "https://pypi.org/pypi/httpx/json": None}), FakeGh([managed()])),
        "gh-list-fails": (FakeRun(root), FakeFetch(pages()), FakeGh([managed()], fail_list=True)),
    }


@pytest.mark.parametrize(
    "case",
    [
        "uv-audit-exit-2",
        "uv-audit-schema-changed",
        "uv-audit-exit-0-with-vulns",
        "npm-audit-error",
        "npm-audit-not-json",
        "pypi-partner-404",
        "pypi-direct-dep-404",
        "gh-list-fails",
    ],
)
def test_i11_a_failed_fetch_leaves_the_issue_untouched(tmp_path, case):
    """I11: 取得が 1 つでも失敗したら、Issue に触れずに transient で止まる（main では終了コード 1）。"""
    root = report_tree(tmp_path)
    run, fetch, gh = failing_inputs(root)[case]
    with pytest.raises(deps.Stop) as e:
        deps.report(root, fetch, gh, run, TODAY)
    assert e.value.cause == "transient"
    assert gh.writes() == []


def test_i11_main_report_exits_1_and_prints_one_line(tmp_path, monkeypatch, capsys):
    """I11: main の report は取得の失敗で stopped・transient の 1 行を出し、終了コード 1 を返す。"""
    root = report_tree(tmp_path)
    gh = FakeGh([managed()])
    monkeypatch.setattr(deps, "run_cmd", FakeRun(root, uv=(2, "")))
    monkeypatch.setattr(deps, "fetch_url", FakeFetch(pages()))
    monkeypatch.setattr(deps, "gh_cli", gh)
    assert deps.main(["report", "--root", str(root)]) == 1
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1
    assert (json.loads(out[0])["status"], json.loads(out[0])["cause"]) == ("stopped", "transient")
    assert gh.writes() == []


def test_report_stops_on_a_broken_hold_rule(tmp_path):
    """read_holds の欄の欠けは config_broken で止まり、何も取得しない。"""
    config = report_config()
    config["packageRules"][1]["description"].pop(2)
    root = report_tree(tmp_path, config)
    run = FakeRun(root)
    gh = FakeGh()
    with pytest.raises(deps.Stop) as e:
        deps.report(root, FakeFetch(pages()), gh, run, TODAY)
    assert e.value.cause == "config_broken"
    assert run.calls == [] and gh.calls == []


# Task 10: 解いてよい固定の判定（#3691 の受け入れ条件 6 の固定・決定 5）


def holds_reported(tmp_path: Path, config: dict | None = None, **page_args) -> tuple[list[dict], object, FakeFetch]:
    root = report_tree(tmp_path, config)
    fetch = FakeFetch(pages(**page_args))
    result, _, _, _ = run_report(root, fetch=fetch)
    return items_of(result, "hold"), result, fetch


def test_3691_6_partner_dropping_the_constraint_is_reported(tmp_path):
    """相手の採用できる最新の版の requires_dist から httpx の行が消えたら「制約の消失」。"""
    found, _, fetch = holds_reported(tmp_path, requires=["anyio<5.0.0,>=3.6.1"])
    assert [(h["package"], h["judgement"]) for h in found] == [("httpx", "制約の消失")]
    # 相手の版は公開から 7 日たった最新（0.18.0）で、3 日前の 0.19.0 は読まない
    assert "https://pypi.org/pypi/githubkit/0.19.0/json" not in fetch.urls


def test_3691_6_partner_relaxing_the_constraint_is_reported_with_the_version(tmp_path):
    """新しい制約は許し、`制約:` は許さない 7 日たった版（httpx 1.0.0）があれば「緩和」とその版。"""
    found, _, _ = holds_reported(tmp_path, requires=["httpx<2.0.0,>=0.23.0"])
    assert [(h["package"], h["judgement"], h["versions"]) for h in found] == [("httpx", "緩和", ["1.0.0"])]


def test_relaxing_without_an_aged_newer_version_is_not_reported(tmp_path):
    """緩和の境界: 新しく許す版が公開から 6 日なら出さない。"""
    found, _, _ = holds_reported(tmp_path, requires=["httpx<2.0.0,>=0.23.0"], httpx={"0.28.1": ago(100), "1.0.0": ago(6)})
    assert found == []


@pytest.mark.parametrize(
    "requires",
    [
        ["httpx>=0.23.0,<1.0.0"],
        ["httpx<1,>=0.23"],
        ['httpx<1.0.0,>=0.23.0; extra == "auth"'],
    ],
    ids=["reordered", "same-meaning", "requested-extra"],
)
def test_equivalent_constraints_are_not_reported(tmp_path, requires):
    """同値（並びの違い・同じ意味の書き換え・プロジェクトが要る extra の行）は出さない。"""
    found, result, _ = holds_reported(tmp_path, requires=requires)
    assert found == []
    assert items_of(result, "tightened") == []


def test_tightened_constraint_is_kept_in_items_only(tmp_path):
    """強化は「解いてよい固定」に出さず、結果 JSON の items に tightened として残すだけ。"""
    root = report_tree(tmp_path)
    result, _, _, gh = run_report(
        root, fetch=FakeFetch(pages(requires=["httpx<0.28.0,>=0.23.0"], httpx={"0.27.0": ago(400), "0.28.1": ago(100)}))
    )
    assert items_of(result, "hold") == []
    assert [i["package"] for i in items_of(result, "tightened")] == ["httpx"]
    assert "tightened" not in gh.bodies[-1]


@pytest.mark.parametrize(
    "line",
    [
        'httpx<1.0.0,>=0.23.0; python_version < "3.10"',
        'httpx<1.0.0,>=0.23.0; sys_platform == "win32"',
        'httpx<1.0.0,>=0.23.0; extra == "all"',
    ],
    ids=["python-below-the-floor", "other-platform", "extra-not-requested"],
)
def test_a_line_whose_marker_is_false_counts_as_absent(tmp_path, line):
    """マーカーはプロジェクトの環境（requires-python の下限・linux・要る extra）で評価し、偽になる行は無いものとして扱う。"""
    found, _, _ = holds_reported(tmp_path, requires=[line])
    assert [h["judgement"] for h in found] == ["制約の消失"]


@pytest.mark.parametrize("days_left, shown", [(0, False), (-1, True)], ids=["review-today", "review-yesterday"])
def test_past_review_date_is_reported(tmp_path, days_left, shown):
    """見直す日が今日ちょうどは出さず、前日は出す（種類によらない。dbos は対象の外）。"""
    found, _, _ = holds_reported(tmp_path, report_config(review=TODAY + timedelta(days=days_left)))
    assert [(h["package"], h["judgement"]) for h in found] == ([("dbos", "見直す日を過ぎた")] if shown else [])


def test_vulnerability_exception_past_its_removal_date_is_reported(tmp_path):
    """脆弱性の例外は、見直す日（外す日）を過ぎたら「解いてよい固定」に出る（決定 9）。"""
    exception = hold_rule("脆弱性の例外", "GHSA-xxxx-yyyy-zzzz", "requests==2.33.0", ago(1), matchPackageNames=["requests"], enabled=False)
    found, _, _ = holds_reported(tmp_path, report_config(extra=[exception]))
    assert [(h["package"], h["kind"], h["judgement"]) for h in found] == [("requests", "脆弱性の例外", "見直す日を過ぎた")]


# Task 10: プレリリースを選べる依存（決定 4）


def prerelease_reported(tmp_path: Path, githubkit: dict[str, date], listed: list[str] | None = None) -> list[dict]:
    root = report_tree(tmp_path, report_config(listed=listed))
    table = pages()
    table["https://pypi.org/pypi/githubkit/json"] = pypi_page(githubkit)
    # 相手（githubkit）の採用できる版がどれになっても、制約は同じ
    for v in githubkit:
        table.setdefault(f"https://pypi.org/pypi/githubkit/{v}/json", version_page(PARTNER_REQUIRES))
    result, _, _, _ = run_report(root, fetch=FakeFetch(table))
    return items_of(result, "prerelease")


@pytest.mark.parametrize(
    "stable_age, pre_age, shown",
    [(366, 7, True), (365, 7, False), (366, 6, False)],
    ids=["stable-366-days", "stable-365-days", "prerelease-6-days"],
)
def test_prerelease_is_offered_when_the_stable_is_older_than_365_days(tmp_path, stable_age, pre_age, shown):
    """7 日たった最新の安定版が 365 日より古く、それより新しいプレリリースが 7 日たっていれば出す（365 日ちょうどは出さない）。"""
    found = prerelease_reported(tmp_path, {"0.17.1": ago(stable_age), "1.0.0b1": ago(pre_age)})
    assert [(p["package"], p["judgement"]) for p in found] == ([("githubkit", "プレリリースを選べる")] if shown else [])


def test_listed_dependency_with_a_recent_stable_can_leave_the_list(tmp_path):
    """規則 6 の一覧にある依存で、7 日たった最新の安定版が 365 日以内になったら「一覧から外してよい」。"""
    found = prerelease_reported(tmp_path, {"0.17.1": ago(30), "1.0.0b1": ago(20)}, listed=["githubkit"])
    assert [(p["package"], p["judgement"]) for p in found] == [("githubkit", "一覧から外してよい")]


def test_path_dependencies_are_not_fetched(tmp_path):
    """`[tool.uv.sources]` のパスの依存は PyPI に問わない。"""
    _, _, fetch = holds_reported(tmp_path)
    assert not [u for u in fetch.urls if "local" in u]


# Task 10: ワークフロー（I5・I6）


def test_i5_supply_chain_report_runs_only_on_schedule_and_dispatch():
    """I5: supply-chain-report は週 1 回の定時と手動でだけ走り、PR では走らない。権限は issues: write と contents: read。"""
    wf = yaml.safe_load(DEPS_WEEKLY.read_text(encoding="utf-8"))
    on = triggers(wf)
    assert on["schedule"] == [{"cron": "0 0 * * 1"}]
    assert "workflow_dispatch" in on
    job = wf["jobs"]["supply-chain-report"]
    # 同じワークフローに PR で走る別のジョブ（renovate-config）があっても、このジョブは定時と手動に限る
    assert job["if"] == "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"
    assert job["permissions"] == {"contents": "read", "issues": "write"}
    assert job["concurrency"] == {"group": "supply-chain-report", "cancel-in-progress": False}
    runs = [s.get("run", "") for s in job["steps"]]
    assert "uv run --frozen --project . python scripts/plugin-deps.py report" in runs


def test_i6_report_waits_the_same_days_as_uv():
    """I6: report が数える待つ日数は plugins/ndf/pyproject.toml の exclude-newer と同じ。"""
    assert deps.WAIT_DAYS == seven_days()


# --- Task 13: Actions の SHA の固定（単位 7・#3691 の受け入れ条件 4・不変条件 I10） ---

WORKFLOWS = REPO / ".github" / "workflows"
# I10 の形（外部の Action の参照は 40 桁の SHA と具体的なバージョンの注記）
PIN_FORM = re.compile(r"@[0-9a-f]{40} # v[0-9]+\.[0-9]+\.[0-9]+")
USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$")
# 記録した `gh api` の応答（2026-11-21 に引いた実物）。軽量のタグ（commit を直に指す）と注記つきのタグ（tag を経る）
CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
PYPI_TAG_OBJECT = "aef6c9b6e301dc803e1bc9133e9ce67ae9b0906b"
PYPI_COMMIT = "7245906fb61fa33a13554f9d5fb856fe61758086"
RECORDED_TAGS = {
    "api repos/actions/checkout/git/ref/tags/v7.0.1": {
        "ref": "refs/tags/v7.0.1",
        "object": {"sha": CHECKOUT_SHA, "type": "commit"},
    },
    "api repos/pypa/gh-action-pypi-publish/git/ref/tags/v1.14.3": {
        "ref": "refs/tags/v1.14.3",
        "object": {"sha": PYPI_TAG_OBJECT, "type": "tag"},
    },
    f"api repos/pypa/gh-action-pypi-publish/git/tags/{PYPI_TAG_OBJECT}": {
        "sha": PYPI_TAG_OBJECT,
        "tag": "v1.14.3",
        "object": {"sha": PYPI_COMMIT, "type": "commit"},
    },
}


class RecordedGh:
    """記録した `gh api` の応答を返す。記録に無い呼び出しと `fail` の呼び出しは CalledProcessError。"""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[str] = []

    def __call__(self, args: list[str]) -> str:
        key = " ".join(args)
        self.calls.append(key)
        if self.fail or key not in RECORDED_TAGS:
            raise subprocess.CalledProcessError(1, ["gh", *args], stderr="HTTP 404: Not Found")
        return json.dumps(RECORDED_TAGS[key])


def commit_all(path: Path) -> str:
    git(path, "add", "-A")
    git(path, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "x")
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True).stdout.strip()


def workflow(*uses: str) -> str:
    steps_ = "".join(f"      - uses: {u}\n" for u in uses)
    return f"name: x\non: push\njobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n{steps_}"


@pytest.fixture
def pin_tree(tmp_path):
    """base に 1 本のワークフローを置いた木。テストは head の中身を書いて (root, base) から action_pins を打つ。"""
    root = init_repo(tmp_path, {".github/workflows/a.yml": workflow("actions/checkout@v7", "./.github/actions/local")})
    return root, commit_all(root)


def pins_on(pin_tree, *uses: str, gh=None):
    root, base = pin_tree
    (root / ".github/workflows/a.yml").write_text(workflow(*uses, "./.github/actions/local"), encoding="utf-8")
    head = commit_all(root)
    return deps.action_pins(root, base, head, gh or RecordedGh())


def test_i10_every_external_action_is_pinned_to_a_sha_with_a_version_note():
    """I10（#3691 の受け入れ条件 4）: `.github/workflows/*.yml` の外部の Action の参照はすべて 40 桁の SHA と版の注記の形。"""
    bad = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        for n, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), 1):
            m = USES_LINE.match(line)
            if not m or m.group(1).startswith("./"):
                continue
            if not PIN_FORM.fullmatch(line[line.index("@", m.start(1)) :].rstrip()):
                bad.append(f"{wf.name}:{n}: {line.strip()}")
    assert bad == []


def test_i10_a_tag_reference_breaks_the_form():
    """I10 の異常: タグで参照する行（`@v7`）を足すと形の検査に当たる。"""
    assert not PIN_FORM.fullmatch("@v7")
    assert not PIN_FORM.fullmatch(f"@{CHECKOUT_SHA}")
    assert PIN_FORM.fullmatch(f"@{CHECKOUT_SHA} # v7.0.1")


def test_i10_script_structure_runs_action_pins_on_the_merge_commit():
    """I10: script-structure.yml が `action-pins` を PR のマージのコミットとその第 1 親で打ち、gh の token を渡す。"""
    found = [s for s in steps(load_workflow()) if "plugin-deps.py action-pins" in s.get("run", "")]
    assert len(found) == 1
    step = found[0]
    assert "--base HEAD^1 --head HEAD" in step["run"]
    assert "GH_TOKEN" in step.get("env", {})


def test_i10_action_pins_passes_a_sha_equal_to_the_tag_commit(pin_tree):
    """action_pins の正常（I10）: 注記のタグ（軽量のタグ）が指すコミットと同じ SHA の行を通す。"""
    gh = RecordedGh()
    result = pins_on(pin_tree, f"actions/checkout@{CHECKOUT_SHA} # v7.0.1", gh=gh)
    assert result.status == "ok"
    assert result.items == [
        {"path": ".github/workflows/a.yml", "line": 7, "action": "actions/checkout", "tag": "v7.0.1", "sha": CHECKOUT_SHA}
    ]
    assert gh.calls == ["api repos/actions/checkout/git/ref/tags/v7.0.1"]


def test_i10_action_pins_follows_an_annotated_tag_to_its_commit(pin_tree):
    """action_pins の境界（I10）: 注記つきのタグは git/tags を辿り、指す先のコミットの SHA と比べる。"""
    gh = RecordedGh()
    result = pins_on(pin_tree, f"pypa/gh-action-pypi-publish@{PYPI_COMMIT} # v1.14.3", gh=gh)
    assert result.status == "ok"
    assert gh.calls[-1] == f"api repos/pypa/gh-action-pypi-publish/git/tags/{PYPI_TAG_OBJECT}"


def test_i10_action_pins_rejects_the_annotated_tag_object_sha(pin_tree):
    """action_pins の異常（I10）: 注記つきのタグのオブジェクトの SHA はコミットでないので止まる。"""
    with pytest.raises(deps.Stop) as e:
        pins_on(pin_tree, f"pypa/gh-action-pypi-publish@{PYPI_TAG_OBJECT} # v1.14.3")
    assert e.value.cause == "check_failed"


def test_i10_action_pins_stops_on_a_sha_different_from_the_note(pin_tree):
    """action_pins の異常（I10）: 注記と違う SHA で check_failed と行を返して止まる。"""
    wrong = "0" * 40
    with pytest.raises(deps.Stop) as e:
        pins_on(pin_tree, f"actions/checkout@{wrong} # v7.0.1")
    assert e.value.cause == "check_failed"
    assert e.value.items == [
        {
            "path": ".github/workflows/a.yml",
            "line": 7,
            "action": "actions/checkout",
            "tag": "v7.0.1",
            "sha": wrong,
            "expected": CHECKOUT_SHA,
        }
    ]


def test_i10_action_pins_stops_on_a_tag_reference(pin_tree):
    """action_pins の異常（I10）: 差分で足した行が SHA と注記の形でなければ止まる（gh を呼ばない）。"""
    gh = RecordedGh()
    with pytest.raises(deps.Stop) as e:
        pins_on(pin_tree, "actions/checkout@v7.0.1", gh=gh)
    assert e.value.cause == "check_failed"
    assert gh.calls == []


def test_i10_action_pins_skips_without_changed_uses_lines(pin_tree):
    """action_pins の境界: 差分に外部の Action の `uses:` の行が無ければ gh を呼ばずに skipped（消した行・`./` の行は見ない）。"""
    root, base = pin_tree
    gh = RecordedGh()
    result = deps.action_pins(root, base, base, gh)
    (root / ".github/workflows/a.yml").write_text(workflow("./.github/actions/other"), encoding="utf-8")
    head = commit_all(root)
    assert (result.status, deps.action_pins(root, base, head, gh).status, gh.calls) == ("skipped", "skipped", [])


def test_i10_action_pins_stops_transient_when_gh_fails(pin_tree):
    """action_pins の異常: タグを引けなければ transient で止まる。"""
    with pytest.raises(deps.Stop) as e:
        pins_on(pin_tree, f"actions/checkout@{CHECKOUT_SHA} # v7.0.1", gh=RecordedGh(fail=True))
    assert e.value.cause == "transient"


def test_i10_main_action_pins_reports_stopped_with_exit_1(pin_tree, monkeypatch, capsys):
    """main の action-pins は注記と違う SHA で結果 JSON を 1 行出し、終了コード 1 を返す。gh はモジュールの gh_cli。"""
    root, base = pin_tree
    (root / ".github/workflows/a.yml").write_text(workflow("actions/checkout@" + "0" * 40 + " # v7.0.1"), encoding="utf-8")
    head = commit_all(root)
    monkeypatch.setattr(deps, "gh_cli", RecordedGh())
    assert deps.main(["action-pins", "--root", str(root), "--base", base, "--head", head]) == 1
    result = json.loads(capsys.readouterr().out)
    assert (result["status"], result["cause"]) == ("stopped", "check_failed")


# --- --help の照らし合わせ（単位 9。#3691 の受け入れ条件 3、決定 11、不変条件 I12 の引数の部分） ---

CLI_FLOORS = REPO / "plugins" / "ndf" / "contracts" / "domains" / "cli-floors.json"
RUNTIMES = REPO / "plugins" / "ndf" / "contracts" / "domains" / "runtimes.json"
SMOKE_LATEST = REPO / ".github" / "workflows" / "runtime-plugin-smoke-latest.yml"
NDF_SRC = REPO / "plugins" / "ndf" / "src" / "ndf"
FLOOR_FILES = ("plugins/ndf/contracts/domains/cli-floors.json", "plugins/ndf/contracts/data/claude-cli.json")


def floor_rows() -> dict[str, dict]:
    return {r["id"]: r for r in json.loads(CLI_FLOORS.read_text(encoding="utf-8"))["rows"]}


def floors_tree(tmp_path: Path, edit=None) -> Path:
    """表の 2 つの JSON だけを持つ木（`floors_ref` の作業ツリーと同じ形）。`edit` は cli-floors.json の dict を直す。"""
    for rel in FLOOR_FILES:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, tmp_path / rel)
    if edit is not None:
        path = tmp_path / FLOOR_FILES[0]
        table = json.loads(path.read_text(encoding="utf-8"))
        edit(table)
        path.write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def synthetic_help(root: Path, runtime: str, drop: str | None = None) -> dict[tuple[str, ...], str]:
    """見本の引数をすべて持つ合成の `--help` の出力（`drop` の引数だけ抜く）。"""
    _, samples = deps.help_samples(root, runtime)
    return {s.args: "Usage\n" + "".join(f"  {f}  説明\n" for f in s.flags if f != drop) for s in samples}


class HostRun:
    """ホストの CLI の代わりに、引数ごとに記録した `--help` の出力を返す。"""

    def __init__(self, helps: dict[tuple[str, ...], str], code: int = 0):
        self.helps, self.code, self.calls = helps, code, []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self.code, self.helps[tuple(argv[1:])], "")


class ImageRun:
    """`docker run ... sh -c <script>` の代わりに、偽の CLI を PATH の先に置いて `<script>` を sh で実際に打つ。"""

    def __init__(self, tmp_path: Path, executable: str, helps: dict[tuple[str, ...], str]):
        self.bin = tmp_path / "image-bin"
        self.bin.mkdir()
        table = tmp_path / "helps.json"
        table.write_text(json.dumps({" ".join(k): v for k, v in helps.items()}), encoding="utf-8")
        cli = self.bin / executable
        cli.write_text(
            f"#!{sys.executable}\nimport json, sys\nh = json.load(open({str(table)!r}))\n"
            "k = ' '.join(sys.argv[1:])\nsys.stdout.write(h.get(k, ''))\nsys.exit(0 if k in h else 2)\n",
            encoding="utf-8",
        )
        cli.chmod(0o755)
        self.home = tmp_path / "home"
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        assert argv[:2] == ["docker", "run"] and argv[-2] == "-c"
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home)}
        return subprocess.run(["sh", "-c", argv[-1]], capture_output=True, text=True, env=env)


@pytest.mark.parametrize("runtime", ["gh", "claude", "codex"])
def test_3691_3_every_sample_flag_in_help_passes(tmp_path, runtime):
    """3691:3: 見本の引数がすべてある `--help`（トップレベルとサブコマンド）で通り、ホストでは見本ごとに 1 回打つ。"""
    root = floors_tree(tmp_path)
    run = HostRun(synthetic_help(root, runtime))
    result = deps.cli_help(root, run, runtime)
    assert result.status == "ok"
    rows = floor_rows()
    assert [c[1:] for c in run.calls] == [s["args"] for s in rows[runtime]["help"]]
    assert {c[0] for c in run.calls} == {rows[runtime]["executable"]}


def test_3691_3_image_runs_one_container_for_all_samples(tmp_path):
    """3691:3: `image` を渡すと、`Run` の呼び出しは見本の数によらず 1 回の `docker run` で、境界の行で見本ごとに分かれる。"""
    root = floors_tree(tmp_path)
    run = ImageRun(tmp_path, "claude", synthetic_help(root, "claude"))
    result = deps.cli_help(root, run, "claude", image="ai-plugins-runtime-smoke-claude")
    assert result.status == "ok", result.summary
    assert len(run.calls) == 1
    argv = run.calls[0]
    assert argv[:-2] == ["docker", "run", "--rm", "--pull", "never", "--entrypoint", "sh", "ai-plugins-runtime-smoke-claude"]
    assert len(result.items) == len(floor_rows()["claude"]["help"]) > 1


def test_3691_3_extra_flags_in_help_still_pass(tmp_path):
    """3691:3 の境界: 見本に無い引数が `--help` に増えても通る。"""
    root = floors_tree(tmp_path)
    helps = {k: v + "  --brand-new-flag  増えた\n  newcommand  増えた\n" for k, v in synthetic_help(root, "codex").items()}
    assert deps.cli_help(root, HostRun(helps), "codex").status == "ok"


def test_3691_3_marker_like_text_in_help_does_not_break_the_split(tmp_path):
    """3691:3 の境界: 出力に境界の行と同じ文字列が無い限り、境界に似た行や改行で終わらない出力があっても見本ごとに分かれる。"""
    root = floors_tree(tmp_path)
    helps = {k: f"0 begin\n1 end 0\n{v}末尾に改行が無い" for k, v in synthetic_help(root, "codex").items()}
    result = deps.cli_help(root, ImageRun(tmp_path, "codex", helps), "codex", image="img")
    assert result.status == "ok", result.summary


@pytest.mark.parametrize("runtime", ["gh", "claude", "codex"])
@pytest.mark.parametrize("image", [None, "img"])
def test_3691_3_a_removed_sample_flag_stops(tmp_path, runtime, image):
    """3691:3: 見本の引数を 1 つ消した `--help` で `check_failed` で止まり、CLI と引数を返す。"""
    root = floors_tree(tmp_path)
    row = floor_rows()[runtime]
    target = row["help"][-1]
    gone = target["flags"][0]
    helps = synthetic_help(root, runtime, drop=gone)
    run = HostRun(helps) if image is None else ImageRun(tmp_path, row["executable"], helps)
    with pytest.raises(deps.Stop) as e:
        deps.cli_help(root, run, runtime, image=image)
    assert e.value.cause == "check_failed"
    assert any(i["args"] == target["args"] and gone in i["missing"] for i in e.value.items)
    assert gone in e.value.summary


def test_3691_3_a_failing_help_command_stops(tmp_path):
    """3691:3: `--help` が 0 以外で終わる（サブコマンドが消えた）と止まる。"""
    root = floors_tree(tmp_path)
    with pytest.raises(deps.Stop) as e:
        deps.cli_help(root, HostRun(synthetic_help(root, "gh"), code=1), "gh")
    assert e.value.cause == "check_failed"
    assert all(i["exit_code"] == 1 for i in e.value.items)


def test_3691_3_a_missing_image_stops(tmp_path):
    """3691:3: `image` を渡してイメージが無い（docker 自身が 125 で終わる）と `check_failed` で止まる。"""
    root = floors_tree(tmp_path)

    def run(argv):
        return subprocess.CompletedProcess(argv, 125, "", "docker: Error response from daemon: No such image: x:latest\n")

    with pytest.raises(deps.Stop) as e:
        deps.cli_help(root, run, "codex", image="x")
    assert e.value.cause == "check_failed"
    assert "最新の CLI のイメージが無い" in e.value.summary


def test_3691_3_output_without_boundaries_stops(tmp_path):
    """3691:3: 見本の区切りが欠けた出力で「出力を見本ごとに分けられない」で止まる。"""
    root = floors_tree(tmp_path)
    help_text = "\n".join(synthetic_help(root, "codex").values())

    def run(argv):
        return subprocess.CompletedProcess(argv, 0, help_text, "")

    with pytest.raises(deps.Stop) as e:
        deps.cli_help(root, run, "codex", image="img")
    assert e.value.cause == "check_failed"
    assert "出力を見本ごとに分けられない" in e.value.summary


def test_3691_3_a_host_cli_that_cannot_start_stops(tmp_path):
    """3691:3: ホストに CLI が無ければ `check_failed` で止まる。"""
    root = floors_tree(tmp_path)

    def run(argv):
        raise FileNotFoundError(argv[0])

    with pytest.raises(deps.Stop) as e:
        deps.cli_help(root, run, "gh")
    assert e.value.cause == "check_failed"


def _drop_help(t):
    next(r for r in t["rows"] if r["id"] == "codex").pop("help")


def _outside_table(t):
    next(r for r in t["rows"] if r["id"] == "claude")["help"][0]["table"] = "../../../scripts/plugin-deps.py"


def _flags_not_list(t):
    next(r for r in t["rows"] if r["id"] == "gh")["help"][0]["flags"] = "api"


def _both_flags_and_table(t):
    next(r for r in t["rows"] if r["id"] == "claude")["help"][0]["flags"] = ["--help"]


@pytest.mark.parametrize(
    ("runtime", "edit"),
    [("codex", _drop_help), ("claude", _outside_table), ("gh", _flags_not_list), ("claude", _both_flags_and_table), ("kiro", None)],
)
def test_3691_3_a_table_of_another_shape_is_config_broken(tmp_path, runtime, edit):
    """3691:3: 表の形が違う `root`（見本が無い・表の参照が data の外・flags が配列でない・行が無い）は `config_broken`。"""
    root = floors_tree(tmp_path, edit)
    with pytest.raises(deps.Stop) as e:
        deps.cli_help(root, HostRun({}), runtime)
    assert e.value.cause == "config_broken"


def test_3691_3_the_claude_top_level_sample_reads_the_args_table():
    """3691:3: claude のトップレベルの見本は data/claude-cli.json の引数とサブコマンドをすべて照らす。"""
    table = json.loads((REPO / FLOOR_FILES[1]).read_text(encoding="utf-8"))
    _, samples = deps.help_samples(REPO, "claude")
    top = next(s for s in samples if s.args == ("--help",))
    assert set(top.flags) == {f for k in deps.HELP_TABLE_KEYS for f in table[k]}


def test_has_word_separates_short_flags_and_prefixed_names():
    """引数は前後を英数字・`_`・`-` に挟まれずにあるときだけ見つける（`-C` と `-c`、`exec` と `exec-server`）。"""
    assert deps.has_word("  -C, --cd <DIR>", "-C")
    assert not deps.has_word("  -c, --config", "-C")
    assert not deps.has_word("  exec-server  実験", "exec")
    assert deps.has_word("  auth:  Authenticate", "auth")


def test_main_cli_help_uses_the_module_run(tmp_path, monkeypatch, capsys):
    """main は子プロセスをモジュールの run_cmd で打つ（差し替えられる）。"""
    root = floors_tree(tmp_path)
    run = HostRun(synthetic_help(root, "gh"))
    monkeypatch.setattr(deps, "run_cmd", run)
    assert deps.main(["cli-help", "--root", str(root), "--runtime", "gh"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"
    assert len(run.calls) == len(floor_rows()["gh"]["help"])


# src/ndf が外部 CLI に渡すサブコマンドとフラグ（I12 の引数の部分）

# 表の実行ファイル → 行の id。名前の定数（`CLAUDE`）と解いたパスの変数（`claude`）も同じ CLI を指す
CLI_OF = {r["executable"]: rid for rid, r in floor_rows().items()}
CLI_NAMES = {**CLI_OF, **{e.upper(): rid for e, rid in CLI_OF.items()}}
# 語の並びの後ろを引数で受け取る関数 → その呼び出し元（ファイル, 呼び出しの名前, 引数の並びの位置）。
# 後ろが利用者の引数の関数は None（トップレベルの claude の引数は data/claude-cli.json の見本が照らす）
DYNAMIC_TAILS = {
    ("adapters/claude.py", "run_plugin"): [("domains/relay/plugin_update.py", "run", 0)],
    ("domains/relay/account_login.py", "_auth"): [("domains/relay/account_login.py", "_auth", 3)],
    ("domains/relay/wrapper.py", "_start"): None,
    ("domains/relay/cli.py", "_passthrough"): None,
}
# claude のトップレベルの引数を組み立てるモジュール（語の並びの頭に実行ファイルを置かない引数の字面）
CLAUDE_TOP_MODULES = ("adapters/claude.py", "domains/relay/wrapper.py")
DASH_WORD = re.compile(r"-{1,2}[A-Za-z][\w-]*")


def _head_cli(node) -> str | None:
    if not isinstance(node, ast.List) or not node.elts:
        return None
    head = node.elts[0]
    name = (
        head.value
        if isinstance(head, ast.Constant)
        else head.id
        if isinstance(head, ast.Name)
        else head.attr
        if isinstance(head, ast.Attribute)
        else None
    )
    return CLI_NAMES.get(name) if isinstance(name, str) else None


def _words(elts) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """語の並びの (サブコマンド, フラグ)。サブコマンドは頭から続く `-` で始まらない字面、フラグは `-` で始まる字面。"""
    path, flags, leading = [], [], True
    for e in elts:
        if isinstance(e, ast.Constant) and isinstance(e.value, str):
            if e.value.startswith("-"):
                flags.append(e.value.split("=", 1)[0])
                leading = False
            elif leading:
                path.append(e.value)
        else:
            leading = False
    return tuple(path), tuple(flags)


def _enclosing(tree) -> dict[int, str]:
    out = {}
    for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        for n in ast.walk(fn):
            out.setdefault(id(n), fn.name)
    return out


def src_invocations(files: dict[str, str]) -> tuple[list[tuple], set[tuple[str, str]]]:
    """src の (CLI, サブコマンド, フラグ, 場所) と、後ろを引数で受け取る語の並びを持つ (ファイル, 関数)。"""
    found, tails = [], set()
    trees = {rel: ast.parse(text) for rel, text in files.items()}
    for rel, tree in trees.items():
        owner = _enclosing(tree)
        headed = set()
        for node in ast.walk(tree):
            cli = _head_cli(node)
            if cli is None:
                continue
            headed.update(id(e) for e in node.elts)
            path, flags = _words(node.elts[1:])
            found.append((cli, path, flags, f"{rel}:{node.lineno}"))
            if any(isinstance(e, ast.Starred) for e in node.elts[1:]):
                tails.add((rel, owner.get(id(node), "")))
        if rel in CLAUDE_TOP_MODULES:
            for node in ast.walk(tree):
                if isinstance(node, (ast.List, ast.Tuple)) and id(node.elts[0] if node.elts else node) not in headed:
                    flags = [
                        e.value
                        for e in node.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str) and DASH_WORD.fullmatch(e.value)
                    ]
                    if flags:
                        found.append(("claude", (), tuple(flags), f"{rel}:{node.lineno}"))
    for (rel, fn), callers in DYNAMIC_TAILS.items():
        if callers is None or rel not in trees:
            continue
        prefix = next(_words(n.elts[1:])[0] for n in ast.walk(trees[rel]) if _head_cli(n) and _enclosing(trees[rel]).get(id(n)) == fn)
        cli = next(_head_cli(n) for n in ast.walk(trees[rel]) if _head_cli(n) and _enclosing(trees[rel]).get(id(n)) == fn)
        for caller_rel, name, at in callers:
            for call in (n for n in ast.walk(trees[caller_rel]) if isinstance(n, ast.Call)):
                f = call.func
                if (f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else "") != name:
                    continue
                elts = call.args[at].elts if isinstance(call.args[at], ast.List) else call.args[at:]
                path, flags = _words(elts)
                found.append((cli, prefix + path, flags, f"{caller_rel}:{call.lineno}"))
    return found, tails


def table_invocations() -> list[tuple]:
    """ランタイムの表の起動の引数（相手役の `review_args` を 2 通りの `{sandbox}` で広げたものと `auth_probe`）。"""
    out = []
    for row in json.loads(RUNTIMES.read_text(encoding="utf-8"))["rows"]:
        cli = CLI_OF.get(row["executable"])
        if cli is None:
            continue
        if "auth_probe" in row:
            out.append((cli, tuple(row["auth_probe"]), (), f"runtimes.json:{row['id']}.auth_probe"))
        for sandbox in ("sandbox_args", "unsandboxed_args"):
            if "review_args" in row and sandbox in row:
                args = [x for a in row["review_args"] for x in (row[sandbox] if a == "{sandbox}" else [a]) if "{" not in x]
                consts = [ast.Constant(a) for a in args]
                path, flags = _words(consts)
                out.append((cli, path, flags, f"runtimes.json:{row['id']}.review_args+{sandbox}"))
    return out


def uncovered(invocations: list[tuple]) -> list[tuple]:
    """見本に無いサブコマンドかフラグを持つ起動。サブコマンドは親の `--help` の見本に、フラグはその `--help` の見本にある。"""
    samples = {cli: {s.args[:-1]: set(s.flags) for s in deps.help_samples(REPO, cli)[1]} for cli in CLI_OF.values()}
    bad = []
    for cli, path, flags, where in invocations:
        known = samples[cli]
        gaps = [w for k, w in enumerate(path) if w not in known.get(path[:k], set())]
        gaps += [f for f in flags if f not in known.get(path, set())]
        if gaps:
            bad.append((where, cli, path, tuple(gaps)))
    return bad


def ndf_sources() -> dict[str, str]:
    return {str(p.relative_to(NDF_SRC)): p.read_text(encoding="utf-8") for p in sorted(NDF_SRC.rglob("*.py"))}


def test_i12_every_cli_argument_from_src_is_in_the_help_samples():
    """I12: src/ndf とランタイムの表が gh・claude・codex に渡すサブコマンドとフラグは、すべて見本にある。"""
    found, _ = src_invocations(ndf_sources())
    assert {c for c, *_ in found} == set(CLI_OF.values())
    assert uncovered(found + table_invocations()) == []


def test_i12_launch_lists_with_a_dynamic_tail_are_known():
    """I12: 語の並びの後ろを引数で受け取る関数はすべて DYNAMIC_TAILS にあり、その呼び出し元の引数も照らされる。"""
    _, tails = src_invocations(ndf_sources())
    assert tails == set(DYNAMIC_TAILS)


def test_i12_adding_an_unsampled_argument_fails():
    """I12: 見本に無いサブコマンドかフラグを渡す起動を src に足すと、照らすテストが落ちる。"""
    added = {
        **ndf_sources(),
        "x/new_gh.py": 'import subprocess\n\ndef prs():\n    return subprocess.run(["gh", "pr", "list", "--json", "number"])\n',
        "x/new_claude.py": 'from ndf.adapters.claude import CLAUDE\n\ndef st():\n    return [CLAUDE, "auth", "status", "--text"]\n',
    }
    found, _ = src_invocations(added)
    bad = {(w.split(":")[0], gaps) for w, _, _, gaps in uncovered(found)}
    assert bad == {("x/new_gh.py", ("pr", "list", "--json")), ("x/new_claude.py", ("--text",))}


# runtime-plugin-smoke-latest.yml の照らし合わせのステップ（詳細設計書の「--help の照らし合わせの置き場」）


def smoke_steps() -> list[dict]:
    wf = yaml.safe_load(SMOKE_LATEST.read_text(encoding="utf-8"))
    return wf["jobs"]["runtime-smoke-latest"]["steps"]


def step_named(name: str) -> tuple[int, dict]:
    found = [(i, s) for i, s in enumerate(smoke_steps()) if s.get("name") == name]
    assert len(found) == 1, name
    return found[0]


def test_i5_smoke_latest_runs_only_on_schedule_and_dispatch_with_floors_ref():
    """I5: 照らし合わせのワークフローは定時と手動でだけ走り、手動の入力に照らす表の ref（floors_ref）を持つ。"""
    wf = yaml.safe_load(SMOKE_LATEST.read_text(encoding="utf-8"))
    on = triggers(wf)
    assert set(on) == {"schedule", "workflow_dispatch"}
    assert on["workflow_dispatch"]["inputs"]["floors_ref"]["default"] == ""


def test_3691_3_checker_comes_from_main_with_three_sparse_files():
    """3691:3: 検証器は main の先端の 3 ファイルだけを sparse で取り、資格を残さない。floors_ref の木は 2 つの JSON だけ。"""
    tool = [s for s in smoke_steps() if s.get("with", {}).get("path") == "tmp/deps-tool"]
    floors = [s for s in smoke_steps() if s.get("with", {}).get("path") == "tmp/deps-floors"]
    assert len(tool) == len(floors) == 1
    for step, files in ((tool[0], {"scripts/plugin-deps.py", *FLOOR_FILES}), (floors[0], set(FLOOR_FILES))):
        w = step["with"]
        assert step["uses"].startswith("actions/checkout@")
        assert (w["fetch-depth"], w["persist-credentials"], w["sparse-checkout-cone-mode"]) == (1, False, False)
        assert set(w["sparse-checkout"].split()) == files
    assert tool[0]["with"]["ref"] == "main"
    assert floors[0]["with"]["ref"] == "${{ inputs.floors_ref }}"
    assert "inputs.floors_ref != ''" in floors[0]["if"] and "inputs.floors_ref != 'main'" in floors[0]["if"]


def test_3691_3_help_steps_run_after_the_smoke_and_before_the_report():
    """3691:3: 2 つの照らし合わせは成果物の後・課題の報告の前に、スモークの成否によらず走る。gh は claude の行だけで打つ。"""
    upload, _ = step_named("Upload smoke artifacts")
    report, _ = step_named("Report the failure as an issue")
    cli_at, cli = step_named("Check CLI --help against the floors table")
    gh_at, gh = step_named("Check gh --help against the floors table")
    assert upload < cli_at < gh_at < report
    assert cli["if"] == "${{ !cancelled() }}"
    assert gh["if"] == "${{ !cancelled() && matrix.runtime == 'claude' }}"
    assert "tmp/deps-tool/scripts/plugin-deps.py cli-help" in cli["run"]
    assert '--image "ai-plugins-runtime-smoke-$RUNTIME"' in cli["run"] and '--runtime "$RUNTIME"' in cli["run"]
    assert "--runtime gh" in gh["run"] and "--image" not in gh["run"]
    for step in (cli, gh):
        assert "inputs." not in step["run"]
        assert "tmp/deps-floors" in step["env"]["FLOORS_ROOT"] and "tmp/deps-tool" in step["env"]["FLOORS_ROOT"]
