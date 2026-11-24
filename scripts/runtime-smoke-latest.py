#!/usr/bin/env python3
"""毎日のワークフロー（`runtime-plugin-smoke-latest.yml`）の手順のうち、判断の要らない 2 つを持つ（#1982・#3687）。

- `resolve --runtime <claude|codex|all>`: 取得元の最新の版を、CLI の版の一覧
  （`tests/runtime-smoke/cli-versions.env`）と同じ `KEY=VALUE` の形で標準出力へ出す。
  取得元に届かない・形が読めないときは終了コード 1 で、標準エラーへ取得元の URL を書く
- `report --run-url <URL> [--outcomes <ディレクトリ>] [--samples <結果 JSON>] [--repo <owner/name>]`: ジョブの成果物を
  JSON のデータとして読み、`--outcomes` の `<ランタイム>.json` が `failure` のランタイムと、`--samples`（`ndf samples verify` の
  結果 JSON）に食い違い（`kind: mismatch`）のある相手ごとに、同じ題の開いている課題があればコメントを足し、無ければ作る。
  無いファイルは読まない。成果物の値は決めた形に合うものだけを題と本文へコピーし、外れた値は標準エラーにも出さない。
  課題の一覧は報告があるときに 1 回だけ取り、取れなければ終了コード 1。作成とコメントが失敗したら、残りの報告を続けてから終了コード 1

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

RUNTIMES = ("claude", "codex")
VERSION_RE = re.compile(r"^[0-9]+(\.[0-9]+)+$")
# ランタイムごとに課題へコピーしてよいバージョンのキー（RESOLVERS が出すキーと同じ）
VERSION_KEYS = {"claude": ("CLAUDE_CODE_VERSION",), "codex": ("CODEX_VERSION",)}
STATUSES = ("success", "failure", "cancelled")  # job.status の値
PEER_RE = re.compile(r"[a-z0-9-]+")
# 見本の定義のスキーマ（plugins/ndf/contracts/schemas/live-sample.schema.json）の sample と同じ形
SAMPLE_RE = re.compile(r"tests/samples/([a-z0-9][a-z0-9_-]*/)+[a-z0-9][a-z0-9_-]*\.json")
DETAIL_MAX = 500  # 食い違いの説明を課題へコピーする文字数。越えた分は切る
NO_DETAIL = "（説明は実行の URL のログを見る）"  # 課題へコピーできない説明の代わり
SHOWN_ITEMS = 50  # 課題の本文に並べる食い違いの数（本文の大きさの上限に収める）

NPM_DIST_TAGS = "https://registry.npmjs.org/-/package/{package}/dist-tags"

TITLE = "最新の版のスモークが落ちた: {runtime}"
SAMPLES_TITLE = "テストの見本が本物の記述と食い違った: {peer}"


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


def _npm_latest(package: str) -> str:
    url = NPM_DIST_TAGS.format(package=package)
    data = fetch_json(url)
    latest = data.get("latest") if isinstance(data, dict) else None
    return _version(url, latest)


def resolve_claude() -> list[tuple[str, str]]:
    return [("CLAUDE_CODE_VERSION", _npm_latest("@anthropic-ai/claude-code"))]


def resolve_codex() -> list[tuple[str, str]]:
    return [("CODEX_VERSION", _npm_latest("@openai/codex"))]


RESOLVERS = {"claude": resolve_claude, "codex": resolve_codex}


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


def read_json(path: Path) -> object:
    """成果物の JSON。ファイルが無ければ None、JSON として読めなければ標準エラーへ知らせて None"""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, RecursionError):
        print(f"WARNING: 成果物を JSON として読めない: {path}", file=sys.stderr)
        return None


def allowed_versions(runtime: str, text: object) -> str:
    """バージョンの一覧のうち、許すキーと形に合うものだけを並べる。1 つも無ければ空"""
    kept = []
    for line in text.splitlines() if isinstance(text, str) else ():
        key, _, value = line.partition("=")
        if key in VERSION_KEYS[runtime] and VERSION_RE.fullmatch(value):
            kept.append(line)
    return "\n".join(kept)


def failed_runtimes(outcomes: Path) -> list[tuple[str, str]]:
    """失敗で終わったランタイムと、課題へコピーしてよいバージョンの一覧。読むのは RUNTIMES の名前のファイルだけ"""
    failed = []
    for runtime in RUNTIMES:
        path = outcomes / f"{runtime}.json"
        data = read_json(path)
        if data is None:
            continue
        status = data.get("status") if isinstance(data, dict) and data.get("runtime") == runtime else None
        if status not in STATUSES:
            print(f"WARNING: runtime か status が決めた形でないため報告しない: {path}", file=sys.stderr)
        elif status == "failure":
            failed.append((runtime, allowed_versions(runtime, data.get("versions"))))
    return failed


def _mismatch(item: object) -> tuple[str, str, str] | None:
    """食い違いの項目の（相手・見本・課題に載せる説明）。相手か見本が決めた形に合わなければ None"""
    if not isinstance(item, dict):
        return None
    peer, sample = item.get("peer"), item.get("sample")
    if not (isinstance(peer, str) and isinstance(sample, str)):
        return None
    if not (PEER_RE.fullmatch(peer) and SAMPLE_RE.fullmatch(sample)):
        return None
    return peer, sample, _shown_detail(item.get("detail"))


def _shown_detail(detail: object) -> str:
    """課題に載せる説明。長ければ切り、1 行の文字列でなければ固定の文にする（食い違いそのものは説明の形に依らず報告する）"""
    # isprintable は改行と制御文字で偽になる（1 行だけを通す）
    if not (isinstance(detail, str) and detail and detail.isprintable()):
        return NO_DETAIL
    return detail if len(detail) <= DETAIL_MAX else f"{detail[: DETAIL_MAX - 1]}…"


def mismatches(samples: Path) -> dict[str, list[str]]:
    """見本の結果 JSON の食い違いを、相手 → `<見本>: <説明>` の並びにする。相手か見本が形に合わない項目は数えない"""
    data = read_json(samples)
    items = data.get("items") if isinstance(data, dict) else None
    found: dict[str, list[str]] = {}
    dropped = 0
    for item in items if isinstance(items, list) else ():
        if isinstance(item, dict) and item.get("kind") == "unreachable":
            continue
        got = _mismatch(item) if isinstance(item, dict) and item.get("kind") == "mismatch" else None
        if got is None:
            dropped += 1
        else:
            found.setdefault(got[0], []).append(f"{got[1]}: {got[2]}")
    if dropped:
        print(f"WARNING: 決めた形に合わない項目を数えなかった: {dropped} 件", file=sys.stderr)
    return dict(sorted(found.items()))


def report_body(runtime: str, run_url: str, versions: str) -> str:
    return "\n".join(
        [
            f"最新の版のスモーク（`runtime-plugin-smoke-latest.yml`）で `{runtime}` が落ちた。",
            "",
            f"- ランタイム: `{runtime}`",
            f"- 実行の URL: {run_url}",
            "- 入れた版:",
            "",
            "```text",
            versions or "解決できなかった",
            "```",
            "",
            "版を上げる手順は `tests/runtime-smoke/README.md` の「版を上げる」にある。"
            "直す PR（版を上げる PR かアダプタの修正）でこの課題を閉じる。",
        ]
    )


def samples_body(peer: str, run_url: str, lines: list[str]) -> str:
    more = [f"（ほかに {len(lines) - SHOWN_ITEMS} 件）"] if len(lines) > SHOWN_ITEMS else []
    return "\n".join(
        [
            f"毎日のジョブ `live-samples`（`runtime-plugin-smoke-latest.yml`）で、`{peer}` のテストの見本が、"
            "相手の公開する記述と食い違った。",
            "",
            f"- 相手: `{peer}`",
            f"- 実行の URL: {run_url}",
            f"- 食い違い: {len(lines)} 件",
            "",
            "```text",
            *lines[:SHOWN_ITEMS],
            *more,
            "```",
            "",
            "見本を録り直し、定義（`plugins/ndf/contracts/live-samples/`）の `absent_keys` を合わせる。"
            f"`ndf samples verify --peer {peer}` が通ることを確かめ、直す PR でこの課題を閉じる。",
        ]
    )


def open_issues(repo: str | None) -> list[dict]:
    """開いている課題の番号と題"""
    return json.loads(run_gh(["issue", "list", *_repo_args(repo), "--state", "open", "--limit", "1000", "--json", "number,title"]))


def report(title: str, body: str, repo: str | None, listed: list[dict]) -> str:
    """`listed`（開いている課題）に同じ題があればコメントを足し、無ければ作る。何をしたかを返す"""
    # 題の完全一致で探す（部分一致にすると別のランタイムの課題へ足してしまう）
    same = sorted(i["number"] for i in listed if i.get("title") == title)
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
    repo = args.repo or os.environ.get("GITHUB_REPOSITORY") or None
    failed = failed_runtimes(Path(args.outcomes)) if args.outcomes else []
    found = mismatches(Path(args.samples)) if args.samples else {}
    issues = [(TITLE.format(runtime=name), report_body(name, args.run_url, versions)) for name, versions in failed]
    issues += [(SAMPLES_TITLE.format(peer=peer), samples_body(peer, args.run_url, lines)) for peer, lines in found.items()]
    if not issues:
        return 0
    try:
        listed = open_issues(repo)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: gh が失敗した（開いている課題の一覧）: {e.stderr or e}", file=sys.stderr)
        return 1
    code = 0
    for title, body in issues:
        try:
            print(report(title, body, repo, listed))
        except subprocess.CalledProcessError as e:
            print(f"ERROR: gh が失敗した（{title}）: {e.stderr or e}", file=sys.stderr)
            code = 1
    return code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("resolve", help="取得元の最新の版を一覧の形で出す")
    p.add_argument("--runtime", required=True, choices=(*RUNTIMES, "all"))
    p.set_defaults(func=cmd_resolve)
    p = sub.add_parser("report", help="失敗の課題を作るか、開いている課題へコメントを足す")
    p.add_argument("--run-url", required=True)
    p.add_argument("--outcomes", help="ランタイムごとの結果（<ランタイム>.json）のディレクトリ（無ければ読まない）")
    p.add_argument("--samples", help="見本の突き合わせの結果 JSON（無ければ読まない）")
    p.add_argument("--repo", help="owner/name（省くと GITHUB_REPOSITORY か gh の既定）")
    p.set_defaults(func=cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
