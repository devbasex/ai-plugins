"""git の木から測る設定の項目（テストと素材）と指示書の注記（#3569。旧い
`project_lib/measure_repo.py`・`compose.py`・`measure_materials.py` の移し先）。秘密の表は `secret.py`、結果の形は `items.py` にある。

ファイルは HEAD の木から読み（`git cat-file`）、秘密の名前の表に当たるファイルは開かない。項目の測定は
`{"status": "measured", "value": ...}`・`{"status": "question", "candidates": ..., "evidence": ...}`・
`{"status": "unknown", "reason": ...}` のどれかを返す。git の 1 回の呼び出しは、締め切りまでの残りと 30 秒の小さい方で打ち切る。
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import time
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any, Callable, NamedTuple, Optional

from ndf.adapters import git
from ndf.contracts import facts, limits
from ndf.domains.project import check
from ndf.domains.project.items import MAX_EVIDENCE, measured, obj, question, seq
from ndf.domains.project.secret import is_secret_path, redact

MAX_READ = 512 * 1024
# git・gh の 1 回の上限の方針（contracts/limits/）
CALL_POLICY = "project.measure-call"
RUNNERS = {  # 実行器 → 当てる設定ファイルの名前と依存の名前
    "phpunit": (("phpunit.xml*",), ("phpunit/phpunit",)),
    "pytest": (("pytest.ini", "conftest.py"), ("pytest",)),
    "jest": (("jest.config.*",), ("jest",)),
    "vitest": (("vitest.config.*",), ("vitest",)),
    "go test": ((), ()),
    "rspec": ((".rspec",), ("rspec",)),
}
INSTRUCTION_FILES = facts.ids("instruction-files", measure=True)
OLD_GUIDE_MARK = "NDF_PLUGIN_GUIDE_START"
TEST_WORDS = re.compile(r"phpunit|pytest|jest|vitest|rspec|go test|npm (?:run )?test|docker compose exec|run-[\w-]*tests?")
IMPORT_TOKEN = re.compile(r"(?:^|\s)@([\w./-]+\.md)\b")
DEP_NAME = re.compile(r"[\s<>=!~\[;]")
REQ_NAME = re.compile(r"[\s<>=!~\[;#]")

# 素材の設定の案（#2315）
MAX_LIBRARIES = 15
ANALYTICS = ("analytics", "gtag", "sentry", "datadog", "mixpanel", "amplitude", "posthog", "segment", "newrelic", "bugsnag", "rollbar")
# 依存の表へ処理系のバージョンを書く行の名前（依存ではない）
PHP_ROW, NODE_ROW, PYTHON_ROW = "php", "node", "python"
RUNTIME_ROWS = (PHP_ROW, PYTHON_ROW, NODE_ROW)


def call_limit() -> float:
    """git・gh の 1 回の上限の秒。"""
    return limits.seconds(CALL_POLICY)


class TimeUp(Exception):
    """測定の締め切りを越えた。読めなかったことを「ファイルが無い」と区別するため、`None` を返さずに上げる。"""


class Tree:
    """HEAD の木と、秘密の名前の表を当てた読み取り。"""

    def __init__(self, root: Path, deadline: float) -> None:
        self.root = root
        self.deadline = deadline
        self.remote = git.Git(root).remote()
        self.files = check.tree(git.Git(root))
        self.skipped: list[str] = sorted(p for p in self.files if is_secret_path(p))

    def left(self) -> float:
        return self.deadline - time.monotonic()

    def git(self, *args: str) -> Optional[str]:
        """git の標準出力。失敗・時間切れ（締め切りの前）なら `None`。締め切りを越えたら `TimeUp`。"""
        left = self.left()
        if left <= 0:
            raise TimeUp
        try:
            done = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, timeout=min(call_limit(), left))
        except subprocess.TimeoutExpired:
            if self.left() <= 0:
                raise TimeUp from None
            return None
        except OSError:  # noqa: NDF401 外部コマンド: git を起動できなければ読めなかったものとして扱う
            return None
        return done.stdout if done.returncode == 0 else None

    def words(self, *args: str) -> list[str]:
        """git の標準出力を空白で区切った並び。失敗なら空。"""
        out = self.git(*args)
        return out.split() if out is not None else []

    def refs(self, prefix: str) -> list[str]:
        """`prefix`（`refs/heads` など）の下の ref の短い名前の並び。失敗なら空。"""
        return self.words("for-each-ref", "--format=%(refname:short)", prefix)

    def read(self, path: str) -> Optional[str]:
        """追跡ファイルの HEAD の中身。秘密の名前・大きすぎる・無いファイルは `None`。"""
        if path not in self.files or is_secret_path(path):
            return None
        out = self.git("cat-file", "-p", f"HEAD:{path}")
        return out if out is not None and len(out) <= MAX_READ else None

    def text(self, path: str) -> str:
        got = self.read(path)
        return got if got is not None else ""

    def shallow(self, *patterns: str) -> list[str]:
        """根と 1 段下で名前が当たる追跡ファイル。"""
        return sorted(
            p for p in self.files if p.count("/") <= 1 and any(fnmatch.fnmatchcase(p.rsplit("/", 1)[-1], pat) for pat in patterns)
        )

    def json(self, path: str) -> dict[str, Any]:
        try:
            value = json.loads(self.text(path) if path in self.files else "{}")
        except ValueError:
            return {}
        return obj(value)

    def toml(self, path: str) -> dict[str, Any]:
        try:
            return tomllib.loads(self.text(path))
        except tomllib.TOMLDecodeError:
            return {}

    def grep(self, paths: list[str], rx: re.Pattern[str]) -> list[dict[str, Any]]:
        """`paths` の行のうち `rx` に当たるもの（秘密の形は伏せる。最大 `MAX_EVIDENCE` 件）。"""
        hits: list[dict[str, Any]] = []
        for path in paths:
            hits += [
                {"path": path, "line": n, "text": redact(line.strip())[:200]}
                for n, line in enumerate(self.text(path).splitlines(), 1)
                if rx.search(line)
            ]
            if len(hits) >= MAX_EVIDENCE:
                return hits[:MAX_EVIDENCE]
        return hits


def _sections(c: dict[str, Any], names: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for sec in names:
        out.update({k: str(v) for k, v in obj(c.get(sec)).items()})
    return out


def _php_dependencies(tree: Tree) -> dict[str, str]:
    """composer.json の require / require-dev。"""
    out: dict[str, str] = {}
    for f in tree.shallow("composer.json"):
        out.update(_sections(tree.json(f), ("require", "require-dev")))
    return out


def _js_dependencies(tree: Tree) -> dict[str, str]:
    """package.json の dependencies / devDependencies と engines.node。"""
    out: dict[str, str] = {}
    for f in tree.shallow("package.json"):
        c = tree.json(f)
        out.update(_sections(c, ("dependencies", "devDependencies")))
        node = obj(c.get("engines")).get("node")
        if node:
            out[NODE_ROW] = str(node)
    return out


def _dep_name(spec: Any) -> str:
    return DEP_NAME.split(str(spec), maxsplit=1)[0].lower()


def _pyproject_project_deps(c: dict[str, Any]) -> dict[str, str]:
    """pyproject.toml の [project] の dependencies / optional-dependencies。"""
    proj = obj(c.get("project"))
    specs = [*seq(proj.get("dependencies")), *(x for v in obj(proj.get("optional-dependencies")).values() for x in seq(v))]
    return {_dep_name(d): str(d) for d in specs}


def _pyproject_tool_deps(c: dict[str, Any]) -> dict[str, str]:
    """pyproject.toml の dependency-groups と Poetry の依存。"""
    out = {_dep_name(d): str(d) for group in obj(c.get("dependency-groups")).values() for d in seq(group)}
    poetry = obj(obj(c.get("tool")).get("poetry"))
    out.update({k.lower(): v for k, v in _sections(poetry, ("dependencies", "dev-dependencies")).items()})
    for g in obj(poetry.get("group")).values():
        out.update({k.lower(): v for k, v in _sections(obj(g), ("dependencies",)).items()})
    return out


def _requirements_deps(tree: Tree) -> dict[str, str]:
    """requirements*.txt の依存。同じ名前は先に出た行を採る。オプションの行と URL の行は読まない。"""
    out: dict[str, str] = {}
    for f in tree.shallow("requirements*.txt"):
        for line in tree.text(f).splitlines():
            name = REQ_NAME.split(line.strip(), maxsplit=1)[0].lower()
            if not line.lstrip().startswith("-") and name and "://" not in name:
                out.setdefault(name, line.strip())
    return out


def _py_dependencies(tree: Tree) -> dict[str, str]:
    """pyproject.toml（project・dependency-groups・poetry）と requirements*.txt。"""
    out: dict[str, str] = {}
    for f in tree.shallow("pyproject.toml"):
        c = tree.toml(f)
        out.update(_pyproject_project_deps(c))
        out.update(_pyproject_tool_deps(c))
        python = obj(c.get("project")).get("requires-python")
        if python:
            out[PYTHON_ROW] = str(python)
    for name, line in _requirements_deps(tree).items():
        out.setdefault(name, line)
    return out


class DepSource(NamedTuple):
    """依存を集める言語 1 つ。言語・その依存の保存先・依存の定義を読む関数。"""

    language: str
    ecosystem: str
    read: Callable[[Tree], dict[str, str]]


# 依存を集める言語の唯一の定義（並びは `dependencies` の返す言語の順）
DEP_SOURCES = (
    DepSource("php", "packagist", _php_dependencies),
    DepSource("javascript", "npm", _js_dependencies),
    DepSource("python", "pypi", _py_dependencies),
)
# 依存の言語 → その依存の保存先
ECOSYSTEM = {s.language: s.ecosystem for s in DEP_SOURCES}
# 依存を集める言語（`dependencies` の返す言語の組と、測れなかったときの既定）
DEP_LANGUAGES = tuple(s.language for s in DEP_SOURCES)


def dependencies(tree: Tree) -> dict[str, dict[str, str]]:
    """言語 → {依存の名前: バージョンの指定}。根と 1 段下の依存の定義から集める。"""
    return {s.language: s.read(tree) for s in DEP_SOURCES}


def compose_services(text: str) -> list[str]:
    """compose のファイルの `services:` の直下の名前（YAML を読まずに字下げで拾う）。空行と行頭がコメントの行は飛ばす。"""
    names: list[str] = []
    inside = False
    indent: Optional[int] = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lead = len(line) - len(line.lstrip())
        if lead == 0:
            inside = line.rstrip() == "services:"
        elif inside:
            indent = lead if indent is None else indent
            if lead == indent and stripped.endswith(":"):
                names.append(stripped[:-1].strip("'\""))
    return names


def service_names(tree: Tree) -> list[str]:
    """根と 1 段下の compose のファイルのサービス名（重複なく整列）。テストの質問の候補にする。"""
    files = tree.shallow("compose*.y*ml", "docker-compose*.y*ml")
    return sorted({name for f in files for name in compose_services(tree.text(f))})


def _test_scripts(tree: Tree, filename: str) -> list[str]:
    """`filename`（package.json / composer.json）の scripts のうち、名前に test を含むもの。"""
    cmds: list[str] = []
    for f in tree.shallow(filename):
        cmds += [f"{f}: {k} = {v}" for k, v in obj(tree.json(f).get("scripts")).items() if "test" in k]
    return cmds


def _test_commands(tree: Tree) -> list[str]:
    cmds = _test_scripts(tree, "package.json") + _test_scripts(tree, "composer.json")
    for f in tree.shallow("Makefile"):
        cmds += [f"{f}: {m.group(0).strip()}" for m in re.finditer(r"^test[\w-]*:.*$", tree.text(f), re.M)]
    cmds += [p for p in tree.files if p.count("/") <= 1 and "test" in p.rsplit("/", 1)[-1] and p.endswith(".sh")]
    return cmds[:MAX_EVIDENCE]


def _runner_configs(tree: Tree, runner: str, configs: tuple[str, ...]) -> list[str]:
    if runner == "go test":
        return [p for p in tree.files if p.endswith("_test.go")][:1]
    return [p for p in tree.files if p.count("/") <= 2 and any(fnmatch.fnmatchcase(p.rsplit("/", 1)[-1], c) for c in configs)]


def instruction_files(tree: Tree) -> list[str]:
    """木にある指示書（`INSTRUCTION_FILES` の順）。"""
    return [p for p in INSTRUCTION_FILES if p in tree.files]


def docs_of(tree: Tree) -> list[str]:
    """指示書と根・1 段下の README。"""
    return instruction_files(tree) + tree.shallow("README*.md")


def measure_test(tree: Tree, deps: dict[str, dict[str, str]], services: list[str]) -> dict[str, Any]:
    all_deps = {k for d in deps.values() for k in d}
    runners = []
    for runner, (configs, dep_names) in RUNNERS.items():
        hit_cfg = _runner_configs(tree, runner, configs)
        if hit_cfg or any(d in all_deps for d in dep_names):
            runners.append({"runner": runner, "config": hit_cfg[:5], "dependency": [d for d in dep_names if d in all_deps]})
    if not runners and any(re.search(r"(^|/)test_[^/]*\.py$", p) for p in tree.files):
        runners.append({"runner": "pytest", "config": [], "dependency": []})
    commands = _test_commands(tree)
    evidence = tree.grep(docs_of(tree), TEST_WORDS)
    if not runners and not commands and not evidence:
        return measured({"suites": []})
    return question({"runners": runners, "commands": commands, "compose_services": services}, evidence)


def _normalize(path: str) -> str:
    parts: list[str] = []
    for p in path.split("/"):
        if p == ".." and parts:
            parts.pop()
        elif p not in ("", ".", ".."):
            parts.append(p)
    return "/".join(parts)


def _old_guide_notes(tree: Tree, files: list[str], imports: list[dict[str, str]]) -> list[str]:
    notes = []
    for f in files + [i["to"] for i in imports]:
        if OLD_GUIDE_MARK not in "\n".join(tree.text(f).splitlines()[:5]):
            continue
        by = [i["from"] for i in imports if i["to"] == f]
        where = f"{f} を {'・'.join(by)} が読み込んでいる" if by else f"{f} が指示書にある"
        notes.append(f"古い NDF の案内 {where}（{OLD_GUIDE_MARK}）。今の NDF を指さないので、読み込みを外すか消す")
    return notes


def instruction_notes(tree: Tree) -> list[str]:
    """指示書の注意書き（指示書かその読み込むファイルに古い NDF の案内がある・指示書が無い）。"""
    files = instruction_files(tree)
    if not files:
        return ["指示書（AGENTS.md・CLAUDE.md など）が無い。NDF と各ランタイムが読む指示書を置くとよい"]
    imports = [
        {"from": f, "to": _normalize(str(PurePosixPath(f).parent / m.group(1)))} for f in files for m in IMPORT_TOKEN.finditer(tree.text(f))
    ]
    return _old_guide_notes(tree, files, imports)


def _library_seeds(deps: dict[str, dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(素材の候補の依存（上限 `MAX_LIBRARIES`）, 解析の SDK の根拠)。処理系のバージョンの行は依存でないので外す。"""
    libraries: list[dict[str, Any]] = []
    analytics: list[dict[str, Any]] = []
    for lang, names in deps.items():
        eco = ECOSYSTEM[lang] if lang in ECOSYSTEM else lang
        for name in sorted(n for n in names if n not in RUNTIME_ROWS):
            if any(w in name.lower() for w in ANALYTICS):
                analytics.append({"text": f"{eco}: {name}"})
            if len(libraries) < MAX_LIBRARIES:
                libraries.append({"name": name, "ecosystem": eco, "alternatives": []})
    return libraries, analytics


def measure_materials(tree: Tree, deps: dict[str, dict[str, str]]) -> dict[str, Any]:
    """素材の設定の候補。README の冒頭・直接の依存・解析の SDK を根拠にする。決めるのは人である。"""
    libraries, analytics = _library_seeds(deps)
    evidence = [
        {"file": f, "text": ln[:200]}
        for f in tree.shallow("README*.md")[:1]
        for ln in [x for x in tree.text(f).splitlines() if x.strip()][:8]
    ]
    candidates = {
        "product": None,
        "needs": {"usage": [], "market": []},
        "seeds": {"libraries": libraries, "environment": []},
        "deferred_milestones": [],
    }
    return question(candidates, evidence + analytics)


Step = Callable[[str, Callable[[], Any]], Any]
# 設定の項目でない処理の名前。`step` が失敗の記録をこの名前で `items` へ入れても、結果に出さない
AUX_STEPS = ("dependencies", "services", "instructions")


def measure(tree: Tree, step: Step, items: dict[str, Any]) -> list[str]:
    """git の木から測る項目（`test`・`materials`）を `items` へ入れ、指示書の注記を返す。"""
    dep = step("dependencies", lambda: dependencies(tree))
    if dep is None:
        dep = {lang: {} for lang in DEP_LANGUAGES}
    services = step("services", lambda: service_names(tree))
    if services is None:
        services = []
    test_result = step("test", lambda: measure_test(tree, dep, services))
    if test_result is not None:
        items["test"] = test_result
    notes = step("instructions", lambda: instruction_notes(tree))  # 素材の README の読み取りが時間を使い切る前に測る
    materials_result = step("materials", lambda: measure_materials(tree, dep))
    if materials_result is not None:
        items["materials"] = materials_result
    for key in AUX_STEPS:
        items.pop(key, None)
    return notes if notes is not None else []  # None は締め切りか失敗。注記を出さない
