# #3960 の確かめのコマンド: 参照・子の表・構文木・値が受け入れ条件のとおりかを終了コードで返す

[issue-3960-design.md](./issue-3960-design.md) の決定 4 とテスト設計が使う 4 つのコマンドである。どれもリポジトリの根で打つ。`<base>` は変更の前のコミットで、`main` を取り込んだ後は `git merge-base HEAD origin/main` である。

## 参照の確かめ（条件 2・3・5・6）

下を `/tmp/refs.py` に置く。`list` で一覧のひな形を作り、分類を埋めてから `check` を打つ。`main` を取り込んで候補が増えたら、`check` が挙げた行を一覧に足す。

```bash
python3 /tmp/refs.py list <base> - > /tmp/refs.tsv
python3 /tmp/refs.py check <base> /tmp/refs.tsv
```

```python
import collections, re, subprocess, sys
mode, base, ledger = sys.argv[1], sys.argv[2], sys.argv[3]
NL = r"[ \t]*(?:\n[ \t]*#?[ \t]*)?"
SEP = NL + r"(?:[・、,，〜～\-–]|と|及び|および)" + NL
RUN = re.compile(r"決定" + NL + r"(\d+)((?:" + SEP + r"(?:決定" + NL + r")?\d+)*)")
PATH = re.compile(r"(?<!docs/glossary/)ndf-rebuild[a-z-]*\.md")
FORM = re.compile(r"docs/specifications/(ndf-rebuild(?:-[a-z]+)*\.md) の決定 (\d+(?:・\d+)*)(?!\d)(?!" + SEP + r"(?:決定" + NL + r")?\d)")
SECTION = re.compile(r"docs/specifications/ndf-rebuild(?:-[a-z]+)*\.md の「[^」\n]+」")
KINDS = ("書き換え", "別の設計", "移設対象外", "決められない")
def git(*a):
    return subprocess.run(["git", *a], capture_output=True, check=True).stdout
def texts(rev):
    names = git("ls-tree", "-rz", "--name-only", rev, "plugins/ndf") if rev else git("ls-files", "-z", "plugins/ndf")
    for name in names.decode().split("\0"):
        try:
            if name:
                yield name, (git("show", f"{rev}:{name}") if rev else open(name, "rb").read()).decode()
        except (UnicodeDecodeError, OSError):
            continue
def runs(name, text):
    for m in RUN.finditer(text):
        parts = re.findall(r"(\D*)(\d+)", m[0])
        nums = []
        for sep, n in parts:
            nums += range(int(nums[-1]) + 1, int(n) + 1) if nums and re.search("[〜～]", sep) else [int(n)]
        line = text[text.rfind("\n", 0, m.start()) + 1:].split("\n")[0]
        for n in nums:
            yield name, line.strip().replace("\t", " "), str(n)
found = collections.Counter()
if mode == "list":
    for name, text in texts(base):
        for row in runs(name, text):
            print("\t".join([*row, "", "", ""]))
    sys.exit()
held = {}
def decisions(name):
    if name not in held:
        try:
            held[name] = set(re.findall(r"^#{2,4} 決定 (\d+)", open("docs/specifications/" + name, encoding="utf-8").read(), re.M))
        except OSError:
            held[name] = set()
    return held[name]
bad = []
rows = [l.split("\t") for l in open(ledger, encoding="utf-8").read().splitlines()]
for r in rows:
    if len(r) != 6 or r[3] not in KINDS or not r[5] or (r[3] == "書き換え") != bool(r[4]):
        bad.append(f"一覧の行の形: {r}")
        r += [""] * (6 - len(r))
want = collections.Counter(row for name, text in texts(base) for row in runs(name, text))
have = collections.Counter((r[0], r[1], r[2]) for r in rows)
bad += [f"候補が一覧に無い: {k}" for k in want - have] + [f"候補に無い一覧の行: {k}" for k in have - want]
new_want = collections.Counter((r[0], r[4]) for r in rows if r[3] == "書き換え")
old_want = collections.Counter((r[0], r[2]) for r in rows if r[3] != "書き換え")
new_have, old_have = collections.Counter(), collections.Counter()
for name, text in texts(None):
    spans = []
    for m in FORM.finditer(text):
        spans.append(m.span())
        for n in m[2].split("・"):
            new_have[name, f"{m[1]}#{n}"] += 1
            if n not in decisions(m[1]):
                bad.append(f"決定が無い: {name} {m[0]}")
    spans += [m.span() for m in SECTION.finditer(text)]
    for m in PATH.finditer(text):
        if not any(a <= m.start() < b for a, b in spans):
            bad.append(f"形が違う: {name} {text[m.start():m.start() + 60]!r}")
    rest = list(text)
    for a, b in spans:
        rest[a:b] = "\0" * (b - a)
    for _, _, n in runs(name, "".join(rest)):
        old_have[name, n] += 1
for label, w, h in (("新しい形", new_want, new_have), ("残した参照", old_want, old_have)):
    bad += [f"{label}が足りない: {k} {c} 件" for k, c in (w - h).items()] + [f"{label}が余る: {k} {c} 件" for k, c in (h - w).items()]
print("\n".join(bad))
print("参照", sum(new_have.values()), "件・残した参照", sum(old_have.values()), "件・違反", len(bad), "件")
sys.exit(bool(bad))
```

候補は、`決定` に続く番号の並びを番号ごとに数える（`・`・`、`・`〜`・改行・2 つ目の「決定」を挟む並びも拾い、範囲は展開する）。番号でない数を拾った候補（例: 「決定 14 と 2026-11-20」）は、根拠を添えて `別の設計` に分ける。`check` は次の 5 つを見る。

1. 候補と一覧が過不足なく合う
2. 確定仕様のパスを書く箇所が、番号の形かセクションの名前の形だけである
3. 指す決定の見出しがある
4. 書き換えた参照の数が、ファイルと番号ごとに一覧と合う
5. 残した参照の数が、ファイルと番号ごとに一覧と合う

`ed88d9b4a` の候補は 716 件である。`identity.py` の「決定 75・決定 79」を書き換えた形で確かめた結果は次のとおりである。

| 書き換えた後の文 | 終了コード |
| --- | ---: |
| `docs/specifications/ndf-rebuild.md の決定 75・79`（一覧どおり） | 0 |
| 上と同じで、ほかの行の 1 件を `移設対象外` に分けて残した | 0 |
| 旧い形のまま残した | 1 |
| `…の決定 75・決定 999`・`…の決定 75、999`・`…の決定 75〜79`・`…の決定 75・999` | 1 |
| パスと番号の間、番号と番号の間で改行した | 1 |
| 1 つだけ旧い形（`…の決定 75・#1142 の決定 79`）・無い文書 | 1 |
| 一覧から候補を 1 行落とした・別の行の番号も変えた | 1 |

## 子の表の確かめ（条件 7）

下を `/tmp/kids.py` に置き、`python3 /tmp/kids.py <base>` を打つ。`children` と `tables` は変更の前から全項目が変わらないこと、先に要る子・順番・レーン・Issue は変更の前の「子の記号」の 52 件とも合うことを見る。

```python
import json, re, subprocess, sys
base = sys.argv[1]
show = lambda p: subprocess.run(["git", "show", f"{base}:{p}"], capture_output=True, text=True, check=True).stdout
KIDS, SPEC = "plugins/ndf/src/ndf/checks/children.json", "docs/specifications/ndf-rebuild.md"
old, new = json.loads(show(KIDS)), json.load(open(KIDS, encoding="utf-8"))
def table(text):
    sec = text[text.index("## 子の記号"):text.index("## 決定と理由")]
    cells = [[c.strip() for c in line.strip("|").split("|")] for line in sec.splitlines() if line.startswith("|")]
    return cells[0], {c[0]: c for c in cells[2:]}
bad = []
for key in ("children", "tables"):
    for k in sorted(set(old[key]) | set(new[key])):
        if json.dumps(old[key].get(k)) != json.dumps(new[key].get(k)):
            bad.append(f"{key} {k}: {old[key].get(k)} → {new[key].get(k)}")
kids = new["children"]
for k, c in table(show(SPEC))[1].items():
    if k in ("R0", "R1"):
        continue
    want = {"issue": int(c[1].strip("#")), "needs": sorted([] if c[3] == "無し" else c[3].split("・")), "order": int(c[4]), "lane": c[5]}
    got = {**{f: kids.get(k, {}).get(f) for f in want}, "needs": sorted(kids.get(k, {}).get("needs", []))}
    if want != got:
        bad.append(f"変更の前の確定仕様と違う {k}: {want} → {got}")
head, rows = table(open(SPEC, encoding="utf-8").read())
if head != ["記号", "Issue", "中身"]:
    bad.append(f"確定仕様の列: {head}")
if set(rows) != set(kids):
    bad.append(f"記号の違い: {sorted(set(rows) ^ set(kids))}")
bad += [f"Issue の違い: {k}" for k in set(rows) & set(kids) if rows[k][1] != f"#{kids[k]['issue']}"]
if [k for k, v in kids.items() if "ndf.core.refs" in v["packages"]] != ["C4"]:
    bad.append("ndf.core.refs のオーナーが C4 だけでない")
if new["tables"].get("contracts/limits/") != ["D1", "T1+", "M3a"]:
    bad.append("contracts/limits/ の書き込み元が D1・T1+・M3a でない")
print("\n".join(bad))
print("子", len(kids), "件・違反", len(bad), "件")
sys.exit(bool(bad))
```

- 変更の前（`<base>` と同じ形）: 1（列と、`M3b+`・`R0`・`R1`・`X9` の記号の違い）
- 「子の記号」だけを決定 1 の形にしたとき: 0（`子 54 件・違反 0 件`）
- その上で C2 の `needs`・C4 の `packages`・`contracts/limits/` の書き込み元・X9 を 1 つずつ削ったとき: どれも 1

## 構文木の確かめ（条件 10）

```bash
python3 - <base> <<'PY'
import ast, subprocess, sys
base = sys.argv[1]
def tree(text):
    t = ast.parse(text)
    for n in ast.walk(t):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and ast.get_docstring(n, clean=False) is not None:
            n.body[0].value.value = ""
    return ast.dump(t)
run = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True, check=True).stdout
names = run("ls-files", "--", "plugins/ndf/*.py").split()
old = {n for n in run("ls-tree", "-r", "--name-only", base, "plugins/ndf").split("\n") if n.endswith(".py")}
bad = sorted(set(names) ^ old)
for n in sorted(set(names) & old):
    before, after = run("show", f"{base}:{n}"), open(n, encoding="utf-8").read()
    if before != after and tree(before) != tree(after):
        bad.append(n)
print("\n".join(bad))
print("違い", len(bad), "件")
sys.exit(bool(bad))
PY
```

- 変更の前: `違い 0 件`
- docstring を 1 行替えたとき: 0 件
- `llm.py` の定数を 1 つ替えたとき: 1 件

## `.py` のほかの値の確かめ（条件 10）

下を `/tmp/values.py` に置き、`python3 /tmp/values.py <base>` を打つ。許す差は次だけで、ほかの値・ファイルの追加と削除・実行権の変更は違反にする。

| ファイル | 許す差 |
| --- | --- |
| `contracts/` の JSON | `summary` の文 |
| `rules.json` | `ndf.adapters.llm.ATTEMPTS` の `reason` の文と、`review_items_source` の決めた替え先 |
| `children.json` | 頭の `summary` の文と、`review_items` の対応表の名前の決めた替え先 |
| `pyproject.toml` | コメント |
| `bin/ndf-claude` | コメントだけの行（1 行目を除く） |
| `script-structure.yml` | `REVIEW_ITEMS_PATHS` の決めた替え先 |

```python
import json, re, subprocess, sys, tomllib
base = sys.argv[1]
run = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True, check=True).stdout
WF, CK = ".github/workflows/script-structure.yml", "plugins/ndf/src/ndf/checks/"
OLD, NEW = "issues/issue-1142-design-review-routing.md", "docs/specifications/ndf-review-items.md"
OLD11, NEW11 = "issue-3388-design-routing.md", "ndf-review-items-11x.md"
def mask(v):
    if isinstance(v, dict):
        return {k: "" if k == "summary" and isinstance(x, str) else mask(x) for k, x in v.items()}
    return [mask(x) for x in v] if isinstance(v, list) else v
def loaded(name, text, before):
    v = json.loads(text)
    if name.startswith("plugins/ndf/contracts/"):
        return mask(v)
    if name == CK + "rules.json":
        for r in v["facts"]["limit_constants"]:
            if r["name"] == "ndf.adapters.llm.ATTEMPTS":
                r["reason"] = ""
        if before and v["spec"]["review_items_source"] == OLD:
            v["spec"]["review_items_source"] = NEW
    if name == CK + "children.json":
        v["summary"] = ""
        for i in v["review_items"] if before else []:
            i["routed_to"] = [NEW11 if r == OLD11 else r for r in i["routed_to"]]
    return v
def value(name, text, before):
    if name.endswith(".json"):
        return json.dumps(loaded(name, text, before))
    if name.endswith(".toml"):
        return repr(tomllib.loads(text))
    if name == WF:
        return text.replace(f"{OLD} issues/{OLD11}", f"{NEW} docs/specifications/{NEW11}") if before else text
    if name == "plugins/ndf/bin/ndf-claude":
        return [l for i, l in enumerate(text.splitlines()) if i == 0 or not re.match(r"\s*#", l)]
    return text
bad = []
for line in run("diff", "--raw", "--no-renames", base, "--", "plugins/ndf", WF).splitlines():
    meta, name = line.split("\t")
    modes = meta.lstrip(":").split()
    if modes[4] != "M" or modes[0] != modes[1]:
        bad.append(f"追加・削除・モードの変更: {name}")
    elif not name.endswith((".py", ".md")):
        if value(name, run("show", f"{base}:{name}"), True) != value(name, open(name, encoding="utf-8").read(), False):
            bad.append(f"説明のほかの値が違う: {name}")
print("\n".join(bad))
print("違い", len(bad), "件")
sys.exit(bool(bad))
```

- 変更の前: 0
- 各ファイルで、許す差だけを加えたとき: 0
- limits の数値・依存のバージョン・起動の行・`rules.json` のほかの値・`children.json` の項目の番号・ワークフローのほかの値を 1 つ変えたとき: どれも 1
