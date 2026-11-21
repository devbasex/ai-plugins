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


# --- 公開から 7 日の検査（単位 3。Task 7・#3370 の受け入れ条件 6・不変条件 I1・I6・決定 2・6・9） ---

DEPS_WEEKLY = REPO / ".github" / "workflows" / "deps-weekly.yml"
REQUIRED_CHECKS = REPO / "scripts" / "required-checks-allow.json"
PROJECT_DECL = REPO / ".ndf" / "project.json"
AGE_NOW = deps.datetime(2026, 11, 21, 3, 0, tzinfo=deps.timezone.utc)


def ago(**delta) -> str:
    """AGE_NOW から delta だけ前の時刻（PyPI と npm の ISO の形）。"""
    return (AGE_NOW - deps.timedelta(**delta)).isoformat().replace("+00:00", "Z")


def uv_lock(*packages: tuple[str, str], extra: str = "") -> str:
    blocks = ['version = 1\nrevision = 3\nrequires-python = ">=3.11"\n']
    blocks += [f'[[package]]\nname = "{n}"\nversion = "{v}"\nsource = {{ registry = "https://pypi.org/simple" }}\n' for n, v in packages]
    blocks.append('[[package]]\nname = "proj"\nversion = "0.0.0"\nsource = { virtual = "." }\n' + extra)
    return "\n".join(blocks)


def npm_lock(*packages: tuple[str, str]) -> str:
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


def run_age(root: Path, fetch, renovate_version: str | None = None, base: str | None = "HEAD^1", head: str | None = "HEAD"):
    return deps.age(root, base, head, fetch, AGE_NOW, renovate_version)


def test_i6_age_waits_the_same_days_as_uv():
    """I6: age の待つ日数は plugins/ndf/pyproject.toml の exclude-newer と同じ（PR の木からは読まない定数）。"""
    assert deps.AGE_DAYS == seven_days()


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
    root = age_tree({"uv.lock": uv_lock(("anyio", "4.15.0"))}, {"uv.lock": uv_lock(("anyio", "4.16.0"))})
    fetch = Registry(pypi={("anyio", "4.16.0"): [ago(**delta), ago(**delta, seconds=-5)]})
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
    root = age_tree({"uv.lock": uv_lock()}, {"uv.lock": uv_lock(("anyio", "4.16.0"))})
    result = run_age(root, Registry(pypi={("anyio", "4.16.0"): [ago(days=1), ago(days=8)]}))
    assert result.status == "ok" and result.items[0]["published"] == ago(days=8)


@pytest.mark.parametrize(
    "registry",
    [
        Registry(),  # PyPI が 404
        Registry(pypi={("anyio", "4.16.0"): []}),  # 版はあるがファイルが無い
    ],
)
def test_i1_age_stops_a_version_without_a_published_date(age_tree, registry):
    """I1 の異常: 公開日の読めない版（取得の失敗・ファイルの無い版）は check_failed で止まる。"""
    root = age_tree({"uv.lock": uv_lock()}, {"uv.lock": uv_lock(("anyio", "4.16.0"))})
    with pytest.raises(deps.Stop) as e:
        run_age(root, registry)
    assert e.value.cause == "check_failed"
    assert [(i["name"], i["verdict"], i["published"]) for i in e.value.items] == [("anyio", "no_date", None)]
    assert e.value.items[0]["reason"]


def test_i1_age_stops_a_source_without_a_published_date(age_tree):
    """I1 の異常: git の取得元のように公開日を持たない依存は取得せずに止まり、手元のコード（virtual・editable）は見ない。"""
    extra = '\n[[package]]\nname = "tool"\nversion = "1.0"\nsource = { git = "https://example.invalid/tool?rev=x#x" }\n'
    extra += '\n[[package]]\nname = "kit"\nversion = "0.1"\nsource = { editable = "kit" }\n'
    root = age_tree({"uv.lock": uv_lock()}, {"uv.lock": uv_lock(extra=extra)})
    fetch = Registry()
    with pytest.raises(deps.Stop) as e:
        run_age(root, fetch)
    assert [(i["name"], i["verdict"]) for i in e.value.items] == [("tool", "no_date")]
    assert fetch.urls == []


def test_i1_age_checks_only_versions_new_in_the_diff(age_tree):
    """I1: 前から lock にあった版は取得せず、差分で入った版だけを確かめる（消えた版も見ない）。"""
    base = uv_lock(("anyio", "4.15.0"), ("idna", "3.10"), ("gone", "1.0"))
    head = uv_lock(("anyio", "4.16.0"), ("idna", "3.10"), ("fresh", "2.0"))
    root = age_tree({"plugins/ndf/uv.lock": base}, {"plugins/ndf/uv.lock": head})
    fetch = Registry(pypi={("anyio", "4.16.0"): [ago(days=9)], ("fresh", "2.0"): [ago(days=9)]})
    result = run_age(root, fetch)
    assert result.status == "ok"
    assert sorted(fetch.urls) == ["https://pypi.org/pypi/anyio/4.16.0/json", "https://pypi.org/pypi/fresh/2.0/json"]
    assert {i["where"] for i in result.items} == {"plugins/ndf/uv.lock"}


def test_i1_age_checks_npm_lock_versions(age_tree):
    """I1: npm の lock に入った版（スコープつきを含む）は npm のレジストリの time で確かめ、link の項目は見ない。"""
    lock = f"{SLIDES}/package-lock.json"
    root = age_tree({lock: npm_lock(("pptxgenjs", "4.0.0"))}, {lock: npm_lock(("pptxgenjs", "4.0.1"), ("@types/node", "22.20.6"))})
    fetch = Registry(npm={"pptxgenjs": {"4.0.1": ago(days=10)}, "@types/node": {"22.20.6": ago(days=2)}})
    with pytest.raises(deps.Stop) as e:
        run_age(root, fetch)
    verdicts = {i["name"]: i["verdict"] for i in e.value.items}
    assert verdicts == {"pptxgenjs": "ok", "@types/node": "too_new"}
    assert "https://registry.npmjs.org/@types%2Fnode" in fetch.urls


def test_i9_age_skips_locks_outside_the_renovate_targets(age_tree):
    """I9: ignorePaths の lock（docs/presentations/）は Renovate の対象の外で、age も見ない。"""
    lock = "docs/presentations/x/slides/package-lock.json"
    root = age_tree({lock: npm_lock()}, {lock: npm_lock(("pptxgenjs", "9.9.9"))})
    fetch = Registry()
    assert run_age(root, fetch).status == "skipped"
    assert fetch.urls == []


def test_i1_age_checks_a_new_uv_version_once(age_tree):
    """I1: UV_VERSION の 2 か所に入った版は、PyPI の uv の同じ版の公開日で 1 回だけ確かめる（決定 12）。"""
    base = {p: (REPO / p).read_text(encoding="utf-8") for p in deps.UV_PIN_FILES}
    head = {p: deps.UV_VERSION_RE.sub(lambda m: m.group(1) + "0.13.8" + m.group(3), t) for p, t in base.items()}
    root = age_tree(base, head)
    fetch = Registry(pypi={("uv", "0.13.8"): [ago(days=3)]})
    with pytest.raises(deps.Stop) as e:
        run_age(root, fetch)
    assert [(i["name"], i["version"], i["verdict"]) for i in e.value.items] == [("uv", "0.13.8", "too_new")]
    assert fetch.urls == ["https://pypi.org/pypi/uv/0.13.8/json"]


def test_i1_age_checks_a_new_renovate_version_in_the_diff(age_tree):
    """I1・決定 2: .github/renovate-version に入った版は npm の renovate の公開日で確かめる。"""
    path = ".github/renovate-version"
    root = age_tree({path: "44.239.1\n"}, {path: "44.240.0\n"})
    fetch = Registry(npm={"renovate": {"44.239.1": ago(days=40), "44.240.0": ago(days=1)}})
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
    root = age_tree({"uv.lock": uv_lock()}, {"uv.lock": uv_lock(("anyio", "4.16.0"))}, rules=[exception_rule(constraint)])
    fetch = Registry(pypi={("anyio", "4.16.0"): [ago(days=1)]})
    if passes:
        result = run_age(root, fetch)
        assert result.status == "ok" and result.items[0]["verdict"] == "exception"
    else:
        with pytest.raises(deps.Stop) as e:
            run_age(root, fetch)
        assert e.value.cause == "check_failed" and e.value.items[0]["verdict"] == "too_new"


def test_age_skips_without_fetching_when_no_version_changed(age_tree):
    """差分に lock・UV_VERSION・Renovate の版が無ければ、取得せずに skipped で終わる（すべての PR で打ってよい）。"""
    root = age_tree({"uv.lock": uv_lock(("anyio", "4.15.0"))}, {"README.md": "x\n"})
    fetch = Registry()
    result = run_age(root, fetch)
    assert result.status == "skipped"
    assert fetch.urls == []


def test_age_stops_on_an_unreadable_lock(age_tree):
    """読めない lock（衝突のマーカー）は invalid で止まる。"""
    root = age_tree({"uv.lock": uv_lock()}, {"uv.lock": "<<<<<<< HEAD\n" + uv_lock()})
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
    fetch = Registry(npm={"renovate": {"44.239.1": ago(days=days_old)}})
    monkeypatch.setattr(deps, "fetch_url", fetch)
    assert deps.main(["age", "--root", str(root), "--renovate-version", "44.239.1"]) == code
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == status
    assert fetch.urls == ["https://registry.npmjs.org/renovate"]


# 公開から 7 日の検査のワークフローと宣言


def test_i1_script_structure_runs_age_on_every_event():
    """I1: script-structure-check は HEAD^1 と HEAD の間で age を打ち、lock を読むため 3.11 以上の Python を使う。"""
    found = [s for s in steps(load_workflow()) if "plugin-deps.py age" in s.get("run", "")]
    assert len(found) == 1
    run = found[0]["run"]
    assert "--base HEAD^1 --head HEAD" in run
    m = re.search(r"--python (3\.\d+)", run)
    assert m and int(m.group(1).split(".")[1]) >= 11


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
    validate_at = next(i for i, r in enumerate(runs) if "renovate-config-validator" in r)
    assert age_at < validate_at
    assert "--strict --no-global .github/renovate.json" in runs[validate_at]
    assert not [r for r in runs if "${{" in r], "run の中で ${{ }} を展開しない（環境変数で渡す）"


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
