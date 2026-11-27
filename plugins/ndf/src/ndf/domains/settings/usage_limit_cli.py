"""サブコマンド `ndf usage-limit`: 終わった外部 AI の起動の出力が利用上限を告げているかを判定する（#1959）。

    ndf usage-limit --runtime <ランタイム> --stdout <ファイル> --stderr <ファイル>

条件は NDF の監視と同じ `agents.launch_usage_limit` で、手順書（`/ndf:external-ai`）の回収が AI に正規表現を読ませずに
監視と同じ結論を得るための口である。結果 JSON の `metrics.usage_limit` が真なら利用上限で、`metrics.line` が根拠の行（利用上限のときだけ載る）。

ログのファイルが無ければ判定せず `precondition` で止める。監視の `tail` は起動の前の無いファイルを空として読むが、
終わった起動のログが無いのはパスの取り違えで、空として読むと利用上限で止まった起動を成功として回収させる。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ndf import cli
from ndf.adapters import agents
from ndf.contracts import facts, result

TOOL = cli.tool_of(__name__)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"ndf {TOOL}", description="終わった起動の出力が利用上限を告げているかを判定する")
    p.add_argument("--runtime", required=True, choices=[r["id"] for r in facts.rows("runtimes")], help="判定する起動のランタイム")
    p.add_argument("--stdout", required=True, type=Path, help="判定する起動の標準出力のファイル")
    p.add_argument("--stderr", required=True, type=Path, help="判定する起動の標準エラー出力のファイル")
    return p


def judge(runtime: str, stdout: Path, stderr: Path) -> dict:
    """判定の結果 JSON。ログのファイルが無ければ `precondition` の stopped。"""
    missing = [str(p) for p in (stdout, stderr) if not p.is_file()]
    if missing:
        return result.stopped(
            TOOL, "precondition", f"判定するログのファイルが無い（{'・'.join(missing)}）。起動のときに逃がした先のパスを渡す"
        )
    hit = agents.launch_usage_limit(runtime, stdout, stderr)
    if hit is None:
        return result.result(TOOL, result.OK, "利用上限でない", metrics={"usage_limit": False})
    return result.result(TOOL, result.OK, "利用上限", metrics={"usage_limit": True, "line": hit})


def main(argv: list[str]) -> int:
    try:
        a = _parser().parse_args(argv)
    except SystemExit as exc:
        if exc.code in (0, None):
            return 0
        return result.emit(result.stopped(TOOL, "invalid", f"引数が誤っている: {' '.join(argv)}"))
    return result.emit(judge(a.runtime, a.stdout, a.stderr))
