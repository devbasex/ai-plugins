#!/usr/bin/env python3
"""依存の検査のサブコマンド（#3370 #3691。詳細設計は issues/issue-3370-3691-design-detail.md の「依存の検査のスクリプト」）。

    python3 scripts/plugin-deps.py targets [--root <リポジトリ>]
    python3 scripts/plugin-deps.py holds   [--root <リポジトリ>]
    python3 scripts/plugin-deps.py uv-pin  [--root <リポジトリ>]
    python3 scripts/plugin-deps.py action-pins --base <コミット> --head <コミット> [--root <リポジトリ>]
    python3 scripts/plugin-deps.py cli-help --runtime <gh|claude|codex> [--image <イメージ>] [--root <表の作業ツリー>]

- `targets`: 追跡している lock（`uv.lock`・`package-lock.json`）から `.github/renovate.json` の `ignorePaths` を
  除き、種類（uv・npm）とディレクトリの組を並べる（不変条件 I9）
- `holds`: `.github/renovate.json` の固定と対象の外の規則から、種類・相手・制約・見直す日・理由の 5 つの欄を読む
  （決定 5・不変条件 I3）
- `uv-pin`: `UV_VERSION` のインストーラを 1 回だけ取得して sha256 を計算し、`plugins/ndf/bin/ndf` と
  `scripts/lib/ndf_wrappers.py` の `UV_INSTALLER_SHA256` を同じ値に書き直す（決定 12。Renovate の規則 10 が打つ）
- `action-pins`: `base` と `head` の差分で足した `.github/workflows/` の外部の Action の `uses:` の行ごとに、注記のタグが指す
  コミット（注記つきのタグは指す先まで辿る）が行の SHA と同じかを確かめる（決定 13・不変条件 I10）
- `cli-help`: `plugins/ndf/contracts/domains/cli-floors.json` の `help` の見本の引数が、CLI の `--help` の出力にあるかを
  照らす（決定 11。#3691 の受け入れ条件 3）。`--image` を渡すとそのイメージの中の CLI を 1 回の `docker run` で全部の
  見本について打ち、無ければホストの CLI を打つ。`--root` からは表の JSON を読むだけでコードを読まない

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
import secrets
import shlex
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


def cmd_action_pins(args: argparse.Namespace, deps: Deps) -> Result:
    return action_pins(args.root, args.base, args.head, deps.gh)


def add_action_pins(p: argparse.ArgumentParser) -> None:
    add_root(p)
    p.add_argument("--base", required=True, help="比べる前のコミット（PR では HEAD^1）")
    p.add_argument("--head", required=True, help="比べる後のコミット（PR では HEAD）")


def cmd_cli_help(args: argparse.Namespace, deps: Deps) -> Result:
    return cli_help(args.root, deps.run, args.runtime, args.image)


def add_cli_help(p: argparse.ArgumentParser) -> None:
    add_root(p)
    p.add_argument("--runtime", required=True, help="cli-floors.json の行（gh・claude・codex）")
    p.add_argument("--image", help="CLI が入ったイメージ（省けばホストの CLI を打つ）")


def add_root(p: argparse.ArgumentParser) -> None:
    p.add_argument("--root", type=Path, default=REPO, help="リポジトリの根（既定はこのスクリプトのリポジトリ）")


# 名前 → (引数の組み立て, 処理)。後の単位はここへ行を足す
COMMANDS: dict[str, tuple[Callable[[argparse.ArgumentParser], None], Callable[[argparse.Namespace, Deps], Result]]] = {
    "targets": (add_root, cmd_targets),
    "holds": (add_root, cmd_holds),
    "uv-pin": (add_root, cmd_uv_pin),
    "action-pins": (add_action_pins, cmd_action_pins),
    "cli-help": (add_cli_help, cmd_cli_help),
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
