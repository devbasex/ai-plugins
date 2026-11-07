#!/usr/bin/env python3
"""公開する Skill とスクリプトに「対象リポジトリ = ai-plugins 自身」の前提が無いかをチェックする。

NDF の Skill は任意のリポジトリに対して実行される。本文が ai-plugins にしか無い操作・
パス・値を条件なしで実行させると、他のリポジトリでは成立せず、担当した AI が自分の判断で
別のものへ振り替える。振り替えた事実は記録に残らない。

規約の本文は plugins/ndf/skills/AUTHORING.md「対象リポジトリを仮定しない」にある。本
スクリプトはそのうち機械的に判定できる部分、すなわち **ai-plugins に固有の語が本文へ
現れていないか**だけをチェックする。書き方が正しいか（探し方を書いているか、形で分岐して
いるか）は判定しない。語を含まないまま ai-plugins の形を前提にした論理も拾えない。

走査するのは、`manifests/*-skills.txt` の和集合が指す Skill の Markdown と `scripts/`、
family の `scripts/` の下のスクリプト（`.py` / `.sh` / `.js`）、`hooks/*.json` である。
配らない Skill と、`tests/`・`experimental/` の下は対象にしない。Markdown には
DOC_WORDS、スクリプトと hook には SCRIPT_WORDS を掛ける。プロジェクトごとに違うものは
`.ndf/` の宣言か引数で受ける。**manifest に載っていながら走査できる本文を 1 本も持たない
Skill があるときは、チェック自体を失敗させる**。読み飛ばすと、公開する Skill の本文が丸ごと
未走査のままチェックが成功する。

既知の当たりは例外リスト `scripts/repo-assumptions-allow/` が 1 項目 1 ファイルで宣言する。
項目は語とファイルの組ごとに当たりの数を持ち、次の 2 種類に分かれる:

    not-a-violation  NDF 自身の置き場を読むなど違反でない当たり。reason にその理由を書く
    pending          違反のまま残す当たり。issue に直す課題の番号を書く

**数を超えると新しい箇所として落とし、数より少ないと宣言の直し忘れとして落とす**。
ファイルを消したり移したりしたときに、宣言だけが残り続けることを防ぐ。

**path は `--skills-dir` に渡すのと同じ書き方（Skill ディレクトリを含むパス）で書く。**
`--skills-dir` は plugin family を 1 つだけ指定でき、そのとき突き合わせるのは**指定した
family に属する項目だけ**である。`--skills-dir` を省いた走査（family をすべて見る）では、
どの family にも属さない項目も陳腐化として落とす。

使い方:

    python3 scripts/check-skill-repo-assumptions.py
    python3 scripts/check-skill-repo-assumptions.py --skills-dir plugins/ndf/skills
    python3 scripts/check-skill-repo-assumptions.py --report   # 走査の規模とヒットの一覧

終了コード:

    0  宣言の外にヒットが無く、宣言の数と実際の数が一致する
    1  宣言の無い組のヒット、または宣言の数を超えたヒットがある
    2  例外リスト・引数・走査の範囲が誤っているか、宣言の数よりヒットが少ない

**2 は 1 より先に判定する。** **`--report` は出力を足すだけで、終了コードは上表のまま**である。
レポートを常に 0 で返すと、同じチェックを一覧として実行した利用者と自動処理が違反を成功として扱う。
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
from allowlist import AllowError, allow_file_name, load_allow  # noqa: E402

# --- 検知する語 -------------------------------------------------------------
# 語の ID → 正規表現。ai-plugins にしか無い操作・ディレクトリ・ファイルを指す語で、例外リストの
# name は語の ID を書く（語そのものはファイル名に使えない）。**網羅ではない**。新しい形が
# 見つかったらここへ足す。
DOC_WORDS: dict[str, str] = {
    "claude-plugin-cmd": r"claude plugin ",
    "codex-plugin-cmd": r"codex plugin ",
    "agy-plugin-cmd": r"agy plugin ",
    "build-runtime-plugins": r"build-runtime-plugins",
    "check-skill-frontmatter": r"check-skill-frontmatter",
    "check-doc-staleness": r"check-doc-staleness",
    "check-markdown-links": r"check-markdown-links",
    "validate-runtime-plugins": r"validate-runtime-plugins",
    "runtime-smoke-test": r"runtime-smoke-test",
    "check-pr-base": r"check-pr-base",
    "claude-plugin-dir": r"\.claude-plugin",
    "codex-plugin-dir": r"\.codex-plugin",
    "marketplace-json": r"marketplace\.json",
    "manifests-dir": r"manifests/",
}

# スクリプトと hook に掛ける語。ai-plugins の置き場・テストや同期のコマンド・ランタイムの組・ブランチ・
# リリースの形。スクリプトは NDF 自身の置き場（.claude-plugin/ など）を正しく読むため、Markdown の語とは分ける
SCRIPT_WORDS: dict[str, str] = {
    "ndf-dir": r"plugins/ndf/",
    "playwright-kit": r"playwright-kit",
    "build-runtime-plugins": r"build-runtime-plugins",
    "check-skill-frontmatter": r"check-skill-frontmatter",
    "check-markdown-links": r"check-markdown-links",
    "check-doc-line-limit": r"check-doc-line-limit",
    "runtime-list": r"claude,codex,kiro",
    "origin-develop": r"origin/develop",
    "quoted-main": r"[\"']main[\"']",
    "quoted-develop": r"[\"']develop[\"']",
    "base-main": r"--base[ =]main\b",
    "admin-merge": r"--admin\b",
    "tag-form": r"--v(?![A-Za-z-])",
    "release-branch": r"release/v(?![a-z])",
    "plugin-manifest": r"plugin\.json",
    "spec-dir": r"docs/specifications",
    "pytest": r"\bpytest\b",
}
KNOWN_WORDS = frozenset(DOC_WORDS) | frozenset(SCRIPT_WORDS)


def compile_words(words: dict[str, str]) -> list[tuple[str, re.Pattern[str]]]:
    return [(word_id, re.compile(regex)) for word_id, regex in words.items()]


DOC_MATCHERS = compile_words(DOC_WORDS)
SCRIPT_MATCHERS = compile_words(SCRIPT_WORDS)
SCRIPT_SUFFIXES = (".py", ".sh", ".js")
SKIP_DIRS = frozenset({"tests", "experimental"})

# --- 例外リスト -------------------------------------------------------------
ALLOW_DIR = pathlib.Path(__file__).resolve().parent / "repo-assumptions-allow"
ALLOW_KINDS = ("not-a-violation", "pending")

RUNTIMES = ("claude", "codex")


def canon(path: pathlib.Path | str) -> str:
    """例外リストと走査対象を突き合わせるための正規形。

    宣言は相対でも絶対でも書ける（`--skills-dir` と同じ）。どちらで書かれても同じ
    ファイルを同じ値にするため、絶対パスへ解決してから比べる。表示には使わない。
    """
    return pathlib.Path(path).resolve().as_posix()


class Hit:
    """本文の 1 行に現れたヒット。1 行に 2 回現れたら 2 件になる。"""

    def __init__(self, skills_dir: pathlib.Path, rel: str, lineno: int, word_id: str, word: str, line: str) -> None:
        # skills_dir は走査の起点（Skill ディレクトリか、family の scripts/・hooks/）
        self.skills_dir = skills_dir
        self.rel = rel
        self.lineno = lineno
        self.word_id = word_id
        self.word = word
        self.line = line
        # 例外リストと突き合わせるキー。family をまたいで同じ相対パスがあっても、
        # 別のファイルとして区別する
        self.key = (canon(skills_dir / rel), word_id)

    def __str__(self) -> str:
        return f"{self.skills_dir / self.rel}:{self.lineno}: {self.word!r} — {self.line.strip()}"


def load_manifest_union(skills_dir: pathlib.Path) -> set[str]:
    """`manifests/(runtime)-skills.txt` の和集合を返す。

    行末の `#` 以降はコメントとして落とす（`scripts/build-runtime-plugins.sh` と
    `scripts/check-skill-frontmatter.py` の manifest 解釈に揃える）。
    """
    man_dir = skills_dir.parent / "manifests"
    names: set[str] = set()
    for runtime in RUNTIMES:
        f = man_dir / f"{runtime}-skills.txt"
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            name = line.split("#", 1)[0].strip()
            if name:
                names.add(name)
    return names


def collect_documents(skills_dir: pathlib.Path, names: set[str] | None = None) -> tuple[list[str], list[str]]:
    """走査する Markdown を、Skill ディレクトリからの相対パスで返す。

    第 2 の戻り値は、manifest に載っていながら走査できる本文を 1 本も持たない Skill 名で
    ある。ディレクトリが無い場合と、あっても対象の Markdown が無い場合のどちらも入る。
    **呼び出し側はこれをチェック成立不可として扱う**。読み飛ばすと、公開する Skill の本文が
    丸ごと未走査のままチェックが成功する。

    `names` は読み込み済みの manifest の和集合。省くとここで読む。
    """
    if names is None:
        names = load_manifest_union(skills_dir)
    docs: list[str] = []
    unscanned: list[str] = []
    for name in sorted(names):
        root = skills_dir / name
        found = 0
        if root.is_dir():
            for path in sorted(root.rglob("*.md")):
                rel = path.relative_to(skills_dir)
                if set(path.relative_to(root).parts) & SKIP_DIRS:
                    continue
                docs.append(rel.as_posix())
                found += 1
        if found == 0:
            unscanned.append(name)
    return docs, unscanned


def collect_scripts(root: pathlib.Path) -> list[str]:
    """`root` の下の配布するスクリプトを、`root` からの相対パスで返す。"""
    if not root.is_dir():
        return []
    found = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_file() and path.suffix in SCRIPT_SUFFIXES and not set(rel.parts) & SKIP_DIRS:
            found.append(rel.as_posix())
    return found


def collect_skill_scripts(skills_dir: pathlib.Path, names: set[str]) -> list[str]:
    """manifest に載る Skill の `scripts/` の下のスクリプトを、Skill ディレクトリからの相対パスで返す。"""
    found = []
    for name in sorted(names):
        found += [f"{name}/scripts/{rel}" for rel in collect_scripts(skills_dir / name / "scripts")]
    return found


def collect_hooks(skills_dir: pathlib.Path) -> list[str]:
    """family の `hooks/` の下の定義を、`hooks/` からの相対パスで返す。"""
    root = skills_dir.parent / "hooks"
    return [path.name for path in sorted(root.glob("*.json"))] if root.is_dir() else []


def scan(base: pathlib.Path, rels: list[str], matchers: list[tuple[str, re.Pattern[str]]]) -> list[Hit]:
    """走査対象の本文からヒットを集める。語ごとに照合するので、同じ行の 2 語は別のヒットになる。

    語の和で先に行を絞る。ほとんどの行に語が無く、語ごとの照合を全行へ掛けると CI の待ちが延びる。"""
    any_word = re.compile("|".join(regex.pattern for _, regex in matchers))
    hits: list[Hit] = []
    for rel in rels:
        text = (base / rel).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not any_word.search(line):
                continue
            for word_id, regex in matchers:
                hits += [Hit(base, rel, lineno, word_id, m.group(0), line) for m in regex.finditer(line)]
    return hits


def owning_skills_dir(key: str, skills_dirs: list[pathlib.Path]) -> pathlib.Path | None:
    """項目のパスがどの family に属するかを Skill ディレクトリで返す。属さなければ None。"""
    for d in skills_dirs:
        if any(key.startswith(canon(root) + "/") for root in (d, d.parent / "scripts", d.parent / "hooks")):
            return d
    return None


def validate_row(file: pathlib.Path, row: dict) -> None:
    """項目のうち `load_allow` が見ないキーの形を確かめる。"""
    count = row["count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise AllowError(f"例外リストの {file.name} の count は 1 以上の整数にする: {count!r}")
    if row["name"] not in KNOWN_WORDS:
        raise AllowError(f"例外リストの {file.name} の name は語の ID にする: {row['name']!r}")
    issue = row.get("issue", 1)
    if isinstance(issue, bool) or not isinstance(issue, int) or issue < 1:
        raise AllowError(f"例外リストの {file.name} の issue は課題の番号にする: {issue!r}")


def row_keys(row: dict) -> set[str]:
    return {"count", "issue"} if row.get("kind") == "pending" else {"count"}


def read_allow(path: pathlib.Path) -> list[dict]:
    """例外リストを読む。読めないか形が違うか同じ組の項目が 2 つあれば AllowError を上げる。"""
    rows = load_allow(path, ALLOW_KINDS, row_keys, validate_row)
    seen = collections.Counter((canon(r["path"]), r["name"]) for r in rows)
    twice = sorted(f"{key[0]} の {key[1]}" for key, n in seen.items() if n > 1)
    if twice:
        raise AllowError(f"同じ語とファイルの組の項目が 2 つある: {', '.join(twice)}")
    return rows


def validate_allow(
    rows: list[dict], counts: collections.Counter, scanned: set[str], skills_dirs: list[pathlib.Path], exhaustive: bool
) -> list[str]:
    """項目が走査の結果と合っているかを確かめ、合わない理由を返す。

    突き合わせるのは、**チェックした Skill ディレクトリに属する項目だけ**である。`--skills-dir`
    は plugin family を 1 つだけ指定でき、そのとき他の family の項目は走査の対象にならない。
    走査していないものを「実在しない」と読むと、正しい宣言のままチェックが落ちる。

    `exhaustive` は family をすべて見た走査（`--skills-dir` を省いた既定）であることを表す。
    このときはどの family にも属さない項目も陳腐化として挙げる。指定を省いた走査で見逃すと、
    消えたファイルの項目が残り続ける。
    """
    problems: list[str] = []
    for row in rows:
        path, name, file = row["path"], row["name"], allow_file_name(row)
        key = canon(path)
        if owning_skills_dir(key, skills_dirs) is None:
            if exhaustive:
                problems.append(f"項目の対象がどの Skill ディレクトリにも属さない: {path}（消したか移したなら {file} も消す）")
            continue
        if key not in scanned:
            problems.append(f"項目の対象が走査の範囲に実在しない: {path}（消したか移したなら {file} も消す）")
            continue
        actual = counts[(key, name)]
        if actual < row["count"]:
            fix = f"count を {actual} へ直す" if actual else "項目を消す"
            problems.append(f"宣言より当たりが少ない: {path} の {name} は宣言 {row['count']}・実際 {actual}（{file} の{fix}）")
    return problems


def resolve_skills_dirs(given: list[str] | None) -> list[pathlib.Path]:
    """チェック対象の Skill ディレクトリを決める。"""
    if given:
        return [pathlib.Path(d) for d in given]
    found = []
    for d in sorted(pathlib.Path("plugins").glob("*")):
        if (d / "manifests").is_dir() and (d / "skills").is_dir():
            found.append(d / "skills")
    return found


def scan_family(skills_dir: pathlib.Path) -> tuple[set[str], list[Hit], str, list[pathlib.Path]]:
    """1 つの family の本文・スクリプト・hook を走査し、(走査した正規形, ヒット, 報告行, 走査できない Skill) を返す。"""
    names = load_manifest_union(skills_dir)
    docs, missing = collect_documents(skills_dir, names)
    unscanned = [skills_dir / name for name in missing]
    scripts_dir = skills_dir.parent / "scripts"
    hooks_dir = skills_dir.parent / "hooks"
    scripts = collect_scripts(scripts_dir)
    skill_scripts = collect_skill_scripts(skills_dir, names)
    hooks = collect_hooks(skills_dir)
    scanned = {canon(skills_dir / rel) for rel in docs + skill_scripts}
    scanned.update(canon(scripts_dir / rel) for rel in scripts)
    scanned.update(canon(hooks_dir / rel) for rel in hooks)
    hits = scan(skills_dir, docs, DOC_MATCHERS)
    hits += scan(scripts_dir, scripts, SCRIPT_MATCHERS)
    hits += scan(skills_dir, skill_scripts, SCRIPT_MATCHERS)
    hits += scan(hooks_dir, hooks, SCRIPT_MATCHERS)
    report_line = (
        f"{skills_dir}: 公開する Skill {len(names)} 個 / Markdown {len(docs)} 本 / "
        f"スクリプト {len(scripts) + len(skill_scripts)} 本（family {len(scripts)}・Skill {len(skill_scripts)}） / "
        f"hook {len(hooks)} 本 / ヒット {len(hits)} 件"
    )
    return scanned, hits, report_line, unscanned


def print_report(report_lines: list[str], rows: list[dict], all_hits: list[Hit], outside: list[Hit]) -> None:
    """`--report` の走査の規模とヒットの一覧を出す。"""
    flagged = {id(h) for h in outside}
    for line in report_lines:
        print(line)
    print(f"例外リスト {len(rows)} 項目 / ヒット {len(all_hits)} 件 （うち宣言の外 {len(outside)} 件）")
    for h in all_hits:
        print(f"  [{'検知' if id(h) in flagged else '既知'}] {h}")


def hits_outside(all_hits: list[Hit], declared: dict[tuple[str, str], int], counts: collections.Counter) -> list[Hit]:
    """宣言の無い組のヒットと、宣言の数を超えた組のヒットを返す。

    数を超えた組は、どの行が新しいかを決められないので組のヒットをすべて返す。"""
    return [h for h in all_hits if counts[h.key] > declared.get(h.key, 0)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--skills-dir",
        action="append",
        default=None,
        help="チェック対象の Skill ディレクトリ。複数指定できる（既定: manifests/ を持つ plugin family の skills/ を全てチェック）",
    )
    ap.add_argument("--allow", default=str(ALLOW_DIR), help="例外リストのディレクトリ（既定: scripts/repo-assumptions-allow/）")
    ap.add_argument("--report", action="store_true", help="走査した本数とヒット数を出す")
    args = ap.parse_args()

    skills_dirs = resolve_skills_dirs(args.skills_dir)
    if not skills_dirs:
        print("[check-skill-repo-assumptions] チェック対象が見つからない", file=sys.stderr)
        return 2
    for d in skills_dirs:
        if not d.is_dir():
            print(f"[check-skill-repo-assumptions] ディレクトリがない: {d}", file=sys.stderr)
            return 2

    scanned: set[str] = set()
    all_hits: list[Hit] = []
    report_lines: list[str] = []
    unscanned: list[pathlib.Path] = []
    for skills_dir in skills_dirs:
        family_scanned, hits, report_line, family_unscanned = scan_family(skills_dir)
        scanned |= family_scanned
        all_hits.extend(hits)
        report_lines.append(report_line)
        unscanned.extend(family_unscanned)

    # 走査の範囲が欠けたままの結果は、ヒットが 0 でも「無い」ことの根拠にならない。
    # 例外リストより先に見る。範囲が欠けていると、項目の実在の判定も当てにならない。
    if unscanned:
        print("[check-skill-repo-assumptions] manifest に載る Skill の本文を走査できない:", file=sys.stderr)
        for path in unscanned:
            print(f"  - {path}（ディレクトリか、走査の対象になる Markdown が無い）", file=sys.stderr)
        print("\n配らなくなったなら manifests/*-skills.txt からも外す。", file=sys.stderr)
        return 2

    try:
        rows = read_allow(pathlib.Path(args.allow))
    except AllowError as e:
        print(f"[check-skill-repo-assumptions] {e}", file=sys.stderr)
        return 2

    counts = collections.Counter(h.key for h in all_hits)
    problems = validate_allow(rows, counts, scanned, skills_dirs, exhaustive=args.skills_dir is None)
    if problems:
        print("[check-skill-repo-assumptions] 例外リストが成立していない:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2

    declared = {(canon(r["path"]), r["name"]): r["count"] for r in rows}
    outside = hits_outside(all_hits, declared, counts)

    # `--report` は出力を足すだけで、判定は変えない。走査の範囲と例外リストの誤り
    # （rc=2）はこの手前で落としており、ヒットだけを 0 で返すと同じ実行の中で終了
    # コードの意味が 2 通りになる。README はこのコマンドをチェックの一覧として載せる。
    if args.report:
        print_report(report_lines, rows, all_hits, outside)

    if outside:
        print("[check-skill-repo-assumptions] 対象リポジトリを仮定した記述がある:", file=sys.stderr)
        for h in outside:
            print(f"  {h}", file=sys.stderr)
        print(
            "\n値は .ndf/ の宣言か引数で受ける。対象リポジトリに無い場合の振る舞いを、コマンドとセットで書く。"
            "書き方は plugins/ndf/skills/AUTHORING.md「対象リポジトリを仮定しない」にある。"
            "\n数を超えた組は、どの行が新しいかを決められないので組の当たりをすべて出している。",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
