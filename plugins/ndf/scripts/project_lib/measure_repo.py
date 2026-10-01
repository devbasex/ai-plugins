"""git の木から測る項目（P1・P2・P5・P6 の git の分・P7・P9・P10）。標準ライブラリと `git` だけで動く。

ファイルは HEAD の木から読み（`git cat-file`）、秘密の名前の表（I6）に当たるファイルは開かない。
項目の測定は `{"status": "measured", "value": ...}`・`{"status": "question", "candidates": ..., "evidence": ...}`・
`{"status": "unknown", "reason": ...}` のどれかを返す。
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import time
import tomllib
from pathlib import PurePosixPath

from . import fingerprint
from .secret import is_secret_path, redact

MAX_READ = 512 * 1024
MAX_EVIDENCE = 20
LANG_EXT = {
    ".php": "php",
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rb": "ruby",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".cs": "csharp",
    ".sh": "shell",
}
# 依存の名前 → フレームワーク（版は依存の定義の主の数字）
FRAMEWORKS = {
    "laravel/framework": "laravel",
    "symfony/framework-bundle": "symfony",
    "next": "next",
    "react": "react",
    "vue": "vue",
    "nuxt": "nuxt",
    "express": "express",
    "@nestjs/core": "nestjs",
    "hono": "hono",
    "svelte": "svelte",
    "django": "django",
    "flask": "flask",
    "fastapi": "fastapi",
}
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
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md", "KIRO.md", "GEMINI.md")
OLD_GUIDE_MARK = "NDF_PLUGIN_GUIDE_START"
TEST_WORDS = re.compile(r"phpunit|pytest|jest|vitest|rspec|go test|npm (?:run )?test|docker compose exec|run-[\w-]*tests?")
POLICY_WORDS = re.compile(r"doc-lint|文体|文言テスト|markdown-writing|書き方")
DEPLOY_WORDS = re.compile(r"deploy|release|publish|デプロイ|リリース|本番|amplify|sam |(?<![a-z])(?:stg|prd)(?![a-z])", re.I)
IMPORT_TOKEN = re.compile(r"(?:^|\s)@([\w./-]+\.md)\b")


def measured(value) -> dict:
    return {"status": "measured", "value": value}


def question(candidates, evidence) -> dict:
    return {"status": "question", "candidates": candidates, "evidence": evidence[:MAX_EVIDENCE]}


def unknown(reason: str) -> dict:
    return {"status": "unknown", "reason": reason}


class TimeUp(Exception):
    """測定の締め切りを越えた。読めなかったことを「ファイルが無い」と区別するため、`None` を返さずに上げる。"""


class Tree:
    """HEAD の木と、秘密の名前の表を当てた読み取り。"""

    def __init__(self, root, deadline: float):
        self.root = root
        self.deadline = deadline
        self.files = fingerprint.tree(root)
        self.skipped: list[str] = sorted(p for p in self.files if is_secret_path(p))

    def left(self) -> float:
        return self.deadline - time.monotonic()

    def git(self, *args) -> str | None:
        left = self.left()
        if left <= 0:
            raise TimeUp
        try:
            p = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, timeout=min(30.0, left))
        except subprocess.TimeoutExpired:
            if self.left() <= 0:
                raise TimeUp from None
            return None
        except OSError:
            return None
        return p.stdout if p.returncode == 0 else None

    def read(self, path: str) -> str | None:
        """追跡ファイルの HEAD の中身。秘密の名前・大きすぎる・無いファイルは `None`。"""
        if path not in self.files or is_secret_path(path):
            return None
        out = self.git("cat-file", "-p", f"HEAD:{path}")
        return out if out is not None and len(out) <= MAX_READ else None

    def shallow(self, *patterns: str) -> list[str]:
        """根と 1 段下で名前が当たる追跡ファイル。"""
        return sorted(
            p for p in self.files if p.count("/") <= 1 and any(fnmatch.fnmatchcase(p.rsplit("/", 1)[-1], pat) for pat in patterns)
        )

    def json(self, path: str) -> dict:
        try:
            v = json.loads(self.read(path) or "{}")
        except ValueError:
            return {}
        return v if isinstance(v, dict) else {}

    def toml(self, path: str) -> dict:
        try:
            return tomllib.loads(self.read(path) or "")
        except tomllib.TOMLDecodeError:
            return {}

    def grep(self, paths, rx: re.Pattern) -> list[dict]:
        """`paths` の行のうち `rx` に当たるもの（秘密の形は伏せる）。"""
        hits = []
        for path in paths:
            for n, line in enumerate((self.read(path) or "").splitlines(), 1):
                if rx.search(line):
                    hits.append({"path": path, "line": n, "text": redact(line.strip())[:200]})
                    if len(hits) >= MAX_EVIDENCE:
                        return hits
        return hits


def _major(spec: str) -> str | None:
    m = re.search(r"(\d+(?:\.\d+)?)", spec or "")
    return m.group(1) if m else None


def _first_number(spec: str) -> str:
    """版の指定の主の数字（`^10.10` → `10`）。主が 0 なら次の数字まで（`^0.115` → `0.115`）。"""
    m = re.search(r"(\d+)(?:\.(\d+))?", spec or "")
    if not m:
        return ""
    return f"0.{m.group(2)}" if m.group(1) == "0" and m.group(2) else m.group(1)


def dependencies(tree: Tree) -> dict[str, dict[str, str]]:
    """言語 → {依存の名前: 版の指定}。根と 1 段下の依存の定義から集める。"""
    out: dict[str, dict[str, str]] = {"php": {}, "javascript": {}, "python": {}}
    for f in tree.shallow("composer.json"):
        c = tree.json(f)
        for sec in ("require", "require-dev"):
            out["php"].update({k: str(v) for k, v in (c.get(sec) or {}).items()})
    for f in tree.shallow("package.json"):
        c = tree.json(f)
        for sec in ("dependencies", "devDependencies"):
            out["javascript"].update({k: str(v) for k, v in (c.get(sec) or {}).items()})
        if isinstance(c.get("engines"), dict) and c["engines"].get("node"):
            out["javascript"]["node"] = str(c["engines"]["node"])
    for f in tree.shallow("pyproject.toml"):
        c = tree.toml(f)
        proj = c.get("project") or {}
        for d in list(proj.get("dependencies") or []) + [x for v in (proj.get("optional-dependencies") or {}).values() for x in v]:
            name = re.split(r"[\s<>=!~\[;]", str(d), 1)[0].lower()
            out["python"][name] = str(d)
        for group in (c.get("dependency-groups") or {}).values():
            for d in group if isinstance(group, list) else []:
                out["python"][re.split(r"[\s<>=!~\[;]", str(d), 1)[0].lower()] = str(d)
        poetry = (c.get("tool") or {}).get("poetry") or {}
        for sec in ("dependencies", "dev-dependencies"):
            out["python"].update({k.lower(): str(v) for k, v in (poetry.get(sec) or {}).items()})
        for g in (poetry.get("group") or {}).values():
            out["python"].update({k.lower(): str(v) for k, v in (g.get("dependencies") or {}).items()})
        if proj.get("requires-python"):
            out["python"]["python"] = str(proj["requires-python"])
    for f in tree.shallow("requirements*.txt"):
        for line in (tree.read(f) or "").splitlines():
            name = re.split(r"[\s<>=!~\[;#]", line.strip(), 1)[0].lower()
            if name:
                out["python"].setdefault(name, line.strip())
    return out


def measure_languages(tree: Tree, deps: dict) -> dict:
    counts: dict[str, int] = {}
    for p in tree.files:
        lang = LANG_EXT.get(PurePosixPath(p).suffix.lower())
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    version_of = {
        "php": _major(deps["php"].get("php", "")),
        "python": _major(deps["python"].get("python", "")),
        "javascript": _major(deps["javascript"].get("node", "")),
        "typescript": _major(deps["javascript"].get("node", "")),
    }
    for f in tree.shallow("go.mod"):
        m = re.search(r"^go\s+(\S+)", tree.read(f) or "", re.M)
        version_of["go"] = m.group(1) if m else None
    dep_lang = {"typescript": "javascript"}
    out = []
    for lang, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        names = deps.get(dep_lang.get(lang, lang), {})
        fws = [f"{fw} {_first_number(names[dep])}".strip() for dep, fw in FRAMEWORKS.items() if dep in names]
        entry = {"name": lang, "frameworks": fws, "files": n}
        if version_of.get(lang):
            entry["version"] = version_of[lang]
        out.append(entry)
    return measured(out)


def compose_services(text: str) -> list[tuple[str, str]]:
    """compose のファイルの `services:` の直下の名前と image（YAML を読まずに字下げで拾う）。"""
    out, inside, indent, cur = [], False, None, None
    images: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        lead = len(line) - len(line.lstrip())
        if lead == 0:
            inside = line.rstrip() == "services:"
            continue
        if not inside:
            continue
        if indent is None:
            indent = lead
        if lead == indent and line.strip().endswith(":"):
            cur = line.strip()[:-1].strip("'\"")
            out.append(cur)
        elif cur and line.strip().startswith("image:"):
            images[cur] = line.split(":", 1)[1].strip().strip("'\"")
    return [(s, images.get(s, "")) for s in out]


def _workflow_services(tree: Tree) -> list[str]:
    """CI のワークフローの `services:` の中の image。"""
    out = []
    for f in sorted(p for p in tree.files if fingerprint.is_input(p) and p.startswith(".github/")):
        text = tree.read(f) or ""
        for m in re.finditer(r"^\s+image:\s*['\"]?([\w./:-]+)", text, re.M):
            out.append(m.group(1))
    return out


def measure_services(tree: Tree) -> tuple[dict, list[str]]:
    files = tree.shallow("compose*.y*ml", "docker-compose*.y*ml")
    names, dbs = [], set()
    for f in files:
        for svc, image in compose_services(tree.read(f) or ""):
            names.append(svc)
            for w in DB_WORDS:
                if w in svc.lower() or w in image.lower():
                    dbs.add(w)
    for image in _workflow_services(tree):
        for w in DB_WORDS:
            if image.lower().startswith(w):
                dbs.add(w)
    return measured({"container": bool(files), "compose_files": files, "databases": sorted(dbs)}), sorted(set(names))


def _test_scripts(tree: Tree, filename: str) -> list[str]:
    """`filename`（package.json / composer.json）の scripts のうち、名前に test を含むもの。"""
    cmds = []
    for f in tree.shallow(filename):
        for k, v in (tree.json(f).get("scripts") or {}).items():
            if "test" in k:
                cmds.append(f"{f}: {k} = {v}")
    return cmds


def _test_commands(tree: Tree) -> list[str]:
    cmds = _test_scripts(tree, "package.json") + _test_scripts(tree, "composer.json")
    for f in tree.shallow("Makefile"):
        cmds += [f"{f}: {m.group(0).strip()}" for m in re.finditer(r"^test[\w-]*:.*$", tree.read(f) or "", re.M)]
    cmds += [p for p in tree.files if p.count("/") <= 1 and re.search(r"test", p.rsplit("/", 1)[-1]) and p.endswith(".sh")]
    return cmds[:MAX_EVIDENCE]


def measure_test(tree: Tree, deps: dict, services: list[str]) -> dict:
    all_deps = {k for d in deps.values() for k in d}
    runners = []
    for runner, (configs, dep_names) in RUNNERS.items():
        hit_cfg = [p for p in tree.files if p.count("/") <= 2 and any(fnmatch.fnmatchcase(p.rsplit("/", 1)[-1], c) for c in configs)]
        if runner == "go test":
            hit_cfg = [p for p in tree.files if p.endswith("_test.go")][:1]
        if hit_cfg or any(d in all_deps for d in dep_names):
            runners.append({"runner": runner, "config": hit_cfg[:5], "dependency": [d for d in dep_names if d in all_deps]})
    if not runners and any(re.search(r"(^|/)test_[^/]*\.py$", p) for p in tree.files):
        runners.append({"runner": "pytest", "config": [], "dependency": []})
    commands = _test_commands(tree)
    docs = [p for p in INSTRUCTION_FILES if p in tree.files] + tree.shallow("README*.md")
    evidence = tree.grep(docs, TEST_WORDS)
    if not runners and not commands and not evidence:
        return measured({"suites": []})
    return question({"runners": runners, "commands": commands, "compose_services": services}, evidence)


def _last_date(tree: Tree, ref: str) -> str | None:
    out = tree.git("log", "-1", "--format=%cI", ref)
    return out.strip() if out else None


def measure_branches(tree: Tree) -> dict:
    state = fingerprint.branch_state(tree.root)
    local = (tree.git("for-each-ref", "--format=%(refname:short)", "refs/heads") or "").split()
    evidence = []
    names = []
    for b in fingerprint.KNOWN_BRANCHES:
        ref = f"origin/{b}" if b in state["present"] else (b if b in local else None)
        if ref:
            names.append(b)
            evidence.append({"text": f"{ref} の最後のコミット: {_last_date(tree, ref)}"})
    if state["head"]:
        evidence.insert(0, {"text": f"origin の HEAD: {state['head']}"})
    if not names and not state["head"]:
        return unknown("origin の HEAD も main・master・develop も無い")
    base = [state["head"]] if state["head"] else []
    base += [b for b in names if b not in base]
    production = [b for b in ("main", "master") if b in names] or base[:1]
    return question({"base": base, "production": production}, evidence)


def _versioned(tree: Tree) -> list[str]:
    out = []
    for f in tree.shallow("package.json", "composer.json"):
        if tree.json(f).get("version"):
            out.append(f"{f}: version")
    for f in tree.shallow("pyproject.toml"):
        c = tree.toml(f)
        if (c.get("project") or {}).get("version") or ((c.get("tool") or {}).get("poetry") or {}).get("version"):
            out.append(f"{f}: version")
    out += [p for p in tree.files if p.endswith("plugin.json") and p.count("/") <= 3][:5]
    return out


def measure_delivery(tree: Tree) -> dict:
    files = tree.shallow(*DEPLOY_FILES)
    workflows = sorted(p for p in tree.files if p.startswith(".github/workflows/") and fingerprint.is_input(p))
    docs = [p for p in INSTRUCTION_FILES if p in tree.files] + tree.shallow("README*.md")
    evidence = tree.grep(workflows, DEPLOY_WORDS) + tree.grep(docs, DEPLOY_WORDS)
    tags = (tree.git("tag", "--sort=-creatordate") or "").split()
    candidates = {
        "deploy_files": files,
        "versioned": _versioned(tree),
        "tags": {"count": len(tags), "latest": tags[:3]},
    }
    return question(candidates, evidence)


def measure_issues_repo(tree: Tree) -> dict:
    local = [p for p in tree.files if re.match(r"issues/[^/]+\.md$", p)]
    templates = [p for p in tree.files if re.match(r"\.github/(pull_request_template\.md|PULL_REQUEST_TEMPLATE/|ISSUE_TEMPLATE/)", p, re.I)]
    hosts = set()
    for t in templates:
        hosts |= url_hosts(tree.read(t) or "")
    return {"markdown_files": len(local), "templates": templates, "hosts": sorted(hosts)}


def url_hosts(text: str) -> set[str]:
    """本文の URL のホスト名だけ（github.com とその配下は除く）。"""
    hosts = {m.group(1).lower() for m in re.finditer(r"https?://([\w.-]+)", text or "")}
    return {h for h in hosts if not h.endswith("github.com") and not h.endswith("githubusercontent.com")}


def measure_checks(tree: Tree, deps: dict) -> dict:
    tools = []
    for name, pats in CHECK_TOOLS:
        for f in tree.shallow(*pats):
            tools.append({"name": name, "config": f})
    for f in tree.shallow("pyproject.toml"):
        for name in PYPROJECT_TOOLS:
            if name in (tree.toml(f).get("tool") or {}):
                tools.append({"name": name, "config": f"{f} [tool.{name}]"})
    if "nunomaduro/larastan" in deps["php"] or "larastan/larastan" in deps["php"]:
        tools.append({"name": "larastan", "config": "composer.json"})
    return measured({"tools": tools})


def measure_policies(tree: Tree) -> dict:
    docs = [p for p in INSTRUCTION_FILES if p in tree.files]
    evidence = tree.grep(docs, POLICY_WORDS)
    candidates = [{"doc_lint": False, "reject_md_wording_tests": False}, {"doc_lint": True, "reject_md_wording_tests": True}]
    return question(candidates, evidence)


def measure_instructions(tree: Tree) -> dict:
    files = [p for p in INSTRUCTION_FILES if p in tree.files]
    imports, notes = [], []
    for f in files:
        base = PurePosixPath(f).parent
        for m in IMPORT_TOKEN.finditer(tree.read(f) or ""):
            imports.append({"from": f, "to": _normalize(str(base / m.group(1)))})
    for f in files + [i["to"] for i in imports]:
        head = "\n".join((tree.read(f) or "").splitlines()[:5])
        if OLD_GUIDE_MARK in head:
            by = [i["from"] for i in imports if i["to"] == f]
            where = f"{f} を {'・'.join(by)} が読み込んでいる" if by else f"{f} が指示書にある"
            notes.append(f"古い NDF の案内 {where}（{OLD_GUIDE_MARK}）。今の NDF を指さないので、読み込みを外すか消す")
    if not files:
        notes.append("指示書（AGENTS.md・CLAUDE.md など）が無い。NDF と各ランタイムが読む指示書を置くとよい")
    return measured({"files": files, "imports": imports, "notes": notes})


def _normalize(path: str) -> str:
    parts: list[str] = []
    for p in path.split("/"):
        if p == "..":
            if parts:
                parts.pop()
        elif p not in ("", "."):
            parts.append(p)
    return "/".join(parts)
