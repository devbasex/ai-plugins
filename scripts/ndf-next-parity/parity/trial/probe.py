"""試走（T2 #3411）の静的な確かめ。対応表の行ごとに、ndf-next だけで機能が届くかを保存先と本文から判定する。

ndf-next の Skill・エージェント・hook の本文が旧いプラグインのスクリプト（対応表の `now`）をまだ呼んでいれば、その行は
ndf-next だけでは動かない（`failed`）。呼んでいなくても、試走のスプリントがその行まで届かなかった行は `not_run` にする。
試走で実際に通した行は、記録（`trial/<日付>.json`）の `passed` に載せる。記録はすべて読み、行が最後に載った記録を
その行の `record` にする。どの記録にも載らない行の `record` は最新の記録である。

同じ名前の旧いスクリプト（`skills/cross-review/scripts/drive.py` と `skills/cross-refactoring/scripts/drive.py` など）は、
名前でなく `now` のパスで区別する。本文が名前だけで書いていれば、本文の置かれた Skill の下のスクリプト・根の `scripts/`・
同じ名前が 1 つだけのときのそれ、の順に解決する（`resolve`）。

    ndf-next の依存の環境で（scripts/ndf-next-parity から）: python -m parity.trial.probe [--write] [--plugin-root <根>]

`--write` は対応表の `trial` の列を書き換える。書かないときは判定の一覧を標準出力へ出すだけである。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from parity import table

# リポジトリの根と、既定のプラグインの根（対応表の locations と本文はプラグインの根からの相対）
REPO = Path(__file__).resolve().parents[4]
PLUGIN_ROOT = REPO / "plugins" / "ndf-next"
# 旧いスクリプトを呼ぶ本文を探す保存先は、本文を移した子（Skill・エージェント・hook・配る表）の locations から引く
TEXT_CHILDREN = ("S1a", "S1b")
# 試走の記録の保存先（対応表のディレクトリからの相対。中の `<日付>.json` を名前の順に読む）
RECORDS = "trial"
ROW_PREFIX = '    {"id": '
STALLED = (
    "試走のスプリントがこの行の振る舞いまで届いておらず、ndf-next だけで通したことをまだ確かめていない（止まった所は最新の記録の runs）"
)


def texts(locations: Mapping[str, Any], root: Path) -> dict[str, str]:
    """本文の保存先の文字のファイル（プラグインの根からの相対 → 中身）。Python のコードは本文に数えない。"""
    found = {}
    for location in sorted({p for child in TEXT_CHILDREN for p in locations[child]}):
        for path in sorted((root / location).rglob("*")):
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
    """旧いプラグインのスクリプト（根の scripts/ と Skill の scripts/ の下）の `now` のパス → 対応表の行。"""
    return {r["now"]: r for r in rows if _is_script(r["now"])}


def resolve(path: str, body: str, scripts: Mapping[str, Mapping[str, Any]]) -> set[str]:
    """本文（`path` に置かれた `body`）が呼ぶ旧いスクリプトの `now` のパス。

    パスで書いた呼び出しはそのパスへ、名前だけの呼び出しは本文の置かれた Skill の下・根の `scripts/`・同じ名前が
    1 つだけのときのそれ、の順に解決する。どれでも決まらなければ、同じ名前のすべてを呼ぶとみなす（見落とさない側へ倒す）。
    """
    home = "/".join(path.split("/")[:2]) + "/scripts/" if path.startswith("skills/") else None
    by_name: dict[str, list[str]] = {}
    for now in scripts:
        by_name.setdefault(Path(now).name, []).append(now)
    found = set()
    for name, nows in by_name.items():
        if not _pattern(name).search(body):
            continue
        written = [now for now in nows if _pattern(now).search(body)]
        if written:
            found.update(written)
            continue
        found.update(_by_name(name, nows, home))
    return found


def _by_name(name: str, nows: list[str], home: str | None) -> list[str]:
    for candidate in (f"{home}{name}" if home else None, f"scripts/{name}"):
        if candidate in nows:
            return [candidate]
    return nows


def _calls(bodies: Mapping[str, str], scripts: Mapping[str, Mapping[str, Any]]) -> dict[str, set[str]]:
    """本文のパス → その本文が呼ぶ旧いスクリプトの `now`。"""
    return {p: resolve(p, body, scripts) for p, body in bodies.items()}


def _label(now: str, scripts: Mapping[str, Mapping[str, Any]]) -> str:
    """理由に書く名前。同じ名前が他にもあれば `now` のパスで書く。"""
    name = Path(now).name
    return name if sum(Path(n).name == name for n in scripts) == 1 else now


def _is_script(now: str) -> bool:
    return (now.startswith("scripts/") or "/scripts/" in now) and Path(now).suffix != ""


def _failed(reason: str) -> dict[str, str]:
    return {"result": "failed", "reason": reason}


def _not_run(reason: str = STALLED) -> dict[str, str]:
    return {"result": "not_run", "reason": reason}


def _missing_locations(row: Mapping[str, Any], locations: Mapping[str, Any], root: Path) -> list[str]:
    to = row.get("to")
    return [p for p in locations[to] if not (root / p).exists()] if to else []


def _skill_calls(under: str, calls: Mapping[str, set[str]], scripts: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """`under` の下の本文が呼ぶ旧いスクリプトを「名前（移行先）」で並べる。"""
    nows = {now for p, called in calls.items() if p.startswith(under) for now in called}
    labels = sorted((_label(now, scripts), scripts[now]) for now in nows)
    return [f"{label}（{row['to'] if 'to' in row else '作らない'}）" for label, row in labels]


def _by_path(
    row: Mapping[str, Any], calls: Mapping[str, set[str]], scripts: Mapping[str, Mapping[str, Any]], root: Path
) -> dict[str, str] | None:
    """Skill・エージェントの行。本文が無いか、旧いスクリプトを呼べば `failed`。"""
    name = row["id"].split(":", 1)[1]
    under = f"skills/{name}/" if row["kind"] == "skill" else f"agents/{name}.md"
    if not (root / under).exists():
        return _failed(f"ndf-next に {under} が無い（移行先 {row['to']}）")
    calls = _skill_calls(under, calls, scripts)
    if calls:
        return _failed(
            f"本文が旧いプラグインのスクリプトを呼ぶ: {'・'.join(calls)}。括弧は新しいエントリポイントのサブコマンドへ置き換える移行先の子"
        )
    return None


def _by_script(row: Mapping[str, Any], calls: Mapping[str, set[str]]) -> dict[str, str] | None:
    """旧いスクリプトの行。ndf-next の本文がまだ呼べば `failed`。"""
    who = sorted(p for p, called in calls.items() if row["now"] in called)
    if not who:
        return None
    to = row.get("to")
    where = f"移行先 {to} が新しいエントリポイントのサブコマンドに置き換えていない" if to else "作らない行なのに呼び手が残る"
    return _failed(f"ndf-next の {len(who)} ファイルがまだ旧い {row['now']} を呼ぶ（例 {who[0]}）。{where}")


def _by_workflow(row: Mapping[str, Any], repo: Path, scripts: Mapping[str, Mapping[str, Any]]) -> dict[str, str] | None:
    """workflow の行。旧いスクリプトの名前を書いていれば `failed`。"""
    called = resolve(row["now"], (repo / row["now"]).read_text(encoding="utf-8"), scripts)
    named = sorted(_label(now, scripts) for now in called)
    if named:
        return _failed(f"{row['now']} が旧いプラグインのスクリプトを直に呼ぶ: {'・'.join(named)}（移行先 {row['to']}）")
    return None


def judge_row(row: Mapping[str, Any], ctx: Mapping[str, Any]) -> dict[str, str]:
    """1 行の試走の結果（`record` は除く）。`ctx` は locations・calls・scripts・root・repo・passed を持つ。

    静的な確かめを記録より先に行う。記録の `passed` は `failed` でない行にだけ効く（旧いプラグインに頼って通った行・
    通した後で本文が旧い呼び出しに戻った行を `passed` に残さない）。
    """
    missing = _missing_locations(row, ctx["locations"], ctx["root"])
    if missing:
        return _failed(f"移行先 {row['to']} の保存先が無い: {'・'.join(missing)}")
    kind = row["kind"]
    found = None
    if kind in ("skill", "agent"):
        found = _by_path(row, ctx["calls"], ctx["scripts"], ctx["root"])
    elif kind == "workflow":
        found = _by_workflow(row, ctx["repo"], ctx["scripts"])
    elif _is_script(row["now"]):
        found = _by_script(row, ctx["calls"])
    if found:
        return found
    if row["id"] in ctx["passed"]:
        return {"result": "passed"}
    if "not_built" in row:
        return {"result": "passed", "reason": "作らない行。ndf-next の本文に呼び手が無い"}
    return _not_run()


def judge(
    features: Mapping[str, Any],
    passed: Mapping[str, str] | Iterable[str] = (),
    record: str = "",
    root: Path = PLUGIN_ROOT,
    repo: Path = REPO,
) -> dict[str, dict[str, str]]:
    """行の id → 試走の結果。`passed` は試走で実際に通した行の id（→ 通した記録）。`record` はほかの行の記録。"""
    passed = passed if isinstance(passed, Mapping) else dict.fromkeys(passed, record)
    scripts = old_scripts(features["rows"])
    ctx = {
        "locations": features["locations"],
        "calls": _calls(texts(features["locations"], root), scripts),
        "scripts": scripts,
        "root": root,
        "repo": repo,
        "passed": passed,
    }
    results = {}
    for r in features["rows"]:
        got = judge_row(r, ctx)
        results[r["id"]] = {**got, "record": passed[r["id"]] if got["result"] == "passed" and r["id"] in passed else record}
    return results


def records(here: Path) -> tuple[dict[str, str], str]:
    """試走の記録をすべて読み、（通した行の id → 最後に載った記録, 最新の記録）を返す。記録の名前は `trial/<日付>.json`。"""
    passed: dict[str, str] = {}
    latest = ""
    for path in sorted((here / RECORDS).glob("*.json")):
        latest = f"{RECORDS}/{path.name}"
        for row_id in json.loads(path.read_text(encoding="utf-8"))["passed"]:
            passed[row_id] = latest
    if not latest:
        raise SystemExit(f"試走の記録が無い: {here / RECORDS}/*.json")
    return passed, latest


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
    parser.add_argument(
        "--plugin-root", type=Path, default=PLUGIN_ROOT, help="本文を読むプラグインの根（既定はリポジトリの plugins/ndf-next）"
    )
    args = parser.parse_args(argv)
    if not args.plugin_root.is_dir():
        print(f"プラグインの根が無い: {args.plugin_root}", file=sys.stderr)
        return 2
    features = table.load("features")
    passed, latest = records(table.HERE)
    results = judge(features, passed, latest, root=args.plugin_root)
    if args.write:
        write_trials(table.HERE / "features.json", results)
    counts = Counter(t["result"] for t in results.values())
    print(
        json.dumps(
            {"tool": "parity-trial", "status": "ok", "summary": f"{len(results)} 行を判定した", "items": [], "metrics": counts},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
