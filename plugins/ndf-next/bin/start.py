"""エントリポイント（隣のシェルのスクリプト）が依存の環境の python で起こすスクリプト。依存の環境の欠けを確かめてから、
自分のパッケージを `python -m` と同じに呼ぶ（#2447）。

依存の環境の中身が欠けていると（入れる途中で止まった・ファイルが消えた）、依存の import が副命令の途中で落ち、実際と違う
理由で止まる。ここで、環境に入っているパッケージの RECORD に載るファイルがすべてあるかを確かめ、欠けていれば入れ直しの
手順を結果 JSON（stopped・deps_missing）で出して止まる。

確かめるのは、確かめた印（環境の中の `.deps-checked`）が無いか、`uv.lock` より古いときだけにする（ふだんの起動は印の
更新時刻を見るだけ）。標準ライブラリだけを読む（依存が欠けていても動く）。
"""

from __future__ import annotations

import json
import runpy
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 呼ぶパッケージ（src/ の下。プラグイン名から切り離した固定の名前）
PACKAGE = "ndf"
STAMP = Path(sys.prefix) / ".deps-checked"
# 入れる手段によって作られないことがあるファイル（RECORD に載っても欠けを数えない）
OPTIONAL_SUFFIXES = (".pyc",)


def broken_distributions() -> list[str]:
    """RECORD に載るファイルが欠けたパッケージ（名前と欠けた数）。"""
    broken = []
    for dist in metadata.distributions():
        files = [f for f in dist.files or () if not str(f).endswith(OPTIONAL_SUFFIXES)]
        gone = sum(1 for f in files if not Path(str(dist.locate_file(f))).exists())
        if gone:
            broken.append(f"{dist.metadata['Name']}（{gone} 件）")
    return sorted(broken)


def checked() -> bool:
    try:
        return STAMP.stat().st_mtime >= (ROOT / "uv.lock").stat().st_mtime
    except OSError:  # noqa: NDF401 ファイル: 印が無ければ確かめる
        return False


def stop(broken: list[str]) -> int:
    """入れ直しの手順を結果 JSON で出し、終了コードの表の deps_missing の終了コードを返す。"""
    steps = f"UV_PROJECT_ENVIRONMENT={sys.prefix} uv sync --frozen --no-dev --reinstall --project {ROOT}"
    detail = f"依存の環境 {sys.prefix} のファイルが欠けている: {', '.join(broken)}。入れ直す: {steps}"
    report = {
        "tool": "cli",
        "status": "stopped",
        "summary": detail,
        "items": [],
        "metrics": {},
        "cause": {"kind": "deps_missing", "detail": detail},
    }
    print(json.dumps(report, ensure_ascii=False))
    print(detail, file=sys.stderr)
    rows = json.loads((ROOT / "contracts" / "facts" / "exit-codes.json").read_text(encoding="utf-8"))["rows"]
    return next(row["code"] for row in rows if row["id"] == "deps_missing")


def main() -> int:
    if not checked():
        broken = broken_distributions()
        if broken:
            return stop(broken)
        try:
            STAMP.touch()
        except OSError:  # noqa: NDF401 ファイル: 印を書けない環境は、次の起動でも確かめる
            pass
    runpy.run_module(PACKAGE, run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
