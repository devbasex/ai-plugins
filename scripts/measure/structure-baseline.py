#!/usr/bin/env python3
"""#1142 の基準: テストを除く NDF のスクリプトの行数と、同じ名前の関数の数を出す。

E1（移行の前）と E9（移行の後）を同じコマンドで打つ。数え方は E1 のまま変えない（決定 8）。

    python3 scripts/measure/structure-baseline.py [plugins/ndf] [--prs N [--prs-json <キャッシュ>]]

`--prs N` は、マージ済みの直近 N 本の Pull Request の変更ファイルの重なり（2 本以上が触ったファイルの数と、
同じファイルを触った Pull Request の組の数）を足して出す（非機能の条件の運用・保守性）。`gh` を使う。
"""
import argparse, ast, collections, itertools, json, pathlib, statistics, subprocess
ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
ap.add_argument("root", nargs="?", default="plugins/ndf")
ap.add_argument("--prs", type=int, help="マージ済みの直近 N 本の Pull Request の変更ファイルの重なりも出す")
ap.add_argument("--prs-json", type=pathlib.Path, help="--prs の取得結果のキャッシュ（あれば読み、無ければ書く）")
args = ap.parse_args()
root = pathlib.Path(args.root)
def is_test(p): return any(x in ("tests", "test") for x in p.parts) or p.name.startswith("test_")
files = [p for p in root.rglob("*") if p.suffix in (".py", ".sh") and p.is_file() and not is_test(p) and ".worktrees" not in p.parts]
lines = {p: sum(1 for _ in p.open(errors="ignore")) for p in files}
print(f"files {len(files)} / lines {sum(lines.values())} / py {sum(v for p,v in lines.items() if p.suffix=='.py')} / sh {sum(v for p,v in lines.items() if p.suffix=='.sh')}")
if files:
    print(f"median lines {statistics.median(lines.values())} / median bytes {statistics.median(p.stat().st_size for p in files)}")
print("over1000:")
for p, n in sorted(lines.items(), key=lambda x: -x[1]):
    if n > 1000: print(f"  {n:5d} {p.relative_to(root)}")
names = collections.defaultdict(list)
for p in files:
    if p.suffix != ".py": continue
    try: t = ast.parse(p.read_text(errors="ignore"))
    except SyntaxError: continue
    for n in t.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name not in ("main", "build_parser"):
            names[n.name].append((str(p.relative_to(root)), ast.dump(n)))
dup = {k: v for k, v in names.items() if len(v) > 1}
same = sum(1 for v in dup.values() if len({d for _, d in v}) < len(v))
print(f"top-level function names defined in 2+ files: {len(dup)} (with identical bodies somewhere: {same})")
for k, v in sorted(dup.items(), key=lambda x: -len(x[1]))[:25]:
    print(f"  {len(v):2d} {k}")
if args.prs:
    if args.prs_json and args.prs_json.exists():
        prs = json.loads(args.prs_json.read_text())
    else:
        prs = json.loads(subprocess.run(["gh", "pr", "list", "--state", "merged", "--limit", str(args.prs),
                                         "--json", "number,mergedAt,files"], capture_output=True, text=True,
                                        check=True).stdout)
        if args.prs_json:
            args.prs_json.write_text(json.dumps(prs, ensure_ascii=False))
    prs = sorted(prs, key=lambda p: p["mergedAt"])[-args.prs:]
    touched = {p["number"]: {f["path"] for f in p["files"]} for p in prs}
    by_file = collections.Counter(f for fs in touched.values() for f in fs)
    pairs = sum(1 for a, b in itertools.combinations(touched, 2) if touched[a] & touched[b])
    print(f"prs {len(prs)} ({prs[0]['number'] if prs else '-'}..{prs[-1]['number'] if prs else '-'}) / "
          f"files touched by 2+ prs {sum(1 for n in by_file.values() if n > 1)} / pr pairs sharing a file {pairs}")
