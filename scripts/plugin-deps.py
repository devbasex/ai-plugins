#!/usr/bin/env python3
"""依存の検査のサブコマンド（#3370 #3691。詳細設計は issues/issue-3370-3691-design-detail.md の「依存の検査のスクリプト」）。

    python3 scripts/plugin-deps.py targets [--root <リポジトリ>]
    python3 scripts/plugin-deps.py holds   [--root <リポジトリ>]
    python3 scripts/plugin-deps.py uv-pin  [--root <リポジトリ>]
    python3 scripts/plugin-deps.py age     [--root <リポジトリ>] [--base <rev> --head <rev>] [--renovate-version <版>]

- `targets`: 追跡している lock（`uv.lock`・`package-lock.json`）から `.github/renovate.json` の `ignorePaths` を
  除き、種類（uv・npm）とディレクトリの組を並べる（不変条件 I9）
- `holds`: `.github/renovate.json` の固定と対象の外の規則から、種類・相手・制約・見直す日・理由の 5 つの欄を読む
  （決定 5・不変条件 I3）
- `uv-pin`: `UV_VERSION` のインストーラを 1 回だけ取得して sha256 を計算し、`plugins/ndf/bin/ndf` と
  `scripts/lib/ndf_wrappers.py` の `UV_INSTALLER_SHA256` を同じ値に書き直す（決定 12。Renovate の規則 10 が打つ）
- `age`: `--base` と `--head` の差分で入った版（lock の各パッケージ・`UV_VERSION`・Renovate の版）と `--renovate-version` の版の
  公開日を読み、公開から 7 日に満たないか公開日が読めないものを拒む。脆弱性の例外の規則（規則 12）の組だけを通す
  （#3370 の受け入れ条件 6・不変条件 I1・決定 2・6・9）

結果は統一の結果 JSON の 1 行（`tool`・`status`・`summary`・`items`、止まったときは `cause`）を標準出力へ出す。
終了コードは `status` が `ok` か `skipped` なら 0、`stopped` なら 1。

サブコマンドは `COMMANDS` に名前と引数の組み立てと処理の組で登録する。外部（HTTP の取得・`gh`・子プロセス）は
`Fetch`・`Gh`・`Run` の引数で渡し、`main` は `Deps` の既定（このモジュールの `fetch_url`・`gh_cli`・`run_cmd`）を
処理へ渡す。単体テストは記録した応答を返す関数へ差し替える。標準ライブラリだけを使う（根の環境の外でも動く）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
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


# --- 公開から 7 日の検査（age。#3370 の受け入れ条件 6・不変条件 I1・決定 2・6・9） ---

# 待つ日数。正は plugins/ndf/pyproject.toml の exclude-newer で、同じ値であることをテストが縛る（I6）。
# 木から読まないのは、宛先の検証器で PR の Renovate の版を確かめるとき、PR が日数を変えて通れないようにするため
AGE_DAYS = 7
RENOVATE_VERSION_FILE = ".github/renovate-version"
RENOVATE_VERSION_FORMAT = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
PYPI_REGISTRY = "https://pypi.org/simple"
PYPI_URL = "https://pypi.org/pypi/{name}/{version}/json"
NPM_REGISTRY = "https://registry.npmjs.org/"
NPM_URL = "https://registry.npmjs.org/{name}"
# uv.lock の取得元のうち、手元のコード（公開日を持たない・Renovate が上げない）として見ないもの
UV_LOCAL_SOURCES = ("editable", "virtual", "directory", "path")


@dataclass(frozen=True)
class Release:
    registry: str  # pypi・npm。公開日を持つ取得元の外（git・URL など）は ""
    name: str
    version: str
    where: str  # 入った場所（lock・UV_VERSION のファイル・.github/renovate-version・--renovate-version）

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.registry, self.name, self.version)


def git_show(root: Path, rev: str, path: str) -> str | None:
    """`rev` の `path` の本文。そのコミットに無ければ None。"""
    p = subprocess.run(["git", "-C", str(root), "show", f"{rev}:{path}"], capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else None


def verify_rev(root: Path, rev: str) -> None:
    p = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], capture_output=True, text=True)
    if p.returncode != 0:
        raise Stop("invalid", f"{rev} はコミットとして読めない")


def uv_lock_releases(text: str, where: str) -> list[Release]:
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
        out.append(Release(registry, str(pkg.get("name", "")), str(pkg.get("version", "")), where))
    return out


def npm_lock_releases(text: str, where: str) -> list[Release]:
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
        out.append(Release(registry, name, str(pkg.get("version", "")), where))
    return out


def renovate_release(version: str, where: str) -> Release:
    if not RENOVATE_VERSION_FORMAT.fullmatch(version):
        raise Stop("invalid", f"{where} の Renovate の版「{version}」が ^[0-9]+\\.[0-9]+\\.[0-9]+$ の形でない")
    return Release("npm", "renovate", version, where)


def changed_releases(root: Path, base: str, head: str) -> list[Release]:
    """`base` から `head` で新しく入った版。lock は Renovate の対象（`targets`。I9）だけを見る。"""
    for rev in (base, head):
        verify_rev(root, rev)
    out: list[Release] = []
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
        out += [Release("pypi", "uv", m[1], rel) for m in UV_VERSION_RE.findall(new) if m[1] not in before]
    old, new = git_show(root, base, RENOVATE_VERSION_FILE), git_show(root, head, RENOVATE_VERSION_FILE)
    if new is not None and new.strip() != (old or "").strip():
        out.append(renovate_release(new.strip(), RENOVATE_VERSION_FILE))
    return out


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def published_at(r: Release, fetch: Fetch, cache: dict[str, bytes]) -> datetime:
    """その版の最初の公開の時刻。読めなければ ValueError（理由つき）。"""
    if r.registry == "pypi":
        url = PYPI_URL.format(name=urllib.parse.quote(r.name, safe=""), version=urllib.parse.quote(r.version, safe=""))
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
    return re.sub(r"[-_.]+", "-", name).lower() if registry == "pypi" else name


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
    found: dict[tuple[str, str, str], Release] = {}
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
            item["verdict"] = "ok" if today - published >= timedelta(days=AGE_DAYS) else "too_new"
        items.append(item)
    if any(i["verdict"] != "ok" for i in items):
        allowed = vulnerability_exceptions(root)
        for i in items:
            if i["verdict"] != "ok" and (normalized(i["registry"], i["name"]), i["version"]) in allowed:
                i["verdict"] = "exception"
    failed = [i for i in items if i["verdict"] in ("too_new", "no_date")]
    if failed:
        names = "、".join(f"{i['name']} {i['version']}（{i['where']}）" for i in failed[:10])
        raise Stop("check_failed", f"公開から {AGE_DAYS} 日に満たないか公開日が読めない版が {len(failed)} 個ある: {names}", items)
    excepted = sum(1 for i in items if i["verdict"] == "exception")
    return Result("ok", f"差分で入った {len(items)} 個の版は公開から {AGE_DAYS} 日たっている（脆弱性の例外 {excepted} 個）", items)


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


def cmd_age(args: argparse.Namespace, deps: Deps) -> Result:
    return age(args.root, args.base, args.head, deps.fetch, datetime.now(timezone.utc), args.renovate_version)


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
    "age": (add_age, cmd_age),
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
