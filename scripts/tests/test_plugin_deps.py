"""依存の定義と lock の一致の検査（#3370 #3691）。

`.github/workflows/script-structure.yml` の `uv lock --check` の手順を縛る（#3370 の受け入れ条件 10、不変条件 IG5）。
`scope` の手順の run は、一時ディレクトリに作った git の木の上で bash で実際に打ち、出力を読む。
4 つの uv のプロジェクトの 7 日の規則（`exclude-newer`）を縛る（#3370 の受け入れ条件 5、不変条件 I6 の uv の部分）。
Renovate の設定（`.github/renovate.json`）と `scripts/plugin-deps.py` の `targets`・`read_holds`・`uv_pin` を縛る
（#3370 の受け入れ条件 6・8・12、#3691 の受け入れ条件 1・6 の固定の欄、不変条件 I3・I5 の設定の部分・I6・I7・I9）。
"""

from __future__ import annotations

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


def commit_all(path: Path, message: str) -> None:
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
    commit_all(tmp_path, "base")
    git(tmp_path, "tag", "base")
    return tmp_path


def change(root: Path, rel_path: str, text: str) -> None:
    (root / rel_path).write_text(text, encoding="utf-8")
    commit_all(root, "change")


class FakeGh:
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
    result = deps.affected(root, "base", "HEAD", pr=7, head_sha=SHA_A, repo="o/r", gh=gh or FakeGh(), out=out)
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
    gh = FakeGh(comments=[])
    result, data = run_affected(deps_repo, gh)
    assert result.status == "ok" and data is None
    assert gh.writes() == []


def test_affected_without_lock_diff_keeps_data_when_a_comment_exists(deps_repo):
    """affected: lock の差分が無くても目印のコメントがあれば、消すために空のデータを置く。"""
    change(deps_repo, "app/src/pkg/net.py", "import httpx  # 変更\n")
    _, data = run_affected(deps_repo, FakeGh(comments=[11]))
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
    gh = FakeGh()
    assert deps.post_affected(data_file(tmp_path), RUN, gh).status == "ok"
    [call] = gh.writes()
    assert call[:4] == ["api", "-X", "POST", "repos/o/r/issues/7/comments"]
    body = call[-1].removeprefix("body=")
    assert body.splitlines()[0] == deps.AFFECTED_MARKER and SHA_A in body and "python-dotenv" in body


def test_post_affected_rewrites_the_one_comment(tmp_path):
    """post_affected の正常: 目印のコメントがあれば書き直し、2 件目以降は消してちょうど 1 件にする。"""
    gh = FakeGh(comments=[11, 12])
    deps.post_affected(data_file(tmp_path), RUN, gh)
    assert [c[:4] for c in gh.writes()] == [
        ["api", "-X", "DELETE", "repos/o/r/issues/comments/12"],
        ["api", "-X", "PATCH", "repos/o/r/issues/comments/11"],
    ]


def test_post_affected_deletes_the_comment_when_the_lock_diff_is_gone(tmp_path):
    """post_affected の境界: lock の差分が無くなった PR では、目印のコメントを消す（前の一覧を残さない）。"""
    gh = FakeGh(comments=[11])
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
    gh = FakeGh(head=head, comments=[11])
    assert deps.post_affected(data_file(tmp_path), run, gh).status == "skipped"
    assert gh.writes() == []


def test_post_affected_fork_without_pr_number_checks_the_head(tmp_path):
    """post_affected: fork の PR（workflow_run の pull_requests が空）でも、PR の今の head が実行の head と同じときだけ書く。"""
    gh = FakeGh()
    assert deps.post_affected(data_file(tmp_path), deps.WorkflowRun("o/r", None, SHA_A), gh).status == "ok"
    assert len(gh.writes()) == 1


def test_post_affected_writes_the_reason_when_the_list_failed(tmp_path):
    """post_affected（E6）: 一覧を作れなかったデータでは、目印のコメントを理由と head の SHA で書き直す。"""
    gh = FakeGh(comments=[11])
    deps.post_affected(data_file(tmp_path, locks=[], error={"cause": "invalid", "reason": "app/uv.lock: 衝突のマーカーがある"}), RUN, gh)
    body = gh.writes()[0][-1]
    assert "一覧を作れない" in body and SHA_A in body and "衝突のマーカー" in body


@pytest.mark.parametrize("over", [{"head_sha": "x"}, {"pr": "7"}, {"locks": "x"}])
def test_post_affected_rejects_a_malformed_artifact(tmp_path, over):
    """post_affected の異常: 形の違う artifact は読まずに止まり、何も書かない。"""
    gh = FakeGh()
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
