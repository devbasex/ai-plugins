"""収束ループの drive が使う部品（#1142 の L0）。cross-review と cross-refactoring の `drive.py` が共有する。

止まるときの JSON の形と終了コードの表は `drive_pause.py` が持ち、ここは子のスクリプトの起動・
KEY=VALUE の読み取り・最終ステータスの決定だけを持つ。
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path


def durable_identity(tmp: Path | None, pr: int) -> str:
    """耐久の記録の鍵の元。状態の置き場（求まらなければ作業ディレクトリと PR）。"""
    return str(tmp.resolve()) if tmp is not None else f"{Path.cwd().resolve()}#{pr}"


def rerun_reason(recorded_head: str | None, current_head: str | None, forced: bool) -> str | None:
    """完了した実行の回を返さずに新しく始める理由。返してよければ `None`（#1340 の I10・決定 2・3）。

    差分を足したかは PR の head で分かる。比べられない（どちらかが無い）ときは前回の結果を返す側に倒す。
    GitHub の一時的な不調のたびに完了したループがもう 1 回回ると費用が掛かるため。#1263 が引数の比較を足す。
    """
    if forced:
        return "--reopen が渡されたため"
    if recorded_head and current_head and recorded_head != current_head:
        return f"完了の時点の head {recorded_head[:7]} から {current_head[:7]} へ進んでいるため"
    return None


def call(cmd: list[str], env: dict | None = None, cwd: str | None = None) -> tuple[int, str]:
    """スクリプトを 1 本実行し、終了コードと標準出力を返す。標準エラーはそのまま流す。"""
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", env=env, cwd=cwd)
    if p.stderr:
        sys.stderr.write(p.stderr)
    return p.returncode, p.stdout


def parse_vars(text: str) -> dict:
    """`statefile.emit` の KEY=VALUE の行を読む。識別子でない鍵と、字句として読めない行は飛ばす。"""
    out = {}
    for line in text.splitlines():
        try:
            words = shlex.split(line)
        except ValueError:
            continue
        for w in words:
            k, sep, v = w.partition("=")
            if sep and k.isidentifier():
                out[k] = v
    return out


def review_status(state: dict) -> str:
    """最後の HEAD が承認されたなら approved、それ以外は final の値（cross-refactoring の finalize が読む）。"""
    sw = state.get("sweep") or {}
    if (
        state.get("final") == "approved"
        and sw.get("verified") is True
        and (sw.get("remaining_open") or 0) == 0
        and sw.get("commit") is None
    ):
        return "approved"
    if state.get("final") == "approved":
        return "unverified"  # 最後の HEAD（スイープの修正・残り・未検証）は承認されていない
    return state.get("final") or "unknown"
