#!/usr/bin/env python3
"""最新の版のスモークの手順のうち、判断の要らない 2 つを持つ（#1982）。

- `resolve --runtime <claude|codex|kiro|agy|all>`: 取得元の最新の版を、CLI の版の一覧
  （`tests/runtime-smoke/cli-versions.env`）と同じ `KEY=VALUE` の形で標準出力へ出す。
  取得元に届かない・形が読めないときは終了コード 1 で、標準エラーへ取得元の URL を書く
- `report --runtime <名前> --run-url <URL> [--versions <パス>] [--repo <owner/name>]`: 題が
  `最新の版のスモークが落ちた: <ランタイム>` の開いている課題があればコメントを足し、無ければ作る。
  `gh` が失敗したら終了コード 1

版を上げる手順（`tests/runtime-smoke/README.md`）も `resolve` の出力を一覧へ写す。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

RUNTIMES = ("claude", "codex", "kiro", "agy")
VERSION_RE = re.compile(r"^[0-9]+(\.[0-9]+)+$")
HEX_RE = re.compile(r"^[0-9a-f]+$")

NPM_DIST_TAGS = "https://registry.npmjs.org/-/package/{package}/dist-tags"
KIRO_MANIFEST = "https://prod.download.cli.kiro.dev/stable/latest/manifest.json"
AGY_MANIFEST = "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/{platform}.json"
# arch の名前は `uname -m` の値に揃える（一覧のキーの末尾）。agy の manifest は別の名前で引く
AGY_PLATFORMS = {"X86_64": "linux_amd64", "AARCH64": "linux_arm64"}
KIRO_ARCHES = {"X86_64": "x86_64", "AARCH64": "aarch64"}

TITLE = "最新の版のスモークが落ちた: {runtime}"


class ResolveError(Exception):
    """取得元に届かない・形が読めない。`url` は標準エラーへ出す取得元"""

    def __init__(self, url: str, reason: str):
        super().__init__(f"{url}: {reason}")
        self.url = url


def fetch_json(url: str) -> object:
    """取得元から JSON を読む。テストはこの関数を置き換える"""
    try:
        with urllib.request.urlopen(url, timeout=30) as res:  # noqa: S310 - 取得元は定数
            return json.load(res)
    except (OSError, ValueError) as e:
        raise ResolveError(url, str(e)) from e


def _version(url: str, value: object) -> str:
    if not isinstance(value, str) or not VERSION_RE.match(value):
        raise ResolveError(url, f"版数が読めない: {value!r}")
    return value


def _hex(url: str, value: object, length: int) -> str:
    if not isinstance(value, str) or len(value) != length or not HEX_RE.match(value):
        raise ResolveError(url, f"checksum が読めない: {value!r}")
    return value


def _npm_latest(package: str) -> str:
    url = NPM_DIST_TAGS.format(package=package)
    data = fetch_json(url)
    latest = data.get("latest") if isinstance(data, dict) else None
    return _version(url, latest)


def resolve_claude() -> list[tuple[str, str]]:
    return [("CLAUDE_CODE_VERSION", _npm_latest("@anthropic-ai/claude-code"))]


def resolve_codex() -> list[tuple[str, str]]:
    return [("CODEX_VERSION", _npm_latest("@openai/codex"))]


def resolve_kiro() -> list[tuple[str, str]]:
    url = KIRO_MANIFEST
    data = fetch_json(url)
    if not isinstance(data, dict) or not isinstance(data.get("packages"), list):
        raise ResolveError(url, "packages が無い")
    version = _version(url, data.get("version"))
    pairs = [("KIRO_CLI_VERSION", version)]
    for key, arch in KIRO_ARCHES.items():
        download = f"{version}/kirocli-{arch}-linux-musl.tar.gz"
        found = [p for p in data["packages"] if isinstance(p, dict) and p.get("download") == download]
        if len(found) != 1:
            raise ResolveError(url, f"{download} が manifest に 1 つだけ載っていない")
        pairs.append((f"KIRO_CLI_SHA256_{key}", _hex(url, found[0].get("sha256"), 64)))
    return pairs


def resolve_agy() -> list[tuple[str, str]]:
    versions: dict[str, str] = {}
    urls: list[tuple[str, str]] = []
    sums: list[tuple[str, str]] = []
    for key, platform in AGY_PLATFORMS.items():
        url = AGY_MANIFEST.format(platform=platform)
        data = fetch_json(url)
        if not isinstance(data, dict):
            raise ResolveError(url, "形が読めない")
        version = _version(url, data.get("version"))
        dl = data.get("url")
        # 版つきの取得先だけを受ける（版を含まない url は固定にならない）
        if not isinstance(dl, str) or not dl.startswith("https://") or f"/{version}-" not in dl:
            raise ResolveError(url, f"版つきの url が読めない: {dl!r}")
        versions[platform] = version
        urls.append((f"AGY_URL_{key}", dl))
        sums.append((f"AGY_SHA512_{key}", _hex(url, data.get("sha512"), 128)))
    if len(set(versions.values())) != 1:
        raise ResolveError(AGY_MANIFEST.format(platform="*"), f"arch ごとの版が違う: {versions}")
    return [("AGY_VERSION", next(iter(versions.values()))), *urls, *sums]


RESOLVERS = {"claude": resolve_claude, "codex": resolve_codex, "kiro": resolve_kiro, "agy": resolve_agy}


def resolve(runtime: str) -> list[tuple[str, str]]:
    names = RUNTIMES if runtime == "all" else (runtime,)
    pairs: list[tuple[str, str]] = []
    for name in names:
        pairs.extend(RESOLVERS[name]())
    return pairs


def run_gh(args: list[str], stdin: str | None = None) -> str:
    """`gh` を動かして標準出力を返す。失敗は CalledProcessError。テストはこの関数を置き換える"""
    res = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, check=True)
    return res.stdout


def _repo_args(repo: str | None) -> list[str]:
    return ["--repo", repo] if repo else []


def report_body(runtime: str, run_url: str, versions: str | None) -> str:
    shown = versions.strip() if versions and versions.strip() else "解決できなかった"
    return "\n".join(
        [
            f"最新の版のスモーク（`runtime-plugin-smoke-latest.yml`）で `{runtime}` が落ちた。",
            "",
            f"- ランタイム: `{runtime}`",
            f"- 実行の URL: {run_url}",
            "- 入れた版:",
            "",
            "```text",
            shown,
            "```",
            "",
            "版を上げる手順は `tests/runtime-smoke/README.md` の「版を上げる」にある。"
            "直す PR（版を上げる PR かアダプタの修正）でこの課題を閉じる。",
        ]
    )


def report(runtime: str, run_url: str, versions: str | None, repo: str | None) -> str:
    """開いている同じ題の課題へコメントを足すか、無ければ作る。何をしたかを返す"""
    title = TITLE.format(runtime=runtime)
    listed = json.loads(run_gh(["issue", "list", *_repo_args(repo), "--state", "open", "--limit", "1000", "--json", "number,title"]))
    # 題の完全一致で探す（部分一致にすると別のランタイムの課題へ足してしまう）
    same = sorted(i["number"] for i in listed if i.get("title") == title)
    body = report_body(runtime, run_url, versions)
    if same:
        run_gh(["issue", "comment", str(same[0]), *_repo_args(repo), "--body-file", "-"], stdin=body)
        return f"commented #{same[0]}"
    out = run_gh(["issue", "create", *_repo_args(repo), "--title", title, "--body-file", "-"], stdin=body)
    return f"created {out.strip()}"


def cmd_resolve(args: argparse.Namespace) -> int:
    try:
        pairs = resolve(args.runtime)
    except ResolveError as e:
        print(f"ERROR: 最新の版を解決できない: {e.url}: {e}", file=sys.stderr)
        return 1
    for key, value in pairs:
        print(f"{key}={value}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    versions = None
    if args.versions:
        path = Path(args.versions)
        versions = path.read_text(encoding="utf-8") if path.is_file() else None
    repo = args.repo or os.environ.get("GITHUB_REPOSITORY") or None
    try:
        print(report(args.runtime, args.run_url, versions, repo))
    except subprocess.CalledProcessError as e:
        print(f"ERROR: gh が失敗した: {e.stderr or e}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("resolve", help="取得元の最新の版を一覧の形で出す")
    p.add_argument("--runtime", required=True, choices=(*RUNTIMES, "all"))
    p.set_defaults(func=cmd_resolve)
    p = sub.add_parser("report", help="失敗の課題を作るか、開いている課題へコメントを足す")
    p.add_argument("--runtime", required=True, choices=RUNTIMES)
    p.add_argument("--run-url", required=True)
    p.add_argument("--versions", help="入れた版の一覧（解決に失敗したときは無くてよい）")
    p.add_argument("--repo", help="owner/name（省くと GITHUB_REPOSITORY か gh の既定）")
    p.set_defaults(func=cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
