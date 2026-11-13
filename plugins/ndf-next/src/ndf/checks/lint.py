"""ruff で数える兆候（決定 36: 既存の規則で見られるものを自作しない）。

`rules.json` の `smells` のうち `by` が `ruff` の規則を、見るファイルすべてへ 1 回の起動で当てる。
しきい値は `rules.json` の `ruff.config` にあり、`--config` で 1 つずつ渡す。ruff の `# noqa` は読ませない
（`--ignore-noqa`）。境界の印は NDF の形で、`marks` が規則ごとに効くかを決めるためである。
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from ndf.checks.model import CheckError, Source

# ruff の終了コード: 0 は指摘なし、1 は指摘あり。ほかは起動や設定の誤り
EXIT_FOUND = 1


def codes(rules: dict[str, Any]) -> list[str]:
    """ruff で数える規則の名前。"""
    return sorted(code for code, rule in rules["smells"].items() if rule.get("by") == "ruff")


def command(rules: dict[str, Any], paths: list[Path]) -> list[str]:
    conf = rules["ruff"]
    settings = [f"--config={key} = {json.dumps(value)}" for key, value in sorted(conf["config"].items())]
    flags = ["--preview"] if conf["preview"] else []
    select = ",".join(codes(rules))
    head = [sys.executable, "-m", "ruff", "check", "--isolated", "--no-cache", "--ignore-noqa", "--output-format=json"]
    return [*head, *flags, f"--select={select}", *settings, *map(str, paths)]


def scan(sources: list[Source], rules: dict[str, Any]) -> dict[Path, list[tuple[int, str]]]:
    """ファイルごとの `(行, 規則)`。ruff を起動できない・結果を読めないときは `CheckError`。"""
    if not sources:
        return {}
    try:
        done = subprocess.run(command(rules, [s.path for s in sources]), capture_output=True, text=True, check=False)
    except OSError as exc:
        raise CheckError(f"ruff を起動できない: {exc}") from exc
    if done.returncode not in (0, EXIT_FOUND):
        raise CheckError(f"ruff が終了コード {done.returncode} で止まった: {done.stderr.strip()[:300]}")
    try:
        found = json.loads(done.stdout)
    except ValueError as exc:
        raise CheckError(f"ruff の結果を読めない: {exc}") from exc
    hits: dict[Path, list[tuple[int, str]]] = defaultdict(list)
    for item in found:
        hits[Path(item["filename"]).resolve()].append((item["location"]["row"], item["code"]))
    return hits
