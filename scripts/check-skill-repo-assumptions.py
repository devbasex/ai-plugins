#!/usr/bin/env python3
"""公開する Skill の本文に「対象リポジトリ = ai-plugins 自身」の前提が無いかを検査する。

NDF の Skill は任意のリポジトリに対して実行される。本文が ai-plugins にしか無い操作・
パス・値を条件なしで実行させると、他のリポジトリでは成立せず、担当した AI が自分の判断で
別のものへ振り替える。振り替えた事実は記録に残らない。

規約の本文は plugins/ndf/skills/README.md「対象リポジトリを仮定しない」にある。本
スクリプトはそのうち機械的に判定できる部分、すなわち **ai-plugins に固有の語が本文へ
現れていないか**だけを検査する。書き方が正しいか（探し方を書いているか、形で分岐して
いるか）は判定しない。

走査するのは `manifests/*-skills.txt` の和集合が指す Skill の Markdown である。配らない
Skill と `tests/` の下は対象にしない。**manifest に載っていながら走査できる本文を 1 本も
持たない Skill があるときは、検査自体を失敗させる**。読み飛ばすと、公開する Skill の本文が
丸ごと未走査のまま検査が成功する。

除外は EXCLUSIONS がファイルと理由の対で宣言する。**宣言した対象が走査の対象として
実在しないときは、ヒットの有無に関わらず検査自体を失敗させる**。ファイルを消したり
移したりしたときに、宣言だけが残り続けることを防ぐ。

**ファイルは `--skills-dir` に渡すのと同じ書き方（Skill ディレクトリを含むパス）で書く。**
`--skills-dir` は plugin family を 1 つだけ指定でき、その family の外にある宣言は走査の
対象にならない。Skill ディレクトリからの相対パスで書くと、どの family の宣言かが判別
できず、指定しなかった family の宣言まで「実在しない」と読んで検査を落とす。実在の検査は
**指定した family に属する宣言だけ**へ掛ける。`--skills-dir` を省いた走査（family を
すべて見る）では、どの family にも属さない宣言も陳腐化として落とす。

使い方:

    python3 scripts/check-skill-repo-assumptions.py
    python3 scripts/check-skill-repo-assumptions.py --skills-dir plugins/ndf/skills
    python3 scripts/check-skill-repo-assumptions.py --report   # 走査の規模とヒットの一覧

終了コード:

    0  除外の外にヒットが無い
    1  除外の外にヒットがある
    2  除外の宣言・引数・走査の範囲が誤っている（検査そのものが成立しない）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

# --- 検知する語 -------------------------------------------------------------
# ai-plugins にしか無い操作・ディレクトリ・ファイルを指す語。issue #292 の棚卸しで
# 見つかった形から作った。**網羅ではない**。新しい形が見つかったらここへ足す。
PATTERNS: tuple[str, ...] = (
    r"claude plugin ",
    r"codex plugin ",
    r"agy plugin ",
    r"build-runtime-plugins",
    r"check-skill-frontmatter",
    r"check-doc-staleness",
    r"check-markdown-links",
    r"validate-runtime-plugins",
    r"runtime-smoke-test",
    r"check-pr-base",
    r"\.claude-plugin",
    r"\.codex-plugin",
    r"marketplace\.json",
    r"manifests/",
)
PATTERN_RE = re.compile("|".join(PATTERNS))

# --- 除外 -------------------------------------------------------------------
# キーは Skill ディレクトリを含むパス（`--skills-dir` に渡すのと同じ書き方）、値は除外
# する理由である。**理由は必須**で、空にすると検査自体が失敗する。
#
# 除外の基準は「記述の主題が NDF 自身の配置・配布であること」と「配布物の形で分岐した
# 先の参照であること」の 2 つに限る。対象リポジトリへの指示は除外しない。
EXCLUSIONS: dict[str, str] = {
    "plugins/ndf/skills/release/references/form-package-plugin.md":
        "配布物の形がプラグインのときだけ読む参照。形で分岐済み",
    "plugins/ndf/skills/development-workflow/references/projects-tracking.md":
        "agy が NDF 自身を複製する位置の説明。対象リポジトリを指していない",
    "plugins/ndf/skills/official-skills-autoloader/SKILL.md":
        "NDF 自身の配布先の指定。対象リポジトリを指していない",
    "plugins/ndf/skills/out-of-scope/references/issue-target.md":
        "NDF の実体を持つ clone を見分ける手順。対象リポジトリを指していない",
}

RUNTIMES = ("claude", "codex", "kiro", "agy")


def canon(path: pathlib.Path | str) -> str:
    """除外の宣言と走査対象を突き合わせるための正規形。

    宣言は相対でも絶対でも書ける（`--skills-dir` と同じ）。どちらで書かれても同じ
    ファイルを同じ値にするため、絶対パスへ解決してから比べる。表示には使わない。
    """
    return pathlib.Path(path).resolve().as_posix()


class Hit:
    """本文の 1 行に現れたヒット。"""

    def __init__(self, skills_dir: pathlib.Path, rel: str, lineno: int, word: str, line: str) -> None:
        self.skills_dir = skills_dir
        self.rel = rel
        self.lineno = lineno
        self.word = word
        self.line = line
        # 除外の宣言と突き合わせるキー。family をまたいで同じ相対パスがあっても、
        # 別のファイルとして区別する
        self.key = canon(skills_dir / rel)

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


def collect_documents(skills_dir: pathlib.Path) -> tuple[list[str], list[str]]:
    """走査する Markdown を、Skill ディレクトリからの相対パスで返す。

    第 2 の戻り値は、manifest に載っていながら走査できる本文を 1 本も持たない Skill 名で
    ある。ディレクトリが無い場合と、あっても対象の Markdown が無い場合のどちらも入る。
    **呼び出し側はこれを検査成立不可として扱う**。読み飛ばすと、公開する Skill の本文が
    丸ごと未走査のまま検査が成功する。
    """
    docs: list[str] = []
    unscanned: list[str] = []
    for name in sorted(load_manifest_union(skills_dir)):
        root = skills_dir / name
        found = 0
        if root.is_dir():
            for path in sorted(root.rglob("*.md")):
                rel = path.relative_to(skills_dir)
                if "tests" in rel.parts:
                    continue
                docs.append(rel.as_posix())
                found += 1
        if found == 0:
            unscanned.append(name)
    return docs, unscanned


def scan(skills_dir: pathlib.Path, docs: list[str]) -> list[Hit]:
    """走査対象の本文からヒットを集める。"""
    hits: list[Hit] = []
    for rel in docs:
        text = (skills_dir / rel).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in PATTERN_RE.finditer(line):
                hits.append(Hit(skills_dir, rel, lineno, m.group(0), line))
    return hits


def owning_skills_dir(key: str, skills_dirs: list[pathlib.Path]) -> pathlib.Path | None:
    """除外の宣言がどの Skill ディレクトリに属するかを返す。属さなければ None。"""
    for d in skills_dirs:
        if key.startswith(canon(d) + "/"):
            return d
    return None


def validate_exclusions(exclusions: dict[str, str], scanned: set[str],
                        skills_dirs: list[pathlib.Path], exhaustive: bool) -> list[str]:
    """除外の宣言が成立しているかを確かめ、成立しない理由を返す。

    実在の検査は、**検査した Skill ディレクトリに属する宣言だけ**へ掛ける。`--skills-dir`
    は plugin family を 1 つだけ指定でき、そのとき他の family の宣言は走査の対象にならない。
    走査していないものを「実在しない」と読むと、正しい宣言のまま検査が落ちる。

    `exhaustive` は family をすべて見た走査（`--skills-dir` を省いた既定）であることを表す。
    このときはどの family にも属さない宣言も陳腐化として挙げる。指定を省いた走査で見逃すと、
    消えたファイルの宣言が残り続ける。
    """
    problems: list[str] = []
    for key, reason in sorted(exclusions.items()):
        if not isinstance(reason, str) or not reason.strip():
            problems.append(f"除外の理由が空である: {key}")
        owner = owning_skills_dir(canon(key), skills_dirs)
        if owner is None:
            if exhaustive:
                problems.append(
                    f"除外の対象がどの Skill ディレクトリにも属さない: {key}"
                    "（消したか移したなら、除外の宣言も外す）")
            continue
        if canon(key) not in scanned:
            problems.append(
                f"除外の対象が走査の範囲に実在しない: {key}"
                "（消したか移したなら、除外の宣言も外す）")
    return problems


def resolve_skills_dirs(given: list[str] | None) -> list[pathlib.Path]:
    """検査対象の Skill ディレクトリを決める。"""
    if given:
        return [pathlib.Path(d) for d in given]
    found = []
    for d in sorted(pathlib.Path("plugins").glob("*")):
        if (d / "manifests").is_dir() and (d / "skills").is_dir():
            found.append(d / "skills")
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skills-dir", action="append", default=None,
                    help="検査対象の Skill ディレクトリ。複数指定できる"
                         "（既定: manifests/ を持つ plugin family の skills/ を全て検査）")
    ap.add_argument("--exclusions", default=None,
                    help="除外の宣言を JSON（{相対パス: 理由}）で差し替える"
                         "（既定: 本スクリプトの EXCLUSIONS）")
    ap.add_argument("--report", action="store_true",
                    help="走査した本数とヒット数を出す")
    args = ap.parse_args()

    skills_dirs = resolve_skills_dirs(args.skills_dir)
    if not skills_dirs:
        print("[check-skill-repo-assumptions] 検査対象が見つからない", file=sys.stderr)
        return 2
    for d in skills_dirs:
        if not d.is_dir():
            print(f"[check-skill-repo-assumptions] ディレクトリがない: {d}", file=sys.stderr)
            return 2

    exclusions = EXCLUSIONS
    if args.exclusions is not None:
        try:
            loaded = json.loads(pathlib.Path(args.exclusions).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"[check-skill-repo-assumptions] 除外の宣言を読めない: {e}", file=sys.stderr)
            return 2
        if not isinstance(loaded, dict):
            print("[check-skill-repo-assumptions] 除外の宣言は {相対パス: 理由} の対で書く",
                  file=sys.stderr)
            return 2
        exclusions = loaded

    scanned: set[str] = set()
    all_hits: list[Hit] = []
    report_lines: list[str] = []
    unscanned: list[pathlib.Path] = []
    for skills_dir in skills_dirs:
        docs, missing = collect_documents(skills_dir)
        unscanned.extend(skills_dir / name for name in missing)
        scanned.update(canon(skills_dir / rel) for rel in docs)
        hits = scan(skills_dir, docs)
        all_hits.extend(hits)
        report_lines.append(
            f"{skills_dir}: 公開する Skill {len(load_manifest_union(skills_dir))} 個 / "
            f"Markdown {len(docs)} 本 / ヒット {len(hits)} 行")

    # 走査の範囲が欠けたままの結果は、ヒットが 0 でも「無い」ことの根拠にならない。
    # 除外の宣言より先に見る。範囲が欠けていると、除外の実在の判定も当てにならない。
    if unscanned:
        print("[check-skill-repo-assumptions] manifest に載る Skill の本文を走査できない:",
              file=sys.stderr)
        for path in unscanned:
            print(f"  - {path}（ディレクトリか、走査の対象になる Markdown が無い）",
                  file=sys.stderr)
        print("\n配らなくなったなら manifests/*-skills.txt からも外す。", file=sys.stderr)
        return 2

    problems = validate_exclusions(exclusions, scanned, skills_dirs,
                                   exhaustive=args.skills_dir is None)
    if problems:
        print("[check-skill-repo-assumptions] 除外の宣言が成立していない:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2

    excluded_keys = {canon(key) for key in exclusions}
    outside = [h for h in all_hits if h.key not in excluded_keys]

    if args.report:
        for line in report_lines:
            print(line)
        print(f"除外の宣言 {len(exclusions)} 件 / ヒット {len(all_hits)} 行 "
              f"（うち除外の外 {len(outside)} 行）")
        for h in all_hits:
            mark = "除外" if h.key in excluded_keys else "検知"
            print(f"  [{mark}] {h}")
        return 0

    if outside:
        print("[check-skill-repo-assumptions] 対象リポジトリを仮定した記述がある:", file=sys.stderr)
        for h in outside:
            print(f"  {h}", file=sys.stderr)
        print("\n対象リポジトリに無い場合の振る舞いを、コマンドとセットで書く。"
              "書き方は plugins/ndf/skills/README.md「対象リポジトリを仮定しない」にある。",
              file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
