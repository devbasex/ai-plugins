"""試走（T2 #3411）の静的な確かめ。対応表の行ごとに、ndf-next だけで機能が届くかを置き場と本文から判定する。

ndf-next の Skill・エージェント・hook の本文が旧いプラグインのスクリプト（対応表の `now`）をまだ呼んでいれば、その行は
ndf-next だけでは動かない（`failed`）。呼んでいなくても、試走のスプリントがその行まで届かなかった行は `not_run` にする。
試走で実際に通した行は、呼び手が `passed` に渡す（`judge` の `passed`）。

    依存の環境で: python -m ndf.parity.trial.probe [--write]

`--write` は対応表の `trial` の列を書き換える。書かないときは判定の一覧を標準出力へ出すだけである。
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from ndf.parity import table

ROOT = Path(__file__).resolve().parents[4]
REPO = ROOT.parents[1]
# 旧いスクリプトを呼ぶ本文を探す置き場は、本文を移した子（Skill・エージェント・hook・配る表）の places から引く
TEXT_CHILDREN = ("S1a", "S1b")
# 試走の記録（同じディレクトリの JSON）
RECORD = "trial/2026-11-15.json"
ROW_PREFIX = '    {"id": '
STALLED = "試走のスプリントは手順 0 で $SCRIPTS が旧いプラグインの scripts/ へ解決したため、この行の振る舞いまで ndf-next だけで流せていない"


def texts(places: Mapping[str, Any], root: Path = ROOT) -> dict[str, str]:
    """本文の置き場の文字のファイル（プラグインの根からの相対 → 中身）。Python のコードは本文に数えない。"""
    found = {}
    for place in sorted({p for child in TEXT_CHILDREN for p in places[child]}):
        for path in sorted((root / place).rglob("*")):
            if path.is_file() and not path.suffix.startswith(".py"):
                found[str(path.relative_to(root))] = path.read_text(encoding="utf-8")
    return found


def _pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w.-]){re.escape(name)}(?![\w-])")


def callers(name: str, bodies: Mapping[str, str], under: str = "") -> list[str]:
    """名前（旧いスクリプトのファイル名）を書いた本文のパス。`under` があればその下だけ。"""
    pat = _pattern(name)
    return sorted(p for p, body in bodies.items() if p.startswith(under) and pat.search(body))


def old_scripts(rows: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """旧いプラグインのスクリプト（根の scripts/ と Skill の scripts/ の下）のファイル名 → 対応表の行。"""
    return {Path(r["now"]).name: r for r in rows if _is_script(r["now"])}


def _is_script(now: str) -> bool:
    return (now.startswith("scripts/") or "/scripts/" in now) and Path(now).suffix != ""


def _failed(reason: str) -> dict[str, str]:
    return {"result": "failed", "reason": reason, "record": RECORD}


def _not_run(reason: str = STALLED) -> dict[str, str]:
    return {"result": "not_run", "reason": reason, "record": RECORD}


def _missing_places(row: Mapping[str, Any], places: Mapping[str, Any], root: Path) -> list[str]:
    to = row.get("to")
    return [p for p in places[to] if not (root / p).exists()] if to else []


def _skill_calls(under: str, bodies: Mapping[str, str], scripts: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """`under` の下の本文が呼ぶ旧いスクリプトを「名前（行き先）」で並べる。"""
    found = []
    for name, row in sorted(scripts.items()):
        if callers(name, bodies, under):
            found.append(f"{name}（{row['to'] if 'to' in row else '作らない'}）")
    return found


def _by_path(row: Mapping[str, Any], bodies: Mapping[str, str], scripts: Mapping[str, Mapping[str, Any]], root: Path) -> dict[str, str] | None:
    """Skill・エージェントの行。本文が無いか、旧いスクリプトを呼べば `failed`。"""
    name = row["id"].split(":", 1)[1]
    under = f"skills/{name}/" if row["kind"] == "skill" else f"agents/{name}.md"
    if not (root / under).exists():
        return _failed(f"ndf-next に {under} が無い（行き先 {row['to']}）")
    calls = _skill_calls(under, bodies, scripts)
    if calls:
        return _failed(f"本文が旧いプラグインのスクリプトを呼ぶ: {'・'.join(calls)}。括弧は新しいエントリポイントの副命令へ置き換える行き先の子")
    return None


def _by_script(row: Mapping[str, Any], bodies: Mapping[str, str]) -> dict[str, str] | None:
    """旧いスクリプトの行。ndf-next の本文がまだ呼べば `failed`。"""
    who = callers(Path(row["now"]).name, bodies)
    if not who:
        return None
    to = row.get("to")
    where = f"行き先 {to} が新しいエントリポイントの副命令に置き換えていない" if to else "作らない行なのに呼び手が残る"
    return _failed(f"ndf-next の {len(who)} ファイルがまだ旧い {row['now']} を呼ぶ（例 {who[0]}）。{where}")


def _by_workflow(row: Mapping[str, Any], repo: Path, scripts: Mapping[str, Mapping[str, Any]]) -> dict[str, str] | None:
    """workflow の行。旧いスクリプトの名前を書いていれば `failed`。"""
    body = {row["now"]: (repo / row["now"]).read_text(encoding="utf-8")}
    named = [name for name in sorted(scripts) if callers(name, body)]
    if named:
        return _failed(f"{row['now']} が旧いプラグインのスクリプトを直に呼ぶ: {'・'.join(named)}（行き先 {row['to']}）")
    return None


def judge_row(row: Mapping[str, Any], ctx: Mapping[str, Any]) -> dict[str, str]:
    """1 行の試走の結果。`ctx` は places・bodies・scripts・root・repo・passed を持つ。"""
    if row["id"] in ctx["passed"]:
        return {"result": "passed", "record": RECORD}
    missing = _missing_places(row, ctx["places"], ctx["root"])
    if missing:
        return _failed(f"行き先 {row['to']} の置き場が無い: {'・'.join(missing)}")
    kind = row["kind"]
    found = None
    if kind in ("skill", "agent"):
        found = _by_path(row, ctx["bodies"], ctx["scripts"], ctx["root"])
    elif kind == "workflow":
        found = _by_workflow(row, ctx["repo"], ctx["scripts"])
    elif _is_script(row["now"]):
        found = _by_script(row, ctx["bodies"])
    if found:
        return found
    if "not_built" in row:
        return {"result": "passed", "reason": "作らない行。ndf-next の本文に呼び手が無い", "record": RECORD}
    return _not_run()


def judge(features: Mapping[str, Any], passed: Iterable[str] = (), root: Path = ROOT, repo: Path = REPO) -> dict[str, dict[str, str]]:
    """行の id → 試走の結果。`passed` は試走で実際に通した行の id。"""
    ctx = {
        "places": features["places"],
        "bodies": texts(features["places"], root),
        "scripts": old_scripts(features["rows"]),
        "root": root,
        "repo": repo,
        "passed": set(passed),
    }
    return {r["id"]: judge_row(r, ctx) for r in features["rows"]}


def write_trials(path: Path, results: Mapping[str, Mapping[str, str]]) -> None:
    """対応表の行（1 行に 1 つの JSON）の `trial` だけを書き換える。ほかの行と並びはそのまま残す。"""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(ROW_PREFIX):
            comma = "," if line.endswith(",") else ""
            row = json.loads(line.removesuffix(","))
            row["trial"] = dict(results[row["id"]])
            line = "    " + json.dumps(row, ensure_ascii=False) + comma
        out.append(line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="対応表の trial の列を書き換える")
    args = parser.parse_args(argv)
    features = table.load("features")
    record = json.loads((table.HERE / RECORD).read_text(encoding="utf-8"))
    results = judge(features, record["passed"])
    if args.write:
        write_trials(table.HERE / "features.json", results)
    counts = Counter(t["result"] for t in results.values())
    print(json.dumps({"tool": "parity-trial", "status": "ok", "summary": f"{len(results)} 行を判定した", "items": [], "metrics": counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
