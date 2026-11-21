#!/usr/bin/env python3
"""依存の検査のサブコマンド（#3370 #3691。詳細設計は issues/issue-3370-3691-design-detail.md の「依存の検査のスクリプト」）。

    python3 scripts/plugin-deps.py targets [--root <リポジトリ>]
    python3 scripts/plugin-deps.py holds   [--root <リポジトリ>]
    python3 scripts/plugin-deps.py uv-pin  [--root <リポジトリ>]
    python3 scripts/plugin-deps.py affected --base <rev> --head <rev> [--repo <r> --pr <番号> --head-sha <sha>] [--out <パス>]
    python3 scripts/plugin-deps.py post-affected --data <パス> --repo <r> --pr <番号か空> --head-sha <sha>
    uv run --frozen --project . python scripts/plugin-deps.py report [--root <リポジトリ>]
    python3 scripts/plugin-deps.py action-pins --base <コミット> --head <コミット> [--root <リポジトリ>]
    python3 scripts/plugin-deps.py age [--root <リポジトリ>] [--base <コミット> --head <コミット>] [--renovate-version <版>]
    python3 scripts/plugin-deps.py cli-help --runtime <gh|claude|codex> [--image <イメージ>] [--root <表の作業ツリー>]
    python3 scripts/plugin-deps.py renovate-gate --pr <番号> --head <SHA> --bot <slug>[bot] [--post]
    python3 scripts/plugin-deps.py renovate-log --log <記録> [--summary <Markdown>]

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
- `report`: 対象の lock の脆弱性・解いてよい固定・プレリリースを選べる依存を集め、目印と作成者で引いた 1 件の
  Issue を作る・書き直す・閉じる（決定 9・不変条件 I2・I11。packaging を使うので根の uv の環境で打つ）
- `action-pins`: `base` と `head` の差分で足した `.github/workflows/` の外部の Action の `uses:` の行ごとに、注記のタグが指す
  コミット（注記つきのタグは指す先まで辿る）が行の SHA と同じかを確かめる（決定 13・不変条件 I10）
- `age`: `--base` と `--head` の差分で入った版（lock の各パッケージ・`UV_VERSION`・Renovate の版）と `--renovate-version` の版の
  公開日を読み、公開から 7 日に満たないか公開日が読めないものを拒む。脆弱性の例外の規則（規則 12）の組だけを通す
  （#3370 の受け入れ条件 6・不変条件 I1・決定 2・6・9）
- `cli-help`: `plugins/ndf/contracts/domains/cli-floors.json` の `help` の見本の引数が、CLI の `--help` の出力にあるかを
  照らす（決定 11。#3691 の受け入れ条件 3）。`--image` を渡すとそのイメージの中の CLI を 1 回の `docker run` で全部の
  見本について打ち、無ければホストの CLI を打つ。`--root` からは表の JSON を読むだけでコードを読まない
- `renovate-gate`: Renovate の PR の今の head で、最も新しい `renovate/artifacts` が Renovate の App の bot の書いた `success`
  かを確かめる。`--post` で結果を commit status `ndf/renovate-gate` として付ける（`.github/workflows/renovate-gate.yml` が
  既定ブランチのコードで打つ。#3370 の受け入れ条件 9・不変条件 I4・決定 8）
- `renovate-log`: Renovate の JSON の記録から、lock の作り直し・`postUpgradeTasks`・解決の失敗で PR もブランチも
  作られなかった更新を並べ、1 件でもあれば失敗で終える（`.github/workflows/renovate.yml` が打つ。#3370 の受け入れ条件 7）

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
import secrets
import shlex
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from datetime import datetime, timedelta, timezone
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
    return _read_json(root / RENOVATE_CONFIG, RENOVATE_CONFIG)


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
    return git_show(root, rev, path) or ""


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


# --- 脆弱性と解いてよい固定の知らせ（report。#3691 の受け入れ条件 6・決定 4・5・9・不変条件 I2・I11） ---
#
# 取得（uv audit・npm audit・PyPI の JSON API・gh issue list）をすべて終えてから Issue を書く。1 つでも失敗したら
# Stop("transient") で止まり、Issue に触れない（I11）。管理の Issue は本文の 1 行目の目印と作成者で引く（I2）。
# 版の比べとマーカーの評価は packaging を使う（根の uv の環境。ほかのサブコマンドは標準ライブラリだけで動くよう、
# 関数の中で import する）。

REPORT_MARK = "<!-- ndf:supply-chain-report -->"
REPORT_AUTHOR = "app/github-actions"  # gh の JSON の author.login（github-actions[bot]）
REPORT_LABEL = "supply-chain"
REPORT_TITLE = "脆弱性と解いてよい固定の知らせ"
PYPI_URL = "https://pypi.org/pypi/{name}/json"
PYPI_VERSION_URL = "https://pypi.org/pypi/{name}/{version}/json"
# 公開から待つ日数（正は plugins/ndf/pyproject.toml の exclude-newer。不変条件 I6 をテストが縛る）
WAIT_DAYS = 7
# 7 日たった最新の安定版がこれより古ければ、プレリリースを選べる依存に出す（決定 4）
STALE_DAYS = 365
UV_AUDIT_SCHEMAS = ("preview",)
NPM_AUDIT_REPORT_VERSION = 2


@dataclass(frozen=True)
class Release:
    version: object  # packaging.version.Version
    published: date


def _json_page(fetch: Fetch, url: str, cache: dict) -> dict:
    if url not in cache:
        try:
            cache[url] = json.loads(fetch(url))
        except (FetchError, ValueError) as e:
            raise Stop("transient", f"PyPI の取得に失敗した（Issue に触れない）: {e}") from e
    return cache[url]


def pypi_releases(fetch: Fetch, name: str, cache: dict) -> list[Release]:
    """版ごとの公開日（最初のファイルの upload_time）。ファイルが無い版・取り下げた版・PEP 440 でない版は除く。"""
    from packaging.version import InvalidVersion, Version

    data = _json_page(fetch, PYPI_URL.format(name=name), cache)
    out = []
    try:
        for raw, files in data["releases"].items():
            files = [f for f in files if not f.get("yanked")]
            if not files:
                continue
            try:
                version = Version(raw)
            except InvalidVersion:
                continue
            out.append(Release(version, min(date.fromisoformat(f["upload_time_iso_8601"][:10]) for f in files)))
    except (KeyError, TypeError, AttributeError, ValueError) as e:
        raise Stop("transient", f"PyPI の {name} の応答の形が読めない（Issue に触れない）: {e!r}") from e
    return sorted(out, key=lambda r: r.version)


def aged(releases: list[Release], today: date) -> list[Release]:
    return [r for r in releases if (today - r.published).days >= WAIT_DAYS]


def latest_stable(releases: list[Release]) -> Release | None:
    stables = [r for r in releases if not r.version.is_prerelease]
    return stables[-1] if stables else None


def uv_audit(root: Path, target: Target, run: Run) -> list[dict]:
    directory = str(root / target.directory)
    p = run(["uv", "audit", "--frozen", "--project", directory, "--output-format", "json"])
    where = f"{target.directory}: uv audit"
    if p.returncode not in (0, 1):
        raise Stop("transient", f"{where} が終了コード {p.returncode} で失敗した（Issue に触れない）: {p.stderr.strip()[-300:]}")
    try:
        data = json.loads(p.stdout)
        schema = data["schema"]["version"]
        vulns = data["vulnerabilities"]
        rows = [
            {
                "section": "vulnerability",
                "target": target.directory,
                "package": v["dependency"]["name"],
                "version": v["dependency"]["version"],
                "advisory": v["id"],
                "link": v.get("link") or "",
                "fix": ", ".join(v.get("fix_versions") or []) or "無し",
            }
            for v in vulns
        ]
    except (ValueError, KeyError, TypeError) as e:
        raise Stop("transient", f"{where} の JSON が読めない（Issue に触れない）: {e!r}") from e
    if schema not in UV_AUDIT_SCHEMAS:
        raise Stop("transient", f"{where} の schema.version「{schema}」は読める形（{'・'.join(UV_AUDIT_SCHEMAS)}）でない")
    if (p.returncode == 1) != bool(rows):
        raise Stop("transient", f"{where} の終了コード {p.returncode} と脆弱性の {len(rows)} 件が食い違う")
    return rows


def _npm_fix(fix) -> str:
    if isinstance(fix, dict):
        return f"{fix.get('name')} {fix.get('version')}" + ("（メジャー）" if fix.get("isSemVerMajor") else "")
    return "あり" if fix else "無し"


def npm_audit(root: Path, target: Target, run: Run) -> list[dict]:
    p = run(["npm", "audit", "--prefix", str(root / target.directory), "--package-lock-only", "--json"])
    where = f"{target.directory}: npm audit"
    try:
        data = json.loads(p.stdout)
        if "error" in data or data.get("auditReportVersion") != NPM_AUDIT_REPORT_VERSION:
            raise ValueError(data.get("message") or f"auditReportVersion が {NPM_AUDIT_REPORT_VERSION} でない")
        packages = json.loads((root / target.lock).read_text(encoding="utf-8")).get("packages", {})
        rows = []
        for name, v in data["vulnerabilities"].items():
            version = next((packages.get(n, {}).get("version") for n in v.get("nodes", []) if n in packages), None) or "不明"
            for via in v["via"]:
                if isinstance(via, dict):  # 文字列は他の依存を経た影響で、勧告はその依存の行が持つ
                    rows.append(
                        {
                            "section": "vulnerability",
                            "target": target.directory,
                            "package": name,
                            "version": version,
                            "advisory": via["url"].rstrip("/").rsplit("/", 1)[-1],
                            "link": via["url"],
                            "fix": _npm_fix(v.get("fixAvailable")),
                        }
                    )
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as e:
        raise Stop("transient", f"{where} が失敗した（終了コード {p.returncode}。Issue に触れない）: {e}") from e
    if (p.returncode != 0) != bool(rows):
        raise Stop("transient", f"{where} の終了コード {p.returncode} と脆弱性の {len(rows)} 件が食い違う")
    return rows


def _requirements(lines: list) -> list:
    from packaging.requirements import InvalidRequirement, Requirement

    try:
        return [Requirement(line) for line in lines if isinstance(line, str)]
    except InvalidRequirement as e:
        raise Stop("config_broken", f"依存の行が読めない: {e}") from e


def uv_projects(root: Path, found: list[Target]) -> list[dict]:
    """uv の対象の pyproject.toml を読む。直接の依存（パスの依存を除く）と、マーカーを評価する環境を返す。"""
    import tomllib

    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.utils import canonicalize_name

    out = []
    for t in found:
        if t.kind != "uv":
            continue
        rel = f"{t.directory}/pyproject.toml" if t.directory != "." else "pyproject.toml"
        try:
            data = tomllib.loads((root / rel).read_text(encoding="utf-8"))
            floor = [s.version for s in SpecifierSet(data["project"]["requires-python"]) if s.operator in (">=", "~=", "==")]
        except (OSError, tomllib.TOMLDecodeError, KeyError, InvalidSpecifier) as e:
            raise Stop("config_broken", f"{rel} の requires-python が読めない: {e!r}") from e
        if not floor:
            raise Stop("config_broken", f"{rel} の requires-python に下限が無い")
        project = data["project"]
        lines = list(project.get("dependencies", []))
        for group in (project.get("optional-dependencies") or {}, data.get("dependency-groups") or {}):
            for values in group.values():
                lines += values
        sources = {canonicalize_name(n) for n in data.get("tool", {}).get("uv", {}).get("sources", {})}
        reqs = [r for r in _requirements(lines) if canonicalize_name(r.name) not in sources]
        python = ".".join(min(floor, key=lambda v: tuple(int(x) for x in v.split(".") if x.isdigit())).split(".")[:2])
        env = {
            "python_version": python,
            "python_full_version": f"{python}.0",
            "sys_platform": "linux",
            "platform_system": "Linux",
            "os_name": "posix",
            "implementation_name": "cpython",
            "platform_python_implementation": "CPython",
        }
        out.append({"directory": t.directory, "requirements": reqs, "env": env})
    return out


def judge_constraint(old, new, candidates: list, pinned) -> tuple[str, list]:
    """相手の新しい制約（無ければ None）と `制約:` を、7 日たった安定版の集まりで比べる（決定 5。文字列では比べない）。"""
    if new is None:
        return "制約の消失", []
    newer = [v for v in candidates if pinned is None or v > pinned]
    relaxed = [v for v in newer if v in new and v not in old]
    if relaxed:
        return "緩和", relaxed
    if [v for v in candidates if v in old and v not in new]:
        return "強化", []
    return "同値", []


def partner_rows(hold: Hold, projects: list[dict], fetch: Fetch, today: date, cache: dict) -> list[dict]:
    """種類「相手の制約」の固定を、相手の採用できる最新の版（7 日たった安定版）の requires_dist で判定する。"""
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import InvalidVersion, Version

    partner = canonicalize_name(hold.partner.split()[0])
    adopt = latest_stable(aged(pypi_releases(fetch, partner, cache), today))
    if adopt is None:
        return []
    page = _json_page(fetch, PYPI_VERSION_URL.format(name=partner, version=adopt.version), cache)
    try:
        requires = page["info"]["requires_dist"] or []
        old = Requirement(hold.constraint).specifier
        pinned = Version(hold.version) if hold.version else None
    except (KeyError, TypeError) as e:
        raise Stop("transient", f"PyPI の {partner} {adopt.version} の応答の形が読めない: {e!r}") from e
    except (InvalidRequirement, InvalidVersion) as e:
        raise Stop("config_broken", f"packageRules[{hold.rule}] の制約か版が読めない: {e}") from e
    # 相手を依存に持つプロジェクトの環境と要る extra（どこにも無ければ相手を使わない環境として既定の 1 つ）
    envs = [(p["env"], {"", *r.extras}) for p in projects for r in p["requirements"] if canonicalize_name(r.name) == partner]
    if not envs and projects:
        envs = [(projects[0]["env"], {""})]
    rows = []
    for name in hold.packages:
        applied = [
            r
            for r in _requirements(requires)
            if canonicalize_name(r.name) == canonicalize_name(name)
            and (r.marker is None or any(r.marker.evaluate({**env, "extra": e}) for env, extras in envs for e in extras))
        ]
        new = None
        for r in applied:
            new = r.specifier if new is None else new & r.specifier
        candidates = [r.version for r in aged(pypi_releases(fetch, name, cache), today) if not r.version.is_prerelease]
        judgement, versions = judge_constraint(old, new, candidates, pinned)
        base = {"package": name, "kind": hold.kind, "partner": f"{partner} {adopt.version}", "versions": [str(v) for v in versions]}
        if judgement in ("制約の消失", "緩和"):
            rows.append({"section": "hold", **base, "judgement": judgement})
        elif judgement == "強化":
            rows.append({"section": "tightened", **base, "constraint": str(new)})
    return rows


def hold_rows(holds: list[Hold], projects: list[dict], fetch: Fetch, today: date, cache: dict) -> list[dict]:
    rows = []
    for h in holds:
        if h.kind == "相手の制約":
            rows += partner_rows(h, projects, fetch, today, cache)
        if h.review < today:
            for name in h.packages or (f"packageRules[{h.rule}]",):
                rows.append(
                    {
                        "section": "hold",
                        "package": name,
                        "kind": h.kind,
                        "partner": h.partner,
                        "versions": [],
                        "judgement": "見直す日を過ぎた",
                        "review": h.review.isoformat(),
                    }
                )
    return rows


def prerelease_rows(projects: list[dict], listed: set[str], fetch: Fetch, today: date, cache: dict) -> list[dict]:
    """直接の依存と規則 6 の一覧の依存で、プレリリースを選べるもの・一覧から外してよいものを並べる（決定 4）。"""
    from packaging.utils import canonicalize_name

    names = {canonicalize_name(r.name) for p in projects for r in p["requirements"]} | listed
    rows = []
    for name in sorted(names):
        releases = aged(pypi_releases(fetch, name, cache), today)
        stable = latest_stable(releases)
        stale = stable is None or (today - stable.published).days > STALE_DAYS
        if name in listed:
            if not stale:
                rows.append(
                    {
                        "section": "prerelease",
                        "package": name,
                        "stable": f"{stable.version}（{stable.published}）",
                        "prerelease": "",
                        "judgement": "一覧から外してよい",
                    }
                )
            continue
        newer = [r for r in releases if r.version.is_prerelease and (stable is None or r.version > stable.version)]
        if stale and newer:
            pre = newer[-1]
            rows.append(
                {
                    "section": "prerelease",
                    "package": name,
                    "stable": f"{stable.version}（{stable.published}）" if stable else "無し",
                    "prerelease": f"{pre.version}（{pre.published}）",
                    "judgement": "プレリリースを選べる",
                }
            )
    return rows


def _cell(value) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(header: list[str], rows: list[list]) -> list[str]:
    if not rows:
        return ["無し"]
    return ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"] + [
        "| " + " | ".join(_cell(c) for c in r) + " |" for r in rows
    ]


def report_body(vulns: list[dict], holds: list[dict], pres: list[dict]) -> str:
    lines = [
        REPORT_MARK,
        "週 1 回のサプライチェーンの検査（`.github/workflows/deps-weekly.yml` の `supply-chain-report`。"
        "`scripts/plugin-deps.py report`）が書き直す。3 つの表がどれも 0 行になると閉じる（#3691 の決定 9）。",
        "",
        "## 脆弱性",
        "",
        *_table(
            ["対象", "パッケージ", "版", "勧告", "直る版"],
            [
                [
                    f"`{v['target']}`",
                    v["package"],
                    v["version"],
                    f"[{v['advisory']}]({v['link']})" if v["link"] else v["advisory"],
                    v["fix"],
                ]
                for v in vulns
            ],
        ),
        "",
        "## 解いてよい固定",
        "",
        *_table(
            ["依存", "種類", "相手", "判定"],
            [
                [
                    h["package"],
                    h["kind"],
                    h["partner"],
                    h["judgement"]
                    + (f"（{', '.join(h['versions'])}）" if h["versions"] else "")
                    + (f"（{h['review']}）" if h.get("review") else ""),
                ]
                for h in holds
            ],
        ),
        "",
        "## プレリリースを選べる依存",
        "",
        *_table(
            ["依存", "7 日たった最新の安定版", "プレリリース", "判定"],
            [[p["package"], p["stable"], p["prerelease"], p["judgement"]] for p in pres],
        ),
        "",
    ]
    return "\n".join(lines)


def _gh(gh: Gh, args: list[str]) -> str:
    try:
        return gh(args)
    except (subprocess.CalledProcessError, OSError) as e:
        stderr = getattr(e, "stderr", "") or ""
        raise Stop("transient", f"gh {' '.join(args[:2])} が失敗した: {stderr.strip()[-300:] or e}") from e


def managed_issue(gh: Gh) -> dict | None:
    """本文の 1 行目の目印と作成者で引いた、開いている管理の Issue。決められなければ config_broken（I2）。"""
    try:
        listed = json.loads(_gh(gh, ["issue", "list", "--state", "open", "--limit", "1000", "--json", "number,author,body"]))
        marked = [i for i in listed if (i.get("body") or "").split("\n", 1)[0].strip() == REPORT_MARK]
        others = [i["number"] for i in marked if (i.get("author") or {}).get("login") != REPORT_AUTHOR]
    except (ValueError, TypeError, KeyError) as e:
        raise Stop("transient", f"gh issue list の応答が読めない: {e!r}") from e
    if others or len(marked) > 1:
        numbers = [i["number"] for i in marked]
        raise Stop(
            "config_broken",
            f"目印つきの Issue を 1 件に決められない（{numbers}。作成者が違う: {others}）。余分な目印を外す",
            [{"section": "issue", "number": n} for n in numbers],
        )
    return marked[0] if marked else None


def _with_body(gh: Gh, args: list[str], body: str) -> str:
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "body.md"
        path.write_text(body, encoding="utf-8")
        return _gh(gh, [*args, "--body-file", str(path)])


def report(root: Path, fetch: Fetch, gh: Gh, run: Run, today: date) -> Result:
    """すべての対象の脆弱性・解いてよい固定・プレリリースを選べる依存を集め、管理の Issue を作る・書き直す・閉じる。"""
    try:
        holds = read_holds(load_config(root))
    except ValueError as e:
        raise Stop("config_broken", str(e)) from e
    found = targets(root)
    projects = uv_projects(root, found)
    # 取得（ここまでに 1 つでも失敗したら Stop で、Issue に触れない。I11）
    vulns = []
    for t in found:
        vulns += uv_audit(root, t, run) if t.kind == "uv" else npm_audit(root, t, run)
    cache: dict = {}
    held = hold_rows(holds, projects, fetch, today, cache)
    listed = {p for h in holds if h.kind == "プレリリースを選ぶ" for p in h.packages}
    pres = prerelease_rows(projects, listed, fetch, today, cache)
    issue = managed_issue(gh)
    # 書き込み
    shown = [h for h in held if h["section"] == "hold"]
    body = report_body(vulns, shown, pres)
    items = vulns + held + pres
    counts = f"脆弱性 {len(vulns)} 件・解いてよい固定 {len(shown)} 件・プレリリースを選べる依存 {len(pres)} 件"
    if not (vulns or shown or pres):
        if issue is None:
            return Result("ok", f"{counts}。知らせの Issue は無く、作らない", items)
        if issue.get("body") != body:
            _with_body(gh, ["issue", "edit", str(issue["number"])], body)
        _gh(gh, ["issue", "close", str(issue["number"]), "--comment", "3 つの表がどれも 0 行になったので閉じる"])
        return Result(
            "ok",
            f"{counts}。Issue #{issue['number']} を閉じた",
            items + [{"section": "issue", "number": issue["number"], "action": "closed"}],
        )
    if issue is None:
        _gh(
            gh,
            [
                "label",
                "create",
                REPORT_LABEL,
                "--force",
                "--color",
                "b60205",
                "--description",
                "脆弱性と解いてよい固定の知らせ（scripts/plugin-deps.py report）",
            ],
        )
        url = _with_body(gh, ["issue", "create", "--title", REPORT_TITLE, "--label", REPORT_LABEL], body).strip().splitlines()[-1]
        number = int(url.rstrip("/").rsplit("/", 1)[-1])
        return Result("ok", f"{counts}。Issue #{number} を作った", items + [{"section": "issue", "number": number, "action": "created"}])
    if issue.get("body") == body:
        return Result(
            "ok",
            f"{counts}。Issue #{issue['number']} は同じ本文で、書き直さない",
            items + [{"section": "issue", "number": issue["number"], "action": "unchanged"}],
        )
    _with_body(gh, ["issue", "edit", str(issue["number"]), "--title", REPORT_TITLE], body)
    return Result(
        "ok",
        f"{counts}。Issue #{issue['number']} を書き直した",
        items + [{"section": "issue", "number": issue["number"], "action": "edited"}],
    )


def cmd_report(args: argparse.Namespace, deps: Deps) -> Result:
    return report(args.root, deps.fetch, deps.gh, deps.run, date.today())


# --- Actions の SHA の固定（決定 13・不変条件 I10） ---

WORKFLOWS_DIR = ".github/workflows"
# ワークフローの `uses:` の行（`- uses:` と `uses:` の両方）。値と行の残り
USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)(.*)$")
# 外部の Action の参照の形: <所有者>/<名前>[/<パス>]@<40 桁の SHA> # v<版>
PIN_RE = re.compile(r"([\w.-]+/[\w.-]+)(?:/[^@\s]*)?@([0-9a-f]{40})")
NOTE_RE = re.compile(r"\s+#\s+(v[0-9]+\.[0-9]+\.[0-9]+)\s*")
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def added_uses(root: Path, base: str, head: str) -> list[tuple[str, int, str, str]]:
    """`base` と `head` の差分で足した外部の Action の `uses:` の行を (パス, 行番号, 値, 行の残り) で返す。"""
    p = subprocess.run(
        ["git", "-C", str(root), "diff", "--no-color", "--no-ext-diff", "--unified=0", base, head, "--", WORKFLOWS_DIR],
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        raise Stop("config_broken", f"git diff {base} {head} が失敗した: {p.stderr.strip()}")
    out = []
    path, line = None, 0
    for row in p.stdout.splitlines():
        if row.startswith("+++ "):
            path = row[6:] if row.startswith("+++ b/") else None
        elif m := HUNK_RE.match(row):
            line = int(m.group(1))
        elif row.startswith("+"):
            u = USES_RE.match(row[1:])
            if path and u and not u.group(1).startswith(("./", "docker://")):
                out.append((path, line, u.group(1), u.group(2)))
            line += 1
    return out


def tag_commit(gh: Gh, action: str, tag: str) -> str:
    """タグが指すコミットの SHA。注記つきのタグ（`type: tag`）は `git/tags` で指す先まで辿る。"""
    try:
        obj = json.loads(gh(["api", f"repos/{action}/git/ref/tags/{tag}"]))["object"]
        while obj["type"] == "tag":
            obj = json.loads(gh(["api", f"repos/{action}/git/tags/{obj['sha']}"]))["object"]
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError, TypeError) as e:
        detail = getattr(e, "stderr", None) or e
        raise Stop("transient", f"{action} のタグ {tag} を引けない: {str(detail).strip()}") from e
    if obj["type"] != "commit":
        raise Stop("check_failed", f"{action} のタグ {tag} はコミットを指さない（{obj['type']}）")
    return obj["sha"]


def action_pins(root: Path, base: str, head: str, gh: Gh) -> Result:
    """差分で足した `uses:` の各行で、注記のタグが指すコミットが行の SHA と同じかを確かめる（不変条件 I10）。"""
    lines = added_uses(root, base, head)
    if not lines:
        return Result("skipped", "差分に外部の Action の uses: の行が無い")
    bad_form = []
    pins = []
    for path, n, value, rest in lines:
        m, note = PIN_RE.fullmatch(value), NOTE_RE.fullmatch(rest)
        if not m or not note:
            bad_form.append({"path": path, "line": n, "uses": (value + rest).strip()})
            continue
        pins.append({"path": path, "line": n, "action": m.group(1), "tag": note.group(1), "sha": m.group(2)})
    if bad_form:
        raise Stop("check_failed", f"40 桁の SHA と版の注記（@<SHA> # v<版>）の形でない参照が {len(bad_form)} 行ある", bad_form)
    resolved: dict[tuple[str, str], str] = {}
    mismatched = []
    for pin in pins:
        key = (pin["action"], pin["tag"])
        if key not in resolved:
            resolved[key] = tag_commit(gh, *key)
        if resolved[key] != pin["sha"]:
            mismatched.append({**pin, "expected": resolved[key]})
    if mismatched:
        raise Stop("check_failed", f"注記のタグが指すコミットと違う SHA が {len(mismatched)} 行ある", mismatched)
    return Result("ok", f"変わった {len(pins)} 行の SHA は注記のタグが指すコミットと同じ", pins)


# --- 公開から 7 日の検査（age。#3370 の受け入れ条件 6・不変条件 I1・決定 2・6・9） ---

# 待つ日数は report と同じ WAIT_DAYS（定数。PR の木から読まないのは、宛先の検証器で PR の Renovate の版を
# 確かめるとき、PR が日数を変えて通れないようにするため）
RENOVATE_VERSION_FILE = ".github/renovate-version"
RENOVATE_VERSION_FORMAT = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
PYPI_REGISTRY = "https://pypi.org/simple"
NPM_REGISTRY = "https://registry.npmjs.org/"
NPM_URL = "https://registry.npmjs.org/{name}"
# uv.lock の取得元のうち、手元のコード（公開日を持たない・Renovate が上げない）として見ないもの
UV_LOCAL_SOURCES = ("editable", "virtual", "directory", "path")


@dataclass(frozen=True)
class NewVersion:
    registry: str  # pypi・npm。公開日を持つ取得元の外（git・URL など）は ""
    name: str
    version: str
    where: str  # 入った場所（lock・UV_VERSION のファイル・.github/renovate-version・--renovate-version）

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.registry, self.name, self.version)


def git_show(root: Path, rev: str, path: str) -> str | None:
    """`rev` の `path` の本文。そのコミットに無ければ None。"""
    p = git_out(root, "show", f"{rev}:{path}")
    return p.stdout if p.returncode == 0 else None


def verify_rev(root: Path, rev: str) -> None:
    p = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], capture_output=True, text=True)
    if p.returncode != 0:
        raise Stop("invalid", f"{rev} はコミットとして読めない")


def uv_lock_releases(text: str, where: str) -> list[NewVersion]:
    try:
        import tomllib  # 3.11 以上。lock を読むときだけ要る（--renovate-version だけなら 3.10 でも動く）
    except ModuleNotFoundError as e:
        raise Stop("precondition", f"{where} を読むには Python 3.11 以上が要る") from e
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise Stop("invalid", f"{where} が TOML として読めない: {e}") from e
    out = []
    for pkg in data.get("package", []):
        source = pkg.get("source", {})
        if any(k in source for k in UV_LOCAL_SOURCES):
            continue
        registry = "pypi" if source.get("registry") == PYPI_REGISTRY else ""
        out.append(NewVersion(registry, str(pkg.get("name", "")), str(pkg.get("version", "")), where))
    return out


def npm_lock_releases(text: str, where: str) -> list[NewVersion]:
    try:
        packages = json.loads(text).get("packages") or {}
    except (json.JSONDecodeError, AttributeError) as e:
        raise Stop("invalid", f"{where} が lock の JSON として読めない: {e}") from e
    out = []
    for key, pkg in packages.items():
        if not key or pkg.get("link"):  # 根の項目と、手元のディレクトリへの link
            continue
        name = pkg.get("name") or key.rsplit("node_modules/", 1)[-1]
        registry = "npm" if str(pkg.get("resolved", "")).startswith(NPM_REGISTRY) else ""
        out.append(NewVersion(registry, name, str(pkg.get("version", "")), where))
    return out


def renovate_release(version: str, where: str) -> NewVersion:
    if not RENOVATE_VERSION_FORMAT.fullmatch(version):
        raise Stop("invalid", f"{where} の Renovate の版「{version}」が ^[0-9]+\\.[0-9]+\\.[0-9]+$ の形でない")
    return NewVersion("npm", "renovate", version, where)


def changed_releases(root: Path, base: str, head: str) -> list[NewVersion]:
    """`base` から `head` で新しく入った版。lock は Renovate の対象（`targets`。I9）だけを見る。"""
    for rev in (base, head):
        verify_rev(root, rev)
    out: list[NewVersion] = []
    readers = {"uv": uv_lock_releases, "npm": npm_lock_releases}
    for t in targets(root):
        old, new = git_show(root, base, t.lock), git_show(root, head, t.lock)
        if new is None or old == new:
            continue
        read = readers[t.kind]
        before = {r.key for r in read(old, f"{base}:{t.lock}")} if old is not None else set()
        out += [r for r in read(new, t.lock) if r.key not in before]
    for rel in UV_PIN_FILES:
        old, new = git_show(root, base, rel), git_show(root, head, rel)
        if new is None or old == new:
            continue
        before = {m[1] for m in UV_VERSION_RE.findall(old or "")}
        # Renovate は GitHub の releases から拾うが、公開日は同じ版の PyPI の uv で読む（未認証の API の上限を避ける）
        out += [NewVersion("pypi", "uv", m[1], rel) for m in UV_VERSION_RE.findall(new) if m[1] not in before]
    old, new = git_show(root, base, RENOVATE_VERSION_FILE), git_show(root, head, RENOVATE_VERSION_FILE)
    if new is not None and new.strip() != (old or "").strip():
        out.append(renovate_release(new.strip(), RENOVATE_VERSION_FILE))
    return out


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def published_at(r: NewVersion, fetch: Fetch, cache: dict[str, bytes]) -> datetime:
    """その版の最初の公開の時刻。読めなければ ValueError（理由つき）。"""
    if r.registry == "pypi":
        url = PYPI_VERSION_URL.format(name=urllib.parse.quote(r.name, safe=""), version=urllib.parse.quote(r.version, safe=""))
    elif r.registry == "npm":
        url = NPM_URL.format(name=urllib.parse.quote(r.name, safe="@"))
    else:
        raise ValueError("公開日を持つ取得元（PyPI・npm のレジストリ）の外にある")
    if url not in cache:
        try:
            cache[url] = fetch(url)
        except FetchError as e:
            raise ValueError(f"取得できない: {e}") from e
    try:
        doc = json.loads(cache[url])
        if r.registry == "pypi":
            times = [parse_time(u["upload_time_iso_8601"]) for u in doc.get("urls", []) if u.get("upload_time_iso_8601")]
        else:
            stamp = (doc.get("time") or {}).get(r.version)
            times = [parse_time(stamp)] if stamp else []
    except (json.JSONDecodeError, AttributeError, TypeError, ValueError) as e:
        raise ValueError(f"応答から公開日を読めない: {e}") from e
    if not times:
        raise ValueError("その版の公開日が応答に無い")
    return min(times)


def normalized(registry: str, name: str) -> str:
    return norm(name) if registry == "pypi" else name


def vulnerability_exceptions(root: Path) -> set[tuple[str, str]]:
    """規則 12（種類 脆弱性の例外）の制約 `<パッケージ>==<版>` の組（決定 9）。名前は PyPI の正規の形でも持つ。"""
    try:
        holds = read_holds(load_config(root))
    except ValueError as e:
        raise Stop("config_broken", str(e)) from e
    out = set()
    for h in holds:
        name, sep, version = h.constraint.partition("==")
        if h.kind == "脆弱性の例外" and sep:
            out |= {(name.strip(), version.strip()), (normalized("pypi", name.strip()), version.strip())}
    return out


def age(root: Path, base: str | None, head: str | None, fetch: Fetch, today: datetime, renovate_version: str | None = None) -> Result:
    """差分で入った版と `renovate_version` の公開日を読み、7 日に満たないか公開日が読めないものを拒む（I1）。

    脆弱性の例外の規則（規則 12）と同じパッケージと版の組だけを 7 日の外として通す。結果の `items` の `verdict` は
    `ok`・`exception`・`too_new`・`no_date` のどれか。
    """
    if (base is None) != (head is None):
        raise Stop("invalid", "--base と --head は 2 つそろえて渡す")
    if base is None and renovate_version is None:
        raise Stop("invalid", "--base と --head か、--renovate-version を渡す")
    found: dict[tuple[str, str, str], NewVersion] = {}
    if renovate_version is not None:
        r = renovate_release(renovate_version, "--renovate-version")
        found[r.key] = r
    if base is not None and head is not None:
        for r in changed_releases(root, base, head):
            found.setdefault(r.key, r)
    if not found:
        return Result("skipped", "差分で入った版は無い（取得しない）")
    cache: dict[str, bytes] = {}
    items = []
    for r in found.values():
        item = {"registry": r.registry, "name": r.name, "version": r.version, "where": r.where, "published": None}
        try:
            published = published_at(r, fetch, cache)
        except ValueError as e:
            item.update(verdict="no_date", reason=str(e))
        else:
            item["published"] = published.isoformat().replace("+00:00", "Z")
            item["verdict"] = "ok" if today - published >= timedelta(days=WAIT_DAYS) else "too_new"
        items.append(item)
    if any(i["verdict"] != "ok" for i in items):
        allowed = vulnerability_exceptions(root)
        for i in items:
            if i["verdict"] != "ok" and (normalized(i["registry"], i["name"]), i["version"]) in allowed:
                i["verdict"] = "exception"
    failed = [i for i in items if i["verdict"] in ("too_new", "no_date")]
    if failed:
        names = "、".join(f"{i['name']} {i['version']}（{i['where']}）" for i in failed[:10])
        raise Stop("check_failed", f"公開から {WAIT_DAYS} 日に満たないか公開日が読めない版が {len(failed)} 個ある: {names}", items)
    excepted = sum(1 for i in items if i["verdict"] == "exception")
    return Result("ok", f"差分で入った {len(items)} 個の版は公開から {WAIT_DAYS} 日たっている（脆弱性の例外 {excepted} 個）", items)


# --- --help の照らし合わせ ---

CLI_FLOORS = "plugins/ndf/contracts/domains/cli-floors.json"
CONTRACTS = "plugins/ndf/contracts"
# 見本の `table` が指してよい表（contracts の data の下の JSON だけ。任意の ref の表から外のパスを読まない）
HELP_TABLE_RE = re.compile(r"data/[a-z0-9][a-z0-9-]*\.json")
# 見本の `table` の表から照らす欄（claude-cli.json の引数とサブコマンドの名前）
HELP_TABLE_KEYS = ("value", "optional", "variadic", "flags", "subcommands")
# docker 自身が失敗したときの終了コード（イメージが無い・起動できない）
DOCKER_FAILED = 125


@dataclass(frozen=True)
class HelpSample:
    args: tuple[str, ...]  # `--help` を打つ引数（実行ファイルの後ろ）
    flags: tuple[str, ...]  # その出力にあるべき引数とサブコマンドの名前


def _read_json(path: Path, rel: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise Stop("config_broken", f"{rel} が無い") from e
    except json.JSONDecodeError as e:
        raise Stop("config_broken", f"{rel} が JSON として読めない: {e}") from e


def _str_list(value, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise Stop("config_broken", f"{where} が空でない文字列の配列でない")
    return tuple(value)


def _table_flags(root: Path, ref, where: str) -> tuple[str, ...]:
    if not isinstance(ref, str) or not HELP_TABLE_RE.fullmatch(ref):
        raise Stop("config_broken", f"{where} の table が data/<名前>.json の形でない: {ref!r}")
    rel = f"{CONTRACTS}/{ref}"
    data = _read_json(root / rel, rel)
    if not isinstance(data, dict):
        raise Stop("config_broken", f"{rel} がオブジェクトでない")
    return tuple(f for key in HELP_TABLE_KEYS for f in _str_list(data.get(key), f"{rel} の {key}"))


def help_samples(root: Path, runtime: str) -> tuple[str, list[HelpSample]]:
    """`cli-floors.json` の行 `runtime` の実行ファイルの名前と見本。表の形が違えば `config_broken` で止める。"""
    table = _read_json(root / CLI_FLOORS, CLI_FLOORS)
    rows = table.get("rows") if isinstance(table, dict) else None
    if not isinstance(rows, list):
        raise Stop("config_broken", f"{CLI_FLOORS} に rows の配列が無い")
    row = next((r for r in rows if isinstance(r, dict) and r.get("id") == runtime), None)
    if row is None:
        raise Stop("config_broken", f"{CLI_FLOORS} に行 {runtime} が無い")
    executable = row.get("executable")
    if not isinstance(executable, str) or not executable:
        raise Stop("config_broken", f"{CLI_FLOORS} の {runtime} の executable が文字列でない")
    raw = row.get("help")
    if not isinstance(raw, list) or not raw:
        raise Stop("config_broken", f"{CLI_FLOORS} の {runtime} の help が空でない配列でない")
    samples = []
    for i, s in enumerate(raw):
        where = f"{CLI_FLOORS} の {runtime} の help[{i}]"
        if not isinstance(s, dict) or set(s) not in ({"args", "flags"}, {"args", "table"}):
            raise Stop("config_broken", f"{where} が args と flags（か table）の組でない")
        flags = _table_flags(root, s["table"], where) if "table" in s else _str_list(s["flags"], f"{where} の flags")
        samples.append(HelpSample(_str_list(s["args"], f"{where} の args"), flags))
    return executable, samples


def help_script(executable: str, samples: list[HelpSample], token: str) -> str:
    """見本ごとに「境界の行 → `<executable> <args>` の標準出力と標準エラー → 境界の行と終了コード」を出す sh の本文。"""
    lines = ['mkdir -p "$HOME" 2>/dev/null']
    for i, s in enumerate(samples):
        lines.append(f"printf '%s\\n' {shlex.quote(f'{token} {i} begin')}")
        lines.append(" ".join(shlex.quote(a) for a in (executable, *s.args)) + " 2>&1 </dev/null")
        lines.append(f"printf '%s %s\\n' {shlex.quote(f'{token} {i} end')} \"$?\"")
    return "\n".join(lines) + "\n"


def split_help(out: str, count: int, token: str) -> list[tuple[str, int]]:
    """`help_script` の出力を見本ごとの (出力, 終了コード) に分ける。区切りが欠ければ `check_failed` で止める。"""
    parts = []
    pos = 0
    for i in range(count):
        # 出力が改行で終わらなくても分けられるよう、行の頭に縛らない（境界は実行ごとの 32 桁の乱数を持つ）
        begin = re.compile(rf"{token} {i} begin\n").search(out, pos)
        end = re.compile(rf"{token} {i} end (\d+)").search(out, begin.end()) if begin else None
        if begin is None or end is None:
            raise Stop("check_failed", f"出力を見本ごとに分けられない（見本 {i} の境界の行が無い）")
        parts.append((out[begin.end() : end.start()], int(end.group(1))))
        pos = end.end()
    return parts


def _run_help(run: Run, argv: list[str]) -> "subprocess.CompletedProcess[str]":
    try:
        return run(argv)
    except OSError as e:
        raise Stop("check_failed", f"{argv[0]} を起動できない: {e}") from e


def _help_outputs(run: Run, executable: str, samples: list[HelpSample], image: str | None) -> list[tuple[str, int]]:
    if image is None:
        done = [_run_help(run, [executable, *s.args]) for s in samples]
        return [((p.stdout or "") + (p.stderr or ""), p.returncode) for p in done]
    token = secrets.token_hex(16)
    # 手元に無いイメージを取りに行かない（--pull never。ビルドで落ちたら docker 自身の失敗になる）
    p = _run_help(
        run, ["docker", "run", "--rm", "--pull", "never", "--entrypoint", "sh", image, "-c", help_script(executable, samples, token)]
    )
    if p.returncode == DOCKER_FAILED and token not in (p.stdout or ""):
        raise Stop(
            "check_failed", f"最新の CLI のイメージが無い（{image}）", [{"image": image, "stderr": (p.stderr or "").strip()[-2000:]}]
        )
    return split_help(p.stdout or "", len(samples), token)


def has_word(text: str, word: str) -> bool:
    """`word` が前後を英数字・`_`・`-` に挟まれずに `text` にあるか（`-C` と `--cd`、`exec` と `exec-server` を分ける）。"""
    return re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", text) is not None


def _help_gap(item: dict) -> str:
    return "・".join(item["missing"]) or f"終了コード {item['exit_code']}"


def cli_help(root: Path, run: Run, runtime: str, image: str | None = None) -> Result:
    """`root` の `cli-floors.json` の見本の引数が、CLI の対応する `--help` の出力にあるかを照らす（決定 11）。

    `image` があればそのイメージの中の CLI を 1 回の `docker run` で全部の見本について打ち、無ければホストの CLI を
    見本ごとに打つ。`root` からは表の JSON を読むだけでコードを読まない。
    """
    executable, samples = help_samples(root, runtime)
    outputs = _help_outputs(run, executable, samples, image)
    items, bad = [], []
    for s, (out, code) in zip(samples, outputs):
        missing = [f for f in s.flags if not has_word(out, f)]
        item = {"cli": runtime, "args": list(s.args), "exit_code": code, "missing": missing}
        items.append(item)
        if missing or code != 0:
            bad.append(item)
    if bad:
        where = "、".join(f"{executable} {' '.join(b['args'])}（{_help_gap(b)}）" for b in bad)
        raise Stop("check_failed", f"{runtime} の --help に見本の引数が無い: {where}", bad)
    where = f"イメージ {image}" if image else "ホスト"
    return Result("ok", f"{runtime} の見本 {len(samples)} 個の引数がすべて {where} の --help にある", items)


# --- 依存の PR の関門（renovate_gate。#3370 の受け入れ条件 9・不変条件 I4・決定 8） ---

ARTIFACTS_CONTEXT = "renovate/artifacts"
GATE_CONTEXT = "ndf/renovate-gate"
GATE_DESCRIPTION_LIMIT = 140  # commit status の description の上限
BOT_RE = re.compile(r"[a-z0-9][a-z0-9-]*\[bot\]")


def _gate_stop(summary: str, state: str, item: dict) -> Stop:
    return Stop("check_failed", summary, [{**item, "state": state}])


def renovate_gate(pr: int, head: str, bot: str, gh: Gh) -> Result:
    """Renovate の PR の今の head で、最も新しい `renovate/artifacts` が `bot` の書いた `success` かを確かめる（I4）。

    `gh api` の `{owner}/{repo}` は `GH_REPO`（ワークフローが渡す）から埋まる。結果の `items[0].state` は付ける
    commit status（`success`・`pending`・`failure`）で、`--head` が PR の今の head と違えば `skipped`（何も付けない）。
    """
    if not BOT_RE.fullmatch(bot or ""):
        raise Stop("config_broken", f"Renovate の App の bot の名前（<slug>[bot]）が無い: {bot!r}。Actions の変数 RENOVATE_APP_SLUG を置く")
    try:
        current = json.loads(_gh(gh, ["api", f"repos/{{owner}}/{{repo}}/pulls/{pr}"]))["head"]["sha"]
    except (ValueError, TypeError, KeyError) as e:
        raise Stop("transient", f"PR #{pr} の head を読めない: {e!r}") from e
    item = {"pr": pr, "head": head}
    if current != head:
        return Result("skipped", f"PR #{pr} の今の head は {current}（古い出来事なので付けない）", [{**item, "current": current}])
    out = _gh(gh, ["api", "--paginate", "--jq", ".[]", f"repos/{{owner}}/{{repo}}/commits/{head}/statuses?per_page=100"])
    try:
        rows = [json.loads(line) for line in out.splitlines() if line.strip()]
        ours = [r for r in rows if r["context"] == ARTIFACTS_CONTEXT]
    except (ValueError, TypeError, KeyError) as e:
        raise Stop("transient", f"commit status の一覧が読めない: {e!r}") from e
    if not ours:
        raise _gate_stop(f"{ARTIFACTS_CONTEXT} が未報告", "pending", item)
    # API は新しい順に返す。id は書いた順に増えるので、ページをまたいでも id の大きい行を最新とする
    latest = max(ours, key=lambda r: r.get("id") or 0)
    creator = (latest.get("creator") or {}).get("login")
    item.update(artifacts=latest.get("state"), creator=creator)
    if creator != bot:
        raise _gate_stop(f"Renovate の App 以外が書いた状態（{creator}）", "failure", item)
    if latest.get("state") == "success":
        return Result("ok", f"{ARTIFACTS_CONTEXT} は {bot} の書いた success", [{**item, "state": "success"}])
    if latest.get("state") == "pending":
        raise _gate_stop(f"{ARTIFACTS_CONTEXT} が保留", "pending", item)
    raise _gate_stop(f"{ARTIFACTS_CONTEXT} が {latest.get('state')}（解決できない上げ方か uv-pin の失敗）", "failure", item)


def post_gate(result: Result, gh: Gh) -> None:
    """関門の結果を PR の head へ commit status `ndf/renovate-gate` として付ける。`skipped` では付けない。"""
    if result.status == "skipped":
        return
    item = result.items[0] if result.items else {}
    state = item.get("state") or "failure"
    head = item.get("head")
    if not head:
        raise Stop("invalid", "付ける先の head が結果に無い")
    _gh(
        gh,
        [
            "api",
            "-X",
            "POST",
            f"repos/{{owner}}/{{repo}}/statuses/{head}",
            "-f",
            f"state={state}",
            "-f",
            f"context={GATE_CONTEXT}",
            "-f",
            f"description={result.summary[:GATE_DESCRIPTION_LIMIT]}",
        ],
    )


# --- PR の無い失敗の知らせ（renovate_log。#3370 の受け入れ条件 7） ---

REASON_LIMIT = 300  # 1 件の理由の文字数（記録の爆発を防ぐ）
LOG_SAMPLE = 20  # 要約に並べる件数


def _log_records(log: Path) -> list[dict]:
    try:
        text = log.read_text(encoding="utf-8")
    except OSError as e:
        raise Stop("invalid", f"Renovate の記録を読めない: {log}: {e}") from e
    records = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError as e:
            raise Stop("invalid", f"Renovate の記録の {n} 行目が JSON でない: {e}") from e
        if isinstance(rec, dict):
            records.append(rec)
    return records


def _last(records: list[dict], msg: str) -> dict | None:
    found = [r for r in records if r.get("msg") == msg]
    return found[-1] if found else None


def _reasons(records: list[dict]) -> dict[str, list[str]]:
    """ブランチ → artifactErrors の理由（lock の作り直しと postUpgradeTasks の失敗）。"""
    out: dict[str, list[str]] = {}
    for r in records:
        if r.get("msg") != "artifactErrors" or not r.get("branch"):
            continue
        for e in r.get("artifactErrors") or []:
            where = e.get("lockFile") or e.get("fileName") or ""
            text = " ".join(str(e.get("stderr") or "").split())[:REASON_LIMIT]
            out.setdefault(r["branch"], []).append(f"{where}: {text}".strip(": "))
    return out


def renovate_log(log: Path) -> Result:
    """Renovate の JSON の記録から、PR もブランチも作られなかった失敗の更新を並べる。

    `branches info extended` の行で結果が `error` かつ PR の番号が無く、`Branch summary` の行でブランチが無いもの
    （lock の作り直し・postUpgradeTasks・解決の失敗）と、`Repository finished` の結果が `lockfile-error`（2 時間より
    新しい更新を PR にせずに止めた）のときの、その理由を持つブランチを拾う。1 件でもあれば `check_failed`。
    """
    records = _log_records(log)
    finished = _last(records, "Repository finished")
    if finished is None:
        raise Stop("invalid", "記録に Repository finished の行が無い（Renovate が終わりまで走っていない）")
    reasons = _reasons(records)
    summary = _last(records, "Branch summary") or {}
    existing = {b.get("branchName") for b in summary.get("branches") or [] if b.get("sha")}
    info = _last(records, "branches info extended") or {}
    failed = []
    for b in info.get("branchesInformation") or []:
        name = b.get("branchName")
        if b.get("result") != "error" or b.get("prNo") or name in existing:
            continue
        upgrades = [f"{u.get('depName')} {u.get('newVersion') or u.get('newValue') or ''}".strip() for u in b.get("upgrades") or []]
        failed.append({"branch": name, "result": "error", "upgrades": upgrades, "reasons": reasons.get(name, [])})
    if finished.get("result") == "lockfile-error":
        seen = {f["branch"] for f in failed}
        for name, why in reasons.items():
            if name not in seen and name not in existing:
                failed.append({"branch": name, "result": "lockfile-error", "upgrades": [], "reasons": why})
        if not reasons:
            failed.append({"branch": None, "result": "lockfile-error", "upgrades": [], "reasons": []})
    if failed:
        names = "、".join(str(f["branch"]) for f in failed[:LOG_SAMPLE])
        raise Stop("check_failed", f"PR もブランチも作られなかった失敗の更新が {len(failed)} 件ある: {names}", failed)
    return Result("ok", f"PR もブランチも作られなかった失敗の更新は無い（Renovate の結果 {finished.get('result')}）")


def log_summary(result: Result) -> str:
    """ジョブの要約（GITHUB_STEP_SUMMARY）へ書く Markdown。"""
    lines = ["## Renovate の PR の無い失敗", "", result.summary, ""]
    if result.items:
        lines += ["| ブランチ | 結果 | 更新 | 理由 |", "| --- | --- | --- | --- |"]
        for f in result.items[:LOG_SAMPLE]:
            cells = [
                f.get("branch") or "（不明）",
                f.get("result", ""),
                "、".join(f.get("upgrades") or []),
                " / ".join(f.get("reasons") or []),
            ]
            lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    return "\n".join(lines) + "\n"


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


def cmd_action_pins(args: argparse.Namespace, deps: Deps) -> Result:
    return action_pins(args.root, args.base, args.head, deps.gh)


def add_action_pins(p: argparse.ArgumentParser) -> None:
    add_root(p)
    p.add_argument("--base", required=True, help="比べる前のコミット（PR では HEAD^1）")
    p.add_argument("--head", required=True, help="比べる後のコミット（PR では HEAD）")


def cmd_age(args: argparse.Namespace, deps: Deps) -> Result:
    return age(args.root, args.base, args.head, deps.fetch, datetime.now(timezone.utc), args.renovate_version)


def cmd_cli_help(args: argparse.Namespace, deps: Deps) -> Result:
    return cli_help(args.root, deps.run, args.runtime, args.image)


def add_cli_help(p: argparse.ArgumentParser) -> None:
    add_root(p)
    p.add_argument("--runtime", required=True, help="cli-floors.json の行（gh・claude・codex）")
    p.add_argument("--image", help="CLI が入ったイメージ（省けばホストの CLI を打つ）")


def cmd_renovate_gate(args: argparse.Namespace, deps: Deps) -> Result:
    try:
        result = renovate_gate(args.pr, args.head, args.bot, deps.gh)
    except Stop as e:
        result = Result("stopped", e.summary, e.items or [{"pr": args.pr, "head": args.head, "state": "failure"}], e.cause)
    if args.post:
        post_gate(result, deps.gh)
    if result.status == "stopped":
        raise Stop(result.cause or "check_failed", result.summary, result.items)
    return result


def add_renovate_gate(p: argparse.ArgumentParser) -> None:
    p.add_argument("--pr", type=int, required=True, help="Renovate の PR の番号")
    p.add_argument("--head", required=True, help="出来事が指す PR の head の SHA")
    p.add_argument("--bot", required=True, help="Renovate の App の bot（<App の slug>[bot]）")
    p.add_argument("--post", action="store_true", help="結果を commit status ndf/renovate-gate として付ける")


def cmd_renovate_log(args: argparse.Namespace, deps: Deps) -> Result:
    try:
        result = renovate_log(args.log)
    except Stop as e:
        result = Result("stopped", e.summary, e.items, e.cause)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as out:
            out.write(log_summary(result))
    if result.status == "stopped":
        raise Stop(result.cause or "check_failed", result.summary, result.items)
    return result


def add_renovate_log(p: argparse.ArgumentParser) -> None:
    p.add_argument("--log", type=Path, required=True, help="Renovate の JSON の記録（LOG_FILE）")
    p.add_argument("--summary", type=Path, help="要約を書き足す Markdown（ワークフローでは GITHUB_STEP_SUMMARY）")


def add_root(p: argparse.ArgumentParser) -> None:
    p.add_argument("--root", type=Path, default=REPO, help="リポジトリの根（既定はこのスクリプトのリポジトリ）")


def add_age(p: argparse.ArgumentParser) -> None:
    add_root(p)
    p.add_argument("--base", help="比べる前のコミット（PR では宛先の先頭 HEAD^1）")
    p.add_argument("--head", help="比べる後のコミット（PR ではマージのコミット HEAD）")
    p.add_argument("--renovate-version", help="差分によらず確かめる Renovate の版（renovate-config のジョブと renovate.yml が渡す）")


# 名前 → (引数の組み立て, 処理)。後の単位はここへ行を足す
COMMANDS: dict[str, tuple[Callable[[argparse.ArgumentParser], None], Callable[[argparse.Namespace, Deps], Result]]] = {
    "targets": (add_root, cmd_targets),
    "holds": (add_root, cmd_holds),
    "uv-pin": (add_root, cmd_uv_pin),
    "affected": (add_affected, cmd_affected),
    "post-affected": (add_post_affected, cmd_post_affected),
    "report": (add_root, cmd_report),
    "action-pins": (add_action_pins, cmd_action_pins),
    "age": (add_age, cmd_age),
    "cli-help": (add_cli_help, cmd_cli_help),
    "renovate-gate": (add_renovate_gate, cmd_renovate_gate),
    "renovate-log": (add_renovate_log, cmd_renovate_log),
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
