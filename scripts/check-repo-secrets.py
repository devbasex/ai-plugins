#!/usr/bin/env python3
"""public のリポジトリへ秘密・`.env`・インフラの定義が入っていないかを見る（#2169 の境界の検査のうち、
サービスのディレクトリに依存しない種類を #2646 で残したもの）。

git が追跡するファイルだけを見る。手元の追跡していないファイル（`.env` の実ファイルなど）で結果は変わらない。

- インフラの定義: CDK・Terraform・CloudFormation の定義。Markdown の本文の語は見ない
- 秘密らしい値: AWS のアクセスキーの形・PEM の秘密鍵の始まりの行。許可の一覧のファイルと値の sha256 の組に
  一致するものを除く。PEM の値は始まりの行から終わりの行（無ければファイルの末尾）までの塊全体で照らす。
  違反の行に照らした値の sha256 を出す。偽の値なら、その値をそのまま許可の行の `sha256` へ写す
  （PEM の塊は行末を `\n` にそろえて結ぶため、ファイルの中身の sha256 とは一致しない）
- .env の実ファイル: 名前が `.env` か `.env.<何か>`（`.env.example` を除く）
- 古い許可: 許可の行が今の木のどこにも当たらない

使い方:

    python3 scripts/check-repo-secrets.py [--root <dir>] [--allow <file>]

終了コード:

    0  違反なし
    1  違反が 1 件以上
    2  読めない（git の根でない・許可の一覧が壊れている・知らない鍵）。違反は出さない
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

DEFAULT_ALLOW = "scripts/repo-secrets-allow.json"

# 違反の種類
INFRA = "インフラの定義"
SECRET = "秘密らしい値"
ENV_FILE = ".env の実ファイル"
STALE_ALLOW = "古い許可"

# 種類 → 直し方。設計の「入出力の契約」の表と同じ並び
FIXES = {
    INFRA: "private リポジトリへ移す",
    SECRET: "値を消して環境変数で受ける。偽の値なら許可へ足す",
    ENV_FILE: "ファイルを消し、.gitignore に足す",
    STALE_ALLOW: "許可の一覧から外す",
}

ACCESS_KEY_PATTERN = re.compile(r"(?<![0-9A-Z])(?:AKIA|ASIA)[0-9A-Z]{16}(?![0-9A-Z])")
PEM_BEGIN = re.compile("-----" + r"BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY" + "-----")
PEM_END = re.compile("-----" + r"END (?:[A-Z0-9]+ )*PRIVATE KEY" + "-----")
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
    digest: str | None = None

    def render(self) -> str:
        where = self.path if self.line is None else f"{self.path}:{self.line}"
        tail = "" if self.digest is None else f"（sha256={self.digest}）"
        return f"{self.kind}: {where} → {FIXES[self.kind]}{tail}"


def digest_of(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Allowlist:
    """許可の行を (path, sha256) で引き、当たった行を覚える。"""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.by_key = {(r["path"], r["sha256"]): r for r in rows}
        self.used: set[int] = set()

    def allows(self, rel: str, digest: str) -> bool:
        row = self.by_key.get((rel, digest))
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


def lines_of(data: bytes) -> list[str]:
    return data.decode("utf-8", errors="replace").splitlines()


def matching_lines(kind: str, rel: str, lines: list[str], hit: Callable[[str], bool]) -> list[Finding]:
    return [Finding(kind, rel, n) for n, line in enumerate(lines, 1) if hit(line)]


def check_infra(rel: str, lines: list[str]) -> list[Finding]:
    name = PurePosixPath(rel).name
    if name in INFRA_NAMES or name.endswith(INFRA_SUFFIXES):
        return [Finding(INFRA, rel)]
    if DEPENDENCY_FILE.match(name):
        return matching_lines(INFRA, rel, lines, lambda line: "aws-cdk-lib" in line)
    if name.endswith(CFN_SUFFIXES):
        return matching_lines(INFRA, rel, lines, CFN_DECL.search)[:1]
    return []


def pem_block(lines: list[str], start: int, line: str, begin: re.Match) -> str:
    """始まりの行の位置から終わりの行（無ければファイルの末尾）までの鍵の塊。

    許可は塊全体の sha256 で照らす。始まりの行だけで照らすと、同じパスの鍵を差し替えても許可されるため。
    """
    end = PEM_END.search(line, begin.end())
    if end:
        return line[begin.start() : end.end()]
    block = [line[begin.start() :]]
    for nxt in lines[start + 1 :]:
        end = PEM_END.search(nxt)
        if end:
            block.append(nxt[: end.end()])
            break
        block.append(nxt)
    return "\n".join(block)


def check_secrets(rel: str, lines: list[str], allow: Allowlist) -> list[Finding]:
    out = []
    for i, line in enumerate(lines):
        values = [m.group(0) for m in ACCESS_KEY_PATTERN.finditer(line)]
        values += [pem_block(lines, i, line, m) for m in PEM_BEGIN.finditer(line)]
        digests = [digest_of(v) for v in values]
        out += [Finding(SECRET, rel, i + 1, d) for d in digests if not allow.allows(rel, d)]
    return out


def check_env(rel: str, lines: list[str]) -> list[Finding]:
    name = PurePosixPath(rel).name
    if name == ".env.example":
        return []
    if name == ".env" or name.startswith(".env."):
        return [Finding(ENV_FILE, rel)]
    return []


def check(root: Path, allow_path: Path) -> list[Finding]:
    root = git_root(root)
    allow = Allowlist(read_allow(allow_path, root))
    out: list[Finding] = []
    for rel in tracked_files(root):
        full = root / rel
        if not full.is_symlink() and not full.is_file():
            continue
        lines = [] if full.is_symlink() else lines_of(full.read_bytes())
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
        print(f"[check-repo-secrets] {e}", file=sys.stderr)
        return 2
    for f in findings:
        print(f.render())
    print(f"[check-repo-secrets] 違反 {len(findings)} 件")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
