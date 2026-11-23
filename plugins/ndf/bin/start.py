"""エントリポイント（隣のシェルのスクリプト）が依存の環境の python で起動するスクリプト。依存の環境の欠けを確かめてから、
自分のパッケージを `python -m` と同じに呼ぶ（#2447）。

依存の環境の中身が欠けていると（入れる途中で止まった・ファイルが消えた）、依存の import がサブコマンドの途中で落ち、実際と違う
理由で止まる。ここで、環境に入っているパッケージの RECORD に載るファイルがすべてあるかを確かめ、欠けていれば入れ直しの
手順を結果 JSON（stopped・deps_missing）で出して止まる。

確かめるのは、確かめたマーカー（環境の中の `.deps-checked`）が無いか、`uv.lock` より古いときだけにする（ふだんの起動はマーカーの
更新時刻を見るだけ）。標準ライブラリだけを読む（依存が欠けていても動く）。

自分のパッケージを呼ぶ前に、エントリポイントと uv run が入れた環境の変数を、エントリポイントが写した呼び出し元の値へ戻す
（`CALLER_ENV`。#4029）。NDF の子孫（プランのステップ・レーンのセッション・cross-review の相手役など）が利用先のリポジトリで
uv run を打っても、NDF の venv を書き換えない。自分の import の道（sys.path）は起動のときに決まっているので変わらない。
NDF が自分のパッケージを子で打つときは `ndf.cli` の `ndf_command` で src を子の sys.path にだけ渡す。写しの変数は
消す（呼び出し元の `CLAUDE_PLUGIN_ROOT` は戻した値として identity.py が読む）。
"""

from __future__ import annotations

import csv
import json
import os
import runpy
import sys
from collections.abc import MutableMapping
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 呼ぶパッケージ（src/ の下。プラグイン名から切り離した固定の名前）
PACKAGE = "ndf"
STAMP = Path(sys.prefix) / ".deps-checked"
# 入れる手段によって作られないことがあるファイル（RECORD に載っても欠けを数えない）
OPTIONAL_SUFFIXES = (".pyc",)
# エントリポイントと uv run が入れる・エントリポイントが消す変数と、エントリポイントが呼び出し元の値を写す変数（無かった値は写しを持たない）
CALLER_ENV = {
    "CLAUDE_PLUGIN_ROOT": "NDF_CALLER_PLUGIN_ROOT",
    "UV_PROJECT_ENVIRONMENT": "NDF_CALLER_UV_PROJECT_ENVIRONMENT",
    "VIRTUAL_ENV": "NDF_CALLER_VIRTUAL_ENV",
    "UV_RUN_RECURSION_DEPTH": "NDF_CALLER_UV_RUN_RECURSION_DEPTH",
    "PATH": "NDF_CALLER_PATH",
    "PYTHONPATH": "NDF_CALLER_PYTHONPATH",
    # エントリポイントが消す変数（uv run --no-sync を同期した環境で動かすため。#3687）
    "UV_ISOLATED": "NDF_CALLER_UV_ISOLATED",
}
# 写しの空の値を「無かった」と読む変数（エントリポイントが無いときも空で写す）
EMPTY_IS_UNSET = frozenset({"CLAUDE_PLUGIN_ROOT"})
# 写しが無いとき、今の値を残す変数。エントリポイントが消した後に値があれば環境ファイル（UV_ENV_FILE）から uv が入れたもので、
# NDF を通らない uv run と同じに子へ渡す（写しがあれば呼び出し元の値を優先する。uv も環境ファイルで既存の値を上書きしない）
KEEP_WHEN_UNHELD = frozenset({"UV_ISOLATED"})


def recorded_files(dist) -> list[str]:
    """RECORD に載るファイルのパス。`Distribution.files` は無いファイルを落として返すので、RECORD を直に読む。"""
    text = dist.read_text("RECORD") or ""
    return [row[0] for row in csv.reader(text.splitlines()) if row and not row[0].endswith(OPTIONAL_SUFFIXES)]


def broken_distributions() -> list[str]:
    """RECORD に載るファイルが欠けたパッケージ（名前と欠けた数）。"""
    broken = []
    for dist in metadata.distributions():
        gone = sum(1 for f in recorded_files(dist) if not Path(str(dist.locate_file(f))).exists())
        if gone:
            broken.append(f"{dist.metadata['Name']}（{gone} 件）")
    return sorted(broken)


def checked() -> bool:
    try:
        return STAMP.stat().st_mtime >= (ROOT / "uv.lock").stat().st_mtime
    except OSError:  # noqa: NDF401 ファイル: マーカーが無ければ確かめる
        return False


def stop(broken: list[str]) -> int:
    """入れ直しの手順を結果 JSON で出し、終了コードの表の deps_missing の終了コードを返す。"""
    steps = f"UV_PROJECT_ENVIRONMENT={sys.prefix} uv sync --frozen --no-dev --reinstall --project {ROOT}"
    return emit_deps_missing(f"依存の環境 {sys.prefix} のファイルが欠けている: {', '.join(broken)}。入れ直す: {steps}")


def emit_deps_missing(detail: str) -> int:
    """依存の欠けを結果 JSON（stopped・deps_missing）1 行で出し、説明を標準エラーへ書き、終了コードの表の deps_missing の
    終了コードを返す。隣のシェルのスクリプト（uv を入れられないとき）もここを呼ぶ。"""
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


def restore_caller_env(env: MutableMapping[str, str]) -> None:
    """`env` を写しの呼び出し元の値へ戻し、写しの変数を消す。写しが 1 つも無ければ（エントリポイントを通らずに打った）何もしない。"""
    if not any(held in env for held in CALLER_ENV.values()):
        return
    for name, held in CALLER_ENV.items():
        value = env.pop(held, None)
        if value is None and name in KEEP_WHEN_UNHELD:
            continue
        if value is None or (not value and name in EMPTY_IS_UNSET):
            env.pop(name, None)
        else:
            env[name] = value


def verify_deps() -> int | None:
    """依存の環境の欠けを確かめる。欠けていれば入れ直しの手順を出して deps_missing の終了コードを返し、欠けが無ければ None。"""
    if checked():
        return None
    broken = broken_distributions()
    if broken:
        return stop(broken)
    try:
        STAMP.touch()
    except OSError:  # noqa: NDF401 ファイル: マーカーを書けない環境は、次の起動でも確かめる
        pass
    return None


def main() -> int:
    code = verify_deps()
    if code is not None:
        return code
    restore_caller_env(os.environ)
    runpy.run_module(PACKAGE, run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
