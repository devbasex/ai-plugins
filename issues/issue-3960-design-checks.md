# #3960 の確かめのコマンド: 参照・子の表・移した表・構文木・値が受け入れ条件のとおりかを終了コードで返す

[issue-3960-design.md](./issue-3960-design.md) の決定 4 とテスト設計が使う 5 つのコマンドである。どれもリポジトリの根で打つ。`<base>` は変更の前のコミットで、`main` を取り込んだ後は `git merge-base HEAD origin/main` である。

## 検証用のディレクトリ

スクリプトと分類済みの一覧は、worktree ごとに 1 回だけ作る非公開のディレクトリに置く。固定のパスは使わない（並行する検証が互いの一覧を上書きするため）。

```bash
W=$(mktemp -d) && echo "$W"
```

出たパスを控え、別のシェルから続けるときは `W=<控えたパス>` を打ってから使う。作り直すと埋めた分類を引き継げないので、PR に一覧を載せ終えるまで消さない。下の Python のブロックは、書いてある名前で全部このディレクトリに置く（`gitread.py` は同じディレクトリから読み込まれる）。

## 共通の読み込み

下を `$W/gitread.py` に置く。変更の前の中身は、ファイルの数によらず `git cat-file --batch` の 1 回で読む。git の起動は、参照の `list` が 2 回・`check` が 3 回、構文木が 4 回、値が 2 回、子の表が 1 回、移した表が 1 回である。

```python
import subprocess
def git(*a):
    return subprocess.run(["git", *a], capture_output=True, check=True).stdout
def blobs(rev, names):
    """`rev` の時点の中身を、git cat-file --batch の 1 回でまとめて読む。"""
    names = list(names)
    ask = "".join(f"{rev}:{n}\n" for n in names).encode()
    out = subprocess.run(["git", "cat-file", "--batch"], input=ask, capture_output=True, check=True).stdout
    held, at = {}, 0
    for n in names:
        end = out.index(b"\n", at)
        size = int(out[at:end].split()[2])
        held[n] = out[end + 1:end + 1 + size]
        at = end + size + 2
    return held
```

## 参照の確かめ（条件 1・2・3・5・6）

下を `$W/refs.py` に置く。`list` で一覧のひな形を作り、分類を埋めてから `check` を打つ。`main` を取り込んで候補が変わったら `list` を打ち直す。埋めた分類は引き継ぎ、増えた行だけが空で出る。

```bash
python3 "$W/refs.py" list <base> "$W/refs.tsv" > "$W/refs.new" && mv "$W/refs.new" "$W/refs.tsv"
python3 "$W/refs.py" check <base> "$W/refs.tsv"
```

```python
import collections, pathlib, re, sys
from gitread import blobs, git
mode, base, ledger = sys.argv[1:4]
NL = r"[ \t]*(?:\n[ \t]*#?[ \t]*)?"
SEP = NL + r"(?:[・、,，〜～\-–]|と|及び|および)" + NL
MORE = "(?:" + SEP + r"(?:決定" + NL + r")?\d+)"
RUN = re.compile(r"決定" + NL + r"\d+" + MORE + "*")
OLD_PATH = re.compile(r"issue-(?:1142|3347|3395|3388)-[a-z0-9-]+(?:\.md)?")
SPEC_PATH = re.compile(r"(?<!docs/glossary/)ndf-rebuild[a-z-]*\.md")
FORM = re.compile(r"docs/specifications/(ndf-rebuild(?:-[a-z]+)*\.md) の決定 (\d+(?:・\d+)*)(?!\d)(?!" + MORE + ")")
SECTION = re.compile(r"docs/specifications/(ndf-rebuild(?:-[a-z]+)*\.md) の「([^」\n]+)」")
KINDS = ("書き換え", "別の設計", "移設対象外", "決められない", "コードの値")
CHECKS = "plugins/ndf/src/ndf/checks/"
OTHER = (CHECKS + "children.json", CHECKS + "rules.json")  # 設計文書のパスは値の確かめが見る
REQUIRED = {CHECKS + "invariants.py": "ndf-rebuild.md「常に成り立つ条件」",
            "plugins/ndf/src/ndf/core/plan/transition_decide.py": "ndf-rebuild-scripted-flow.md「洗い出しの一覧」"}
UNMOVED = "ndf-rebuild.md「番号の対応表」"
def texts(rev):
    if rev:
        held = blobs(rev, (n for n in git("ls-tree", "-rz", "--name-only", rev, "plugins/ndf").decode().split("\0") if n))
    else:
        held = {n: pathlib.Path(n).read_bytes() for n in git("ls-files", "-z", "plugins/ndf").decode().split("\0") if n and pathlib.Path(n).is_file()}
    for name, raw in held.items():
        try:
            yield name, raw.decode()
        except UnicodeDecodeError:
            continue
def old_refs(name, text):
    """古い形の参照の（位置・番号かパスの並び）。番号の並びは 1 つの組で、範囲は展開する。"""
    found = []
    for m in RUN.finditer(text):
        nums = []
        for sep, n in re.findall(r"(\D*)(\d+)", m[0]):
            nums += range(int(nums[-1]) + 1, int(n) + 1) if nums and re.search("[〜～]", sep) else [int(n)]
        found.append((m.start(), [str(n) for n in nums]))
    if name not in OTHER:
        found += [(m.start(), [m[0]]) for m in OLD_PATH.finditer(text)]
    return sorted(found)
def candidates(name, text):
    """組の並び。組は（行の文・番号かパス）の並び。"""
    for pos, tokens in old_refs(name, text):
        line = text[text.rfind("\n", 0, pos) + 1:].split("\n")[0].strip().replace("\t", " ")
        yield [(name, line, t) for t in tokens]
groups = [g for name, text in texts(base) for g in candidates(name, text)]
flat = [c for g in groups for c in g]
rows = [l.split("\t") for l in open(ledger, encoding="utf-8").read().splitlines()] if pathlib.Path(ledger).is_file() else []
if mode == "list":
    kept = collections.defaultdict(list)
    for r in rows:
        kept[tuple(r[:3])].append(r[3:])
    total = collections.Counter(flat)
    kept = {c: olds if len(olds) == total[c] else [] for c, olds in kept.items()}
    for c in flat:
        print("\t".join([*c, *(kept[c].pop(0) if kept.get(c) else ["", "", ""])]))
    sys.exit()
def headings(name, held={}):
    if name not in held:
        path = pathlib.Path("docs/specifications") / name
        held[name] = re.findall(r"^#{2,4} (.+)$", path.read_text(encoding="utf-8"), re.M) if path.is_file() else []
    return held[name]
bad = []
for r in rows:
    r += [""] * (6 - len(r))
    path, new = not r[2].isdigit(), r[4]
    if r[3] == "書き換え":
        ok = bool(re.search("#|「", new)) or (path and new == "=")
    else:
        ok = r[3] in KINDS and new == (UNMOVED if path else "")
    if not ok or not r[5].strip():
        bad.append(f"一覧の行の形: {r}")
if [tuple(r[:3]) for r in rows] != flat:
    have, want = collections.Counter(tuple(r[:3]) for r in rows), collections.Counter(flat)
    bad += [f"候補が一覧に無い: {k}" for k in want - have] + [f"候補に無い一覧の行: {k}" for k in have - want]
    bad.append("一覧の並びが候補と違う（list で並べ直す）")
    print("\n".join(bad))
    print("違反", len(bad), "件")
    sys.exit(1)
left, want, untouched = iter(rows), collections.defaultdict(list), collections.defaultdict(list)
for g in groups:
    items = []
    for name, line, token in g:
        r = next(left)
        items += [("new", r[4])] if r[4] not in ("", "=") else [("old", token)] if not r[4] else []
        untouched[name, line].append(not r[4])
    want[g[0][0]].append(items)
refs, seen = 0, set()
for name, text in texts(None):
    seen.add(name)
    spans, found = [], []
    for m in FORM.finditer(text):
        spans.append(m.span())
        for n in m[2].split("・"):
            found.append((m.start(), ("new", f"{m[1]}#{n}")))
            if not any(h.startswith(f"決定 {n}:") for h in headings(m[1])):
                bad.append(f"決定が無い: {name} {m[1]} の決定 {n}")
    for m in SECTION.finditer(text):
        spans.append(m.span())
        if name not in OTHER:
            found.append((m.start(), ("new", f"{m[1]}「{m[2]}」")))
        if m[2] not in headings(m[1]):
            bad.append(f"見出しが無い: {name} {m[0]}")
    for m in SPEC_PATH.finditer(text):
        if not any(a <= m.start() < b for a, b in spans):
            bad.append(f"形が違う: {name} {text[m.start():m.start() + 60]!r}")
    rest = list(text)
    for a, b in spans:
        rest[a:b] = "\0" * (b - a)
    found += [(p, ("old", t)) for p, ts in old_refs(name, "".join(rest)) for t in ts]
    found = [item for _, item in sorted(found, key=lambda f: f[0])]
    refs += sum(kind == "new" for kind, _ in found)
    at = 0
    for items in want.get(name, []):
        now = found[at:at + len(items)]
        if sorted(now) != sorted(items):
            bad.append(f"並びが一覧と違う: {name} {at + 1} 番目から {items} のはずが {now}")
            break
        at += len(items)
    else:
        if found[at:]:
            bad.append(f"一覧に無い参照: {name} {found[at:]}")
    lines = {l.strip().replace("\t", " ") for l in text.split("\n")}
    bad += [f"残す行が変わった: {name} {line}" for (n, line), keep in untouched.items() if n == name and all(keep) and line not in lines]
    if name in REQUIRED and ("new", REQUIRED[name]) not in found:
        bad.append(f"決めたセクションを指していない: {name} {REQUIRED[name]}")
    bad += [f"設計文書のパスが残る: {name} {m[0]}" for m in OLD_PATH.finditer(text)]
bad += [f"ファイルが無い: {name}" for name in sorted(set(want) - seen)]
print("\n".join(bad))
print("候補", len(flat), "件・新しい参照", refs, "件・違反", len(bad), "件")
sys.exit(bool(bad))
```

候補は 2 種類で、ファイルの中の並びの順に出る。

- `決定` に続く番号。番号ごとに 1 件と数える（`・`・`、`・`〜`・改行・2 つ目の「決定」を挟む並びも拾い、範囲は展開する）。番号でない数を拾った候補（例: 「決定 14 と 2026-11-20」）は、根拠を添えて `別の設計` に分ける。コードの値の文字列の中の作り直しの決定は `コードの値` に分け、新しい参照を空にする
- 設計文書 36 本のパス（番号の列にファイル名が入る）。`書き換え` の新しい参照には、セクションを指すなら `ndf-rebuild.md「常に成り立つ条件」` の形を書く。同じ文の番号の参照や別のファイルの名前へ畳むなら `=` を書く。`移設対象外` と `決められない` のパスは `ndf-rebuild.md「番号の対応表」` とする。`children.json` と `rules.json` の中のパスは候補に出さず、残りだけを見る

`check` は次の 6 つを見る。数ではなく、ファイルの中の並びで 1 件ずつ対応付ける。

1. 候補と一覧が、並びの順まで過不足なく合う
2. 変更の後の参照の並びが、一覧の並びと 1 件ずつ合う（1 つの「決定」に並ぶ番号の組の中だけは、確定仕様のファイルごとにまとめた並べ替えを許す）
3. 書き換えの無い候補だけの行は、文が変わらない
4. 確定仕様のパスを書く箇所が、番号の形かセクションの名前の形だけで、指す決定とセクションの見出しがある
5. `invariants.py` が「常に成り立つ条件」を、`transition_decide.py` が「洗い出しの一覧」を指す
6. 設計文書 36 本のパスが 1 つも残らない

`f8c20aae5` の候補は 725 件（番号 716・パス 9）である。設計文書のパスを書く 7 ファイルと、`identity.py` の「決定 75・決定 79」を書き換えた形で確かめた結果は次のとおりである。

| 書き換えた後の文 | 終了コード |
| --- | ---: |
| 一覧どおり（`…の決定 75・79`） | 0 |
| 上と同じで、ほかの行の 1 件を `移設対象外` に分けて残した | 0 |
| 旧い形のまま残した | 1 |
| `…の決定 75・決定 999`・`…の決定 75、999`・`…の決定 75〜79`・`…の決定 75・999` | 1 |
| パスと番号の間、番号と番号の間で改行した | 1 |
| 1 つだけ旧い形（`…の決定 75・#1142 の決定 79`）・無い文書 | 1 |
| 一覧から候補を 1 行落とした・残す行の文を変えた | 1 |
| `identity.py` の 2 つの「決定 101」のうち、一覧と違うほうを書き換えた（数は同じ） | 1 |
| `transition_decide.py` の旧いパスを削るだけにした（一覧を `=` にしても）・無い見出しを指した | 1 |
| `invariants.py` に設計文書のパスを残した | 1 |

## 子の表の確かめ（条件 7）

下を `$W/kids.py` に置き、`python3 "$W/kids.py" <base>` を打つ。`children` と `tables` は変更の前から全項目が変わらないこと、先に要る子・順番・レーン・Issue は変更の前の「子の記号」の 52 件とも合うことを見る。確定仕様の側は、52 件の記号・Issue・中身が変わらないことと、R0・R1 が表の直前の段落に `R0 #3757「<変更の前の中身>」` の形で移ったことを見る。

```python
import json, sys
from gitread import blobs
base = sys.argv[1]
KIDS, SPEC = "plugins/ndf/src/ndf/checks/children.json", "docs/specifications/ndf-rebuild.md"
before = {n: raw.decode() for n, raw in blobs(base, (KIDS, SPEC)).items()}
old, new = json.loads(before[KIDS]), json.load(open(KIDS, encoding="utf-8"))
def table(text):
    sec = text[text.index("## 子の記号"):text.index("## 決定と理由")]
    lead = sec[:sec.index("\n|")]
    cells = [[c.strip() for c in line.strip("|").split("|")] for line in sec.splitlines() if line.startswith("|")]
    return lead, cells[0], cells[2:]
bad = []
for key in ("children", "tables"):
    for k in sorted(set(old[key]) | set(new[key])):
        if json.dumps(old[key].get(k)) != json.dumps(new[key].get(k)):
            bad.append(f"{key} {k}: {old[key].get(k)} → {new[key].get(k)}")
kids = new["children"]
lead, head, body = table(open(SPEC, encoding="utf-8").read())
rows = {c[0]: c for c in body}
for c in table(before[SPEC])[2]:
    k = c[0]
    if k in ("R0", "R1"):
        if f"{k} {c[1]}「{c[2]}」" not in lead:
            bad.append(f"表の直前の段落に無い: {k} {c[1]}「{c[2]}」")
        continue
    if rows.get(k, [])[:3] != c[:3]:
        bad.append(f"記号・Issue・中身が変更の前と違う: {c[:3]} → {rows.get(k)}")
    want = {"issue": int(c[1].strip("#")), "needs": sorted([] if c[3] == "無し" else c[3].split("・")), "order": int(c[4]), "lane": c[5]}
    got = {**{f: kids.get(k, {}).get(f) for f in want}, "needs": sorted(kids.get(k, {}).get("needs", []))}
    if want != got:
        bad.append(f"変更の前の確定仕様と違う {k}: {want} → {got}")
if head != ["記号", "Issue", "中身"]:
    bad.append(f"確定仕様の列: {head}")
if sorted(c[0] for c in body) != sorted(kids):
    bad.append(f"記号の違い: {sorted(set(rows) ^ set(kids))}（表 {len(body)} 行）")
bad += [f"Issue か中身が無い: {c}" for c in body if c[1] != f"#{kids.get(c[0], {}).get('issue')}" or not c[2:] or not c[2]]
if [k for k, v in kids.items() if "ndf.core.refs" in v["packages"]] != ["C4"]:
    bad.append("ndf.core.refs のオーナーが C4 だけでない")
if new["tables"].get("contracts/limits/") != ["D1", "T1+", "M3a"]:
    bad.append("contracts/limits/ の書き込み元が D1・T1+・M3a でない")
print("\n".join(bad))
print("子", len(kids), "件・違反", len(bad), "件")
sys.exit(bool(bad))
```

- 変更の前（`<base>` と同じ形）: 1（列・記号の違いと、R0・R1 が段落に無いこと）
- 「子の記号」だけを決定 1 の形にしたとき: 0（`子 54 件・違反 0 件`）
- その上で C2 の `needs`・C4 の `packages`・`contracts/limits/` の書き込み元・X9 を 1 つずつ削ったとき: どれも 1
- X3 の中身を変えた・R0 か R1 の文を消した・変えた・行を重ねた・X9 の中身を空にしたとき: どれも 1

## 移した表の確かめ（条件 11）

下を `$W/moved.py` に置き、`python3 "$W/moved.py" <base>` を打つ。移す 2 つの表を、行の ID ごとに全部のセルで変更の前と比べる。規則 `review-items` は 11.x の行き先の形と ID の有無だけを見るので、`Z10` を別の有効な記号に変えた誤りはこのコマンドが拾う。許す差は、182 項目の表のリンク先の文書の名前だけである。

```python
import collections, pathlib, sys
from gitread import blobs
base = sys.argv[1]
OLD11, NEW11 = "issue-3388-design-routing.md", "ndf-review-items-11x.md"
TABLES = (("issues/issue-1142-design-review-routing.md", "docs/specifications/ndf-review-items.md", "項目", 182),
          (f"issues/{OLD11}", f"docs/specifications/{NEW11}", "行", 72))
def table(text, key):
    """見出しの先頭の列が `key` で 3 列を超える表（読み方の表は 2 列）。（見出し・行）の並び。"""
    found, rows = [], None
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not line.startswith("|"):
            rows = None
        elif rows is not None:
            rows.append(cells)
        elif cells[0] == key and len(cells) > 3:
            rows = []
            found.append((cells, rows))
    return [(head, rows[1:]) for head, rows in found]
bad, total = [], 0
before = blobs(base, [t[0] for t in TABLES])
for source, moved, key, count in TABLES:
    (head, want), *_ = table(before[source].decode().replace(f"]({OLD11})", f"]({NEW11})"), key)
    tables = table(pathlib.Path(moved).read_text(encoding="utf-8"), key) if pathlib.Path(moved).is_file() else []
    now_head, now = tables[0] if len(tables) == 1 else ([], [])
    if len(want) != count:
        bad.append(f"変更の前の表が {count} 行でない: {source} {len(want)} 行")
    if now_head != head:
        bad.append(f"列が違うか、表が 1 つでない: {moved} {now_head}")
    if [r[0] for r in now] != [r[0] for r in want]:
        bad.append(f"行の並びか過不足が違う: {moved} {len(want)} 行 → {len(now)} 行")
    held = collections.defaultdict(list)
    for r in now:
        held[r[0]].append(r)
    for r in want:
        if held[r[0]] != [r]:
            bad.append(f"セルが違う: {moved} {r} → {held[r[0]]}")
    total += len(want)
print("\n".join(bad))
print("比べた行", total, "件・違反", len(bad), "件")
sys.exit(bool(bad))
```

- 変更の前（移した 2 本が無い）: 1
- 2 つの表を、リンク先の文書の名前だけを替えて移したとき: 0（`比べた行 254 件・違反 0 件`）
- 対応表の `3342-10` の行き先を `Z10` から `Z9` に、`要求 338` の行き先を `Z10` から `Z1` に変えたとき: どれも 1
- 対応表の行を落とす・重ねる・要旨か見出しを変えたとき: どれも 1
- 182 項目の表の要求の行かリンク先を変えたとき: どれも 1

## 構文木の確かめ（条件 10）

下を `$W/tree.py` に置き、`python3 "$W/tree.py" <base>` を打つ。ファイルの集合は全体で比べ、中身は `git diff` が挙げた `.py` だけを読む。

```python
import ast, sys
from gitread import blobs, git
base = sys.argv[1]
def tree(text):
    t = ast.parse(text)
    for n in ast.walk(t):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and ast.get_docstring(n, clean=False) is not None:
            n.body[0].value.value = ""
    return ast.dump(t)
now = {n for n in git("ls-files", "-z", "--", "plugins/ndf/*.py").decode().split("\0") if n}
old = {n for n in git("ls-tree", "-rz", "--name-only", base, "plugins/ndf").decode().split("\0") if n.endswith(".py")}
changed = {n for n in git("diff", "-z", "--name-only", "--no-renames", base, "--", "plugins/ndf/*.py").decode().split("\0") if n}
bad = sorted(now ^ old)
before = blobs(base, sorted(changed & now & old))
bad += [n for n, raw in before.items() if tree(raw.decode()) != tree(open(n, encoding="utf-8").read())]
print("\n".join(bad))
print("変わった .py", len(before), "件・違い", len(bad), "件")
sys.exit(bool(bad))
```

- 変更の前: `違い 0 件`
- docstring を 1 行替えたとき: 0 件
- `llm.py` の定数を 1 つ替えたとき: 1 件

## `.py` 以外のファイルの値の確かめ（条件 10）

下を `$W/values.py` に置き、`python3 "$W/values.py" <base>` を打つ。許す差は次だけで、ほかの値・ファイルの追加と削除・実行権の変更は違反にする。

| ファイル | 許す差 |
| --- | --- |
| `contracts/` の JSON | `summary` の文 |
| `rules.json` | `ndf.adapters.llm.ATTEMPTS` の `reason` の文と、`review_items_source` の決めた替え先 |
| `children.json` | 頭の `summary` の文と、`review_items` の対応表の名前の決めた替え先 |
| `pyproject.toml` | コメント |
| `bin/ndf-claude` | コメントだけの行（1 行目を除く） |
| `script-structure.yml` | `REVIEW_ITEMS_PATHS` の決めた替え先 |

```python
import json, re, sys, tomllib
from gitread import blobs, git
base = sys.argv[1]
WF, CK = ".github/workflows/script-structure.yml", "plugins/ndf/src/ndf/checks/"
OLD, NEW = "issues/issue-1142-design-review-routing.md", "docs/specifications/ndf-review-items.md"
OLD11, NEW11 = "issue-3388-design-routing.md", "ndf-review-items-11x.md"
def hide(v, key):
    """空でない文だけを伏せる。キーの削除・空の文・文字列でない値は違いとして残す。"""
    if isinstance(v.get(key), str) and v[key].strip():
        v[key] = "<文>"
def mask(v):
    for x in v.values() if isinstance(v, dict) else v if isinstance(v, list) else ():
        mask(x)
    if isinstance(v, dict):
        hide(v, "summary")
    return v
def loaded(name, text, before):
    v = json.loads(text)
    if name.startswith("plugins/ndf/contracts/"):
        return mask(v)
    if name == CK + "rules.json":
        for r in v["facts"]["limit_constants"]:
            if r["name"] == "ndf.adapters.llm.ATTEMPTS":
                hide(r, "reason")
        if before and v["spec"]["review_items_source"] == OLD:
            v["spec"]["review_items_source"] = NEW
    if name == CK + "children.json":
        hide(v, "summary")
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
bad, targets = [], []
for line in git("diff", "--raw", "--no-renames", base, "--", "plugins/ndf", WF).decode().splitlines():
    meta, name = line.split("\t")
    modes = meta.lstrip(":").split()
    if modes[4] != "M" or modes[0] != modes[1]:
        bad.append(f"追加・削除・モードの変更: {name}")
    elif not name.endswith((".py", ".md")):
        targets.append(name)
for name, raw in blobs(base, targets).items():
    if value(name, raw.decode(), True) != value(name, open(name, encoding="utf-8").read(), False):
        bad.append(f"説明のほかの値が違う: {name}")
print("\n".join(bad))
print("違い", len(bad), "件")
sys.exit(bool(bad))
```

- 変更の前: 0
- 各ファイルで、許す差だけを加えたとき: 0
- `ATTEMPTS` の `reason` のキーを消す・空の文にする、`contracts/` の `summary` を空にしたとき: どれも 1（空でない文どうしの差だけを許す）
- limits の数値・依存のバージョン・起動の行・`rules.json` のほかの値・`children.json` の項目の番号・ワークフローのほかの値を 1 つ変えたとき: どれも 1
