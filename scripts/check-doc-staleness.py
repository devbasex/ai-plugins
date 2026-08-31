#!/usr/bin/env python3
"""説明文書に書かれた Skill 数と版数を、実体・マニフェスト・plugin.json と突き合わせる。

配布する Skill の数はランタイムごとに違い、その数が利用者の読む 2 本の説明文書
（`README.md` と `plugins/ndf/README.md`）に書かれている。数を機械的に突き合わせる検査は
プラグインの定義ファイルにしか届いていなかったため、版を上げるたびに説明文書の側へ古い数が
残った。ここでは説明文書の側を突き合わせの対象へ入れる。

読み取れないこと自体も食い違いと同じく失敗として扱う。素通りさせると、数の記載を消すか
書式を変えるだけでこの検査を無効化できてしまう。

`scripts/validate-runtime-plugins.sh` から呼ばれる。単独でも実行できる。

    python3 scripts/check-doc-staleness.py --root .
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 説明文書に数と版数を書いているのは NDF プラグインだけである。他の family が同じ書き方を
# 始めたときに対象を広げる。
FAMILY = "ndf"
ROOT_README = "README.md"
PLUGIN_README = f"plugins/{FAMILY}/README.md"
PLUGIN_JSON = f"plugins/{FAMILY}/.claude-plugin/plugin.json"
SKILLS_DIR = f"plugins/{FAMILY}/skills"
OPTIONAL_DIR = f"plugins/{FAMILY}/optional-skills"


def manifest_path(runtime: str) -> str:
    return f"plugins/{FAMILY}/manifests/{runtime}-skills.txt"


# `README.md` はランタイムを Claude Code / Kiro / Codex の順、`plugins/ndf/README.md` は
# Claude Code / Codex / Kiro の順で書いている。位置ではなく名前で対応づける。表記も
# 2 本で違う（`Kiro` と `Kiro CLI`）ため、対応表を文書ごとに持つ。
ROOT_README_RUNTIMES = {"Claude Code": "claude", "Kiro": "kiro", "Codex": "codex"}
PLUGIN_README_RUNTIMES = {"Claude Code": "claude", "Codex": "codex", "Kiro CLI": "kiro"}

RUNTIME_COUNT = re.compile(r"(Claude Code|Kiro|Codex)向け core\s*(\d+)\s*個")
SOURCE_COUNT = re.compile(r"元Skills（\s*(\d+)\s*個\s*）")
CATEGORY_LINE = re.compile(r"^\s+-\s+(?P<label>.+?)\s+\((?P<count>\d+)\)\s*[:：]\s*(?P<names>.+)$")
TABLE_ROW = re.compile(r"^\|\s*(Claude Code|Codex|Kiro CLI)\s*\|\s*(\d+)\s*個\s*\|")
LAYOUT_SKILLS = re.compile(r"唯一の実体（\s*(\d+)\s*個\s*）")
LAYOUT_OPTIONAL = re.compile(r"どの配布先にも載せない Skill（\s*(\d+)\s*個\s*）")
UPGRADE_HEADING = re.compile(r"^##\s+v(\d+\.\d+\.\d+)\s+へ更新するとき\s*$", re.MULTILINE)


class Report:
    """食い違いを集めて、最後にまとめて出す。

    最初の 1 件で止めると、版を上げたときに残った古い記載を 1 つずつしか直せない。
    """

    def __init__(self) -> None:
        self.errors: list[str] = []

    def add(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")

    def add_source(self, message: str) -> None:
        self.errors.append(message)


def manifest_skill_count(root: Path, runtime: str, report: Report) -> int | None:
    """ランタイムへ配る Skill の数。コメントと空行は数えない。

    数え方は `scripts/validate-runtime-plugins.sh` の `manifest_skill_count` と同じにする。
    """
    manifest = root / manifest_path(runtime)
    if not manifest.is_file():
        report.add_source(f"{manifest_path(runtime)} が無い（{runtime} の配布 Skill 数を数える相手）")
        return None
    return sum(
        1
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    )


def skill_dir_count(root: Path, relative: str, report: Report) -> int | None:
    """`SKILL.md` を持つディレクトリの数。README.md などのファイルは数えない。"""
    directory = root / relative
    if not directory.is_dir():
        report.add_source(f"{relative} が無い（Skill の実体を数える相手）")
        return None
    return sum(1 for child in directory.iterdir() if (child / "SKILL.md").is_file())


def plugin_version(root: Path, report: Report) -> str | None:
    path = root / PLUGIN_JSON
    if not path.is_file():
        report.add_source(f"{PLUGIN_JSON} が無い（版数を突き合わせる相手）")
        return None
    version = json.loads(path.read_text(encoding="utf-8")).get("version")
    if not isinstance(version, str):
        report.add_source(f"{PLUGIN_JSON} に version がない")
        return None
    return version


def read_document(root: Path, relative: str, report: Report) -> str | None:
    path = root / relative
    if not path.is_file():
        report.add_source(f"{relative} が無い（検査の対象の説明文書）")
        return None
    return path.read_text(encoding="utf-8")


def check_runtime_counts(body: str, counts: dict[str, int | None], report: Report) -> None:
    """`README.md` の「<ランタイム>向け core N 個」を、マニフェストの行数と突き合わせる（A）。

    同じ書き方が概要と一覧表の 2 箇所にある。どちらも対象にするため、出現をすべて見る。
    """
    found: dict[str, list[int]] = {label: [] for label in ROOT_README_RUNTIMES}
    for label, number in RUNTIME_COUNT.findall(body):
        found[label].append(int(number))
    for label, runtime in ROOT_README_RUNTIMES.items():
        expected = counts.get(runtime)
        if not found[label]:
            report.add(
                ROOT_README,
                f"公開Skills の {label} の数が読み取れない"
                f"（`{label}向け core <数>個` の形で書く。{manifest_path(runtime)}: {expected}）",
            )
            continue
        if expected is None:
            continue
        for described in found[label]:
            if described != expected:
                report.add(
                    ROOT_README,
                    f"公開Skills の {label} の数が食い違う"
                    f"（記載: {described} / {manifest_path(runtime)}: {expected}）",
                )


def category_lines(body: str) -> list[re.Match[str]] | None:
    """元 Skill 数の行に続くカテゴリ内訳を、途切れるまで拾う。

    位置で拾うのは、`README.md` の別の箇所にある同じ形の箇条書きを巻き込まないためである。
    """
    lines = body.splitlines()
    anchor = next((i for i, line in enumerate(lines) if SOURCE_COUNT.search(line)), None)
    if anchor is None:
        return None
    matched: list[re.Match[str]] = []
    for line in lines[anchor + 1 :]:
        found = CATEGORY_LINE.match(line)
        if not found:
            break
        matched.append(found)
    return matched


def check_source_counts(body: str, total: int | None, detail: str, report: Report) -> None:
    """`README.md` の元 Skill 数（B）とカテゴリ内訳（C）を突き合わせる。"""
    described = SOURCE_COUNT.findall(body)
    if not described:
        report.add(
            ROOT_README,
            f"元Skills の数が読み取れない（`元Skills（<数>個）` の形で書く。{detail}）",
        )
    elif total is not None:
        for value in described:
            if int(value) != total:
                report.add(
                    ROOT_README,
                    f"元Skills の数が食い違う（記載: {value} / {detail}）",
                )

    matched = category_lines(body)
    if not matched:
        report.add(
            ROOT_README,
            "元Skills のカテゴリ内訳が読み取れない"
            f"（`  - <分類> (<数>): <Skill 名>, ...` の形で元Skills の行の直後に並べる。{detail}）",
        )
        return
    for found in matched:
        label = found.group("label")
        declared = int(found.group("count"))
        listed = [name for name in re.split(r"[,、]", found.group("names")) if name.strip()]
        if declared != len(listed):
            report.add(
                ROOT_README,
                f"カテゴリ内訳「{label}」の数が、並ぶ Skill 名の数と食い違う"
                f"（宣言: {declared} / 並ぶ名前: {len(listed)}）",
            )
    if total is not None:
        summed = sum(int(found.group("count")) for found in matched)
        if summed != total:
            report.add(
                ROOT_README,
                f"カテゴリ内訳の合計が食い違う（合計: {summed} / {detail}）",
            )


def check_distribution_table(body: str, counts: dict[str, int | None], report: Report) -> None:
    """`plugins/ndf/README.md` の配布先の表を、マニフェストの行数と突き合わせる（D）。"""
    found: dict[str, list[int]] = {label: [] for label in PLUGIN_README_RUNTIMES}
    for line in body.splitlines():
        row = TABLE_ROW.match(line)
        if row:
            found[row.group(1)].append(int(row.group(2)))
    for label, runtime in PLUGIN_README_RUNTIMES.items():
        expected = counts.get(runtime)
        if not found[label]:
            report.add(
                PLUGIN_README,
                f"配布先の表に {label} の行が無い"
                f"（`| {label} | <数> 個 | ... |` の形で書く。{manifest_path(runtime)}: {expected}）",
            )
            continue
        if expected is None:
            continue
        for described in found[label]:
            if described != expected:
                report.add(
                    PLUGIN_README,
                    f"配布先の表の {label} の数が食い違う"
                    f"（記載: {described} / {manifest_path(runtime)}: {expected}）",
                )


def check_layout_counts(
    body: str, skills: int | None, optional: int | None, report: Report
) -> None:
    """`plugins/ndf/README.md` のレイアウト図に書かれた 2 つの数を突き合わせる（E）。"""
    for pattern, expected, wording, source in (
        (LAYOUT_SKILLS, skills, "唯一の実体（<数> 個）", f"{SKILLS_DIR}/ の実体"),
        (LAYOUT_OPTIONAL, optional, "どの配布先にも載せない Skill（<数> 個）", f"{OPTIONAL_DIR}/ の実体"),
    ):
        described = pattern.findall(body)
        if not described:
            report.add(
                PLUGIN_README,
                f"レイアウト図の数が読み取れない（`{wording}` の形で書く。{source}: {expected}）",
            )
            continue
        if expected is None:
            continue
        for value in described:
            if int(value) != expected:
                report.add(
                    PLUGIN_README,
                    f"レイアウト図の数が食い違う（記載: {value} / {source}: {expected}）",
                )


def check_upgrade_heading(body: str, version: str | None, report: Report) -> None:
    """更新案内の見出しの版数を `plugin.json` の版と突き合わせる（F）。

    本文がその版の変更内容を説明しているかは機械では決められない。ここで見るのは見出しの
    版数だけで、版を上げたときに必ずこの節へ触る状態を作ることを目的とする。本文を読み直す
    機会は `docs/plugin-development-guide.md` のバージョン管理の手順が作る。
    """
    headings = UPGRADE_HEADING.findall(body)
    if not headings:
        report.add(
            PLUGIN_README,
            f"更新案内の見出しが無い（`## v<版> へ更新するとき` の形で書く。{PLUGIN_JSON}: v{version}）",
        )
        return
    if len(headings) > 1:
        report.add(
            PLUGIN_README,
            f"更新案内の見出しが {len(headings)} 個ある（v{' / v'.join(headings)}）。"
            "この節は現行の版の 1 つだけにする",
        )
        return
    if version is not None and headings[0] != version:
        report.add(
            PLUGIN_README,
            f"更新案内の見出しの版数が古い（見出し: v{headings[0]} / {PLUGIN_JSON}: v{version}）",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="リポジトリの根（既定: カレントディレクトリ）")
    args = parser.parse_args()
    root = Path(args.root).resolve()

    report = Report()
    counts = {
        runtime: manifest_skill_count(root, runtime, report)
        for runtime in ("claude", "codex", "kiro")
    }
    skills = skill_dir_count(root, SKILLS_DIR, report)
    optional = skill_dir_count(root, OPTIONAL_DIR, report)
    version = plugin_version(root, report)

    total = None if skills is None or optional is None else skills + optional
    detail = f"{SKILLS_DIR}/ の実体 {skills} + {OPTIONAL_DIR}/ の {optional} = {total}"

    root_body = read_document(root, ROOT_README, report)
    if root_body is not None:
        check_runtime_counts(root_body, counts, report)
        check_source_counts(root_body, total, detail, report)

    plugin_body = read_document(root, PLUGIN_README, report)
    if plugin_body is not None:
        check_distribution_table(plugin_body, counts, report)
        check_layout_counts(plugin_body, skills, optional, report)
        check_upgrade_heading(plugin_body, version, report)

    if report.errors:
        for error in report.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("documented skill counts and version headings are up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
