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
    """I3: 脆弱性の例外（パッケージマネージャー側の指定）は、同じ勧告の ID・版・外す日を持つ規則 12（種類 脆弱性の例外）と
    組になる。規則だけが先にあるのは、例外を 2 つの PR で入れる手順の先行の PR（規則を先に承認する）として通す（r2-5）。"""
    rules = [h for h in deps.read_holds(renovate()) if h.kind == "脆弱性の例外"]
    for name, version, advisory, until in vulnerability_exceptions():
        match = [h for h in rules if h.constraint == f"{name}=={version}"]
        assert match, f"{name}=={version} の例外に規則が無い"
        if advisory:
            assert match[0].partner == advisory and match[0].review.isoformat() == until


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


def test_post_affected_fork_writes_nothing(tmp_path):
    """r2-3: fork の PR（workflow_run の pull_requests が空）は、開いた PR の一覧も引かずに書かない（元の PR を確かめられない）。"""
    gh = CommentGh(comments=[11])
    assert deps.post_affected(data_file(tmp_path), deps.WorkflowRun("o/r", None, SHA_A), gh).status == "skipped"
    assert gh.calls == []


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
    same_repo = "github.event_name == 'pull_request' && github.event.pull_request.head.repo.full_name == github.repository"
    [step] = [s for s in steps(wf) if "plugin-deps.py affected" in s.get("run", "")]
    assert step["if"] == same_repo, "fork の PR では投稿先が無いので一覧を作らない"
    assert "--base HEAD^1 --head HEAD" in step["run"] and "continue-on-error" not in step
    [upload] = [s for s in steps(wf) if str(s.get("uses", "")).startswith("actions/upload-artifact")]
    assert upload["if"] == same_repo
    assert upload["with"]["name"] == "deps-affected" and upload["with"]["if-no-files-found"] == "ignore"
    comment = yaml.safe_load(AFFECTED_WORKFLOW.read_text(encoding="utf-8"))
    run_on = triggers(comment)["workflow_run"]
    assert run_on["workflows"] == [wf["name"]] and run_on["types"] == ["completed"]
    [job] = comment["jobs"].values()
    assert "github.event.workflow_run.pull_requests[0].number" in job["if"], "投稿先が空（fork）ならジョブを省く"
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


# --- 公開から 7 日の検査（単位 3。Task 7・#3370 の受け入れ条件 6・不変条件 I1・I6・決定 2・6・9） ---

REQUIRED_CHECKS = REPO / "scripts" / "required-checks-allow.json"
PROJECT_DECL = REPO / ".ndf" / "project.json"
AGE_NOW = deps.datetime(2026, 11, 21, 3, 0, tzinfo=deps.timezone.utc)


def stamp_ago(**delta) -> str:
    """AGE_NOW から delta だけ前の時刻（PyPI と npm の ISO の形）。"""
    return (AGE_NOW - deps.timedelta(**delta)).isoformat().replace("+00:00", "Z")


def age_uv_lock(*packages: tuple[str, str], extra: str = "") -> str:
    blocks = ['version = 1\nrevision = 3\nrequires-python = ">=3.11"\n']
    blocks += [f'[[package]]\nname = "{n}"\nversion = "{v}"\nsource = {{ registry = "https://pypi.org/simple" }}\n' for n, v in packages]
    blocks.append('[[package]]\nname = "proj"\nversion = "0.0.0"\nsource = { virtual = "." }\n' + extra)
    return "\n".join(blocks)


def age_npm_lock(*packages: tuple[str, str]) -> str:
    entries = {"": {"name": "deck", "version": "0.0.0"}, "node_modules/local": {"resolved": "lib/local", "link": True}}
    for name, version in packages:
        tail = name.rsplit("/", 1)[-1]
        entries[f"node_modules/{name}"] = {"version": version, "resolved": f"https://registry.npmjs.org/{name}/-/{tail}-{version}.tgz"}
    return json.dumps({"name": "deck", "lockfileVersion": 3, "packages": entries}, indent=2)


def exception_rule(constraint: str) -> dict:
    """脆弱性の例外の規則 12（決定 9）。"""
    name = constraint.split("==", 1)[0]
    return {
        "description": [
            "種類: 脆弱性の例外",
            "相手: GHSA-xxxx-yyyy-zzzz",
            f"制約: {constraint}",
            "見直す日: 2026-12-31",
            "理由: 7 日に満たない直しを入れる",
        ],
        "matchPackageNames": [name],
        "enabled": False,
    }


class Registry:
    """PyPI の JSON API と npm のレジストリの記録した応答。無い版は 404 の FetchError。"""

    def __init__(self, pypi: dict | None = None, npm: dict | None = None):
        self.pypi = pypi or {}  # (名前, 版) → アップロードの時刻の並び
        self.npm = npm or {}  # 名前 → {版: 時刻}
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        m = re.fullmatch(r"https://pypi\.org/pypi/([^/]+)/([^/]+)/json", url)
        if m and (m.group(1), m.group(2)) in self.pypi:
            files = [{"upload_time_iso_8601": t} for t in self.pypi[(m.group(1), m.group(2))]]
            return json.dumps({"info": {"version": m.group(2)}, "urls": files}).encode()
        m = re.fullmatch(r"https://registry\.npmjs\.org/(.+)", url)
        if m and m.group(1).replace("%2F", "/") in self.npm:
            return json.dumps({"time": {"created": "2016-01-01T00:00:00.000Z", **self.npm[m.group(1).replace("%2F", "/")]}}).encode()
        raise deps.FetchError(f"{url}: HTTP Error 404: Not Found")


@pytest.fixture()
def age_tree(tmp_path: Path):
    """宛先の先頭（HEAD^1）に base、マージのコミット（HEAD）に head を置いた木（作業ツリーは HEAD）。None は消す。"""

    def make(base: dict[str, str], head: dict[str, str | None], rules: list[dict] | None = None) -> Path:
        git(tmp_path, "init", "-q")
        git(tmp_path, "config", "user.email", "t@example.invalid")
        git(tmp_path, "config", "user.name", "t")
        config = {"ignorePaths": ["docs/presentations/**"], "packageRules": rules or []}
        for rel_path, text in {".github/renovate.json": json.dumps(config), **base}.items():
            (tmp_path / rel_path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel_path).write_text(text, encoding="utf-8")
        git(tmp_path, "add", "-A")
        git(tmp_path, "commit", "-q", "-m", "base")
        for rel_path, text in head.items():
            if text is None:
                (tmp_path / rel_path).unlink()
                continue
            (tmp_path / rel_path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel_path).write_text(text, encoding="utf-8")
        git(tmp_path, "add", "-A")
        git(tmp_path, "commit", "-q", "--allow-empty", "-m", "head")
        return tmp_path

    return make


def run_age(
    root: Path,
    fetch,
    renovate_version: str | None = None,
    base: str | None = "HEAD^1",
    head: str | None = "HEAD",
    config_ref: str | None = None,
):
    return deps.age(root, base, head, fetch, AGE_NOW, renovate_version, config_ref)


@pytest.mark.parametrize(
    ("delta", "passes"),
    [
        ({"days": 7}, True),
        ({"days": 30}, True),
        ({"days": 6, "hours": 23, "minutes": 59}, False),
        ({"days": 6}, False),
    ],
)
def test_i1_age_boundary_between_six_and_seven_days(age_tree, delta, passes):
    """I1 の境界: lock に入った版は公開からちょうど 7 日で通り、6 日（と 7 日の 1 分前）で check_failed で止まる。"""
    root = age_tree({"uv.lock": age_uv_lock(("anyio", "4.15.0"))}, {"uv.lock": age_uv_lock(("anyio", "4.16.0"))})
    fetch = Registry(pypi={("anyio", "4.16.0"): [stamp_ago(**delta), stamp_ago(**delta, seconds=-5)]})
    if passes:
        result = run_age(root, fetch)
        assert result.status == "ok"
        assert [(i["name"], i["version"], i["verdict"]) for i in result.items] == [("anyio", "4.16.0", "ok")]
    else:
        with pytest.raises(deps.Stop) as e:
            run_age(root, fetch)
        assert e.value.cause == "check_failed"
        assert [(i["name"], i["verdict"]) for i in e.value.items] == [("anyio", "too_new")]
    assert fetch.urls == ["https://pypi.org/pypi/anyio/4.16.0/json"]


def test_i1_age_uses_the_first_upload_of_the_version(age_tree):
    """I1: 公開日はその版の最初のアップロード（後から足したファイルの時刻で 7 日に満たなくしない）。"""
    root = age_tree({"uv.lock": age_uv_lock()}, {"uv.lock": age_uv_lock(("anyio", "4.16.0"))})
    result = run_age(root, Registry(pypi={("anyio", "4.16.0"): [stamp_ago(days=1), stamp_ago(days=8)]}))
    assert result.status == "ok" and result.items[0]["published"] == stamp_ago(days=8)


@pytest.mark.parametrize(
    "registry",
    [
        Registry(),  # PyPI が 404
        Registry(pypi={("anyio", "4.16.0"): []}),  # 版はあるがファイルが無い
    ],
)
def test_i1_age_stops_a_version_without_a_published_date(age_tree, registry):
    """I1 の異常: 公開日の読めない版（取得の失敗・ファイルの無い版）は check_failed で止まる。"""
    root = age_tree({"uv.lock": age_uv_lock()}, {"uv.lock": age_uv_lock(("anyio", "4.16.0"))})
    with pytest.raises(deps.Stop) as e:
        run_age(root, registry)
    assert e.value.cause == "check_failed"
    assert [(i["name"], i["verdict"], i["published"]) for i in e.value.items] == [("anyio", "no_date", None)]
    assert e.value.items[0]["reason"]


def test_i1_age_stops_a_source_without_a_published_date(age_tree):
    """I1 の異常: git の取得元のように公開日を持たない依存は取得せずに止まり、手元のコード（virtual・editable）は見ない。"""
    extra = '\n[[package]]\nname = "tool"\nversion = "1.0"\nsource = { git = "https://example.invalid/tool?rev=x#x" }\n'
    extra += '\n[[package]]\nname = "kit"\nversion = "0.1"\nsource = { editable = "kit" }\n'
    root = age_tree({"uv.lock": age_uv_lock()}, {"uv.lock": age_uv_lock(extra=extra)})
    fetch = Registry()
    with pytest.raises(deps.Stop) as e:
        run_age(root, fetch)
    assert [(i["name"], i["verdict"]) for i in e.value.items] == [("tool", "no_date")]
    assert fetch.urls == []


def test_i1_age_checks_only_versions_new_in_the_diff(age_tree):
    """I1: 前から lock にあった版は取得せず、差分で入った版だけを確かめる（消えた版も見ない）。"""
    base = age_uv_lock(("anyio", "4.15.0"), ("idna", "3.10"), ("gone", "1.0"))
    head = age_uv_lock(("anyio", "4.16.0"), ("idna", "3.10"), ("fresh", "2.0"))
    root = age_tree({"plugins/ndf/uv.lock": base}, {"plugins/ndf/uv.lock": head})
    fetch = Registry(pypi={("anyio", "4.16.0"): [stamp_ago(days=9)], ("fresh", "2.0"): [stamp_ago(days=9)]})
    result = run_age(root, fetch)
    assert result.status == "ok"
    assert sorted(fetch.urls) == ["https://pypi.org/pypi/anyio/4.16.0/json", "https://pypi.org/pypi/fresh/2.0/json"]
    assert {i["where"] for i in result.items} == {"plugins/ndf/uv.lock"}


def test_i1_age_checks_npm_lock_versions(age_tree):
    """I1: npm の lock に入った版（スコープつきを含む）は npm のレジストリの time で確かめ、link の項目は見ない。"""
    lock = f"{SLIDES}/package-lock.json"
    root = age_tree({lock: age_npm_lock(("pptxgenjs", "4.0.0"))}, {lock: age_npm_lock(("pptxgenjs", "4.0.1"), ("@types/node", "22.20.6"))})
    fetch = Registry(npm={"pptxgenjs": {"4.0.1": stamp_ago(days=10)}, "@types/node": {"22.20.6": stamp_ago(days=2)}})
    with pytest.raises(deps.Stop) as e:
        run_age(root, fetch)
    verdicts = {i["name"]: i["verdict"] for i in e.value.items}
    assert verdicts == {"pptxgenjs": "ok", "@types/node": "too_new"}
    assert "https://registry.npmjs.org/@types%2Fnode" in fetch.urls


def test_i9_age_skips_locks_outside_the_renovate_targets(age_tree):
    """I9: ignorePaths の lock（docs/presentations/）は Renovate の対象の外で、age も見ない。"""
    lock = "docs/presentations/x/slides/package-lock.json"
    root = age_tree({lock: age_npm_lock()}, {lock: age_npm_lock(("pptxgenjs", "9.9.9"))})
    fetch = Registry()
    assert run_age(root, fetch).status == "skipped"
    assert fetch.urls == []


def test_i1_age_checks_a_new_uv_version_once(age_tree):
    """I1: UV_VERSION の 2 か所に入った版は、PyPI の uv の同じ版の公開日で 1 回だけ確かめる（決定 12）。"""
    base = {p: (REPO / p).read_text(encoding="utf-8") for p in deps.UV_PIN_FILES}
    head = {p: deps.UV_VERSION_RE.sub(lambda m: m.group(1) + "0.13.8" + m.group(3), t) for p, t in base.items()}
    root = age_tree(base, head)
    fetch = Registry(pypi={("uv", "0.13.8"): [stamp_ago(days=3)]})
    with pytest.raises(deps.Stop) as e:
        run_age(root, fetch)
    assert [(i["name"], i["version"], i["verdict"]) for i in e.value.items] == [("uv", "0.13.8", "too_new")]
    assert fetch.urls == ["https://pypi.org/pypi/uv/0.13.8/json"]


def test_i1_age_checks_a_new_renovate_version_in_the_diff(age_tree):
    """I1・決定 2: .github/renovate-version に入った版は npm の renovate の公開日で確かめる。"""
    path = ".github/renovate-version"
    root = age_tree({path: "44.239.1\n"}, {path: "44.240.0\n"})
    fetch = Registry(npm={"renovate": {"44.239.1": stamp_ago(days=40), "44.240.0": stamp_ago(days=1)}})
    with pytest.raises(deps.Stop) as e:
        run_age(root, fetch)
    assert [(i["name"], i["version"], i["where"]) for i in e.value.items] == [("renovate", "44.240.0", path)]
    assert fetch.urls == ["https://registry.npmjs.org/renovate"]


@pytest.mark.parametrize("version", ["44.240", "latest", "44.240.0;rm", "../44.0.0"])
def test_age_rejects_a_renovate_version_of_the_wrong_form(age_tree, version):
    """決定 2: Renovate の版は ^[0-9]+\\.[0-9]+\\.[0-9]+$ の形だけで、違えば取得せずに invalid で止まる。"""
    root = age_tree({}, {})
    fetch = Registry()
    with pytest.raises(deps.Stop) as e:
        run_age(root, fetch, renovate_version=version, base=None, head=None)
    assert e.value.cause == "invalid"
    assert fetch.urls == []


@pytest.mark.parametrize(("constraint", "passes"), [("anyio==4.16.0", True), ("anyio==4.15.9", False), ("idna==4.16.0", False)])
def test_i1_age_passes_only_the_vulnerability_exception_pair(age_tree, constraint, passes):
    """I1・I3・決定 9: 7 日に満たない版は、規則 12（脆弱性の例外）と同じパッケージと版の組だけを通し、例外と違う版は止める。"""
    root = age_tree({"uv.lock": age_uv_lock()}, {"uv.lock": age_uv_lock(("anyio", "4.16.0"))}, rules=[exception_rule(constraint)])
    fetch = Registry(pypi={("anyio", "4.16.0"): [stamp_ago(days=1)]})
    if passes:
        result = run_age(root, fetch)
        assert result.status == "ok" and result.items[0]["verdict"] == "exception"
    else:
        with pytest.raises(deps.Stop) as e:
            run_age(root, fetch)
        assert e.value.cause == "check_failed" and e.value.items[0]["verdict"] == "too_new"


def test_i1_age_ignores_a_vulnerability_exception_added_in_the_same_pr(age_tree):
    """r1-6: 例外は宛先（HEAD^1）の設定から読む。同じ PR で足した例外では、その PR の 7 日に満たない版を通さない。"""
    config = json.dumps({"ignorePaths": ["docs/presentations/**"], "packageRules": [exception_rule("anyio==4.16.0")]})
    root = age_tree({"uv.lock": age_uv_lock()}, {"uv.lock": age_uv_lock(("anyio", "4.16.0")), ".github/renovate.json": config})
    with pytest.raises(deps.Stop) as e:
        run_age(root, Registry(pypi={("anyio", "4.16.0"): [stamp_ago(days=1)]}))
    assert e.value.items[0]["verdict"] == "too_new"


def test_i1_age_checks_a_lock_moved_out_of_the_targets_in_the_same_pr(age_tree):
    """r1-6: 対象の外（ignorePaths）も宛先の設定から読む。同じ PR で lock を対象の外にしても、変えた lock は確かめる。"""
    config = json.dumps({"ignorePaths": ["docs/presentations/**", "app/**"]})
    root = age_tree({"app/uv.lock": age_uv_lock()}, {"app/uv.lock": age_uv_lock(("anyio", "4.16.0")), ".github/renovate.json": config})
    with pytest.raises(deps.Stop) as e:
        run_age(root, Registry(pypi={("anyio", "4.16.0"): [stamp_ago(days=1)]}))
    assert [(i["name"], i["verdict"]) for i in e.value.items] == [("anyio", "too_new")]


def test_i1_age_checks_every_changed_lock_when_the_base_has_no_config(tmp_path):
    """r1-6: 宛先に設定がまだ無い（設定と lock を同じ PR で入れる）ときは、対象の外も例外も無いものとして確かめる。"""
    lock = tmp_path / "docs/presentations/x/slides/package-lock.json"
    lock.parent.mkdir(parents=True)
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.invalid")
    git(tmp_path, "config", "user.name", "t")
    lock.write_text(age_npm_lock(), encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    lock.write_text(age_npm_lock(("pptxgenjs", "9.9.9")), encoding="utf-8")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/renovate.json").write_text(json.dumps({"ignorePaths": ["docs/presentations/**"]}), encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "head")
    with pytest.raises(deps.Stop) as e:
        run_age(tmp_path, Registry(npm={"pptxgenjs": {"9.9.9": stamp_ago(days=1)}}))
    assert [(i["name"], i["verdict"]) for i in e.value.items] == [("pptxgenjs", "too_new")]


def test_age_reads_nothing_but_one_diff_when_no_version_changed(age_tree, monkeypatch):
    """r1-7: 依存に当たるパスが差分に無ければ、git diff を 1 回打つだけで設定も lock も読まずに skipped で終わる。"""
    root = age_tree({"uv.lock": age_uv_lock(("anyio", "4.15.0"))}, {"README.md": "x\n"})
    calls = []
    real = deps.git_out
    monkeypatch.setattr(deps, "git_out", lambda r, *a: calls.append(a[0]) or real(r, *a))
    monkeypatch.setattr(deps, "tracked", lambda r: pytest.fail("git ls-files を打たない"))
    assert run_age(root, Registry()).status == "skipped"
    assert calls == ["diff"]


def test_age_skips_without_fetching_when_no_version_changed(age_tree):
    """差分に lock・UV_VERSION・Renovate の版が無ければ、取得せずに skipped で終わる（すべての PR で打ってよい）。"""
    root = age_tree({"uv.lock": age_uv_lock(("anyio", "4.15.0"))}, {"README.md": "x\n"})
    fetch = Registry()
    result = run_age(root, fetch)
    assert result.status == "skipped"
    assert fetch.urls == []


def test_age_stops_on_an_unreadable_lock(age_tree):
    """読めない lock（衝突のマーカー）は invalid で止まる。"""
    root = age_tree({"uv.lock": age_uv_lock()}, {"uv.lock": "<<<<<<< HEAD\n" + age_uv_lock()})
    with pytest.raises(deps.Stop) as e:
        run_age(root, Registry())
    assert e.value.cause == "invalid"


@pytest.mark.parametrize(("base", "head"), [("HEAD^1", None), (None, None), ("no-such-rev", "HEAD")])
def test_age_stops_on_wrong_arguments(age_tree, base, head):
    """呼び出しの誤り（--base と --head の片方だけ・どちらも無い・読めないコミット）は invalid。"""
    root = age_tree({}, {})
    with pytest.raises(deps.Stop) as e:
        run_age(root, Registry(), base=base, head=head)
    assert e.value.cause == "invalid"


@pytest.mark.parametrize(("days_old", "code", "status"), [(8, 0, "ok"), (2, 1, "stopped")])
def test_main_age_renovate_version(age_tree, monkeypatch, capsys, days_old, code, status):
    """main の age --renovate-version（renovate-config のジョブと renovate.yml が打つ形）は結果 JSON を 1 行出す。"""
    root = age_tree({}, {})
    fetch = Registry(npm={"renovate": {"44.239.1": stamp_ago(days=days_old)}})
    monkeypatch.setattr(deps, "fetch_url", fetch)
    assert deps.main(["age", "--root", str(root), "--renovate-version", "44.239.1"]) == code
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == status
    assert fetch.urls == ["https://registry.npmjs.org/renovate"]


def test_age_reads_the_renovate_version_exception_from_the_base_config(age_tree):
    """r2-4・r3-3: renovate-config のジョブの形（--config-ref HEAD^1 と --renovate-version）では、PR で足した Renovate の
    版の例外で 7 日に満たない版を通さず、宛先にある例外だけを通す。差分の lock は読まず、版の 1 件だけを取得する。"""
    rule = exception_rule("renovate==44.239.1")
    added = json.dumps({"ignorePaths": [], "packageRules": [rule]})
    fetch = Registry(npm={"renovate": {"44.239.1": stamp_ago(days=1)}})
    root = age_tree({}, {".github/renovate.json": added, "plugins/x/uv.lock": 'version = 1\n[[package]]\nname = "x"\nversion = "1.0"\n'})
    with pytest.raises(deps.Stop) as e:
        run_age(root, fetch, renovate_version="44.239.1", base=None, head=None, config_ref="HEAD^1")
    assert [i["verdict"] for i in e.value.items] == ["too_new"]
    assert fetch.urls == ["https://registry.npmjs.org/renovate"]


def test_age_passes_the_renovate_version_excepted_in_the_base_config(age_tree):
    """r3-3: 宛先（--config-ref）に入った例外の版は 7 日に満たなくても通す。"""
    fetch = Registry(npm={"renovate": {"44.239.1": stamp_ago(days=1)}})
    root = age_tree({}, {}, rules=[exception_rule("renovate==44.239.1")])
    result = run_age(root, fetch, renovate_version="44.239.1", base=None, head=None, config_ref="HEAD^1")
    assert result.status == "ok" and result.items[0]["verdict"] == "exception"


def test_age_config_ref_with_a_diff_is_invalid(age_tree):
    """r3-3: --config-ref は差分の指定と併せない（差分の例外は宛先の --base から読む）。"""
    with pytest.raises(deps.Stop) as e:
        run_age(age_tree({}, {}), Registry(), renovate_version="44.239.1", config_ref="HEAD^1")
    assert e.value.cause == "invalid"


# 公開から 7 日の検査のワークフローと宣言


def test_i1_script_structure_runs_age_on_every_event():
    """I1: script-structure-check は HEAD^1 と HEAD の間で age を条件なしに打ち、lock を読むため 3.11 以上の Python を使う。
    r3-4: affected と age は同じ setup-python の実行ファイルで打ち、uv に Python を別に探させない。"""
    all_steps = steps(load_workflow())
    found = [s for s in all_steps if "plugin-deps.py age" in s.get("run", "")]
    assert len(found) == 1
    run = found[0]["run"]
    assert "--base HEAD^1 --head HEAD" in run and "if" not in found[0]
    # r2-8: 差分はシェルで先に見ず、age の中の 1 回の git diff だけで決める
    assert "git " not in run and run.count("plugin-deps.py age") == 1
    users = [s for s in all_steps if re.search(r"plugin-deps\.py (age|affected)", s.get("run", ""))]
    assert len(users) == 2
    # 2 つの手順が同じ setup-python の手順の出力を指す（手順の id は Python のバージョンに合わせて変わってよい）
    ids = {re.fullmatch(r"\$\{\{ steps\.(\w+)\.outputs\.python-path \}\}", s["env"]["PYTHON"]).group(1) for s in users}
    assert len(ids) == 1
    for s in users:
        assert s["run"].startswith('"$PYTHON" scripts/plugin-deps.py') and "uv run" not in s["run"]
    [setup] = [s for s in all_steps if s.get("id") in ids]
    assert setup["uses"].startswith("actions/setup-python@") and setup["with"]["update-environment"] is False
    assert int(str(setup["with"]["python-version"]).split(".")[1]) >= 11


def load_deps_weekly() -> dict:
    return yaml.safe_load(DEPS_WEEKLY.read_text(encoding="utf-8"))


def test_3370_6_renovate_config_runs_on_renovate_files_only():
    """#3370 の受け入れ条件 6: renovate-config は Renovate の設定と版を変えた PR だけで走り、読み取りの権限だけを持つ。"""
    wf = load_deps_weekly()
    assert sorted(triggers(wf)["pull_request"]["paths"]) == [".github/renovate-version", ".github/renovate.json"]
    job = wf["jobs"]["renovate-config"]
    assert job["if"] == "github.event_name == 'pull_request'"
    assert job["concurrency"] == {"group": "renovate-config-${{ github.event.pull_request.number }}", "cancel-in-progress": True}
    assert (wf.get("permissions"), job.get("permissions", wf.get("permissions"))) == ({"contents": "read"}, {"contents": "read"})


def test_3370_6_renovate_config_validates_with_the_base_checker_first():
    """#3370 の受け入れ条件 6: 宛先の先頭（HEAD^1）の検証器の age を通ってから、その版の validator --strict を打つ。"""
    runs = [s.get("run", "") for s in load_deps_weekly()["jobs"]["renovate-config"]["steps"]]
    age_at = next(i for i, r in enumerate(runs) if " age " in r)
    assert "git show HEAD^1:scripts/plugin-deps.py" in runs[age_at] and "--renovate-version" in runs[age_at]
    # r2-4・r3-3: 例外は宛先の設定から読み（--config-ref）、差分の lock と UV_VERSION は読まない（--base を渡さない）
    assert '"$checker" age --root "$GITHUB_WORKSPACE" --config-ref HEAD^1 --renovate-version' in runs[age_at]
    assert "--base" not in runs[age_at]
    validate_at = next(i for i, r in enumerate(runs) if "renovate-config-validator" in r)
    assert age_at < validate_at
    assert "--strict --no-global .github/renovate.json" in runs[validate_at]
    assert not [r for r in runs if "${{" in r], "run の中で ${{ }} を展開しない（環境変数で渡す）"


@pytest.mark.parametrize(("days_old", "ok"), [(8, True), (2, False), (None, False)])
def test_3370_6_first_config_is_checked_without_the_pr_checker(tmp_path, days_old, ok):
    """r1-5: 宛先の先頭（HEAD^1）に検証器が無い初回も、PR の検証器を打たずにレジストリの公開日で 7 日を確かめる。"""
    step = next(s for s in load_deps_weekly()["jobs"]["renovate-config"]["steps"] if " age " in s.get("run", ""))
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    marker = tmp_path / "pr-checker-ran"
    (repo / "scripts" / "plugin-deps.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n", encoding="utf-8")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "pr"], check=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # 手順は実行の時刻（date）と比べるので、固定の AGE_NOW でなく今から数える
    now = deps.datetime.now(deps.timezone.utc)
    times = {} if days_old is None else {"44.239.1": (now - deps.timedelta(days=days_old)).isoformat().replace("+00:00", "Z")}
    (tmp_path / "packument.json").write_text(json.dumps({"time": times}), encoding="utf-8")
    curl = bin_dir / "curl"
    curl.write_text(f"#!/bin/sh\ncat {tmp_path / 'packument.json'}\n", encoding="utf-8")
    curl.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "RUNNER_TEMP": str(tmp_path), "GITHUB_WORKSPACE": str(repo)}
    p = subprocess.run(
        ["bash", "-e", "-c", step["run"]], cwd=repo, env={**env, "RENOVATE_VERSION": "44.239.1"}, capture_output=True, text=True
    )
    assert (p.returncode == 0) is ok, p.stdout + p.stderr
    assert not marker.exists()


@pytest.mark.parametrize(("text", "ok"), [("44.239.1\n", True), ("44.239\n", False), ("44.239.1; curl x\n", False), ("", False)])
def test_renovate_config_reads_the_version_through_the_format_check(tmp_path, text, ok):
    """決定 2: 版の読み取りの手順は ^[0-9]+\\.[0-9]+\\.[0-9]+$ に合わない版で止まり、合えば出力に置く。"""
    step = next(s for s in load_deps_weekly()["jobs"]["renovate-config"]["steps"] if s.get("id") == "version")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "renovate-version").write_text(text, encoding="utf-8")
    out = tmp_path / "github-output"
    out.write_text("", encoding="utf-8")
    p = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=tmp_path, env={**os.environ, "GITHUB_OUTPUT": str(out)}, capture_output=True)
    assert (p.returncode == 0) is ok
    assert (out.read_text(encoding="utf-8") == f"version={text.strip()}\n") is ok


def test_renovate_config_is_declared_not_required_and_ci_exempt():
    """renovate-config は Renovate の設定を変えた PR にだけ走るので必須にせず、deps-weekly.yml は手元のテストの外に置く。"""
    allow = json.loads(REQUIRED_CHECKS.read_text(encoding="utf-8"))
    assert "renovate-config" in {e["name"] for e in allow["not_required"]}
    exempt = json.loads(PROJECT_DECL.read_text(encoding="utf-8"))["test"]["ci_exempt"]
    assert ".github/workflows/deps-weekly.yml" in {e["job"] for e in exempt}


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


def _foreign_call(t):
    row = next(r for r in t["rows"] if r["id"] == "gh")
    row["executable"] = "sh"
    row["help"].append({"args": ["-c", "touch pwned"], "flags": ["x"]})


def test_3691_3_floors_cannot_choose_the_command_to_run(tmp_path):
    """3691:3: floors_ref の表の executable と信頼済みの表に無い引数は打たない。照らす名前だけを floors から読む。"""
    trusted = floors_tree(tmp_path / "tool")
    floors = floors_tree(tmp_path / "floors", _foreign_call)
    run = HostRun(synthetic_help(trusted, "gh"))
    result = deps.cli_help(trusted, run, "gh", floors=floors)
    assert {c[0] for c in run.calls} == {"gh"}
    assert ["-c", "touch pwned"] not in [c[1:] for c in run.calls]
    # r2-6: 信頼済みの表に無い引数は打たずに untested として残し、既存の引数の見本はマージ前でも照らす
    assert result.status == "ok"
    assert [i["args"] for i in result.items if "untested" in i] == [["-c", "touch pwned"]]
    assert [i for i in result.items if "exit_code" in i]


def test_3691_3_floors_with_only_new_args_runs_nothing(tmp_path):
    """r2-6: floors の見本がどれも信頼済みの表に無い引数なら、何も打たずに skipped で終える（失敗の課題を作らない）。"""

    def only_new(t):
        row = next(r for r in t["rows"] if r["id"] == "gh")
        row["help"] = [{"args": ["new", "--help"], "flags": ["--x"]}]

    trusted = floors_tree(tmp_path / "tool")
    run = HostRun({})
    result = deps.cli_help(trusted, run, "gh", floors=floors_tree(tmp_path / "floors", only_new))
    assert result.status == "skipped" and run.calls == []
    assert [i["args"] for i in result.items] == [["new", "--help"]]


def test_3691_3_floors_flags_are_checked_with_the_trusted_executable(tmp_path):
    """3691:3: floors の表で足した照らす名前は、信頼済みの表の実行ファイルと引数で打った出力に照らす。"""

    def more_flags(t):
        row = next(r for r in t["rows"] if r["id"] == "gh")
        row["executable"] = "sh"
        row["help"][1]["flags"].append("--paginate")

    trusted = floors_tree(tmp_path / "tool")
    floors = floors_tree(tmp_path / "floors", more_flags)
    run = HostRun(synthetic_help(trusted, "gh"))
    with pytest.raises(deps.Stop) as e:
        deps.cli_help(trusted, run, "gh", floors=floors)
    assert any("--paginate" in i["missing"] for i in e.value.items)
    assert {c[0] for c in run.calls} == {"gh"}


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
    # 判断の入力（`launch_inputs.Reader`）が起動できるかを確かめる起動引数。`_start` と同じ `args` の並び
    ("domains/relay/wrapper.py", "__init__"): None,
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
        assert '--root tmp/deps-tool --floors "$FLOORS_ROOT"' in step["run"]
        assert "tmp/deps-floors" in step["env"]["FLOORS_ROOT"] and "tmp/deps-tool" in step["env"]["FLOORS_ROOT"]


# --- 単位 4: Renovate の起動（renovate.yml・renovate_log。#3370 の受け入れ条件 7・不変条件 I5・I8） ---

RENOVATE_WF = REPO / ".github" / "workflows" / "renovate.yml"
GATE_WF = REPO / ".github" / "workflows" / "renovate-gate.yml"
APP_BOT = "ndf-renovate[bot]"
GATE_SHA = "c" * 40
OLD_SHA = "d" * 40


def load_renovate_wf() -> dict:
    return yaml.safe_load(RENOVATE_WF.read_text(encoding="utf-8"))


def load_gate_wf() -> dict:
    return yaml.safe_load(GATE_WF.read_text(encoding="utf-8"))


def fake_gh_bin(tmp_path: Path, answers: dict[str, str], jq: bool = False) -> tuple[dict[str, str], Path]:
    """`gh` の差し替え。引数を 1 行ずつ記録し、引数に含まれる鍵の最初の答えを標準出力へ出す（無ければ失敗）。
    `jq` なら、`--jq` の式を答えの JSON に本物の jq で当てて出す（env を読む式を試す）。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh-calls.jsonl"
    (tmp_path / "gh-answers.json").write_text(json.dumps(answers), encoding="utf-8")
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"answers = json.load(open({str(tmp_path / 'gh-answers.json')!r}))\n"
        "for key, out in answers.items():\n"
        "    if any(key in a for a in sys.argv[1:]):\n"
        f"        if {jq!r} and '--jq' in sys.argv:\n"
        "            import subprocess\n"
        "            jq = sys.argv[sys.argv.index('--jq') + 1]\n"
        "            sys.exit(subprocess.run(['jq', '-r', jq], input=out, text=True).returncode)\n"
        "        print(out)\n"
        "        sys.exit(0)\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}, log


def gh_calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


def run_wf_step(wf: dict, step: dict, cwd: Path, env: dict[str, str]) -> tuple[subprocess.CompletedProcess, dict[str, str]]:
    """手順の run を、ワークフローの env と渡した env で bash -e で打ち、GITHUB_OUTPUT の値を返す。"""
    out = cwd / "github-output"
    out.write_text("", encoding="utf-8")
    full = {**os.environ, **{k: str(v) for k, v in (wf.get("env") or {}).items()}, **env}
    full.update(GITHUB_OUTPUT=str(out), GITHUB_REPOSITORY="o/r")
    p = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=cwd, env=full, capture_output=True, text=True)
    pairs = [line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if line]
    return p, dict(pairs)


def renovate_step(id_or_name: str) -> dict:
    steps_ = load_renovate_wf()["jobs"]["renovate"]["steps"]
    found = [s for s in steps_ if id_or_name in (s.get("id"), s.get("name"))]
    assert len(found) == 1, id_or_name
    return found[0]


def test_i5_renovate_runs_only_on_schedule_and_dispatch():
    """I5: renovate.yml は週 1 回の定時と手動でだけ走り、PR では走らない。実行はリポジトリに 1 つずつ。"""
    wf = load_renovate_wf()
    on = triggers(wf)
    assert set(on) == {"schedule", "workflow_dispatch"}
    assert on["schedule"] == [{"cron": "0 0 * * 1"}]
    assert set(on["workflow_dispatch"]["inputs"]) == {"base_branch", "verify_scenario"}
    assert on["workflow_dispatch"]["inputs"]["verify_scenario"]["options"] == ["unresolvable", "fetch-failure"]
    assert wf["concurrency"] == {"group": "renovate", "cancel-in-progress": False}
    assert wf["permissions"] == {"contents": "read"}


def test_i8_renovate_is_skipped_until_the_app_variable_is_set():
    """I8: App と秘密を置くまで（変数 RENOVATE_APP_CLIENT_ID が空の間）定時の実行はジョブごと飛ぶ。"""
    job = load_renovate_wf()["jobs"]["renovate"]
    assert job["if"] == "vars.RENOVATE_APP_CLIENT_ID != ''"
    app = next(s for s in job["steps"] if s.get("id") == "app")
    assert app["uses"].startswith("actions/create-github-app-token@")
    assert app["with"]["client-id"] == "${{ vars.RENOVATE_APP_CLIENT_ID }}"
    assert app["with"]["private-key"] == "${{ secrets.RENOVATE_APP_PRIVATE_KEY }}"
    assert "app-id" not in app["with"]
    perms = {k: v for k, v in app["with"].items() if k.startswith("permission-")}
    assert perms == {
        "permission-contents": "write",
        "permission-pull-requests": "write",
        "permission-issues": "write",
        "permission-statuses": "write",
        "permission-checks": "read",
    }


def test_3370_7_checks_run_before_the_token_and_the_log_after_renovate():
    """#3370 の受け入れ条件 7: 宛先と版の検証は token を作る前に、PR の無い失敗の知らせは Renovate の失敗の後でも走る。"""
    steps_ = load_renovate_wf()["jobs"]["renovate"]["steps"]
    keys = [s.get("id") or s.get("name") for s in steps_]
    at = {k: i for i, k in enumerate(keys)}
    age_step = renovate_step("Check that the Renovate version is at least 7 days old")
    assert at["base"] < at["version"] < keys.index(age_step["name"]) < at["app"] < at["renovate"]
    assert age_step["run"] == 'python3 scripts/plugin-deps.py age --renovate-version "$RENOVATE_VERSION"'
    log_step = renovate_step("Report updates that failed without a pull request")
    assert keys.index(log_step["name"]) > at["renovate"]
    assert "!cancelled()" in log_step["if"] and "steps.renovate.outcome != 'skipped'" in log_step["if"]
    assert 'renovate-log --log "$RENOVATE_LOG_FILE" --summary "$GITHUB_STEP_SUMMARY"' in log_step["run"]
    moved = renovate_step("Check that the verify branch did not move")
    assert keys.index(moved["name"]) > at["renovate"] and "steps.base.outputs.verify == 'true'" in moved["if"]
    checkout = steps_[0]
    assert checkout["with"] == {"ref": "${{ github.event.repository.default_branch }}", "persist-credentials": False}
    assert not [s for s in steps_ if "${{" in s.get("run", "")], "run の中で ${{ }} を展開しない（環境変数で渡す）"


def test_3370_7_renovate_exits_non_zero_on_errors_and_keeps_a_json_log():
    """#3370 の受け入れ条件 7: 取得の失敗で中断したらジョブが失敗し（exitCodeForErrors）、記録を JSON で残す。"""
    wf = load_renovate_wf()
    step = renovate_step("renovate")
    assert step["uses"].startswith("renovatebot/github-action@")
    env = step["env"]
    assert env["RENOVATE_EXIT_CODE_FOR_ERRORS"] == "true"
    assert env["RENOVATE_ONBOARDING"] == "false" and env["RENOVATE_REQUIRE_CONFIG"] == "required"
    assert env["RENOVATE_BINARY_SOURCE"] == "install"
    assert env["RENOVATE_LOG_FORMAT"] == "json"
    assert env["RENOVATE_ALLOWED_COMMANDS"] == "${{ steps.base.outputs.allowed_commands }}"
    assert env["RENOVATE_FORCE"] == "${{ steps.base.outputs.force }}"
    assert step["with"]["renovate-version"] == "${{ steps.version.outputs.version }}"
    # Renovate は LOG_* の値を小文字にして読み、コンテナの /tmp はホストの /tmp に重なる
    path = wf["env"]["RENOVATE_LOG_FILE"]
    assert path == path.lower() and path.startswith("/tmp/")
    assert json.loads(wf["env"]["ALLOWED_COMMANDS"]) == ["^python3 scripts/plugin-deps\\.py uv-pin$", "^uv lock --project \\.$"]


@pytest.mark.parametrize("scenario", ["unresolvable", "fetch-failure"])
def test_3370_9_verify_force_only_touches_httpx_on_its_own_branches(scenario):
    """受け入れ条件 9 と 7 の確かめ: 検証の全体の設定は本番の接頭辞・題を使わず、httpx の 2 行の規則だけを持つ。"""
    force = json.loads(load_renovate_wf()["env"][f"VERIFY_FORCE_{scenario.upper().replace('-', '_')}"])
    assert force["branchPrefix"] == "renovate-verify/"
    assert force["dependencyDashboard"] == {"enabled": True, "title": "依存の一覧（検証）", "autoclose": True}
    assert force["includePaths"] == ["plugins/ndf/**"] and force["enabledManagers"] == ["pep621"]
    assert force["lockFileMaintenance"] == {"enabled": False}
    assert force["hostRules"] == [{"matchHost": "renovate-verify.invalid", "abortOnError": True}]
    off, httpx = force["packageRules"]
    assert off == {"matchPackageNames": ["*", "!httpx"], "enabled": False}
    assert httpx["matchPackageNames"] == ["httpx"] and httpx["allowedVersions"] == "==1.0.dev6"
    assert httpx["ignoreUnstable"] is False and httpx["dependencyDashboardApproval"] is False
    assert ("registryUrls" in httpx) is (scenario == "fetch-failure")
    if scenario == "fetch-failure":
        assert httpx["registryUrls"] == ["https://renovate-verify.invalid/simple/"]


def base_step_run(tmp_path: Path, base: str, scenario: str = "", compare: str = "behind"):
    answers = {"git/ref/heads/": GATE_SHA, "compare/": compare}
    env, log = fake_gh_bin(tmp_path, answers)
    p, out = run_wf_step(load_renovate_wf(), renovate_step("base"), tmp_path, {**env, "BASE_BRANCH": base, "SCENARIO": scenario})
    return p, out, gh_calls(log)


def test_3370_7_main_runs_with_the_allowed_commands_without_reading_branches(tmp_path):
    """宛先が main（定時の実行は入力が空）なら検証の設定を作らず、許すコマンドだけを渡す。gh は打たない。"""
    for base in ("", "main"):
        cwd = tmp_path / (base or "empty")
        cwd.mkdir()
        p, out, calls = base_step_run(cwd, base)
        assert p.returncode == 0, p.stderr
        assert out["verify"] == "false" and "force" not in out and "base_patterns" not in out
        assert json.loads(out["allowed_commands"]) == ["^python3 scripts/plugin-deps\\.py uv-pin$", "^uv lock --project \\.$"]
        assert calls == []


@pytest.mark.parametrize(("scenario", "compare"), [("unresolvable", "behind"), ("fetch-failure", "identical")])
def test_3370_9_verify_branch_at_an_ancestor_of_main_runs_the_scenario(tmp_path, scenario, compare):
    """verify/ の先端が main の祖先（compare/main...<先端> が behind か identical）なら、その場面の設定で進む。"""
    p, out, calls = base_step_run(tmp_path, "verify/3370-httpx", scenario, compare)
    assert p.returncode == 0, p.stderr
    assert out["verify"] == "true" and out["branch"] == "verify/3370-httpx" and out["sha"] == GATE_SHA
    assert json.loads(out["allowed_commands"]) == [], "検証の実行では postUpgradeTasks を 1 つも打たない"
    assert json.loads(out["base_patterns"]) == ["verify/3370-httpx"]
    force = json.loads(out["force"])
    assert ("registryUrls" in force["packageRules"][1]) is (scenario == "fetch-failure")
    assert ["api", "repos/o/r/git/ref/heads/verify/3370-httpx", "--jq", ".object.sha"] in calls
    assert ["api", f"repos/o/r/compare/main...{GATE_SHA}", "--jq", ".status"] in calls


@pytest.mark.parametrize("compare", ["ahead", "diverged"])
def test_3370_9_verify_branch_not_under_main_stops(tmp_path, compare):
    """接頭辞だけでは認めない: verify/ の先端に main に無いコミットがあれば token を作る前に止まる。"""
    p, out, _ = base_step_run(tmp_path, "verify/3370-httpx", "unresolvable", compare)
    assert p.returncode != 0 and "verify" not in out


@pytest.mark.parametrize(
    ("base", "scenario"),
    [
        ("renovate/x", "unresolvable"),
        ("feature/verify", "unresolvable"),
        ("verify/a/b", "unresolvable"),
        ("verify/x;id", "unresolvable"),
        ("verify/3370-httpx", ""),
        ("verify/3370-httpx", "other"),
    ],
)
def test_3370_9_other_base_branches_and_missing_scenarios_stop(tmp_path, base, scenario):
    """main と verify/<名前> 以外の宛先、場面の無い verify/ の宛先は、ブランチを読まずに止まる。"""
    p, out, calls = base_step_run(tmp_path, base, scenario)
    assert p.returncode != 0 and "verify" not in out
    assert calls == []


def test_renovate_reads_the_version_through_the_format_check(tmp_path):
    """決定 2: 版の読み取りの手順は deps-weekly.yml の renovate-config と同じ形の検査を持つ。"""
    assert (
        renovate_step("version")["run"]
        == next(s for s in load_deps_weekly()["jobs"]["renovate-config"]["steps"] if s.get("id") == "version")["run"]
    )


def renovate_records(*records: dict, finished: str | None = "done") -> list[dict]:
    """記録の行。Renovate の bunyan の形（name・level・msg と欄）にそろえる。"""
    rows = [{"name": "renovate", "hostname": "h", "pid": 7, "level": 20, "time": "2026-11-23T00:00:00.000Z", "v": 0, **r} for r in records]
    if finished is not None:
        rows.append(
            {"name": "renovate", "level": 30, "repository": "o/r", "msg": "Repository finished", "result": finished, "status": "activated"}
        )
    return rows


def write_log(tmp_path: Path, rows: list[dict], extra: str = "") -> Path:
    path = tmp_path / "renovate.ndjson"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows) + extra, encoding="utf-8")
    return path


def branch_info(*branches: dict) -> dict:
    return {"msg": "branches info extended", "repository": "o/r", "branchesInformation": list(branches)}


def branch_summary(existing: list[str], inactive: list[str]) -> dict:
    return {"msg": "Branch summary", "branches": [{"branchName": b, "sha": GATE_SHA} for b in existing], "inactiveBranches": inactive}


HTTPX_UPGRADE = {"depName": "httpx", "newVersion": "1.0.dev6", "packageFile": "plugins/ndf/pyproject.toml", "updateType": "major"}
ARTIFACT_ERROR = {
    "level": 40,
    "branch": "renovate/plugins-ndf",
    "msg": "artifactErrors",
    "artifactErrors": [
        {
            "lockFile": "plugins/ndf/uv.lock",
            "stderr": "No solution found when resolving dependencies:\n  githubkit 0.17.1 depends on httpx<1.0.0",
        }
    ],
}


def test_3370_7_renovate_log_passes_runs_that_made_their_pull_requests(tmp_path):
    """PR を作った・承認待ち・作るものが無い更新だけの記録は ok。"""
    rows = renovate_records(
        branch_summary(["renovate/a"], ["renovate/b"]),
        branch_info(
            {"branchName": "renovate/a", "prNo": 12, "result": "pr-created", "upgrades": [HTTPX_UPGRADE]},
            {"branchName": "renovate/b", "prNo": None, "result": "needs-approval", "upgrades": []},
        ),
    )
    result = deps.renovate_log(write_log(tmp_path, rows))
    assert result.status == "ok" and result.items == []


def test_3370_7_renovate_log_lists_a_failed_update_without_branch_or_pr(tmp_path):
    """lock の作り直しの失敗でブランチも PR も無い更新を、理由（artifactErrors の stderr）とともに並べて止まる。"""
    rows = renovate_records(
        ARTIFACT_ERROR,
        branch_summary([], ["renovate/plugins-ndf"]),
        branch_info({"branchName": "renovate/plugins-ndf", "prNo": None, "result": "error", "upgrades": [HTTPX_UPGRADE]}),
    )
    with pytest.raises(deps.Stop) as e:
        deps.renovate_log(write_log(tmp_path, rows))
    assert e.value.cause == "check_failed"
    (item,) = e.value.items
    assert item["branch"] == "renovate/plugins-ndf" and item["result"] == "error"
    assert item["upgrades"] == ["httpx 1.0.dev6"]
    assert item["reasons"] == [
        "plugins/ndf/uv.lock: No solution found when resolving dependencies: githubkit 0.17.1 depends on httpx<1.0.0"
    ]


@pytest.mark.parametrize(("existing", "pr"), [(["renovate/plugins-ndf"], None), ([], 15)])
def test_3370_7_renovate_log_skips_errors_that_left_a_branch_or_a_pr(tmp_path, existing, pr):
    """ブランチか PR が残った失敗は PR の側（renovate/artifacts と関門）が知らせるので、ここでは並べない。"""
    rows = renovate_records(
        branch_summary(existing, []),
        branch_info({"branchName": "renovate/plugins-ndf", "prNo": pr, "result": "error", "upgrades": []}),
    )
    assert deps.renovate_log(write_log(tmp_path, rows)).status == "ok"


def test_3370_7_renovate_log_lists_the_branch_of_a_lockfile_error_abort(tmp_path):
    """lock の失敗でリポジトリの処理ごと止まった（結果 lockfile-error）ときは、理由を持つブランチを並べる。"""
    rows = renovate_records(
        ARTIFACT_ERROR, {"level": 30, "branch": "renovate/plugins-ndf", "msg": "Lock file error - aborting"}, finished="lockfile-error"
    )
    with pytest.raises(deps.Stop) as e:
        deps.renovate_log(write_log(tmp_path, rows))
    assert [(i["branch"], i["result"]) for i in e.value.items] == [("renovate/plugins-ndf", "lockfile-error")]


@pytest.mark.parametrize(
    ("rows", "extra"),
    [(renovate_records(finished=None), ""), (renovate_records(), "{not json\n")],
)
def test_renovate_log_stops_on_an_unfinished_or_broken_log(tmp_path, rows, extra):
    """Repository finished の無い記録（途中で止まった）と JSON でない行は invalid で止まる。"""
    with pytest.raises(deps.Stop) as e:
        deps.renovate_log(write_log(tmp_path, rows, extra))
    assert e.value.cause == "invalid"


def test_renovate_log_stops_without_a_log(tmp_path):
    with pytest.raises(deps.Stop) as e:
        deps.renovate_log(tmp_path / "missing.ndjson")
    assert e.value.cause == "invalid"


def test_main_renovate_log_writes_the_summary_and_exits_1(tmp_path, capsys):
    """main は失敗の一覧をジョブの要約へ書き足し、1 行の結果 JSON と終了コード 1 で終わる。"""
    rows = renovate_records(
        branch_info({"branchName": "renovate/plugins-ndf", "prNo": None, "result": "error", "upgrades": [HTTPX_UPGRADE]}), ARTIFACT_ERROR
    )
    summary = tmp_path / "summary.md"
    summary.write_text("前の手順の要約\n", encoding="utf-8")
    code = deps.main(["renovate-log", "--log", str(write_log(tmp_path, rows)), "--summary", str(summary)])
    out = json.loads(capsys.readouterr().out.strip())
    assert code == 1 and out["status"] == "stopped" and out["cause"] == "check_failed"
    text = summary.read_text(encoding="utf-8")
    assert text.startswith("前の手順の要約\n") and "| renovate/plugins-ndf | error | httpx 1.0.dev6 |" in text


# --- 単位 4: 依存の PR の関門（renovate-gate.yml・renovate_gate。#3370 の受け入れ条件 9・不変条件 I4・決定 8） ---


def gate_pr(number: int, sha: str = GATE_SHA, ref: str = "renovate/plugins-ndf", repo: str | None = "o/r") -> dict:
    return {
        "number": number,
        "head": {"sha": sha, "ref": ref, "repo": {"full_name": repo} if repo else None},
        "base": {"repo": {"full_name": "o/r"}},
    }


class StatusGh:
    """`gh api` の差し替え。PR の今の head と、その SHA の commit status の一覧（新しい順）を返し、書き込みを記録する。

    `pulls` は番号 → PR（省けば番号 7 の Renovate の PR が `head` を指す）、`branches` は SHA を先端に持つブランチの名前。"""

    def __init__(
        self,
        head: str,
        statuses: list[dict],
        fail: bool = False,
        pulls: dict[int, dict] | None = None,
        branches: list[str] | None = None,
        post_fail: bool = False,
    ):
        self.head = head
        self.statuses = statuses
        self.fail = fail
        self.post_fail = post_fail
        self.pulls = pulls if pulls is not None else {7: gate_pr(7, head)}
        self.branches = branches or []
        self.calls: list[list[str]] = []
        self.posts: list[dict[str, str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if self.fail:
            raise subprocess.CalledProcessError(1, ["gh", *args], stderr="HTTP 502")
        if args[:3] == ["api", "-X", "GET"] and args[3] == "repos/{owner}/{repo}/pulls":
            branch = next(a for a in args if a.startswith("head=")).split(":", 1)[1]
            return json.dumps([p for p in self.pulls.values() if p["head"]["ref"] == branch])
        if "-X" in args:
            if self.post_fail:
                raise subprocess.CalledProcessError(1, ["gh", *args], stderr="HTTP 502")
            self.posts.append(dict(a.split("=", 1) for a in args if "=" in a and not a.startswith("repos/")) | {"url": args[3]})
            return "{}"
        endpoint = next(a for a in args if a.startswith("repos/"))
        if endpoint.endswith("/branches-where-head"):
            return "\n".join(self.branches) + "\n"
        if endpoint.startswith("repos/{owner}/{repo}/pulls/"):
            return json.dumps(self.pulls[int(endpoint.rsplit("/", 1)[1])])
        if "/statuses" in endpoint:
            assert args[:4] == ["api", "--paginate", "--jq", ".[]"]
            return "".join(json.dumps(s) + "\n" for s in self.statuses)
        raise AssertionError(args)

    def status_reads(self) -> int:
        return sum(1 for c in self.calls if "/statuses?" in c[-1])


def gate_status(state: str, login: str = APP_BOT, id_: int = 1, context: str = "renovate/artifacts") -> dict:
    return {"id": id_, "state": state, "context": context, "creator": {"login": login, "type": "Bot"}, "created_at": "2026-11-23T00:00:00Z"}


def test_i4_gate_passes_the_latest_success_written_by_the_app_bot():
    """I4: 今の head で App の bot が書いた最新の renovate/artifacts の success だけを通す。"""
    gh = StatusGh(
        GATE_SHA,
        [gate_status("success", id_=3), gate_status("failure", id_=2), gate_status("success", context="ndf/other", login="x", id_=4)],
    )
    result = deps.renovate_gate([7], GATE_SHA, APP_BOT, gh)
    assert result.status == "ok" and result.items[0]["state"] == "success"
    assert gh.calls[1][-1] == f"repos/{{owner}}/{{repo}}/commits/{GATE_SHA}/statuses?per_page=100"


@pytest.mark.parametrize(
    ("statuses", "state"),
    [
        ([], "pending"),
        ([gate_status("pending")], "pending"),
        ([gate_status("failure")], "failure"),
        ([gate_status("error")], "failure"),
        ([gate_status("success", login="someone", id_=5), gate_status("failure", id_=4)], "failure"),
        ([gate_status("success", login="someone", id_=5), gate_status("success", id_=4)], "failure"),
        ([gate_status("failure", id_=6), gate_status("success", id_=2)], "failure"),
        ([gate_status("success", context="renovate/stability-days")], "pending"),
    ],
    ids=[
        "unreported",
        "pending",
        "red",
        "error",
        "other-actor-after-red",
        "other-actor-after-success",
        "red-after-success",
        "other-context",
    ],
)
def test_i4_gate_rejects_red_unreported_pending_and_other_actors(statuses, state):
    """I4: 赤・未報告・保留・別の主体が後から書いた成功を拒む（未報告と保留は pending、ほかは failure）。"""
    with pytest.raises(deps.Stop) as e:
        deps.renovate_gate([7], GATE_SHA, APP_BOT, StatusGh(GATE_SHA, statuses))
    assert e.value.cause == "check_failed"
    assert e.value.items[0]["state"] == state


def test_i4_gate_uses_the_status_id_not_the_page_order():
    """I4: ページをまたいで順が崩れても、id の大きい（後に書いた）行を最新とする。"""
    with pytest.raises(deps.Stop) as e:
        deps.renovate_gate([7], GATE_SHA, APP_BOT, StatusGh(GATE_SHA, [gate_status("success", id_=1), gate_status("failure", id_=9)]))
    assert e.value.items[0]["artifacts"] == "failure"


def test_i4_gate_skips_a_success_on_an_old_head():
    """I4: 出来事の head が PR の今の head と違えば（古い head の成功）、状態を読まず付けずに skipped で終える。"""
    gh = StatusGh(GATE_SHA, [gate_status("success")])
    result = deps.renovate_gate([7], OLD_SHA, APP_BOT, gh)
    assert result.status == "skipped" and result.items[0]["current"] == {7: GATE_SHA}
    assert len(gh.calls) == 1


@pytest.mark.parametrize("bot", ["", "[bot]", "Renovate[bot]", "ndf-renovate"])
def test_i4_gate_without_the_app_slug_is_config_broken(bot):
    """RENOVATE_APP_SLUG が無ければ（--bot が "[bot]"）、bot を確かめられないので config_broken で止まる。"""
    with pytest.raises(deps.Stop) as e:
        deps.renovate_gate([7], GATE_SHA, bot, StatusGh(GATE_SHA, [gate_status("success")]))
    assert e.value.cause == "config_broken"


def test_gate_stops_transient_when_gh_fails():
    with pytest.raises(deps.Stop) as e:
        deps.renovate_gate([7], GATE_SHA, APP_BOT, StatusGh(GATE_SHA, [], fail=True))
    assert e.value.cause == "transient"


@pytest.mark.parametrize(
    ("statuses", "head", "posted", "code"),
    [
        ([gate_status("success")], GATE_SHA, "success", 0),
        ([gate_status("pending")], GATE_SHA, "pending", 1),
        ([gate_status("failure")], GATE_SHA, "failure", 1),
        ([gate_status("success")], OLD_SHA, None, 0),
    ],
)
def test_i4_main_renovate_gate_posts_the_status_on_the_head(monkeypatch, capsys, statuses, head, posted, code):
    """I4: --post は判定を PR の head の ndf/renovate-gate に付ける。古い head の出来事では付けない。"""
    gh = StatusGh(GATE_SHA, statuses)
    monkeypatch.setattr(deps, "gh_cli", gh)
    assert deps.main(["renovate-gate", "--pr", "7", "--head", head, "--bot", APP_BOT, "--post"]) == code
    out = json.loads(capsys.readouterr().out.strip())
    if posted is None:
        assert gh.posts == [] and out["status"] == "skipped"
        return
    (post,) = gh.posts
    assert post["url"] == f"repos/{{owner}}/{{repo}}/statuses/{GATE_SHA}"
    assert post["state"] == posted and post["context"] == "ndf/renovate-gate"
    assert 0 < len(post["description"]) <= 140


def test_i4_gate_reads_and_posts_once_per_sha_for_many_pull_requests(monkeypatch, capsys):
    """r3-2: 同じ SHA の Renovate の PR が複数でも、head の確かめは PR ごと、status の一覧の取得と投稿は SHA ごとに 1 回。
    head が進んだ PR は判定に入れず、説明に判定した PR と head を書く（r3-1）。"""
    pulls = {7: gate_pr(7), 12: gate_pr(12, ref="renovate-verify/x"), 9: gate_pr(9, OLD_SHA, "renovate/other")}
    gh = StatusGh(GATE_SHA, [gate_status("success")], pulls=pulls)
    monkeypatch.setattr(deps, "gh_cli", gh)
    argv = ["renovate-gate", "--pr", "7", "--pr", "12", "--pr", "9", "--head", GATE_SHA, "--bot", APP_BOT, "--post"]
    assert deps.main(argv) == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["items"][0]["prs"] == [7, 12]
    assert [c[-1] for c in gh.calls if c[-1].startswith("repos/{owner}/{repo}/pulls/")] == [
        f"repos/{{owner}}/{{repo}}/pulls/{n}" for n in (7, 12, 9)
    ]
    assert gh.status_reads() == 1
    (post,) = gh.posts
    assert post["state"] == "success" and "#7・#12" in post["description"] and GATE_SHA[:7] in post["description"]


def test_i4_main_renovate_gate_posts_failure_when_the_slug_is_missing(monkeypatch, capsys):
    """I4: 判定できない（slug が無い）ときも pending のまま残さず failure を付ける。"""
    gh = StatusGh(GATE_SHA, [gate_status("success")])
    monkeypatch.setattr(deps, "gh_cli", gh)
    assert deps.main(["renovate-gate", "--pr", "7", "--head", GATE_SHA, "--bot", "[bot]", "--post"]) == 1
    assert [p["state"] for p in gh.posts] == ["failure"]
    assert json.loads(capsys.readouterr().out.strip())["cause"] == "config_broken"


def test_3370_9_renovate_reports_artifact_errors_always():
    """#3370 の受け入れ条件 9・I4: 解決できない上げ方を renovate/artifacts の赤として必ず出す（関門が読む状態）。"""
    assert renovate()["statusCheckWhen"]["artifactError"] == "always"


def test_i4_gate_runs_from_the_base_definition_and_checker():
    """I4: 関門は既定ブランチの定義で動く出来事（pull_request_target・status）と merge_group だけで起動し、
    既定ブランチの検証器を取り出す（PR のコードを取り出さない）。merge queue も既定ブランチの検証器で判定し直す（r3-1）。"""
    wf = load_gate_wf()
    on = triggers(wf)
    assert set(on) == {"pull_request_target", "status", "merge_group"}
    assert on["pull_request_target"]["types"] == ["opened", "synchronize", "reopened"]
    jobs = wf["jobs"]
    assert set(jobs) == {"renovate-gate", "renovate-gate-merge-queue"}
    assert " ".join(jobs["renovate-gate"]["if"].split()) == (
        "github.event_name == 'pull_request_target' || (github.event_name == 'status' && github.event.context == 'renovate/artifacts')"
    )
    assert jobs["renovate-gate-merge-queue"]["if"] == "github.event_name == 'merge_group'"
    job = jobs["renovate-gate"]
    assert job["permissions"] == {"contents": "read", "pull-requests": "read", "statuses": "write"}
    checkouts = [s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")]
    assert [c["with"] for c in checkouts] == [{"ref": "${{ github.event.repository.default_branch }}", "persist-credentials": False}]
    gate = next(s for s in job["steps"] if "plugin-deps.py renovate-gate" in s.get("run", ""))
    assert '--head "$SHA" --bot "${RENOVATE_APP_SLUG}[bot]" --post' in gate["run"]
    assert gate["run"].count("plugin-deps.py") == 1, "見つけた PR はまとめて 1 回の起動で渡す（r3-2）"
    assert gate["env"]["GH_REPO"] == "${{ github.repository }}"
    assert gate["env"]["RENOVATE_APP_SLUG"] == "${{ vars.RENOVATE_APP_SLUG }}"
    assert gate["env"]["GH_TOKEN"] == "${{ github.token }}"
    queue = jobs["renovate-gate-merge-queue"]
    assert queue["permissions"] == {"contents": "read", "pull-requests": "read", "statuses": "write"}
    scope, checkout, recheck, fallback = queue["steps"]
    assert scope["id"] == "scope" and "RENOVATE_APP_SLUG" not in scope["env"], "候補の無い PR は slug を読まない（r4-3）"
    assert scope["env"]["QUEUE_REF"] == "${{ github.event.merge_group.head_ref }}"
    assert scope["env"]["SHA"] == "${{ github.event.merge_group.head_sha }}"
    assert str(checkout["uses"]).startswith("actions/checkout@") and checkout["if"] == "steps.scope.outputs.prs != ''"
    assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    assert checkout["with"]["persist-credentials"] is False and checkout["with"]["sparse-checkout"] == "scripts/plugin-deps.py"
    assert recheck["if"] == "steps.scope.outputs.prs != ''", "検証器は候補があるときだけ起動する（r4-4）"
    assert '--head "$HEAD" --queue-sha "$SHA"' in recheck["run"] and '--bot "${RENOVATE_APP_SLUG}[bot]" --post' in recheck["run"]
    assert recheck["env"]["HEAD"] == "${{ steps.scope.outputs.head }}"
    assert recheck["env"]["SHA"] == "${{ github.event.merge_group.head_sha }}"
    assert recheck["env"]["GH_REPO"] == "${{ github.repository }}"
    assert recheck["env"]["RENOVATE_APP_SLUG"] == "${{ vars.RENOVATE_APP_SLUG }}"
    assert fallback["if"] == "failure() && steps.check.outputs.posted != 'true'" and recheck["id"] == "check"
    assert not [s for j in jobs.values() for s in j["steps"] if "${{" in s.get("run", "")], "run の中で ${{ }} を展開しない"


def open_pr(number: int, sha: str, ref: str, repo: str = "o/r") -> dict:
    return {"number": number, "state": "open", "head": {"sha": sha, "ref": ref, "repo": {"full_name": repo}}}


def scope_run(tmp_path: Path, head_repo: str, head_ref: str, pulls: list | None = None, answers: dict | None = None):
    """scope の手順を偽の gh で打つ。SHA を先端に持つブランチは、pulls のうち同じリポジトリで GATE_SHA を指す head から作る。"""
    pulls = pulls or []
    branches = [{"name": p["head"]["ref"]} for p in pulls if p["head"]["repo"]["full_name"] == "o/r" and p["head"]["sha"] == GATE_SHA]
    default = {"branches-where-head": json.dumps(branches), "statuses/": "{}", "/pulls": json.dumps(pulls)}
    env, log = fake_gh_bin(tmp_path, answers or default, jq=True)
    step = next(s for s in load_gate_wf()["jobs"]["renovate-gate"]["steps"] if s.get("id") == "scope")
    p, out = run_wf_step(load_gate_wf(), step, tmp_path, {**env, "HEAD_REPO": head_repo, "HEAD_REF": head_ref, "SHA": GATE_SHA})
    return p, out, gh_calls(log)


BRANCHES_AT = ["api", f"repos/o/r/commits/{GATE_SHA}/branches-where-head", "--jq"]


def test_i4_gate_reads_only_renovate_branches_at_the_sha(tmp_path):
    """r3-2: 候補は SHA を先端に持つブランチのうち Renovate の接頭辞のものだけで引き、開いている PR を全ページ読まない。
    通常の PR の push は、ブランチの 1 回の読み取りと status の 1 回の書き込みだけで終わる。"""
    p, _, calls = scope_run(tmp_path, "o/r", "feature/x", [open_pr(5, GATE_SHA, "feature/x")])
    assert p.returncode == 0, p.stderr
    assert calls[0][:3] == BRANCHES_AT[:3] and len(calls) == 2 and calls[1][:3] == ["api", "-X", "POST"]
    assert not [c for c in calls if "--paginate" in c or any("pulls?" in a for a in c)]


def test_i4_gate_lists_pull_requests_by_the_renovate_branch(tmp_path):
    """r3-2: Renovate のブランチがあれば、そのブランチを head に持つ開いた PR だけを引く（通常のブランチは引かない）。"""
    pulls = [open_pr(7, GATE_SHA, "renovate/plugins-ndf"), open_pr(5, GATE_SHA, "feature/x")]
    p, out, calls = scope_run(tmp_path, "o/r", "feature/x", pulls)
    assert p.returncode == 0, p.stderr
    assert out["prs"] == "7"
    listed = [c for c in calls if "repos/o/r/pulls" in c]
    assert listed == [
        [
            "api",
            "-X",
            "GET",
            "repos/o/r/pulls",
            "-f",
            "state=open",
            "-f",
            "head=o:renovate/plugins-ndf",
            "--jq",
            listed[0][-1],
        ]
    ]


@pytest.mark.parametrize("ref", ["renovate/plugins-ndf", "renovate-verify/plugins-ndf"])
def test_i4_renovate_pull_requests_go_to_the_checker(tmp_path, ref):
    """I4: 同じリポジトリの renovate/ と renovate-verify/ の PR は、何も付けずに検証器へ進む。"""
    p, out, calls = scope_run(tmp_path, "o/r", ref, [open_pr(7, GATE_SHA, ref)])
    assert p.returncode == 0, p.stderr
    assert out["prs"] == "7"
    assert not [c for c in calls if "POST" in c]


def test_i4_renovate_pull_request_whose_head_moved_posts_nothing(tmp_path):
    """Renovate の PR の head が進んだ古い出来事では、検証器も success も打たない（新しい head の出来事が判定する）。"""
    p, out, calls = scope_run(tmp_path, "o/r", "renovate/plugins-ndf", [open_pr(7, OLD_SHA, "renovate/plugins-ndf")])
    assert p.returncode == 0, p.stderr
    assert out["prs"] == ""
    assert not [c for c in calls if "POST" in c]


@pytest.mark.parametrize(("repo", "ref"), [("o/r", "feature/x"), ("fork/r", "renovate/plugins-ndf"), ("o/r", "xrenovate/y")])
def test_i4_other_pull_requests_get_success_without_the_checker(tmp_path, repo, ref):
    """対象の外の PR（fork・renovate/ でない head）は、checkout も Python も打たずに success を付けるだけ。"""
    p, out, calls = scope_run(tmp_path, repo, ref, [open_pr(5, GATE_SHA, ref, repo)])
    assert p.returncode == 0, p.stderr
    assert out["prs"] == ""
    assert calls[1:] == [
        [
            "api",
            "-X",
            "POST",
            f"repos/o/r/statuses/{GATE_SHA}",
            "-f",
            "state=success",
            "-f",
            "context=ndf/renovate-gate",
            "-f",
            "description=Renovate の PR でない",
        ],
    ]


@pytest.mark.parametrize(
    "answers",
    [{"statuses/": "{}"}, {"branches-where-head": json.dumps([{"name": "renovate/plugins-ndf"}]), "statuses/": "{}"}],
    ids=["branches", "pulls"],
)
def test_i4_failed_candidate_listing_posts_no_success(tmp_path, answers):
    """r2-2: 候補のブランチか PR を読めなければ（gh api の失敗）、success を付けずにステップが落ちる。"""
    p, _, calls = scope_run(tmp_path, "o/r", "feature/x", answers=answers)
    assert p.returncode != 0
    assert not [c for c in calls if "POST" in c]


@pytest.mark.parametrize(("repo", "ref"), [("o/r", "feature/x"), ("fork/r", "renovate/plugins-ndf")])
def test_i4_other_pull_requests_at_a_renovate_head_go_to_the_checker(tmp_path, repo, ref):
    """r1-3・r2-1: status はコミットに付くので、同じ SHA を先端に持つ開いた Renovate の PR があれば、通常の PR からは
    無条件の success を付けず、見つけた Renovate の PR の番号（出来事の PR でなく）を検証器へ渡す。"""
    p, out, calls = scope_run(tmp_path, repo, ref, [open_pr(7, GATE_SHA, "renovate/plugins-ndf"), open_pr(5, GATE_SHA, ref, repo)])
    assert p.returncode == 0, p.stderr
    assert out["prs"] == "7"
    assert not [c for c in calls if "POST" in c]


def test_i4_pull_request_and_status_events_share_one_concurrency_group_per_sha():
    """r1-4: 2 つの出来事は 1 つのジョブで、同じ SHA のグループで直列に走り、走っている判定を取り消さない。"""
    job = load_gate_wf()["jobs"]["renovate-gate"]
    assert job["concurrency"] == {
        "group": "renovate-gate-${{ github.event.pull_request.head.sha || github.event.sha }}",
        "cancel-in-progress": False,
    }
    sha = "${{ github.event.pull_request.head.sha || github.event.sha }}"
    assert [s["env"]["SHA"] for s in job["steps"] if "SHA" in (s.get("env") or {})] == [sha, sha]


def test_i4_status_event_rechecks_only_open_renovate_pull_requests_at_the_sha(tmp_path):
    """status の出来事では、その SHA を先端に持つ開いた同じリポジトリの Renovate の PR だけを判定し直し、success を付けない。
    見つけた PR は検証器の 1 回の起動にまとめて渡す（r3-2）。"""
    pulls = [
        open_pr(7, GATE_SHA, "renovate/plugins-ndf"),
        open_pr(9, OLD_SHA, "renovate/other"),
        open_pr(10, GATE_SHA, "feature/x"),
        open_pr(11, GATE_SHA, "renovate/fork", "fork/r"),
        open_pr(12, GATE_SHA, "renovate-verify/x"),
    ]
    p, out, calls = scope_run(tmp_path, "", "", pulls)
    assert p.returncode == 0, p.stderr
    assert out["prs"] == "7 12"
    assert not [c for c in calls if "POST" in c]
    checker = tmp_path / "scripts" / "plugin-deps.py"
    checker.parent.mkdir()
    record = tmp_path / "checker-calls.jsonl"
    checker.write_text(f"import json, sys\nopen({str(record)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n", encoding="utf-8")
    step = load_gate_wf()["jobs"]["renovate-gate"]["steps"][-1]
    p, _ = run_wf_step(load_gate_wf(), step, tmp_path, {"PRS": out["prs"], "SHA": GATE_SHA, "RENOVATE_APP_SLUG": "ndf-renovate"})
    assert p.returncode == 0, p.stderr
    assert gh_calls(record) == [["renovate-gate", "--pr", "7", "--pr", "12", "--head", GATE_SHA, "--bot", APP_BOT, "--post"]]


QUEUE_SHA = "e" * 40
BASE_SHA = "a" * 40


def queue_ref(number) -> str:
    return f"refs/heads/gh-readonly-queue/main/pr-{number}-{BASE_SHA}"


def queue_step(name: str) -> dict:
    steps_ = load_gate_wf()["jobs"]["renovate-gate-merge-queue"]["steps"]
    return next(s for s in steps_ if name in (s.get("id"), s.get("name")))


def queue_scope_run(tmp_path: Path, ref: str, pr: dict | None = None, pulls: list | None = None, parents: list | None = None):
    """merge queue の scope の手順を偽の gh で打つ。キューのコミットの親は既定で [宛先, GATE_SHA]（GATE_SHA がキューに入った版）。"""
    pulls = pulls or []
    parents = [BASE_SHA, GATE_SHA] if parents is None else parents
    branches = [{"name": q["head"]["ref"]} for q in pulls if q["head"]["repo"]["full_name"] == "o/r" and q["head"]["sha"] == GATE_SHA]
    pr = pr or open_pr(5, OLD_SHA, "feature/x")
    queued = {
        "object": {"parents": {"nodes": [{"oid": x} for x in parents]}},
        "pullRequest": {"headRefName": pr["head"]["ref"], "headRepository": {"nameWithOwner": pr["head"]["repo"]["full_name"]}},
    }
    answers = {
        "graphql": json.dumps({"data": {"repository": queued}}),
        "branches-where-head": json.dumps(branches),
        "statuses/": "{}",
        "/pulls": json.dumps(pulls),
    }
    env, log = fake_gh_bin(tmp_path, answers, jq=True)
    p, out = run_wf_step(load_gate_wf(), queue_step("scope"), tmp_path, {**env, "QUEUE_REF": ref, "SHA": QUEUE_SHA})
    return p, out, gh_calls(log)


def queue_post(state: str, description: str) -> list[str]:
    return [
        "api", "-X", "POST", f"repos/o/r/statuses/{QUEUE_SHA}",
        "-f", f"state={state}", "-f", "context=ndf/renovate-gate", "-f", f"description={description}",
    ]  # fmt: skip


def test_i4_merge_queue_passes_a_plain_pull_request_without_the_checker(tmp_path):
    """r4-1・r4-4: 通常の PR は、キューのコミットの第 2 親（キューに入った版）を先端に持つ Renovate の PR が無ければ、
    検証器を起動せずにシェルでキューのコミットへ success を付ける。PR の今の head（OLD_SHA）でなく第 2 親を調べる。"""
    p, out, calls = queue_scope_run(tmp_path, queue_ref(5))
    assert p.returncode == 0, p.stderr
    assert out == {"head": GATE_SHA, "prs": ""}
    assert calls[0][:2] == ["api", "graphql"], "キューのコミットの親と PR の head は 1 回の GraphQL で読む（r5-3）"
    assert f"sha={QUEUE_SHA}" in calls[0] and "number=5" in calls[0]
    assert calls[1][:2] == ["api", f"repos/o/r/commits/{GATE_SHA}/branches-where-head"]
    assert calls[2:] == [queue_post("success", f"PR #5 の head {GATE_SHA[:7]} は Renovate の PR でない")]


def test_i4_merge_queue_checks_the_queued_version_of_a_renovate_pull_request(tmp_path):
    """r4-1: Renovate の PR は、今の head が進んでいても（OLD_SHA）、キューに入った版（第 2 親）を検証器へ渡す。"""
    p, out, calls = queue_scope_run(tmp_path, queue_ref(5), pr=open_pr(5, OLD_SHA, "renovate/plugins-ndf"))
    assert p.returncode == 0, p.stderr
    assert out == {"head": GATE_SHA, "prs": "5"}
    assert len(calls) == 1 and calls[0][:2] == ["api", "graphql"], "Renovate の PR は読み取り 1 回で候補が決まる（r5-3）"


@pytest.mark.parametrize(("ref", "repo"), [("feature/x", "o/r"), ("renovate/plugins-ndf", "fork/r")])
def test_i4_merge_queue_follows_a_renovate_pull_request_at_the_queued_version(tmp_path, ref, repo):
    """r3-1: 通常の PR（fork を含む）でも、キューに入った版を先端に持つ同じリポジトリの Renovate の PR があれば、その判定に従う。"""
    pulls = [open_pr(7, GATE_SHA, "renovate/plugins-ndf")]
    p, out, calls = queue_scope_run(tmp_path, queue_ref(5), pr=open_pr(5, GATE_SHA, ref, repo), pulls=pulls)
    assert p.returncode == 0, p.stderr
    assert out == {"head": GATE_SHA, "prs": "7"}
    assert not [c for c in calls if "POST" in c]


@pytest.mark.parametrize(
    ("ref", "parents", "description"),
    [
        ("refs/heads/main", None, "merge queue の一時ブランチの形でない"),
        (f"refs/heads/gh-readonly-queue/main/pr-x-{BASE_SHA}", None, "merge queue の一時ブランチの形でない"),
        ("", None, "merge queue の一時ブランチの形でない"),
        (queue_ref(5), [BASE_SHA], "PR #5: キューのコミットに PR の head（第 2 親）が無い"),
    ],
    ids=["main", "number", "empty", "one-parent"],
)
def test_i4_merge_queue_without_a_queued_version_posts_failure(tmp_path, ref, parents, description):
    """一時ブランチの形でないか、キューのコミットに PR の head が無ければ、版を決められないので failure を付ける。"""
    p, out, calls = queue_scope_run(tmp_path, ref, parents=parents)
    assert p.returncode == 0, p.stderr
    assert out == {}
    assert calls[-1] == queue_post("failure", description)


def test_i4_merge_queue_checker_runs_with_the_queued_head(tmp_path):
    """r4-1: 検証器にはキューに入った版を --head、キューのコミットを --queue-sha で渡す。"""
    checker = tmp_path / "scripts" / "plugin-deps.py"
    checker.parent.mkdir()
    record = tmp_path / "checker-calls.jsonl"
    checker.write_text(f"import json, sys\nopen({str(record)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n", encoding="utf-8")
    env = {"PRS": "5 7", "HEAD": GATE_SHA, "SHA": QUEUE_SHA, "RENOVATE_APP_SLUG": "ndf-renovate"}
    p, _ = run_wf_step(load_gate_wf(), queue_step("check"), tmp_path, env)
    assert p.returncode == 0, p.stderr
    assert gh_calls(record) == [
        ["renovate-gate", "--pr", "5", "--pr", "7", "--head", GATE_SHA, "--queue-sha", QUEUE_SHA, "--bot", APP_BOT, "--post"]
    ]


def test_i4_merge_queue_without_the_base_checker_posts_failure(tmp_path):
    """r4-2: 既定ブランチに検証器がまだ無ければ（関門の導入前）、Renovate の候補は failure にしてステップを落とす。"""
    env, log = fake_gh_bin(tmp_path, {"statuses/": "{}"})
    p, out = run_wf_step(load_gate_wf(), queue_step("check"), tmp_path, {**env, "PRS": "7", "HEAD": GATE_SHA, "SHA": QUEUE_SHA})
    assert p.returncode == 1 and out == {"posted": "true"}
    assert gh_calls(log) == [queue_post("failure", "既定ブランチに検証器 scripts/plugin-deps.py が無い（関門の導入前）")]


@pytest.mark.parametrize(
    ("posts", "code"),
    [(True, 1), (False, 1), (False, 2), (True, 0)],
    ids=["posted-failure", "post-failed", "crashed", "posted-success"],
)
def test_i4_merge_queue_reports_whether_the_checker_posted(tmp_path, posts, code):
    """r5-2・r6-1: 検証器がステップ出力へ書いた posted をそのまま残し、検証器の終了コードで終える。結果 JSON を解析する CLI は起動しない。"""
    checker = tmp_path / "scripts" / "plugin-deps.py"
    checker.parent.mkdir()
    write = "open(os.environ['GITHUB_OUTPUT'], 'a').write('posted=true\\n')\n" if posts else ""
    checker.write_text(f"import os, sys\n{write}sys.exit({code})\n", encoding="utf-8")
    env = {"PRS": "7", "HEAD": GATE_SHA, "SHA": QUEUE_SHA, "RENOVATE_APP_SLUG": "ndf-renovate"}
    p, out = run_wf_step(load_gate_wf(), queue_step("check"), tmp_path, env)
    assert p.returncode == code and out == ({"posted": "true"} if posts else {})
    assert "jq" not in queue_step("check")["run"]


def test_i4_main_renovate_gate_reports_posted_only_after_the_post(monkeypatch, capsys, tmp_path):
    """r5-2・r6-1: 検証器は投稿を終えたときだけ items[0].posted とステップ出力の posted=true を書き、投稿の API が落ちたら書かない。"""
    argv = ["renovate-gate", "--pr", "7", "--head", GATE_SHA, "--queue-sha", QUEUE_SHA, "--bot", APP_BOT, "--post"]
    output = tmp_path / "github-output"
    output.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(deps, "gh_cli", StatusGh(GATE_SHA, [gate_status("failure")]))
    assert deps.main(argv) == 1
    assert json.loads(capsys.readouterr().out)["items"][0]["posted"] == "failure"
    assert output.read_text(encoding="utf-8") == "posted=true\n"
    output.write_text("", encoding="utf-8")
    monkeypatch.setattr(deps, "gh_cli", StatusGh(GATE_SHA, [gate_status("failure")], post_fail=True))
    assert deps.main(argv) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["cause"] == "transient" and not [i for i in out["items"] if "posted" in i]
    assert output.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    ("statuses", "posted", "code"),
    [
        ([gate_status("success")], "success", 0),
        ([gate_status("failure")], "failure", 1),
        ([gate_status("pending")], "failure", 1),
        ([], "failure", 1),
    ],
    ids=["success", "red", "pending", "unreported"],
)
def test_i4_merge_queue_rechecks_the_queued_version(monkeypatch, capsys, statuses, posted, code):
    """r4-1: 検証器はキューに入った版の renovate/artifacts を、PR の今の head を読まずに判定し、キューのコミットへ付ける。
    success でなければ待たずに failure にする。"""
    gh = StatusGh(GATE_SHA, statuses, pulls={7: gate_pr(7, OLD_SHA)})
    monkeypatch.setattr(deps, "gh_cli", gh)
    argv = ["renovate-gate", "--pr", "7", "--head", GATE_SHA, "--queue-sha", QUEUE_SHA, "--bot", APP_BOT, "--post"]
    assert deps.main(argv) == code
    assert not [c for c in gh.calls if c[-1].startswith("repos/{owner}/{repo}/pulls")]
    assert gh.calls[0][-1] == f"repos/{{owner}}/{{repo}}/commits/{GATE_SHA}/statuses?per_page=100"
    (post,) = gh.posts
    assert post["url"] == f"repos/{{owner}}/{{repo}}/statuses/{QUEUE_SHA}" and post["state"] == posted
    assert "#7" in post["description"]


def test_i4_gate_is_declared_as_a_status_awaiting_the_ruleset():
    """I4: ndf/renovate-gate は renovate-gate.yml が付ける status として宣言し、ruleset への追加は承認ゲート 2 を待つ。
    renovate/artifacts は必須にしない（決定 8）。2 本の新しいワークフローは手元のテストの外に置く。"""
    allow = json.loads(REQUIRED_CHECKS.read_text(encoding="utf-8"))
    statuses = {e["name"]: e["workflow"] for e in allow["statuses"]}
    assert statuses["ndf/renovate-gate"] == ".github/workflows/renovate-gate.yml"
    assert statuses["renovate/artifacts"] == ".github/workflows/renovate.yml"
    assert "ndf/renovate-gate" in {e["name"] for e in allow["awaiting"]}
    assert "renovate/artifacts" not in {e["name"] for e in allow["awaiting"]}
    assert set(load_gate_wf()["jobs"]) <= {e["name"] for e in allow["not_required"]}
    exempt = {e["job"] for e in json.loads(PROJECT_DECL.read_text(encoding="utf-8"))["test"]["ci_exempt"]}
    assert {".github/workflows/renovate.yml", ".github/workflows/renovate-gate.yml"} <= exempt


@pytest.fixture()
def show_repo(tmp_path: Path) -> Path:
    """git_show と show の振る舞いを固定する木。中身のあるファイルと空のファイルを 1 コミットに置く。"""
    git(tmp_path, "init", "-q")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")
    commit_affected(tmp_path, "base")
    return tmp_path


def test_git_show_returns_text_at_rev(show_repo):
    assert deps.git_show(show_repo, "HEAD", "sub/a.txt") == "hello\nworld\n"


def test_git_show_returns_none_for_missing_path_or_rev(show_repo):
    assert deps.git_show(show_repo, "HEAD", "nope.txt") is None
    assert deps.git_show(show_repo, "no-such-rev", "sub/a.txt") is None


def test_git_show_keeps_empty_file_as_empty_string(show_repo):
    assert deps.git_show(show_repo, "HEAD", "empty.txt") == ""


def test_git_show_reads_old_rev_after_change(show_repo):
    (show_repo / "sub" / "a.txt").write_text("changed\n", encoding="utf-8")
    commit_affected(show_repo, "change")
    assert deps.git_show(show_repo, "HEAD~1", "sub/a.txt") == "hello\nworld\n"
    assert deps.git_show(show_repo, "HEAD", "sub/a.txt") == "changed\n"


def test_show_returns_text_or_empty(show_repo):
    assert deps.show(show_repo, "HEAD", "sub/a.txt") == "hello\nworld\n"
    assert deps.show(show_repo, "HEAD", "nope.txt") == ""
    assert deps.show(show_repo, "HEAD", "empty.txt") == ""
    assert deps.show(show_repo, "no-such-rev", "sub/a.txt") == ""


def test_git_out_returns_completed_process_without_raising(show_repo):
    ok = deps.git_out(show_repo, "show", "HEAD:sub/a.txt")
    assert ok.returncode == 0 and ok.stdout == "hello\nworld\n"
    bad = deps.git_out(show_repo, "show", "HEAD:nope.txt")
    assert bad.returncode != 0 and bad.stdout == "" and bad.stderr


def resolve_index(tmp_path: Path, files: list[str]):
    """resolve の検査用の索引。ファイルは作らず、名前だけから dotted の表を作る（読めないファイルは飛ばされる）。"""
    return deps.SourceIndex(tmp_path, files)


def test_resolve_unknown_name_is_empty(tmp_path):
    """resolve: 索引に無い名前は空。"""
    assert resolve_index(tmp_path, ["a/mod.py"]).resolve("nope", "a/x.py") == []


def test_resolve_single_candidate_is_returned_regardless_of_importer(tmp_path):
    """resolve: 候補が 1 つなら、import する側と重ならなくてもそれを返す。"""
    assert resolve_index(tmp_path, ["a/mod.py"]).resolve("mod", "zzz/x.py") == ["a/mod.py"]


def test_resolve_picks_the_longest_common_prefix(tmp_path):
    """resolve: 同じ名前が複数あれば、import する側とパスの頭が最も長く重なるものだけを返す。"""
    index = resolve_index(tmp_path, ["a/mod.py", "b/c/mod.py", "b/d/mod.py"])
    assert index.resolve("mod", "b/c/x.py") == ["b/c/mod.py"]
    assert index.resolve("mod", "a/y.py") == ["a/mod.py"]


def test_resolve_ties_keep_every_best_candidate_in_file_order(tmp_path):
    """resolve: 重なりが同じ長さの候補は全部を、索引に入った順で返す。"""
    index = resolve_index(tmp_path, ["b/mod.py", "a/mod.py", "c/mod.py"])
    assert index.resolve("mod", "x/y.py") == ["b/mod.py", "a/mod.py", "c/mod.py"]
    index = resolve_index(tmp_path, ["p/q/mod.py", "p/r/mod.py", "s/mod.py"])
    assert index.resolve("mod", "p/t.py") == ["p/q/mod.py", "p/r/mod.py"]


def test_resolve_prefix_is_counted_in_characters_not_path_segments(tmp_path):
    """resolve: 重なりはパスの区切りでなく文字で数える（pkgA/ から見て pkgAB/ が pkg/ より長く重なる）。"""
    index = resolve_index(tmp_path, ["pkg/mod.py", "pkgAB/mod.py"])
    assert index.resolve("mod", "pkgA/x.py") == ["pkgAB/mod.py"]


def test_resolve_importer_equal_to_candidate_wins(tmp_path):
    """resolve: import する側と同じパスの候補は最も長く重なるので、それだけを返す。"""
    index = resolve_index(tmp_path, ["a/mod.py", "a/b/mod.py"])
    assert index.resolve("mod", "a/b/mod.py") == ["a/b/mod.py"]
