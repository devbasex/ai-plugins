"""副命令 `ndf materials`: 課題の本文と設計文書から、モードの判定の材料を集める（#3569。旧い `mode-materials.py` の移し先）。

    ndf materials --issue <番号>... [--root <リポジトリ>] [--out <判定の根拠のファイル>]

課題ごとに、種類・公開インタフェースの変更の有無・触るファイルの候補・候補ごとのテストのファイルを出す。判定表の行は
選ばない。`--out` を渡すと、判定の根拠のファイルの下書き（row・mode・condition が空）を書く。conductor が埋めて
`ndf loop sprint` に渡す。結果は 1 行の結果 JSON（形は contracts/schemas/io/materials.result.schema.json）。

終了コード: 0 = 集めた / 6 = 本文を読めない課題がある（種類 `incomplete`。`gaps` に課題と理由）/ 3 = 追跡されたファイルの
一覧を読めない（git の外）/ 1 = 下書きを書けない。使い方の誤りは副命令の表が `invalid`（2）で返す。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ndf import cli
from ndf.adapters import git, github
from ndf.contracts import result
from ndf.domains.materials import collect

TOOL = cli.tool_of(__name__)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"ndf {TOOL}", description="課題の本文と設計文書から、モードの判定の材料を集める")
    p.add_argument("--issue", type=int, nargs="+", required=True, help="課題の番号（1 つ以上）")
    p.add_argument("--root", default=".", help="リポジトリの作業場所")
    p.add_argument("--out", help="判定の根拠のファイルの下書きを書く先")
    return p


def _body_reader(repo: git.Git):
    hub: list[github.GitHub] = []

    def body_of(number: int) -> str:
        try:
            if not hub:
                hub.append(github.GitHub(repo.remote_repo(), github.client()))
            return hub[0].issue_body(number).body
        except (github.GitHubError, git.GitError) as exc:
            raise ValueError(str(exc)) from exc

    return body_of


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()
    repo = git.Git(root)
    try:
        files = repo.run("ls-files").splitlines()
    except git.GitError as exc:
        return result.emit(result.stopped(TOOL, "precondition", f"追跡されたファイルの一覧を読めない: {exc}"))
    found = collect.gather(root, args.issue, files, _body_reader(repo))
    metrics: dict[str, object] = {"issues": len(found.items), "gaps": len(found.gaps)}
    if found.gaps:
        detail = f"{len(found.gaps)} 件の課題の本文を読めない"
        return result.emit(result.stopped(TOOL, "incomplete", detail, items=found.items, metrics=metrics, gaps=found.gaps))
    if args.out:
        try:
            collect.write_draft(Path(args.out), collect.draft(found.items))
        except OSError as exc:
            return result.emit(result.stopped(TOOL, "unknown", f"下書きを書けない: {exc}", items=found.items, metrics=metrics))
        metrics["out"] = args.out
    empty = [i["number"] for i in found.items if not i["files_found"]]
    note = f"。触るファイルの候補が 0 件: {' '.join(f'#{n}' for n in empty)}" if empty else ""
    return result.emit(
        result.result(TOOL, result.OK, f"{len(found.items)} 件の課題の判定の材料を集めた{note}", items=found.items, metrics=metrics)
    )
