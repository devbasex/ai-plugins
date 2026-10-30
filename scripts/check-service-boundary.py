#!/usr/bin/env python3
"""サービスのディレクトリ（`factory/`）とプラグイン・配布物・インフラの境目を見る（#2169 の境界の検査）。

git が追跡するファイルだけを見る。手元の追跡していないファイル（`.env` の実ファイルなど）で結果は変わらない。

- サービスへの参照: `plugins/` の下と `.claude-plugin/marketplace.json` に、パスの区切りとして現れる `factory/`、
  または行き先がサービスのディレクトリのシンボリックリンクがある
- 配布物の木の重なり: `marketplace.json` の `source` が根・サービスのディレクトリ・その祖先か子孫を指す
- インフラの定義: CDK・Terraform・CloudFormation の定義。Markdown の本文の語は見ない
- 秘密らしい値: AWS のアクセスキーの形・PEM の秘密鍵の始まりの行。許可の一覧のファイルと値の sha256 の組に
  一致するものを除く
- .env の実ファイル: 名前が `.env` か `.env.<何か>`（`.env.example` を除く）
- 値を持つ .env.example: `factory/` の下の `.env.example` で、`=` の後が空でない行
- 古い許可: 許可の行が今の木のどこにも当たらない

サービスのディレクトリの `LICENSE` の種類と文面は合否の条件にしない（秘密らしい値の検査はほかと同じく当てる）。

使い方:

    python3 scripts/check-service-boundary.py [--root <dir>] [--allow <file>]

終了コード:

    0  違反なし
    1  違反が 1 件以上
    2  読めない（git の根でない・許可の一覧が壊れている・知らない鍵）。違反は出さない
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

SERVICE_DIR = "factory"
MARKETPLACE = ".claude-plugin/marketplace.json"
DEFAULT_ALLOW = "scripts/service-boundary-allow.json"

# 違反の種類
REF = "サービスへの参照"
OVERLAP = "配布物の木の重なり"
INFRA = "インフラの定義"
SECRET = "秘密らしい値"
ENV_FILE = ".env の実ファイル"
ENV_VALUE = "値を持つ .env.example"
STALE_ALLOW = "古い許可"

# 種類 → 直し方。設計の「入出力の契約」の表と同じ並び
FIXES = {
    REF: "参照を外す。プラグインからサービスを使わない",
    OVERLAP: "source を plugins/ の下へ戻す",
    INFRA: "private リポジトリへ移す",
    SECRET: "値を消して環境変数で受ける。偽の値なら許可へ足す",
    ENV_FILE: "ファイルを消し、.gitignore に足す",
    ENV_VALUE: "値を消し、名前だけにする",
    STALE_ALLOW: "許可の一覧から外す",
}

SERVICE_REF = re.compile(r"(?<![\w.-])" + SERVICE_DIR + r"/")
SECRET_PATTERNS = (
    re.compile(r"(?<![0-9A-Z])(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])"),
    re.compile("-----" + r"BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY" + "-----"),
)
INFRA_NAMES = {"cdk.json", "cdk.context.json", ".terraform.lock.hcl"}
INFRA_SUFFIXES = (".tf", ".tf.json", ".tfvars")
DEPENDENCY_FILE = re.compile(r"^(package\.json|pyproject\.toml|requirements[^/]*\.txt)$")
CFN_SUFFIXES = (".json", ".yaml", ".yml", ".template")
CFN_DECL = re.compile(r"AWSTemplateFormatVersion|AWS::[A-Za-z0-9]+::[A-Za-z0-9]+")
ALLOW_KEYS = {"version", "secrets"}
ALLOW_ROW_KEYS = {"path", "sha256", "reason"}
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class Unreadable(Exception):
    """検査が成り立たない（終了コード 2）。"""


@dataclass(frozen=True)
class Finding:
    kind: str
    path: str
    line: int | None = None

    def render(self) -> str:
        where = self.path if self.line is None else f"{self.path}:{self.line}"
        return f"{self.kind}: {where} → {FIXES[self.kind]}"


class Allowlist:
    """許可の行を (path, sha256) で引き、当たった行を覚える。"""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.by_key = {(r["path"], r["sha256"]): r for r in rows}
        self.used: set[int] = set()

    def allows(self, rel: str, value: str) -> bool:
        row = self.by_key.get((rel, hashlib.sha256(value.encode()).hexdigest()))
        if row is None:
            return False
        self.used.add(id(row))
        return True

    def stale(self) -> list[dict]:
        return [r for r in self.rows if id(r) not in self.used]


def git_root(root: Path) -> Path:
    r = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if r.returncode != 0:
        raise Unreadable(f"git のリポジトリでない: {root}")
    top = Path(r.stdout.strip()).resolve()
    if top != root.resolve():
        raise Unreadable(f"git の根でない（根は {top}）: {root}")
    return top


def tracked_files(root: Path) -> list[str]:
    r = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True)
    if r.returncode != 0:
        raise Unreadable(f"git ls-files が失敗した: {r.stderr.decode(errors='replace').strip()}")
    return [p for p in r.stdout.decode("utf-8", errors="surrogateescape").split("\0") if p]


def read_allow(path: Path, root: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Unreadable(f"許可の一覧を読めない: {path}: {e}") from e
    if not isinstance(data, dict) or set(data) - ALLOW_KEYS or data.get("version") != 1:
        raise Unreadable(f"許可の一覧の形が違う（鍵は {sorted(ALLOW_KEYS)}、version は 1）: {path}")
    rows = data.get("secrets", [])
    if not isinstance(rows, list):
        raise Unreadable(f"許可の一覧の secrets が並びでない: {path}")
    for row in rows:
        validate_allow_row(row, root)
    return rows


def validate_allow_row(row: object, root: Path) -> None:
    if not isinstance(row, dict) or set(row) != ALLOW_ROW_KEYS:
        raise Unreadable(f"許可の行の鍵は {sorted(ALLOW_ROW_KEYS)} だけ: {row}")
    p, digest, reason = row["path"], row["sha256"], row["reason"]
    if not (isinstance(p, str) and p and isinstance(reason, str) and reason.strip()):
        raise Unreadable(f"許可の行の path と reason が空: {row}")
    if any(c in p for c in "*?[") or p.endswith("/") or (root / p).is_dir():
        raise Unreadable(f"許可の path はファイル 1 つだけ（ディレクトリと glob は受けない）: {p}")
    if not (isinstance(digest, str) and SHA256.match(digest)):
        raise Unreadable(f"許可の sha256 が 64 字の 16 進でない: {row}")


def in_service(rel: str) -> bool:
    return rel == SERVICE_DIR or rel.startswith(SERVICE_DIR + "/")


def lines_of(data: bytes) -> list[str]:
    return data.decode("utf-8", errors="replace").splitlines()


def matching_lines(kind: str, rel: str, lines: list[str], hit: Callable[[str], bool]) -> list[Finding]:
    return [Finding(kind, rel, n) for n, line in enumerate(lines, 1) if hit(line)]


def check_reference(root: Path, rel: str, lines: list[str]) -> list[Finding]:
    full = root / rel
    if full.is_symlink():
        target = os.path.normpath(os.path.join(os.path.dirname(rel), os.readlink(full)))
        return [Finding(REF, rel)] if in_service(target.replace(os.sep, "/")) else []
    return matching_lines(REF, rel, lines, SERVICE_REF.search)


def check_sources(root: Path) -> list[Finding]:
    path = root / MARKETPLACE
    if not path.is_file():
        return []
    try:
        plugins = json.loads(path.read_text(encoding="utf-8")).get("plugins", [])
    except ValueError as e:
        raise Unreadable(f"{MARKETPLACE} を読めない: {e}") from e
    out = []
    for plugin in plugins:
        source = plugin.get("source") if isinstance(plugin, dict) else None
        if not isinstance(source, str):
            continue
        s = os.path.normpath(source).replace(os.sep, "/")
        if s in (".", "") or in_service(s) or (SERVICE_DIR + "/").startswith(s + "/"):
            out.append(Finding(OVERLAP, f"{MARKETPLACE}#{plugin.get('name', source)}"))
    return out


def check_infra(rel: str, lines: list[str]) -> list[Finding]:
    name = PurePosixPath(rel).name
    if name in INFRA_NAMES or name.endswith(INFRA_SUFFIXES):
        return [Finding(INFRA, rel)]
    if DEPENDENCY_FILE.match(name):
        return matching_lines(INFRA, rel, lines, lambda line: "aws-cdk-lib" in line)
    if name.endswith(CFN_SUFFIXES):
        return matching_lines(INFRA, rel, lines, CFN_DECL.search)[:1]
    return []


def check_secrets(rel: str, lines: list[str], allow: Allowlist) -> list[Finding]:
    out = []
    for n, line in enumerate(lines, 1):
        for pattern in SECRET_PATTERNS:
            for m in pattern.finditer(line):
                if not allow.allows(rel, m.group(0)):
                    out.append(Finding(SECRET, rel, n))
    return out


def has_value(line: str) -> bool:
    return bool(not line.lstrip().startswith("#") and "=" in line and line.split("=", 1)[1].strip())


def check_env(rel: str, lines: list[str]) -> list[Finding]:
    name = PurePosixPath(rel).name
    if name == ".env.example":
        if not in_service(rel):
            return []
        return matching_lines(ENV_VALUE, rel, lines, has_value)
    if name == ".env" or name.startswith(".env."):
        return [Finding(ENV_FILE, rel)]
    return []


def check(root: Path, allow_path: Path) -> list[Finding]:
    root = git_root(root)
    allow = Allowlist(read_allow(allow_path, root))
    out = check_sources(root)
    for rel in tracked_files(root):
        full = root / rel
        if not full.is_symlink() and not full.is_file():
            continue
        lines = [] if full.is_symlink() else lines_of(full.read_bytes())
        if rel.startswith("plugins/") or rel == MARKETPLACE:
            out += check_reference(root, rel, lines)
        out += check_infra(rel, lines)
        out += check_secrets(rel, lines, allow)
        out += check_env(rel, lines)
    out += [Finding(STALE_ALLOW, r["path"]) for r in allow.stale()]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="見るリポジトリの根（既定: 今の git の根）")
    ap.add_argument("--allow", default=None, help=f"許可の一覧（既定: <root>/{DEFAULT_ALLOW}）")
    args = ap.parse_args(argv)
    try:
        if args.root is None:
            r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
            if r.returncode != 0:
                raise Unreadable("git のリポジトリの中で打つか、--root を渡す")
            root = Path(r.stdout.strip())
        else:
            root = Path(args.root)
        allow = Path(args.allow) if args.allow else root / DEFAULT_ALLOW
        findings = check(root, allow)
    except Unreadable as e:
        print(f"[check-service-boundary] {e}", file=sys.stderr)
        return 2
    for f in findings:
        print(f.render())
    print(f"[check-service-boundary] 違反 {len(findings)} 件")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
