"""git の木から測る宣言の項目（P1・P2・P5・P6 の git の分・P7・P9・P10・素材）（#3569。旧い
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
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, Optional

from ndf.adapters import git
from ndf.contracts import facts
from ndf.domains.project import check
from ndf.domains.project.items import MAX_EVIDENCE, measured, obj, question, seq, unknown
from ndf.domains.project.secret import is_secret_path, redact

MAX_READ = 512 * 1024
CALL_LIMIT = 30.0
LANG_EXT = {ext: row["id"] for row in facts.rows("languages", code=True) for ext in row["extensions"]}
# 依存の名前とフレームワークの組（版は依存の定義の主の数字）
FRAMEWORKS = (
    ("laravel/framework", "laravel"),
    ("symfony/framework-bundle", "symfony"),
    ("next", "next"),
    ("react", "react"),
    ("vue", "vue"),
    ("nuxt", "nuxt"),
    ("express", "express"),
    ("@nestjs/core", "nestjs"),
    ("hono", "hono"),
    ("svelte", "svelte"),
    ("django", "django"),
    ("flask", "flask"),
    ("fastapi", "fastapi"),
)
RUNNERS = {  # 実行器 → 当てる設定ファイルの名前と依存の名前
    "phpunit": (("phpunit.xml*",), ("phpunit/phpunit",)),
    "pytest": (("pytest.ini", "conftest.py"), ("pytest",)),
    "jest": (("jest.config.*",), ("jest",)),
    "vitest": (("vitest.config.*",), ("vitest",)),
    "go test": ((), ()),
    "rspec": ((".rspec",), ("rspec",)),
}
DB_WORDS = ("mysql", "postgres", "mariadb", "redis", "mongo")
DEPLOY_FILES = (
    "amplify.yml",
    "samconfig.toml",
    "buildspec.yml",
    "appspec.yml",
    "serverless.yml",
    "vercel.json",
    "netlify.toml",
    "fly.toml",
    "Procfile",
    "app.yaml",
)
CHECK_TOOLS = (  # 名前 → 設定ファイルの名前
    ("pint", ("pint.json",)),
    ("phpstan", ("phpstan.neon*",)),
    ("eslint", (".eslintrc*", "eslint.config.*")),
    ("prettier", (".prettierrc*", "prettier.config.*")),
    ("biome", ("biome.json*",)),
    ("ruff", ("ruff.toml", ".ruff.toml")),
    ("mypy", ("mypy.ini",)),
    ("rubocop", (".rubocop.yml",)),
    ("golangci-lint", (".golangci.y*ml",)),
    ("markdownlint", (".markdownlint*",)),
    ("pre-commit", (".pre-commit-config.yaml",)),
)
PYPROJECT_TOOLS = ("ruff", "black", "mypy", "isort")
INSTRUCTION_FILES = facts.ids("instruction-files", measure=True)
OLD_GUIDE_MARK = "NDF_PLUGIN_GUIDE_START"
TEST_WORDS = re.compile(r"phpunit|pytest|jest|vitest|rspec|go test|npm (?:run )?test|docker compose exec|run-[\w-]*tests?")
POLICY_WORDS = re.compile(r"doc-lint|文体|文言テスト|markdown-writing|document-style|書き方")
DEPLOY_WORDS = re.compile(r"deploy|release|publish|デプロイ|リリース|本番|amplify|sam |(?<![a-z])(?:stg|prd)(?![a-z])", re.I)
IMPORT_TOKEN = re.compile(r"(?:^|\s)@([\w./-]+\.md)\b")
DEP_NAME = re.compile(r"[\s<>=!~\[;]")
REQ_NAME = re.compile(r"[\s<>=!~\[;#]")
# 本番チャネルの候補（origin の HEAD が無いときに既定ブランチとして当てる名前）
DEFAULT_BRANCH_CANDIDATES = check.KNOWN_BRANCHES[:2]

# 素材の宣言の案（#2315）
MAX_LIBRARIES = 15
ANALYTICS = ("analytics", "gtag", "sentry", "datadog", "mixpanel", "amplitude", "posthog", "segment", "newrelic", "bugsnag", "rollbar")
# 依存の言語と、その依存の置き場の組
ECOSYSTEM = (("php", "packagist"), ("javascript", "npm"), ("python", "pypi"))
# 依存の表へ処理系の版を書く行の名前（依存ではない）
PHP_ROW, NODE_ROW, PYTHON_ROW = "php", "node", "python"
RUNTIME_ROWS = (PHP_ROW, PYTHON_ROW, NODE_ROW)


class TimeUp(Exception):
    """測定の締め切りを越えた。読めなかったことを「ファイルが無い」と区別するため、`None` を返さずに上げる。"""


class Tree:
    """HEAD の木と、秘密の名前の表を当てた読み取り。"""

    def __init__(self, root: Path, deadline: float) -> None:
        self.root = root
        self.deadline = deadline
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
            done = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, timeout=min(CALL_LIMIT, left))
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


def _major(spec: str) -> Optional[str]:
    m = re.search(r"(\d+(?:\.\d+)?)", spec)
    return m.group(1) if m else None


def _first_number(spec: str) -> str:
    """版の指定の主の数字（`^10.10` → `10`）。主が 0 なら次の数字まで（`^0.115` → `0.115`）。"""
    m = re.search(r"(\d+)(?:\.(\d+))?", spec)
    if not m:
        return ""
    return f"0.{m.group(2)}" if m.group(1) == "0" and m.group(2) else m.group(1)


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


def dependencies(tree: Tree) -> dict[str, dict[str, str]]:
    """言語 → {依存の名前: 版の指定}。根と 1 段下の依存の定義から集める。"""
    return {"php": _php_dependencies(tree), "javascript": _js_dependencies(tree), "python": _py_dependencies(tree)}


def _spec(deps: dict[str, dict[str, str]], lang: str, name: str) -> str:
    return deps[lang][name] if name in deps[lang] else ""


def _versions(tree: Tree, deps: dict[str, dict[str, str]]) -> dict[str, Optional[str]]:
    node = _major(_spec(deps, "javascript", NODE_ROW))
    version_of = {
        "php": _major(_spec(deps, "php", PHP_ROW)),
        "python": _major(_spec(deps, "python", PYTHON_ROW)),
        "javascript": node,
        "typescript": node,
    }
    for f in tree.shallow("go.mod"):
        m = re.search(r"^go\s+(\S+)", tree.text(f), re.M)
        version_of["go"] = m.group(1) if m else None
    return version_of


def measure_languages(tree: Tree, deps: dict[str, dict[str, str]]) -> dict[str, Any]:
    counts = Counter(LANG_EXT[s] for s in (PurePosixPath(p).suffix.lower() for p in tree.files) if s in LANG_EXT)
    version_of = _versions(tree, deps)
    out = []
    for lang, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        dep_lang = "javascript" if lang == "typescript" else lang
        names = deps[dep_lang] if dep_lang in deps else {}
        fws = [f"{fw} {_first_number(names[dep])}".strip() for dep, fw in FRAMEWORKS if dep in names]
        entry: dict[str, Any] = {"name": lang, "frameworks": fws, "files": n}
        if version_of.get(lang):
            entry["version"] = version_of[lang]
        out.append(entry)
    return measured(out)


def _services_block_lines(text: str) -> Iterator[tuple[int, str]]:
    """トップレベルの `services:` の中の行を (字下げ, 行) で返す（空行とコメントは除く）。"""
    inside = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        lead = len(line) - len(line.lstrip())
        if lead == 0:
            inside = line.rstrip() == "services:"
        elif inside:
            yield lead, line


def compose_services(text: str) -> list[tuple[str, str]]:
    """compose のファイルの `services:` の直下の名前と image（YAML を読まずに字下げで拾う）。"""
    names: list[str] = []
    images: dict[str, str] = {}
    indent: Optional[int] = None
    for lead, line in _services_block_lines(text):
        indent = lead if indent is None else indent
        stripped = line.strip()
        if lead == indent and stripped.endswith(":"):
            names.append(stripped[:-1].strip("'\""))
        elif names and stripped.startswith("image:"):
            images[names[-1]] = line.split(":", 1)[1].strip().strip("'\"")
    return [(s, images[s] if s in images else "") for s in names]


def _workflow_services(tree: Tree) -> list[str]:
    """CI のワークフローの `services:` の中の image。"""
    out: list[str] = []
    for f in sorted(p for p in tree.files if check.is_input(p) and p.startswith(".github/")):
        out += [m.group(1) for m in re.finditer(r"^\s+image:\s*['\"]?([\w./:-]+)", tree.text(f), re.M)]
    return out


def measure_services(tree: Tree) -> tuple[dict[str, Any], list[str]]:
    files = tree.shallow("compose*.y*ml", "docker-compose*.y*ml")
    names: list[str] = []
    dbs: set[str] = set()
    for f in files:
        for svc, image in compose_services(tree.text(f)):
            names.append(svc)
            dbs |= {w for w in DB_WORDS if w in svc.lower() or w in image.lower()}
    for image in _workflow_services(tree):
        dbs |= {w for w in DB_WORDS if image.lower().startswith(w)}
    return measured({"container": bool(files), "compose_files": files, "databases": sorted(dbs)}), sorted(set(names))


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


def docs_of(tree: Tree) -> list[str]:
    """指示書と根・1 段下の README。"""
    return [p for p in INSTRUCTION_FILES if p in tree.files] + tree.shallow("README*.md")


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


def default_branch(tree: Tree) -> Optional[str]:
    """既定ブランチ。origin の HEAD の指す先 → ローカルの main・master の順。どれも無ければ `None`。"""
    head = tree.git("symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if head and head.strip():
        return head.strip().split("/", 1)[-1]
    local = tree.words("for-each-ref", "--format=%(refname:short)", "refs/heads")
    return next((b for b in DEFAULT_BRANCH_CANDIDATES if b in local), None)


def measure_branches(tree: Tree) -> dict[str, Any]:
    state = check.branch_state(git.Git(tree.root))
    local = tree.words("for-each-ref", "--format=%(refname:short)", "refs/heads")
    evidence: list[dict[str, Any]] = []
    names: list[str] = []
    for b in check.KNOWN_BRANCHES:
        ref = f"origin/{b}" if b in state["present"] else (b if b in local else None)
        if ref:
            names.append(b)
            last = tree.git("log", "-1", "--format=%cI", ref)
            evidence.append({"text": f"{ref} の最後のコミット: {last.strip() if last else None}"})
    if state["head"]:
        evidence.insert(0, {"text": f"origin の HEAD: {state['head']}"})
    if not names and not state["head"]:
        return unknown(f"origin の HEAD も {'・'.join(check.KNOWN_BRANCHES)} も無い")
    base = [state["head"]] if state["head"] else []
    base += [b for b in names if b not in base]
    production = [b for b in DEFAULT_BRANCH_CANDIDATES if b in names] or base[:1]
    return question({"base": base, "production": production}, evidence)


def _versioned(tree: Tree) -> list[str]:
    out = [f"{f}: version" for f in tree.shallow("package.json", "composer.json") if tree.json(f).get("version")]
    for f in tree.shallow("pyproject.toml"):
        c = tree.toml(f)
        if obj(c.get("project")).get("version") or obj(obj(c.get("tool")).get("poetry")).get("version"):
            out.append(f"{f}: version")
    return out + [p for p in tree.files if p.endswith("plugin.json") and p.count("/") <= 3][:5]


def measure_delivery(tree: Tree) -> dict[str, Any]:
    workflows = sorted(p for p in tree.files if p.startswith(".github/workflows/") and check.is_input(p))
    evidence = tree.grep(workflows, DEPLOY_WORDS) + tree.grep(docs_of(tree), DEPLOY_WORDS)
    tags = tree.words("tag", "--sort=-creatordate")
    candidates = {
        "deploy_files": tree.shallow(*DEPLOY_FILES),
        "versioned": _versioned(tree),
        "tags": {"count": len(tags), "latest": tags[:3]},
    }
    return question(candidates, evidence)


def url_hosts(text: str) -> set[str]:
    """本文の URL のホスト名だけ（github.com とその配下は除く）。"""
    hosts = {m.group(1).lower() for m in re.finditer(r"https?://([\w.-]+)", text)}
    return {h for h in hosts if not h.endswith("github.com") and not h.endswith("githubusercontent.com")}


def measure_issues_repo(tree: Tree) -> dict[str, Any]:
    local = [p for p in tree.files if re.match(r"issues/[^/]+\.md$", p)]
    templates = [p for p in tree.files if re.match(r"\.github/(pull_request_template\.md|PULL_REQUEST_TEMPLATE/|ISSUE_TEMPLATE/)", p, re.I)]
    hosts: set[str] = set()
    for t in templates:
        hosts |= url_hosts(tree.text(t))
    return {"markdown_files": len(local), "templates": templates, "hosts": sorted(hosts)}


def measure_checks(tree: Tree, deps: dict[str, dict[str, str]]) -> dict[str, Any]:
    tools = [{"name": name, "config": f} for name, pats in CHECK_TOOLS for f in tree.shallow(*pats)]
    for f in tree.shallow("pyproject.toml"):
        configured = obj(tree.toml(f).get("tool"))
        tools += [{"name": name, "config": f"{f} [tool.{name}]"} for name in PYPROJECT_TOOLS if name in configured]
    if "nunomaduro/larastan" in deps["php"] or "larastan/larastan" in deps["php"]:
        tools.append({"name": "larastan", "config": "composer.json"})
    return measured({"tools": tools})


def measure_policies(tree: Tree) -> dict[str, Any]:
    evidence = tree.grep([p for p in INSTRUCTION_FILES if p in tree.files], POLICY_WORDS)
    candidates = [{"doc_lint": False, "reject_md_wording_tests": False}, {"doc_lint": True, "reject_md_wording_tests": True}]
    return question(candidates, evidence)


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


def measure_instructions(tree: Tree) -> dict[str, Any]:
    files = [p for p in INSTRUCTION_FILES if p in tree.files]
    imports = [
        {"from": f, "to": _normalize(str(PurePosixPath(f).parent / m.group(1)))} for f in files for m in IMPORT_TOKEN.finditer(tree.text(f))
    ]
    notes = _old_guide_notes(tree, files, imports)
    if not files:
        notes.append("指示書（AGENTS.md・CLAUDE.md など）が無い。NDF と各ランタイムが読む指示書を置くとよい")
    return measured({"files": files, "imports": imports, "notes": notes})


def _library_seeds(deps: dict[str, dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(素材の候補の依存（上限 `MAX_LIBRARIES`）, 解析の SDK の根拠)。処理系の版の行は依存でないので外す。"""
    libraries: list[dict[str, Any]] = []
    analytics: list[dict[str, Any]] = []
    for lang, names in deps.items():
        eco = next((e for k, e in ECOSYSTEM if k == lang), lang)
        for name in sorted(n for n in names if n not in RUNTIME_ROWS):
            if any(w in name.lower() for w in ANALYTICS):
                analytics.append({"text": f"{eco}: {name}"})
            if len(libraries) < MAX_LIBRARIES:
                libraries.append({"name": name, "ecosystem": eco, "alternatives": []})
    return libraries, analytics


def measure_materials(tree: Tree, deps: dict[str, dict[str, str]]) -> dict[str, Any]:
    """素材の宣言の候補。README の冒頭・直接の依存・解析の SDK を根拠にする。決めるのは人である。"""
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


def measure(tree: Tree, step: Step, items: dict[str, Any]) -> dict[str, Any]:
    """git の木から測る項目を `items` へ入れ、リポジトリの中の課題の置き場（`issues` の材料）を返す。"""
    dep = step("languages", lambda: dependencies(tree))
    if dep is None:
        dep = {"php": {}, "javascript": {}, "python": {}}
    else:
        got = step("languages", lambda: measure_languages(tree, dep))
        if got is not None:
            items["languages"] = got
    services_got = step("services", lambda: measure_services(tree))
    services: list[str] = []
    if services_got:
        items["services"], services = services_got
    for key, fn in (
        ("test", lambda: measure_test(tree, dep, services)),
        ("branches", lambda: measure_branches(tree)),
        ("delivery", lambda: measure_delivery(tree)),
        ("checks", lambda: measure_checks(tree, dep)),
        ("ndf_policies", lambda: measure_policies(tree)),
        ("instructions", lambda: measure_instructions(tree)),
        ("materials", lambda: measure_materials(tree, dep)),
    ):
        got = step(key, fn)
        if got is not None:
            items[key] = got
    repo_issues = step("issues", lambda: measure_issues_repo(tree))
    return repo_issues if repo_issues else {"markdown_files": 0, "templates": [], "hosts": []}
