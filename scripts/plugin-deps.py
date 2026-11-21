#!/usr/bin/env python3
"""依存の検査のサブコマンド（#3370 #3691。詳細設計は issues/issue-3370-3691-design-detail.md の「依存の検査のスクリプト」）。

    python3 scripts/plugin-deps.py targets [--root <リポジトリ>]
    python3 scripts/plugin-deps.py holds   [--root <リポジトリ>]
    python3 scripts/plugin-deps.py uv-pin  [--root <リポジトリ>]
    python3 scripts/plugin-deps.py affected --base <rev> --head <rev> [--repo <r> --pr <番号> --head-sha <sha>] [--out <パス>]
    python3 scripts/plugin-deps.py post-affected --data <パス> --repo <r> --pr <番号か空> --head-sha <sha>

- `targets`: 追跡している lock（`uv.lock`・`package-lock.json`）から `.github/renovate.json` の `ignorePaths` を
  除き、種類（uv・npm）とディレクトリの組を並べる（不変条件 I9）
- `holds`: `.github/renovate.json` の固定と対象の外の規則から、種類・相手・制約・見直す日・理由の 5 つの欄を読む
  （決定 5・不変条件 I3）
- `uv-pin`: `UV_VERSION` のインストーラを 1 回だけ取得して sha256 を計算し、`plugins/ndf/bin/ndf` と
  `scripts/lib/ndf_wrappers.py` の `UV_INSTALLER_SHA256` を同じ値に書き直す（決定 12。Renovate の規則 10 が打つ）
- `affected`: 2 つのコミットの lock の差分から、変わったパッケージ・使うモジュール・そのテスト・推移的な依存の親を
  並べた一覧のデータを `--out` へ書く（決定 10）。GitHub へは書かない。`tomllib` を使うので Python 3.11 以上で打つ
- `post-affected`: 既定のブランチのコードで一覧のデータの PR 番号と head の SHA を確かめ、目印のコメントを作る・
  書き直す・消す（決定 10。`.github/workflows/deps-affected-comment.yml` が打つ）

結果は統一の結果 JSON の 1 行（`tool`・`status`・`summary`・`items`、止まったときは `cause`）を標準出力へ出す。
終了コードは `status` が `ok` か `skipped` なら 0、`stopped` なら 1。

サブコマンドは `COMMANDS` に名前と引数の組み立てと処理の組で登録する。外部（HTTP の取得・`gh`・子プロセス）は
`Fetch`・`Gh`・`Run` の引数で渡し、`main` は `Deps` の既定（このモジュールの `fetch_url`・`gh_cli`・`run_cmd`）を
処理へ渡す。単体テストは記録した応答を返す関数へ差し替える。標準ライブラリだけを使う（根の環境の外でも動く）。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

TOOL = "plugin-deps"
REPO = Path(__file__).resolve().parents[1]
RENOVATE_CONFIG = ".github/renovate.json"
USER_AGENT = "plugin-deps"

# 外部の差し替え口
Fetch = Callable[[str], bytes]  # URL → 本文。失敗は FetchError
Gh = Callable[[list[str]], str]  # `gh` の引数 → 標準出力。失敗は subprocess.CalledProcessError
Run = Callable[[list[str]], "subprocess.CompletedProcess[str]"]  # 子プロセス（check しない）

# lock のファイル名と種類
LOCKS = {"uv.lock": "uv", "package-lock.json": "npm"}

# 固定と対象の外の規則の description の 5 行（決定 5。この順で持つ）
HOLD_FIELDS = (("kind", "種類"), ("partner", "相手"), ("constraint", "制約"), ("review", "見直す日"), ("reason", "理由"))
HOLD_KINDS = ("相手の制約", "自分の非互換", "対象の外", "プレリリースを選ぶ", "脆弱性の例外")

# UV_VERSION とインストーラの sha256 を持つ 2 か所（決定 12）
UV_PIN_FILES = ("plugins/ndf/bin/ndf", "scripts/lib/ndf_wrappers.py")
UV_INSTALLER_URL = "https://astral.sh/uv/{version}/install.sh"
UV_VERSION_RE = re.compile(r'^(UV_VERSION *= *")([0-9.]+)(")', re.M)
UV_SHA_RE = re.compile(r'^(UV_INSTALLER_SHA256 *= *")([0-9a-f]{64})(")', re.M)
# plugins/ndf/bin/ndf のハッシュの注記が名指すインストーラの版
UV_SHA_NOTE_RE = re.compile(r"(releases/download/)([0-9.]+)(/uv-installer\.sh の sha256)")


class FetchError(Exception):
    """取得元に届かない・応答が読めない。"""


class Stop(Exception):
    """処理を止める。`cause` は結果 JSON の `cause`（`config_broken`・`transient` など）。"""

    def __init__(self, cause: str, summary: str, items: list | None = None):
        super().__init__(summary)
        self.cause = cause
        self.summary = summary
        self.items = items or []


@dataclass(frozen=True)
class Target:
    kind: str  # uv・npm
    directory: str  # リポジトリの根からの相対パス（根は "."）
    lock: str


@dataclass(frozen=True)
class Hold:
    rule: int  # packageRules の添字
    packages: tuple[str, ...]
    version: str | None  # allowedVersions が `==<版>` のときのその版
    kind: str
    partner: str
    constraint: str
    review: date
    reason: str


@dataclass
class Result:
    status: str  # ok・skipped・stopped
    summary: str
    items: list = field(default_factory=list)
    cause: str | None = None

    def as_json(self) -> dict:
        out = {"tool": TOOL, "status": self.status, "summary": self.summary, "items": self.items}
        if self.cause:
            out["cause"] = self.cause
        return out


@dataclass(frozen=True)
class Deps:
    fetch: Fetch
    gh: Gh
    run: Run


def fetch_url(url: str) -> bytes:
    try:
        # urllib の既定の User-Agent は astral.sh が 403 で断る
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=60) as res:  # noqa: S310 - 取得元は定数の形
            return res.read()
    except OSError as e:
        raise FetchError(f"{url}: {e}") from e


def gh_cli(args: list[str]) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def run_cmd(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True)


# --- 設定の読み取り ---


def load_config(root: Path) -> dict:
    path = root / RENOVATE_CONFIG
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise Stop("config_broken", f"{RENOVATE_CONFIG} が無い") from e
    except json.JSONDecodeError as e:
        raise Stop("config_broken", f"{RENOVATE_CONFIG} が JSON として読めない: {e}") from e


def glob_regex(pattern: str) -> re.Pattern[str]:
    """Renovate の `ignorePaths` の glob（`**` は階層をまたぐ・`*` はまたがない）を、根からのパス全体に当てる正規表現にする。"""
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def tracked(root: Path) -> list[str]:
    p = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, text=True)
    if p.returncode != 0:
        raise Stop("config_broken", f"git ls-files が失敗した: {p.stderr.strip()}")
    return [r for r in p.stdout.split("\0") if r]


def targets(root: Path) -> list[Target]:
    """追跡している lock から `ignorePaths` を除き、種類とディレクトリの組を返す（不変条件 I9）。"""
    ignore = [glob_regex(p) for p in load_config(root).get("ignorePaths", [])]
    out = []
    for rel in tracked(root):
        name = rel.rsplit("/", 1)[-1]
        if name not in LOCKS or any(r.match(rel) for r in ignore):
            continue
        directory = rel.rsplit("/", 1)[0] if "/" in rel else "."
        out.append(Target(LOCKS[name], directory, rel))
    return sorted(out, key=lambda t: (t.directory, t.kind))


def is_hold_rule(rule: dict) -> bool:
    """固定と対象の外の規則か。形（版の制限・無効・プレリリースの許可）か、description の 1 行目の `種類:` で決める。"""
    desc = rule.get("description")
    first = desc[0] if isinstance(desc, list) and desc else desc if isinstance(desc, str) else ""
    return (
        "allowedVersions" in rule or rule.get("enabled") is False or rule.get("ignoreUnstable") is False or str(first).startswith("種類:")
    )


def read_holds(config: dict) -> list[Hold]:
    """固定と対象の外の規則から 5 つの欄を読む。欄の欠け・順の違い・形の誤りは ValueError（決定 5・不変条件 I3）。"""
    out = []
    for i, rule in enumerate(config.get("packageRules", [])):
        if not is_hold_rule(rule):
            continue
        desc = rule.get("description")
        if not isinstance(desc, list) or len(desc) != len(HOLD_FIELDS):
            raise ValueError(f"packageRules[{i}]: description が {len(HOLD_FIELDS)} 行の配列でない")
        values = {}
        for line, (key, label) in zip(desc, HOLD_FIELDS):
            prefix = f"{label}: "
            if not isinstance(line, str) or not line.startswith(prefix) or not line[len(prefix) :].strip():
                raise ValueError(f"packageRules[{i}]: 欄「{label}」が欠けている（{line!r}）")
            values[key] = line[len(prefix) :].strip()
        if values["kind"] not in HOLD_KINDS:
            raise ValueError(f"packageRules[{i}]: 種類「{values['kind']}」は {'・'.join(HOLD_KINDS)} のどれでもない")
        try:
            review = date.fromisoformat(values["review"])
        except ValueError as e:
            raise ValueError(f"packageRules[{i}]: 見直す日「{values['review']}」が ISO の日付でない") from e
        allowed = rule.get("allowedVersions")
        version = allowed[2:] if isinstance(allowed, str) and allowed.startswith("==") else None
        out.append(
            Hold(
                rule=i,
                packages=tuple(rule.get("matchPackageNames", [])),
                version=version,
                kind=values["kind"],
                partner=values["partner"],
                constraint=values["constraint"],
                review=review,
                reason=values["reason"],
            )
        )
    return out


def dashboard_lines(holds: list[Hold]) -> list[str]:
    """依存の一覧の Issue の頭に並べる固定の行（`dependencyDashboard.header` と同じ中身。#3370 の受け入れ条件 8）。"""
    lines = []
    for h in holds:
        for name in h.packages:
            head = f"{name} {h.version}" if h.version else name
            lines.append(f"- 固定: {head}（{h.kind}・相手 {h.partner}・見直す日 {h.review.isoformat()}）")
    return lines


# --- UV_VERSION とインストーラの sha256 ---


def uv_pins(root: Path) -> dict[str, tuple[str, str]]:
    """2 か所の `UV_VERSION` と `UV_INSTALLER_SHA256` の組を読む。1 か所に 1 つずつ無ければ Stop。"""
    out = {}
    for rel in UV_PIN_FILES:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except FileNotFoundError as e:
            raise Stop("config_broken", f"{rel} が無い") from e
        versions = UV_VERSION_RE.findall(text)
        shas = UV_SHA_RE.findall(text)
        if len(versions) != 1 or len(shas) != 1:
            raise Stop("config_broken", f"{rel} の UV_VERSION と UV_INSTALLER_SHA256 が 1 つずつでない")
        out[rel] = (versions[0][1], shas[0][1])
    return out


def uv_pin(root: Path, fetch: Fetch) -> Result:
    """インストーラを 1 回だけ取得して sha256 を計算し、2 か所を同じ版とハッシュに書き直す（決定 12）。"""
    pins = uv_pins(root)
    versions = {v for v, _ in pins.values()}
    if len(versions) != 1:
        raise Stop("config_broken", "2 か所の UV_VERSION が違う", [{"path": p, "version": v} for p, (v, _) in pins.items()])
    version = versions.pop()
    url = UV_INSTALLER_URL.format(version=version)
    try:
        body = fetch(url)
    except FetchError as e:
        raise Stop("transient", f"uv {version} のインストーラを取得できない（どちらのファイルも書き換えない）: {e}") from e
    sha = hashlib.sha256(body).hexdigest()
    items = [{"url": url, "version": version, "sha256": sha}]
    if all(s == sha for _, s in pins.values()):
        return Result("ok", f"uv {version} のインストーラの sha256 は 2 か所とも同じで、書き換えない", items)
    texts = {}
    for rel in UV_PIN_FILES:
        text = (root / rel).read_text(encoding="utf-8")
        text = UV_SHA_RE.sub(lambda m: m.group(1) + sha + m.group(3), text)
        text = UV_SHA_NOTE_RE.sub(lambda m: m.group(1) + version + m.group(3), text)
        texts[rel] = text
    for rel, text in texts.items():
        (root / rel).write_text(text, encoding="utf-8")
    return Result("ok", f"uv {version} のインストーラの sha256 を 2 か所へ書いた", items)


# --- 確かめるテストの一覧（affected・post-affected。決定 10） ---

# 配布名（正規化した名前）→ import の名前。載らないものは `-` を `_` にした名前（詳細設計の「affected の lock の種類ごとの扱い」）
IMPORT_NAMES = {
    "python-dotenv": ("dotenv",),
    "ruamel-yaml": ("ruamel.yaml",),
    "pyyaml": ("yaml",),
    "markdown-it-py": ("markdown_it",),
}
# import しない直接の依存と、その使われ方。4 つの pyproject.toml の直接の依存は、import の名前が見つかるかここに載る
NO_IMPORT = {
    "bump-my-version": "CLI として使う",
    "pytest-playwright": "pytest のプラグインとして読み込まれる",
    "pytest-xdist": "pytest のプラグインとして読み込まれる",
    "ruff": "CLI として使う",
    "shellcheck-py": "CLI（shellcheck）として使う",
    "mdit-py-plugins": "import する箇所が無い（scripts/lib/ndf_wrappers.py の GROUPS が名前だけを持つ）",
    "slack-sdk": "import する箇所が無い（scripts/lib/ndf_wrappers.py の GROUPS が名前だけを持つ）",
}
AFFECTED_MARKER = "<!-- ndf:deps-affected -->"
COMMENT_AUTHOR = "github-actions[bot]"
COMMENT_LIMIT = 60000  # GitHub のコメントの上限（65536 文字）より小さく切る
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
CONFLICT_RE = re.compile(r"^(<<<<<<<|=======|>>>>>>>)(?: |$)", re.M)
JS_EXTS = (".js", ".mjs", ".cjs")
JS_IMPORT_RE = re.compile(
    r"""\brequire(?:\.resolve)?\(\s*["']([^"']+)["']|\bimport\b[^;"']*?\bfrom\s*["']([^"']+)["']|\bimport\s*["']([^"']+)["']"""
)


@dataclass(frozen=True)
class Change:
    name: str
    before: str | None  # 消えたパッケージは after が None、入ったパッケージは before が None
    after: str | None
    parents: tuple[str, ...]  # 推移的な依存のとき、lock の関係を逆に辿って届く直接の依存。直接の依存なら空


@dataclass(frozen=True)
class WorkflowRun:
    """`workflow_run` の出来事から取った、信頼できる値（fork の PR では pr が空）。"""

    repo: str
    pr: int | None
    head_sha: str


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def lock_graph(text: str) -> tuple[dict[str, set[str]], dict[str, set[str]], set[str]]:
    """lock の本文 → (名前 → バージョンの集まり, 名前 → 依存する名前, 直接の依存)。空の本文は空の lock。読めなければ ValueError。"""
    versions: dict[str, set[str]] = {}
    edges: dict[str, set[str]] = {}
    direct: set[str] = set()
    if not text.strip():
        return versions, edges, direct
    if CONFLICT_RE.search(text):
        raise ValueError("衝突のマーカーがある")
    if text.lstrip().startswith("{"):
        try:
            packages = json.loads(text)["packages"]
            for key, entry in packages.items():
                deps_ = set()
                for k in ("dependencies", "optionalDependencies", "peerDependencies", "devDependencies"):
                    deps_ |= set(entry.get(k) or {})
                if key == "":
                    direct |= deps_
                    continue
                name = key.rsplit("node_modules/", 1)[-1]
                versions.setdefault(name, set()).add(str(entry["version"]))
                edges.setdefault(name, set()).update(deps_)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise ValueError(f"package-lock.json の形が読めない: {e!r}") from e
        return versions, edges, direct
    import tomllib  # 3.11 から。ほかのサブコマンドは 3.10 でも動くよう、ここで読む

    try:
        for entry in tomllib.loads(text)["package"]:
            name = norm(entry["name"])
            deps_ = {norm(d["name"]) for d in entry.get("dependencies", [])}
            for key in ("optional-dependencies", "dev-dependencies"):
                for group in (entry.get(key) or {}).values():
                    deps_ |= {norm(d["name"]) for d in group}
            source = entry.get("source") or {}
            if "virtual" in source or "editable" in source or "directory" in source:
                direct |= deps_
            else:
                versions.setdefault(name, set()).add(str(entry["version"]))
            edges.setdefault(name, set()).update(deps_)
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise ValueError(f"uv.lock の形が読めない: {e!r}") from e
    return versions, edges, direct


def parents_of(name: str, edges: dict[str, set[str]], direct: set[str]) -> tuple[str, ...]:
    """逆向きの関係を辿り、届いた直接の依存を返す（直接の依存なら空）。"""
    if name in direct:
        return ()
    reverse: dict[str, set[str]] = {}
    for src, dsts in edges.items():
        for d in dsts:
            reverse.setdefault(d, set()).add(src)
    seen, todo, found = {name}, [name], set()
    while todo:
        for up in reverse.get(todo.pop(), ()):
            if up in seen:
                continue
            seen.add(up)
            if up in direct:
                found.add(up)
            else:
                todo.append(up)
    return tuple(sorted(found))


def lock_diff(old: str, new: str) -> list[Change]:
    """2 つの lock（`uv.lock` か `package-lock.json`）の本文から、変わったパッケージと推移的な依存の親を返す。読めなければ ValueError。"""
    old_v, old_e, old_d = lock_graph(old)
    new_v, new_e, new_d = lock_graph(new)
    out = []
    for name in sorted(set(old_v) | set(new_v)):
        before, after = old_v.get(name), new_v.get(name)
        if before == after:
            continue
        # 入った・変わったパッケージは head の関係から、消えたパッケージは base の関係から引く
        edges, direct = (new_e, new_d) if after else (old_e, old_d)
        out.append(
            Change(
                name=name,
                before=", ".join(sorted(before)) if before else None,
                after=", ".join(sorted(after)) if after else None,
                parents=parents_of(name, edges, direct),
            )
        )
    return out


def git_out(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def changed_locks(root: Path, base: str, head: str) -> list[str]:
    """base と head の間で変わった lock のパス（Renovate の `ignorePaths` に当たるものを除く）。"""
    p = git_out(root, "diff", "--name-only", "-z", base, head, "--")
    if p.returncode != 0:
        raise Stop("config_broken", f"git diff {base} {head} が失敗した: {p.stderr.strip()}")
    ignore = [glob_regex(g) for g in load_config(root).get("ignorePaths", [])]
    return sorted(r for r in p.stdout.split("\0") if r and r.rsplit("/", 1)[-1] in LOCKS and not any(g.match(r) for g in ignore))


def show(root: Path, rev: str, path: str) -> str:
    """`git show <rev>:<path>`。そのコミットに無ければ空。"""
    p = git_out(root, "show", f"{rev}:{path}")
    return p.stdout if p.returncode == 0 else ""


class SourceIndex:
    """追跡しているソースの import と、テストからモジュールへの参照の索引（標準ライブラリの ast と正規表現で読む）。"""

    def __init__(self, root: Path, files: list[str]):
        self.files = files
        self.py_imports: dict[str, set[str]] = {}  # .py → import した名前（`from a import b` は a と a.b）
        self.uses: dict[str, set[str]] = {}  # .py → import か文字列の名指しで使うファイル
        self.js_imports: dict[str, set[str]] = {}  # .js → require・import のパッケージ名
        self.dotted: dict[str, list[str]] = {}
        file_set = set(files)
        by_base: dict[str, list[str]] = {}
        for f in files:
            by_base.setdefault(f.rsplit("/", 1)[-1], []).append(f)
            if f.endswith(".py"):
                self.dotted.setdefault(self.dotted_name(f, file_set), []).append(f)
        for f in files:
            try:
                text = (root / f).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if f.endswith(JS_EXTS):
                self.js_imports[f] = {js_package(s) for m in JS_IMPORT_RE.finditer(text) for s in m.groups() if s and not s.startswith(".")}
            elif f.endswith(".py"):
                self.read_py(f, text, file_set, by_base)

    @staticmethod
    def dotted_name(f: str, file_set: set[str]) -> str:
        parts = f[:-3].split("/")
        if parts[-1] == "__init__":
            parts.pop()
        i = len(parts) - 1
        while i > 0 and "/".join(parts[:i] + ["__init__.py"]) in file_set:
            i -= 1
        return ".".join(parts[i:])

    def resolve(self, name: str, importer: str) -> list[str]:
        """import の名前 → ファイル。同じ名前が複数あれば、import する側とパスの頭が長く重なるものを選ぶ。"""
        found = self.dotted.get(name, [])
        if len(found) < 2:
            return found
        best = max(len(os.path.commonprefix([f, importer])) for f in found)
        return [f for f in found if len(os.path.commonprefix([f, importer])) == best]

    def read_py(self, f: str, text: str, file_set: set[str], by_base: dict[str, list[str]]) -> None:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return
        names: set[str] = set()
        uses: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module)
                names |= {f"{node.module}.{a.name}" for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                base = f.split("/")[: -node.level]
                mod = base + (node.module.split(".") if node.module else [])
                for cand in [mod] + [mod + [a.name] for a in node.names]:
                    for p in ("/".join(cand) + ".py", "/".join(cand + ["__init__.py"])):
                        if p in file_set:
                            uses.add(p)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.endswith((".py", *JS_EXTS)):
                base_name = node.value.rsplit("/", 1)[-1]
                for target in by_base.get(base_name, []):
                    uses.add(target)
        for n in names:
            uses.update(self.resolve(n, f))
        uses.discard(f)
        self.py_imports[f] = names
        self.uses[f] = uses

    def modules(self, scope: list[str], kind: str, name: str) -> list[str] | None:
        """scope のファイルのうち、配布名 name を使うもの。import しない依存なら None。"""
        if kind == "npm":
            return [f for f in scope if name in self.js_imports.get(f, ())]
        if name in NO_IMPORT:
            return None
        wanted = IMPORT_NAMES.get(name, (name.replace("-", "_"),))
        return [f for f in scope if any(i == w or i.startswith(w + ".") for i in self.py_imports.get(f, ()) for w in wanted)]

    def tests(self, module: str) -> list[str]:
        """module を使うテスト（直接、か tests の下の補助のモジュールを 1 つ挟んで）。module がテストならそれ自身を含む。"""
        out = {module} if is_test(module) else set()
        helpers = {h for h, u in self.uses.items() if module in u and not is_test(h) and "/tests/" in f"/{h}"}
        for t, u in self.uses.items():
            if is_test(t) and (module in u or u & helpers):
                out.add(t)
        return sorted(out)


def is_test(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return base.endswith(".py") and (base.startswith("test_") or base.endswith("_test.py"))


def js_package(spec: str) -> str:
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") else parts[0]


def scope_files(files: list[str], directory: str, kind: str, project_dirs: set[str]) -> list[str]:
    """lock のプロジェクトのソース（その下の別のプロジェクトのディレクトリを除く）。"""
    exts = (".py",) if kind == "uv" else JS_EXTS
    prefix = "" if directory == "." else directory + "/"
    nested = [d + "/" for d in project_dirs if d != directory and (d + "/").startswith(prefix) and d != "."]
    return [
        f
        for f in files
        if f.startswith(prefix) and f.endswith(exts) and "node_modules/" not in f and not any(f.startswith(n) for n in nested)
    ]


def marker_comments(gh: Gh, repo: str, pr: int) -> list[int]:
    """PR の目印のコメント（1 行目が目印で、作成者が github-actions[bot]）の ID。"""
    jq = f'.[] | select(.user.login == "{COMMENT_AUTHOR}" and (.body | split("\\n")[0]) == "{AFFECTED_MARKER}") | .id'
    out = gh(["api", "--paginate", f"repos/{repo}/issues/{pr}/comments", "--jq", jq])
    return [int(x) for x in out.split()]


def affected(
    root: Path,
    base: str,
    head: str,
    *,
    pr: int | None = None,
    head_sha: str | None = None,
    repo: str | None = None,
    gh: Gh | None = None,
    out: Path | None = None,
) -> Result:
    """base と head の lock の差分から一覧のデータを作り、out へ書く。GitHub へは書かない（読むのは目印のコメントの有無だけ）。

    lock の差分が無く、目印のコメントも無ければ out を書かない。読めない lock では理由のデータを書き、ok で終える（E6）。
    """
    data: dict = {"version": 1, "pr": pr, "head_sha": head_sha, "locks": [], "error": None}
    locks = changed_locks(root, base, head)
    if locks:
        files = tracked(root)
        project_dirs = {f.rsplit("/", 1)[0] if "/" in f else "." for f in files if f.rsplit("/", 1)[-1] in LOCKS}
        index = None
        for lock in locks:
            directory = lock.rsplit("/", 1)[0] if "/" in lock else "."
            kind = LOCKS[lock.rsplit("/", 1)[-1]]
            try:
                changes = lock_diff(show(root, base, lock), show(root, head, lock))
            except ValueError as e:
                data["locks"] = []
                data["error"] = {"cause": "invalid", "reason": f"{lock}: {e}"}
                break
            index = index or SourceIndex(root, [f for f in files if f.endswith((".py", *JS_EXTS))])
            scope = scope_files(files, directory, kind, project_dirs)
            rows = []
            for c in changes:
                mods = index.modules(scope, kind, c.name)
                rows.append(
                    {
                        "name": c.name,
                        "before": c.before,
                        "after": c.after,
                        "parents": list(c.parents),
                        "no_import": NO_IMPORT[c.name] if mods is None else None,
                        "modules": [{"path": m, "tests": index.tests(m)} for m in mods or []],
                    }
                )
            if rows:
                data["locks"].append({"lock": lock, "kind": kind, "changes": rows})
    if data["error"]:
        summary, cause = f"一覧を作れない: {data['error']['reason']}", "invalid"
    else:
        summary, cause = f"変わったパッケージは {sum(len(x['changes']) for x in data['locks'])} 個", None
    if not data["locks"] and not data["error"]:
        try:
            keep = bool(repo and pr and gh and marker_comments(gh, repo, pr))
        except (subprocess.CalledProcessError, OSError, ValueError):
            keep = True  # 読めなければデータを置き、既定のブランチの側で決める
        if not keep:
            return Result("ok", "lock の差分が無く、目印のコメントも無いので一覧のデータを置かない")
        summary = "lock の差分が無い（目印のコメントを消すためにデータを置く）"
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return Result("ok", summary, data["locks"], cause)


def fence(lines: list[str]) -> str:
    """artifact の文字列をコードのブロックに入れる（中のバッククォートの並びより長い囲みにする）。"""
    text = "\n".join(re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", line) for line in lines)
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}text\n{text}\n{ticks}"


def affected_body(data: dict) -> str | None:
    """一覧のコメントの本文。載せるものが無ければ None。本文の骨組みはこのコードが書き、artifact の値はコードのブロックに入れる。"""
    sha = data["head_sha"]
    if data["error"]:
        return f"{AFFECTED_MARKER}\n一覧を作れない（`{sha}`）。理由:\n\n{fence([str(data['error'].get('reason'))])}\n"
    if not data["locks"]:
        return None
    lines = []
    for lock in data["locks"]:
        lines.append(str(lock["lock"]))
        for c in lock["changes"]:
            head = f"  {c['name']} {c['before'] or '（無し）'} → {c['after'] or '（消えた）'}"
            if c["parents"]:
                head += f"（推移的な依存。親: {', '.join(map(str, c['parents']))}）"
            lines.append(head)
            if c.get("no_import"):
                lines.append(f"    import しない: {c['no_import']}")
            elif not c["modules"]:
                lines.append("    使うモジュールが無い")
            for m in c["modules"]:
                lines.append(f"    {m['path']}")
                lines.append("      テスト: " + (", ".join(map(str, m["tests"])) if m["tests"] else "テストが無い"))
    total, kept = len(lines), []
    for line in lines:
        if sum(len(x) + 1 for x in kept) + len(line) > COMMENT_LIMIT - 500:
            kept.append(f"…（{total - len(kept)} 行を省いた）")
            break
        kept.append(line)
    return f"{AFFECTED_MARKER}\n### 確かめるテストの一覧（lock の差分から。対象の head: `{sha}`）\n\n{fence(kept)}\n"


def read_affected(path: Path) -> dict:
    """一覧のデータを形だけ確かめて読む（PR のコードが作ったデータなので、決めた形の外は受けない）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ok = (
            isinstance(data, dict)
            and isinstance(data.get("pr"), int)
            and isinstance(data.get("head_sha"), str)
            and SHA_RE.match(data["head_sha"])
            and isinstance(data.get("locks"), list)
            and (data.get("error") is None or isinstance(data["error"], dict))
            and all(isinstance(x, dict) and isinstance(x.get("changes"), list) for x in data["locks"])
        )
    except (OSError, ValueError) as e:
        raise Stop("invalid", f"一覧のデータが読めない: {e}") from e
    if not ok:
        raise Stop("invalid", "一覧のデータの形が違う")
    return data


def post_affected(data: Path, run: WorkflowRun, gh: Gh) -> Result:
    """一覧のデータの PR 番号と head の SHA を確かめてから、目印のコメントを作るか書き直すか消す。"""
    d = read_affected(data)
    pr, sha = d["pr"], d["head_sha"]
    if sha != run.head_sha or (run.pr is not None and pr != run.pr):
        return Result("skipped", f"データの PR #{pr}・{sha} が実行の PR #{run.pr}・{run.head_sha} と合わないので書かない")
    try:
        current = gh(["api", f"repos/{run.repo}/pulls/{pr}", "--jq", ".head.sha"]).strip()
        if current != sha:
            return Result("skipped", f"PR #{pr} の今の head は {current} で、古い実行（{sha}）の結果は書かない")
        ids = marker_comments(gh, run.repo, pr)
        body = affected_body(d)
        items = [{"pr": pr, "head_sha": sha}]
        for extra in ids[1:]:
            gh(["api", "-X", "DELETE", f"repos/{run.repo}/issues/comments/{extra}"])
        if body is None:
            if not ids:
                return Result("ok", f"PR #{pr} は lock の差分も目印のコメントも無い", items)
            gh(["api", "-X", "DELETE", f"repos/{run.repo}/issues/comments/{ids[0]}"])
            return Result("ok", f"PR #{pr} は lock の差分が無くなったので目印のコメントを消した", items)
        if ids:
            gh(["api", "-X", "PATCH", f"repos/{run.repo}/issues/comments/{ids[0]}", "-f", f"body={body}"])
            return Result("ok", f"PR #{pr} の目印のコメントを書き直した", items)
        gh(["api", "-X", "POST", f"repos/{run.repo}/issues/{pr}/comments", "-f", f"body={body}"])
        return Result("ok", f"PR #{pr} に目印のコメントを作った", items)
    except subprocess.CalledProcessError as e:
        raise Stop("transient", f"gh api が失敗した（{e.returncode}）: {(e.stderr or '').strip()[:300]}") from e


# --- サブコマンド ---


def cmd_targets(args: argparse.Namespace, deps: Deps) -> Result:
    found = targets(args.root)
    items = [{"kind": t.kind, "directory": t.directory, "lock": t.lock} for t in found]
    return Result("ok", f"対象の lock は {len(found)} 個", items)


def cmd_holds(args: argparse.Namespace, deps: Deps) -> Result:
    try:
        holds = read_holds(load_config(args.root))
    except ValueError as e:
        raise Stop("config_broken", str(e)) from e
    items = [
        {
            "rule": h.rule,
            "packages": list(h.packages),
            "version": h.version,
            "kind": h.kind,
            "partner": h.partner,
            "constraint": h.constraint,
            "review": h.review.isoformat(),
            "reason": h.reason,
        }
        for h in holds
    ]
    return Result("ok", f"固定と対象の外の規則は {len(holds)} 個", items)


def cmd_uv_pin(args: argparse.Namespace, deps: Deps) -> Result:
    return uv_pin(args.root, deps.fetch)


def cmd_affected(args: argparse.Namespace, deps: Deps) -> Result:
    return affected(args.root, args.base, args.head, pr=args.pr, head_sha=args.head_sha, repo=args.repo, gh=deps.gh, out=args.out)


def cmd_post_affected(args: argparse.Namespace, deps: Deps) -> Result:
    return post_affected(args.data, WorkflowRun(args.repo, args.pr, args.head_sha), deps.gh)


def add_affected(p: argparse.ArgumentParser) -> None:
    add_root(p)
    p.add_argument("--base", required=True, help="宛先の先頭（ワークフローでは HEAD^1）")
    p.add_argument("--head", required=True, help="PR のマージのコミット（ワークフローでは HEAD）")
    p.add_argument("--repo", help="<owner>/<名前>。目印のコメントの有無を読む")
    p.add_argument("--pr", type=int, help="PR の番号")
    p.add_argument("--head-sha", help="PR の head の SHA（マージのコミットでない）")
    p.add_argument("--out", type=Path, help="一覧のデータを書く先")


def add_post_affected(p: argparse.ArgumentParser) -> None:
    p.add_argument("--data", type=Path, required=True, help="artifact deps-affected の JSON")
    p.add_argument("--repo", required=True, help="<owner>/<名前>")
    p.add_argument(
        "--pr", type=lambda v: int(v) if v else None, default=None, help="workflow_run の pull_requests[0].number（fork では空）"
    )
    p.add_argument("--head-sha", required=True, help="workflow_run の head_sha")


def add_root(p: argparse.ArgumentParser) -> None:
    p.add_argument("--root", type=Path, default=REPO, help="リポジトリの根（既定はこのスクリプトのリポジトリ）")


# 名前 → (引数の組み立て, 処理)。後の単位はここへ行を足す
COMMANDS: dict[str, tuple[Callable[[argparse.ArgumentParser], None], Callable[[argparse.Namespace, Deps], Result]]] = {
    "targets": (add_root, cmd_targets),
    "holds": (add_root, cmd_holds),
    "uv-pin": (add_root, cmd_uv_pin),
    "affected": (add_affected, cmd_affected),
    "post-affected": (add_post_affected, cmd_post_affected),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="plugin-deps.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, (setup, _) in COMMANDS.items():
        setup(sub.add_parser(name))
    args = parser.parse_args(argv)
    deps = Deps(fetch=fetch_url, gh=gh_cli, run=run_cmd)
    try:
        result = COMMANDS[args.command][1](args, deps)
    except Stop as e:
        result = Result("stopped", e.summary, e.items, e.cause)
    print(json.dumps(result.as_json(), ensure_ascii=False))
    return 1 if result.status == "stopped" else 0


if __name__ == "__main__":
    sys.exit(main())
